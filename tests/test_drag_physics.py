"""拖拽动量物理通道判据(A4 拖拽动量物理波;《拖拽物理_翻面_滑翔调研.md》§1/§4)。

与 scratch/_drag_probe.py 同口径:合成拖拽轨迹(app 同款被动观测 —— body.apply
后直接覆写 st.pos)驱动 GenericInsectBody,断言:
  B1 滞后方向 ≤10° / B2 幅值带 / B3 响应与回零 / B4 触须幅度比与相位延迟 /
  dt 无关性 / 纯函数收敛与过冲 / 非拖拽零影响(无 drag 键 + 状态精确零)/
  果蝇同构 / gain·axes 契约 / 渲染侧绕髋旋转几何 / 判定门与钳位。

运行:python tests/test_drag_physics.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from neuropet.body.base import (DRAG_DETECT_V, DRAG_LEG_GAIN, DRAG_MAX_DEG,
                                DRAG_THETA_MAX_DEG, GenericInsectBody,
                                drag_lag_step)
from neuropet.core.contracts import Behavior, BehaviorCommand, PetState
from neuropet.core.world import WorldModel

DT = 1 / 60
DIAG = math.sqrt(0.5)


def make_body(params, pet_id="tp", heading=0.0, pos=(960.0, 540.0)):
    st = PetState(pet_id=pet_id, species_id="roach", pos=pos)
    st.heading = heading
    body = GenericInsectBody(st, dict(params))
    view = WorldModel(1920, 1080).snapshot(pet_id, 0.0)
    return body, st, view


def drag_run(body, st, view, v_fn, frames, dt=DT):
    """合成拖拽(app 同款覆写 st.pos);越界跑台:平移回中心并同步平移
    body 的观测锚 _drag_prev(仅测试侧),外拖速度观测不受边界钳位污染。"""
    cmd = BehaviorCommand(Behavior.IDLE, priority=1)
    hist = []
    for i in range(frames):
        body.apply(cmd, view, dt)
        vx, vy = v_fn(i * dt)
        nx, ny = st.pos[0] + vx * dt, st.pos[1] + vy * dt
        if not (100.0 <= nx <= 1820.0 and 100.0 <= ny <= 980.0):
            dx, dy = 960.0 - nx, 540.0 - ny
            nx, ny = 960.0, 540.0
            body._drag_prev = (body._drag_prev[0] + dx, body._drag_prev[1] + dy)
        st.pos = (nx, ny)
        hist.append(body.pose())
    return hist


def steady(v_mag, dir_deg):
    a = math.radians(dir_deg)
    return lambda t: (v_mag * math.cos(a), v_mag * math.sin(a))


def d_angle_err(d, want):
    got = math.atan2(d[1], d[0])
    return math.degrees(abs(math.atan2(math.sin(got - want),
                                       math.cos(got - want))))


# ---- B1 滞后方向(拖右上 → 腿甩左下;体轴系期望 = 拖速反方向) ----
def test_b1_direction() -> None:
    from neuropet.species.cockroach import PARAMS as ROACH
    body, st, view = make_body(ROACH)
    vfn = lambda t: (min(1500.0, 1200.0 * t) * DIAG,
                     -min(1500.0, 1200.0 * t) * DIAG)
    hist = drag_run(body, st, view, vfn, 80)
    d = hist[-1]["drag"]["d"]
    want = math.atan2(DIAG, -DIAG)          # 拖速(右上)的反方向 = 左下 135°
    err = d_angle_err(d, want)
    assert err <= 10.0, f"B1 方向误差 {err:.2f}° 应 ≤10°(d={d})"


# ---- B2 幅值带:v=600/1500/2500 → 12~20° / 26~35° / 35°(钳制) ----
def test_b2_amplitude_bands() -> None:
    from neuropet.species.cockroach import PARAMS as ROACH
    for v, lo, hi in ((600.0, 12.0, 20.0), (1500.0, 26.0, 35.5),
                      (2500.0, 33.0, 35.6)):
        body, st, view = make_body(ROACH)
        hist = drag_run(body, st, view, steady(v, -45.0), 110)
        mag = math.hypot(*hist[-1]["drag"]["d"])
        assert lo <= mag <= hi, f"B2 v={v:.0f} 幅值 {mag:.1f}° 应在 {lo}~{hi}"


# ---- B3 响应:阶跃 10~90% ≤150ms;松手回零(≤10%)≤400ms ----
def test_b3_response_and_release() -> None:
    from neuropet.species.cockroach import PARAMS as ROACH
    body, st, view = make_body(ROACH)
    hist = drag_run(body, st, view, steady(1500.0, -45.0), 90)
    mags = [math.hypot(*p["drag"]["d"]) if "drag" in p else 0.0 for p in hist]
    final = mags[-1]
    assert final > 30.0, f"满拖应收敛到满幅附近,得到 {final:.1f}°"

    def cross(frac):
        th = final * frac
        for i, m in enumerate(mags):
            if m >= th:
                return i * DT * 1000.0
        return 1e9
    rise = cross(0.9) - cross(0.1)
    assert rise <= 150.0, f"B3a 10→90% 响应 {rise:.0f}ms 应 ≤150ms"
    rest = drag_run(body, st, view, lambda t: (0.0, 0.0), 40)
    settle = next((i * DT * 1000.0 for i, p in enumerate(rest)
                   if math.hypot(*p["drag"]["d"]) <= 0.10 * final), None)
    assert settle is not None and settle <= 400.0, \
        f"B3b 松手回零 {settle}ms 应 ≤400ms"


# ---- B4 触须:施加幅度比(×1.5,基 ±20/梢 ±30°)1.2~2.0;梢端延迟 40~120ms ----
def test_b4_antenna_ratio_and_delay() -> None:
    from neuropet.species.cockroach import PARAMS as ROACH
    body, st, view = make_body(ROACH)
    hist = drag_run(body, st, view, steady(1500.0, -45.0), 90)
    dr = hist[-1]["drag"]
    ag, tr_ = dr["ant_gain"], dr["ant_tip_ratio"]
    base_ap = min(20.0, ag * math.hypot(*dr["ant"]))
    tip_ap = min(30.0, ag * tr_ * math.hypot(*dr["ant_tip"]))
    ratio = tip_ap / max(1e-6, base_ap)
    assert 1.2 <= ratio <= 2.0, f"B4a 梢/基幅度比 {ratio:.2f} 应在 1.2~2.0"
    base = [math.hypot(*p["drag"]["ant"]) if "drag" in p else 0.0 for p in hist]
    tip = [math.hypot(*p["drag"]["ant_tip"]) if "drag" in p else 0.0 for p in hist]

    def t50(seq):
        th = seq[-1] * 0.5
        return next((i * DT * 1000.0 for i, v in enumerate(seq) if v >= th), 1e9)
    delay = t50(tip) - t50(base)
    assert 40.0 <= delay <= 120.0, f"B4b 梢端相位延迟 {delay:.0f}ms 应在 40~120"


# ---- dt 无关性:1/60 与 1/240 步长收敛到同一稳态(幅值/方向) ----
def test_dt_independence() -> None:
    from neuropet.species.cockroach import PARAMS as ROACH
    ends = []
    for dt, n in ((DT, 110), (DT / 4, 440)):
        body, st, view = make_body(ROACH)
        hist = drag_run(body, st, view, steady(1500.0, -45.0), n, dt=dt)
        ends.append(hist[-1]["drag"]["d"])
    (d0, d1) = ends
    m0, m1 = math.hypot(*d0), math.hypot(*d1)
    assert abs(m0 - m1) <= 1.5, f"稳态幅值随 dt 漂移 {abs(m0 - m1):.2f}° 应 ≤1.5°"
    err = d_angle_err(d0, math.atan2(d1[1], d1[0]))
    assert err <= 3.0, f"稳态方向随 dt 漂移 {err:.2f}° 应 ≤3°"


# ---- 纯函数:drag_lag_step 半隐式欧拉收敛 + 过冲 ≤1 个(ζ=0.7)+ dt 收敛 ----
def test_pure_step_convergence() -> None:
    tgt = (26.0, -26.0)
    seq = []
    d, dd = [0.0, 0.0], [0.0, 0.0]
    for _ in range(int(1.2 / DT)):
        d, dd = drag_lag_step(d, dd, tgt, DT)
        seq.append(math.hypot(*d))
    final = math.hypot(*d)
    assert abs(final - math.hypot(*tgt)) < 0.05, f"应收敛到目标,得到 {final:.2f}°"
    peak = max(seq)
    assert peak <= final * 1.15, f"欠阻尼过冲 {peak / final:.2f} 应 ≤1.15(过冲 1 个)"
    # dt 减半、步数加倍 → 同一轨迹(半隐式欧拉一阶收敛,容差 0.5°)
    d2, dd2 = [0.0, 0.0], [0.0, 0.0]
    for _ in range(int(1.2 / (DT / 2))):
        d2, dd2 = drag_lag_step(d2, dd2, tgt, DT / 2)
    assert math.hypot(*[a - b for a, b in zip(d, d2)]) <= 0.5, \
        f"dt 无关性:两步长终态差 {math.hypot(d[0]-d2[0], d[1]-d2[1]):.3f}° 应 ≤0.5°"


# ---- 非拖拽零影响:无 "drag" 键、内部状态精确零、运动学状态不被扰动 ----
def test_nondrag_zero_impact() -> None:
    from neuropet.species.fruitfly import PARAMS as FLY
    body, st, view = make_body(FLY)
    cmd = BehaviorCommand(Behavior.EXPLORE, target=(3000.0, 540.0), intensity=1.0)
    for _ in range(60):
        body.apply(cmd, view, DT)
    p = body.pose()
    assert "drag" not in p, "非拖拽态 pose 不应携带 drag 键(冻结面)"
    assert body._drag_d == [0.0, 0.0] and body._drag_dd == [0.0, 0.0], \
        "非拖拽态滞后状态应精确为零(零开销路径)"
    assert body._drag_d_ant == [0.0, 0.0]
    # 观测不扰动运动学状态:速度/位置仍由 body 自身积分决定
    assert abs(st.speed - body._speed) < 1e-9
    assert math.isfinite(st.pos[0]) and math.isfinite(st.pos[1])


# ---- 果蝇同构:同一通道结构(generic body,species 无关) ----
def test_fly_isomorphic_channel() -> None:
    from neuropet.species.cockroach import PARAMS as ROACH
    from neuropet.species.fruitfly import PARAMS as FLY
    roach_d, fly_d = None, None
    body, st, view = make_body(FLY)
    hist = drag_run(body, st, view, steady(1500.0, -45.0), 90)
    dr = hist[-1]["drag"]
    fly_d = dr["d"]
    assert math.hypot(*fly_d) > 26.0, "果蝇满拖幅值应与蟑螂同带(>26°)"
    body2, st2, view2 = make_body(ROACH)
    h2 = drag_run(body2, st2, view2, steady(1500.0, -45.0), 90)
    roach_d = h2[-1]["drag"]["d"]
    assert set(h2[-1]["drag"].keys()) == set(dr.keys()), "通道键结构应同构"
    err = d_angle_err(fly_d, math.atan2(roach_d[1], roach_d[0]))
    assert err <= 3.0, f"果蝇/蟑螂稳态方向应一致(误差 {err:.2f}°)"


# ---- gain/axes 契约:三对增益与单位轴向量 ----
def test_gain_axes_contract() -> None:
    from neuropet.species.cockroach import PARAMS as ROACH
    body, st, view = make_body(ROACH)
    assert DRAG_LEG_GAIN == (1.0, 0.85, 0.75)
    hist = drag_run(body, st, view, steady(900.0, -45.0), 60)
    dr = hist[-1]["drag"]
    assert dr["gain"] == (1.0, 0.85, 0.75)
    axes = dr["axes"]
    assert len(axes) == 6, "应提供 6 条静息足向轴"
    for ax, ay in axes:
        assert abs(math.hypot(ax, ay) - 1.0) < 1e-6, "足向轴应为单位向量"
    for key in ("d", "ant", "ant_tip"):
        assert all(math.isfinite(v) for v in dr[key])


# ---- 渲染侧几何:drag 偏角使腿链绕髋旋转,髋不动、足端甩向拖速反方向 ----
def test_leg_rotation_geometry() -> None:
    from neuropet.render.renderer import _draw_legs_hybrid

    class Rec:
        def __init__(self):
            self.lines = []

        def line(self, pts, fill=None, width=None):
            self.lines.append(list(pts))

        def ellipse(self, box, fill=None):
            pass

    leg = {"points": [(38.0, -15.0), (30.0, -40.0), (26.0, -52.0), (24.0, -58.0)],
           "lift": 0.0}
    drag = {"d": (35.0 * -DIAG, 35.0 * DIAG), "gain": DRAG_LEG_GAIN,
            "axes": [(0.406, -0.914)],
            "ant": (0.0, 0.0), "ant_gain": 0.6,
            "ant_tip": (0.0, 0.0), "ant_tip_ratio": 1.5}
    base = Rec()
    _draw_legs_hybrid(base, lambda p: p, [leg], {}, False, 3.0)
    spun = Rec()
    _draw_legs_hybrid(spun, lambda p: p, [leg], {}, False, 3.0, drag)

    def endpoints(rec):
        out = []
        for ln in rec.lines:
            for q in ln:
                out.append(q)
        return out
    eb, es = endpoints(base), endpoints(spun)
    assert any(abs(q[0] - leg["points"][0][0]) < 1e-6 and
               abs(q[1] - leg["points"][0][1]) < 1e-6 for q in es), "髋点不动"
    tip0 = max(eb, key=lambda q: math.hypot(q[0] - 38.0, q[1] + 15.0))
    tip1 = max(es, key=lambda q: math.hypot(q[0] - 38.0, q[1] + 15.0))
    disp = (tip1[0] - tip0[0], tip1[1] - tip0[1])
    dn = math.hypot(*drag["d"])
    dhat = (drag["d"][0] / dn, drag["d"][1] / dn)
    assert (disp[0] * dhat[0] + disp[1] * dhat[1]) > 0.0, \
        "足端位移应有沿拖速反方向(−v)的正投影"
    assert math.hypot(*disp) > 1.0, "足端应发生可见偏摆"


# ---- 判定门与钳位:低于门速不进通道;超速幅值钳 35°(硬钳 60° 内) ----
def test_detect_gate_and_clamp() -> None:
    from neuropet.species.cockroach import PARAMS as ROACH
    body, st, view = make_body(ROACH)
    hist = drag_run(body, st, view, steady(0.6 * DRAG_DETECT_V, -45.0), 90)
    assert "drag" not in hist[-1], "低于判定门速不应进入拖拽通道"
    body, st, view = make_body(ROACH)
    hist = drag_run(body, st, view, steady(4000.0, -45.0), 110)
    mag = math.hypot(*hist[-1]["drag"]["d"])
    assert mag <= DRAG_MAX_DEG + 0.6, f"超速幅值应钳在满幅 35°,得到 {mag:.1f}°"
    assert mag <= DRAG_THETA_MAX_DEG


def main() -> int:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"[OK] {fn.__name__}")
    print(f"[PASS] {len(fns)}/{len(fns)} 拖拽动量物理判据")
    return 0


if __name__ == "__main__":
    sys.exit(main())
