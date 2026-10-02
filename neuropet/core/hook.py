# -*- coding: utf-8 -*-
"""core.hook —— WH_MOUSE_LL 全局低级鼠标钩子(投喂模式:吞掉左键并取点击坐标)。

移植自 scratch/proto_hook.py 的 LowLevelMouseHook(原型已验证),并遵守
scratch/原型测量报告.md "坑 5" 的全部工程约定:

1. 安装(SetWindowsHookExW)、消息泵(GetMessageW)、卸载(UnhookWindowsHookEx)
   必须在同一条专用线程;外部只能 PostThreadMessageW(WM_APP_QUIT) 请求退出,
   由泵线程在消息循环退出后"线程内 Unhook",再 join(跨线程 Unhook 会失败/泄漏)。
2. HOOKPROC 回调对象必须作为成员持有(self._proc)防 ctypes 回调被 GC;
   钩子回调必须快进快出(LL 钩子超时会被系统"静默摘除",默认容忍仅几百 ms):
   回调里只做入队与标志判断,绝不直接执行业务回调。
3. capture 开启时 WM_LBUTTONDOWN/UP 都 return 1 吞掉(避免孤儿 UP 事件),
   DOWN 的屏幕物理像素坐标入内部队列,由独立派发线程调用 on_left_click(x, y)。
   一次点击只回调一次(DOWN 触发;UP 只吞不回调)。
4. LLMHF_INJECTED:SendInput 注入的事件同样会经过 LL 钩子(原型实测标记 1/1),
   本类不做过滤、照常处理;如需区分真人/脚本可在回调里按 flags 过滤。
5. 进程退出兜底:atexit 注册 stop;显式 stop 时注销。
6. 控制面板穿透(hotfix):构造时可传 is_passthrough(x, y) -> bool,由 App 传入
   "该点是否位于控制面板内"的快速判断。capture 开启时,面板上的左键【不吞】、
   CallNextHookEx 放行(控制面板因此可正常点击),且不调 on_left_click;
   面板外的左键照旧吞掉并回调。吞/放判定抽为模块级纯函数 should_swallow()。

── is_passthrough 的线程与性能约束(必须遵守,违反会被系统静默摘钩)──
* 它在【钩子线程】内被同步调用,系统里每一次鼠标事件都会经过这里;
* 只允许读取 App 预先缓存好的面板矩形等纯数据(应为微秒级),【禁止】任何
  tkinter / COM / 窗口句柄查询 / 文件与网络 IO / 等待锁等可能阻塞的调用;
* is_passthrough 抛出异常时按"吞掉"处理(安全侧),仅打印一次警告,
  绝不让异常逃出钩子回调;
* 本模块的对外判定入口是纯函数 should_swallow(capture, x, y, is_passthrough),
  可离线测试(见 tests/test_hook_passthrough.py)。

对外接口(被 app.py 按此约定使用):
    LLMouseHook(on_left_click, is_passthrough=None, **kw)
    + start() / set_capture(bool) / stop()
    **kw 为预留扩展参数,当前忽略。只传 on_left_click 的旧用法照常工作。

非 Windows 平台或钩子安装失败时抛出带原因的 RuntimeError(可读)。
"""
from __future__ import annotations

import atexit
import ctypes
import queue
import sys
import threading
import traceback
from typing import Callable

__all__ = ["LLMouseHook", "should_swallow", "IS_WINDOWS"]

IS_WINDOWS = sys.platform == "win32"

# ---------------- WinAPI 常量(数值在任何平台定义都安全) ----------------
WH_MOUSE_LL = 14              # 低级鼠标钩子
HC_ACTION = 0                 # nCode == HC_ACTION 才是有效事件
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_APP_QUIT = 0x8001          # WM_APP+1:通知消息泵线程退出(原型同款)
LLMHF_INJECTED = 0x1          # 事件由 SendInput 等注入的标记

