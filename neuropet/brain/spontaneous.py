"""自发行为层:昼夜节律 / 自发理毛 / 主动趋近主人(互动性扩展 B01/B02/B03)。

为什么需要这一层
----------------
审计 R3 判定当前最大的互动性缺口是 **"宠物绝不主动找人"**:`app._step_frame`
只有 observe→decide→apply,没有任何 idle/自发分支;无刺激时一律随机游走。
用户离开再回来,宠物既不察觉也不靠近 —— "活物感"恰恰来自"它会注意到你"。

文献依据(见 docs/references/互动性扩展规格.md)
--------------------------------------------
* B01 理毛:果蝇/蟑螂停歇期周期性理毛,单次 1~3s,每 1~3min 一次
  (Seeds et al., *eLife* 2014, doi:10.7554/eLife.02951)。
* B02 主动趋近:central place foraging / 探索行为;宠物应注意到主人并靠近。
* B03 昼夜节律:蟑螂夜行(白天退入暗缝静息),果蝇晨昏性、夜间静息
  (King & Sehgal, *Eur J Neurosci* 2018, PMCID:6353709)。

一个必须踩对的陷阱
------------------
"长时间无互动 → 主动趋近主人"若只看"无刺激时长",会在 **无头仿真/终验**
里也触发 —— 例如 QF 的 `circle.fly` 跑 60s 完全无刺激,此时"无互动"恒真,
宠物会朝一个从未动过的光标点靠拢,从而污染直线度/转弯半径等运动学指标
(那是在测"自发巡游是否转圈",不该被目标导向行为干扰)。

因此 B02 的真实语义必须是 **"主人来过、现在安静了"**:
    has_owner(光标曾移动过) AND 光标静止 ≥15s AND 无显著刺激 ≥15s
无头仿真里光标从未移动 → has_owner 为假 → 不触发。语义也更正确:
"找主人"前提是**有主人可找**。

代价:每帧 O(1),纯 Python,零新增依赖。

信任双向动力学(A2,调研《互动体验_情绪信任躲藏调研.md》§1)
------------------------------------------------------------
用户痛点:"很多时候只会恐惧,不会增加信任"。修复 = 给 trust 加双向方程:
    trust' = clamp01(trust + Δ_event + K_CO·dt·G_fear − (trust−0.2)·dt/τ_TRUST)
事件项(slow_approach / 定时投喂 streak / 主动趋近抵达)与 grab 档位减负
由各脑 on_event/observe 调用本模块的 TrustDynamics 记账器与纯函数;
分档(怕人/警惕/习惯/主动靠近)行为表同样在此定义,两脑同构读取。
"""
from __future__ import annotations

import math
import random
from datetime import datetime

# ================================ 信任动力学(A2) ================================
# 分档阈值:怕人 <0.25 / 警惕 <0.50 / 习惯 <0.75 / 主动靠近 ≥0.75
TRUST_TIER_EDGES = (0.25, 0.50, 0.75)
TRUST_TIER_ZH = ("怕人", "警惕", "习惯", "主动靠近")
# 逃逸反射阈的档位偏置(负 = 更易惊):怕人 GF 阈 −0.08(蟑螂震动反射阈
# 0.50→0.42 同源),习惯/主动靠近 +0.05(温和共处后惊跳反应钝化,
# Rankin 2009 习惯化跨情境泛化到"人"这个对象)。
TRUST_TIER_ESCAPE_BIAS = (-0.08, 0.0, 0.05, 0.05)
# grab 惩罚按档减负:习惯 −0.09 / 主动靠近 −0.06(怕人/警惕维持 −0.12)。
# 语义:信任高的个体把抓持解读为"主人互动"的成分更多,应激更少。
TRUST_TIER_GRAB = (0.12, 0.12, 0.09, 0.06)
# wants_owner 触发所需的光标静止时长:主动靠近档 15s→8s(更愿意找主人)。
TRUST_TIER_OWNER_STILL = (15.0, 15.0, 15.0, 8.0)
TRUST_BASE = 0.2          # 回归基线(跨天淡忘 ≠ 删除记忆)
K_CO = 6e-5               # 共处项系数 /s:纯共处 0.2→1.0 需 ~3.7h
TAU_TRUST = 72.0 * 3600.0  # 回归时间常数 72h

