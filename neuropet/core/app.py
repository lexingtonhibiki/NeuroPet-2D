"""应用编排器:单主循环(模拟+渲染+UI,绝对节拍)、宠物生命周期、
投喂/冻结/拖拽、面板。渲染走单一共享全屏舞台(见 windowing.py 与原型报告)。

线程模型:主线程 = tkinter + 模拟 + 渲染;投喂模式的低级鼠标钩子(core.hook)
运行独立线程,经 bus.publish_threaded 回主线程。
"""
from __future__ import annotations

import ctypes
import gc
import json
import math
import os
import sys
import threading
import time
import tkinter as tk
import uuid
from collections import deque

from .bus import EventBus
from .config import (DATA_DIR, PLUGINS_DIR, PETS_PATH, AppConfig, load_config,
                     load_roster, profile_dir, save_config, save_roster,
                     snap_crawl_speed, CRAWL_SPEED_CHOICES)
from .desktop import DEFAULT_MARGIN
from .contracts import (Behavior, BehaviorCommand, MovementMode,
                        PetState, StimulusKind)
from .i18n import set_lang, t
from .interfaces import IBody, IBrain, SpeciesPlugin
from .plugin import PluginRegistry
from .windowing import OverlayStage, display_signature, set_dpi_aware, working_set_mb
from .autostart import enabled as autostart_enabled
from .autostart import set_startup as write_autostart
from ..perception.screen import ScreenMonitor
from .world import WorldModel
from ..brain.mastery import (learned_from_summary, mastery_enabled,
                             mastery_level, readout)
from . import instr as _I          # r25 U0:插桩(默认关;NEUROPET_INSTR=1 开)
                                   # 只观测不改行为,关闭时入口函数首行短路

FRAME_DT = 1.0 / 60.0

# ================= v0.2.0:全局爬行速度 + 可用桌面 + 拖尾 =================
# 速度倍率只作用在**运动参数**上(cruise/sprint/fly_speed/accel/escape 上限),
# 绝不同乘全局 dt —— 否则饥饿、记忆、食物与飞行节奏会被一起改坏。
CRAWL_SPEED_ENV = "NEUROPET_CRAWL_SPEED"   # 启动档位覆盖(验收/调试用)
TRAIL_KEY = "trail"                        # 拖尾开关事件主题

# r25 A4:熟练度重算节拍(与面板 2Hz 同拍)。等级只从经历统计里长出来,用户
# 不再手拧;**自动派生只写 brain(内存),不写 cfg、不落盘,且只上调不下调
# (ratchet)**(见 _mastery_beat;手动入口 app.set_intelligence 仍照旧写
# cfg + save_config,并保留上下调权限)。NEUROPET_MASTERY=off = **停止自适应**
# (脑保持当前等级,节拍零写入;cfg.intelligence 只作新宠的种子)—— 任何自动
# 路径都不下调等级、不触发记忆删除,是"冻住"等级的唯一入口。
# 读数( app.mastery_of )一律 = **脑内实际等级**(单一真源)。
MASTERY_BEAT_S = 2.0

PANEL_RECT_PAD = 4          # 面板点击穿透矩形的外扩余量(物理像素)
PANEL_RECT_REFRESH_S = 0.5  # 面板矩形缓存的主线程刷新周期(秒)
_BUBBLE_TICK_S = 0.1        # C2 气泡层每帧 update 的节流周期(10Hz)

# ================= §6:隐藏宠物并保留记忆 + 召回 =================
# 隐藏会话文件:记录当前隐藏宠的 pet_id/物种清单(save_all / hide / recall
# 时重写)。重启后 run() → _restore_hidden 按原 pet_id 重建并立即隐藏,
# 记忆/体型档经 profile 载入恢复 —— 隐藏态跨重启还原。
_SESSION_FILE = DATA_DIR / "session.json"

# ================= r16 Task E:启动名册读配置(data/pets.json) =================
# 用户报障:旧 run() 硬编码"每个注册物种各 add 一只"——每次启动都凭空多出
# 两只。改为读 data/pets.json 逐条 add_pet(文件缺失自动生成缺省,与旧硬编码
# 等价,老用户无感);未知物种跳过并警告,不崩;仍受 cfg.max_pets 上限。
# 面板/托盘的增删/隐藏/召回全走 App 方法,在方法内回写可见名册(隐藏宠不进
# 名册——它有 session.json 独立机制)。回写仅在会话运行后生效(_roster_live,
# run() 置位):测试/离线脚本直接构造 App 不落盘,预防 session.json 式幻影
# 改动污染仓库真实 data/。
_PETS_FILE = PETS_PATH

# ================= AG5:尺寸缩放(用户需求五档,决策记录 §4.3 吸收项) =================
# 一根标量 k 贯通(提案 A §5.1):骨骼/画布/阴影等全部 px 量 ×k;角度、步频、
# duty、时间常数不变。BL 单位制下速度语义:v[px/s]×k 且 body_len×k → v[BL/s]
# 恒定 —— 行为(步频-速度映射、感知、脑决策)不因体型放大/缩小而变速变调。
SCALE_CHOICES = (0.5, 0.75, 1.0, 1.5, 2.0)
_SCALE_ENV = "NEUROPET_SCALE"   # 启动档位覆盖(验收/测试用;缺省走 profile/1.0)

# ================= AG5:内存护栏(预算 250MB(ADR-0036:拟真优先;3d 管线 GL 驱动驻留地板 ~190MB,用户裁决 130→250)) =================
# 实测分解(69.5MB 基线,scratch/mem_breakdown.py):Python/PIL/tk 基线 ~21 +
# 躯干 1x 旋转桶 6.1(60 桶×102KB)+ master ~1.0 + 阴影精灵 3.8(22 桶)+
# 阴影补丁 ~25-37(56 上限 ×0.65MB,评论"20KB/片"已失真,为最大黑洞)+
# PhotoImage ~0.5。护栏三杠杆(提案 A §5.2/§6 L1/L2 双桶 + 降档):
GUARD_ANGLE_STEP = 2        # 降档量化 1°→2°(仅 trim/purge 态;见 _apply_memory_guard)
GUARD_ROT_BUDGET = {"roach": int(6.0 * 1024 * 1024),   # 降档预算(压力态)
                    "fly": 512 * 1024}
# 基线预算(r24 转向连续性):ok 态 1° 量化 + 整圈驻留。旧基线 2°/3MB 只盖住
# ±14° 跟随窗,持续/快速转向即 LRU 重建风暴——活体实测快转窗桶缺失 61-67%、
# 被迫渲染 14-19ms(p50),_tick 被拖到 30-50ms,全 app 显示节奏塌到 ~20fps
# (scratch/_r24_turn)。整圈 closed=180 张精灵 ≈10.9MB;16MB 再留 spread 工作集。
# 2D 工作集实测 ~66MB,距 215MB trim 阈极远;压力态仍回落 GUARD_* 降档。
ROT_BASE_BUDGET = {"roach": int(16.0 * 1024 * 1024),
                   "fly": 1024 * 1024}
# r24 转向连续性:角速度入帧档。原地转向 speed≈0 旧口径掉 10fps 档 → 49°/s
# 的转向每显示帧跳 ~5°,肉眼即"瞬变"。>35°/s(驻留 >20°/s)保 30fps 档。
TURN_TIER_ENTER_DEG_S = 35.0
TURN_TIER_RELEASE_DEG_S = 20.0
L2_BUDGET_BASE = {"roach": int(4.0 * 1024 * 1024),     # k<1.5:小档精灵很小
                  "fly": 1024 * 1024}
L2_BUDGET_HIGH = {"roach": int(10.0 * 1024 * 1024),    # k≥1.5:2x 精灵 0.92MB/桶
                  "fly": int(2.5 * 1024 * 1024)}
# 阴影缓存上限(L1/L2 双桶口径的降档;渲染器内部硬上限 22/56 不动,app 周期性
# 裁剪到目标值 —— 裁掉的桶由跟随式预热线程按转向方向补回):
SHADOW_CAP_BASE = (22, 24)     # (精灵桶, 补丁桶) k<1.5:蟑螂贴地行走 24 桶 ≈15MB
SHADOW_CAP_HIGH = (12, 8)      # k≥1.5:精灵/补丁尺寸 ×k²,上限收紧
MEM_SAMPLE_S = 5.0             # 工作集采样周期(提案 A §6:5s)
# PF 快赢③(回差,调研 §E3/H4):52/58 贴 60MB 目标仅 2MB 余量 → 50/55;
# 并加双阈值回差 + 动作最短间隔,消除阈值附近"裁剪→预热情回灌→再裁剪"乒乓:
#   ok→trim @>50(立即);trim→purge @>55(立即升级);trim→ok @<45(回差 5MB);
#   purge→trim @<50;trim/purge 的重复动作须距上次动作 ≥MEM_DWELL_S。
MEM_TRIM_MB = 215.0            # >215MB → 裁剪各缓存一半(降级阶梯 L4;ADR-0036 预算 250MB)
MEM_PURGE_MB = 230.0           # >230MB → 缓存清空重建(降级阶梯 L5)
MEM_RELEASE_MB = 200.0         # 回差解除线:低于此值才回到 ok 态
MEM_DWELL_S = 30.0             # 同向降级动作的最短间隔(防乒乓安全阀)

# PF 快赢③:帧率档位双阈值回差(调研 §E3:升 40/降 30 + 档位最短驻留 500ms,
# 消除 speed 在 40px/s 边界抖动造成的 60/30 档乒乓 —— 帧耗突变反过来污染 P95)。
# 30/10 档边界同理(speed>1 进 30 档 / >0.5 才留在 30 档)。
FRAME_FAST_ENTER = 40.0        # 进入 60fps 档的速度阈值(px/s)
FRAME_FAST_RELEASE = 30.0      # 留在 60fps 档的最低速度(以下降档,回差 10)
FRAME_MID_ENTER = 1.0          # 进入 30fps 档的速度阈值(px/s)
FRAME_MID_RELEASE = 0.5        # 留在 30fps 档的最低速度(以下降 10fps 档)
TIER_DWELL_S = 0.5             # 档位最短驻留(秒);抓握/飞行入 60fps 档不受限

# PF 快赢②:gc 调优(调研 §E4/H3)。预热完成后 gc.freeze() 把常驻对象
# (模块/类/物种 PARAMS/连接组/姿态库/tk 内部)移出代际扫描,消除 gen2 全回收
# 扫遍全部常驻对象的 5~15ms 偶发 P99 尖刺;配合阈值放宽降低代际回收频率:
# gen0 700→1000(单次暂停略增、频次降 ~30%),gen1/gen2 10→15(gen2 全回收
# 频次约降一半)。冻结后的新增对象(缓存桶等)仍在正常代,行为不变。
GC_FREEZE_AFTER_S = 5.0        # run() 起算:预热+首轮渲染基本完成后再冻结
GC_THRESHOLDS = (1000, 15, 15)

# 渲染配额(前瞻):>RENDER_QUOTA 只宠物时每帧最多渲染该数量,其余跳帧轮转
RENDER_QUOTA = 2


def snap_scale(k: float) -> float:
    """任意 k 吸附到最近的标准档(五档外的值防御性归档)。"""
    return min(SCALE_CHOICES, key=lambda c: abs(c - float(k)))


def emo_channels(em) -> tuple[float, float, float] | None:
    """B2 装配守卫:从 `brain.emotion()` 取 (fear, hunger, arousal)。

    逐项要求**有限实数且 ∈[0,1]**(bool/str 视为非数,与 mastery `_num` 拒绝
    str 的"脏值 = 上游 bug 信号"契约一致,架构 §20);任何一项脏(None/缺失/
    非数/NaN/±inf/越界)→ **整体返回 None = 本轮无情绪数据**,由装配点跳过
    本轮装配(不抛、不中断帧)。插件脑不保证 emotion() 契约,而 `_tick` 的
    `except Exception` 只会整帧中断(c9d7da1 同类事故)——守卫必须在这里。
    """
    if em is None:
        return None
    out = []
    for name in ("fear", "hunger", "arousal"):
        v = getattr(em, name, None)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        v = float(v)
        if not (0.0 <= v <= 1.0):      # NaN/±inf/越界 一并落网(NaN 比较恒 False)
            return None
        out.append(v)
    return (out[0], out[1], out[2])


# B2:情绪读数的**有界保持窗**(s)。0.25s = 15 帧 @60Hz(主循环 FRAME_DT):
# ≥ 多帧 ⇒ 一次瞬时脏读数不会让通道抖动;≪ 人眼可感的"卡住"时长 ⇒ 也不掩护
# 上游持续违约。**超过它必须回到中性**:守卫"跳过装配"若不写状态,通道会
# 无界陈旧(`set_emotion_gait` 是这几个字段的唯一写者)——复验 r2 问题 1:
# `_emo_fear` 停在斜坡值、停顿窗按 ~55% 占空无限期开,且此时
# `NEUROPET_EMO_GAIT=off` 也不生效(装配被跳过 ⇒ off 根本没被调用)。
EMO_HOLD_MAX_S = 0.25
# 情绪读数不健康的限频诊断(行/s 上限 = 1/该值):不刷屏,也不静默藏上游 bug。
EMO_DIAG_MIN_GAP_S = 10.0

_EMO_KEYS = ("fear", "hunger", "curiosity", "anger", "trust", "valence",
             "arousal")   # 与 `EmotionState.as_dict` / 面板 EMOTIONS 同键同序
_EMO_NEUTRAL = {k: 0.0 for k in _EMO_KEYS}


def emo_read(brain) -> tuple:
    """B2 情绪读取守卫(**`emotion()` 的调用面**,唯一入口)→ (em, channels)
    或 (None, None) = 本轮无健康读数。

    覆盖三类:① `emotion()` **本身抛异常**(复验 r2 问题 3);② 返回 None;
    ③ 返回值脏(缺失/非数/bool/NaN/±inf/越界,由 `emo_channels` 判)。
    **异常不逃逸到帧循环**——`_tick` 的 except 只会整帧中断(c9d7da1 同类
    事故)。守卫长在**调用面**而不是某一个调用点:装配点、面板读数
    (`pet_status`)以及将来任何新读法都默认受保护(复验 r2 问题 2:面板
    6Hz 的 `pet_status → emotion()` 曾是守卫外的缺口)。
    """
    try:
        em = brain.emotion()
    except Exception:
        return None, None
    ch = emo_channels(em)
    if ch is None:
        return None, None
    return em, ch


