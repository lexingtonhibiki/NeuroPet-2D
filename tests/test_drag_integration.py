# -*- coding: utf-8 -*-
"""拖拽力学集成验收(ADR-0031/0032;踩坑 #13"离线全绿≠可交付"的制度化)。

直接驱动 App._step / _on_press/_on_drag/_on_release 真实管线,验收拖拽
carried 姿态与松手抛掷(ADR-0032;用户裁决"关节式拖拽,停即恢复标准俯视
图态"):

  E3 F3 单元面:DragDynamics 匀速输入稳态偏转 ∝ −v、静止回归、旧签名兼容。
  F 抓握跟随:held 期 pos 逐帧跟随拖拽意向(仲裁器 drag 90 生效)。
  G 冻结拖拽(用户需求 #5):冻结宠 press 不置 held,拖拽经 frozen_tick
    位置意向合成(实机探查确认的路径)。
  H carried 姿态:拖拽中足端重锚定到当前体下静息位(旧版钉在抓取前世界
    坐标),甩动由摆角通道表达关节感;鼠标停 2s 恢复标准姿态;held 期甩动
    翻面观测(ADR-0034:翻面已退役,保留摆角观测路径断言)。
  I 松手抛掷惯性:末速度交棒 body 惯性滑行(τ=0.35s),强抛触发翻面
    (ADR-0034:翻面退役后本判据改为断言滑行与外拖观测)。

运行:python tests/test_hide_drag_integration.py(断言脚本直跑)
"""
from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

RESULTS: list[tuple[str, bool, str]] = []
DT = 1.0 / 60.0


