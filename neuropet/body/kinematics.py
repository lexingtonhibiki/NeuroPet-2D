"""昆虫腿运动学:真 3D 关节链闭式 IK(缺省)+ 平面遗留链(NEUROPET_LEG3D=0)。

================================================================================
== v3 真 3D 链(腿部"真 3D 腿链"波;实施规格
   docs/references/三维腿链实施规格.md,数值权威
   docs/references/腿部三维运动学权威_蟑螂果蝇.md〔R1〕§7 编码就绪参数表)====
================================================================================
腿条目带 "len3d" 键即启用真 3D 链:ThC 球窝偏航(α)→ coxa 固定短节 →
CTr 股节俯仰(β)→ FTi 膝内角(γ)→ TiTa 被动 + 5 跗分节平铺,solve()
返回 **8 点契约**(规格 §1.2):

    points = [attach, CTr, knee, ankle, m1, m2, m3, claw]
      [0] attach=体壁穿出点(pts[0] 消费契约不变:腿指纹/盖片);
      [1] CTr=基节远端=有效髋(IK 平面几何原点);
      [2] FTi 膝;[3] TiTa 踝;[4..6] 跗分节中间锚 ×3;[7] 爪尖=步态钉住点。
    angles = (yaw°, β°, γ°)(键与遗留版一致);
    angles3d = (α, β, γ, δ, φ)(度;δ=胫节俯角、φ=膝方位前偏角,诊断/校准用)。

不带 "len3d" 的腿条目同样走 3D 路径(节段长回退 l1/l2/pose_n/tarsus),
输出同为 8 点 —— 渲染层按点数自适应(len(pts)>=7 即解剖链,
render/renderer.py:_draw_legs_hybrid 对 7 点(v2)与 8 点(v3)共用一套)。
**NEUROPET_LEG3D=0**(一级回滚开关,规格 §7.1)与缺省档共用同一按键
分派(回滚语义 = "v3 波合入前行为");_solve_legacy 函数体逐字保留供
v1 直连与回溯。
**【L2F 收尾波 2026-09-16 分派修复】**v3 波曾把缺省分派改为"全体 →
_solve_3d",令已提交的 v2 7 点契约(tests/test_body ①④⑧、
scratch/_leg3d_probe.py A1~A12、ADR-0026/0027)与世界钉足端到端断言
全部失锚。现恢复按键分派:**缺省档 "trochanter/chain3d" 键 → _solve_v2
(7 点,蟑螂现行契约);其余 → _solve_legacy(4 点,果蝇现行契约)**;
len3d 五键在缺省档休眠(物种 PARAMS 中的 len3d 数据保留不删);
NEUROPET_LEG3D=2 = v3 8 点链开发档(全体 _solve_3d,v3 波按其规格 §6/§7
验收并同步改写 test_body/探针后方可切缺省)。分派详见 solve() docstring。

3D 求解(规格 §2;每腿 ~8 次 sin/cos + 2 次 acos,全标量无迭代):
  0) 镜像入腿系(y×side);**钉足语义 = 爪尖**(规格 §2.0):跗链自爪尖回铺,
     踝 = tip − R_vis·dir_t(R_vis = tarsus_ext×tarsus3d;支撑相 dir_t =
     世界冻结跗向×side,缺省/摆动相 = 中立足向 yaw0);
  1) ThC 偏航 α = atan2(踝−E),E = 有效髋 = attach 沿中立足向前移
     coxa3d·cos(θ_coxa)(基节俯角 θ_coxa≈60°,俯视只剩短投影,规格 §1.2;
     无 len3d 的腿保持旧"全长前移"语义);偏差钳 rom3d.yaw(含 1e-9 容差);
  2) 竖直落差 h = max(H − lift_gain·lift, 0.30·H)(lift_gain=1.0 精确 z 几何,
     替代旧 0.45 美术系数);
  3) 径向环带(踝口径)d = clamp(|踝−E|, d_min3, d_max3)(式同遗留版,
     长度换 f3d/t3d、膝限位换 rom3d.knee);
  4) 竖直平面 2 骨闭式解,**膝下垂支**(膝低于髋-踝连线 —— 蟑螂匍匐真实
     姿态:R1 §3.2 股节自体表下俯 35/30/28°、膝点离地仅 0.03~0.045 BL;
     治"蜘蛛腿"的上拱膝即在此):
        β = elev0 + acos((r²+f²−t²)/(2rf))    # 股节俯角,下垂为正
        γ = acos((f²+t²−r²)/(2ft))            # 膝内角(180°=伸直)
  5) ROM 钳制 β∈rom3d.femur_pitch、γ∈rom3d.knee;δ = γ+β−180°(胫节俯角,
     下正;由内角关系 γ=180°−β+δ' 反解),钳 [−85°, 88°] 退化保护;
  6) 重建(钳后角度):踝 A = E+(cosα,sinα)·d;膝 K = E+(cos(α−φ),sin(α−φ))
     ·f3d·cosβ,其中 φ = clamp(femur_lead_k·β, 0, 25°) 为**膝方位前偏角**
     —— R1 §7.3 实测股节方位角(45/105/150°)恒先于足向角(54/118/159°)
     约 9~13° ≈ 0.35×β(规格 §2.4"股节方位角超前"替代 bow hack;腿系角度
     逆时针为正、"朝前"= 角度减小侧,故取 α−φ;镜像回身体系后左右对称);
  7) 跗链平铺:自踝沿 dir_t 按 tarsus_seg 累积比铺 m1/m2/m3,tip = 踝 +
     R_vis·dir_t —— 未钳制时与输入钉点逐位相等 → 世界钉足支撑相零滑移;
  8) 镜像回身体系输出 8 点。

**FTi 角度口径换算(两处裁决 #2,勿混)**:R1 的"FTi 3D 内角"157/148/156°
与本求解器 γ、现行 TIBIA_RANGE 同为**内角口径**(180°=伸直);文献另给
"铰链转角"23/32/24°(0°=伸直)。换算式:内角 = 180° − 铰链转角
(157 = 180 − 23)。R1 §7.4 FTi ROM 0~125°(铰链口径)→ 内角域 [55,180],
按 D2 §2.2 留 5° 安全边距 → rom3d.knee = (60,175);rom3d.knee **缺省回退
仍为现行 TIBIA_RANGE 常量**(裁决 #2)。

髋高 H(rest3d 反解,规格 §3.2;R1 §6.2 "Σℓ·sinγ = 基节离地高"的闭式):
    δ'_n = γ_n + β_n − 180°           (胫节俯角,下正)
    H = f3d·sin(β_n) + t3d·sin(δ'_n)
β_n=股节俯角(R1 35/30/28°)、γ_n=FTi 3D 内角(R1 157/148/156°)→
H≈16.5/13.8/19.1px ≈ R1 基节离地高 0.137/0.155/0.164 BL。无 rest3d 的腿
回退 pose_n 的旧平面公式(h = t·sin(180−γ−β) − f·sinβ,上拱膝口径)。

**reach 语义(规格 §3.2 的执行偏离,经裁决 #1 记录)**:`reach` 属性保持
l1+l2(v2 键激活时沿用 v2 全链口径)不变 —— gait 阀(REST/LAND/OVER/
trigger)与 gait_sim 训练几何、已落盘 GaitProfile 的 provenance 适应度复核
(tests/test_gait_training.py ④)全部锚定该口径;3D 节段和另以 `reach3d`
(=f3d+t3d,缺省=l1+l2)暴露。已验证 gait 域(0.78/0.86/0.88×reach,
reach=l1+l2)映射到踝域后仍落在 [d_min3, d_max3] 内(coxa 投影与跗节
R_vis 吸收差值),世界钉足支撑相零钳制;gait 阀切换 reach3d 与数据重训
同属 L2 校准波,一并执行。

缩放(AG5):len3d 为 1x 绝对 px;base.GenericInsectBody 与 rig.from_params
按 params["scale"] 注入私有键 "_scale_k"(species PARAMS 永不携带),
3D 长度同比缩放 —— 与 SkeletonSpec.scaled(k) 等价(tests/test_scaling.py ②;
core/app.scale_params 不识别 len3d,故由消费侧注入,若日后 scale_params
直接缩放 len3d 应同步移除该注入,勿双重缩放)。

================================================================================
== v1 平面链(_solve_legacy;v3 波之前的 solve() 函数体,逐字保留)============
================================================================================
平面 yaw + 竖直平面 2 骨 IK + 膝拱美饰偏移,输出 4 点链
[attach, 膝, 踝, 足]。建模约定(俯视 2D 渲染,x 朝前,y 朝体侧):
关节 1 coxa 偏航 yaw(α)包络见 COXA_YAW_ENV;关节 2 femur 俯仰(β)
[-10°,+70°](上拱为正);关节 3 tibia 膝内角(γ)[20°,140°]。
伪 3D:髋高 H 由名义姿态(pose_n,默认 30°/70°)按节段长反解,lift 等效
抬足缩小竖直落差(h_eff = H − 0.45·lift)。coxa 拆分:基节建为"有效髋"
(attach 沿中立向前移 coxa);不可达目标沿视线钳到 [d_min, d_max] 环带。

================================================================================
== v2 链(_solve_v2;骨架重构波 ADR-0026/0027/0028,v3 起休眠保留)============
================================================================================
"trochanter" 键激活的 4+2 自由度 7 点链(球面交膝 + TiTa 前勾 +
跗节钳制弯折吸收),实现与语义见 _solve_v2 与 ADR 系列;v3 分派不再路由到它
(species PARAMS 已切换 len3d 键),代码逐字保留供回溯/对照。
"""
from __future__ import annotations

