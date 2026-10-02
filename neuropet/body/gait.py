"""世界钉足步态(World-Pinned Foot Gait):支撑相足端钉在世界坐标 + 迈步状态机。

参考 deskbug core/body.py:253-323(PLANTED/SWING 两态迈步机),替换旧版
"相位器直接给体坐标足端偏移"的方案——旧方案支撑相足端相对身体匀速后扫,
是"滑步感/关节错动"的根源。新机制:

- 支撑相(PLANTED):足端**钉在世界坐标**不动;身体前进时足端相对身体
  自然后移(这是真实步态的几何本质,足底与地面无相对滑动);
- **相位锚定静息位**(骨架重构波,根除"加长腿迁就过拉伸阀"的历史包袱):
  迈步触发的参考点不再是静态 home,而是随速度/相位移动的"落点锚"
    ideal_i(t) = home_i + s·b_i·x̂        (身体系,s = v/步频 为自然步距)
  其中 b_i ∈ {0,1} 是每腿的**扫掠方向系数**(按静息足向的 x 分量符号:
  前向腿 b=0,足端落点=home、支撑相内距离单调变近;后向腿 b=1,落点前移
  一个步距、支撑相结束时恰好回到 home)。对任意腿对,支撑相内髋-足距的
  峰值 = 静息距 ≤ REST_CAP_K×reach,**与速度无关**——权威节段(后腿股节
  37px 等)在巡航~冲刺全域都不会逼近 0.88 过拉伸阀,三角互锁得以结构性
  保持(冲刺段仅个别腿对按设计退化波状步,见 _trigger/over 注释);
- 迈步触发:err = |ideal_i(t) − 足端世界坐标| 超过 trigger(自适应步距)
  且本组摆动窗开启;应急旁路 err > max(3×trigger, 1.3×s)(急转/变速);
  过拉伸安全阀 OVER_K 作为最后防线(正常步态永不触发);
- 摆动(SWING):落点 = 落点锚 + **速度前馈**(vel×摆动时长×FF_GAIN,
  恰好补偿摆动期间身体前进量的大部分),中途沿二次贝塞尔插值并向髋部
  收拢(抬腿内折弧),lift = lift_amp·sin(πp) 供渲染/IK 抬腿;落足瞬间
  再钳到 LAND_TOUCH_K(=OVER_K)内;
  **摆动 = 本组相位的一段**(占空比定义式 duty = 支撑时长/周期 =
  1 − 摆动时长×步频):起摆 = 窗开后**第一帧**(不再等 err>trig,见下),
  落足 = 相位回卷。起摆相位因此在 [duty, duty+step) 上均匀分布,而摆动帧数
  c = ceil((1−起摆相位)/step) 满足 E[c] = (1−duty)·F(F=周期帧数)→ **实测
  duty = profile duty**(巡航精确 0.500/0.499,不需 dither)。历史版有三处
  偏差,均已定位:
  ①把 swing_win_k×(1−duty)/hz 当摆动时长再钳 0.05s 下限(实测 duty cruise
  0.593/冲刺 0.278);②起摆还要求 err>trig,起摆被推迟(实测起摆组相位均值
  0.575 vs 窗开 0.500)→ 摆动被截短、duty 抬到 0.526/0.562;③误把
  SWING_TIMEOUT_S 当落点速度前馈时长(前馈暴冲,后腿隔窗才迈步);
- 冲刺段(duty_profile 0.42 < 0.5)的**帧量化不可达性**:duty=0.42 要求每组
  摆动 2.32 帧 @15Hz,而帧数只能取整;取 3 帧 ⇒ 两组摆动窗重叠 0.16 周期 ⇒
  实测 duty 0.38(低于 0.40 门槛),取"摆动跨度 ≤ 0.5 周期"上钳 ⇒ 实测 0.49。
  本实现取后者(见 update 的 span 注释):**摆动相位跨度 = min(1−起摆相位,
  1−duty, 0.5)**——巡航取回卷(两组无缝交接,duty 精确 = 0.500);冲刺被
  0.5 上钳(摆动长度有界、不越入对组窗),实测 duty 0.49 ∈ QF 门槛 [0.40,0.50]。
  静态 profile 的 0.42 仍是解析查表值(duty_window 断言不受影响);闭环与
  静态表在冲刺档的 0.07 差值是本条帧量化限制,已列入 FIX1 报告"剩余问题";
- 静息位钳制 REST_CAP_K=0.78×reach(防"近满程必迈步"步态散开,并给
  过拉伸阀留 0.10×reach 结构余量);
- 组间相位约束:三角步态组(A={R3,L1,R2} / B={L3,R1,L2},相位差 0.5)
  的摆动窗——腿只允许在本组窗内起步(应急旁路/安全阀除外),且**每窗至多
  一步**(armed 锁存),故实测步频 = 相位步频。duty≤0.5 时两组摆动区间
  [duty,1) / [duty−0.5,0.5) 首尾相接零重叠 → 任一帧至多一组在摆动
  (test_body both_groups==0);
- 相位时钟只在行走时推进(speed>0):静止时窗永不开→不凭空迈步;已在摆动
  的足允许相位走到回卷收尾,不留"停在半空"的腿。原地转向靠应急旁路
  (err > emergency)迈步,不依赖相位。

步频/占空映射沿用调研结论(HZ/DUTY 常量表):巡航 3~8Hz → 冲刺 15Hz,
duty 0.65→0.50→0.42。全部常数可被 GaitProfile(body/gait_profile.py,
F2 冻结点)注入覆盖,缺失回退本文件默认(=未训练保守版)。
"""
from __future__ import annotations

