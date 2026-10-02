"""真 3D 腿链验收(腿部 3D 运动学重构波;规格 docs/references/三维腿链实施规格.md
§6 断言清单,数值权威 R1 §7)。运行:python tests/test_leg3d.py

八组断言(组号对应规格 §6 表,按两处裁决与现行几何适配处均在注释注明):
  ① 投影数学:solve→FK 往返一致(<1e-6);踝落在髋→踝射线上;φ=0 时
     |膝−踝|3D = t3d;points 长度=8 且全部有限;
  ② ROM 钳制:两物种 6 腿 × 网格目标:α/β/γ 全落 rom3d(±1°);远目标
     clamped=True;无 NaN;rom3d 缺省回退 = 现行 FEMUR/TIBIA 常量(裁决 #2);
  ③ 世界钉足无滑步:TripodGait+新求解器巡航 300 帧:支撑相爪尖/踝(世界逆
     变换)漂移 <1px;both_groups==0;静止不乱步、位移触发迈步;
  ④ 静息:rest_local 处 solve 的爪尖**逐位**回到钉点;方位=yaw0;距离=
     min(|home−attach|, 0.78×reach);rest_local 钳制决策与 gait 安全阀
     不产生异常(裁决 #1 冒烟;绝对照片带 39/54/62、54/118/159 的重对齐
     属 L2 校准波,见 test_body ⑧ 现行几何带);
  ⑤ 双管线/同构:gait_sim consistency_max_error(roach/fruitfly × 默认表)
     <1e-6;NEUROPET_LEG3D=0 时 solve() 分派与 _solve_legacy 逐位一致;
  ⑥ 性能预算:solve() 6 腿×2000 帧均值 <0.5ms/帧(渲染半由
     tests/test_render_budget.py ⑩/② 承担:蟑 P50≤7ms、跗链增量 ≤30 line);
  ⑦ 果蝇:6 腿 solve 有限;d_max3 > 静息距(规格 §4.6);渲染跗链可见
     R_vis ≤0.11BL(NMF 全跗链×折叠系数 0.35,校准前初值);
  ⑧ 缩放不变量:SkeletonSpec scaled(k) 后 len3d×k、rom3d/rest3d/tarsus_seg
     不变;schema v2 双版本可读。
"""
from __future__ import annotations

import math
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from neuropet.core.contracts import Behavior, BehaviorCommand, PetState
from neuropet.core.world import WorldModel
from neuropet.body.base import GenericInsectBody
from neuropet.body.gait import TripodGait
from neuropet.body.gait_profile import DEFAULT_PARAMS
from neuropet.body.kinematics import FEMUR_RANGE, TIBIA_RANGE, \
    LegKinematics, _leg3d_enabled
from neuropet.body.gait_sim import consistency_max_error
from neuropet.body.rig import SkeletonSpec
from neuropet.species.cockroach import PARAMS as ROACH
from neuropet.species.fruitfly import PARAMS as FLY

DT = 1.0 / 60.0


def _kins(params: dict) -> list[tuple[dict, LegKinematics]]:
    return [(leg, LegKinematics(leg)) for leg in params["legs"]]