if IS_WINDOWS:
    from ctypes import wintypes

    ULONG_PTR = ctypes.c_size_t
    LRESULT = ctypes.c_ssize_t
    WPARAM = ctypes.c_size_t
    LPARAM = ctypes.c_ssize_t
    HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, WPARAM, LPARAM)

    class _POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    class _MSLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [("pt", _POINT), ("mouseData", wintypes.DWORD),
                    ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ULONG_PTR)]

    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32
    _user32.SetWindowsHookExW.argtypes = [wintypes.INT, HOOKPROC,
                                          wintypes.HINSTANCE, wintypes.DWORD]
    _user32.SetWindowsHookExW.restype = wintypes.HHOOK
    _user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
    _user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    _user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, WPARAM, LPARAM]
    _user32.CallNextHookEx.restype = LRESULT
    _user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                    wintypes.UINT, wintypes.UINT]
    _user32.GetMessageW.restype = ctypes.c_int
    _user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT,
                                           WPARAM, LPARAM]
    _user32.PostThreadMessageW.restype = wintypes.BOOL


# ---------------- 吞/放判定(模块级纯函数,便于离线测试) ----------------
# is_passthrough 异常"只警告一次"标志:钩子线程内 print 也算轻活,
# 但绝不能每次鼠标事件都打印(会刷屏 + 拖慢钩子回调);竞态最多导致多印一次,可接受。
_passthrough_warned = False


def _warn_passthrough_error(exc: BaseException) -> None:
    """is_passthrough 抛异常时打印一次警告(之后静默,仍按"吞掉"处理)。"""
    global _passthrough_warned
    if not _passthrough_warned:
        _passthrough_warned = True
        print(f"[neuropet.core.hook] is_passthrough 在钩子线程内抛出异常 {exc!r},"
              "该次按\"吞掉\"处理(仅警告一次,避免拖慢钩子回调)。", flush=True)


def should_swallow(capture: bool,
                   x: int,
                   y: int,
                   is_passthrough: Callable[[int, int], bool] | None) -> bool:
    """判定 capture 开启时坐标 (x, y) 处的左键事件是否应被钩子吞掉。

    规则(与子 agent B 的接口契约一致):
      - capture 为 False                       -> 不吞(返回 False);
      - capture 为 True 且 is_passthrough 为 None -> 吞(兼容旧用法:全屏捕获);
      - is_passthrough(x, y) 为 True(面板内)  -> 不吞,放行给系统;
      - 其余(面板外)                          -> 吞;
      - is_passthrough 抛异常                  -> 吞(安全侧),异常不外逃、仅警告一次。

    本函数必须保持轻量:除一次 is_passthrough 调用外无任何 IO/阻塞,
    供钩子回调每次左键事件调用;对 None / 负数等非法坐标也不抛异常。
    """
    if not capture:
        return False                       # 非投喂模式:一律放行
    if is_passthrough is None:
        return True                        # 旧用法:未提供面板判定,全屏吞掉
    try:
        if is_passthrough(x, y):
            return False                   # 点在控制面板内:放行,面板可点击
    except Exception as exc:               # 不让异常逃出钩子回调(坑 5)
        _warn_passthrough_error(exc)
        return True                        # 异常时按"吞掉"处理(安全侧)
    return True                            # 面板外:吞