# slow_approach(光标低速持续接近)事件参数(复用 perception 光标 EMA 速度):
SA_V_MIN = 30.0     # ≥V_STILL(perception 手势 idle 上限):光标须真的在动,
                    # 防"宠物自己漂近静止光标"被误记为人的温和靠近
SA_V_MAX = 300.0    # <V_SLOW(perception approach_slow 上档 350 内取保守值)
SA_RADIUS = 160.0   # 只在近距计数(再远谈不上"向宠物靠近")
SA_SUSTAIN_S = 5.0  # 低速持续接近满 5s 才计 1 次
SA_COOLDOWN_S = 45.0
SA_CREDIT = 0.03
# 主动趋近抵达(approach_cursor 臂执行中,进入光标 80px 且光标静止 ≥10s):
ARRIVE_RADIUS = 80.0
ARRIVE_STILL_S = 10.0
ARRIVE_COOLDOWN_S = 60.0
ARRIVE_CREDIT = 0.03
# 定时投喂 streak:距上次 fed 20~90s 承认"定时节奏",奖励 +0.02×min(n,5);
# <20s 视为同一餐快喂、>90s 视为节奏中断,均不计入。
FED_GAP_S = (20.0, 90.0)
FED_STREAK_BONUS = 0.02
FED_STREAK_MAX = 5


def trust_tier(trust: float) -> int:
    """trust ∈ [0,1] → 档位 0..3(怕人/警惕/习惯/主动靠近)。"""
    t = max(0.0, min(1.0, trust))
    if t < TRUST_TIER_EDGES[0]:
        return 0
    if t < TRUST_TIER_EDGES[1]:
        return 1
    if t < TRUST_TIER_EDGES[2]:
        return 2
    return 3


def trust_tier_zh(trust: float) -> str:
    return TRUST_TIER_ZH[trust_tier(trust)]


def cohab_gain(fear: float) -> float:
    """共处项的恐惧门控 G_fear:害怕时不涨信任(1.0 / 0.3 / 0.05)。"""
    if fear < 0.3:
        return 1.0
    return 0.3 if fear < 0.6 else 0.05


