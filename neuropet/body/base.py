"""通用昆虫身体:运动状态机 + 三角步态 + 三关节腿 IK + 飞行状态机。

升级要点(对照 docs/references/locomotion_昆虫运动学调研.md 与
docs/references/行为学_美洲大蠊与果蝇.md《桌宠参数速查表》):
- 腿:LegKinematics 三关节(coxa yaw / femur / tibia)闭式 IK,关节角硬限位;
- 步态:世界钉足(body/gait.py,参考 deskbug body.py:253-323)——支撑相足端
  钉在世界坐标不动,足端偏离静息位超阈值才迈步;三角步态组(A={R3,L1,R2} /
  组 B={L3,R1,L2})的摆动窗作为组间相位约束,同组足同时摆动;
- 逃逸:收到 ESCAPE 先**急停僵住**(蟑螂 0.15~0.55s / 果蝇 0.08~0.20s 随机,
  "惊觉"节奏,文献与 deskbug brain.py:275;僵住期身体静止、触角仍动),
  随后背风转身(角速度 1500~3000°/s,目标角加**强度相关抖动**:强刺激
  ±10° 保背风反射可靠 Levi 2000,弱刺激放大到 ±45° Camhi 1978 随机化),
  转身完成后冲刺 0.3~0.5s(观赏截断 ≤1500px/s,体长 115px;真实 25~50 BL/s
  见行为学规范文档)→ 减速警戒(触角高频摆动 2~5s);
- 果蝇飞行:FlightStateMachine GROUNDED→TAKEOFF(先跳 4~8px+俯仰,后开翅)
  →FLY(巡航+saccade)→LAND(减速→触地收翅);飞行中 ESCAPE 直接加速逃逸;
- EAT/GROOM 专属小动作(口器/前足);mode/activity 语义与占位版一致
  (App 依赖 activity==EAT 判进食,依赖 mode==FLY 提帧)。

pose 契约(contracts):half/altitude/segments/legs/antennae/wings 键与占位版
完全兼容;新增键:pitch(俯仰,度)、alert(警戒,触角高频)、sprint(冲刺中)、
drag(拖拽动量滞后)。
"""
from __future__ import annotations

import math
import os
import random
import time

from neuropet.body.flight import FlightStateMachine, FlightState
from neuropet.body.gait import TripodGait
from neuropet.body.gait_profile import load_params
from neuropet.body.kinematics import LegKinematics
from neuropet.body.poses import PoseLibrary
from neuropet.body.rig import MicroElastic, SkeletonSpec
from neuropet.core.contracts import (Behavior, BehaviorCommand, MovementMode,
                                     PetState)
from neuropet.core.mathutil import clamp, dist, wrap_angle
from neuropet.core.world import WorldView

# ---- 逃逸/观赏截断常量(详见 docs/蟑螂果蝇行为学规范.md 对照表) ----
ESCAPE_TURN_RATE_DPS = 2200.0   # 逃逸偏航角速度(°/s),文献 1500~3000,取中值
ESCAPE_TURN_RATE_RANGE = (1500.0, 3000.0)
ESCAPE_TURN_MAX_S = 0.15        # 转身阶段上限(文献:~62ms 内完成初始转向)
ESCAPE_SPRINT_CAP = 1500.0      # 冲刺观赏截断(px/s;真实 25~50 BL/s ≈ 2900~5800)
ESCAPE_SPRINT_DUR = (0.3, 0.5)  # 冲刺爆发时长(s,跑约 3~10 BL)
ESCAPE_DECEL_T = 0.2            # 冲刺后减速时长(s)
ALERT_TIME = 3.0                # 逃逸后警戒时长(s,触角高频摆动 2~5s)
FLY_ESCAPE_CAP = 1100.0         # 果蝇飞行观赏截断上限(px/s,ADR-005:900~1100)
# v0.2.0 速度倍率(v0.2.0 全局"爬行速度"):所有速度类参数按 **基准 × 倍率**
# 一次算出,不累积乘法(每次滑杆只从 `_p_base` 重算)。倍率上限 8×,但保留
# **绝对安全上限**:dt 抖动(休眠恢复/长帧)时单帧位移也远小于屏宽,宠物不会
# 一次"穿屏",也不可能撞进任务栏。ABS 上限取 ≈45 BL/s,仍在美洲大蠊文献
# 冲刺区间(25~50 BL/s)之内。
SPEED_MULT_MIN = 0.5
SPEED_MULT_MAX = 8.0
ABS_SPEED_MAX = 5200.0          # 任何路径下的硬上限(px/s)
# 闭合风压对逃速的加成:风压 0 时**完全等于改动前**的 `min(sprint,cap)×intensity`
# ,风压满时 ×(1+该系数)。这样"更强的逼近 ⇒ 更快的逃逸"是单调的,又不会让
# 日常逃逸整体变慢(身体留白口径见 core.desktop.margin_for)。
ESCAPE_PRESSURE_GAIN = 0.6
GLIDE_SPEED_FLOOR = 0.75        # 滑翔型前进速度下限(fly_speed 的比例):滑翔不能失速悬停
# ---- 逃逸急停僵住(文献与 deskbug brain.py:275:_freeze_first=0.15~0.55s) ----
ESCAPE_FREEZE_S = (0.15, 0.55)  # 默认档(蟑螂);果蝇经 params["escape_freeze_s"] 用短档
# ---- 逃逸方向抖动:强刺激 ±10°(保背风反射可靠,Levi 2000)→ 弱刺激 ±45°
# (尾须介导逃逸的角度随机化,Camhi 1978;deskbug brain.py:269-270 用满档 ±45°) ----
ESCAPE_JITTER_MIN_DEG = 10.0
ESCAPE_JITTER_MAX_DEG = 45.0
# ---- F4 转向契约(骨架重构波):_turn_toward 双段控制 ----
# 脑-身转向协议(Agent-4 两态 run-and-turn 落地后的实际接口):脑以
# "前导角"表达期望角速度——aim = h + ω·dt + 微摆,每帧重建;身体必须
# **全量执行**该前导角(≤ ω_max·dt + MICRO_AIM_MAX,协议带),否则期望
# ω 被衰减 k·dt 倍(60fps 下 ×1/12),弧线/微摆纹理全部失效。
# 因此 body 侧为**双段**:
#   协议带内(|delta| ≤ TURN_LEAD_BAND):全量执行=精确跟随脑指令 ω;
#   超出协议带:F4 比例控制(角速度 = gain×角误差,限幅 rate,τ≈0.2s)——
#   持续大角误差指数收敛而非被单帧全额执行(历史转圈根因链的 body 侧
#   防御;新脑已以 MICRO_AIM_MAX/ARC_OMEGA/STEER_OMEGA_MAX 在源头封顶)。
# 逃逸反射转身:饱和增益(大误差即刻打满 esc_rate)——背风反射是弹道级
# 翻身(文献 1500~3000°/s),保留速率饱和语义。
TURN_GAIN = 5.0                 # 比例段增益(1/s),τ_turn≈0.2s(冻结 0.15~0.25)
TURN_LEAD_BAND = 0.052          # 协议带(rad ≈3°):≥ ω_max·dt(0.85×1/60=0.014)
                                # + 航向微摆限幅(±1.7°=0.030),60fps 口径
ESCAPE_TURN_GAIN = 60.0

# ---- 拖拽动量滞后通道(A4 拖拽动量物理波;《拖拽物理_翻面_滑翔调研.md》§1)----
# app._on_drag 直接覆写 state.pos,body 被动观测(零 app 改动):每帧用
# "原始位移 − 上一帧自身位移" 估计外拖速度 → EMA(α=0.35,60fps 口径)→
# 二阶弹簧-阻尼滞后(ω_n≈18.8 rad/s≈3Hz、ζ=0.7 欠阻尼,半隐式欧拉)→
# 经 pose() 新增可选键 "drag" 下发渲染(与 "bones" 同款加法通道,渲染侧
# 把腿链绕髋旋转、触须鞭状滞后)。非拖拽态通道保持精确零、pose() 不含
# "drag" 键(零开销路径;非拖拽输出与现状逐位一致,冻结面零破坏)。
DRAG_EMA_ALPHA = 0.35      # 拖拽速度 EMA 系数(帧间;调研 §1.2)
DRAG_OMEGA_N = 18.8        # 滞后二阶环节自然频率(rad/s ≈3Hz;响应 ~80ms)
DRAG_ZETA = 0.7            # 阻尼比(欠阻尼,松手过冲 1 个回摆)
DRAG_FULL_V = 1500.0       # 满幅拖拽速度(px/s;= 冲刺观赏截断,幅值线性归一)
DRAG_MAX_DEG = 35.0        # 腿滞后角满幅(°;v=1500px/s → 35°,偏摆包络内留余量)
DRAG_THETA_MAX_DEG = 60.0  # 滞后角向量硬钳位(°;调研 §1.2 |θ|≤60°)
DRAG_DETECT_V = 60.0       # 外拖速度判定门(px/s;低于此视为未被拖)
DRAG_LEG_GAIN = (1.0, 0.85, 0.75)  # 前/中/后三对足增益(后足拖尾感)
DRAG_ANT_GAIN = 0.6        # 触须基端增益(相对腿;基端钳 ±20°)
DRAG_ANT_BASE_MAX = 20.0
DRAG_ANT_TIP_RATIO = 1.5   # 触须梢端幅度比 ×1.5(鞭状,梢端钳 ±30°)
DRAG_ANT_TIP_TAU = 0.08    # 梢端一阶再滞后时间常数(s;50% 延迟 ≈55ms ∈ 40~80ms)
DRAG_ANT_TIP_MAX = 30.0

