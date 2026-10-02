"""果蝇飞行状态机:GROUNDED → TAKEOFF(先跳后开翅)→ FLY(巡航+saccade)→ LAND → GROUNDED。

依据 docs/references/行为学_美洲大蠊与果蝇.md §2.2(速查表 #17~#24):
- 起飞:逃逸起飞"后腿先蹬跳、后开翅"(巨纤维介导):起跳 4~8px + 俯仰,
  约 0.12s 后开翅进入爬升;真实威胁→起跳 150~250ms 延迟属大脑决策预算,不由身体承担;
- 飞行:巡航高度 fly_altitude(0~70px)带轻微浮动(±0.2 BL 量级);
  saccade:每 0.5~1s 一次 30°~90°、<100ms 的快速转向(角速度≈Δ角/时长);
- 逃逸:飞行中收到 ESCAPE → 直接加速逃逸(增益 ×1.3,抑制 saccade);
- 降落:减速→下降→近地收翅(前足前伸由渲染层表现)→触地 → GROUNDED;
- 振翅:真实 ~200Hz 无法逐帧呈现,渲染为 12~24Hz 半透明翅影(此处 18Hz 相位)。

滑翔型物种(params["glide"]=True,如美洲大蠊的翅盖滑翔)v2(§3.2 参数表施工图):
- 与果蝇"悬停微调"不同,滑翔型 TAKEOFF 后不能悬停:FLY 段按空气动力学骨架
  下沉 sink = v/GR(设计 GR=6 → 650px/s 前进时 sink≈108px/s,滑翔比 ≈6,
  旧 v1 匀速 11px/s 的 59:1 失真已弃),轨迹沿航向朝 glide_target;
- 巡航纹理:pitch = -atan(1/GR)(航迹角,随 GR 单调)、横摆正弦
  ±~6px @0.4Hz、bank-to-turn 蜿蜒(roll = k·yaw_rate,同号,钳 ±25°;
  连续正弦无线突跳,saccade 禁用保留);
- 着地四拍:近地 flare(alt<15px 抬头减速,sink×0.6)→ 失速收翅
  snap(fold 0→1 ≤80ms + sink 提至 200px/s)→ 触地 → 滑停 0.2~0.3s
  (pitch -3°→0 缓收);入场展翅为 ~150ms ease-out 角度曲线;
- 物种差异全部由参数区分(PARAMS glide_* 键),不写死果蝇行为,
  果蝇(glide 缺省 False)行为不变。

altitude 全程连续(状态切换不跳变,跳升段亦为连续斜率),供测试断言。

滑翔 v3 姿态振幅通道(第十轮,返工根因 #5「滑翔虚影固定位置」:v2 巡航
pitch 恒定、roll/sway 不进 pose、渲染侧墙钟横摆 0.4Hz 低于肉眼可读带 →
躯干逐帧同图=虚影固定)。全时间线方程(t_g 为巡航拍内连续积分时钟,
进入/退出只乘淡入窗 w,相位永不重置 → 无跳变):

    roll_sway(t_g)  = A_r·sin(2π·f_r·t_g + φ_r)·w        A_r=6°,  f_r=2.2Hz
    y_bob(t_g)      = A_b·sin(2π·f_b·t_g + φ_b)·w        A_b=2.5px, f_b=1.6Hz
                     φ_b = φ_r + 0.5π(与 roll 相位差 0.5π∈[0.3π,0.7π],非同步→有机感)
    pitch_extra(t_g)= 1.2°·sin(2π·f_r·t_g + φ_r)·w       (−9.5° 基准上的微摆;
                     渲染 pitch 缓存 0.5° 桶可分辨 → 躯干长度可见呼吸)
    drift_v(t_g)    = k_d·roll_sway(滚右漂右,沿体轴法向,位置连续积分)
    w(t)            = smoothstep(线性淡入进度),入巡航 0→1 @0.6s,
                     flare/snap/触地 1→0 @0.3s(位置项 drift 另按指数归零)

连续性预算(60Hz):|Δroll|≤1.4°/帧(=A_r·2π·f_r/60=1.381°,任务书 0.8°/帧
与 A_r=6°/f_r=2.2Hz 正弦物理不相容——±6°@2.2Hz 的最优波形(三角波)峰速
52.8°/s=0.88°/帧仍超 0.8,按本方程推预算,见 tests/test_glide_v2.py 注);
|Δy_bob|≤0.45px/帧(≤2 预算);|Δdrift|≤2px/帧(k_d=0.03·v,任务书 ~0.12·v
会给 468px/s=7.8px/帧 超预算 4 倍 → 收紧到预算内,滚右漂右符号不变)。
"""
from __future__ import annotations

