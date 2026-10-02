"""三因子突触可塑 + 资格迹 + 昆虫神经调质(章鱼胺/多巴胺)。

替换什么
--------
原 `fly_brain.py` 的 KC→MBON 写入是 `Δw = η·KC·DAN` —— **只有前突触项与调质项,
缺后突触项**,本质是两因子 Hebb,不是三因子规则;也没有资格迹,因此
"奖赏晚于刺激到达"时信用无法分配。审计(R1)判学习规则生物合理性 4/10。

文献依据
--------
* Frémaux & Gerstner (2016) *Front Neural Circuits* 9:85,
  doi:10.3389/fncir.2015.00085 —— 三因子 = pre × post × 调质,核心是
  eligibility trace(共发放时打"可塑标签",调质稍后到达才落实权重改变)。
* Aso et al. (2014) *eLife* 3:e04577, doi:10.7554/eLife.04577
  —— KC→MBON 是果蝇联想学习的可塑位点,DAN 提供调质门控。
* Burke et al. (2012) *Nature* 492:433, doi:10.1038/nature11614
  —— 昆虫的奖励主信号是**章鱼胺(octopamine)**,它门控多巴胺神经元;
  "昆虫多巴胺=奖励"是过度简化。
* Rescorla & Wagner (1972) —— 预测误差 δ = λ − V 驱动条件化。

代价:纯 Python,每帧只对活跃突触做 O(活跃数×输出数) 次乘加。
"""
from __future__ import annotations

import math


class EligibilityTrace:
    """突触资格迹:共发放时打标签,调质到达后落实权重改变。"""

    __slots__ = ("value", "tau")

    def __init__(self, tau: float = 4.0) -> None:
        self.tau = max(0.05, float(tau))     # 信用分配窗口(秒)
        self.value = 0.0

    def update(self, dt: float, pre: float, post: float) -> float:
        """elig ← elig·e^(−dt/τ) + pre·post(Hebb 式共发放标签)。"""
        self.value = self.value * math.exp(-dt / self.tau) + pre * post
        return self.value

    def decay(self, dt: float) -> None:
        self.value *= math.exp(-dt / self.tau)

    def reset(self) -> None:
        self.value = 0.0


def three_factor_dw(eta: float, delta: float, elig: float,
                    modulator: float = 1.0) -> float:
    """三因子权重增量:Δw = η · δ(预测误差) · elig(资格迹) · 调质增益。

    delta      —— 奖赏预测误差(R − V_pred);正=意外奖励,负=意外缺失
    elig       —— 资格迹(pre×post 的时间累积)
    modulator  —— 调质门控(章鱼胺/多巴胺),0 则完全不长
    """
    return float(eta) * float(delta) * float(elig) * float(modulator)


def rpe(reward: float, v_pred: float) -> float:
    """奖赏预测误差 δ = R − V_pred(Rescorla-Wagner / Schultz 式)。"""
    return float(reward) - float(v_pred)


class VectorTrace:
    """n 维资格迹向量(在线学习公用底座,蟑螂 TD(λ) critic / 隐层迹用)。

    三种步进模式(Sutton & Barto 2018 §12):
    * replacing(替代迹):特征分量 >0 处迹置 1,否则按 γλ 衰减 —— 线性 TD(λ)
      的标准写法,迹幅不随特征持续时间膨胀;
    * ema(指数平均):迹 ← 迹·k + x·(1−k),迹幅收敛到特征幅值 —— 蘑菇体式
      "共活动打标签、调质稍后落实"的向量版(与 EligibilityTrace 标量版同构);
    * leakymax(泄漏最大值):e ← max(|x|, e·k) —— 首个活跃帧迹幅即达 |x|
      (事件级更新在冷启动时也有完整幅值),停止活动后按 τ 指数衰减。

    代价:每帧 O(n) 次乘加,纯 Python。
    """

    __slots__ = ("value", "gamma_lambda", "n")

    def __init__(self, n: int, gamma_lambda: float = 0.784) -> None:
        self.n = int(n)
        self.gamma_lambda = float(gamma_lambda)   # 典型 γ=0.98, λ=0.8 → 0.784
        self.value = [0.0] * self.n

    def replacing_step(self, x: list[float]) -> None:
        """replacing traces:φ_i>0 处 e_i←1,其余按 γλ 衰减。"""
        g = self.gamma_lambda
        v = self.value
        for i in range(self.n):
            v[i] = 1.0 if x[i] > 0.0 else v[i] * g

    def ema_step(self, x: list[float], decay: float) -> None:
        """指数平均迹:decay=exp(−dt/τ);标签幅值归一到特征幅值(迹 ≤ max|x|)。"""
        keep = 1.0 - decay
        v = self.value
        for i in range(self.n):
            v[i] = v[i] * decay + (x[i] if x[i] > 0.0 else -x[i]) * keep

    def leakymax_step(self, x: list[float], decay: float) -> None:
        """泄漏最大值迹:e ← max(|x|, e·decay)。隐层资格迹用这个语义,
        保持与旧即时 delta 规则同量级、又具备秒级信用窗。"""
        v = self.value
        for i in range(self.n):
            xi = x[i] if x[i] > 0.0 else -x[i]
            d = v[i] * decay
            v[i] = xi if xi > d else d

    def reset(self) -> None:
        for i in range(self.n):
            self.value[i] = 0.0


class Neuromodulator:
    """昆虫神经调质:章鱼胺(奖励/唤醒) + 多巴胺(惩罚/RPE) 的全局增益。

    与"情绪只当输入特征"不同,这里是**全局突触/读出增益**:调质标量直接缩放
    可塑率与读出幅度(Burke 2012:OA 门控 DA;Waddell 2013:DA 双价 RPE)。
    """

    __slots__ = ("oa", "da", "tau", "gain_floor", "gain_ceil")

    def __init__(self, tau: float = 1.5, gain_floor: float = 0.5,
                 gain_ceil: float = 1.5) -> None:
        self.oa = 0.0      # 章鱼胺:奖励/唤醒(昆虫主要正价信号)
        self.da = 0.0      # 多巴胺:惩罚/厌恶 + 部分 RPE
        self.tau = float(tau)
        self.gain_floor = float(gain_floor)
        self.gain_ceil = float(gain_ceil)

    def reward(self, amount: float = 1.0) -> None:
        """正向事件(进食等):抬章鱼胺。"""
        self.oa = min(1.0, self.oa + float(amount))

    def punish(self, amount: float = 1.0) -> None:
        """负向事件(被抓/威胁等):抬多巴胺。"""
        self.da = min(1.0, self.da + float(amount))

    def decay(self, dt: float) -> None:
        k = math.exp(-float(dt) / self.tau)
        self.oa *= k
        self.da *= k

    @property
    def plasticity_gain(self) -> float:
        """可塑门控:任一调质升高即可写入(Burke 2012 的 OA→DA 门控)。"""
        return max(self.gain_floor, self.oa + 0.5 * self.da)

    @property
    def readout_gain(self) -> float:
        """读出增益:钳在 [floor, ceil],调质高时行为幅度更大。"""
        return max(self.gain_floor,
                   min(self.gain_ceil, 1.0 + 0.5 * self.oa - 0.4 * self.da))

    def reset(self) -> None:
        self.oa = 0.0
        self.da = 0.0
