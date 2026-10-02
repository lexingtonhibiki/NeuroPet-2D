# -*- coding: utf-8 -*-
"""钩子面板穿透离线测试:should_swallow 纯函数 + _on_event 回调模拟(不真实安装钩子)。

运行:python tests/test_hook_passthrough.py
全程离线、可重复运行:不调用 start()(即不安装 WH_MOUSE_LL)、不动真实鼠标。
背景 hotfix:投喂模式下低级鼠标钩子曾吞掉全局所有左键,导致控制面板无法点击;
现在 capture 开启时,位于控制面板内(is_passthrough(x, y) 为 True)的左键放行。
"""
from __future__ import annotations

import ctypes
import io
import sys
import threading
import time
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import neuropet.core.hook as hook
from neuropet.core.hook import should_swallow as hs

# 模拟一块控制面板矩形(物理像素):x∈[100,300], y∈[100,200]
PANEL = (100, 100, 300, 200)


def in_panel(x, y) -> bool:
    """is_passthrough 的典型实现:只读缓存矩形,无任何 tkinter 调用。"""
    return PANEL[0] <= x <= PANEL[2] and PANEL[1] <= y <= PANEL[3]


def reset_warn_flag() -> None:
    """重置模块级"异常只警告一次"标志,保证各用例互不干扰、可重复运行。"""
    hook._passthrough_warned = False


# ---------------------------------------------------------------- 纯函数用例
def test_capture_off_no_swallow() -> None:
    print("[test] capture 关闭:一律不吞(无论有无 is_passthrough)")
    assert hs(False, 10, 10, in_panel) is False, "capture off 即使面板内也不该吞"
    assert hs(False, 500, 500, in_panel) is False, "capture off 面板外也不该吞"
    assert hs(False, -5, -5, None) is False, "capture off 且无 passthrough 也不该吞"
    assert hs(False, None, None, None) is False, "capture off 对非法坐标也不该吞"


def test_capture_on_no_passthrough_swallow() -> None:
    print("[test] capture 开启 + 无 passthrough:吞(向后兼容旧用法)")
    assert hs(True, 500, 500, None) is True, "无 is_passthrough 时应全屏吞掉"
    assert hs(True, -5, -5, None) is True, "负坐标同样吞掉"


def test_panel_point_passes_through() -> None:
    print("[test] capture 开启 + 面板判定:面板点放行,非面板点吞")
    assert hs(True, 150, 150, in_panel) is False, "面板内左键必须放行(本 hotfix 核心)"
    assert hs(True, 100, 100, in_panel) is False, "面板左上角(边界含)应放行"
    assert hs(True, 300, 200, in_panel) is False, "面板右下角(边界含)应放行"
    assert hs(True, 500, 500, in_panel) is True, "面板外应吞掉"
    assert hs(True, 99, 150, in_panel) is True, "面板左边界外一点应吞掉"


def test_passthrough_exception_swallows_without_raise() -> None:
    print("[test] is_passthrough 抛异常:按吞掉处理、异常不外逃、只警告一次")
    reset_warn_flag()

    def boom(x, y) -> bool:
        raise RuntimeError("模拟:禁止在钩子线程调用 tkinter")

    buf = io.StringIO()
    with redirect_stdout(buf):                    # 钩子线程内不许让异常逃出
        assert hs(True, 1, 2, boom) is True, "异常时应按'吞掉'处理"
        assert hs(True, 3, 4, boom) is True, "第二次异常仍吞(但不再打印)"
    out = buf.getvalue()
    assert out.count("is_passthrough") == 1, f"应只警告一次,实际输出:\n{out}"
    assert "RuntimeError" in out, "警告里应带上异常信息"


def test_weird_coords_no_crash() -> None:
    print("[test] None / 负数等非法坐标:不崩,规则照常")
    reset_warn_flag()

    def boom(x, y) -> bool:
        raise TypeError("None 无法比较(模拟糟糕实现)")

    assert hs(True, None, None, lambda x, y: True) is False, "面板判定说'是'就放行"
    assert hs(True, None, None, lambda x, y: False) is True, "面板判定说'否'就吞"
    assert hs(True, None, None, boom) is True, "非法坐标引发的异常也按吞掉处理"
    assert hs(True, -1, -99999, in_panel) is True, "远离面板的负坐标应吞掉"
    assert hs(True, 2**31, 2**31, in_panel) is True, "超大坐标应吞掉(不越界崩溃)"


# ---------------------------------------------------------------- 实例级用例
def _drive(h, msg: int, x: int, y: int):
    """伪造 MSLLHOOKSTRUCT 并直接驱动钩子回调(未 start,未安装真实钩子)。

    注意:结构体必须在本函数栈上存活到 _on_event 返回,否则 ctypes 会回收
    内存,_on_event 读到的坐标将是脏数据。
    """
    info = hook._MSLLHOOKSTRUCT()
    info.pt.x = x
    info.pt.y = y
    return h._on_event(hook.HC_ACTION, msg, ctypes.addressof(info))


