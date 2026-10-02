# -*- coding: utf-8 -*-
"""perception.screen —— 屏幕感知:前台窗口跟踪 / 可见窗表 / 新窗事件 diff / 区块感知。

纯新增模块(A3),暂不接线到 app(主 agent 集成)。实施依据
docs/references/屏幕感知_窗口交互技术调研.md(E4 实测施工图)与
scratch/screen_probe.py 实测探针;线程模型抄 core/hook.py 三线程约定。

── 对外接口 ──
    ScreenMonitor()
        .foreground(force=False) -> WindowInfo | None   # 前台窗,节流 2Hz(0.5s)
        .snapshot(force=False)   -> list[WindowInfo]    # 可见顶层窗表,节流 ≥2s
        .diff_events(now=None, snapshot_windows=None) -> list[ScreenEvent]
        .close() / __del__                              # 幂等清理
    region_of(pos, screen_w, screen_h, fg_rect=None, edge=80, taskbar_h=48) -> str
    WinEventHook(on_event, event_min, event_max)        # 可选事件推送(见下)

── 成本契约(实测,Win11 375 顶层窗)──
    foreground 一对查询 2.28µs,2Hz 轮询 ≈9µs/s(帧预算 0.005%);
    全量快照(375 窗)≈1.4ms,【只允许事件驱动或 ≥2s 节流,禁止每帧轮询】;
    region_of 纯几何 ≈0.49µs/次。DPI:进程为 PER_MONITOR_AWARE_V2,
    GetWindowRect 返回物理像素,与舞台/钩子坐标同系,无需换算。

── 防御约定(绝不抛异常进主循环)──
    * IsWindowVisible 假 / IsIconic 真 / DWMWA_CLOAKED≠0(UWP 挂起)→ 快照中整窗丢弃;
    * rect 非法(r<=l 或 b<=t 或不与虚屏相交)→ 整窗丢弃;
    * foreground() 不丢弃但如实报告 is_minimized/cloaked,rect 非法时为 None;
    * 隐私边界:只取 rect/类名/可见性,不读标题与内容。

── 事件模型(diff_events)──
    与上次快照 diff,产出 ScreenEvent(kind: "appear"|"grow"|"vanish", hwnd, rect,
    center, class_name)。per-hwnd 去抖 ≥3s(调研 §2.2:dict[hwnd]->t,<1KB);
    grow 判定 = 面积较上次扩大 >5%;首次调用只建立基线返回空列表。

── WinEventHook(可选)──
    EVENT_SYSTEM_FOREGROUND 出上下文钩子(实测 Python 可用,回调时延 P50≈0ms),
    线程模型整抄 core/hook.py:安装/泵/卸载同一条泵线程,回调只入队快进快出,
    派发线程再调 on_event。不 start() 也可只做常量参考。
"""
from __future__ import annotations

import atexit
import ctypes
import queue
import sys
import threading
import time
import traceback
from dataclasses import dataclass

__all__ = [
    "IS_WINDOWS", "WindowInfo", "ScreenEvent", "ScreenMonitor",
    "region_of", "WinEventHook",
]

IS_WINDOWS = sys.platform == "win32"

# ---------------- 常量(数值在任何平台定义都安全) ----------------
DWMWA_CLOAKED = 14              # DWMWINDOWATTRIBUTE:UWP 挂起/隐身标记
EVENT_SYSTEM_FOREGROUND = 0x0003
EVENT_OBJECT_SHOW = 0x8002
EVENT_OBJECT_HIDE = 0x8003
WINEVENT_OUTOFCONTEXT = 0
WM_APP_QUIT = 0x8001            # 与 core/hook.py 同款泵退出消息
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
SM_CXSCREEN, SM_CYSCREEN = 0, 1

FG_MIN_INTERVAL = 0.5           # 前台轮询节流:2Hz
SNAP_MIN_INTERVAL = 2.0         # 快照节流:仅事件驱动或 ≥2s(调研 §1.2 红线)
DEBOUNCE_S = 3.0                # per-hwnd 事件去抖(调研 §2.2)
GROW_RATIO = 1.05               # 面积扩大 >5% 记 grow
SNAPSHOT_CAP = 64               # 窗表缓存上限(调研 §1 接入点,≈8KB)
DEFAULT_AREA_FRAC = 0.008       # 快照面积门:≥0.8% 主屏(≈130×130px @1080p)