# ---- 情绪 → 步态泄漏(单元 B2;R5)---------------------------------------------
# 内部状态从行为泄漏(无 UI):hunger → 步幅↓ / fear → 停顿↑、触角下垂角↑ /
# arousal → 触角扫频↑。三条通道**各自阈值化**(smoothstep 斜坡,阈值以下精确 0)
# → 默认情绪态(hunger 0.35 / fear 0.0 / arousal ≈0.09)整条通道惰性,既有
# 路径零改动;`NEUROPET_EMO_GAIT=off` 亦恒惰性(= 改动前行为,无破坏性副作用)。
# **状态无锁存**(r25 B2 修复 1):阈下帧把通道状态写回中性 ⇒ 通道状态只依赖
# 当前情绪值,任意情绪序列(跨阈上下多次、`clear_memory` 跳变)之后切 off 均
# 逐位回到改动前行为。**映射只在非逃逸分支生效**(修复 2):逃逸序列中性化
# 步长缩放,冲刺步频不被饥饿通道拉起(见 `_apply_escape`)。
# 幅度约束:步长只经 gait.stride_scale 削 s=v/hz,步频/duty 查表自变量还原为
# 驱动速度 → 节奏与占空带不动(test_acceptance_final hz∈[3,8]、duty∈[0.42,0.50])。
EMO_HUNGER_LO = 0.45        # 饥饿阈值(低于此 = 无能量约束)
EMO_HUNGER_MAX = 0.25       # 满饥饿时步长/地面速度上限削减(25%)
EMO_FEAR_LO = 0.70          # 恐惧阈值(R5 判据同值:fear ≥0.7)
EMO_FEAR_STOP_MAX = 0.60    # 满恐惧时"停顿窗"占空比上限
EMO_FEAR_BRAKE_K = 6.0      # 停顿制动率(×accel;与逃逸"惊觉僵住"同率)
EMO_DROOP_RAD = 0.45        # 满恐惧时触角下垂(俯视=外摊)附加角(rad)
EMO_AROUSAL_LO = 0.40       # 唤醒阈值
EMO_SWEEP_GAIN = 0.60       # 满唤醒时触角扫频增益(+60%)
EMO_VIG_CYCLE_S = (0.85, 1.25, 1.00)   # 停顿节律周期(s;三值轮转,非等周期)

# ---- v0.2.0 边界提前减速(朝可用边界走时压速,墙线截断只作最后安全网) ----
EDGE_LOOK = 72.0         # 沿期望方向的探距(px)
EDGE_SLOWDOWN = 0.45     # 探出可用区时的目标速度系数


def emo_ramp(x: float, lo: float) -> float:
    """阈值以上 0→1 的 smoothstep 斜坡(纯函数;判据与运行时同口径复用)。

    x ≤ lo → 0;单调、连续、|d/dx| 有界(C1,无跳变);NaN/±inf → 0
    (脏值当 0,与本项目"脏值→0"契约一致)。"""
    if not (x > lo):
        return 0.0
    t = (x - lo) / (1.0 - lo)
    if t >= 1.0:
        return 1.0
    return t * t * (3.0 - 2.0 * t)


def emo_gait_on() -> bool:
    """情绪→步态通道总开关(默认开;`NEUROPET_EMO_GAIT=off` = 改动前行为)。

    逐调用读 env(与 NEUROPET_LEG3D 同惯例)→ 测试/诊断可即时切换。关闭语义
    是"停止调制",**不做任何破坏性动作**(不删数据、不写档案;见架构 §20)。"""
    return os.environ.get("NEUROPET_EMO_GAIT", "1").strip().lower() \
        not in ("0", "false", "off")


def ant_phase_integ_on() -> bool:
    """触角相位积分开关(r26 U2;默认关=逐字节旧行为)。

    逐调用读 env(与 emo_gait_on 同惯例)→ 测试/诊断可即时切换。只切计算
    路径,无状态落盘(回退开关安全要求)。"""
    return os.environ.get("NEUROPET_ANT_PHASE_INTEG", "0").strip().lower() \
        in ("1", "true", "on")


def _cursor_pressure(world, pos) -> float:
    """闭合风压读数(0..1):感知层同一口径,**只读世界快照里已算好的光标运动学**。

    v0.2.0:不新增任何鼠标采样 —— ``WorldView.cursor`` 是
    ``perception.mouse.update_kinematics`` 每帧写好的同一实例,这里只是把
    "鼠标速度向量在鼠标→宠物方向上的正投影"换算成 0..1。取不到(合成
    快照/无光标)返回 0,行为退回改动前的下限。
    """
    try:
        from neuropet.perception.mouse import closing_pressure
        return closing_pressure(world.cursor, pos)
    except Exception:
        return 0.0


def drag_lag_step(d, dd, tgt, dt: float, omega_n: float = DRAG_OMEGA_N,
                  zeta: float = DRAG_ZETA):
    """拖拽滞后二阶环节单步(分量式纯函数;测试/探针与运行时同口径复用)。

    θ̈ = −ω_n²·(θ−φ_t) − 2ζω_n·θ̇,半隐式欧拉(先积分速度再积分位置)。
    d/dd:2 分量滞后"角向量"(度,体轴系)及其导数;tgt:目标角向量
    (被拖 = 幅值×拖速反方向单位向量,松手 = 原点 → 同一方程自由衰减)。"""
    w2 = omega_n * omega_n
    c = 2.0 * zeta * omega_n
    nddx = dd[0] + (-(w2 * (d[0] - tgt[0])) - c * dd[0]) * dt
    nddy = dd[1] + (-(w2 * (d[1] - tgt[1])) - c * dd[1]) * dt
    return [d[0] + nddx * dt, d[1] + nddy * dt], [nddx, nddy]