import math
import os

from neuropet.core.mathutil import clamp

# ---- 关节角包络(度,来源:运动学调研 §2.2 / §6 汇总表) ----
# coxa yaw 相对"中立朝向"(腿根→home 方向)的偏差限位:(朝后极限, 朝前极限)
# 中限放宽到 ±52°/前腿朝后 60°/后腿朝后 66°:世界钉足步态下支撑相足端相对
# 身体后移,深后摆是常态,包络须覆盖整个步态扫掠角(±40° 会在深摆时把
# 画出的足端从钉住点掰开,视觉上足底"滑动")。
# 物种可用腿条目键 "yaw_env": (朝后°, 朝前°) 覆盖(果蝇小体型大扫掠)。
COXA_YAW_ENV = {"front": (60.0, 60.0), "mid": (52.0, 52.0), "rear": (66.0, 48.0)}
FEMUR_RANGE = (-10.0, 70.0)    # 遗留口径 0=水平,+70=高抬;3D 口径下垂为正
TIBIA_RANGE = (20.0, 140.0)    # 膝内角;140≈近伸直,20≈紧折(内角口径)

# ---- v2 链关节硬限位(骨架重构波;v2 休眠后仅作历史常量保留) ----
KNEE_RANGE_V2 = (40.0, 165.0)  # FTi 内角 γ(v2 口径)
CTR_RANGE = (-10.0, 40.0)      # CTr 俯仰=股节仰角 β(v2 口径)
BEND_W = {"front": 0.50, "mid": 0.30, "rear": 0.60}
BASE_K = {"front": 0.15, "mid": 0.48, "rear": 0.54}
BASE_OFF = {"front": 0.0, "mid": 170.0, "rear": 127.0}   # 度,相对 φ0(腿系)
# TiTa 跗节展角(度;照片 §3.2:跗节相对髋→爪尖连线向前勾 前 66→49°/
# 中 134→126°/后 167→158°)。
TARSUS_SPLAY = {"front": 17.0, "mid": 7.5, "rear": 8.8}

# 名义站姿(v1 链反解髋高 H 用)
_BETA_N = math.radians(30.0)   # femur 仰角(上拱正)
_GAMMA_N = math.radians(70.0)  # 膝内角

# ---- v3 真 3D 链常量(规格 §2/§3/§4.1) ----
ROM3D_TITA_DEFAULT = (-25.0, 60.0)     # TiTa+跗间装饰摆(渲染层消费,IK 不用)
TARSUS_SEG_DEFAULT = (0.2, 0.2, 0.2, 0.2, 0.2)   # 5 跗分节等分(比例,和=1)
LIFT_GAIN_DEFAULT = 1.0                # lift→竖直落差增益(1.0=物理正确)
FEMUR_LEAD_DEFAULT = 0.35              # 膝方位前偏系数 φ=k·β(规格 §2.4 校准项)
FEMUR_LEAD_MAX_DEG = 25.0              # φ 上限(度)
COXA_PITCH_DEG = 60.0                  # 基节俯角(规格 §1.2;俯视投影 = cos)
TIBIA_PITCH_MIN_DEG = -85.0            # 胫节俯角钳制带(度,下正;退化保护)
TIBIA_PITCH_MAX_DEG = 88.0


def _leg3d_enabled() -> bool:
    """NEUROPET_LEG3D 环境开关(规格 §7.1):缺省启用 3D;=0 回退 v1 平面链。

    每次调用实时读取(测试可在进程内切换;验收门槛:LEG3D=0 下
    tests/test_body.py 全绿)。"""
    return os.environ.get("NEUROPET_LEG3D", "1") != "0"


def _pair2(raw, fallback: tuple[float, float]) -> tuple[float, float]:
    """防御式解析 (lo, hi) 对;非法回退。"""
    try:
        lo, hi = float(raw[0]), float(raw[1])
        if math.isfinite(lo) and math.isfinite(hi) and lo <= hi:
            return (lo, hi)
    except (TypeError, ValueError, IndexError):
        pass
    return fallback


