# -*- coding: utf-8 -*-
"""全套件跑手：一条命令跑完 tests/test_*.py，输出紧凑摘要。

为什么需要它（r25 实测教训）：
- 项目原有跑法是 shell 单行循环 `for f in tests/test_*.py; do python "$f"; done`。
  该命令**以 `for` 开头**，不匹配 `.claude/settings.local.json` 里任何
  `Bash(python ...)` 白名单规则 → 一旦权限判定模型限流、auto 模式 fail-closed，
  **整套件就跑不了**（2026-09-22 已因此损失数小时，先后两次）。
  本脚本让"跑全套件"变成**单条 `python tools/run_all_tests.py`**，命中白名单即不受判定器影响。
- 枚举**必须来自文件系统**（`Path.rglob`），不能用 `pkgutil.walk_packages` 之类的包遍历 API:
  后者遇到导入失败的包会**静默跳过且不再下钻**，覆盖面会虚高（同源教训见 workflow §5 坑表）。

用法::

    python tools/run_all_tests.py            # 全部
    python tools/run_all_tests.py test_body  # 只跑名字含该子串的

退出码：0 = 全绿；1 = 有失败；2 = 一个都没找到（防"静默跑空"）。
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = ROOT / "tests"
OUT_DIR = ROOT / "logs"          # 失败正文落盘处(见主循环)
PER_TEST_TIMEOUT_S = 600


def discover(pattern: str = "") -> list[Path]:
    """文件系统枚举（不要换成包遍历 API，见模块 docstring）。"""
    files = sorted(TESTS_DIR.rglob("test_*.py"))
    if pattern:
        files = [f for f in files if pattern in f.name]
    return files


def main() -> int:
    pattern = sys.argv[1] if len(sys.argv) > 1 else ""
    files = discover(pattern)
    if not files:
        print(f"[runner] 未发现任何 test_*.py（pattern={pattern!r}）——拒绝静默通过", flush=True)
        return 2

    print(f"[runner] 发现 {len(files)} 套（{TESTS_DIR}）", flush=True)
    failed: list[str] = []
    t0 = time.perf_counter()

    for f in files:
        rel = f.relative_to(ROOT).as_posix()
        t = time.perf_counter()
        try:
            proc = subprocess.run(
                [sys.executable, str(f)],
                cwd=str(ROOT), capture_output=True, text=True,
                timeout=PER_TEST_TIMEOUT_S, encoding="utf-8", errors="replace",
            )
            rc = proc.returncode
            last = next(
                (ln.strip() for ln in reversed(proc.stdout.splitlines()) if ln.strip()),
                "(无输出)",
            )
        except subprocess.TimeoutExpired:
            rc, last, out, err = 124, f"超时 >{PER_TEST_TIMEOUT_S}s", "", ""
        else:
            out, err = proc.stdout, proc.stderr
        dt = time.perf_counter() - t
        mark = "ok  " if rc == 0 else "FAIL"
        print(f"[{mark}] {rel:48s} rc={rc:<3d} {dt:5.1f}s | {last[:200]}", flush=True)
        if rc != 0:
            failed.append(rel)
            # 失败必须留下**可诊断的现场**:只留最后一行 ⇒ 复盘 flake 时"不知道红在哪"。
            # (r25 实测:两次全套件各红一个**不同**套件,而两者的失败正文都被本跑手吃掉,
            #  只能靠单跑复现碰运气 —— 判据红了却拿不到红的原因,等于没测。)
            dump = OUT_DIR / f"_runner_{f.stem}.txt"
            dump.write_text(
                f"# {rel}  rc={rc}\n\n== stdout ==\n{out}\n== stderr ==\n{err}\n",
                encoding="utf-8",
            )
            print(f"[runner]   失败正文 → {dump.relative_to(ROOT).as_posix()}", flush=True)

    total = time.perf_counter() - t0
    print(f"\n[runner] {len(files) - len(failed)}/{len(files)} 通过，用时 {total:.0f}s", flush=True)
    if failed:
        print("[runner] 失败清单:", flush=True)
        for name in failed:
            print(f"  - {name}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
