"""Capture only this program's own demo windows, using disposable pet data."""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    with tempfile.TemporaryDirectory(prefix="neuropet-panel-capture-") as temp:
        os.environ["NEUROPET_DATA_DIR"] = temp
        from neuropet.core.windowing import set_dpi_aware
        set_dpi_aware()
        from neuropet.core.app import App
        from neuropet.ui.compact_panel import ControlPanel
        from PIL import ImageGrab
        app = App()
        panel = ControlPanel(app)
        app._panel = panel
        panel.set_on_drop_request(app._on_panel_drop)
        for i in range(10):
            panel.add_pet("species.cockroach" if i % 2 == 0 else "species.fruitfly")
        panel.tree.selection_set(panel.tree.get_children()[0])
        app.root.update()
        panel.tree.yview_moveto(0)
        def capture(win, name):
            app.root.update()
            time.sleep(.25)  # Let Windows finish its own-window show animation.
            app.root.update()
            x, y = win.winfo_rootx(), win.winfo_rooty()
            path = ROOT / "assets" / "demos" / name
            ImageGrab.grab((x, y, x + win.winfo_width(), y + win.winfo_height())).save(path)
            print(path)
        try:
            capture(panel.win, "panel.png")
            for name in ("feed_now_btn", "freeze_btn", "hide_btn", "memory_btn", "settings_btn", "pause_all_btn"):
                button = getattr(panel, name)
                bottom = button.winfo_rooty() + button.winfo_height() - panel.win.winfo_rooty()
                print(f"{name}: bottom={bottom}, window={panel.win.winfo_height()}")
                assert bottom <= panel.win.winfo_height(), f"{name} is clipped"
            panel.memory_btn.invoke()
            capture(panel.extra_windows["memory"], "memory.png")
            panel.extra_windows["memory"].destroy()
            panel.settings_btn.invoke()
            capture(panel.extra_windows["settings"], "settings.png")
        finally:
            app.shutdown()


if __name__ == "__main__":
    main()
