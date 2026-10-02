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
        "NeuroPet 2D\n\n"
        "双击 NeuroPet-2D.exe。启动默认一只蟑螂和一只果蝇，可添加到10只。\n"
        "选中列表中的宠物，可以投喂、暂停、隐藏或召回。\n"
        "按住虫体拖动；全部暂停控制整个桌面。\n"
        "关闭面板后宠物继续活动，从系统托盘重新打开或保存退出。\n\n"
        "存档保存在旁边的 data/ 文件夹。更新时关闭程序并保留这个目录。\n"
        "移动程序时，EXE 与 _internal/ 文件夹一起移动。\n\n"
        "源代码、操作说明和性能报告：\n"
        "https://github.com/lexingtonhibiki/NeuroPet-2D\n"
        "项目许可证为 MIT，依赖许可证和 pystray 的 Python 源码轮子位于 licenses/。\n",
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