# ---------------------------------------------------------------- ① 投影数学
def test_fk_roundtrip() -> None:
    for params, tag in ((ROACH, "roach"), (FLY, "fly")):
        for leg, kin in _kins(params):
            ex, ey = kin.hip3d[0], kin.hip3d[1] * kin.side     # 腿系有效髋
            r_vis = kin.tarsus_ext * kin.tarsus3d
            tgt = (kin.home[0] + 6.0, kin.home[1] + 5.0)
            for lift, tdir in ((0.0, None), (0.0, 2.1), (3.0, None)):
                r = kin.solve(tgt, lift, tarsus_dir=tdir)
                pts = r["points"]
                assert len(pts) == 8, f"{tag}/{leg['kind']}: 应输出 8 点"
                assert all(math.isfinite(v) for p in pts for v in p)
                a3 = r["angles3d"]
                assert len(a3) == 5, "angles3d = (α,β,γ,δ,φ)"
                alpha, beta, gamma, _delta, phi = (math.radians(v) for v in a3)
                # 链起点:pts[0]=attach、pts[1]=CTr=有效髋(身体系)
                assert abs(pts[0][0] - kin.attach[0]) < 1e-9
                assert abs(pts[0][1] - kin.attach[1]) < 1e-9
                assert abs(pts[1][0] - kin.hip3d[0]) < 1e-9
                assert abs(pts[1][1] - kin.hip3d[1]) < 1e-9
                # 以下比较统一转回**腿系**(镜像 y×side;α/φ 均为腿系角)
                ank_l = (pts[3][0], pts[3][1] * kin.side)
                knee_l = (pts[2][0], pts[2][1] * kin.side)
                dt_x, dt_y = (math.cos(tdir * kin.side), math.sin(tdir * kin.side)) \
                    if tdir is not None else (math.cos(kin.yaw0), math.sin(kin.yaw0))
                d_re = math.hypot(ank_l[0] - ex, ank_l[1] - ey)
                ang_re = math.atan2(ank_l[1] - ey, ank_l[0] - ex)
                assert abs(math.atan2(math.sin(ang_re - alpha),
                                      math.cos(ang_re - alpha))) < 1e-9, \
                    "踝应落在髋→踝射线(方位 α)上"
                # FK 往返:由 angles3d 正向重建 8 点与 points 一致(<1e-6)
                k_re = (ex + math.cos(alpha - phi) * kin.f3d * math.cos(beta),
                        ey + math.sin(alpha - phi) * kin.f3d * math.cos(beta))
                a_re = (ex + math.cos(alpha) * d_re, ey + math.sin(alpha) * d_re)
                assert abs(k_re[0] - knee_l[0]) < 1e-6 and abs(k_re[1] - knee_l[1]) < 1e-6, \
                    "膝 FK 往返不一致"
                assert abs(a_re[0] - ank_l[0]) < 1e-9 and abs(a_re[1] - ank_l[1]) < 1e-9, \
                    "踝 FK 往返不一致"
                for idx, cum in zip((4, 5, 6, 7), kin.tarsus_cum + (1.0,)):
                    m_l = (pts[idx][0], pts[idx][1] * kin.side)
                    m_re = (a_re[0] + dt_x * r_vis * cum,
                            a_re[1] + dt_y * r_vis * cum)
                    assert abs(m_re[0] - m_l[0]) < 1e-6 \
                        and abs(m_re[1] - m_l[1]) < 1e-6, \
                        f"跗链点 {idx} FK 往返不一致"
            # φ=0 退化:膝与踝同方位,|膝−踝| 的链长 = t3d(闭式三角闭合)
            kin0 = LegKinematics(dict(leg, femur_lead_k=0.0))
            r0 = kin0.solve(tgt, 0.0)
            p0 = r0["points"]
            beta0 = r0["angles"][1]
            h0 = max(kin0.height3d, 0.30 * kin0.height3d)     # lift=0
            d0 = math.hypot(p0[3][0] - ex,
                            p0[3][1] * kin.side - ey)
            kx3d = kin0.f3d * math.cos(math.radians(beta0))
            kz3d = kin0.f3d * math.sin(math.radians(beta0))
            seg = math.sqrt((kx3d - d0) ** 2 + (kz3d - h0) ** 2)
            assert abs(seg - kin0.t3d) < 1e-6, \
                f"φ=0 时 |膝−踝| {seg:.6f} 应 = t3d {kin0.t3d}"
    print("    [fk] solve→FK 往返 <1e-6、踝在射线上、φ=0 闭合、8 点有限 全通过")


