"""滑翔 v3 姿态振幅测试(无显示器)。运行:python tests/test_glide_v2.py

返工根因 #5(用户裁决):滑翔时"只有虚影固定位置……没有可见的连续振幅"。
v2 巡航 pitch 恒定 −9.5°、roll/sway 不进 pose、渲染横摆走墙钟 0.4Hz(2.5s
周期)低于肉眼可读带 → 躯干逐帧同图 = 虚影固定。v3 在 body 层加全时间线
连续振幅方程(neuropet/body/flight.py 模块头),渲染按相位逐帧重算。

验收门槛(任务书第十轮):
  ① 振荡连续性:60Hz 滑翔段逐帧差受预算约束;幅值带 A_r∈[5°,7°]、
     A_b∈[2,3.5]px;
  ② 耦合:roll>0 相位内漂移速度同号(滚右漂右);|φ_r−φ_b|∈[0.3π,0.7π];
  ③ 签名解冻:不同滑翔相位的 _pose_signature 不同 → OPT-07 不再冻结;
  ④ 性能:滑翔帧耗对照基线(v2 等价位)P50 增幅 ≤1ms;
  ⑤ 进入/退出滑翔无相位/位置跳变(淡入淡出,触地滑停期连续归零)。

【预算偏差诚实声明】任务书 |Δroll|≤0.8°/帧 与 A_r=6°/f_r=2.2Hz 正弦物理
不相容:正弦峰速 = A_r·2π·f_r/60 = 1.381°/帧;对 ±6°@2.2Hz 的任何波形,
峰速下界 = 三角波 4·A/T = 52.8°/s = 0.88°/帧,仍 >0.8。按项目惯例(预算=
方程峰速×帧长,如翻面 12°/帧)取 1.4°/帧 门;drift 系数同理由任务书 ~0.12
收紧到 0.03 以满足 |Δpos|≤2px/帧(468px/s→117px/s),符号耦合不变。
"""
from __future__ import annotations

import math
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from neuropet.body.flight import (FlightState, FlightStateMachine,
                                  GLIDE_BOB_HZ, GLIDE_DRIFT_K, GLIDE_ROLL_HZ)
from neuropet.render.renderer import (_pose_signature, _render_pose_full,
                                      render_pose_hybrid)

DT = 1.0 / 60.0
SPEED = 650.0          # 蟑螂 fly_speed(巡航档)
PARAMS = {"glide": True, "fly_altitude": 55.0, "fly_speed": SPEED,
          "glide_gr": 6.0}


def make_fsm(seed: int = 42) -> FlightStateMachine:
    random.seed(seed)
    fsm = FlightStateMachine(dict(PARAMS))
    fsm.start_takeoff()
    return fsm


def run_sustained_glide(frames: int, seed: int = 42):
    """起飞 → 巡航;虚拟补给高度(alt<40 回填 55)保持巡航拍不进 flare,
    用于稳态振幅测量(状态机方程本身不因回填改变,只影响 altitude 通道)。"""
    fsm = make_fsm(seed)
    rows = []
    entered = None
    for i in range(frames):
        fsm.update(DT, SPEED)
        if fsm.state is FlightState.FLY and not fsm.flare:
            if entered is None:
                entered = i
            rows.append((i - entered, fsm.glide_roll, fsm.glide_bob,
                         fsm.glide_pitch_extra, fsm.glide_drift,
                         fsm.roll, fsm.pitch))
        if fsm.altitude < 40.0:
            fsm.altitude = 55.0               # 虚拟补给(仅测试台架)
        if entered is not None and (fsm.flare or
                                    fsm.state is not FlightState.FLY):
            break
    assert len(rows) > 240, f"巡航段不足 4s:{len(rows)} 帧"
    return fsm, rows