def test_constructor_backward_compat() -> None:
    """构造接口:旧用法照常;is_passthrough 被保存;非法类型报 TypeError。"""
    if not hook.IS_WINDOWS:
        print("[skip] 非 Windows 平台,跳过构造用例")
        return
    print("[test] 构造接口:旧用法兼容 + is_passthrough 校验")

    def noop(x, y) -> None:
        return None

    h = hook.LLMouseHook(on_left_click=noop)             # 旧用法:只传 on_left_click
    assert h.is_passthrough is None, "未传 is_passthrough 应为 None"
    h.set_capture(True)
    assert h.capturing is True
    h.set_capture(False)
    h.stop()                                             # 未 start 就 stop 必须安全幂等

    h2 = hook.LLMouseHook(noop, is_passthrough=in_panel)  # 新用法:位置参数
    assert h2.is_passthrough is in_panel
    h3 = hook.LLMouseHook(on_left_click=noop, is_passthrough=None)  # 关键字风格
    assert h3.is_passthrough is None
    try:
        hook.LLMouseHook(noop, is_passthrough=123)       # 非可调用必须立刻报错
    except TypeError:
        pass
    else:
        raise AssertionError("is_passthrough=123 应抛 TypeError")


def test_hook_callback_simulation() -> None:
    """模拟钩子回调与派发线程:面板点放行、面板外吞掉并入队、异常吞掉不崩。"""
    if not hook.IS_WINDOWS:
        print("[skip] 非 Windows 平台,跳过回调模拟用例")
        return
    print("[test] _on_event 模拟:面板放行 / 非面板吞 / 异常吞 / 派发线程回调")
    clicks: list[tuple[int, int]] = []
    h = hook.LLMouseHook(on_left_click=lambda x, y: clicks.append((x, y)),
                         is_passthrough=in_panel)

    # ---- capture off:回调直接放行(返回值非 1),不入队、不计数 ----
    h.set_capture(False)
    ret = _drive(h, hook.WM_LBUTTONDOWN, 500, 500)
    assert ret != 1, "capture off 时左键必须放行(不得返回 1)"
    assert h._events.empty() and h.swallowed_down == 0, "capture off 不应入队/计数"

    # ---- capture on + 面板点:放行,面板可点击(本 hotfix 核心) ----
    h.set_capture(True)
    ret = _drive(h, hook.WM_LBUTTONDOWN, 150, 150)
    assert ret != 1, "面板内左键应放行(不得返回 1)"
    assert h._events.empty() and h.swallowed_down == 0, "面板点不应入队/计数"

    # ---- capture on + 面板外:吞掉(return 1)、入队、计数 ----
    ret = _drive(h, hook.WM_LBUTTONDOWN, 900, 600)
    assert ret == 1, "面板外左键 DOWN 应被吞掉(返回 1)"
    assert h.swallowed_down == 1 and h.last_click == (900, 600), "DOWN 应计数并记录坐标"
    ret = _drive(h, hook.WM_LBUTTONUP, 900, 600)
    assert ret == 1 and h.swallowed_up == 1, "UP 也应吞掉并计数(避免孤儿 UP)"

    # ---- capture on + is_passthrough 抛异常:吞掉且异常不外逃 ----
    def boom(x, y) -> bool:
        raise RuntimeError("boom")

    h.is_passthrough = boom
    reset_warn_flag()
    buf = io.StringIO()
    with redirect_stdout(buf):
        ret = _drive(h, hook.WM_LBUTTONDOWN, 42, 42)
    assert ret == 1, "is_passthrough 抛异常时应按吞掉处理"
    assert "RuntimeError" in buf.getvalue(), "应在钩子线程打印一次警告"

    # ---- 派发线程真实运转(仅派发线程,不安装钩子):面板外点击回调一次 ----
    while not h._events.empty():          # 排干前面用例遗留的队列事件,隔离验证
        h._events.get_nowait()
    h.is_passthrough = in_panel
    _drive(h, hook.WM_LBUTTONDOWN, 900, 600)   # 重新造一次面板外点击
    t = threading.Thread(target=h._dispatch_loop, name="test-dispatch", daemon=True)
    h._dispatch = t                       # 交给 stop() 统一回收
    t.start()
    deadline = time.monotonic() + 2.0
    while not clicks and time.monotonic() < deadline:
        time.sleep(0.01)
    assert clicks == [(900, 600)], f"面板外 DOWN 应派发一次正确坐标,得到 {clicks}"

    # 面板点再驱动一次:队列与回调都保持不动
    _drive(h, hook.WM_LBUTTONDOWN, 200, 150)
    deadline = time.monotonic() + 0.2
    while time.monotonic() < deadline:
        time.sleep(0.01)
    assert clicks == [(900, 600)], "面板点放行后不得触发投喂回调"
    h.stop()                              # 幂等回收派发线程(未 start 也安全)
    assert h.capturing is True and clicks == [(900, 600)]


def main() -> None:
    test_capture_off_no_swallow()
    test_capture_on_no_passthrough_swallow()
    test_panel_point_passes_through()
    test_passthrough_exception_swallows_without_raise()
    test_weird_coords_no_crash()
    test_constructor_backward_compat()
    test_hook_callback_simulation()
    print("[hook_passthrough] ALL OK")


if __name__ == "__main__":
    main()
