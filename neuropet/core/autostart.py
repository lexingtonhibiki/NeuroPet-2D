"""Windows 用户级开机自启动(HKCU Run;stdlib ``winreg``,不需要管理员)。

口径(v0.2.0 用户需求):

- **注册表是真源**:勾选状态只从 ``HKCU\\...\\CurrentVersion\\Run`` 读,
  不看配置、不缓存。上一次勾选失败(权限/策略/被杀软拦)不会被当成成功。
- **只动本程序那一个值**:取消勾选只删 ``VALUE_NAME``,不碰同一键下的其它
  程序;启用时同名覆盖。
- **路径跟着程序走**:写的是**当前**便携 EXE(或源码运行的 pythonw + 入口
  脚本)的绝对路径,带引号封装,含空格与中文都有效,并附 ``--autostart``
  (开机启动时先落托盘,不弹面板)。
- **移动文件夹后要重新勾**:读回的路径与当前路径不一致时
  :func:`state` 报 ``matches=False``,面板因此显示未勾选 —— 不会显示一个
  实际指向旧路径的"已开启"。
- **非 Windows / 注册表不可用**:全部退化为 False + 失败信息,由界面回滚
  复选框并给双语错误,绝不假装成功。

不启动额外服务、计划任务或后台守护:自启动就是"登录时多跑一次本程序",
重复实例由 ``main.py`` 的单实例守卫挡住(仅 ``--autostart`` 走守卫,
手动启动与调试入口行为不变)。
"""
from __future__ import annotations

import os
import sys

VALUE_NAME = "NeuroPet-2D"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_FLAG = "--autostart"
# 命名互斥体:同一用户会话内只允许一份"开机自启动实例"。
MUTEX_NAME = "Local\\NeuroPet-2D-autostart"


def is_supported() -> bool:
    """当前平台是否支持(非 Windows → False,界面据此禁用并说明)。"""
    return os.name == "nt"


def entry_path() -> str | None:
    """自启动要执行的程序路径。

    - 便携版(``sys.frozen``)→ EXE 绝对路径;
    - 源码运行 → 优先同目录的 ``pythonw.exe``(无控制台),入口取仓库的
      ``NeuroPet.py`` 绝对路径;``pythonw.exe`` 不存在时退回当前解释器
      (会带一个控制台窗口,属已知代价,仍能工作)。
    """
    try:
        if getattr(sys, "frozen", False):
            return os.path.abspath(sys.executable)
        root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        script = os.path.join(root, "NeuroPet.py")
        if not os.path.isfile(script):
            return None
        exe = sys.executable
        pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
        return pythonw if os.path.isfile(pythonw) else exe
    except Exception:
        return None


def command(path: str | None = None) -> str | None:
    """完整命令行:``"<程序>" <入口脚本> --autostart``(路径含空格也安全)。"""
    exe = path if path is not None else entry_path()
    if not exe:
        return None
    parts = ['"%s"' % exe]
    if not getattr(sys, "frozen", False):
        root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        script = os.path.join(root, "NeuroPet.py")
        if not os.path.isfile(script):
            return None
        parts.append('"%s"' % script)
    parts.append(AUTOSTART_FLAG)
    return " ".join(parts)


def _read() -> str | None:
    """读注册表当前值(不存在 → None)。失败抛 OSError 由调用方兜底。"""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, VALUE_NAME)
            return value if isinstance(value, str) else None
    except FileNotFoundError:
        return None


def state() -> tuple[bool, bool]:
    """``(是否已注册, 是否指向当前程序)``。

    只有两者都为真才当作"开机自启动已开启";便携文件夹被移动过之后第二项
    会变假,面板据此显示未勾选,提示用户重新勾一次以更新路径。
    """
    if not is_supported():
        return False, False
    try:
        current = _read()
    except Exception:
        return False, False
    if not current:
        return False, False
    try:
        return True, current.strip() == (command() or "").strip()
    except Exception:
        return True, False


def enabled() -> bool:
    """面板勾选态的真源(注册表已注册 **且** 指向当前程序)。"""
    registered, matches = state()
    return registered and matches


def enable() -> str:
    """写入自启动项;返回写入的命令行。失败抛 OSError/ImportError。"""
    cmd = command()
    if not cmd:
        raise OSError("cannot resolve autostart entry")
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, cmd)
    return cmd


def disable() -> None:
    """删除本程序的那一个值;本来就没有 → 幂等成功。"""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        pass


def acquire_single_instance() -> bool:
    """自启动单实例守卫:拿到互斥体 → True(本进程是第一个)。

    已经存在 → False,调用方应当安静退出(避免登录自启动把宠物窗口复制一
    份)。只在 ``--autostart`` 路径上调用:手动启动与 ``--probe``/``--smoke``
    的调试行为保持不变。返回 True 表示"未持有但也不阻塞"。
    """
    if not is_supported():
        return True
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if not handle:
            return True                      # 建不出来就不拦(失败安全)
        already = ctypes.get_last_error() == 183   # ERROR_ALREADY_EXISTS
        return not already
    except Exception:
        return True


def set_startup(enabled_state: bool) -> bool:
    """面板唯一入口:切换自启动。返回**实际生效**状态(写入失败 → False)。

    "生效"= 操作成功 **且** 复查注册表与请求一致(移动过文件夹的旧路径算
    不生效,面板据此提示重新勾选)。
    """
    want = bool(enabled_state)
    try:
        if want:
            enable()
        else:
            disable()
    except Exception:
        return False
    try:
        return enabled() == want
    except Exception:
        return False