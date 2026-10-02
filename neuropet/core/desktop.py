"""桌面可用区域(work area):窗口边界、身体积分、拖拽仲裁三处共用的**唯一**口径。

v0.2.0 之前,同一个"宠物能走到哪"的问题有三份互不一致的实现:
``core/world.py`` 的 clamp 用 60px、``body/base.py`` 的软墙用 80px、投喂/面板
落食用 30px,并且三者都以**整屏**为准 —— 任务栏在底/顶/左/右时边界与真实可
用桌面不符,宠物被拖到屏幕下缘会出现"回弹一截"的瞬移。本模块给出唯一事实源:

- ``monitor_work_area(rect)`` —— Windows ``MonitorFromPoint`` +
  ``GetMonitorInfo`` 取**当前显示器**的 ``rcWork``(已扣除任务栏,四边通用);
  非 Windows/取不到时回退到传入矩形本身(安全跨平台回退,不写死任务栏高度);
- ``DesktopArea`` —— 启动取一次、之后按 ``poll_s`` 节流刷新(自动隐藏任务栏
  会让 work area 变化),对外只暴露 ``usable(margin)`` / ``clamp()`` /
  ``inside()`` 三个纯口径的读取方法。任何 Win32 异常都退化到"整屏可用",
  绝不把宠物关在屏幕外。
"""
from __future__ import annotations

import time

# 身体留白的下限:沿用 v0.1.x 的 80px 软墙,保证小体型宠物的可达范围与
# 改动前一致(不因为换了口径就偷偷缩小活动区);体型变大时按半宽的 0.7 倍
# 线性放大,保证身体不会被任务栏吃掉。
BODY_MARGIN_FLOOR = 80.0
BODY_MARGIN_RATIO = 0.7
# 无身体尺寸可用时的兜底留白(等于下限)。
DEFAULT_MARGIN = BODY_MARGIN_FLOOR
# 节流刷新周期(秒)。work area 只在任务栏显隐/自动隐藏滑出时变化,2s 足够,
# 且远低于"不要每宠每帧 Win32 查询"的要求。
POLL_S = 2.0

_IS_WINDOWS = True
try:                                     # pragma: no cover - 平台分支
    import os as _os
    _IS_WINDOWS = _os.name == "nt"
except Exception:                        # pragma: no cover
    _IS_WINDOWS = False


def _win32_work_area(rect: tuple[int, int, int, int]):
    """Windows:取包含 ``rect`` 中心的显示器的 rcWork(l,t,r,b)。

    ``MonitorFromPoint`` 用 ``MONITOR_DEFAULTTONEAREST``:多显示器下宠物所在
    显示器不是主屏时也拿得到正确的任务栏留白;虚拟桌面负坐标(副屏在左)
    也照常工作。函数级 import + 全 try,任何失败都返回 None 由调用方回退。
    """
    if not _IS_WINDOWS:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD),
                        ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT),
                        ("dwFlags", wintypes.DWORD)]

        user32 = ctypes.windll.user32
        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user32.MonitorFromPoint.restype = wintypes.HANDLE
        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE,
                                           ctypes.POINTER(MONITORINFO)]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        left, top, right, bottom = rect
        cx = int((left + right) * 0.5)
        cy = int((top + bottom) * 0.5)
        hmon = user32.MonitorFromPoint(wintypes.POINT(cx, cy), 2)
        if not hmon:
            return None
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if not user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            return None
        r = info.rcWork
        if r.right - r.left < 64 or r.bottom - r.top < 64:
            return None                    # 异常小 → 视为取不到
        return (int(r.left), int(r.top), int(r.right), int(r.bottom))
    except Exception:
        return None


def _intersect(a, b):
    left = max(a[0], b[0])
    top = max(a[1], b[1])
    right = min(a[2], b[2])
    bottom = min(a[3], b[3])
    if right - left < 64 or bottom - top < 64:
        return None                        # 工作区与逻辑屏不重叠 → 回退整屏
    return (left, top, right, bottom)


