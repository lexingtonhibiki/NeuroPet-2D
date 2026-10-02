"""美洲大蠊物种包。

感知画像与运动参数均按《docs/references/行为学_美洲大蠊与果蝇.md》精调;
BL/s 语义:1 BL = 115px(观赏体长,ADR-005)。默认大脑:RoachBrain(brain.roach_nn)。
不飞(r23 用户裁决:滑翔姿态去除;fly_speed=0 → body._flyer=False,TAKEOFF
自动回退地面行走,flight 状态机仅果蝇使用)。
"""
from __future__ import annotations

from neuropet.brain.roach_brain import RoachBrain
from neuropet.body.base import GenericInsectBody
from neuropet.core.interfaces import SpeciesPlugin

# 运动参数(BL/s 换算自《桌宠参数速查表》):
# 巡航文献 0.8~7 BL/s,取 ~2.6;冲刺文献 25~50 BL/s,屏幕观赏截断至 1500px/s
# (≈13 BL/s;步频 = speed/stride_len:巡航 7.5Hz 落文献 3~8Hz,见行为学规范文档)
PARAMS = {
    # GaitProfile(F2)查找键:运行时按此在 data/gait/ 查训练产物,
    # 缺失回退默认文件/内置常数(body/base.py 构造时注入)。
    "gait_species": "cockroach",
    "body_len": 115,
    "window_half": 120,
    "segments": [  # (x, y, rx, ry) 身体局部坐标,x 朝前
        (44, 0, 15, 14),     # 头
        (14, 0, 24, 25),     # 胸(前翅根)
        (-30, 0, 33, 21),    # 腹
    ],
    "legs": [
        # =========================================================================
        # v3 真 3D 腿链(2026-09-16,规格 docs/references/三维腿链实施规格.md;
        # 数值权威 docs/references/腿部三维运动学权威_蟑螂果蝇.md〔R1〕§7;
        # 每腿新增 len3d/rest3d/rom3d/tarsus_seg/femur_lead_k 五键,旧键全保留)。
        # R1 §7.2 节段长(BL×115px;femur3d 含转节:股+转,BL×115):
        #   前 coxa 0.085→9.8 / femur (0.170+0.030)→23.0 / tibia 0.137→15.8 /
        #     tarsus 0.077→8.9;中 0.095→10.9 / (0.219+0.035)→29.2 /
        #     0.204→23.5 / 0.095→10.9;后 0.105→12.1 / (0.285+0.040)→37.4 /
        #     0.191→22.0 / 0.113→13.0(reach3d = f3d+t3d = 38.8/52.7/59.4)。
        # rest3d(R1 §3.2/§7.3 静息角):yaw 足向 +54/+118/+159°(R1 照片中值,
        #   现行 home 几何的实效 yaw0 见 tests/test_body.py ⑧ 带);股节俯角
        #   35/30/28°(下垂正);FTi 3D 内角 157/148/156° —— **内角口径
        #   (180°=伸直),与 TIBIA_RANGE 同口径;铰链转角口径 = 180−内角
        #   (23/32/24°),勿混**(两处裁决 #2)。
        # rom3d(R1 §7.4 换算):yaw = COXA_YAW_ENV(与 v1 阀一致);femur_pitch
        #   = FEMUR_RANGE;knee:R1 FTi ROM 0~125°(铰链)→ 内角 [55,180],
        #   上限按 D2 §2.2 收 5° 边距 → 175;**下限放宽到 40**(=铰链 140°,
        #   越出 R1 带)——因现行 home 静息距下前足踝距 ≈7.7px,若取 R1 下限
        #   60° 会令静息/支撑相触发环带下钳 → 钉足视觉滑动;L2 校准随 home
        #   重定后收回 R1 带(已知残留)。
        # tarsus_seg(R1 §7.2 跗分节拆分 ta1..ta5)+ femur_lead_k(规格 §2.4,
        #   R1 §7.3 方位超前 45/105/150 先于足向 54/118/159 ≈0.35×β)。
        # 注意:下列 v2 键(trochanter/claw/diam/tarsomeres/base_k/base_off/
        # bend_w)自 v3 起休眠(solve() 缺省走 8 点 3D 链;NEUROPET_LEG3D=0
        # 回退 v1 平面链),仅为骨架重构波回溯保留;gait 阀口径 reach 在 3D
        # 模式恒为 l1+l2(kinematics.reach,冻结口径,见 kinematics 模块头)。
        # =========================================================================
        # ---- 旧键(骨架重构波注释,原样保留)----
        # 节段长(BL×115px,规格 §6.2 设计值经 §8.4 标定冻结):
        #   前 股13.5/胫26.5/跗9.5+爪1.5;中 股20.5/胫23.5/跗14.8+爪2.4;
        #   后 股23.0/胫47.0/跗29.6+爪2.4(股/胫长于 §6.2 设计值:加长全链
        #   reach 使 gait 过拉伸阀/静息钳制留出 ±5% 制造噪声余量,静息
        #   足距不变——更深的静息折叠,照片读感不变)。
        # pose_n=(β_n,γ_n) 定 v1 髋高 H;base_k/base_off(ADR-0028)与 bend_w
        # 为 v2 有效髋投影/膝弓向标定(v2 休眠后仅回溯)。
        # diam(BL×115,规格 §7.2;股:基跗 ≥2.8:1 硬指标 → 前股节 0.026BL):
        #   缘板宽/股/胫/基跗/端跗 = 前(4.6,3.0,1.6,0.98,0.8) 中(5.2,3.0,1.73,1.04,0.8)
        #   后(6.33,3.45,1.84,1.15,0.92)。
        {"kind": "front", "side": -1, "group": 0, "attach": (38, -15), "home": (46.74, -34.65),
         "coxa": 5.2, "trochanter": 2.3, "l1": 13.5, "l2": 26.5, "tarsus": 9.5, "claw": 1.5,
         "pose_n": (33.0, 86.0), "tarsus_ext": 1.0,
         "base_k": 0.0, "base_off": 0.0, "bend_w": 0.05,
         "diam": (4.6, 3.0, 1.6, 0.98, 0.8), "tarsomeres": (0.30, 0.22, 0.18, 0.16, 0.14),
         "len3d": {"coxa": 9.8, "femur": 23.0, "tibia": 15.8, "tarsus": 8.9},
         "rest3d": {"yaw_deg": 54.0, "femur_pitch_deg": 35.0, "knee_deg": 157.0},
         "rom3d": {"yaw": (60.0, 60.0), "femur_pitch": (-10.0, 70.0),
                   "knee": (40.0, 175.0), "tita": (-25.0, 60.0)},
         "tarsus_seg": (0.29, 0.23, 0.18, 0.15, 0.15),
         "femur_lead_k": 0.35},
        {"kind": "mid",   "side": -1, "group": 1, "attach": (28, -18), "home": (-3.32, -49.75),
         "coxa": 5.75, "trochanter": 2.88, "l1": 20.5, "l2": 23.5, "tarsus": 14.8, "claw": 2.4,
         "pose_n": (19.0, 111.0), "tarsus_ext": 1.0,
         "base_k": 0.48, "base_off": 170.0, "bend_w": 0.15,
         "diam": (5.2, 3.0, 1.73, 1.04, 0.8), "tarsomeres": (0.30, 0.22, 0.18, 0.16, 0.14),
         "len3d": {"coxa": 10.9, "femur": 29.2, "tibia": 23.5, "tarsus": 10.9},
         "rest3d": {"yaw_deg": 118.0, "femur_pitch_deg": 30.0, "knee_deg": 148.0},
         "rom3d": {"yaw": (52.0, 52.0), "femur_pitch": (-10.0, 70.0),
                   "knee": (40.0, 175.0), "tita": (-25.0, 60.0)},
         "tarsus_seg": (0.29, 0.23, 0.18, 0.15, 0.15),
         "femur_lead_k": 0.35},
        {"kind": "rear",  "side": -1, "group": 0, "attach": (2, -16), "home": (-70.87, -28.85),
         "coxa": 6.9, "trochanter": 3.5, "l1": 23.0, "l2": 47.0, "tarsus": 29.6, "claw": 2.4,
         "pose_n": (17.0, 139.0), "tarsus_ext": 1.0,
         "base_k": 0.54, "base_off": 127.0, "bend_w": 1.10,
         "diam": (6.33, 3.45, 1.84, 1.15, 0.92), "tarsomeres": (0.30, 0.22, 0.18, 0.16, 0.14),
         "len3d": {"coxa": 12.1, "femur": 37.4, "tibia": 22.0, "tarsus": 13.0},
         "rest3d": {"yaw_deg": 159.0, "femur_pitch_deg": 28.0, "knee_deg": 156.0},
         "rom3d": {"yaw": (66.0, 48.0), "femur_pitch": (-10.0, 70.0),
                   "knee": (40.0, 175.0), "tita": (-25.0, 60.0)},
         "tarsus_seg": (0.29, 0.23, 0.18, 0.15, 0.15),
         "femur_lead_k": 0.35},
        {"kind": "front", "side": 1,  "group": 1, "attach": (38, 15),  "home": (46.74, 34.65),
         "coxa": 5.2, "trochanter": 2.3, "l1": 13.5, "l2": 26.5, "tarsus": 9.5, "claw": 1.5,
         "pose_n": (33.0, 86.0), "tarsus_ext": 1.0,
         "base_k": 0.0, "base_off": 0.0, "bend_w": 0.05,
         "diam": (4.6, 3.0, 1.6, 0.98, 0.8), "tarsomeres": (0.30, 0.22, 0.18, 0.16, 0.14),
         "len3d": {"coxa": 9.8, "femur": 23.0, "tibia": 15.8, "tarsus": 8.9},
         "rest3d": {"yaw_deg": 54.0, "femur_pitch_deg": 35.0, "knee_deg": 157.0},
         "rom3d": {"yaw": (60.0, 60.0), "femur_pitch": (-10.0, 70.0),
                   "knee": (40.0, 175.0), "tita": (-25.0, 60.0)},
         "tarsus_seg": (0.29, 0.23, 0.18, 0.15, 0.15),
         "femur_lead_k": 0.35},
        {"kind": "mid",   "side": 1,  "group": 0, "attach": (28, 18),  "home": (-3.32, 49.75),
         "coxa": 5.75, "trochanter": 2.88, "l1": 20.5, "l2": 23.5, "tarsus": 14.8, "claw": 2.4,
         "pose_n": (19.0, 111.0), "tarsus_ext": 1.0,
         "base_k": 0.48, "base_off": 170.0, "bend_w": 0.15,
         "diam": (5.2, 3.0, 1.73, 1.04, 0.8), "tarsomeres": (0.30, 0.22, 0.18, 0.16, 0.14),
         "len3d": {"coxa": 10.9, "femur": 29.2, "tibia": 23.5, "tarsus": 10.9},
         "rest3d": {"yaw_deg": 118.0, "femur_pitch_deg": 30.0, "knee_deg": 148.0},
         "rom3d": {"yaw": (52.0, 52.0), "femur_pitch": (-10.0, 70.0),
                   "knee": (40.0, 175.0), "tita": (-25.0, 60.0)},
         "tarsus_seg": (0.29, 0.23, 0.18, 0.15, 0.15),
         "femur_lead_k": 0.35},
        {"kind": "rear",  "side": 1,  "group": 1, "attach": (2, 16),   "home": (-70.87, 28.85),
         "coxa": 6.9, "trochanter": 3.5, "l1": 23.0, "l2": 47.0, "tarsus": 29.6, "claw": 2.4,
         "pose_n": (17.0, 139.0), "tarsus_ext": 1.0,
         "base_k": 0.54, "base_off": 127.0, "bend_w": 1.10,
         "diam": (6.33, 3.45, 1.84, 1.15, 0.92), "tarsomeres": (0.30, 0.22, 0.18, 0.16, 0.14),
         "len3d": {"coxa": 12.1, "femur": 37.4, "tibia": 22.0, "tarsus": 13.0},
         "rest3d": {"yaw_deg": 159.0, "femur_pitch_deg": 28.0, "knee_deg": 156.0},
         "rom3d": {"yaw": (66.0, 48.0), "femur_pitch": (-10.0, 70.0),
                   "knee": (40.0, 175.0), "tita": (-25.0, 60.0)},
         "tarsus_seg": (0.29, 0.23, 0.18, 0.15, 0.15),
         "femur_lead_k": 0.35},
    ],
    # 触须:着生点=头部前缘两侧、复眼之间(权威 §1:(48,±5)px;base.py 乘
    # side*0.4,故此处给 12 → 实际 ±4.8px);长 ≥1.2×BL(NC-State:触角长于
    # 身体)= 140px;9 节鞭状(旧 3 节=折线硬杆)。
    "antenna_base": (48, 12),
    "antenna_len": 140,
    "antenna_segments": 9,
    "stride_len": 40,
    "stride_amp": 17,
    # 摆动相抬腿幅度(px;A11:lift 0.04~0.07BL = 4.6~8.05px → 取 0.043BL;
    # 旧缺省 0.16×stride_amp=2.7px 过小,抬腿不可读)
    "stride_lift": 5.0,
    "cruise": 300,     # ≈2.6 BL/s(文献 0.8~7)
    "sprint": 1500,    # 观赏截断(文献 25~50 BL/s = 2875~5750px/s,见行为学规范)
    # r23:滑翔整体去除(用户裁决)。fly_speed=0 → _flyer=False → 不起飞;
    # 逃逸走地面冲刺/僵住链路。飞行状态机(body/flight.py)仅果蝇使用。
    "fly_speed": 0,
    "fly_altitude": 50,
    "wing_span": 70,
    "turn_rate": 3.6,
    "accel": 900,
    # 逃逸急停僵住(s 随机档):蟑螂受惊先"惊觉僵住"再爆发(文献锚点,
    # deskbug brain.py:275 同款 0.15~0.55s;身体层实现,brain 的 decide 不变)
    "escape_freeze_s": (0.15, 0.55),
}


