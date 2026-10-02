# -*- coding: utf-8 -*-
"""r25 U0 性能/命中率插桩(默认**关闭**;env `NEUROPET_INSTR` 开启)。

纪律(架构 §8「日志不得重新引入裸 print 到坏流」):
- **只观测**:不改运动/渲染/学习的任何数值路径,不改变控制流判定;
- **关闭零成本**:`_ON=False` 时每个入口首行短路(一次全局查表 + 分支,
  实测 <0.1µs/次),判据「关闭时帧耗增量 ≈ 0」由 `tools/u0_baseline.py`
  开关对照自测;
- **输出只走 `neuropet.diag`**:pythonw/--noconsole 下 stdout 是坏句柄,
  裸 print 抛 OSError 会穿出 mainloop 直接退出程序(commit c9d7da1)。

用法::

    import neuropet.core.instr as _I
    _I.bump("rot_hit")                 # 计数
    _I.observe("render_ms", ms)        # 序列(算分位)
    _I.mark("interval_ms")             # 间隔序列(自动与上次 mark 求差)
    t = _I.begin(); work(); _I.end("panel_tick_ms", t)   # 计时(关闭时 t=None)
    _I.note("ws_mb", mb)               # 末值
    _I.seen("rot_keys", key)           # 集合去重(首见返回 True)
    print(_I.report())                 # 结构化 dict
    _I.dump("steady2")                 # 汇总写进 logs/diag.log
"""
from __future__ import annotations

import os
import time

__all__ = ["ON", "enabled", "set_enabled", "reset", "bump", "observe", "mark",
           "begin", "end", "note", "seen", "report", "dump", "format_report"]

_TRUE = ("1", "on", "true", "yes", "y")

ON: bool = os.environ.get("NEUROPET_INSTR", "").strip().lower() in _TRUE

_counters: dict = {}
_series: dict = {}
_notes: dict = {}
_sets: dict = {}
_last: dict = {}


def enabled() -> bool:
    return ON


def set_enabled(flag: bool) -> None:
    """显式开关(测试/探针用);生产恒由环境变量决定。"""
    global ON
    ON = bool(flag)


def reset() -> None:
    _counters.clear()
    _series.clear()
    _notes.clear()
    _sets.clear()
    _last.clear()


def bump(name: str, n: int = 1) -> None:
    if not ON:
        return
    _counters[name] = _counters.get(name, 0) + n


def observe(name: str, value: float) -> None:
    if not ON:
        return
    _series.setdefault(name, []).append(float(value))


def mark(name: str) -> None:
    """记一次时刻;第二次起把与上次的间隔(ms)写入该名的序列。"""
    if not ON:
        return
    t = time.perf_counter()
    prev = _last.get(name)
    _last[name] = t
    if prev is not None:
        _series.setdefault(name, []).append((t - prev) * 1000.0)


def begin() -> float | None:
    """计时起点(关闭时返回 None,零成本)。"""
    return time.perf_counter() if ON else None


def end(name: str, t0: float | None) -> None:
    if not ON or t0 is None:
        return
    _series.setdefault(name, []).append((time.perf_counter() - t0) * 1000.0)


def note(name: str, value) -> None:
    if not ON:
        return
    _notes[name] = value


def seen(name: str, key) -> bool:
    """key 是否**首次**出现(集合去重)。关闭时恒 True(调用方只看计数)。"""
    if not ON:
        return True
    s = _sets.setdefault(name, set())
    new = key not in s
    if new:
        s.add(key)
    return new


def _pct(sorted_xs: list, q: float) -> float:
    if not sorted_xs:
        return 0.0
    return sorted_xs[min(len(sorted_xs) - 1, int(len(sorted_xs) * q))]


def series_stats(name: str) -> dict:
    xs = _series.get(name) or []
    if not xs:
        return {"n": 0}
    s = sorted(xs)
    return {"n": len(s), "min": s[0], "p50": _pct(s, 0.50), "p90": _pct(s, 0.90),
            "p95": _pct(s, 0.95), "p99": _pct(s, 0.99), "max": s[-1],
            "mean": sum(s) / len(s)}


def series_raw(name: str) -> list:
    return list(_series.get(name) or [])


def report() -> dict:
    return {
        "counters": dict(_counters),
        "series": {k: series_stats(k) for k in _series},
        "sets": {k: len(v) for k, v in _sets.items()},
        "notes": dict(_notes),
    }


def format_report(tag: str = "") -> str:
    lines = [f"[instr] ==== {tag} ====" if tag else "[instr] ===="]
    for k in sorted(_counters):
        lines.append(f"[instr] c {k} = {_counters[k]}")
    for k in sorted(_series):
        st = series_stats(k)
        lines.append(
            f"[instr] s {k}: n={st['n']} min={st.get('min',0):.3f} "
            f"p50={st.get('p50',0):.3f} p90={st.get('p90',0):.3f} "
            f"p95={st.get('p95',0):.3f} p99={st.get('p99',0):.3f} "
            f"max={st.get('max',0):.3f}")
    for k in sorted(_sets):
        lines.append(f"[instr] set {k} = {len(_sets[k])}")
    for k in sorted(_notes):
        lines.append(f"[instr] n {k} = {_notes[k]}")
    return "\n".join(lines)


def dump(tag: str = "") -> None:
    from neuropet.diag import log as _dlog      # 延迟导入:插桩不参与启动链
    for line in format_report(tag).splitlines():
        _dlog(line)
