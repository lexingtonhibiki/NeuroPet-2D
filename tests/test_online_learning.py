"""运行时在线自学习验收(R1' 三件套落地:RPE+资格迹+消退 / 老虎机 / TD(λ))。

断言对应《神经自学习_在线升级调研.md》§4.8 验收草目与实施任务书六条:
  ① 习得趋近:气味+奖励配对 ×4 → 气味通道 KC→MBON 权重、吸引读出
     (MBON[0:4])、SEEK 决策分数上升;fed 信用使 seek_food 臂 μ 上升;
  ①b 信用分配时序:气味在前、投喂时气味已消失 0.5s(< τ_e=4s)→ 权重仍
     写到气味通道(资格迹弥合;旧版只写"当下活跃 KC"则配对丢失);
  ② 消退:只闻不吃(气味在场每满 5s 记一次消退事件 ×8)→ 吸引回落、
     学习增量被消退 ≥50%;静置 3min → w_ext 主动遗忘 ≥40% 且趋近部分回升
     (对立记忆,Yang 2023);
  ③ 老虎机:合成环境(臂表 + 固定 μ 排序)60 决策点 → 最优臂选择率 >60%
     (UCB1 + softmax 温度);
  ④ RPE:连续同源投喂 ×6,前 2 次补饿(意外奖励)、其后自然饱食 → 单次
     写入量单调不增,后 3 次均值 < 前 3 次均值 ×30%(预测误差+饱食门控
     双重收敛);δ = R − γV 严格小于 R;蟑螂侧线性 critic V 随奖励上升;
  ⑤ 持久化:学习状态 save→新脑 load→存档逐字段一致 + 同气味探针读出一致
     + v1 档案兼容(无新键/行缺 w_ext 时缺省回退);
  ⑥ 预算:2000 帧均值 observe+decide+learn 全链路**本进程 CPU 时间**(3 遍取
     min ≤400µs = 16ms 帧预算 2.5%;学习增量红线 100µs/帧)+ 状态/资格迹稀疏度
     预算。r25 账本行 22(**原判据 → 新判据 → 为什么**,阈值 400 未动):原判据
     用 `perf_counter` 墙钟 —— 它把「被别人的进程抢走的 CPU」记进本征成本
     (实测满载抬 1.8~2.0×,同轮 CPU 时间只 1.4×),导致同一棵未改动的树上
     全套件时红时绿(dump:461.9µs;单跑恒绿 308~339µs)。改为 `process_time`
     后闲时读数同量级(275.6 vs 281.3µs)⇒ 阈值语义不变。⚠ 满载机(CPU 100%)
     上 CPU 时间自身也会被缓存/SMT 争用抬过 400µs ⇒ 验收不得在满载机上跑
     (数字见 docs/handoff/r25-test-flake-r1.md §5)。

直跑:python tests/test_online_learning.py
"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from neuropet.core.contracts import PetState, Stimulus, StimulusKind
from neuropet.core.world import WorldModel
from neuropet.brain.fly_brain import KC_N, FlyConnectomeBrain
from neuropet.brain.instinct import BANDIT_ARMS, BanditArbiter
from neuropet.brain.roach_brain import RoachBrain

DT = 1.0 / 60.0
SCREEN = (1920, 1080)
ODOR_SRC = (600.0, 540.0)


def make_scene(pet_id: str, pos: tuple[float, float] = (960.0, 540.0)):
    w = WorldModel(*SCREEN)
    st = PetState(pet_id=pet_id, species_id="test", pos=pos)
    w.upsert_pet(st)
    return w, st, w.snapshot(pet_id, 0.0)


def odor(intensity: float = 0.95, src=ODOR_SRC) -> Stimulus:
    d = (src[0] - 960.0, src[1] - 540.0)
    n = math.hypot(*d) or 1.0
    return Stimulus(StimulusKind.ODOR_FOOD, source="food:test01", pos=src,
                    intensity=intensity, direction=(d[0] / n, d[1] / n))


def wind(intensity: float = 0.4, src=(1600.0, 540.0)) -> Stimulus:
    d = (src[0] - 960.0, src[1] - 540.0)
    n = math.hypot(*d) or 1.0
    return Stimulus(StimulusKind.WIND, source="cursor", pos=src,
                    intensity=intensity, direction=(d[0] / n, d[1] / n),
                    meta={"gesture": "rush"})


def attract(b: FlyConnectomeBrain) -> float:
    """决策分数:吸引 MBON 表达读出(decide() 的 SEEK 增益即 0.6+0.8×该值)。"""
    return sum(b.mbon[0:4]) / 4.0


def odor_readout(b: FlyConnectomeBrain, view, frames: int = 4, dt: float = 0.1) -> float:
    """同气味探针:气味在场数拍后的吸引读出。"""
    for _ in range(frames):
        b.observe(view, [odor()], dt)
        b.decide(view)
    return attract(b)


def odor_active_kcs(b: FlyConnectomeBrain) -> set[int]:
    return {i for i in range(KC_N) if b.kc[i] > 0.05}


def attract_rows(b: FlyConnectomeBrain, active: set[int]) -> list:
    return [row for j in range(4) for row in b._kc_mbon[j] if int(row[0]) in active]


def pair(b: FlyConnectomeBrain, view, *, odor_at_fed: bool, topup: bool,
         dt: float = 0.1) -> None:
    """一次投喂配对:气味在前 3s(<CS_ONLY_S,不触发消退);可选"气味先
    消失 0.5s 再投喂"(资格迹弥合时序的场景);可选投喂前补饿到饥饿门控
    饱和(hunger→1.0,gate=1)。topup 原为固定 5 次,但首试次初始饥饿从
    0 起、fed 又 −0.3,gate 实为 0.72——"门控全开"的测试意图落空,
    写入量被饥饿门控压低造成非单调假象。"""
    for _ in range(30):                       # 气味在场 3s
        b.observe(view, [odor()], dt)
        b.decide(view)
    if not odor_at_fed:
        for _ in range(5):                    # 气味先消失 0.5s
            b.observe(view, [], dt)
    if topup:
        for _ in range(20):                   # 补饿至饱和(每拍 +0.1,≤1.0 封顶)
            if b.emo[3] >= 1.0:
                break
            b.on_event("hungry", {})
    b.on_event("fed", {})
    b.observe(view, [odor()] if odor_at_fed else [], dt)   # 下一拍 δ 单步写入


# ------------------------------------------------------------------ ① 习得趋近
def test_learned_approach_and_credit() -> None:
    w, st, view = make_scene("ol1")
    b = FlyConnectomeBrain(st)
    b.observe(view, [odor()], 0.1)
    b.decide(view)
    active = odor_active_kcs(b)
    assert active, "气味应激活 KC 群"
    base_rows = sum(r[1] for r in attract_rows(b, active)) / len(attract_rows(b, active))
    att0 = odor_readout(b, view)

    # 配对 ×4:气味在前、投喂时气味仍在场(持续在场场景)
    seq = []
    for _ in range(4):
        pair(b, view, odor_at_fed=True, topup=True)
        seq.append(attract(b))
    rows = attract_rows(b, active)
    learned_rows = sum(r[1] for r in rows) / len(rows)
    # 习得:权重、吸引读出、SEEK 决策分数三者都应严格上升(仿真级)
    assert learned_rows > base_rows, \
        f"气味通道权重应增长:{base_rows:.4f} → {learned_rows:.4f}"
    assert seq[-1] > seq[0] and seq[-1] > att0, \
        f"吸引读出应随配对上升:{att0:.4f} → {seq[0]:.4f} → {seq[-1]:.4f}"
    # 对照:新鲜脑同场景读出(决策分数 = 0.6 + 0.8×attract)
    w2, st2, view2 = make_scene("ol1c")
    b2 = FlyConnectomeBrain(st2)
    att_fresh = odor_readout(b2, view2)
    assert seq[-1] > att_fresh, \
        f"习得读出应高于新鲜脑:{seq[-1]:.4f} vs {att_fresh:.4f}"
    assert 0.6 + 0.8 * seq[-1] > 0.6 + 0.8 * att_fresh, "习得后 SEEK 决策分数应上升"
    # 操作式信用:fed 奖励"被投喂时正在做的事" → seek_food 臂 μ 上升并成为
    # 最优臂。μ 由平均式更新 (R−μ)/max(2,n) 与联想播种 0.7μ+0.3·(0.5+0.5v)
    # 共同决定,4 次 fed 的确定性终值 ≈0.83( fresh 先验 ≈0.76),故断言
    # "相对抬升 + 最优臂"而非绝对阈值。
    mu0 = FlyConnectomeBrain(PetState(pet_id="ol1p", species_id="test",
                                      pos=(100.0, 100.0))).bandit.mu["seek_food"]
    mu_seek = b.bandit.mu["seek_food"]
    assert mu_seek > mu0 + 0.05, \
        f"seek_food 臂 μ 应被 fed 抬升:{mu0:.3f} → {mu_seek:.3f}"
    arm_best, _, _ = b.bandit.best_arm()
    assert arm_best == "seek_food", \
        f"fed 后 seek_food 应成为最优臂,实际 {arm_best}"


# ------------------------------------------------------------------ ①b 信用分配时序
def test_eligibility_bridges_reward_delay() -> None:
    """缺陷①回归:奖励到达时气味已消失 0.5s(KC 全部静默)——
    资格迹应把信用分配回气味通道(旧版只写"当下活跃 KC"→配对丢失)。"""
    w, st, view = make_scene("ol1d")
    b = FlyConnectomeBrain(st)
    b.observe(view, [odor()], 0.1)
    active = odor_active_kcs(b)
    base = sum(r[1] for r in attract_rows(b, active)) / len(attract_rows(b, active))
    for _ in range(3):
        pair(b, view, odor_at_fed=False, topup=True)   # 投喂时气味已消失
    grown = sum(r[1] for r in attract_rows(b, active)) / len(attract_rows(b, active))
    assert grown >= base + 0.008, \
        f"气味消失后投喂仍应写入气味通道(资格迹弥合):{base:.4f} → {grown:.4f}"


# ------------------------------------------------------------------ ② 消退+自发恢复
def test_extinction_and_spontaneous_recovery() -> None:
    w, st, view = make_scene("ol2")
    b = FlyConnectomeBrain(st)
    for _ in range(3):
        pair(b, view, odor_at_fed=False, topup=True)
    b.habit.reset()      # 探针前清习惯化:与新鲜脑对照同口径(气味增益一致)
    b.observe(view, [odor()], 0.1)
    b.decide(view)
    pre = attract(b)
    # 新鲜脑对照(同气味同投影 → 同基线读出)
    view_c = make_scene("ol2c")[2]
    b_c = FlyConnectomeBrain(PetState(pet_id="ol2c2", species_id="test",
                                      pos=(100.0, 100.0)))
    fresh = odor_readout(b_c, view_c)
    assert pre > fresh, f"习得基线应高于新鲜水平:{pre:.4f} vs {fresh:.4f}"

    def ext_mass() -> float:
        return sum(row[2] for j in range(4) for row in b._kc_mbon[j])

    # 只闻不吃:气味持续在场,每满 5s 一次消退事件(8 次 ≈ 45s)
    for _ in range(180):
        b.observe(view, [odor()], 0.25)
        b.decide(view)
    post = attract(b)
    ext_after = ext_mass()
    assert ext_after > 0.0, "CS-only 应写入对立消退记忆 w_ext"
    assert post < pre, f"只闻不吃应使趋近回落:{pre:.4f} → {post:.4f}"
    # 学习到的增量至少被消退掉一半(相对新鲜基线口径,免受 KC 幅值绝对值影响)
    assert post - fresh <= 0.5 * (pre - fresh), \
        f"习得吸引应回落 ≥50%:{pre:.4f}(鲜 {fresh:.4f}) → {post:.4f}"

    # 自发恢复:静置 3min,消退记忆被主动遗忘(Yang 2023;×0.995^180 ≈ 0.41)
    for _ in range(360):
        b.observe(view, [], 0.5)
    b.observe(view, [odor()], 0.5)
    b.decide(view)
    rec = attract(b)
    assert ext_mass() <= 0.6 * ext_after, \
        f"静置 3min 后 w_ext 应主动遗忘 ≥40%:{ext_after:.4f} → {ext_mass():.4f}"
    assert rec > post, f"消退记忆遗忘后趋近应部分回升:{post:.4f} → {rec:.4f}"


# ------------------------------------------------------------------ ③ 老虎机
def test_bandit_convergence() -> None:
    arb = BanditArbiter(level=3, seed=1234)
    # 合成环境:K=7 臂、固定 μ 排序(最优臂 rest=0.9,次优 0.55,差距 0.35)
    star = {"explore_wander": 0.20, "seek_food": 0.35, "approach_cursor": 0.45,
            "groom": 0.50, "rest": 0.90, "near_cold": 0.30,
            "near_small_target": 0.55}
    assert set(star) == set(BANDIT_ARMS)
    best = max(star, key=star.get)
    counts = {a: 0 for a in BANDIT_ARMS}
    rewards: list[float] = []
    for t in range(1, 61):                    # 60 个决策点
        a = arb.select()                      # argmax UCB
        r = star[a]                           # 确定性回报 = 该臂 μ*
        arb.update(a, r)
        arb.trial(a)
        if t > 10:                            # 前 10 点为探索热身,不计入
            counts[a] += 1
            rewards.append(r)
    rate = counts[best] / 50.0
    mean_r = sum(rewards) / len(rewards)
    print(f"    [bandit] 最优臂({best})选择率 {rate:.0%},后 50 点平均回报 {mean_r:.2f}")
    assert rate > 0.6, f"最优臂选择率应 >60%,实际 {rate:.0%}"
    assert mean_r > 0.8, f"平均回报应收敛,实际 {mean_r:.2f}"
    # softmax 温度读出:大分差 → 高概率选优;同分 → 可抽样到两者
    big = [arb.softmax_pick({"a": 4.0, "b": 0.0}, 3.0) for _ in range(200)]
    assert big.count("a") / 200 >= 0.9, \
        f"大分差应以 ≥90% 概率选 a,实际 {big.count('a') / 200:.0%}"
    both = {arb.softmax_pick({"a": 1.0, "b": 1.0}, 3.0) for _ in range(60)}
    assert both == {"a", "b"}, "同分应可抽样到两者"


# ------------------------------------------------------------------ ④ RPE 写入衰减
def test_rpe_write_decay() -> None:
    w, st, view = make_scene("ol4")
    b = FlyConnectomeBrain(st)
    b.observe(view, [odor()], 0.1)
    active = odor_active_kcs(b)

    def total_w() -> float:
        return sum(r[1] for r in attract_rows(b, active))

    ws = [total_w()]
    for k in range(6):
        # 前 2 次投喂前补饿(意外奖励:饥饿门控全开);其后自然饱食 ——
        # 预期内奖励经 δ=R−γV 与饱食门控(Krashes 2009)双重衰减,写入量
        # 单调回落直至归零(Rescorla-Wagner 预测误差学说的行为学签名)
        pair(b, view, odor_at_fed=True, topup=(k < 2))
        ws.append(total_w())
    writes = [ws[i + 1] - ws[i] for i in range(6)]
    # 单调不增:前 4 次有实际写入的增量必须逐次回落(第 5/6 次增量 ≈ 0,
    # 只剩每秒慢衰减的微弱负项,不参与单调性口径)
    assert all(writes[i + 1] <= writes[i] + 1e-9 for i in range(3)), \
        f"重复投喂单次写入量应单调不增:{[f'{x:.5f}' for x in writes]}"
    early, late = sum(writes[:3]) / 3.0, sum(writes[3:]) / 3.0
    assert early > 1e-4, f"前 3 次应有实际写入,实际 {early:.5f}"
    assert late < 0.3 * early, \
        f"预期内(饱食)重复投喂写入量应衰减 ≥70%:前3均值 {early:.5f},后3均值 {late:.5f}"
    assert b.last_rpe < 1.0, \
        f"气味在场时 V>0,δ=R−γV 应严格小于 R,实际 {b.last_rpe:.3f}"

    # 蟑螂侧:线性 TD critic 的 V 应随奖励事件上升
    wr, str_, vr = make_scene("ol4r")
    rb = RoachBrain(str_)
    vs = []
    for _ in range(6):
        rb.observe(vr, [], DT)
        rb.decide(vr)
        rb.on_event("fed", {})
        rb.observe(vr, [], DT)
        vs.append(rb._v_prev)
    assert vs[-1] > vs[0], f"critic V 应随奖励上升:{vs[0]:.4f} → {vs[-1]:.4f}"
    assert rb._last_delta != 0.0, "critic 应记录最近事件 δ"


# ------------------------------------------------------------------ ⑤ 持久化往返
def test_persistence_roundtrip() -> None:
    w, st, view = make_scene("ol5")
    b = FlyConnectomeBrain(st)
    for _ in range(3):                        # 3 次配对 → n_pair=3 → 巩固(半衰期收紧)
        pair(b, view, odor_at_fed=False, topup=True)
    b.on_event("grab", {})                    # 臂惩罚 + 厌恶通道写入
    data = b.save()
    assert data["version"] == 2
    assert any(n >= 3 for n in data["n_pair"]), "3 次配对应触发巩固记账"

    b2 = FlyConnectomeBrain(PetState(pet_id="ol5b", species_id="test",
                                     pos=(100.0, 100.0)))
    b2.load(data)
    assert b2.save() == data, "save→load→save 应逐字段一致"
    assert b2.bandit.mu == b.bandit.mu and b2.bandit.n == b.bandit.n, "臂状态应一致"
    # 同气味探针:两脑吸引读出一致(权重+投影同源)。
    # 习惯化状态与每秒慢衰减累加器均不在持久化范围(秒级运行时状态),
    # 探针前对两脑同置零以控制变量 —— 否则 b 的慢衰减可能在探针窗口内
    # 恰好触发一次(×0.9999),而 b3 不会,读出出现 ~1e-6 的假差异。
    b.habit.reset()
    b._mb_decay_acc = 0.0
    view3 = make_scene("ol5c")[2]
    b3 = FlyConnectomeBrain(PetState(pet_id="ol5c2", species_id="test",
                                     pos=(100.0, 100.0)))
    b3.load(data)
    b3._mb_decay_acc = 0.0
    a1 = odor_readout(b, view)
    a3 = odor_readout(b3, view3)
    assert abs(a1 - a3) < 1e-9, f"载入后行为读出应一致:{a1:.6f} vs {a3:.6f}"

    # v1 兼容:老档案(行 [j,kc,w]、无新键)读入不弃档,w_ext 缺省 0
    v1 = {k: v for k, v in data.items() if k not in ("n_pair", "d_mbon", "arms")}
    v1["version"] = 1
    v1["kc_mbon"] = [r[:3] for r in data["kc_mbon"]]
    b4 = FlyConnectomeBrain(PetState(pet_id="ol5d", species_id="test",
                                     pos=(100.0, 100.0)))
    b4.load(v1)
    assert b4._kc_mbon[0][0][2] == 0.0, "v1 行的 w_ext 应缺省 0"
    assert abs(b4._kc_mbon[0][0][1] - data["kc_mbon"][0][2]) < 1e-12, "v1 权重应恢复"

    # 蟑螂侧:critic + 臂 往返
    wr, str_, vr = make_scene("ol5r")
    rb = RoachBrain(str_)
    for _ in range(3):
        rb.observe(vr, [], DT)
        rb.decide(vr)
        rb.on_event("fed", {})
    rd = rb.save()
    rb2 = RoachBrain(PetState(pet_id="ol5s", species_id="test", pos=(100.0, 100.0)))
    rb2.load(rd)
    assert rb2.save() == rd, "蟑螂 save→load→save 应逐字段一致"
    assert rb2._theta == rb._theta, "critic 权重应一致"
    assert rb2.bandit.mu == rb.bandit.mu, "臂状态应一致"


# ------------------------------------------------------------------ ⑥ 预算
def test_frame_budget() -> None:
    # r24 口径:min-of-3(基准测量标准:后台负载只增不减,min 最接近代码本征
    # 成本;单次均值在同一机器上实测 359~485µs 波动 ±15%,点估计不可作门)。
    # 阈值 350→400:阈值 350 写于脑扩大前(基线~120µs 时代);连接ome 现为
    # 514 节点全链动力学。r24 两轮优化(θ 选择 bisect 化/KC 稀疏写扫/MBON
    # 反向索引读出/heading_cue 内联/CX 核预乘)把本征成本 554→~365µs
    # (-34%),纯 Python 已近地板——numpy 向量化可再砍 3-4× 但会把 numpy
    # 卷进 PyInstaller(32MB 绿色 exe 是产品卖点,不换)。400µs = 16ms 帧
    # 预算的 2.5%,行为无感。
    for cls in (FlyConnectomeBrain, RoachBrain):
        stim = [odor(0.7), wind(0.4)]
        n = 2000
        best_us = float("inf")
        best_wall = 0.0
        for _rep in range(3):
            w, st, view = make_scene("ol6" + cls.brain_id[-2:])
            b = cls(st)
            # r25 账本行 22(口径改正,**阈值未动**):计时源 墙钟 → **本进程 CPU 时间**。
            # 原判据:perf_counter 的 2000 帧均值(3 遍 min)≤400µs。同一棵未改动的树
            # 上,它被打成过红(dump:3 遍 min 461.9µs;探针 min-of-8 533.9µs),
            # 而单跑恒绿 308~339µs —— 根因是墙钟把「被别人抢走的 CPU」算进了本征成本
            # (工作流 §5 坑表第 7 条「判据隐式依赖未受控环境输入」的套件级重演)。
            # CPU 时间只计本进程真正消耗掉的 CPU ⇒ 测的是本征成本:被抢占的墙钟
            # 不再计入。阈值语义不变(400µs = 16ms 帧预算的 2.5%)。
            # 实测(本机 13 个他人 python 进程):CPU 时间 n=8 轮 281~305µs
            # (极差/中位 8%),与墙钟同量级;分辨率 = 15.625ms/2000 帧 ≈ 7.8µs/帧。
            # ⚠ 残留(量化过,不许蒸发):满载(CPU 100%)时 CPU 时间仍被缓存/SMT
            # 争用抬到 414~523µs ⇒ **满载机上本条仍会红**,验收不得在满载机上跑。
            c0 = time.process_time()
            t0 = time.perf_counter()
            for i in range(n):
                b.observe(view, stim, DT)
                b.decide(view)
                if i % 200 == 50:
                    b.on_event("fed", {})
                if i % 500 == 250:
                    b.on_event("grab", {})
            wall_us = (time.perf_counter() - t0) / n * 1e6
            us = (time.process_time() - c0) / n * 1e6
            if us < best_us:
                best_us, best_wall = us, wall_us
        avg_us = best_us
        print(f"    [{cls.brain_id}] observe+decide+learn {avg_us:.1f} µs/帧 CPU"
              f"(3 遍取 min;同轮墙钟 {best_wall:.1f}µs/帧 = {best_wall / max(1e-9, avg_us):.2f}×;"
              f"全链路;学习增量红线 100µs)")
        wall_hint = ""
        if best_wall > avg_us * 1.15:      # 墙钟明显高于 CPU ⇒ 本机被别人的负载挤过
            wall_hint = (f";同轮墙钟 {best_wall:.1f}µs/帧"
                         f"({best_wall / max(1e-9, avg_us):.2f}× CPU)—— "
                         f"墙钟远高于 CPU 说明本机在跑别人的活")
        assert avg_us <= 400.0, \
            (f"{cls.brain_id}: 3 遍 min {avg_us:.1f}µs CPU 超出预算"
             f"(400µs=帧预算 2.5%){wall_hint}")
    # 状态与稀疏度预算
    wf, stf, viewf = make_scene("ol6x")
    fly = FlyConnectomeBrain(stf)
    for _ in range(120):
        fly.observe(viewf, [odor(0.7)], DT)
        fly.decide(viewf)
    assert fly.state_size_bytes() < 50 * 1024, "果蝇脑节点状态应 <50KB"
    assert len(fly._elig) < 200, f"资格迹应保持稀疏,实际 {len(fly._elig)} 项"
    assert len(fly.bandit.mu) == 7, "臂表应为 7 臂"


TESTS = [test_learned_approach_and_credit,
         test_eligibility_bridges_reward_delay,
         test_extinction_and_spontaneous_recovery,
         test_bandit_convergence,
         test_rpe_write_decay,
         test_persistence_roundtrip,
         test_frame_budget]


def main() -> None:
    failed = 0
    for fn in TESTS:
        try:
            fn()
            print(f"[ok] {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"[FAIL] {fn.__name__}: {exc}")
        except Exception as exc:  # 非断言异常也要暴露
            failed += 1
            print(f"[ERROR] {fn.__name__}: {type(exc).__name__}: {exc}")
    if failed:
        print(f"在线学习测试:{len(TESTS) - failed}/{len(TESTS)} 通过")
        sys.exit(1)
    print(f"在线学习测试:全部 {len(TESTS)} 项通过")
    sys.exit(0)


if __name__ == "__main__":
    main()