def record(tag: str, ok: bool, detail: str) -> None:
    RESULTS.append((tag, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {tag}: {detail}")


class _Evt:
    """最小 tk 事件桩(x/y 物理像素)。"""

    def __init__(self, x: float, y: float) -> None:
        self.x, self.y = x, y


def _make_app(tmp: Path):
    import neuropet.core.app as appmod

    appmod._SESSION_FILE = tmp / "session.json"
    orig_profile = appmod.profile_dir

    def _tmp_profile(pet_id: str) -> Path:
        d = tmp / "profiles" / pet_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    appmod.profile_dir = _tmp_profile
    app = appmod.App()
    app.cfg.max_pets = 6
    return app, appmod, orig_profile


# ============================================================
# E3 单元面:DragDynamics 匀速输入稳态(速度驱动 F3)
# ============================================================
def test_f3_velocity_drive_unit() -> None:
    from neuropet.physics.drag import DragDynamics

    dd = DragDynamics(0.8)
    for _ in range(240):
        p = dd.step(DT, 0.0, 0.0, 300.0, 0.0)
    ss = max(abs(v) for v in p.leg_swings)
    expect = 0.015 * 2.0 * 300.0
    record("E3.unit_steady_state", abs(ss - expect) <= 0.1,
           f"匀速稳态偏转 {ss:.2f}° ≈ S·V_DRIVE·v = {expect:.2f}°(解析一致)")
    for _ in range(240):
        p = dd.step(DT, 0.0, 0.0)
    ss0 = max(abs(v) for v in p.leg_swings)
    record("E3.unit_rest_returns", ss0 <= 0.5,
           f"静止 4s 后偏转 {ss0:.3f}° ≤ 0.5°(v→0 自然回归)")
    dd2 = DragDynamics(0.8)
    for _ in range(240):
        p2 = dd2.step(DT, 4000.0, 0.0)
    record("E3.unit_backward_compat",
           abs(max(abs(v) for v in p2.leg_swings) - 0.015 * 4000.0) <= 0.1,
           "旧签名 step(dt, ax, ay) 稳态逐位兼容")


# ============================================================
# G:冻结拖拽
# ============================================================
def test_frozen_drag_via_intent(tmp: Path) -> None:
    app, appmod, orig = _make_app(tmp)
    try:
        pid = app.add_pet("species.cockroach", pos=(600.0, 400.0))
        h = app.pets[pid]
        app.set_frozen(pid, True)
        record("G.frozen_set", h.state.frozen is True and h.state.held is False,
               "冻结生效(held 保持 False)")
        app._drag_pid = None
        h.drag_offset = (0.0, 0.0)
        app._on_press(_Evt(600.0, 400.0))
        record("G.press_no_held", h.state.held is False,
               "冻结宠 press 不进入 held(frozen 分支语义)")
        for dx, dy in ((20.0, 0.0), (20.0, 10.0), (25.0, 15.0), (30.0, 20.0)):
            app._on_drag(_Evt(600.0 + dx * 3.0, 400.0 + dy * 3.0))
            app._step(DT)
        drag = app._kernel.feature("drag")
        ip = drag._intent.get(pid)
        record("G.intent_routed", ip is not None,
               f"拖拽意向经 _on_drag 进入 DragFeature: {ip}")
        app._step(DT)
        d_target = math.hypot(h.state.pos[0] - ip[0], h.state.pos[1] - ip[1]) \
            if ip else 1e9
        record("G.frozen_pos_follows", d_target <= 1.0,
               f"冻结宠位置跟随意向(偏差 {d_target:.3f}px)")
        app._on_release(_Evt(*ip))
        app._step(DT)
        record("G.release_clean", drag._intent.get(pid) is None,
               "释放后意向清空")
    finally:
        app.root.destroy()
        appmod.profile_dir = orig


# ============================================================
# H/I:carried 姿态 + 松手抛掷
# ============================================================
def test_drag_carried_and_fling(tmp: Path) -> None:
    app, appmod, orig = _make_app(tmp)
    try:
        pid = app.add_pet("species.cockroach", pos=(500.0, 500.0))
        h = app.pets[pid]
        body = h.body
        homes = [leg["home"] for leg in body.p["legs"]]

        def _foot_err() -> list[float]:
            pose = body.pose()
            ch, sh = math.cos(h.state.heading), math.sin(h.state.heading)
            out = []
            for i, leg in enumerate(pose["legs"]):
                hx, hy = homes[i]
                rest = (hx * ch - hy * sh, hx * sh + hy * ch)
                foot = leg["points"][-1]
                out.append(math.hypot(foot[0] - rest[0], foot[1] - rest[1]))
            return out

        h.drag_offset = (0.0, 0.0)
        app._drag_pid = pid
        app._on_press(_Evt(500.0, 500.0))
        record("H.grabbed", h.state.held is True, "抓取生效")
        for i in range(1, 61):                    # 600px 拖拽(600px/s)
            app._on_drag(_Evt(500.0 + i * 10.0, 500.0))
            app._step(DT)
        errs = _foot_err()
        record("H.feet_reanchored_during_drag", max(errs) <= 90.0,
               f"拖拽 600px 后足端仍锚在当前体下静息位 ±90px"
               f"(max {max(errs):.1f}px;修复前指向抓取前锚点 ≈470px)")
        for _ in range(120):                      # 停 2s → 标准姿态
            app._on_drag(_Evt(1100.0, 500.0))
            app._step(DT)
        errs_stop = _foot_err()
        dp = app._kernel.feature("drag").pose_of(h)
        max_swing = max(abs(v) for v in dp.leg_swings)
        record("H.recovers_standard_stance",
               max(errs_stop) <= 12.0 and max_swing <= 2.0,
               f"停 2s 后足端偏差 max {max(errs_stop):.2f}px ≤12、"
               f"摆角 {max_swing:.2f}° ≤2°(标准俯视图态)")
        # held 期持续猛拽:加速度驱动的关节响应(单方向 2400px/s²)
        app._on_drag(_Evt(1100.0, 500.0))
        app._step(DT)
        peaks = []
        for i in range(1, 11):
            app._on_drag(_Evt(1100.0 + i * 40.0, 500.0))
            app._step(DT)
            dp = app._kernel.feature("drag").pose_of(h)
            if dp is not None:
                peaks.append(max(abs(v) for v in dp.leg_swings))
        record("H.yank_drives_joints",
               bool(peaks) and max(peaks) >= 8.0,
               f"held 期持续猛拽 → 摆角峰值 {max(peaks) if peaks else 0:.1f}° ≥8°"
               f"(关节惯性响应;a 通道)")
        release_pos = tuple(h.state.pos)
        app._on_release(_Evt(release_pos[0], release_pos[1]))
        record("H.released_clean", h.state.held is False, "释放正常")

        # ---- I:松手抛掷(先甩出速度再释放) ----
        app._on_press(_Evt(release_pos[0], release_pos[1]))
        for i in range(1, 16):                    # 15px/帧 = 900px/s
            app._on_drag(_Evt(release_pos[0] + i * 15.0, release_pos[1]))
            app._step(DT)
        app._on_release(_Evt(release_pos[0] + 225.0, release_pos[1]))
        record("I.fling_handed_off", body._fling is not None
               and math.hypot(*body._fling) > 500.0,
               f"松手把末速度交给身体: fling={body._fling}")
        moved = 0.0
        for _ in range(30):
            prev = tuple(h.state.pos)
            app._step(DT)
            moved += math.hypot(h.state.pos[0] - prev[0],
                                h.state.pos[1] - prev[1])
        record("I.glides_after_release", moved >= 150.0,
               f"释放后惯性滑行 {moved:.0f}px ≥150px(非瞬间钉死)")
        record("I.fling_decays", body._fling is None
               or math.hypot(*body._fling) < 500.0,
               f"0.5s 后 fling 衰减: {body._fling}(指数 τ=0.35s)")
    finally:
        app.root.destroy()
        appmod.profile_dir = orig


# ============================================================ 主流程
def main() -> None:
    with tempfile.TemporaryDirectory(prefix="neuropet_drag_") as td:
        tmp = Path(td)
        test_f3_velocity_drive_unit()
        test_frozen_drag_via_intent(tmp)
        test_drag_carried_and_fling(tmp)
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n[拖拽集成验收] {len(RESULTS) - len(failed)}/{len(RESULTS)} 项通过")
    if failed:
        for tag, _, detail in failed:
            print(f"  FAIL: {tag} — {detail}")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
