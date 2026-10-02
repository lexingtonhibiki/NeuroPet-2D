"""数学工具:向量、角度、随机数。坐标约定:屏幕像素,原点左上,y 轴向下。"""
from __future__ import annotations

import math
import random

TAU = math.tau


def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if v < lo else hi if v > hi else v


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * clamp(t)


def wrap_angle(a: float) -> float:
    """角度归一化到 (-pi, pi]。"""
    while a > math.pi:
        a -= TAU
    while a <= -math.pi:
        a += TAU
    return a


def angle_lerp(a: float, b: float, t: float) -> float:
    return a + wrap_angle(b - a) * clamp(t)


def dist(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(bx - ax, by - ay)


def rand_uniform(lo: float, hi: float) -> float:
    return random.uniform(lo, hi)


def rand_sign() -> float:
    return 1.0 if random.random() < 0.5 else -1.0
