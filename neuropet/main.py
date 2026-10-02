"""入口:python -m neuropet.main"""
from __future__ import annotations


def main() -> None:
    import sys
    if "--probe" in sys.argv:
        sys.argv.remove("--probe")
        from neuropet.release_probe import main as probe
        probe()
        return
    # v0.2.0 单实例守卫:只挡"开机自启动"的重复实例(登录时被启动了两次),
    # 手动启动与 --probe/--smoke 调试入口行为不变。已经有一份在跑就安静
    # 退出,避免桌面上出现两套宠物窗口。
    if "--autostart" in sys.argv:
        from neuropet.core.autostart import acquire_single_instance
        if not acquire_single_instance():
            return
    from neuropet.core.windowing import set_dpi_aware
    set_dpi_aware()  # 必须先于任何窗口创建
    from neuropet.core.app import App
    app = App()
    if "--smoke" in sys.argv:
        import json
        from pathlib import Path
        output = Path(sys.argv[sys.argv.index("--smoke")+1])
        def finish_smoke():
            output.write_text(json.dumps({"pets": len(app.pets),
                "capacity": app.cfg.max_pets, "tray": app._tray is not None,
                "panel": app._panel.win.winfo_viewable(),
                "uploads": app._upload_count}, indent=2), encoding="utf-8")
            app.shutdown()
        app.root.after(5000, finish_smoke)
    try:
        app.run()
    except KeyboardInterrupt:
        app.shutdown()


if __name__ == "__main__":
    main()