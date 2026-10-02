"""入口:python -m neuropet.main"""
from __future__ import annotations


def main() -> None:
    from neuropet.core.windowing import set_dpi_aware
    set_dpi_aware()  # 必须先于任何窗口创建
    from neuropet.core.app import App
    app = App()
    try:
        app.run()
    except KeyboardInterrupt:
        app.shutdown()


if __name__ == "__main__":
    main()
