"""黑腹果蝇物种包。默认大脑:FlyConnectomeBrain(brain.fly_connectome,FlyWire 简化回路)。

感知画像与运动参数按《docs/references/行为学_美洲大蠊与果蝇.md》精调;
BL/s 语义:1 BL = 40px(观赏体长;r19 R2 由 30 起提到 40——30px 下折翅
盖背=一块米色板,用户报障"像木板",40px 画布 63²→83² 才能读出
头/眼/翅/腿层次。回退:改回 30 即恢复旧观感,渲染自适应无其他改动)。
"""
from __future__ import annotations

from neuropet.brain.fly_brain import FlyConnectomeBrain
from neuropet.body.base import GenericInsectBody
from neuropet.core.interfaces import SpeciesPlugin

# 运动参数(BL/s 换算自《桌宠参数速查表》):爬行典型 3 BL/s(峰值 6~8);
# 飞行真实 80~120 BL/s = 2400~3600px/s,屏幕观赏截断至 ~1000px/s(ADR-005);
# 步频 = speed/stride_len ≈ 8Hz(果蝇小步幅高频,定性合理)。
PARAMS = {
    # GaitProfile(F2)查找键:运行时按此在 data/gait/ 查训练产物,
    # 缺失回退默认文件/内置常数(body/base.py 构造时注入)。
    "gait_species": "fruitfly",
    # 观赏体长(r19 R2:30→40px,见模块 docstring;BL/s 注释按 30px 口径写,
    # 实际 BL/s 随体长增大同比例变小,行为节奏 step_hz=v/stride_len 不变)
    "body_len": 40,
    "window_half": 78,
    "segments": [
        (12, 0, 7.5, 7),    # 头(含大复眼)
        (1, 0, 9, 8),       # 胸(全身最宽处)
        (-11, 0, 8, 6),     # 腹(半径 11→8:果蝇胸最宽、腹收窄,权威 §1)
    ],
    "legs": [
        # 骨架重构波(果蝇小体型简化骨架,D2 §1.4 权威):
        # 节段比例取 NeuroMechFly v2 官方模型【核验】胫/股比(前 0.80/中 0.87/
        # 后 0.93;D2 §1.4),绝对长按权威规格 §1 侧视"单足总长 0.9~1.2BL"
        # 校准(NMF 胶囊为碰撞代理偏短 ~10%,加关节头补偿):股+胫 ≈0.60~0.70BL,
        # 跗节≈股节(果蝇与蟑螂"跗≈0.5 股"的最大形态差异,D2 §1.4)。渲染跗节(tarsus 键)为俯视可见的外伸段,按权威 §1 可见度 0.05~0.12BL定标(静息时跗分节向背面折叠,解剖全长入 rig 骨架 spec):
        #   前 股9.9/胫7.9;中 股11.0/胫9.6;后 股11.0/胫10.2(渲染跗节 4.5/5.0/5.5)。
        # home = 权威 §5.3 足印 front (11,±8) / mid (2,±10) / rear (−8,±10)
        # (静息收拢、足端伸出体轮廓 0.05~0.12BL,权威 §1);attach 使静息距
        # (home−attach)≈8px≈0.27BL——静息高拱(静息距/reach≈0.44,D2 §1.6
        # "静息腿姿高拱"),行走时步态自然外摆。
        # coxa(基节)≈0.05BL【核验:NMF 量级】,建为有效髋前移(LegKinematics)。
        # yaw_env (75,75):巡航步距 11px≈2.7×静息距,支撑相方位角扫掠 ≈±60°,
        # 小体型腿必须放宽包络(实测扫掠极值 ≈98°:步距 11px ≈2.7×静息距,支撑相足端从髋前扫到髋后;否则深摆时渲染足端从钉住点掰开)。
        # ------------------------------------------------------------------------
        # v3 真 3D 腿链(2026-09-16,规格 docs/references/三维腿链实施规格.md;
        # 数值权威 R1 docs/references/腿部三维运动学权威_蟑螂果蝇.md §5 NMF v2
        # 核验值,每腿新增 len3d/rest3d/rom3d/tarsus_seg/femur_lead_k,旧键全留)。
        # len3d = NMF v2 rigging.yaml 胶囊长(mm)×(30/2.8)px/mm ×1.1 关节头补偿
        # (规格 §4.6②;femur3d=转节+股 R1 §5.2,tarsus3d=ta1..ta5 链长和):
        #   前 coxa 0.365→4.3 / femur 0.705→8.31 / tibia 0.518→6.11 /
        #     tarsus 0.652→7.68;中 0.181→2.13 / 0.784→9.24 / 0.667→7.86 /
        #     0.671→7.91;后 0.199→2.35 / 0.836→9.85 / 0.684→8.06 / 0.771→9.09
        #   (reach3d = f3d+t3d = 14.4/17.1/17.9px;gait 阀 reach 仍 = l1+l2,
        #     见 kinematics.py "reach 语义")。
        # rest3d:β_n 股节俯角(下垂正)45/50/45°、γ_n 膝内角 140/135/140° ——
        #   NMF pose_default 量级的静息高拱初值(校准前,L2 定冻结);yaw_deg
        #   = 现行 home 几何实效 yaw0(39.8/100.0/140.2°,存档用)。
        # rom3d:yaw = (120,120)(=yaw_env);femur_pitch −20~+145(规格 §1.3
        #   果蝇 CTr 文献带映射,俯角口径);knee 15~175(规格 §1.3 果蝇 FTi
        #   文献带 10~180 内角口径收边;静息 γ≈55~60° 落带内)。
        # tarsus_seg = NMF ta1..ta5 占比(R1 §5.2 差分);femur_lead_k = 0.35。
        # ------------------------------------------------------------------------
        {"kind": "front", "side": -1, "group": 0, "attach": (5.0, -3.0),  "home": (11, -8),
         "coxa": 1.5, "l1": 9.9, "l2": 7.9, "tarsus": 4.5, "yaw_env": (120.0, 120.0),
         "len3d": {"coxa": 4.3, "femur": 8.31, "tibia": 6.11, "tarsus": 7.68},
         "rest3d": {"yaw_deg": 39.8, "femur_pitch_deg": 45.0, "knee_deg": 140.0},
         "rom3d": {"yaw": (120.0, 120.0), "femur_pitch": (-20.0, 145.0),
                   "knee": (15.0, 175.0), "tita": (-25.0, 60.0)},
         "tarsus_seg": (0.345, 0.236, 0.152, 0.133, 0.133),
         "femur_lead_k": 0.35},
        {"kind": "mid",   "side": -1, "group": 1, "attach": (3.5, -1.5),  "home": (2, -10),
         "coxa": 1.5, "l1": 11.0, "l2": 9.6, "tarsus": 5.0, "yaw_env": (120.0, 120.0),
         "len3d": {"coxa": 2.13, "femur": 9.24, "tibia": 7.86, "tarsus": 7.91},
         "rest3d": {"yaw_deg": 100.0, "femur_pitch_deg": 50.0, "knee_deg": 135.0},
         "rom3d": {"yaw": (120.0, 120.0), "femur_pitch": (-20.0, 145.0),
                   "knee": (15.0, 175.0), "tita": (-25.0, 60.0)},
         "tarsus_seg": (0.435, 0.238, 0.136, 0.095, 0.095),
         "femur_lead_k": 0.35},
        {"kind": "rear",  "side": -1, "group": 0, "attach": (-2.0, -5.0), "home": (-8, -10),
         "coxa": 1.5, "l1": 11.0, "l2": 10.2, "tarsus": 5.5, "yaw_env": (120.0, 120.0),
         "len3d": {"coxa": 2.35, "femur": 9.85, "tibia": 8.06, "tarsus": 9.09},
         "rest3d": {"yaw_deg": 140.2, "femur_pitch_deg": 45.0, "knee_deg": 140.0},
         "rom3d": {"yaw": (120.0, 120.0), "femur_pitch": (-20.0, 145.0),
                   "knee": (15.0, 175.0), "tita": (-25.0, 60.0)},
         "tarsus_seg": (0.458, 0.227, 0.126, 0.095, 0.095),
         "femur_lead_k": 0.35},
        {"kind": "front", "side": 1,  "group": 1, "attach": (5.0, 3.0),   "home": (11, 8),
         "coxa": 1.5, "l1": 9.9, "l2": 7.9, "tarsus": 4.5, "yaw_env": (120.0, 120.0),
         "len3d": {"coxa": 4.3, "femur": 8.31, "tibia": 6.11, "tarsus": 7.68},
         "rest3d": {"yaw_deg": 39.8, "femur_pitch_deg": 45.0, "knee_deg": 140.0},
         "rom3d": {"yaw": (120.0, 120.0), "femur_pitch": (-20.0, 145.0),
                   "knee": (15.0, 175.0), "tita": (-25.0, 60.0)},
         "tarsus_seg": (0.345, 0.236, 0.152, 0.133, 0.133),
         "femur_lead_k": 0.35},
        {"kind": "mid",   "side": 1,  "group": 0, "attach": (3.5, 1.5),   "home": (2, 10),
         "coxa": 1.5, "l1": 11.0, "l2": 9.6, "tarsus": 5.0, "yaw_env": (120.0, 120.0),
         "len3d": {"coxa": 2.13, "femur": 9.24, "tibia": 7.86, "tarsus": 7.91},
         "rest3d": {"yaw_deg": 100.0, "femur_pitch_deg": 50.0, "knee_deg": 135.0},
         "rom3d": {"yaw": (120.0, 120.0), "femur_pitch": (-20.0, 145.0),
                   "knee": (15.0, 175.0), "tita": (-25.0, 60.0)},
         "tarsus_seg": (0.435, 0.238, 0.136, 0.095, 0.095),
         "femur_lead_k": 0.35},
        {"kind": "rear",  "side": 1,  "group": 1, "attach": (-2.0, 5.0),  "home": (-8, 10),
         "coxa": 1.5, "l1": 11.0, "l2": 10.2, "tarsus": 5.5, "yaw_env": (120.0, 120.0),
         "len3d": {"coxa": 2.35, "femur": 9.85, "tibia": 8.06, "tarsus": 9.09},
         "rest3d": {"yaw_deg": 140.2, "femur_pitch_deg": 45.0, "knee_deg": 140.0},
         "rom3d": {"yaw": (120.0, 120.0), "femur_pitch": (-20.0, 145.0),
                   "knee": (15.0, 175.0), "tita": (-25.0, 60.0)},
         "tarsus_seg": (0.458, 0.227, 0.126, 0.095, 0.095),
         "femur_lead_k": 0.35},
    ],
    # 触角:短小具芒,俯视仅伸出 0.08~0.15BL(权威 §1;旧 9px=0.3BL 过长如蟑螂须)
    "antenna_base": (15, 2),
    "antenna_len": 5,
    "stride_len": 11,
    "stride_amp": 5.5,
    "cruise": 90,       # 爬行 ≈3 BL/s(文献典型 7.4mm/s≈3 BL/s)
    "sprint": 240,      # ≈8 BL/s(文献峰值 6~8)
    "fly_speed": 1000,  # 飞行观赏截断(真实 80~120 BL/s,ADR-005 声明)
    "fly_altitude": 55,
    # F4 冻结点:地面转向角速度上限 3.8 rad/s(冻结区间 3.5~4.0;旧 7.0 是
    # 逃逸级数值被巡航复用,是果蝇转圈问题的 body 侧帮凶之一);
    # 逃逸反射转向不受此限(_turn_toward 逃逸档以饱和增益独立调用)。
    "turn_rate": 3.8,
    "accel": 1400,
    "wing_stub": True,
    "wing_span": 20,
    # 逃逸急停僵住:果蝇用短档(0.08~0.2s,果蝇惊觉反应远快于蟑螂,
    # 文献锚点同 cockroach.escape_freeze_s;身体层实现,brain 的 decide 不变)
    "escape_freeze_s": (0.08, 0.20),
}