class TrustDynamics:
    """信任事件记账器(两脑同构,O(1) 状态):slow_approach 计时、
    定时投喂 streak、主动趋近抵达冷却。被动项(共处/回归)是无状态
    纯函数 passive_delta,脑侧每帧直接调用。"""

    def __init__(self) -> None:
        self.sa_t = 0.0             # slow_approach 已持续时长(s)
        self.sa_prev_d: float | None = None  # 上一拍到光标距离(判"正在接近")
        self.sa_credit_at = -1e9    # 上次 slow_approach 计分时刻(s)
        self.last_fed_t = -1.0      # 上次 fed 时刻(s;-1 = 从未)
        self.fed_streak = 0         # 定时投喂连击数
        self.arrive_credit_at = -1e9  # 上次"趋近抵达"计分时刻(s)

    # ---- 被动项:共处积累 + 慢回归(每帧) ----
    def passive_delta(self, dt: float, fear: float, trust: float) -> float:
        g = cohab_gain(fear)
        return K_CO * dt * g - (trust - TRUST_BASE) * dt / TAU_TRUST

    # ---- 事件项:fed streak(调用于 on_event("fed")) ----
    def feed(self, now: float) -> float:
        gap = (now - self.last_fed_t) if self.last_fed_t >= 0.0 else None
        bonus = 0.0
        if gap is not None and FED_GAP_S[0] <= gap <= FED_GAP_S[1]:
            self.fed_streak += 1
            bonus = FED_STREAK_BONUS * min(self.fed_streak, FED_STREAK_MAX)
        else:
            self.fed_streak = 1
        self.last_fed_t = now
        return bonus

    # ---- 事件项:光标低速持续接近(调用于 observe,复用光标 EMA 速度) ----
    def slow_approach(self, dt: float, px: float, py: float,
                      cursor: object | None, now: float) -> float:
        """光标以低速(sa 速度带)且距离单调缩短地逼近宠物满 SA_SUSTAIN_S
        → 计 1 次 +SA_CREDIT;SA_COOLDOWN_S 内不重复计。速度读的是
        perception 3 帧 EMA 平滑后的 cursor.speed(零新依赖)。"""
        if cursor is None:
            self.sa_t = 0.0
            self.sa_prev_d = None
            return 0.0
        cx, cy = float(getattr(cursor, "x", 0.0)), float(getattr(cursor, "y", 0.0))
        d = math.hypot(cx - px, cy - py)
        credit = 0.0
        sp = float(getattr(cursor, "speed", 0.0))
        approaching = (self.sa_prev_d is not None and d < self.sa_prev_d - 0.1)
        if SA_V_MIN <= sp < SA_V_MAX and d < SA_RADIUS and approaching:
            self.sa_t += dt
            if (self.sa_t >= SA_SUSTAIN_S
                    and now - self.sa_credit_at >= SA_COOLDOWN_S):
                credit = SA_CREDIT
                self.sa_credit_at = now
                self.sa_t = 0.0
        else:
            self.sa_t = 0.0
        self.sa_prev_d = d
        return credit

    # ---- 事件项:主动趋近抵达(observe;由脑侧先判 approach_cursor 臂在执行) ----
    def arrival(self, px: float, py: float, cursor: object | None,
                now: float, cursor_still_s: float) -> float:
        if cursor is None:
            return 0.0
        cx, cy = float(getattr(cursor, "x", 0.0)), float(getattr(cursor, "y", 0.0))
        if (math.hypot(cx - px, cy - py) < ARRIVE_RADIUS
                and cursor_still_s >= ARRIVE_STILL_S
                and now - self.arrive_credit_at >= ARRIVE_COOLDOWN_S):
            self.arrive_credit_at = now
            return ARRIVE_CREDIT
        return 0.0

    # ---- 持久化(向后兼容:老档案缺省回退) ----
    def to_dict(self) -> dict:
        return {"sa_t": self.sa_t, "sa_credit_at": self.sa_credit_at,
                "last_fed_t": self.last_fed_t, "fed_streak": self.fed_streak,
                "arrive_credit_at": self.arrive_credit_at}

    def load(self, d: dict | None) -> None:
        d = d or {}
        self.sa_t = float(d.get("sa_t", self.sa_t))
        self.sa_credit_at = float(d.get("sa_credit_at", self.sa_credit_at))
        self.last_fed_t = float(d.get("last_fed_t", self.last_fed_t))
        self.fed_streak = max(0, int(d.get("fed_streak", self.fed_streak)))
        self.arrive_credit_at = float(d.get("arrive_credit_at",
                                            self.arrive_credit_at))

    def reset(self) -> None:
        self.__init__()


def circadian_rest_bias(nocturnal: bool = False) -> float:
    """昼夜节律静息倾向(0..1),由本地时钟决定。

    nocturnal=True(蟑螂):昼伏夜出 —— 正午前后静息倾向最高。
    nocturnal=False(果蝇):晨昏活跃、后半夜静息。
    """
    now = datetime.now()
    h = now.hour + now.minute / 60.0
    if nocturnal:
        return max(0.0, min(1.0, 1.0 - abs(h - 13.0) / 7.0))
    if 6.0 <= h < 18.0:
        return 0.0
    if h < 6.0:
        return max(0.0, min(1.0, 1.0 - abs(h - 3.0) / 4.0))
    return max(0.0, min(1.0, 1.0 - abs(h - 21.0) / 4.0))


