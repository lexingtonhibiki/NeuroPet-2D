# -*- coding: utf-8 -*-
"""diag 流健壮性回归:坏/缺 stdout 时 log / log_exc / log() 调用方的 print
都不得抛异常(r24:pythonw/--noconsole 下 stdout 为 None 或坏句柄,原先
EINVAL 穿出 _tick 的 except 并穿出 tkinter mainloop → 程序退出)。

运行:python tests/test_diag_stream.py
全程离线:不创建窗口、不装鼠标钩子。
"""
from __future__ import annotations

import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _DeadStream:
    """存在的坏流:任何 write 都抛 EINVAL(复现 pythonw 失效句柄)。"""

    def write(self, _s: str) -> int:
        raise OSError(22, "Invalid argument")

    def flush(self) -> None:
        raise OSError(22, "Invalid argument")


def _with_diag_log(td: str):
    import neuropet.diag as diag
    orig = diag._LOG
    diag._LOG = Path(td) / "diag.log"
    return diag, orig


def test_dead_stream_never_raises() -> None:
    import neuropet.diag as diag

    with tempfile.TemporaryDirectory() as td:
        mod, orig_log = _with_diag_log(td)
        orig_out = sys.stdout
        say = lambda s: print(s, file=orig_out)          # noqa: E731
        try:
            sys.stdout = _DeadStream()
            say("[test] 坏 stdout:log() 不抛")
            mod.log("喂 hello")
            try:
                raise ValueError("boom")
            except ValueError:
                mod.log_exc("_tick")
            say("[test] 坏 stdout:log_exc() 不抛(流已停用,print 变 no-op)")
            assert sys.stdout is None, sys.stdout
            say("[test] 坏 stdout:stdout 已停用为 None,后续 print 无操作")
            print("业务侧 print:坏流已停用,应为无操作(不抛)")
        finally:
            sys.stdout = orig_out
            mod._LOG = orig_log

        # 关键不变量:异常内容仍然落盘(diag.log 是唯一线索)
        text = (Path(td) / "diag.log").read_text(encoding="utf-8")
        assert "喂 hello" in text, text
        assert "_tick" in text and "ValueError" in text, text


def test_none_stdout_and_healthy_stream() -> None:
    import neuropet.diag as diag

    with tempfile.TemporaryDirectory() as td:
        mod, orig_log = _with_diag_log(td)
        orig_out = sys.stdout
        try:
            sys.stdout = None
            print("[test] stdout=None(非交互 pythonw):log/log_exc 不抛")
            mod.log("x")
            try:
                raise KeyError("k")
            except KeyError:
                mod.log_exc("_step_frame")

            print("[test] 健康流:照常写控制台")
            buf = io.StringIO()
            sys.stdout = buf
            mod.log("healthy line")
            assert "healthy line" in buf.getvalue(), buf.getvalue()
        finally:
            sys.stdout = orig_out
            mod._LOG = orig_log

        text = (Path(td) / "diag.log").read_text(encoding="utf-8")
        assert "healthy line" in text and "KeyError" in text, text


def test_report_callback_exception_never_raises() -> None:
    import neuropet.diag as diag

    with tempfile.TemporaryDirectory() as td:
        mod, orig_log = _with_diag_log(td)
        orig_out = sys.stdout
        try:
            print("[test] 坏 stdout:report_callback_exception 不抛(替代 tk 默认)",
                  file=orig_out)
            sys.stdout = _DeadStream()
            try:
                raise OSError(22, "Invalid argument")
            except OSError:
                mod.report_callback_exception(*sys.exc_info())
            assert sys.stdout is None
        finally:
            sys.stdout = orig_out
            mod._LOG = orig_log

        text = (Path(td) / "diag.log").read_text(encoding="utf-8")
        assert "[tk]" in text and "OSError" in text, text


def main() -> None:
    test_dead_stream_never_raises()
    test_none_stdout_and_healthy_stream()
    test_report_callback_exception_never_raises()
    print("[diag-stream] ALL OK")


if __name__ == "__main__":
    main()