if IS_WINDOWS:
    from ctypes import wintypes

    _user32 = ctypes.windll.user32
    _dwmapi = ctypes.windll.dwmapi
    _WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    _WINEVENTPROC = ctypes.WINFUNCTYPE(
        None, ctypes.c_void_p, wintypes.DWORD, wintypes.HWND,
        ctypes.c_long, ctypes.c_long, wintypes.DWORD, wintypes.DWORD)


# ---------------- 轻量数据结构 ----------------
@dataclass(frozen=True)
class WindowInfo:
    """单个顶层窗口的只读快照(rect 为物理像素 (l, t, r, b);非法时为 None)。"""
    hwnd: int
    rect: tuple[int, int, int, int] | None
    class_name: str
    is_minimized: bool = False
    cloaked: bool = False

    @property
    def area(self) -> int:
        if not self.rect:
            return 0
        return max(0, (self.rect[2] - self.rect[0])) * max(0, (self.rect[3] - self.rect[1]))

    @property
    def center(self) -> tuple[int, int]:
        if not self.rect:
            return (0, 0)
        return ((self.rect[0] + self.rect[2]) // 2, (self.rect[1] + self.rect[3]) // 2)


@dataclass(frozen=True)
class ScreenEvent:
    """窗口事件(轻量,只有 rect/类名,无标题无内容)。kind: appear|grow|vanish。"""
    kind: str
    hwnd: int
    rect: tuple[int, int, int, int]
    center: tuple[int, int]
    class_name: str


# ---------------- Win32 查询封装(模块级,测试可注入替换) ----------------
def _fg_hwnd() -> int:
    return int(_user32.GetForegroundWindow() or 0)


def _get_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    r = wintypes.RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return (int(r.left), int(r.top), int(r.right), int(r.bottom))


def _is_visible(hwnd: int) -> bool:
    return bool(_user32.IsWindowVisible(hwnd))


def _is_iconic(hwnd: int) -> bool:
    return bool(_user32.IsIconic(hwnd))


def _is_cloaked(hwnd: int) -> bool:
    cloaked = wintypes.DWORD(0)
    # DwmGetWindowAttribute 对非 DWM 窗可能失败:按"不隐身"处理,不抛
    if _dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED,
                                     ctypes.byref(cloaked), ctypes.sizeof(cloaked)) != 0:
        return False
    return bool(cloaked.value)


def _class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    n = _user32.GetClassNameW(hwnd, buf, 256)
    return buf.value if n > 0 else ""


def _virtual_screen() -> tuple[int, int, int, int]:
    """虚屏 (l, t, r, b);查询失败退化到主屏。"""
    l = _user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    t = _user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    w = _user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    h = _user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    if w <= 0 or h <= 0:
        w, h = _user32.GetSystemMetrics(SM_CXSCREEN), _user32.GetSystemMetrics(SM_CYSCREEN)
        l, t = 0, 0
    if w <= 0 or h <= 0:
        return (0, 0, 1920, 1080)   # 最后兜底(非桌面会话)
    return (int(l), int(t), int(l + w), int(t + h))


def _primary_screen_area() -> int:
    w = _user32.GetSystemMetrics(SM_CXSCREEN)
    h = _user32.GetSystemMetrics(SM_CYSCREEN)
    return w * h if w > 0 and h > 0 else 1920 * 1080


def _valid_rect(rect: tuple[int, int, int, int] | None, vs: tuple[int, int, int, int]) -> bool:
    """rect 合法性防御:正面积且与虚屏相交(允许部分出屏)。"""
    if not rect:
        return False
    l, t, r, b = rect
    if r <= l or b <= t:
        return False
    vl, vt, vr, vb = vs
    return r > vl and l < vr and b > vt and t < vb


def _enum_top_hwnds() -> list[int]:
    """EnumWindows 全量顶层 hwnd(z 序)。回调只收集,快进快出。"""
    out: list[int] = []
    def _cb(hwnd, lparam):
        out.append(int(hwnd))
        return True
    proc = _WNDENUMPROC(_cb)        # 持引用至枚举结束,防 GC
    _user32.EnumWindows(proc, 0)
    return out


