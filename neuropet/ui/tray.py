"""系统托盘:pystray + Pillow 实现(实现 agent T)。

需求背景:控制面板的关闭按钮 = 隐藏(withdraw),面板藏起后程序只剩悬浮窗,
此前只能重启程序才能找回面板;托盘成为隐藏后的唯一唤出入口。

线程模型(与 core.hook 鼠标钩子线程同一 marshal 模式):
- pystray 的消息泵运行在独立守护线程(本模块自建线程跑 Icon.run,
  等价于官方 run_detached,但持有线程对象便于带超时 join,退出绝不卡死);
- 托盘回调(左键/菜单项)发生在托盘线程,**禁止**直接触碰 tkinter;
  一律经 app.bus.publish_threaded 投递,由 App 主循环每帧 bus.drain()
  在主线程重放(不直接用 root.after——它非线程安全)。

交互:
- 左键单击图标 = 切换面板 显示/隐藏(pystray 中左键触发 default=True 菜单项);
- 右键菜单:[显示控制面板] / [投喂模式](勾选态)/ [退出];
- 退出经 bus 回主线程后调用 app.shutdown()(记忆保存、钩子卸载、timeEndPeriod)。

降级:pystray 导入失败(未安装/离线)时 start() 返回 False,App 打印警告后
无托盘运行,其余功能不受影响;stop() 幂等且 join 带超时,任何异常不阻塞退出。
"""
from __future__ import annotations

import threading

from ..core.i18n import t

# 托盘线程 → 主线程 的跨线程主题(主循环 bus.drain() 重放到主线程执行)
TOPIC_TOGGLE_PANEL = "tray/toggle_panel"
TOPIC_TOGGLE_FEEDING = "tray/toggle_feeding"
TOPIC_QUIT = "tray/quit"

# 图标缓存:按尺寸只绘制一次(键 = 目标边长)
_IMAGE_CACHE: dict[int, "object"] = {}

# 托盘线程 join 超时(秒):正常路径 WM_STOP 处理在毫秒级,超时只是兜底,
# 线程本身是 daemon,超时后进程仍可立即退出
_JOIN_TIMEOUT_S = 3.0