# ---------------------------------------------------------------- ② ROM 钳制
def test_rom_clamp() -> None:
    for params, tag in ((ROACH, "roach"), (FLY, "fly")):
        for leg, kin in _kins(params):
            fem_lo, fem_hi = kin.rom3d["femur_pitch"]
            knee_lo, knee_hi = kin.rom3d["knee"]
            for ddx in (-80, -40, -17, 0, 17, 40, 90):
                for ddy in (-90, -45, 0, 45, 90):
                    tgt = (kin.home[0] + ddx, kin.home[1] + ddy)
                    r = kin.solve(tgt, lift=1.5)
                    a = r["angles3d"]
                    assert all(math.isfinite(v) for v in a), "angles3d 含非有限值"
                    yaw_d, beta_d, gamma_d = r["angles"]
                    dyaw = math.degrees(math.atan2(
                        math.sin(math.radians(yaw_d - math.degrees(kin.yaw0))),
                        math.cos(math.radians(yaw_d - math.degrees(kin.yaw0)))))
                    env_rear, env_front = kin.rom3d["yaw"]
                    assert -env_front - 1.0 <= dyaw <= env_rear + 1.0, \
                        f"{tag}/{leg['kind']}: yaw 偏差 {dyaw:.1f}° 越 rom3d.yaw"
                    assert fem_lo - 1.0 <= beta_d <= fem_hi + 1.0, \
                        f"{tag}/{leg['kind']}: β {beta_d:.1f}° 越 rom3d.femur_pitch"
                    assert knee_lo - 1.0 <= gamma_d <= knee_hi + 1.0, \
                        f"{tag}/{leg['kind']}: γ {gamma_d:.1f}° 越 rom3d.knee"
            far = kin.solve((kin.attach[0] + 10 * kin.reach, kin.home[1]), 0.0)
            assert far["clamped"], "远目标应报告钳制"
    # rom3d 缺省回退 = 现行 FEMUR/TIBIA 常量(裁决 #2)
    kin_def = LegKinematics({"kind": "mid", "attach": (0.0, 12.0),
                             "home": (0.0, 30.0), "l1": 20.0, "l2": 22.0})
    assert kin_def.rom3d["femur_pitch"] == FEMUR_RANGE
    assert kin_def.rom3d["knee"] == TIBIA_RANGE
    r = kin_def.solve((25.0, 40.0), 0.0)
    assert FEMUR_RANGE[0] - 1.0 <= r["angles"][1] <= FEMUR_RANGE[1] + 1.0
    assert TIBIA_RANGE[0] - 1.0 <= r["angles"][2] <= TIBIA_RANGE[1] + 1.0
    print("    [rom] 网格目标 α/β/γ 全落 rom3d(±1°)、远目标钳制、缺省回退常量 全通过")


# ---------------------------------------------------------------- ③ 世界钉足
def test_world_pinned_no_slide() -> None:
    groups = [leg["group"] for leg in ROACH["legs"]]
    for params, tag in ((ROACH, "roach"), (FLY, "fly")):
        w = WorldModel(1920, 1080)
        st = PetState(pet_id=tag, species_id=tag, pos=(500.0, 540.0))
        st.heading = 0.0
        body = GenericInsectBody(st, dict(params))
        view = w.snapshot(tag, 0.0)
        cmd = BehaviorCommand(Behavior.EXPLORE, target=(3000.0, 540.0),
                              intensity=1.0)
        for _ in range(90):
            body.apply(cmd, view, DT)
        prev_claw: dict[int, tuple[float, float]] = {}
        prev_ankle: dict[int, tuple[float, float]] = {}
        both_groups = 0
        for _ in range(300):
            body.apply(cmd, view, DT)
            gt = body._gait
            pose = body.pose()
            sw = [0, 0]
            for j, foot in enumerate(gt._feet):
                if foot.state == 0:
                    sw[groups[j]] += 0            # 支撑相
                else:
                    sw[groups[j]] += 1
            if sw[0] and sw[1]:
                both_groups += 1
            hx, hy = st.pos
            ch, sh = math.cos(st.heading), math.sin(st.heading)
            for j, leg_out in enumerate(pose["legs"]):
                if gt._feet[j].state == 0 and leg_out["lift"] <= 0.01:
                    pts = leg_out["points"]
                    for key, prev, pidx in (("claw", prev_claw, -1),
                                            ("ankle", prev_ankle, 3)):
                        px, py = pts[pidx]
                        wx = hx + px * ch - py * sh
                        wy = hy + px * sh + py * ch
                        if j in prev:
                            d = math.hypot(prev[j][0] - wx, prev[j][1] - wy)
                            assert d < 1.0, \
                                f"{tag} 支撑相 {key} 世界漂移 {d:.2f}px(腿 {j})"
                        prev[j] = (wx, wy)
                else:
                    prev_claw.pop(j, None)
                    prev_ankle.pop(j, None)
        assert both_groups == 0, f"{tag} 两组摆动窗重叠 {both_groups} 帧"
        # 静止不乱步
        for _ in range(30):
            body.apply(BehaviorCommand(Behavior.IDLE, priority=1), view, DT)
        lifts = 0
        for _ in range(90):
            body.apply(BehaviorCommand(Behavior.IDLE, priority=1), view, DT)
            lifts += sum(1 for lg in body.pose()["legs"] if lg["lift"] > 0.1)
        assert lifts == 0, f"{tag} 静止期出现 {lifts} 次抬腿"
        # 位移(原地转向)触发迈步
        turns = 0
        for _ in range(90):
            body.apply(BehaviorCommand(Behavior.TURN,
                                       target=(st.pos[0], st.pos[1] - 400.0),
                                       priority=10), view, DT)
            turns += sum(1 for lg in body.pose()["legs"] if lg["lift"] > 0.1)
        assert turns > 10, f"{tag} 原地转向未触发迈步({turns})"
    print("    [pinned] 300 帧巡航支撑相爪尖/踝漂移 <1px、三角互锁、静止/转向 全通过")