# ---------------- 区块感知(纯函数,零 Win32,调研 §三) ----------------
def region_of(pos: tuple[float, float],
              screen_w: int,
              screen_h: int,
              fg_rect: tuple[int, int, int, int] | None = None,
              edge: int = 80,
              taskbar_h: int = 48) -> str:
    """把物理像素坐标分类为屏幕区块(优先级:任务栏 > 四角 > 边缘带 > 前台窗内 > 中央)。

    返回值:"taskbar" | "corner_tl|tr|bl|br" | "edge" | "in_fg" | "center"。
    分区定义(调研 §三):边缘带 edge px;四角 = 两邻边带交集;任务栏区 = y > H-taskbar_h;
    前台窗内 = 点在前台 rect。非法输入(退化屏幕/非法 fg_rect)安全退化,绝不抛异常。
    """
    x, y = pos[0], pos[1]
    if screen_w <= 0 or screen_h <= 0 or edge < 0:
        return "center"
    if y > screen_h - taskbar_h and taskbar_h > 0:
        return "taskbar"
    tl = x < edge and y < edge
    tr = x > screen_w - edge and y < edge
    bl = x < edge and y > screen_h - edge
    br = x > screen_w - edge and y > screen_h - edge
    if tl: return "corner_tl"
    if tr: return "corner_tr"
    if bl: return "corner_bl"
    if br: return "corner_br"
    if x < edge or x > screen_w - edge or y < edge:
        return "edge"
    if (fg_rect and len(fg_rect) == 4
            and fg_rect[0] < fg_rect[2] and fg_rect[1] < fg_rect[3]
            and fg_rect[0] <= x < fg_rect[2] and fg_rect[1] <= y < fg_rect[3]):
        return "in_fg"
    return "center"


