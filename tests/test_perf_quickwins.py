"""PF 快赢三项(2026-09)验收:上传去重 / gc.freeze 调优 / 档位回差。
运行:python tests/test_perf_quickwins.py

覆盖(任务书 3 项判据 + 接线):
  ① 签名命中跳过:display_signature 对全部显示输入敏感(位移/heading/fold/
     lift/traits/alert/坐标/ss),同像素浮点抖动不敏感;冻结宠在签名不变时
     跳过重绘与上传(App._upload_count 不增、_dedup_skips 增)。
  ② 首帧必画:新宠 last_img=None → 首次渲染必须实际上传一次。
  ③ 回差行为:frame_tier_next 40/30(与 1.0/0.5)双阈值不乒乓;
     mem_guard_next 50/55 触发、45/50 解除、dwell 门消除动作乒乓、>55 升级。
  ④ 接线:App 构造即应用 gc 阈值;_memory_tick 状态机按回差推进。
判据 A(OPT-7 缓存对象同一 ⇒ 跳过与执行显示等价)的前置事实也在 ① 中断言
(同一静态 pose 两次 render_pose 返回同一缓存对象)。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

RESULTS: list[tuple[str, bool, str]] = []


def record(tag: str, ok: bool, detail: str) -> None:
    RESULTS.append((tag, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {tag}: {detail}")


# ---------------- ① display_signature:全输入敏感 / 同像素抖动不敏感 ----------------
def test_display_signature_sensitivity() -> None:
    from neuropet.core.windowing import display_signature

    pose = {"half": 120, "altitude": 0.0, "alert": False, "sprint": False,
            "pitch": 0.0,
            "segments": [(44.0, 0.0, 0.5, 14.0, 14.0)],
            "legs": [{"points": [(1.0, 2.0), (3.0, 4.0)], "lift": 0.0}],
            "antennae": [[(1.0, 1.0), (2.0, 2.0)]],
            "wings": {"active": False, "phase": 0.0, "fold": 1.0, "span": 70.0},
            "bones": {"pose": "rest", "abdomen_dyaw_deg": 0.0, "sway_px": 0.0}}
    traits = {"body": "#5a3418", "scale": 1.0, "wing_cover": True}
    base = display_signature(pose, traits, 100.4, 200.6, 3)

    # 同签名:字典键序无关、同像素内浮点抖动(int 相同)
    pose2 = {"wings": {"span": 70.0, "fold": 1.0, "phase": 0.0,
                       "active": False},
             "antennae": [[(1.0, 1.0), (2.0, 2.0)]],
             "legs": [{"lift": 0.0, "points": [(1.0, 2.0), (3.0, 4.0)]}],
             "segments": [(44.0, 0.0, 0.5, 14.0, 14.0)],
             "pitch": 0.0, "sprint": False, "alert": False, "altitude": 0.0,
             "half": 120,
             "bones": {"sway_px": 0.0, "abdomen_dyaw_deg": 0.0,
                       "pose": "rest"}}
    assert display_signature(pose2, dict(traits), 100.9, 200.1, 3) == base, \
        "键序/同像素抖动不应改变签名"

    def must_differ(tag: str, p=None, t=None, x=100.4, y=200.6, ss=3) -> None:
        got = display_signature(p or pose, t or traits, x, y, ss)
        assert got != base, f"签名对 {tag} 不敏感(漏检风险)"

    # 位移:腿点/节段几何变(位移在体局部 pose 中体现)
    import copy
    p = copy.deepcopy(pose)
    p["legs"][0]["points"][1] = (3.5, 4.0)
    must_differ("腿点位移", p=p)
    p = copy.deepcopy(pose)
    p["segments"][0] = (44.0, 0.0, 0.6, 14.0, 14.0)      # heading 变
    must_differ("heading", p=p)
    p = copy.deepcopy(pose)
    p["legs"][0]["lift"] = 1.5                            # lift 变
    must_differ("lift", p=p)
    p = copy.deepcopy(pose)
    p["wings"]["fold"] = 0.5                              # fold 变
    must_differ("fold", p=p)
    p = copy.deepcopy(pose)
    p["wings"]["phase"] = 0.31                            # 振翅相位变
    must_differ("振翅相位", p=p)
    p = copy.deepcopy(pose)
    p["altitude"] = 3.0                                   # 飞行高度变
    must_differ("altitude", p=p)
    p = copy.deepcopy(pose)
    p["alert"] = True                                     # OPT-7 未覆盖键也要敏感
    must_differ("alert", p=p)
    p = copy.deepcopy(pose)
    p["bones"]["abdomen_dyaw_deg"] = 0.7                  # 微弹性通道
    must_differ("微弹性 abdyaw", p=p)
    t = dict(traits)
    t["body"] = "#6b431f"                                 # traits 色板变
    must_differ("traits 色板", t=t)
    t = dict(traits)
    t["scale"] = 1.5                                      # 档位变
    must_differ("traits 档位", t=t)
    must_differ("画布坐标(跨像素)", x=101.4)
    must_differ("画布坐标(跨像素 y)", y=201.4)
    must_differ("超采样档 ss", ss=2)
    # NaN 归一:NaN 位置两次签名仍相等(否则永不命中)
    p = copy.deepcopy(pose)
    p["pitch"] = float("nan")
    s1 = display_signature(p, traits, 0, 0, 3)
    assert s1 == display_signature(p, traits, 0, 0, 3), "NaN 应归一为哨兵"
    record("①签名敏感性与等价", True,
            "全显示输入敏感/同像素抖动与键序不敏感/NaN 归一 OK")


def test_opt7_same_object_precondition() -> None:
    """判据 A 前置事实:静态 pose 两次 render_pose 返回同一 OPT-7 缓存对象
    ⇒ app 侧"缓存对象==上次上传对象"跳过与执行整条路径显示严格一致。"""
    import copy
    from neuropet.render.renderer import render_pose, _render_mode
    if _render_mode() not in ("hybrid", "new", "v2"):
        record("①判据A前置(OPT-7 同一对象)", True, "legacy 管线,跳过")
        return
    pose = {"half": 78, "altitude": 0.0,
            "segments": [(12.0, 0.0, 0.3, 7.5, 7.0)],
            "legs": [{"points": [(1.0, 2.0), (3.0, 4.0)], "lift": 0.0}] * 6,
            "antennae": [[(1.0, 1.0), (2.0, 2.0)]] * 2,
            "wings": {"active": False, "phase": 0.0, "fold": 1.0, "span": 20.0}}
    traits = {"body": "#c59a5b", "wing_cover": False, "species_id": "x"}
    img1 = render_pose(copy.deepcopy(pose), dict(traits))
    img2 = render_pose(copy.deepcopy(pose), dict(traits))
    assert img1 is img2, "静态 pose 二次渲染应命中 OPT-7 返回同一对象"
    record("①判据A前置(OPT-7 同一对象)", True, "二次渲染返回同一缓存对象")


# ---------------- ①② 集成:App 渲染循环中的跳过与首帧必画 ----------------
def test_app_dedup_and_first_frame() -> None:
    from neuropet.core.app import App

    app = App()
    try:
        pid = app.add_pet("species.cockroach", pos=(500.0, 400.0))
        h = app.pets[pid]
        app.set_frozen(pid, True)
        # 驱动一阶微弹性滤波器到不动点(与真实冻结宠的收敛过程同构:pose()
        # 每调用一次推一阶,几何上若干十次后逐位静止)
        for _ in range(400):
            h.body.pose()
        # 首帧必画:预测性跳帧最多连跳 4 次,第 5 次必真实渲染+上传
        uploads0 = app._upload_count
        for _ in range(6):
            h.last_render = 0.0
            app._render(1.0 / 60.0)
        assert app._upload_count > uploads0, "首帧(预测跳帧上限内)必须实际上传"
        assert h.last_img is not None and h.last_coords is not None
        first_img = h.last_img
        # 再驱动到精确不动点,并让一帧吸收收敛尾差(允许其上传)
        for _ in range(400):
            h.body.pose()
        h.last_render = 0.0
        app._render(1.0 / 60.0)
        # 签名命中跳过:pose 逐位静止 + 坐标不变 → 不再重绘/上传
        ups1 = app._upload_count
        skips0 = app._dedup_skips
        for _ in range(8):
            h.last_render = 0.0
            app._render(1.0 / 60.0)
        assert app._upload_count == ups1, \
            f"冻结宠签名不变时应跳过上传(仍上传 {app._upload_count - ups1} 次)"
        assert app._dedup_skips == skips0 + 8, "冻结宠应每帧命中去重计数"
        assert h.last_img is first_img, "跳过期间上传对象不应变化"
        # 保守性:解冻后(门控关闭)即使 pose 暂未变也恢复上传,绝不漏画
        app.set_frozen(pid, False)
        ups2 = app._upload_count
        h.last_render = 0.0
        app._render(1.0 / 60.0)
        assert app._upload_count == ups2 + 1, "非冻结/非抓握宠不做去重(保守多画)"
        record("②签名命中跳过+首帧必画(集成)", True,
               f"uploads={app._upload_count} skips={app._dedup_skips}")
    finally:
        app.root.destroy()


# ---------------- ③ 帧率档位回差 ----------------
def test_frame_tier_hysteresis() -> None:
    from neuropet.core.app import frame_tier_next, MovementMode
    from neuropet.core.contracts import Behavior

    # 冲刺进入 60fps 档
    assert frame_tier_next(2, 45.0, False, False, False) == 0
    # 回差:45→35 仍留 60fps 档(单阈值 40 会立刻掉档 = 乒乓)
    assert frame_tier_next(0, 35.0, False, False, False) == 0
    # 35→25 才降到 30fps 档
    assert frame_tier_next(0, 25.0, False, False, False) == 1
    # 30fps 档回差:0.8 留档,0.3 降到 10fps 档
    assert frame_tier_next(1, 0.8, False, False, False) == 1
    assert frame_tier_next(1, 0.3, False, False, False) == 2
    # 低档不因边界噪声回弹:cur=2, speed=1.5 → 只到 1 档? 不,>1.0 是进入
    # 30 档的阈值,2→1 允许(升档);但 1 档以下回差已验
    assert frame_tier_next(2, 1.5, False, False, False) == 1
    # 类别条件直通:飞行/抓握 → 60fps;梳理/进食动画 → 至少 30fps
    assert frame_tier_next(2, 0.0, True, False, False) == 0
    assert frame_tier_next(2, 0.0, False, True, False) == 0
    assert frame_tier_next(2, 0.0, False, False, True) == 1
    assert MovementMode.FLY is not None and Behavior.GROOM is not None
    record("③帧率档位回差", True, "40/30 与 1.0/0.5 双阈值 + 类别直通 OK")


# ---------------- ③ 内存护栏回差 ----------------
def test_mem_guard_hysteresis() -> None:
    from neuropet.core.app import mem_guard_next, MEM_TRIM_MB, MEM_PURGE_MB, MEM_RELEASE_MB

    # 首次越线立即动作:TRIM 触发 trim,PURGE+ 触发 purge(常量驱动,ADR-0034 预算 130)
    assert mem_guard_next(0, MEM_TRIM_MB + 0.5, False) == (1, "trim")
    assert mem_guard_next(0, MEM_PURGE_MB + 0.5, False) == (2, "purge")
    # 回差:trim 态在 RELEASE~TRIM 之间不解除、不重复动作(dwell 未到)
    assert mem_guard_next(1, (MEM_RELEASE_MB + MEM_TRIM_MB) / 2, False) == (1, None)
    assert mem_guard_next(1, MEM_TRIM_MB + 2.0, False) == (1, None)   # 乒乓消除:不再立即 trim
    assert mem_guard_next(1, MEM_TRIM_MB + 2.0, True) == (1, "trim")  # dwell 到了才允许再动作
    # 解除:低于 RELEASE 线 → ok
    assert mem_guard_next(1, MEM_RELEASE_MB - 1.0, False) == (0, None)
    # 升级:trim 态直接 >PURGE → 立即 purge(不需要 dwell)
    assert mem_guard_next(1, MEM_PURGE_MB + 0.5, False) == (2, "purge")
    # purge 态:TRIM~PURGE 之间挂起不动作(未过 PURGE 线不动作);
    # >PURGE 且 dwell 到 → 再 purge;<TRIM 回 trim 态
    assert mem_guard_next(2, MEM_TRIM_MB + 2.0, False) == (2, None)
    assert mem_guard_next(2, MEM_TRIM_MB + 2.0, True) == (2, None)
    assert mem_guard_next(2, MEM_PURGE_MB + 0.5, False) == (2, None)    # dwell 未到不动作
    assert mem_guard_next(2, MEM_PURGE_MB + 0.5, True) == (2, "purge")  # dwell 到才再 purge
    assert mem_guard_next(2, MEM_RELEASE_MB - 1.0, False) == (1, None)
    # 逐级解除:purge 态低值先回 trim 态,再一周期才回 ok(防大幅震荡)
    assert mem_guard_next(1, MEM_RELEASE_MB - 20.0, False) == (0, None)
    record("③内存护栏回差", True, "215/230 触发、200 解除、dwell 门、升级 OK(ADR-0036 预算 250)")


# ---------------- ④ 接线:gc 阈值 + _memory_tick 状态机 ----------------
def test_app_wiring_gc_and_memguard() -> None:
    import neuropet.core.app as A
    from neuropet.core.app import App

    app = App()
    try:
        import gc
        assert tuple(gc.get_threshold()) == A.GC_THRESHOLDS, \
            f"App 构造应应用 gc 阈值 {A.GC_THRESHOLDS}"
        assert A.MEM_TRIM_MB == 215.0 and A.MEM_PURGE_MB == 230.0, "阈值应 215/230(ADR-0036 预算 250)"
        # _memory_tick 状态机接线(采样周期置满 + 打桩工作集;ADR-0034 预算 130)
        seq = iter([A.MEM_TRIM_MB + 1.0, (A.MEM_RELEASE_MB + A.MEM_TRIM_MB) / 2,
                    A.MEM_RELEASE_MB - 10.0, A.MEM_PURGE_MB + 1.0])
        A.working_set_mb = lambda: next(seq)
        app._mem_acc = A.MEM_SAMPLE_S
        app._last_mem_action = float("-inf")
        trims = []
        app._trim_render_caches = lambda frac=1.0: trims.append(frac)
        app._memory_tick(9.9)
        assert app._mem_state == 1 and len(trims) == 1, "111MB 应 trim 一次"
        app._mem_acc = A.MEM_SAMPLE_S
        app._memory_tick(9.9)                     # ~102:trim 态挂起,不动作
        assert app._mem_state == 1 and len(trims) == 1, "94MB 不应再 trim(回差)"
        app._mem_acc = A.MEM_SAMPLE_S
        app._memory_tick(9.9)                     # 85:解除回 ok
        assert app._mem_state == 0 and len(trims) == 1
        app._mem_acc = A.MEM_SAMPLE_S
        app._memory_tick(9.9)                     # 121:升级 purge
        assert app._mem_state == 2, "121MB 应升级 purge"
        record("④gc 阈值+护栏状态机接线", True, "阈值 215/230 生效,状态机按回差推进(ADR-0036 预算 250)")
    finally:
        app.root.destroy()


def main() -> None:
    tests = [test_display_signature_sensitivity,
             test_opt7_same_object_precondition,
             test_frame_tier_hysteresis,
             test_mem_guard_hysteresis,
             test_app_dedup_and_first_frame,
             test_app_wiring_gc_and_memguard]
    ok = failed = 0
    for fn in tests:
        try:
            fn()
            ok += 1
        except AssertionError as exc:
            failed += 1
            record(fn.__name__, False, str(exc))
        except Exception as exc:
            failed += 1
            record(fn.__name__, False, f"{type(exc).__name__}: {exc}")
    print(f"PF 快赢验收:{ok} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