# ---------------------------------------------------------------- ④ 静息钉点
def test_rest_pinned_and_clamp_smoke() -> None:
    for params, tag in ((ROACH, "roach"), (FLY, "fly")):
        g = TripodGait(params)
        kins = [LegKinematics(leg) for leg in params["legs"]]
        g.bind_legs(kins, [int(leg["group"]) for leg in params["legs"]])
        for leg, kin, foot in zip(params["legs"], kins, g._feet):
            rest = foot.rest_local
            r = kin.solve(rest, 0.0, tarsus_dir=None)
            pts = r["points"]
            tip = pts[-1]
            assert math.hypot(tip[0] - rest[0], tip[1] - rest[1]) < 1e-9, \
                f"{tag}/{leg['kind']}: 静息钉点未逐位复原(爪尖 {tip} vs {rest})"
            # 方位 = yaw0(静息足向由 attach/home 几何定义;同在腿系内比较)
            ang = math.degrees(math.atan2(
                (tip[1] - kin.attach[1]) * kin.side,
                tip[0] - kin.attach[0]))
            yaw0 = math.degrees(math.atan2(
                (rest[1] - kin.attach[1]) * kin.side,
                rest[0] - kin.attach[0]))
            assert abs(math.atan2(math.sin(math.radians(ang - yaw0)),
                                  math.cos(math.radians(ang - yaw0)))) < 0.5, \
                f"{tag}/{leg['kind']}: 静息方位 {ang:.2f}° ≠ yaw0 {yaw0:.2f}°"
            # 踝未越环带(无 d 钳制 → 钉点保真;裁决 #1 冒烟:钳制决策无异常)
            d_ank = math.hypot(pts[3][0] - kin.hip3d[0],
                               pts[3][1] - kin.hip3d[1])
            assert kin.d_min3 - 1e-6 <= d_ank <= kin.d_max3 + 1e-6, \
                f"{tag}/{leg['kind']}: 静息踝距 {d_ank:.2f} 越 " \
                f"[{kin.d_min3:.2f},{kin.d_max3:.2f}]"
            # 距离结构:rest_local = min(|home−attach|, 0.78×reach)(gait 侧已钳)
            d_home = math.hypot(leg["home"][0] - kin.attach[0],
                                leg["home"][1] - kin.attach[1])
            expect = min(d_home, 0.78 * kin.reach)
            assert abs(math.hypot(rest[0] - kin.attach[0], rest[1] - kin.attach[1])
                       - expect) < 1e-6, "rest_local 应为 min(home距, 0.78×reach)"
            # gait 安全阀口径:over_k(0.88)×reach 高于静息距(结构性余量)
            assert expect <= 0.88 * kin.reach + 1e-9, \
                f"{tag}/{leg['kind']}: 静息距越 0.88 安全阀"
    print("    [rest] 静息钉点逐位复原/方位=yaw0/踝域零钳制/安全阀余量 全通过")


