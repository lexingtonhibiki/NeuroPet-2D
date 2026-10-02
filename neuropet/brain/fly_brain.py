"""果蝇连接组简化大脑 FlyConnectomeBrain(IBrain + brain 插件)。

按 docs/references/fly_circuit_简化设计素材.md 实现 14 模块速率网络
(S1~S12 + E1,总节点 514;素材文档表内加总标称 518,4 节点差异的
诚实说明见 docs/果蝇连接组简化方案.md §7):

    S1 ORN(8) → S2 LN(4,前馈抑制) → S3 PN(8)
        ├→ S4 KC(400,固定种子稀疏随机投影,~5% 激活)
        │     └→ S5 MBON(8)+DAN(4):全脑唯一学习位,三因子可塑,
        │          DAN 由饥饿/奖惩门控(Krashes 2009)
        └→ S6 LH(6):先天直通(CO2 厌恶/食物趋向/信息素),不经学习
    S7 视觉(12):LOOM 超选择 4 象限 + 小目标 4 + 光流 2 + 威胁汇聚 2
    S8 风感(6)/ S9 温度(4)/ S10 CX 航向环(24)/ S11 GF 逃逸触发(6)
    S12 运动仲裁 WTA(16)/ E1 情绪慢变量(8,对各通路做全局增益)

关键行为(素材 §3.1):果蝇对"风+食物气味同现"提高警觉(vigilance↑ →
GF 触发阈值下降);纯风为警戒(不逃),纯气味为先天趋近(LH)。
GF 触发 = LOOM 超选择 × (风/机械感一致性) 的 ~100ms 时间一致性乘法门控
+ 1-3s 不应期 + 旁路仲裁(直接抢占 S12 WTA)。

探索转向(默认 CX 巡游)为"两态 run-and-turn":RUN 态朝世界固定航点直行
(OU 仅 ±1.7° 有界微摆),每 2.5~6s 一个决策点按 VC 短弧配方重抽(1/3 停 /
1/3 换向 / 1/3 维持),TURN 态以 20~34°/s 限幅角速度画弧 40~120°(Maye 2007;
D1 §3.2)。合成角速度恒 ≤0.6rad/s → 最小转弯半径 ≥5BL,结构性杜绝转圈
(旧版把 OU 角速度开环积分进航向偏移 _aim_off,每帧再生误差,实测 |ω| 均值
181°/s、23% 帧饱和)。航点仅作近距离跑道(前向 ±46°、距离 ≥3BL)。物种差异:
果蝇不做 thigmotaxis 贴墙(deskbug brain.py:877-878 对飞行种 can_fly 直接
返回 0 偏置),见下方 WALL_FOLLOW 注释。

在线自学习 v2(调研《神经自学习_在线升级调研.md》三件套 · 路线 A/B):
KC→MBON 写入升级为完整 RL 语义 —— ① 资格迹(τ_e=4s)弥合"气味在前、
奖励在后"的时序错配;② δ=R−γV 预测误差缩减写入(预期内奖励少写/不写);
③ CS-only/预期落空写对立消退记忆 w_ext(可自发恢复);④ 重复强化转巩固
(衰减率收紧,STM→LTM);⑤ 厌恶 MBON[4:8] 读出进入警觉度与 SEEK 增益
(修复"写而不读"死路);⑥ 自发层由多臂老虎机仲裁(操作式学习,
instinct.BanditArbiter)。持久化 kc_mbon v2 行 [j,kc,w,w_ext] + arms/n_pair。

约束:禁用 numpy,仅 list/float;节点状态 < 50KB(素材 §5,不含连接表);
PN→KC、KC→MBON 投影用固定种子,跨机器行为可复现。
"""
from __future__ import annotations

import bisect
import math
import random
import sys

from neuropet.core.contracts import (Behavior, BehaviorCommand, EmotionState,
                                     Stimulus, StimulusKind)
from neuropet.core.interfaces import IBrain
from neuropet.core.mathutil import TAU, clamp, dist, wrap_angle
from neuropet.core.plugin import PluginBase
from neuropet.core.world import WorldView

from neuropet.brain.instinct import (BANDIT_ARM_CTX, BANDIT_ARM_ZH,
                                     BanditArbiter, InstinctLibrary)
from neuropet.brain.memory import AssociationMemory, EpisodicMemory
from neuropet.brain.plasticity import rpe, three_factor_dw
from neuropet.brain.cx_ring import CXRingAttractor, heading_cue
from neuropet.brain.habituation import Habituation
from neuropet.brain.spontaneous import (Spontaneous, TRUST_TIER_ESCAPE_BIAS,
                                        TRUST_TIER_GRAB, TRUST_TIER_OWNER_STILL,
                                        TrustDynamics, trust_tier, trust_tier_zh)

# 习惯化:持续存在的刺激每多久再记一次"呈现"(秒)。见 observe() 内注释。
HABIT_SUSTAIN_S = 1.5
# 逃跑反射(GF 通路:逼近阴影 SHADOW / 身体接触 CONTACT)对习惯化高度抵抗 ——
# 生存相关的强刺激不能因重复呈现而失效,这是逃逸反射的普遍性质;因此强度
# ≥0.5 的这两类不参与习惯化,只对风/震动等弱刺激做(Rankin #7 刺激特异性)。
HABIT_EXEMPT_KINDS = ("SHADOW", "CONTACT")

# ---- 在线自学习(RPE 三件套 · 路线 A/B;调研《神经自学习_在线升级调研.md》§4.3) ----
# 全部为工程值,文献锚:τ_e=4s(trace conditioning CS-US ≤15s 为 delay 型,
# Kropf 2026 群体钟覆盖秒级间隔);γ=0.98(有效视界 ~50 步);η 沿用原标定;
# 巩固半衰期 0.999^693≈0.5(≈11.5min)/0.9999^6930≈0.5(≈1.9h)(Huang 2024)。
ELIG_TAU = 4.0            # 资格迹时间常数(s):气味在前、奖励在后 1~4s 仍可配对
ELIG_TAU_ATTACK = 0.3     # 资格迹上升时间常数(s):快上升跟踪 KC 激活,3s 内达稳态
                          # (慢累积版在重复配对下迹残留+新累积反超首次,单次写入
                          # 量 0.029→0.059 非单调,违反 Rescorla-Wagner 签名)
ELIG_FLOOR = 1e-3         # 资格迹裁剪下限(稀疏 dict 防膨胀)
GAMMA = 0.98              # 折扣因子
ETA_REWARD = 0.09         # 吸引通道学习率(沿用原标定)
ETA_PUNISH = 0.10         # 厌恶通道学习率(沿用原标定)
ETA_EXTINCT = 0.08        # 对立消退记忆写入率(CS-only / 预期落空,Das 2014)
CS_ONLY_S = 5.0           # 气味在场持续多少秒无奖励 → 触发一次消退事件
EXT_DECAY_PER_S = 0.995   # 消退记忆每秒主动遗忘(τ≈3.3min → 几分钟自发恢复,Yang 2023)
CONSOLIDATE_N = 3         # 强化 ≥3 次 → 通道转巩固(衰减率收紧,Huang 2024 STM→LTM)
DECAY_STD = 0.999         # 未巩固通道每秒慢衰减(半衰期 ≈11.5min)
DECAY_LTM = 0.9999        # 巩固通道每秒慢衰减(半衰期 ≈1.9h,"练过的忘得慢")
BANDIT_LAYER_MAX = 20     # 自发层优先级上限:学习臂只在该层排序,需求/反射不受影响
BANDIT_W_GAIN = 6.0       # 臂 UCB 分值([0,~1.7]) → WTA 分数的换算增益
BANDIT_NEAR_TIE = 1.5     # WTA 分数差小于该值才算"近平手",才做 softmax 抽样
SPONT_TRIAL_S = 4.0       # 同臂持续选中时,隔多少秒记一次"决策点"(UCB 的 t/n 口径)

# ---------------- 模块节点预算(素材文档 §1 表) ----------------
NODE_COUNTS = {
    "S1_ORN": 8, "S2_LN": 4, "S3_PN": 8, "S4_KC": 400,
    "S5_MBON": 8, "S5_DAN": 4, "S6_LH": 6, "S7_VIS": 12,
    "S8_WIND": 6, "S9_TEMP": 4, "S10_CX": 24, "S11_GF": 6,
    "S12_MOTOR": 16, "E1_EMO": 8,
}
TOTAL_NODES = sum(NODE_COUNTS.values())          # 514(素材表标称 518,见定稿文档)

# ORN 8 通道:0-5 食物类(醋/酵母/糖/果/发酵/通用),6 = CO2,7 = 信息素
N_FOOD_CH, CH_CO2, CH_MATE = 6, 6, 7
KC_N = NODE_COUNTS["S4_KC"]
KC_ACTIVE_TARGET = 20                            # ~5% KC 过阈(素材 §5-2)
PN_PER_KC_PROJ = 120                             # 每 PN 随机投 ~120/400 KC
MBON_IN_DEGREE = 60                              # 每 MBON 随机读 ~60 KC

# 时间常数(s):感觉 50-200ms,运动 100-500ms,情绪 2-20s(素材 §0-5)
TAU_ORN, TAU_PN = 0.05, 0.08
WIND_TAU_ATTACK, WIND_TAU_DECAY = 0.04, 0.15     # 风痕迹:快起慢衰(~100ms 窗)
LOOM_TAU_DECAY = 0.12
GF_BASE_TH = 0.55                                # GF 基准触发阈
GF_REFRACT = (1.0, 3.0)                          # 逃逸不应期(s,素材 §5-4)

# ---- 探索转向参数包(两态 run-and-turn;参数出处标注) ----
# OU 过程:d(ou)/dt = -ou/τ + σ·N(0,1)(即 dθ = -κ·θ·dt + σ·√dt·N,κ=1/τ),
# 原实现把该角速度状态开环积分进航向(deskbug brain.py:508-511),是"转圈"
# 根因之一。本版 OU 降级为"航向微摆纹理源":以 MICRO_RATE_* 限幅角速度
# (±0.86°/s)形式驱动航向微摆,不再做开环速率积分。σ 按 F4 冻结点"OU 退烧"从 3.0 → 1.1:
# 稳态 std = σ√(τ/2) ≈ 0.82 rad/s(旧 σ=3.0 时 2.22 rad/s 直接驱动航向,
# 巡航 90px/s 下转弯半径仅 1.4BL,数学上就是绕圈)。
OU_TAU = 1.1                 # OU 相关时间 τ(s),κ=1/τ≈0.91/s(deskbug species.py:176)
OU_SIGMA = 1.1               # OU 驱动噪声强度(F4 退烧:3.0 → 1.1;deskbug species.py:177 同源)
OU_RATE_MAX = 1.2            # OU 状态限幅 ±1.2 rad/s(F4:3.0 → 1.2;deskbug brain.py:510 同源)
EXPLORE_WP_PULL = 6.0        # 航点 P 牵引基础增益(/s):ω_wp = err·PULL·WP_P_SCALE 再限幅;
                             # 置 0 时脑忽略航点(纯直航惯性),供牵引开/关对照测试
EXPLORE_LOOKAHEAD = 180.0    # 探索目标点前视距离(px,果蝇体长 30px 的 6 倍观赏尺度)
EXPLORE_SPEED_NOISE = 0.30   # 探索速度噪声:急转减速的调制深度(行为学常识)

# ---- 屏幕区块偏好(B2/ADR-0029;调研《互动体验_情绪信任躲藏调研.md》§2.3"果蝇"列) ----
# 果蝇无贴缝习性(不进 HIDDEN),仅航点分布轻微偏中央/避任务栏区。
FLY_REGION_W = {
    "taskbar": 0.30, "corner": 0.45, "edge": 0.40,
    "in_fg": 0.45, "center": 0.60,
}

# ---- 两态转向(run-and-turn):RUN 直行 / TURN 转向事件 + 三重防御 ----
# 行为学:果蝇行走/爬行 = "直线段 + 离散转向事件"(Maye 2007 burst 结构),
# 而非连续角速度游走;转向配方取 VC 短弧重抽(D1 §3.2):角速度 20~40°/s、
# 同号限长、1/3 换向、1/3 停、偶发原地转小表演。
RUN_DUR_S = (2.5, 6.0)       # RUN 直行段时长(s;Maye 2007 直线段量级)
ARC_OMEGA = (0.35, 0.60)     # 弧线转向角速度档(rad/s ≈ 20~34°/s;VC 奔跑弧 19~37°/s)
ARC_DELTA = (0.30, 0.55)     # 单次转向幅值(rad ≈ 17°~32°):**时长安全上限**。
                             # 单弧时长 = 幅值/ω,而 ω 受半径保证(|ω|≤v/(R_MIN·BL))
                             # 与挡位 [0.35,0.60] 双重约束 ⇒ 原幅值 40°~120° 的
                             # 单弧时长可达 1.2~6.0s,与 §7"最长连续同向 <1.2s"
                             # **结构性不可同真**(实测 arc 段 1.40s 即破门)。
                             # 弧时长 ≈ 幅值/ω,故幅值必须 ≈ ω×时长 ≤0.6×0.85=0.51rad;
                             # 探索转向改由**多次短弧**承担(run-and-turn 的语义)。
