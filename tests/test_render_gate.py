"""r25 单元 1:渲染门记账回滚验收(A-1 = T2)。运行:python tests/test_render_gate.py

被测承诺(`neuropet/core/app.py::_render` 门序):**因躯干桶未缓存而「预测跳帧」
的那次 `continue` 不吃掉配额槽位、也不重置自己的延迟钟** ⇒ 该宠下一 tick
立即重试,而不是再等一整个 interval(16/33/100ms 档)。

判据(确定性、进程内、**不依赖时序** —— 用虚拟时钟钉死 `now`,与真实耗时无关):
  ① 被 prewarm 门跳过时,该宠 `h.last_render` **逐位不变**(延迟钟未被重置);
  ② 同 tick **后序宠照常渲染**(配额槽位没被空转吃掉;render_pose 打桩计数);
  ③ **下一 tick 该宠立即重试**(不再被 interval 门挡一整个 interval)。
  ④⑤ **记账位置钉死**(F-1):上传去重门(`app.py:1364-1377` 那两条 `continue`)命中时,
     记账与 r25 改动**前**逐位一致 —— 仍**吃**一个配额槽位(④)、仍把该宠
     `h.last_render` 重置为本 tick 的 `now`(⑤)。⇒ 谁把 `app.py:1347/:1348` 两行再往
     **后**挪到 dedup 门之后,④⑤**必红**(①②③仍绿);挪回改动前的位置(预测跳帧
     之前)则④⑤不变(那条路径本来就没动过 ⇒ 这正是"位置"而非"行为"的判据)。

构造:3 宠(蟑 + 蝇 ×2;`RENDER_QUOTA=2` ⇒ 配额门参与),把
`neuropet.render.torso_art.has_bucket` 打桩为恒 False(该宠的桶永远缺),
把 `neuropet.render.renderer.render_pose` 打桩计数(真渲在下面跑,只包一层计数)。
④⑤ 另起一套 3 蝇夹具(无蟑 ⇒ prewarm 门不参与),把 dedup 门的记账单独拎出来。

⚠ `quota_left` 是 `_render` 的**局部变量,外部读不到** ⇒ 断言只落在**可观测后果**
  (`h.last_render` 逐位比较 + render_pose 调用计数)上;不许为了可观测去改产品代码。
⚠ 光标输入缝钉到屏外、名册/profile/会话打进临时目录(与 `tools/u0_baseline.py::
  _make_app` 同惯例):夹具不碰仓库 `data/`,也不让外部世界决定红绿。
"""
from __future__ import annotations

import sys
import tempfile
import time as _real_time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

RESULTS: list[tuple[str, bool, str]] = []

PIN_CURSOR = (-4000.0, -4000.0)   # 屏外:所有光标通道的 d < *_R 门全关