def emo_dict(em) -> dict:
    """面板读数用的**安全字典**(与 `EmotionState.as_dict` 同键、同 round(3))。

    逐项只接受有限实数(bool/非数/NaN/±inf/缺失 → 0.0,与"脏值→0"契约一致):
    插件脑的 `as_dict()` 同样可能抛或吐非数,而它跑在 6Hz 面板通路上——每
    0.17s 中断一帧。键序 = `EmotionState.as_dict` = 面板 `EMOTIONS`。
    """
    out = {}
    for k in _EMO_KEYS:
        v = getattr(em, k, None)
        if isinstance(v, bool) or not isinstance(v, (int, float)) \
                or not math.isfinite(v):
            out[k] = 0.0
        else:
            out[k] = round(float(v), 3)
    return out


def scale_params(params: dict, k: float) -> dict:
    """物种 PARAMS 的 k 等比缩放副本(AG5 缩放管线唯一入口的参数半场)。

    规则(提案 A §5.1,BL 单位制):
    - 长度 ×k:body_len/window_half/segments/legs(attach/home/coxa/l1/l2/
      tarsus)/antenna_*/stride_*/wing_span/fly_altitude —— 几何与画布全随 k;
    - 速度 ×k:cruise/sprint/fly_speed/accel(px/s、px/s²)。**保持体长速度
      不变的证明**:v[BL/s] = v[px/s]/body_len[px];两端 ×k 后比值不变,故
      步频映射 step_hz(v)(gait.py 全按 v/body_len 比值式)与 duty 不变,
      行为节奏与体型无关(决策记录 F1/D2 §3.5:θ 与 s 无关,零重训);
    - 不变:turn_rate(rad/s)、escape_freeze_s(s)、gait_species、group/
      side/kind/yaw_env(角度/比例)、antenna_segments(节数);
    - escape_sprint_cap:注入 ×k 值(base.py 默认常量是 1x 观赏截断,缩放后
      保持同一 BL/s 截断语义)。
    与 SkeletonSpec.scaled(k)(rig 侧唯一入口)的等价性由 tests/test_scaling.py
    断言:from_params(species, scale_params(P,k)) ≡ from_params(species,P).scaled(k)。
    """
    k = float(snap_scale(k))

    def s_len(v: float) -> float:
        return round(float(v) * k, 4)

    p = json.loads(json.dumps(params))       # 深拷贝,绝不污染物种单例 PARAMS
    for key in ("body_len", "antenna_len", "stride_len", "stride_amp",
                "stride_lift", "wing_span", "fly_altitude",
                "cruise", "sprint", "fly_speed", "accel"):
        if key in p:
            p[key] = s_len(p[key])
    p["window_half"] = max(4, int(round(float(p.get("window_half", 120)) * k)))
    p["segments"] = [[s_len(x), s_len(y), s_len(rx), s_len(ry)]
                     for (x, y, rx, ry) in p["segments"]]
    for leg in p["legs"]:
        leg["attach"] = (s_len(leg["attach"][0]), s_len(leg["attach"][1]))
        leg["home"] = (s_len(leg["home"][0]), s_len(leg["home"][1]))
        for key in ("coxa", "l1", "l2", "tarsus"):
            if key in leg:
                leg[key] = s_len(leg[key])
        # v2 腿扩展键(ADR-0026)同为长度量纲:参数层单点缩放——kin.reach 等
        # 派生长度参与 trochanter/claw(如 tarsus+claw),必须让 kinematics 读到
        # 已缩放值;len3d 系仍由消费侧 _scale_k 注入缩放,勿在此重复(见
        # body/kinematics.py 模块头"缩放"节)。
        if "diam" in leg:
            leg["diam"] = [s_len(v) for v in leg["diam"]]
        for key in ("trochanter", "claw"):
            if key in leg:
                leg[key] = s_len(leg[key])
    # 翅铰/翅端/尾须:rig.from_params 原先把它们硬编码为 1x 常量,导致 k≠1 时
    # 只有腿与体节变大、翅铰与尾须仍钉在 1x 位置(与 scaled(k) 不等价)。
    # 这里按 D1 冻结缺省值注入后再 ×k,与 rig 侧缺省保持同值(k=1 行为不变)。
    # r24:缺省判据与 rig.from_params 对齐为**物种键**(gait_species)——r23
    # 剔除蟑螂 glide 参数后,旧式 `p.get("glide")` 判据恒 False,给蟑螂注入
    # tip_x=-25/无尾须,与 rig 物种缺省(-56/cerci 必有)残差 31px(test_scaling 红)。
    species = str(p.get("gait_species", ""))
    if float(p.get("wing_span", 0)) > 0:
        p.setdefault("wing_hinge_x", 14.0)
        p.setdefault("wing_tip_x", -56.0 if species == "cockroach" else -25.0)
        p["wing_hinge_x"] = s_len(p["wing_hinge_x"])
        p["wing_tip_x"] = s_len(p["wing_tip_x"])
    if (species == "cockroach" or p.get("glide")) \
            and "cerci_anchor" not in p:
        p["cerci_anchor"] = [-49.0, 4.4]      # 权威 §1:蟑螂尾须基点
        p["cerci_len"] = 10.4
    if "cerci_anchor" in p:
        p["cerci_anchor"] = (s_len(p["cerci_anchor"][0]), s_len(p["cerci_anchor"][1]))
        p["cerci_len"] = s_len(p["cerci_len"])
    if "antenna_base" in p:
        p["antenna_base"] = (s_len(p["antenna_base"][0]), s_len(p["antenna_base"][1]))
    if float(p.get("fly_speed", 0)) > 0:
        # 观赏截断随 k:1500px/s@1x 是 13BL/s 的屏幕折算,放大后保持同 BL/s 语义
        from neuropet.body.base import ESCAPE_SPRINT_CAP
        p.setdefault("escape_sprint_cap", ESCAPE_SPRINT_CAP)
        p["escape_sprint_cap"] = s_len(p["escape_sprint_cap"])
    p["scale"] = k                            # 档位标记(诊断/测试读)
    return p


def quota_rotation(n: int, cursor: int, quota: int) -> list[int]:
    """渲染配额轮转的纯函数:>quota 只宠物时返回本帧的候选下标序列。

    从 cursor 起环形给出全部 n 个下标;调用方按序取前 quota 个「到期」宠物,
    其余跳帧。n≤quota 时永不截流(默认双宠用不到,前瞻 >2 只场景)。
    轮转保证任意宠物至多连续饿 quota-1 帧后必然获得配额(不.starve)。"""
    if n <= 0:
        return []
    cursor %= n
    return [(cursor + i) % n for i in range(n)]


def feeding_expired(feeding: bool, until: float | None, now: float) -> bool:
    """投喂模式是否已超时(模块级纯函数,便于离线测试)。

    - feeding=False 或 until=None(不限时)→ 永不超时;
    - feeding=True 且 now >= until → 已到期(含恰好到期的瞬间)。
    """
    if not feeding or until is None:
        return False
    return now >= until


# ================= PF 快赢③:档位回差(模块级纯函数,便于离线测试) =================
def frame_tier_next(cur: int, speed: float, flying: bool, held: bool,
                    anim: bool, turn_deg_s: float = 0.0) -> int:
    """自适应帧率档位(0=60fps / 1=30fps / 2=10fps),双阈值回差。

    进入用高阈值、驻留用低阈值:speed 在边界抖动时档位不乒乓。
    - →0 档:速度 >40 进档;已在 0 档则 >30 才降出(回差 10px/s);
    - →1 档:速度 >1.0 进档;已在 1 档则 >0.5 才降出;
    - flying/held/anim(梳理/进食)是类别条件,无噪声边界,直通。
    - r24:主动转向(角速度,回差 35/20°/s)至少保 1 档——原地转向
     speed≈0 旧口径掉 2 档(10fps),49°/s 转向每显示帧跳 ~5° 成"瞬变"。
    档位最短驻留(TIER_DWELL_S)由调用方叠加。"""
    fast_thr = FRAME_FAST_RELEASE if cur == 0 else FRAME_FAST_ENTER
    if speed > fast_thr or flying or held:
        return 0
    turn_thr = TURN_TIER_RELEASE_DEG_S if cur <= 1 else TURN_TIER_ENTER_DEG_S
    mid_thr = FRAME_MID_RELEASE if cur == 1 else FRAME_MID_ENTER
    if speed > mid_thr or anim or abs(turn_deg_s) > turn_thr:
        return 1
    return 2


def mem_guard_next(state: int, mb: float, dwell_ok: bool) -> tuple[int, str | None]:
    """内存护栏状态机(0=ok / 1=trim / 2=purge),双阈值回差。

    - 越线首触立即动作(0→trim @>50、任意→purge @>55 升级);
    - 已在降级态时,重复动作须 dwell_ok(距上次动作 ≥MEM_DWELL_S)——
      消除"裁剪→预热线回灌→马上又裁剪"的阈值附近乒乓;
    - 解除带回差:trim 态 <45 才回 ok(5MB 回差),purge 态 <50 回 trim。
    返回 (新状态, 动作);动作 None 表示本周期不动作。"""
    if mb > MEM_PURGE_MB:
        if state < 2:
            return 2, "purge"
        return (2, "purge" if dwell_ok else None)
    if state == 2:
        return ((1, None) if mb < MEM_TRIM_MB else (2, None))
    if mb > MEM_TRIM_MB:
        if state == 0:
            return 1, "trim"
        return (1, "trim" if dwell_ok else None)
    if state == 1 and mb < MEM_RELEASE_MB:
        return 0, None
    return state, None


class PetHandle:
    def __init__(self, state: PetState, body: IBody, brain: IBrain,
                 scale: float = 1.0) -> None:
        self.state = state
        self.body = body
        self.brain = brain
        self.scale = float(scale)     # 体型档位(五档,AG5;渲染 traits 与
                                      # 持久化都以它为唯一事实源)
        self.drag_offset = (0.0, 0.0)
        self._drag_pose = None        # r10:physics.drag.DragPose(拖拽力学 v2)
        self.last_render = 0.0        # 自适应帧率
        self.last_pos = state.pos
        self.last_cost_ms = 0.0       # 上次 render_pose 耗时(PF:重帧退避用)
        self.cost_backoff = 0         # r24:滞回退避剩余帧数(见 _render)
        self.skip_streak = 0          # 预测性跳帧连续计数(PF,见 _render)
        self.frame_tier = 0           # 帧率档位 0/1/2(PF 快赢③:回差状态)
        self.tier_since = 0.0         # 当前档位进入时刻(最短驻留用)
        self.last_disp_sig = None     # 上次实际上传的完整显示签名(PF 快赢①;
                                      # None=首帧必画)
        self.last_img = None          # 上次实际上传的图像对象(OPT-7 缓存同一性)
        self.last_coords = None       # 上次上传的整数画布坐标
        self.chibi = False            # Q 版(E3/B4 引擎 sticky 状态;渲染半场见 traits)
        self.iridescent = False       # 虹色(E3/B4 sticky;同上)

    @property
    def pet_id(self) -> str:
        return self.state.pet_id


