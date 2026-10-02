"""蟑螂大脑 RoachBrain(IBrain + brain 插件)。

蟑螂(美洲大蠊)连接组未开源,采用"功能仿生"路线:
- 小型前馈网络(纯 Python):16 维感知特征 → 32 tanh 隐层 → 3 维行为效价
  (趋近/回避/游走),固定种子初始化;fed/危险事件做奖励调制的 delta 学习;
- 规则仲裁(优先级降序):
  1. 尾须震动反射:单拍响应,逃逸方向 = 反风向 ± 抖动(强震动 ±35° 可靠背风,
     弱震动 ±(90~180°) 大角度变向,符合行为学的高方向变异性);
  2. 接触/逼近阴影 → 逃逸;
  3. 负趋光(预留接口 set_phototaxis);
  4. 触角嗅觉梯度趋食 + 饥饿驱动;
  5. thigmotaxis 贴墙转向(近墙沿边行走 / 远墙弱吸引,可关);
  6. 默认游走/驻留(两态 run-and-turn:直行段 + 限幅弧线转向事件,OU 仅
     ±1.7° 航向微摆;航点 = 前向跑道,防"目标在侧后切圆")。
- 同样实现情绪(E1 七维)/ 联想/情景记忆 / 本能库 / story 覆写 cold 先验协议。
- 探索转向参数包源自 deskbug 参考实现(brain.py:508-511 OU / :875-888 贴墙)
  与 VC 短弧重抽配方(D1 §3.2),参数出处见下方常量注释。

在线自学习 v2(调研《神经自学习_在线升级调研.md》三件套 · 路线 C/B):
- 线性 TD(λ) 价值 critic:V(s)=θ·φ(s),φ 即 16 维特征;replacing traces,
  事件级 δ = R + γV(s′) − V(s) 驱动 θ 更新;V 调制探索强度("预期好→更起劲");
- _nn_learn 升级为三因子(η·err·隐层资格迹·|δ|):预期内奖励写入自动衰减;
- 自发行为层(理毛/找主人/静息/游走)由多臂老虎机仲裁(操作式学习);
- 持久化新增 "critic"/"arms" 键(老档案缺省回退,向后兼容)。
"""
from __future__ import annotations

import math
import random

from neuropet.brain.spontaneous import (Spontaneous, TRUST_TIER_ESCAPE_BIAS,
                                        TRUST_TIER_GRAB, TRUST_TIER_OWNER_STILL,
                                        TrustDynamics, circadian_rest_bias,
                                        trust_tier, trust_tier_zh)
from neuropet.core.contracts import (Behavior, BehaviorCommand, EmotionState,
                                     Stimulus, StimulusKind)
from neuropet.core.interfaces import IBrain
from neuropet.core.mathutil import clamp, dist, wrap_angle
from neuropet.core.plugin import PluginBase
from neuropet.core.world import WorldView

from neuropet.brain.instinct import (BANDIT_ARM_CTX, BANDIT_ARM_ZH,
                                     BanditArbiter, InstinctLibrary)
from neuropet.brain.memory import AssociationMemory, EpisodicMemory
from neuropet.brain.plasticity import VectorTrace, rpe

N_IN, N_HID, N_OUT = 16, 32, 3   # 输入特征 / tanh 隐层 / 行为效价(approach, avoid, wander)

# 输入特征下标
(F_WIND, F_WSIN, F_WCOS, F_VIB, F_CONTACT, F_SHADOW, F_ODOR, F_OSIN, F_OCOS,
 F_OGRAD, F_COLD, F_LIGHT, F_HUNGER, F_FEAR, F_CURI, F_AROUSAL) = range(16)

# ---- 在线自学习参数(路线 C 线性 TD(λ) critic + 路线 B 老虎机;调研 §4.3) ----
# γ=0.98 → 有效视界 ~50 步 ≈ 蟑螂一次逃逸冲刺的帧数;λ=0.8 → 信用窗 ~5s,
# 与 trace conditioning 的 CS-US 窗口同量级;η_λ=0.02 → 50~150 个奖励帧
# 可见 V 漂移(线性 TD 线性收敛)。均为工程值,推导见调研文档 §3.3。
GAMMA = 0.98                 # 折扣因子
CRIT_GAMMA_LAMBDA = 0.784    # γλ = 0.98 × 0.8(replacing traces 衰减系数)
CRIT_ELIG_TAU = 4.0          # 隐层资格迹时间常数(s)
CRIT_ETA = 0.02              # critic 学习率(小 η 防 V 发散,V 另行 clamp ±1)
BANDIT_W_GAIN = 6.0          # 臂 UCB 分值 → WTA 分数换算增益
BANDIT_NEAR_TIE = 1.5        # 分差小于该值才做 softmax 抽样(保可复现验收)
SPONT_TRIAL_S = 4.0          # 同臂持续选中时的决策点记账间隔(s)

THIGMO_ON_DEFAULT = True   # thigmotaxis(贴边偏好)默认开启,可经 set_thigmotaxis 关闭

# ---- 探索转向参数包(两态 run-and-turn + OU 纹理;参数出处标注) ----
# OU 过程:d(ou)/dt = -ou/τ + σ·N(0,1)(即 dθ = -κ·θ·dt + σ·√dt·N,κ=1/τ),
# 原实现把该角速度状态开环积分进航向(deskbug brain.py:508-511 同构),与果蝇
# 同病(转圈根因链)。本版 OU 降级为"航向微摆纹理源":以 MICRO_RATE_* 限幅
# 角速度(±0.86°/s)形式驱动航向微摆,不再做开环速率积分。σ 按 F4 冻结点"OU 退烧"从 2.2 → 1.0:
# 稳态 std = σ√(τ/2) ≈ 0.89 rad/s(旧稳态 1.97rad/s @ 巡航 300px/s → 半径
# 2.6BL,是蟑螂版的"小圈漂移"源)。出处:deskbug species.py:94(τ)/
# Maye 2007(转向角时间相关性的 OU 统计)。
OU_TAU = 1.6                 # OU 相关时间 τ(s),κ=1/τ≈0.63/s → 长程相关(deskbug species.py:94)
OU_SIGMA = 1.0               # OU 驱动噪声强度(F4 退烧:2.2 → 1.0;deskbug species.py:95 同源)
OU_RATE_MAX = 1.2            # OU 状态限幅 ±1.2 rad/s(F4:3.0 → 1.2;deskbug brain.py:510 同源)
EXPLORE_WP_PULL = 3.0        # 航点 P 牵引基础增益(/s):ω_wp = err·PULL·WP_P_SCALE 再限幅;
                             # 置 0 时脑忽略航点(纯直航惯性),供牵引开/关对照测试
EXPLORE_LOOKAHEAD = 220.0    # 探索目标点前视距离(px,≈2 倍体长,供步态/转向观赏)

# ---- 两态转向(run-and-turn):RUN 直行 / TURN 转向事件 + 三重防御 ----
# 与果蝇同构(VC 短弧重抽配方 D1 §3.2);蟑螂巡航快(300px/s),角速度限幅
# 按"最小弧线半径 ≥3BL"推导:ω ≤ 300/(3×115) = 0.87 → 取 0.85rad/s。
RUN_DUR_S = (2.5, 6.0)       # RUN 直行段时长(s;Maye 2007 直线段量级)
ARC_OMEGA = (0.35, 0.60)     # 弧线转向角速度档(rad/s ≈ 20~34°/s;VC 奔跑弧 19~37°/s)
ARC_DELTA = (0.698, 2.094)   # 单次转向幅值 40°~120°(rad)
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
TURN_RADIUS_MIN_BL = 4.0     # 最小弧线半径(体长):转向角速度限幅 = v/(R_MIN·BL),
                             # 随实际速度自适应(转弯减速时角速度同步下调),
                             # 结构性保证任意速度下弧线半径 ≥4BL(任务验收 ≥3BL;
                             # 旧 OU 稳态 1.97rad/s @ 巡航 300px/s → 半径 2.6BL)