# ---------------------------------------------------------------- ⑤ 双管线/同构
def test_consistency_and_legacy_dispatch() -> None:
    worst = 0.0
    for species, params in (("cockroach", ROACH), ("fruitfly", FLY)):
        spp_speeds = (float(params["cruise"]), float(params["sprint"]))
        for v in spp_speeds:
            err, n = consistency_max_error(species, dict(DEFAULT_PARAMS), v, 300)
            worst = max(worst, err)
            assert err < 1e-6, \
                f"{species}@{v:.0f}px/s gait_sim 一致性偏差 {err:.2e}px(≥1e-6)"
    print(f"    [iso] gait_sim↔TripodGait 最大偏差 {worst:.2e}px(<1e-6,4 组合)")
    # LEG3D=0 分派一致性:solve() ≡ _solve_legacy(逐位)
    old = os.environ.get("NEUROPET_LEG3D")
    os.environ["NEUROPET_LEG3D"] = "0"
    try:
        assert not _leg3d_enabled()
        for params, _tag in ((ROACH, "roach"), (FLY, "fly")):
            for leg, _kin in _kins(params):
                kin = LegKinematics(leg)
                for tgt in ((kin.home[0] + 6.0, kin.home[1] + 5.0),
                            (kin.attach[0] + 10 * kin.reach, kin.home[1]),
                            (kin.home[0] - 30.0, kin.home[1] + 20.0)):
                    a = kin.solve(tgt, 1.5)
                    b = kin._solve_legacy(tgt, 1.5, None)
                    assert a["points"] == b["points"] and a["angles"] == b["angles"] \
                        and a["clamped"] == b["clamped"], \
                        "LEG3D=0 下 solve() 应与 _solve_legacy 逐位一致"
                    assert len(a["points"]) == 4, "回退模式应输出 4 点"
    finally:
        if old is None:
            os.environ.pop("NEUROPET_LEG3D", None)
        else:
            os.environ["NEUROPET_LEG3D"] = old
    assert _leg3d_enabled(), "环境恢复后应回到 3D 缺省"
    print("    [legacy] NEUROPET_LEG3D=0 solve()≡_solve_legacy(逐位,4 点) 全通过")


# ---------------------------------------------------------------- ⑥ 性能预算
def test_solver_budget() -> None:
    kins = [kin for _leg, kin in _kins(ROACH)]
    targets = [(kin.home[0] + 6.0, kin.home[1] + 5.0) for kin in kins]
    n_frames = 2000
    for kin in kins:                       # 预热(代码路径/分配器)
        kin.solve(targets[0], 0.0)
    t0 = time.perf_counter()
    for f in range(n_frames):
        lift = (f % 7) / 7.0 * 3.0
        for kin, tgt in zip(kins, targets):
            kin.solve(tgt, lift)
    dt_ms = (time.perf_counter() - t0) * 1000.0 / n_frames
    assert dt_ms < 0.5, f"6 腿 solve 均值 {dt_ms:.3f}ms/帧 超出 0.5ms 预算"
    print(f"    [perf] 6 腿 solve × {n_frames} 帧:{dt_ms:.4f}ms/帧(<0.5ms 预算;" 
          f"渲染半见 tests/test_render_budget.py ⑩/②)")


# ---------------------------------------------------------------- ⑦ 果蝇
def test_fly_flat_constraints() -> None:
    fly_legs = _kins(FLY)
    for leg, kin in fly_legs:
        for ddx in (-10, -5, 0, 5, 10):
            for ddy in (-10, -5, 0, 5, 10):
                r = kin.solve((kin.home[0] + ddx, kin.home[1] + ddy), 1.0)
                assert len(r["points"]) == 8
                assert all(math.isfinite(v) for p in r["points"] for v in p)
        # d_max3 > 静息距(规格 §4.6:落点钳制域覆盖步距)
        d_rest = math.hypot(leg["home"][0] - kin.attach[0],
                            leg["home"][1] - kin.attach[1])
        d_rest_c = min(d_rest, 0.78 * kin.reach)
        assert kin.d_max3 > d_rest_c, \
            f"{leg['kind']}: d_max3 {kin.d_max3:.2f} ≤ 静息距 {d_rest_c:.2f}"
        assert kin.d_min3 < d_rest_c, \
            f"{leg['kind']}: d_min3 {kin.d_min3:.2f} ≥ 静息距 {d_rest_c:.2f}"
        # 渲染跗链可见长度 = R_vis = tarsus_ext×tarsus3d ≤0.11BL
        # (NMF 全跗链 0.65~0.77mm × 折叠系数 0.35;校准前初值,L2 定冻结)
        r_vis = kin.tarsus_ext * kin.tarsus3d
        assert r_vis <= FLY["body_len"] * 0.11 + 1e-6, \
            f"{leg['kind']}: R_vis {r_vis:.2f}px 超 0.11BL"
    # 渲染跗节 line 数(≤8/腿)由 tests/test_render_budget.py ⑩ 黑盒断言,此处不重复
    print("    [fly] 6 腿 solve 有限、d_max3>静息距>d_min3、R_vis≤0.11BL 全通过")


