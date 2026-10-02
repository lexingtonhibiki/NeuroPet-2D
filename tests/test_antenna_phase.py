# -*- coding: utf-8 -*-
"""触角相位积分判据 U3(tests/test_antenna_phase.py;架构 r26-visible-arch-r1 §1.2+§1.5)。

workflow §6 两句(纸板防线):
- 什么情况下会红:NEUROPET_ANT_PHASE_INTEG=0(旧 t*hz 路)跑判据 A ⇒
  arousal 阶跃帧梢端位移远超正常单帧位移,红在位移行,不红在别处。
- 什么情况下会绿:开关=1 ⇒ 判据 A 阶跃位移 ≤ 正常单帧位移+1e-6 且判据 B
  逐帧差 ≤1e-9;当次负向对照(开关=0)必须观测到红,否则本判据作废。

运行:python tests/test_antenna_phase.py(断言脚本直跑,非 pytest)。
本文件默认走积分路(os.environ.setdefault(..., "1")):直跑绿;
显式 NEUROPET_ANT_PHASE_INTEG=0 跑 ⇒ 判据 A 变红(负向对照的红绿分开证据)。
"""
from __future__ import annotations

import math
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("NEUROPET_ANT_PHASE_INTEG", "1")
os.environ["NEUROPET_EMO_GAIT"] = "1"  # arousal 通道输入缝钉死

from neuropet.body.base import GenericInsectBody  # noqa: E402
from neuropet.core.contracts import PetState  # noqa: E402
from neuropet.core.world import WorldModel  # noqa: E402
from neuropet.species.cockroach import PARAMS as ROACH  # noqa: E402

DT = 1.0 / 60.0
TWO_PI = 6.2831853  # 与 base.py 同字面量(同口径,不另取 math.pi)


def make_body():
    random.seed(20260923)
    w = WorldModel(1920, 1080)
    st = PetState(pet_id="roach", species_id="roach", pos=(960.0, 540.0))
    body = GenericInsectBody(st, dict(ROACH))
    world = w.snapshot("roach", 0.0)
    return body, world


def settle_rest_arousal0(body, world, frames=30):
    """中性情绪 + 静止 + rest 姿态暖机(姿态/crossfade 定态,sweep_hz 恒 0.7)。"""
    body.set_emotion_gait(0.0, 0.0, 0.0)
    for _ in range(frames):
        body._integrate(DT, world)


def tip(body):
    """单侧触角梢端世界坐标(恒等 to_screen:只量相对位移,与投影无关)。"""
    out = body._antennae_pose(lambda x, y: (x, y))
    return out[0][-1]


def disp(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def test_step_no_jump() -> None:
    """判据 A(主):恒定 sweep_hz 下 arousal 0→1 瞬切,阶跃帧梢端位移无跳变。"""
    from neuropet.body.base import ant_phase_integ_on
    body, world = make_body()
    settle_rest_arousal0(body, world)
    # 把 _ant_t 抬到会话量级(旧路 Δphase=_ant_t×Δhz,不抬则跳变不够大)。
    body._ant_t = 60.0
    settle_rest_arousal0(body, world, frames=5)
    hz0, _ = body._antenna_sweep_hz()
    p0 = tip(body)
    body.set_emotion_gait(0.0, 0.0, 1.0)  # arousal 瞬切:sweep_hz 阶跃
    hz1, _ = body._antenna_sweep_hz()
    assert hz1 > hz0, f"阶跃未生效 hz {hz0}→{hz1}"
    body._integrate(DT, world)
    p1 = tip(body)
    step = disp(p0, p1)
    # 同 hz 下正常单帧位移预算(阶跃后连跑 120 帧取最大)。
    prev, mx = p1, 0.0
    for _ in range(120):
        body._integrate(DT, world)
        cur = tip(body)
        mx = max(mx, disp(prev, cur))
        prev = cur
    tag = "integ" if ant_phase_integ_on() else "old"
    print(f"    [A/{tag}] hz {hz0:.4f}→{hz1:.4f} 阶跃位移 {step:.6f}px, "
          f"正常单帧最大 {mx:.6f}px")
    assert step <= mx + 1e-6, \
        f"触角阶跃跳变:阶跃位移 {step:.6f} > 正常单帧 {mx:.6f}+1e-6"


def test_phase_matches_analytic() -> None:
    """判据 B(回归守卫):sweep_hz 恒定时积分相位 ≡ t*hz 解析值(逐帧 ≤1e-9)。"""
    body, world = make_body()
    settle_rest_arousal0(body, world)
    body._ant_t, body._ant_phase, body._ant_flutter_phase = 5.0, 0.0, 0.0
    t0, ph0, fl0 = body._ant_t, body._ant_phase, body._ant_flutter_phase
    worst, worst_fl = 0.0, 0.0
    for _ in range(120):
        body._integrate(DT, world)
        hz, _ = body._antenna_sweep_hz()
        assert hz == 0.7, f"sweep_hz 应恒 0.7,实测 {hz}"
        exp = ph0 + (body._ant_t - t0) * hz * TWO_PI
        exp_fl = fl0 + (body._ant_t - t0) * 8.0 * TWO_PI
        worst = max(worst, abs(body._ant_phase - exp))
        worst_fl = max(worst_fl, abs(body._ant_flutter_phase - exp_fl))
    print(f"    [B] 主扫逐帧差最大 {worst:.2e},颤动逐帧差最大 {worst_fl:.2e}")
    assert worst <= 1e-9, f"积分相位偏离解析值 {worst:.2e} > 1e-9"
    assert worst_fl <= 1e-9, f"颤动积分相位偏离 {worst_fl:.2e} > 1e-9"


def test_negative_control_old_path_jumps() -> None:
    """掐线法:开关=0(旧路)同构造必须变红,证明判据守的是承诺层。"""
    os.environ["NEUROPET_ANT_PHASE_INTEG"] = "0"
    try:
        try:
            test_step_no_jump()
        except AssertionError as exc:
            print(f"    [neg] 旧路如期变红(位移行):{exc}")
            return
        raise AssertionError("负向对照失效:旧路未变红,判据 A 可能是纸板")
    finally:
        os.environ["NEUROPET_ANT_PHASE_INTEG"] = "1"


def main() -> int:
    from neuropet.body.base import ant_phase_integ_on
    print(f"[info] NEUROPET_ANT_PHASE_INTEG={'1' if ant_phase_integ_on() else '0'}")
    fns = [test_step_no_jump, test_phase_matches_analytic,
           test_negative_control_old_path_jumps]
    for fn in fns:
        fn()
        print(f"[OK] {fn.__name__}")
    print(f"[PASS] {len(fns)}/{len(fns)} 触角相位判据")
    return 0


if __name__ == "__main__":
    sys.exit(main())
