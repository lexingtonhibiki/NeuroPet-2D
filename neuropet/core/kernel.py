"""FeaturePlugin 内核(ADR-0031):作用域化副作用 + 状态权威仲裁器。

设计来源:Cordis/Koishi 内核调研(docs/references/cordis内核调研.md)三个
可直接借鉴点在本模块的落法:

1. **副作用随作用域自动回收**:功能插件经 FeatureContext 拿到的每个注册
   (每帧钩子/总线订阅/位置写入授权)都登记到该作用域;unmount 逆序执行
   全部 disposer —— 插件写错也漏不掉清理,多写者打架在结构上不可能存活。
2. **可逆性 = 路径无关**:反复 mount/unmount 后行为只取决于最终启用集合、
   钩子表不增长(tests/test_kernel.py 锁定)。
3. **状态权威做成仲裁服务**:`st.pos` 的功能写者必须每帧提交"写入意向"
   (writer + priority),主循环每帧恰好一次按优先级合成写入;意向帧界有效
   (begin_pet 清空)——功能被卸载即自然失去写权(租约随作用域失效)。
   优先级冻结:drag 90 > locomotion 40(body 积分为
   缺省权威,不显式登记)。

已关闭作用域再注册抛 ScopeClosedError(仿 Koishi 4.16.4:僵尸插件写入直接
报错,而不是静默失效)。

主循环管道位(app._step / app._render 调用):
    held_tick     抓握宠每帧(拖拽动力学 + 位置意向)
    frozen_tick   冻结宠每帧(冻结拖拽仅位置意向合成,用户需求 #5)
    rewrite_command 命令管道:脑命令 → 功能改写链(注册序)→ body
    post_apply    body.apply 之后(位置意向提交通常在此前完成)
    apply_pos     仲裁合成(每帧一次;held/frozen 分支亦各自调用)
    post_move     位置定案之后(松手衰减等)
    render        每渲染帧一次(遮挡门控等)
"""
from __future__ import annotations

from abc import ABC
from typing import Any, Callable

from .contracts import PetState

# ---- 位置权威优先级(ADR-0031 冻结) ----
POS_LOCOMOTION = 40     # body._integrate 缺省权威(不显式登记)
POS_DRAG = 90           # 抓握拖拽(用户手,最高)


class ScopeClosedError(RuntimeError):
    """已卸载作用域上的注册/写入(僵尸插件防护)。"""


class _Hook:
    __slots__ = ("order", "seq", "fn", "stage", "scope")

    def __init__(self, stage: str, order: int, seq: int, fn: Callable,
                 scope: str = "") -> None:
        self.stage = stage
        self.order = order
        self.seq = seq
        self.fn = fn
        self.scope = scope