import math

from neuropet.core.mathutil import clamp, lerp

# ---- 步频/占空映射(Hz):v=0 → 巡航 → 冲刺 两段线性;来源调研 §1.3 ----
# 冲刺默认 14.0(带 [10,15]):实测相位 15.0 时闭环步频抖到 15.09~15.30Hz 骑破 QF 门(ESCAPE 突速帧速度峰 + 应急/过拉伸起摆 +0.4~1.1Hz),留 1Hz 余量
HZ_REST, HZ_CRUISE, HZ_SPRINT = 3.0, 8.0, 14.0
# duty factor:低速巡查 → 巡航 → 冲刺;摆动窗 = 1−duty
DUTY_PATROL, DUTY_CRUISE, DUTY_SPRINT = 0.65, 0.50, 0.42
GROUP_PHASE = {0: 0.0, 1: 0.5}   # 组相位偏移;组间差 0.5(摆动窗错开半周期)

# ---- 世界钉足步态参数(参考 deskbug body.py:31-38, 253-323) ----
TRIGGER_K = 0.30        # 迈步触发步距系数:trigger = 0.30×(v/步频),速度自适应
TRIGGER_MIN_K = 0.10    # 触发下限(×reach):静止/微动时不乱步
TRIGGER_MAX_K = 0.40    # 触发上限(×reach):冲刺时防一步跨过可达域
REST_CAP_K = 0.78       # 静息位钳制(×reach):相位锚定下支撑相峰值=静息距,
                        # 0.78 给过拉伸阀(0.88)留 0.10×reach 结构余量
LAND_CAP_K = 0.86       # 摆动落点钳制(×reach):必须低于 OVER_K——
                        # 落足距离不得一落地就超过安全阀(阶序:rest<land<over)
LAND_TOUCH_K = 0.88     # 落足瞬间按当前髋位再收一次(=OVER_K,防落足即超阀)
OVER_K = 0.88           # 过拉伸安全阀:髋足距超 reach×此值立即迈步(解除组互锁;
                        # 相位锚定下正常步态不触发,仅姿态异常时兜底)
FF_GAIN = 0.60          # 落点速度前馈增益(摆动期间身体前进量补偿,余量防加速过冲)
EMERGENCY_ERR_K = 3.0   # 应急旁路:err > max(此值×trigger, 1.3×s) 无视组窗
EMERGENCY_STRIDE_K = 1.3  # 应急旁路的步距项:稳态 err 峰值=1.0×s,1.3×s 保证
                          # 匀速直行时组窗先于应急触发(互锁不被旁路破坏)
# 抬腿内折弧系数(前/中/后腿对):后腿蹬伸弧最大,呈扇形层次(deskbug LIFT_K)
LIFT_K = (0.30, 0.22, 0.38)
SWING_DUR_MIN, SWING_DUR_MAX = 1.0 / 60.0, 0.30   # 摆动时长兜底钳制(s):正常步态
                            # 由相位给出(见 update),这两个常数只作数值兜底
                            # (gait_sim 训练仿真器仍引用,保留符号)
SWING_WIN_K = 0.70      # 摆动起步可行窗占本组摆动窗的比例(防御性上限):腿只允许
                        # 在摆动窗的**前 SWING_WIN_K 段**起步。正常步态起摆必然落在
                        # 窗开后一帧内(step≈0.13~0.24 < 0.3×(1−duty)),此约束不
                        # 生效;保留以实现 F2 冻结接口 swing_win_k 的消费语义。
SWING_TIMEOUT_S = 0.30  # 摆动相位兜底超时(s;相位停转的异常场景解除卡死)
LAND_GRACE_TAU = 0.05   # r24:静止/僵住入场在途摆动腿的快收时间常数(τ,
                        # 首帧收 ~25%、~150ms 收敛;替代旧版单帧瞬降的
                        # ~100px+ 姿态跳,又不拖长"惊觉僵住"的观感)
# 差速(转弯内外侧步幅差,GaitProfile 键 diff_k_in/diff_k_out;0=对称)
DIFF_K_IN, DIFF_K_OUT = 0.20, 0.20
# metachronal 波状相位偏移(度;GaitProfile 键 metachronal_deg):低速档逐对
# 递进(前→后腿对相位滞后 δ×对序号,近虫波状步;默认 0=纯三角,不改现状)
METACHRONAL_DEG = 0.0