ARC_DUR_S = 0.85             # 单弧**目标时长**(s):omega 由 幅值/该时长 派生后落档,
                             # 使单弧时长 ≈ ARC_DUR_S(而不是幅值与 ω 独立抽号)
ARC_TIME_MAX = 8.0           # 单次转向事件时长保险丝(s)
TURN_ENTER = 0.873           # RUN→TURN 进入阈:航点偏角 >50° 才转(滞回上阈)
TURN_EXIT = 0.175            # TURN→RUN 退出阈(10°;滞回下阈,杜绝抖振)
DAB_PROB = 1.0 / 3.0         # 决策点:1/3 停顿(VC 重抽配方:D1 §3.2)
ARC_PROB = 1.0 / 3.0         # 决策点:1/3 换向(其余 1/3 维持直行)
DAB_DUR_S = (0.25, 0.70)     # 停顿时长(s):VC"一冲一停"节奏
SHOW_PROB = 0.08             # 原地转小表演概率(VC cat==0 的 1/10 档,略降)
SHOW_DELTA = (1.571, 3.84)   # 表演转角 90°~220°(rad;<270° 同号累计上限)
SHOW_LEAD = 0.9              # 表演期 TURN 命令的目标方向超前角(rad,恒定超前→匀速原地转)
SAME_SIGN_ARCS = 2           # 连续同号随机转向事件上限(超过强制换号:VC 防锁圆)
SAME_SIGN_ROT_MAX = 4.19     # 同号累计随机转角上限(rad,240°;§5.7 同号累计 <270°)
THRESH_SAME_SIGN = 0.3       # "在转"判据(rad/s ≈17.2°/s):与 §7 验收门槛同口径
                             # (test_acceptance_final._circle 的 longest_same_dir
                             #  正是 |ω|>0.3 且同号才计"同向连续",故记账阈值必须
                             #  与之一致,否则限长与实际被计量的段不同步)
RUN_SAME_SIGN_S = 0.85       # **同号限长(时间口径)**:|ω|>0.3rad/s 且同号的
                             # 连续时长上限(s)。arc 态的 SAME_SIGN_ARCS/ROT_MAX
                             # 只覆盖离散转向事件,而实测 longest_same_dir=2.28s
                             # 的违规段**全程在 run 态**——航点 P 牵引以 ω≈0.5rad/s
                             # 追一个 ±46° 偏角,累计 73° 才收敛。必须用**时间**口径
                             # 而非转角口径:转角上限对慢转无效(0.3rad 在 ω=0.35 时
                             # 已耗时 1.7s)。0.3rad/s 与 §7"最长连续同向"门槛同口径。
RUN_STRAIGHTEN_S = 0.30      # 超限后的**拉直窗口**(s):窗口内 RUN 态不朝航点转
                             # (aim=当前航向,ω→0)→ 同向段结构性被打断,且航向不
                             # 反向(不制造往返抖动,航点仍可到达)
TURN_RADIUS_MIN_BL = 4.0     # 最小弧线半径(体长):转向角速度限幅 = v/(R_MIN·BL),
                             # 随实际速度自适应(转弯减速时角速度同步下调),
                             # 结构性保证任意速度下弧线半径 ≥4BL(任务验收 ≥3BL)
TURN_SPEED_FLOOR = 0.35      # 速度参考下限(×巡航):低速时按此参考限幅,防低速急转
BODY_LEN = 30.0              # 体长(px,species PARAMS.body_len 同值,半径换算用)
WP_P_SCALE = 0.35            # 航点 P 增益系数:ω = err·EXPLORE_WP_PULL·WP_P_SCALE
MICRO_RATE_GAIN = 0.009      # OU → 航向微摆角速度增益:稳态摆速 std≈0.007rad/s,
                             # 航向偏航被航点对准项均值回拉(位置 OU,3σ<1°),
                             # 10s 直行漂移 <5° 结构保证(防御②零漂移项)。
                             # 注:微摆必须以"角速度"形式进 lead,若以"偏角"形式
                             # 叠加,bang-bang body 会把它当整帧增量重复执行,
                             # 等效隐藏角速度 ou·gain/dt(旧版开环积分的同族缺陷)
MICRO_RATE_MAX = 0.015       # 微摆角速度限幅(rad/s,≈0.86°/s,均值回拉下 10s 漂移 <5°)
WP_DIST_T = (2.0, 4.5)       # 航点距离 = 巡航速度 × U(2.0,4.5)s(保证一段可完成的直行)
WP_ANGLE_MAX = 0.803         # 新航点与当前航向夹角限 ±46°(<TURN_ENTER 50°:
                             #  重选后不立即触发航点弧,RUN 直行段得保;B 案防御1c ±120°内)
WP_ARRIVE_MIN = 90.0         # 航点距离下限(px ≈ 3BL,防"近距目标切圆")
WP_ARRIVE_R = 70.0           # 到达半径(px):进入即前向重选(验收统计 <250px 内算到点)
CRUISE_SCALE = 90.0          # 巡航速度尺度(px/s,species PARAMS.cruise 同值,航点距离用)
HEADING_STEP_RESET = 1.2     # 单帧航向阶跃 >1.2rad(撞墙反弹)→ 复位转向状态与航点
                             # (A 案"航向阶跃检测复位";防 _aim_off 类持久偏置残留)
# 物种差异:果蝇不做 thigmotaxis 贴墙转向(deskbug brain.py:877-878 对飞行种
# can_fly 返回 0 偏置;果蝇为开放式随机 burst 觅食,无沿墙行为),
# 故无 WALL_* 参数,_explore_target 不含贴墙分支。
WALL_FOLLOW = False

# E1 下标:0 恐惧 1 饥饿 2 好奇 3 满足 4 疲劳 5 性/信息素 6 温度不适 7 悲伤
E_FEAR, E_HUNGER, E_CURI, E_SAT, E_FATIGUE, E_SEX, E_THERMAL, E_SAD = range(8)


def _food_channel(source: str) -> int:
    """食物源 → 0-5 通道(按 id 稳定散列,同源同通道)。"""
    return sum(ord(c) for c in source) % N_FOOD_CH


AIM_MIN_LEN = 24.0   # 前导点最短距离(px,≈0.8BL):再短方向解析会被数值噪声主导


def _aim_point(world: WorldView, px: float, py: float, aim: float,
               look: float) -> tuple[float, float]:
    """沿 aim 方向取前导点:越屏时**按比例缩短前导距离**,而不是裁剪坐标。

    关键:身体侧 `_steer_toward` 用"到前导点的方位角"当期望航向。若把越屏的
    前导点直接用 clamp_to_screen 裁到墙上,方位角就不再等于 aim(偏差可达
    数十度)→ 身体出现**无指令的过转**,实测 ω 达脑侧限幅 v/(4BL) 的 1.8 倍
    → 自主爬行弧半径跌破 §7 门槛 2.5BL(实测 1.92BL,候选 698 帧、
    全部集中在离墙 1~4BL 的帧)。此处解析求出沿 aim 方向还能走多远,取
    min(look, 该距离) ⇒ 前导点方位角恒 == aim,半径保证在贴墙时也成立。
    """
    m = 60.0                      # 与 WorldView.clamp_to_screen 同 margin
    w, h = world.screen
    ca, sa = math.cos(aim), math.sin(aim)
    L = look
    if ca > 1e-6:
        L = min(L, (w - m - px) / ca)
    elif ca < -1e-6:
        L = min(L, (m - px) / ca)
    if sa > 1e-6:
        L = min(L, (h - m - py) / sa)
    elif sa < -1e-6:
        L = min(L, (m - py) / sa)
    L = max(L, AIM_MIN_LEN)
    return (px + ca * L, py + sa * L)