# ---------------- ScreenMonitor ----------------
class ScreenMonitor:
    """前台/窗表/事件的 ctypes 轮询器(主路径,调研 §1 方案 A)。

    节流红线:foreground 2Hz;snapshot 仅事件驱动或 ≥2s。所有查询防御式,
    非桌面会话/受限环境返回空结果而非抛异常。线程安全级别:预期在主循环
    单线程调用(与 WorldModel 同约定),内部仅做无锁缓存。
    """

    def __init__(self, min_area_frac: float = DEFAULT_AREA_FRAC) -> None:
        self.min_area_frac = float(min_area_frac)
        self._fg_cache: WindowInfo | None = None
        self._fg_t: float = float("-inf")
        self._snap_cache: list[WindowInfo] = []
        self._snap_t: float = float("-inf")
        self._prev_windows: dict[int, WindowInfo] | None = None   # None=未建基线
        self._last_emit: dict[int, float] = {}                    # hwnd -> 上次事件时刻
        self._closed = False

    # ---- 对外接口 ----
    def foreground(self, force: bool = False) -> WindowInfo | None:
        """前台窗口信息(2Hz 节流;force=True 绕过节流)。

        不因 is_minimized/cloaked 丢弃(如实报告);rect 非法(最小化坐标
        -32000 之类)时 rect=None。无前台(锁屏/安全桌面)返回 None。
        """
        if self._closed:
            return None
        now = time.monotonic()
        if not force and (now - self._fg_t) < FG_MIN_INTERVAL:
            return self._fg_cache
        self._fg_t = now
        self._fg_cache = self._query_foreground()
        return self._fg_cache

    def snapshot(self, force: bool = False) -> list[WindowInfo]:
        """可见顶层窗表(≥2s 节流;force=True 绕过)。已过面积门/防御过滤。"""
        if self._closed:
            return []
        now = time.monotonic()
        if not force and (now - self._snap_t) < SNAP_MIN_INTERVAL:
            return self._snap_cache
        self._snap_t = now
        self._snap_cache = self._capture_windows()
        return self._snap_cache

    def diff_events(self,
                    now: float | None = None,
                    snapshot_windows: list[WindowInfo] | None = None,
                    ) -> list[ScreenEvent]:
        """与上次快照 diff 出 appear/grow/vanish 事件(per-hwnd 去抖 ≥3s)。

        now:外部注入时刻(测试用),缺省 time.monotonic();
        snapshot_windows:外部注入窗表(测试用/复用已有快照),缺省自取
        snapshot(force=True)。首次调用只建立基线,返回 []。
        """
        if self._closed:
            return []
        t = time.monotonic() if now is None else float(now)
        wins = list(self.snapshot(force=True)) if snapshot_windows is None \
            else list(snapshot_windows)
        return self._diff(wins, t)

    def close(self) -> None:
        """释放缓存与回调引用。幂等,可重复调用。"""
        self._closed = True
        self._fg_cache = None
        self._snap_cache = []
        self._prev_windows = None
        self._last_emit = {}

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    # ---- 内部实现 ----
    def _query_foreground(self) -> WindowInfo | None:
        try:
            hwnd = _fg_hwnd()
            if not hwnd:
                return None
            rect = _get_rect(hwnd)
            info = WindowInfo(
                hwnd=hwnd, rect=rect,
                class_name=_class_name(hwnd),
                is_minimized=_is_iconic(hwnd),
                cloaked=_is_cloaked(hwnd),
            )
            if info.is_minimized or info.cloaked:
                return WindowInfo(hwnd, None, info.class_name,
                                  info.is_minimized, info.cloaked)  # rect 防御置 None
            return info
        except Exception:
            return None                     # 绝不抛进主循环

    def _capture_windows(self) -> list[WindowInfo]:
        """枚举+四查防御+面积门,产出可见窗表(z 序,cap 64)。"""
        try:
            vs = _virtual_screen()
            min_area = self.min_area_frac * _primary_screen_area()
            out: list[WindowInfo] = []
            for hwnd in _enum_top_hwnds():
                if not _is_visible(hwnd) or _is_iconic(hwnd) or _is_cloaked(hwnd):
                    continue
                rect = _get_rect(hwnd)
                if not _valid_rect(rect, vs):
                    continue
                l, t, r, b = rect
                if (r - l) * (b - t) < min_area:
                    continue                # 面积门:丢弃小窗(托盘浮层/隐形 helper)
                out.append(WindowInfo(hwnd, rect, _class_name(hwnd)))
                if len(out) >= SNAPSHOT_CAP:
                    break                   # z 序前 64(顶层最有感知价值)
            return out
        except Exception:
            return []                       # 防御:枚举失败当空表

    def _diff(self, wins: list[WindowInfo], now: float) -> list[ScreenEvent]:
        cur = {w.hwnd: w for w in wins}
        if self._prev_windows is None:      # 首次:只建基线,不产事件(防洪水)
            self._prev_windows = cur
            return []
        prev = self._prev_windows
        events: list[ScreenEvent] = []

        def _emit(hwnd: int, kind: str, w: WindowInfo) -> None:
            t0 = self._last_emit.get(hwnd)
            if t0 is not None and (now - t0) < DEBOUNCE_S:
                return                      # per-hwnd 去抖 ≥3s(调研 §2.2)
            self._last_emit[hwnd] = now
            events.append(ScreenEvent(kind, hwnd, w.rect or (0, 0, 0, 0),
                                      w.center, w.class_name))

        for hwnd, w in cur.items():
            p = prev.get(hwnd)
            if p is None:
                _emit(hwnd, "appear", w)
            elif w.area > p.area * GROW_RATIO:
                _emit(hwnd, "grow", w)
        for hwnd, p in prev.items():
            if hwnd not in cur:
                _emit(hwnd, "vanish", p)

        self._prev_windows = cur
        # 去抖表修剪:过期(>去抖窗)才清,在窗内的记录必须保留——否则窗口
        # "消失又重现"时去抖记录丢失,3s 去抖语义被破坏;仍保证无界增长不可能
        self._last_emit = {h: t for h, t in self._last_emit.items()
                           if (now - t) < DEBOUNCE_S}
        return events


