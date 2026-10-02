"""便携包 ZIP:白名单打包 + 固定排序,产物可复现。

只收四样东西(与 v0.1.0 / v0.1.1 同口径):
    NeuroPet-2D.exe、_internal/、licenses/、README.txt、LICENSE.txt

运行期产生的 ``data/``、``logs/``、``.git`` 一律不收 —— 白名单是按**顶层名**
枚举的,新增目录不会悄悄混进去。条目顺序按路径排序固定,便于逐次比对。
"""
from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "dist" / "NeuroPet-2D"
WHITELIST = ("NeuroPet-2D.exe", "_internal", "licenses", "README.txt",
             "LICENSE.txt")


def entries(stage: Path = STAGE):
    """白名单内的全部文件(相对路径,已排序);缺失顶层名会被跳过。"""
    out = []
    for name in WHITELIST:
        path = stage / name
        if path.is_file():
            out.append(path.relative_to(stage).as_posix())
        elif path.is_dir():
            for item in path.rglob("*"):
                if item.is_file():
                    out.append(item.relative_to(stage).as_posix())
    return sorted(out)


def build(version: str, stage: Path = STAGE) -> Path:
    target = ROOT / "dist" / f"NeuroPet-2D-v{version}-windows-x64.zip"
    if target.exists():
        target.unlink()
    names = entries(stage)
    if not names:
        raise SystemExit(f"nothing to pack in {stage}")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for name in names:
            zf.write(stage / name, f"NeuroPet-2D/{name}")
    digest = hashlib.sha256(target.read_bytes()).hexdigest().upper()
    print(f"{target.relative_to(ROOT)}\n"
          f"  entries: {len(names)}\n"
          f"  bytes:   {target.stat().st_size:,}\n"
          f"  sha256:  {digest}")
    return target


def main() -> None:
    version = "0.2.0"
    for arg in sys.argv[1:]:
        if arg.startswith("--version="):
            version = arg.split("=", 1)[1]
    build(version)


if __name__ == "__main__":
    main()