class LegKinematics:
    """单条腿的闭式 IK 求解器。参数来自 species PARAMS 的单个腿条目
    (attach/home/l1/l2/side/kind + 可选 len3d/rest3d/rom3d/tarsus_seg/
    femur_lead_k,缺省时回退合理默认)。"""

    def __init__(self, leg: dict) -> None:
        self.attach = (float(leg.get("attach", (0.0, 12.0))[0]),
                       float(leg.get("attach", (0.0, 12.0))[1]))
        self.home = (float(leg.get("home", (0.0, 30.0))[0]),
                     float(leg.get("home", (0.0, 30.0))[1]))
        self.coxa = max(0.0, float(leg.get("coxa", 0.0)))          # 基节(D2 拆分)
        self.l1 = max(1e-3, float(leg.get("l1", 20.0)))   # 股节(转节+股,见 D2 §1.1)
        self.l2 = max(1e-3, float(leg.get("l2", 22.0)))   # 胫节
        self.side = 1.0 if float(leg.get("side", 1)) >= 0 else -1.0
        self.kind = str(leg.get("kind", "mid"))
        self.tarsus = max(1.0, float(leg.get("tarsus", 0.22 * self.l2)))  # 足端小节
        # 跗节渲染外伸系数(跗节贴地程度):v1 口径蟑螂 1.0 /果蝇 0.35(折叠);
        # v3 链 R_vis = tarsus_ext × tarsus3d(俯视可见跗链全长)。
        self.tarsus_ext = clamp(float(leg.get("tarsus_ext", 0.35)), 0.1, 1.0)
        rear_env, front_env = COXA_YAW_ENV.get(self.kind, (40.0, 40.0))
        if "yaw_env" in leg:   # 物种级覆盖(果蝇小体型大扫掠等)
            try:
                rear_env, front_env = float(leg["yaw_env"][0]), float(leg["yaw_env"][1])
            except (TypeError, ValueError, IndexError):
                pass
        self._front_env = math.radians(front_env)
        self._rear_env = math.radians(rear_env)

        # ---- v2 脚手架参数(休眠保留;缺省全部安全回退,老 PARAMS 不炸) ----
        self.v2 = "trochanter" in leg or "chain3d" in leg
        self.trochanter = max(0.0, float(leg.get("trochanter", 0.0)))
        self.claw = max(0.0, float(leg.get("claw", 0.0)))
        # 直径表/跗分节占比:渲染层权威(_SPEC_LEG),此处透传
        self.diam = None
        if "diam" in leg:
            try:
                self.diam = tuple(max(0.2, float(v)) for v in leg["diam"][:5])
            except (TypeError, ValueError):
                self.diam = None
        self.tarsomeres = None
        if "tarsomeres" in leg:
            try:
                ts = tuple(float(v) for v in leg["tarsomeres"][:5])
                s = sum(ts) or 1.0
                self.tarsomeres = tuple(v / s for v in ts)
            except (TypeError, ValueError):
                self.tarsomeres = None
        # 有效髋基节投影(v2)与膝弓向权重(v2;条目级覆盖 > 按对缺省)
        try:
            bk = float(leg.get("base_k", BASE_K.get(self.kind, 0.0)))
        except (TypeError, ValueError):
            bk = BASE_K.get(self.kind, 0.0)
        try:
            bo = math.radians(float(leg.get("base_off",
                                             BASE_OFF.get(self.kind, 0.0))))
        except (TypeError, ValueError):
            bo = math.radians(BASE_OFF.get(self.kind, 0.0))
        self.base_k = clamp(bk, 0.0, 1.0)
        self.base_off = bo
        try:
            bw = float(leg.get("bend_w", BEND_W.get(self.kind, 0.0)))
        except (TypeError, ValueError):
            bw = BEND_W.get(self.kind, 0.0)
        self.bend_w = clamp(bw, 0.0, 1.5)
        try:
            sp = float(leg.get("tarsus_splay",
                               TARSUS_SPLAY.get(self.kind, 0.0)))
        except (TypeError, ValueError):
            sp = TARSUS_SPLAY.get(self.kind, 0.0)
        self.tarsus_splay = clamp(sp, -45.0, 45.0)

        # 中立朝向(腿根→home;镜像到"侧向恒正"的腿坐标系计算)。
        # 镜像修正(骨架重构波):attach 与 home 的 y 必须**同侧镜像**——
        # 历史版只镜像 home 不镜像 attach,left 腿的 yaw0 混入 2×attach_y
        # 偏差(左右腿包络中心不对称,前腿偏差达 15°)。
        ax_b, ay_b = self.attach                       # 身体系(hip 计算用)
        ax, ay = self.attach[0], self.attach[1] * self.side   # 腿系(yaw0 用)
        hx, hy = self.home[0], self.home[1] * self.side
        yaw0 = math.atan2(hy - ay, hx - ax)
        self.yaw0 = yaw0
        # 有效髋 = attach 沿中立方向前移 coxa(coxa 拆分,IK 平面几何原点);
        # 身体系方向单位向量 = (home−attach)/|home−attach|
        _d0 = math.hypot(self.home[0] - ax_b, self.home[1] - ay_b)
        if _d0 > 1e-6 and self.coxa > 0.0:
            _ux = (self.home[0] - ax_b) / _d0
            _uy = (self.home[1] - ay_b) / _d0
            self.hip = (ax_b + _ux * self.coxa, ay_b + _uy * self.coxa)
        else:
            self.hip = self.attach
        # v2(ADR-0028):attach=体壁穿出点;有效髋沿 (φ0+base_off) 前移
        # (coxa+trochanter)·base_k。腿系→身系镜像回。
        if self.v2:
            _blen = (self.coxa + self.trochanter) * self.base_k
            _bang = yaw0 + self.base_off
            _bux, _buy = math.cos(_bang), math.sin(_bang)
            self.hip = (ax_b + _bux * _blen, ay_b + _buy * self.side * _blen)
        self.coxa_tip = self.hip   # v2 7 点契约:coxa_tip=CTr=有效髋(合并珠远端)
        # 后足膝弯分支在中立姿态确定。若每帧按法线 x 符号重选半球，
        # 支撑足转弯时越过身体局部中线会令膝点瞬跳。
        self._rear_bend_sign = 1
        if self.kind == "rear":
            _home_x = self.home[0] - self.hip[0]
            _home_y = (self.home[1] - self.hip[1]) * self.side
            _home_yaw = math.atan2(_home_y, _home_x)
            _neutral_tarsus = _home_yaw - math.radians(self.tarsus_splay)
            _neutral_ankle_y = self.home[1] * self.side \
                - math.sin(_neutral_tarsus) * (self.tarsus + self.claw) \
                * self.tarsus_ext
            # n0.x = -e1.y。保留旧前弯规则在静息时选中的符号，之后随腿连续运动。
            self._rear_bend_sign = (
                1 if _neutral_ankle_y < self.hip[1] * self.side else -1)
        # 髋高 H(v1/v2):名义姿态下足端恰好触地 → H = l2·sin(δ_n) − l1·sin(β_n)
        beta_n, gamma_n = _BETA_N, _GAMMA_N
        if "pose_n" in leg:
            try:
                beta_n = math.radians(float(leg["pose_n"][0]))
                gamma_n = math.radians(float(leg["pose_n"][1]))
            except (TypeError, ValueError, IndexError):
                pass
        delta_n = math.pi - gamma_n - beta_n
        h_nom = self.l2 * math.sin(delta_n) - self.l1 * math.sin(beta_n)
        span = self.l1 + self.l2
        # 下限 0.05×span(R2):前/中足基节近乎贴地;上限防"腿软"塌平。
        self.height = clamp(h_nom, 0.05 * span, 0.55 * span)
        # 可达域(v1 平面距离 d 环带,自有效髋起算),由膝内角包络反解:
        if self.v2:
            # v2:γ∈[40°,165°] → 3D 髋-踝距离环带,再以名义髋高折算平面环带。
            g_lo, g_hi = KNEE_RANGE_V2
            r_lo2 = self.l1 ** 2 + self.l2 ** 2 \
                - 2 * self.l1 * self.l2 * math.cos(math.radians(g_lo))
            r_hi2 = self.l1 ** 2 + self.l2 ** 2 \
                - 2 * self.l1 * self.l2 * math.cos(math.radians(g_hi))
            h2 = self.height ** 2
            self.d_min = max(0.10 * span,
                             math.sqrt(max(r_lo2 - h2, 0.0)))
            self.d_max = max(math.sqrt(max(r_hi2 - 0.25 * h2, 1e-6)),
                             self.d_min + 1e-3)
        else:
            r_gamma_max = math.sqrt(max(self.l1 ** 2 + self.l2 ** 2
                                        - 2 * self.l1 * self.l2 * math.cos(math.radians(140.0)), 1e-9))
            r_gamma_min = math.sqrt(max(self.l1 ** 2 + self.l2 ** 2
                                        - 2 * self.l1 * self.l2 * math.cos(math.radians(20.0)), 1e-9))
            self.d_min = max(0.14 * span,
                             math.sqrt(max(r_gamma_min ** 2 - self.height ** 2, 0.0)))
            self.d_max = min(0.97 * span,
                             math.sqrt(max(r_gamma_max ** 2 - (0.5 * self.height) ** 2, 1e-6)))
            self.d_max = max(self.d_max, self.d_min + 1e-3)

        # ================================================================
        # ---- v3 真 3D 链参数(规格 §3.1;防御式解析,缺省安全回退) ----
        # ================================================================
        # AG5 缩放:base/rig 注入的 1x 归一系数(私有键,species PARAMS 不携带)
        try:
            self.scale_k = clamp(float(leg.get("_scale_k", 1.0)), 0.05, 8.0)
        except (TypeError, ValueError):
            self.scale_k = 1.0
        len3d = leg.get("len3d")
        self.has_len3d = isinstance(len3d, dict)
        if not self.has_len3d:
            len3d = {}
        try:
            self.coxa3d = max(0.0, float(len3d.get("coxa", self.coxa))) * self.scale_k
            self.f3d = max(1e-3, float(len3d.get("femur", self.l1))) * self.scale_k
            self.t3d = max(1e-3, float(len3d.get("tibia", self.l2))) * self.scale_k
            self.tarsus3d = max(1e-3,
                                float(len3d.get("tarsus", self.tarsus))) * self.scale_k
        except (TypeError, ValueError):
            self.coxa3d = self.coxa * self.scale_k
            self.f3d = self.l1 * self.scale_k
            self.t3d = self.l2 * self.scale_k
            self.tarsus3d = self.tarsus * self.scale_k
        self.span3d = self.f3d + self.t3d
        # 有效髋 3D:attach 沿中立足向前移 coxa3d·cos(θ_coxa)(基节俯角 60°,
        # 俯视短投影,规格 §1.2;无 len3d 的腿保持旧"全长前移"语义)
        _c3h = self.coxa3d * math.cos(math.radians(COXA_PITCH_DEG)) \
            if self.has_len3d else self.coxa
        if _d0 > 1e-6 and _c3h > 0.0:
            _u3x = (self.home[0] - ax_b) / _d0
            _u3y = (self.home[1] - ay_b) / _d0
            self.hip3d = (ax_b + _u3x * _c3h, ay_b + _u3y * _c3h)
        else:
            self.hip3d = self.attach
        # rest3d:静息姿态(β_n 股节俯角下垂正、γ_n 膝内角;R1 §3.2/§7.3)
        rest3d = leg.get("rest3d")
        self.has_rest3d = isinstance(rest3d, dict)
        if not self.has_rest3d:
            rest3d = {}
        _b3n = _g3n = None
        if self.has_rest3d:
            try:
                _b3n = float(rest3d.get("femur_pitch_deg", 30.0))
                _g3n = float(rest3d.get("knee_deg", 140.0))
            except (TypeError, ValueError):
                _b3n = _g3n = None
        if _b3n is None:
            # 回退:pose_n(遗留上拱口径)→ 度数存档,按遗留公式反解 H
            _b3n, _g3n = math.degrees(beta_n), math.degrees(gamma_n)
            _dlt3 = math.pi - gamma_n - beta_n
            h3 = self.t3d * math.sin(_dlt3) - self.f3d * math.sin(beta_n)
        else:
            # 3D 竖直链闭式(见模块头):δ' = γ+β−180, H = f·sinβ + t·sinδ'
            _bn = math.radians(clamp(_b3n, -10.0, 89.0))
            _gn = math.radians(clamp(_g3n, 20.0, 179.0))
            _dlt3 = _gn + _bn - math.pi
            h3 = self.f3d * math.sin(_bn) + self.t3d * math.sin(_dlt3)
        self.height3d = clamp(h3, 0.05 * self.span3d, 0.55 * self.span3d)
        try:
            _yaw3 = float(rest3d.get("yaw_deg", math.degrees(self.yaw0)))
        except (TypeError, ValueError):
            _yaw3 = math.degrees(self.yaw0)
        self.rest3d = {"yaw_deg": round(_yaw3, 2),
                       "femur_pitch_deg": round(_b3n, 2),
                       "knee_deg": round(_g3n, 2)}
        # rom3d:限位(度)。yaw (朝后,朝前);knee 缺省回退现行 TIBIA_RANGE
        # (裁决 #2);femur_pitch 缺省回退 FEMUR_RANGE;tita 装饰带。
        rom3d = leg.get("rom3d")
        if not isinstance(rom3d, dict):
            rom3d = {}
        self.rom3d = {
            "yaw": _pair2(rom3d.get("yaw"), (rear_env, front_env)),
            "femur_pitch": _pair2(rom3d.get("femur_pitch"), FEMUR_RANGE),
            "knee": _pair2(rom3d.get("knee"), TIBIA_RANGE),
            "tita": _pair2(rom3d.get("tita"), ROM3D_TITA_DEFAULT),
        }
        self._front_env3 = math.radians(self.rom3d["yaw"][1])
        self._rear_env3 = math.radians(self.rom3d["yaw"][0])
        # tarsus_seg:5 跗分节比例(和归一;缺省等分)。累积比 c1..c3 供平铺。
        ts_raw = leg.get("tarsus_seg")
        segs = None
        if isinstance(ts_raw, (list, tuple)) and len(ts_raw) == 5:
            try:
                vals = [float(v) for v in ts_raw]
                if all(math.isfinite(v) and v >= 0.0 for v in vals):
                    s = sum(vals)
                    if s > 1e-6:
                        segs = tuple(v / s for v in vals)
            except (TypeError, ValueError):
                segs = None
        self.tarsus_seg = segs if segs else TARSUS_SEG_DEFAULT
        _c = self.tarsus_seg
        self.tarsus_cum = (_c[0], _c[0] + _c[1], _c[0] + _c[1] + _c[2])
        # 膝方位前偏系数(规格 §2.4;R1 §7.3 方位超前 ≈0.35×β)
        try:
            flk = float(leg.get("femur_lead_k", FEMUR_LEAD_DEFAULT))
        except (TypeError, ValueError):
            flk = FEMUR_LEAD_DEFAULT
        self.femur_lead_k = clamp(flk, 0.0, 1.0)
        # 3D 可达环带(踝口径;式同遗留版,长度换 f3d/t3d、膝限位换 rom3d.knee)
        _k_lo = math.radians(self.rom3d["knee"][0])
        _k_hi = math.radians(self.rom3d["knee"][1])
        r_lo = math.sqrt(max(self.f3d ** 2 + self.t3d ** 2
                             - 2 * self.f3d * self.t3d * math.cos(_k_lo), 1e-9))
        r_hi = math.sqrt(max(self.f3d ** 2 + self.t3d ** 2
                             - 2 * self.f3d * self.t3d * math.cos(_k_hi), 1e-9))
        self.d_min3 = max(0.14 * self.span3d,
                          math.sqrt(max(r_lo ** 2 - self.height3d ** 2, 0.0)))
        self.d_max3 = min(0.97 * self.span3d,
                          math.sqrt(max(r_hi ** 2 - (0.5 * self.height3d) ** 2,
                                        1e-6)))
        self.d_max3 = max(self.d_max3, self.d_min3 + 1e-3)

    # ---------------- 主接口 ----------------
    def solve(self, foot_target: tuple[float, float], lift: float = 0.0,
              tarsus_dir: float | None = None
              ) -> dict:
        """足端目标(**爪尖**钉点,身体局部坐标)→ 关节角与腿链点。

        链分派(L2F 收尾波 2026-09-16 修复;原 v3 波分派令已提交的 v2 7 点
        契约与 A1~A12 验收全部失锚,是本波 4 红项的共同根因,证据见
        scratch/_l2f_evidence/MANIFEST.md 与 docs/references/腿部3D实施与验收记录.md):
          NEUROPET_LEG3D=2 → 全体 _solve_3d(v3 8 点开发档;species len3d 五键
                             就绪后由 v3 波按其规格 §6/§7 验收并同步改写
                             test_body 契约与探针口径后方可切缺省,不得静默);
          缺省(=1/未设)  → "trochanter/chain3d" 键 → _solve_v2(v2 7 点,
                             ADR-0026/0027/0028 冻结契约;蟑螂);其余 →
                             _solve_legacy(v1 4 点;果蝇);
          NEUROPET_LEG3D=0 → 与缺省档相同(回滚语义 = "v3 波合入前行为":
                             v2 腿走 _solve_v2、其余 v1,见规格 §7.1;
                             reach 亦保持 v2 全链口径,tests/test_body 全绿)。
        腿条目同时携带 v2 键与 len3d 键时(当前 working copy 的物种 PARAMS)
        v2 键优先:len3d 五键在缺省档为休眠数据,不激活 8 点链。

        _solve_v2 返回 7 点契约(ADR-0027):
            {"points": [wall, coxa_tip, knee, ankle, tar1, tar3, claw],
             "angles": (yaw_deg, beta_deg, gamma_deg),
             "clamped": bool, "lift": lift}
        _solve_3d 返回 8 点契约(规格 §1.2):
            {"points": [attach, CTr, knee, ankle, m1, m2, m3, claw],
             "angles": (yaw_deg, beta_deg, gamma_deg),
             "angles3d": (yaw_deg, beta_deg, gamma_deg, delta_deg, phi_deg),
             "clamped": bool, "lift": lift}
        _solve_legacy(NEUROPET_LEG3D=0 回滚,逐位一致)返回:
            {"points": [髋, 膝, 踝, 足端](4 点),
              "angles": (yaw_deg, femur_deg, tibia_deg),
              "clamped": bool, "lift": lift}
        任何越界目标都被钳制:关节角 ROM/硬限位;不可达时踝钳到可达环带。

        tarsus_dir:可选,跗节朝向(身体局部弧度,未镜像)。支撑相由 body 层
        锚定世界朝向(触地时冻结)——真实昆虫跗节贴地后不再随腿系平面
        旋转(旋转发生在胫节上方关节);缺省 = 中立足向 yaw0。
        """
        mode = os.environ.get("NEUROPET_LEG3D", "1")
        if self.v2 and mode != "2":
            # v2 键激活(缺省档 + LEG3D=0 回滚档):7 点链。
            # 回滚档语义 = "v3 波合入前行为"(规格 §7.1)—— 合入前蟑螂
            # 正是 _solve_v2,故回滚不得把 v2 腿降级到 v1。
            return self._solve_v2(foot_target, lift, tarsus_dir)
        if mode == "2":
            return self._solve_3d(foot_target, lift, tarsus_dir)
        return self._solve_legacy(foot_target, lift, tarsus_dir)

    # ---------------- v3 真 3D 链(规格 §2) ----------------
    def _solve_3d(self, foot_target: tuple[float, float], lift: float,
                  tarsus_dir: float | None) -> dict:
        lift = max(0.0, float(lift))
        # 0) 镜像入腿系;钉足=爪尖:跗链自爪尖回铺(规格 §2.0)
        tx, ty = foot_target[0], foot_target[1] * self.side
        r_vis = self.tarsus_ext * self.tarsus3d
        if tarsus_dir is not None:
            ta = tarsus_dir * self.side
            dtx, dty = math.cos(ta), math.sin(ta)
        else:
            dtx, dty = math.cos(self.yaw0), math.sin(self.yaw0)
        anx, any_ = tx - dtx * r_vis, ty - dty * r_vis
        # 1) ThC 偏航(有效髋 E;偏差包络钳制,含 1e-9 容差判 clamped)
        ex, ey = self.hip3d[0], self.hip3d[1] * self.side
        dx, dy = anx - ex, any_ - ey
        clamped = False
        d_plane = math.hypot(dx, dy)
        if d_plane < 1e-6:
            dx, dy, d_plane = math.cos(self.yaw0), math.sin(self.yaw0), 1e-6
        yaw = math.atan2(dy, dx)
        delta_yaw = yaw - self.yaw0                 # 偏差归一化到 (-pi, pi]
        delta_yaw = math.atan2(math.sin(delta_yaw), math.cos(delta_yaw))
        yaw_c = self.yaw0 + clamp(delta_yaw, -self._front_env3, self._rear_env3)
        if abs(yaw_c - yaw) > 1e-9:
            clamped = True
        ux, uy = math.cos(yaw_c), math.sin(yaw_c)
        # 2) 竖直落差(精确 z 几何;lift_gain=1.0,下钳 0.30·H 防越界)
        h = max(self.height3d - LIFT_GAIN_DEFAULT * lift, 0.30 * self.height3d)
        # 3) 径向环带钳制(踝口径)
        d = clamp(d_plane, self.d_min3, self.d_max3)
        if d != d_plane:
            clamped = True
        # 4) 竖直平面 2 骨闭式解(膝下垂支:β=股节俯角,下垂为正)
        r = math.hypot(d, h)
        r = clamp(r, 1e-4, 0.999 * (self.f3d + self.t3d))
        elev0 = math.atan2(h, d)                           # 髋→踝视线俯角
        cos_a1 = clamp((r * r + self.f3d * self.f3d - self.t3d * self.t3d)
                       / (2 * r * self.f3d), -1.0, 1.0)
        beta = elev0 + math.acos(cos_a1)                   # 膝在视线下方支
        cos_g = clamp((self.f3d ** 2 + self.t3d ** 2 - r * r)
                      / (2 * self.f3d * self.t3d), -1.0, 1.0)
        gamma = math.acos(cos_g)                           # 膝内角(180=伸直)
        # 5) ROM 钳制 + 胫节俯角(δ = γ+β−180°,下正;内角关系反解)
        fem_lo, fem_hi = self.rom3d["femur_pitch"]
        knee_lo, knee_hi = self.rom3d["knee"]
        beta_c = clamp(beta, math.radians(fem_lo), math.radians(fem_hi))
        gamma_c = clamp(gamma, math.radians(knee_lo), math.radians(knee_hi))
        if abs(beta_c - beta) > 1e-9 or abs(gamma_c - gamma) > 1e-9:
            clamped = True
        delta = gamma_c + beta_c - math.pi
        delta = clamp(delta, math.radians(TIBIA_PITCH_MIN_DEG),
                      math.radians(TIBIA_PITCH_MAX_DEG))
        # 6) 重建(钳后角度):踝在 α 射线上;膝方位前偏 φ=k·β(朝体前外,
        #    R1 §7.3:股方位恒先于足向 ≈0.35β;腿系"朝前"=角度减小侧 → α−φ)
        phi = clamp(self.femur_lead_k * beta_c, 0.0,
                    math.radians(FEMUR_LEAD_MAX_DEG))
        axc, ayc = ex + ux * d, ey + uy * d                # 踝(钳后)
        ka = yaw_c - phi
        klen = self.f3d * math.cos(beta_c)
        kx, ky = ex + math.cos(ka) * klen, ey + math.sin(ka) * klen
        # 7) 跗链平铺:自踝沿 dir_t 铺 m1/m2/m3;爪尖 = 踝 + R_vis·dir_t
        #    (未钳制时与输入钉点逐位相等 → 世界钉足零滑移)
        m1 = (axc + dtx * r_vis * self.tarsus_cum[0],
              ayc + dty * r_vis * self.tarsus_cum[0])
        m2 = (axc + dtx * r_vis * self.tarsus_cum[1],
              ayc + dty * r_vis * self.tarsus_cum[1])
        m3 = (axc + dtx * r_vis * self.tarsus_cum[2],
              ayc + dty * r_vis * self.tarsus_cum[2])
        tpx, tpy = axc + dtx * r_vis, ayc + dty * r_vis
        # 8) 镜像回身体系输出 8 点
        pts = [(self.attach[0], self.attach[1] * self.side),
               (ex, ey), (kx, ky), (axc, ayc), m1, m2, m3, (tpx, tpy)]
        if self.side < 0:
            pts = [(px, py * self.side) for px, py in pts]
        return {"points": pts,
                "angles": (math.degrees(yaw_c), math.degrees(beta_c),
                           math.degrees(gamma_c)),
                "angles3d": (math.degrees(yaw_c), math.degrees(beta_c),
                             math.degrees(gamma_c), math.degrees(delta),
                             math.degrees(phi)),
                "clamped": clamped, "lift": lift}

    # ---------------- v1 平面链(逐字保留;NEUROPET_LEG3D=0) ----------------
    def _solve_legacy(self, foot_target: tuple[float, float], lift: float,
                      tarsus_dir: float | None) -> dict:
        """旧平面 2 骨链(4 点)。本函数体 = v3 波之前 solve() 的 v1 分支逐字
        保留(回滚开关的"重构前行为"基准)。"""
        lift = max(0.0, float(lift))
        # 镜像到腿坐标系(侧向恒正),使左右腿共用一套数学
        tx, ty = foot_target[0], foot_target[1] * self.side
        ax, ay = self.hip[0], self.hip[1] * self.side   # 平面几何以有效髋为原点
        dx, dy = tx - ax, ty - ay
        clamped = False

        # 1) coxa yaw 包络:偏差相对中立朝向,"朝前"为负方向(朝 +x 收拢)
        d_plane = math.hypot(dx, dy)
        if d_plane < 1e-6:
            dx, dy, d_plane = math.cos(self.yaw0), math.sin(self.yaw0), 1e-6
        yaw = math.atan2(dy, dx)
        delta_yaw = yaw - self.yaw0                 # 偏差归一化到 (-pi, pi]
        delta_yaw = math.atan2(math.sin(delta_yaw), math.cos(delta_yaw))
        yaw_c = self.yaw0 + clamp(delta_yaw, -self._front_env, self._rear_env)
        if abs(yaw_c - yaw) > 1e-9:   # 容差比较:atan2 归一的浮点噪声不算钳制
            clamped = True
        ux, uy = math.cos(yaw_c), math.sin(yaw_c)

        # 2) 竖直落差(lift 抬足 → 落差变小,但不低于一半站高,防"腿软")
        h_eff = max(self.height - 0.45 * lift, 0.5 * self.height)
        # 3) 径向可达域钳制(不可达 → 足端钳到环带)
        d = clamp(d_plane, self.d_min, self.d_max)
        if d != d_plane:
            clamped = True

        # 4) 平面双骨 IK(竖直平面内:髋→足视线的俯角 + 膝上拱)
        r = math.hypot(d, h_eff)
        r = clamp(r, 1e-4, 0.999 * (self.l1 + self.l2))
        elev0 = math.atan2(h_eff, d)                       # 髋→足视线低于水平的角度
        cos_a1 = clamp((r * r + self.l1 * self.l1 - self.l2 * self.l2)
                       / (2 * r * self.l1), -1.0, 1.0)
        beta = math.acos(cos_a1) - elev0                   # femur 仰角(膝上线之上)
        cos_g = clamp((self.l1 ** 2 + self.l2 ** 2 - r * r)
                      / (2 * self.l1 * self.l2), -1.0, 1.0)
        gamma = math.acos(cos_g)                           # 膝内角
        # 5) 关节角硬限位(正常步态内不触发;极端目标在此收口)
        beta_c = clamp(beta, math.radians(FEMUR_RANGE[0]), math.radians(FEMUR_RANGE[1]))
        gamma_c = clamp(gamma, math.radians(TIBIA_RANGE[0]), math.radians(TIBIA_RANGE[1]))
        if abs(beta_c - beta) > 1e-9 or abs(gamma_c - gamma) > 1e-9:
            clamped = True
        delta = max(1e-3, math.pi - gamma_c - beta_c)      # tibia 相对水平的俯角
        delta = min(delta, math.radians(88.0))

        # 6) 由(可能已钳制的)角度重建腿链投影点
        # 膝/踝在腿坐标系内恒向"前外"侧拱(镜像回身体坐标系后自然左右对称)
        bow1 = self.l1 * math.sin(beta_c) * 0.35
        bow2 = self.l2 * math.sin(delta) * 0.10
        # pts[0] = 真实 attach(渲染腿从体壁长出,首段含基节);
        # 膝/踝/足以有效髋为链起点(coxa 拆分,IK 原点)
        kx = ax + ux * (self.l1 * math.cos(beta_c)) + uy * bow1
        ky = ay + uy * (self.l1 * math.cos(beta_c)) - ux * bow1
        axk = kx + ux * (self.l2 * math.cos(delta)) + uy * bow2
        ayk = ky + uy * (self.l2 * math.cos(delta)) - ux * bow2
        # 足端 = 目标(未越界时);越界时沿钳制后方向放到可达距离
        foot_base = (ax + ux * d, ay + uy * d)
        # 跗节:沿行进方向再铺一小节(足端小爪的锚点;外伸系数按物种贴地程度)。
        # 支撑相 tarsus_dir 锚定世界朝向(镜像到腿系 = 角度×side);摆动相随腿系平面。
        if tarsus_dir is not None:
            ta = tarsus_dir * self.side
            txu, tyu = math.cos(ta), math.sin(ta)
        else:
            txu, tyu = ux, uy
        fx = foot_base[0] + txu * self.tarsus * self.tarsus_ext
        fy = foot_base[1] + tyu * self.tarsus * self.tarsus_ext

        pts = [(self.attach[0], self.attach[1] * self.side), (kx, ky), (axk, ayk), (fx, fy)]
        if self.side < 0:  # 镜像回身体坐标系
            pts = [(px, py * self.side) for px, py in pts]
        return {"points": pts,
                "angles": (math.degrees(yaw_c), math.degrees(beta_c),
                           math.degrees(gamma_c)),
                "clamped": clamped, "lift": lift}

    # ---------------- v2 链(4+2 自由度;ADR-0026/0027/0028;v3 起休眠) ----------------
    def _solve_v2(self, foot_target: tuple[float, float], lift: float,
                  tarsus_dir: float | None) -> dict:
        """规格 §8.2 五步:偏航钳位 → 跗节回扣 → 竖直落差/环带 → 球面交膝 →
        跗节分节。全部闭式;踝点在环带内时**精确**落在步态钉住点。
        (v3 起 solve() 不再路由到本函数;保留供回溯/对照。)"""
        lift = max(0.0, float(lift))
        # 镜像到腿坐标系(侧向恒正),左右腿共用一套数学
        tx, ty = foot_target[0], foot_target[1] * self.side
        ax, ay = self.hip[0], self.hip[1] * self.side
        clamped = False

        # 1) ThC 偏航包络(同 v1:偏差相对中立朝向,"朝前"为负方向)
        dx, dy = tx - ax, ty - ay
        d_plane = math.hypot(dx, dy)
        if d_plane < 1e-6:
            dx, dy = math.cos(self.yaw0), math.sin(self.yaw0)
            d_plane = 1e-6
        yaw = math.atan2(dy, dx)
        delta_yaw = math.atan2(math.sin(yaw - self.yaw0),
                               math.cos(yaw - self.yaw0))
        yaw_c = self.yaw0 + clamp(delta_yaw, -self._front_env, self._rear_env)
        if abs(yaw_c - yaw) > 1e-9:
            clamped = True
        ux, uy = math.cos(yaw_c), math.sin(yaw_c)

        # 2) 跗节回扣(规格 §8.2-2):踝目标 = 爪尖钉住点 − 跗节向量。
        #    支撑相(tarsus_dir 已锚定)ext=1.0 贴地全外伸;摆动相沿链方向
        #    ext×0.7(跗节下垂,俯视可见缩短)。
        stance = tarsus_dir is not None and lift <= 0.1
        if tarsus_dir is not None:
            ta = tarsus_dir * self.side
        else:
            ta = yaw_c
        # TiTa 前勾(照片 §3.2):体系内朝体前偏转 splay;腿系镜像后对左右
        # 腿对称(左 ta=166.5−8.8=157.7,右 ta=66−17=49,均朝体前)。
        ta -= math.radians(self.tarsus_splay)
        txu, tyu = math.cos(ta), math.sin(ta)
        ext = 1.0 if stance else 0.70
        l_tar = (self.tarsus + self.claw) * self.tarsus_ext * ext
        avx, avy = tx - txu * l_tar, ty - tyu * l_tar          # 踝目标 A'

        # 3) 竖直落差(同 v1)+ 可达环带(γ∈[40°,165°],按 h_eff 精算)
        h_eff = max(self.height - 0.45 * lift, 0.5 * self.height)
        g_lo2 = self.l1 ** 2 + self.l2 ** 2 \
            - 2 * self.l1 * self.l2 * math.cos(math.radians(KNEE_RANGE_V2[0]))
        g_hi2 = self.l1 ** 2 + self.l2 ** 2 \
            - 2 * self.l1 * self.l2 * math.cos(math.radians(KNEE_RANGE_V2[1]))
        d = math.hypot(avx - ax, avy - ay)
        if d < 1e-6:
            avx, avy, d = ax + ux, ay + uy, 1.0
        d_min = max(0.10 * (self.l1 + self.l2),
                    math.sqrt(max(g_lo2 - h_eff * h_eff, 0.0)))
        d_max = max(math.sqrt(max(g_hi2 - h_eff * h_eff, 1e-6)), d_min + 1e-3)
        d_cl = clamp(d, d_min, d_max)
        d_was_clamped = d_cl != d
        if d_was_clamped:
            clamped = True
        avx = ax + (avx - ax) * (d_cl / d)
        avy = ay + (avy - ay) * (d_cl / d)

        # 4) 球面交膝:膝在 |膝−髋|=l1、|膝−踝|=l2 的交圆上;圆方位
        #    e2 = normalize(P⊥(ẑ) + w·P⊥(n̂)) —— ẑ 分量=竖直面内上拱(T1:
        #    膝弯主要发生在竖直面,俯视只余粗细/抬足),n̂ 分量=水平面按对
        #    定向的弓向(前/中=体后侧,后=体前侧)。踝点精确闭合。
        vx, vy, vz = avx - ax, avy - ay, -h_eff
        r = math.sqrt(vx * vx + vy * vy + vz * vz)
        r = max(r, 1e-6)
        e1x, e1y, e1z = vx / r, vy / r, vz / r
        a_ = (self.l1 * self.l1 - self.l2 * self.l2 + r * r) / (2.0 * r)
        a_ = clamp(a_, -self.l1, self.l1)
        b_ = math.sqrt(max(self.l1 * self.l1 - a_ * a_, 0.0))
        # P⊥(ẑ):竖直单位向量在弦正交平面上的投影(竖直上拱分量)
        pz = (0.0 - e1z * e1x, 0.0 - e1z * e1y, 1.0 - e1z * e1z)
        # n̂:平面弦垂线,按对取前/后一侧(rear 膝向前弓,前/中向后弓)
        n0x, n0y = -e1y, e1x
        if self.kind == "rear":
            if self._rear_bend_sign < 0:
                n0x, n0y = -n0x, -n0y
        elif n0x > 0.0:
            n0x, n0y = -n0x, -n0y
        w = self.bend_w
        e2x = pz[0] + w * n0x
        e2y = pz[1] + w * n0y
        e2z = pz[2]
        e2n = math.sqrt(e2x * e2x + e2y * e2y + e2z * e2z)
        if e2n < 1e-9:                       # 退化(竖直弦):纯竖直上拱
            e2x, e2y, e2z, e2n = 0.0, 0.0, 1.0, 1.0
        e2x, e2y, e2z = e2x / e2n, e2y / e2n, e2z / e2n
        kx3 = ax + a_ * e1x + b_ * e2x
        ky3 = ay + a_ * e1y + b_ * e2y
        kz3 = h_eff + a_ * e1z + b_ * e2z
        beta = math.atan2(kz3 - h_eff,
                          max(1e-6, math.hypot(kx3 - ax, ky3 - ay)))
        cos_g = clamp((self.l1 ** 2 + self.l2 ** 2 - r * r)
                      / (2 * self.l1 * self.l2), -1.0, 1.0)
        gamma = math.acos(cos_g)

        # 5) 跗节 5 分节:tar1/tar3 锚点沿跗节方向平铺(tarsomeres 占比,
        #    爪不计入分节)。环带未钳制时跗节=名义长、爪尖=步态钉住点
        #    (逐位一致);环带钳制时(足端摆到髋前,踝目标落入折叠不可达
        #    区)由 5 节被动跗节"弯折"吸收:方向转为钳制踝→钉住点,长度取
        #    实际距离(≤1.25×名义仍钉在原点,足底不滑);过伸按上限收短。
        ts_tot = self.tarsus + self.claw
        ts_frac = (self.tarsus / ts_tot) if ts_tot > 1e-9 else 1.0
        tm = self.tarsomeres or (0.30, 0.22, 0.18, 0.16, 0.14)
        l_eff = l_tar
        if d_was_clamped:
            vfx, vfy = tx - avx, ty - avy
            n_f = math.hypot(vfx, vfy)
            if n_f > 1e-6:
                txu, tyu = vfx / n_f, vfy / n_f
                l_eff = min(n_f, 1.25 * l_tar)
        claw = (avx + txu * l_eff, avy + tyu * l_eff)
        f1 = tm[0] * ts_frac * l_eff
        f3 = (tm[0] + tm[1] + tm[2]) * ts_frac * l_eff
        pts = [(self.attach[0], self.attach[1] * self.side),   # 0 wall(体壁)
               (ax, ay),                                       # 1 coxa_tip(CTr)
               (kx3, ky3),                                     # 2 knee(FTi)
               (avx, avy),                                     # 3 ankle(TiTa)
               (avx + txu * f1, avy + tyu * f1),               # 4 tar1
               (avx + txu * f3, avy + tyu * f3),               # 5 tar3
               claw]                                           # 6 claw(钉住)
        if self.side < 0:  # 镜像回身体坐标系
            pts = [(px, py * self.side) for px, py in pts]
        return {"points": pts,
                "angles": (math.degrees(yaw_c), math.degrees(beta),
                           math.degrees(gamma)),
                "clamped": clamped, "lift": lift}

    @property
    def reach(self) -> float:
        """腿全长,供步幅/避让/静息钳制参考。**gait 阀与 gait_sim 训练几何的
        冻结口径 = 跟随生效链**(L2F 收尾波 2026-09-16 恢复 ADR-0027 语义;
        v3 波曾把缺省档也改成 l1+l2,致 gait 静息钳制/过拉伸阀的可达域整体
        塌缩 —— 静息足距 mid 0.724<0.85 出带、探针 A2/A5/A8/A9 全红、
        世界钉足 drift 5.64px,4 红项根因):
        - v2 链生效(trochanter 键激活且非 LEG3D=2 开发档,即缺省档与
          LEG3D=0 回滚档):v2 全链口径 l1+l2+(tarsus+claw)·tarsus_ext
          (ADR-0027;前 51.0 / 中 61.2 / 后 102.0px,gait 阀
          0.78/0.86/0.88×reach 与 GaitProfile provenance 均锚定此值);
        - LEG3D=2(v3 8 点开发档):恒为 l1+l2 —— 与 v3 可达域(踝环带
          [d_min3, d_max3],f3d+t3d 口径)经验证覆盖整个 gait 域(零钳制,
          世界钉足不滑);已落盘 GaitProfile 的 provenance 复核亦锚定
          l1+l2 口径。
        3D 节段和另见 reach3d(=f3d+t3d);gait 阀切换 reach3d 须与数据重训
        同批(L2 校准波)。"""
        if self.v2 and os.environ.get("NEUROPET_LEG3D", "1") != "2":
            return self.l1 + self.l2 + \
                (self.tarsus + self.claw) * self.tarsus_ext
        return self.l1 + self.l2

    @property
    def reach3d(self) -> float:
        """3D 节段和 f3d+t3d(规格 §3.2 口径;缺省 = l1+l2 数值不变)。
        gait 阀切换到本口径须与数据重训同批(L2 校准波),当前不切换。"""
        return self.f3d + self.t3d