def test_1_oscillation_continuity_and_amplitude() -> None:
    """① 连续性:|Δroll|≤1.4°/帧(方程峰 1.381,见文件头偏差声明)、
    |Δbob|≤2px/帧;幅值带 A_r∈[5,7]°、A_b∈[2,3.5]px;pitch 微摆活跃。"""
    fsm, rows = run_sustained_glide(600)
    # -- 连续性(含淡入段:smoothstep×正弦仍二阶连续) --
    d_roll = max(abs(rows[i][1] - rows[i - 1][1]) for i in range(1, len(rows)))
    d_bob = max(abs(rows[i][2] - rows[i - 1][2]) for i in range(1, len(rows)))
    assert d_roll <= 1.4, f"|Δroll| 峰 {d_roll:.3f}°/帧 > 1.4 预算"
    assert d_bob <= 2.0, f"|Δy_bob| 峰 {d_bob:.3f}px/帧 > 2 预算"
    # -- 幅值带(淡入完成 w=1 后的稳态窗) --
    steady = [r for r in rows if r[0] * DT > 0.8]
    a_r = max(abs(r[1]) for r in steady)
    a_b = max(abs(r[2]) for r in steady)
    assert 5.0 <= a_r <= 7.0, f"A_r={a_r:.2f}° 出带 [5,7]"
    assert 2.0 <= a_b <= 3.5, f"A_b={a_b:.2f}px 出带 [2,3.5]"
    # -- pitch 微摆(±1.2° 同 f_r;非恒定 → 0.5° 缓存桶逐帧穿越) --
    a_p = max(abs(r[3]) for r in steady)
    s_p = statistics.pstdev([r[3] for r in steady])
    assert 1.0 <= a_p <= 1.3 and s_p > 0.5, \
        f"pitch 微摆异常:峰 {a_p:.2f}°、σ {s_p:.2f}"
    # -- drift 预算(0.03×650×6°/60 = 1.95px/帧) --
    d_drift = max(abs(rows[i][4] - rows[i - 1][4])
                  for i in range(1, len(rows)))
    assert d_drift <= 2.0, f"|Δdrift| 峰 {d_drift:.3f}px/帧 > 2 预算"
    print(f"    [cont] |Δroll|={d_roll:.3f}°/帧(≤1.4) |Δbob|={d_bob:.3f}px/帧"
          f"(≤2) |Δdrift|={d_drift:.3f}px/帧(≤2)")
    print(f"    [amp ] A_r={a_r:.2f}°∈[5,7]  A_b={a_b:.2f}px∈[2,3.5]  "
          f"pitch 微摆峰={a_p:.2f}°")


def test_2_drift_coupling_and_phase_offset() -> None:
    """② 滚右漂右:glide_roll>3° 相位内 drift 单调增、<−3° 单调减;
    相位差 |φ_r−φ_b|∈[0.3π,0.7π](非同步,方程给 0.5π)。"""
    _, rows = run_sustained_glide(600)
    right = left = 0
    for i in range(1, len(rows)):
        dd = rows[i][4] - rows[i - 1][4]
        if rows[i - 1][1] > 3.0:
            right += 1
            assert dd > -1e-6, f"roll={rows[i-1][1]:.2f}°>0 但漂移減少 {dd:.3f}"
        elif rows[i - 1][1] < -3.0:
            left += 1
            assert dd < 1e-6, f"roll={rows[i-1][1]:.2f}°<0 但漂移增加 {dd:.3f}"
    assert right > 20 and left > 20, "两个漂移方向的样本都应充足"
    # -- 相位差:已知频率的正交投影定相(帧数即相位时钟,dt 步长) --
    def phase_at(samples, hz):
        num = den = 0.0
        for t, v in samples:
            num += v * math.cos(6.2831853 * hz * t * DT)
            den += v * math.sin(6.2831853 * hz * t * DT)
        return math.atan2(num, den)          # x=A·sin(ωt+φ) 的 φ
    steady = [r for r in rows if r[0] * DT > 0.8]
    ph_r = phase_at([(r[0], r[1]) for r in steady], GLIDE_ROLL_HZ)
    ph_b = phase_at([(r[0], r[2]) for r in steady], GLIDE_BOB_HZ)
    diff = abs(ph_r - ph_b) % (2.0 * math.pi)
    diff = min(diff, 2.0 * math.pi - diff)
    assert 0.3 * math.pi <= diff <= 0.7 * math.pi, \
        f"roll-bob 相位差 {diff / math.pi:.2f}π 出带 [0.3π,0.7π]"
    print(f"    [coup] 滚右漂右 {right} 帧 / 滚左漂左 {left} 帧;相位差 "
          f"{diff / math.pi:.2f}π ∈ [0.3π,0.7π]")


# ---- ③④ 共用:手工蟑螂滑翔 pose(仿 test_render_budget.make_pose) ----
_SEGS = [(0.0, -38.0, 7.0, 6.0), (0.0, 0.0, 11.0, 9.0),
         (0.0, 26.0, 10.0, 8.0), (-4.0, 44.0, 8.0, 7.0)]
_LEGS = [((-13.0, -26.0), (-26.0, -40.0), 18.0, 20.0),
         ((-15.0, 2.0), (-32.0, -2.0), 19.0, 20.0),
         ((-11.0, 30.0), (-24.0, 42.0), 17.0, 16.0),
         ((13.0, -26.0), (26.0, -40.0), 18.0, 20.0),
         ((15.0, 2.0), (32.0, -2.0), 19.0, 20.0),
         ((11.0, 30.0), (24.0, 42.0), 17.0, 16.0)]