class _Foot:
    """单足状态(足端世界坐标 + 迈步状态机)。"""
    __slots__ = ("rest_local", "attach_local", "reach", "group", "pair",
                 "fwd_bias", "world", "state", "t", "dur", "start", "target",
                 "lift", "armed", "ph_start", "ph_span")
    PLANTED, SWING = 0, 1

    def __init__(self, attach: tuple[float, float], home: tuple[float, float],
                 reach: float, group: int, pair: int) -> None:
        self.attach_local = attach
        self.reach = max(1e-3, reach)
        # 静息位钳制 REST_CAP_K×reach:home 落在髋(attach)同侧,超程沿方向收回
        d = math.hypot(home[0] - attach[0], home[1] - attach[1])
        cap = REST_CAP_K * self.reach
        if d > cap and d > 1e-6:
            k = cap / d
            home = (attach[0] + (home[0] - attach[0]) * k,
                    attach[1] + (home[1] - attach[1]) * k)
        self.rest_local = home
        self.group = int(group)
        self.pair = pair
        # 扫掠方向系数(相位锚定):静息足向的 x 分量 < −0.1(朝后)→ b=1,
        # 落点锚前移一个步距、支撑相收在 home;前向/侧向腿 b=0,落点=home。
        rx, ry = home[0] - attach[0], home[1] - attach[1]
        d = math.hypot(rx, ry)
        self.fwd_bias = 1.0 if (d > 1e-6 and rx / d < -0.10) else 0.0
        self.world = (0.0, 0.0)       # 足端世界坐标(支撑相恒定)
        self.state = _Foot.PLANTED
        self.t = 0.0                  # 摆动已历时(s;仅兜底超时用)
        self.dur = 0.12               # 本次摆动兜底时长(s)
        self.start = (0.0, 0.0)       # 摆动起点(世界)
        self.target = (0.0, 0.0)      # 落点(世界)
        self.lift = 0.0               # 当前抬起量(px,渲染/IK 用)
        self.armed = True             # 本摆动窗内是否还未迈步(每窗限一步)
        self.ph_start = 0.0           # 本次摆动起摆相位
        self.ph_span = 0.5            # 本次摆动**锁存**的相位跨度 = min(到本组回卷,
                                      # 名义宽度 1−duty):窗内起摆取回卷(两组无缝
                                      # 交接,duty = profile),提前起摆取名义宽度
                                      # (摆动长度有界,不越入对组窗)。必须锁存,
                                      # 不可每帧用实时 duty 重算——变速时跨度中途
                                      # 跳变会让 p 回退(足端倒走)

    def reset(self, world: tuple[float, float]) -> None:
        """足端重新种到 world(初始化/瞬移/落地收腿)。"""
        self.world = world
        self.state = _Foot.PLANTED
        self.t = 0.0
        self.start = world
        self.target = world
        self.lift = 0.0
        self.armed = True
        self.ph_start = 0.0
        self.ph_span = 0.5