class FeatureContext:
    """功能插件作用域:注册即登记,dispose 整组回收(幂等)。

    生命线:每个注册方法返回 disposer(调用即单独撤销该注册);作用域关闭
    后任何注册/写入意向抛 ScopeClosedError —— "任何注册必须可撤销"是插件
    API 红线(Cordis 调研《可逆的插件系统》)。
    """

    def __init__(self, kernel: "FeatureKernel", feature_id: str) -> None:
        self._kernel = kernel
        self.feature_id = feature_id
        self._disposed = False
        self._disposers: list[Callable[[], None]] = []

    # ---------------- 注册面(全部返回 disposer) ----------------
    def every_frame(self, stage: str, order: int,
                    fn: Callable[..., None]) -> Callable[[], None]:
        """登记每帧钩子。stage 见模块 docstring 管道位;同 stage 内按
        (order, 登记序) 升序执行。"""
        holder: list[_Hook] = []

        def _do() -> None:
            holder.clear()
            holder.append(self._kernel._add_hook(stage, order,
                                                 self.feature_id, fn))

        def _remove() -> None:
            hooks = self._kernel._hooks.get(stage)
            if hooks and holder:
                try:
                    hooks.remove(holder[0])
                except ValueError:
                    pass

        return self._register(_remove, _do)

    def subscribe(self, topic: str,
                  handler: Callable[[str, dict], None]) -> Callable[[], None]:
        """总线订阅入作用域(unmount 自动退订;bus.publish 异常本就被吞)。"""
        bus = self._kernel.bus

        def _dispose() -> None:
            bus.unsubscribe_all(handler)

        return self._register(_dispose,
                              lambda: bus.subscribe(topic, handler))

    def claim_pos(self, pet_id: str, value: tuple[float, float],
                  writer: str, priority: int) -> None:
        """本帧位置写入意向(帧界有效;优先级合成见 StateArbiter)。"""
        self._require_open()
        self._kernel.arbiter.claim(pet_id, value, writer, priority,
                                   self.feature_id)

    # ---------------- 机制 ----------------
    def _register(self, dispose: Callable[[], None],
                  do: Callable[[], None]) -> Callable[[], None]:
        self._require_open()
        do()
        entry = (do, dispose)

        def _disposer() -> None:
            if entry in self._disposers:
                self._disposers.remove(entry)
                dispose()

        self._disposers.append(entry)
        return _disposer

    def _require_open(self) -> None:
        if self._disposed:
            raise ScopeClosedError(
                f"功能 {self.feature_id!r} 作用域已卸载:注册/写入被拒绝"
                f"(僵尸插件防护,ADR-0031)")

    def dispose(self) -> None:
        """逆序回收全部副作用(幂等;dispose 后作用域永久关闭)。"""
        if self._disposed:
            return
        self._disposed = True
        while self._disposers:
            _, dispose = self._disposers.pop()
            try:
                dispose()
            except Exception as exc:      # 单条回收失败不阻断其余回收
                print(f"[kernel] {self.feature_id} disposer error: {exc!r}")


class StateArbiter:
    """每宠物 st.pos 权威仲裁器(ADR-0031 最小版:集中写入 + 注册表)。

    写者每帧提交意向 claim(pet_id, value, writer, priority);apply 每帧恰好
    一次:取最高优先级意向(同优先级按提交序,先到先得)覆写 st.pos(钳屏),
    无意向则 body 积分结果自然生效。begin_pet 在每宠每帧起点清空上一帧意向
    ——功能卸载后其钩子已从管道摘除,不再有意向产生,写权自动失效。

    v0.2.0:``pet_clamp_fn(pos, pet_id)`` 可选。留白**按宠物身体半径**给,
    与 ``body/base.py`` 的软墙同一个矩形 —— 旧口径仲裁恒用整屏 60px 而身体
    用 80px,拖到边缘时两个钳位互相拉扯,用户看到的就是"松手弹回一截"。
    不传时退回单参 ``clamp_fn``,旧调用方(测试/插件)行为不变。
    """

    def __init__(self, clamp_fn: Callable[[tuple[float, float]],
                                          tuple[float, float]],
                 pet_clamp_fn: Callable[[tuple[float, float], str],
                                        tuple[float, float]] | None = None
                 ) -> None:
        self._claims: dict[str, list[tuple[int, int, str, str,
                                          tuple[float, float]]]] = {}
        self._seq = 0
        self._clamp = clamp_fn
        self._pet_clamp = pet_clamp_fn or (lambda pos, _pid: clamp_fn(pos))
        self.last_writer: dict[str, str] = {}      # 诊断:本帧胜出写者

    def begin_pet(self, pet_id: str) -> None:
        self._claims.pop(pet_id, None)
        self.last_writer.pop(pet_id, None)

    def claim(self, pet_id: str, value: tuple[float, float], writer: str,
              priority: int, scope_id: str = "") -> None:
        self._seq += 1
        self._claims.setdefault(pet_id, []).append(
            (int(priority), self._seq, writer, scope_id, value))

    def resolve(self, pet_id: str) -> tuple[tuple[float, float], str] | None:
        """最高优先级意向(不写入;诊断/预览用)。"""
        xs = self._claims.get(pet_id)
        if not xs:
            return None
        best = max(xs, key=lambda c: (c[0], -c[1]))
        return best[4], best[2]

    def apply(self, st: PetState) -> None:
        """合成写入:有意向 → 最高优先级值(钳屏);无 → 不动(body 生效)。"""
        pid = st.pet_id
        xs = self._claims.get(pid)
        if not xs:
            return
        best = max(xs, key=lambda c: (c[0], -c[1]))
        st.pos = self._pet_clamp(best[4], pid)
        self.last_writer[pid] = best[2]
        self._claims.pop(pid, None)