_TRAITS = {"species_id": "species.cockroach", "wing_cover": True,
           "body_len": 115.0, "torso_body": "#6e3413", "leg_edge": "#3a1c0a",
           "haltere": "#8a4a20", "fly_altitude": 38.0,
           "glide_sway_px": 6.5, "glide_sway_hz": 0.4, "glide_bank_k": 1.0}


def make_roach_glide_pose(heading: float = 0.6, wing_phase: float = 0.35,
                          glide: dict | None = None, pitch: float = -9.46,
                          altitude: float = 30.0) -> dict:
    ch, sh = math.cos(heading), math.sin(heading)

    def T(x, y):
        return (x * ch - y * sh, x * sh + y * ch)

    segs = [(*T(x, y), heading, rx, ry) for (x, y, rx, ry) in _SEGS]
    legs = []
    for (ax, ay), (hx, hy), l1, l2 in _LEGS:
        fx, fy = ax + (hx - ax) * 0.5 - 2.0, ay + (hy - ay) * 0.5
        legs.append({"points": [T(ax, ay), T((ax + fx) / 2, (ay + fy) / 2),
                                T(fx, fy),
                                T(fx + ch * 2.2, fy + sh * 2.2)], "lift": 2.5})
    ants = [[T(0.0, -34.0), T(6.0 * ch, -34.0 * sh + 6.0 * sh),
             T(12.0 * ch, 4.0)], [T(0.0, -34.0), T(-6.0 * ch, 4.0),
                                  T(-12.0 * ch, 4.0)]]
    pose = {"half": 120, "altitude": altitude, "segments": segs, "legs": legs,
            "antennae": ants, "pitch": pitch,
            "wings": {"active": True, "phase": wing_phase, "span": 70.0,
                      "fold": 0.0}}
    if glide is not None:
        pose["glide"] = glide
    return pose


def test_3_signature_unfreezes_with_glide_phase() -> None:
    """③ 不同滑翔相位的签名不同;0.5°/0.5px 量化语义;无键 → 前缀兼容。"""
    base = dict(half=120, altitude=30.0,
                segments=make_roach_glide_pose()["segments"],
                legs=make_roach_glide_pose()["legs"],
                antennae=make_roach_glide_pose()["antennae"],
                pitch=-9.46,
                wings={"active": True, "phase": 0.35, "span": 70.0,
                       "fold": 0.0})
    pa = dict(base, glide={"roll_deg": 6.0, "bob_px": 2.5, "drift_px": 8.0,
                           "fade": 1.0})
    pb = dict(base, glide={"roll_deg": 4.5, "bob_px": 2.0, "drift_px": 6.0,
                           "fade": 1.0})
    sa = _pose_signature(pa, _TRAITS, 2)
    sb = _pose_signature(pb, _TRAITS, 2)
    assert sa != sb, "两个滑翔相位的 pose 签名相同 → OPT-07 仍会冻结画面"
    pc = dict(base, glide={"roll_deg": 6.2, "bob_px": 2.6, "drift_px": 8.0,
                           "fade": 0.8})
    assert _pose_signature(pc, _TRAITS, 2) == sa, \
        "0.5°/0.5px 量子内的相位差应命中同一缓存(量化语义)"
    sn = _pose_signature(base, _TRAITS, 2)
    assert sn != sa, "无 glide 键与有键签名应不同"
    diff_idx = [k for k in range(len(sa)) if sa[k] != sn[k]]
    assert len(sa) == len(sn) and diff_idx == [10], \
        "glide 量子为唯一差异槽(ADR-0034:flip 槽已退役)"
    # OPT-07 包装:同签名二次调用命中缓存(返回同一对象),滑翔态可入缓存
    img1 = render_pose_hybrid(pa, _TRAITS)
    img2 = render_pose_hybrid(pa, _TRAITS)
    assert img1 is img2, "同签名滑翔帧应命中 OPT-07 缓存"
    print("    [sig  ] 相位 6.0°/2.5px vs 4.5°/2.0px → 签名不同;量子内命中同签;")