class FlyConnectomeBrain(IBrain, PluginBase):
    """黑腹果蝇简化连接组大脑:先天(LH)/习得(MB)分离 + GF 逃逸抢占。"""

    brain_id = "fly_connectome"
    SPECIES = "fly"
    manifest = {"id": "brain.fly_connectome", "name": "果蝇连接组大脑",
                "version": "0.1", "type": "brain", "api": 1,
                "description": "FlyWire 简化 14 模块速率网络(514 节点):"
                               "KC 稀疏编码三因子学习 + GF 乘法门控逃逸 + LH 先天直通"}

    def __init__(self, state=None, app=None, **kw) -> None:
        super().__init__(app=app, **kw)
        self.state = state
        self.flyer = True
        self.level = 3
        # ---- 固定种子随机投影(素材 §5-5:跨机器可复现) ----
        rng = random.Random(20240918)  # FlyWire 主论文发表年月
        # PN→KC:每 PN 投 ~120 KC,权重 U(0.6,1.0)(连接表 #4)
        self._pn_kc: list[list[tuple[int, float]]] = [
            [(k, rng.uniform(0.6, 1.0)) for k in rng.sample(range(KC_N), PN_PER_KC_PROJ)]
            for _ in range(8)]
        # KC→MBON(可塑,核心学习位):吸引 0-3 / 厌恶 4-7,各随机子集(连接表 #5)
        # 行 = [kc_idx, w, w_ext]:w=习得效价(现状),w_ext=对立消退记忆
        # (Das 2014/Felsenberg 2017;表达读出 = w − w_ext,只中和不翻转)
        self._kc_mbon: list[list[list[float]]] = [   # [j][i] = [kc_idx, w, w_ext]
            [[k, 0.10, 0.0] for k in rng.sample(range(KC_N), MBON_IN_DEGREE)]
            for _ in range(8)]
        # 反向索引:kc → [(mbon_j, 行号)],供三因子更新只扫活跃 KC
        self._kc_mbon_rev: list[list[tuple[int, int]]] = [[] for _ in range(KC_N)]
        for j in range(8):
            for i, row in enumerate(self._kc_mbon[j]):
                self._kc_mbon_rev[int(row[0])].append((j, i))
        # ---- 节点状态(每节点 1 float 激活 + 痕迹,总状态 <50KB) ----
        self.orn = [0.0] * 8
        self.ln = [0.0] * 4
        self.pn = [0.0] * 8
        self.kc = [0.0] * KC_N
        self.mbon = [0.0] * 8
        self.dan = [0.0] * 4          # 0 奖励 1 惩罚 2 熟悉 3 新异
        self.lh = [0.0] * 6           # 0-1 食物趋向 2 CO2 厌恶 3 腐败 4 预留 5 信息素
        self.vis = [0.0] * 12         # 0-3 LOOM 象限 4-7 小目标 8-9 光流 10-11 威胁汇聚
        self.wind = [0.0] * 6         # 0-3 象限 4 全局 5 碰撞/触碰
        self.temp = [0.0] * 4         # 0 冷厌恶 1 热厌恶 2 舒适带 3 温度告警
        self.eb = [0.0] * 16          # EB 航向环(环吸引子槽位,由 self.cx 同步)
        self.cx = CXRingAttractor(n=16)   # 真环形吸引子(持久活动+角速度积分)
        self.habit = Habituation()        # 非联想学习:习惯化/敏化/去习惯化
        self._habit_on: dict[str, float] = {}   # 各刺激已持续时长(呈现计数用)
        # 自发行为层(互动性 B01 理毛 / B02 主动找主人 / B03 昼夜节律)。
        # 果蝇为晨昏性 → nocturnal=False。
        self.spon = Spontaneous(nocturnal=False)
        self.fb = [0.0] * 4           # FB 行动门控
        self.no = [0.0] * 2           # 速度
        self.pen = [0.0] * 2          # 角速度整合
        self.gf = [0.0] * 6           # 0 驱动 1 阈值 2-3 左右 GF 4-5 抢占总线
        self.motor = [0.0] * 16       # 8 原语 + 8 增益
        self.emo = [0.0, 0.35, 0.5, 0.15, 0.0, 0.0, 0.0, 0.0]
        self._trust = 0.2
        self._trust_dyn = TrustDynamics()   # 信任事件记账(A2,调研 §1)
        # r25 A4:熟练度统计(经历计数;随 save()/load() 持久化)
        self._n_fed = 0        # 被投喂次数(on_event "fed")
        self._n_danger = 0     # 遇险次数(on_event "grab")
        self._valence = 0.0
        # ---- 痕迹 / GF 门控状态 ----
        self._wind_trace = 0.0
        self._loom_trace = 0.0
        self._loom_now = 0.0
        self._odor_now = 0.0
        self._co2_now = 0.0
        self._small_now = 0.0
        self._mech_now = 0.0
        self._odor_dir = (1.0, 0.0)   # 食物气味来向单位向量(指向源)
        self._co2_dir = (1.0, 0.0)
        self._threat_dir = (1.0, 0.0) # 威胁来向(逃逸取反方向)
        self._gf_free_at = -1.0       # 不应期截止
        self._gf_fired = False
        self.vigilance = 0.0          # 警觉度(风/风+气味共现抬高 → GF 阈下降)
        self._prev_loom_int: dict[str, float] = {}   # 每源的上一拍强度(扩张率)
        self._kc_active = 0
        self._kc_active_idx: list[int] = []          # 本拍过阈 KC 索引(资格迹标签用)
        self._familiar = 0.0
        self._novelty = 1.0
        self._mb_decay_acc = 0.0
        self._prev_heading = state.heading if state else 0.0
        # ---- 记忆/情绪/本能 ----
        self.episodic = EpisodicMemory(self.level)
        self.assoc = AssociationMemory(self.level)
        self.instinct = InstinctLibrary.default()
        # ---- 运行时 ----
        self._t = 0.0
        self._rng = random.Random(7)  # 行为噪声(航向漂移/不应期)固定种子
        self._wp: tuple[float, float] | None = None
        self._wp_until = 0.0
        # OU 微摆纹理状态(rad/s;经 MICRO_RATE_* 限幅后并入转向角速度,不再开环积分)
        self._ou = 0.0
        self._dt_last = 1.0 / 60.0
        # ---- 两态转向状态机(run-and-turn) ----
        self._mode = "run"            # run 直行 / arc 弧线转向 / dab 停顿 / show 原地转表演
        self._mode_until = self._rng.uniform(*RUN_DUR_S)   # 当前子状态截止时刻(s)
        self._cruise_cmd: BehaviorCommand | None = None    # dab/show 当拍命令(优先级 11)
        self._turn_sign = 1.0         # 当前转向事件符号(+1 逆时针 / -1 顺时针)
        self._turn_omega = 0.0        # 当前弧线角速度档(rad/s)
        self._turn_rem = 0.0          # 剩余转角(rad;show 期 = 目标转角)
        self._turn_start_h = 0.0      # show 起始航向(按实际转角判完成)
        self._arc_random = False      # 当前弧是否随机换向事件(航点对准弧不进防锁圆记账)
        self._streak_sign = 0         # 连续同号随机转向事件计数(防锁圆:VC 强制重抽)
        self._streak_n = 0
        self._streak_rot = 0.0        # 同号累计随机转角(rad)
        self._run_sign = 0            # 同号转向段符号(全局口径,覆盖航点牵引)
        self._run_n = 0               # 同号段已持续帧数(|ω|>0.3rad/s 口径)
        self._wp_side = 0             # 下次航点重选的强制侧(+1/-1;0=自由抽号)
        self._straight_until = 0.0    # 拉直窗口截止时刻(s)
        self._omega_meas = 0.0        # 上一拍实测角速度(rad/s,observe 缓存,转向记账用)
        self._cmd_queue: list[BehaviorCommand] = []
        self._injected: list[Stimulus] = []
        self._last_stimuli: list[Stimulus] = []
        self._dan_reward = 0.0
        self._dan_punish = 0.0
        # ---- 在线自学习状态(RPE+资格迹+对立消退+巩固+老虎机;调研 §4.2) ----
        self._elig: dict[int, float] = {}   # kc → 资格迹(稀疏,稳态幅值=KC 激活)
        self._v_attract = 0.0               # V(s)=0.6·mean(MBON[0:4]),每帧重算
        self._v_averse = 0.0                # 厌恶通道读出(修复"写而不读"死路)
        self._aversion = 0.0                # decide()/警觉度使用的厌恶水平
        self._n_pair = [0] * 8              # 每通道强化配对计数(巩固记账)
        self._d_mbon = [DECAY_STD] * 8      # 每通道每秒慢衰减率(巩固后收紧)
        self._cs_only_acc = 0.0             # 气味在场且无奖励的累计时长(s)
        self._dan_pending_r = 0.0           # 待处理奖励事件 R(边沿触发单步写入)
        self._dan_pending_p = 0.0           # 待处理惩罚事件 R
        self.last_rpe = 0.0                 # 最近一次奖励事件 δ(遥测/验收断言)
        self.bandit = BanditArbiter(level=self.level)
        self.bandit.seed_from_instinct(self.instinct, self.SPECIES)
        self._active_arm: str | None = None  # 最近选中的学习臂(操作式信用分配)
        self._arm_trial_t = -1e9             # 上次臂决策点记账时刻(s)
        # ---- SCREEN_EVENT 好奇候选记账(B2/ADR-0029;hwnd -> 上次处理时刻) ----
        self._evt_cur: dict[int, float] = {}

    # ================= 感知(observe:全部 14 模块推进一拍) =================
    def observe(self, world: WorldView, stimuli: list[Stimulus], dt: float) -> None:
        self._t += dt
        for s in self._injected:
            stimuli.append(s)
        self._injected.clear()
        self._last_stimuli = stimuli
        dt = max(1e-4, dt)
        self._dt_last = dt
        # ---- 探索转向 OU 纹理(deskbug brain.py:508-511 同构;Maye 2007) ----
        # 退烧后(σ=1.1)稳态 std≈0.82rad/s,仅作 RUN 态 ±1.7° 航向微摆的
        # 驱动源;不再像旧版那样把 _ou 开环积分进航向偏移(转圈根因之一)。
        self._ou = clamp(self._ou + (-self._ou / OU_TAU) * dt
                         + OU_SIGMA * math.sqrt(dt) * self._rng.gauss(0.0, 1.0),
                         -OU_RATE_MAX, OU_RATE_MAX)

        # ---- 非联想学习:习惯化/敏化按刺激种类缩放强度(Rankin 2009) ----
        # 这是真实昆虫最普遍的可塑形式:反复无害的风/阴影不再每次都触发满额
        # 逃逸。此前完全缺失(审计 R2),"重复吓它反应不变"是不真实的主因。
        self.habit.decay(dt)
        if stimuli:
            inten: dict[str, float] = {}
            for s in stimuli:
                k = getattr(s.kind, "name", str(s.kind))
                inten[k] = max(inten.get(k, 0.0), min(1.0, s.intensity))
            # 呈现计数:起始记一次,持续存在每 HABIT_SUSTAIN_S 再记一次。
            # 不能逐帧记 —— 60fps 下连续风 1 秒就记满 60 次,会导致"鼠标一直
            # 动就再也不逃";习惯化由**离散呈现**驱动(Rankin #4 频率效应)。
            for k, iv in inten.items():
                if k in HABIT_EXEMPT_KINDS and iv >= 0.5:
                    continue               # 逃跑反射不参与习惯化,见常量注释
                if k not in self._habit_on:
                    self._habit_on[k] = 0.0
                    self.habit.present(k, iv)
                else:
                    self._habit_on[k] += dt
                    if self._habit_on[k] >= HABIT_SUSTAIN_S:
                        self._habit_on[k] = 0.0
                        self.habit.present(k, iv)
            for k in [k for k in self._habit_on if k not in inten]:
                del self._habit_on[k]
            scaled = []
            for s in stimuli:
                k = getattr(s.kind, "name", str(s.kind))
                g = min(1.0, self.habit.gain(k))
                scaled.append(s if g >= 0.999 else
                              Stimulus(kind=s.kind, source=s.source, pos=s.pos,
                                       intensity=s.intensity * g,
                                       direction=s.direction, meta=s.meta))
            stimuli = scaled
        # ---- 自发行为计时(互动性 B01/B02/B03) ----
        # 用习惯化之后的强度判定"是否真的无事发生";peak<0.15 才算空闲。
        self.spon.update(dt, max((s.intensity for s in stimuli), default=0.0),
                         getattr(world, "cursor", None))
        # ---- 感觉汇入:外部事件 → 通道驱动 ----
        orn_drive = [0.0] * 8
        wind_q = [0.0] * 4
        loom_q = [0.0] * 4          # 各象限阴影原始强度
        loom_rise = [0.0] * 4       # 扩张率(强度增量归一)
        wind_glob = vib = contact = cold = light = 0.0
        px, py = self.state.pos
        for s in stimuli:
            k = s.kind
            if k == StimulusKind.ODOR_FOOD:
                ch = _food_channel(s.source)
                orn_drive[ch] = max(orn_drive[ch], s.intensity)
                if s.direction:
                    self._odor_dir = s.direction
            elif k == StimulusKind.ODOR_MATE:
                orn_drive[CH_MATE] = max(orn_drive[CH_MATE], s.intensity)
            elif k == StimulusKind.SHADOW:
                q = self._quadrant(s, px, py)
                prev = self._prev_loom_int.get(s.source, s.intensity)
                if s.intensity > loom_q[q]:
                    loom_q[q] = s.intensity
                    # 超选择条件之一:面积膨胀率(素材连接表 #10)
                    loom_rise[q] = clamp((s.intensity - prev) / 0.02)
                    self._threat_dir = self._to_source(s, px, py)
            elif k == StimulusKind.WIND:
                q = self._quadrant(s, px, py)
                wind_q[q] = max(wind_q[q], s.intensity)
                wind_glob = max(wind_glob, s.intensity)
            elif k == StimulusKind.VIBRATION:
                vib = max(vib, s.intensity)   # 机械感 → GF 多模态成分(#13)
            elif k == StimulusKind.CONTACT:
                contact = max(contact, s.intensity)
            elif k == StimulusKind.COLD:
                cold = max(cold, s.intensity)
            elif k == StimulusKind.LIGHT:
                light = max(light, s.intensity)
        for s in stimuli:                 # 扩张率参照帧后更新
            if s.kind == StimulusKind.SHADOW:
                self._prev_loom_int[s.source] = s.intensity

        # ---- S1-S3 嗅觉前级:ORN → LN 前馈抑制 → PN(连接表 #1-3) ----
        a_orn = min(1.0, dt / TAU_ORN)
        a_pn = min(1.0, dt / TAU_PN)
        for i in range(8):
            self.orn[i] += (orn_drive[i] - self.orn[i]) * a_orn
        total_orn = sum(self.orn)
        inh = 1.0 / (1.0 + 0.8 * total_orn)          # LN 增益控制,防饱和
        for j in range(4):
            self.ln[j] += (clamp(total_orn * 0.3) - self.ln[j]) * a_orn
        for i in range(8):
            self.pn[i] += (clamp(self.orn[i] * inh * 0.95) - self.pn[i]) * a_pn

        # ---- S4 蘑菇体 KC:稀疏编码 ----
        self._step_kc()

        # ---- S5 MBON/DAN:三因子可塑(唯一学习位) ----
        self._step_mb(dt)

        # ---- S6 侧角 LH:先天直通(绕过 KC,连接表 #7-9) ----
        food = max(self.pn[:N_FOOD_CH]) if N_FOOD_CH else 0.0
        self.lh[0] += (food * 0.90 - self.lh[0]) * a_pn    # 食物趋向
        self.lh[1] += (food * 0.60 - self.lh[1]) * a_pn
        self.lh[2] += (self.orn[CH_CO2] * 1.00 - self.lh[2]) * a_orn   # CO2 天然厌恶
        self.lh[3] += (self.orn[CH_CO2] * 0.35 - self.lh[3]) * a_pn    # 腐败警戒(近似)
        self.lh[4] += (light * 0.4 - self.lh[4]) * a_pn                # 预留:光厌恶
        self.lh[5] += (self.orn[CH_MATE] * 0.90 - self.lh[5]) * a_pn   # 信息素

        # ---- S7 视觉:LOOM 超选择 + 小目标 + 光流 + 汇聚 ----
        for q in range(4):
            sel = clamp((loom_q[q] - 0.5) * 2.0) * (0.3 + 0.7 * loom_rise[q])
            self.vis[q] += (sel - self.vis[q]) * min(1.0, dt / 0.03)
        cur = world.cursor
        d_cur = dist(px, py, cur.x, cur.y)
        small = clamp(1.0 - d_cur / 180.0) * clamp(1.0 - cur.speed / 220.0) \
            if d_cur < 180 else 0.0
        self._small_now = small
        for i in range(4, 8):
            self.vis[i] += (small - self.vis[i]) * min(1.0, dt / 0.1)
        speed_norm = clamp(self.state.speed / 400.0)
        self.vis[8] += (speed_norm - self.vis[8]) * min(1.0, dt / 0.1)
        self.vis[9] = self.vis[8] * 0.6
        loom_sel = max(self.vis[0:4])                     # 威胁汇聚(连接表 #10-11)
        self._loom_now = loom_sel
        self.vis[10] += (loom_sel - self.vis[10]) * min(1.0, dt / 0.02)
        # 连接表 #22 运动拷贝:自运动光流被预期抵消,不并入 LOOM
        self.vis[11] = max(self.vis[11] * math.exp(-dt / LOOM_TAU_DECAY), loom_sel)

        # ---- S8 风感(JON):快攻击慢衰减痕迹(~100ms 一致性窗) ----
        for q in range(4):
            v = wind_q[q]
            w = self.wind[q]
            self.wind[q] = w + (v - w) * (min(1.0, dt / WIND_TAU_ATTACK) if v > w
                                          else 1.0 - math.exp(-dt / WIND_TAU_DECAY))
        g = wind_glob
        self._wind_trace = self._wind_trace + \
            (g - self._wind_trace) * (min(1.0, dt / WIND_TAU_ATTACK) if g > self._wind_trace
                                      else 1.0 - math.exp(-dt / WIND_TAU_DECAY))
        self.wind[4] = self._wind_trace
        self.wind[5] += (max(vib, contact) - self.wind[5]) * min(1.0, dt / 0.02)
        self._mech_now = max(self._wind_trace, vib, contact * 0.8)

        # ---- S9 温度:偏好 ~25°C,冷/热厌恶 + 告警 ----
        self.temp[0] += (clamp(cold * 1.2) - self.temp[0]) * min(1.0, dt / 0.3)
        self.temp[1] *= math.exp(-dt / 1.0)               # 热通道本期无刺激源,预留
        self.temp[2] = 1.0 - max(self.temp[0], self.temp[1])
        self.temp[3] = max(self.temp[0], self.temp[1])

        # ---- S10 CX:真环形吸引子(EB/P-EN)+ FB 门控 + NO 速度 ----
        # 旧实现把当前 heading 低通复制成一个单峰 —— 它是 heading 的**函数**
        # 而非**记忆**:断输入即消失、也无法靠角速度更新航向(无路径积分),
        # 审计 R1 判其为"装饰性环"。现改为 neuropet/brain/cx_ring.py:
        # 持久活动(Seelig 2015)+ P-EN 角速度位移(Turner-Evans 2017)+
        # 线索锚定;self.eb 同步其活动,下游(EB→FB 门控/快照)无需改动。
        omega = wrap_angle(self.state.heading - self._prev_heading) / dt
        self.cx.step(dt, omega=omega,
                     cue=heading_cue(self.state.heading, self.cx.n, 1.2),
                     cue_gain=0.5)          # 有视觉时锚定,暗处靠积分维持
        self.eb = self.cx.activity()
        h = int((self.state.heading % TAU) / TAU * 16) % 16
        self._prev_heading = self.state.heading
        self._omega_meas = omega
        # 航向阶跃检测(A 案复位项):撞墙反弹等单帧大转角 → 复位两态状态与航点,
        # 杜绝"_aim_off 类持久偏置"在航向突变后残留(旧版反弹后目标点转到身后,
        # 靠牵引花 0.5~1s 拉回,期间是一段紧密圆弧)
        if abs(omega) > HEADING_STEP_RESET:
            self._reset_cruise()
        self.pen[0] += (clamp(abs(omega) * 0.3) - self.pen[0]) * min(1.0, dt / 0.1)
        self.pen[1] = clamp(omega * 0.2, -1.0, 1.0)
        self.no[0] += (speed_norm - self.no[0]) * min(1.0, dt / 0.2)
        self.no[1] = clamp(self.state.speed / 900.0)
        for i in range(4):
            self.fb[i] = clamp(1.0 - self.emo[E_FATIGUE]) * (0.6 + 0.4 * self.eb[h])

        # ---- 警觉度:风+食物气味同现 → GF 阈值下降(任务关键行为) ----
        self._odor_now = max(self.pn[:N_FOOD_CH])
        self._co2_now = self.lh[2]
        co_occur = clamp(min(self._wind_trace, self._odor_now) * 2.5)
        # 学得的厌恶上下文抬高警觉:被抓/危险配过的气味 → 更容易惊
        # (修复厌恶 MBON[4:8]"写而不读"死路的行为学出口)
        self.vigilance = clamp(0.6 * self._wind_trace + 0.8 * co_occur
                               + 0.3 * self._aversion)

        # ---- E1 情绪:一阶低通的威胁/奖励计数器(素材 §4) ----
        # 注:E1 与 GF 并行接收感觉输入(LPLC2 双投射);恐惧先更新再进 GF 门,
        # 体现"威胁环境更易惊跳"的增益放大,而不是滞后一拍。
        danger = max(loom_sel * 0.8, contact, self._co2_now * 0.5)
        self.emo[E_FEAR] = max(self.emo[E_FEAR] * math.exp(-dt / 6.0), clamp(danger))
        self.emo[E_HUNGER] = clamp(self.emo[E_HUNGER] + 0.004 * dt)
        curi_target = clamp(0.35 + 0.5 * self._small_now + 0.35 * self._novelty
                            - 0.3 * self.emo[E_SAT])
        self.emo[E_CURI] += (curi_target - self.emo[E_CURI]) * min(1.0, dt / 30.0)
        self.emo[E_SAT] = max(self.emo[E_SAT] * math.exp(-dt / 240.0),
                              clamp(self.temp[2]) * 0.5)
        move = speed_norm * dt * 0.02
        self.emo[E_FATIGUE] = clamp(self.emo[E_FATIGUE] + move - dt * 0.01)
        self.emo[E_SEX] = self.lh[5]
        self.emo[E_THERMAL] = self.temp[3]
        if not stimuli:
            self.emo[E_SAD] = clamp(self.emo[E_SAD] + dt * 0.002)
        else:
            self.emo[E_SAD] = max(0.0, self.emo[E_SAD] - dt * 0.01)
        self._valence += ((self.emo[E_SAT] * 0.8 - self.emo[E_FEAR] * 0.8)
                          - self._valence) * min(1.0, dt / 5.0)

        # ---- 信任双向动力学(A2,调研《互动体验_情绪信任躲藏调研.md》§1) ----
        # trust' = clamp01(trust + Δ_event + K_CO·dt·G_fear − (trust−0.2)·dt/τ)
        # 事件项:slow_approach(光标低速持续接近,复用 perception EMA 速度)
        # + 主动趋近抵达(approach_cursor 臂执行中进入 80px 且光标静止);
        # 共处/回归项无状态。grab/streak 事件项在 on_event 侧记账。
        cur_dyn = getattr(world, "cursor", None)
        arrive_credit = 0.0
        if self._active_arm == "approach_cursor":
            arrive_credit = self._trust_dyn.arrival(
                px, py, cur_dyn, self._t, self.spon.since_cursor_s)
        self._trust = clamp(
            self._trust
            + self._trust_dyn.passive_delta(dt, self.emo[E_FEAR], self._trust)
            + self._trust_dyn.slow_approach(dt, px, py, cur_dyn, self._t)
            + arrive_credit)

        # ---- S11 GF:时间一致性乘法门控 + 不应期(素材 §3.1 / 连接表 #13-15) ----
        coh = clamp(self._mech_now * 1.2)                 # 风/机械一致性 0..1
        drive = loom_sel * (0.6 + 0.4 * coh) + contact * 1.0   # 碰撞 w=1.0 几乎必达
        theta = self.gf_threshold()
        self.gf[0] += (drive - self.gf[0]) * min(1.0, dt / 0.02)
        self.gf[1] = theta
        self.gf[2] = drive * (1.0 - coh * 0.5)            # 左右 GF 近似对称
        self.gf[3] = drive
        self.gf[4] = self.gf[5] = 1.0 if (drive > theta) else 0.0
        if drive > theta and self._t >= self._gf_free_at and \
                (loom_sel > 0.05 or contact > 0.05):
            self._gf_fired = True
            self._gf_free_at = self._t + self._rng.uniform(*GF_REFRACT)
            self.emo[E_FEAR] = 1.0
            self._dan_punish = max(self._dan_punish, 1.0)  # 危险上下文写入厌恶记忆
            self._dan_pending_p = max(self._dan_pending_p, 1.0)
            self.episodic.remember("巨纤维触发:逼近威胁,紧急逃逸!", salience=0.85,
                                   ts=self._t)

        # ---- 学习痕迹衰减 / 记忆遗忘推进 ----
        self._dan_reward *= math.exp(-dt / 0.5)
        self._dan_punish *= math.exp(-dt / 0.5)
        self._mb_decay_acc += dt
        if self._mb_decay_acc >= 1.0:                     # 每秒一次慢衰减(遗忘)
            self._mb_decay_acc = 0.0
            for j in range(8):
                dj = self._d_mbon[j]                      # 巩固通道衰减更慢(Huang 2024)
                for row in self._kc_mbon[j]:
                    row[1] *= dj
                    if row[2] > 0.0:
                        # 消退记忆被主动遗忘 → 趋近自发恢复(Yang 2023)
                        row[2] *= EXT_DECAY_PER_S
        self.episodic.tick(dt)
        self.assoc.tick(dt)

    # ---- KC 稀疏编码:高分位阈值使 ~5% KC 过阈(素材 §5-2) ----
    def _step_kc(self) -> None:
        # r24 性能:第 20 大值用 (v,i) 元组 + bisect.insort 的 20 槽升序表
        # (C 级 memmove,替代 sorted(400)/heapq 包装);KC 写入/清除只触
        # 活跃集(prev 清 + new 写,替代全 400 扫);mean = 活跃值直和
        # (0.0 加法精确等价于全量和)。数值逐位等价于旧实现。
        acc = [0.0] * KC_N
        driven = False
        pn = self.pn
        pn_kc = self._pn_kc
        for i in range(8):
            pv = pn[i]
            if pv <= 0.05:
                continue
            driven = True
            for k, w in pn_kc[i]:
                acc[k] += pv * w
        kc = self.kc
        act = self._kc_active_idx
        if not driven:
            for i in act:
                kc[i] = 0.0
            act.clear()
            self._kc_active = 0
            return
        insort = bisect.insort
        top: list = []
        # 只扫候选(被驱动 PN 的出边并集,通常 ≤240/400),免全 400 enumerate
        cand = set()
        cand_add = cand.add
        for i in range(8):
            pv = pn[i]
            if pv > 0.05:
                for k, _w in pn_kc[i]:
                    cand_add(k)
        for k in cand:
            v = acc[k]
            if len(top) < KC_ACTIVE_TARGET:
                insort(top, (v, k))
            elif v > top[0][0]:
                del top[0]
                insort(top, (v, k))
        if len(top) < KC_ACTIVE_TARGET:
            theta = 0.0                                   # 非零不足 20:第 20 大=0
        else:
            theta = top[0][0]
        for i in act:                                     # 只清上帧活跃
            kc[i] = 0.0
        act.clear()
        n_act = 0
        s_act = 0.0
        for v, i in top:
            if v > theta and v > 1e-6:
                kc[i] = min(1.0, (v - theta) * 2.5)
                n_act += 1
                act.append(i)
                s_act += kc[i]
        self._kc_active = n_act
        mean_act = s_act / KC_N                           # 非活跃项恒 0,直和等价
        self._familiar += (mean_act - self._familiar) * 0.05   # 熟悉度慢变量
        self._novelty = clamp(1.0 - self._familiar * 6.0)
        self.dan[2] = clamp(self._familiar * 6.0)
        self.dan[3] = self._novelty

    # ---- MBON 读出 + DAN 门控三因子写入(素材 §5-3;v2:RPE+资格迹+对立消退) ----
    def _step_mb(self, dt: float) -> None:
        # ① 资格迹:稀疏衰减 + 活跃 KC 打标签(elig ← elig·e^(−dt/τ) + KC·dt/τ,
        #    稳态幅值 = KC 激活;"气味在场打标签、奖励到达才落实",Frémaux 2016。
        #    修复缺陷①:奖励只写"当下活跃 KC"导致气味在前/奖励在后的配对丢失)
        kdec = math.exp(-dt / ELIG_TAU)
        # 双时间常数:上升沿快速逼近 KC 激活(稳态幅值=KC,与时长弱相关,
        # 保证重复配对的单次写入量由 δ 主导而单调回落);KC 归零后按 ELIG_TAU
        # 慢衰减,保留"气味在前、奖励在后"的桥接(Frémaux 2016 打标签语义)
        atk = min(1.0, dt / ELIG_TAU_ATTACK)
        elig = self._elig
        for k in list(elig):
            kc_v = self.kc[k]
            if kc_v > elig[k]:
                elig[k] = elig[k] + (kc_v - elig[k]) * atk
            else:
                e = elig[k] * kdec
                if e < ELIG_FLOOR:
                    del elig[k]
                else:
                    elig[k] = e
        for k in self._kc_active_idx:
            if k not in elig:
                elig[k] = min(2.0, self.kc[k] * atk)
        # ② MBON 表达读出 = 0.6·Σ(w − w_ext)·KC:吸引与对立消退记忆中和
        #    (Das 2014 平行对立记忆;decide() 读表达值而非裸 w)
        #    r24 性能:KC 稀疏(≤20/400 活跃),按反向索引只扫活跃 KC 的出边
        #    (~24 次触达)替代 8×60 全行扫描;kv=0 行贡献恒 0,数学等价
        #    (求和顺序差异 ~1e-16,远小于验收容差;逐次运行顺序确定)。
        kc = self.kc
        rev = self._kc_mbon_rev
        s = [0.0] * 8
        for k in self._kc_active_idx:
            kv = kc[k]
            for j, i in rev[k]:
                row = self._kc_mbon[j][i]
                s[j] += kv * (row[1] - row[2])
        mbon = self.mbon
        for j in range(8):
            mbon[j] = clamp(0.6 * s[j])
        self._v_attract = sum(self.mbon[0:4]) / 4.0
        self._v_averse = sum(self.mbon[4:8]) / 4.0
        self._aversion = self._v_averse
        # ③ 奖励/惩罚事件:RPE 驱动的单步写入(事件级,非逐帧批量)
        if self._dan_pending_r > 0.0:
            self._learn_reward_event()
        if self._dan_pending_p > 0.0:
            self._learn_punish_event()
        # ④ CS-only 消退监测:气味在场 ≥CS_ONLY_S 秒且无奖励 DAN → 写对立记忆
        if self._odor_now > 0.12 and self.dan[0] <= 0.01:
            self._cs_only_acc += dt
            if self._cs_only_acc >= CS_ONLY_S:
                self._cs_only_acc = 0.0
                self._extinguish()
        else:
            self._cs_only_acc = 0.0
        # ⑤ DAN 痕迹读出(调质可视化/消退门控;写入已改为事件驱动)
        dec = math.exp(-dt / 0.5)                         # DAN 痕迹(多巴胺信号 ~秒级)
        self.dan[0] = max(self.dan[0] * dec, self._dan_reward)
        self.dan[1] = max(self.dan[1] * dec, self._dan_punish)

    def _learn_reward_event(self) -> None:
        """奖励事件单步三因子写入:δ = R − γ·V(Rescorla-Wagner;Bennett 2021)。

        修复缺陷②:旧版对活跃 KC 无差别等强写入,反复投喂权重顶满 clamp——
        现在预期内奖励(δ→0/饱食 gate→0)自然少写、不写。信用分配扫资格迹
        而非"当下活跃 KC"。 """
        r = self._dan_pending_r
        self._dan_pending_r = 0.0
        gate = clamp((self.emo[E_HUNGER] - 0.2) / 0.5)    # 饥饿门控(Krashes 2009)
        delta = rpe(r, GAMMA * self._v_attract)
        self.last_rpe = delta
        if delta > 0.0 and gate > 0.0:
            self._write_weight((0, 1, 2, 3), ETA_REWARD * gate, delta)
            for j in range(4):                # STM→LTM 巩固记账(Huang 2024)
                self._n_pair[j] += 1
                self._d_mbon[j] = (DECAY_LTM if self._n_pair[j] >= CONSOLIDATE_N
                                   else DECAY_STD)
            # US 已落实写入 → 资格迹消费掉(每试次信用独立):否则上一试次
            # 残迹(3.6s 间隔后仍余 41%)叠加本次迹,重复投喂单次写入量非单调
            # (0.054→0.074),违反 Rescorla-Wagner 签名。饱食未写不清,
            # 保留与下次投喂的桥接信用。
            self._elig.clear()
        elif delta < 0.0:
            # 预期落空(过饱投喂/过预测)→ 写对立记忆而非等强正写入
            self._extinguish(ETA_EXTINCT * (-delta))

    def _learn_punish_event(self) -> None:
        """惩罚事件单步写入厌恶通道 δ = R − γ·V_av(预期危险少写,同 RW)。"""
        r = self._dan_pending_p
        self._dan_pending_p = 0.0
        delta = rpe(r, GAMMA * self._v_averse)
        if delta > 0.0:
            self._write_weight((4, 5, 6, 7), ETA_PUNISH, delta)
            for j in range(4, 8):
                self._n_pair[j] += 1
                self._d_mbon[j] = (DECAY_LTM if self._n_pair[j] >= CONSOLIDATE_N
                                   else DECAY_STD)
            self._elig.clear()                # 惩罚写入同样消费资格迹(同上)

    def _extinguish(self, strength: float = ETA_EXTINCT) -> None:
        """CS-only/预期落空:向吸引通道写对立消退记忆 w_ext(上限=w,只中和
        不翻转 —— 对立记忆模型,Felsenberg 2017/Jürgensen 2024)。"""
        for k, e in self._elig.items():
            dw = strength * e
            if dw <= 0.0:
                continue
            for j, idx in self._kc_mbon_rev[k]:
                if j < 4:
                    row = self._kc_mbon[j][idx]
                    if row[1] > row[2]:
                        row[2] = min(row[1], row[2] + dw)

    def _write_weight(self, mbons: tuple[int, ...], eta: float,
                      delta: float = 1.0) -> None:
        """三因子写入:Δw = η·δ·elig·gate(经 plasticity.three_factor_dw)。

        只扫资格迹非零 KC 的反向索引:稀疏、事件级单步;elig 把"奖励到达前
        数秒内活跃过的 KC"全部纳入信用分配。"""
        if eta <= 0.0:
            return
        mset = set(mbons)
        for k, e in self._elig.items():
            if e < ELIG_FLOOR:
                continue
            for j, idx in self._kc_mbon_rev[k]:
                if j in mset:
                    row = self._kc_mbon[j][idx]
                    row[1] = min(1.0, row[1] + three_factor_dw(eta, delta, e))

    @staticmethod
    def _quadrant(s: Stimulus, px: float, py: float) -> int:
        """刺激来向 → 视觉/风象限:0 右 1 下 2 左 3 上。"""
        if s.direction:
            dx, dy = s.direction
        else:
            d = max(1e-3, dist(px, py, *s.pos))
            dx, dy = (s.pos[0] - px) / d, (s.pos[1] - py) / d
        if abs(dx) >= abs(dy):
            return 0 if dx > 0 else 2
        return 1 if dy > 0 else 3

    @staticmethod
    def _to_source(s: Stimulus, px: float, py: float) -> tuple[float, float]:
        """指向刺激源的单位向量(direction 优先,几何兜底)。"""
        if s.direction:
            d = math.hypot(*s.direction) or 1.0
            return (s.direction[0] / d, s.direction[1] / d)
        d = max(1e-3, dist(px, py, *s.pos))
        return ((s.pos[0] - px) / d, (s.pos[1] - py) / d)

    # ---- GF 触发阈值:E1 恐惧与警觉度(风/风+气味)压低阈值 + 信任档位偏置 ----
    def gf_threshold(self) -> float:
        # 信任档位调制(调研 §1 分档行为表):怕人 −0.08(更易惊),
        # 警惕 0(基线),习惯/主动靠近 +0.05(温和共处后惊跳钝化)
        return clamp(GF_BASE_TH + TRUST_TIER_ESCAPE_BIAS[trust_tier(self._trust)]
                     - 0.30 * self.vigilance - 0.20 * self.emo[E_FEAR],
                     0.15, 0.85)

    def kc_active_fraction(self) -> float:
        return self._kc_active / KC_N

    def state_size_bytes(self) -> int:
        """节点状态占用估算(不含连接表,素材 §5-1:每节点激活+上拍+可塑迹)。"""
        total = 0
        for name in ("orn", "ln", "pn", "kc", "mbon", "dan", "lh", "vis",
                     "wind", "temp", "eb", "fb", "no", "pen", "gf", "motor", "emo"):
            lst = getattr(self, name)
            total += sys.getsizeof(lst) + 8 * len(lst)
        return total + 8 * TOTAL_NODES * 2   # 上拍/可塑迹预算

    # ================= 决策(decide:S12 WTA 仲裁) =================
    def decide(self, world: WorldView) -> BehaviorCommand:
        if self._cmd_queue:
            return self._cmd_queue.pop(0)
        px, py = self.state.pos
        # 连接表 #15:GF 旁路仲裁层,逃逸独占最高优先级
        if self._gf_fired:
            self._gf_fired = False
            fx, fy = -self._threat_dir[0], -self._threat_dir[1]
            target = world.clamp_to_screen((px + fx * 480, py + fy * 480))
            return BehaviorCommand(Behavior.ESCAPE, target=target, intensity=1.0,
                                   priority=100, reason="GF 逃逸触发(旁路仲裁)")
        # 候选 = (优先级, 命令, 学习臂|None):臂非 None 的"自发层"候选参与
        # 多臂老虎机仲裁(操作式学习);需求/反射层(>BANDIT_LAYER_MAX)与
        # 无臂反射(挪窝/CO2 转向/进食/起降)保持硬 WTA,行为学验收不受影响。
        cands: list[tuple[int, BehaviorCommand, str | None]] = []

        # S9 温度 + 联想:冷区态度(本能先验 vs 联想覆写,story 机制)
        attitude_cold = self._cold_attitude()
        zone = self._nearest_zone(world, "cold")
        in_cold = self.temp[3] > 0.45
        if zone is not None:
            if in_cold and attitude_cold < 0.0:
                # 连接表 #16:冷/热厌恶 → 挪窝(优先级 2,仅次于逃逸)
                zx, zy = zone.pos
                d = max(1e-3, dist(px, py, zx, zy))
                away = world.clamp_to_screen((px + (px - zx) / d * 520,
                                              py + (py - zy) / d * 520))
                cands.append((50, BehaviorCommand(
                    Behavior.EXPLORE, target=away, intensity=0.8, priority=50,
                    reason="温度不适,挪窝"), None))
            elif attitude_cold > 0.2:
                # story 覆写 cold 先验 → 趋近冷区(学习与记忆设计 §3)
                cands.append((20, BehaviorCommand(
                    Behavior.EXPLORE, target=world.clamp_to_screen(zone.pos),
                    intensity=0.6, priority=20, reason="联想:冷区值得期待,趋近"),
                    "near_cold"))
        # 连接表 #9:CO2/腐败 → 背向转向(警戒性回避,不到 GF 级)
        if self._co2_now > 0.25:
            fx, fy = -self._co2_dir[0], -self._co2_dir[1]
            cands.append((30, BehaviorCommand(
                Behavior.TURN, target=world.clamp_to_screen((px + fx * 260, py + fy * 260)),
                intensity=clamp(self._co2_now), priority=30, reason="CO2 警戒,背向转向"),
                None))
        # 饥饿/觅食簇(优先级 3):LH 先天 + MBON 习得增益(素材 §3.2)
        attract = self._v_attract
        f = self._nearest_food(world, 900.0)
        if f is not None:
            d = dist(px, py, *f.pos)
            if d < 60 and self.emo[E_HUNGER] > 0.25:
                cands.append((40, BehaviorCommand(Behavior.EAT, priority=40,
                                                  intensity=0.9, reason="到达食物,进食"),
                              None))
            elif self.emo[E_HUNGER] > 0.45:
                # 习得吸引增强执着度;学得的厌恶压低趋近(危险气味别去)
                gain = clamp(0.6 + 0.8 * attract - 0.5 * self._aversion)
                cands.append((40, BehaviorCommand(
                    Behavior.SEEK_FOOD, target=f.pos, intensity=gain, priority=40,
                    reason=f"饥饿觅食(MBON 吸引 {attract:.2f})"), "seek_food"))
        if self._odor_now > 0.12 and self._aversion < 0.25 \
                and not any(c[0] == 40 for c in cands):
            # 先天趋向:不饿也轻微趋源(LH 直通,冷启动即可用);厌恶学习后收回
            tx, ty = px + self._odor_dir[0] * 300, py + self._odor_dir[1] * 300
            cands.append((12, BehaviorCommand(
                Behavior.SEEK_FOOD, target=world.clamp_to_screen((tx, ty)),
                intensity=0.5, priority=12, reason="食物气味趋向(LH 先天)"),
                "seek_food"))
        # 好奇/探索簇:小目标 → 好奇增益 → 低速接近(素材 §3.3)
        if self._small_now > 0.3 and self.emo[E_CURI] > 0.45:
            cur = world.cursor  # 局部取用,避免引用 observe() 的同名局部
            cands.append((18, BehaviorCommand(
                Behavior.EXPLORE, target=world.clamp_to_screen((cur.x, cur.y)),
                intensity=0.5, priority=18, reason="好奇:接近小目标"),
                "near_small_target"))
        # SCREEN_EVENT 好奇候选(B2/ADR-0029,调研 §4.3 果蝇列):新窗/窗口
        # 变化 <600px → curiosity 抬升 + 一次性趋近候选(规则层,不入 bandit)
        evt_tgt = self._window_interest(world)
        if evt_tgt is not None and self.emo[E_FEAR] < 0.3:
            cands.append((18, BehaviorCommand(
                Behavior.EXPLORE, target=world.clamp_to_screen(evt_tgt),
                intensity=0.5, priority=18, reason="新窗出现,好奇接近"), None))
        # ---- 自发行为层(互动性扩展 B01 理毛 / B02 找主人 / B03 昼夜) ----
        # 权重设计:压过默认巡游(10)与弱气味趋向(12),但被觅食(40)、
        # 温度不适(50)、逃逸(100)等一切真实需求抢占 —— 自发行为永远不该
        # 压过生存需求。
        if self.spon.wants_groom:
            cands.append((16, BehaviorCommand(
                Behavior.GROOM, priority=16, intensity=0.6,
                reason="自发理毛(停歇期固定动作模式)"), "groom"))
        elif self.spon.groom_cd <= 0.0:
            self.spon.groom_begin()
        elif self.spon.poll_rest() and self.emo[E_FEAR] < 0.2:
            cands.append((13, BehaviorCommand(
                Behavior.REST, priority=13, intensity=0.5,
                reason="昼夜节律:静息倾向"), "rest"))
        # B02 主动趋近受信任档位门控(调研 §1 分档行为表):怕人档(0)禁用
        # approach_cursor 臂;主动靠近档(3)静止触发阈 15s→8s(更愿意找主人)
        _tier = trust_tier(self._trust)
        if _tier >= 1 and self.spon.owner_ready(TRUST_TIER_OWNER_STILL[_tier]) \
                and self.emo[E_FEAR] < 0.15:
            cur = world.cursor
            cands.append((15, BehaviorCommand(
                Behavior.EXPLORE,
                target=world.clamp_to_screen((cur.x, cur.y)),
                intensity=0.25, priority=15,
                reason="主人安静,主动趋近(非逃逸)"), "approach_cursor"))
        # 默认:CX 巡游(两态 run-and-turn:RUN 直行 / TURN 转向事件)
        self._step_cruise(world)
        if self._cruise_cmd is not None:
            # 停顿(dab)/ 原地转表演(show)子状态:优先级 10,仅压过普通巡游,
            # 可被气味趋向(12)/好奇(18)/起降(11)/逃逸(100)当拍抢占
            cands.append((10, self._cruise_cmd, "explore_wander"))
        else:
            # 探索速度噪声:转向事件期减速(转向-速度负耦合,行为学常识)
            slow = 0.8 if self._mode == "arc" else 1.0
            inten = clamp((1.0 - self.emo[E_FATIGUE] * 0.6)
                          * (1.0 - EXPLORE_SPEED_NOISE * abs(self._ou) / OU_RATE_MAX)
                          * slow)
            cands.append((10, BehaviorCommand(Behavior.EXPLORE,
                                              target=self._explore_target(world),
                                              intensity=inten, priority=10,
                                              reason="CX 巡游" if self._mode == "run"
                                              else "CX 巡游·转向弧"), "explore_wander"))
        # 驻留/休息(优先级 5):疲劳/满足高
        if self.emo[E_FATIGUE] > 0.7 or (self.emo[E_SAT] > 0.75
                                         and self.emo[E_HUNGER] < 0.3):
            cands.append((6, BehaviorCommand(Behavior.REST, priority=6,
                                             intensity=0.7, reason="驻留休整"), "rest"))
        # 飞行种偶发起降(仅压过默认巡游,可被其余一切抢占)
        if self.flyer and self._rng.random() < 0.01:
            mode = getattr(self.state, "mode", None)
            if mode is not None and mode.value == "crawl":
                cands.append((11, BehaviorCommand(Behavior.TAKEOFF, priority=11,
                                                  intensity=0.8, reason="自发起飞"), None))
            elif mode is not None and mode.value == "fly":
                cands.append((11, BehaviorCommand(Behavior.LAND, priority=11,
                                                  intensity=0.8, reason="自发降落"), None))
        # ---- S12 WTA:需求/反射层硬优先级;自发层由多臂老虎机仲裁(连接表 #19) ----
        # 学习臂只重排自发层(prio ≤ 20):fresh 臂同拿 n=1 满额置信加分,
        # 排序由本能 μ 先验决定(与旧纯优先级 WTA 同序,行为学验收零漂移);
        # 强化后 μ 上升/未奖励臂 μ 回落才产生重排 —— "做→好结果→更常做"。
        needs = [c for c in cands if c[0] > BANDIT_LAYER_MAX]
        if needs:
            best = max(needs, key=lambda c: c[0])
            self._record_arm(best[2])
        else:
            best = self._arbitrate_spontaneous(cands)
        self._emit_motor(best[1])
        return best[1]

    def _arbitrate_spontaneous(self, cands: list) -> tuple:
        """自发层仲裁:score = 优先级 + BANDIT_W_GAIN·UCB(arm)。

        分数差距 >BANDIT_NEAR_TIE 直接取最高(近似确定性,保可复现验收);
        近平手才按 softmax 逆温度抽样(情绪调制探索-利用)。"""
        curi, aro = self.emo[E_CURI], self.emo[E_FEAR]
        scored: list[tuple[float, int, BehaviorCommand, str | None]] = []
        for p, cmd, arm in cands:
            if p > BANDIT_LAYER_MAX:
                continue
            u = 1.0 if arm is None else self.bandit.ucb(arm, curi, aro)
            scored.append((p + BANDIT_W_GAIN * u, p, cmd, arm))
        if not scored:
            scored.append((0.0, 0, cands[-1][1], None))
        top = max(s for s, _, _, _ in scored)
        near = [t for t in scored if top - t[0] < BANDIT_NEAR_TIE]
        if len(near) > 1:
            idx = self.bandit.softmax_pick(
                {i: t[0] for i, t in enumerate(near)},
                self.bandit.inv_beta(aro, curi))
            winner = near[idx]
        else:
            winner = near[0]
        self._record_arm(winner[3])
        # 返回口径与 cands 一致的 (prio, cmd, arm) 三元组:decide() 侧
        # best[1] 必须是 BehaviorCommand(曾把 4 元组原样返回,best[1]
        # 取到 int 优先级,_emit_motor 取 .behavior 崩——在线学习 1/7 根因)
        return winner[1], winner[2], winner[3]

    def _record_arm(self, arm: str | None) -> None:
        """决策点记账:臂切换或每 SPONT_TRIAL_S 秒记一次(UCB 的 t/n 口径);
        同帧记录"当前活跃臂"供 fed/grab 做操作式信用分配。"""
        if arm is None:
            return
        if arm != self._active_arm or self._t - self._arm_trial_t >= SPONT_TRIAL_S:
            self.bandit.trial(arm)
            self._arm_trial_t = self._t
        self._active_arm = arm

    # ================= 两态转向状态机(run-and-turn) =================
    def _reset_cruise(self) -> None:
        """转向状态复位(撞墙反弹/航向阶跃后):回 RUN 态、清转向子状态。

        本设计无 _aim_off 类持久偏置(航点是世界固定点,lead 每帧由当前
        几何重算),故弹墙后只需复位子状态;航点保留(目标连续),若已落在
        身后由 TURN_ENTER 滞回触发朝航点的弧线回转(限幅半径 ≥3BL),
        而不是旧版"目标点瞬间转到身后,牵引花 0.5~1s 拉回"的紧密圆弧。
        """
        self._mode = "run"
        self._mode_until = self._t + self._rng.uniform(*RUN_DUR_S)
        self._cruise_cmd = None
        self._arc_random = False
        self._streak_sign, self._streak_n, self._streak_rot = 0, 0, 0.0
        self._run_sign, self._run_n, self._wp_side = 0, 0, 0

    def _renew_waypoint(self, world: WorldView, dir_bias: float | None = None) -> None:
        """前向重选巡游航点(防御①③:距离 ≥3BL、夹角限 ±46°、可完成时长)。

        旧版航点全屏均匀撒点且与航向无关,配合开环 _aim_off 会出现"目标总在
        侧后 → 切圆"的绕圈形态(B 案防御 1c);本版航点 = 当前航向前方
        巡航速度 × U(1.5,3.5)s 处,是一段保证可完成的直行跑道。
        """
        st = self.state
        px, py = st.pos
        spd = st.speed if st.speed > 20.0 else CRUISE_SCALE
        base = st.heading if dir_bias is None else dir_bias
        cand = self._wp
        # 强制侧(_wp_side):RUN 态同号转角超限时置为对侧 → 新航点的**偏移
        # 方向**不再自由抽号,而是固定落在该侧,从而把"同向连续转"打断。
        side = self._wp_side
        self._wp_side = 0
        for _ in range(4):
            d = max(spd * self._rng.uniform(*WP_DIST_T), WP_ARRIVE_MIN)
            if side != 0:
                a = base + side * abs(self._rng.uniform(-WP_ANGLE_MAX,
                                                        WP_ANGLE_MAX))
            else:
                a = base + self._rng.uniform(-WP_ANGLE_MAX, WP_ANGLE_MAX)
            cand = world.clamp_to_screen((px + math.cos(a) * d,
                                          py + math.sin(a) * d))
            dd = dist(px, py, *cand)
            ad = abs(wrap_angle(math.atan2(cand[1] - py, cand[0] - px) - base))
            if dd >= WP_ARRIVE_MIN and ad <= WP_ANGLE_MAX:
                # 区块偏好拒绝采样(ADR-0029,调研 §2.3 果蝇列):w≥1(无区块
                # 语义)不消耗 rng,与旧随机流逐位一致(QF 零扰动)。
                w_reg = self._region_weight(world.region_at(cand))
                if w_reg >= 1.0 or self._rng.random() < w_reg:
                    break
        self._wp = cand
        self._wp_until = self._t + self._rng.uniform(4.0, 9.0)

    def _region_weight(self, region: str | None) -> float:
        """果蝇区块偏好(调研 §2.3 表"果蝇"列;单列不分子态)。
        region=None → 1.0(感知未注入时退回旧均匀撒点,零行为漂移)。"""
        if region is None:
            return 1.0
        if region.startswith("corner"):
            region = "corner"
        return FLY_REGION_W.get(region, 0.5)

    def _window_interest(self, world: WorldView) -> tuple[float, float] | None:
        """SCREEN_EVENT → 好奇候选(调研 §4.3 果蝇列):appear/grow 距 <600px
        → curiosity+0.08 并返回事件中心作为一次性探索目标(30s/hwnd 节流;
        不注册新 bandit 臂 —— 臂表在 instinct.py 冻结,走规则层 arm=None)。"""
        evs = getattr(world, "window_events", None) or ()
        px, py = self.state.pos
        hit: tuple[float, float] | None = None
        for e in evs:
            if getattr(e, "kind", "") not in ("appear", "grow"):
                continue
            hwnd = int(getattr(e, "hwnd", 0) or 0)
            if self._t - self._evt_cur.get(hwnd, -1e9) < 30.0:
                continue
            self._evt_cur[hwnd] = self._t
            c = getattr(e, "center", (0, 0))
            if dist(px, py, c[0], c[1]) < 600.0:
                self.emo[E_CURI] = clamp(self.emo[E_CURI] + 0.08)
                hit = (float(c[0]), float(c[1]))
        if len(self._evt_cur) > 64:
            recent = sorted(self._evt_cur.items(), key=lambda kv: -kv[1])[:32]
            self._evt_cur = dict(recent)
        return hit

    def _wp_consumed(self) -> bool:
        """航点已到达或已被错过(越过且拉开距离)→ 需要前向重选。"""
        if self._wp is None:
            return True
        st = self.state
        d = dist(st.pos[0], st.pos[1], *self._wp)
        if d < WP_ARRIVE_R:
            return True
        if d < EXPLORE_LOOKAHEAD * 0.8:
            to_wp = math.atan2(self._wp[1] - st.pos[1], self._wp[0] - st.pos[0])
            if math.cos(wrap_angle(to_wp - st.heading)) < 0.0:
                return True
        return False

    def _start_arc(self, sign: float, delta: float, omega: float,
                   random_turn: bool) -> None:
        """进入 TURN(弧线)态:以角速度档 omega 画 delta 角的弧。

        omega 档 0.35~0.6rad/s(VC 奔跑弧 19~37°/s)→ 巡航下弧半径 ≥3BL;
        随机换向事件做防锁圆记账(连续同号 >SAME_SIGN_ARCS 或累计
        >SAME_SIGN_ROT_MAX 时强制换号,VC"同号限长强制重抽"配方)。
        """
        if random_turn:
            if self._streak_sign == sign:
                self._streak_n += 1
                self._streak_rot += delta
            else:
                self._streak_sign, self._streak_n, self._streak_rot = sign, 1, delta
            if self._streak_n > SAME_SIGN_ARCS or self._streak_rot > SAME_SIGN_ROT_MAX:
                sign = -sign                     # 强制换向(1/3 换向的确定性形态)
                self._streak_sign, self._streak_n, self._streak_rot = sign, 1, delta
        self._mode = "arc"
        self._arc_random = random_turn
        self._turn_sign = sign
        self._turn_omega = omega
        self._turn_rem = delta
        self._mode_until = self._t + min(delta / omega * 1.5 + 0.5, ARC_TIME_MAX)

    def _to_run(self) -> None:
        """回到 RUN 态(滞回:TURN 进入阈 50° / 航点重选后偏角 ≤46° → 不抖振)。"""
        self._mode = "run"
        self._mode_until = self._t + self._rng.uniform(*RUN_DUR_S)
        self._cruise_cmd = None
        if self._arc_random:
            self._wp = None          # 随机弧转完 → 前向重选跑道(下拍 renew)

    def _step_cruise(self, world: WorldView) -> None:
        """两态转向状态机推进(decide 期调用;dt 用 observe 缓存)。

        RUN 态:直行(航点 P 对准 + OU 微摆),每 2.5~6s 一个决策点:
            1/3 停(dab)/ 1/3 换向(arc)/ 1/3 维持 —— VC 短弧重抽配方;
            航点偏角 >50°(TURN_ENTER)→ 朝航点弧线转向。
        TURN 态:限幅角速度弧线,按实测角速度记账,转完回 RUN(滞回防抖振)。
        dab/show:停顿与原地转小表演,以优先级 10 子命令下发,当拍可被抢占。
        """
        t, dt = self._t, self._dt_last
        st = self.state
        self._cruise_cmd = None
        # ---- 同号限长(时间口径,全局覆盖 run/arc/show)----------------------
        # 与 §7"最长连续同向 <1.2s"同口径(|ω|>0.3rad/s 且同号计"在转"),超
        # RUN_SAME_SIGN_S 即强制拉直 RUN_STRAIGHTEN_S → 同向段长度结构性 ≤
        # 0.85s(+一拍响应),不随 ω 大小漂移(转角口径对慢转无效);同时把下次
        # 航点重选钉在**对侧**,限制同号累计(§7 <270°)。
        w_dt = self._omega_meas * dt
        sgn = 1 if w_dt > 0 else (-1 if w_dt < 0 else 0)
        if abs(self._omega_meas) > THRESH_SAME_SIGN:
            if sgn == self._run_sign:
                self._run_n += 1
            else:
                self._run_sign, self._run_n = sgn, 1
        else:
            self._run_n = 0
        if self._run_n > int(RUN_SAME_SIGN_S / dt) and sgn != 0:
            self._run_sign, self._run_n = 0, 0
            self._wp_side = -sgn
            self._straight_until = t + RUN_STRAIGHTEN_S
            if self._mode != "run":
                self._to_run()          # 弧/表演被同号限长截断 → 立即回 RUN
                self._cruise_cmd = None
        # 航点维护:过期 / 到达 / 错过 → 前向重选
        if self._wp is None or t > self._wp_until or self._wp_consumed():
            self._renew_waypoint(world)
        if self._mode == "dab":
            if t >= self._mode_until:
                self._to_run()
            else:
                self._cruise_cmd = BehaviorCommand(
                    Behavior.REST, target=self._wp, priority=10, intensity=0.6,
                    reason="游走停顿(一冲一停)")
                return
        if self._mode == "show":
            turned = abs(wrap_angle(st.heading - self._turn_start_h))
            if turned >= self._turn_rem or t >= self._mode_until:
                self._to_run()
            else:
                # 原地转小表演(VC cat==0):TURN 原语使身体以 1.5×turn_rate
                # 边减速边原地转(目标方向恒超前 SHOW_LEAD → 匀速原地转)
                aim = st.heading + self._turn_sign * SHOW_LEAD
                tx, ty = world.clamp_to_screen(
                    (st.pos[0] + math.cos(aim) * 220.0,
                     st.pos[1] + math.sin(aim) * 220.0))
                self._cruise_cmd = BehaviorCommand(
                    Behavior.TURN, target=(tx, ty), priority=10, intensity=0.7,
                    reason="原地转向小表演")
                return
        if self._mode == "arc":
            # 按实测角速度记账(对 body 侧转向增益差异不敏感,F4 联调安全)
            self._turn_rem -= abs(self._omega_meas) * dt
            if self._turn_rem <= 0.0 or t >= self._mode_until:
                self._to_run()
            return
        # ---- RUN 态 ----
        if self._wp is None:          # 随机弧转完后前向重选跑道
            self._renew_waypoint(world)
        assert self._wp is not None
        wp_dir = math.atan2(self._wp[1] - st.pos[1], self._wp[0] - st.pos[0])
        err = abs(wrap_angle(wp_dir - st.heading))
        if t >= self._mode_until:
            # 决策点:原地转表演(独立抽签)→ 停 1/3 / 换向 1/3 / 维持 1/3
            # (VC 决策周期重抽配方:D1 §3.2;符号恒对称抽号 = 结构性零偏航)
            if self._rng.random() < SHOW_PROB:
                sign = 1.0 if self._rng.random() < 0.5 else -1.0
                self._mode = "show"
                self._turn_sign = sign
                self._turn_rem = self._rng.uniform(*SHOW_DELTA)
                self._turn_start_h = st.heading
                self._mode_until = t + self._turn_rem / 2.5 + 1.0   # 保险丝
            elif self._rng.random() < DAB_PROB:
                self._mode = "dab"
                self._mode_until = t + self._rng.uniform(*DAB_DUR_S)
            elif self._rng.random() < ARC_PROB:
                sign = 1.0 if self._rng.random() < 0.5 else -1.0
                delta = self._rng.uniform(*ARC_DELTA)
                # ω 由幅值派生(不是独立抽号):单弧时长 ≈ ARC_DUR_S,上不破
                # §7"最长连续同向 <1.2s"
                omega = clamp(delta / ARC_DUR_S, ARC_OMEGA[0], ARC_OMEGA[1])
                self._start_arc(sign, delta, omega, random_turn=True)
            else:
                self._mode_until = t + self._rng.uniform(*RUN_DUR_S)   # 维持直行
        elif EXPLORE_WP_PULL > 0.0 and err > TURN_ENTER:
            # 牵引开关同时门控航点弧(置 0 = 完全忽略航点,供对照测试)
            # 航点偏角过大(弹墙后/被外力拽偏):朝航点弧线转向(滞回进入)
            d_err = wrap_angle(wp_dir - st.heading)
            # 幅值与 ω 同样受 ARC_DELTA/ARC_DUR_S 约束 → 对准弧时长 ≤ ~1.1s
            delta = min(abs(d_err), ARC_DELTA[1] * 1.2)
            omega = clamp(delta / ARC_DUR_S, ARC_OMEGA[0], ARC_OMEGA[1])
            self._start_arc(1.0 if d_err > 0 else -1.0, delta, omega,
                            random_turn=False)

    # ---- 巡游目标:限幅合成角速度的一帧前瞻 lead(替代开环 _aim_off 积分) ----
    def _explore_target(self, world: WorldView) -> tuple[float, float]:
        """CX 巡游目标点 = 当前航向 + 合成角速度·dt 前瞻 + OU 有界微摆。

        转向全部表达为"限幅角速度 → 一帧 lead":run 态 ω = 航点 P 对准
        (比例增益)+ arc 态 ω = 弧线档;body 的 bang-bang 限速或比例控制
        (Agent-1 F4)跟踪该 lead 都得 ω_actual ≈ ω_cmd。角速度上限随实际
        速度自适应(|ω| ≤ v/(R_MIN·BL))→ 弧线半径恒 ≥R_MIN·BL(速度越低
        转得越慢,不再有"低速小半径急转");lead 每帧由当前几何重算,无开环
        积分累积(旧版 _aim_off 每帧再生误差是转圈根因之二)。
        """
        st = self.state
        px, py = st.pos
        h = st.heading
        dt = self._dt_last
        v_ref = max(st.speed, TURN_SPEED_FLOOR * CRUISE_SCALE)
        cap = v_ref / (TURN_RADIUS_MIN_BL * BODY_LEN)
        if self._mode == "arc":
            omega = clamp(self._turn_sign * self._turn_omega, -cap, cap)
        elif self._t < self._straight_until:
            omega = 0.0            # 拉直窗口:对准项置零(仅余 OU 微摆)→ ω≈0
        else:
            wp_dir = math.atan2(self._wp[1] - py, self._wp[0] - px) \
                if self._wp is not None else h
            omega = clamp(wrap_angle(wp_dir - h) * (EXPLORE_WP_PULL * WP_P_SCALE)
                          + clamp(self._ou * MICRO_RATE_GAIN,
                                  -MICRO_RATE_MAX, MICRO_RATE_MAX),
                          -cap, cap)
        # OU 微摆以"限幅角速度"进 lead(±0.86°/s,随 cap 收敛):偏航被航点
        # 对准项均值回拉,10s 直行漂移 <5° 结构保证(防御②零漂移项)
        micro_rate = clamp(self._ou * MICRO_RATE_GAIN,
                           -MICRO_RATE_MAX, MICRO_RATE_MAX)
        aim = h + (omega + micro_rate) * dt
        # 越屏时按比例缩短前导距离(而非裁剪坐标):保证前导点方位角 == aim,
        # 贴墙时也不出现"无指令过转"(见 _aim_point 说明)
        return _aim_point(world, px, py, aim, EXPLORE_LOOKAHEAD)

    def _emit_motor(self, cmd: BehaviorCommand) -> None:
        """S12 8 原语 + 8 增益状态更新(调试/面板可读)。"""
        prim = {Behavior.ESCAPE: 4, Behavior.EXPLORE: 0, Behavior.SEEK_FOOD: 0,
                Behavior.EAT: 0, Behavior.REST: 5, Behavior.GROOM: 6,
                Behavior.TURN: 2, Behavior.TAKEOFF: 4}.get(cmd.behavior, 0)
        for i in range(8):
            self.motor[i] = 1.0 if i == prim else 0.0
        self.motor[8] = clamp(cmd.intensity)
        self.motor[9] = self.emo[E_FEAR]        # 恐惧 → 急停/闪避增益 ↑
        self.motor[10] = self.emo[E_HUNGER]     # 饥饿 → 食物通道增益 ↑
        self.motor[11] = self.emo[E_CURI]
        self.motor[12] = 1.0 - self.emo[E_FATIGUE]
        self.motor[13] = self.vigilance
        self.motor[14] = self.temp[3]
        self.motor[15] = clamp(-self._valence)

    def _cold_attitude(self) -> float:
        """冷区态度 = 出厂先验(本能库)与联想效价的置信加权合成;
        正向联想(story)足够强时直接覆写负先验。"""
        prior = self.instinct.valence_prior(self.SPECIES, "cold")   # ≈ -0.69
        a = self.assoc.get("context:cold")
        if a is None or a.count == 0:
            return prior
        conf = clamp(a.weight * 8.0)
        att = (1.0 - conf) * prior + conf * a.valence
        if a.valence > 0.2:
            att = max(att, a.valence)     # 效价转正 → 覆写回避先验
        return att

    def _nearest_zone(self, world: WorldView, kind: str):
        best, bd = None, 1e18
        for z in world.zones.values():
            if z.kind != kind:
                continue
            d = dist(self.state.pos[0], self.state.pos[1], *z.pos)
            if d < bd:
                best, bd = z, d
        return best

    @staticmethod
    def _nearest_food(world: WorldView, max_r: float):
        """WorldView 快照内找最近食物(WorldView 无 nearest_food 方法)。"""
        px, py = world.pets[world.self_id].pos if world.pets else (0.0, 0.0)
        best, bd = None, max_r
        for f in world.foods.values():
            d = dist(px, py, *f.pos)
            if d < bd:
                best, bd = f, d
        return best

    # ================= 情绪 / 事件 / 学习协议 =================
    def emotion(self) -> EmotionState:
        return EmotionState(
            fear=self.emo[E_FEAR], hunger=self.emo[E_HUNGER],
            curiosity=self.emo[E_CURI], anger=0.0, trust=self._trust,
            valence=self._valence,
            arousal=clamp(max(self.emo[E_FEAR], self._wind_trace * 0.5,
                              self._odor_now * 0.35, self.emo[E_HUNGER] * 0.25)))

    def on_event(self, name: str, data: dict) -> None:
        if name == "grab":
            self._n_danger += 1        # r25 A4:熟练度统计(遇险)
            self.emo[E_FEAR] = min(1.0, self.emo[E_FEAR] + 0.55)
            # grab 惩罚按信任档减负(调研 §1):习惯 −0.09 / 主动靠近 −0.06,
            # 怕人/警惕维持 −0.12 —— 信任高的个体对抓持的应激解读更少
            self._trust = max(0.0, self._trust
                              - TRUST_TIER_GRAB[trust_tier(self._trust)])
            self._dan_punish = max(self._dan_punish, 0.8)
            self._dan_pending_p = max(self._dan_pending_p, 0.8)
            self.assoc.learn("human:grab", -0.5, 0.10)
            # 操作式信用:被抓惩罚"被抓时正在做的事"(如探索着靠近被拎起)
            if self._active_arm is not None:
                self.bandit.update(self._active_arm, -1.0)
            self.episodic.remember("被抓了起来,很害怕", salience=0.9)
        elif name == "released":
            self.episodic.remember("被放开,趁机逃跑!", salience=0.7)
            px, py = self.state.pos
            self._cmd_queue.append(BehaviorCommand(
                Behavior.ESCAPE,
                target=(px + self._rng.uniform(-500, 500), py + self._rng.uniform(-500, 500)),
                intensity=1.0, priority=95, reason="脱手逃逸"))
        elif name == "fed":
            self._n_fed += 1           # r25 A4:熟练度统计(投喂)
            self.emo[E_HUNGER] = max(0.0, self.emo[E_HUNGER] - 0.3)
            self.emo[E_SAT] = min(1.0, self.emo[E_SAT] + 0.35)
            # +0.06 基础 + 定时投喂 streak 加成(A2,调研 §1:20~90s 间隔承认
            # "定时节奏",+0.02×min(n,5);同餐快喂/节奏中断不加成)
            self._trust = min(1.0, self._trust + 0.06
                              + self._trust_dyn.feed(self._t))
            self._valence = clamp(self._valence + 0.25, -1.0, 1.0)
            # DAN 奖励信号:饥饿门控下才有效写入吸引记忆(Krashes 2009);
            # v2 改为"事件 pending",由下一拍 _step_mb 以 δ=R−γV 单步落实
            r_now = 1.0 if self.emo[E_HUNGER] > 0.2 else 0.3
            self._dan_reward = max(self._dan_reward, r_now)
            self._dan_pending_r = max(self._dan_pending_r, r_now)
            # 操作式信用分配:fed 奖励"被投喂时正在做的事",并把联想效价
            # 播种进臂先验(联想→操作式打通,修复 R2 死路 1)
            if self._active_arm is not None:
                self.bandit.update(self._active_arm, 1.0)
                ctx = BANDIT_ARM_CTX.get(self._active_arm)
                if ctx:
                    self.bandit.seed_assoc(self._active_arm, self.assoc.valence(ctx))
            self.assoc.learn("human:feed", 0.7, 0.20)
            self.episodic.remember("吃到了主人投喂的食物,很满足", salience=0.6)
        elif name == "hungry":
            self.emo[E_HUNGER] = min(1.0, self.emo[E_HUNGER] + 0.1)
        elif name == "freeze_on":
            self.episodic.remember("被冻结了(魔法?)", salience=0.3)
        elif name == "external_stimulus":
            try:
                k = StimulusKind(data.get("kind"))
            except ValueError:
                return
            pos = tuple(data.get("source_pos", self.state.pos))
            self._injected.append(Stimulus(k, source="system", pos=pos,
                                           intensity=float(data.get("intensity", 0.8))))
        elif name == "story":
            # 先验注入:直接对情境联想写正/负效价(意识层覆写本能)
            about = str(data.get("about", ""))
            valence = float(data.get("valence", 0.0))
            self.assoc.learn(f"context:{about}", valence, 0.25)
            self.episodic.remember(f"听到了一个故事:{data.get('text', '')}", salience=0.5)

    def inject_command(self, cmd: BehaviorCommand) -> None:
        self._cmd_queue.append(cmd)

    def set_intelligence(self, level: int) -> None:
        self.level = max(1, min(5, int(level)))
        self.episodic.set_level(self.level)
        self.assoc.set_level(self.level)
        self.bandit.set_level(self.level)   # 智能越高:探索越少、利用越果断

    def learning_summary(self) -> str:
        """面板"学习"行:各学习器状态 + 最近学会的偏好(调研 §4.8 可读数值)。"""
        arm, mu, n = self.bandit.best_arm()
        cons = sum(1 for d in self._d_mbon if d > DECAY_STD + 1e-9)
        txt = (f"信任:{trust_tier_zh(self._trust)}档 {self._trust:.2f} | "
               f"吸引{self._v_attract:.2f} 厌恶{self._aversion:.2f} | "
               f"偏好 {BANDIT_ARM_ZH.get(arm, arm)} μ={mu:.2f}(n={n})")
        if cons:
            txt += f" | 已巩固 {cons} 通道"
        return txt

    def memory_digest(self, limit: int = 50) -> list[str]:
        lines = [f"[GF] 警觉度 {self.vigilance:.2f} 触发阈 {self.gf_threshold():.2f} "
                 f"KC 激活 {self._kc_active}/{KC_N}"]
        lines += self.assoc.digest(limit)
        lines += self.episodic.digest(limit)
        return lines

    # ================= 持久化 =================
    def _restore_pet_state(self, data: dict) -> None:
        """回灌与等级/寿命相关的持久量(r25 A4 黑盒缺陷 A 的修复)。

        两件事,顺序关键:

        1. **等级只上调**(与 ``app._mastery_beat`` 的 ratchet 同规):存档里的
           ``level`` 高于当前(重启后 = ``cfg.intelligence`` 基线)才抬高。必须
           在 ``episodic/assoc.load`` **之前**做 —— 它们内部的 ``trim()`` 按当前
           容量删条目,基线等级偏低时会把存档里的记忆直接删掉(自动路径的破坏性
           删除)。老档案同样有 ``level`` 键,行为向后兼容;无键 → 不动。
        2. **年龄**:``age_s`` 是熟练度派生项,不落盘会让重启丢档(见 mastery.py)。
           非有限值/负值/缺键一律忽略(脏档不崩,保持构造值 0.0)。
        """
        try:
            lv = int(data.get("level", 0) or 0)
        except (TypeError, ValueError):
            lv = 0
        if 1 <= lv <= 5 and lv > self.level:
            self.set_intelligence(lv)
        if self.state is not None:
            try:
                age = float(data.get("age_s", 0.0) or 0.0)
            except (TypeError, ValueError):
                age = 0.0
            if math.isfinite(age) and age > 0.0:
                self.state.age_s = age

    def save(self) -> dict:
        kc_mbon = [[j, int(row[0]), row[1], row[2]]
                   for j in range(8) for row in self._kc_mbon[j]]
        return {
            "brain": self.brain_id, "version": 2, "t": self._t, "level": self.level,
            "emo": self.emo[:], "trust": self._trust, "valence": self._valence,
            "trust_dyn": self._trust_dyn.to_dict(),
            "episodes": self.episodic.save(), "assoc": self.assoc.save(),
            "kc_mbon": kc_mbon,
            "n_pair": self._n_pair[:], "d_mbon": self._d_mbon[:],
            "arms": self.bandit.to_dict(),
            # ---- r25 A4:熟练度统计(老档案无此键 → load 缺省 0) ----
            "n_fed": self._n_fed, "n_danger": self._n_danger,
            # r25 A4-fix:宠物年龄随脑存档落盘(黑盒缺陷 A:age_s 不落盘 →
            # 重启归零 → 等级必降 → trim() 永久删记忆)。老档案无此键 → 0.0。
            "age_s": float(self.state.age_s) if self.state is not None else 0.0,
        }

    def load(self, data: dict) -> None:
        if not isinstance(data, dict) or data.get("brain") not in (None, self.brain_id):
            return
        self._restore_pet_state(data)      # r25 A4-fix:等级下限(只上调)+ 年龄
        emo = data.get("emo")
        if isinstance(emo, list) and len(emo) == 8:
            self.emo = [clamp(float(v)) for v in emo]
        self._trust = clamp(float(data.get("trust", self._trust)))
        self._trust_dyn.load(data.get("trust_dyn"))   # 老档案缺省回退(A2)
        self._valence = clamp(float(data.get("valence", self._valence)), -1.0, 1.0)
        self._t = float(data.get("t", self._t))
        self.episodic.load(data.get("episodes", []))
        self.assoc.load(data.get("assoc", {}))
        # KC→MBON 权重:v2 行 [j,kc,w,w_ext];v1 行 [j,kc,w] 读入 w_ext=0
        # (向后兼容老档案——升级不弃档,"记忆不可丢"承诺)
        pos: dict[tuple[int, int], tuple[float, float]] = {}
        for r in data.get("kc_mbon", []):
            if isinstance(r, (list, tuple)) and len(r) in (3, 4):
                try:
                    pos[(int(r[0]), int(r[1]))] = (
                        float(r[2]), float(r[3]) if len(r) == 4 else 0.0)
                except (TypeError, ValueError):
                    continue
        for j in range(8):
            for row in self._kc_mbon[j]:
                w = pos.get((j, int(row[0])))
                if w is not None:
                    row[1] = clamp(w[0], 0.0, 1.0)
                    row[2] = clamp(w[1], 0.0, 1.0)
        n_pair = data.get("n_pair")
        if isinstance(n_pair, list) and len(n_pair) == 8:
            self._n_pair = [max(0, int(v)) for v in n_pair]
            self._d_mbon = [DECAY_LTM if n >= CONSOLIDATE_N else DECAY_STD
                            for n in self._n_pair]
        self.bandit.load(data.get("arms"))
        self.bandit.set_level(self.level)
        # r25 A4:熟练度统计回灌(老档案无键 → 0,向后兼容)
        try:
            self._n_fed = max(0, int(data.get("n_fed", 0)))
            self._n_danger = max(0, int(data.get("n_danger", 0)))
        except (TypeError, ValueError):
            self._n_fed = self._n_danger = 0

    def clear_memory(self) -> None:
        self.episodic.items.clear()
        self.assoc.items.clear()
        for rows in self._kc_mbon:      # KC→MBON 权重回出厂
            for row in rows:
                row[1] = 0.10
                row[2] = 0.0            # 对立消退记忆一并归零(ADR-006 全清)
        self._n_pair = [0] * 8
        self._d_mbon = [DECAY_STD] * 8
        self._elig.clear()
        self._cs_only_acc = 0.0
        self._dan_pending_r = 0.0
        self._dan_pending_p = 0.0
        self._v_attract = 0.0
        self._v_averse = 0.0
        self._aversion = 0.0
        self.bandit.reset()
        self._active_arm = None
        self.emo = [0.0, 0.35, 0.5, 0.15, 0.0, 0.0, 0.0, 0.0]
        self._trust = 0.2
        self._trust_dyn.reset()
        # r25 A4:经历计数随"忘记一切"一并归零(与 trust/emo 同等对待)
        self._n_fed = 0
        self._n_danger = 0
        self._valence = 0.0