class Spontaneous:
    """自发行为状态机:计时 + 主人活动追踪 + 候选生成。"""

    def __init__(self, nocturnal: bool = False, rng: random.Random | None = None,
                 groom_every: tuple[float, float] = (60.0, 180.0),
                 groom_len: tuple[float, float] = (1.0, 3.0)) -> None:
        self.nocturnal = bool(nocturnal)
        # 必须用**自己的** RNG,不能共用脑的:本类在 __init__ 就会抽一次
        # uniform(60,180) 作为理毛倒计时,共用会整体平移脑的随机流,
        # 使依赖确定性随机的行为学测试(OU 自相关/航点牵引/贴边)全部偏移。
        # 固定种子保证可复现。
        self._rng = rng or random.Random(20240918)
        self.groom_every = groom_every
        self.groom_len = groom_len
        self.idle_s = 0.0            # 无显著刺激累计时长
        self.groom_left = 0.0        # 理毛剩余时长(>0 表示正在理毛)
        self.groom_cd = self._rng.uniform(*groom_every)
        # 静息必须是**时段(bout)**而不是逐帧抛硬币:后者会让宠物在整夜里
        # 反复"起步—停—起步",既不像真实睡眠,也会把转弯半径类运动学指标
        # 打塌(实测:果蝇 60s 转弯半径中位数从 4BL 掉到 0.87BL)。
        self.rest_left = 0.0         # 当前静息时段剩余
        self.rest_cd = self._rng.uniform(45.0, 120.0)   # 距下次静息的倒计时
        self.has_owner = False       # 光标是否曾经移动过(有主人可找)
        self.since_cursor_s = 0.0    # 光标静止时长
        self._last_cursor: tuple[float, float] | None = None

    # ---------------------------------------------------------- 每帧
    def update(self, dt: float, peak_intensity: float,
               cursor: object | None) -> None:
        """在 observe() 里每帧调用。cursor 需有 .x/.y(无则传 None)。"""
        if peak_intensity < 0.15:
            self.idle_s += dt
        else:
            self.idle_s = 0.0
        if self.groom_left > 0.0:
            self.groom_left = max(0.0, self.groom_left - dt)
        else:
            self.groom_cd -= dt
        if self.rest_left > 0.0:
            self.rest_left = max(0.0, self.rest_left - dt)
        else:
            self.rest_cd -= dt
        # 主人活动追踪
        if cursor is not None:
            cx, cy = float(getattr(cursor, "x", 0.0)), float(getattr(cursor, "y", 0.0))
            if self._last_cursor is None:
                self._last_cursor = (cx, cy)
            elif math.hypot(cx - self._last_cursor[0], cy - self._last_cursor[1]) > 2.0:
                self._last_cursor = (cx, cy)
                self.since_cursor_s = 0.0
                self.has_owner = True
            else:
                self.since_cursor_s += dt

    def poll_rest(self) -> bool:
        """是否应处于静息时段(昼夜节律 B03)。

        进入条件:不在静息中 + 倒计时到点 + 真的空闲(idle>20s)+ 当前时段
        确有静息倾向。进入后持续一个 3~8s 的 bout,结束后进入 60~180s 不应期。
        """
        if self.rest_left > 0.0:
            return True
        if self.rest_cd > 0.0 or self.idle_s < 20.0:
            return False
        bias = circadian_rest_bias(self.nocturnal)
        if bias <= 0.05:
            return False
        self.rest_left = self._rng.uniform(3.0, 8.0)
        self.rest_cd = self.rest_left + self._rng.uniform(60.0, 180.0)
        return True

    @property
    def wants_groom(self) -> bool:
        return self.groom_left > 0.0

    def groom_begin(self) -> None:
        self.groom_cd = self._rng.uniform(*self.groom_every)
        self.groom_left = self._rng.uniform(*self.groom_len)

    @property
    def wants_owner(self) -> bool:
        """主人来过、现在安静、且自身无刺激 → 缓慢趋近(不是逃跑)。"""
        return self.owner_ready(TRUST_TIER_OWNER_STILL[1])

    def owner_ready(self, min_still: float = 15.0) -> bool:
        """同 wants_owner,但"光标静止时长"阈值可由信任档位调制
        (主动靠近档 8s,其余 15s;见 TRUST_TIER_OWNER_STILL)。"""
        return (self.has_owner and self.since_cursor_s >= min_still
                and self.idle_s >= 15.0)

    # ---------------------------------------------------------- 持久化
    def to_dict(self) -> dict:
        return {"groom_cd": self.groom_cd, "has_owner": self.has_owner}

    def load(self, d: dict | None) -> None:
        d = d or {}
        self.groom_cd = float(d.get("groom_cd", self.groom_cd))
        self.has_owner = bool(d.get("has_owner", False))

    def reset(self) -> None:
        self.idle_s = 0.0
        self.groom_left = 0.0
        self.groom_cd = self._rng.uniform(*self.groom_every)
        self.has_owner = False
        self.since_cursor_s = 0.0
        self._last_cursor = None
