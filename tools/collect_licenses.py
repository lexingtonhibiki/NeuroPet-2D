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