def monitor_work_area(screen: tuple[int, int]
                      ) -> tuple[int, int, int, int]:
    """逻辑屏幕 (w,h) 对应的可用桌面矩形(l, t, r, b),已扣除任务栏。

    与世界坐标同原点:Tk 的 ``winfo_screenwidth/height`` 取主显示器大小,
    而本函数把 rcWork 与该整屏矩形求交,因此结果仍在 (0,0)-(w,h) 内,窗口
    geometry 与 canvas 坐标无需额外偏移。
    """
    full = (0, 0, int(screen[0]), int(screen[1]))
    work = _win32_work_area(full)
    if work is None:
        return full
    clipped = _intersect(work, full)
    return clipped if clipped is not None else full


def margin_for(half: float | None) -> float:
    """模块级留白口径(``DesktopArea.margin_for`` 的唯一实现)。

    ``half`` = 物种的渲染半宽(``window_half``)。返回
    ``max(BODY_MARGIN_FLOOR, |half| × BODY_MARGIN_RATIO)``:
    统一 ``body/base.py._bounds`` 与拖拽仲裁的边距,消除"body 说 80 /
    arbiter 说 60"互相拉扯造成的边缘瞬移;下限取 80 让小体型宠物的可达范围
    与 v0.1.x 完全一致。
    """
    try:
        h = abs(float(half))
    except (TypeError, ValueError):
        h = 0.0
    return max(BODY_MARGIN_FLOOR, h * BODY_MARGIN_RATIO)


class DesktopArea:
    """可用区域缓存(节流刷新);给 App 一个 ``desktop`` 句柄,全链共用。"""

    def __init__(self, screen: tuple[int, int], poll_s: float = POLL_S) -> None:
        self.screen = (int(screen[0]), int(screen[1]))
        self.poll_s = float(poll_s)
        self._rect = monitor_work_area(self.screen)
        self._checked = time.perf_counter()

    # ---- 刷新 ----
    def refresh(self, force: bool = False) -> bool:
        """按节流周期重取 work area;返回矩形是否变化(变化时调用方可省缓存)。"""
        now = time.perf_counter()
        if not force and now - self._checked < self.poll_s:
            return False
        self._checked = now
        rect = monitor_work_area(self.screen)
        if rect == self._rect:
            return False
        self._rect = rect
        return True

    # ---- 只读口径 ----
    @property
    def rect(self) -> tuple[int, int, int, int]:
        """可用桌面矩形(l, t, r, b),绝对屏幕坐标。"""
        return self._rect

    def inset(self, margin: float | None = None) -> tuple[int, int, int, int]:
        """可用矩形按身体留白内缩后的整数矩形。

        ``margin=None`` → ``DEFAULT_MARGIN``(无身体尺寸时的兜底);
        留白至少留 1px,屏比留白还小时退化为一条线/一个点而不是负宽矩形。
        """
        m = DEFAULT_MARGIN if margin is None else float(margin)
        left, top, right, bottom = self._rect
        x0 = int(round(min(m, max(0.0, (right - left) * 0.45))))
        y0 = int(round(min(m, max(0.0, (bottom - top) * 0.45))))
        return (left + x0, top + y0, right - x0, bottom - y0)

    def margin_for(self, half: float | None) -> float:
        """见模块级 :func:`margin_for`(同口径,方法只是转发)。"""
        return margin_for(half)

    def clamp(self, pos, margin: float | None = None
              ) -> tuple[float, float]:
        """把点夹进 ``inset(margin)``;屏比留白还小时夹到中线。"""
        x0, y0, x1, y1 = self.inset(margin)
        x, y = float(pos[0]), float(pos[1])
        if x1 < x0:
            x0 = x1 = (x0 + x1) * 0.5
        if y1 < y0:
            y0 = y1 = (y0 + y1) * 0.5
        return (min(max(x, float(x0)), float(x1)),
                min(max(y, float(y0)), float(y1)))

    def inside(self, pos, margin: float | None = None) -> bool:
        x0, y0, x1, y1 = self.inset(margin)
        return x0 <= pos[0] <= x1 and y0 <= pos[1] <= y1