"""核心数据契约(冻结接口)。

所有跨模块共享的类型集中在此。修改任何字段/枚举必须先写 ADR(docs/),
因为多个并行开发的模块依赖这些定义。

坐标约定:屏幕物理像素(已开启 PER_MONITOR_AWARE_V2 DPI 感知),原点屏幕左上,y 向下。
角度约定:heading 为弧度,0 = 朝右 (+x),顺时针为正(屏幕坐标系)。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StimulusKind(str, Enum):
    WIND = "wind"            # 气流:鼠标快速移动/猛冲产生的"风"
    VIBRATION = "vibration"  # 震动:光标在附近点按、拖拽、高频抖动
    SHADOW = "shadow"        # 视觉阴影:大而快的目标逼近(光标覆盖)
    ODOR_FOOD = "odor_food"  # 食物气味
    ODOR_MATE = "odor_mate"  # 信息素(预留)
    CONTACT = "contact"      # 身体接触(被抓取、碰撞)
    LIGHT = "light"          # 光照(预留:负趋光)
    COLD = "cold"            # 寒冷区域(联想学习演示)
    STORY = "story"          # 叙事/先验注入("听说雪很美")


class Behavior(str, Enum):
    IDLE = "idle"
    EXPLORE = "explore"
    GROOM = "groom"
    SEEK_FOOD = "seek_food"
    EAT = "eat"
    ESCAPE = "escape"          # 逃逸(方向由大脑给出)
    REST = "rest"
    TURN = "turn"
    TAKEOFF = "takeoff"        # 果蝇:起飞
    FLY_WANDER = "fly_wander"  # 果蝇:飞行漫游
    LAND = "land"              # 果蝇:降落
    FROZEN = "frozen"          # 用户冻结(App 层强制,大脑不可决策此行为)
    HELD = "held"              # 正被抓持(App 层强制)
    # ---- ADR-0029:躲藏族(HIDE 的行为子状态,见 docs/架构决策记录ADR.md) ----
    # HIDE = 遁入暗缝(入缝走向 + 锁定贴边 + 触须微摆 + 步态冻结);
    # PEEK = HIDDEN 态的"露头偷看"子状态(前部 ~25% 体长移出遮挡 0.6~1.2s)。
    # 向后兼容:只增枚举成员,旧脑不发射、旧 body 走 _apply_idleish 安全兜底、
    # str 值序列化(activity.value)对旧档案/旧面板透明。
    HIDE = "hide"              # (ADR-0034 休眠:躲藏功能已整体剔除;枚举
    PEEK = "peek"              #  只增不减,旧档案序列化兼容,无发射者/处理器)


class MovementMode(str, Enum):
    CRAWL = "crawl"
    FLY = "fly"
    SWIM = "swim"  # 预留,本期不实现


# ---------- 感知 ----------

@dataclass
class Stimulus:
    kind: StimulusKind
    source: str                 # "cursor" | "food" | "zone" | "system" | "pet:<id>"
    pos: tuple[float, float]    # 刺激源位置
    intensity: float            # 0..1,已被物种敏感度加权前 的客观强度
    direction: tuple[float, float] | None = None  # 刺激来向单位向量(指向刺激源)
    ts: float = field(default_factory=time.perf_counter)
    meta: dict[str, Any] = field(default_factory=dict)


# ---------- 决策 ----------

@dataclass
class BehaviorCommand:
    behavior: Behavior
    target: tuple[float, float] | None = None   # 目标点(探索/觅食/逃逸方向)
    intensity: float = 1.0                      # 0..1 执行强度
    priority: int = 0                           # 越大越优先;App 层只执行最高优先级
    duration: float = 0.0                       # 建议持续时间(s),0 = 由大脑持续更新
    reason: str = ""                            # 调试/展示用,如 "风+气味→危险"


# ---------- 情绪 ----------

@dataclass
class EmotionState:
    fear: float = 0.0        # 恐惧
    hunger: float = 0.3      # 饥饿(初始略饿)
    curiosity: float = 0.5   # 好奇
    anger: float = 0.0       # 愤怒
    trust: float = 0.2       # 对人的信任(投喂增加、抓取减少)
    valence: float = 0.0     # 效价 -1..1(不愉快..愉快)
    arousal: float = 0.2     # 唤醒度 0..1(平静..激动)

    def as_dict(self) -> dict[str, float]:
        return {k: round(getattr(self, k), 3) for k in (
            "fear", "hunger", "curiosity", "anger", "trust", "valence", "arousal")}


# ---------- 记忆 ----------

@dataclass
class MemoryRecord:
    ts: float                  # time.time()
    kind: str                  # "episodic" | "semantic_note"
    summary: str               # 中文摘要,面板可读
    salience: float = 0.5      # 显著度 0..1,决定保留时长
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class Association:
    """语义联想:key 形如 "wind:cursor_rush"、"cold"。
    valence>0 表示该情境与好事关联(趋近),<0 表示危险(回避)。
    学习事件按 delta 更新 valence/weight,weight 越大越难被覆盖。"""
    key: str
    valence: float = 0.0       # -1..1
    weight: float = 0.1        # 0..1 置信度
    count: int = 0             # 累计学习次数


# ---------- 世界对象 ----------

@dataclass
class FoodItem:
    food_id: str
    pos: tuple[float, float]
    amount: float = 1.0                  # 剩余量,吃到 0 移除
    kind: str = "crumb"
    odor_radius: float = 420.0           # 气味可感知半径(px)
    odor_strength: float = 0.9           # 0..1,随 amount 衰减
    created: float = field(default_factory=time.time)


@dataclass
class Zone:
    zone_id: str
    kind: str                    # "cold" | (预留 "warm")
    pos: tuple[float, float]
    radius: float = 220.0
    intensity: float = 0.8       # 刺激强度
    # 该情境的语义联想由大脑的记忆系统维护,Zone 本身只承载物理刺激


# ---------- 宠物状态 ----------

@dataclass
class PetState:
    pet_id: str
    species_id: str
    name: str = ""
    pos: tuple[float, float] = (600.0, 400.0)
    heading: float = 0.0                 # 弧度
    speed: float = 0.0                   # px/s(当前实际)
    altitude: float = 0.0                # 离地高度 px(0=地面;果蝇飞行>0)
    mode: MovementMode = MovementMode.CRAWL
    activity: Behavior = Behavior.IDLE   # 当前表现行为(供展示)
    frozen: bool = False                 # 用户冻结:不动、可拖拽
    held: bool = False                   # 正被用户抓着
    stomach: float = 0.7                 # 饱食度 0..1
    age_s: float = 0.0
    # 运动学细节(腿部关节角、触角、翅)不放在这里,由 body 模块的 Pose 承载

    def as_summary(self) -> dict[str, Any]:
        return {
            "pet_id": self.pet_id, "species": self.species_id, "name": self.name,
            "pos": [round(self.pos[0]), round(self.pos[1])],
            "mode": self.mode.value, "activity": self.activity.value,
            "frozen": self.frozen, "held": self.held,
            "stomach": round(self.stomach, 2), "age_s": round(self.age_s, 1),
        }


# ---------- 光标追踪(感知输入) ----------

@dataclass
class CursorSample:
    t: float
    x: float
    y: float


@dataclass
class CursorKinematics:
    """由光标轨迹计算的运动学特征(perception 模块负责更新)。"""
    x: float = 0.0
    y: float = 0.0
    vx: float = 0.0            # px/s
    vy: float = 0.0
    speed: float = 0.0         # px/s
    accel: float = 0.0         # px/s^2
    gesture: str = "idle"      # idle | approach_slow | approach_fast | rush | jab | retreat | orbit
    near_pet_id: str | None = None