# ---------------------------------------------------------------- ⑧ 缩放不变量
def test_scaling_invariants() -> None:
    for species, params in (("cockroach", ROACH), ("fruitfly", FLY)):
        spec = SkeletonSpec.from_params(species, params)
        assert spec.spec["schema_version"] == 2
        l0 = spec.spec["legs"][0]
        assert "len3d" in l0 and "rest3d" in l0 and "rom3d" in l0 \
            and "tarsus_seg" in l0, "v3 腿 spec 应含 len3d/rest3d/rom3d/tarsus_seg"
        for k in (0.5, 0.75, 1.5, 2.0):
            s = spec.scaled(k)
            l0s = s.spec["legs"][0]
            for lk in ("coxa", "femur", "tibia", "tarsus"):
                assert abs(l0s["len3d"][lk] - l0["len3d"][lk] * k) < 1e-6, \
                    f"{species} len3d.{lk} 应 ×k"
            assert l0s["rest3d"] == l0["rest3d"], "rest3d 与尺度无关"
            assert l0s["rom3d"] == l0["rom3d"], "rom3d 与尺度无关"
            assert l0s["tarsus_seg"] == l0["tarsus_seg"], "tarsus_seg 与尺度无关"
            assert abs(l0s["reach"] - l0["reach"] * k) < 1e-6, "reach 应 ×k"
    # v1 spec(无 v3 键)仍可读(加法式扩展)
    v1_spec = SkeletonSpec.from_params("fruitfly", FLY).to_dict()
    v1_spec["schema_version"] = 1
    for lg in v1_spec["legs"]:
        for key in ("len3d", "rest3d", "rom3d", "tarsus_seg", "trochanter",
                    "claw", "diam", "tarsomeres", "ik"):
            lg.pop(key, None)
    SkeletonSpec(v1_spec)                  # 不抛错即可
    print("    [scale] len3d×k / rest3d/rom3d/tarsus_seg 不变 / v1 spec 可读 全通过")


TESTS_3D = [test_fk_roundtrip, test_rom_clamp, test_world_pinned_no_slide,
            test_rest_pinned_and_clamp_smoke, test_consistency_and_legacy_dispatch,
            test_solver_budget, test_fly_flat_constraints, test_scaling_invariants]


def main() -> None:
    if os.environ.get("NEUROPET_LEG3D", "") != "2":
        # 波间裁决(L2F §七 / 通告 §6):缺省世界 = v2 七点链(ADR-0026/0027),
        # v3 八点链为 NEUROPET_LEG3D=2 休眠开发档。本文件断言以 v3 为前提
        # (含"LEG3D=0≡v1"旧回滚语义,与两波规格 §7.1 相斥),缺省世界不适用;
        # LEG3D=0 回退契约已由 tests/test_body.py(11/11)覆盖。v3 轨道恢复时
        # 按 docs/三维腿链v3合并状态与协调通告.md §6/§7 全量验收后再启用。
        print("[SKIP] NEUROPET_LEG3D 未启用:缺省世界=v2,本文件仅在其 =2 开发档运行")
        sys.exit(0)
    failed = 0
    for fn in TESTS_3D:
        try:
            fn()
            print(f"[ok] {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"[FAIL] {fn.__name__}: {exc}")
        except Exception as exc:
            failed += 1
            print(f"[ERROR] {fn.__name__}: {type(exc).__name__}: {exc}")
    if failed:
        print(f"腿链 3D 测试:{len(TESTS_3D) - failed}/{len(TESTS_3D)} 通过")
        sys.exit(1)
    print(f"腿链 3D 测试:全部 {len(TESTS_3D)} 项通过")
    sys.exit(0)


if __name__ == "__main__":
    main()