class FruitFly(SpeciesPlugin):
    manifest = {"id": "species.fruitfly", "name": "黑腹果蝇", "version": "0.1",
                "type": "species", "api": 1,
                "description": "黑腹果蝇 Drosophila melanogaster:风感极敏、趋醋香、saccade 飞行"}

    def create_body(self, state):
        return GenericInsectBody(state, dict(PARAMS))

    def create_brain(self, state):
        return FlyConnectomeBrain(state=state)

    def perception_profile(self) -> dict[str, float]:
        """感知画像(按行为学文献精调):
        - 黑腹果蝇:琼氏器(Johnston's organ)对气流/风速变化极敏感(风感第一通道);
          looming(扩张暗影)触发威胁逃掠;嗅角对醋酸/发酵气味灵敏;
          无尾须,基线震动感受弱;冷回避通路敏感;性信息素(cVA)保留。"""
        return {"wind": 1.0,        # 琼氏器:对气流极敏感
                "vibration": 0.3,   # 无尾须,基线震感弱
                "shadow": 0.9,      # 扩张视觉目标=威胁(looming)
                "odor_food": 0.9,   # 醋香/发酵气味ORN灵敏
                "odor_mate": 0.35,  # 信息素(预留)
                "cold": 0.65,       # 冷回避较强
                # 感知通道行为参数(perception.mouse 使用):
                "wind_gust_gain": 1.6,   # rush/快速冲近时风感增益(风通道差异化)
                "wind_odor_gate": 1.0}   # 风+食物气味同时出现 → meta["coincide"](GF 门控提示)

    def render_traits(self) -> dict:
        # 色板按权威规格 §2([GT2] 高清照片取样,总色调比旧版亮一档金黄橙)。
        return {"body": "#c59a5b", "highlight": "#e0be85", "dark": "#3d2c17",
                "legs": "#9c7443", "legs_swing": "#7a5a33",
                "antenna": "#3a2812", "eyes": "#a8301f",
                "wing_cover": False,
                # ---- W2 躯干精灵(neuropet/render/torso_art.py)专用键:
                #      legacy 渲染路径安全忽略。键名以 torso_art._pal_for 实际
                #      读取为准,色值迁移自权威规格 §2 照片取样色板。
                "torso_thorax": "#c08a42",        # 胸盾底色(亮金黄棕)
                "torso_thorax_stripe": "#7a4e1e", # 5 条细纵纹 / 盾片沟
                "torso_abdomen": "#d9b269",       # 腹部底色
                "torso_abdomen_band": "#2a1c10",  # 腹环带(深棕黑,勿纯黑)
                "torso_head": "#a4793a",          # 头
                "torso_eye": "#a8301f",           # 砖红大复眼(占头宽 ~85%)
                "torso_eye_gl": (212, 96, 63),    # 复眼高光点(#d4603f 预混)
                "torso_wing_rest": (222, 208, 168),  # 静息折翅(乳白玻璃感)
                "torso_wing_vein": (150, 130, 84),   # 折翅缘脉
                "torso_highlight": (255, 250, 220),  # 高光(烘焙为不透明色)
                # ---- 软阴影(权威 §4:2.5mm/1mg 小虫投影几乎不可见)----
                "shadow_alpha": 22,               # 旧 78 → 22(近零)
                "shadow_rx": 0.17, "shadow_ry": 0.09}  # 精灵半轴 ×BL(旧 0.50/0.24)


PLUGIN_CLS = FruitFly
