"""神经拟真度验收:环形吸引子 / 三因子可塑 / 习惯化。

断言取自本轮调研规格:
  * `docs/references/神经拟真度审计与升级方案.md`(R1 §4 验收指标 1/3/4/6)
  * `docs/references/学习系统审计与在线学习规格.md`(R2 §4 断言 ①②③④⑤)

直跑:`python tests/test_neuro_realism.py`
"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from neuropet.brain.cx_ring import CXRingAttractor, heading_cue
from neuropet.brain.habituation import Habituation
from neuropet.brain.plasticity import (EligibilityTrace, Neuromodulator,
                                       rpe, three_factor_dw)

FAILS: list[str] = []
DT = 1.0 / 60.0
TAU = math.tau


def check(cond, msg: str) -> None:
    if cond:
        print(f"  [ok] {msg}")
    else:
        print(f"  [FAIL] {msg}")
        FAILS.append(msg)


def wrap(a: float) -> float:
    return (a + math.pi) % TAU - math.pi


def pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    sxx = math.sqrt(sum((a - mx) ** 2 for a in xs))
    syy = math.sqrt(sum((b - my) ** 2 for b in ys))
    return sxy / (sxx * syy) if sxx > 0 and syy > 0 else 0.0


# ------------------------------------------------------------------ ① 持久活动
def test_cx_persistence() -> None:
    print("[1] CX 环形吸引子:无输入时的持久活动(Seelig 2015)")
    ring = CXRingAttractor()
    ring.set_heading(1.0)
    h0 = ring.decoded()
    for _ in range(int(5.0 / DT)):          # 5s 无输入
        ring.step(DT, omega=0.0)
    drift = abs(wrap(ring.decoded() - h0))
    coh = ring.coherence()
    check(coh >= 0.5, f"5s 后 bump 仍在(环状一致性 {coh:.3f} ≥ 0.5)")
    check(drift <= 0.4, f"5s 无输入航向漂移 {math.degrees(drift):.1f}° ≤ 23°")
    check(ring.bump_amp() > 0.1,
          f"峰谷比 {ring.bump_amp():.3f} > 0.1(仍是局部包,未摊平)")


# ------------------------------------------------------------------ ② 路径积分
def test_cx_path_integration() -> None:
    print("[2] CX:角速度路径积分(Turner-Evans 2017)")
    ring = CXRingAttractor()
    ring.set_heading(0.0)
    omega = 0.6                              # rad/s
    n_steps = int(6.0 / DT)
    dec_unwrap = 0.0
    prev = ring.decoded()
    xs: list[float] = []
    ys: list[float] = []
    for i in range(n_steps):
        ring.step(DT, omega=omega)
        cur = ring.decoded()
        dec_unwrap += wrap(cur - prev)
        prev = cur
        t = (i + 1) * DT
        xs.append(t * omega)
        ys.append(dec_unwrap)
    r = pearson(xs, ys)
    final_err = abs(ys[-1] - xs[-1])
    check(r >= 0.95, f"解算航向 vs 解析积分 Pearson r={r:.4f} ≥ 0.95")
    check(final_err <= 0.6,
          f"6s 累计角误差 {math.degrees(final_err):.1f}° (积分 {math.degrees(xs[-1]):.0f}°)")


# ------------------------------------------------------------------ ③ 单峰性
def test_cx_single_bump() -> None:
    print("[3] CX:单峰性(局部兴奋 + 全局抑制)")
    ring = CXRingAttractor()
    ring.v = [1.0] * ring.n                  # 均匀初始化(最坏情况)
    for _ in range(int(3.0 / DT)):
        ring.step(DT, omega=0.0)
    coh = ring.coherence()
    amp = ring.bump_amp()
    check(coh >= 0.3, f"均匀起振后收敛出单峰(一致性 {coh:.3f} ≥ 0.3)")
    check(amp > 0.05, f"形成真实峰谷差 {amp:.3f}(未保持均匀)")


# ------------------------------------------------------------------ ④ 线索锚定
def test_cx_cue_anchoring() -> None:
    print("[4] CX:视觉线索锚定(有线索时跟随线索)")
    ring = CXRingAttractor()
    ring.set_heading(0.0)
    target = 2.0
    for _ in range(int(4.0 / DT)):
        ring.step(DT, omega=0.0, cue=heading_cue(target, ring.n, width=1.2),
                  cue_gain=0.8)
    err = abs(wrap(ring.decoded() - target))
    check(err <= 0.35, f"解算航向收敛到线索(误差 {math.degrees(err):.1f}° ≤ 20°)")


# ------------------------------------------------------------------ ⑤ 三因子
def test_three_factor_plasticity() -> None:
    print("[5] 三因子可塑 + 资格迹(Frémaux & Gerstner 2016)")
    # (a) 完整三因子:pre×post 配对 + 调质 → 权重增长
    el = EligibilityTrace(tau=4.0)
    nm = Neuromodulator()
    nm.reward(1.0)
    w = 0.5
    for _ in range(120):
        el.update(DT, pre=1.0, post=1.0)
        w += three_factor_dw(0.1, rpe(1.0, 0.0), el.value, nm.plasticity_gain)
    check(w > 0.5, f"pre×post 配对 + 调质 → 权重 0.500 → {w:.4f}")

    # (b) 缺后突触项(post=0)→ 不应增长(证明真三因子而非两因子)
    el2 = EligibilityTrace(tau=4.0)
    w2 = 0.5
    for _ in range(120):
        el2.update(DT, pre=1.0, post=0.0)
        w2 += three_factor_dw(0.1, rpe(1.0, 0.0), el2.value,
                              nm.plasticity_gain)
    check(abs(w2 - 0.5) < 1e-9, f"post=0 时权重不变({w2:.6f})——有三因子才有可塑")

    # (c) 无调质 → 不写入(Burke 2012 的调质门控)
    el3 = EligibilityTrace(tau=4.0)
    w3 = 0.5
    for _ in range(120):
        el3.update(DT, pre=1.0, post=1.0)
        w3 += three_factor_dw(0.1, rpe(1.0, 0.0), el3.value, 0.0)
    check(abs(w3 - 0.5) < 1e-9, f"调质=0 时不写入({w3:.6f})")

    # (d) 延迟奖赏的信用分配:刺激先到、奖赏晚 2s,仍应写入
    el4 = EligibilityTrace(tau=4.0)
    w4 = 0.5
    for i in range(240):
        pre = 1.0 if i < 30 else 0.0          # 刺激只在头 0.5s
        el4.update(DT, pre=pre, post=pre)
        delta = rpe(1.0, 0.0) if i == 120 else 0.0   # 奖赏晚 2s
        w4 += three_factor_dw(0.1, delta, el4.value, nm.plasticity_gain)
    check(w4 > 0.5, f"延迟 2s 的奖赏仍能写入(资格迹信用分配)→ {w4:.4f}")


# ------------------------------------------------------------------ ⑥ 调质增益
def test_neuromodulator_gain() -> None:
    print("[6] 神经调质:章鱼胺/多巴胺全局增益(Burke 2012)")
    nm = Neuromodulator()
    base = nm.readout_gain
    nm.reward(1.0)
    up = nm.readout_gain
    nm.reset()
    nm.punish(1.0)
    dn = nm.readout_gain
    check(up > base, f"章鱼胺抬升读出增益 {base:.3f} → {up:.3f}")
    check(dn < base, f"多巴胺压低读出增益 {base:.3f} → {dn:.3f}")
    check(nm.plasticity_gain > 0.0, "调质存在时可塑门控打开")


# ------------------------------------------------------------------ ⑦~⑪ 习惯化
def test_habituation_rankin() -> None:
    print("[7] 非联想学习:习惯化 / 敏化 / 去习惯化(Rankin 2009)")
    # ① 渐进衰减到渐近水平(Rankin #1)
    #    口径说明:R2 规格 §3.1 的 κ_h≈0.15 与其 §4 断言①"10 次 ≤50%"互相矛盾,
    #    这里按 Rankin #1 原文"递减到渐近水平"取 20 次呈现。
    hab = Habituation(kappa_h=0.40, tau_rec=60.0)
    gains = []
    for _ in range(20):
        hab.present("wind", intensity=0.3, dt=0.5)
        gains.append(hab.gain("wind"))
    check(gains[-1] <= 0.5 * gains[0],
          f"20 次无害弱刺激后响应 {gains[0]:.3f} → {gains[-1]:.3f}"
          f"(≤50%,Rankin #1)")
    check(all(b <= a + 1e-9 for a, b in zip(gains, gains[1:])),
          "响应逐次单调不增(Rankin #1 渐进)")

    # ⑦ 刺激特异性(Rankin #7):habituated wind vs 新异 shadow
    g_wind = hab.gain("wind")
    g_shadow = hab.gain("shadow")
    check(g_shadow > g_wind,
          f"刺激特异性:wind {g_wind:.3f} 已习惯,shadow {g_shadow:.3f} 仍满响应")

    # ② 自发恢复(Rankin #2):回到未习惯基线的 80% 以上
    baseline = Habituation(kappa_h=0.40, tau_rec=60.0).gain("wind")   # =1.0
    h_before = hab.gain("wind")
    for _ in range(int(300.0 / DT)):
        hab.decay(DT)
    g_after = hab.gain("wind")
    check(g_after > h_before and g_after >= 0.8 * baseline,
          f"撤刺激 300s 后自发恢复 {h_before:.3f} → {g_after:.3f}"
          f"(≥基线 {baseline:.2f} 的 80%,Rankin #2)")

    # ④ 去习惯化(Rankin #8)
    hab2 = Habituation(kappa_h=0.40)
    for _ in range(10):
        hab2.present("wind", intensity=0.3, dt=0.5)
    low = hab2.gain("wind")
    hab2.present("contact", intensity=0.95, dt=0.0)
    hab2.dishabituate(except_key="contact")
    high = hab2.gain("wind")
    check(high > low + 0.1,
          f"强新异刺激触发去习惯化 {low:.3f} → {high:.3f}(Rankin #8)")

    # ⑤ 敏化(Rankin sensitization)
    hab3 = Habituation(sens_kappa=0.25)
    base = hab3.gain("contact")
    for _ in range(5):
        hab3.present("contact", intensity=0.9, dt=0.5)
    check(hab3.gain("contact") > base * 1.1,
          f"连续强刺激敏化:{base:.3f} → {hab3.gain('contact'):.3f}")

    # 增益下限:永不完全失敏(安全约束)
    hab4 = Habituation(kappa_h=0.9)
    for _ in range(200):
        hab4.present("wind", intensity=0.3, dt=0.1)
    check(hab4.gain("wind") >= 0.2 - 1e-9,
          f"响应增益有下限 {hab4.gain('wind'):.3f} ≥ 0.2(不会完全失敏)")


# ------------------------------------------------------------------ ⑫ 预算
def test_budget() -> None:
    print("[8] 每帧开销预算(单宠 ≤4ms)")
    ring = CXRingAttractor()
    ring.set_heading(0.0)
    hab = Habituation()
    el = EligibilityTrace()
    nm = Neuromodulator()

    n = 2000
    t0 = time.perf_counter()
    for _ in range(n):
        ring.step(DT, omega=0.3)
    t_ring = (time.perf_counter() - t0) / n

    t0 = time.perf_counter()
    for _ in range(n):
        hab.present("wind", 0.3, DT)
        hab.gain("wind")
        hab.decay(DT)
    t_hab = (time.perf_counter() - t0) / n

    t0 = time.perf_counter()
    for _ in range(n):
        el.update(DT, 1.0, 1.0)
        three_factor_dw(0.1, rpe(1.0, 0.0), el.value, nm.plasticity_gain)
        nm.decay(DT)
    t_plast = (time.perf_counter() - t0) / n

    total = t_ring + t_hab + t_plast
    print(f"    ring={t_ring*1000:.3f}ms  habituation={t_hab*1000:.3f}ms  "
          f"plasticity={t_plast*1000:.3f}ms  合计={total*1000:.3f}ms")
    check(total * 1000.0 <= 1.0,
          f"三项合计 {total*1000:.3f}ms/帧 ≤ 1.0ms(预算 4ms)")


def main() -> None:
    print("=" * 74)
    print("神经拟真度验收(环吸引子 / 三因子可塑 / 非联想学习)")
    print("=" * 74)
    test_cx_persistence()
    test_cx_path_integration()
    test_cx_single_bump()
    test_cx_cue_anchoring()
    test_three_factor_plasticity()
    test_neuromodulator_gain()
    test_habituation_rankin()
    test_budget()
    print("\n" + "=" * 74)
    if FAILS:
        print(f"失败 {len(FAILS)} 项:")
        for f in FAILS:
            print(f"  x {f}")
    else:
        print("全部判据通过")
    print("=" * 74)
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
