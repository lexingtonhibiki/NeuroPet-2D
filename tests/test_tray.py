"""托盘离线测试(不注入真实托盘图标):图标绘制、模块导入、
菜单结构与回调映射(假 app 桩验证 显示面板/投喂开关/退出 走对路)。
运行:python tests/test_tray.py
真实托盘的点击/菜单交互依赖桌面环境,不在本测试范围(人工确认)。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ---------------- 假 app / 假面板桩 ----------------
class FakeWin:
    """Toplevel 桩:只暴露 tray._apply_toggle_panel 用到的最小接口。"""

    def __init__(self) -> None:
        self._state = "normal"
        self.calls: list[str] = []

    def state(self) -> str:
        return self._state

    def deiconify(self) -> None:
        self._state = "normal"
        self.calls.append("deiconify")

    def lift(self) -> None:
        self.calls.append("lift")

    def focus_force(self) -> None:
        self.calls.append("focus")


class FakePanel:
    def __init__(self) -> None:
        self.win = FakeWin()

    def hide(self) -> None:              # 与 ControlPanel.hide 同语义:withdraw
        self.win._state = "withdrawn"


class FakeApp:
    """App 桩:真实 EventBus + 可观测的 toggle_feeding/shutdown 调用。"""

    def __init__(self) -> None:
        from neuropet.core.bus import EventBus
        self.bus = EventBus()
        self.feeding = False
        self._panel = None
        self.toggles: list[bool] = []
        self.shutdowns = 0

    def toggle_feeding(self, on: bool) -> bool:
        self.feeding = on
        self.toggles.append(on)
        # 模拟 App.toggle_feeding 的行为:发布 user/feeding(托盘借此同步菜单勾选态)
        self.bus.publish("user/feeding", {"on": on})
        return on

    def shutdown(self) -> None:
        self.shutdowns += 1


def main() -> None:
    # 0) 模块可导入,且 tray.py 顶层不得导入 pystray(缺失时模块仍可导入 = 离线降级前提)
    import neuropet.ui.tray as _tray_mod
    assert "neuropet.ui.tray" in sys.modules
    assert "pystray" not in sys.modules, "tray.py 顶层导入了 pystray,离线降级会失效"

    from neuropet.ui.tray import (TOPIC_QUIT, TOPIC_TOGGLE_FEEDING,
                                  TOPIC_TOGGLE_PANEL, TrayIcon, make_tray_image)

    # 1) 图标 PIL 生成:尺寸/模式正确,且按尺寸缓存(同尺寸只画一次)
    img32 = make_tray_image(32)
    assert img32.size == (32, 32) and img32.mode == "RGBA"
    assert make_tray_image(32) is img32, "32px 图标应走缓存"
    img256 = make_tray_image(256)
    assert img256.size == (256, 256)
    out = Path(__file__).parents[1] / "scratch" / "tray_preview.png"
    out.parent.mkdir(exist_ok=True)
    img256.save(out)
    print(f"[tray-test] 图标绘制 OK: {img32.size} {img32.mode} -> 预览 {out}")

    # 2) 菜单结构与回调映射(不启动真实托盘线程;若 pystray 缺失则跳过结构断言)
    app = FakeApp()
    tray = TrayIcon(app)
    tray.stop()                          # 未启动时 stop 必须安全(幂等)
    tray._register_bus()                 # 只注册总线处理器,不起托盘线程
    try:
        import pystray
    except Exception:
        print("[tray-test] pystray 未安装,跳过菜单结构断言")
    else:
        menu = tray._build_menu(pystray)
        items = [it for it in menu if it is not pystray.Menu.SEPARATOR]
        assert [it.text for it in items] == ["显示控制面板", "投喂模式", "退出"]
        assert items[0].default is True, "左键单击 = default 项 = 显示/隐藏面板"
        assert items[1].checked is False, "初始投喂关闭 → 菜单不勾选"
        app.feeding = True
        assert items[1].checked is True, "勾选态应跟随 app.feeding"
        app.feeding = False

    # 3) 托盘线程回调 → 只经 bus 投递,不直接改状态
    tray._on_toggle_panel(None)
    tray._on_toggle_feeding(None)
    tray._on_quit(None)
    topics = [t for t, _ in list(app.bus._queue.queue)]   # 窥视,不消费(留给 drain)
    assert topics == [TOPIC_TOGGLE_PANEL, TOPIC_TOGGLE_FEEDING, TOPIC_QUIT], topics
    assert app.shutdowns == 0 and app.toggles == [], "托盘线程侧不得直接改状态"

    # 4) 主线程重放(drain):面板 显示/隐藏、投喂开关、退出
    app._panel = FakePanel()
    app._panel.win._state = "withdrawn"   # 场景:面板已隐藏(关闭按钮),由托盘唤出
    app.bus.drain()
    assert app._panel.win.calls == ["deiconify", "lift", "focus"], \
        "隐藏状态下面板应被 deiconify+lift+focus 唤出"
    assert app.toggles == [True] and app.feeding is True, "投喂应切到开"
    assert app.shutdowns == 1, "退出必须走 app.shutdown()"

    # 5) 面板可见时再次左键 → 隐藏;投喂再切一次 → 关
    tray._on_toggle_panel(None)
    tray._on_toggle_feeding(None)
    app.bus.drain()
    assert app._panel.win.state() == "withdrawn", "面板可见时左键应隐藏面板"
    assert app.toggles == [True, False] and app.feeding is False

    # 6) user/feeding 事件(面板勾选/超时自动关闭路径)→ 菜单同步不崩(_icon=None 安全)
    tray._on_feeding_changed("user/feeding", {"on": True})

    # 7) stop 幂等:再次调用不抛错
    tray.stop()

    print("[tray-test] ALL OK")


if __name__ == "__main__":
    main()
