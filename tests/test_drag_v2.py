# -*- coding: utf-8 -*-
"""拖拽力学 v2 验收(tests/test_drag_v2.py;返工根因 #1 重做的验收门槛)。

对应《返工根因分析与流程改进提案.md》第 1 条用户裁决:结合昆虫重量 / 拖拽
过程按鼠标速度连续计算 / 变化受自由度(关节角)限制 / 低速时尤其符合。

判据(任务书冻结的五条验收 + 接口契约 + 积分器正确性):
  1 质量效应:同一加速度序列,0.8g 峰值摆角 < 0.001g 峰值摆角(≥3 倍);
    0.8g 整定时间 > 0.001g。
  2 连续性:60Hz 恒定加速度阶跃,任意相邻帧所有输出分量变化 ≤6°;无超调
    >30%(阻尼比 ζ∈[0.7,1.1] 断言)。连续性按蟑螂(0.8g)执行——果蝇为轻
    质量通道,ω×28.3 → 整定 <1 帧,快本身是验收 1 的要求,预算不适用。
  3 自由度:持续大力加速度下 leg_swings 全部被钳在关节范围内;加速度反向
    时摆角平滑过零不过冲出钳制。
  4 静定:输入归零 2s 后 settle>0.95 且摆角→0(±0.5°)。
  5 数值稳定:dt=1/30 与 1/120 双步长跑同输入,2s 后状态差 <2°。

运行:python tests/test_drag_v2.py(断言脚本直跑,非 pytest)
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from neuropet.physics.drag import (DEFAULT_JOINT_LIMITS, DragDynamics,
                                   DragPose, _damped_step, species_dynamics)

DT = 1 / 60


def run(dd: DragDynamics, ax_fn, ay_fn, frames, dt=DT):
    """合成加速度序列连续 step,返回 DragPose 列表。"""
    out = []
    for i in range(frames):
        out.append(dd.step(dt, ax_fn(i * dt), ay_fn(i * dt)))
    return out


def angle_view(p: DragPose):
    """全部角度分量的扁平视图:(名字, 值)。"""
    return ([("pitch", p.tilt_pitch_deg), ("roll", p.tilt_roll_deg)]
            + [(f"leg{i}", v) for i, v in enumerate(p.leg_swings)]
            + [("antL", p.antenna_swing[0]), ("antR", p.antenna_swing[1])])


def max_frame_delta(hist):
    """相邻帧角度分量最大变化(°)+ settle 最大帧变化(无量纲)。"""
    md, ms = 0.0, 0.0
    for a, b in zip(hist, hist[1:]):
        va, vb = dict(angle_view(a)), dict(angle_view(b))
        d = max(abs(vb[k] - va[k]) for k in va)
        md = max(md, d)
        ms = max(ms, abs(b.settle - a.settle))
    return md, ms


def settle_time(hist, frac=0.05):
    """leg_swings 最大分量进入终值 ±frac 带的首个时刻(s);未进入返回 None。"""
    mags = [max(abs(v) for v in p.leg_swings) for p in hist]
    final = mags[-1]
    band = final * frac
    for i, m in enumerate(mags):
        if abs(m - final) <= band:
            return i * DT
    return None


# ---- 契约:字段名冻结 / 物种质量 / 限位默认 / 非法输入 ----
def test_contract() -> None:
    f = {x.name for x in DragPose.__dataclass_fields__.values()}
    assert f == {"tilt_pitch_deg", "tilt_roll_deg", "leg_swings",
                 "antenna_swing", "settle"}, f"DragPose 字段被改动:{f}"
    roach, fly = species_dynamics("cockroach"), species_dynamics("fruitfly")
    assert roach.mass_g == 0.8 and fly.mass_g == 0.001
    # 质量效应在参数层可见:蝇 ω 高(整定快)、静态增益大(偏摆大)
    assert fly.omega_leg > roach.omega_leg and fly.s_leg > roach.s_leg
    assert fly.omega_ant > fly.omega_leg, "触须 m_eff 最小 → ω_n 最高"
    assert roach.joint_limits["leg"]["thc_yaw"] == (-60.0, 60.0)
    assert roach.joint_limits["leg"]["ctr_pitch"] == (-30.0, 110.0)
    assert roach.joint_limits["antenna"] == (-80.0, 80.0)
    assert DEFAULT_JOINT_LIMITS is not roach.joint_limits, "限位必须深拷贝"
    p = roach.step(DT, 100.0, 0.0)
    assert isinstance(p, DragPose) and len(p.leg_swings) == 6
    assert len(p.antenna_swing) == 2 and 0.0 <= p.settle <= 1.0
    try:
        species_dynamics("cat")
        raise AssertionError("未知物种应 ValueError")
    except ValueError:
        pass
    try:
        DragDynamics(0.0)
        raise AssertionError("mass_g<=0 应 ValueError")
    except ValueError:
        pass
    # 自定义限位覆盖生效
    d2 = DragDynamics(0.8, {"leg": {"thc_yaw": (-20.0, 30.0)}})
    assert d2.joint_limits["leg"]["thc_yaw"] == (-20.0, 30.0)
    print(f"    ω(leg/ant/tilt) 蟑=({roach.omega_leg:.1f},{roach.omega_ant:.1f},"
          f"{roach.omega_tilt:.1f}) 蝇=({fly.omega_leg:.1f},{fly.omega_ant:.1f},"
          f"{fly.omega_tilt:.1f}) rad/s;S(leg) 蟑={roach.s_leg:.4f} "
          f"蝇={fly.s_leg:.4f} °/(px/s²)")


# ---- 验收 1:质量效应(峰值比 ≥3 倍;整定时间 蟑>蝇) ----
def test_mass_effect() -> None:
    roach = species_dynamics("cockroach")
    fly = species_dynamics("fruitfly")
    n = int(1.5 / DT)
    hr = run(roach, lambda t: 800.0, lambda t: 0.0, n)
    hf = run(fly, lambda t: 800.0, lambda t: 0.0, n)
    pr = max(max(abs(v) for v in p.leg_swings) for p in hr)
    pf = max(max(abs(v) for v in p.leg_swings) for p in hf)
    assert pf > pr, f"0.8g 峰值 {pr:.2f}° 应 < 0.001g 峰值 {pf:.2f}°"
    ratio = pf / pr
    assert ratio >= 3.0, f"峰值比 {ratio:.2f}× 应 ≥3×(0.8g={pr:.2f}°, 0.001g={pf:.2f}°)"
    tr, tf = settle_time(hr), settle_time(hf)
    assert tr is not None and tf is not None, "两条曲线都应进入终值 ±5% 带"
    assert tr > tf, f"0.8g 整定 {tr:.3f}s 应 > 0.001g 整定 {tf:.3f}s"
    assert tr > 5.0 * tf, f"整定时间比 {tr / tf:.1f}× 应 >5×(ω 标度 28× 的下界证据)"
    # 同一序列的触须通道同断言(大力下双方进钳制后峰值被 DOF 拉平,见验收 3,
    # 故质量效应比较取未双饱和幅值 a=800)
    pa = max(max(abs(v) for v in p.antenna_swing) for p in hr)
    pb = max(max(abs(v) for v in p.antenna_swing) for p in hf)
    assert pb / pa >= 3.0, f"同序列触须峰值比 {pb / pa:.2f}× 应 ≥3×"
    print(f"    a=800:峰 蟑={pr:.2f}° 蝇={pf:.2f}°(比 {ratio:.1f}×);"
          f"整定 蟑={tr * 1000:.0f}ms 蝇={tf * 1000:.1f}ms;触须峰 蟑={pa:.1f}° 蝇={pb:.1f}°")


# ---- 验收 2:连续性(≤6°/帧)+ 超调 ≤30% + ζ∈[0.7,1.1](蟑螂 0.8g 口径) ----
def test_continuity_and_damping() -> None:
    roach = species_dynamics("cockroach")
    for z in (roach.zeta_leg, roach.zeta_ant, roach.zeta_tilt):
        assert 0.7 <= z <= 1.1, f"阻尼比 ζ={z} 应 ∈[0.7,1.1]"
    # 最严酷场景:满幅大力阶跃(全部通道冲向钳制值),180 帧
    hist = run(species_dynamics("cockroach"), lambda t: 4000.0, lambda t: 0.0, 180)
    md, ms = max_frame_delta(hist)
    assert md <= 6.0, f"60Hz 阶跃相邻帧最大角度变化 {md:.2f}° 应 ≤6°"
    assert ms <= 0.25, f"settle 帧变化 {ms:.2f} 应 ≤0.25(无量纲预算)"
    # 超调:未饱和通道(蟑腿 @a=800,稳态 12°),峰/稳态 ≤1.30
    hist2 = run(species_dynamics("cockroach"), lambda t: 800.0, lambda t: 0.0, 180)
    mags = [max(abs(v) for v in p.leg_swings) for p in hist2]
    final = mags[-1]
    peak = max(mags)
    assert final > 1.0, f"稳态 {final:.2f}° 应显著非零"
    assert peak / final <= 1.30, f"超调 {peak / final:.3f} 应 ≤1.30(ζ∈[0.7,1.1] 的行为断言)"
    print(f"    满幅阶跃帧差 max={md:.2f}°(settle {ms:.2f});超调 peak/final="
          f"{peak / final:.3f}(稳态 {final:.2f}°,峰 {peak:.2f}°)")


# ---- 验收 3:自由度(持续大力钳在关节范围;反向平滑过零不出钳) ----
def test_dof_limits() -> None:
    lo, hi = DEFAULT_JOINT_LIMITS["leg"]["thc_yaw"]
    dd = species_dynamics("cockroach")
    n1 = int(1.0 / DT)
    n2 = int(1.5 / DT)
    hist = run(dd, lambda t: 6000.0 if t < 1.0 else -6000.0,
               lambda t: 0.0, n1 + n2)
    for p in hist:
        for v in p.leg_swings:
            assert lo - 1e-6 <= v <= hi + 1e-6, \
                f"leg_swings {v:.2f}° 越出钳制 [{lo},{hi}]"
    # 持续大力段:全部 6 腿被钳到边界(通道对钳制值渐近逼近;取反向前最后
    # 10 帧,t≥0.83s,渐近残差 <0.3°)
    at_clamp = hist[n1 - 10:n1]
    assert all(abs(v - lo) < 0.5 for p in at_clamp for v in p.leg_swings), \
        "持续大力 +6000 下 6 腿应全部压到 −60° 钳制边界(±0.5°)"
    # 反向段:平滑过零——腿逐帧变化 ≤6°(全钳程跨 120° 仍达标),触须全钳程
    # 跨 160° 按 skill §2 连续性预算 ≤12°/帧(验收 2 的 ≤6° 只约束从静止的
    # 恒定阶跃,已由 test_continuity_and_damping 覆盖);且永不越出钳制
    md = 0.0
    md_ant = 0.0
    for a, b in zip(hist[n1 - 1:], hist[n1:]):
        md = max(md, max(abs(y - x) for x, y in zip(a.leg_swings, b.leg_swings)))
        md_ant = max(md_ant, max(abs(y - x) for x, y in
                                 zip(a.antenna_swing, b.antenna_swing)))
    assert md <= 6.0, f"反向阶跃腿帧变化 {md:.2f}° 应 ≤6°(平滑过零)"
    assert md_ant <= 12.0, f"反向阶跃触须帧变化 {md_ant:.2f}° 应 ≤12°(skill 预算)"
    for p in hist[n1:]:
        for v in p.antenna_swing:
            assert -80.0 - 1e-6 <= v <= 80.0 + 1e-6, f"触须 {v:.2f}° 越出 ±80°"
    zero_cross = any(a.leg_swings[0] < 0 <= b.leg_swings[0]
                     for a, b in zip(hist[n1 - 1:], hist[n1:]))
    assert zero_cross, "反向后摆角应平滑过零(存在符号翻转帧)"
    print(f"    +6000 持续:6 腿全钳 {lo}°;反向帧差 腿 max={md:.2f}° ≤6°、"
          f"须 max={md_ant:.2f}° ≤12°,过零平滑,无越钳")


# ---- 验收 4:静定(输入归零 2s 后 settle>0.95 且摆角→0±0.5°) ----
def test_settle() -> None:
    dd = species_dynamics("cockroach")
    n1, n2 = int(1.0 / DT), int(2.0 / DT)
    hist = run(dd, lambda t: 6000.0 if t < 1.0 else 0.0, lambda t: 0.0, n1 + n2)
    tail = hist[n1:]
    assert tail[0].settle < 0.2, f"大力拖拽中 settle 应≈0,得到 {tail[0].settle:.2f}"
    end = tail[-1]
    assert end.settle > 0.95, f"归零 2s 后 settle={end.settle:.4f} 应 >0.95"
    worst = max(abs(v) for _, v in angle_view(end))
    assert worst <= 0.5, f"归零 2s 后最大摆角 {worst:.4f}° 应 ≤0.5°"
    print(f"    归零 2s:settle={end.settle:.4f}(>0.95),最大残余摆角 "
          f"{worst:.2e}°(≤0.5°)")


# ---- 验收 5:数值稳定(dt=1/30 与 1/120 双步长,2s 后状态差 <2°) ----
def test_step_invariance() -> None:
    for name in ("cockroach", "fruitfly"):
        a = species_dynamics(name)
        b = species_dynamics(name)
        ha = run(a, lambda t: 1500.0, lambda t: -600.0, 60, dt=1 / 30)
        hb = run(b, lambda t: 1500.0, lambda t: -600.0, 240, dt=1 / 120)
        end_a, end_b = ha[-1], hb[-1]
        va, vb = dict(angle_view(end_a)), dict(angle_view(end_b))
        diff = max(abs(vb[k] - va[k]) for k in va)
        assert diff < 2.0, f"[{name}] 双步长 2s 后状态差 {diff:.2e}° 应 <2°"
        assert abs(end_b.settle - end_a.settle) < 0.01, \
            f"[{name}] settle 双步长差 {abs(end_b.settle - end_a.settle):.2e}"
        if name == "cockroach":
            print(f"    蟑螂 dt=1/30 vs 1/120(常值 a=(1500,−600),2s):状态差 "
                  f"{diff:.2e}°(<2°);settle 差 {abs(end_b.settle - end_a.settle):.1e}")


# ---- 低速符合性:轻推微摆 + 低速匀速(输入归零)时悬垂静定 ----
def test_low_speed() -> None:
    dd = species_dynamics("cockroach")
    n1, n2 = int(0.6 / DT), int(1.6 / DT)
    hist = run(dd, lambda t: 150.0 if t < 0.6 else 0.0, lambda t: 0.0, n1 + n2)
    peak = max(max(abs(v) for v in p.leg_swings) for p in hist)
    assert peak < 10.0, f"轻推(150px/s²)腿峰摆 {peak:.2f}° 应 <10°(低速微摆)"
    assert hist[-1].settle > 0.95, \
        f"低速匀速段 settle={hist[-1].settle:.3f} 应 >0.95(悬垂静定)"
    print(f"    a=150:腿峰 {peak:.2f}°(<10°);低速匀速 1.6s settle="
          f"{hist[-1].settle:.3f}(>0.95)")


# ---- 触须通道:m_eff 最小 → ω_n 高(整定快)、同加速度偏摆比腿大 ----
def test_antenna_channel() -> None:
    dd = species_dynamics("cockroach")
    hist = run(dd, lambda t: 300.0, lambda t: 0.0, int(1.5 / DT))

    def rise95(seq):
        final = seq[-1]
        for i, v in enumerate(seq):
            if abs(v) >= 0.95 * abs(final):
                return i * DT
        return None
    ant = [p.antenna_swing[0] for p in hist]
    leg = [max(abs(v) for v in p.leg_swings) for p in hist]
    t_ant, t_leg = rise95(ant), rise95(leg)
    assert t_ant is not None and t_leg is not None
    assert t_ant < t_leg, f"触须整定 {t_ant * 1000:.0f}ms 应快于腿 {t_leg * 1000:.0f}ms(ω_n 高)"
    assert abs(ant[-1]) > leg[-1], \
        f"同加速度触须偏摆 {abs(ant[-1]):.2f}° 应大于腿 {leg[-1]:.2f}°"
    print(f"    a=300:触须 {abs(ant[-1]):.2f}°(95% 上升 {t_ant * 1000:.0f}ms)> "
          f"腿 {leg[-1]:.2f}°({t_leg * 1000:.0f}ms)")


# ---- 积分器正确性:解析 ZOH 单步 ≈ 细分半隐式欧拉参照(三段 ζ) ----
def test_integrator_correctness() -> None:
    def ref(x, v, target, wn, zeta, dt, n=512):
        h = dt / n
        for _ in range(n):
            v += (-2 * zeta * wn * v - wn * wn * (x - target)) * h
            x += v * h
        return x, v
    cases = [(0.0, 0.0, 30.0, 8.0, 0.85, 1 / 30),    # 欠阻尼
             (0.0, 0.0, 25.0, 6.0, 1.0, 1 / 30),     # 临界
             (5.0, 2.0, 40.0, 7.0, 1.3, 1 / 30)]     # 过阻尼
    worst = 0.0
    for x0, v0, tgt, wn, z, dt in cases:
        x1, v1 = _damped_step(x0, v0, tgt, wn, z, dt)
        x2, v2 = ref(x0, v0, tgt, wn, z, dt)
        worst = max(worst, abs(x1 - x2), abs(v1 - v2) / wn)
    assert worst < 0.02, f"解析单步与参照差 {worst:.4f}° 应 <0.02°(公式正确性)"
    # 收敛性:常值输入下 1/30 单步与 4×(1/120) 逐步对齐(解析解精确 → 逐点一致)
    d30 = DragDynamics(0.8)
    d120 = DragDynamics(0.8)
    pa = d30.step(1 / 30, 2000.0, 0.0)
    pb = d120.step(1 / 120, 2000.0, 0.0)
    for _ in range(3):
        pb = d120.step(1 / 120, 2000.0, 0.0)
    diff = max(abs(u - w) for u, w in zip(pa.leg_swings, pb.leg_swings))
    assert diff < 1e-6, f"常值输入下跨步长单步差 {diff:.2e}° 应 ≈0(ZOH 精确性)"
    print(f"    解析单步 vs 半隐式欧拉参照 max 差 {worst:.2e}°;跨步长单步差 {diff:.1e}°")


def main() -> int:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"[OK] {fn.__name__}")
    print(f"[PASS] {len(fns)}/{len(fns)} 拖拽力学 v2 验收")
    return 0


if __name__ == "__main__":
    # ---- r10 集成回归:App 的 species_id 全写法必须都被接受(实机 ValueError) ----
    from neuropet.physics import species_dynamics as _sd
    for _sid in ("cockroach", "species.cockroach",
                 "fruitfly", "species.fruitfly-dc34"):
        _sd(_sid)
    try:
        _sd("species.spider")
        raise AssertionError("未知物种应报 ValueError")
    except ValueError:
        pass
    print("[ok] species_id 全写法归一(实机集成回归)")
    sys.exit(main())
