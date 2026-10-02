"""Check the documented portable artifact, default startup, and same-id restart."""
import json
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "dist" / "NeuroPet-2D"

def main():
    for file in ("NeuroPet-2D.exe", "README.txt", "LICENSE.txt",
                 "licenses/Pillow-LICENSE", "licenses/pystray-COPYING.LGPL",
                 "licenses/Python-LICENSE.txt", "licenses/pyinstaller-COPYING.txt",
                 "licenses/pystray-0.19.5-py2.py3-none-any.whl"):
        assert (PACKAGE / file).is_file(), f"Missing distribution file: {file}"
    with zipfile.ZipFile(PACKAGE / "licenses" / "pystray-0.19.5-py2.py3-none-any.whl") as source:
        assert "pystray/_win32.py" in source.namelist()
    with tempfile.TemporaryDirectory(prefix="neuropet-portable-smoke-") as temp:
        data = Path(temp) / "data"
        env = {**os.environ, "NEUROPET_DATA_DIR": str(data)}
        ids = None
        for attempt in range(2):
            result = Path(temp) / f"smoke-{attempt}.json"
            subprocess.run([str(PACKAGE / "NeuroPet-2D.exe"), "--smoke", str(result)],
                           cwd=temp, env=env, timeout=30, check=True)
            report = json.loads(result.read_text("utf-8"))
            assert report["pets"] == 2 and report["capacity"] == 10, report
            assert report["panel"] and report["tray"] and report["uploads"] > 0, report
            roster = json.loads((data / "pets.json").read_text("utf-8"))
            current = [pet["pet_id"] for pet in roster]
            if ids is not None:
                assert current == ids, "Restart created new pet identities"
            ids = current
        assert len(list((data / "profiles").glob("*"))) == 2
    print("PASS: portable notices/source, normal startup, tray, rendering and same-id restart")

if __name__ == "__main__":
    main()
