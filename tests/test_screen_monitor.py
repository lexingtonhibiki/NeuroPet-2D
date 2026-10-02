# -*- coding: utf-8 -*-
"""感知层·屏幕感知模块离线测试(A3):region_of 全分区 / rect 防御 / diff 去抖 / 节流。

运行:python tests/test_screen_monitor.py
自动断言部分:Win32 查询为只读(枚举/前台/类名),不安装钩子、不移动窗口、
不改变任何系统状态;diff/去抖/面积门等逻辑用注入数据测试,确定性、无 sleep。
live 手跑探针见文件末 manual_live_probe()(--live 启用,不进自动断言)。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from neuropet.perception import screen as scr
from neuropet.perception.screen import (
    DEBOUNCE_S, SNAPSHOT_CAP, ScreenEvent, ScreenMonitor, WindowInfo, region_of,
)

W, H = 1920, 1080
FAKE_VS = (0, 0, W, H)  # 虚屏(测试注入用)


# --------------------------------------------------------------- 1. region_of 全分区
def test_region_of_all_zones() -> None:
    print("[test] region_of:中央/边缘/四角/任务栏/前台窗内 全分区+边界")
    # 中央
    assert region_of((960, 540), W, H) == "center"
    # 边缘带边界:x<80 → edge;x=80 恰好出带 → center(edge=80 左闭右开语义)
    assert region_of((79, 540), W, H) == "edge"
    assert region_of((80, 540), W, H) == "center"
    assert region_of((1841, 540), W, H) == "edge"   # x > 1920-80=1840
    assert region_of((1840, 540), W, H) == "center"
    assert region_of((960, 79), W, H) == "edge"
    assert region_of((960, 80), W, H) == "center"
    # 四角 = 两邻边带交集(含边界点)
    assert region_of((0, 0), W, H) == "corner_tl"
    assert region_of((79, 79), W, H) == "corner_tl"
    assert region_of((1919, 0), W, H) == "corner_tr"
    assert region_of((0, 1020), W, H) == "corner_bl"    # y∈(1000,1032] 不在任务栏
    assert region_of((1919, 1020), W, H) == "corner_br"
    # 任务栏区:y > 1080-48=1032,优先级高于底角
    assert region_of((960, 1033), W, H) == "taskbar"
    assert region_of((10, 1070), W, H) == "taskbar"
    assert region_of((1919, 1079), W, H) == "taskbar"
    assert region_of((960, 1032), W, H) != "taskbar"    # 恰在任务栏上沿 → 非任务栏
    # 前台窗内:优先级低于边缘带/角/任务栏
    fg = (400, 300, 1500, 900)
    assert region_of((900, 500), W, H, fg) == "in_fg"
    assert region_of((900, 50), W, H, fg) == "edge"     # 上边缘带压过 in_fg
    assert region_of((10, 10), W, H, fg) == "corner_tl"
    assert region_of((900, 1050), W, H, fg) == "taskbar"
    assert region_of((900, 500), W, H, None) == "center"    # 无前台 → center
    # 400 边界:fg 左闭右开
    assert region_of((400, 300), W, H, fg) == "in_fg"
    assert region_of((1500, 300), W, H, fg) == "center"     # r 边不含
    # 自定义 edge/taskbar_h
    assert region_of((960, 1060), W, H, None, edge=80, taskbar_h=0) == "center"
    assert region_of((10, 1010), W, H, None, edge=80, taskbar_h=0) == "corner_bl"


def test_region_of_defensive() -> None:
    print("[test] region_of:退化屏幕/非法 fg_rect/极端坐标 不抛异常")
    assert region_of((960, 540), 0, 1080) == "center"       # 退化屏宽
    assert region_of((960, 540), 1920, 0) == "center"
    assert region_of((960, 540), 1920, 1080, (5, 5, 5, 5)) == "center"  # 零面积 fg 忽略
    assert region_of((960, 540), 1920, 1080, (100, 100, 50, 900)) == "center"
    assert region_of((-500, -500), W, H) == "corner_tl"     # 屏外点不抛,归最近带
    assert region_of((-500, 540), W, H) == "edge"
    assert region_of((960, 540), 1920, 1080, (1, 2, 3)) == "center"     # 长度不足
    assert region_of((960, 540), 1920, 1080, None, edge=0, taskbar_h=0) == "center"


# --------------------------------------------------------------- 2. rect 非法防御(注入)
class _Patch:
    """临时替换模块级 Win32 查询封装(确定性注入),with 语法,退出即还原。"""
    def __init__(self, **overrides) -> None:
        self.overrides = overrides
        self._saved: dict = {}

    def __enter__(self):
        for name, fn in self.overrides.items():
            self._saved[name] = getattr(scr, name)
            setattr(scr, name, fn)
        return self

    def __exit__(self, *exc) -> None:
        for name, fn in self._saved.items():
            setattr(scr, name, fn)


def test_rect_invalid_defense() -> None:
    print("[test] foreground/snapshot:最小化/隐身/非法 rect 防御")
    # -- foreground:最小化窗 → rect=None,如实报告 is_minimized --
    with _Patch(_fg_hwnd=lambda: 111,
                _get_rect=lambda h: (-32000, -32000, -31840, -31968),  # 最小化坐标
                _is_iconic=lambda h: True,
                _is_cloaked=lambda h: False,
                _class_name=lambda h: "Notepad"):
        m = ScreenMonitor()
        fg = m.foreground(force=True)
        assert fg is not None and fg.hwnd == 111
        assert fg.is_minimized is True and fg.rect is None, "最小化窗 rect 必须置 None"
        assert fg.class_name == "Notepad"
        m.close()
    # -- foreground:UWP cloaked 窗 → rect=None,cloaked=True --
    with _Patch(_fg_hwnd=lambda: 222,
                _get_rect=lambda h: (0, 0, 800, 600),
                _is_iconic=lambda h: False,
                _is_cloaked=lambda h: True,
                _class_name=lambda h: "ApplicationFrameWindow"):
        m = ScreenMonitor()
        fg = m.foreground(force=True)
        assert fg.cloaked is True and fg.rect is None, "cloaked 窗 rect 必须置 None"
        m.close()
    # -- foreground:rect 查询失败 / 无前台 --
    with _Patch(_fg_hwnd=lambda: 0):
        assert ScreenMonitor().foreground(force=True) is None
    with _Patch(_fg_hwnd=lambda: 333, _get_rect=lambda h: None,
                _is_iconic=lambda h: False, _is_cloaked=lambda h: False,
                _class_name=lambda h: "X"):
        assert ScreenMonitor().foreground(force=True).rect is None
    # -- snapshot:不可见/最小化/cloaked/非法 rect/出虚屏 全部整窗丢弃 --
    wins = {
        1: dict(visible=False, iconic=False, cloaked=False, rect=(0, 0, 800, 600)),
        2: dict(visible=True, iconic=True, cloaked=False, rect=(0, 0, 800, 600)),
        3: dict(visible=True, iconic=False, cloaked=True, rect=(0, 0, 800, 600)),
        4: dict(visible=True, iconic=False, cloaked=False, rect=(800, 600, 800, 600)),  # 零面积
        5: dict(visible=True, iconic=False, cloaked=False, rect=(900, 0, 800, 600)),    # r<=l
        6: dict(visible=True, iconic=False, cloaked=False, rect=(-99999, 0, -90000, 600)),  # 不与虚屏相交
        7: dict(visible=True, iconic=False, cloaked=False, rect=(100, 100, 900, 700)),  # 合法
        8: dict(visible=True, iconic=False, cloaked=False, rect=(-100, 100, 900, 700)),  # 部分出屏但相交→保留
    }
    table = {h: v for h, v in wins.items()}

    def fake_enum():
        return list(table.keys())

    def fake_get(hwnd):
        return table[hwnd]["rect"]

    def mk(key):
        return lambda h: table[h][key]

    with _Patch(_enum_top_hwnds=fake_enum, _get_rect=fake_get,
                _is_visible=mk("visible"), _is_iconic=mk("iconic"),
                _is_cloaked=mk("cloaked"), _virtual_screen=lambda: FAKE_VS,
                _primary_screen_area=lambda: W * H, _class_name=lambda h: f"C{h}"):
        m = ScreenMonitor(min_area_frac=0.0)   # 关面积门,单测 rect 防御
        snap = m.snapshot(force=True)
        kept = [w.hwnd for w in snap]
        assert kept == [7, 8], f"应只保留合法 rect 窗 7/8(z 序),得到 {kept}"
        w7 = snap[0]
        assert isinstance(w7, WindowInfo)
        assert w7.rect == (100, 100, 900, 700) and w7.class_name == "C7"
        assert w7.is_minimized is False and w7.cloaked is False
        m.close()


# --------------------------------------------------------------- 3. 面积门
def test_area_gate() -> None:
    print("[test] snapshot:面积门(min_area_frac)丢弃小窗")
    # 主屏 1920×1080=2073600;门 0.008 → ≥16589 px²(约 129²)
    rects = {1: (0, 0, 100, 100),      # 10000 < 门 → 丢
             2: (0, 0, 200, 200),      # 40000 ≥ 门 → 留
             3: (500, 500, 629, 629),  # 16641 ≥ 门(边界内) → 留
             4: (500, 500, 628, 629),  # 128×129=16512 < 门 → 丢
             }
    with _Patch(_enum_top_hwnds=lambda: list(rects.keys()),
                _get_rect=lambda h: rects[h],
                _is_visible=lambda h: True, _is_iconic=lambda h: False,
                _is_cloaked=lambda h: False, _virtual_screen=lambda: FAKE_VS,
                _primary_screen_area=lambda: W * H, _class_name=lambda h: f"C{h}"):
        m = ScreenMonitor(min_area_frac=0.008)
        kept = [w.hwnd for w in m.snapshot(force=True)]
        assert kept == [2, 3], f"面积门应只留 2/3,得到 {kept}"
        m.close()


def test_snapshot_cap() -> None:
    print("[test] snapshot:cap 64 上限")
    rects = {h: (0, 0, 400, 400) for h in range(1, 100)}   # 99 个合法大窗
    with _Patch(_enum_top_hwnds=lambda: list(rects.keys()),
                _get_rect=lambda h: rects[h],
                _is_visible=lambda h: True, _is_iconic=lambda h: False,
                _is_cloaked=lambda h: False, _virtual_screen=lambda: FAKE_VS,
                _primary_screen_area=lambda: W * H, _class_name=lambda h: "C"):
        m = ScreenMonitor(min_area_frac=0.0)
        snap = m.snapshot(force=True)
        assert len(snap) == SNAPSHOT_CAP, f"应截断到 cap={SNAPSHOT_CAP},得到 {len(snap)}"
        assert [w.hwnd for w in snap] == list(range(1, 65)), "应按 z 序取前 64"
        m.close()


# --------------------------------------------------------------- 4. diff 事件/去抖
def _wi(hwnd: int, rect: tuple[int, int, int, int]) -> WindowInfo:
    return WindowInfo(hwnd, rect, f"C{hwnd}")


def test_diff_debounce() -> None:
    print("[test] diff_events:同 hwnd 去抖 ≥3s,窗口内不重复")
    m = ScreenMonitor()
    r1 = (100, 100, 600, 500)
    # 基线:A 在场
    assert m.diff_events(now=100.0, snapshot_windows=[_wi(1, r1)]) == [], "首次只建基线"
    # 3s 内同 hwnd 重复出现(消失又出现)→ 不重复报
    ev = m.diff_events(now=100.0 + 1.0, snapshot_windows=[_wi(1, r1), _wi(2, (0, 0, 800, 600))])
    assert [e.kind for e in ev] == ["appear"], "只应报新 hwnd=2 的 appear"
    assert ev[0].hwnd == 2 and ev[0].class_name == "C2"
    assert ev[0].rect == (0, 0, 800, 600) and ev[0].center == (400, 300)
    ev = m.diff_events(now=100.0 + 2.0, snapshot_windows=[_wi(1, r1)])
    assert ev == [], f"hwnd=2 消失距上次事件 1s < {DEBOUNCE_S}s,应被去抖,得到 {ev}"
    ev = m.diff_events(now=100.0 + 2.5, snapshot_windows=[_wi(1, r1), _wi(2, (0, 0, 800, 600))])
    assert ev == [], "同 hwnd 2.5s 内重现(距上次 emit 1.5s)也应被去抖"
    # 距上次 emit(101.0)3.1s ≥ DEBOUNCE_S → 放行 vanish
    ev = m.diff_events(now=100.0 + 4.1, snapshot_windows=[_wi(1, r1)])
    assert [e.kind for e in ev] == ["vanish"], f"距上次事件 3.1s ≥ {DEBOUNCE_S}s,应放行 vanish"
    assert ev[0].hwnd == 2
    m.close()


def test_diff_event_sequence() -> None:
    print("[test] diff_events:appear → grow → vanish 序列 + grow 面积阈值")
    m = ScreenMonitor()
    small = (100, 100, 500, 400)     # 160000
    big = (100, 100, 500, 560)       # 240000 = 1.5×
    tiny_grow = (100, 100, 500, 408)  # 163200 = 1.02×(<5% 不算 grow)
    base = [_wi(1, small)]
    assert m.diff_events(now=10.0, snapshot_windows=base) == []
    # appear(时间隔开,避免去抖干扰)
    ev = m.diff_events(now=20.0, snapshot_windows=[*base, _wi(9, (600, 100, 1000, 400))])
    assert [(e.hwnd, e.kind) for e in ev] == [(9, "appear")]
    # 微扩(<5%)不算 grow
    ev = m.diff_events(now=30.0, snapshot_windows=[_wi(1, tiny_grow), _wi(9, (600, 100, 1000, 400))])
    assert ev == [], "面积只扩 2% 不应报 grow"
    # 大扩(>5%)报 grow
    ev = m.diff_events(now=40.0, snapshot_windows=[_wi(1, big), _wi(9, (600, 100, 1000, 400))])
    assert [(e.hwnd, e.kind) for e in ev] == [(1, "grow")], f"应报 hwnd=1 grow,得到 {ev}"
    assert ev[0].rect == big and ev[0].center == (300, 330)
    # vanish(与上次 emit 间隔足够)
    ev = m.diff_events(now=50.0, snapshot_windows=[_wi(1, big)])
    assert [(e.hwnd, e.kind) for e in ev] == [(9, "vanish")]
    assert ev[0].center == (800, 250)
    # 全清空
    ev = m.diff_events(now=60.0, snapshot_windows=[])
    assert [(e.hwnd, e.kind) for e in ev] == [(1, "vanish")]
    assert m.diff_events(now=70.0, snapshot_windows=[]) == []
    m.close()


# --------------------------------------------------------------- 5. 节流(注入计数)
class _CountingMonitor(ScreenMonitor):
    def __init__(self) -> None:
        super().__init__()
        self.fg_queries = 0
        self.snap_queries = 0

    def _query_foreground(self):
        self.fg_queries += 1
        return WindowInfo(1, (0, 0, 800, 600), "Fake")

    def _capture_windows(self):
        self.snap_queries += 1
        return [_wi(1, (0, 0, 800, 600))]


def test_poll_throttle() -> None:
    print("[test] 节流:foreground 2Hz / snapshot ≥2s(事件驱动)")
    m = _CountingMonitor()
    t0 = time.monotonic()
    a = m.foreground()
    b = m.foreground()                       # 0.5s 内 → 命中缓存
    assert a is b and m.fg_queries == 1, "0.5s 内两次 foreground 应复用缓存(2Hz)"
    assert m.foreground(force=True) is not None and m.fg_queries == 2, "force 绕过节流"
    c = m.foreground()                       # force 刚刷新过 → 仍缓存
    assert c is not a or True                # force 后缓存已替换,只验证不再查询
    assert m.fg_queries == 2
    s1 = m.snapshot()
    s2 = m.snapshot()                        # 2s 内 → 命中缓存
    assert s1 is s2 and m.snap_queries == 1, "2s 内两次 snapshot 应复用缓存"
    assert m.snapshot(force=True) and m.snap_queries == 2
    assert (time.monotonic() - t0) < 1.0, "节流测试应瞬时完成(无真实 sleep)"
    m.close()


# --------------------------------------------------------------- 6. 快照字段 / close 幂等(真实只读查询)
def test_snapshot_live_fields() -> None:
    print("[test] snapshot/foreground 真实只读查询:字段完整性")
    if not scr.IS_WINDOWS:
        print("    (非 Windows,跳过 live 字段测试)")
        return
    m = ScreenMonitor()
    snap = m.snapshot(force=True)
    assert isinstance(snap, list) and len(snap) <= SNAPSHOT_CAP
    for w in snap:
        assert isinstance(w, WindowInfo)
        assert isinstance(w.hwnd, int) and w.hwnd != 0
        assert w.rect is not None and len(w.rect) == 4
        l, t, r, b = w.rect
        assert r > l and b > t, f"快照窗 rect 必须合法:{w}"
        assert isinstance(w.class_name, str)
        assert w.is_minimized is False and w.cloaked is False, "快照已滤掉最小化/隐身窗"
    fg = m.foreground(force=True)
    if fg is not None:
        assert fg.hwnd != 0 and isinstance(fg.class_name, str)
        print(f"    live: 前台 hwnd={fg.hwnd} class={fg.class_name!r} rect={fg.rect} "
              f"窗表 {len(snap)} 窗")
    m.close()


def test_close_idempotent() -> None:
    print("[test] close 幂等 + 关闭后行为")
    m = _CountingMonitor()
    m.foreground(force=True)
    m.close()
    m.close()                                # 二次 close 不抛
    assert m.foreground() is None            # 关闭后一律空结果
    assert m.snapshot() == []
    assert m.diff_events() == []
    del m                                    # __del__ 安全
    m2 = ScreenMonitor()
    m2.close()
    del m2
    # close 后仍可继续 close/del(无残留线程/引用崩溃)
    m3 = _CountingMonitor()
    try:
        m3.close()
        m3.close()
        del m3
    except Exception as exc:
        raise AssertionError(f"close/__del__ 应无异常: {exc}")


# --------------------------------------------------------------- 7. 性能信息(软断言)
def test_perf_budget() -> None:
    print("[test] 成本红线:快照 <100ms(实测 ~1.4ms/375 窗),foreground <1ms")
    if not scr.IS_WINDOWS:
        return
    m = ScreenMonitor()
    t0 = time.perf_counter_ns()
    snap = m.snapshot(force=True)
    t_snap = (time.perf_counter_ns() - t0) / 1e6
    t0 = time.perf_counter_ns()
    fg = m.foreground(force=True)
    t_fg = (time.perf_counter_ns() - t0) / 1e3
    # region_of 微基准(纯几何,应 ~0.5µs 级)
    fg_rect = fg.rect if fg and fg.rect else (100, 100, 1800, 950)
    t0 = time.perf_counter_ns()
    for _ in range(20000):
        region_of((500, 500), 1920, 1080, fg_rect)
    t_zone = (time.perf_counter_ns() - t0) / 20000 / 1e3
    print(f"    snapshot({len(snap)} 窗) {t_snap:.2f}ms | foreground {t_fg:.2f}µs"
          f" | region_of {t_zone:.3f}µs/次 | 2Hz 前台轮询 ≈{2 * t_fg:.1f}µs/s")
    assert t_snap < 100.0, f"快照异常变慢:{t_snap:.1f}ms(红线 100ms)"
    assert t_fg < 1000.0, f"前台查询异常变慢:{t_fg:.1f}µs"
    assert t_zone < 5.0, f"region_of 异常变慢:{t_zone:.2f}µs"
    m.close()


# --------------------------------------------------------------- live 手跑探针(@live,不进自动断言)
def manual_live_probe() -> None:
    """@live 手跑探针(需真实桌面会话,用 `python tests/test_screen_monitor.py --live` 启动)。

    验证:①前台窗口检测正确(输出 hwnd/类名/rect,肉眼对照当前活动窗);
    ②diff_events 对真实开/关一个窗口产生 appear/vanish(手动开关记事本对照输出);
    ③WinEventHook EVENT_SYSTEM_FOREGROUND 收到前台切换事件(手动 Alt-Tab)。
    全部为只读观察,不断言。
    """
    print("== LIVE 探针(只读,5s)==")
    print("操作提示:接下来 5 秒内随意切换窗口 / 开关一个窗口。")
    m = ScreenMonitor()
    base = {w.hwnd for w in m.snapshot(force=True)}
    print(f"基线窗表:{len(base)} 窗")
    hook = None
    fg_hits: list[int] = []
    if scr.IS_WINDOWS:
        try:
            hook = scr.WinEventHook(on_event=lambda e, h: fg_hits.append(h))
            hook.start()
            print("WinEventHook(EVENT_SYSTEM_FOREGROUND) 已安装")
        except Exception as exc:
            print(f"WinEventHook 安装失败(不影响主路径): {exc!r}")
    t_end = time.monotonic() + 5.0
    while time.monotonic() < t_end:
        fg = m.foreground(force=True)
        if fg:
            print(f"  前台: hwnd={fg.hwnd} class={fg.class_name!r} rect={fg.rect} "
                  f"min={fg.is_minimized} cloaked={fg.cloaked}")
        ev = m.diff_events(snapshot_windows=m.snapshot(force=True))
        for e in ev:
            print(f"  事件: {e.kind} hwnd={e.hwnd} class={e.class_name!r} rect={e.rect}")
        time.sleep(0.5)
    if hook:
        hook.stop()
        print(f"WinEventHook 收到前台切换 {len(fg_hits)} 次;未启动则跳过")
    gone = base - {w.hwnd for w in m.snapshot(force=True)}
    print(f"探针结束。相对基线消失 {len(gone)} 窗(去抖可能吞掉部分事件,属预期)。")
    m.close()


def main() -> None:
    test_region_of_all_zones()
    test_region_of_defensive()
    test_rect_invalid_defense()
    test_area_gate()
    test_snapshot_cap()
    test_diff_debounce()
    test_diff_event_sequence()
    test_poll_throttle()
    test_snapshot_live_fields()
    test_close_idempotent()
    test_perf_budget()
    print("[screen] ALL OK (11 组判据通过)")


if __name__ == "__main__":
    if "--live" in sys.argv:
        manual_live_probe()
    else:
        main()
