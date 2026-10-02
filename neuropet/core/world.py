"""世界模型:宠物/食物/区域/光标的权威状态。主循环单线程读写,钩子线程只投事件。"""
from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable

from .contracts import CursorKinematics, CursorSample, FoodItem, PetState, Zone
from .mathutil import dist

# ---- 区块感知纯函数(ADR-0029;引用 perception.screen,消费其数据形状) ----
# perception.screen 顶层只定义常量/纯函数,Win32 绑定在 IS_WINDOWS 分支内,
# 跨平台导入安全;防御式兜底:任何导入失败退化为"无区块语义"(region=None),
# 旧调用方零破坏。
try:
    from neuropet.perception.screen import region_of as _screen_region_of
except Exception:                                       # pragma: no cover
    _screen_region_of = None

# v0.2.0:可用桌面边界(已扣除任务栏)的唯一事实源。WorldModel 持有一份
# DesktopArea,``clamp_to_screen`` 一律走它 —— 身体积分、拖拽仲裁、面板落食
# 三条路径从此是同一个口径(旧代码分别是 80 / 60 / 30px 且都以整屏为准)。
from .desktop import DEFAULT_MARGIN, DesktopArea

# 合成 WorldView(测试/离线脚本)没有 desktop 句柄时的兜底:按屏幕尺寸缓存
# 一份,保证"没有句柄"只退化成共用同一份边界,不会退回旧的整屏 60px 口径。
_FALLBACK_DESKTOP: dict = {}


def _desktop_for(screen: tuple[int, int]) -> DesktopArea:
    key = (int(screen[0]), int(screen[1]))
    area = _FALLBACK_DESKTOP.get(key)
    if area is None:
        area = DesktopArea(key)
        _FALLBACK_DESKTOP[key] = area
    return area


def region_of(pos: tuple[float, float], screen_w: int, screen_h: int,
              fg_rect: tuple[int, int, int, int] | None = None) -> str:
    """屏幕区块分类(转 perception.screen.region_of,任务栏>四角>边缘>前台窗内>中央)。

    返回:"taskbar" | "corner_tl|tr|bl|br" | "edge" | "in_fg" | "center";
    感知模块不可用时安全退化为 "center"(无区块语义,偏好矩阵退回旧行为)。"""
    if _screen_region_of is None:
        return "center"
    return _screen_region_of(pos, screen_w, screen_h, fg_rect)


@dataclass
class WorldView:
    """传给大脑的只读世界快照(浅拷贝,禁止修改)。"""
    t: float
    screen: tuple[int, int]
    pets: dict[str, PetState]          # 含自己
    self_id: str
    foods: dict[str, FoodItem]
    zones: dict[str, Zone]
    cursor: CursorKinematics
    # ---- ADR-0029 屏幕感知可选字段(缺省 None/空:旧调用方逐位零破坏) ----
    fg_window_rect: tuple[int, int, int, int] | None = None   # 前台窗 rect(l,t,r,b)
    region: str | None = None            # self 宠所在区块(region_of 产物;None=未注入)
    window_events: tuple = ()            # ScreenEvent 序列(appear|grow|vanish)
    # v0.2.0:可用桌面边界句柄(任务栏已扣除)。缺省 None 时按屏幕尺寸取一份
    # 共用兜底(见 ``_desktop_for``),语义与 WorldModel 持有的那一份一致。
    desktop: object | None = None

    def area(self) -> DesktopArea:
        """本快照使用的可用区域句柄(注入的优先,否则共用兜底)。"""
        return self.desktop if self.desktop is not None \
            else _desktop_for(self.screen)

    def region_at(self, pos: tuple[float, float]) -> str | None:
        """任意点的区块分类(躲藏目的地/航点偏好共用;fg_window_rect 参与判定)。

        返回 "taskbar"|"corner_*"|"edge"|"in_fg"|"center";**视图未注入感知
        (fg_window_rect 与 region 均为 None)时返回 None = 无区块语义** ——
        脑侧偏好权重取 1.0,航点撒点退回旧行为(合成 WorldView/旧测试零扰动,
        区块偏好在主 agent 注入感知数据后才生效)。感知模块缺失时退化 "center"。"""
        if self.fg_window_rect is None and self.region is None:
            return None
        if _screen_region_of is None or self.screen[0] <= 0:
            return "center"
        return _screen_region_of(pos, self.screen[0], self.screen[1],
                                 self.fg_window_rect)

    def clamp_to_screen(self, pos: tuple[float, float],
                        margin: float = DEFAULT_MARGIN
                        ) -> tuple[float, float]:
        """可用桌面边界钳位(任务栏已扣除;``margin`` = 身体留白)。

        ``margin`` 语义统一为"身体留白":``desktop.margin_for(画布半径)``
        给出的值覆盖整个画布,面板落食用 ``margin=30`` 这类小值仍是"贴边"
        的显式意图。
        """
        return self.area().clamp(pos, margin)

    def usable(self, margin: float = DEFAULT_MARGIN
               ) -> tuple[float, float, float, float]:
        """可用矩形 (x0, y0, x1, y1);身体积分的软墙与运动目标共用它。"""
        return self.area().inset(margin)

    def nearest_food(self, pos: tuple[float, float], max_r: float = 1e9) -> FoodItem | None:
        """大脑在快照上查询最近食物(只读)。"""
        best, bd = None, max_r
        for f in self.foods.values():
            d = dist(pos[0], pos[1], *f.pos)
            if d < bd:
                best, bd = f, d
        return best