import enum
import math
import random

from neuropet.core.mathutil import clamp, lerp

WING_VIS_HZ = 18.0            # 翅影可见频率(真实 ~200Hz → 观赏 12~24Hz,速查表 #19)
JUMP_T = 0.12                 # 蹬跳时长(s);随后开翅
WING_OPEN_T = 0.10            # 开翅过渡(s)
CLIMB_T = 0.55                # 爬升到巡航高度的标称时间(s)
SACCADE_INTERVAL = (0.5, 1.0) # saccade 间隔(s,速查表 #20)
SACCADE_ARC = (30.0, 90.0)    # saccade 幅度(度)
SACCADE_DUR = (0.05, 0.09)    # saccade 历时(s,<100ms)
ESCAPE_GAIN = 1.3             # 飞行逃逸速度增益

# ---- 滑翔型物种参数 v2(glide=True 启用;§3.2 参数表施工图,见 cockroach.py) ----
# v1(旧→新留痕):GLIDE_SINK_RATE=11 匀速下沉 → v2 sink=v/GR(滑翔比 59:1 → ≈6);
#                GLIDE_PITCH=-5 恒值 → v2 pitch=-atan(1/GR)(航迹角随 GR 单调);
#                GLIDE_LAND_DESCENT=170 → 200(收翅失速砸落档)。
# 以下为缺省值,全部可被 species PARAMS 同名键覆盖(glide_gr/glide_flare_alt/
# glide_sway_px/glide_sway_hz/glide_yaw_meander_deg/glide_bank_gain)。
GLIDE_GR = 6.0                # 设计滑翔比 GR(升阻比):sink = v/GR;昆虫合理带 1~6,取上限档
GLIDE_FLARE_ALT = 15.0        # 近地拉平(flare)触发高度(px)
GLIDE_FLARE_GR = 10.0         # flare 期等效 GR(sink ×0.6 语义,拉平减速)
GLIDE_FLARE_DWELL = 0.15      # flare 保持时长(s)后失速收翅
GLIDE_FLARE_PITCH = 6.0       # flare 抬头角(度,正=抬头)
GLIDE_SPREAD_T = 0.15         # 入场展翅时长(s,ease-out 角度曲线;~120ms 即达 96% 展角)
GLIDE_SNAP_T = 0.08           # 触地收翅 snap 上限(s,≤80ms)
GLIDE_SNAP_SINK = 200.0       # 收翅失速下沉速率(px/s,砸落拍)
GLIDE_SLIDE_T = 0.25          # 触地后滑停时长(s,0.2~0.3 档)
GLIDE_SWAY_HZ = 0.4           # 横摆频率(0.3~0.5Hz 带)
GLIDE_SWAY_PX = 6.5           # 横摆幅度(px,±3.5~9px 带;≈0.057BL@BL=115)
GLIDE_YAW_MEANDER = 50.0      # 蜿蜒偏航角速度幅(度/s;航向摆幅 =A/ω ≈ ±32°,<±60° 限)
GLIDE_BANK_GAIN = 0.30        # bank-to-turn:roll(度) = gain × yaw_rate(度/s);钳 ±25°
GLIDE_BANK_MAX = 25.0
GLIDE_LAND_DESCENT = 200.0    # 滑翔硬着陆下降速率(px/s;旧 170,v2 收翅砸落档)