class AmericanCockroach(SpeciesPlugin):
    manifest = {"id": "species.cockroach", "name": "美洲大蠊", "version": "0.1",
                "type": "species", "api": 1,
                "description": "美洲大蠊 Periplaneta americana:尾须震动感知、嗅觉灵敏、负趋光、趋缝性"}

    def create_body(self, state):
        return GenericInsectBody(state, dict(PARAMS))

    def create_brain(self, state):
        return RoachBrain(state=state)

    def perception_profile(self) -> dict[str, float]:
        """感知画像(按行为学文献精调):
        - 美洲大蠊:尾须(cerci)丝状感受器与腿部震感对近距震动/触碰极敏感,
          触角嗅觉灵敏;对远距气流的"风感"弱(文献中
          的风敏感主要来自后方急促气流体表吹拂,建模归入 vibration 通道);
          夜行性,视觉阴影反应中等;信息素感知保留。"""
        return {"wind": 0.2,        # 远距风感弱(尾须主要响应近距气流/震动)
                "vibration": 0.95,  # 腿/尾须震动极敏感:点按、拖拽、贴身主导通道
                "shadow": 0.5,      # 夜行性,looming 反应中等
                "odor_food": 0.95,  # 触角嗅觉灵敏
                "odor_mate": 0.6,   # 信息素(预留)
                "cold": 0.45,
                # 感知通道行为参数(perception.mouse 使用):
                "wind_gust_gain": 0.6,   # 冲刺风对其影响弱
                "wind_odor_gate": 0.0}   # 不做"风+气味"coincide 门控提示

    def render_traits(self) -> dict:
        # 色板全部按权威规格 §2(照片取样 hex,预混不透明烘焙)。
        # legacy 键(body/legs/antenna 等)与新 torso_* 键并存,双管线各取所需。
        return {"body": "#5a3418", "highlight": "#7a4c24", "dark": "#33200e",
                "legs": "#7a3814", "legs_swing": "#6b4a2a",
                "antenna": "#6b4a3a", "eyes": "#120c06",
                "wing_cover": True, "wing_cover_color": "#6b431f",
                # ---- W2 躯干精灵(neuropet/render/torso_art.py)专用键:
                #      legacy 渲染路径安全忽略。键名以 torso_art._pal_for 实际
                #      读取为准;色值迁移自权威规格 §2 照片取样色板。
                "torso_body": "#6e3413",          # 覆翅渐变中点(革质红棕)
                "torso_body_edge": "#3f1d08",     # 覆翅外描边(1px 暗色收边)
                "torso_wing_seam": "#47220b",     # 纵翅中缝 / 尾端缺刻
                "torso_wing_vein": "#331512",     # 纵脉(比底色暗一档半)
                "torso_teg_head": "#7a2f1d",      # 覆翅头端(肩区红棕,渐变起点)
                "torso_teg_tail": "#a06a3a",      # 覆翅尾端(金黄琥珀,渐变终点)
                "torso_teg_tip": "#d79e57",       # 覆翅末端高光区
                "torso_teg_margin": "#b5764a",    # 覆翅侧缘浅条带
                "torso_ab_band": "#3f2823",       # 外露腹末暗横带
                "torso_ab_light": "#6c4131",      # 外露腹末节间亮带
                "torso_pronotum": "#3e1b17",      # 前胸背板中央(暗桃花心木)
                "torso_pronotum_rim": "#ad5214",  # 背板周缘琥珀窄环(1.5~2px)
                "torso_pronotum_edge": "#2a120c", # 背板深描边
                "torso_pronotum_mark": "#2a120c", # 前缘一对小暗肾斑
                "torso_head": "#4a2018",          # 头(背板前缘露一线)
                "torso_leg_core": "#933c10",      # 尾须基节用亮棕(与腿色同源)
                # 油光高光(烘焙为不透明色)
                "torso_highlight": (255, 240, 210),
                # ---- 软阴影(权威 §4:俯视投影=紧贴足迹的低对比淡斑)----
                "shadow_alpha": 52,               # 中心墨量(旧 96 过深)
                "shadow_rx": 0.40, "shadow_ry": 0.21}   # 精灵半轴 ×BL(足印内)


PLUGIN_CLS = AmericanCockroach