class GenericInsectBody:
    """由 species 参数驱动的通用六足身体。

    参数缺失回退:escape_turn_rate_deg(默认 2200)、escape_sprint_cap(1500)、
    stride_lift、antenna_segments 等新键均有默认值;既有键(segments/legs/
    antenna_base/antenna_len/stride_len/stride_amp/cruise/sprint/fly_speed/
    fly_altitude/turn_rate/accel/window_half/body_len)语义不变。
    """

    def __init__(self, state: PetState, params: dict) -> None:
        self.state = state
        self.p = params
        # v0.2.0:速度类参数的**基准快照**。``set_speed_multiplier`` 每次都从
        # 这里重算,不做 "当前值 ×m" —— 后者在滑杆反复升降时会让参数指数级
        # 漂移。非速度键(几何、步态 profile、escape_freeze_s)不受影响。
        self._p_base = {k: params[k] for k in
                        ("cruise", "sprint", "fly_speed", "accel",
                         "escape_sprint_cap") if k in params}
        # 不飞物种没有 escape_sprint_cap 键(``_apply_escape`` 取模块常量),
        # 必须把它补进基准快照:否则 8× 时 sprint 涨到 12000 而 cap 还是
        # 1500,min() 又把倍率吃掉 —— 这正是"改了 sprint 却跑不起来"的根因。
        self._p_base.setdefault("escape_sprint_cap", ESCAPE_SPRINT_CAP)
        self._speed_mult = 1.0
        self._fly_cap = FLY_ESCAPE_CAP        # 随倍率缩放的飞行/逃逸上限
        self._bounds_now: tuple | None = None  # 本帧可用矩形(见 apply/_bounds)
        self._speed = 0.0                     # 当前速度 px/s
        self._turn_norm = 0.0                 # 归一化转向强度(供步态内外侧差速)
        self._last_heading = state.heading
        self._pivot_t: float | None = None    # r24:撞墙枢转目标朝向(None=空闲)
        self._wander_target: tuple[float, float] | None = None
        self._cmd: BehaviorCommand | None = None
        self._eat_anim = 0.0
        self._groom_anim = 0.0
        self._ant_t = random.uniform(0.0, 10.0)      # 触角摆动绝对时间(s)
        # r26 U2:触角积分相位(与 _ant_t 并存;_ant_t 不删,尾摆仍用它)
        self._ant_phase = 0.0                # 主扫积分相位(rad)
        self._ant_flutter_phase = 0.0        # 8Hz 颤动积分相位(rad)
        self._alert_t = 0.0                          # 警戒剩余时间(s)
        # 腿:IK 求解器与参数一一对应(不修改 params 原条目)。
        # v3/AG5:scale_params 只缩放旧长度键,LEN3D 缩放经私有键 "_scale_k"
        # 注入(species PARAMS 不携带;k=1 恒等,见 kinematics.py 模块头)。
        try:
            _sk = clamp(float(params.get("scale", 1.0)), 0.05, 8.0)
        except (TypeError, ValueError):
            _sk = 1.0
        self._legs = [dict(leg, _scale_k=_sk) for leg in params["legs"]]
        self._kin = [LegKinematics(leg) for leg in self._legs]
        self._gait = TripodGait(params)
        # ---- GaitProfile 注入(F2 冻结点):params["gait_species"] 存在 →
        # 查 data/gait/<species>_*.json → 默认文件 → 内置默认;缺失/损坏
        # 全链回退,行为与未注入一致。
        self._species_key = str(params.get("gait_species", "")) or None
        self._gait_params: dict = {}
        if self._species_key:
            self._gait_params = load_params(self._species_key)
            self._gait.apply_profile(self._gait_params)
        self._gait.bind_legs(self._kin, [int(leg["group"]) for leg in self._legs])
        self._gait.reset(state.pos, state.heading)    # 初始种脚(标准站姿)
        # 跗节世界朝向锚定(R2 解剖修正):支撑相跗节贴地、世界朝向冻结
        # (真实昆虫跗节触地后不随腿系平面旋转);触地帧取髋→足世界方向,
        # 离地摊清(摆动相随腿系平面)。None = 未锚定。
        self._tarsus_yaw_w: list[float | None] = [None] * len(self._legs)
        # ---- 骨架层(M1):SkeletonSpec(F1)+ 姿态库 + 微弹性 ----
        self._rig = SkeletonSpec.from_params(self._species_key or "generic", params)
        self._poses = PoseLibrary()
        self._micro = MicroElastic(6)            # τ/下沉角经 GaitProfile 注入(下)
        if self._gait_params:
            self._micro.set_profile(self._gait_params.get("sag_tau_ms", 60.0) / 1000.0,
                                    self._gait_params.get("sag_max_deg", 4.0))
        self._sway_amp_px = float(self._gait_params.get("sway_amp_px", 0.0))
        self._sway_freq_ratio = float(self._gait_params.get("sway_freq_ratio", 1.0))
        self._pitch_amp_deg = float(self._gait_params.get("pitch_amp_deg", 0.0))
        self._pose_name = "rest"
        self._flyer = float(params.get("fly_speed", 0)) > 0 and \
            float(params.get("fly_altitude", 0)) > 0
        self._flight = FlightStateMachine(params) if self._flyer else None        # 内部逃逸状态机:turn → sprint → decel(感知同帧,当帧启动转身)
        self._esc: dict | None = None
        self._esc_rate = math.radians(clamp(
            float(params.get("escape_turn_rate_deg", ESCAPE_TURN_RATE_DPS)),
            *ESCAPE_TURN_RATE_RANGE))
        self._dt = 1.0 / 60.0                 # 上一帧 apply 的 dt(微弹性用)
        # ---- 拖拽动量滞后通道(被动观测 state.pos 的外部位移;模块头注) ----
        self._drag_prev = (float(state.pos[0]), float(state.pos[1]))
        self._drag_self_move = (0.0, 0.0)     # 上一帧自身位移(px;世界系)
        self._drag_v = [0.0, 0.0]             # 外拖速度 EMA(世界系 px/s)
        self._drag_d = [0.0, 0.0]             # 滞后角向量(度,体轴系)
        self._drag_dd = [0.0, 0.0]            # 其导数(度/s)
        self._drag_d_ant = [0.0, 0.0]         # 触须梢端再滞后状态(度,体轴系)
        self._drag_on = False                 # pose() 是否携带 "drag" 键
        # ---- 松手抛掷(ADR-0032):None=无抛掷(逐位一致路径) ----
        self._fling: list[float] | None = None
        # ---- 情绪 → 步态通道(B2;默认惰性,唯一写入口 set_emotion_gait)----
        self._emo_speed = 1.0        # 步长/地面速度缩放(hunger;1.0 = 不缩放)
        self._emo_fear = 0.0         # 恐惧斜坡 0..1(停顿窗 + 触角下垂)
        self._emo_arousal = 0.0      # 唤醒斜坡 0..1(触角扫频)
        self._emo_vig_t = 0.0        # 停顿节律时钟(s;仅行进分支推进)
        self._emo_vig_i = 0          # 节律周期轮转下标(确定性,不掷 RNG)
        # ---- 位置权威帧标记(ADR-0033 D4):HideTimeline 等编排权威搬动
        # 身体的帧,外拖观测归零(躲藏行走≠被拖拽;灭转身段幽灵速度) ----
        self._pos_external = False
        # 静息足向单位轴(髋 attach → 静息足 home;渲染侧投影用,不随步态变)
        self._drag_axes = []
        for _leg in self._legs:
            _ax = _leg["home"][0] - _leg["attach"][0]
            _ay = _leg["home"][1] - _leg["attach"][1]
            _n = math.hypot(_ax, _ay) or 1.0
            self._drag_axes.append((_ax / _n, _ay / _n))

    # ================= 全局速度倍率(v0.2.0) =================
    def set_speed_multiplier(self, mult: float) -> float:
        """把"用户爬行速度倍率 × 临时 buff"一次算进速度类基准参数。

        覆盖 cruise / sprint / fly_speed / accel / escape_sprint_cap 以及
        飞行逃逸上限(``FLY_ESCAPE_CAP`` 原来是模块常量,8× 时会被它钳死 → 本轮
        改为按倍率缩放的实例值,保证 8× 真的跑得出来)。**不重建 body**,
        因此 ``_speed``、步态相位、钉足世界坐标全部原地保留:滑杆升降是
        "有限加减速",不是顿挫。

        幂等:倍率不变直接返回(面板刷新、语言切换都不会重算)。
        """
        try:
            m = float(mult)
        except (TypeError, ValueError):
            return self._speed_mult
        if not (m == m):                      # NaN → 忽略
            return self._speed_mult
        m = max(SPEED_MULT_MIN, min(SPEED_MULT_MAX, m))
        if abs(m - self._speed_mult) < 1e-9:
            return self._speed_mult
        self._speed_mult = m
        p = self.p
        base = self._p_base
        for key, value in base.items():
            try:
                p[key] = float(value) * m
            except (TypeError, ValueError):
                continue
        self._fly_cap = FLY_ESCAPE_CAP * m
        # 当前速度不跳变:交给各自的加速度限幅逐步跟上新上限/下限时。
        self._speed = min(self._speed, ABS_SPEED_MAX)
        return m

    @property
    def speed_multiplier(self) -> float:
        return self._speed_mult

    # ================= 情绪 → 步态(单元 B2;R5) =================
    def set_emotion_gait(self, fear: float, hunger: float,
                         arousal: float) -> None:
        """把脑内情绪装配进运动层(单向只读;无 UI 暴露)。

        调用方 = `core/app.py` 的每帧装配点(单一真源 = `brain.emotion()`)。
        三条通道全部**阈值化 + smoothstep 斜坡**(阈值以下精确 0,见模块头
        常量);阈下**同样把状态写回中性值**(不是早退不写)——通道状态只依赖
        **当前**情绪值、不依赖历史(**无锁存**,r25 B2 黑盒问题 1 修复):
        早退不写曾使情绪跌回阈下后步长/停顿窗保留上次斜坡值(实测
        hunger 0.75→0.10 后 `stride_scale` 仍 0.858002;`clear_memory` 跳变后
        恐惧通道可无限期保留停顿窗),且此时 `NEUROPET_EMO_GAIT=off` 因同一
        早退而**不生效**(与"off = 改动前逐位相同"的承诺冲突)。中性值写入
        零行为变化(×1.0 / ×0.0 逐位不变),默认情绪态仍是零开销路径。
        """
        if not (hunger > EMO_HUNGER_LO or fear > EMO_FEAR_LO
                or arousal > EMO_AROUSAL_LO):
            # 阈下:状态写回中性(无锁存);不读 env/不掷 RNG
            self._emo_speed, self._emo_fear, self._emo_arousal = 1.0, 0.0, 0.0
            self._gait.stride_scale = 1.0
            self._emo_vig_t, self._emo_vig_i = 0.0, 0   # 停顿窗关闭(节律复位)
            return
        if not emo_gait_on():
            self._emo_speed, self._emo_fear, self._emo_arousal = 1.0, 0.0, 0.0
            self._gait.stride_scale = 1.0          # 回退:恢复改动前行为
            self._emo_vig_t, self._emo_vig_i = 0.0, 0
            return
        self._emo_speed = 1.0 - EMO_HUNGER_MAX * emo_ramp(hunger, EMO_HUNGER_LO)
        self._emo_fear = emo_ramp(fear, EMO_FEAR_LO)
        self._emo_arousal = emo_ramp(arousal, EMO_AROUSAL_LO)
        # 步长缩放交给步态(唯一消费点):把"步距锚 s=v/hz"与查表自变量同一处
        # 还原,步频/duty 因此不动(边界:gait.stride_scale 只被本方法与
        # _apply_escape 的中性化写,后者见其注释)
        self._gait.stride_scale = self._emo_speed

    def _emo_halt(self, dt: float) -> bool:
        """恐惧停顿窗(B2):reflex 级"停-看"覆盖行进意图 → 本帧不行进。

        节律用 EMO_VIG_CYCLE_S 三值轮转(**确定性**,不掷 RNG——判据要可复现,
        且不扰动全局随机流);停顿窗占比 = EMO_FEAR_STOP_MAX × 恐惧斜坡。制动
        率与逃逸"惊觉僵住"同(accel×6):急停即六足立定,相位冻结与收腿由
        gait 的静止分支接管。仅被地面行进(EXPLORE/SEEK_FOOD)分支查询——
        逃逸/进食等反射优先,不受本通道影响。
        """
        if self._emo_fear <= 0.0:
            return False
        cyc = EMO_VIG_CYCLE_S[self._emo_vig_i % len(EMO_VIG_CYCLE_S)]
        self._emo_vig_t += dt
        if self._emo_vig_t >= cyc:
            self._emo_vig_t -= cyc
            self._emo_vig_i += 1
            cyc = EMO_VIG_CYCLE_S[self._emo_vig_i % len(EMO_VIG_CYCLE_S)]
        if self._emo_vig_t >= EMO_FEAR_STOP_MAX * self._emo_fear * cyc:
            return False
        self._speed = max(0.0, self._speed
                          - self.p["accel"] * EMO_FEAR_BRAKE_K * dt)
        return True

    # ================= 命令处理 =================
    def apply(self, cmd: BehaviorCommand, world: WorldView, dt: float) -> None:
        st = self.state
        self._dt = max(1e-4, float(dt))
        self._cmd = cmd
        # 本帧可用矩形算一次(转向减速与积分软墙共用;Win32 work area 由
        # DesktopArea 自己按 2s 节流,这里不做任何系统查询)。
        self._bounds_now = self._bounds(world)
        b = cmd.behavior
        st.activity = b                       # activity 语义与占位版一致
        if b != Behavior.EAT:
            self._eat_anim = 0.0
        if b != Behavior.GROOM:
            self._groom_anim = 0.0
        self._alert_t = max(0.0, self._alert_t - dt)
        self._last_heading = st.heading
        if b is Behavior.ESCAPE:
            self._apply_escape(cmd, world, dt)
        elif self._esc is not None:
            # 脑为权威:非 ESCAPE 命令取消内部逃逸序列(动量自然衰减)
            self._esc = None
            self._apply_ground(cmd, world, dt)
        elif b in (Behavior.EXPLORE, Behavior.SEEK_FOOD):
            # B2:恐惧停顿窗(reflex 级覆盖;命中则本帧不行进)。放在逃逸取消
            # 分支之后,逃逸簿记(_esc)不受影响;触角/微动作仍照常推进。
            if not self._emo_halt(dt):
                self._apply_ground(cmd, world, dt)
        elif b is Behavior.TAKEOFF:
            self._apply_takeoff(cmd, world, dt)
        elif b is Behavior.FLY_WANDER:
            self._apply_fly(cmd, world, dt)
        elif b is Behavior.LAND:
            self._apply_land(cmd, world, dt)
        else:  # IDLE / GROOM / EAT / REST / TURN
            self._apply_idleish(cmd, world, dt)

        self._integrate(dt, world)

    # ---- 地面行走(探索/觅食) ----
    def _apply_ground(self, cmd: BehaviorCommand, world: WorldView, dt: float) -> None:
        tgt = cmd.target or self._wander(world)
        # B2:饥饿削步长(× _emo_speed;步频/duty 由驱动速度查表 → 节奏不动)。
        # 逃逸冲刺不受此限(生存反射优先于能耗,见 set_emotion_gait)。
        spd = self.p["cruise"] * clamp(cmd.intensity, 0.4, 1.0) * self._emo_speed
        self._steer_toward(tgt, spd, world, dt)

    # ---- 逃逸(尾须反射级:当帧启动转身) ----
    def _apply_escape(self, cmd: BehaviorCommand, world: WorldView, dt: float) -> None:
        st = self.state
        # B2(修复 2):情绪步长映射**限定在非逃逸分支**——逃逸/冲刺期间中性化
        # 步长缩放,使 `step_hz/duty` 的查表自变量(`v/stride_scale` 还原)就是
        # 真实速度,不被饥饿通道拉起(黑盒实测:冲刺期 hz 11.000→13.303,与
        # `_apply_ground` 注释"逃逸冲刺不受此限"矛盾;逃逸速度本就不乘
        # `_emo_speed`,这里补上同一承诺的另一半)。下一帧装配点会按当前情绪
        # 重新写回 `_emo_speed`(见 `set_emotion_gait`)。
        self._gait.stride_scale = 1.0
        if self._flight and (self._flight.flying or
                             self._flight.state is not FlightState.GROUNDED):
            # 飞行中逃逸:直接加速(状态机增益 ×1.3),朝背向目标转向;
            # 落地后进入警戒(触角高频),与地面逃逸的警戒语义一致
            self._flight.escape()
            self._alert_t = ALERT_TIME
            if cmd.target:
                desired = math.atan2(cmd.target[1] - st.pos[1],
                                     cmd.target[0] - st.pos[0])
                rate = max(self._esc_rate, math.radians(720.0))
                st.heading = self._turn_toward(st.heading, desired, rate, dt,
                                               gain=ESCAPE_TURN_GAIN)
            cap = min(self._fly_cap, self.p["fly_speed"] * self._flight.speed_gain())
            self._speed += (cap - self._speed) * min(1.0, 4.0 * dt)
            self._speed = min(self._speed, ABS_SPEED_MAX)
            return
        # 地面逃逸:FREEZE(急停僵住)→ TURN_AWAY → SPRINT → DECEL(调研 §5 状态机)
        if self._esc is None:
            desired = math.atan2(cmd.target[1] - st.pos[1],
                                 cmd.target[0] - st.pos[0]) if cmd.target \
                else st.heading + math.pi
            # 逃逸方向随机化:强刺激小抖动(±10°,保背风反射可靠,Levi 2000
            # 的风向编码)→ 弱刺激大角度(±45°,Camhi 1978 尾须介导逃逸随机化,
            # 防"永远直线逃逸";deskbug brain.py:269-270 恒用 ±45°)
            inten = clamp(float(getattr(cmd, "intensity", 1.0)), 0.0, 1.0)
            jmax = math.radians(ESCAPE_JITTER_MIN_DEG + (1.0 - inten) *
                                (ESCAPE_JITTER_MAX_DEG - ESCAPE_JITTER_MIN_DEG))
            jitter = random.uniform(-jmax, jmax)
            fz_lo, fz_hi = self.p.get("escape_freeze_s", ESCAPE_FREEZE_S)
            self._esc = {"phase": "freeze", "t": 0.0, "freeze": random.uniform(fz_lo, fz_hi),
                         "desired": desired + jitter, "jitter": jitter,
                         "dur": random.uniform(*ESCAPE_SPRINT_DUR)}
        self._alert_t = ALERT_TIME
        e = self._esc
        e["t"] += dt
        if e["phase"] == "freeze":
            # 急停僵住:身体静止(速度快速衰减,不加位移动作);触角仍动
            # (_ant_t 在 _integrate 中恒推进)——逃逸的"惊觉"节奏,
            # 文献与 deskbug brain.py:275(_freeze_first=0.15~0.55s)一致
            self._speed = max(0.0, self._speed - self.p["accel"] * 6.0 * dt)
            if cmd.target:   # 刺激持续期间持续重瞄(风向更新,抖动项保持)
                e["desired"] = math.atan2(cmd.target[1] - st.pos[1],
                                          cmd.target[0] - st.pos[0]) + e["jitter"]
            if e["t"] >= e["freeze"]:
                e["phase"] = "turn"
                e["t"] = 0.0
        elif e["phase"] == "turn":
            if cmd.target:   # 刺激持续期间允许重瞄(风向更新,抖动项保持)
                e["desired"] = math.atan2(cmd.target[1] - st.pos[1],
                                          cmd.target[0] - st.pos[0]) + e["jitter"]
            st.heading = self._turn_toward(st.heading, e["desired"], self._esc_rate,
                                           dt, gain=ESCAPE_TURN_GAIN)
            self._speed = max(0.0, self._speed - self.p["accel"] * 0.8 * dt)
            if abs(wrap_angle(e["desired"] - st.heading)) < math.radians(14.0) or \
                    e["t"] > ESCAPE_TURN_MAX_S:
                e["phase"] = "sprint"
                e["t"] = 0.0
        elif e["phase"] == "sprint":
            cap = float(self.p.get("escape_sprint_cap", ESCAPE_SPRINT_CAP))
            # v0.2.0:风压持续越强 → 逃速越高。驱动力 = 脑给的刺激强度 ×
            # (1 + 0.6×闭合风压):风压 0 时逐位等于改动前口径(不把日常逃逸
            # 整体变慢),风压满时 ×1.6;速度仍受 accel 限幅 ⇒ 鼠标停下后
            # 平滑回落,不突变。
            drive = clamp(float(getattr(cmd, "intensity", 1.0)), 0.6, 1.0) * \
                (1.0 + ESCAPE_PRESSURE_GAIN * _cursor_pressure(world, st.pos))
            target_v = min(self.p["sprint"], cap) * drive
            self._speed += clamp(target_v - self._speed,
                                 -self.p["accel"] * 2.5 * dt, self.p["accel"] * 2.5 * dt)
            if e["t"] >= e["dur"]:
                e["phase"] = "decel"
                e["t"] = 0.0
        else:  # decel:冲刺 3~10 BL 后减速警戒
            self._speed = max(self.p["cruise"] * 0.3,
                              self._speed - self.p["accel"] * 2.0 * dt)
            if e["t"] >= ESCAPE_DECEL_T:
                self._esc = None

    # ---- 起飞(果蝇:先跳后开翅) ----
    def _apply_takeoff(self, cmd: BehaviorCommand, world: WorldView, dt: float) -> None:
        if self._flight:
            self._flight.start_takeoff()
            # 蹬跳阶段腿推前冲(真实初速 0.5~1.5 m/s 的观赏近似)
            push = min(self.p["fly_speed"] * 0.25, self._speed + 600 * dt)
            self._speed += (push - self._speed) * min(1.0, 6.0 * dt)
        else:
            self._apply_ground(cmd, world, dt)        # 不飞物种回退为行走

    # ---- 飞行漫游(saccade + 悬停微调) ----
    def _apply_fly(self, cmd: BehaviorCommand, world: WorldView, dt: float) -> None:
        if not self._flight:
            self._apply_ground(cmd, world, dt)
            return
        if self._flight.state is FlightState.GROUNDED:
            self._flight.start_takeoff()               # 脑直接要求飞行 → 自动起飞
        st = self.state
        st.heading = wrap_angle(st.heading + self._flight.yaw_rate * dt)
        st.heading = wrap_angle(st.heading + random.uniform(-0.5, 0.5) * dt)
        target_v = self.p["fly_speed"] * clamp(cmd.intensity, 0.5, 1.0) * \
            self._flight.speed_gain()
        target_v = min(target_v, self._fly_cap)
        if getattr(self._flight, "glide", False):
            # 滑翔型不能悬停:无论 intensity 如何,前进速度保持 ≥ fly_speed×0.75
            target_v = max(target_v, self.p["fly_speed"] * GLIDE_SPEED_FLOOR)
        self._speed += (target_v - self._speed) * min(1.0, 2.5 * dt)
        self._speed = min(self._speed, ABS_SPEED_MAX)

    # ---- 降落(减速进近 → 触地收翅) ----
    def _apply_land(self, cmd: BehaviorCommand, world: WorldView, dt: float) -> None:
        if self._flight and self._flight.state is not FlightState.GROUNDED:
            self._flight.start_land()
            self._speed = max(self._speed * (1.0 - 2.2 * dt), 0.0)
        else:
            self._decelerate(dt)

    # ---- 静止系(IDLE/GROOM/EAT/REST/TURN) ----
    def _apply_idleish(self, cmd: BehaviorCommand, world: WorldView, dt: float) -> None:
        st = self.state
        if cmd.behavior is Behavior.TURN and cmd.target:
            desired = math.atan2(cmd.target[1] - st.pos[1], cmd.target[0] - st.pos[0])
            st.heading = self._turn_toward(st.heading, desired,
                                           self.p["turn_rate"] * 1.5, dt)
        self._decelerate(dt)
        if cmd.behavior is Behavior.EAT:
            self._eat_anim += dt * 9.0                 # 口器/前足小动作
        elif cmd.behavior is Behavior.GROOM:
            self._groom_anim += dt * 5.0               # 前足擦刷触角

    # ================= 运动积分 =================
    def _bounds(self, world: WorldView) -> tuple[float, float, float, float]:
        """本宠的可用矩形 (x0, y0, x1, y1):可用桌面 ∩ 自身身体留白。

        任务栏已由 ``core.desktop`` 扣除;留白取 ``margin_for(window_half)``
        —— 与拖拽仲裁(StateArbiter → world.clamp_to_screen →
        desktop.margin_for)**同一个函数**,消除"body 说 80 / arbiter 说 60"
        互相拉扯造成的边缘瞬移。合成快照没有 ``area()/usable`` 时退回
        ``clamp_to_screen`` 的同源口径。
        """
        area = getattr(world, "area", None)
        try:
            half = float(self.p["window_half"])
        except (KeyError, TypeError, ValueError):
            half = 0.0
        margin = 0.0
        if area is not None:
            try:
                margin = float(area().margin_for(half))
            except Exception:
                margin = 0.0
        if margin <= 0.0:
            from neuropet.core.desktop import margin_for as _mf
            margin = float(_mf(half))
        usable = getattr(world, "usable", None)
        if usable is not None:
            try:
                return usable(margin)
            except Exception:
                pass
        x, y = world.clamp_to_screen(self.state.pos, margin)
        w, h = world.screen
        return (margin, margin, w - margin, h - margin)

    def _wander(self, world: WorldView) -> tuple[float, float]:
        if self._wander_target is None:
            x0, y0, x1, y1 = self._bounds(world)
            if x1 <= x0 or y1 <= y0:
                x0, y0, x1, y1 = 1.0, 1.0, max(2.0, world.screen[0] - 1.0), \
                    max(2.0, world.screen[1] - 1.0)
            self._wander_target = (random.uniform(x0, x1),
                                   random.uniform(y0, y1))
        return self._wander_target

    def _steer_toward(self, tgt: tuple[float, float], spd: float,
                      world: WorldView, dt: float) -> None:
        st = self.state
        d = dist(st.pos[0], st.pos[1], *tgt)
        desired = math.atan2(tgt[1] - st.pos[1], tgt[0] - st.pos[0])
        st.heading = self._turn_toward(st.heading, desired, self.p["turn_rate"], dt)
        arrive = clamp(d / 90.0, 0.25, 1.0)
        target_speed = spd * arrive
        # v0.2.0:朝边界走时**提前**减速,不靠墙线截断兜底(截断只是最后一道
        # 安全网)。沿期望方向探一段 EDGE_LOOK px,越界则按比例压速;这样
        # 贴边行走是"慢下来贴边",而不是"撞墙急停+枢转"。
        x0, y0, x1, y1 = self._bounds_now or self._bounds(world)
        look = EDGE_LOOK
        px = st.pos[0] + math.cos(desired) * look
        py = st.pos[1] + math.sin(desired) * look
        if px < x0 or px > x1 or py < y0 or py > y1:
            target_speed *= EDGE_SLOWDOWN
        acc = self.p["accel"] * (2.2 if target_speed > self._speed else 1.0)
        self._speed += clamp(target_speed - self._speed, -acc * dt, acc * dt)
        if d < 14:
            self._wander_target = None

    def _decelerate(self, dt: float) -> None:
        self._speed = max(0.0, self._speed - self.p["accel"] * 1.6 * dt)

    @staticmethod
    def _turn_toward(heading: float, desired: float, rate: float, dt: float,
                     gain: float = TURN_GAIN) -> float:
        """朝 desired 转动:F4 双段控制(协议带全量执行 + 带外比例趋近)。

        - |delta| ≤ TURN_LEAD_BAND:全量执行——脑以"前导角 = ω·dt(+微摆)"
          每帧重建目标,全量执行即精确跟随期望角速度(两态 run-and-turn
          的实际协议,见模块头注);
        - 带外:比例控制 step = clamp(gain·delta·dt, ±rate·dt),τ=1/gain
          ≈0.2s(冻结 0.15~0.25s)——持续大角误差指数收敛,不被单帧全额
          执行(历史转圈根因链的 body 侧防御);
        - 逃逸反射转身以 gain=ESCAPE_TURN_GAIN 调用(即刻饱和,弹道级
          翻身语义保留,rate 仍为硬上限)。
        """
        delta = wrap_angle(desired - heading)
        if abs(delta) <= TURN_LEAD_BAND:
            step = clamp(delta, -rate * dt, rate * dt)   # 协议带:全量(仍限速兜底)
        else:
            step = clamp(gain * delta * dt, -rate * dt, rate * dt)
        return wrap_angle(heading + step)

    def _integrate(self, dt: float, world: WorldView) -> None:
        st = self.state
        # r24 转向连续性:撞墙枢转。旧口径在碰墙帧把 heading 单帧瞬反(~175°,
        # 活体取帧实测:身体绕钉死足端瞬翻,姿态一次跳 ~180px,即用户报的
        # 「转向瞬变」次主因)。改为把反射角设为目标,以 PIVOT_RATE 高角速度
        # 扫过去(~0.2s/180°):位置钳制仍即时(不出屏),朝向连续旋转;枢转
        # 期角速度进 _turn_norm → 步态差速/帧档/桶回退都按真转向对待。
        if self._pivot_t is not None:
            d_ = wrap_angle(self._pivot_t - st.heading)
            st.heading = wrap_angle(
                st.heading + clamp(d_, -self.PIVOT_RATE * dt,
                                   self.PIVOT_RATE * dt))
            if abs(d_) <= self.PIVOT_RATE * dt:
                self._pivot_t = None
        # 飞行垂直通道由状态机积分(altitude 全程连续)
        if self._flight is not None:
            self._flight.update(dt, self._speed)
            st.altitude = self._flight.altitude
            st.mode = MovementMode.FLY if self._flight.flying else MovementMode.CRAWL
        else:
            st.altitude = 0.0
            st.mode = MovementMode.CRAWL
        # 转向强度(供步态内外侧差速,~90°/s 归一化)
        self._turn_norm = clamp(wrap_angle(st.heading - self._last_heading) /
                                max(dt, 1e-4) / math.radians(90.0), -1.0, 1.0)
        # 绝对安全上限(v0.2.0):任一路径(滑杆 8×、buff、抛掷)都不会超过它,
        # dt 抖动时单帧位移 = 上限 × dt ≪ 屏宽 ⇒ 不会"穿屏"。
        self._speed = min(self._speed, ABS_SPEED_MAX)
        vx = math.cos(st.heading) * self._speed
        vy = math.sin(st.heading) * self._speed
        # 松手抛掷(ADR-0032):外速度指数衰减;自足运动速度另算。fling
        # 位移**不计入** _drag_self_move → 被外拖观测看到 → 强抛自然触发
        # 翻面(抛出去仰面朝天=拟真),拖拽摆角通道亦随滑行拖尾。
        fx = fy = 0.0
        if self._fling is not None:
            fx, fy = self._fling
            if math.hypot(fx, fy) < self.FLING_STOP_V:
                self._fling = None
                fx = fy = 0.0
            else:
                k = math.exp(-dt / self.FLING_TAU)
                self._fling = [fx * k, fy * k]
        own_dx, own_dy = vx * dt, vy * dt
        nx, ny = st.pos[0] + own_dx + fx * dt, st.pos[1] + own_dy + fy * dt
        # v0.2.0 边界口径:与拖拽仲裁、面板落食、随机航点共用同一个"可用
        # 桌面"(任务栏已扣除),留白由自身画布半径导出。旧口径这里是整屏 80px
        # 常量,和仲裁的整屏 60px 不一致 —— 用户把宠物拖到屏幕下缘时会看到
        # "松手回弹一截",任务栏在别的边时更明显。
        x0, y0, x1, y1 = self._bounds_now or self._bounds(world)
        inside = x0 <= st.pos[0] <= x1 and y0 <= st.pos[1] <= y1
        if inside:
            # 自内向外穿越墙线 → 连续截断 + 枢转。截断只作用于被挡住的那
            # 一轴,另一轴的位移原样保留 ⇒ 贴边滑行,不"弹回远处"、不重置
            # 位置、不随机重掷朝向(枢转角只在本帧首触设定,见下)。
            hit_x = hit_y = False
            if nx < x0:
                hit_x = True
            elif nx > x1:
                hit_x = True
            if ny < y0:
                hit_y = True
            elif ny > y1:
                hit_y = True
            if hit_x:
                if self._pivot_t is None:   # 仅首触设定:贴墙滑行期反射角随动会漂移
                    self._pivot_t = wrap_angle(math.pi - st.heading)
                nx = min(max(nx, x0), x1)
                if self._fling is not None:
                    self._fling[0] = -self._fling[0] * 0.5   # 抛掷撞墙阻尼反弹
                if self._esc is not None:
                    self._esc = None
            if hit_y:
                if self._pivot_t is None:
                    self._pivot_t = wrap_angle(-st.heading)
                ny = min(max(ny, y0), y1)
                if self._fling is not None:
                    self._fling[1] = -self._fling[1] * 0.5
                if self._esc is not None:
                    self._esc = None
        else:
            # 已在可用区之外(工作区缩小/自动隐藏任务栏滑出/编排搬动):
            # **不单帧夹回**。只朝区内最近的合法点转向,并把速度压到巡航档 ——
            # 沿有限速度自己走回去,视觉上是"转身折返",不是瞬移。
            tx = min(max(st.pos[0], x0), x1)
            ty = min(max(st.pos[1], y0), y1)
            if abs(tx - st.pos[0]) > 1e-6 or abs(ty - st.pos[1]) > 1e-6:
                st.heading = self._turn_toward(
                    st.heading, math.atan2(ty - st.pos[1], tx - st.pos[0]),
                    self.p["turn_rate"] * 1.5, dt)
            self._speed = min(self._speed, self.p["cruise"])
        # 拖拽动量观测的自身位移记账:无外拖(fling=None)时记录**实际
        # 位移表达式**(nx−pos 的同一浮点值)→ 下帧 ivx≡0 精确归零
        # (零开销不变量);有 fling 时自足部分用推断值
        # (残差 ~1e-13 级,fling 属外拖本就该被观测到)。
        applied_dx, applied_dy = nx - st.pos[0], ny - st.pos[1]
        if self._fling is not None:
            self._drag_self_move = (own_dx, own_dy)
        else:
            self._drag_self_move = (applied_dx, applied_dy)
        st.pos = (nx, ny)
        st.speed = self._speed
        # 步态推进:仅在地面爬行(飞行时足端跟随静息位,落地即标准站姿)。
        # 逃逸分段:转身段腿仍快速交替(紧迫感);**僵住期停踏步**(Q 缺陷:
        # 躯干静止而六足继续走步与"惊觉僵住"的文献节奏自相矛盾——急停即
        # 六足立定,触角仍动)。世界钉足:足端世界坐标由 gait 维护,身体
        # 移动时支撑足相对身体后移(不滑步)。
        if st.mode is MovementMode.CRAWL:
            scramble = self._speed
            if self._esc is not None and self._esc.get("phase") == "turn":
                scramble = max(scramble, self.p["cruise"] * 0.6)
            elif self._esc is not None and self._esc.get("phase") == "freeze":
                scramble = 0.0
            self._gait.update(dt, scramble, st.pos, st.heading, self._turn_norm)
        else:
            self._gait.reset(st.pos, st.heading)
        self._ant_t += dt
        # r26 U2:积分相位每 tick 推进(恒累积,开关只切 _antennae_pose 使用路径)
        _sw_hz, _ = self._antenna_sweep_hz()
        self._ant_phase += _sw_hz * dt * 6.2831853
        self._ant_flutter_phase += 8.0 * dt * 6.2831853
        self._update_drag(dt)

    # ---- 拖拽动量滞后(被动观测;调研 §1.2 更新方程) ----
    def _update_drag(self, dt: float) -> None:
        """拖拽动量滞后通道(每帧 _integrate 末尾)。

        外拖速度 = (原始位移 − 上一帧自身位移)/dt(app 拖拽直接覆写
        state.pos,body 被动观测)→ EMA → 体轴系期望滞后角向量(方向 =
        拖速反方向、幅值随速度线性至 35° 满)→ 二阶弹簧-阻尼(半隐式欧拉)。
        松手后同一方程 φ_t→0 自由衰减(自然回摆 ~300ms)。零开销路径:
        从未被拖/已回零 → 状态精确归零,pose() 不携带 "drag" 键。
        ADR-0033 D4:_pos_external 帧(编排权威搬运,如躲藏行走/滑入)的
        位移观测强制归零——躲藏行走不是被拖拽,基线对齐防转身段幽灵速度。"""
        st = self.state
        px, py = st.pos
        ox, oy = self._drag_prev
        self._drag_prev = (px, py)
        k = DRAG_EMA_ALPHA
        if getattr(self, "_pos_external", False):
            self._pos_external = False
            self._drag_self_move = (0.0, 0.0)
            ivx = ivy = 0.0
        else:
            smx, smy = self._drag_self_move
            self._drag_self_move = (0.0, 0.0)
            ivx = (px - ox - smx) / dt     # 瞬时外拖速度(EMA 前的原始差分)
            ivy = (py - oy - smy) / dt
        self._drag_v[0] += k * (ivx - self._drag_v[0])
        self._drag_v[1] += k * (ivy - self._drag_v[1])
        vmag = math.hypot(self._drag_v[0], self._drag_v[1])
        if vmag > DRAG_DETECT_V:
            # 世界→体轴(前=+x):期望滞后角 = 拖速反方向,幅值线性至 35°
            ch, sh = math.cos(st.heading), math.sin(st.heading)
            vbx = ch * self._drag_v[0] + sh * self._drag_v[1]
            vby = -sh * self._drag_v[0] + ch * self._drag_v[1]
            amp = DRAG_MAX_DEG * min(1.0, vmag / DRAG_FULL_V)
            phi = math.atan2(-vby, -vbx)
            self._drag_d, self._drag_dd = drag_lag_step(
                self._drag_d, self._drag_dd,
                (amp * math.cos(phi), amp * math.sin(phi)), dt)
            n = math.hypot(self._drag_d[0], self._drag_d[1])
            if n > DRAG_THETA_MAX_DEG:               # |θ| ≤ 60° 硬钳位
                f = DRAG_THETA_MAX_DEG / n
                self._drag_d[0] *= f
                self._drag_d[1] *= f
        elif (math.hypot(self._drag_d[0], self._drag_d[1]) > 1e-3 or
              math.hypot(self._drag_dd[0], self._drag_dd[1]) > 1e-3):
            self._drag_d, self._drag_dd = drag_lag_step(
                self._drag_d, self._drag_dd, (0.0, 0.0), dt)
        else:
            self._drag_d[0] = self._drag_d[1] = 0.0
            self._drag_dd[0] = self._drag_dd[1] = 0.0
        # 触须梢端:对腿通道的一阶再滞后(鞭状相位延迟 40~80ms)
        kt = min(1.0, dt / DRAG_ANT_TIP_TAU)
        self._drag_d_ant[0] += (self._drag_d[0] - self._drag_d_ant[0]) * kt
        self._drag_d_ant[1] += (self._drag_d[1] - self._drag_d_ant[1]) * kt
        self._drag_on = (self._drag_d[0] * self._drag_d[0] +
                         self._drag_d[1] * self._drag_d[1]) > 2.5e-3

    # ================= 姿态 =================
    def pose(self) -> dict:
        st = self.state
        cos_h, sin_h = math.cos(st.heading), math.sin(st.heading)

        def to_screen(lx: float, ly: float) -> tuple[float, float]:
            return (lx * cos_h - ly * sin_h, lx * sin_h + ly * cos_h)

        # 姿态库推进:行为/状态 → 目标姿态 → crossfade(集中一处,帧率无关)
        frozen = self._esc is not None and self._esc.get("phase") == "freeze"
        self._pose_name = PoseLibrary.pick(
            st.activity, st.mode, st.mode is MovementMode.FLY,
            self._alert_t > 0.0, frozen,
            speed_norm=self._speed / max(1.0, self.p["cruise"]))
        self._poses.set_target(self._pose_name)
        self._poses.update(self._dt)
        # 微弹性躯干通道:腹部偏航滞后(转向甩尾)+ 体摆(GaitProfile,默认 0)
        ab_dyaw_deg = self._micro.abdomen_lag(self._dt, st.heading)
        sway_px = self._sway_amp_px * math.sin(2.0 * math.pi *
                                               self._gait.step_hz(self._speed) *
                                               self._sway_freq_ratio * self._ant_t)

        segs = self._segments(to_screen)
        legs_out = self._legs_pose(to_screen)
        antennae = self._antennae_pose(to_screen)
        wings = self._wings_pose()
        pitch = self._flight.pitch if self._flight else 0.0
        # ---- 滑翔 v3 振幅通道(返工根因 #5):roll/bob/drift 相位由
        # flight 方程下发进 pose → 渲染逐帧重算躯干/位置,不再冻结。
        # 仅滑翔型且振幅活跃(fade>0 或漂移未清)时发射;果蝇/地面无此键,
        # pose 与改前逐位一致。pitch 键叠加 ±1.2° 微摆(0.5° 缓存桶可分辨)。 ----
        glide_out = None
        if self._flight is not None and getattr(self._flight, "glide", False) \
                and (self._flight.glide_fade > 0.005
                     or abs(self._flight.glide_drift) > 0.05):
            glide_out = {"roll_deg": round(self._flight.roll
                                           + self._flight.glide_roll, 2),
                         "bob_px": round(self._flight.glide_bob, 2),
                         "drift_px": round(self._flight.glide_drift, 2),
                         "fade": round(self._flight.glide_fade, 3)}
            pitch += self._flight.glide_pitch_extra
        # ---- 骨架层新增键(F3:只增可选键,旧渲染安全忽略) ----
        out = {"half": self.p["window_half"], "altitude": st.altitude,
               "segments": segs, "legs": legs_out, "antennae": antennae,
               "wings": wings,
               "pitch": pitch,
               "alert": self._alert_t > 0.0,
               "sprint": self._esc is not None and self._esc.get("phase") == "sprint",
               "speed_norm": clamp(self._speed / max(1.0, self.p["cruise"]), 0.0, 1.5),   # ADR-0035:3D 步态驱动
               "idle_t": round(time.monotonic() % 3600.0, 2),   # r18:静息生命感时钟(果蝇翅呼吸;手动 pose 不带此键=测试确定性不受扰)
               # r24 转向连续性:当前角速度(°/s,_turn_norm 归一基准 90°/s 还原)。
               # 渲染侧据此放宽躯干桶的「最近已缓存桶」回退半径(快转数度误差
               # 不可感),帧档侧据此保 30fps;手动 pose 不带此键=向后安全。
               "turn_deg_s": round(self._turn_norm * 90.0, 1),
               "bones": {"pose": self._pose_name,
                         "abdomen_dyaw_deg": ab_dyaw_deg,
                         "sway_px": sway_px,
                         "pitch_extra_deg": self._pitch_amp_deg * clamp(
                             self._speed / max(1.0, self.p["sprint"]), 0.0, 1.0),
                         "scale": self._rig.spec.get("scale", 1.0)},
               # r14 task2b(R5):钉足同步步态相位恒下发(只增键)。与 2D 世界
               # 钉足同一时钟——静止/逃逸僵住期 _gait.phase 冻结 → 3D 通道天然
               # 零动,消除"僵住期腿仍蹬"失真;相位推进成本本在 _integrate,
               # 此处仅读 3 个现成属性(零开销路径保持)。
               "gait": {"phase": float(self._gait.phase),                # [0,1)
                        "hz": float(self._gait.step_hz(self._speed)),    # 3~14Hz
                        "duty": float(self._gait.duty(self._speed))}}    # 0.42~0.65
        if self._drag_on:
            # 拖拽动量滞后(A4):加法通道,与 "bones" 同款可选键;渲染侧消费
            # d/gain/axes 旋转腿链、ant/ant_tip 做触须鞭状滞后(见 renderer)。
            out["drag"] = {"d": (self._drag_d[0], self._drag_d[1]),
                           "gain": DRAG_LEG_GAIN,
                           "axes": tuple(self._drag_axes),
                           "ant": (self._drag_d[0], self._drag_d[1]),
                           "ant_gain": DRAG_ANT_GAIN,
                           "ant_tip": (self._drag_d_ant[0], self._drag_d_ant[1]),
                           "ant_tip_ratio": DRAG_ANT_TIP_RATIO}
        if glide_out is not None:
            out["glide"] = glide_out
        return out

    @property
    def rig_spec(self) -> dict:
        """骨架 spec 只读视图(F1 消费面:渲染/训练/缩放 agent)。"""
        return self._rig.to_dict()

    def _segments(self, to_screen) -> list:
        st = self.state
        segs = []
        for i, (lx, ly, rx, ry) in enumerate(self.p["segments"]):
            if i == 0:  # 头:EAT 口器咀嚼点头 / GROOM 摆头
                if self._eat_anim > 0:
                    lx += math.sin(self._eat_anim) * 2.0
                elif self._groom_anim > 0:
                    lx += math.sin(self._groom_anim * 0.8) * 1.2
                    ly += math.sin(self._groom_anim * 1.6) * 1.0
            x, y = to_screen(lx, ly)
            segs.append((x, y, st.heading, rx, ry))
        return segs

    def _legs_pose(self, to_screen) -> list[dict]:
        st = self.state
        flying = st.mode is MovementMode.FLY
        eat, groom = self._eat_anim, self._groom_anim
        head = self.p["segments"][0]
        pose = self._poses
        leg_tuck = pose.get("leg_tuck", 0.0)       # 飞行收腿系数(air 姿态)
        leg_drag = pose.get("leg_drag", 3.0)       # 飞行足端后拖(px)
        sag_gain = pose.get("sag_gain", 1.0)       # 微弹性膝下沉姿态增益
        legs_out = []
        for i, (leg, kin) in enumerate(zip(self._legs, self._kin)):
            side = float(leg["side"])
            home = leg["home"]
            lift = 0.0
            if flying:
                # 飞行收腿(air 姿态表):向体轴收拢并后拖,微下垂摆动
                fx = leg["attach"][0] + (home[0] - leg["attach"][0]) * leg_tuck - leg_drag
                fy = leg["attach"][1] + (home[1] - leg["attach"][1]) * leg_tuck + \
                    math.sin(self._ant_t * 3.0 + side) * 0.6
                lift = 3.0
            elif st.activity is Behavior.EAT and leg["kind"] == "front":
                fx = head[0] + 10.0 + 3.5 * math.sin(eat + side)
                fy = side * (6.0 + 2.5 * math.cos(eat * 0.8))
                lift = 1.5
            elif st.activity is Behavior.GROOM and leg["kind"] == "front":
                # 前足擦刷触角基部(左右交替画圈)
                fx = head[0] + 8.0 + 5.0 * math.sin(groom * 1.2 + side * 0.5)
                fy = side * (3.0 + 2.0 * math.cos(groom * 1.2))
                lift = 2.5
            else:
                # 世界钉足足端 → 身体局部坐标(支撑足随身体移动"相对后移",
                # 世界坐标恒定;摆动足带抬起量)
                fx, fy, glift = self._gait.foot_local(i, st.pos, st.heading)
                lift = glift
            # 跗节世界朝向锚定:支撑相冻结(触地帧取髋→足世界方向)
            tarsus_dir = None
            if not flying and lift <= 0.1 \
                    and self._gait._feet[i].state == 0:
                if self._tarsus_yaw_w[i] is None:
                    hip_w = (st.pos[0] + kin.hip[0] * math.cos(st.heading)
                             - kin.hip[1] * math.sin(st.heading),
                             st.pos[1] + kin.hip[0] * math.sin(st.heading)
                             + kin.hip[1] * math.cos(st.heading))
                    fw = self._gait._feet[i].world
                    self._tarsus_yaw_w[i] = math.atan2(fw[1] - hip_w[1],
                                                       fw[0] - hip_w[0])
                tarsus_dir = self._tarsus_yaw_w[i] - st.heading
            else:
                self._tarsus_yaw_w[i] = None
            res = kin.solve((fx, fy), lift, tarsus_dir=tarsus_dir)
            pts = res["points"]
            # 微弹性(D2 §2.4):支撑相膝点负载下沉(一阶弹簧-阻尼,τ/下沉角
            # 来自 GaitProfile/rig spec);几何表现 = 膝拱向髋-足连线收拢
            # (膝全量、踝 0.4),即"负载下沉 2~6°"的等效横移;摆动相回零。
            if not flying:
                sag = self._micro.knee_sag(
                    i, self._dt, lift <= 0.1,
                    load_norm=st.speed / max(1.0, self.p["cruise"]),
                    gain=sag_gain)
                if sag > 1e-4:
                    pts = [p for p in pts]
                    # 髋→足向量端点:3D 链(8 点)取爪尖 pts[-1];遗留 4 点
                    # 链取 pts[3](=足端)—— 两链 pts[-1] 语义一致。
                    (x0, y0), (x3, y3) = pts[0], pts[-1]
                    dx, dy = x3 - x0, y3 - y0
                    n = math.hypot(dx, dy)
                    if n > 1e-3:
                        ux, uy = dx / n, dy / n       # 髋→足单位向量
                        # 下沉索引:3D 链 膝=pts[2]、踝=pts[3];遗留 4 点链
                        # 膝=pts[1]、踝=pts[2](LEG3D=0 行为逐位不变)。
                        for idx, w in (((2, 1.0), (3, 0.4)) if len(pts) >= 7
                                       else ((1, 1.0), (2, 0.4))):
                            px, py = pts[idx]
                            # 到连线的带符号垂距(法向 n̂=(−uy,ux))
                            dperp = (px - x0) * (-uy) + (py - y0) * ux
                            move = math.copysign(min(sag * w, abs(dperp)), dperp)
                            pts[idx] = (px + uy * move, py - ux * move)
            pts = [to_screen(*p) for p in pts]
            legs_out.append({"points": pts, "lift": res["lift"],
                             "swing": res["lift"] > 0.1,
                             "angles": res["angles"]})
        return legs_out

    def _antenna_sweep_hz(self) -> tuple:
        """触角主扫频率(单一方程;_integrate 推进与 _antennae_pose 同口径复用)。

        数值语义不变(EMO_SWEEP_GAIN/速度慢扫/惰性条件原样),只抽共用。"""
        pose = self._poses
        alert_flutter = pose.get("antenna_alert", 0.0)   # 警戒颤动权重(0~1)
        sweep_hz = pose.get("antenna_hz", 0.7) \
            + (1.0 - alert_flutter) * min(0.9, self._speed / 600.0)
        if self._emo_arousal > 0.0:      # B2:唤醒 → 触角扫频 ↑(惰性时不乘)
            sweep_hz *= 1.0 + EMO_SWEEP_GAIN * self._emo_arousal
        return sweep_hz, alert_flutter

    def _antennae_pose(self, to_screen) -> list:
        """触角:多节鞭状折线(权威形态 §5.4)。参数取自姿态库(PoseLibrary
        crossfade:rest/alert/walk 的摆频/摆幅/警戒颤动),行进附加速度慢扫;
        梢端 s>0.6 段追加向后曲率 → 照片中的 S 形回勾鞭梢。默认 9 节。
        r26 U2:NEUROPET_ANT_PHASE_INTEG=1 时两处相位取积分值(防 hz 阶跃跳变);
        默认 0 走旧 t*hz 路径(逐字节旧行为)。"""
        st = self.state
        base = self.p.get("antenna_base", (30, 4))
        length = float(self.p.get("antenna_len", 40))
        n_seg = max(2, int(self.p.get("antenna_segments", 9)))
        pose = self._poses
        sweep_hz, alert_flutter = self._antenna_sweep_hz()
        amp = pose.get("antenna_amp", 0.30)
        use_integ = ant_phase_integ_on()
        phase_main = self._ant_phase if use_integ else \
            self._ant_t * sweep_hz * 6.2831853
        phase_flutter = self._ant_flutter_phase if use_integ else \
            self._ant_t * 8.0 * 6.2831853
        out = []
        for side in (-1.0, 1.0):
            # 基点同样过 to_screen(旧版漏旋转,基点留在体侧原坐标,
            # 与第二点间画出一根横穿全身的"假触须段")
            pts = [to_screen(base[0], base[1] * side * 0.4)]
            for k in range(1, n_seg + 1):
                s = k / n_seg
                sway = amp * math.sin(phase_main
                                      - s * 1.15 + (0.0 if side > 0 else 0.7))
                if alert_flutter > 0.3:  # 梢端 5~10Hz 颤动(警戒姿态)
                    sway += 0.10 * s * alert_flutter * \
                        math.sin(phase_flutter + s * 2.0)
                a = 0.85 + sway * 0.5 * s   # 基角 ~49° 前外,越往梢端摆幅越大(鞭状)
                if self._emo_fear > 0.0:    # B2:恐惧 → 下垂/外摊角 ↑(惰性时不加)
                    a += EMO_DROOP_RAD * self._emo_fear * s
                if s > 0.6:                 # 梢端回勾:追加向后曲率(照片 S 形鞭梢)
                    a += 0.5 * (s - 0.6) ** 2 * 1.2
                lx = base[0] + length * s * math.cos(a)
                ly = side * (base[1] * 0.4 + length * s * math.sin(a) + 1.2 * s)
                pts.append(to_screen(lx, ly))
            out.append(pts)
        return out

    def _wings_pose(self) -> dict:
        if self._flight is not None:
            f = self._flight
            return {"active": f.wing_active, "phase": f.wing_phase,
                    "span": float(self.p.get("wing_span", 0.0)),
                    "fold": f.fold}
        # 蟑螂:地面翅盖闭合(渲染层以 wing_cover 特征绘制),无翅影
        return {"active": False, "phase": 0.0,
                "span": float(self.p.get("wing_span", 0.0)), "fold": 1.0}

    def window_half(self) -> int:
        return self.p["window_half"]

    # ---- 被拖拽中(ADR-0032;用户裁决"关节式拖拽,停即恢复标准俯视图态") ----
    def carried_tick(self, dt: float) -> None:
        """held 期每帧回调(features/drag.py 调用;IBody 加法式方法实现)。

        旧缺陷:步态世界钉足只在 apply/_integrate 里更新,held 分支跳过
        body.apply → 足端钉在**抓取前**的世界坐标,宠物被拖走后六腿仍指向
        拖拽前锚点(用户:"动量锚点都是拖拽前的位置")。现改为每帧把足端
        重锚定到**当前**体下的静息位(标准俯视图态);随鼠标甩动的关节感由
        拖拽摆角通道(pose["drag"],DragFeature 的 a+V_DRIVE·v 动力学)表达,
        鼠标停 → 摆角自然衰减 → 恢复标准姿态。跗节朝向锚一并清(触地帧
        重取);拖拽动量/甩动翻面观测在此续走(held 期 ivx=pos 差分)。"""
        st = self.state
        self._gait.reset(st.pos, st.heading)
        for i in range(len(self._tarsus_yaw_w)):
            self._tarsus_yaw_w[i] = None
        self._update_drag(dt)

    # ---- 松手抛掷(ADR-0032;市场对标 docs/references/桌宠市场对标_2026.md) ----
    FLING_TAU = 0.35          # 惯性滑行衰减时间常数(s;~0.8s 归静)
    PIVOT_RATE = math.radians(900.0)   # r24:撞墙枢转角速度(180°≈0.2s,弹道级)
    FLING_MIN_V = 120.0       # 低于此速度的松手=轻放,不产生抛掷(px/s)
    FLING_CAP = 1400.0        # 抛出速度上限(px/s;观赏截断,≈ escape cap)
    FLING_STOP_V = 15.0       # 衰减到此速度即停(防无限蠕动)

    def set_fling(self, vx: float, vy: float) -> None:
        """松手抛掷入口(DragFeature 经 "user/grab_release" 调用)。"""
        self._fling = [float(vx), float(vy)]