TURN_SPEED_FLOOR = 0.35      # 速度参考下限(×巡航):低速时按此参考限幅,防低速急转
BODY_LEN = 115.0             # 体长(px,species PARAMS.body_len 同值,半径换算用)
WP_P_SCALE = 2.0 / 3.0       # 航点 P 增益系数:ω = err·EXPLORE_WP_PULL·WP_P_SCALE
MICRO_RATE_GAIN = 0.009      # OU → 航向微摆角速度增益(防御②零漂移项;与果蝇同构,
                             # 必须以"角速度"形式进 lead——以"偏角"形式会被 bang-bang
                             # body 当整帧增量重复执行,等效隐藏角速度,见 fly 注)
MICRO_RATE_MAX = 0.015       # 微摆角速度限幅(rad/s,≈0.86°/s,均值回拉下 10s 漂移 <5°)
WP_DIST_T = (1.5, 3.5)       # 航点距离 = 巡航速度 × U(1.5,3.5)s(保证一段可完成的直行)
WP_ANGLE_MAX = 0.803         # 新航点与当前航向夹角限 ±46°(<TURN_ENTER 50°:
                             #  重选后不立即触发航点弧,RUN 直行段得保;B 案防御1c ±120°内)
WP_ARRIVE_MIN = 345.0        # 航点距离下限(px = 3BL,防"近距目标切圆")
WP_ARRIVE_R = 90.0          # 到达半径(px):进入即前向重选(真到点,验收阈值 100px)
CRUISE_SCALE = 300.0         # 巡航速度尺度(px/s,species PARAMS.cruise 同值,航点距离用)
HEADING_STEP_RESET = 1.2     # 单帧航向阶跃 >1.2rad(撞墙反弹)→ 复位转向状态
                             # (A 案"航向阶跃检测复位";本设计无持久偏置可残留)
FAR_ATTRACT_MAX = 0.60       # 远墙弱吸引角速度限幅(rad/s);再经速度自适应 cap 收敛
                             # (带内切向跟随不限幅:物理约束,须 ≥v/d 防撞墙,见下)
WALL_FOLLOW_S = (6.0, 12.0)  # 连续贴墙跟随时长上限(s):超时主动离墙穿越开阔区。
                             # 行为学:蜚蠊沿墙行走一段后会离墙穿越开阔区再接近
                             # 其他壁面(非永久贴墙);deskbug wall_margin 亦只做
                             # 局部吸引。防止"永劫贴墙环屏"与航点跑道自锁
WALL_EXIT_MIN_S = 3.0        # 离墙穿越的最短持续时间(s):期间抑制贴墙切向项,
                             # 让航点对准/离墙弧以限幅角速度完成转向(半径 ≥3BL)

WALL_TRIGGER_DIST = 260.0    # 贴墙带触发距离(px)。出处 deskbug species.py:105
                             # wall_margin=120;因本项目蟑螂巡航 300px/s 远高于
                             # deskbug 尺度,按"贴墙带内可完成 ≥90° 转向"上调,
                             # 否则贴墙转向来不及生效即撞墙反弹(行为学:蟑螂
                             # 接近壁面时提前减速转向,Levi 2000 escape 前减速)
WALL_ATTRACT_GAIN = 0.6      # 远墙弱吸引增益(/s·rad):deskbug brain.py:879-880 为
                             # 0.25×0.8=0.2,按同比例体系上调至 0.6(仍为弱引导,
                             # 行为学:蜚蠊趋缝性);合成时再经 FAR_ATTRACT_MAX 限幅保半径
WALL_TANGENT_GAIN = 4.0      # 近墙沿边切向增益(/s·rad)。出处 deskbug brain.py:883-888
                             # 原始增益 2.0(×0.8=1.6);因须压过航点牵引(3.0)——壁面是
                             # 物理约束,贴近时优先于目标——且贴墙跟随的几何判据为
                             # 转向速率 ≥ v/d(300px/s@100px → ≥3rad/s),上调至 4.0;
                             # 带内切向"不限角速度幅值"(否则回避不及撞墙),不计入
                             # 巡航转弯半径统计(等同物理回避,类比逃逸转身)
WALL_SPEED_FACTOR = 0.7      # 贴墙带内探索速度系数(行为学常识:蜚蠊贴边慢行探缝)
WALL_HOLD_DIST = 120.0       # 贴墙跟随保持距离(px,≈1 倍体长;行为学:蜚蠊贴边行走时
                             # 触角/尾须保持与壁面接触)
WALL_HUG_TILT = 0.45         # 带内切向目标朝壁面的内倾角上限(rad):按 (带缘-保持距)
                             # 线性收敛,在保持距离处归于纯切向 → 平滑贴边不撞墙
WALL_PULL_DAMP = 0.1         # 贴墙带内航点牵引衰减系数:壁面是物理约束,贴近时
                             # 航点若在墙后,牵引不得压过贴墙转向(deskbug 无此项,
                             # 其航点机制不与墙冲突;此处为防"目标在墙后撞墙"补充;
                             # 实测 0.1 时贴墙段零硬反弹)
EXPLORE_SPEED_NOISE = 0.25   # 探索速度噪声:急转减速的调制深度(行为学常识:转向-速度负耦合)

# ---- 躲藏状态机(B2/ADR-0029;调研《互动体验_情绪信任躲藏调研.md》§3.3) ----


EVT_ALERT_NEAR_PX = 600.0     # 新窗事件 <600px → 警觉 fear+0.1(§4.3 刺激映射)


EVT_ALERT_COOLDOWN_S = 30.0   # 窗口事件警觉节流(ADR-0034 改名)   # 同 hwnd 事件 30s 不重复计(§4.3 节流)


# 区块偏好矩阵 w(region) → 0..1(调研 §2.3;仅作用于航点重选的拒绝采样,
# 不与 thigmotaxis 贴墙力冲突)。列:白天 / 夜间 / fear>0.5。
REGION_W_ROACH = {
    "taskbar": (0.95, 0.50, 0.95),
    "corner":  (0.80, 0.55, 0.90),
    "edge":    (0.60, 0.50, 0.75),
    "in_fg":   (0.30, 0.50, 0.10),   # 前台窗内:白天低偏好、fear 时强回避(敏感)
    "center":  (0.25, 0.60, 0.08),   # 开放场范式:fear → 中央权重骤降
}


def _clampw(v: float) -> float:
    return -1.5 if v < -1.5 else 1.5 if v > 1.5 else v