# ---- 滑翔 v3 姿态振幅参数(方程见模块头;全部可被 PARAMS 同名键覆盖) ----
GLIDE_ROLL_AMP = 6.0          # 横滚摆动幅 A_r(度;验收带 [5,7])
GLIDE_ROLL_HZ = 2.2           # 横滚摆动频率 f_r(Hz;肉眼可读带)
GLIDE_BOB_AMP = 2.5           # 垂直起伏幅 A_b(px;验收带 [2,3.5])
GLIDE_BOB_HZ = 1.6            # 垂直起伏频率 f_b(Hz;与 f_r 非同步)
GLIDE_PITCH_SWING = 1.2       # 俯仰微摆幅(度;0.5° 缓存桶可分辨)
GLIDE_FADE_IN_T = 0.6         # 振幅淡入时长(s,smoothstep 前身线性进度)
GLIDE_FADE_OUT_T = 0.3        # 振幅淡出时长(s;flare/snap/触地共用)
GLIDE_DRIFT_K = 0.03          # 侧向漂移系数 k_d(px/s 每度,按 v=1 归一:
                              # drift_v = k_d·v·roll;0.12 会超 |Δpos|≤2px/帧)


class FlightState(enum.Enum):
    GROUNDED = "grounded"
    TAKEOFF = "takeoff"
    FLY = "fly"
    LAND = "land"


