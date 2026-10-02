"""向量化降阶步态仿真器(训练系统专用,Agent-2;运行时零依赖本模块)。

== 与 body/gait.py 的一致性契约(冻结点 F2 §1 规则 4) ==
本模块是 D2 §3.4 规格的"2D 俯视运动学 + 世界钉足约束"批量仿真器:
P 个个体 × 6 腿全部向量化,身体 = 刚体平面运动(本仿真器取直线爬行,
heading=0、turn_norm=0、speed>0 恒成立),支撑足世界钉死,身体位置按指令
速度积分(与 base.py `_integrate` 同序:先积分位置、再以新位置调 update)。

规则常数**不从本文件复刻**:
  * 参数解析语义   → gait.TripodGait(params) + apply_profile(profile)
                     (逐键白名单/量程校验与运行时一字不差,消除二重实现);
  * 静息位钳制/扫掠系数 → gait._Foot.__init__(REST_CAP_K 钳制 + fwd_bias);
  * 触发/钳制/摆窗常数   → 经 TripodGait 解析后的实例属性(resolve_gait_constants);
  * 步频/占空映射        → TripodGait.step_hz / duty 的分段线性式(逐式复刻);
  * 迈步状态机           → TripodGait.update 逐式复刻(见 rollout_batch 内联注释)。

因此 P=1、无扰动(seed=None)、heading=0 时本仿真器的逐帧足端世界坐标与
运行时 TripodGait 一致到浮点尾数(math.hypot 与 np.hypot 的 1 ulp 量级,
实测最大 ~1e-13px,远小于 <1e-6px 验收线;tests/test_gait_training.py ①)。

== 同步记录(gait.py 摆动口径修复,fixed after 2026-09-15 上午) ==
gait.py 的摆动改为**相位量驱动**后,本文件旧版的"时长驱动"状态机已整体重写
(旧版实例:摆动进度 p = t/dur、起摆附加 err>trig 闸门、无 armed 每窗一步、
行走中允许应急旁路、dur = swing_win_k×(1−duty)/hz 再钳 [1/60,0.30])。现行
口径(逐条对应 gait.py 现行行号):
  1. 摆动窗开 = `duty <= ph < duty + swing_win_k×(1−duty)`   (gait.py:353,356,402)
  2. 上膛 armed:窗关段(ph < duty)**无条件**刷新为 True,起摆后置 False
     (每窗至多一步;gait.py:387-388,432);
  3. 起摆 = 行走中 & 窗开 & armed(无 err>trig 闸门);应急旁路只在
     "相位时钟停转"(speed≈0 且 turn_norm≈0)时生效 —— 本仿真器恒为行走态,
     故起摆 = (过拉伸安全阀 over) | (窗开 & armed)   (gait.py:408-410);
  4. 摆动相位跨度锁存 `ph_span = max(min(1−ph, 1−duty, 0.5), 1e-6)` (gait.py:431);
  5. 摆动进度 = 相位进度 `p = clamp(((ph − ph_start) % 1) / ph_span, 0, 1)`,
     兜底 `t ≥ SWING_TIMEOUT_S → p = 1`                     (gait.py:463-467);
  6. `dur` 只作**落点速度前馈的时长**:`dur = (1−duty)/max(hz,1e-3)`
     (不再乘 swing_win_k、不再钳 [SWING_DUR_MIN, SWING_DUR_MAX];
     gait.py:87-89 关于"gait_sim 仍引用 SWING_DUR_MIN/MAX"的注释已随之失效
     —— 本文件不再引用这两个符号,留待 gait.py 侧清理);
  7. 贝塞尔内折弧 / 落点钳制 land_cap / 落足钳制 land_touch 逐式同 gait.py:434-445、
     469-489;
  8. `rest_cap_k` 是**惰性键**:gait.py 只在 apply_profile 里存下它,update 从不
     消费(静态站姿的 reach 钳制用的是模块常量 REST_CAP_K,在 _Foot.__init__ 里),
     本仿真器同样不消费 ⇒ 两侧仍然一致(该维在 ζ 中按先验锚定,见 _ANCHOR_KEYS)。

== AG2 终审差异清单(2026-09-16,逐函数对照 gait.py,全部已修) ==
  A1 [致命] 起摆分支引用未定义的 `ideal_y`(NameError,首次触发起摆即崩)
     → 现 `ideal_y = broadcast(anchor_y)`,与 gait.py `ideal[1]`(=静息 y,
       heading=0)逐位一致;
  A2 [语义] 过拉伸阀触发未消耗 armed(旧:`fire = over|窗开&armed`),而
     gait.py:414 `over_fire = over and (f.armed or not walking)` —— 行走中
     一窗一步对旁路同样成立 → 现 `(over & armed) | (窗开 & armed)`;
  A3 [训练效力] group_phase/metachronal_deg/lift_k 原从共享 consts 读取,
     逐个体 θ 维在批量评估中无梯度(纯漂移)→ 改为逐个体从 pv 注入;
     P=1 时与 consts 路径数值逐位一致(consts 亦来自同一 profile 解析);
  A4 [状态残留] 落足时 t_sw 清零,而 gait.py 只在起摆时 f.t=0 → 对齐删除
     (无可观测差异,纯内部状态对齐);
  A5 [诊断口径] steps_emergency = "窗外起摆帧数"(fire & ~window_open),与
     gait.py debug_fires["over"](over 触发,含窗内)口径不同 —— 仅诊断
     用,不影响轨迹与一致性;保留仿真器口径(互锁破坏的直接度量)。
  A6 [浮点结合律,二审发现] 组相位合成 ph = phase + gp + wave/τ:gait.py 按
     ((phase + gp) + wave/τ) 逐项相加,本仿真器旧版预合成 gp+wave 再加
     phase —— IEEE 加法不可结合,metachronal_deg≠0 时产生 1 ulp 的 ph 差,
     在摆动窗边界翻转触发(训练后参数常驻窗沿,实测 25 帧·足摆动掩码失配,
     由 train_gait 落盘前一致性自检拦截)。修复:逐项相加,顺序同 gait.py。
     默认参数(metachronal=0)下 wave=0,两口径逐位相同,故一审未暴露。

== 降阶假设(D2 §3.4,拟真度由惩罚项与文献比对兜底) ==
  * 无惯性/接触力:身体按指令速度前进(与运行时运动学同构);因此 D2 §3.3 的
    速度项 v̄/v_target 在本模型下**恒为 1.0、无判别力**(ξ 中仍保留以对齐规格,
    真正的梯度来自滑移/jerk/体高/静息偏差/互锁/三角违反项 —— 见报告 §3.2);
  * 适应度只读轨迹量,**永不反馈修正动力学**(测量不改变轨迹,一致性不被破坏);
  * "翻倒/失速"判定:支撑足髋距 > 真实 reach(IK 不可达=物理上必然打滑/绊倒)
    时该帧位移在 v̄ 统计中记零(推进力丢失),轨迹本身不变;
  * 渲染级滑移:支撑足髋距 > LegKinematics.d_max(可达环带上界)时渲染 IK
    会钳制→足底视觉滑动,按超程像素累计为 slip;
  * 体高:直腿近似 H=√(L_eff²−d²)(L_eff=0.95×(l1+l2)),支撑足均值,
    只取波动 std/BL 作惩罚(体高波动项,D2 §3.3)。

== 适应度(D2 §3.3 原式七项 + 结构/文献带罚项) ==
  F = w_v·min(v̄/v_target,1.2) + w_st·stab − w_sl·slip_ratio − w_j·jerk_norm
      − w_h·height_var − w_p·rest_dev − w_f·fall_rate
      − w_il·interlock − w_ms·multiswing − w_sd·stride_band
      − w_bg·both_groups(仅工作域 ≤1.5×cruise,v1.2)− w_anchor·anchor
  权重:D2 §3.3 起步值 w_v=1.0 w_st=0.5 w_sl=0.8 w_j=0.2 w_h=0.2 w_p=0.3 w_f=10;
  新增三项与先验锚定的公式与理由见 fitness_from_metrics 内注释与报告 §3.2。
  多档防作弊(D2 §3.5):F = min over 评估速度档 × mean over 域随机化种子;
  域随机化 = 节段长 ±5% / 摩擦(滑移阈)±10% / 落点方向 ±1° / 初相扰动,
  同一种子内逐足固定(制造误差语义),seed=None 时全部为零(=一致性模式)。

== 训练向量 θ(29 维,与 GaitProfile params 白名单一一对应) ==
见 PARAM_SPEC;decode/encode 与 neuropet/body/gait_profile.py 的量程相容
(训练界 ⊆ schema 界,文献带作为硬界:巡航 hz∈[3,8]、冲刺 hz∈[10,15]、
duty 0.42~0.50 等,D2 §4 验收线);theta_to_params 负责 θ 行 → 冻结 schema 的
params 子树(group_phase 两维合成 [g0,g1] 列表等)。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from neuropet.body.gait import SWING_TIMEOUT_S, TripodGait, _Foot
from neuropet.body.kinematics import LegKinematics
from neuropet.core.mathutil import clamp

# ===================================================================
# C4 绿线护栏:numpy(训练专用依赖)**不在模块顶层 import**
# ===================================================================
# 本模块住在生产包内(neuropet/body/),但只服务离线训练/验收。旧实现把
# `import numpy as np` 放在模块顶层:生产路径今天不 import 本模块,exe 里因此
# 恰好没有 numpy —— 但那是扇没上锁的门:任何一处新增 import(或打包器改为
# 收集整包)就会把 numpy 树(约 31MB 源码)卷进 exe,打破「单文件、零第三方
# 运行时依赖、约 32MB」这条卖点(同型历史事故见 build_exe.bat 头注)。
# 改为**首次真正用到时才加载**:无 numpy 环境下 `import neuropet.body.gait_sim`
# 不抛(公开符号名/签名全在 —— 注解因 `from __future__ import annotations` 本就
# 不求值,数值体一律经下面的 `np` 代理);只有调用训练数值函数才要求 numpy,
# 缺失时给明确指引而非 AttributeError。
class _LazyNumpy:
    """numpy 懒加载代理:属性首次访问时 import,并回填本模块 `np` 全局。"""

    def __getattr__(self, name: str):
        global np
        try:
            import numpy as _np
        except ImportError as exc:        # 未装 numpy(绿色 exe / 生产环境)
            raise ImportError(
                "neuropet.body.gait_sim 的训练数值函数需要 numpy(离线训练专用"
                "依赖,不随绿色 exe 发布);请先在训练环境 `pip install numpy`。"
            ) from exc
        np = _np                          # 回填:此后访问零代理开销
        return getattr(_np, name)


np = _LazyNumpy()

_TAU = 2.0 * math.pi

# ===================================================================
# 训练向量 θ 规格(29 维):key → (训练下界, 训练上界, 默认值)
# 训练界 ⊆ gait_profile._PARAM_RANGES(schema 界);步频/占空取文献带
# (D2 §4 验收:巡航 3~8Hz、冲刺 10~15Hz、duty 0.42~0.50;果蝇巡航
# 4~8Hz 由 species_overrides 给出)。cap 三键界不相交且 rest<land<touch
# (0.88 默认)≤over,天然满足阶序,无需排序修正。
# ===================================================================
_SPEC_BASE: list[tuple[str, float, float, float]] = [
    # 步频(Hz):rest/cruise/sprint;巡航带 3~8、冲刺带 10~15(文献验收)
    ("hz_rest", 1.5, 6.0, 3.0),
    ("hz_cruise", 3.0, 8.0, 8.0),
    ("hz_sprint", 10.0, 15.0, 15.0),
    # 占空:θ 界取验收带内子域;**v1.3 互锁结构约束**(project_params/
    # project_pv)把 cruise/sprint 两结点下钳到带上沿 0.5(工作域
    # both_groups=0 的可行域塌缩点,依据见 project_params ②)——θ 维保留
    # 以维持 29 维向量与 decode/encode 往返,搜索中经 project_pv 落在可行面。
    ("duty_patrol", 0.55, 0.75, 0.65),
    ("duty_cruise", 0.45, 0.50, 0.50),
    ("duty_sprint", 0.42, 0.46, 0.42),
    # 组相位:组 0 近 0、组 1 近 0.5(三角先验附近搜索)
    ("group_phase_0", 0.0, 0.05, 0.0),
    ("group_phase_1", 0.45, 0.55, 0.5),
    # 触发/钳制:阶序 rest(0.68~0.80) < land(0.78~0.86) < touch(0.88 固定)
    # ≤ over(0.88~0.94);schema 界内
    ("trigger_k", 0.15, 0.50, 0.30),
    ("trigger_min_k", 0.05, 0.20, 0.10),
    ("trigger_max_k", 0.20, 0.50, 0.40),
    ("rest_cap_k", 0.68, 0.80, 0.78),
    ("land_cap_k", 0.78, 0.86, 0.86),
    ("over_k", 0.88, 0.94, 0.88),
    # 前馈(D2:落点速度前馈增益)
    ("ff_gain", 0.30, 1.10, 0.60),
    # 抬腿内折弧(前/中/后)
    ("lift_k_0", 0.10, 0.55, 0.30),
    ("lift_k_1", 0.10, 0.55, 0.22),
    ("lift_k_2", 0.10, 0.55, 0.38),
    # 差速(直线仿真器不识别→先验锚定)
    ("diff_k_in", 0.0, 0.45, 0.20),
    ("diff_k_out", 0.0, 0.45, 0.20),
    # metachronal 波状相位偏移(度)
    ("metachronal_deg", -12.0, 12.0, 0.0),
    # 应急旁路
    ("emergency_err_k", 2.0, 6.0, 3.0),
    ("emergency_stride_k", 1.10, 2.00, 1.30),
    # 摆窗比例
    ("swing_win_k", 0.50, 0.90, 0.70),
    # 体摆(渲染层消费,不识别→锚定)
    ("sway_amp_px", 0.0, 0.01, 0.0),
    ("sway_freq_ratio", 0.70, 1.40, 1.0),
    ("pitch_amp_deg", 0.0, 3.0, 0.0),
    # 微弹性(渲染层消费,不识别→锚定)
    ("sag_tau_ms", 30.0, 90.0, 60.0),
    ("sag_max_deg", 2.0, 6.0, 4.0),
]

# gait.py(TripodGait)不消费、需先验锚定的维:冻结文档统计的 7 维(渲染/姿态层
# 预留:diff/sway/sag)+ 本次审计新发现的 1 个"消费但惰性"键
# **rest_cap_k**(apply_profile 收下,update 从不读;静态站姿钳制用模块常量
# REST_CAP_K)—— 一并锚定,防止它在无梯度方向上随机漂移出有意义的取值。
# AG2 终审追加(A6):直线匀速仿真器中**不可辨识**的 6 个调度维 ——
# trigger_k/min/max(行走中起摆只认相位窗,err 闸门已废)、emergency_err_k/
# stride_k(应急旁路仅"相位停转"时生效,仿真器恒行走)、swing_win_k(起摆
# 必在窗开后一帧,防御性上沿永不触及)。它们在真实行为层(急转/静止/原地
# 转向)仍被消费,但本仿真器观测不到 ⇒ 锚定到随库默认 + 训练器落盘前投影,
# 防止"训练"把它们漂移到无依据的取值。
_ANCHOR_KEYS = ("diff_k_in", "diff_k_out", "sway_amp_px", "sway_freq_ratio",
                "pitch_amp_deg", "sag_tau_ms", "sag_max_deg", "rest_cap_k",
                "trigger_k", "trigger_min_k", "trigger_max_k",
                "emergency_err_k", "emergency_stride_k", "swing_win_k",
                "group_phase_0", "group_phase_1", "metachronal_deg")

# θ 键 → GaitProfile params 键(除 group_phase 两维需合成列表外一一对应)
_THETA_TO_PROFILE: dict[str, str] = {k: k for k in
                                     ("hz_rest", "hz_cruise", "hz_sprint",
                                      "duty_patrol", "duty_cruise", "duty_sprint",
                                      "trigger_k", "trigger_min_k", "trigger_max_k",
                                      "rest_cap_k", "land_cap_k", "over_k",
                                      "ff_gain", "diff_k_in", "diff_k_out",
                                      "metachronal_deg", "emergency_err_k",
                                      "emergency_stride_k", "swing_win_k",
                                      "sway_amp_px", "sway_freq_ratio",
                                      "pitch_amp_deg", "sag_tau_ms", "sag_max_deg")}

# 果蝇文献带覆盖(D2 §4 #9:3 BL/s 时步频 4~8Hz)
_SPECIES_OVERRIDES: dict[str, dict[str, tuple[float, float]]] = {
    "fruitfly": {"hz_cruise": (4.0, 8.0),
                 # 冲刺闭环实测(行为层无种子随机)在 hz_sprint=15.0 时骑线
                 # (观测 14.69~15.30Hz,QF 门 ≤15;实测对相位上限有 +0.4Hz
                 # 级超越量,来自 ESCAPE 突速帧的速度峰)。训练带上限 14.0
                 # 留 1.0Hz 确定性余量。巡航档不受影响。
                 "hz_sprint": (10.0, 14.0)},
}

# θ 维序(PARAM_SPEC 顺序)
THETA_KEYS: list[str] = [row[0] for row in _SPEC_BASE]
N_DIM = len(THETA_KEYS)          # = 29


def param_spec(species: str) -> list[tuple[str, float, float, float]]:
    """按物种给出 θ 规格(默认参数来自随库保守默认,量程内截断)。"""
    from neuropet.body.gait_profile import DEFAULT_PARAMS
    over = _SPECIES_OVERRIDES.get(species, {})
    out = []
    for key, lo, hi, _def in _SPEC_BASE:
        if key in over:
            lo, hi = over[key]
        dv = float(DEFAULT_PARAMS.get(key, _def))
        dv = min(max(dv, lo), hi)
        out.append((key, lo, hi, dv))
    return out


# ---- 适应度归一常数(以默认参数标定) ----
JERK_SCALE = 30.0       # 足端二阶差分 RMS(px)→ 归一 1.0 的尺度(≈0.25BL/帧²量级)
HEIGHT_SCALE = 0.06     # 体高 std/BL → 归一 1.0 的尺度(默认≈0.006~0.02)
W_V, W_ST, W_SL, W_J, W_H, W_P, W_F = 1.0, 0.5, 0.8, 0.2, 0.2, 0.3, 10.0
W_IL = 0.30             # 窗外起摆(互锁破坏)罚权
W_MS = 0.30             # 同时离地 ≥4 足(三角违反)罚权
W_SD = 0.20             # 步长越文献带罚权(D2 §4 #4:0.05~0.35 BL)
W_ANCHOR = 0.05         # 未识别/惰性维的先验锚定权重(防漂移,非 D2 项)
W_BG = 6.0              # 两组同时有摆动足帧占比罚权(fitness v1.1 引入、
                        # v1.2 加权并限域、v1.3 以 duty 结构投影兜底为 0):
                        # test_body「both_groups==0」三角互锁不变量的仿真内
                        # 镜像。v1.0 未计此项,roach/sprint 首训产物把
                        # group_phase_1 推到 0.55 + duty_sprint 0.46(两窗结
                        # 构重叠);v1.1 权重 1.0 只把 fly/cruise 清零,roach
                        # 各档仍残留 5~30% 违帧(其他项收益更大)⇒ v1.2 权重
                        # 6.0;v1.2b 锚定 group_phase 后 roach 仍余 4~8 帧/窗
                        # ——归因:1.5×cruise 档 duty@v<0.5 的**帧量化交接
                        # 竞态**(每组交接 ~50% 概率重叠 1 帧,连续罚项压不住
                        # 离散余量)⇒ v1.3 把 duty 两结点投影到带上沿 0.5
                        # (可行域塌缩点,project_params ②),W_BG 保留作
                        # 运行期安全网。**只作用于工作域**(v_target ≤
                        # 1.5×cruise):蟑螂 ≥9.8BL/s 观赏极速下步距 75~100px
                        # 对前腿静息距 37px 已超腿系几何,阀旁路解互锁是
                        # gait.py OVER_K 的设计语义,冻结默认表在同域同样破
                        # 互锁(实测 @1125px/s both=102/105 帧)——极速域的硬
                        # 判据用 fall_frames(几何不可达=视觉滑步),不用互锁。
STRIDE_BL_LO, STRIDE_BL_HI = 0.05, 0.35     # D2 §4 #4 步长文献带(BL)


def decode_theta(X: np.ndarray, species: str) -> dict[str, np.ndarray]:
    """θ 矩阵 (P,29) ∈[0,1] → 逐键数值数组 (P,);越界分量钳到 [0,1]。"""
    X = np.clip(np.asarray(X, dtype=np.float64), 0.0, 1.0)
    out: dict[str, np.ndarray] = {}
    for j, (key, lo, hi, _d) in enumerate(param_spec(species)):
        out[key] = lo + X[:, j] * (hi - lo)
    return out


def encode_theta(params: dict, species: str) -> np.ndarray:
    """单个体参数字典 → θ 行向量(先验默认的逆映射;测试/起点用)。"""
    theta = np.zeros((1, N_DIM), dtype=np.float64)
    for j, (key, lo, hi, dv) in enumerate(param_spec(species)):
        v = float(params.get(key, dv))
        theta[0, j] = (min(max(v, lo), hi) - lo) / (hi - lo)
    return theta


def default_theta(species: str) -> np.ndarray:
    """θ 起点 = 随库保守默认(量程内截断)。

    注意:θ 的界是**文献验收带**(如 hz_cruise ≤ 8、duty_cruise ≤ 0.50),
    而随库默认恰取在带的上沿,因此默认起点 = 上沿的 1.0(而非带中心)。
    这是设计意图:带内搜索,默认点本身合法。
    """
    from neuropet.body.gait_profile import DEFAULT_PARAMS
    return encode_theta(DEFAULT_PARAMS, species)


def theta_to_params(values: dict) -> dict:
    """θ 解码值(标量或长度 1 的数组)→ GaitProfile params 子树(冻结 schema)。

    键集与 docs/接口冻结_GaitProfile.md §1 白名单一致:group_phase 两维合成
    [g0,g1];lift_k 三维合成列表;其余逐键直通。
    """

    def _f(key: str) -> float:
        v = values[key]
        return float(v[0]) if isinstance(v, np.ndarray) else float(v)

    out = {pkey: _f(tkey) for tkey, pkey in _THETA_TO_PROFILE.items()}
    out["group_phase"] = [_f("group_phase_0"), _f("group_phase_1")]
    out["lift_k"] = [_f("lift_k_0"), _f("lift_k_1"), _f("lift_k_2")]
    return out


# ===================================================================
# 物种几何(静态;一次构建,全训练复用)
# ===================================================================
class SpeciesGeometry:
    """从 species PARAMS 构建静态腿几何(与运行时逐位同源)。

    rest_local / fwd_bias 直接由 gait._Foot 构造读取(复用 REST_CAP_K 钳制
    与扫掠方向系数,零二重实现);d_max(可达环带上界)取自 LegKinematics。
    """

    def __init__(self, species: str, params: dict) -> None:
        self.species = species
        self.body_len = max(1.0, float(params.get("body_len", 50.0)))
        self.cruise = max(1.0, float(params.get("cruise", 150.0)))
        self.sprint = max(self.cruise + 1.0, float(params.get("sprint", 800.0)))
        stride_amp = float(params.get("stride_amp", 0.32 * self.body_len * 0.35))
        self.lift_amp = float(params.get("stride_lift", max(1.5, 0.16 * stride_amp)))
        legs = params["legs"]
        n = len(legs)
        kins = [LegKinematics(leg) for leg in legs]
        groups = [int(leg.get("group", i % 2)) for i, leg in enumerate(legs)]
        # _Foot 构造 = 运行时静息位钳制 + fwd_bias 的唯一事实源
        feet = [_Foot(kin.attach, kin.home, kin.reach, grp, i % 3)
                for i, (kin, grp) in enumerate(zip(kins, groups))]
        self.n_legs = n
        self.rest_local = np.array([f.rest_local for f in feet], dtype=np.float64)  # (6,2)
        self.attach = np.array([f.attach_local for f in feet], dtype=np.float64)    # (6,2)
        self.reach = np.array([f.reach for f in feet], dtype=np.float64)            # (6,)
        self.fwd_bias = np.array([f.fwd_bias for f in feet], dtype=np.float64)      # (6,)
        self.group = np.array(groups, dtype=np.int64)
        self.pair = np.arange(n, dtype=np.int64) % 3
        self.side_sign = np.where(self.rest_local[:, 1] >= 0.0, 1.0, -1.0)          # (6,)
        self.d_max = np.array([k.d_max for k in kins], dtype=np.float64)            # (6,)
        self.reach_true = np.array([k.reach for k in kins], dtype=np.float64)
        # 直腿近似有效长(体高代理;0.95 = 膝微屈经验系数)
        self.l_eff = 0.95 * np.array([k.l1 + k.l2 for k in kins], dtype=np.float64)
        self.pair_of_leg = self.pair.copy()


def resolve_gait_constants(species_params: dict, profile: dict | None) -> dict:
    """经运行时 TripodGait(=apply_profile 白名单语义)解析步态常数。

    这是"仿真器与 gait.py 共享同一数学"的关键一环:参数校验/回退不重复实现,
    直接以运行时对象为单一事实源,读取其解析后的标量属性。
    """
    g = TripodGait(dict(species_params))
    if profile is not None:
        g.apply_profile(profile)
    return {
        "hz_rest": g.hz_rest, "hz_cruise": g.hz_cruise, "hz_sprint": g.hz_sprint,
        "duty_patrol": g.duty_patrol, "duty_cruise": g.duty_cruise,
        "duty_sprint": g.duty_sprint,
        "group_phase": (float(g.group_phase.get(0, 0.0)),
                        float(g.group_phase.get(1, 0.5))),
        "trigger_k": g.trigger_k, "trigger_min_k": g.trigger_min_k,
        "trigger_max_k": g.trigger_max_k,
        "rest_cap_k": g.rest_cap_k, "land_cap_k": g.land_cap_k,
        "land_touch_k": g.land_touch_k, "over_k": g.over_k,
        "ff_gain": g.ff_gain,
        "emergency_err_k": g.emergency_err_k,
        "emergency_stride_k": g.emergency_stride_k,
        "swing_win_k": g.swing_win_k,
        "metachronal_deg": g.metachronal_deg,
        "lift_k": tuple(g.lift_k),
    }


@dataclass
class RolloutMetrics:
    """批量 rollout 的原始统计(全部 (P,) 数组;适应度组装前量)。"""
    v_dist: np.ndarray          # 有效位移(px;翻倒帧记零)
    t_meas: float               # 测量窗时长(s)
    stab_frames: np.ndarray     # COM 在支撑多边形内(且≥3 足)的帧数
    fall_frames: np.ndarray     # 支撑足髋距 > reach(几何不可达)帧数
    slip_px: np.ndarray         # 支撑足渲染级滑移累计(px)
    jerk_sum: np.ndarray        # 足端(身体系)二阶差分平方和
    h_sum: np.ndarray           # 体高代理累计
    h2_sum: np.ndarray          # 体高代理平方累计
    restdev_sum: np.ndarray     # |足端-静息位| 累计(px)
    steps: np.ndarray           # 摆动启动总次数(测窗)
    steps_emergency: np.ndarray # 窗外(过拉伸阀)启动次数 = 互锁破坏诊断
    multiswing_frames: np.ndarray   # 同时离地 ≥4 足的帧数(三角违反)
    both_groups_frames: np.ndarray  # 两组各有 ≥1 足在摆动的帧数(QF both_groups 口径)
    # --- 诊断(冠军报告用) ---
    steps_per_foot: np.ndarray | None = None   # (P,6) 每足步数
    max_hipdist: np.ndarray | None = None      # (P,6) 支撑足髋距峰值
    speed: float = 0.0


@dataclass
class Trace:
    """一致性模式(P=1)下的逐帧记录。"""
    world: list = field(default_factory=list)      # 每帧 (6,2) 足端世界坐标
    new_swings: list = field(default_factory=list) # 每帧新启动摆动的足数
    swinging: list = field(default_factory=list)   # 每帧 (6,) 摆动掩码
    pose_x: list = field(default_factory=list)     # 每帧身体 x


def rollout_batch(geom: SpeciesGeometry, consts: dict,
                  pv: dict[str, np.ndarray], speed: float, seed: int | None,
                  dt: float = 1.0 / 60.0, n_frames: int = 300,
                  warmup_frames: int = 60, v_void_on_fall: bool = True,
                  return_trace: bool = False) -> tuple[RolloutMetrics, Trace | None]:
    """批量推进 n_frames 帧,P 个个体同时评估(全部 (P,6)/(P,) 向量化)。

    pv: decode_theta 的输出(逐键 (P,) 数组)。seed=None → 无域随机化
    (一致性模式,与 gait.py 逐位可比)。逐式对应 gait.TripodGait.update:
      相位器 → 落点锚/髋 → PLANTED(armed 刷新/触发判定/过拉伸阀) → SWING
      (贝塞尔 + 落足钳制),分支顺序与标量版一致(本帧新触发的足当帧不进
      摆动分支,下一帧才推进)。
    """
    P = pv["hz_rest"].shape[0]
    n_legs = geom.n_legs

    # ---- 域随机化(D2 §3.5;seed=None 全零=逐位一致模式) ----
    if seed is None:
        reach_p = np.broadcast_to(geom.reach, (P, n_legs)).copy()
        fric = np.ones((P, n_legs))
        cosd = np.ones((P, n_legs))
        sind = np.zeros((P, n_legs))
        phase0 = np.zeros(P)
    else:
        rng = np.random.default_rng(seed)
        reach_p = geom.reach[None, :] * (1.0 + 0.05 * rng.uniform(-1, 1, (P, n_legs)))
        fric = 1.0 + 0.10 * rng.uniform(-1, 1, (P, n_legs))
        dang = np.radians(1.0) * rng.uniform(-1, 1, (P, n_legs))
        cosd, sind = np.cos(dang), np.sin(dang)
        phase0 = rng.uniform(0.0, 1.0, P)

    # ---- 步频/占空映射(逐式复刻 TripodGait.step_hz / duty;速度标量) ----
    v_bl = max(0.0, speed) / geom.body_len
    cruise_bl = geom.cruise / geom.body_len
    sprint_bl = geom.sprint / geom.body_len
    t1 = clamp(v_bl / cruise_bl)
    t2 = clamp((v_bl - cruise_bl) / max(sprint_bl - cruise_bl, 1e-6))
    cruise_side = v_bl <= cruise_bl
    hz = np.where(cruise_side,
                  pv["hz_rest"] + (pv["hz_cruise"] - pv["hz_rest"]) * t1,
                  pv["hz_cruise"] + (pv["hz_sprint"] - pv["hz_cruise"]) * t2)
    duty = np.where(cruise_side,
                    pv["duty_patrol"] + (pv["duty_cruise"] - pv["duty_patrol"]) * t1,
                    pv["duty_cruise"] + (pv["duty_sprint"] - pv["duty_cruise"]) * t2)
    duty = np.clip(duty, 1e-6, 1.0 - 1e-6)
    duty_c = duty[:, None]                                    # (P,1)
    hz = np.maximum(hz, 1e-3)
    stride = speed / hz                                        # (P,) 自然步距 s
    # 摆动时长(仅作落点速度前馈时长;gait.py:422)
    dur_c = ((1.0 - duty) / hz)[:, None]                       # (P,1)
    vlx = speed                                                # heading=0: ch=1, sh=0
    natural = speed / hz
    trig = np.clip(pv["trigger_k"][:, None] * natural[:, None],
                   pv["trigger_min_k"][:, None] * reach_p,
                   pv["trigger_max_k"][:, None] * reach_p)     # (P,6) 时不变
    emergency = np.maximum(pv["emergency_err_k"][:, None] * trig,
                           pv["emergency_stride_k"][:, None] * stride[:, None])
    # 落点锚/静息/髋的体静态项(heading=0:世界 = 体 + 局部)
    anchor_dx = geom.rest_local[None, :, 0] + stride[:, None] * geom.fwd_bias[None, :]
    anchor_y = geom.rest_local[None, :, 1] + 0.0              # (1,6)
    hip_x0 = geom.attach[None, :, 0]                          # (1,6)
    hip_y = geom.attach[None, :, 1]                           # (1,6)
    # 组相位/波状偏移/抬腿弧:**逐个体**从 pv 注入(θ 维在训练中必须有梯度;
    # consts 同样来自该 profile 经运行时解析,P=1 时与 consts 路径数值逐位一致)
    gp_leg = np.where(geom.group[None, :] == 0,
                      pv["group_phase_0"][:, None],
                      pv["group_phase_1"][:, None])                    # (P,6)
    wave = (np.radians(pv["metachronal_deg"])[:, None] * geom.pair[None, :]
            * geom.side_sign[None, :]) / _TAU                          # (P,6)
    lk = np.stack([pv["lift_k_0"], pv["lift_k_1"], pv["lift_k_2"]],
                  axis=1)                                              # (P,3)
    lift_k = lk[:, geom.pair]                                          # (P,6)
    ff = pv["ff_gain"][:, None]
    land_cap = pv["land_cap_k"][:, None] * reach_p
    land_touch = consts["land_touch_k"] * reach_p
    over_lim = pv["over_k"][:, None] * reach_p
    # 摆动窗开相位 = duty;窗的上沿 = duty + swing_win_k×(1−duty) (gait.py:353,356)
    win_open_ph = duty_c
    late_ph = duty_c + pv["swing_win_k"][:, None] * (1.0 - duty_c)

    # ---- 状态(gait._Foot 的向量化镜像) ----
    phase = phase0.copy()
    posx = 0.0
    world = np.tile(geom.rest_local[None, :, :], (P, 1, 1)).copy()   # 种脚=静息位(gait.reset)
    swing = np.zeros((P, n_legs), dtype=bool)                 # False=PLANTED True=SWING
    t_sw = np.zeros((P, n_legs))
    dur_sw = np.full((P, n_legs), 0.12)
    ph_start = np.zeros((P, n_legs))
    ph_span = np.full((P, n_legs), 0.5)
    armed = np.ones((P, n_legs), dtype=bool)
    start = world.copy()
    target = world.copy()

    # ---- 统计累计 ----
    zerosP = np.zeros(P)
    v_dist = zerosP.copy(); stab_frames = zerosP.copy()
    fall_frames = zerosP.copy(); slip = zerosP.copy()
    jerk_sum = zerosP.copy(); h_sum = zerosP.copy(); h2_sum = zerosP.copy()
    restdev_sum = zerosP.copy(); steps = zerosP.copy(); steps_em = zerosP.copy()
    multi_sw = zerosP.copy(); both_grp = zerosP.copy()
    steps_per_foot = np.zeros((P, n_legs)); max_hip = np.zeros((P, n_legs))
    prev = world.copy(); prev2 = world.copy()
    trace = Trace() if return_trace else None
    arange5 = np.arange(n_legs - 1)
    g0 = (geom.group == 0)
    g1 = (geom.group == 1)

    for fi in range(n_frames):
        # ---- 相位器(gait.py:318-320;本仿真器恒行走 speed>0 ⇒ 恒推进) ----
        phase = (phase + hz * dt) % 1.0
        posx += vlx * dt                       # 与 base._integrate 同序:先积分位置
        sw0 = swing                            # 帧初摆动掩码(gait.py 的 if/else 分支)
        planted0 = ~sw0
        # 落点锚/髋(世界;heading=0)
        ideal_x = posx + anchor_dx             # (P,6)
        hip_x = posx + hip_x0
        hdx0 = hip_x - world[:, :, 0]
        hdy0 = hip_y - world[:, :, 1]
        hipdist0 = np.hypot(hdx0, hdy0)        # (P,6)
        # 组相位(metachronal 偏移并入 gp_wave;与 gait.py:377-380 同序相加)
        # **结合律对齐**(AG2 二审修复):gait.py 是 ((phase + gp) + wave/τ),
        # 浮点加法不可结合——预先合成 gp+wave 再加 phase 会产生 1 ulp 的 ph
        # 差,在窗边界上翻转触发(训练后的参数常驻边界,实测 25 帧·足失配)。
        # 必须逐项相加,顺序与 gait.py 完全一致。
        ph = ((phase[:, None] + gp_leg) + wave) % 1.0
        ideal_y = np.broadcast_to(anchor_y, (P, n_legs))   # heading=0: ideal_y=静息y
        # 摆动窗(gait.py:353,356,402):duty ≤ ph < late
        window_open = (ph >= win_open_ph) & (ph < late_ph)
        # 上膛(每窗至多一步;gait.py:387-388)—— **无条件**在窗关段刷新
        armed = armed | (ph < win_open_ph)
        # ---- PLANTED 分支(gait.py:389-447) ----
        over = hipdist0 > over_lim
        # 行走中禁用应急旁路(gait.py:406-408:fire = over or (err>emergency and
        # not walking));本仿真器 speed>0 ⇒ walking 恒真 ⇒ 只剩 over 与
        # "窗开且 armed"两条。**过拉伸阀同样消耗本窗 armed**(gait.py:414
        # `over_fire = over and (f.armed or not walking)`)——一窗一步对旁路
        # 触发同样成立,否则减速/急转瞬态中同窗双步会把实测步频顶过相位上限。
        fire = ((over & armed) | (window_open & armed)) & planted0
        # ---- SWING 分支(gait.py:448-489):只处理**帧初已在摆动**的足 ----
        if sw0.any():
            t_sw = np.where(sw0, t_sw + dt, t_sw)
            span0 = np.maximum(ph_span, 1e-6)
            prog = (ph - ph_start) % 1.0
            p = np.clip(prog / span0, 0.0, 1.0)
            p = np.where(t_sw >= SWING_TIMEOUT_S, 1.0, p)      # 相位兜底超时
            ps = p * p * (3.0 - 2.0 * p)                       # smoothstep
            u = 1.0 - ps
            mx = start[:, :, 0] + (target[:, :, 0] - start[:, :, 0]) * 0.5
            my = start[:, :, 1] + (target[:, :, 1] - start[:, :, 1]) * 0.5
            cx = mx + (hip_x - mx) * lift_k                    # 控制点向髋收拢(内折弧)
            cy = my + (hip_y - my) * lift_k[None, :]
            bz_x = u * u * start[:, :, 0] + 2 * u * ps * cx + ps * ps * target[:, :, 0]
            bz_y = u * u * start[:, :, 1] + 2 * u * ps * cy + ps * ps * target[:, :, 1]
            landed = sw0 & (p >= 1.0)
            # 落足:按当前髋位钳到 land_touch(与 gait.py:481-487 同式)
            ldx = target[:, :, 0] - hip_x
            ldy = target[:, :, 1] - hip_y
            ld = np.hypot(ldx, ldy)
            kk = np.where(ld > land_touch, land_touch / np.maximum(ld, 1e-12), 1.0)
            land_x = hip_x + ldx * kk
            land_y = hip_y + ldy * kk
            w_x = np.where(landed, land_x, np.where(sw0, bz_x, world[:, :, 0]))
            w_y = np.where(landed, land_y, np.where(sw0, bz_y, world[:, :, 1]))
            world[:, :, 0] = w_x
            world[:, :, 1] = w_y
            swing = sw0 & ~landed
            # gait.py 落足时不重置 t(仅起摆时 f.t=0),此处对齐不留状态差
        # ---- 起摆(gait.py:411-445) ----
        if fire.any():
            # 落点 = 落点锚 + 速度前馈(vlx×dur×ff;gait.py:437-438)
            tx = ideal_x + (vlx * dur_c) * ff
            ty = ideal_y + (0.0 * dur_c) * ff                  # vly=0(heading=0)
            tdx = tx - hip_x
            tdy = ty - hip_y
            # 落点方向噪声(域随机化;δ=0 时 rot 为单位矩阵,逐位还原)
            rdx = tdx * cosd - tdy * sind
            rdy = tdx * sind + tdy * cosd
            dd = np.hypot(rdx, rdy)
            kcap = np.where(dd > land_cap, land_cap / np.maximum(dd, 1e-12), 1.0)
            tx = hip_x + rdx * kcap
            ty = hip_y + rdy * kcap
            start[:, :, 0] = np.where(fire, world[:, :, 0], start[:, :, 0])
            start[:, :, 1] = np.where(fire, world[:, :, 1], start[:, :, 1])
            target[:, :, 0] = np.where(fire, tx, target[:, :, 0])
            target[:, :, 1] = np.where(fire, ty, target[:, :, 1])
            dur_sw = np.where(fire, dur_c, dur_sw)
            # ph_start = 起摆帧的本组相位;ph_span = min(到回卷, 1−duty, 0.5) 锁存
            ph_start = np.where(fire, ph, ph_start)
            ph_span = np.where(fire, np.maximum(
                np.minimum(np.minimum(1.0 - ph, 1.0 - duty_c), 0.5), 1e-6), ph_span)
            t_sw = np.where(fire, 0.0, t_sw)
            armed = armed & ~fire
            swing = swing | fire
            steps += fire.sum(axis=1)
            steps_per_foot += fire
            # 窗外起摆(互锁破坏)= 由过拉伸阀旁路触发的、不在窗内者
            steps_em += (fire & ~window_open).sum(axis=1)
        # ---- 统计(测窗内) ----
        if fi >= warmup_frames:
            fall_p = planted0 & (hipdist0 > geom.reach_true[None, :])
            fall_frames += fall_p.sum(axis=1).astype(np.float64)
            if v_void_on_fall:
                v_dist += np.where(fall_p.any(axis=1), 0.0, vlx * dt)
            else:
                v_dist += vlx * dt
            slip_v = np.clip(hipdist0 - geom.d_max[None, :] * fric, 0.0, None)
            slip += np.where(planted0, slip_v, 0.0).sum(axis=1)
            max_hip = np.maximum(max_hip, np.where(planted0, hipdist0, 0.0))
            # 三角违反诊断:同时离地足数 ≥4;两组同时有摆动足的帧
            nsw = swing.sum(axis=1)
            multi_sw += (nsw >= 4).astype(np.float64)
            both_grp += (swing[:, g0].any(axis=1) & swing[:, g1].any(axis=1)
                         ).astype(np.float64)
            # 支撑稳定性:≥3 足着地 + COM(体原点)在支撑凸包内
            # (原点在凸包内 ⟺ 凸包点绕原点的最大角间隙 < π,精确判定)
            k = planted0.sum(axis=1)
            ang = np.arctan2(world[:, :, 1], world[:, :, 0] - posx)
            angm = np.where(planted0, ang, 10.0)
            S = np.sort(angm, axis=1)
            jlast = np.maximum(k - 1, 0)
            last_real = np.take_along_axis(S, jlast[:, None], 1)[:, 0]
            wrap_gap = S[:, 0] + _TAU - last_real
            dgap = np.diff(S, axis=1)
            valid = arange5[None, :] < (k - 1)[:, None]
            max_gap = np.maximum(wrap_gap,
                                 np.where(valid, dgap, -1.0).max(axis=1))
            stab_frames += ((k >= 3) & (max_gap < math.pi)).astype(np.float64)
            # 体高代理(直腿近似;支撑足均值)
            h2 = np.maximum(0.0, geom.l_eff[None, :] ** 2 - hipdist0 ** 2)
            hv = np.sqrt(h2)
            hw = np.where(planted0, hv, 0.0)
            h_mean = np.where(k > 0, hw.sum(axis=1) / np.maximum(k, 1), 0.0)
            h_sum += h_mean
            h2_sum += h_mean * h_mean
            # 静息偏差 + 足端二阶差分(关节抖动驱动量;身体系)
            fx = world[:, :, 0] - posx
            fy = world[:, :, 1]
            restdev_sum += (np.abs(fx - geom.rest_local[None, :, 0])
                            + np.abs(fy - geom.rest_local[None, :, 1])).sum(axis=1)
            d2x = fx - 2.0 * prev[:, :, 0] + prev2[:, :, 0]
            d2y = fy - 2.0 * prev[:, :, 1] + prev2[:, :, 1]
            jerk_sum += (d2x * d2x + d2y * d2y).sum(axis=1)
            prev2 = prev.copy()
            prev = world.copy()
            if return_trace:
                trace.world.append(world[0].copy())
                trace.new_swings.append(int(fire[0].sum()))
                trace.swinging.append(swing[0].copy())
                trace.pose_x.append(posx)
    t_meas = (n_frames - warmup_frames) * dt
    return RolloutMetrics(v_dist=v_dist, t_meas=t_meas, stab_frames=stab_frames,
                          fall_frames=fall_frames, slip_px=slip, jerk_sum=jerk_sum,
                          h_sum=h_sum, h2_sum=h2_sum, restdev_sum=restdev_sum,
                          steps=steps, steps_emergency=steps_em,
                          multiswing_frames=multi_sw, both_groups_frames=both_grp,
                          steps_per_foot=steps_per_foot, max_hipdist=max_hip,
                          speed=speed), trace


def fitness_from_metrics(m: RolloutMetrics, v_target: float, stride_ref: float,
                         geom: SpeciesGeometry, pv: dict[str, np.ndarray],
                         species: str, dt: float = 1.0 / 60.0) -> np.ndarray:
    """D2 §3.3 适应度组装(全 (P,) 向量化;各项归一到 [0,1] 量级)。

    F = w_v·min(v̄/v_target,1.2)        ← 速度(达标即饱和;本降阶模型恒 1.0)
      + w_st·stab                       ← 支撑多边形稳定占比
      − w_sl·(slip/stride)              ← 滑移占比(≤1)
      − w_j·jerk_norm                   ← 足端二阶差分 RMS 归一
      − w_h·height_var                  ← 体高波动 std/BL 归一
      − w_p·rest_dev                    ← 与静息位锚的平均偏差/reach
      − w_f·fall_rate                   ← 几何不可达(翻倒/绊倒)帧占比
      − w_il·interlock                  ← 窗外起摆占步数比(三角互锁破坏)
      − w_ms·multiswing                 ← 同时离地 ≥4 足帧占比
      − w_sd·stride_band                ← 步长越出 D2 §4 #4 文献带的归一量
      − w_bg·both_groups                ← 两组同时有摆动足的帧占比(v1.2 权重
                                           6.0、仅工作域;test_body 镜像)
      − w_anchor·anchor                 ← 未识别/惰性维先验锚定(L2,非 D2 项)
    """
    n_m = max(m.t_meas / max(dt, 1e-9), 1.0)      # 测量窗帧数
    v_bar = m.v_dist / max(m.t_meas, 1e-6)
    f_v = np.minimum(v_bar / max(v_target, 1e-6), 1.2)
    stab = m.stab_frames / n_m
    slip_ratio = np.clip(m.slip_px / np.maximum(m.steps * stride_ref, 1e-6), 0.0, 1.0)
    jerk_rms = np.sqrt(m.jerk_sum / max(n_m * geom.n_legs, 1.0))
    jerk_norm = np.clip(jerk_rms / JERK_SCALE, 0.0, 1.0)
    h_mean = m.h_sum / n_m
    h_std = np.sqrt(np.maximum(m.h2_sum / n_m - h_mean * h_mean, 0.0))
    height_var = np.clip(h_std / geom.body_len / HEIGHT_SCALE, 0.0, 1.0)
    rest_dev = m.restdev_sum / (max(n_m * geom.n_legs, 1.0) * float(np.mean(geom.reach)))
    fall_rate = np.clip(m.fall_frames / n_m, 0.0, 1.0)
    interlock = m.steps_emergency / np.maximum(m.steps, 1.0)
    multiswing = m.multiswing_frames / n_m
    # 步长(实测) = 单腿一个周期走过的距离 = 位移 / 单腿步数(steps/6);越带罚
    v_bar_safe = np.maximum(v_bar, 1e-6)
    steps_per_leg = np.maximum(m.steps / 6.0, 1e-6)
    stride_bl = (v_bar_safe * m.t_meas / steps_per_leg) / geom.body_len
    stride_band = (np.clip((stride_bl - STRIDE_BL_HI) / STRIDE_BL_HI, 0.0, 1.0)
                   + np.clip((STRIDE_BL_LO - stride_bl) / STRIDE_BL_LO, 0.0, 1.0))
    F = (W_V * f_v + W_ST * stab - W_SL * slip_ratio - W_J * jerk_norm
         - W_H * height_var - W_P * rest_dev - W_F * fall_rate
         - W_IL * interlock - W_MS * multiswing - W_SD * stride_band)
    # both_groups 互锁罚:仅工作域(v_target ≤ 1.5×cruise;极速域属过拉伸阀
    # 解除互锁的设计语义,几何上不可清零,见 W_BG 注释)
    if v_target <= 1.5 * float(geom.cruise):
        F = F - W_BG * (m.both_groups_frames / n_m)
    # 未识别/惰性维先验锚定(归一偏差平方;防无梯度维度漂移出先验)
    anchor = np.zeros_like(F)
    spec = {row[0]: row for row in param_spec(species)}
    for key in _ANCHOR_KEYS:
        _k, lo, hi, dv = spec[key]
        anchor += ((pv[key] - dv) / (hi - lo)) ** 2
    F = F - W_ANCHOR * anchor / len(_ANCHOR_KEYS)
    return F


def evaluate_population(geom: SpeciesGeometry, consts: dict,
                        pv: dict[str, np.ndarray], speeds: list[float],
                        seeds: list[int | None], dt: float = 1.0 / 60.0,
                        n_frames: int = 300, warmup_frames: int = 60,
                        collect: bool = False
                        ) -> tuple[np.ndarray, dict]:
    """多速度档取 min × 多随机种子取 mean(D2 §3.5 防作弊)→ (P,) 适应度。

    返回 (F, diag);diag 含各速度档均值/最优适应度与逐(速度,种子)矩阵
    (collect=True 时),训练器用 diag 记录收敛与验收数据。
    """
    P = pv["hz_rest"].shape[0]
    F_acc = np.full(P, np.inf)
    diag: dict = {"per_speed": {}, "per_speed_max": {}, "raw": {}}
    stride_ref = float(np.mean(geom.reach)) * 0.6      # 步长参考(reach 比例口径)
    for v in speeds:
        F_seed = np.zeros(P)
        for sd in seeds:
            m, _tr = rollout_batch(geom, consts, pv, v, sd, dt, n_frames,
                                   warmup_frames)
            Fs = fitness_from_metrics(m, v, stride_ref, geom, pv, geom.species)
            F_seed += Fs
            if collect:
                diag["raw"][(v, sd)] = Fs.copy()
        F_seed /= max(len(seeds), 1)
        diag["per_speed"][v] = F_seed.copy()
        diag["per_speed_max"][v] = float(F_seed.max())
        F_acc = np.minimum(F_acc, F_seed)
    return F_acc, diag


def species_geometry(species: str) -> SpeciesGeometry:
    """按物种键构建几何(训练器/测试统一入口)。"""
    from neuropet.species.cockroach import PARAMS as ROACH
    from neuropet.species.fruitfly import PARAMS as FLY
    table = {"cockroach": ROACH, "fruitfly": FLY}
    if species not in table:
        raise KeyError(f"未知物种: {species}")
    return SpeciesGeometry(species, dict(table[species]))


def species_params(species: str) -> dict:
    """按物种键返回运行时 PARAMS(训练器统一入口)。"""
    from neuropet.species.cockroach import PARAMS as ROACH
    from neuropet.species.fruitfly import PARAMS as FLY
    table = {"cockroach": ROACH, "fruitfly": FLY}
    if species not in table:
        raise KeyError(f"未知物种: {species}")
    return dict(table[species])


# ===================================================================
# 训练-测试共用评估入口(单一事实源:训练器、test_gait_training、
# 实施记录报告全部经这里取数,保证"训练产物适应度 ≥ 默认基线"
# 与复现实验用同一协议)
# ===================================================================
# 预设 → 评估速度计划((基准档, 比例) 对;min over speeds 压制单点过拟合)。
# **全工作包络**:GaitProfile 是全速域三段折线(hz_rest/cruise/sprint 三结点
# 共享一张表),sprint 预设若只评 sprint 段速度,优化器会把 hz_cruise 压到
# 3.14Hz 换取 sprint 段少步数低 jerk(v1.1 实测,巡航段 stride 96px → 安全阀
# 循环、互锁尽毁)——因此每个预设都必须在**巡航工作域 + 自名域**上取 min:
#   工作域(互锁契约区):0.6/1.0×cruise + 1.5×cruise(QF 冲刺统计入口);
#   自名域:cruise 预设加 1.0×sprint(防冲刺段翻倒),sprint 预设加
#   0.75/1.0×sprint。
# 种子:None=无域随机化(确定性主梯度)+ 1 个随机化种子(泛化)。
# **训练协议 = 评估协议**(TRAIN/EVAL 同常数;v1.0 曾 2 种子训练 3 种子评估,
# roach/cruise 候选训练 +0.009、完整评估反负,经典 train/eval mismatch)。
SPEED_PLAN = {
    "cruise": (("cruise", 0.6), ("cruise", 1.0), ("cruise", 1.5),
               ("sprint", 1.0)),
    "sprint": (("cruise", 0.6), ("cruise", 1.0), ("cruise", 1.5),
               ("sprint", 0.75), ("sprint", 1.0)),
}
TRAIN_SEEDS: tuple[int | None] = (None, 20260916)
EVAL_SEEDS: tuple[int | None] = (None, 20260916)
EVAL_FRAMES = 240          # 4s @60fps(冲刺 15Hz=60 周期/巡航 8Hz=32 周期)
EVAL_WARMUP = 60           # 1s 起步瞬态不采样


def preset_speeds(species: str, preset: str) -> list[float]:
    """预设的评估速度(px/s),按 SPEED_PLAN 全包络展开。"""
    sp = species_params(species)
    return [float(sp[base]) * frac for base, frac in SPEED_PLAN[preset]]


def params_to_pv(params: dict) -> dict[str, np.ndarray]:
    """GaitProfile params 子树(标量)→ decode_theta 同形的 pv(批量=1)。

    与 theta_to_params 互逆(键一一对应;group_phase/lift_k 拆维)。未知键
    忽略;缺失键回退 DEFAULT_PARAMS(与运行时加载链一致)。
    """
    from neuropet.body.gait_profile import DEFAULT_PARAMS
    src = dict(DEFAULT_PARAMS)
    src.update(params)
    pv: dict[str, np.ndarray] = {}
    for key in _THETA_TO_PROFILE:
        pv[key] = np.array([float(src[key])], dtype=np.float64)
    pv["group_phase_0"] = np.array([float(src["group_phase"][0])], dtype=np.float64)
    pv["group_phase_1"] = np.array([float(src["group_phase"][1])], dtype=np.float64)
    pv["lift_k_0"] = np.array([float(src["lift_k"][0])], dtype=np.float64)
    pv["lift_k_1"] = np.array([float(src["lift_k"][1])], dtype=np.float64)
    pv["lift_k_2"] = np.array([float(src["lift_k"][2])], dtype=np.float64)
    return pv


def project_params(params: dict, species: str) -> dict:
    """落盘前投影:单调 repairing + 结构不变量 + 不可辨识维回收先验。

    ① 步频/占空单调(运行时映射单调不减/不增的结构前提,与 QF
       hz_monotonic / duty 单调同向):hz_rest ≤ hz_cruise ≤ hz_sprint、
       duty_patrol ≥ duty_cruise ≥ duty_sprint;
    ② 三角互锁结构投影(v1.2b 组相位 / v1.3 duty):
       - group_phase=[0, 0.5]、metachronal_deg=0。test_body「both_groups==0」
         不变量在 θ 空间是**零测度切片**——组间偏移 0.55 时 B 组在 A 组摆动窗
         内起摆(实测 stride 折叠成 0.05 相位、整段摆动嵌进 A 组窗,每周期 5%
         重叠);连续优化不可靠命中 offset=0.5 的精确点,故按 D2 §3.2-1 三角
         先验取默认、不作搜索维。波状/四足步态表达与互锁验收不相容,留待
         后续波次放开(须同步改验收口径);
       - **duty_cruise = duty_sprint = 0.5(文献带 [0.42,0.50] 上沿,v1.3)**。
         互锁的占空结构前提:duty ≥ 0.5 时两组摆动窗 [duty,1)/[duty−0.5,0.5)
         在本组回卷点首尾相接,组 A 落足帧 = 组 B 起摆帧(相位锚定下起摆帧
         lateness < 1 帧步长,回卷落足必在本窗后第一帧 ⇒ 交接零重叠);任何
         duty < 0.5 的结点使组 B 窗在组 A 锁存摆动跨度(0.5 周期)结束前开启,
         **帧量化下每组交接以 ~50% 概率重叠 1 帧**(实测:训练产物
         duty_sprint=0.42/0.46 @1.5×cruise 档 both=8/4 帧,0.6/1.0×cruise 档
         duty ≥ 0.5 全零;同参数仅改 duty_sprint=0.5 → 全零)。工作域
         both_groups=0 是硬验收(interlock.operating_zone 门槛=0),可行域
         塌缩到带沿,故 duty 两结点按结构约束投影、不作自由搜索维;
    ③ 不可辨识维 → 先验默认(深拷贝语义,防外部改写内置表)。
    投影只收紧可行域;duty 上钳例外地**沿带沿抬升**(0.42/0.46 → 0.5)——
    抬升方向 = 互锁可行方向,落点仍在本档文献带 [0.42,0.50] 与 schema 界内,
    且单调序(patrol ≥ cruise ≥ sprint = 0.5)不受破坏。
    """
    from neuropet.body.gait_profile import DEFAULT_PARAMS
    out = dict(params)
    # ① 单调 repairing(钳到相邻档,方向沿文献带)
    out["hz_rest"] = min(float(out["hz_rest"]), float(out["hz_cruise"]))
    out["hz_cruise"] = min(float(out["hz_cruise"]), float(out["hz_sprint"]))
    out["duty_sprint"] = min(float(out["duty_sprint"]), float(out["duty_cruise"]))
    out["duty_cruise"] = min(float(out["duty_cruise"]), float(out["duty_patrol"]))
    # ② 三角互锁结构投影(零测度约束 → 先验值/带上沿)
    out["group_phase"] = list(DEFAULT_PARAMS["group_phase"])
    out["metachronal_deg"] = DEFAULT_PARAMS["metachronal_deg"]
    out["duty_cruise"] = max(float(out["duty_cruise"]), 0.5)
    out["duty_sprint"] = max(float(out["duty_sprint"]), 0.5)
    # ③ 不可辨识维 → 先验默认(深拷贝语义,防外部改写内置表)
    for key in _ANCHOR_KEYS:
        if key in DEFAULT_PARAMS and key in out:
            out[key] = DEFAULT_PARAMS[key]
    return out


def project_pv(pv: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """训练期互锁结构投影(decode_theta 输出的逐键 (P,) 数组,与落盘
    project_params 同构,使**搜索直接在可行面上进行**、训练适应度 = 落盘
    复算适应度):工作域 both_groups=0 的可行域要求 duty 两结点 = 0.5
    (帧量化交接零重叠的结构下界,依据见 project_params ②)。
    """
    out = dict(pv)
    out["duty_cruise"] = np.maximum(pv["duty_cruise"], 0.5)
    out["duty_sprint"] = np.maximum(pv["duty_sprint"], 0.5)
    return out


def evaluate_params(species: str, params: dict, preset: str,
                    seeds: tuple[int | None] = EVAL_SEEDS,
                    n_frames: int = EVAL_FRAMES,
                    warmup_frames: int = EVAL_WARMUP) -> float:
    """单个体参数表的协议化适应度(与训练同函数、同常数;测试/报告入口)。

    F = min over 速度档 × mean over 域随机化种子(D2 §3.5)。
    """
    geom = species_geometry(species)
    consts = resolve_gait_constants(species_params(species), {"params": params})
    pv = params_to_pv(params)
    speeds = preset_speeds(species, preset)
    f, _diag = evaluate_population(geom, consts, pv, speeds, list(seeds),
                                   n_frames=n_frames, warmup_frames=warmup_frames)
    return float(f[0])


def consistency_max_error(species: str, params: dict, speed: float,
                          n_frames: int = 300) -> tuple[float, int]:
    """gait_sim(P=1、无域随机化)vs 运行时 TripodGait 同路径逐帧对比。

    两路同序推进:先积分身体位置、再 update/逐帧步进;逐帧比较 6 足世界
    坐标与摆动掩码。返回 (最大绝对偏差 px, 帧数)。
    """
    geom = species_geometry(species)
    consts = resolve_gait_constants(species_params(species), {"params": params})
    pv = params_to_pv(params)
    dt = 1.0 / 60.0
    _m, tr = rollout_batch(geom, consts, pv, speed, None, dt, n_frames, 0,
                           v_void_on_fall=False, return_trace=True)
    g = TripodGait(species_params(species))
    g.apply_profile({"params": params})
    kins = [LegKinematics(leg) for leg in species_params(species)["legs"]]
    groups = [int(leg.get("group", i % 2))
              for i, leg in enumerate(species_params(species)["legs"])]
    g.bind_legs(kins, groups)
    g.reset((0.0, 0.0), 0.0)
    posx = 0.0
    max_err = 0.0
    mism = 0
    for fi in range(n_frames):
        posx += speed * dt
        g.update(dt, speed, (posx, 0.0), 0.0)
        sim_w = tr.world[fi]
        for j in range(geom.n_legs):
            gx, gy = g.foot_world(j)
            max_err = max(max_err, abs(gx - sim_w[j, 0]), abs(gy - sim_w[j, 1]))
            if (g.is_swinging(j)) != bool(tr.swinging[fi][j]):
                mism += 1
    if mism:
        raise AssertionError(
            f"摆动掩码不一致:{mism} 帧·足(species={species} v={speed:.0f})")
    return max_err, n_frames


# ===================================================================
# 闭环诊断(用**运行时** TripodGait,与 QF 验收同口径)
# ===================================================================
def closed_loop_metrics(species: str, profile_params: dict, speed: float,
                        seconds: float = 6.0, warmup: float = 1.0,
                        dt: float = 1.0 / 60.0) -> dict:
    """直线匀速闭环:运行时 TripodGait(=部署侧实现)驱动的实测指标。

    与 tests/test_acceptance_final.py 的 `_closed_loop_walk` 同口径(支撑相 =
    足端世界坐标逐帧严格不动),但只跑**纯步态**(不经行为层,速度恒定),
    用于训练产物 vs 默认表的对照:
      实测步频 f = 摆动上升沿数 / 6 / 采样秒数
      实测 duty   = 支撑帧数 / (采样帧数 × 6)
      互锁违反    = 同帧两组各有摆动足的帧数
    """
    prof = {"params": profile_params}
    g = TripodGait(species_params(species))
    g.apply_profile(prof)
    kins = [LegKinematics(leg) for leg in species_params(species)["legs"]]
    groups = [int(leg.get("group", i % 2))
              for i, leg in enumerate(species_params(species)["legs"])]
    g.bind_legs(kins, groups)
    g.reset((0.0, 0.0), 0.0)
    n = int(seconds / dt)
    w0 = int(warmup / dt)
    posx = 0.0
    prev = [g.foot_world(j) for j in range(6)]
    prev_moving = [False] * 6
    planted = 0
    moving_frames = 0
    entries = 0
    both = 0
    over_swings = 0
    phases = []
    for i in range(n):
        posx += speed * dt
        g.update(dt, speed, (posx, 0.0), 0.0)
        feet = [g.foot_world(j) for j in range(6)]
        moving = [math.hypot(a[0] - b[0], a[1] - b[1]) > 0.0
                  for a, b in zip(feet, prev)]
        if i >= w0:
            moving_frames += 1
            planted += sum(1 for mv in moving if not mv)
            entries += sum(1 for pm, mv in zip(prev_moving, moving) if mv and not pm)
            grp0 = any(moving[j] for j in range(6) if groups[j] == 0)
            grp1 = any(moving[j] for j in range(6) if groups[j] == 1)
            both += 1 if (grp0 and grp1) else 0
            phases.append(g.phase)
        prev, prev_moving = feet, moving
    secs = max(moving_frames * dt, 1e-9)
    return {"speed": speed, "f_hz": entries / 6.0 / secs,
            "duty": planted / max(moving_frames * 6.0, 1.0),
            "both_groups_frames": both,
            "measured_frames": moving_frames,
            "over_swings": over_swings,
            "phase_f_hz": (g.step_hz(speed)), "duty_profile": g.duty(speed)}