class FeaturePlugin(ABC):
    """功能插件基类:mount(ctx) 里经 ctx 登记全部副作用;unmount 做对象级
    收尾(可选)。实例状态归插件,但每宠缓存必须经 bus 事件(system/pet_*)
    或钩子自清 —— 卸载/重挂载后行为只取决于最终启用集合(路径无关)。"""
    feature_id: str = ""

    def __init__(self, app: Any) -> None:
        self.app = app

    def mount(self, ctx: FeatureContext) -> None: ...
    def unmount(self) -> None: ...


class FeatureKernel:
    """功能注册表 + 主循环管道执行器 + 仲裁器宿主。"""

    def __init__(self, app: Any, bus, clamp_fn, pet_clamp_fn=None) -> None:
        self.app = app
        self.bus = bus
        self.arbiter = StateArbiter(clamp_fn, pet_clamp_fn)
        self._features: dict[str, FeaturePlugin] = {}
        self._contexts: dict[str, FeatureContext] = {}
        self._hooks: dict[str, list[_Hook]] = {}
        self._seq = 0

    # ---------------- 生命周期 ----------------
    def mount(self, feature: FeaturePlugin) -> FeaturePlugin:
        fid = feature.feature_id
        if not fid:
            raise ValueError("FeaturePlugin.feature_id 未设置")
        if fid in self._features:
            raise ValueError(f"功能已挂载: {fid}(先 unmount 再 mount)")
        ctx = FeatureContext(self, fid)
        self._features[fid] = feature
        self._contexts[fid] = ctx
        feature.mount(ctx)
        return feature

    def unmount(self, feature_id: str) -> bool:
        feature = self._features.pop(feature_id, None)
        ctx = self._contexts.pop(feature_id, None)
        if feature is None or ctx is None:
            return False
        for hooks in self._hooks.values():
            hooks[:] = [h for h in hooks if getattr(h, "scope", "") != feature_id]
        try:
            feature.unmount()
        except Exception as exc:
            print(f"[kernel] {feature_id}.unmount error: {exc!r}")
        ctx.dispose()
        return True

    def unmount_all(self) -> None:
        for fid in list(self._features):
            self.unmount(fid)

    def feature(self, feature_id: str) -> FeaturePlugin | None:
        return self._features.get(feature_id)

    # ---------------- 管道 ----------------
    def _add_hook(self, stage: str, order: int, scope: str,
                  fn: Callable) -> _Hook:
        self._seq += 1
        hook = _Hook(stage, order, self._seq, fn, scope)
        self._hooks.setdefault(stage, []).append(hook)
        return hook

    def run_stage(self, stage: str, *args: Any) -> None:
        hooks = self._hooks.get(stage)
        if not hooks:
            return
        for h in sorted(hooks, key=lambda x: (x.order, x.seq)):
            h.fn(*args)

    def rewrite_command(self, h, cmd, dt: float):
        """命令管道:按注册序让功能改写脑命令(返回 None = 保持原命令)。
        替代旧 app 内联改写(根因 A 的 hack,ADR-0031 决策 3)。"""
        hooks = self._hooks.get("rewrite_command")
        if not hooks:
            return cmd
        out = cmd
        for hk in sorted(hooks, key=lambda x: (x.order, x.seq)):
            r = hk.fn(h, out, dt)
            if r is not None:
                out = r
        return out

    def apply_pos(self, st: PetState) -> None:
        """位置权威合成(每帧每宠恰好一次;held/frozen 分支亦调用)。"""
        self.arbiter.apply(st)

    def begin_pet(self, st: PetState) -> None:
        """每宠每帧起点:清上一帧位置意向(帧界租约)。"""
        self.arbiter.begin_pet(st.pet_id)

    def drag_overlay(self, h) -> dict | None:
        """渲染拖拽姿态叠加:聚合已挂载功能提供的 drag_overlay(h)。
        多功能同时提供时取挂载序第一个非 None(当前仅 drag 功能)。"""
        for feature in self._features.values():
            fn = getattr(feature, "drag_overlay", None)
            if fn is None:
                continue
            out = fn(h)
            if out is not None:
                return out
        return None