class FlightStateMachine:
    """果蝇(或其他飞虫)的垂直通道状态机。

    身体模块每帧调用 update(dt),随后读取属性:
      state / altitude(目标高度) / wing_active / wing_phase /
      pitch(俯仰,度,正=头抬起) / landing_gear(近地前足前伸) / yaw_rate(saccade 角速度)
    """

    def __init__(self, params: dict) -> None:
        self.alt_cruise = clamp(float(params.get("fly_altitude", 55.0)), 10.0, 70.0)
        self.speed_cruise = max(60.0, float(params.get("fly_speed", 600.0)))
        # 滑翔型:TAKEOFF 后不能悬停,高度自然缓慢下沉,硬着陆(见模块 docstring)
        self.glide = bool(params.get("glide", False))
        # 滑翔 v2 参数(PARAMS 覆盖 → 模块缺省;见文件头 v2 参数表)
        self.glide_gr = max(1.0, float(params.get("glide_gr", GLIDE_GR)))
        self.glide_flare_alt = max(5.0, float(params.get("glide_flare_alt",
                                                         GLIDE_FLARE_ALT)))
        self.glide_sway_px = float(params.get("glide_sway_px", GLIDE_SWAY_PX))
        self.glide_sway_hz = clamp(float(params.get("glide_sway_hz", GLIDE_SWAY_HZ)),
                                   0.3, 0.5)
        self.glide_yaw_meander = math.radians(
            float(params.get("glide_yaw_meander_deg", GLIDE_YAW_MEANDER)))
        self.glide_bank_gain = float(params.get("glide_bank_gain", GLIDE_BANK_GAIN))
        self.state = FlightState.GROUNDED
        self.t = 0.0                       # 当前状态内计时(s)
        self.altitude = 0.0                # 当前高度(px,由本机积分,保证连续)
        self.jump_alt = random.uniform(4.0, 8.0)
        self.pitch = 0.0
        self.wing_active = False
        self.wing_phase = random.uniform(0.0, 6.283)
        self.fold = 1.0                    # 1=收拢(地面),0=展开(飞行中);渲染用
        self.landing_gear = False
        self._yaw_rate = 0.0               # saccade 期间的偏航角速度(rad/s)
        self._saccade_rate = 0.0
        self._saccade_left = 0.0
        self._saccade_timer = random.uniform(*SACCADE_INTERVAL)
        self._float_seed = random.uniform(0.0, 6.283)
        self.escape_t = 0.0                # 飞行逃逸增益剩余时间(s)
        # 滑翔 v2 纹理状态(供渲染/探针读取)
        self.sway = 0.0                    # 横摆偏移(px,巡航正弦;渲染侧再投影到体轴法向)
        self.roll = 0.0                    # bank-to-turn 倾侧角(度,与 yaw_rate 同号)
        self.flare = False                 # 近地拉平中(alt<flare_alt,抬头减速)
        self._flare_t = 0.0                # flare 已保持时长(s)
        self._snap_left = 0.0              # 收翅 snap 剩余(s,>0 即收翅中)
        self._snap_phase = False           # 失速收翅砸落段(直至触地)
        self.slide_left = 0.0              # 触地滑停剩余(s,0.2~0.3)
        # 滑翔 v3 振幅通道(供 pose()/渲染/测试读取;方程见模块头)
        self.glide_roll = 0.0              # 横滚摆动(度,A_r·sin·w)
        self.glide_bob = 0.0               # 垂直起伏(px,A_b·sin·w,相位差 0.5π)
        self.glide_pitch_extra = 0.0       # 俯仰微摆(度,±1.2° 同 f_r)
        self.glide_drift = 0.0             # 侧向漂移积分(px,体轴法向;滚右漂右)
        self.glide_fade = 0.0              # 振幅淡入窗 w∈[0,1](smoothstep 前身)
        self._fade = 0.0                   # 淡入线性进度(smoothstep 前身)
        self._osc_t = 0.0                  # 振荡相位时钟(s,仅巡航拍推进)
        self._osc_ph = self._float_seed    # 最近一次 roll 相位(淡出期冻结复用)

    # ---------------- 命令接口 ----------------
    def start_takeoff(self) -> bool:
        if self.state is FlightState.GROUNDED:
            self.state = FlightState.TAKEOFF
            self.t = 0.0
            self.jump_alt = random.uniform(4.0, 8.0)
            self.flare = False
            self._flare_t = 0.0
            self._snap_left = 0.0
            self._snap_phase = False
            self.slide_left = 0.0
            self.sway = 0.0
            self.roll = 0.0
            self.glide_roll = 0.0
            self.glide_bob = 0.0
            self.glide_pitch_extra = 0.0
            self.glide_drift = 0.0
            self.glide_fade = 0.0
            self._osc_t = 0.0
            self._osc_ph = self._float_seed
            return True
        return False

    def start_land(self) -> bool:
        if self.state in (FlightState.TAKEOFF, FlightState.FLY):
            self.state = FlightState.LAND
            self.t = 0.0
            self._yaw_rate = 0.0
            return True
        return False

    def escape(self) -> bool:
        """飞行/起飞中收到 ESCAPE:直接加速逃逸;地面返回 False(由地面逃逸处理)。"""
        if self.state in (FlightState.FLY, FlightState.TAKEOFF):
            self.escape_t = 0.8
            self._saccade_left = 0.0
            return True
        return False

    @property
    def flying(self) -> bool:
        return self.state in (FlightState.TAKEOFF, FlightState.FLY, FlightState.LAND) \
            and self.altitude > 0.5

    # ---------------- 每帧推进 ----------------
    def update(self, dt: float, speed: float) -> None:
        dt = max(0.0, dt)
        self.t += dt
        self.escape_t = max(0.0, self.escape_t - dt)
        st = self.state

        if st is FlightState.GROUNDED:
            self.altitude = max(0.0, self.altitude - 60.0 * dt)   # 兜底回落,保持连续
            if self.glide and self.slide_left > 0.0:
                # 滑停拍:触地后 pitch -3°→0 抬头归位(0.2~0.3s 窗口)
                self.slide_left = max(0.0, self.slide_left - dt)
                self.pitch = lerp(self.pitch, 0.0, min(1.0, 8.0 * dt))
            else:
                self.pitch = max(0.0, self.pitch - 120.0 * dt)
            if self.glide and (self._fade > 0.0
                               or abs(self.glide_drift) > 0.01):
                # v3:触地滑停期残余振幅/漂移连续归零(fade 在 flare/snap 已
                # 大幅衰减,此处收尾 → 全程无相位/位置跳变)
                self._osc_fadeout(dt)
            self.wing_active = False
            self.fold = min(1.0, self.fold + 2.5 * dt)
            self.landing_gear = False
            self._yaw_rate = 0.0
            return

        if st is FlightState.TAKEOFF:
            # 阶段 1(0~JUMP_T):蹬跳 4~8px + 俯仰抬头,翅未开
            # 阶段 2:开翅爬升(WING_OPEN_T 过渡)到巡航高度
            if self.t < JUMP_T:
                p = self.t / JUMP_T
                self.altitude = self.jump_alt * p
                self.pitch = 18.0 * p
                self.wing_active = False
            else:
                # 滑翔型爬升更快(收敛 14/s、爬升窗 0.28s vs 0.55s):v2 巡航高
                # 50px、sink≈108px/s,入滑高度必须贴近巡航高,否则巡航段被
                # flare 吃掉(§3.2 时长核算);果蝇路径参数逐位不变
                climb_w = 0.28 if self.glide else CLIMB_T
                climb = clamp((self.t - JUMP_T) / max(climb_w, 1e-3), 0.0, 1.0)
                target = lerp(self.jump_alt, self.alt_cruise, climb)
                rate = 14.0 if self.glide else 8.0
                self.altitude += (target - self.altitude) * min(1.0, rate * dt) \
                    + 30.0 * dt * climb
                self.altitude = min(self.altitude, self.alt_cruise * 1.15)
                self.pitch = lerp(18.0, 4.0, climb)
                self.wing_active = True
                # 开翅过渡:fold 1(收拢)→ 0(展开)。滑翔型:GLIDE_SPREAD_T
                # ease-out 角度曲线(fold=(1-p)²,~120ms 即 96% 展角,§3.2 入场);
                # 悬停型:WING_OPEN_T 线性(果蝇逐位不变)。
                if self.glide:
                    p_sp = clamp((self.t - JUMP_T) / max(GLIDE_SPREAD_T, 1e-3),
                                 0.0, 1.0)
                    self.fold = (1.0 - p_sp) ** 2
                else:
                    self.fold = clamp(1.0 - (self.t - JUMP_T) / max(WING_OPEN_T, 1e-3),
                                      0.0, 1.0)
            if self.t >= JUMP_T + (GLIDE_SPREAD_T if self.glide else WING_OPEN_T) \
                    + 0.30 or self.altitude >= self.alt_cruise * 0.95:
                self.state = FlightState.FLY
                self.t = 0.0
                self._saccade_timer = random.uniform(*SACCADE_INTERVAL)
        elif st is FlightState.FLY:
            self.wing_active = True
            self.fold = 0.0                     # 飞行中翅全展开(fold 语义:1=收拢)
            if self.glide:
                # 滑翔 v2(§3.2 参数表):巡航 sink=v/GR、pitch=-atan(1/GR)、
                # 横摆正弦 + bank 蜿蜒(禁 saccade);近地 flare 抬头减速 →
                # 失速收翅(snap ≤80ms)砸落 → 触地滑停。轨迹仍沿起飞航向朝
                # glide_target,水平/垂直速度比 ≈ GR。
                self._glide_step(dt, speed, lambda v: v / self.glide_gr)
                if self.altitude <= 0.0:
                    self._touchdown()
            else:
                # 悬停型(果蝇):巡航高度 + 轻微浮动(±4px,悬停微调观感)
                float_alt = self.alt_cruise * (0.93 + 0.07 * math.sin(
                    1.3 * self.t + self._float_seed))
                self.altitude += (float_alt - self.altitude) * min(1.0, 3.0 * dt)
                self.pitch = lerp(self.pitch, 2.0 + 1.5 * math.sin(2.1 * self.t),
                                  min(1.0, 3 * dt))
                self._update_saccade(dt)
        elif st is FlightState.LAND:
            # 下降进近:滑翔型恒定快速下降(收翅砸落档 200px/s);果蝇 90→28px/s 随高度收窄缓触地
            if self.glide:
                self.landing_gear = self.altitude < 22.0
                self._glide_step(dt, speed, lambda v: GLIDE_LAND_DESCENT)
                if self.altitude <= 0.0:
                    self._touchdown()
            else:
                descend = lerp(90.0, 28.0, clamp(self.altitude / max(self.alt_cruise, 1e-3)))
                self.altitude = max(0.0, self.altitude - descend * dt)
                self.pitch = lerp(self.pitch, -8.0 if self.altitude > 10.0 else 0.0,
                                  min(1.0, 4.0 * dt))
                self.wing_active = self.altitude > 8.0
                self.fold = 1.0 if self.altitude <= 8.0 else clamp(1.0 - self.altitude / 60.0)
                self.landing_gear = self.altitude < 22.0
                self._yaw_rate = 0.0
                if self.altitude <= 0.0:
                    self._touchdown()

        # 振翅相位:以观赏翅影频率推进(真实 200Hz 不可逐帧,见模块 docstring)
        if self.wing_active:
            self.wing_phase = (self.wing_phase + WING_VIS_HZ * 6.2831853 * dt) % 6.2831853

    def _glide_step(self, dt: float, speed: float, cruise_sink) -> None:
        """滑翔 v2 共用推进(FLY 自然下滑与 LAND 进近共用;§3.2 参数表)。

        cruise_sink(v) 给出该状态的巡航下沉速率(FLY=v/GR,LAND=砸落档);
        三段状态:巡航(横摆+bank 蜿蜒)→ 近地 flare(alt<flare_alt 抬头减速,
        sink×0.6)→ 失速收翅 snap(fold 0→1 ≤80ms + sink 提至砸落档)。
        触地后由 _touchdown 进入滑停(slide_left 0.2~0.3s)。"""
        v = max(1.0, float(speed))
        if self._snap_phase:
            # ---- 失速砸落拍:收翅 snap(fold→1,≤80ms)+ 大下沉,直至触地 ----
            if self._snap_left > 0.0:
                self._snap_left = max(0.0, self._snap_left - dt)
            self.fold = clamp(1.0 - self._snap_left / max(GLIDE_SNAP_T, 1e-3),
                              0.0, 1.0)
            self.pitch = lerp(self.pitch, -3.0, min(1.0, 8.0 * dt))
            self.roll = lerp(self.roll, 0.0, min(1.0, 6.0 * dt))
            self._yaw_rate = lerp(self._yaw_rate, 0.0, min(1.0, 4.0 * dt))
            self._osc_fadeout(dt)                 # v3:振幅/漂移连续归零
            self.sway = 0.0
            self.landing_gear = True
            self.altitude = max(0.0, self.altitude - GLIDE_SNAP_SINK * dt)
            if self.altitude <= 0.0:
                self._touchdown()
            return
        if self.flare:
            # ---- 近地拉平拍:抬头减速(sink ×0.6),保持 GLIDE_FLARE_DWELL
            #      后失速 → 进入收翅 snap(四拍之"失速-收翅") ----
            self._flare_t += dt
            self.pitch = lerp(self.pitch, GLIDE_FLARE_PITCH, min(1.0, 6.0 * dt))
            self.roll = lerp(self.roll, 0.0, min(1.0, 6.0 * dt))
            self._yaw_rate = lerp(self._yaw_rate, 0.0, min(1.0, 4.0 * dt))
            self._osc_fadeout(dt)                 # v3:振幅/漂移连续归零
            self.landing_gear = True
            self.altitude = max(0.0, self.altitude
                                - 0.6 * v / GLIDE_FLARE_GR * dt)
            if self._flare_t >= GLIDE_FLARE_DWELL or self.altitude <= 0.0:
                self._snap_phase = True                 # 收翅 snap 启动(≤80ms)
                self._snap_left = GLIDE_SNAP_T
            if self.altitude <= 0.0:
                self._touchdown()
            return
        # ---- 巡航拍:sink=v/GR,pitch≈-atan(1/GR) 航迹角,横摆+bank 蜿蜒 ----
        if self.altitude <= self.glide_flare_alt:
            self.flare = True                            # 进入近地拉平
            self._flare_t = 0.0
            return
        sink = cruise_sink(v)
        self.altitude = max(0.0, self.altitude - sink * dt)
        # pitch 目标 = 航迹角 -atan(1/GR)(度;GR=6 → -9.46°,随 GR 单调);
        # 收敛 8/s:0.2s 内入带(滑翔总时长 ~0.6s,慢收敛会被 flare 截断)
        pitch_t = -math.degrees(math.atan(1.0 / self.glide_gr))
        self.pitch = lerp(self.pitch, pitch_t, min(1.0, 8.0 * dt))
        # 横摆(sway):正弦 ±glide_sway_px @ 0.3~0.5Hz(空中姿态残余振荡)
        self.sway = self.glide_sway_px * math.sin(
            6.2831853 * self.glide_sway_hz * self.t + self._float_seed)
        # 蜿蜒偏航(禁 saccade:连续正弦,无线突跳);base._apply_fly 消费
        # yaw_rate 积分到 heading → 轨迹真摆,落点仍朝 glide_target 方向
        self._yaw_rate = self.glide_yaw_meander * math.sin(
            0.5 * 6.2831853 * self.glide_sway_hz * self.t + self._float_seed)
        # bank-to-turn:roll(度) = gain × yaw_rate(度/s),同号,钳 ±25°
        self.roll = clamp(math.degrees(self._yaw_rate) * self.glide_bank_gain,
                          -GLIDE_BANK_MAX, GLIDE_BANK_MAX)
        # ---- 滑翔 v3 振幅通道:连续正弦,相位 _osc_t 仅在此拍推进(无跳变) ----
        self._osc_t += dt
        self._fade = min(1.0, self._fade + dt / GLIDE_FADE_IN_T)
        self._apply_osc()
        # 侧向漂移耦合:drift_v = k_d·v·roll(滚右漂右;位置连续积分,
        # 峰值 k_d=0.03 时 ≤1.95px/帧 ≤2 预算,见模块头)
        self.glide_drift += GLIDE_DRIFT_K * v * self.glide_roll * dt

    def _apply_osc(self) -> None:
        """滑翔 v3:按当前 _osc_t 相位与淡入窗 w 重算 roll/bob/pitch 微摆。
        相位永不重置,w→0 即振幅无跳变淡出;φ_b = φ_r + 0.5π(非同步)。"""
        w = self._fade * self._fade * (3.0 - 2.0 * self._fade)
        self.glide_fade = w
        self._osc_ph = 6.2831853 * GLIDE_ROLL_HZ * self._osc_t + self._float_seed
        s_r = math.sin(self._osc_ph)
        self.glide_roll = GLIDE_ROLL_AMP * s_r * w
        self.glide_bob = GLIDE_BOB_AMP * math.sin(
            6.2831853 * GLIDE_BOB_HZ * self._osc_t + self._float_seed
            + 0.5 * math.pi) * w
        self.glide_pitch_extra = GLIDE_PITCH_SWING * s_r * w

    def _osc_fadeout(self, dt: float) -> None:
        """v3 非巡航拍(flare/snap)振幅淡出:相位冻结在最后巡航帧(_osc_ph),
        仅窗 w 收敛到 0;漂移偏移按指数归零(位置连续,触地无残差跳变)。"""
        if self._fade > 0.0:
            self._fade = max(0.0, self._fade - dt / GLIDE_FADE_OUT_T)
            self._apply_osc()
        self.glide_drift = lerp(self.glide_drift, 0.0, min(1.0, 5.0 * dt))

    def _touchdown(self) -> None:
        """触地:回 GROUNDED,姿态/翅状态归位(滑翔到地与降落完成共用,altitude 连续)。
        滑翔型:进入滑停拍(slide_left 0.2~0.3s,pitch -3°→0 由 GROUNDED 段缓收)。"""
        self.altitude = 0.0
        self.state = FlightState.GROUNDED
        self.t = 0.0
        self.pitch = 0.0 if not self.glide else -3.0    # 滑停起点(前低后停观感)
        self.wing_active = False
        self.fold = 1.0
        self.landing_gear = False
        self._yaw_rate = 0.0
        self.flare = False
        self._flare_t = 0.0
        self._snap_left = 0.0
        self._snap_phase = False
        self.sway = 0.0
        self.roll = 0.0
        if self.glide:
            self.slide_left = GLIDE_SLIDE_T

    def _update_saccade(self, dt: float) -> None:
        """saccade 调度:每 0.5~1s 一次 30°~90°、<100ms 的快转;逃逸增益期间抑制。"""
        if self._saccade_left > 0.0:
            self._saccade_left -= dt
            self._yaw_rate = self._saccade_rate
            if self._saccade_left <= 0.0:
                self._yaw_rate = 0.0
                self._saccade_timer = random.uniform(*SACCADE_INTERVAL)
            return
        self._yaw_rate = 0.0
        if self.escape_t > 0.0:
            return
        self._saccade_timer -= dt
        if self._saccade_timer <= 0.0:
            arc = math.radians(random.uniform(*SACCADE_ARC)) * random.choice((-1.0, 1.0))
            dur = random.uniform(*SACCADE_DUR)
            self._saccade_rate = arc / dur
            self._saccade_left = dur

    # ---------------- 读取 ----------------
    @property
    def yaw_rate(self) -> float:
        return self._yaw_rate

    def speed_gain(self) -> float:
        return ESCAPE_GAIN if self.escape_t > 0.0 else 1.0
