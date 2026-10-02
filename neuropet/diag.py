# -*- coding: utf-8 -*-
"""诊断日志:pythonw 无控制台时被吞的 print/traceback 落到 logs/diag.log。

r22 教训:start_3d.bat 用 pythonw(无 stdout),渲染回退/每帧异常全部静默,
"宠物不显示"零线索。有控制台时照常 print,同时追加写文件(环形 ≤64KB)。
"""
from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

_LOG = Path(__file__).resolve().parents[1] / "logs" / "diag.log"
_CAP = 65536


def _console(text: str) -> None:
    """有控制台才写。流**存在但已损坏**时(pythonw/--noconsole 下被重定向到
    失效句柄)write 抛 OSError(EINVAL)——r24 实测该异常穿出 _tick 的 except、
    再穿出 tkinter 回调,直接终止整个程序。故一次失败即停用该流(print 对
    None 是无操作,此后所有 print 调用自动安全)。"""
    if sys.stdout is None:
        return
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except Exception:
        sys.stdout = None                   # 坏流停用,不再二次伤害调用方


def _file(text: str) -> None:
    try:
        _LOG.parent.mkdir(exist_ok=True)
        with _LOG.open("a", encoding="utf-8") as f:
            f.write(text)
        if _LOG.stat().st_size > _CAP:      # 环形:超限只留末半,防刷盘无界
            txt = _LOG.read_text(encoding="utf-8", errors="ignore")
            _LOG.write_text(txt[-_CAP // 2:], encoding="utf-8")
    except Exception:
        pass                                # 诊断日志自身永不抛


def log(msg: str) -> None:
    line = f"{time.strftime('%m-%d %H:%M:%S')} {msg}\n"
    _console(line)
    _file(line)


def log_exc(where: str) -> None:
    exc = traceback.format_exc(limit=8)
    log(f"[{where}] {exc.strip().splitlines()[-1]}")
    _console(exc)


def report_callback_exception(etype: type, val: BaseException, tb) -> None:
    """替代 tkinter 默认 report_callback_exception。默认实现用 print 打印,
    坏流下该 print 自身抛 OSError 并穿出 mainloop → 整个程序退出(r24 实测;
    面板按钮回调等不经 _tick 的异常也走这里)。本实现只写 diag.log,绝不外抛。"""
    try:
        text = "".join(traceback.format_exception(etype, val, tb, limit=8))
    except Exception:
        text = f"{etype} {val}\n"
    log(f"[tk] {etype.__name__}: {val}")
    _file(text)