class TripodGait:
    """世界钉足三角步态控制器。参数从 species PARAMS 读取,缺失时回退默认。

    步态常数可被 GaitProfile(F2 冻结点,body/gait_profile.py)注入:
      gait.apply_profile(profile_dict)   # 缺失键回退默认,损坏整包回退
    训练系统(scratch/gaitsim)与运行时共用同一套数学,保证一致性。

    用法(由 GenericInsectBody 驱动):
      gait.bind_legs(kin_list, group_list)   # 用 IK 求解器的 attach/home/reach 绑定
      gait.reset(pos, heading)               # 种脚(初始化/飞行/瞬移)
      gait.update(dt, speed, pos, heading)   # 每帧推进(仅地面)
      lx, ly, lift = gait.foot_local(i, pos, heading)  # 供 IK 的足端身体局部坐标
    """

    def __init__(self, params: dict) -> None:
        self.body_len = max(1.0, float(params.get("body_len", 50.0)))
        self.cruise = max(1.0, float(params.get("cruise", 150.0)))    # px/s
        self.sprint = max(self.cruise + 1.0, float(params.get("sprint", 800.0)))
        self.stride_amp = float(params.get("stride_amp", 0.32 * self.body_len * 0.35))
        self.lift_amp = float(params.get("stride_lift", max(1.5, 0.16 * self.stride_amp)))
        # ---- 步态常数(GaitProfile 可覆盖;默认=本文件常量) ----
        self.hz_rest, self.hz_cruise, self.hz_sprint = HZ_REST, HZ_CRUISE, HZ_SPRINT
        self.duty_patrol, self.duty_cruise, self.duty_sprint = \
            DUTY_PATROL, DUTY_CRUISE, DUTY_SPRINT
        self.group_phase = dict(GROUP_PHASE)
        self.trigger_k, self.trigger_min_k, self.trigger_max_k = \
            TRIGGER_K, TRIGGER_MIN_K, TRIGGER_MAX_K
        self.rest_cap_k, self.land_cap_k, self.over_k = REST_CAP_K, LAND_CAP_K, OVER_K
        self.land_touch_k = LAND_TOUCH_K
        self.ff_gain = FF_GAIN
        self.emergency_err_k, self.emergency_stride_k = \
            EMERGENCY_ERR_K, EMERGENCY_STRIDE_K
        self.lift_k = tuple(LIFT_K)
        self.swing_win_k = SWING_WIN_K
        self.diff_k_in, self.diff_k_out = DIFF_K_IN, DIFF_K_OUT
        self.metachronal_deg = METACHRONAL_DEG
        self.phase = 0.0            # 组间相位约束相位器 [0,1)
        self.speed = 0.0            # 上次推进时的速度(px/s)
        # 步长缩放(单元 B2 情绪→步态唯一消费点;1.0 = 惰性,逐位不变):
        # <1 时**只削步长**——下锚步距 s = v/hz 与步频/duty 的查表自变量同时
        # 按 v/stride_scale 还原为"驱动速度",故能量受限的个体步长变短而
        # 节奏(step_hz/duty)不变(见 body/base.py set_emotion_gait)。
        self.stride_scale = 1.0
        self._feet: list[_Foot] = []
        # 起摆原因计数(观测用,开销可忽略;训练系统/QF 排障读这里)
        self.debug_fires = {"window": 0, "over": 0, "emergency": 0}

    # ---------------- GaitProfile 注入(F2) ----------------
    def apply_profile(self, profile: dict | None) -> None:
        """把 GaitProfile JSON 的 params 子树注入步态常数(缺失键回退默认)。

        只接受有限白名单键,非法值(类型/量纲)一律忽略——损坏配置不得
        破坏运行时(回退=本文件默认,与历史行为一致)。
        """
        if not isinstance(profile, dict):
            return
        p = profile.get("params", profile)   # 允许传整包或 params 子树
        if not isinstance(p, dict):
            return

        def _num(key: str, cur: float, lo: float, hi: float) -> float:
            try:
                v = float(p[key])
            except (KeyError, TypeError, ValueError):
                return cur
            return v if lo <= v <= hi else cur

        def _tuple3(key: str, cur: tuple) -> tuple:
            try:
                v = p[key]
                t = (float(v[0]), float(v[1]), float(v[2]))
            except (KeyError, TypeError, ValueError, IndexError):
                return cur
            return t if all(math.isfinite(x) for x in t) else cur

        self.hz_rest = _num("hz_rest", self.hz_rest, 0.5, 20.0)
        self.hz_cruise = _num("hz_cruise", self.hz_cruise, 1.0, 25.0)
        self.hz_sprint = _num("hz_sprint", self.hz_sprint, 2.0, 40.0)
        self.duty_patrol = _num("duty_patrol", self.duty_patrol, 0.3, 0.95)
        self.duty_cruise = _num("duty_cruise", self.duty_cruise, 0.3, 0.95)
        self.duty_sprint = _num("duty_sprint", self.duty_sprint, 0.3, 0.95)
        self.trigger_k = _num("trigger_k", self.trigger_k, 0.05, 0.6)
        self.trigger_min_k = _num("trigger_min_k", self.trigger_min_k, 0.02, 0.3)
        self.trigger_max_k = _num("trigger_max_k", self.trigger_max_k, 0.1, 0.6)
        self.rest_cap_k = _num("rest_cap_k", self.rest_cap_k, 0.5, 0.88)
        self.land_cap_k = _num("land_cap_k", self.land_cap_k, 0.5, 0.88)
        self.over_k = _num("over_k", self.over_k, 0.8, 0.95)
        self.land_touch_k = _num("land_touch_k", self.land_touch_k,
                                 self.over_k - 0.02, self.over_k)
        self.ff_gain = _num("ff_gain", self.ff_gain, 0.0, 1.2)
        self.emergency_err_k = _num("emergency_err_k", self.emergency_err_k, 1.5, 8.0)
        self.emergency_stride_k = _num("emergency_stride_k",
                                       self.emergency_stride_k, 1.05, 3.0)
        self.swing_win_k = _num("swing_win_k", self.swing_win_k, 0.3, 0.95)
        self.diff_k_in = _num("diff_k_in", self.diff_k_in, 0.0, 0.6)
        self.diff_k_out = _num("diff_k_out", self.diff_k_out, 0.0, 0.6)
        self.metachronal_deg = _num("metachronal_deg", self.metachronal_deg, -25.0, 25.0)
        self.lift_k = _tuple3("lift_k", self.lift_k)
        try:
            gp = p["group_phase"]
            g0, g1 = float(gp[0]), float(gp[1])
            if 0.0 <= g0 < 1.0 and 0.0 <= g1 < 1.0:
                self.group_phase = {0: g0, 1: g1}
        except (KeyError, TypeError, ValueError, IndexError):
            pass

    # ---------------- 绑定 / 种脚 ----------------
    def bind_legs(self, kin_list, group_list) -> None:
        """与腿 IK 求解器一一绑定(attach/home/l1+l2 取自 LegKinematics)。"""
        self._feet = [_Foot(kin.attach, kin.home, kin.reach, grp, i % 3)
                      for i, (kin, grp) in enumerate(zip(kin_list, group_list))]

    def reset(self, pos: tuple[float, float], heading: float) -> None:
        """全部足端种到当前静息位的世界坐标(步态从"标准站姿"重新开始)。"""
        ch, sh = math.cos(heading), math.sin(heading)
        for f in self._feet:
            lx, ly = f.rest_local
            f.reset((pos[0] + lx * ch - ly * sh, pos[1] + lx * sh + ly * ch))

    # ---------------- 速度 → 步频 / duty(摆动窗调度用) ----------------
    def step_hz(self, speed: float | None = None) -> float:
        """步频(Hz):巡航段 3→8 Hz 线性,冲刺段 8→14 Hz 线性;全程单调。

        stride_scale<1 时按 v/stride_scale 查表(B2:节奏由**驱动**速度决定,
        能量受限只削步长不削步频)。=1.0 时该式被跳过 → 与历史逐位一致。
        """
        v = self.speed if speed is None else speed
        if self.stride_scale != 1.0:
            v = v / max(0.2, self.stride_scale)
        v_bl = max(0.0, v) / self.body_len
        cruise_bl = self.cruise / self.body_len
        sprint_bl = self.sprint / self.body_len
        if v_bl <= cruise_bl:
            return lerp(self.hz_rest, self.hz_cruise, v_bl / cruise_bl)
        return lerp(self.hz_cruise, self.hz_sprint,
                    clamp((v_bl - cruise_bl) / max(sprint_bl - cruise_bl, 1e-6)))

    def duty(self, speed: float | None = None) -> float:
        """占空系数:0.65(停/慢)→ 0.50(巡航)→ 0.42(冲刺);摆动窗 = 1−duty。

        与 step_hz 同口径查表(stride_scale 还原驱动速度)→ B2 的步长调制
        不改变 duty(守住 test_acceptance_final 的 [0.42, 0.50] 带)。
        """
        v = self.speed if speed is None else speed
        if self.stride_scale != 1.0:
            v = v / max(0.2, self.stride_scale)
        v_bl = max(0.0, v) / self.body_len
        cruise_bl = self.cruise / self.body_len
        sprint_bl = self.sprint / self.body_len
        if v_bl <= cruise_bl:
            return lerp(self.duty_patrol, self.duty_cruise, v_bl / cruise_bl)
        return lerp(self.duty_cruise, self.duty_sprint,
                    clamp((v_bl - cruise_bl) / max(sprint_bl - cruise_bl, 1e-6)))

    # ---------------- 主推进 ----------------
    def update(self, dt: float, speed: float,
               pos: tuple[float, float], heading: float,
               turn_norm: float = 0.0) -> None:
        """推进世界钉足步态(帧率无关;仅在地面爬行时调用)。

        - 相位器按 step_hz 推进,提供两组错开 0.5 的摆动窗(组间约束);
        - PLANTED:足端世界坐标不动;相对**相位锚定落点锚**的偏差超 trigger
          (且本组窗开且本窗内未迈步)或应急旁路/过拉伸安全阀 → 起摆;
        - SWING:贝塞尔插值 start→target(中点向髋收拢 = 抬腿内折),
          lift 呈正弦包络;落足后足端重新钉死;
        - turn_norm:归一化转向强度(−1~1,base.py 供入),用于内外侧
          步幅差速(GaitProfile 键 diff_k_in/out;转弯外侧腿步幅放大、
          内侧缩小,弧线行走的六足自然差速)。
        """
        dt = max(0.0, dt)
        self.speed = speed
        hz = self.step_hz(speed)
        duty = self.duty(speed)
        # 相位时钟只在**行走中**推进:相位是摆动窗(占空)的唯一判据,若静止仍
        # 推进,窗开就会在零位移下凭空迈步(test_body「静止不乱步」)。速度**归零
        # 且不转向**(急停/逃逸"惊觉僵住"段:base.py 置 scramble=0)时,手上的
        # 摆动**当场落足**:否则腿会悬空跨过整段僵住期(相位停转 → 只能等 0.3s
        # 兜底超时),恢复行走后在采样帧里继续"移动",把实测 duty 压低且逐次
        # 抖动(fly sprint QF 曾量到 0.287,而 profile = 0.42)。保留 turn_norm≠0
        # 时不落足:原地转向必须靠抬腿重摆(test_body「位移触发迈步」)。
        walking = speed > 1e-6
        if walking or any(f.state == _Foot.SWING for f in self._feet):
            self.phase = (self.phase + hz * max(0.0, dt)) % 1.0
        ch, sh = math.cos(heading), math.sin(heading)
        if not walking and abs(turn_norm) < 1e-6:
            # r24 转向连续性:瞬降 → 快收(τ=30ms,~90ms 收敛)。旧版进入
            # 静止/僵住的首帧把所有摆动腿**瞬移**到落点——活体取帧实测单帧
            # ~100-180px 姿态跳(逃逸"惊觉僵住"入场肉眼可见,scratch/_r24_turn
            # 事件 i=198)。改为向落点指数收敛的连续收腿:不产生新迈步(触发
            # 条件不变),在途摆动连续落地;相位冻结点由下方 SWING 分支跳过,
            # 不会覆写本通道。保留原钳制口径(land_touch 内收)。
            k = 1.0 - math.exp(-dt / LAND_GRACE_TAU)
            for f in self._feet:
                if f.state != _Foot.SWING:
                    continue
                ax, ay = f.attach_local
                hip = (pos[0] + ax * ch - ay * sh, pos[1] + ax * sh + ay * ch)
                dx, dy = f.target[0] - hip[0], f.target[1] - hip[1]
                d = math.hypot(dx, dy)
                if d > self.land_touch_k * f.reach:
                    kk = self.land_touch_k * f.reach / d
                    lx, ly = hip[0] + dx * kk, hip[1] + dy * kk
                else:
                    lx, ly = f.target
                f.world = (f.world[0] + (lx - f.world[0]) * k,
                           f.world[1] + (ly - f.world[1]) * k)
                f.lift *= (1.0 - k)
                if math.hypot(lx - f.world[0], ly - f.world[1]) < 0.5:
                    f.world = (lx, ly)
                    f.state = _Foot.PLANTED
                    f.lift = 0.0
        vlx, vly = ch * speed, sh * speed          # 速度前馈向量(px/s)
        stride = speed / max(hz, 1e-3)             # 自然步距 s = v/步频(px)
        # ---- 占空 = 相位量(定义式 duty = 支撑时长/周期,支撑相 = 1 − 摆动相):
        #   本组摆动区间 = 相位 [duty, 1):起摆相位 ≥ duty、落足 = 相位回卷。
        #   duty(实测) = 1 − (S_A+S_B)/(2N)(S_g = 组 g 摆动帧数、N = 总帧数):
        #   两组摆动窗在 duty=0.5 时首尾相接,而回卷正是两组共同的边界 ⇒
        #   "对组落足帧 = 本组起摆帧"同一帧完成**无缝交接** ⇒ duty ≡ 0.50;
        #   duty_profile 0.42(冲刺)时两窗本就重叠 ⇒ S_A+S_B > N ⇒ duty < 0.50。
        #   ① 门开相位 = duty,**不得提前**(提前半步会让起摆相位落进对组摆动窗,
        #      三角互锁被破坏:test_body both_groups==0)。
        #   ② 起摆**不得再要求 err>trig**(会把起摆推迟到 err 积累够大,实测起摆
        #      组相位均值 0.575 → 摆动被截短、duty 抬到 0.526/0.562)。
        #   ③ 行走中**不得走应急旁路提前起摆**:提前起摆的摆动仍以本组回卷收尾
        #      ⇒ 摆动被拉长、越入对组窗口 → 实测 sprint 有 29% 帧六足同时离地
        #      (nsw=6)把 duty 压到 0.349。旁路只保留给"相位时钟停转"的场合
        #      (静止/原地转向:walking=False)与过拉伸安全阀 over。
        win_open_ph = duty                          # 摆动窗开相位(= duty)
        # 起步可行窗:窗的前 swing_win_k 段(防御性上限:正常步态起摆必落在
        # 窗开后一帧内,此约束不生效;保留该键消费以维持 F2 冻结接口语义)
        late_ph = duty + self.swing_win_k * (1.0 - duty)

        for f in self._feet:
            # 相位锚定落点锚(身体系):home + s·b·x̂(身体系 x̂=(1,0),
            # b=扫掠方向系数);差速:转弯外侧步幅放大、内侧缩小
            b = f.fwd_bias
            if turn_norm != 0.0:
                side = 1.0 if f.rest_local[1] >= 0.0 else -1.0
                outer = 1.0 if side * turn_norm > 0.0 else -1.0
                kdiff = self.diff_k_out if outer > 0.0 else self.diff_k_in
                s_eff = stride * (1.0 + kdiff * outer * abs(turn_norm))
                s_eff = max(0.0, s_eff)
            else:
                s_eff = stride
            # 静息位/落点锚/髋的世界坐标(每帧随身体刚体变换)
            rx, ry = f.rest_local
            abx, aby = rx + s_eff * b, ry                         # 锚(身体系)
            ideal = (pos[0] + abx * ch - aby * sh, pos[1] + abx * sh + aby * ch)
            ax, ay = f.attach_local
            hip = (pos[0] + ax * ch - ay * sh, pos[1] + ax * sh + ay * ch)
            # 本组相相位(含 metachronal 波状偏移)
            wave = math.radians(self.metachronal_deg) \
                * f.pair * (1.0 if f.rest_local[1] >= 0.0 else -1.0)
            ph = (self.phase + self.group_phase.get(f.group, 0.0)
                  + wave / (2.0 * math.pi)) % 1.0

            # 上膛(每窗至多一步):**无条件**在窗关段(ph < win_open_ph)刷新。
            # 曾误置于 PLANTED 分支内 → 摆动尾端跨过 wrap 落到窗开之后的那一帧,
            # 该腿当帧是 SWING、下一帧已是窗内 PLANTED,整个窗关段都没执行到
            # 上膛句 → armed 停在 False → **整窗漏步**(冲刺段实测相位 10.03Hz、
            # 实际迈步 9.68Hz、QF 量到 9.24Hz)。
            if ph < win_open_ph:
                f.armed = True
            if f.state == _Foot.PLANTED:
                err = math.hypot(ideal[0] - f.world[0], ideal[1] - f.world[1])
                over = math.hypot(hip[0] - f.world[0], hip[1] - f.world[1]) \
                    > self.over_k * f.reach
                # 迈步触发(三条并列,任一成立):
                #  ① 相位窗:行走中、本组窗开、本窗尚未迈步 → **立即**迈步。
                #     不再要求 err>trig:相位的 duty 语义就是"支撑相占周期比",
                #     用 err 门槛会推迟起摆、截短摆动(实测 duty 0.526/0.562)。
                #  ② 应急旁路 err > max(emergency_err_k×trig, emergency_stride_k
                #     ×s):急转/变速/原地转向时相位于窗无关地补步(静止时相位
                #     冻结,原地转向只靠此路;1.3×s 保证匀速直行时组窗先触发,
                #     互锁不被旁路破坏);
                #  ③ 过拉伸安全阀 over:髋足距超 reach×OVER_K,姿态异常兜底。
                window_open = win_open_ph <= ph < late_ph
                trig = self._trigger(hz, f.reach)
                emergency = max(self.emergency_err_k * trig,
                                self.emergency_stride_k * stride)
                # 行走中只认相位窗与过拉伸阀,且**一窗至多一步**——CPG 窗是
                # 步频的物理上限:过拉伸阀若不占窗,减速/急转瞬态中落地即再摆,
                # 同窗双步会把实测步频顶到相位上限之上(果蝇冲刺实测 15.22Hz
                # > 相位 15.0Hz 即此因)。静止/原地转向时相位时钟停转、窗口永不
                # 开启,应急旁路**不得**要求 armed(否则冻住的腿永不再迈);
                # 过拉伸阀在静止时同理不要求 armed。
                over_fire = over and (f.armed or not walking)
                emg_fire = err > emergency and not walking
                fire = over_fire or emg_fire
                if not fire and walking and window_open and f.armed:
                    fire = True
                if fire:
                    if over_fire:
                        self.debug_fires["over"] += 1
                    elif emg_fire:
                        self.debug_fires["emergency"] += 1
                    else:
                        self.debug_fires["window"] += 1
                if fire:
                    # 起摆时刻 = 本组摆动窗开启后的**第一帧**(ph ≥ duty):两组
                    # 摆动窗在 duty=0.5 时首尾相接,起摆帧即对组的落足帧 ⇒ 无缝
                    # (见 SWING 分支)。**不做**任何帧数 dither / 提前起摆:
                    # 帧数 dither 会制造 1 帧全足支撑空档把 duty 抬到 0.53。
                    f.state = _Foot.SWING
                    f.t = 0.0
                    # 摆动时长 = 名义 (1−duty)/hz;仅作**落点速度前馈的时长**
                    # (实际进度由相位给出)。曾误置为 SWING_TIMEOUT_S(0.30s)→
                    # 前馈暴冲(果蝇后腿落点被推到锚前方 ~12px)→ err 先降后升,
                    # 窗口开时 err<trig → 后腿隔窗才迈步(duty 0.58/0.55)。
                    f.dur = (1.0 - duty) / max(hz, 1e-3)
                    f.ph_start = ph
                    # 摆动相位跨度 = min(到本组回卷, 名义宽度 1−duty):
                    #   窗内起摆(ph_start ≥ duty)⇒ 1−ph_start ≤ 1−duty ⇒ 取回卷,
                    #   两组在回卷帧完成无缝交接 ⇒ duty 精确 = profile;
                    #   提前起摆(过拉伸阀 over / 相位停转时的应急旁路)⇒ 回卷还很远,
                    #   若仍以回卷收尾会把摆动拉长、越入对组窗 → 实测 sprint 29% 帧
                    #   六足同时离地(duty 0.349);取名义宽度 1−duty 则摆动长度有界,
                    #   不会越过对组窗口。
                    f.ph_span = max(min(1.0 - ph, 1.0 - duty, 0.5), 1e-6)
                    f.armed = False
                    f.start = f.world
                    # 落点 = 当前落点锚 + 速度前馈(摆动期间身体前进量×增益;
                    # 相位锚定下落足时髋-足距 = |rest + s·b·x̂ − 0.4·v·dur·x̂|,
                    # 对任意腿对 ≤ REST_CAP_K×reach + 步距余量,与速度弱相关)
                    tx = ideal[0] + vlx * f.dur * self.ff_gain
                    ty = ideal[1] + vly * f.dur * self.ff_gain
                    # 落点钳制在髋周围 land_cap×reach 内(阶序 < 过拉伸阀)
                    dx, dy = tx - hip[0], ty - hip[1]
                    d = math.hypot(dx, dy)
                    if d > self.land_cap_k * f.reach:
                        k = self.land_cap_k * f.reach / d
                        tx, ty = hip[0] + dx * k, hip[1] + dy * k
                    f.target = (tx, ty)
                else:
                    f.lift = 0.0
            else:  # SWING
                if not walking and abs(turn_norm) < 1e-6:
                    continue          # r24:静止/僵住期由上方快收通道独占
                f.t += dt
                # ---- 摆动进度 = **相位进度**,落足 = 本组相位回卷(唯一判据)----
                # 这是唯一能同时满足 duty 门槛与三角互锁的口径:
                #   duty(实测) = 1 − (S_A+S_B)/(2N),其中 S_g = 组 g 的摆动帧数、
                #   N = 总帧数。两组摆动窗在 duty=0.5 时恰好首尾相接,**无缝隙**
                #   要求"下一组起摆帧 = 上一组落足帧",而相位回卷正是两组共同的
                #   边界(组 A 回卷于 φ=1.0、组 B 于 φ=0.5,同一帧),故回卷落足
                #   自动无缝 ⇒ duty ≡ 0.50(巡航;实测 0.499)。
                #   任何"提前落足"(如按帧数 dither)都会在落足帧与对组起摆帧之间
                #   留出 1 帧全足支撑的空档 → duty 被抬高到 0.53(实测)且呈整数
                #   量化(8.05Hz×60fps ⇒ 每周期 7.45 帧,帧数只能取 3/4,
                #   组 A 恒 4、组 B 恒 3 ⇒ duty 恒 0.530,std=0)。
                #   冲刺段(duty_profile 0.42 < 0.5)两组摆动窗**本就重叠**,
                #   回卷落足使 S_A+S_B > N ⇒ duty < 0.5 ✓(QF 冲刺门槛 [0.40,0.50])。
                span = max(f.ph_span, 1e-6)
                prog = (ph - f.ph_start) % 1.0
                p = clamp(prog / span, 0.0, 1.0)
                if f.t >= SWING_TIMEOUT_S:
                    p = 1.0
                ps = p * p * (3.0 - 2.0 * p)        # smoothstep
                mx = f.start[0] + (f.target[0] - f.start[0]) * 0.5
                my = f.start[1] + (f.target[1] - f.start[1]) * 0.5
                # 控制点向髋收拢 → 足端轨迹内折(抬腿弧,腿对差异化)
                k = self.lift_k[f.pair % 3]
                cx = mx + (hip[0] - mx) * k
                cy = my + (hip[1] - my) * k
                u = 1.0 - ps
                f.world = (u * u * f.start[0] + 2 * u * ps * cx + ps * ps * f.target[0],
                           u * u * f.start[1] + 2 * u * ps * cy + ps * ps * f.target[1])
                f.lift = self.lift_amp * math.sin(math.pi * p)
                if p >= 1.0:
                    # 落足:摆动期身体继续前进,最终钳到当前髋位 land_touch 内
                    dx, dy = f.target[0] - hip[0], f.target[1] - hip[1]
                    d = math.hypot(dx, dy)
                    if d > self.land_touch_k * f.reach:
                        kk = self.land_touch_k * f.reach / d
                        f.world = (hip[0] + dx * kk, hip[1] + dy * kk)
                    else:
                        f.world = f.target
                    f.state = _Foot.PLANTED
                    f.lift = 0.0

    def _trigger(self, hz: float, reach: float) -> float:
        """迈步触发阈值:0.30×自然步距(v/hz),钳在 [0.10, 0.40]×reach。

        相位锚定下稳态"本组摆动窗开启时的偏差"= 1.0×自然步距(落点锚随
        身体前进),恒大于 0.30×步距 → 同组腿每窗必迈、组内同步;速度自适应
        保证低速小步/冲刺大步。
        """
        natural = self.speed / max(hz, 1e-3)
        return clamp(self.trigger_k * natural,
                     self.trigger_min_k * reach, self.trigger_max_k * reach)

    # ---------------- 查询 ----------------
    def foot_local(self, index: int, pos: tuple[float, float],
                   heading: float) -> tuple[float, float, float]:
        """第 index 足端的身体局部坐标与抬起量(供腿 IK 求解)。"""
        f = self._feet[index]
        ch, sh = math.cos(heading), math.sin(heading)
        dx, dy = f.world[0] - pos[0], f.world[1] - pos[1]
        # 世界 → 身体局部(逆旋转)
        return (dx * ch + dy * sh, -dx * sh + dy * ch, f.lift)

    def foot_world(self, index: int) -> tuple[float, float]:
        """第 index 足端的**世界坐标**(支撑相恒定——不滑步断言的直接依据)。"""
        return self._feet[index].world

    def leg_phase(self, group: int) -> float:
        """某组腿的摆动窗相位:组内同相,组间差 0.5(诊断/测试用)。"""
        return (self.phase + self.group_phase.get(int(group), 0.0)) % 1.0

    def is_swinging(self, index: int) -> bool:
        return self._feet[index].state == _Foot.SWING
