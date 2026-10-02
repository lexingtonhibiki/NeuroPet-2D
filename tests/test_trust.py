# -*- coding: utf-8 -*-
"""信任双向动力学验收(A2;调研《互动体验_情绪信任躲藏调研.md》§1)。

判据清单(≥10 项,全部断言式、可重复运行):
  ① 方程逐项:被动项 = K_CO·dt·G_fear − (trust−0.2)·dt/τ_TRUST(τ=72h),
     G_fear 按 fear 分档 1.0/0.3/0.05;整脑集成 60s 共处 → +K_CO·60;
  ② trust 钳位 [0,1](fed 顶满 / grab 见底);
  ③ grab 惩罚按信任档减负:怕人/警惕 −0.12,习惯 −0.09,主动靠近 −0.06;
  ④ 定时投喂 streak:20~90s 间隔承认节奏,+0.02×min(n,5);首喂无加成,
     n≥5 封顶(上限判据);<20s 同餐快喂不加成;
  ⑤ slow_approach:光标低速(<300px/s 且在动)持续接近满 5s 计 1 次
     +0.03;同一窗口只计 1 次;45s 冷却内不再计,冷却后恢复;
  ⑥ 主动趋近抵达:approach_cursor 臂执行中进入光标 80px 且光标静止 ≥10s
     → +0.03,冷却内不重复;
  ⑦ 四档阈值边界:0/0.25/0.5/0.75 → 怕人/警惕/习惯/主动靠近;
  ⑧ 逃逸阈随档变化:果蝇 gf_threshold 怕人 −0.08、习惯 +0.05;
     蟑螂震动反射阈 0.42/0.50/0.55 + 行为级(0.45 强度怕人档逃、警惕档不逃);
  ⑨ approach_cursor 臂开关:怕人档禁用,警惕档恢复;主动靠近档
     wants_owner 触发阈 15s→8s;
  ⑩ 蟑/蝇同构:同事件序列两脑 trust 轨迹一致(1e-12);
  ⑪ 面板文案:learning_summary 含"信任:"与档位中文名;
  ⑫ 持久化:trust_dyn save/load 往返一致;老档案缺该键缺省回退。

直跑:python tests/test_trust.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from neuropet.core.contracts import Behavior, PetState, Stimulus, StimulusKind
from neuropet.core.world import WorldModel
from neuropet.brain.fly_brain import FlyConnectomeBrain
from neuropet.brain.roach_brain import RoachBrain
from neuropet.brain.spontaneous import (TRUST_TIER_ZH, TrustDynamics, cohab_gain,
                                        K_CO, TAU_TRUST, trust_tier, trust_tier_zh)

DT = 1.0 / 60.0
SCREEN = (1920, 1080)
POS = (960.0, 540.0)
FAILS: list[str] = []


def check(cond, msg: str) -> None:
    if cond:
        print(f"  [ok] {msg}")
    else:
        print(f"  [FAIL] {msg}")
        FAILS.append(msg)


def make_brain(cls, tag: str, pos=POS):
    w = WorldModel(*SCREEN)
    st = PetState(pet_id=tag, species_id=tag, pos=pos)
    w.upsert_pet(st)
    return cls(state=st), w, w.snapshot(tag, 0.0)


# ------------------------------------------------------------------ ① 方程逐项
def test_equation_terms() -> None:
    print("[1] 方程逐项:被动项(共处 + 回归)与 G_fear 分档")
    td = TrustDynamics()
    # 纯回归(高恐惧 G=0.05 压到最小,信任高 → 净回归):分项核算
    dt, trust, fear = 3600.0, 0.8, 0.9
    expect = K_CO * dt * 0.05 - (trust - 0.2) * dt / TAU_TRUST
    check(abs(td.passive_delta(dt, fear, trust) - expect) < 1e-12,
          f"被动项 = K_CO·dt·G_fear − (trust−0.2)·dt/τ({expect:+.5f})")
    # G_fear 分档
    check((cohab_gain(0.1), cohab_gain(0.45), cohab_gain(0.9)) == (1.0, 0.3, 0.05),
          "G_fear 分档:fear<0.3→1.0,<0.6→0.3,否则 0.05")
    # 整脑集成:60s 空刺激共处(fear≈0)→ trust 增量 ≈ K_CO·60(回归项此量级可忽略)
    for cls in (FlyConnectomeBrain, RoachBrain):
        b, w, view = make_brain(cls, "eq" + cls.SPECIES)
        t0 = b.emotion().trust
        for _ in range(int(60.0 / DT)):
            b.observe(w.snapshot(b.state.pet_id, b._t + DT), [], DT)
        gain = b.emotion().trust - t0
        check(abs(gain - K_CO * 60.0) < 2e-4,
              f"{cls.SPECIES}:60s 低恐惧共处 trust +{gain:.5f} ≈ K_CO·60={K_CO * 60.0:.5f}")


# ------------------------------------------------------------------ ② 钳位
def test_clamp() -> None:
    print("[2] trust 钳位 [0,1]")
    for cls in (FlyConnectomeBrain, RoachBrain):
        b, w, view = make_brain(cls, "clamp" + cls.SPECIES)
        b._trust = 0.99
        b.on_event("fed", {})
        check(b.emotion().trust <= 1.0, f"{cls.SPECIES}:fed 顶满不越界({b.emotion().trust:.3f})")
        b._trust = 0.02
        b.on_event("grab", {})
        check(b.emotion().trust >= 0.0, f"{cls.SPECIES}:grab 见底不为负({b.emotion().trust:.3f})")


# ------------------------------------------------------------------ ③ grab 档位减负
def test_grab_tier_scaling() -> None:
    print("[3] grab 惩罚按信任档减负(−0.12/−0.09/−0.06)")
    for cls in (FlyConnectomeBrain, RoachBrain):
        got = []
        for t0 in (0.3, 0.6, 0.8):        # 警惕 / 习惯 / 主动靠近
            b, _, _ = make_brain(cls, f"gr{cls.SPECIES}{t0}")
            b._trust = t0
            b.on_event("grab", {})
            got.append(round(t0 - b.emotion().trust, 3))
        check(got == [0.12, 0.09, 0.06],
              f"{cls.SPECIES}:grab 减负档位差 {got}(警惕/习惯/主动靠近)")


# ------------------------------------------------------------------ ④ 定时投喂 streak
def test_fed_streak() -> None:
    print("[4] 定时投喂 streak:+0.02×min(n,5),首喂无加成,n≥5 封顶")
    for cls in (FlyConnectomeBrain, RoachBrain):
        b, w, view = make_brain(cls, "streak" + cls.SPECIES)
        b.observe(view, [], DT)               # _t 起步
        b._trust = 0.0                        # 压低基线,防 +0.06×9 撞 1.0 钳位
        b.on_event("fed", {})
        check(abs(b._trust - 0.06) < 1e-12, f"{cls.SPECIES}:首喂仅基础 +0.06(无 streak 加成)")
        deltas = []
        for k in range(2, 8):                 # 之后每 30s 一喂(∈20~90s)
            b._t = 30.0 * k
            before = b._trust
            b.on_event("fed", {})
            deltas.append(round(b._trust - before, 6))
        expect = [round(0.06 + 0.02 * min(n, 5), 6) for n in range(2, 8)]
        check(deltas == expect,
              f"{cls.SPECIES}:streak 加成序列 {deltas} == 0.02×min(n,5) 封顶 {expect}")
        # <20s 同餐快喂:streak 归 1、无加成
        b._t += 5.0
        before = b._trust
        b.on_event("fed", {})
        check(abs(b._trust - before - 0.06) < 1e-12,
              f"{cls.SPECIES}:5s 后再喂视为同餐快喂,无加成")


# ------------------------------------------------------------------ ⑤ slow_approach
def test_slow_approach() -> None:
    print("[5] slow_approach:低速持续接近 5s 计 1 次,45s 冷却")
    for cls in (FlyConnectomeBrain, RoachBrain):
        b, w, _ = make_brain(cls, "sa" + cls.SPECIES)
        b.observe(w.snapshot(b.state.pet_id, 0.0), [], DT)
        t0 = b._trust
        # 光标自宠物上方 155px 处以 30px/s 缓慢逼近(EMA 产物 speed 手工置位)
        w.cursor.x, w.cursor.y = POS[0], POS[1] - 155.0
        w.cursor.speed = 30.0
        for i in range(int(5.2 / DT)):        # 5.2s:越过 5s 计分沿(浮点累加容差)
            w.cursor.y += 30.0 * DT
            b.observe(w.snapshot(b.state.pet_id, b._t + DT), [], DT)
        check(abs(b._trust - t0 - 0.03) < 2e-3,
              f"{cls.SPECIES}:5s 低速逼近计 1 次 +0.03(Δ={b._trust - t0:.4f})")
        once = b._trust
        # 继续逼近(冷却窗内)→ 不得再计
        for i in range(int(5.0 / DT)):
            w.cursor.y += 30.0 * DT
            b.observe(w.snapshot(b.state.pet_id, b._t + DT), [], DT)
        check(b._trust - once < 0.01,
              f"{cls.SPECIES}:同窗/冷却内不重复计分(Δ={b._trust - once:.4f})")
        # 光标撤到远处静止,快进 46s 越过冷却,再逼近 → 恢复计分
        w.cursor.x, w.cursor.y, w.cursor.speed = 50.0, 50.0, 0.0
        b.observe(w.snapshot(b.state.pet_id, b._t + 46.0), [], 46.0)
        w.cursor.x, w.cursor.y = POS[0], POS[1] - 155.0
        w.cursor.speed = 30.0
        before = b._trust
        for i in range(int(5.2 / DT)):
            w.cursor.y += 30.0 * DT
            b.observe(w.snapshot(b.state.pet_id, b._t + DT), [], DT)
        check(b._trust - before > 0.025,
              f"{cls.SPECIES}:45s 冷却后恢复计分(Δ={b._trust - before:.4f})")
        # 高速冲近(rush)不算温和靠近
        b2, w2, _ = make_brain(cls, "sar" + cls.SPECIES)
        b2.observe(w2.snapshot(b2.state.pet_id, 0.0), [], DT)
        t0 = b2._trust
        w2.cursor.x, w2.cursor.y = POS[0], POS[1] - 155.0
        w2.cursor.speed = 900.0
        for i in range(int(6.0 / DT)):
            w2.cursor.y += 900.0 * DT
            b2.observe(w2.snapshot(b2.state.pet_id, b2._t + DT), [], DT)
        check(b2._trust - t0 < 0.005,
              f"{cls.SPECIES}:高速冲近不计信任(Δ={b2._trust - t0:.4f})")


# ------------------------------------------------------------------ ⑥ 主动趋近抵达
def test_arrival_credit() -> None:
    print("[6] 主动趋近抵达:+0.03(80px 内且光标静止 ≥10s),冷却内不重复")
    for cls in (FlyConnectomeBrain, RoachBrain):
        b, w, _ = make_brain(cls, "ar" + cls.SPECIES)
        b._trust = 0.4
        b._active_arm = "approach_cursor"
        b.spon.has_owner = True
        b.spon.since_cursor_s = 30.0
        b.spon.idle_s = 30.0
        w.cursor.x, w.cursor.y = POS[0] + 60.0, POS[1]
        before = b._trust
        b.observe(w.snapshot(b.state.pet_id, b._t + DT), [], DT)
        check(abs(b._trust - before - 0.03) < 2e-3,
              f"{cls.SPECIES}:抵达光标 80px 计 +0.03(Δ={b._trust - before:.4f})")
        before = b._trust
        b.observe(w.snapshot(b.state.pet_id, b._t + DT), [], DT)
        check(abs(b._trust - before) < 1e-6,
              f"{cls.SPECIES}:冷却内抵达不重复计分")


# ------------------------------------------------------------------ ⑦ 四档边界
def test_tier_edges() -> None:
    print("[7] 四档阈值边界 0.25/0.50/0.75")
    seq = [(0.0, 0), (0.249, 0), (0.25, 1), (0.499, 1),
           (0.5, 2), (0.749, 2), (0.75, 3), (1.0, 3)]
    check(all(trust_tier(t) == e for t, e in seq), f"分档边界 {seq}")
    check(all(trust_tier_zh(t) == TRUST_TIER_ZH[e] for t, e in seq),
          f"档位中文名 {TRUST_TIER_ZH}")


# ------------------------------------------------------------------ ⑧ 逃逸阈随档
def test_escape_threshold_tiers() -> None:
    print("[8] 逃逸阈随档:果蝇 GF 阈 / 蟑螂震动反射阈")
    b, _, _ = make_brain(FlyConnectomeBrain, "gf")
    th = []
    for t in (0.1, 0.3, 0.6):
        b._trust = t
        th.append(round(b.gf_threshold(), 6))
    check(th == [round(0.55 - 0.08, 6), 0.55, round(0.55 + 0.05, 6)],
          f"果蝇 gf_threshold 怕人/警惕/习惯 = {th}")
    r, _, _ = make_brain(RoachBrain, "vt")
    vt = []
    for t in (0.1, 0.3, 0.6):
        r._trust = t
        vt.append(round(r.vibration_threshold(), 6))
    check(vt == [0.42, 0.50, 0.55], f"蟑螂震动反射阈 怕人/警惕/习惯 = {vt}")
    # 行为级:0.45 强度震动,怕人档(阈 0.42)逃,警惕档(阈 0.50)不逃
    for t, esc in ((0.1, True), (0.3, False)):
        r2, w2, view = make_brain(RoachBrain, f"vb{t}")
        r2._trust = t
        s = Stimulus(StimulusKind.VIBRATION, source="cursor", pos=(1400.0, 540.0),
                     intensity=0.45)
        r2.observe(view, [s], DT)
        cmd = r2.decide(view)
        check((cmd.behavior == Behavior.ESCAPE) == esc,
              f"trust={t}:震动 0.45 {'应逃' if esc else '不应逃'}(得 {cmd.behavior.name})")


# ------------------------------------------------------------------ ⑨ 臂开关与 wants_owner 阈
def _approach_reason(cls, tag, trust, still_s) -> bool:
    b, w, _ = make_brain(cls, tag)
    b._trust = trust
    b.spon.groom_left = 0.0
    b.spon.has_owner = True
    b.spon.since_cursor_s = still_s
    b.spon.idle_s = 30.0
    b.spon.rest_left = 0.0
    b.spon.rest_cd = 999.0
    for i in range(5):
        b.observe(w.snapshot(b.state.pet_id, i * DT), [], DT)
        cmd = b.decide(w.snapshot(b.state.pet_id, i * DT))
        if "主人安静" in cmd.reason:
            return True
    return False


def test_approach_arm_gating() -> None:
    print("[9] approach_cursor 臂开关 + wants_owner 触发阈调制")
    for cls in (FlyConnectomeBrain, RoachBrain):
        check(not _approach_reason(cls, "ag0" + cls.SPECIES, 0.2, 30.0),
              f"{cls.SPECIES}:怕人档(<0.25)禁用主动趋近臂")
        check(_approach_reason(cls, "ag1" + cls.SPECIES, 0.4, 30.0),
              f"{cls.SPECIES}:警惕档恢复主动趋近(静止 30s)")
        check(not _approach_reason(cls, "ag2" + cls.SPECIES, 0.4, 10.0),
              f"{cls.SPECIES}:警惕档静止 10s(<15s)不触发")
        check(_approach_reason(cls, "ag3" + cls.SPECIES, 0.8, 10.0),
              f"{cls.SPECIES}:主动靠近档静止 10s(≥8s)即触发(15s→8s)")


# ------------------------------------------------------------------ ⑩ 蟑/蝇同构
def test_isomorphism() -> None:
    print("[10] 蟑/蝇同构:同事件序列 trust 轨迹一致")
    b1, w1, v1 = make_brain(FlyConnectomeBrain, "iso1")
    b2, w2, v2 = make_brain(RoachBrain, "iso2")
    b1.observe(v1, [], DT)
    b2.observe(v2, [], DT)
    b1.on_event("fed", {})
    b2.on_event("fed", {})
    b1.observe(v1, [], 30.0)
    b2.observe(v2, [], 30.0)
    b1.on_event("fed", {})                    # gap=30s → streak=2 → +0.04
    b2.on_event("fed", {})
    b1.observe(v1, [], DT)
    b2.observe(v2, [], DT)
    b1.on_event("grab", {})
    b2.on_event("grab", {})
    check(abs(b1._trust - b2._trust) < 1e-12,
          f"两脑 trust 终值一致:{b1._trust:.6f} vs {b2._trust:.6f}")


# ------------------------------------------------------------------ ⑪ 面板文案
def test_panel_text() -> None:
    print("[11] learning_summary 含信任档位中文名")
    for cls in (FlyConnectomeBrain, RoachBrain):
        b, _, _ = make_brain(cls, "pt" + cls.SPECIES)
        b._trust = 0.1
        check("怕人" in b.learning_summary() and "信任" in b.learning_summary(),
              f"{cls.SPECIES}:learning_summary 含'信任:怕人'({b.learning_summary()})")
        b._trust = 0.6
        check("习惯" in b.learning_summary(), f"{cls.SPECIES}:trust=0.6 显示'习惯'档")


# ------------------------------------------------------------------ ⑫ 持久化
def test_persistence() -> None:
    print("[12] trust_dyn 持久化 + 老档案兼容")
    b, w, view = make_brain(FlyConnectomeBrain, "pf1")
    b.observe(view, [], DT)
    b.on_event("fed", {})
    data = b.save()
    check("trust_dyn" in data and data["trust_dyn"]["fed_streak"] == 1,
          "save 含 trust_dyn 记账")
    b2 = FlyConnectomeBrain(PetState(pet_id="pf2", species_id="t", pos=(100.0, 100.0)))
    b2.load(data)
    check(b2.save() == data and b2._trust_dyn.fed_streak == 1,
          "save→load→save 逐字段一致")
    old = {k: v for k, v in data.items() if k != "trust_dyn"}
    b3 = FlyConnectomeBrain(PetState(pet_id="pf3", species_id="t", pos=(100.0, 100.0)))
    b3.load(old)
    check(b3._trust_dyn.fed_streak == 0 and b3._trust_dyn.last_fed_t == -1.0,
          "老档案(无 trust_dyn 键)缺省回退")
    r, _, rview = make_brain(RoachBrain, "pf4")
    r.observe(rview, [], DT)
    r.on_event("fed", {})
    rd = r.save()
    r2 = RoachBrain(PetState(pet_id="pf5", species_id="t", pos=(100.0, 100.0)))
    r2.load(rd)
    check(r2.save() == rd, "蟑螂 trust_dyn 往返一致")


def main() -> None:
    print("=" * 74)
    print("信任双向动力学验收(A2)")
    print("=" * 74)
    test_equation_terms()
    test_clamp()
    test_grab_tier_scaling()
    test_fed_streak()
    test_slow_approach()
    test_arrival_credit()
    test_tier_edges()
    test_escape_threshold_tiers()
    test_approach_arm_gating()
    test_isomorphism()
    test_panel_text()
    test_persistence()
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