# ---------------- 可选:WinEventHook(调研 §2 方案 B) ----------------
class WinEventHook:
    """EVENT_SYSTEM_FOREGROUND 等出上下文 WinEvent 钩子(可选,不装也不影响主路径)。

    线程模型整抄 core/hook.py 三线程约定:
      - 主线程:构造 / start / stop;
      - "winevent-pump" 泵线程:SetWinEventHook 安装 + GetMessageW 泵 + 线程内
        UnhookWinEvent(三步必须同线程);回调只入队,快进快出;
      - "winevent-dispatch" 派发线程:从队列取事件调 on_event(event, hwnd)。
    """

    def __init__(self,
                 on_event=None,
                 event_min: int = EVENT_SYSTEM_FOREGROUND,
                 event_max: int = EVENT_SYSTEM_FOREGROUND) -> None:
        if not IS_WINDOWS:
            raise RuntimeError(
                "neuropet.perception.screen.WinEventHook 仅支持 Windows;"
                f"当前平台为 {sys.platform!r}。")
        if on_event is not None and not callable(on_event):
            raise TypeError("on_event 必须是可调用对象,签名为 on_event(event: int, hwnd: int)")
        self.on_event = on_event
        self.event_min = int(event_min)
        self.event_max = int(event_max)
        self._events: "queue.Queue[tuple[int, int]]" = queue.Queue(maxsize=1024)
        self._quit = threading.Event()
        self._ready = threading.Event()
        self._unhooked = threading.Event()
        self._proc = None                   # 持引用防 GC(hook.py 坑 5)
        self._thread: threading.Thread | None = None
        self._dispatch: threading.Thread | None = None
        self._tid = 0
        self._hook = 0
        self._stop_requested = False
        self.dropped = 0

    def start(self, timeout: float = 3.0) -> None:
        if self._thread is not None:
            return                          # 幂等
        self._proc = _WINEVENTPROC(self._on_hook_event)
        self._dispatch = threading.Thread(target=self._dispatch_loop,
                                          name="winevent-dispatch", daemon=True)
        self._thread = threading.Thread(target=self._pump, name="winevent-pump", daemon=True)
        self._dispatch.start()
        self._thread.start()
        try:
            if not self._ready.wait(timeout):
                raise RuntimeError("WinEventHook:泵线程超时未就绪。")
            if not self._hook:
                raise RuntimeError(
                    f"WinEventHook:SetWinEventHook 安装失败,GetLastError="
                    f"{ctypes.windll.kernel32.GetLastError()}。")
        except Exception:
            self.stop(timeout)
            raise
        atexit.register(self._stop_safe)

    def stop(self, timeout: float = 3.0) -> None:
        if self._stop_requested:
            return
        self._stop_requested = True
        if self._tid:
            _user32.PostThreadMessageW(self._tid, WM_APP_QUIT, 0, 0)
        if self._thread:
            self._thread.join(timeout)
        self._unhooked.wait(timeout)
        self._quit.set()
        if self._dispatch:
            self._dispatch.join(timeout)
        atexit.unregister(self._stop_safe)

    # ---- 泵线程:安装 -> 泵 -> 线程内卸载(同线程,hook.py 坑 5) ----
    def _pump(self) -> None:
        self._tid = ctypes.windll.kernel32.GetCurrentThreadId()
        self._hook = _user32.SetWinEventHook(
            self.event_min, self.event_max, None, self._proc, 0, 0, WINEVENT_OUTOFCONTEXT)
        self._ready.set()
        if not self._hook:
            return
        msg = wintypes.MSG()
        while True:
            r = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r <= 0 or msg.message == WM_APP_QUIT:
                break
        _user32.UnhookWinEvent(self._hook)
        self._hook = 0
        self._unhooked.set()

    def _on_hook_event(self, hook, event, hwnd, obj, child, thread, time_ms) -> None:
        """回调红线:快进快出,只入队(满则丢弃计数),绝不执行业务逻辑。"""
        try:
            self._events.put_nowait((int(event), int(hwnd or 0)))
        except queue.Full:
            self.dropped += 1

    def _dispatch_loop(self) -> None:
        while True:
            try:
                event, hwnd = self._events.get(timeout=0.1)
            except queue.Empty:
                if self._quit.is_set():
                    return
                continue
            try:
                if self.on_event is not None:
                    self.on_event(event, hwnd)
            except Exception:
                traceback.print_exc()

    def _stop_safe(self) -> None:
        try:
            self.stop()
        except Exception:
            pass
