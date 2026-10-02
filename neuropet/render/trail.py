"""高速拖尾(v0.2.0):Canvas 矢量线段装饰,复用少量 line 项而非每帧新增图像。

设计取舍(用户需求:高速拖尾随速度变长、停下即消、不吃内存):

- **不产生任何位图**:每宠最多 ``MAX_SEGMENTS`` 条 1px 线,10 宠上限 40 项。
  与"每帧新建 PhotoImage"的宠物精灵管线完全隔离,也不进显示签名。
- **只加不减的项池**:``create_line`` 只在首次需要时创建,之后一律
  ``coords`` + ``state`` 复用 ⇒ 画布项数与显示时长无关,不会累积。
- **长度随速度单调、有上限**:拖尾画的是**最近若干帧的实际位移折线**,
  采样间隔固定(主循环绝对节拍),所以速度越快相邻点间距越大、尾巴单调变长,
  并硬截在 ``MAX_LEN``;消退期按比例把整条尾巴收回宠物身下。
- **出现阈值按体长给**:``max(THR_MIN, THR_PER_BODYLEN × 画布半径)``,
  慢速爬行不出尾,冲刺出满尾。
- **方向沿近期实际轨迹**:历史点取自 ``PetState.pos``(世界坐标),不是速度
  向量的合成方向;折线按点数切成 ≤4 段直线近似,尾端落在宠身上。
- **消退**:停下后 ``FADE_S`` 内按比例把长度收回到零(而不是突然消失),
  随后清空历史与线项;暂停/抓握因速度为 0 走同一条消退路径。

历史 deque 上限 ``HISTORY`` 个点,时间戳只用于剔除陈旧点,不做插值。
"""
from __future__ import annotations

import math
from collections import deque

HISTORY = 8          # 每宠历史点上限(= 最多 7 段折线)
MAX_SEGMENTS = 4     # 每宠线项上限(10 宠 → 全局 ≤40 项)
MAX_LEN = 240.0      # 单宠拖尾总长上限(px)
FADE_S = 0.15        # 停下后的消退时长(用户需求 100~200ms)
THR_MIN = 260.0      # 出尾速度下限(px/s)
THR_PER_BODYLEN = 3.5  # 速度阈值 = 该系数 × 渲染半宽(2× 蟑螂 ≈420px/s)
COLOR = "#7f8f99"    # 细线色:轻薄、不与虫体争视觉


class TrailLayer:
    """拖尾装饰层(挂在共享画布上;随宠物移除而清理)。"""

    def __init__(self, canvas, tag: str = "trail") -> None:
        self.canvas = canvas
        self.tag = tag
        self.enabled = True
        self._hist: dict[str, deque] = {}
        self._items: dict[str, list[int]] = {}
        self._fade: dict[str, float] = {}

    # ---------------- 开关 ----------------
    def set_enabled(self, on: bool) -> None:
        """设置里关闭拖尾:立即清掉全部既有线项与历史(不留残影)。"""
        on = bool(on)
        if on == self.enabled:
            return
        self.enabled = on
        if not on:
            self.clear()

    # ---------------- 每帧更新 ----------------
    def update(self, pet_id: str, x: float, y: float, speed: float,
               half: float, dt: float, now: float) -> None:
        """记录一个历史点并重绘该宠的拖尾。

        ``speed`` 必须是**真实位移速度**(``PetState.speed``,含抛掷),不是
        配置速度;``half`` 是画布半径,用于按体长定阈值。
        """
        if not self.enabled:
            return
        threshold = max(THR_MIN, THR_PER_BODYLEN * abs(float(half) or 0.0))
        hist = self._hist.get(pet_id)
        if float(speed) >= threshold:
            if hist is None:
                hist = deque(maxlen=HISTORY)
                self._hist[pet_id] = hist
            # 陈旧点先剔:暂停/长时间静止后重新起步不会拖出一条长直线。
            if hist and now - hist[-1][2] > 0.5:
                hist.clear()
            hist.append((float(x), float(y), now))
            self._fade[pet_id] = 1.0
        else:
            fade = self._fade.get(pet_id, 0.0) - (float(dt) / FADE_S)
            if fade <= 0.0 or hist is None or len(hist) < 2:
                self.remove(pet_id)
                return
            self._fade[pet_id] = fade
        self._draw(pet_id)

    def _draw(self, pet_id: str) -> None:
        hist = self._hist[pet_id]
        fade = self._fade[pet_id]
        span = MAX_LEN * fade
        if span < 8.0 or len(hist) < 2:
            self._hide(pet_id)
            return
        # 从最新点往回累计:够到 span 或走完历史为止(速度越快尾巴越长)。
        pts = list(hist)
        chain = [pts[-1]]
        total = 0.0
        for i in range(len(pts) - 2, -1, -1):
            seg = math.hypot(pts[i + 1][0] - pts[i][0],
                             pts[i + 1][1] - pts[i][1])
            if total + seg > span:
                if seg > 1e-6:
                    k = (span - total) / seg
                    # Walk backward from the newer endpoint by the remaining
                    # length. Starting at the older end would extend the tail.
                    ax = pts[i + 1][0] + (pts[i][0] - pts[i + 1][0]) * k
                    ay = pts[i + 1][1] + (pts[i][1] - pts[i + 1][1]) * k
                    chain.append((ax, ay, pts[i][2]))
                break
            total += seg
            chain.append(pts[i])
        if len(chain) < 2:
            self._hide(pet_id)
            return
        chain.reverse()                       # 最旧 → 最新
        pieces = self._chords(chain)
        items = self._items.setdefault(pet_id, [])
        while len(items) < len(pieces):
            item = self.canvas.create_line(0, 0, 0, 0, width=1, fill=COLOR,
                                           tag=self.tag, state="hidden")
            self.canvas.tag_lower(item)       # 垫在宠物图像之下
            items.append(item)
        for item in items[len(pieces):]:
            self.canvas.itemconfig(item, state="hidden")
        for item, (x0, y0, x1, y1) in zip(items, pieces):
            self.canvas.coords(item, x0, y0, x1, y1)
            self.canvas.itemconfig(item, state="normal")

    @staticmethod
    def _chords(chain: list) -> list[tuple[float, float, float, float]]:
        """折线 → ≤MAX_SEGMENTS 条直线(首尾点之间的弦,保持整体走向)。"""
        n = len(chain)
        groups = min(MAX_SEGMENTS, n - 1)
        if groups <= 0:
            return []
        per = (n - 1 + groups - 1) // groups
        out = []
        idx = n - 1
        while idx > 0 and len(out) < MAX_SEGMENTS:
            j = max(0, idx - per)
            out.append((chain[j][0], chain[j][1], chain[idx][0], chain[idx][1]))
            idx = j
        return out

    # ---------------- 清理 ----------------
    def _hide(self, pet_id: str) -> None:
        for item in self._items.get(pet_id, ()):
            self.canvas.itemconfig(item, state="hidden")

    def remove(self, pet_id: str) -> None:
        """彻底清理一只宠的拖尾(隐藏/移除/退出共用)。"""
        for item in self._items.pop(pet_id, ()):
            try:
                self.canvas.delete(item)
            except Exception:
                pass
        self._hist.pop(pet_id, None)
        self._fade.pop(pet_id, None)

    def clear(self) -> None:
        for pid in list(self._items):
            self.remove(pid)
        self._hist.clear()
        self._fade.clear()
