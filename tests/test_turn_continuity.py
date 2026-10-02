"""r24 转向连续性回归:①帧档角速度 ②撞墙枢转 ③僵住快收 ④旋转桶 slack 回退。

背景(活体探针 scratch/_r24_turn_probe.py,真实 _tick 60Hz 实测):旧口径下
蟑螂快转窗显示节奏塌到 ~20fps(桶缺失 67% + 预测跳帧 73% + 被迫渲染
18.8/46.2ms)、撞墙单帧瞬反 ~175°、逃逸僵住入场摆动腿瞬降 ~100-180px——
即用户报障「转向不够连贯,会瞬变」的三条根因。本文件钉住修复语义。
运行:python tests/test_turn_continuity.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from neuropet.core.app import frame_tier_next
from neuropet.core.contracts import Behavior, BehaviorCommand, PetState
from neuropet.core.mathutil import wrap_angle
from neuropet.core.world import WorldModel
from neuropet.body.base import GenericInsectBody
from neuropet.body.gait import LAND_GRACE_TAU, _Foot
from neuropet.render import torso_art
from neuropet.species.cockroach import PARAMS as ROACH

DT = 1.0 / 60.0
SCREEN = (1920, 1080)
TRAITS_ROACH = {"body": "#5a3418", "highlight": "#7a4c24", "dark": "#33200e",
                "legs": "#3c2210", "legs_swing": "#6b4a2a", "antenna": "#241407"}

RESULTS: list[tuple[str, bool, str]] = []


def record(tag: str, ok: bool, detail: str) -> None:
    RESULTS.append((tag, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {tag}: {detail}")


# ① 帧档角速度(纯函数;原地转向不再掉 10fps 档)
def test_tier_turn_aware() -> None:
    cases = [
        # (cur, speed, turn_deg_s, 期望)
        (2, 0.0, 40.0, 1),     # 原地主动转向:10fps → 30fps 档
        (1, 0.0, 25.0, 1),     # 驻留低阈值 20°/s:仍在转向 → 保持 30fps
        (1, 0.0, 10.0, 2),     # 转向已停(回差内)→ 回落 10fps
        (2, 0.0, 0.0, 2),      # 无转向不升档(语义不变)
        (2, 0.0, 25.0, 2),     # 进档高阈值 35°/s:慢转不足以升档(回差)
        (0, 100.0, 500.0, 0),  # 快速移动本就在 60fps 档,不受转向影响
    ]
    for cur, spd, turn, want in cases:
        got = frame_tier_next(cur, spd, False, False, False, turn)
        assert got == want, f"tier({cur},{spd},{turn}) = {got}, 期望 {want}"
    # 旧五参调用(默认 turn=0)语义不变
    assert frame_tier_next(2, 45.0, False, False, False) == 0
    assert frame_tier_next(1, 0.3, False, False, False) == 2
    record("① 帧档角速度", True,
           "原地转向(>35°/s 入档/>20°/s 驻留)保 30fps;无转向语义不变")


# ② 撞墙枢转:朝墙直行,全程单帧朝向变化有界(无 ~175° 瞬反),且确会折返
def test_wall_pivot_continuous() -> None:
    w = WorldModel(*SCREEN)
    st = PetState(pet_id="wp", species_id="wp", pos=(1650.0, 540.0))
    st.heading = 0.0                            # 正东直行,右墙 x=1920-80
    body = GenericInsectBody(st, dict(ROACH))
    view = w.snapshot("wp", 0.0)
    cmd = BehaviorCommand(Behavior.EXPLORE, target=(3000.0, 540.0),
                          intensity=1.0)
    bound_deg = math.degrees(body.PIVOT_RATE + ROACH["turn_rate"]) * DT * 1.15
    max_dh = 0.0
    crossed = False
    turned = False
    for _ in range(300):                        # 5s:抵达墙 + 枢转 + 回头
        prev = st.heading
        body.apply(cmd, view, DT)
        dh = abs(math.degrees(wrap_angle(st.heading - prev)))
        max_dh = max(max_dh, dh)
        assert dh <= bound_deg + 0.5, \
            f"单帧朝向跳 {dh:.1f}° > 界 {bound_deg:.1f}°(枢转应连续)"
        if st.pos[0] >= 1919.5 - 80.0:          # 贴墙线
            crossed = True
        if crossed and abs(wrap_angle(st.heading - 0.0)) > math.pi * 0.6:
            turned = True                       # 已明显折返(>108°)
    margin_ok = (80.0 - 1.0 <= st.pos[0] <= SCREEN[0] - 80.0 + 1.0)
    record("② 撞墙枢转连续", crossed and turned and margin_ok,
           f"触墙={crossed} 折返={turned} 最大单帧Δ={max_dh:.1f}°"
           f"(PIVOT_RATE={math.degrees(body.PIVOT_RATE):.0f}°/s 折算界 "
           f"{bound_deg:.1f}°) 位置界内={margin_ok}")


# ③ 僵住快收:行走中突停,在途摆动腿连续收敛(无瞬降),且不产生新迈步
def test_freeze_grace_landing() -> None:
    w = WorldModel(*SCREEN)
    st = PetState(pet_id="fg", species_id="fg", pos=(500.0, 540.0))
    st.heading = 0.0
    body = GenericInsectBody(st, dict(ROACH))
    view = w.snapshot("fg", 0.0)
    cmd = BehaviorCommand(Behavior.EXPLORE, target=(3000.0, 540.0),
                          intensity=1.0)
    for _ in range(60):                         # 起步,保证有腿在摆动窗内
        body.apply(cmd, view, DT)
    gt = body._gait
    swinging0 = {j for j, f in enumerate(gt._feet) if f.state == _Foot.SWING}
    gap0 = {j: math.dist(gt._feet[j].world, gt._feet[j].target)
            for j in swinging0}
    # 直接以 speed=0 + turn=0 驱动步态(= 僵住/急停口径)。
    # 过拉伸安全阀(over)在静止期本就允许补步(姿态兜底,预存在语义,
    # test_body ⑪ 另测)——本测试只钉连续性:任何足端都不得单帧瞬移。
    step_cap = 0.0
    grace_cap = 0.0
    for i in range(int(0.5 / DT) + 1):
        prev = {j: f.world for j, f in enumerate(gt._feet)}
        gt.update(DT, 0.0, st.pos, st.heading, 0.0)
        for j, f in enumerate(gt._feet):
            d = math.dist(prev[j], f.world)
            step_cap = max(step_cap, d)
            if j in swinging0 and i == 0:
                grace_cap = max(grace_cap, d)
                assert d <= 0.35 * gap0[j] + 0.5, \
                    f"僵住收敛单帧位移 {d:.1f}px > 35% 初始间隙 {gap0[j]:.1f}"
            assert d <= 40.0, f"足 {j} 单帧瞬移 {d:.1f}px(旧瞬降缺陷回归)"
    all_down = all(f.state == _Foot.PLANTED for f in gt._feet)
    record("③ 僵住快收", bool(swinging0) and all_down,
           f"入场摆动腿={len(swinging0)} 快收首帧最大位移={grace_cap:.1f}px"
           f"(τ={LAND_GRACE_TAU*1000:.0f}ms,界=35%初始间隙) "
           f"全程单帧上限={step_cap:.1f}px(<40 无瞬移) 0.5s 内全落={all_down}")


# ④ 旋转桶 slack 回退:快转未命中精确桶时取最近已缓存桶(位图等价),不烘焙
def test_rot_slack_fallback() -> None:
    torso_art.invalidate()
    torso_art._angle_step = 1
    a = torso_art.get_torso("species.cockroach", 60, 100.0, 1.0, TRAITS_ROACH)
    n0 = len(torso_art._rot)
    b = torso_art.get_torso("species.cockroach", 60, 102.4, 1.0, TRAITS_ROACH,
                            slack_deg=4.0)
    n1 = len(torso_art._rot)
    same = np.array_equal(np.asarray(a), np.asarray(b))
    no_bake = (n1 == n0)
    torso_art.get_torso("species.cockroach", 60, 102.4, 1.0, TRAITS_ROACH)
    n2 = len(torso_art._rot)
    record("④ 桶 slack 回退", same and no_bake and n2 > n1,
           f"slack 取邻桶位图等价={same} 未新增条目={no_bake}"
           f"({n0}→{n1}) slack=0 时正常烘焙({n1}→{n2})")
    torso_art.invalidate()


def main() -> None:
    test_tier_turn_aware()
    test_wall_pivot_continuous()
    test_freeze_grace_landing()
    test_rot_slack_fallback()
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n[转向连续性] {len(RESULTS) - len(failed)}/{len(RESULTS)} 项通过")
    if failed:
        for tag, _, d in failed:
            print(f"  FAIL: {tag} — {d}")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
