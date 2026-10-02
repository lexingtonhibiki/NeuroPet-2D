"""Preserve distribution license texts alongside the executable."""
import importlib.metadata
import sys
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "dist" / "NeuroPet-2D" / "licenses"

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for package in ("Pillow", "pystray", "pyinstaller", "six"):
        dist = importlib.metadata.distribution(package)
        for file in dist.files or []:
            if any(part in file.name.lower() for part in ("license", "copying")):
                shutil.copyfile(dist.locate_file(file), OUT / f"{package}-{file.name}")
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.exists():
        shutil.copyfile(python_license, OUT / "Python-LICENSE.txt")
    tk_license = Path(sys.base_prefix) / "tcl" / "tk8.6" / "license.terms"
    if tk_license.exists():
        shutil.copyfile(tk_license, OUT / "Tk-license.terms")
    shutil.copyfile(ROOT / "LICENSE", OUT.parent / "LICENSE.txt")
    shutil.copyfile(ROOT / "third_party" / "pystray-0.19.5-py2.py3-none-any.whl",
                    OUT / "pystray-0.19.5-py2.py3-none-any.whl")
    (OUT.parent / "README.txt").write_text(
        "NeuroPet 2D / 中英双语 · Bilingual (zh-CN / en)\n"
        "====================================================\n\n"
        "中文 · Chinese\n"
        "-------------\n"
        "双击 NeuroPet-2D.exe。启动默认一只蟑螂和一只果蝇，可添加到10只。\n"
        "选中列表中的宠物，可以投喂、暂停、隐藏或召回。\n"
        "按住虫体拖动；全部暂停控制整个桌面。\n"
        "关闭面板后宠物继续活动，从系统托盘重新打开或保存退出。\n"
        "设置里可以切换界面语言：简体中文 / English，切换后立即生效并保存。\n"
        "设置里可以调全局爬行速度（0.5×~8×，默认 2×）、开关高速拖尾、\n"
        "勾选开机自启动（只写当前用户，不需要管理员）。\n"
        "宠物自己写下的记忆保持原文，不会被翻译。\n\n"
        "English\n"
        "-------\n"
        "Double-click NeuroPet-2D.exe. One cockroach and one fruit fly start by\n"
        "default; add up to 10 pets on the desk.\n"
        "Select a pet in the list to feed, pause, hide or recall it.\n"
        "Drag an insect to move it; Pause all controls the whole desk.\n"
        "Closing the panel keeps the pets running: reopen it or save and quit\n"
        "from the system tray.\n"
        "Switch the interface language under Settings - Language:\n"
        "Simplified Chinese / English, applied and saved immediately.\n"
        "Settings also holds the global crawling speed (0.5x-8x, default 2x), the\n"
        "speed-trail switch and start with Windows (current user only, no admin).\n"
        "After moving this folder, re-tick start with Windows so the path updates.\n"
        "Memories your pets wrote stay in their original wording, untranslated.\n\n"
        "存档 / Saves\n"
        "------------\n"
        "存档保存在旁边的 data/ 文件夹。更新时关闭程序并保留这个目录。\n"
        "Saves live in the neighbouring data/ folder. Close the program and keep\n"
        "that folder when updating.\n"
        "移动程序时，EXE 与 _internal/ 文件夹一起移动。\n"
        "When moving the program, move the EXE together with _internal/.\n\n"
        "源代码、操作说明和性能报告 / Source, docs and performance report:\n"
        "https://github.com/lexingtonhibiki/NeuroPet-2D\n"
        "项目许可证为 MIT，依赖许可证和 pystray 的 Python 源码轮子位于 licenses/。\n"
        "MIT licensed; dependency licenses and pystray's source wheel are in\n"
        "licenses/.\n",
        encoding="utf-8-sig")
    (OUT / "README.txt").write_text(
        "NeuroPet-2D: MIT; see LICENSE.txt in the portable package.\n"
        "Pillow 11.3.0: https://github.com/python-pillow/Pillow/tree/11.3.0\n"
        "pystray 0.19.5 (LGPLv3): exact pure-Python source wheel provided here.\n"
        "https://github.com/moses-palmer/pystray/tree/v0.19.5\n"
        "six: https://github.com/benjaminp/six\n"
        "Python / Tcl / Tk: bundled Python license text.\n"
        "PyInstaller bootloader: COPYING.txt includes the distribution exception.\n"
        "Rebuild with Python 3.13, requirements-dev.txt and tools/build_release.py.\n",
        encoding="utf-8")

if __name__ == "__main__":
    main()
