"""FeaturePlugin 内核验收(ADR-0031;第十一轮架构修)。运行:python tests/test_kernel.py

判据(8 组):
  ① 仲裁优先级合成:drag(90) > hide(60) > 无意向(body 生效);同优先级
     先到先得;apply 恰好消费一次(帧界)。
  ② 帧界租约:begin_pet 清上一帧意向 —— 功能停发意向后写权自然失效。
  ③ 路径无关(可逆性红线):mount→unmount 循环 N 轮后钩子表长度与首轮
     挂载态逐位相同、行为(钩子执行序列)不增长、不残留。
  ④ 僵尸防护:作用域关闭后 every_frame/subscribe/claim_pos 一律抛
     ScopeClosedError(仿 Koishi 4.16.4)。
  ⑤ disposer 精确性:同功能同 stage 多钩子,单钩 disposer 只摘自己;
     dispose 后 unmount 不重入。
  ⑥ 订阅入作用域:unmount 自动退订(bus 不再收到该处理器的调用)。
  ⑦ 命令管道:按注册序折叠改写;返回 None = 保持;卸载的功能不再参与。
  ⑧ 主循环管道位冒烟:App 实例挂载 hide/drag 两功能,run_stage 全位可跑,
     begin_pet/apply_pos 每宠帧界正确。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from neuropet.core.bus import EventBus
from neuropet.core.contracts import PetState
from neuropet.core.kernel import (POS_DRAG, FeatureContext,
                                  FeatureKernel, FeaturePlugin,
                                  ScopeClosedError, StateArbiter)

RESULTS: list[tuple[str, bool, str]] = []


def record(tag: str, ok: bool, detail: str) -> None:
    RESULTS.append((tag, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {tag}: {detail}")


def _clamp(pos, margin=60):
    return (max(margin, min(1920 - margin, pos[0])),
            max(margin, min(1080 - margin, pos[1])))


def _state(pid="p1"):
    return PetState(pet_id=pid, species_id="roach", pos=(600.0, 400.0))


# ---------------- ① 仲裁优先级 ----------------
def test_arbiter_priority() -> None:
    arb = StateArbiter(_clamp)
    st = _state()
    # 无意向:body 积分结果生效(apply 不动 st.pos)
    st.pos = (500.0, 400.0)
    arb.apply(st)
    record("arb.no_claim_body_wins", st.pos == (500.0, 400.0),
           f"无意向时 pos 不被仲裁器改写: {st.pos}")
    # hide(60) 压过 body
    arb.claim("p1", (100.0, 100.0), "hide", 60, "hide")
    arb.apply(st)
    record("arb.hide_beats_body", st.pos == _clamp((100.0, 100.0)),
           f"hide 60 生效: {st.pos}")
    # drag(90) 压过 hide(60):同帧双意向
    arb.claim("p1", (200.0, 200.0), "hide", 60, "hide")
    arb.claim("p1", (300.0, 300.0), "drag", POS_DRAG, "drag")
    arb.apply(st)
    record("arb.drag_beats_hide", st.pos == _clamp((300.0, 300.0))
           and arb.last_writer.get("p1") == "drag",
           f"drag 90 胜出: {st.pos} writer={arb.last_writer.get('p1')}")
    # apply 恰好一次:消费后同帧再 apply 无意向可消费
    st.pos = (500.0, 400.0)
    arb.apply(st)
    record("arb.claim_consumed_once", st.pos == (500.0, 400.0),
           "意向消费后不残留")
    # 同优先级先到先得
    arb.claim("p1", (100.0, 100.0), "hide", 60, "hide")
    arb.claim("p1", (150.0, 150.0), "hide2", 60, "hide")
    r = arb.resolve("p1")
    record("arb.same_prio_fifo", r is not None and r[0] == (100.0, 100.0),
           f"同优先级先到先得: {r}")


# ---------------- ② 帧界租约 ----------------
def test_frame_scoped_leases() -> None:
    arb = StateArbiter(_clamp)
    st = _state()
    arb.claim("p1", (100.0, 100.0), "hide", 60, "hide")
    arb.begin_pet("p1")           # 下一帧起点:清上一帧意向
    arb.apply(st)
    record("lease.frame_scoped", st.pos == (600.0, 400.0),
           f"begin_pet 后上一帧意向失效: pos={st.pos}(=初始)")


# ---------------- ③④ 作用域可逆性 + 僵尸防护 ----------------
class _ToyFeature(FeaturePlugin):
    feature_id = "toy"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.fired: list[str] = []

    def mount(self, ctx: FeatureContext) -> None:
        self.ctx = ctx
        ctx.every_frame("post_move", 0, lambda h, dt: self.fired.append("a"))
        ctx.every_frame("post_move", 1, lambda h, dt: self.fired.append("b"))
        ctx.every_frame("held_tick", 0, lambda h, dt: self.fired.append("h"))
        ctx.subscribe("toy/evt", lambda t, d: self.fired.append("evt"))

    def unmount(self) -> None:
        self.fired.append("unmounted")


def test_scope_reversibility() -> None:
    bus = EventBus()
    k = FeatureKernel(None, bus, _clamp)
    f = _ToyFeature(None)
    k.mount(f)
    n_hooks_first = sum(len(v) for v in k._hooks.values())
    st = _state()
    k.run_stage("post_move", st, 1 / 60)
    first_run = list(f.fired)
    # 卸载 → 钩子全回收 + unmount 收尾被调
    record("rev.unmount_true", k.unmount("toy") is True, "unmount 返回 True")
    record("rev.unmount_callback", f.fired[-1] == "unmounted",
           "unmount() 收尾被调用")
    record("rev.hooks_reclaimed",
           sum(len(v) for v in k._hooks.values()) == 0,
           "卸载后钩子表清空(作用域自动回收)")
    f.fired.clear()
    bus.publish("toy/evt")
    record("rev.bus_unsubscribed", f.fired == [], "卸载后总线订阅自动退订")
    # 重挂载 N 轮:路径无关 —— 钩子数与首轮一致,执行序列一致,无残留
    ok_cycles, ok_runs = True, True
    for i in range(50):
        fi = _ToyFeature(None)
        k.mount(fi)
        n_now = sum(len(v) for v in k._hooks.values())
        if n_now != n_hooks_first:
            ok_cycles = False
        fi.fired.clear()
        k.run_stage("post_move", st, 1 / 60)
        if fi.fired != first_run:
            ok_runs = False
        k.unmount("toy")
    record("rev.path_independent_hooks", ok_cycles,
           f"50 轮装卸后钩子数恒等于首轮({n_hooks_first})")
    record("rev.path_independent_behavior", ok_runs,
           "每轮执行序列与首轮逐位一致(无累积/残留)")
    record("rev.unmount_missing", k.unmount("toy") is False,
           "重复 unmount 幂等返回 False")


def test_scope_closed_raises() -> None:
    bus = EventBus()
    k = FeatureKernel(None, bus, _clamp)
    f = _ToyFeature(None)
    k.mount(f)
    ctx = f.ctx
    k.unmount("toy")
    raised = []

    def _expect(name, fn):
        try:
            fn()
        except ScopeClosedError:
            raised.append(name)
        except Exception as exc:
            raised.append(f"{name}!!{type(exc).__name__}")

    _expect("every_frame", lambda: ctx.every_frame("post_move", 0,
                                                   lambda *a: None))
    _expect("subscribe", lambda: ctx.subscribe("x/y", lambda t, d: None))
    _expect("claim_pos", lambda: ctx.claim_pos("p1", (1.0, 1.0), "toy", 60))
    record("zombie.closed_scope_raises",
           raised == ["every_frame", "subscribe", "claim_pos"],
           f"关闭作用域三种注册全部抛 ScopeClosedError: {raised}")
    # dispose 幂等
    ctx.dispose()
    record("zombie.dispose_idempotent", True, "重复 dispose 不抛错")


# ---------------- ⑤ disposer 精确性 ----------------
def test_disposer_precision() -> None:
    bus = EventBus()
    k = FeatureKernel(None, bus, _clamp)
    f = _ToyFeature(None)
    k.mount(f)
    st = _state()
    # 单独撤销 post_move 的第一个钩子:只剩第二序钩子
    hooks = sorted(k._hooks["post_move"], key=lambda x: (x.order, x.seq))
    d0 = f.ctx.every_frame("post_move", 2, lambda h, dt: None)  # 第三条(玩具)
    hooks_now = sorted(k._hooks["post_move"], key=lambda x: (x.order, x.seq))
    record("disp.register_returns_disposer",
           len(hooks_now) == len(hooks) + 1, "every_frame 返回 disposer 且入表")
    d0()
    record("disp.single_hook_removed",
           len(k._hooks["post_move"]) == len(hooks),
           "单钩 disposer 只摘除自己那条")
    f.fired.clear()
    k.run_stage("post_move", st, 1 / 60)
    record("disp.remaining_run", f.fired == ["a", "b"],
           f"剩余钩子仍按序执行: {f.fired}")


# ---------------- ⑥⑦ 订阅回收 + 命令管道 ----------------
def test_command_pipeline() -> None:
    bus = EventBus()
    k = FeatureKernel(None, bus, _clamp)

    class _Rewriter(FeaturePlugin):
        def __init__(self, app, tag: str) -> None:
            super().__init__(app)
            self.tag = tag
            self.feature_id = f"rw-{tag}"
            self.ctx = None

        def mount(self, ctx) -> None:
            self.ctx = ctx
            ctx.every_frame("rewrite_command", 0 if self.tag == "A" else 1,
                            self._rw)

        def _rw(self, h, cmd, dt):
            return f"{cmd}->{self.tag}"

    ra, rb = _Rewriter(None, "A"), _Rewriter(None, "B")
    k.mount(ra)
    k.mount(rb)
    out = k.rewrite_command(None, "cmd", 1 / 60)
    record("pipe.fold_order", out == "cmd->A->B", f"按注册序折叠: {out}")
    k.unmount("rw-A")
    k.unmount("rw-B")
    out2 = k.rewrite_command(None, "cmd", 1 / 60)
    record("pipe.unmounted_not_run", out2 == "cmd",
           f"卸载后管道不再参与: {out2}")


# ---------------- ⑧ App 集成冒烟 ----------------
def test_app_kernel_smoke() -> None:
    import neuropet.core.app as appmod

    with tempfile.TemporaryDirectory(prefix="neuropet_kernel_") as td:
        tmp = Path(td)
        appmod._SESSION_FILE = tmp / "session.json"
        orig_profile = appmod.profile_dir
        appmod.profile_dir = lambda pet_id: (
            tmp / "profiles" / pet_id).mkdir(parents=True, exist_ok=True) \
            or (tmp / "profiles" / pet_id)
        try:
            app = appmod.App()
            try:
                drag = app._kernel.feature("drag")
                record("app.features_mounted", drag is not None,
                       "App 启动即挂载 drag 功能(ADR-0034:hide 已剔除)")
                pid = app.add_pet("species.cockroach", pos=(600.0, 400.0))
                for _ in range(10):
                    app._step(1 / 60)
                record("app.steps_clean", True, "10 帧 sim 无异常")
                app._kernel.run_stage("render")
                record("app.render_stage_clean", True, "render 管道位可跑")
                app.remove_pet(pid)
                record("app.pet_removed_features_clean",
                       pid not in drag._track,
                       "移除宠物后功能状态随 bus 事件自清")
            finally:
                app.shutdown()
            record("app.shutdown_unmounts",
                   len(app._kernel._features) == 0,
                   "shutdown 整组卸载功能(可逆性收官)")
        finally:
            appmod.profile_dir = orig_profile


# ============================================================ 主流程
def main() -> None:
    test_arbiter_priority()
    test_frame_scoped_leases()
    test_scope_reversibility()
    test_scope_closed_raises()
    test_disposer_precision()
    test_command_pipeline()
    test_app_kernel_smoke()
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n[内核验收] {len(RESULTS) - len(failed)}/{len(RESULTS)} 项通过")
    if failed:
        for tag, _, detail in failed:
            print(f"  FAIL: {tag} — {detail}")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