class App:
    def __init__(self) -> None:
        self.cfg = load_config()
        set_lang(self.cfg.language)   # 配置优先;缺失/不认识时 load_config 已填系统语言
        self.root = tk.Tk()
        # r24:默认 report_callback_exception 用 print 打印(tkinter/__init__.py
        # :1788),pythonw/--noconsole 下 stdout 是坏句柄 → 该 print 抛 OSError
        # 穿出 mainloop、整个程序退出。换成只落 diag.log 的实现(不动窗口显示)。
        from neuropet.diag import report_callback_exception
        self.root.report_callback_exception = report_callback_exception
        self.root.withdraw()
        self.bus = EventBus()
        self.registry = PluginRegistry()
        self._register_builtin_plugins()
        self._external_reports = self.registry.scan_dir(PLUGINS_DIR)
        for pid, err in self._external_reports:
            if err:
                print(f"[plugins] 加载失败 {pid}: {err}")
        self.world = WorldModel(self.root.winfo_screenwidth(), self.root.winfo_screenheight())
        self.stage = OverlayStage(self.root, *self.world.screen)
        self.stage.bind_interaction(self._on_press, self._on_drag, self._on_release)
        try:
            self._stage_hwnd = int(self.stage.win.winfo_id())
        except Exception:
            self._stage_hwnd = 0
        # v0.2.0:可用桌面边界句柄就是 world 持有的那一份(任务栏已扣除),
        # 身体软墙/拖拽仲裁/面板落食三处共用;2s 节流刷新在 _step_frame。
        self._desktop_acc = 1e9      # 初值远超周期:首帧立即取一次
        self.stage.trail.set_enabled(bool(getattr(self.cfg, "trail_enabled", True)))
        self._crawl_speed = snap_crawl_speed(
            getattr(self.cfg, "crawl_speed", 2.0))
        # v0.2.0:``--autostart``(登录自启动)启动时先落托盘。默认 False —— 手动
        # 双击仍遵循 ``cfg.panel_visible``,两个开关语义独立。
        self._startup_tray_only = False
        # ---- C2 气泡引导层(共享画布独立 canvas 项,不进宠物精灵管线) ----
        self._static_dirty = True
        self.pets: dict[str, PetHandle] = {}
        # r16 Task E:启动名册文件路径(测试可指向 tmp)与会话回写开关。
        self._pets_path = _PETS_FILE
        self._roster_live = False      # run() 置 True 后增删才回写 pets.json
        # §6:隐藏中的宠物(pet_id → PetHandle 整包保留)。移出 self.pets
        # 即同时停 sim(_step)与渲染(_render)——两者都遍历 self.pets;
        # state/body/brain/体型档原地保留,召回时逐位恢复,不占可见名额。
        self._hidden: dict[str, PetHandle] = {}
        # r11/r12:FeaturePlugin 内核(ADR-0031/0034)——drag 功能插件化;
        # 位置写权威经 StateArbiter 每帧合成(drag 90 > locomotion 40)。
        from .kernel import FeatureKernel
        from ..features import DragFeature
        self._kernel = FeatureKernel(self, self.bus,
                                     self.world.clamp_to_screen,
                                     self._clamp_pos)
        self._kernel.mount(DragFeature(self))
        # ---- E4/A3 屏幕感知 + B4 食物效果引擎(集成接线) ----
        self._screen_mon = ScreenMonitor()   # 前台跟踪(内部 2Hz 节流,~20µs/s)
        self._screen_events: tuple = ()      # ≥2s 刷新的新窗/消失事件缓存
        self._screen_event_t = 0.0
        self._fx: dict = {}                  # pet_id → feeding.EffectEngine(avatar 效果)
        self._fx_tick_t = 0.0
        self._mastery_acc = 0.0              # r25 A4:熟练度重算累加器(2s 节拍)
        self._emo_hold: dict = {}            # B2:pet_id → (t_last_ok, 三通道)
        self._emo_log_t = -1e9               # B2:情绪读数不健康的限频诊断时刻
        self._speed_mult: dict = {}          # pet_id → 速度倍率(蓝糖珠 buff)
        self._species_insts: dict[str, SpeciesPlugin] = {}
        self.feeding = False
        self._feeding_until: float | None = None  # 投喂截止时刻(perf_counter);None=不限时/未开启
        # 面板矩形/可见性缓存:主线程每 0.5s 刷新,钩子线程只读(元组原子读,线程安全)
        self._panel_rect: tuple[int, int, int, int] | None = None
        self._panel_visible = False
        self._panel_extra_rects: tuple = ()
        # 点击穿透的同进程命中判断(纯 Win32,钩子线程可用):排除透明舞台,
        # 否则铺满全屏的色键窗口会把整块桌面都当成"自己人"。
        self._own_pid = os.getpid()
        self._stage_hwnd = 0
        self._panel_rect_acc = 1.0       # 节流累加器(初值≥周期:首帧立即刷新)
        self._hook = None
        self._running = True
        self._panel = None
        self._tray = None               # 系统托盘(ui.tray.TrayIcon),run() 中懒加载
        self._cursor_getter = self._make_cursor_getter()
        # PF:预测性跳帧开关 —— 仅 hybrid 管线需要(legacy 不用躯干精灵)
        from neuropet.render.renderer import _render_mode
        self._prewarm_on = _render_mode() in ("hybrid", "new", "v2")
        if self._prewarm_on:
            # PF:缩短 GIL 轮转片 —— 预热线程与主线程都是"短促 Python 段 +
            # 长 PIL C 段"的混合,默认 5ms 轮转会让主线程 Python 段最长等
            # 5ms 才抢回 GIL(实测轻帧被抬高 2-4ms);降到 1.5ms 后延迟收紧
            sys.setswitchinterval(0.0015)
        # 渲染段耗时环形统计(最近 120 帧;经 render_stats() 暴露,供面板/调试)
        self._frame_ms: deque = deque(maxlen=120)
        # PF 快赢①:上传去重计数(诊断;经 render_stats() 暴露)
        self._dedup_skips = 0
        self._upload_count = 0
        # PF 快赢②:gc 调优 —— 阈值放宽立即生效;freeze 在预热完成后
        # (GC_FREEZE_AFTER_S,见 _step_frame)一次性执行
        gc.set_threshold(*GC_THRESHOLDS)
        self._gc_frozen = False
        self._run_t0 = time.perf_counter()
        # PF 快赢③:内存护栏回差状态机(0=ok/1=trim/2=purge)+ 上次动作时刻
        self._mem_state = 0
        self._last_mem_action = float("-inf")
        # AG5:内存护栏 —— 启动即应用基线降档(2° 桶 + 预算下调 + L2 预算),
        # 必须在 _prewarm_torso 起线程之前生效,否则预热会把 1° 桶先灌满。
        self._apply_memory_guard()
        # AG5:渲染配额轮转游标 + 内存采样累加器(见 _render/_memory_tick)
        self._render_cursor = 0
        # r24:预热线程的自测角速度记账(pet_id → (上次朝向°, 时刻)),仅预热
        # 线程读写,与渲染线程无共享
        self._pwm_rate: dict[str, tuple[float, float]] = {}
        self._mem_acc = MEM_SAMPLE_S   # 初值≥周期:首帧立即采样一次基线
        self._ws_peak = 0.0            # 观测峰值(诊断打印用)
        self.root.protocol("WM_DELETE_WINDOW", self.shutdown)

    # ---------------- 插件 ----------------
    def _register_builtin_plugins(self) -> None:
        from neuropet.species import cockroach, fruitfly
        for mod in (cockroach, fruitfly):
            self.registry.register_class(mod.PLUGIN_CLS)

    def species(self, species_id: str) -> SpeciesPlugin:
        if species_id not in self._species_insts:
            self._species_insts[species_id] = self.registry.create(species_id, app=self)
        return self._species_insts[species_id]

    def species_manifests(self):
        return self.registry.manifests("species")

    # ---------------- 宠物生命周期 ----------------
    def add_pet(self, species_id: str, pos=None, intelligence: int | None = None,
                brain_id: str | None = None,
                pet_id: str | None = None) -> str:
        """新建宠物。``intelligence`` = **初始等级**(缺省 ``cfg.intelligence`` ——
        cfg 的值就是"新宠种子",这也是它现在唯一的运行期用途)。

        语义(勿踩):这是**下界**而非定值 —— ``_mastery_beat`` 的 ratchet 只
        上调,故该值不会被自动派生调低;若 ``pet_id`` 已存在档案,载入时还会取
        ``max(初始等级, 档案里的等级)``(档案等级同样只上调,见 brain
        ``_restore_pet_state``)。要"冻住"一个精确等级(调试/插件),用回退开关
        ``NEUROPET_MASTERY=off``(停止自适应,脑保持当前等级)。
        """
        if len(self.pets) >= self.cfg.max_pets:
            raise RuntimeError(t("error.desk_full", total=self.cfg.max_pets))
        sp = self.species(species_id)
        if pet_id is None:
            pet_id = f"{species_id}-{uuid.uuid4().hex[:6]}"
        elif pet_id in self.pets or pet_id in self._hidden:
            pet_id = f"{pet_id}-{uuid.uuid4().hex[:4]}"   # 防 id 冲突(防御性)
        if pos is None:
            # Place a newcomer in the least occupied slot instead of stacking every
            # insect in the centre. This also avoids a large merged upload at startup.
            width, height = self.world.screen
            slots = [(width * (0.15 + col * 0.17), height * (0.32 + row * 0.30))
                     for row in range(2) for col in range(5)]
            existing = [h.state.pos for h in self.pets.values()]
            pos = max(slots, key=lambda p: min(
                ((p[0]-q[0])**2 + (p[1]-q[1])**2 for q in existing), default=0.0))
        state = PetState(pet_id=pet_id, species_id=species_id,
                         name=sp.get_manifest().name, pos=self.world.clamp_to_screen(pos))
        # AG5:体型档位 —— profile 持久值优先;NEUROPET_SCALE 环境变量为
        # 验收/测试覆盖(五档 launch_check 复测用);最后吸附到标准档。
        scale = self._load_scale(pet_id)
        env = self._startup_scale_override()
        if env is not None:
            scale = env
        scale = snap_scale(scale)
        body = self._build_body(sp, state, scale)
        brain = sp.create_brain(state)
        if brain_id:
            brain = self.registry.create(brain_id, app=self, state=state)
        brain.set_intelligence(intelligence or self.cfg.intelligence)
        handle = PetHandle(state, body, brain, scale=scale)
        self.pets[pet_id] = handle
        self.world.upsert_pet(state)
        # 身体建好后按**它自己的**留白再钳一次位置(上面的钳位用的是无身体
        # 尺寸的默认留白):体型大的蟑螂不会一出生就半个身子压在任务栏上。
        state.pos = self.world.clamp_to_screen(state.pos, self._margin_for(handle))
        self.stage.ensure_pet_item(pet_id)
        self._load_profile(handle)
        self.bus.publish("system/pet_added", {"pet_id": pet_id, "species": species_id})
        if self._panel:
            self._panel.refresh_pets()
        self._write_roster()            # r16 Task E:可见名册回写 pets.json
        return pet_id

    def remove_pet(self, pet_id: str) -> None:
        h = self.pets.pop(pet_id, None)
        if h is None:
            h = self._hidden.pop(pet_id, None)   # 隐藏态宠物也允许彻底移除
        if h:
            self._save_profile(h)
            self.stage.remove_pet(pet_id)
            self.world.remove_pet(pet_id)
            self._write_hidden_session()
            self.bus.publish("system/pet_removed", {"pet_id": pet_id})
            if self._panel:
                self._panel.refresh_pets()
            self._write_roster()        # r16 Task E:可见名册回写 pets.json

    # ---------------- r16 Task E:启动名册读配置(data/pets.json) ----------------
    def _load_startup_roster(self) -> None:
        """启动名册读配置(替代旧 run() 硬编码"每注册物种各 add 一只")。

        - 读 self._pets_path 逐条 add_pet;文件缺失由 config.load_roster 自动
          生成缺省名册(cockroach+fruitfly 各一只,与旧行为等价,老用户无感);
        - 未知物种:该条跳过并打印警告,不崩(物种插件可能被卸载/改名);
        - 仍受 cfg.max_pets 上限(与旧 run 一致,超限条目忽略);
        - 条目可选 ``pet_id``(r25 修复):传给 add_pet 以**复用同一档案目录**
          (记忆/信任/等级/年龄跨重启保真);缺该字段的旧名册 → 发新 uuid,
          行为与修复前一致(老玩家既有孤儿档案无法匹配,属一次性损失);
        - 本方法**不做回写**:跳过/忽略的条目原样保留在文件里(装回插件后
          仍生效),名册只在用户增删时被重写;
        - 调用序契约:先于 _restore_hidden(隐藏宠还原在其之后,现序不变)。
        """
        for entry in load_roster(self._pets_path):
            if len(self.pets) >= self.cfg.max_pets:
                print(f"[app] 名册载入:已达最大宠物数 {self.cfg.max_pets},"
                      f"其余条目忽略")
                break
            try:
                self.add_pet(entry["species"], pos=entry.get("pos"),
                             pet_id=entry.get("pet_id"))
            except Exception as exc:
                print(f"[app] 名册条目跳过 {entry['species']}: {exc}")

    def _visible_roster(self) -> list[dict]:
        """当前可见名册快照(物种 + ``pet_id``;隐藏宠不进名册,走 session.json)。

        r25 修复:**必须带 pet_id** —— 此前只存 species,重启时 add_pet 发新
        uuid → profile_dir 换目录 → ``_load_profile`` 读不到档案,记忆/信任/
        等级/年龄全部作废(实测 ``data/profiles/`` 累积 2284 个孤儿目录)。
        隐藏宠本就在 session.json 里按 pet_id 记录,口径由此统一。
        """
        return [{"species": h.state.species_id, "pet_id": h.pet_id}
                for h in self.pets.values()]

    def _write_roster(self) -> None:
        """可见名册回写 pets.json(增删/隐藏/召回后由各入口调用)。

        仅会话运行后生效(_roster_live,run() 在名册载入+隐藏还原完成后置
        True):测试/离线脚本直接构造 App 不落盘,避免污染仓库真实 data/。
        写盘失败仅打印,不影响宠物生命周期主流程。"""
        if not self._roster_live:
            return
        try:
            save_roster(self._visible_roster(), self._pets_path)
        except Exception as exc:
            print(f"[app] 名册回写失败: {exc}")

    # ---------------- §6:隐藏宠物并保留记忆 + 召回 ----------------
    def hide_pet(self, pet_id: str) -> bool:
        """隐藏宠物并整包保留记忆(调研 §6)。

        把 PetHandle(state/body/brain/体型档/学习器)原地移入 _hidden:
        主循环 sim(_step)与渲染(_render)都遍历 self.pets,移出即同时停
        模拟与渲染;舞台 canvas 项删除、世界快照不再含它(其他宠/食物感知
        不到)。情绪/记忆/学习全部冻结保留,召回时逐位恢复。
        重复隐藏同一 id 幂等(返回 False,无副作用)。
        """
        h = self.pets.pop(pet_id, None)
        if h is None:
            return False            # 已隐藏或不存在:幂等无副作用
        self._hidden[pet_id] = h
        self.world.remove_pet(pet_id)
        self.stage.remove_pet(pet_id)
        self._save_profile(h)       # 与 remove_pet 同规:隐藏即落盘一份
        self._write_hidden_session()
        self.bus.publish("system/pet_hidden", {"pet_id": pet_id})
        if self._panel:
            self._panel.refresh_pets()
        self._write_roster()        # r16 Task E:隐藏宠退出可见名册
        return True

    def recall_pet(self, pet_id: str) -> bool:
        """召回隐藏宠物:同一 PetHandle 原样放回(self.pets),位置/情绪/
        记忆/学习状态与隐藏前逐位连续。重复召回幂等(返回 False);
        可见名额已满(max_pets)时拒绝并保持在隐藏态(不丢状态)。"""
        h = self._hidden.pop(pet_id, None)
        if h is None:
            return False            # 未隐藏:幂等无副作用
        if len(self.pets) >= self.cfg.max_pets:
            self._hidden[pet_id] = h
            return False
        self.pets[pet_id] = h
        self.world.upsert_pet(h.state)      # 重新参与世界快照(FoodView/感知)
        self.stage.ensure_pet_item(pet_id)
        self._write_hidden_session()
        self.bus.publish("system/pet_recalled", {"pet_id": pet_id})
        if self._panel:
            self._panel.refresh_pets()
        self._write_roster()        # r16 Task E:召回宠回归可见名册
        return True

    def hidden_pets(self) -> dict[str, PetHandle]:
        """面板/托盘只读视图:当前隐藏中的宠物句柄快照(pet_id → PetHandle)。"""
        return dict(self._hidden)

    def _write_hidden_session(self) -> None:
        """隐藏会话落盘:隐藏宠 pet_id/物种清单(data/session.json)。
        重启后 run() → _restore_hidden 据此还原隐藏态。"""
        try:
            records = [{"pet_id": pid, "species_id": h.state.species_id}
                       for pid, h in self._hidden.items()]
            _SESSION_FILE.write_text(
                json.dumps({"hidden": records}, ensure_ascii=False, indent=1),
                "utf-8")
        except Exception as exc:
            print(f"[app] 隐藏会话保存失败: {exc}")

    def _read_hidden_session(self) -> list[dict]:
        try:
            if _SESSION_FILE.exists():
                d = json.loads(_SESSION_FILE.read_text("utf-8"))
                recs = d.get("hidden", [])
                if isinstance(recs, list):
                    return [r for r in recs if isinstance(r, dict)]
        except Exception as exc:
            print(f"[app] 隐藏会话载入失败: {exc}")
        return []

    def _restore_hidden(self) -> None:
        """启动还原隐藏会话(§6:启动载入后隐藏态还原)。

        按会话记录的原 pet_id 重建宠物并立即隐藏:记忆/体型档经 add_pet 的
        profile 载入恢复;位置为屏幕默认(与可见宠一致,位置本就不跨重启
        持久化)。单条失败(物种缺失等)跳过;结束后按实际隐藏集重写会话。
        """
        recs = self._read_hidden_session()
        if not recs:
            return
        for rec in recs:
            pid, sp = rec.get("pet_id"), rec.get("species_id")
            try:
                new_id = self.add_pet(sp, pet_id=pid)
                self.hide_pet(new_id)
            except Exception as exc:
                print(f"[app] 隐藏宠还原失败 {pid}: {exc}")
        self._write_hidden_session()

    # ---------------- v0.2.0:统一边界口径(任务栏已扣除的可用桌面) ----------------
    def _margin_for(self, h) -> float:
        """该宠的边界留白 = 渲染半宽(与 body 软墙同一个函数,不留分歧)。"""
        try:
            half = h.body.window_half()
        except Exception:
            half = 0.0
        return self.world.desktop.margin_for(half)

    # ---------------- AG5:体型缩放(五档,每宠独立) ----------------
    @staticmethod
    def _startup_scale_override() -> float | None:
        """NEUROPET_SCALE 环境变量(验收通道):设置时全部新宠强制该档。

        供五档 launch_check 复测(切档各跑一次)与测试注入;非法值忽略。
        """
        import os
        raw = os.environ.get(_SCALE_ENV)
        if not raw:
            return None
        try:
            return snap_scale(float(raw))
        except (TypeError, ValueError):
            return None

    def _species_params(self, species_id: str) -> dict | None:
        """物种包模块级 PARAMS(权威数值单一事实源);外部插件无 PARAMS 时 None。"""
        sp = self._species_insts.get(species_id)
        if sp is None:
            return None
        import sys as _sys
        mod = _sys.modules.get(type(sp).__module__)
        params = getattr(mod, "PARAMS", None)
        return params if isinstance(params, dict) else None

    def _build_body(self, sp, state: PetState, scale: float) -> IBody:
        """按档位构建身体:k=1 走插件原路径(零改动);k≠1 用 scale_params
        的缩放参数直接重建 GenericInsectBody(体节/腿长/触角/步距/速度全 ×k,
        由 __init__ 重建 SkeletonSpec/LegKinematics/TripodGait —— 即"body 用
        scaled 骨架重建";与 rig.scaled(k) 的等价性见 tests/test_scaling.py)。"""
        if scale == 1.0:
            body = sp.create_body(state)
        else:
            params = self._species_params(sp.get_manifest().id)
            if params is None:                       # 外部插件不支持缩放:原样回退
                body = sp.create_body(state)
            else:
                from neuropet.body.base import GenericInsectBody
                body = GenericInsectBody(state, scale_params(params, scale))
                # rig spec 的 scale 标记回写:参数已按 ×k 重建,from_params 会
                # 把 scale 硬编码为 1.0;这里标注真实档位(pose()["bones"]["scale"]
                # 供渲染/诊断读)。
                body._rig.spec["scale"] = float(scale)
        # v0.2.0:全局爬行速度倍率在这里一次算进速度类参数(与体型档位同一次
        # 构建,不会累积乘法)。buff 也在同一入口叠加,见 _speed_mult。
        self._apply_speed_multiplier_to(body, pet_id=state.pet_id)
        return body

    def _apply_speed_multiplier_to(self, body, pet_id: str | None = None) -> None:
        """把"全局倍率 × 该宠 buff"写进 body(插件 body 无此方法则跳过)。"""
        setter = getattr(body, "set_speed_multiplier", None)
        if setter is None:
            return
        buff = 1.0
        if pet_id is not None:
            try:
                buff = float(self._speed_mult.get(pet_id, 1.0))
            except Exception:
                buff = 1.0
        setter(self._crawl_speed * buff)

    def _sync_speed_multipliers(self) -> None:
        """把当前倍率同步给**全部**宠物(含隐藏宠:召回即生效)。"""
        for h in list(self.pets.values()) + list(self._hidden.values()):
            self._apply_speed_multiplier_to(h.body, pet_id=h.pet_id)

    def crawl_speed(self) -> float:
        return self._crawl_speed

    def set_crawl_speed(self, mult: float) -> float:
        """全局爬行速度档位(面板入口):吸附 → 保存 → 立即生效。

        生效方式是**就地改写速度参数**,不重建 body ⇒ 当前位置、朝向、当前
        速度、钉足世界坐标与步态相位全部连续,滑杆升降只是有限加减速。
        """
        value = snap_crawl_speed(mult)
        self._crawl_speed = value
        self.cfg.crawl_speed = value
        save_config(self.cfg)
        self._sync_speed_multipliers()
        self.bus.publish("user/crawl_speed", {"mult": value})
        return value

    def set_trails(self, on: bool) -> bool:
        """高速拖尾开关(面板入口):存配置 + 立即清/显,默认开。"""
        self.cfg.trail_enabled = bool(on)
        save_config(self.cfg)
        self.stage.trail.set_enabled(self.cfg.trail_enabled)
        self.bus.publish(TRAIL_KEY, {"on": self.cfg.trail_enabled})
        return self.cfg.trail_enabled

    def trails(self) -> bool:
        return bool(getattr(self.stage.trail, "enabled", True))

    def _clamp_pos(self, pos, pet_id: str | None = None):
        """按宠物身体半径钳位(仲裁器专用;无该宠时用默认留白)。"""
        h = self.pets.get(pet_id) if pet_id else None
        margin = self._margin_for(h) if h is not None else DEFAULT_MARGIN
        return self.world.clamp_to_screen(pos, margin)

    # ---------------- Windows 开机自启动(用户级,注册表为唯一真源) ----------------
    def autostart_enabled(self) -> bool:
        """**读注册表**的当前状态(不是配置):便携文件夹移动后路径不再匹配
        就会显示未勾选,提示重新启用以更新路径。"""
        try:
            return bool(autostart_enabled())
        except Exception:
            return False

    def set_autostart(self, on: bool) -> bool:
        """写/删 HKCU Run 的本程序项。返回**实际生效**状态。

        失败时返回 False,面板负责回滚复选框并给双语错误 —— 绝不把
        "配置里写了 true"当成"系统已注册"。
        """
        try:
            write_autostart(bool(on))
            # The registry helper returns operation success, including successful
            # deletion. UI callers need the resulting enabled state instead.
            return self.autostart_enabled()
        except Exception as exc:
            from neuropet.diag import log
            log(f"[autostart] 切换失败({on}): {exc!r}")
            return self.autostart_enabled()

    def pet_scale(self, pet_id: str) -> float:
        h = self.pets.get(pet_id)
        return float(h.scale) if h else 1.0

    def set_pet_scale(self, pet_id: str, scale: float) -> float:
        """面板/托盘设档:重建该宠 body(即改即生效)+ 持久化 + 护栏重估。

        PetState(pos/heading/speed/情绪/记忆)在 handle 侧共享,新 body 构造时
        gait 已按当前位姿种脚,画面无跳变;躯干缓存键与 k 无关(L1)/含 k(L2),
        无需失效任何缓存。返回吸附后的实际档位。"""
        h = self.pets.get(pet_id)
        if not h:
            return 1.0
        k = snap_scale(scale)
        if k != h.scale:
            sp = self.species(h.state.species_id)
            h.scale = k
            h.body = self._build_body(sp, h.state, k)
            h.last_cost_ms = 0.0
            h.skip_streak = 0
            # PF 快赢①:几何/traits 全变,去重记账一并作废(防御性,签名本身
            # 也必然变化,此处保证语义一目了然)
            h.last_disp_sig = None
            h.last_img = None
            h.last_coords = None
            self._apply_memory_guard()           # 最高档位变化 → L2/阴影预算重估
            self._save_scale(h)
            self.bus.publish("user/scale", {"pet_id": pet_id, "scale": k})
        return k

    def _traits_base(self, h: PetHandle) -> dict:
        """k=1 基准 traits(torso_art L1 桶键;经 torso.base_traits 剥缩放键)。"""
        from neuropet.render import torso
        return torso.base_traits(self._render_traits(h))

    def _render_traits(self, h: PetHandle) -> dict:
        """渲染 traits = 物种色板 + 档位三键(scale/body_len_base/body_len)。

        body_len 恒供给基础值×k:渲染底盘阴影按 BL×k 缩放(精灵半轴 ×BL);
        torso 侧经 base_traits() 剥离缩放键后取 L1 旋转桶(五档共享 1x 桶),
        scale/body_len_base 则进入 torso.L2 成品桶键(F3:旋转缓存键含 scale)。
        """
        traits = dict(self.species(h.state.species_id).render_traits())
        # r17:物种键必须随 traits 下发——render3d 按 traits["species_id"]
        # 分派资产;曾缺此键 → 静默缺省蟑螂,活体果蝇被渲染成蟑螂(用户报障
        # 两轮;hybrid 按色板键取色故无感)。同物种内逐帧恒定,缓存键无 churn。
        traits["species_id"] = h.state.species_id
        base = self._species_params(h.state.species_id) or {}
        bl = float(base.get("body_len", 115 if "roach" in h.state.species_id else 30))
        traits["body_len"] = bl * float(h.scale)
        traits["body_len_base"] = bl
        traits["scale"] = float(h.scale)
        # E3/C1:Q 版/虹色 sticky 状态 → 渲染 traits(键仅在生效时出现,
        # 关闭态与基线逐键一致 → L1/L2/master 缓存键与烘焙输出零变化;
        # 生效键经 base_traits/_thash 进桶键 → Q 版/虹色相位 master 各自成桶)。
        if getattr(h, "chibi", False):
            traits["chibi"] = True
        if getattr(h, "iridescent", False):
            from neuropet.render import torso_art as _ta
            traits["hue_phase"] = _ta.hue_phase_now()
        return traits

    # ---------------- AG5:档位持久化(data/profiles/<pet_id>/scale.json) ----------------
    def _load_scale(self, pet_id: str) -> float:
        try:
            p = profile_dir(pet_id) / "scale.json"
            if p.exists():
                d = json.loads(p.read_text("utf-8"))
                return snap_scale(float(d.get("scale", 1.0)))
        except Exception as exc:
            print(f"[app] 档位载入失败 {pet_id}: {exc}")
        return 1.0

    def _save_scale(self, h: PetHandle) -> None:
        try:
            p = profile_dir(h.pet_id) / "scale.json"
            p.write_text(json.dumps({"scale": float(h.scale)}, ensure_ascii=False),
                         "utf-8")
        except Exception as exc:
            print(f"[app] 档位保存失败 {h.pet_id}: {exc}")

    # ---------------- AG5:内存护栏(L1/L2 双桶 + 降档 + 周期采样) ----------------
    def _apply_memory_guard(self) -> None:
        """按内存态/最高档位设置各缓存的量化/预算(护栏唯一入口)。

        - torso_art(1x L1 旋转桶):**ok 态 = 1° 量化 + 整圈预算**(r24 转向
          连续性:2°/3MB 旧基线只盖跟随窗,快转即重建风暴,见 ROT_BASE_BUDGET
          注);trim/purge 态降回 2° + 6MB。五档共享同一份 L1(键只含
          base_traits),故降档与档位无关、只做一次;
        - torso.L2 成品桶:k≥1.5 上调预算(放大精灵 0.92MB/桶@蟑 2x);
        - 阴影缓存:预算目标存档,实际裁剪由 _trim_render_caches 周期执行。
        注意:torso_art.invalidate() 会把量化复位为 1°,故任何全清之后必须
        重新调用本方法(见 _memory_tick 的 >58MB 分支)。
        """
        import os as _os
        if _os.environ.get("NEUROPET_MEM_GUARD", "1") in ("0", "false", "off"):
            return                               # 测量基线时可用环境变量关掉
        from neuropet.render import torso, torso_art
        kmax = max((h.scale for h in self.pets.values()), default=1.0)
        high = kmax >= 1.5
        degraded = self._mem_state > 0
        torso_art._angle_step = GUARD_ANGLE_STEP if degraded else 1
        torso_art._ROT_BUDGET.update(
            GUARD_ROT_BUDGET if degraded else ROT_BASE_BUDGET)
        l2b = L2_BUDGET_HIGH if high else L2_BUDGET_BASE
        torso.set_l2_budget(roach=l2b["roach"], fly=l2b["fly"])
        self._shadow_cap = SHADOW_CAP_HIGH if high else SHADOW_CAP_BASE

    def _trim_render_caches(self, frac: float = 1.0) -> None:
        """把阴影缓存裁到护栏目标(frac<1 时按比例再砍,降级阶梯用)。

        渲染器内部只在超过自身硬上限(22/56)时淘汰;k=1 时阴影补丁实际可
        涨到 ~25-37MB(0.65MB/片 × 行走扫过的朝向桶),是 69.5MB 基线的最大
        构成 —— 这里周期性裁到护栏目标,被裁桶由预热线程按转向方向补回。"""
        from neuropet.render import renderer as _R
        cap_s, cap_p = getattr(self, "_shadow_cap", SHADOW_CAP_BASE)
        cap_s = max(4, int(cap_s * frac))
        cap_p = max(4, int(cap_p * frac))
        while len(_R._shadow_sprites) > cap_s:
            _R._shadow_sprites.popitem(last=False)
        while len(_R._shadow_patches) > cap_p:
            _R._shadow_patches.popitem(last=False)

    def _memory_tick(self, dt: float) -> None:
        """工作集周期采样 + 超限降级(PF 快赢③回差版:>50 裁剪 / >55 清空重建,
        解除线 45/50 + 动作最短间隔 MEM_DWELL_S,见 mem_guard_next)。"""
        self._mem_acc += dt
        if self._mem_acc < MEM_SAMPLE_S:
            return
        self._mem_acc = 0.0
        mb = working_set_mb()
        if mb < 0:
            return
        _I.observe("ws_mb", mb)          # r25 U0:稳态工作集序列(5s 一采样)
        _I.note("pets_n", len(self.pets))
        self._ws_peak = max(self._ws_peak, mb)
        dwell_ok = (time.perf_counter() - self._last_mem_action) >= MEM_DWELL_S
        state, action = mem_guard_next(self._mem_state, mb, dwell_ok)
        if state != self._mem_state:
            _I.bump(f"mem_state_{state}")     # r25 U0:护栏状态迁移计数
            self._mem_state = state
        if action is None:
            return
        self._last_mem_action = time.perf_counter()
        if action == "purge":
            # L5:缓存清空重建(躯干 master/旋转桶/L2/阴影全清;量化随 invalidate
            # 复位,必须重新应用护栏)。gc 配合(调研 §E4):unfreeze 把常驻代
            # 放回可回收集合 → collect → 重新 freeze,清得干净且不丢冻结收益。
            from neuropet.render import torso, torso_art
            torso_art.invalidate()
            torso.invalidate_l2()
            self._apply_memory_guard()
            gc.unfreeze()
            gc.collect()
            gc.freeze()
            # r24:经 diag.log 而非 print——pythonw/--noconsole 下 stdout 可能
            # 是坏句柄,裸 print 抛 OSError 会让本帧静默中断(调试信息本就该落盘)。
            from neuropet.diag import log as _dlog
            _dlog(f"[app] 内存护栏 L5: {mb:.1f}MB > {MEM_PURGE_MB}MB,缓存清空重建")
            _I.bump("mem_purge")               # r25 U0:护栏动作计数 + 触发工作集
            _I.note("mem_purge_mb_last", round(mb, 1))
        else:
            self._trim_render_caches(frac=0.5)   # L4:各缓存砍半
            from neuropet.diag import log as _dlog
            _dlog(f"[app] 内存护栏 L4: {mb:.1f}MB > {MEM_TRIM_MB}MB,缓存减半")
            _I.bump("mem_trim")
            _I.note("mem_trim_mb_last", round(mb, 1))

    # ---------------- 主循环(绝对节拍,防 after 漂移) ----------------
    def run(self) -> None:
        try:
            self._load_startup_roster()   # r16 Task E:读 pets.json(缺省生成)
        except Exception as exc:
            print(f"[app] 初始宠物创建失败: {exc}")
        self._restore_hidden()   # §6:上次退出时隐藏的宠物按原 id 还原隐藏态
        # 名册载入与隐藏还原期间不回写(未知物种条目/超限条目保留在文件里);
        # 此后用户经面板/托盘增删即实时回写 pets.json。
        self._roster_live = True
        if self._prewarm_on:   # r22:3d/3d2 渲染不采样 2D 躯干桶,预热线程纯空转
            self._prewarm_torso()
        from neuropet.ui.compact_panel import ControlPanel
        self._panel = ControlPanel(self)
        self._panel.set_on_drop_request(self._on_panel_drop)   # r10:选择器→桌面
        # 系统托盘:懒加载 + 失败降级(无托盘也能正常运行,绝不因此崩溃)。
        # 面板关闭=隐藏,托盘是隐藏后的唯一唤出入口。
        try:
            from neuropet.ui.tray import TrayIcon
            tray = TrayIcon(self)
            if tray.start():
                self._tray = tray
            else:
                print("[app] 无托盘运行，收起面板后可从任务栏恢复")
        except Exception as exc:
            print(f"[app] 托盘初始化失败,无托盘运行: {exc}")
        # v0.2.0:``--autostart`` 启动(登录自启动)时先落托盘,不弹面板。手动双击仍
        # 遵循 ``cfg.panel_visible`` —— 两个开关(面板可见 / 系统自启动)语义
        # 独立,不要互相顶替。
        if "--autostart" in sys.argv:
            self._startup_tray_only = True
        if self._startup_tray_only or not self.cfg.panel_visible:
            self._panel.hide()
        self._next_t = time.perf_counter()
        self._last_tick = self._next_t
        self._run_t0 = self._next_t     # PF 快赢②:gc.freeze 计时起点(run 实际开始)
        self.root.after(1, self._tick)
        self.root.mainloop()

    def _tick(self) -> None:
        if not self._running:
            return
        now = time.perf_counter()
        # 绝对节拍:落后太多(>250ms,如系统休眠)才重置
        if now - self._next_t > 0.25:
            self._next_t = now
        dt = min(0.05, max(0.001, now - getattr(self, "_last_tick", now-FRAME_DT)))
        self._last_tick = now
        self._next_t += FRAME_DT
        _I.observe("dt_ms", dt * 1000.0)      # r25 U0:R2 帧间隔口径
        _I.mark("interval_ms")                # 真实 tick 间隔(墙钟)
        t_sf = _I.begin()
        try:
            self._step_frame(dt)
        except Exception:
            from neuropet.diag import log_exc    # r22:pythonw 无控制台,print 不可见
            log_exc("_tick")
        _I.end("step_frame_ms", t_sf)
        if not self._running:
            return
        delay = max(1, int((self._next_t - time.perf_counter()) * 1000))
        self.root.after(delay, self._tick)

    def _step_frame(self, dt: float) -> None:
        self.bus.drain()
        if not self._running:
            return   # 托盘「退出」等 shutdown 可能在 drain 重放中触发,当帧立即收敛
        now = time.perf_counter()
        # 投喂限时:开启且超时 → 自动退出(纯函数判定;面板勾选经
        # toggle_feeding 内部的 set_feeding(False) 自动同步)
        if feeding_expired(self.feeding, self._feeding_until, now):
            self.toggle_feeding(False)
            self.bus.publish("user/feeding", {"on": False, "reason": "timeout"})
        # 面板矩形缓存节流刷新(主线程;供钩子线程穿透判断只读)
        self._panel_rect_acc += dt
        if self._panel_rect_acc >= PANEL_RECT_REFRESH_S:
            self._panel_rect_acc = 0.0
            self._refresh_panel_rect()
        # v0.2.0:可用桌面节流刷新(任务栏显隐/自动隐藏滑出)。DesktopArea 自己
        # 再按 2s 判一次,这里只是给它节流的机会 —— 不会每帧每宠查 Win32。
        self._desktop_acc += dt
        if self._desktop_acc >= 2.0:
            self._desktop_acc = 0.0
            try:
                self.world.refresh_desktop()
            except Exception:
                pass
        pos = self._cursor_getter()
        if pos:
            from neuropet.perception.mouse import update_kinematics
            self.world.update_cursor(pos[0], pos[1], update_kinematics)
        t_st = _I.begin()       # r25 U0:仿真段(脑+身体)耗时
        self._step(dt)
        _I.end("step_ms", t_st)
        self._render(dt)
        # PF 快赢②:预热完成后冻结常驻对象(一次性)。启动前 5s 内模块/类/
        # 物种 PARAMS/连接组/姿态库/tk 内部与首轮烘焙的 master 等已就位,
        # freeze 把它们移出 gen0/1/2 扫描 → gen2 全回收不再扫遍常驻对象,
        # 消除 5~15ms 级 P99 尖刺;其后新增的缓存桶仍在正常代,照常回收。
        if not self._gc_frozen and now - self._run_t0 >= GC_FREEZE_AFTER_S:
            self._gc_frozen = True
            gc.freeze()
            print(f"[app] gc.freeze: 常驻对象已移出代际扫描 "
                  f"(阈值 {gc.get_threshold()}, 启动 {now - self._run_t0:.1f}s 后)")
        self._memory_tick(dt)   # AG5:工作集采样/降级(5s 周期,常态零开销)
        if self._panel:
            t_pn = _I.begin()   # r25 U0:面板 tick 耗时(可见/隐藏两态)
            self._panel.tick(dt)
            _I.end("panel_tick_ms", t_pn)

    def _prewarm_torso(self) -> None:
        """启动躯干旋转缓存「跟随式预热」线程(PF 性能专项,缓存接线)。

        宠物行走/转向以 ~1-3°/帧扫过 1° 朝向桶,首遇桶在蟑螂上是一次
        480² 旋转重建(~12-13ms)——直接打进渲染段预算。两个后台线程持续
        "跟随"蟑螂当前朝向:每轮从当前 heading 向转向方向找最近缺失桶并用
        与逐帧路径相同的实现构建(逐位等价,只是提前付费),双线程奇偶分桶
        合计 ~150 桶/s,盖过宠物 ≤180°/s 的转向消耗。PIL 旋转释放 GIL,
        不阻塞渲染主线程。果蝇未命中仅 ~0.35ms,不值得跟随,跳过。
        legacy 管线不用躯干精灵(_prewarm_on=False),任何异常都不影响运行。"""
        def worker(idx: int) -> None:
            try:
                import math
                from neuropet.render import torso_art
                from neuropet.render import renderer as _R
                from neuropet.render import torso
                try:    # 后台线程降优先级(尽力而为):预热不与渲染抢调度
                    import ctypes
                    ctypes.windll.kernel32.SetThreadPriority(
                        ctypes.windll.kernel32.GetCurrentThread(), -1)
                except Exception:
                    pass
                last_hd: dict[str, float] = {}
                while self._running:
                    handles = [h for h in list(self.pets.values())
                               if "cockroach" in h.state.species_id]
                    active_ids = {h.pet_id for h in handles}
                    last_hd = {pid: hd for pid, hd in last_hd.items() if pid in active_ids}
                    did = False
                    for h in handles:
                        st = h.state
                        # AG5:L1(1x 旋转桶)键 = base_traits(五档共享);阴影/
                        # L2(成品桶)键含缩放 → 用 _render_traits(含 body_len×k)。
                        traits_base = self._traits_base(h)
                        traits_sc = self._render_traits(h)
                        hd = math.degrees(st.heading) % 360.0
                        prev = last_hd.get(h.pet_id)
                        if prev is None:
                            sign = 1.0
                        else:
                            d = (hd - prev + 180.0) % 360.0 - 180.0
                            sign = 1.0 if d >= 0 else -1.0
                        last_hd[h.pet_id] = hd
                        fold = 0.0 if st.mode == MovementMode.FLY else 1.0
                        # r24 体验:跟随半径按**实际角速度**自适应。旧固定 ±14°
                        # 在持续转向(~90-180°/s,每帧 1.5-3°，一轮 2 线程仅填
                        # 1-2 桶)下永远追不上,实测稳态桶缺失率 76%、2/3 帧
                        # >5ms。改为:目标 = 当前朝向 + ω×LOOKAHEAD(0.35s 前
                        # 视),span 随 ω 放大(上限 40°)——转向越快、前瞻越远,
                        # 把"即将用到的一整段弧"提前备好。角速度由本线程自测
                        # (与渲染线程无共享状态)。
                        nv = self._pwm_rate.get(h.pet_id)   # (hd_prev, t_prev)
                        now_t = time.perf_counter()
                        om = 0.0
                        if nv is not None:
                            dth = (hd - nv[0] + 180.0) % 360.0 - 180.0
                            dtt = max(1e-4, now_t - nv[1])
                            om = dth / dtt                  # °/s
                        self._pwm_rate[h.pet_id] = (hd, now_t)
                        look = max(-40.0, min(40.0, om * 0.35))
                        span = int(min(40.0, 14.0 + abs(om) * 0.18))
                        aim = (hd + look) % 360.0
                        # 最近缺失桶(哈希只算一次;奇偶分工:两线程各管一半)。
                        # 跟随窗口必须明显小于旋转缓存预算,否则窗口自身会把
                        # 预算挤荡成永久重建循环;16MB 预算下 40° 窗安全。
                        target = torso_art.first_missing_bucket(
                            st.species_id, traits_base, aim, fold,
                            sign=sign, span=span, parity=idx % 2)
                        if target is not None:
                            torso_art.prewarm(st.species_id, traits_base,
                                              heading_deg=target, fold=fold,
                                              span_deg=0)
                            did = True
                        # L2 成品桶跟随(AG5):缩放宠物把转向前方的缩放精灵补上
                        # (重采样 ~1-4ms,预热提前付费,不进渲染帧)
                        if h.scale != 1.0:
                            did |= torso.prewarm_scaled(
                                st.species_id, h.body.window_half(), target or hd,
                                fold, traits_sc)
                        # 阴影跟随:转向方向前方的 10° 桶(精灵重建 ~4.5ms,
                        # 是 P95 尖刺的另一来源)。键与渲染同源用原始度数;
                        # traits 用含缩放版(精灵尺寸随 body_len×k)。
                        sh_hd = math.degrees(st.heading) + sign * 10.0
                        if len(handles) <= 2 and not _R.shadow_bucket_ready(st.species_id, traits_sc,
                                                      sh_hd):
                            _R.prewarm_shadow(st.species_id, traits_sc, sh_hd)
                            did = True
                    # r24 实测:让路节拍从 0.001 调到 0.004 对主线程 p90 无
                    # 改善(仍 ~16ms)——瓶颈是单桶 PIL 旋转 ~7ms 持 GIL 期间
                    # 主线程 Python 段无法推进,非 sleep 间隔。保留 0.001
                    # (预热带宽最大);预热本身已把 p50 从 10.8 砍到 4.4ms。
                    time.sleep((0.001 if len(handles) <= 2 else 0.012) if did else 0.02)
            except Exception as exc:
                print(f"[app] 躯干跟随预热退出: {exc!r}")
        for i in range(2):
            threading.Thread(target=worker, args=(i,), daemon=True,
                             name=f"torso-prewarm-{i}").start()

    def _step(self, dt: float) -> None:
        from neuropet.perception.mouse import compute_stimuli
        from neuropet.body.base import emo_gait_on   # B2:回退开关(单一真源)
        # r25 A4:熟练度重算(2s 节拍;仅整数等级变化才写 brain,见 _mastery_beat)
        self._mastery_acc += dt
        if self._mastery_acc >= MASTERY_BEAT_S:
            self._mastery_acc = 0.0
            self._mastery_beat()
        for h in list(self.pets.values()):
            st = h.state
            st.age_s += dt
            now_t = time.perf_counter()
            if now_t - self._screen_event_t >= 2.0:      # E4:≥2s 刷新窗口事件
                self._screen_event_t = now_t
                try:
                    self._screen_events = tuple(self._screen_mon.diff_events())
                except Exception:
                    self._screen_events = ()
            fg = self._screen_mon.foreground()
            view = self.world.snapshot(
                h.pet_id, now_t,
                fg_window_rect=(fg.rect if fg is not None else None),
                window_events=self._screen_events,
                region_aware=True)
            if now_t - self._fx_tick_t >= 1.0:           # B4:效果墙钟到期回收
                self._fx_tick_t = now_t
                eng = self._fx.get(h.pet_id)
                if eng is not None:
                    for cmd in eng.tick(time.time()):
                        self._apply_effect(h, cmd)
            stimuli = compute_stimuli(st, view, self.species(st.species_id).perception_profile())
            h.brain.observe(view, stimuli, dt)
            # B2 情绪→步态:每帧把脑内情绪装配进运动层(单一真源 = brain.emotion();
            # 无 UI 暴露,只从行为泄漏)。插件 body 无此方法则跳过;通道在阈值
            # 以下恒惰性(见 body/base.py 模块头 EMO_* 常量)。三条护栏:
            # ① `off` 判定在守卫**之前**:回退开关的生效不许取决于上游数据是否
            #    健康(旧实现脏值 ⇒ 跳过装配 ⇒ off 根本没被调用 = 回退承诺失效);
            # ② 脏值/异常 ⇒ **有界保持**(≤EMO_HOLD_MAX_S)后回中性:一次瞬时脏
            #    读数不抖动,也不无界陈旧(本方法是这些字段的唯一写者);
            # ③ 守卫在 `emotion()` 的**调用面**(`emo_read`):抛异常/None/脏值
            #    都不逃逸到帧循环,面板读数同源(`pet_status`)。
            _emo_set = getattr(h.body, "set_emotion_gait", None)
            if _emo_set is not None:
                if not emo_gait_on():
                    self._emo_hold.pop(h.pet_id, None)
                    _emo_set(0.0, 0.0, 0.0)          # 中性 = 改动前行为(逐位)
                else:
                    _em, _ch = emo_read(h.brain)
                    if _ch is not None:
                        self._emo_hold[h.pet_id] = (now_t, _ch)
                        _emo_set(*_ch)
                    else:
                        _held = self._emo_hold.get(h.pet_id)
                        if (_held is not None
                                and now_t - _held[0] <= EMO_HOLD_MAX_S):
                            _emo_set(*_held[1])      # 有界保持:≤T
                        else:
                            self._emo_hold.pop(h.pet_id, None)
                            _emo_set(0.0, 0.0, 0.0)  # 超时 ⇒ 回中性(无陈旧)
                        self._emo_diag(h.pet_id, now_t)
            self._kernel.begin_pet(st)
            if st.held:
                st.speed = 0.0
                self._kernel.run_stage("held_tick", h, dt)
                self._kernel.apply_pos(st)
                continue
            if st.frozen:
                st.activity = Behavior.FROZEN
                st.speed = 0.0
                self._kernel.run_stage("frozen_tick", h, dt)
                self._kernel.apply_pos(st)
                continue
            cmd = h.brain.decide(view)
            cmd2 = self._kernel.rewrite_command(h, cmd, dt)   # ADR-0031 命令管道
            h.body.apply(cmd2 if cmd2 is not None else cmd, view, dt)
            self._kernel.run_stage("post_apply", h, dt)
            self._kernel.apply_pos(st)     # 每帧一次位置权威合成(F4)
            self._kernel.run_stage("post_move", h, dt)
            self._try_eat(h)
            st.stomach = max(0.0, st.stomach - 0.004 * dt)
            if st.stomach <= 0.15:
                h.brain.on_event("hungry", {"stomach": st.stomach})

    def _emo_diag(self, pet_id: str, now_t: float) -> None:
        """B2:情绪读数不健康(异常/None/脏值)的**限频**诊断(≤1 行/10s)。

        不走 `log_exc`——那不是"异常逃逸"(帧照常推进,`_tick` 的 except 出口
        只记真异常),但也不能静默:插件脑违约是上游 bug 的信号(§20 同契约)。
        每帧一行会刷爆 diag.log,故按 EMO_DIAG_MIN_GAP_S 限频。
        """
        if now_t - self._emo_log_t < EMO_DIAG_MIN_GAP_S:
            return
        self._emo_log_t = now_t
        try:
            from neuropet.diag import log
            log(f"[emo] {pet_id}: emotion() 无健康读数(异常/None/脏值)"
                f" → 有界保持 {EMO_HOLD_MAX_S}s 后回中性")
        except Exception:
            pass                  # 诊断自身永不抛(tk 回调链上不得二次伤害)

    def _try_eat(self, h: PetHandle) -> None:
        st = h.state
        if st.activity != Behavior.EAT or st.stomach > 0.95:
            return
        # 取食半径随身体尺寸走(至少 52px):留白随体型放大后,钉在边缘的食物
        # 若还用固定 52px 就永远够不到,宠物会对着食物"想吃吃不到"。
        reach = max(52.0, self._margin_for(h))
        f = self.world.nearest_food(st.pos, max_r=reach)
        if f:
            from neuropet.feeding import FOODS, food_modifiers
            mods = food_modifiers(st.species_id, f.kind)
            done = self.world.eat_food(f.food_id, bite=0.34 * mods["bite_mult"])
            spec = FOODS.get(f.kind)
            st.stomach = min(1.0, st.stomach + float(
                spec.nutrition if spec is not None else 0.18))
            h.brain.on_event("fed", {"food_kind": f.kind})
            self.bus.publish("user/fed_effect", {"pet_id": h.pet_id,
                                                 "food": f.food_id,
                                                 "food_kind": f.kind})
            self._static_dirty = True
            self._consume_food_effects(h, f.kind)

    def _fx_of(self, pet_id: str):
        from neuropet.feeding import EffectEngine
        return self._fx.setdefault(pet_id, EffectEngine())

    def _consume_food_effects(self, h: "PetHandle", kind: str) -> None:
        eng = self._fx_of(h.pet_id)
        for cmd in eng.apply(kind, now=time.time(),
                             species_id=h.state.species_id):
            self._apply_effect(h, cmd)

    def _apply_effect(self, h: "PetHandle", cmd) -> None:
        '''EffectCommand 分发(feeding.py 协议;Q 版/虹色渲染半场走 traits)。'''
        from neuropet.feeding import (ChibiToggle, EffectExpired, Iridescence,
                                      ScaleChange, SpeedMultiplier, TrustBoost,
                                      UnlockNotice)
        if isinstance(cmd, ScaleChange):
            self.set_pet_scale(h.pet_id, h.scale + cmd.delta)
        elif isinstance(cmd, SpeedMultiplier):
            self._speed_mult[h.pet_id] = float(cmd.mult)
            self._rebuild_with_speed(h)
        elif isinstance(cmd, ChibiToggle):
            h.chibi = bool(cmd.on)
            self._static_dirty = True
        elif isinstance(cmd, Iridescence):
            h.iridescent = bool(cmd.on)
            self._static_dirty = True
        elif isinstance(cmd, TrustBoost):
            td = getattr(h.brain, "trust_dyn", None)
            if td is not None and hasattr(td, "boost"):
                td.boost(cmd.amount, clear_fear=cmd.clear_fear)
        elif isinstance(cmd, EffectExpired):
            if cmd.category == "speed" and self._speed_mult.pop(h.pet_id, None):
                self._rebuild_with_speed(h)
        elif isinstance(cmd, UnlockNotice):
            from neuropet.feeding import FOODS
            spec = FOODS.get(cmd.kind)
            label = spec.label if spec is not None else cmd.kind
            epi = getattr(h.brain, "episodic", None)
            if epi is not None and hasattr(epi, "remember"):
                epi.remember("第一次吃到" + label + ",身体起了变化!", salience=0.8)
            # C2:变身气泡与情景记忆同帧(同一分支先后执行,时机一致)
            self.bus.publish("user/transform",
                             {"pet_id": h.pet_id, "kind": cmd.kind, "label": label})

    def _rebuild_with_speed(self, h: "PetHandle") -> None:
        '''速度 buff 重建 body(set_pet_scale 同路径;到期/顶替双入口)。

        重建后立即重新套用"全局倍率 × buff"(``_build_body`` 已套一次全局,
        这里补 buff 的那一次 —— 两者都从基准参数算,不叠乘)。'''
        sp = self.species(h.state.species_id)
        h.body = self._build_body(sp, h.state, h.scale)
        h.last_disp_sig = None
        h.last_img = None
        h.last_coords = None
        h.last_cost_ms = 0.0

    def _render(self, dt: float) -> None:
        import math
        from neuropet.render.renderer import render_pose
        from neuropet.render import torso_art
        now = time.perf_counter()
        t0 = time.perf_counter()             # 渲染段计时(全部宠物 render+贴台)
        # 渲染级功能钩子(ADR-0031 管道位;遮挡门控已随 ADR-0034 退役)。
        self._kernel.run_stage("render")
        # AG5 渲染配额(前瞻):>2 只时每帧最多渲染 RENDER_QUOTA 只,其余跳帧。
        # 轮转游标每帧前进 QUOTA 个槽位,保证任一宠物至多饿 quota-1 个轮转位;
        # ≤2 只时配额永不截流(默认双宠场景行为与旧版完全一致)。
        pets = list(self.pets.values())
        self.stage.begin_frame()
        self.stage.move_pets({h.pet_id: h.state.pos for h in pets})
        # v0.2.0 高速拖尾:每帧按**真实位移速度**记录历史点。走的是画布矢量
        # 线项(非图像),不占渲染配额、不进显示签名,也不影响宠物上传去重。
        trail = self.stage.trail
        if trail.enabled:
            for h in pets:
                st = h.state
                try:
                    half = float(h.body.window_half())
                except Exception:
                    half = 0.0
                trail.update(h.pet_id, st.pos[0], st.pos[1], st.speed,
                             half, dt, now)
        quota = RENDER_QUOTA if len(pets) <= 3 else 4
        order = quota_rotation(len(pets), self._render_cursor, quota)
        # If every candidate is skipped, still rotate the starting point. A
        # consumed slot below overrides this with the last serviced position.
        self._render_cursor += 1
        quota_left = quota
        for i in order:
            h = pets[i]
            st = h.state
            # 自适应帧率(PF 快赢③:双阈值回差 + 档位最短驻留)。快速移动/飞行/
            # 抓握 60fps;慢速/动画 30fps;静止/冻结 10fps。进入用高阈值
            # (>40)、驻留用低阈值(>30),speed 边界抖动不再造成档位乒乓;
            # 抓握/飞行入 60fps 档不受驻留限制(交互与飞行表现优先)。
            desired = frame_tier_next(
                h.frame_tier, st.speed, st.mode == MovementMode.FLY, st.held,
                st.activity in (Behavior.GROOM, Behavior.EAT),
                h.body._turn_norm * 90.0)
            # r24:多宠时封顶 30fps 档(desired 越大越慢,取 max)。hybrid 底盘
            # 全合成实测 ~8ms(p50)/17ms(p90)/宠——2 只都进 60fps 档时最坏帧
            # 2×~8ms 恰好顶满 16.7ms 预算,实测稳态 12% 帧 >16ms(肉眼卡顿);
            # 30fps 档下 2 宠/33ms 才有余量。单宠不受限(仍可 60fps)。
            if len(pets) > 1 and desired < 1:
                desired = 1
            if desired != h.frame_tier:
                if (desired == 0 and (st.held or st.mode == MovementMode.FLY)) \
                        or now - h.tier_since >= TIER_DWELL_S:
                    h.frame_tier = desired
                    h.tier_since = now
            interval = (0.016, 0.033, 0.1)[h.frame_tier]
            # PF:重帧退避 —— 上次该宠 render_pose >9ms 时降到 ≤30fps,防重帧
            # 背靠背连续超预算。r24:由**单帧**退避改为**滞回**退避(重帧后维持
            # 18 帧 ≈0.3s 的 30fps)。实测稳态仍有 ~12% 帧 >16ms(底盘全合成
            # 本征成本,非桶缺失),单帧退避下重帧会背靠背交替出现(60→30→60
            # 抖);滞回让重负载期稳定在 30fps,视觉更平顺(30fps 对行走宠
            # 观感无感,而掉帧/抖动可感)。代价:轻载下偶发多付 0.3s 的 30fps。
            if h.last_cost_ms > 9.0:
                h.cost_backoff = 18
            if h.cost_backoff > 0:
                h.cost_backoff -= 1
                interval = max(interval, 0.033)
            if now - h.last_render < interval:
                continue                      # 未到期:不占配额
            if len(pets) > 2 and (quota_left <= 0 or (quota_left < quota and (time.perf_counter()-t0) > .012)):
                continue                      # 配额用尽:本帧跳过(不推进 last_render)
            pose = h.body.pose()
            ov = self._kernel.drag_overlay(h)   # r10 力学 v2 通道(ADR-0031)
            if ov is not None:
                pose["drag"] = ov
            traits = self._render_traits(h)
            # PF:预测性跳帧 —— 该朝向躯干精灵未缓存(首遇 1° 桶 = 一次
            # ~12-13ms 的 480² 旋转重建,足以把整帧拖超预算 2 倍)。本 tick
            # 跳过该宠(画面原地滞留一帧,16ms,不可感),预热线程随即把它
            # 需要的桶备好;连跳 4 帧强制渲染一次,防止极端持续转向下饿死。
            # 查询用基准 traits(L1 桶键与 k 无关,五档共享)。
            if (self._prewarm_on and "cockroach" in st.species_id
                    and h.skip_streak < 4
                    and not torso_art.has_bucket(
                        st.species_id, self._traits_base(h),
                        math.degrees(st.heading) % 360.0,
                        0.0 if st.mode == MovementMode.FLY else 1.0)):
                h.skip_streak += 1
                continue
            # r25 单元 1(记账回滚):配额与延迟钟的记账放在**预测跳帧之后** ——
            # 上面那次 continue 不吃配额槽位(槽位回收给同 tick 的后序宠)、也
            # 不重置自己的延迟钟 ⇒ 下一 tick 立即重试,不必再等一整个 interval。
            # ⚠ 槽位口径 = 过了 interval 门且未被跳帧的宠数;dedup 命中仍吃槽(未变)。
            quota_left -= 1
            self._render_cursor = i + 1
            h.last_render = now
            h.skip_streak = 0
            # PF 快赢①:上传去重 —— 显示(签名+坐标)与上次实际上传一致时,
            # 画布逐位不变,整体跳过 render_pose 重绘与 PhotoImage 重建/上传
            # (调研 §A2-b:省 2.6~3.4ms/宠)。仅 hybrid 管线(legacy 无 OPT-7)。
            # 门控:只有冻结/抓握宠的 pose 可能逐位静止(apply 停推进;站立宠
            # 触角按拟真设计 0.7Hz 慢扫,签名必然不命中),活动宠直接跳过检查
            # 本身,主路径零额外开销;坐标整数预检不等时也不必查签名。
            # 判据 A:OPT-7 签名命中且缓存对象==上次上传对象 → 正常路径也会
            #   返回同一对象,跳过与执行严格等价(与现管线显示语义一致);
            # 判据 B:完整显示签名精确相等(含 OPT-7 未覆盖的 alert/sprint/
            #   pitch/bones 键)→ 渲染输出逐位相等,OPT-7 条目被挤出也能跳。
            # 首帧 last_disp_sig/last_img 为 None 必画;记账只发生在真正上传前,
            # 预测跳帧(上面 continue)不会污染。
            ix, iy = int(st.pos[0]), int(st.pos[1])
            sig = None
            if (self._prewarm_on and (st.frozen or st.held)
                    and h.last_img is not None and (ix, iy) == h.last_coords):
                from neuropet.render import renderer as _RR
                ss = 2 if _RR._hy_level >= 3 else 3
                if _RR._OPT7_CACHE.get(
                        _RR._pose_signature(pose, traits, ss)) is h.last_img:
                    h.last_cost_ms = 0.0
                    self._dedup_skips += 1
                    continue
                sig = display_signature(pose, traits, st.pos[0], st.pos[1], ss)
                if sig == h.last_disp_sig:
                    h.last_cost_ms = 0.0
                    self._dedup_skips += 1
                    continue
            h.last_disp_sig = sig
            t_r = time.perf_counter()
            img = render_pose(pose, traits)
            h.last_cost_ms = (time.perf_counter() - t_r) * 1000.0
            self.stage.update_pet(h.pet_id, st.pos[0], st.pos[1], img)
            self._upload_count += 1
            h.last_img = img
            h.last_coords = (ix, iy)
        if self._static_dirty:
            self.stage.render_static(self.world.foods, self.world.zones)
            self._static_dirty = False
        self.stage.end_frame()
        self.stage.fit_viewport()
        _ms = (time.perf_counter() - t0) * 1000.0
        self._frame_ms.append(_ms)
        _I.observe("render_ms", _ms)

    # ---------------- C2 气泡引导(触发时机表:调研文档 §5.3) ----------------
    def render_stats(self) -> dict:
        """渲染段耗时统计(最近 120 帧环形窗;P50/P95 单位 ms,供面板/调试)。

        附带上传去重计数(PF 快赢①):uploads=实际 update_pet 次数,
        dedup_skips=签名命中跳过次数(跳过重绘+上传)。"""
        xs = sorted(self._frame_ms)
        n = len(xs)
        if not n:
            return {"n": 0, "p50": 0.0, "p95": 0.0, "max": 0.0, "last": 0.0,
                    "uploads": self._upload_count,
                    "dedup_skips": self._dedup_skips}
        return {"n": n, "p50": xs[n // 2],
                "p95": xs[min(n - 1, int(n * 0.95))],
                "max": xs[-1], "last": xs[-1],
                "uploads": self._upload_count,
                "dedup_skips": self._dedup_skips}

    # ---------------- 交互:抓取/拖拽(共享舞台命中) ----------------
    def _on_press(self, event) -> None:
        # Real Tk events provide screen coordinates; offline fixtures historically
        # supply already-global x/y only.
        event.x_root = getattr(event, "x_root", event.x)
        event.y_root = getattr(event, "y_root", event.y)
        pid = self.stage.hit_pet(event.x_root, event.y_root, self.pets)
        if not pid:
            return
        h = self.pets[pid]
        h.drag_offset = (h.state.pos[0] - event.x_root, h.state.pos[1] - event.y_root)
        self._kernel.feature("drag").clear_intent(pid)   # 新抓取:清陈旧意向
        if not h.state.frozen:
            h.state.held = True
            h.brain.on_event("grab", {"frozen": False})
            self.bus.publish("user/grab", {"pet_id": pid})

    def _on_drag(self, event) -> None:
        event.x_root = getattr(event, "x_root", event.x)
        event.y_root = getattr(event, "y_root", event.y)
        pid = self.stage.hit_pet(event.x_root, event.y_root, self.pets)
        pid = pid or self._drag_pid
        if not pid:
            return
        h = self.pets[pid]
        self._drag_pid = pid
        # ADR-0031:位置不再直写 st.pos —— 意向交 DragFeature,经仲裁器
        # 每帧合成(held/frozen 分支 apply_pos;与其他写者显式定序)。
        # 边界留白用该宠自己的画布半径 ⇒ 拖拽与身体软墙是同一个矩形,
        # 不会再出现"拖到边缘后宠物落后一段/松手弹回"。
        self._kernel.feature("drag").on_drag(
            pid, self.world.clamp_to_screen(
                (event.x_root + h.drag_offset[0], event.y_root + h.drag_offset[1]),
                self._margin_for(h)))

    def _on_release(self, event) -> None:
        pid = getattr(self, "_drag_pid", None)
        self._drag_pid = None
        if not pid or pid not in self.pets:
            return
        h = self.pets[pid]
        self._kernel.feature("drag").clear_intent(pid)
        if h.state.held:
            h.state.held = False
            h.brain.on_event("released", {"dropped_from": list(h.state.pos)})
            self.bus.publish("user/grab_release", {"pet_id": pid})

    _drag_pid: str | None = None

    # ---------------- ADR-0031/0034:拖拽动力学已插件化(features/drag.py) ----------------
    # hide 已整体剔除(ADR-0034);本文件不再内联任何位置权威写者,
    # pos 写入一律经 StateArbiter 每帧合成。

    # ---------------- 面板能力 ----------------
    def trigger_behavior(self, pet_id: str, behavior_name: str) -> None:
        h = self.pets.get(pet_id)
        if not h:
            return
        h.brain.inject_command(BehaviorCommand(behavior=Behavior(behavior_name),
                                               priority=50, reason="面板触发"))
        self.bus.publish("user/behavior_trigger", {"pet_id": pet_id, "behavior": behavior_name})

    def escape_test(self, pet_id: str) -> None:
        import math
        import random
        h = self.pets.get(pet_id)
        if not h:
            return
        ang = random.uniform(0, 6.283)
        src = (h.state.pos[0] - math.cos(ang) * 200, h.state.pos[1] - math.sin(ang) * 200)
        h.brain.on_event("external_stimulus", {"kind": StimulusKind.WIND.value,
                                               "intensity": 0.95, "source_pos": list(src)})
        self.bus.publish("user/escape_test", {"pet_id": pet_id})

    def set_frozen(self, pet_id: str, frozen: bool) -> None:
        h = self.pets.get(pet_id)
        if not h:
            return
        h.state.frozen = frozen
        h.state.held = False
        h.brain.on_event("freeze_on" if frozen else "freeze_off", {})
        self.bus.publish("user/freeze", {"pet_id": pet_id, "frozen": frozen})

    def set_intelligence(self, pet_id: str | None, level: int) -> None:
        """手动/插件入口:设置等级并写 cfg(``save_config``)——行为与 A4 之前一致。

        **语义(勿踩)**:``NEUROPET_MASTERY`` 开着时,本入口的**下调**是瞬态的
        —— ``_mastery_beat`` 每 2s 按经历重算,派生值高于本次设置值时会在 ≤1 个
        节拍内把它**抬回去**(那是设计好的 ratchet:自动路径只上调、不下调,
        防"降级→容量缩→trim 删记忆"的正反馈);只有**上调**是永久的(成为
        下界,且随 cfg 落盘跨重启)。调试/插件要**冻住**等级,用回退开关
        ``NEUROPET_MASTERY=off``(**停止自适应**:脑保持当前等级,节拍零写入;
        非旧语义"回退到 cfg"——那个会降级删记忆,见 ``_mastery_beat``)。
        """
        targets = [self.pets[pet_id]] if pet_id else list(self.pets.values())
        for h in targets:
            h.brain.set_intelligence(max(1, min(5, int(level))))
        self.cfg.intelligence = max(1, min(5, int(level)))
        save_config(self.cfg)
        self.bus.publish("user/intelligence", {"level": self.cfg.intelligence})

    # ---------------- r25 A4:熟练度(自适应等级 + 可感知读数) ----------------
    def _derived_level(self, pet_id: str) -> int | None:
        """按**经历统计**派生等级(``brain/mastery.py`` 纯函数的唯一运行期入口)。

        ``None`` = 宠物不存在或统计取不出来(调用方自行兜底)。
        **只给 ``_mastery_beat`` 的 on 路径用**:它不是读数 —— 读数
        (``mastery_of``)必须返回**脑内实跑等级**,否则又会出现"读数 2、
        脑实际跑 5"的分歧(r25 A4-r2 裁决 ②)。
        """
        h = self.pets.get(pet_id)
        if h is None:
            return None
        try:
            n_mem = len(self.memory_of(pet_id))
        except Exception:
            n_mem = 0                  # 坏插件脑:统计取不出 → 按 0 计(不派高)
        return mastery_level({
            "n_memory": n_mem,
            "trust": getattr(h.brain, "_trust", 0.0),
            "age_s": h.state.as_summary().get("age_s", 0.0),
            "n_fed": getattr(h.brain, "_n_fed", 0),
            "n_danger": getattr(h.brain, "_n_danger", 0),
        })

    def mastery_of(self, pet_id: str) -> tuple[int, str]:
        """返回 ``(等级 1..5, 一行中文读数)``;面板「看它」页读数行的唯一数据源。

        **等级 = 脑内实际等级**(``brain.level``,单一真源)—— 不是派生值、也
        不是 ``cfg.intelligence``。等级决定记忆容量/学习增益/探索温度,读数若
        取自别处就会出现"读数与实跑不一致"这一整类分歧,r25 A4-r2 裁决 ②
        要求消除它(前身实现:on 态读数 = 派生值,实测可"读数 2、脑跑 5";
        off 态读数 = cfg,实测可"读数 4、脑跑 5")。派生值只是"下次要不要抬档"
        的输入,见 ``_mastery_beat``;写进脑之后读数立刻跟上。
        唯一例外:未选中的占位读数 ``"(未选择宠物)"``(没有脑可读)→ 返回
        ``cfg.intelligence``(新宠种子),面板只用于显示"无选中"。

        ⚠ ``NEUROPET_MASTERY=off`` 不改本方法的取值口径(仍是脑内等级):
        off = **停止自适应**,不写脑,故读数自然稳定在脑的当前值。
        """
        h = self.pets.get(pet_id)
        if h is None:
            return max(1, min(5, int(self.cfg.intelligence))), "(未选择宠物)"
        try:
            n_mem = len(self.memory_of(pet_id))
        except Exception:
            n_mem = 0
        learned = "-"
        try:
            learned = learned_from_summary(h.brain.learning_summary())
        except Exception:
            pass                       # 插件脑无该方法 → 读数行降级为 "-"
        try:
            v = max(1, min(5, int(getattr(h.brain, "level"))))
        except Exception:
            v = max(1, min(5, int(self.cfg.intelligence)))  # 坏插件脑:兜底基线
        return v, readout(v, n_mem, learned)

    def _mastery_beat(self) -> None:
        """2s 节拍:重算熟练度,把等级**只往上调**(ratchet),写的只有 ``brain``。

        沿用现有传播链(brain.set_intelligence → episodic/assoc/bandit 同步)。
        **不写回 ``cfg.intelligence``、不调 ``save_config``**(r25 A4 收口):
          - ``cfg.intelligence`` 语义 = 熟练度**基线(初始值)**,派生值不该把基线冲掉;
          - 派生等级可由已持久化的 stats(``n_memory``/``trust``/``age_s``/
            ``n_fed``/``n_danger`` 随 brain 与档案落盘)完整复现 → 不落盘不丢信息。
            ⚠ 该论证的前提是 ``age_s`` **确实落盘**(r25 A4-fix 才成立:此前
            年龄不落盘,重启归零 → 等级必降 → ``trim()`` 永久删记忆);
          - 旧办法会让任何驱动真 App ≥2s 的测试**静默重写受版本控制的
            ``data/config.json``**(污染提交 + 测试间顺序依赖),多宠时还会被
            最后一只宠覆盖。

        **只上调不下调(r25 A4-fix,黑盒缺陷 A/B)**:派生值 ≤ 当前等级时一律
        不写。下调会经 ``EpisodicMemory.set_level→trim()``/``AssocMemory.trim()``
        **永久删除**记忆,并形成「等级降 → 容量缩 → 记忆计数降 → 等级再降」的
        正反馈螺旋;桌宠"离开一段时间回来发现失忆掉档"也是错误的产品行为。
        下调只留给手动入口 ``app.set_intelligence``(插件/调试 API,权限不变);
        要"钉住"等级用 ``NEUROPET_MASTERY=off``。

        off 态(回退开关)= **停止自适应**(r25 A4-r2 裁决,语义已改):本方法
        立即返回、**一个等级都不写、不碰记忆**。原语义是"回退到
        ``cfg.intelligence``"(把脑内等级**下调**对齐回基线),实测**破坏性**:
        基线 2 + 档案 L5/220 条 → 载入后首拍对齐回 2 →
        ``EpisodicMemory.set_level→trim()`` 按 cap(2)=40 **静默删 180 条**
        (黑盒探针 P12:220 → 40)。回退开关的存在意义是**安全** —— 用户出于
        诊断设一个环境变量,代价不该是宠物的记忆,故 ratchet 对 on/off
        **一视同仁**:任何自动路径都不下调等级、不触发记忆删除。off 只表示
        "别再自适应",档位由手动 ``set_intelligence`` 或新宠种子决定,读数取
        脑内值(``mastery_of``)。写路径与读路径同级 try 保护:坏插件脑抛异常
        不得直穿 ``_step``(黑盒缺陷 C)。
        """
        if not mastery_enabled():
            return                     # 停止自适应:零写入(见 docstring)
        for pid in list(self.pets):
            h = self.pets.get(pid)
            if h is None:
                continue               # 2s 窗口内被移除
            try:
                v = self._derived_level(pid)
                if v is None or v <= int(getattr(h.brain, "level", v)):
                    continue           # 未跨整数等级,或派生值更低(ratchet):不写
                h.brain.set_intelligence(v)     # 只写 brain:自动派生不碰 cfg、不落盘
            except Exception:
                continue               # 坏插件脑(level 非数/set_intelligence 抛):
                                       # 与读路径同级 try,不打断整帧

    def toggle_feeding(self, on: bool) -> bool:
        self.feeding = on
        # 限时自动退出:ttl>0 记录截止时刻;ttl<=0 → None 表示不限时
        if on:
            ttl = self.cfg.feeding_timeout_s
            self._feeding_until = time.perf_counter() + ttl if ttl and ttl > 0 else None
            self._refresh_panel_rect()   # 立即刷新穿透缓存,首击即生效
        else:
            self._feeding_until = None   # 关闭时清空限时
        if on and self._hook is None:
            try:
                from .hook import LLMouseHook
                try:    # 兼容并行 hotfix:hook 增加 is_passthrough 参数前也能跑
                    self._hook = LLMouseHook(on_left_click=self.drop_food,
                                             is_passthrough=self._panel_contains)
                except TypeError:
                    self._hook = LLMouseHook(on_left_click=self.drop_food)
                self._hook.start()
            except Exception as exc:
                print(f"[app] 鼠标钩子不可用,投喂模式无法全局捕获点击: {exc}")
                self.feeding = False
                self._feeding_until = None
                if self._panel:
                    self._panel.set_feeding(False)
                return False
        if self._hook:
            self._hook.set_capture(on)
        self.bus.publish("user/feeding", {"on": on})
        if self._panel:
            # 同步勾选框(BooleanVar.set 不触发 command,不会反向写回);
            # 超时自动关闭时靠这里把面板勾选复位
            self._panel.set_feeding(self.feeding)
        return self.feeding

    # ---------------- 界面语言(v0.1.1) ----------------
    def set_language(self, code: str) -> str:
        """切换界面语言并立即生效:存配置 → 面板就地刷新 → 托盘同步。

        不重建窗口、不重启托盘线程,不动任何宠物状态(选中项、编号、暂停与
        隐藏态、打开的可选窗口及其关联宠物都原样保留)。返回生效的语言码。"""
        lang = set_lang(code)
        self.cfg.language = lang
        save_config(self.cfg)
        if self._panel:
            self._panel.apply_language()
        if self._tray:
            self._tray.refresh_language()
        return lang

    # ---------------- 面板穿透缓存(钩子线程只读) ----------------
    def _refresh_panel_rect(self) -> None:
        """主线程刷新面板矩形/可见性缓存(供 _panel_contains 只读)。

        缓存含 PANEL_RECT_PAD 外扩余量;面板隐藏/销毁时按不可见处理。
        写入顺序:先 rect 后 visible —— 保证钩子线程读到 visible=True 时
        rect 一定是有效值(反之先 False 再清 rect)。

        v0.2.0:穿透区域从"主面板 + 已登记的可选窗口"扩到**本进程的全部 Tk
        顶层窗口**,因此 ttk 下拉的弹出层(独立原生窗 ``.!tk::combobox::Popdown``,
        不在 ``extra_windows`` 里)也会被放行 —— 否则投喂模式的全局鼠标钩子
        会把点击下拉项当成"点在桌面上"而吞掉。枚举在主线程做(钩子线程禁止
        调 Tk),透明舞台被显式排除,否则整屏都成了穿透区、投喂模式失效。
        """
        panel = self._panel
        if panel is None:
            self._panel_visible = False
            self._panel_rect = None
            return
        try:
            win = panel.win
            if not win.winfo_exists() or not win.winfo_viewable():
                self._panel_visible = False
                self._panel_rect = None
                return
            x0 = int(win.winfo_rootx()) - PANEL_RECT_PAD
            y0 = int(win.winfo_rooty()) - PANEL_RECT_PAD
            x1 = x0 + int(win.winfo_width()) + PANEL_RECT_PAD * 2
            y1 = y0 + int(win.winfo_height()) + PANEL_RECT_PAD * 2
            self._panel_rect = (x0, y0, x1, y1)
            self._panel_extra_rects = self._own_toplevel_rects()
            self._panel_visible = True
        except Exception:
            # 窗口销毁中等 tkinter 异常:一律按不可见处理
            self._panel_visible = False
            self._panel_rect = None

    def _own_toplevel_rects(self) -> tuple:
        """本进程可见 Tk 顶层窗口矩形(排除透明舞台),供点击穿透判定。

        ``winfo children .`` 只在**主线程**调用(钩子线程不碰 Tk);每个矩形
        读 4 个 winfo,每 0.5s 一次,开销可忽略。
        """
        try:
            names = self.root.tk.call("winfo", "children", ".")
        except Exception:
            return ()
        try:
            skip = int(self.stage.win.winfo_id())
        except Exception:
            skip = None
        rects = []
        for name in names or ():
            try:
                widget = self.root.nametowidget(name)
                if not widget.winfo_exists() or not widget.winfo_viewable():
                    continue
                if skip is not None and int(widget.winfo_id()) == skip:
                    continue
                x, y = int(widget.winfo_rootx()), int(widget.winfo_rooty())
                width, height = int(widget.winfo_width()), int(widget.winfo_height())
            except Exception:
                continue
            if width <= 0 or height <= 0:
                continue
            rects.append((x - PANEL_RECT_PAD, y - PANEL_RECT_PAD,
                          x + width + PANEL_RECT_PAD, y + height + PANEL_RECT_PAD))
        return tuple(rects)

    def _panel_contains(self, x: int, y: int) -> bool:
        """点 (x, y) 是否落在本程序的界面上(钩子线程调用:禁止 tkinter 调用)。

        返回 True 的点钩子不吞左键 → 面板、设置窗口和 ttk 下拉的弹出层都能
        正常点击(投喂模式不被全局拦截)。

        判据两层:① 主线程缓存的矩形(面板 + 全部可见顶层窗,0.5s 节流);
        ② **同进程命中判断** —— 纯 Win32 ``WindowFromPoint`` +
        ``GetWindowThreadProcessId``,精确无缓存延迟,所以刚弹出的下拉层第一
        次点击就能放行;透明色键舞台被显式排除(它铺满全屏,若当成"自己人",
        投喂模式会整体失效)。第 ② 层不碰 Tk,可在钩子线程安全调用。
        """
        if self._panel_visible:
            rect = self._panel_rect    # 元组原子读,线程安全
            if rect is not None:
                x0, y0, x1, y1 = rect
                if x0 <= x <= x1 and y0 <= y <= y1:
                    return True
            for ex0, ey0, ex1, ey1 in getattr(self, "_panel_extra_rects", ()):
                if ex0 <= x <= ex1 and ey0 <= y <= ey1:
                    return True
        return self._own_ui_window_at(x, y)

    def _own_ui_window_at(self, x: int, y: int) -> bool:
        """点 (x,y) 最顶层的窗口是否属于本进程且不是透明舞台(纯 Win32)。"""
        try:
            import ctypes
            from ctypes import wintypes

            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

            user32 = ctypes.windll.user32
            user32.WindowFromPoint.argtypes = [wintypes.POINT]
            user32.WindowFromPoint.restype = wintypes.HWND
            hwnd = user32.WindowFromPoint(POINT(int(x), int(y)))
            if not hwnd:
                return False
            stage = self._stage_hwnd
            if stage and int(hwnd) == int(stage):
                return False                      # 透明色键舞台:不当自己人
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
            return pid.value == self._own_pid
        except Exception:
            return False

    def drop_food(self, x: int, y: int, kind: str | None = None) -> None:
        import random as _random
        from neuropet.feeding import FOODS, roll_food
        pos = self.world.clamp_to_screen((float(x), float(y)), margin=30)
        if kind is not None and kind in FOODS:   # r10:面板指定种类;否则抽签
            spec = FOODS[kind]
        else:
            spec = roll_food(_random.Random())   # 投喂的"讲究":种类/颜色/效果抽签
        self.world.add_food(pos, kind=spec.kind)
        self._static_dirty = True
        self.bus.publish_threaded("user/feed_drop", {"pos": list(pos)})

    def _on_panel_drop(self, kind: str) -> None:
        """面板投喂选择器:按选中种类在宠物前下方落食(无宠物则屏中心)。"""
        h = None
        try:
            sel = getattr(self._panel, "selected", None)
            pid = sel() if callable(sel) else None
            h = self.pets.get(pid) if pid else None
        except Exception:
            h = None
        if h is None:
            h = next(iter(self.pets.values()), None)
        if h is not None:
            x, y = h.state.pos[0] + 60.0, h.state.pos[1] + 40.0
        else:
            x, y = self.world.screen[0] / 2.0, self.world.screen[1] / 2.0
        self.drop_food(int(x), int(y), kind=kind)
        self.bus.publish("user/panel_feed", {"kind": kind})

    def place_cold_zone(self, pos=None) -> None:
        if pos is None:
            pos = self._cursor_getter() or (self.world.screen[0] / 2, self.world.screen[1] / 2)
        self.world.add_zone("cold", self.world.clamp_to_screen(pos))
        self._static_dirty = True
        self.bus.publish("world/zone_added", {"kind": "cold"})

    def tell_story(self, story_id: str = "snow_beautiful") -> None:
        for h in self.pets.values():
            h.brain.on_event("story", {"id": story_id, "about": "cold",
                                       "valence": +0.8,
                                       "text": "主人说:冬天的雪景很美"})
        self.bus.publish("story/cold_positive", {"story_id": story_id})

    def memory_of(self, pet_id: str) -> list[str]:
        h = self.pets.get(pet_id) or self._hidden.get(pet_id)
        return h.brain.memory_digest() if h else []

    def clear_memory(self, pet_id: str) -> None:
        h = self.pets.get(pet_id) or self._hidden.get(pet_id)
        if not h:
            return
        h.brain.clear_memory()
        prof = profile_dir(pet_id) / "memory.json"
        if prof.exists():
            prof.unlink()
        self.bus.publish("user/memory_cleared", {"pet_id": pet_id})

    def pet_status(self, pet_id: str) -> dict:
        h = self.pets.get(pet_id)
        if not h:
            return {}
        em, _ch = emo_read(h.brain)      # B2:守卫在 `emotion()` 的调用面
        d = h.state.as_summary()
        # 无健康读数 → 中性字典(不沿用装配线的保持窗:读数是**瞬时观测**,
        # 且读数若续期保持,6Hz 面板会把"有界"变回"无界")。
        d["emotion"] = emo_dict(em) if em is not None else dict(_EMO_NEUTRAL)
        return d

    # ---------------- 持久化 ----------------
    def _load_profile(self, h: PetHandle) -> None:
        av = profile_dir(h.pet_id) / "avatar.json"
        if av.exists():
            try:
                eng = self._fx_of(h.pet_id)
                eng.load(json.loads(av.read_text("utf-8")), now=time.time())
                for cmd in eng.rebuild_commands(time.time()):
                    self._apply_effect(h, cmd)
            except Exception as exc:
                print("[app] avatar 载入失败 " + h.pet_id + ": " + str(exc))
        p = profile_dir(h.pet_id) / "memory.json"
        if p.exists():
            try:
                h.brain.load(json.loads(p.read_text("utf-8")))
            except Exception as exc:
                print(f"[app] 记忆载入失败 {h.pet_id}: {exc}")

    def _save_profile(self, h: PetHandle) -> None:
        p = profile_dir(h.pet_id) / "memory.json"
        try:
            p.write_text(json.dumps(h.brain.save(), ensure_ascii=False, indent=1), "utf-8")
        except Exception as exc:
            print(f"[app] 记忆保存失败 {h.pet_id}: {exc}")
        self._save_scale(h)   # AG5:体型档位随 profile 一并持久化(重启恢复)
        try:
            eng = self._fx.get(h.pet_id)
            if eng is not None:
                av = profile_dir(h.pet_id) / "avatar.json"
                av.write_text(json.dumps(eng.serialize(), ensure_ascii=False),
                              "utf-8")
        except Exception as exc:
            print("[app] avatar 保存失败 " + h.pet_id + ": " + str(exc))

    def clear_effects(self, pet_id: str) -> None:
        '''面板"恢复原形":清 sticky/timed 效果(图鉴/统计保留;体型档不动)。'''
        h = self.pets.get(pet_id)
        eng = self._fx.get(pet_id)
        if h is None or eng is None:
            return
        for cmd in eng.clear():
            self._apply_effect(h, cmd)
        self._save_profile(h)

    def save_all(self) -> None:
        for h in list(self.pets.values()):
            self._save_profile(h)
        for h in self._hidden.values():     # §6:隐藏宠一并持久化(记忆不丢)
            self._save_profile(h)
        self._write_hidden_session()        # 隐藏态清单(重启还原用)
        self._write_roster()                # r16 Task E:退出前名册终写(一致性)

    # ---------------- 退出 ----------------
    def shutdown(self) -> None:
        self._running = False
        self._kernel.unmount_all()   # ADR-0031:功能副作用整组回收(可逆性)
        if self._tray:
            try:
                # 移除托盘图标并停线程(PostMessage 非阻塞 + join 带超时,不卡退出)
                self._tray.stop()
            except Exception as exc:
                print(f"[app] 托盘停止异常: {exc!r}")
        if self._hook:
            self._hook.stop()
        try:
            self._screen_mon.close()
        except Exception:
            pass
        self.save_all()
        try:
            self.stage.destroy()
        finally:
            self.root.destroy()
        print(f"[app] 已退出。最终内存 {working_set_mb():.1f} MB"
              f"(峰值 {getattr(self, '_ws_peak', 0):.1f}); "
              f"渲染段 {self.render_stats()}")

    # ---------------- 工具 ----------------
    @staticmethod
    def _make_cursor_getter():
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        pt = POINT()
        user32 = ctypes.windll.user32

        def get():
            if user32.GetCursorPos(ctypes.byref(pt)):
                return (float(pt.x), float(pt.y))
            return None
        return get
