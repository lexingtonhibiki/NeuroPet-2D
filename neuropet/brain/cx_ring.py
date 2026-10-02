"""CX 环形吸引子(ring attractor)—— 果蝇中央复合体航向回路的最小真实实现。

为什么必须替换原实现
--------------------
原 `fly_brain.py` 的 "EB 航向环" 是把**当前 heading** 低通复制成一个单峰::

    h = int(heading / 2π * 16); target = 1 / 0.45 / 0
    eb[i] += (target - eb[i]) * dt / 0.1

它是 heading 的**函数**,不是 heading 的**记忆**:断开输入立刻消失,也无法在
黑暗中靠自运动(角速度)更新航向 —— 即没有路径积分。审计(R1)判其为"装饰性环"。

文献依据
--------
* Seelig & Jayaraman (2015) *Nature* 521:186, doi:10.1038/nature14446
  —— CX EB 群体活动编码航向,黑暗中靠自运动维持持久活动。
* Turner-Evans et al. (2017) *eLife* 6:e23496, doi:10.7554/eLife.23496
  —— P-EN(tile)神经元把 bump 按角速度沿环位移,是路径积分的回路 motif。
* Kim et al. (2017) *Science* 356:849, doi:10.1126/science.aal4835
  —— 局部兴奋 + 全局抑制的 ring attractor,并公开了仿真源码。

模型(墨西哥帽 + 软顶归一)
------------------------
每步分两段,与回路一一对应:

1. **P-EN 位移通路**(显式):把活动包沿环平移 s = ω·dt·N/(2π) 槽,
   分数线性插值 —— Turner-Evans (2017) 的 tile 位移。
2. **递归吸引子**(涌现):
       r   = relu(v)
       u_i = g_e·(Ke⊗r)_i − g_i·(Ki⊗r)_i        # 局部兴奋 − 更宽的环绕抑制
       v_i += dt/τ · ( u_i − leak·v_i + I_i )
   总活动超过 target 时做除法归一(全局抑制反馈,生物上由 GI 中间神经元实现)
       decoded = arg( Σ_i r_i·e^{iθ_i} )          # 环状读出(向量平均)

**哪些性质是涌现的、哪些是显式建模的**(诚实标注):
   涌现 —— 持久活动、单峰自组织、线索锚定、bump 宽度;
   显式 —— 角速度→位移的传递(P-EN 通路本身就是"位移器",把它写成平移
           就是对该回路的直接建模,不是绕过)。

实现上必须踩对的三个坑(本模块前两版都踩过,已修)
------------------------------------------------
1. **位移量化**:每帧位移常远小于 1 槽(ω=0.6rad/s、60fps、N=16 时仅 0.026 槽),
   逐帧 `round(s)` 恒为 0 → bump 完全不动,路径积分恒等失败。必须分数插值。
2. **均匀态不动点**:若只用"归一化兴奋核 + 全局抑制",均匀活动是稳定不动点,
   从均匀初值永远收敛不出 bump。必须让**环绕抑制比兴奋更宽**(墨西哥帽),
   使均匀模特征值为负(取 g_i > g_e + leak),窄 bump 仍为正 → 自发成峰。
3. **别把位移塞进递归核**:第一版把位移做进核表、靠泄漏积分把 bump "拉"过去,
   但归一化总在复位驱动幅值(峰处驱动仅 0.365·v),实测 6s 只走出指令位移的 6%
   (扫描 g_e 1.6→8.0 也只从 0.089 提到 0.320)。位移必须是独立通路。

代价:N=16 → 每帧约 512 次乘加 + 一次 atan2,实测 ~0.05ms,零新增依赖。
"""
from __future__ import annotations

import math

TAU = math.tau


def _circ_ofs(m: int, n: int) -> float:
    """环上偏移 m(0..n-1) 对应的带符号最短距离。"""
    return float(m) if m <= n // 2 else float(m - n)


