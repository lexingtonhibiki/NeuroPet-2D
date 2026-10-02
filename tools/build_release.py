"""Reproducible portable build: embedded gait tables, no user archives."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

def main():
    from neuropet.ui.tray import make_tray_image
    icon = ROOT / "build" / "pet.ico"
    icon.parent.mkdir(exist_ok=True)
    make_tray_image(256).save(icon, sizes=[(16,16), (32,32), (48,48), (256,256)])
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--onedir", "--windowed", "--name", "NeuroPet-2D", "--icon", str(icon),
        "--add-data", "data/gait;data/gait", "--hidden-import", "pystray._win32",
        "--exclude-module", "numpy", "--exclude-module", "matplotlib",
        "--exclude-module", "pytest", "--exclude-module", "OpenGL",
        "--exclude-module", "moderngl", *sys.argv[1:], "NeuroPet.py"], cwd=ROOT, check=True)
    from tools.collect_licenses import main as collect_licenses
    collect_licenses()

if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    main()
