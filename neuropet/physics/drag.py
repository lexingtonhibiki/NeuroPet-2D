# -*- coding: utf-8 -*-
"""拖拽力学 v2:每帧连续二阶弹簧-阻尼积分(返工根因 #1 的重做)。

被否的旧实现(neuropet/body/base.py drag_lag_step + tests/test_drag_physics.py):
抓取瞬间按光标速度一次性算偏角目标 + 一阶滞后跟踪——不连续、无昆虫质量、
不受自由度限制(《返工根因分析与流程改进提案.md》证据链 #1)。本模块按用户
裁决重建:结合体重 / 拖拽过程按鼠标加速度连续计算 / 受关节角限制 / 低速时
悬垂静定。

一、全时间线方程(状态变量 / 驱动力 / 阻尼 / DOF,每帧连续积分)
  每个摆角自由度 q ∈ {tilt_pitch, tilt_roll, leg_swings×6, antenna×2} 为
  独立二阶通道(φ_rest=0,q 为相对静息位的偏移):
      I_q·q'' = −c_q·q' − k_q·(q − q_rest) + m_eff,q·a_drive(t)
  归一化:q'' = −2ζω_n·q' − ω_n²·(q − q_rest) + ω_n²·S_q·a_drive(t)
      ω_n = √(k_q/I_q),ζ = c_q/(2√(k_q·I_q)),静态增益 S_q = m_eff,q/k_q
      → 稳态 q_ss = S_q·a(常加速度段)。
  a_drive 投影(体轴系 ax=前后 / ay=左右,px/s²):
      leg_i     = −(GL_pair·ax + GLAT·ay·side_i)   身体向前加速 → 腿因惯性
                  向后拖,故取负号;前后分量全腿同向,侧向分量左右反号(横滚
                  差动);GL=前/中/后 (1.0, 0.85, 0.75) 沿用旧判据 DRAG_LEG_
                  GAIN 口径(tests/test_drag_physics.py B2 幅值带的连续化)。
      antenna   = −(ax ± ANT_LAT·ay)(左右触须轻微差动,主响应前后分量)。
      tilt      = ax / ay 直接驱动,θ_target = clamp(k_t·a, ±25°)。
  step() 对该 ODE 做**精确 ZOH 离散化**(解析状态转移,见 _damped_step):
  任意步长无条件稳定、无截断发散;常值输入下 dt=1/30 与 1/120 逐点一致。

二、质量入参数(mass_g,克;参考 m0 = 0.8g = 美洲大蠊成体)
      ω_q = OMEGA_?0 · (m/m0)^(−0.5)  → 重 → ω_n 低、响应慢、整定时间长
      S_q = S_?0    · (m/m0)^(−0.4)   → 重 → 同加速度峰值摆角小(任务书裁决)
    果蝇 0.001g(质量比 1/800):ω×28.3(整定快)、S×14.5(峰值摆角大,验收 1
    的 ≥3 倍由此保证)。触须 m_eff 相对最小 → ω_n 最高、同加速度偏摆最大。
    注:指数 −0.5/−0.4 是任务书裁决的参数化方向(重→慢+峰值小),非文献
    异速标度,特此诚实注明。

三、自由度钳制(默认值来源,读到值即注明出处)
  leg ThC 偏航 ±60°:任务书冻结;与 body/kinematics.py COXA_YAW_ENV["front"]
    =(±60°)一致;佐证:静息足向角前 50–60°/中 115–130°/后 160–172°、慢行
    摆幅 ±15–25°【照片量测,docs/references/形态学权威_美洲大蠊.md §1】。
  leg CTr 俯仰 [−30°,+110°]:任务书冻结(kinematics.py CTR_RANGE=(−10,40)
    为步态 v2 俯仰 β 口径;拖拽被动悬垂范围更宽,二者口径不同不冲突)。
    本接口每腿只输出 ThC 偏航一维,CTr 范围经 joint_limits 透出供渲染端
    悬垂姿态使用。
  触须 ±80°、tilt ±25°:任务书冻结。
  钳制实现:越界即钉位并钳速度(防钳制壁后蓄能,松钳瞬间回跳)→ 反向加速
  度时摆角平滑过零不出钳(验收 3)。

四、settle(0=完全随动,1=完全悬垂静定;渲染端混合系数)
  |a| > A_IDLE(加速/变向中)→ 指数降 0(τ_down=0.08s);
  |a| ≤ A_IDLE(输入归零≈低速匀速拖拽)→ 指数升 1(τ_up≈0.4s,任务书)。
  低速拖拽时身体垂下静定、摆角归零——"低速时尤其要符合"的落点。

五、动画十二原则对照(gameanim 十二原则;skill §2)
  follow-through / overlapping action → 各通道 ω_n 分层(须 > 腿 > tilt),
    松手后分层回零不同步;slow-in-out → 二阶系统速度天然 S 曲线;
    arcs → 摆角解析连续、反向平滑过零;secondary motion → settle 悬垂静定;
    squash&stretch / anticipation 不适用(刚体旋转量、被动响应)。

六、连续性预算(60Hz,断言在 tests/test_drag_v2.py)
  蟑螂(0.8g)满幅阶跃下相邻帧角度分量变化 ≤6°(实测最严酷为触须 ~5.3°/帧);
  无超调 >30%(ζ∈[0.7,1.1],ζ=0.85 解析超调 e^(−πζ/√(1−ζ²))≈7.4%)。
  果蝇为轻质量通道,ω×28.3 → 整定 <1 帧(60fps),连续性预算不适用——
  这是质量效应本身(验收 1 要求它快),非缺陷。

输入约定(接口语义,渲染接线方必读):
  cursor_ax / cursor_ay = **体轴系**光标加速度(px/s²),x=前向为正、
  y=左侧为正;屏幕系→体轴旋转由调用方完成(perception/mouse.py 现状只出
  EMA 速度/标量加速度,不改它,换算属后续接线波)。
  输出符号:leg_swings / antenna_swing 正=向体前方偏(ax>0 → 惯性后甩 →
  负);tilt_pitch 正=后仰(头抬尾沉,ax>0 → 正);tilt_roll 正=右倾
  (向左加速 ay>0 → 正)。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from neuropet.core.mathutil import clamp

__all__ = ["DragPose", "DragDynamics", "species_dynamics"]


@dataclass
class DragPose:
    tilt_pitch_deg: float      # 体轴俯仰(前后加速度激起;正=后仰)
    tilt_roll_deg: float       # 体轴横滚(左右加速度激起;正=右倾)
    leg_swings: list[float]    # 6 条腿根部摆角偏移(deg),顺序 L1L2L3R1R2R3
    antenna_swing: tuple[float, float]  # 左右触须摆角偏移(deg)
    settle: float              # 0=完全随动 1=完全悬垂静定(用于渲染端混合)


# ---- 质量基准与标度(模块 docstring 二节) ----
# 蟑螂 0.8g:任务书冻结基准。形态学权威_美洲大蠊.md 未载体重(体长 38–53mm
# 【UF/IFAS】);文献成体湿重常见 0.7–1.0g,取 0.8g,如实注明非文档实测。
# 果蝇 0.001g:docs/references/形态学权威_黑腹果蝇.md §4 "体长 2.5mm、体重 ~1mg"。
M_REF_G = 0.8
OMEGA_EXP = -0.5        # ω ∝ (m/m0)^−0.5:重 → ω_n 低、响应慢
GAIN_EXP = -0.4         # S ∝ (m/m0)^−0.4:重 → 峰值小(800× 质量差 → 峰值比 ~14.5×)

# ---- 通道基准参数(蟑螂 0.8g 锚点) ----
OMEGA_LEG0 = 7.2        # rad/s;ζ=0.85 → 2% 整定 ~0.65s(提重物的沉重感);
                        # 取 7.2 使全钳程反向(跨 120°)帧差 ~5.3° ≤6°(验收 3)
OMEGA_ANT0 = 10.0       # rad/s;60Hz 满幅阶跃帧差 ≤5.3°(预算 ≤6°,docstring 六)
OMEGA_TILT0 = 6.0       # rad/s;临界阻尼跟随
ZETA_LEG = 0.85         # 验收 2 要求 ζ∈[0.7,1.1];解析超调 ≈7.4% ≤30%
ZETA_ANT = 0.90
ZETA_TILT = 1.0
S_LEG0 = 0.015          # °/(px/s²):60° 满幅 @ a=4000(冲刺档加速度)
S_ANT0 = 0.023          # 触须相对最轻:同加速度偏摆比腿大(任务书)
K_TILT0 = 0.00625       # θ=clamp(k_t·a,±25°):25° 满幅 @ a=4000
GL = (1.0, 0.85, 0.75)  # 前/中/后腿前后灵敏度(旧判据 DRAG_LEG_GAIN 口径)
GLAT = 0.5              # 侧向加速度 → 左右腿差动
ANT_LAT = 0.15          # 触须左右差动系数

A_IDLE = 30.0           # px/s²;|a|≤此值≈输入归零/低速匀速 → settle 升
TAU_SETTLE_UP = 0.4     # 任务书 τ≈0.4s
TAU_SETTLE_DOWN = 0.08  # 加速时快速降回 0
A_INPUT_MAX = 20000.0   # 输入防爆钳(非物理参数,不影响验收幅值)
# F3(ADR-0031)速度驱动系数(1/s):驱动力 = a + V_DRIVE·v。匀速拖拽的
# 准静态气阻 ∝ −v(绳牵球心智模型):v=600px/s 中速拖拽 → 等效 a=1200 →
# 腿 ~18° 稳定拖尾;v=1500(冲刺截断)→ 30°。静止 v→0 自然回归。
V_DRIVE = 2.0

# 默认关节限位(度)。来源见模块 docstring 三节(逐值注明出处)。
DEFAULT_JOINT_LIMITS: dict = {
    "leg": {"thc_yaw": (-60.0, 60.0),      # 任务书冻结;=kinematics.COXA_YAW_ENV["front"];
                                           # 静息足向/摆幅佐证【形态学权威_美洲大蠊 §1】
            "ctr_pitch": (-30.0, 110.0)},  # 任务书冻结(步态口径 CTR_RANGE=(−10,40) 更窄)
    "antenna": (-80.0, 80.0),              # 任务书冻结
    "tilt": (-25.0, 25.0),                 # 任务书冻结
}

# 前/中/后腿静息足向角(左,度;0=正前):照片量测
# 【docs/references/形态学权威_美洲大蠊.md §1/§5.3】。GL 投影模型的物理佐证,
# 动力学不直接使用(摆角是相对静息位的偏移)。
REST_YAW_DEG = (54.0, 127.0, 161.0)


def _damped_step(x: float, v: float, target: float,
                 wn: float, zeta: float, dt: float) -> tuple[float, float]:
    """二阶通道 ẍ = −2ζω·ẋ − ω²(x−target) 的精确 ZOH 离散化(解析状态转移)。

    输入在步长内视为常值(零阶保持),返回 (x', v')。任意 dt 无条件稳定;
    常值输入下不同步长的解逐点一致(验收 5 的根基)。三段:
      ζ<1 欠阻尼: y(t) = e^(−αt)·[y0·cos(ωd·t) + (v0+α·y0)/ωd·sin(ωd·t)]
      ζ=1 临界:   y(t) = e^(−αt)·[y0 + (v0+α·y0)·t]
      ζ>1 过阻尼: y(t) = A·e^(r1·t) + B·e^(r2·t)
    其中 y = x−target,α = ζω,ωd = ω√(1−ζ²),r1,2 = ω(−ζ±√(ζ²−1)),
    A = (v0 − r2·y0)/(r1 − r2),B = y0 − A。
    """
    if dt <= 0.0:
        return x, v
    y = x - target
    z = clamp(float(zeta), 0.05, 4.0)
    if abs(z - 1.0) < 1e-9:                       # 临界阻尼(双重要极限的退化式)
        a = wn
        e = math.exp(-a * dt)
        c1 = y + (v + a * y) * dt
        return target + c1 * e, (v + a * y) * e - a * c1 * e
    if z < 1.0:                                   # 欠阻尼
        wd = wn * math.sqrt(1.0 - z * z)
        al = z * wn
        e = math.exp(-al * dt)
        s = math.sin(wd * dt)
        c = math.cos(wd * dt)
        yn = e * (y * c + (v + al * y) / wd * s)
        vn = e * (v * c - (wn * wn * y + al * v) / wd * s)
        return target + yn, vn
    r1 = -wn * (z - math.sqrt(z * z - 1.0))       # 过阻尼:两实根
    r2 = -wn * (z + math.sqrt(z * z - 1.0))
    den = r1 - r2
    a_c = (v - r2 * y) / den
    b_c = y - a_c
    e1 = math.exp(r1 * dt)
    e2 = math.exp(r2 * dt)
    return target + a_c * e1 + b_c * e2, a_c * r1 * e1 + b_c * r2 * e2


class _Channel:
    """单二阶通道:状态 (x, v) + 目标钳制 + 越界钉位(速度同钳,防蓄能)。"""
    __slots__ = ("x", "v", "wn", "zeta", "lo", "hi")

    def __init__(self, wn: float, zeta: float, lo: float, hi: float) -> None:
        self.x = 0.0
        self.v = 0.0
        self.wn = max(1e-6, float(wn))
        self.zeta = float(zeta)
        self.lo, self.hi = (float(lo), float(hi)) if lo < hi else (hi, lo)

    def step(self, dt: float, target: float) -> float:
        self.x, self.v = _damped_step(self.x, self.v,
                                      clamp(target, self.lo, self.hi),
                                      self.wn, self.zeta, dt)
        if self.x >= self.hi:      # 钳位同时钳速度:只允许向域内运动
            self.x, self.v = self.hi, min(self.v, 0.0)
        elif self.x <= self.lo:
            self.x, self.v = self.lo, max(self.v, 0.0)
        return self.x


class DragDynamics:
    """拖拽动力学:质量参数化的连续二阶弹簧-阻尼通道组(冻结接口,见模块头)。

    用法:d = species_dynamics("cockroach");每帧 pose = d.step(dt, ax, ay),
    (ax, ay) 为体轴系光标加速度(px/s²)。连续积分,禁止跳帧后重算初值
    (旧版"抓取瞬间一次性算偏角"正是被否的形态)。
    """

    def __init__(self, mass_g: float, joint_limits: dict | None = None):
        m = float(mass_g)
        if not math.isfinite(m) or m <= 0.0:
            raise ValueError(f"mass_g 必须为正有限值,得到 {mass_g!r}")
        self.mass_g = m
        q = m / M_REF_G
        w = q ** OMEGA_EXP                     # 重 → ω_n 低(响应慢)
        g = q ** GAIN_EXP                      # 重 → 静态增益小(峰值小)
        self.omega_leg = OMEGA_LEG0 * w
        self.omega_ant = OMEGA_ANT0 * w
        self.omega_tilt = OMEGA_TILT0 * w
        self.s_leg = S_LEG0 * g                # °/(px/s²),静态增益
        self.s_ant = S_ANT0 * g
        self.k_tilt = K_TILT0 * g
        self.zeta_leg = ZETA_LEG
        self.zeta_ant = ZETA_ANT
        self.zeta_tilt = ZETA_TILT

        # ---- 关节限位规范化(深拷贝默认;调用方可覆盖) ----
        lim = {"leg": dict(DEFAULT_JOINT_LIMITS["leg"]),
               "antenna": DEFAULT_JOINT_LIMITS["antenna"],
               "tilt": DEFAULT_JOINT_LIMITS["tilt"]}
        if joint_limits:
            for k, val in dict(joint_limits).items():
                if k == "leg":
                    lim["leg"].update(dict(val))
                else:
                    lim[k] = tuple(float(x) for x in val)
        self.joint_limits = lim
        thc = tuple(lim["leg"]["thc_yaw"])
        ant = tuple(lim["antenna"])
        tilt = tuple(lim["tilt"])

        # ---- 通道组(状态连续存活于实例上) ----
        self._tilt_p = _Channel(self.omega_tilt, ZETA_TILT, *tilt)
        self._tilt_r = _Channel(self.omega_tilt, ZETA_TILT, *tilt)
        # 顺序 L1L2L3R1R2R3:索引 0..2 = 左前/左中/左后(side=-1),3..5 = 右
        self._leg_side = (-1.0, -1.0, -1.0, 1.0, 1.0, 1.0)
        self._legs = [_Channel(self.omega_leg, ZETA_LEG, *thc) for _ in range(6)]
        self._ant_l = _Channel(self.omega_ant, ZETA_ANT, *ant)
        self._ant_r = _Channel(self.omega_ant, ZETA_ANT, *ant)
        self._settle = 0.0

    # ------------------------------------------------------------------
    def step(self, dt: float, cursor_ax: float, cursor_ay: float,
             body_vx: float = 0.0, body_vy: float = 0.0) -> DragPose:
        """每帧连续积分(冻结接口;语义见模块 docstring 输入约定节)。

        F3(ADR-0031):新增可选体轴速度 (body_vx, body_vy)(缺省 0,旧调用
        逐位不变)。驱动力 = a + V_DRIVE·v —— 任务书方程
        I·φ''=−c·φ'−k·(φ−φ_rest)+m·a−d·v 的归一化落法(d = S·V_DRIVE):
        匀速拖拽(v>0, a≈0)时肢体保持 ∝ −v 的稳定偏转,不再提前回中。"""
        dt = max(0.0, float(dt))
        ax = clamp(float(cursor_ax) + V_DRIVE * float(body_vx),
                   -A_INPUT_MAX, A_INPUT_MAX)
        ay = clamp(float(cursor_ay) + V_DRIVE * float(body_vy),
                   -A_INPUT_MAX, A_INPUT_MAX)

        tilt_p = self._tilt_p.step(dt, self.k_tilt * ax)
        tilt_r = self._tilt_r.step(dt, self.k_tilt * ay)
        legs = []
        for i, ch in enumerate(self._legs):
            drive = -(GL[i % 3] * ax + GLAT * ay * self._leg_side[i])
            legs.append(ch.step(dt, self.s_leg * drive))
        ant_l = self._ant_l.step(dt, self.s_ant * (-ax - ANT_LAT * ay))
        ant_r = self._ant_r.step(dt, self.s_ant * (-ax + ANT_LAT * ay))

        # settle:加速中 → 指数降 0;输入归零(≈低速匀速)→ 指数升 1(τ≈0.4s)
        moving = (ax * ax + ay * ay) > A_IDLE * A_IDLE
        if dt > 0.0:
            tau = TAU_SETTLE_DOWN if moving else TAU_SETTLE_UP
            tgt = 0.0 if moving else 1.0
            self._settle = tgt + (self._settle - tgt) * math.exp(-dt / tau)
        return DragPose(float(tilt_p), float(tilt_r), legs, (ant_l, ant_r),
                        float(self._settle))

    def reset(self) -> None:
        """状态清零(瞬移/重新抓取时由调用方决定是否调用)。"""
        for ch in (self._tilt_p, self._tilt_r, self._ant_l, self._ant_r,
                   *self._legs):
            ch.x = 0.0
            ch.v = 0.0
        self._settle = 0.0


# 物种质量(g):蟑螂=任务书基准 0.8g(来源注释见 M_REF_G);
# 果蝇=~1mg【docs/references/形态学权威_黑腹果蝇.md §4】。
_SPECIES_MASS_G = {"cockroach": 0.8, "fruitfly": 0.001}


def species_dynamics(species: str) -> DragDynamics:
    """按物种构建拖拽动力学('cockroach'≈0.8g、'fruitfly'≈0.001g,冻结接口)。

    r10 集成修复:App 侧 species_id 形如 'species.cockroach' /
    'species.fruitfly-dc34'(带变体后缀),这里做子串归一,两种写法都收;
    真未知物种仍报错(实机验收抓到的 ValueError 根因=只认短键)。"""
    s = str(species)
    key = ("cockroach" if "cockroach" in s
           else "fruitfly" if "fruitfly" in s else s)
    try:
        m = _SPECIES_MASS_G[key]
    except KeyError:
        raise ValueError(
            f"未知物种 {species!r};可用:{sorted(_SPECIES_MASS_G)}") from None
    return DragDynamics(m)
