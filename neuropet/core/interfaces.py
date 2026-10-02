"""插件/模块接口(冻结)。实现方:body、brain、species、perception、ui。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .contracts import (BehaviorCommand, EmotionState, PetState, Stimulus)
from .plugin import PluginBase
from .world import WorldView

class IBrain(ABC):
    """大脑:感知→情绪→记忆→决策。由 brain 插件实现(species 内默认绑定)。"""
    brain_id: str = "abstract"

    @abstractmethod
    def observe(self, world: WorldView, stimuli: list[Stimulus], dt: float) -> None: ...

    @abstractmethod
    def decide(self, world: WorldView) -> BehaviorCommand: ...

    @abstractmethod
    def emotion(self) -> EmotionState: ...

    @abstractmethod
    def on_event(self, name: str, data: dict) -> None:
        """接收生命周期事件:grab / released / fed / freeze_on / freeze_off /
        story / demote(智能变更)等。"""

    @abstractmethod
    def inject_command(self, cmd: BehaviorCommand) -> None:
        """用户从面板立即触发行为。"""

    @abstractmethod
    def set_intelligence(self, level: int) -> None: ...

    @abstractmethod
    def memory_digest(self, limit: int = 50) -> list[str]:
        """人类可读的记忆摘要(中文),供面板查看。"""

    @abstractmethod
    def save(self) -> dict: ...

    @abstractmethod
    def load(self, data: dict) -> None: ...

    @abstractmethod
    def clear_memory(self) -> None:
        """仅由用户明确操作触发。"""

class IBody(ABC):
    """身体:把 BehaviorCommand 翻译为运动学状态与姿态(渲染输入)。"""
    def __init__(self, state: PetState) -> None:
        self.state = state

    @abstractmethod
    def apply(self, cmd: BehaviorCommand, world: WorldView, dt: float) -> None:
        """推进运动学:更新 state(pos/heading/speed/mode/activity),并更新内部 Pose。"""

    @abstractmethod
    def pose(self) -> Any:
        """渲染所需姿态。约定 dict:
        {
          "scale": px_per_body_unit, "altitude": float,
          "segments": [(x, y, angle, rx, ry), ...],     # 身体局部坐标,头在前
          "legs": [[(x, y), ...], ...],                 # 每条腿的关节点序列(足端最后)
          "antennae": [[(x, y), ...], ...],
          "wings": {"active": bool, "phase": float, "span": float},
        }"""

    @abstractmethod
    def window_half(self) -> int:
        """建议悬浮窗半径(px)。"""

    def carried_tick(self, dt: float) -> None:
        """被拖拽中每帧回调(ADR-0032,加法式默认方法)。

        语义:足端等自由部位重锚定到**当前**体位(标准俯视图态),被动
        拖尾由拖拽摆角通道表达;鼠标停则摆角衰减回标准姿态。缺省 no-op,
        旧 body 实现零破坏。"""
        return None

    def set_fling(self, vx: float, vy: float) -> None:
        """松手抛掷(ADR-0032,加法式默认方法;市场对标 Ragdoll/Goose 派)。

        语义:松手瞬间把拖拽末速度交给身体,身体按指数衰减惯性滑行(撞墙
        反弹),强抛经外拖速度观测自然触发翻面。缺省 no-op,旧 body 零破坏。"""
        return None

class SpeciesPlugin(PluginBase):
    """物种插件:外观参数 + 身体/大脑工厂 + 感知画像。核心不写死任何物种。"""

    @abstractmethod
    def create_body(self, state: PetState) -> IBody: ...

    @abstractmethod
    def create_brain(self, state: PetState) -> IBrain: ...

    @abstractmethod
    def perception_profile(self) -> dict[str, float]:
        """物种感知敏感度(0..1):
        wind / vibration / shadow / odor_food / odor_mate / vision 等。"""

    @abstractmethod
    def render_traits(self) -> dict[str, Any]:
        """渲染外观参数(颜色、比例、翅有无等)。"""