class CXRingAttractor:
    """EB/P-EN 航向环:持久活动 + 角速度路径积分 + 感觉线索锚定。"""

    def __init__(self, n: int = 16, tau: float = 0.08,
                 sigma_e: float = 1.25, sigma_i: float = 3.0,
                 gain_e: float = 1.6, gain_i: float = 1.7, leak: float = 0.05,
                 target: float = 1.0, shift_steps: int = 8,
                 noise: float = 0.02, seed: int = 20240918) -> None:
        self.n = int(n)
        self.tau = float(tau)
        self.sigma_e = float(sigma_e)     # 局部兴奋宽度(必须窄)
        self.sigma_i = float(sigma_i)     # 环绕抑制宽度(必须更宽)
        self.gain_e = float(gain_e)
        self.gain_i = float(gain_i)       # > gain_e + leak → 均匀态不稳定
        self.leak = float(leak)
        self.target = float(target)       # 总活动上限(软顶,非恒定)
        self.shift_steps = int(shift_steps)
        self.noise = float(noise)
        self.v: list[float] = [0.0] * self.n
        self._rng_state = int(seed)
        self._K_e: list[list[float]] = []
        self._K_i: list[list[float]] = []
        self._build_kernels()

    # ---------------------------------------------------------- 结构
    def _build_kernels(self) -> None:
        """预计算位移核表 K[q][m]:q=量化位移(单位 1/shift_steps 槽),m=环偏移。

        预计算而非逐帧 exp() —— 每帧 512 次 exp 在纯 Python 下代价过高,
        查表 + 线性插值后降到纯乘加。
        """
        n, qn = self.n, self.n * self.shift_steps
        for sigma in (self.sigma_e, self.sigma_i):
            tbl: list[list[float]] = []
            for q in range(qn):
                s = q / float(self.shift_steps)
                row = [math.exp(-((_circ_ofs(m, n) - s) ** 2)
                                / (2.0 * sigma * sigma)) for m in range(n)]
                tot = sum(row) or 1.0
                tbl.append([x / tot for x in row])
            if not self._K_e:
                self._K_e = tbl
            else:
                self._K_i = tbl
        # r24 性能:step() 只消费 s=0 表,增益在构造后不变 → 预乘出最内环
        # 权重(免去每帧 2×16 次 gain 属性/乘法);r2 为双倍拼接缓冲(免负
        # 索引回绕)。数值与旧式逐项差 ~1e-16(乘法结合序),验收容差内。
        self._we = [self.gain_e * x for x in self._K_e[0]]
        self._wi = [self.gain_i * x for x in self._K_i[0]]
        self._r2 = [0.0] * (2 * self.n)

    # ---------------------------------------------------------- 噪声
    def _rand(self) -> float:
        """确定性线性同余(保证可复现,不引入 random 依赖状态)。"""
        self._rng_state = (1103515245 * self._rng_state + 12345) & 0x7FFFFFFF
        return self._rng_state / 0x7FFFFFFF - 0.5

    # ---------------------------------------------------------- 位移
    def _rotate(self, s: float) -> None:
        """把活动包沿环平移 s 槽(s 可为分数,线性插值)—— P-EN 位移通路。

        这是 Turner-Evans (2017) 的 tile 位移回路:角速度经 P-EN 群体把 bump
        沿 EB 环推动。**必须做分数插值** —— 每帧位移常远小于 1 槽
        (ω=0.6rad/s、60fps、N=16 时仅 0.026 槽),量化取整会恒为 0,
        bump 完全不动。这是本模块第一版把位移塞进递归核里时踩的坑。
        """
        n = self.n
        v = self.v
        out = [0.0] * n
        for i in range(n):
            x = i - s
            j = math.floor(x)
            f = x - j
            out[i] = v[j % n] * (1.0 - f) + v[(j + 1) % n] * f
        self.v = out

    # ---------------------------------------------------------- 动力学
    def step(self, dt: float, omega: float = 0.0,
             cue: list[float] | None = None, cue_gain: float = 1.0) -> None:
        """推进一步。omega=角速度(rad/s,带符号);cue=外部航向线索(长度 n)。

        两步走(与回路对应):
          1. P-EN 位移通路 —— 按 ω 把 bump 沿环平移(见 `_rotate`);
          2. 递归吸引子 —— 局部兴奋 − 环绕抑制(墨西哥帽)+ 线索 + 泄漏 +
             总活动软顶,负责**持久性、单峰性、线索锚定**(这三点是涌现的)。
        """
        n = self.n
        v = self.v
        # --- 1) 递归吸引子(对称墨西哥帽,先整形) ---
        # r24 性能:核权重在构造器预乘(we=ge·Ke / wi=gi·Ki,见 __init__),
        # 内环免去 gain 属性寻址;r2 双倍拼接免负索引回绕。
        r2 = self._r2
        r2[n:] = [x if x > 0.0 else 0.0 for x in v]
        r2[:n] = r2[n:]
        we, wi = self._we, self._wi
        alpha = min(1.0, float(dt) / self.tau)
        lk = self.leak
        for i in range(n):
            exc = inh = 0.0
            base = i + n
            for m in range(n):
                rm = r2[base - m]
                exc += we[m] * rm
                inh += wi[m] * rm
            drive = exc - inh - lk * v[i]
            if cue is not None:
                drive += cue_gain * cue[i]
            v[i] = v[i] + alpha * drive
        # --- 2) P-EN 角速度位移(后位移,保证本帧净位移就是 s) ---
        # 顺序说明:若先位移再整形,墨西哥帽的整形会把 bump 往回拽(实测
        # 6s 累计角只有指令的 76%);位移放在最后,本帧净位移即解析值。
        s = (float(omega) * float(dt)) * n / TAU
        if s:
            self._rotate(s)
        # 发放率非负
        for i in range(n):
            if v[i] < 0.0:
                v[i] = 0.0
        # 活动近乎沉寂时注入确定性微噪声,打破均匀对称(坑 2)
        if self.noise > 0.0 and sum(v) < 0.25 * self.target:
            for i in range(n):
                v[i] = max(0.0, v[i] + self.noise * abs(self._rand()))
        # 全局抑制反馈:总活动**超上限时**才归一(软顶,允许沉寂)
        tot = sum(v)
        if tot > self.target:
            k = self.target / tot
            for i in range(n):
                v[i] *= k

    # ---------------------------------------------------------- 读出
    def activity(self) -> list[float]:
        """当前 EB 活动(非负放电率),可直接喂给下游/快照。"""
        return [x if x > 0.0 else 0.0 for x in self.v]

    def decoded(self) -> float:
        """环状读出:活动向量平均的幅角(rad,0..2π)。"""
        sx = sy = 0.0
        for i, x in enumerate(self.v):
            if x > 0.0:
                a = TAU * i / self.n
                sx += x * math.cos(a)
                sy += x * math.sin(a)
        if abs(sx) < 1e-12 and abs(sy) < 1e-12:
            return 0.0
        return math.atan2(sy, sx) % TAU

    def bump_amp(self) -> float:
        """bump 峰谷比(衡量"是否真的形成了局部包")。"""
        a = self.activity()
        return (max(a) - min(a)) if a else 0.0

    def coherence(self) -> float:
        """环状读出的一致性(0~1):1=单峰,0=均匀/多峰抵消。"""
        sx = sy = tot = 0.0
        for i, x in enumerate(self.v):
            if x > 0.0:
                a = TAU * i / self.n
                sx += x * math.cos(a)
                sy += x * math.sin(a)
                tot += x
        if tot < 1e-12:
            return 0.0
        return math.hypot(sx, sy) / tot

    # ---------------------------------------------------------- 控制
    def set_heading(self, heading: float, amp: float = 1.0) -> None:
        """用外部线索把 bump 钉到指定航向(视觉锚定/初始化)。"""
        n = self.n
        c = int(round((float(heading) % TAU) / TAU * n)) % n
        self.v = [amp if i == c else 0.0 for i in range(n)]
        tot = sum(self.v) or 1.0
        k = self.target / tot
        self.v = [x * k for x in self.v]

    def reset(self) -> None:
        self.v = [0.0] * self.n


def heading_cue(heading: float, n: int = 16, width: float = 1.0) -> list[float]:
    """生成以 heading 为中心的高斯航向线索(视觉/偏振光输入到 EB)。

    r24 性能:每帧调用(observe→cx.step),内联 _circ_ofs/round/除法提式,
    直写循环(生成器帧开销大),数值逐位等价。"""
    c = (float(heading) % TAU) / TAU * n
    half = n // 2
    inv = 1.0 / (2.0 * width * width)
    exp = math.exp
    out = []
    push = out.append
    for i in range(n):
        m = int(round(abs(i - c))) % n
        d = float(m) if m <= half else float(m - n)
        push(exp(-d * d * inv))
    return out