def make_tray_image(size: int = 32):
    """程序化绘制托盘图标:深色圆角底 + 简笔蟑螂(俯视:头/三节身体/六足/双触角)。

    以 8 倍超采样绘制后 LANCZOS 缩到目标尺寸(32px 下线条更平滑);
    结果按尺寸缓存,整个进程只画一次;纯内存,不落盘。
    返回 PIL RGBA Image。"""
    cached = _IMAGE_CACHE.get(size)
    if cached is not None:
        return cached

    from PIL import Image, ImageDraw

    ss = 8                       # 超采样倍率
    s = size * ss
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    def W(f: float) -> int:      # 归一化坐标 [0,1] → 像素
        return int(f * s)

    # ---- 深色圆角底(四角透明,融入托盘) ----
    pad = max(1, int(0.03 * s))
    d.rounded_rectangle([pad, pad, s - pad, s - pad], radius=int(0.22 * s),
                        fill=(30, 34, 40, 255),
                        outline=(96, 106, 118, 255), width=max(1, ss))
    outline_w = max(1, ss // 2)
    lw = max(1, int(0.04 * s))   # 足/触角线宽

    # ---- 身体:三节长椭圆(前胸/中胸/腹) ----
    d.ellipse([W(0.34), W(0.32), W(0.80), W(0.68)],
              fill=(122, 76, 36, 255), outline=(40, 26, 12, 255), width=outline_w)
    for fx in (0.50, 0.64):      # 分节线
        d.line([W(fx), W(0.335), W(fx), W(0.665)],
               fill=(40, 26, 12, 255), width=outline_w)

    # ---- 头 + 复眼 ----
    hx, hy, hr = W(0.28), W(0.50), W(0.085)
    d.ellipse([hx - hr, hy - hr, hx + hr, hy + hr],
              fill=(74, 45, 20, 255), outline=(40, 26, 12, 255), width=outline_w)
    er = W(0.022)
    for ey in (0.47, 0.53):      # 两粒浅色小复眼
        d.ellipse([hx - int(er * 1.2), W(ey) - er, hx + int(er * 1.2), W(ey) + er],
                  fill=(226, 190, 110, 255))

    # ---- 双触角:从头前缘向前外方展开的两段折线 ----
    for side in (-1, 1):
        d.line([hx - int(hr * 0.6), hy + side * int(hr * 0.45),
                W(0.14), hy + side * W(0.15),
                W(0.07), hy + side * W(0.20)],
               fill=(36, 20, 7, 255), width=lw, joint="curve")

    # ---- 六足:三对两段式(体缘 → 膝 → 足尖),前足朝前、后足朝后 ----
    for ax, tilt in ((0.42, -0.07), (0.56, 0.0), (0.70, 0.07)):
        for side in (-1, 1):
            x0, y0 = W(ax), W(0.5 + side * 0.12)          # 体缘锚点
            x1, y1 = x0 + W(tilt), W(0.5 + side * 0.30)   # 膝点:向外展开
            x2, y2 = x1 + W(tilt) + W(0.03), W(0.5 + side * 0.25)  # 足尖回收
            d.line([x0, y0, x1, y1, x2, y2],
                   fill=(58, 34, 16, 255), width=lw, joint="curve")

    im = im.resize((size, size), Image.LANCZOS)
    _IMAGE_CACHE[size] = im
    return im


class TrayIcon:
    """托盘图标封装。App 持有一个实例:start() 启动、stop() 收尾。

    对外接口:start() -> bool / stop() -> None(均幂等安全)。
    对 App 的控制全部经 bus 回主线程;本类在托盘线程侧只做 publish。
    """

    def __init__(self, app) -> None:
        self.app = app
        self._icon = None                          # pystray.Icon(启动后才有)
        self._thread: threading.Thread | None = None

    # ---------------- 生命周期 ----------------
    def start(self) -> bool:
        """启动托盘(独立守护线程)。成功 True;pystray 不可用返回 False(降级)。"""
        try:
            import pystray                         # 懒加载:缺失时优雅降级
        except Exception as exc:
            print(f"[tray] pystray 不可用({exc}),降级为无托盘运行")
            return False

        menu = self._build_menu(pystray)
        icon = pystray.Icon(
            "NeuroPet",
            icon=make_tray_image(32),
            title=t("tray.title"),
            menu=menu)
        self._icon = icon
        self._register_bus()

        # 自建线程跑 Icon.run(等价官方 run_detached,但线程对象可 join)
        self._thread = threading.Thread(
            target=icon.run, name="neuropet-tray", daemon=True)
        self._thread.start()
        return True

    def _register_bus(self) -> None:
        """注册主线程侧处理器(经 bus.drain() 重放,可安全触碰 tkinter)。
        独立成方法:离线测试无需启动真实托盘即可验证回调映射。"""
        bus = self.app.bus
        bus.subscribe(TOPIC_TOGGLE_PANEL, self._apply_toggle_panel)
        bus.subscribe(TOPIC_TOGGLE_FEEDING, self._apply_toggle_feeding)
        bus.subscribe(TOPIC_QUIT, self._apply_quit)
        # 面板勾选/超时自动关闭也会发布 user/feeding → 重建菜单保持勾选态同步
        bus.subscribe("user/feeding", self._on_feeding_changed)

    def stop(self) -> None:
        """移除托盘图标并停线程。幂等;join 带超时,任何异常不阻塞进程退出。"""
        icon, thread = self._icon, self._thread
        self._icon, self._thread = None, None
        if icon is None:
            return
        try:
            # win32 后端:PostMessage(WM_STOP) 非阻塞;托盘线程收到后
            # PostQuitMessage → 主循环清理(NIM_DELETE 移除图标)后自行退出
            icon.stop()
        except Exception as exc:
            print(f"[tray] 停止异常(忽略): {exc!r}")
        if thread is not None and thread.is_alive():
            thread.join(timeout=_JOIN_TIMEOUT_S)
            if thread.is_alive():
                print("[tray] 托盘线程未在超时内退出(daemon 线程,不阻塞退出)")

    # ---------------- 右键菜单(托盘线程构建/触发) ----------------
    def _build_menu(self, pystray):
        """构建右键菜单。「显示控制面板」标记 default=True → pystray 左键单击触发它。
        「投喂模式」勾选态为动态 callable:每次菜单项激活后 pystray 自动
        update_menu() 重建,勾选框随即反映最新 feeding 状态。
        文案是 callable(而非常量):语言切换后 `refresh_language()` 重建
        win32 菜单句柄时重新取值,托盘线程不重建、不重启。"""
        return pystray.Menu(
            pystray.MenuItem(lambda item: t("tray.panel"), self._on_toggle_panel, default=True),
            pystray.MenuItem(lambda item: t("tray.feeding"), self._on_toggle_feeding,
                             checked=lambda item: bool(
                                 getattr(self.app, "feeding", False))),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda item: t("tray.quit"), self._on_quit),
        )

    # ---- 托盘线程回调:只 publish,绝不触碰 tkinter/App 可变状态以外的东东 ----
    def _on_toggle_panel(self, icon=None, item=None) -> None:
        self.app.bus.publish_threaded(TOPIC_TOGGLE_PANEL, {})

    def _on_toggle_feeding(self, icon=None, item=None) -> None:
        on = not bool(getattr(self.app, "feeding", False))
        self.app.bus.publish_threaded(TOPIC_TOGGLE_FEEDING, {"on": on})

    def _on_quit(self, icon=None, item=None) -> None:
        self.app.bus.publish_threaded(TOPIC_QUIT, {})

    # ---------------- 主线程处理器(bus.drain() 重放) ----------------
    def _apply_toggle_panel(self, topic: str, data: dict) -> None:
        """主线程:显示/隐藏控制面板。panel.py 只读——通过其 win(Toplevel)
        deiconify+lift+focus_force 唤出,经 panel.hide() 隐藏。"""
        panel = getattr(self.app, "_panel", None)
        if panel is None:
            return
        try:
            win = panel.win
            if win.state() == "withdrawn":         # 隐藏中 → 唤出并置顶聚焦
                win.deiconify()
                win.lift()
                win.focus_force()
            else:                                  # 可见 → 隐藏(同关闭按钮)
                panel.hide()
        except Exception as exc:
            print(f"[tray] 面板切换失败: {exc!r}")

    def _apply_toggle_feeding(self, topic: str, data: dict) -> None:
        """主线程:切换投喂模式(勾选态经 toggle_feeding → user/feeding 同步回菜单)。"""
        on = bool(data.get("on"))
        if bool(getattr(self.app, "feeding", False)) == on:
            return                                 # 状态已一致(超时自动关闭等竞态)
        self.app.toggle_feeding(on)

    def _apply_quit(self, topic: str, data: dict) -> None:
        """主线程:完整退出(记忆保存、钩子卸载、托盘停止、timeEndPeriod)。"""
        self.app.shutdown()

    def _on_feeding_changed(self, topic: str, data: dict) -> None:
        self._refresh_menu()

    def refresh_language(self) -> None:
        """语言切换后同步托盘悬停标题与菜单文案(不重建图标、不重启线程)。"""
        icon = self._icon
        if icon is None:
            return
        try:
            icon.title = t("tray.title")     # pystray 公共 setter,win32 走 NIF_TIP
        except Exception:
            pass
        self._refresh_menu()

    def _refresh_menu(self) -> None:
        """投喂状态在面板/超时侧变化时重建菜单,勾选态与实际状态保持一致。
        win32 菜单对象无线程亲和性,主线程调用安全;失败仅忽略(不影响运行)。"""
        icon = self._icon
        if icon is None:
            return
        try:
            icon.update_menu()
        except Exception:
            pass
