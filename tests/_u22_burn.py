# -*- coding: utf-8 -*-
"""账本行 22 的可控负载源(只在取证时用,不属任何判据)。

为什么要自己造负载:本机不是独占机,「别人恰好没在跑」不可复现。要证明
「判据是否被未受控负载打成假红」,必须能把负载当成**自变量**拧大拧小。

用法::

    python tests/_u22_burn.py <进程数> <秒数>       # 前台等待
    # 取背景负载(跑手那边调用):
    python tests/_u22_burn.py 8 900 &

每个子进程是一个纯 Python 忙循环(单线程、无 I/O、无内存增长),
所以它模拟的正是「本机另有 N 个他人 python 进程在算」。
**负载量必须写进报告**:N=12 会把这台 12 逻辑核的机器打满。
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

CHILD = r"""
import time, os
t_end = time.time() + float(os.environ["U22_BURN_S"])
s = int(os.environ.get("U22_SEED", "1"))
while time.time() < t_end:
    for _ in range(200000):
        s = (s * 1103515245 + 12345) & 0x7FFFFFFF
    if s == 0:
        print("impossible")
"""


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0
    import os
    env = dict(os.environ, U22_BURN_S=str(secs))
    procs = []
    for i in range(n):
        procs.append(subprocess.Popen(
            [sys.executable, "-c", CHILD], env=dict(env, U22_SEED=str(i + 1)),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    print(f"[burn] {n} 个忙循环子进程,持续 {secs:.0f}s(pid "
          f"{[p.pid for p in procs]})", flush=True)
    t0 = time.time()
    for p in procs:
        p.wait()
    print(f"[burn] 结束,墙钟 {time.time() - t0:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