class WorldModel:
    def __init__(self, screen_w: int, screen_h: int) -> None:
        self.screen = (screen_w, screen_h)
        self.desktop = DesktopArea(self.screen)
        self.pets: dict[str, PetState] = {}
        self.foods: dict[str, FoodItem] = {}
        self.zones: dict[str, Zone] = {}
        self.cursor = CursorKinematics()
        self._cursor_hist: deque[CursorSample] = deque(maxlen=90)  # ~1.5s @60Hz
        self._lock = threading.Lock()

    # ---- 光标 ----
    def update_cursor(self, x: float, y: float, kinematics_fn) -> None:
        """kinematics_fn: perception 模块提供的特征更新器(负责速度/手势分类)。"""
        now = time.perf_counter()
        self._cursor_hist.append(CursorSample(now, x, y))
        kinematics_fn(self._cursor_hist, self.cursor)

    # ---- 宠物 ----
    def upsert_pet(self, state: PetState) -> None:
        self.pets[state.pet_id] = state

    def remove_pet(self, pet_id: str) -> None:
        self.pets.pop(pet_id, None)

    # ---- 食物 ----
    def add_food(self, pos: tuple[float, float], kind: str = "crumb") -> FoodItem:
        f = FoodItem(food_id=uuid.uuid4().hex[:10], pos=pos, kind=kind)
        self.foods[f.food_id] = f
        return f

    def eat_food(self, food_id: str, bite: float) -> bool:
        """返回 True 表示食物已耗尽需移除。"""
        f = self.foods.get(food_id)
        if f is None:
            return True
        f.amount = max(0.0, f.amount - bite)
        f.odor_strength = 0.9 * f.amount
        if f.amount <= 1e-3:
            self.foods.pop(food_id, None)
            return True
        return False

    # ---- 区域 ----
    def add_zone(self, kind: str, pos: tuple[float, float], radius: float = 220.0) -> Zone:
        z = Zone(zone_id=uuid.uuid4().hex[:8], kind=kind, pos=pos, radius=radius)
        self.zones[z.zone_id] = z
        return z

    def clear_zones(self) -> None:
        self.zones.clear()

    # ---- 快照 ----
    def snapshot(self, self_id: str, t: float,
                 fg_window_rect: tuple[int, int, int, int] | None = None,
                 window_events: Iterable = (),
                 region_aware: bool = False) -> WorldView:
        """世界快照(ADR-0029:fg_window_rect/window_events 为可选注入)。

        region 仅在 region_aware=True(或注入了 fg_window_rect)时计算 ——
        既有调用方(两参快照)快照逐位一致、脑侧 rng 流零扰动,区块偏好
        矩阵只在主 agent 接线(注入感知数据)后生效。"""
        st = self.pets.get(self_id)
        region = None
        if st is not None and _screen_region_of is not None \
                and (region_aware or fg_window_rect is not None):
            region = _screen_region_of(st.pos, self.screen[0], self.screen[1],
                                       fg_window_rect)
        return WorldView(
            t=t, screen=self.screen,
            pets=dict(self.pets), self_id=self_id,
            foods=dict(self.foods), zones=dict(self.zones),
            cursor=self.cursor,
            fg_window_rect=fg_window_rect, region=region,
            window_events=tuple(window_events),
            desktop=self.desktop,
        )

    # ---- 查询 ----
    def nearest_food(self, pos: tuple[float, float], max_r: float = 1e9) -> FoodItem | None:
        best, bd = None, max_r
        for f in self.foods.values():
            d = dist(pos[0], pos[1], *f.pos)
            if d < bd:
                best, bd = f, d
        return best

    def clamp_to_screen(self, pos: tuple[float, float],
                        margin: float = DEFAULT_MARGIN
                        ) -> tuple[float, float]:
        """可用桌面边界钳位(任务栏已扣除;见 WorldView 同名方法)。"""
        return self.desktop.clamp(pos, margin)

    def usable(self, margin: float = DEFAULT_MARGIN
               ) -> tuple[float, float, float, float]:
        return self.desktop.inset(margin)

    def refresh_desktop(self, force: bool = False) -> bool:
        """节流重取 work area(任务栏显隐/自动隐藏滑出);返回是否变化。"""
        return self.desktop.refresh(force)