class RoachBrain(IBrain, PluginBase):
    """美洲大蠊大脑:尾须反射优先的小网络 + 规则仲裁。"""

    brain_id = "roach_nn"
    SPECIES = "roach"
    manifest = {"id": "brain.roach_nn", "name": "蟑螂神经网络大脑",
                "version": "0.1", "type": "brain", "api": 1,
                "description": "16-32-3 前馈网 + 尾须反射/负趋光/嗅觉梯度/贴边偏好规则仲裁"}

    def __init__(self, state=None, app=None, **kw) -> None:
        super().__init__(app=app, **kw)
        self.state = state
        self.flyer = False
        self.level = 3
        rng = random.Random(20195172)  # 固定种子:跨机器可复现
        # 16-32-3 前馈权重(隐层 tanh,输出 tanh)
        self.w_ih = [[rng.uniform(-0.4, 0.4) for _ in range(N_IN)] for _ in range(N_HID)]
        self.w_ho = [[rng.uniform(-0.4, 0.4) for _ in range(N_HID)] for _ in range(N_OUT)]
        self.b_h = [0.0] * N_HID
        self.b_o = [0.0] * N_OUT
        self._hid = [0.0] * N_HID
        self._out = [0.0] * N_OUT
        # 感知痕迹
        self._wind_trace = 0.0
        self._wind_dir = (1.0, 0.0)
        self._odor_prev: dict[str, float] = {}
        self._odor_grad = 0.0
        self._odor_dir = (1.0, 0.0)
        self._odor_now = 0.0
        self._threat_dir = (1.0, 0.0)
        # 情绪(七维语义与 EmotionState 对齐)
        self._fear = 0.0
        self._hunger = 0.35
        self._curiosity = 0.45
        self._trust = 0.2
        self._trust_dyn = TrustDynamics()   # 信任事件记账(A2,调研 §1)
        # r25 A4:熟练度统计(经历计数;随 save()/load() 持久化)
        self._n_fed = 0        # 被投喂次数(on_event "fed")
        self._n_danger = 0     # 遇险次数(on_event "grab")
        self._valence = 0.0
        self._arousal = 0.2
        self._satisfaction = 0.1
        # 配置接口
        self.phototaxis_negative = True   # 负趋光开关(预留接口)
        self.thigmotaxis = THIGMO_ON_DEFAULT
        # 记忆/本能
        self.episodic = EpisodicMemory(self.level)
        self.assoc = AssociationMemory(self.level)
        self.instinct = InstinctLibrary.default()
        # 运行时
        self._t = 0.0
        self._rng = random.Random(11)
        # 自发行为层(互动性 B01/B02/B03)。美洲大蠊为夜行动物 → nocturnal=True。
        self.spon = Spontaneous(nocturnal=True)
        self._wp: tuple[float, float] | None = None
        self._wp_until = 0.0
        # OU 微摆纹理状态(rad/s;经 MICRO_RATE_* 限幅后并入转向角速度,不再开环积分)
        self._ou = 0.0
        self._dt_last = 1.0 / 60.0
        self._in_wall_band = False     # 最近一拍是否处于贴墙带(供探索减速/禁止随机弧)
        # ---- 两态转向状态机(run-and-turn,与果蝇同构) ----
        self._mode = "run"            # run 直行 / arc 弧线转向 / dab 停顿 / show 原地转表演
        self._mode_until = self._rng.uniform(*RUN_DUR_S)
        self._cruise_cmd: BehaviorCommand | None = None
        self._turn_sign = 1.0         # 当前转向事件符号
        self._turn_omega = 0.0        # 当前弧线角速度档(rad/s)
        self._turn_rem = 0.0          # 剩余转角(rad;show 期 = 目标转角)
        self._turn_start_h = 0.0      # show 起始航向
        self._arc_random = False
        self._streak_sign = 0         # 防锁圆记账(连续同号强制换向)
        self._streak_n = 0
        self._streak_rot = 0.0
        self._prev_heading = self.state.heading if self.state else 0.0  # 实测角速度/航向阶跃检测用
        self._omega_meas = 0.0
        # 贴墙跟随计时与离墙穿越(防"永劫贴墙环屏")
        self._wall_since: float | None = None   # 本段连续贴墙起始时刻
        self._wall_follow_dur = 0.0             # 本段允许的贴墙时长(s)
        self._wall_exit_until = 0.0             # 离墙穿越截止时刻(期间抑制切向项)
        self._wall_hold = False                 # 最近一拍贴墙切向项是否在施力(统计口径用)
        self._cmd_queue: list[BehaviorCommand] = []
        self._injected: list[Stimulus] = []
        self._last_stimuli: list[Stimulus] = []
        self.features = [0.0] * N_IN      # 最近一拍特征向量(调试可读)
        # ---- 在线自学习(TD(λ) critic + 隐层资格迹 + 老虎机;调研 §4.2) ----
        self._theta = [0.0] * N_IN        # 线性价值权重 V(s) = θ·φ(s)
        self._critic_e = VectorTrace(N_IN, CRIT_GAMMA_LAMBDA)  # replacing traces
        self._nn_elig = VectorTrace(N_HID, 1.0)                # 隐层资格迹(泄漏最大值)
        self._v_prev = 0.0                # 上帧 V(s),供 δ = R + γV(s′) − V(s)
        self._last_delta = 0.0            # 最近事件 δ(遥测 + _nn_learn 写入调制)
        self.bandit = BanditArbiter(level=self.level)
        self.bandit.seed_from_instinct(self.instinct, self.SPECIES)
        self._active_arm: str | None = None   # 最近选中的学习臂(操作式信用分配)
        self._arm_trial_t = -1e9
        # ---- 躲藏状态机(B2/ADR-0029;调研 §3.3;状态不持久化,重启归 NORMAL) ----
        self._evt_seen: dict[int, float] = {}   # hwnd -> 上次处理时刻(30s 节流)
        self.day_phase: bool | None = None      # 昼夜覆写(None=按本地时钟自判)

    # ================= 感知 =================
    def observe(self, world: WorldView, stimuli: list[Stimulus], dt: float) -> None:
        self._t += dt
        for s in self._injected:
            stimuli.append(s)
        self._injected.clear()
        self._last_stimuli = stimuli
        dt = max(1e-4, dt)
        self._dt_last = dt
        # ---- 自发行为计时(互动性 B01 理毛 / B02 找主人 / B03 昼夜节律) ----
        self.spon.update(dt, max((s.intensity for s in stimuli), default=0.0),
                         getattr(world, "cursor", None))
        # ---- 探索转向 OU 纹理(deskbug brain.py:508-511 同构;Maye 2007) ----
        # 退烧后(σ=1.0)稳态 std≈0.89rad/s,仅作 RUN 态 ±1.7° 航向微摆的
        # 驱动源;不再像旧版那样把 _ou 开环积分进航向偏移(转圈根因之一)。
        self._ou = clamp(self._ou + (-self._ou / OU_TAU) * dt
                         + OU_SIGMA * math.sqrt(dt) * self._rng.gauss(0.0, 1.0),
                         -OU_RATE_MAX, OU_RATE_MAX)
        px, py = self.state.pos
        # 实测角速度 + 航向阶跃检测(A 案复位项):撞墙反弹等单帧大转角 →
        # 复位两态状态与航点,杜绝持久偏置在航向突变后残留
        omega = wrap_angle(self.state.heading - self._prev_heading) / dt
        self._prev_heading = self.state.heading
        self._omega_meas = omega
        if abs(omega) > HEADING_STEP_RESET:
            self._reset_cruise()

        wind = vib = contact = shadow = cold = light = 0.0
        odor = 0.0
        for s in stimuli:
            k = s.kind
            if k == StimulusKind.WIND:
                if s.intensity >= wind:
                    wind = s.intensity
                    self._wind_dir = self._to_source(s, px, py)
            elif k == StimulusKind.VIBRATION:
                vib = max(vib, s.intensity)      # 尾须:震动即威胁
            elif k == StimulusKind.CONTACT:
                contact = max(contact, s.intensity)
            elif k == StimulusKind.SHADOW:
                if s.intensity >= shadow:
                    shadow = s.intensity
                    self._threat_dir = self._to_source(s, px, py)
            elif k == StimulusKind.ODOR_FOOD:
                if s.intensity >= odor:
                    grad = s.intensity - self._odor_prev.get(s.source, s.intensity)
                    odor = s.intensity
                    self._odor_grad = clamp(self._odor_grad * 0.9 + grad * 5.0)
                    self._odor_dir = self._to_source(s, px, py)
            elif k == StimulusKind.COLD:
                cold = max(cold, s.intensity)
            elif k == StimulusKind.LIGHT:
                light = max(light, s.intensity)
        for s in stimuli:
            if s.kind == StimulusKind.ODOR_FOOD:
                self._odor_prev[s.source] = s.intensity
        self._odor_now = odor
        # 风痕迹(快起慢衰)
        self._wind_trace = self._wind_trace + \
            (wind - self._wind_trace) * (min(1.0, dt / 0.03) if wind > self._wind_trace
                                         else 1.0 - math.exp(-dt / 0.2))

        # ---- 特征向量 → 前馈网络 ----
        x = self.features
        x[F_WIND], x[F_WSIN], x[F_WCOS] = wind, self._wind_dir[1], self._wind_dir[0]
        x[F_VIB], x[F_CONTACT], x[F_SHADOW] = vib, contact, shadow
        x[F_ODOR], x[F_OSIN], x[F_OCOS] = odor, self._odor_dir[1], self._odor_dir[0]
        x[F_OGRAD], x[F_COLD], x[F_LIGHT] = self._odor_grad, cold, light
        x[F_HUNGER], x[F_FEAR], x[F_CURI] = self._hunger, self._fear, self._curiosity
        x[F_AROUSAL] = self._arousal
        self._hid = [math.tanh(sum(xi * wi for xi, wi in zip(x, row)) + bh)
                     for row, bh in zip(self.w_ih, self.b_h)]
        self._out = [math.tanh(sum(hi * wi for hi, wi in zip(self._hid, row)) + bo)
                     for row, bo in zip(self.w_ho, self.b_o)]

        # ---- 在线学习痕迹(每帧 O(16+32) 次乘加;事件级更新在 on_event) ----
        # φ 已在 features:replacing trace 供 TD(λ);隐层泄漏最大值迹供 _nn_learn
        self._critic_e.replacing_step(x)
        self._nn_elig.leakymax_step(self._hid, math.exp(-dt / CRIT_ELIG_TAU))
        self._v_prev = clamp(sum(t * xi for t, xi in zip(self._theta, x)),
                             -1.0, 1.0)

        # ---- 情绪动力学(指数衰减 + 瞬时抬升) ----
        danger = max(vib, contact, shadow * 0.9, wind * 0.6)
        self._fear = max(self._fear * math.exp(-dt / 4.0), clamp(danger))
        self._hunger = clamp(self._hunger + 0.004 * dt)
        self._curiosity = clamp(self._curiosity + (0.4 - self._curiosity) * dt / 40.0
                                + 0.3 * self._odor_grad * dt)
        self._satisfaction = max(self._satisfaction * math.exp(-dt / 240.0),
                                 (1.0 - cold) * 0.4)
        self._valence += ((self._satisfaction * 0.7 - self._fear * 0.8)
                          - self._valence) * min(1.0, dt / 5.0)
        self._arousal = clamp(max(self._fear, self._wind_trace * 0.5,
                                  odor * 0.4, self._hunger * 0.25))

        # ---- 信任双向动力学(A2,调研《互动体验_情绪信任躲藏调研.md》§1) ----
        # 与果蝇同构:trust' = trust + Δ_event + K_CO·dt·G_fear − (trust−0.2)dt/τ
        # 事件项:slow_approach(光标低速持续接近,复用 perception EMA 速度)
        # + 主动趋近抵达;grab/streak 在 on_event 侧记账。
        cur_dyn = getattr(world, "cursor", None)
        arrive_credit = 0.0
        if self._active_arm == "approach_cursor":
            arrive_credit = self._trust_dyn.arrival(
                px, py, cur_dyn, self._t, self.spon.since_cursor_s)
        self._trust = clamp(
            self._trust
            + self._trust_dyn.passive_delta(dt, self._fear, self._trust)
            + self._trust_dyn.slow_approach(dt, px, py, cur_dyn, self._t)
            + arrive_credit)

        self.episodic.tick(dt)
        self.assoc.tick(dt)

    @staticmethod
    def _to_source(s: Stimulus, px: float, py: float) -> tuple[float, float]:
        if s.direction:
            d = math.hypot(*s.direction) or 1.0
            return (s.direction[0] / d, s.direction[1] / d)
        d = max(1e-3, dist(px, py, *s.pos))
        return ((s.pos[0] - px) / d, (s.pos[1] - py) / d)

    def vibration_threshold(self) -> float:
        """尾须震动反射阈(基准 0.5)+ 信任档位偏置(与果蝇 GF 阈同构调制)。"""
        return clamp(0.5 + TRUST_TIER_ESCAPE_BIAS[trust_tier(self._trust)],
                     0.30, 0.70)

    # ================= 决策(规则仲裁优先于网络输出) =================
    def decide(self, world: WorldView) -> BehaviorCommand:
        if self._cmd_queue:
            return self._cmd_queue.pop(0)
        px, py = self.state.pos
        clamp_pos = world.clamp_to_screen  # 位置钳制;标量钳制用顶部导入的 clamp

        # 1) 尾须震动反射:单拍响应,方向 = 反风向 ± 抖动,反应要快。
        #    反射阈按信任档位调制(A2,调研 §1 分档行为表):怕人 0.42(更易惊),
        #    警惕 0.50(基线),习惯/主动靠近 0.55(温和共处后反射钝化)
        vib_th = self.vibration_threshold()
        vib_s = next((s for s in self._last_stimuli
                      if s.kind == StimulusKind.VIBRATION and s.intensity > vib_th), None)
        if vib_s is not None:
            self._fear = 1.0
            self.episodic.remember("尾须感到震动,弹开!", salience=0.8)
            return self._escape_from(vib_s, world, priority=95, reason="尾须震动反射")
        contact_s = next((s for s in self._last_stimuli
                          if s.kind == StimulusKind.CONTACT and s.intensity > 0.5), None)
        if contact_s is not None:
            self._fear = 1.0
            return self._escape_from(contact_s, world, priority=92, reason="接触逃逸")
        # 2) 逼近阴影(扩张 + 快速)
        sh = next((s for s in self._last_stimuli
                   if s.kind == StimulusKind.SHADOW and s.intensity > 0.6
                   and s.meta.get("gesture") in ("approach_fast", "rush")), None)
        if sh is not None:
            return self._escape_from(sh, world, priority=85, reason="逼近阴影逃逸")
        # 3) 负趋光(接口保留:可经 set_phototaxis 关闭)
        light_s = next((s for s in self._last_stimuli
                        if s.kind == StimulusKind.LIGHT and s.intensity > 0.3), None)
        if self.phototaxis_negative and light_s is not None:
            d = self._to_source(light_s, px, py)
            tgt = clamp((px - d[0] * 460, py - d[1] * 460))
            return BehaviorCommand(Behavior.EXPLORE, target=tgt, intensity=0.8,
                                   priority=35, reason="负趋光,避光移动")
        # 4) 触角嗅觉梯度趋食 + 饥饿驱动
        f = self._nearest_food(world, 900.0)
        if f is not None and dist(px, py, *f.pos) < 60 and self._hunger > 0.25:
            return BehaviorCommand(Behavior.EAT, priority=40, intensity=0.9, reason="进食")
        if self._hunger > 0.5 and self._odor_now > 0.15:
            gain = min(1.0, 0.65 + 0.3 * max(0.0, self._odor_grad))
            if f is not None:
                return BehaviorCommand(Behavior.SEEK_FOOD, target=f.pos,
                                       intensity=gain, priority=40,
                                       reason="触角嗅觉梯度趋食")
            tgt = clamp((px + self._odor_dir[0] * 320, py + self._odor_dir[1] * 320))
            return BehaviorCommand(Behavior.SEEK_FOOD, target=tgt, intensity=gain,
                                   priority=38, reason="逆气味梯度探索")
        # 5) 冷区:本能先验 vs 联想覆写(story 协议,与果蝇一致)
        attitude = self._cold_attitude()
        zone = self._nearest_zone(world, "cold")
        if zone is not None:
            zx, zy = zone.pos
            d = max(1e-3, dist(px, py, zx, zy))
            if d < zone.radius and attitude < 0.0:
                away = clamp_pos((px + (px - zx) / d * 500, py + (py - zy) / d * 500))
                return BehaviorCommand(Behavior.EXPLORE, target=away, intensity=0.8,
                                       priority=50, reason="冷区不适,离开")
            if attitude > 0.2:
                return BehaviorCommand(Behavior.EXPLORE, target=clamp_pos((zx, zy)),
                                       intensity=0.6, priority=20,
                                       reason="联想:冷区值得期待,趋近")
        # 5.5)+6) 自发行为层(B01 理毛 / B02 找主人 / B03 昼夜)与默认游走/驻留
        #      合并为"自发层候选",由多臂老虎机仲裁(操作式学习接入点):
        #      fresh 臂同拿满额置信加分,排序由本能 μ 先验决定(与旧优先级
        #      次序一致);学习后才产生重排。规则层 1-4 不受影响。
        sp: list[tuple[int, BehaviorCommand, str | None]] = []
        if self.spon.wants_groom:
            sp.append((16, BehaviorCommand(Behavior.GROOM, priority=16, intensity=0.6,
                                           reason="自发理毛(停歇期固定动作模式)"), "groom"))
        elif self.spon.groom_cd <= 0.0:
            self.spon.groom_begin()
        if self.spon.poll_rest() and self._fear < 0.2:
            sp.append((13, BehaviorCommand(Behavior.REST, priority=13, intensity=0.5,
                                           reason="昼夜节律:静息倾向(夜行动物,白天倦)"),
                       "rest"))
        # B02 主动趋近受信任档位门控(与果蝇同构):怕人档(0)禁用 approach_cursor
        # 臂;主动靠近档(3)静止触发阈 15s→8s(更愿意找主人)
        _tier = trust_tier(self._trust)
        if _tier >= 1 and self.spon.owner_ready(TRUST_TIER_OWNER_STILL[_tier]) \
                and self._fear < 0.15:
            cur = getattr(world, "cursor", None)
            if cur is not None:
                sp.append((15, BehaviorCommand(
                    Behavior.EXPLORE, target=clamp_pos((cur.x, cur.y)),
                    intensity=0.25, priority=15,
                    reason="主人安静,主动趋近(非逃逸)"), "approach_cursor"))
        # 6) 网络效价调制默认行为:游走 → 探索(逃逸安全由规则层 1-3 保证)
        _avoid, _approach, wander = self._out
        if wander >= 0.0:
            # 两态 run-and-turn 状态机推进(含航点维护/停顿/表演子状态)
            self._step_cruise(world)
            if self._cruise_cmd is not None:
                # 停顿(dab)/ 原地转表演(show)子状态命令(仅压过普通游走)
                sp.append((10, self._cruise_cmd, "explore_wander"))
            else:
                tgt = self._explore_target(world)
                inten = clamp(0.5 + 0.4 * wander + self._curiosity * 0.3
                              - self._fear * 0.4)
                # critic 价值调制:预期处境好(V>0)→ 探索更起劲(路线 C 最小出口)
                inten = clamp(inten * (1.0 + 0.15 * self._v_prev))
                # 探索速度噪声:转向事件期减速(转向-速度负耦合,行为学常识)
                slow = 0.8 if self._mode == "arc" else 1.0
                inten = clamp(inten * (1.0 - EXPLORE_SPEED_NOISE
                                       * abs(self._ou) / OU_RATE_MAX) * slow)
                # thigmotaxis 贴墙慢行(行为学常识:蜚蠊贴边慢行探缝)
                if self._in_wall_band:
                    inten = clamp(inten * WALL_SPEED_FACTOR)
                sp.append((10, BehaviorCommand(Behavior.EXPLORE, target=tgt,
                                               intensity=inten, priority=10,
                                               reason="游走探索(网络 wander>0)"),
                           "explore_wander"))
        else:
            sp.append((5, BehaviorCommand(Behavior.REST, priority=5, intensity=0.7,
                                          reason="驻留(网络 wander<0)"), "rest"))
        return self._arbitrate_spontaneous(sp)[1]

    def _arbitrate_spontaneous(self, sp: list) -> tuple:
        """自发层仲裁:score = 优先级 + BANDIT_W_GAIN·UCB(arm)。

        分差 >BANDIT_NEAR_TIE 直接取最高(近似确定);近平手按 softmax 逆温度
        抽样(情绪调制)。同帧记账"当前活跃臂",供 fed/grab 信用分配。"""
        curi, aro = self._curiosity, self._fear
        scored: list[tuple[float, int, BehaviorCommand, str | None]] = []
        for p, cmd, arm in sp:
            u = 1.0 if arm is None else self.bandit.ucb(arm, curi, aro)
            scored.append((p + BANDIT_W_GAIN * u, p, cmd, arm))
        if not scored:
            scored.append((0.0, 0,
                           BehaviorCommand(Behavior.REST, priority=5, intensity=0.7,
                                           reason="驻留"), "rest"))
        top = max(s for s, _, _, _ in scored)
        near = [t for t in scored if top - t[0] < BANDIT_NEAR_TIE]
        if len(near) > 1:
            idx = self.bandit.softmax_pick({i: t[0] for i, t in enumerate(near)},
                                           self.bandit.inv_beta(aro, curi))
            winner = near[idx]
        else:
            winner = near[0]
        arm = winner[3]
        if arm is not None:
            if arm != self._active_arm or self._t - self._arm_trial_t >= SPONT_TRIAL_S:
                self.bandit.trial(arm)
                self._arm_trial_t = self._t
            self._active_arm = arm
        # 返回口径 (prio, cmd, arm):调用方取 [1] 当命令对象(曾把 4 元组
        # 原样返回,[1] 是 int 优先级——'int' has no attribute 'behavior' 同源 bug)
        return winner[1], winner[2], winner[3]

    # ---- 尾须逃逸:反风向 ± 抖动(强可靠、弱多变);强刺激小概率改滑翔 ----
    def _escape_from(self, s: Stimulus, world: WorldView, *, priority: int,
                     reason: str) -> BehaviorCommand:
        d = self._to_source(s, self.state.pos[0], self.state.pos[1])
        if s.intensity >= 0.75:
            jitter = math.radians(self._rng.uniform(-35.0, 35.0))  # 强刺激:可靠背风
        else:
            # 弱刺激:大角度变向(行为学:逃逸方向高变异性,90-180°)
            jitter = math.radians(self._rng.uniform(90.0, 180.0)
                                  * (1.0 if self._rng.random() < 0.5 else -1.0))
        cos_j, sin_j = math.cos(jitter), math.sin(jitter)
        ex = -(d[0] * cos_j - d[1] * sin_j)
        ey = -(d[0] * sin_j + d[1] * cos_j)
        target = world.clamp_to_screen((self.state.pos[0] + ex * 420,
                                        self.state.pos[1] + ey * 420))
        self._learn_danger()
        return BehaviorCommand(Behavior.ESCAPE, target=target, intensity=1.0,
                               priority=priority, reason=reason)

    def _learn_danger(self) -> None:
        self.assoc.learn("cursor:threat", -0.5, 0.10)

    # ================= 躲藏状态机(B2/ADR-0029;调研 §2/§3/§4) =================
    def _is_day(self) -> bool:
        """白天态:显式覆写(day_phase)优先,否则按昼夜节律曲线自判
        (nocturnal → circadian_rest_bias>0.3 即白昼倦;spontaneous.py:194)。"""
        if self.day_phase is not None:
            return bool(self.day_phase)
        return circadian_rest_bias(self.spon.nocturnal) > 0.3

    def _region_weight(self, region: str | None) -> float:
        """区块偏好权重(调研 §2.3 矩阵;region=None → 1.0 退回旧均匀行为)。"""
        if region is None:
            return 1.0
        if region.startswith("corner"):
            region = "corner"
        row = REGION_W_ROACH.get(region)
        if row is None:
            return 0.5
        if self._fear > 0.5:
            return row[2]
        return row[0] if self._is_day() else row[1]

    def _window_events(self, world: WorldView) -> None:
        """SCREEN_EVENT 反应(调研 §4.3):appear/grow 距 <600px → 警觉
        fear+0.1(当帧);大窗(>25% 屏)<300px → 置躲藏触发位。同 hwnd 30s 节流。"""
        evs = getattr(world, "window_events", None) or ()
        if not evs:
            return
        px, py = self.state.pos
        area_scr = max(1, world.screen[0] * world.screen[1])
        for e in evs:
            if getattr(e, "kind", "") not in ("appear", "grow"):
                continue
            hwnd = int(getattr(e, "hwnd", 0) or 0)
            if self._t - self._evt_seen.get(hwnd, -1e9) < EVT_ALERT_COOLDOWN_S:
                continue
            self._evt_seen[hwnd] = self._t
            c = getattr(e, "center", (0, 0))
            d = dist(px, py, c[0], c[1])
            if d < EVT_ALERT_NEAR_PX:
                self._fear = clamp(self._fear + 0.1)
                rect = getattr(e, "rect", None)
        if len(self._evt_seen) > 64:                  # 有界:保最近 32 个 hwnd
            recent = sorted(self._evt_seen.items(), key=lambda kv: -kv[1])[:32]
            self._evt_seen = dict(recent)

    # ================= 两态转向状态机(run-and-turn,与果蝇同构) =================
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

    def _renew_waypoint(self, world: WorldView, dir_bias: float | None = None) -> None:
        """前向重选巡游航点(防御①③:距离 ≥3BL、夹角限 ±46°、可完成时长;
        替代旧版全屏均匀撒点——目标与航向无关时会出现"目标总在侧后 → 切圆")。

        dir_bias:指定基准方向(rad,如离墙穿越时朝屏心),缺省=当前航向。
        """
        st = self.state
        px, py = st.pos
        spd = st.speed if st.speed > 20.0 else CRUISE_SCALE
        base = st.heading if dir_bias is None else dir_bias
        cand = self._wp
        for _ in range(4):
            d = max(spd * self._rng.uniform(*WP_DIST_T), WP_ARRIVE_MIN)
            a = base + self._rng.uniform(-WP_ANGLE_MAX, WP_ANGLE_MAX)
            cand = world.clamp_to_screen((px + math.cos(a) * d,
                                          py + math.sin(a) * d))
            dd = dist(px, py, *cand)
            ad = abs(wrap_angle(math.atan2(cand[1] - py, cand[0] - px) - base))
            if dd >= WP_ARRIVE_MIN and ad <= WP_ANGLE_MAX:
                # 区块偏好拒绝采样(ADR-0029,调研 §2.3 矩阵):w<1 时按 w
                # 概率保留、失败重抽(≤4 次同现状);w≥1(无区块语义)不消耗
                # rng —— 与旧随机流逐位一致,QF 巡游统计零扰动。
                w_reg = self._region_weight(world.region_at(cand))
                if w_reg >= 1.0 or self._rng.random() < w_reg:
                    break
        self._wp = cand
        self._wp_until = self._t + self._rng.uniform(4.0, 9.0)

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

        omega 档 0.35~0.6rad/s(VC 奔跑弧 19~37°/s)→ 弧半径 ≥3.6BL;
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

        RUN 态:直行(航点 P 对准 + OU 微摆 + thigmotaxis 偏置),每 2.5~6s
        一个决策点:停 1/3 / 换向 1/3 / 维持 1/3(VC 短弧重抽配方)。
        与果蝇的差异:贴墙带内不做随机转向事件(壁面是物理约束,随机弧可能
        指向墙内导致撞墙;贴墙段的离开交给"衰减航点牵引"的力平衡,与旧版
        一致零硬反弹)。
        """
        t, dt = self._t, self._dt_last
        st = self.state
        self._cruise_cmd = None
        # 贴墙带判定(_explore_target 与本函数共用;带内禁止随机弧)+
        # 连续贴墙计时:超时主动离墙穿越开阔区(WALL_FOLLOW_S)
        if self.thigmotaxis:
            w_scr, h_scr = world.screen
            px_, py_ = st.pos
            self._in_wall_band = min(px_, py_, w_scr - px_, h_scr - py_) \
                <= WALL_TRIGGER_DIST
        else:
            self._in_wall_band = False
        if self._in_wall_band:
            if self._wall_since is None:
                self._wall_since = t
                self._wall_follow_dur = self._rng.uniform(*WALL_FOLLOW_S)
            if t - self._wall_since >= self._wall_follow_dur \
                    and t >= self._wall_exit_until:
                # 离墙穿越:航点改钉屏心方向(±46°),此后 WALL_EXIT_MIN_S 内
                # 抑制贴墙切向项,由限幅的航点对准/离墙弧完成转向(半径 ≥3BL)
                cx, cy = w_scr / 2.0, h_scr / 2.0
                self._wall_exit_until = t + max(WALL_EXIT_MIN_S,
                                                self._rng.uniform(3.0, 5.0))
                self._renew_waypoint(world, dir_bias=math.atan2(cy - py_, cx - px_))
        else:
            self._wall_since = None
        exiting = t < self._wall_exit_until
        self._wall_hold = self._in_wall_band and self.thigmotaxis and not exiting
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
        can_arc = (not self._in_wall_band) or exiting   # 带内禁随机弧(防指向墙内);
                                                        # 离墙穿越期除外(弧指向开阔区)
        if t >= self._mode_until:
            # 决策点:原地转表演(独立抽签,带内不出)→ 停 1/3 / 换向 1/3 / 维持
            if can_arc and self._rng.random() < SHOW_PROB:
                sign = 1.0 if self._rng.random() < 0.5 else -1.0
                self._mode = "show"
                self._turn_sign = sign
                self._turn_rem = self._rng.uniform(*SHOW_DELTA)
                self._turn_start_h = st.heading
                self._mode_until = t + self._turn_rem / 2.5 + 1.0   # 保险丝
            elif self._rng.random() < DAB_PROB:
                self._mode = "dab"
                self._mode_until = t + self._rng.uniform(*DAB_DUR_S)
            elif can_arc and self._rng.random() < ARC_PROB:
                sign = 1.0 if self._rng.random() < 0.5 else -1.0   # 对称抽号:零偏航
                self._start_arc(sign, self._rng.uniform(*ARC_DELTA),
                                self._rng.uniform(*ARC_OMEGA), random_turn=True)
            else:
                self._mode_until = t + self._rng.uniform(*RUN_DUR_S)   # 维持直行
        elif can_arc and EXPLORE_WP_PULL > 0.0 and err > TURN_ENTER:
            # 牵引开关同时门控航点弧(置 0 = 完全忽略航点,供对照测试)
            # 航点偏角过大(弹墙后/被外力拽偏):朝航点弧线转向(滞回进入)
            d_err = wrap_angle(wp_dir - st.heading)
            omega = clamp(abs(d_err) * 2.0, ARC_OMEGA[0], ARC_OMEGA[1])
            self._start_arc(1.0 if d_err > 0 else -1.0,
                            min(abs(d_err), ARC_DELTA[1] * 1.2), omega,
                            random_turn=False)

    # ---- 探索目标:限幅合成角速度的一帧前瞻 lead + thigmotaxis 贴墙偏置 ----
    def _explore_target(self, world: WorldView) -> tuple[float, float]:
        """游走探索目标点 = 当前航向 + 合成角速度·dt 前瞻 + OU 有界微摆。

        转向全部表达为"限幅角速度 → 一帧 lead":run 态 ω = 航点 P 对准
        (比例增益限幅)+ 远墙弱吸引(限幅)+ 带内切向跟随(物理约束不限幅);
        arc 态 ω = 弧线档。body 的 bang-bang 限速或比例控制(Agent-1 F4)跟踪
        该 lead 都得 ω_actual ≈ ω_cmd。非贴墙约束的角速度上限随实际速度自适应
        (|ω| ≤ v/(R_MIN·BL))→ 弧线半径恒 ≥R_MIN·BL;lead 每帧由当前几何
        重算,无开环积分累积(旧版 _aim_off 每帧再生误差是转圈根因之二,
        蟑螂同构受益)。
        """
        st = self.state
        px, py = st.pos
        h = st.heading
        dt = self._dt_last
        t = self._t
        # 转向角速度上限随实际速度自适应(贴墙切向项除外:物理约束)
        v_ref = max(st.speed, TURN_SPEED_FLOOR * CRUISE_SCALE)
        cap = v_ref / (TURN_RADIUS_MIN_BL * BODY_LEN)
        if self._mode == "arc":
            omega = clamp(self._turn_sign * self._turn_omega, -cap, cap)
        else:
            wp_dir = math.atan2(self._wp[1] - py, self._wp[0] - px) \
                if self._wp is not None else h
            if self.thigmotaxis:
                wall_w = self._wall_steer(px, py, world.screen, h)
                if self._wall_hold:
                    # 带内:壁面是物理约束,切向跟随优先(WALL_TANGENT_GAIN 不
                    # 限幅,须 ≥v/d 防撞墙);航点对准按 WALL_PULL_DAMP 衰减
                    # (legacy 0.1×PULL 力平衡:贴墙段靠它最终脱墙回归航点)
                    omega = wall_w + wrap_angle(wp_dir - h) \
                        * (EXPLORE_WP_PULL * WALL_PULL_DAMP)
                else:
                    # 开阔域或离墙穿越期:航点 P 对准(限幅)+ 远墙弱吸引(限幅)
                    omega = clamp(wrap_angle(wp_dir - h)
                                  * (EXPLORE_WP_PULL * WP_P_SCALE),
                                  -cap, cap) \
                        + (0.0 if t < self._wall_exit_until
                           else clamp(wall_w, -FAR_ATTRACT_MAX, FAR_ATTRACT_MAX))
                    omega = clamp(omega, -cap, cap)
            else:
                omega = clamp(wrap_angle(wp_dir - h)
                              * (EXPLORE_WP_PULL * WP_P_SCALE),
                              -cap, cap)
        # OU 微摆以"限幅角速度"进 lead(±0.86°/s):偏航被航点对准项均值回拉,
        # 10s 直行漂移 <5° 结构保证(防御②零漂移项);贴墙跟随期不叠加——
        # 壁面几何主导,防 4× 切向增益把微摆噪声放大成抖转
        if self._wall_hold:
            micro_rate = 0.0
        else:
            micro_rate = clamp(self._ou * MICRO_RATE_GAIN,
                               -MICRO_RATE_MAX, MICRO_RATE_MAX)
        aim = h + (omega + micro_rate) * dt
        return world.clamp_to_screen((px + math.cos(aim) * EXPLORE_LOOKAHEAD,
                                      py + math.sin(aim) * EXPLORE_LOOKAHEAD))

    @staticmethod
    def _wall_steer(px: float, py: float, screen: tuple[int, int],
                    heading: float) -> float:
        """thigmotaxis 贴墙转向角速度(rad/s;deskbug brain.py:875-888 同构)。

        - 距最近墙 > WALL_TRIGGER_DIST:朝最近墙弱吸引(趋缝性,轻微);
        - 进入贴墙带:切向目标(取与当前航向夹角最近者)按"保持距离"
          向壁面内倾(收敛到 ~1 体长贴边跟随),强拉沿边行走,
          表现为"贴边走一段"而不是撞墙反弹。
        返回叠加到航向偏移上的角速度(由调用方乘 dt 积分)。
        """
        w, h = screen
        walls = (px, py, w - px, h - py)          # 到 左/上/右/下 墙的距离
        wi = min(range(4), key=walls.__getitem__)
        if walls[wi] > WALL_TRIGGER_DIST:
            # 朝向最近墙的目标航向:左→π,上→-π/2,右→0,下→+π/2。
            # (deskbug brain.py:878-879 的 {1:π/2, 3:-π/2} 按 y 向上坐标书写;
            #  本项目坐标约定为 y 向下(contracts.py),上/下取反修正)
            toward = (math.pi, -0.5 * math.pi, 0.0, 0.5 * math.pi)[wi]
            return wrap_angle(toward - heading) * WALL_ATTRACT_GAIN
        # 各墙的切线方向(左/右墙:竖直;上/下墙:水平),取距当前航向最近者
        tangents = ((0.5 * math.pi, -0.5 * math.pi), (0.0, math.pi),
                    (0.5 * math.pi, -0.5 * math.pi), (0.0, math.pi))[wi]
        opt = tangents[0] if abs(wrap_angle(tangents[0] - heading)) <= \
            abs(wrap_angle(tangents[1] - heading)) else tangents[1]
        # 切向目标朝壁面内倾:距墙越远倾角越大,到 WALL_HOLD_DIST 归零
        inward = (0.0, 0.5 * math.pi, math.pi, -0.5 * math.pi)[wi]
        tilt = clamp((walls[wi] - WALL_HOLD_DIST)
                     / (WALL_TRIGGER_DIST - WALL_HOLD_DIST)) * WALL_HUG_TILT
        d_in = wrap_angle(inward - opt)
        target = opt + clamp(d_in, -tilt, tilt)
        return wrap_angle(target - heading) * WALL_TANGENT_GAIN

    def _cold_attitude(self) -> float:
        """同果蝇:先验与联想的置信加权,正向联想覆写负先验。"""
        prior = self.instinct.valence_prior(self.SPECIES, "cold")
        a = self.assoc.get("context:cold")
        if a is None or a.count == 0:
            return prior
        conf = clamp(a.weight * 8.0)
        att = (1.0 - conf) * prior + conf * a.valence
        if a.valence > 0.2:
            att = max(att, a.valence)
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

    # ---- 在线学习:TD(λ) critic + 隐层→输出三因子更新(事件级单步) ----
    def _critic_learn(self, r: float) -> float:
        """线性 TD(λ) critic:δ = R + γV(s′) − V(s);θ ← θ + η·δ·e。

        e 为 replacing traces(observe 每帧维护);V 与 θ 均 clamp 防发散。
        返回 δ:它同时是 _nn_learn 的写入调制 —— 预期内奖励(V 已抬升)
        δ→0,写入量自动衰减(Rescorla-Wagner / Sutton & Barto §12-13)。"""
        v_now = clamp(sum(t * xi for t, xi in zip(self._theta, self.features)),
                      -1.0, 1.0)
        delta = rpe(r + GAMMA * v_now, self._v_prev)
        self._last_delta = delta
        eta = CRIT_ETA * (1.0 + 0.15 * (self.level - 3))   # 智能等级 → 学得更较快
        ev = self._critic_e.value
        for i in range(N_IN):
            g = eta * delta * ev[i]
            if g:
                self._theta[i] = max(-3.0, min(3.0, self._theta[i] + g))
        return delta

    def _nn_learn(self, target: list[float]) -> None:
        """隐层→输出权重事件更新(v2:三因子 = η·err·资格迹·|δ|)。

        旧版是无 δ、无迹的即时 delta 规则(审计 R1/R2 判缺陷);现在:
        ① 写入量被 |δ| 调制 —— 预期内奖励自动少写;② 信用分配扫隐层资格迹
        —— 事件前数秒活跃过的单元仍被更新。签名与调用点保持不变。"""
        mod = clamp(abs(self._last_delta), 0.0, 1.5)
        if mod <= 1e-3:
            return
        lr = 0.03 * (1.0 + 0.3 * self.level)
        ev = self._nn_elig.value
        for o in range(N_OUT):
            err = target[o] - self._out[o]
            if abs(err) < 1e-3:
                continue
            row = self.w_ho[o]
            for i in range(N_HID):
                row[i] = _clampw(row[i] + lr * err * ev[i] * mod)

    # ================= 情绪 / 事件 / 学习协议 =================
    def emotion(self) -> EmotionState:
        return EmotionState(fear=self._fear, hunger=self._hunger,
                            curiosity=self._curiosity, anger=0.0,
                            trust=self._trust, valence=self._valence,
                            arousal=self._arousal)

    def on_event(self, name: str, data: dict) -> None:
        if name == "grab":
            self._n_danger += 1        # r25 A4:熟练度统计(遇险)
            self._fear = min(1.0, self._fear + 0.6)
            # grab 惩罚按信任档减负(调研 §1,与果蝇同构)
            self._trust = max(0.0, self._trust
                              - TRUST_TIER_GRAB[trust_tier(self._trust)])
            self._critic_learn(-1.0)            # 惩罚事件:critic δ + NN 三因子
            self._nn_learn([-0.5, 1.0, -0.5])
            self.assoc.learn("human:grab", -0.5, 0.10)
            if self._active_arm is not None:    # 操作式信用:惩罚正在做的事
                self.bandit.update(self._active_arm, -1.0)
            self.episodic.remember("被抓了起来,很害怕", salience=0.9)
        elif name == "released":
            self.episodic.remember("被放开,钻缝逃跑!", salience=0.7)
            px, py = self.state.pos
            self._cmd_queue.append(BehaviorCommand(
                Behavior.ESCAPE,
                target=(px + self._rng.uniform(-500, 500), py + self._rng.uniform(-500, 500)),
                intensity=1.0, priority=95, reason="脱手逃逸"))
        elif name == "fed":
            self._n_fed += 1           # r25 A4:熟练度统计(投喂)
            self._hunger = max(0.0, self._hunger - 0.3)
            self._satisfaction = min(1.0, self._satisfaction + 0.35)
            # +0.06 基础 + 定时投喂 streak 加成(与果蝇同构,A2 调研 §1)
            self._trust = min(1.0, self._trust + 0.06
                              + self._trust_dyn.feed(self._t))
            self._valence = clamp(self._valence + 0.25, -1.0, 1.0)
            self._critic_learn(1.0)             # 奖励事件:critic δ + NN 三因子
            self._nn_learn([1.0, -0.5, -0.2])   # 奖励调制:强化"趋近"
            self.assoc.learn("human:feed", 0.7, 0.20)
            # 操作式信用分配:fed 奖励"被投喂时正在做的事" + 联想播种臂先验
            if self._active_arm is not None:
                self.bandit.update(self._active_arm, 1.0)
                ctx = BANDIT_ARM_CTX.get(self._active_arm)
                if ctx:
                    self.bandit.seed_assoc(self._active_arm, self.assoc.valence(ctx))
            self.episodic.remember("吃到了主人投喂的食物,很满足", salience=0.6)
        elif name == "hungry":
            self._hunger = min(1.0, self._hunger + 0.1)
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
            about = str(data.get("about", ""))
            valence = float(data.get("valence", 0.0))
            self.assoc.learn(f"context:{about}", valence, 0.25)
            self.episodic.remember(f"听到了一个故事:{data.get('text', '')}", salience=0.5)

    def inject_command(self, cmd: BehaviorCommand) -> None:
        self._cmd_queue.append(cmd)

    # ---- 配置接口(负趋光 / 贴边偏好可关) ----
    def set_phototaxis(self, negative: bool) -> None:
        self.phototaxis_negative = bool(negative)

    def set_thigmotaxis(self, on: bool) -> None:
        self.thigmotaxis = bool(on)

    def set_intelligence(self, level: int) -> None:
        self.level = max(1, min(5, int(level)))
        self.episodic.set_level(self.level)
        self.assoc.set_level(self.level)
        self.bandit.set_level(self.level)   # 智能越高:探索越少、利用越果断

    def learning_summary(self) -> str:
        """面板"学习"行:critic 状态 + 最近学会的臂偏好(可读数值,调研 §4.8)。"""
        arm, mu, n = self.bandit.best_arm()
        return (f"信任:{trust_tier_zh(self._trust)}档 {self._trust:.2f} | "
                f"V={self._v_prev:+.2f} 最近δ={self._last_delta:+.2f} | "
                f"偏好 {BANDIT_ARM_ZH.get(arm, arm)} μ={mu:.2f}(n={n})")

    def memory_digest(self, limit: int = 50) -> list[str]:
        lines = [f"[NN] 效价 approach{self._out[0]:+.2f} avoid{self._out[1]:+.2f} "
                 f"wander{self._out[2]:+.2f}"]
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
        return {
            "brain": self.brain_id, "version": 1, "t": self._t, "level": self.level,
            "fear": self._fear, "hunger": self._hunger, "curiosity": self._curiosity,
            "trust": self._trust, "valence": self._valence,
            "trust_dyn": self._trust_dyn.to_dict(),
            "satisfaction": self._satisfaction,
            "episodes": self.episodic.save(), "assoc": self.assoc.save(),
            "w_ih": [row[:] for row in self.w_ih],
            "w_ho": [row[:] for row in self.w_ho],
            "b_h": self.b_h[:], "b_o": self.b_o[:],
            "phototaxis": self.phototaxis_negative, "thigmotaxis": self.thigmotaxis,
            # ---- 在线学习新字段(缺省回退:老档案无这些键也能读) ----
            "critic": self._theta[:], "arms": self.bandit.to_dict(),
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
        self._fear = clamp(float(data.get("fear", self._fear)))
        self._hunger = clamp(float(data.get("hunger", self._hunger)))
        self._curiosity = clamp(float(data.get("curiosity", self._curiosity)))
        self._trust = clamp(float(data.get("trust", self._trust)))
        self._trust_dyn.load(data.get("trust_dyn"))   # 老档案缺省回退(A2)
        self._valence = clamp(float(data.get("valence", self._valence)), -1.0, 1.0)
        self._satisfaction = clamp(float(data.get("satisfaction", self._satisfaction)))
        self._t = float(data.get("t", self._t))
        self.episodic.load(data.get("episodes", []))
        self.assoc.load(data.get("assoc", {}))
        try:
            w_ih = data.get("w_ih")
            w_ho = data.get("w_ho")
            if (isinstance(w_ih, list) and len(w_ih) == N_HID
                    and all(len(r) == N_IN for r in w_ih)):
                self.w_ih = [[float(v) for v in row] for row in w_ih]
            if (isinstance(w_ho, list) and len(w_ho) == N_OUT
                    and all(len(r) == N_HID for r in w_ho)):
                self.w_ho = [[float(v) for v in row] for row in w_ho]
            b_h, b_o = data.get("b_h"), data.get("b_o")
            if isinstance(b_h, list) and len(b_h) == N_HID:
                self.b_h = [float(v) for v in b_h]
            if isinstance(b_o, list) and len(b_o) == N_OUT:
                self.b_o = [float(v) for v in b_o]
        except (TypeError, ValueError):
            pass
        self.phototaxis_negative = bool(data.get("phototaxis", True))
        self.thigmotaxis = bool(data.get("thigmotaxis", THIGMO_ON_DEFAULT))
        # ---- 在线学习状态(老档案无键 → 保持零/缺省,向后兼容) ----
        critic = data.get("critic")
        if isinstance(critic, list) and len(critic) == N_IN:
            try:
                self._theta = [max(-3.0, min(3.0, float(v))) for v in critic]
            except (TypeError, ValueError):
                pass
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
        rng = random.Random(20195172)   # 网络权重回出厂
        self.w_ih = [[rng.uniform(-0.4, 0.4) for _ in range(N_IN)] for _ in range(N_HID)]
        self.w_ho = [[rng.uniform(-0.4, 0.4) for _ in range(N_HID)] for _ in range(N_OUT)]
        self.b_h = [0.0] * N_HID
        self.b_o = [0.0] * N_OUT
        # 在线学习状态一并归零(ADR-006:clear_memory 是唯一删除路径)
        self._theta = [0.0] * N_IN
        self._critic_e.reset()
        self._nn_elig.reset()
        self._v_prev = 0.0
        self._last_delta = 0.0
        self.bandit.reset()
        self._active_arm = None
        self._fear = 0.0
        self._hunger = 0.35
        self._curiosity = 0.45
        self._trust = 0.2
        self._trust_dyn.reset()
        # r25 A4:经历计数随"忘记一切"一并归零(与 trust/hunger 同等对待)
        self._n_fed = 0
        self._n_danger = 0
        self._valence = 0.0