class LLMouseHook:
    """WH_MOUSE_LL 全局鼠标钩子:capture 开启时吞掉左键 DOWN/UP 并回调点击坐标。

    线程模型(3 线):
      - 主线程:构造 / start / set_capture / stop;
      - "ll-mouse-hook" 钩子线程:安装钩子 + GetMessageW 消息泵;回调只入队;
      - "ll-mouse-dispatch" 派发线程:从队列取事件并调用 on_left_click(x, y)
        (业务回调绝不在钩子线程内执行,避免钩子回调超时被系统静默摘除)。

    is_passthrough(x, y) -> bool:由 App 传入"该点是否位于控制面板内"的快速
    判断,在钩子线程内被调用——只能读缓存矩形,禁止 tkinter/COM 等阻塞调用
    (详见模块 docstring)。传 None 表示无面板、全屏吞(向后兼容旧用法)。
    """

    def __init__(self,
                 on_left_click: Callable[[int, int], None] | None = None,
                 is_passthrough: Callable[[int, int], bool] | None = None,
                 **kw) -> None:
        if not IS_WINDOWS:
            raise RuntimeError(
                "neuropet.core.hook.LLMouseHook 仅支持 Windows(WH_MOUSE_LL 为 WinAPI);"
                f"当前平台为 {sys.platform!r},投喂模式无法全局捕获左键点击。")
        if not callable(on_left_click):
            raise TypeError("on_left_click 必须是可调用对象,签名为 on_left_click(x: int, y: int)")
        if is_passthrough is not None and not callable(is_passthrough):
            raise TypeError(
                "is_passthrough 必须是可调用对象,签名为 is_passthrough(x: int, y: int) -> bool;或为 None")
        self.on_left_click = on_left_click
        self.is_passthrough = is_passthrough  # 面板穿透判定(钩子线程内调用,须轻量)
        self.capture_on: bool = False          # 投喂捕获开关(set_capture 切换)
        # 钩子回调 -> 派发线程的事件队列;满则丢弃并计数(绝不阻塞钩子回调)
        self._events: "queue.Queue[tuple[int, int, int, bool]]" = queue.Queue(maxsize=1024)
        self._quit = threading.Event()         # 通知派发线程退出
        self._ready = threading.Event()        # 钩子线程安装完成(无论成败)
        self._unhooked = threading.Event()     # 已在线程内 Unhook
        self._proc = None                      # HOOKPROC,持引用防 GC(坑 5)
        self._thread: threading.Thread | None = None
        self._dispatch: threading.Thread | None = None
        self._tid = 0
        self._hh = None
        self._stop_requested = False
        # 统计(仅供调试/面板展示)
        self.swallowed_down = 0
        self.swallowed_up = 0
        self.dropped = 0                       # 队列满被丢弃的事件数
        self.last_click: tuple[int, int] | None = None

    # ---------------- 对外接口 ----------------
    @property
    def capturing(self) -> bool:
        """当前是否处于"吞左键"状态。"""
        return self.capture_on

    def start(self, timeout: float = 3.0) -> None:
        """启动钩子线程 + 派发线程并安装 WH_MOUSE_LL。失败抛 RuntimeError。"""
        if not IS_WINDOWS:
            raise RuntimeError("LLMouseHook 仅支持 Windows 平台。")
        if self._thread is not None:
            return                              # 已启动,幂等
        if not callable(self.on_left_click):
            raise TypeError("on_left_click 必须是可调用对象,签名为 on_left_click(x: int, y: int)")
        self._proc = HOOKPROC(self._on_event)   # 必须先创建并持有引用防 GC
        self._dispatch = threading.Thread(target=self._dispatch_loop,
                                          name="ll-mouse-dispatch", daemon=True)
        self._thread = threading.Thread(target=self._run,
                                        name="ll-mouse-hook", daemon=True)
        self._dispatch.start()
        self._thread.start()
        try:
            if not self._ready.wait(timeout):
                raise RuntimeError("LLMouseHook:钩子线程超时未就绪(消息泵启动失败)。")
            if not self._hh:
                err = _kernel32.GetLastError()
                raise RuntimeError(
                    f"LLMouseHook:SetWindowsHookExW(WH_MOUSE_LL) 安装失败,"
                    f"GetLastError={err}(受限环境/沙箱可能禁止全局钩子)。")
        except Exception:
            self.stop(timeout)                  # 半启动状态清理后再抛
            raise
        atexit.register(self._stop_safe)        # 进程退出兜底卸载

    def set_capture(self, on: bool) -> None:
        """开启/关闭"吞左键并回调坐标"。线程安全(GIL 下布尔读写原子)。"""
        self.capture_on = bool(on)

    def stop(self, timeout: float = 3.0) -> None:
        """请求钩子线程退出(线程内 Unhook)并回收派发线程。幂等。"""
        if self._stop_requested:
            return
        self._stop_requested = True
        if self._tid:
            # 正确退出姿势:只投递 WM_APP_QUIT,由泵线程自己 Unhook(坑 5)
            _user32.PostThreadMessageW(self._tid, WM_APP_QUIT, 0, 0)
        if self._thread:
            self._thread.join(timeout)
        self._unhooked.wait(timeout)
        self._quit.set()                        # 钩子线程已死,派发线程排干队列后退出
        if self._dispatch:
            self._dispatch.join(timeout)
        atexit.unregister(self._stop_safe)

    # ---------------- 钩子线程 ----------------
    def _run(self) -> None:
        """钩子线程:安装 -> 消息泵 -> 线程内 Unhook(三步必须同线程)。"""
        self._tid = _kernel32.GetCurrentThreadId()
        self._hh = _user32.SetWindowsHookExW(WH_MOUSE_LL, self._proc, None, 0)
        self._ready.set()
        if not self._hh:
            return
        msg = wintypes.MSG()
        while True:
            r = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r <= 0 or msg.message == WM_APP_QUIT:
                break
        _user32.UnhookWindowsHookEx(self._hh)   # 关键:在钩子线程内卸载
        self._hh = None
        self._unhooked.set()

    def _on_event(self, n_code: int, w_param: int, l_param: int) -> int:
        """钩子回调:红线是"快进快出"(超时会被系统静默摘钩,坑 5)。

        判定路径刻意保持最轻:capture 关闭时不解析坐标直接放行;
        capture 开启时解析一次坐标 -> 调模块级纯函数 should_swallow 定吞/放,
        吞则入队(非阻塞)并 return 1,放行则 CallNextHookEx,绝无阻塞调用。
        """
        if n_code == HC_ACTION:
            msg = int(w_param)
            if msg in (WM_LBUTTONDOWN, WM_LBUTTONUP):
                if self.capture_on:
                    info = ctypes.cast(l_param, ctypes.POINTER(_MSLLHOOKSTRUCT)).contents
                    x, y = int(info.pt.x), int(info.pt.y)
                else:
                    x = y = 0                   # capture 关闭:坐标不会被用到
                if should_swallow(self.capture_on, x, y, self.is_passthrough):
                    try:
                        self._events.put_nowait((msg, x, y,
                                                 bool(info.flags & LLMHF_INJECTED)))
                    except queue.Full:          # 队列满只丢弃,绝不阻塞钩子线程
                        self.dropped += 1
                    if msg == WM_LBUTTONDOWN:
                        self.swallowed_down += 1
                        self.last_click = (x, y)
                    else:
                        self.swallowed_up += 1
                    return 1                    # DOWN/UP 都吞掉,避免孤儿 UP(坑 5)
        return _user32.CallNextHookEx(None, n_code, w_param, l_param)

    # ---------------- 派发线程 ----------------
    def _dispatch_loop(self) -> None:
        """把钩子线程入队的左键事件派发给业务回调(一次点击只派发 DOWN 一次)。"""
        while True:
            try:
                msg, x, y, _injected = self._events.get(timeout=0.1)
            except queue.Empty:
                if self._quit.is_set():
                    return
                continue
            if msg != WM_LBUTTONDOWN:
                continue                        # UP 只吞不回调:一次点击一次投喂
            try:
                if self.on_left_click is not None:
                    self.on_left_click(x, y)
            except Exception:                   # 业务回调异常不能拖垮派发线程
                traceback.print_exc()

    def _stop_safe(self) -> None:
        """atexit 兜底:任何异常都不能阻止进程退出。"""
        try:
            self.stop()
        except Exception:
            pass