def record(tag: str, ok: bool, detail: str) -> None:
    RESULTS.append((tag, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {tag}: {detail}")


class _VirtualClock:
    """虚拟 `perf_counter`(替换 `neuropet.core.app.time`)。

    `_render` 只经模块属性读 `time.perf_counter()`(`app.py:1276/:1277/:1379/:1381/:1386`,5 处)
    ⇒ 换成它之后,interval 门的判定完全由本测试给定,**与两次 tick 真实隔了
    多少毫秒无关**。不钉死就等于把红绿交给机器负载(工作流 §5「判据隐式依赖
    未受控的环境输入」同族),而本判据要分辨的差值恰好是 ms 级。
    """

    def __init__(self, t0: float) -> None:
        self.t = float(t0)

    def perf_counter(self) -> float:
        return self.t

    def __getattr__(self, name):             # 未用到的接口透传真模块(防御)
        return getattr(_real_time, name)


TICK = 1.0 / 60.0
CLOCK_T0 = 1000.0        # 虚拟时钟原点(远大于任何 interval,且不撞 0)
SEED = 0.0               # 各宠 last_render 的哨兵「很久没渲染」:1000 - 0 ≫ 0.1s
INTERVAL = 0.033         # 档位钉 1(=30fps);3 宠时 desired 被封顶 ≥1,档位只可能是 1


def test_prewarm_skip_rollback() -> None:
    from neuropet.core import app as appmod
    from neuropet.core.app import App, RENDER_QUOTA
    from neuropet.render import renderer, torso_art

    assert RENDER_QUOTA == 2, f"本判据按 RENDER_QUOTA=2 构造(现值 {RENDER_QUOTA})"

    calls: list[str] = []                    # render_pose 打桩计数(按 species_id 归属)
    clock = _VirtualClock(CLOCK_T0)
    orig_rp = renderer.render_pose
    orig_hb = torso_art.has_bucket
    orig_time = appmod.time
    orig_session = appmod._SESSION_FILE
    orig_profdir = appmod.profile_dir

    def counting_render_pose(pose, traits):
        calls.append(str(traits.get("species_id")))
        return orig_rp(pose, traits)         # 真渲照跑,只包一层计数

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        appmod._SESSION_FILE = tmp / "session.json"

        def _prof(pid: str) -> Path:
            d = tmp / "profiles" / pid
            d.mkdir(parents=True, exist_ok=True)
            return d
        appmod.profile_dir = _prof
        app = App()
        try:
            app._pets_path = tmp / "pets.json"
            app._cursor_getter = lambda: PIN_CURSOR
            appmod.time = clock
            torso_art.has_bucket = lambda *a, **kw: False    # 该宠的桶永远缺
            renderer.render_pose = counting_render_pose
            app._prewarm_on = True                            # 默认 hybrid 即真;钉住被测分支

            assert len(app.pets) == 0, "App() 构造不应预载名册"
            assert app.cfg.max_pets >= 3, \
                f"本判据需 3 宠(cfg.max_pets={app.cfg.max_pets})"
            # 3 宠:蟑(轮转序队首)+ 蝇 ×2(后序),彼此远离 ⇒ 不走重叠合成
            roach_pid = app.add_pet("species.cockroach", pos=(360.0, 320.0))
            app.add_pet("species.fruitfly", pos=(900.0, 320.0))
            app.add_pet("species.fruitfly", pos=(1400.0, 320.0))
            h_roach = app.pets[roach_pid]
            assert "cockroach" in h_roach.state.species_id
            for h in app.pets.values():
                h.last_render = SEED
                h.frame_tier = 1                 # interval = 0.033
                h.tier_since = clock.t           # 驻留锁:本测试期内档位不变
                h.last_cost_ms = 0.0             # 无重帧退避
                h.cost_backoff = 0
                h.skip_streak = 0
            app._render_cursor = 0               # 轮转序 = [0,1,2]:蟑在队首

            # ---- tick 1:蟑的桶缺 ⇒ 进 prewarm 门被预测跳帧 ----
            n0 = len(calls)
            clock.t = CLOCK_T0
            app._render(TICK)
            tick1 = calls[n0:]
            record("①prewarm 跳过不重置延迟钟", h_roach.last_render == SEED,
                   f"h.last_render={h_roach.last_render!r}(跳过前 = {SEED!r})"
                   f" skip_streak={h_roach.skip_streak} 真渲={tick1}")
            record("②同 tick 后序宠照常渲染", len(tick1) == 2,
                   f"tick1 render_pose 次数={len(tick1)}(期望 2 = 配额 2 全给后序宠;"
                   f"物种={tick1})")

            # ---- tick 2:虚拟时钟只 +1ms(< interval);桶已备好 ⇒ 应立即重试 ----
            torso_art.has_bucket = lambda *a, **kw: True      # 预热线程已备好该桶
            n1 = len(calls)
            clock.t = CLOCK_T0 + 0.001
            app._render(TICK)
            tick2 = calls[n1:]
            n_roach2 = sum(1 for c in tick2 if "cockroach" in c)
            record("③下一 tick 立即重试", n_roach2 == 1,
                   f"tick2 蟑 render_pose 次数={n_roach2}(期望 1);"
                   f"tick2 全量={tick2};虚拟时差 0.001s < interval {INTERVAL}s"
                   f"(当时已渲染过的宠仍应被 interval 挡)")
        finally:
            renderer.render_pose = orig_rp
            torso_art.has_bucket = orig_hb
            appmod.time = orig_time
            appmod._SESSION_FILE = orig_session
            appmod.profile_dir = orig_profdir
            try:
                app.root.destroy()
            except Exception:                             # noqa: BLE001
                pass


def test_dedup_skip_accounting_unchanged() -> None:
    """④⑤:上传去重 `continue` 上的记账(r25 修复轮 1 F-1;位置判据)。

    3 蝇(`RENDER_QUOTA=2` ⇒ 配额门参与;**无蟑 ⇒ prewarm 门不参与**,把 dedup 单独
    拎出来):队首那只 `st.held=True` + `last_img`/`last_coords`/`last_disp_sig` 备齐
    ⇒ 本 tick 必在 dedup 门 `continue`(`_dedup_skips` +1,④⑤都把它当**前提**断言,
    否则会是"空转绿")。此后:① 槽位若被它吃掉 1 个 ⇒ 后序 2 只里只 1 只能渲(④);
    ② 它自己的延迟钟被重置为本 tick 的 `now`(⑤)。两条都只在「记账行在 dedup 门
    **之前**」时成立 ⇒ 把 `app.py:1347/:1348` 挪到 dedup 门之后,两条同时红。
    """
    from neuropet.core import app as appmod
    from neuropet.core.app import App, RENDER_QUOTA
    from neuropet.render import renderer

    assert RENDER_QUOTA == 2, f"本判据按 RENDER_QUOTA=2 构造(现值 {RENDER_QUOTA})"

    calls: list[str] = []
    clock = _VirtualClock(CLOCK_T0)
    SENTINEL = object()                      # 桩签名:与 h0.last_disp_sig 同一个对象
    orig_rp = renderer.render_pose
    orig_sig = appmod.display_signature
    orig_time = appmod.time
    orig_session = appmod._SESSION_FILE
    orig_profdir = appmod.profile_dir

    def counting_render_pose(pose, traits):
        calls.append(str(traits.get("species_id")))
        return orig_rp(pose, traits)         # 真渲照跑,只包一层计数

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        appmod._SESSION_FILE = tmp / "session.json"

        def _prof(pid: str) -> Path:
            d = tmp / "profiles" / pid
            d.mkdir(parents=True, exist_ok=True)
            return d
        appmod.profile_dir = _prof
        app = App()
        try:
            app._pets_path = tmp / "pets.json"
            app._cursor_getter = lambda: PIN_CURSOR
            appmod.time = clock
            appmod.display_signature = lambda *a, **kw: SENTINEL   # 恒等 ⇒ 签名必命中
            renderer.render_pose = counting_render_pose
            app._prewarm_on = True                            # dedup 门要求它

            pids = [app.add_pet("species.fruitfly", pos=(x, 320.0))
                    for x in (360.0, 900.0, 1400.0)]
            assert len(app.pets) == 3, f"本判据需 3 宠(现 {len(app.pets)} 只)"
            for h in app.pets.values():
                h.last_render = SEED
                h.frame_tier = 1                 # interval = 0.033(队首那只可能被
                h.tier_since = clock.t           #   抓握档拉到 0 ⇒ 0.016,都远小于 1000)
                h.last_cost_ms = 0.0
                h.cost_backoff = 0
                h.skip_streak = 0
            h0 = app.pets[pids[0]]
            st0 = h0.state
            st0.held = True                      # dedup 门前提(冻结/抓握)
            h0.last_img = object()               # 非 None ⇒ 首帧保护不挡;唯一对象 ⇒ OPT-7 同一性不命中
            h0.last_coords = (int(st0.pos[0]), int(st0.pos[1]))
            h0.last_disp_sig = SENTINEL          # 与桩返回相等 ⇒ dedup 命中的第二次判据
            app._render_cursor = 0               # 轮转序 = [0,1,2]:h0 在队首

            n0, d0 = len(calls), app._dedup_skips
            clock.t = CLOCK_T0
            app._render(TICK)
            tick1 = calls[n0:]
            hit = app._dedup_skips - d0
            record("④dedup 跳过仍吃配额槽位", hit == 1 and len(tick1) == 1,
                   f"dedup_skips +{hit}(需 =1:门确实命中 —— 否则本条与⑤都是空转绿);"
                   f"tick1 render_pose 次数={len(tick1)}(期望 1:2 个槽位被 dedup 吃掉 1 个,"
                   f"只剩 1 个给后序 2 只 ⇒ 队尾那只被 quota 门挡;物种={tick1})")
            record("⑤dedup 跳过仍重置延迟钟", hit == 1 and h0.last_render == CLOCK_T0,
                   f"h0.last_render={h0.last_render!r}(期望 {CLOCK_T0!r} = 本 tick 的 now;"
                   f"改动前该行在 dedup 门之前 ⇒ dedup 命中同样重置)")
        finally:
            renderer.render_pose = orig_rp
            appmod.display_signature = orig_sig
            appmod.time = orig_time
            appmod._SESSION_FILE = orig_session
            appmod.profile_dir = orig_profdir
            try:
                app.root.destroy()
            except Exception:                             # noqa: BLE001
                pass


def main() -> None:
    for fn in (test_prewarm_skip_rollback, test_dedup_skip_accounting_unchanged):
        try:
            fn()
        except Exception as exc:                          # noqa: BLE001
            record(fn.__name__, False, f"{type(exc).__name__}: {exc}")
    bad = [t for (t, ok, _) in RESULTS if not ok]
    print(f"渲染门记账回滚验收:{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