def test_4_render_cost_delta_vs_baseline() -> None:
    """④ 滑翔帧耗对照:V3(带 glide 键,总横滚扫描 ±18°)vs 基线(同 pose
    无键 = v2 等价位)。交替采样同热状态,P50 增幅 ≤1ms。"""
    rolls = [6.0 * math.sin(6.2831853 * 2.2 * i * DT)
             + 15.0 * math.sin(6.2831853 * 0.2 * i * DT) for i in range(180)]
    bobs = [2.5 * math.sin(6.2831853 * 1.6 * i * DT + 0.5 * math.pi)
            for i in range(180)]
    drifts = [8.0 * math.sin(6.2831853 * 2.2 * i * DT + 0.7)
              for i in range(180)]
    # 航向按生产蜿蜒速率缓慢摆动(±0.5rad @0.13Hz,同 _apply_fly yaw meander
    # 量级):10° 朝向桶偶有穿越,不人为制造缓存抖动
    v3 = [make_roach_glide_pose(heading=0.6 + 0.5 * math.sin(
                                    6.2831853 * 0.13 * i * DT),
                                wing_phase=0.35 + i * 0.11,
                                glide={"roll_deg": round(rolls[i], 2),
                                       "bob_px": round(bobs[i], 2),
                                       "drift_px": round(drifts[i], 2),
                                       "fade": 1.0},
                            pitch=-9.46 + 1.2 * math.sin(
                                6.2831853 * 2.2 * i * DT))
          for i in range(180)]
    bas = [make_roach_glide_pose(heading=0.6 + 0.5 * math.sin(
                                     6.2831853 * 0.13 * i * DT),
                                 wing_phase=0.35 + i * 0.11,
                                 pitch=-9.46) for i in range(180)]
    for p in bas + v3:                    # 预热(缓存/解释器)
        _render_pose_full(p, _TRAITS)
    N = 90
    tv, tb = [], []
    for i in range(N):
        j = i % 180
        t0 = time.perf_counter()
        _render_pose_full(v3[j], _TRAITS)
        tv.append((time.perf_counter() - t0) * 1000.0)
        t0 = time.perf_counter()
        _render_pose_full(bas[j], _TRAITS)
        tb.append((time.perf_counter() - t0) * 1000.0)
    tv.sort(); tb.sort()
    p50v, p50b = tv[N // 2], tb[N // 2]
    assert p50v - p50b <= 1.0, \
        f"滑翔 v3 帧耗 P50 {p50v:.2f}ms − 基线 {p50b:.2f}ms = {p50v-p50b:.2f}ms > 1ms"
    print(f"    [perf ] 滑翔 v3 P50={p50v:.2f}ms vs 基线 P50={p50b:.2f}ms "
          f"(Δ={p50v - p50b:+.2f}ms ≤1ms)")


def test_5_touchdown_continuity_no_phase_jump() -> None:
    """⑤ 自然滑翔到触地全程:|Δbob|、|Δdrift| 逐帧受预算;触地/滑停期
    无跳变,滑停后 1.5s 内振幅与漂移归零(不再有残余虚影偏移)。"""
    random.seed(11)
    fsm = FlightStateMachine(dict(PARAMS))
    fsm.start_takeoff()
    prev_bob = prev_drift = 0.0
    max_db = max_dd = 0.0
    touchdown_at = None
    for i in range(int(4.0 / DT)):
        fsm.update(DT, SPEED)
        max_db = max(max_db, abs(fsm.glide_bob - prev_bob))
        max_dd = max(max_dd, abs(fsm.glide_drift - prev_drift))
        prev_bob, prev_drift = fsm.glide_bob, fsm.glide_drift
        if fsm.state is FlightState.GROUNDED and touchdown_at is None:
            touchdown_at = i
        if touchdown_at is not None and i > touchdown_at + int(1.5 / DT):
            break
    assert touchdown_at is not None, "应自然触地"
    assert max_db <= 2.0 and max_dd <= 2.0, \
        f"触地全程连续性破约:bob {max_db:.2f}px / drift {max_dd:.2f}px 每帧"
    assert abs(fsm.glide_drift) < 0.5 and fsm.glide_fade == 0.0, \
        f"滑停后残余:drift={fsm.glide_drift:.2f}px fade={fsm.glide_fade}"
    print(f"    [land ] 触地全程 |Δbob|≤{max_db:.2f}px |Δdrift|≤{max_dd:.2f}px;"
          f"滑停后 drift≈{fsm.glide_drift:.2f}px、fade=0(无残余偏移)")


TESTS = [test_1_oscillation_continuity_and_amplitude,
         test_2_drift_coupling_and_phase_offset,
         test_3_signature_unfreezes_with_glide_phase,
         test_4_render_cost_delta_vs_baseline,
         test_5_touchdown_continuity_no_phase_jump]

if __name__ == "__main__":
    ok = 0
    for t in TESTS:
        print(f"[RUN ] {t.__name__}")
        try:
            t()
            ok += 1
            print(f"[PASS] {t.__name__}")
        except AssertionError as e:
            print(f"[FAIL] {t.__name__}: {e}")
    print(f"\n{ok}/{len(TESTS)} passed")
    sys.exit(0 if ok == len(TESTS) else 1)
