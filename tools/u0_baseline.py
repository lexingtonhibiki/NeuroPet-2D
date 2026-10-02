# -*- coding: utf-8 -*-
"""r25 U0 基线采集探针(源码模式;须独占机器,不与其他重活并发)。

与 `tests/test_live_motion.py::_drive` 同惯例:直接以 60Hz 实时节拍驱动真实
`app._tick()`(与 mainloop 同一函数),名册/profile/会话全部打进临时目录
(不碰仓库 data/)。插桩经 `neuropet.core.instr`(env NEUROPET_INSTR)。

**时基(2026-09-22 单元,见 `Timebase`)**:`_drive` 的墙钟节拍受 Windows sleep
粒度限制(~21.8ms/tick),而 `app._tick` 的 `dt` 上钳位是 0.05 ⇒ 不钉时基时每 tick
推进 **3.00 倍**仿真时间。本探针现**默认钉死**时基(`_drive` 前把 `app._next_t` 置为
当刻墙钟 ⇒ `dt ≡ FRAME_DT`),并**断言**每个 tick 的 dt 合规:不合规即抛
`TimebaseError`、**不落盘**(不许静默降级)。`--no-pin-dt` = 对照臂(旧口径)。

**光标输入一律钉到屏外(见 `_pin_cursor`;别删)** —— 本探针采集的每一行都与
它有关:真实鼠标经 `perception/mouse.py` 会写进 `brain._fear`(单帧可到 1.0),
恐惧再经 `body._emo_halt` 打开停顿窗,**强制行走臂也会停**(EXPLORE 分支被覆盖)
⇒ 不钉死则本文件的数字取决于**跑测时鼠标在哪**(工作流 §5 坑表第 7 条同族)。

用法::

    python tools/u0_baseline.py steady --tag s2 --pets 2 --sec 60
    python tools/u0_baseline.py steady --tag s3 --pets 3 --sec 60
    python tools/u0_baseline.py steady --tag s2walk --pets 2 --sec 60 --force walk
    python tools/u0_baseline.py steady --tag s2panel --pets 2 --sec 60 --panel on
    python tools/u0_baseline.py steady --tag s2off --pets 2 --sec 60 --instr off
    python tools/u0_baseline.py r1 --sec 20            # 慢速运动平滑(R1)
    python tools/u0_baseline.py r4 --sec 20            # 支撑多边形含质心(R4)
    python tools/u0_baseline.py r5                     # 情绪→步态(R5)
    python tools/u0_baseline.py coldstart --runs 3     # 冷启动(源码模式)
    python tools/u0_baseline.py steady --tag s2 --pets 2 --sec 60 --no-pin-dt   # 对照臂
    python tools/u0_baseline.py r1 --sec 3 --negctrl   # 负向对照:必抛 TimebaseError

产物:logs/u0_<tag>.json + 控制台摘要。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FRAME_DT = 1.0 / 60.0
SPECIES = {"roach": "species.cockroach", "fly": "species.fruitfly"}
OUT_DIR = ROOT / "logs"

# ---- 时基钉的判据常量(2026-09-22 时基单元;见 Timebase docstring) ----
TB_DT_HARD = 0.0200        # 单 tick dt 硬上限(s)= 1.2 个名义帧;离钳位 0.05 有 2.5× 余量
TB_P99_X = 1.02            # 窗口 dt p99 上限(×FRAME_DT):2% ≈ 333µs 的调用开销余量
TB_CLAMP_LO = 0.049        # 对照臂「确实撞钳位」的下限:dt p50 必须 ≥ 49ms

_PIN_DT = True             # 由 --no-pin-dt / --negctrl 关闭(见 main)
_NEGCTRL = False           # 只用于负向对照:断言按钉住口径执行,但**不装钉子**


# ---------------------------------------------------------------- 脚手架
PIN_CURSOR = (-4000.0, -4000.0)      # 屏外:所有光标通道的 d < *_R 门全关


def _pin_cursor(app) -> None:
    """把 app 的光标输入缝钉到屏外(**本探针的口径核心,别删**)。

    为什么必须钉(2026-09-22 B2 终验实测,`docs/handoff/r25-test-B2-r3.md` 问题 #2):
    真实鼠标经 `perception/mouse.py` 的 SHADOW/VIBRATION/CONTACT/WIND 四通道写进
    脑内恐惧(`roach_brain.observe`:`_fear = max(衰减, clamp(danger))`),
    **单帧可到 1.0**,且**把鼠标静止压在宠物走线上即可**触发(走 CONTACT 通道,
    `d<46 且 speed<40`,不必移动)。恐惧经 `body._emo_halt` 打开停顿窗,
    **连强制行走臂也会停**(`base.py` 的 EXPLORE 分支被覆盖)。
    ⇒ 不钉死,本探针的一切行为类读数(R5 停顿占比 / R1 位移与增量 / R4 支撑多边形 /
    steady 的自然轨迹与运动量→桶构建)都取决于跑测时鼠标在哪 —— 即
    「判据隐式依赖未受控的环境输入」(工作流 §5 坑表第 7 条同族)。
    缝名 = app 的光标输入属性(实现先例:`tests/test_emo_gait.py::_make_app`)。
    """
    app._cursor_getter = lambda: PIN_CURSOR


def _make_app(tmp: Path):
    """临时目录隔离(名册/profile/会话);不调用 run(),不落盘仓库 data/。

    **光标钉屏外**见 `_pin_cursor`(不钉则采集值被真实鼠标污染)。"""
    import neuropet.core.app as appmod
    appmod._SESSION_FILE = tmp / "session.json"

    def _prof(pid: str) -> Path:
        d = tmp / "profiles" / pid
        d.mkdir(parents=True, exist_ok=True)
        return d

    appmod.profile_dir = _prof
    app = appmod.App()
    app._pets_path = tmp / "pets.json"
    _pin_cursor(app)
    return app, appmod


def _add_pets(app, spec: list) -> list:
    pids = []
    for i, item in enumerate(spec):
        kind, k = (item if isinstance(item, (list, tuple)) else (item, 1.0))
        pid = app.add_pet(SPECIES[kind], pos=(360.0 + 380.0 * i, 380.0 + 120.0 * i),
                          pet_id=f"{kind}{i + 1}")
        if k != 1.0:
            app.set_pet_scale(pid, k)
        pids.append(pid)
    return pids


def _make_panel(app, visible: bool):
    """复刻 run() 的面板构造两行(不跑 mainloop);visible=deiconify。"""
    from neuropet.ui.panel import ControlPanel
    app._panel = ControlPanel(app)
    app._panel.set_on_drop_request(app._on_panel_drop)
    if visible:
        app._panel.win.deiconify()
    else:
        app._panel.hide()
    return app._panel


def _force_walk(app, h, intensity: float = 1.0):
    """注入点=决策出口(实现零改动):恒定 EXPLORE,intensity 定速。"""
    from neuropet.core.contracts import Behavior, BehaviorCommand
    st0 = h.state
    orig = h.brain.decide

    def _walk(_view):
        tgt = (st0.pos[0] + 400.0 * math.cos(st0.heading),
               st0.pos[1] + 400.0 * math.sin(st0.heading))
        return BehaviorCommand(Behavior.EXPLORE, target=tgt, intensity=intensity,
                               reason="u0-harness")
    h.brain.decide = _walk
    return orig


def _teardown(app) -> None:
    """收尾必须永不抛:测量已在 teardown 之前完成,收尾异常会吞掉产物。"""
    try:
        app._running = False
        app.stage.destroy()
    except Exception:
        pass
    try:
        app.root.destroy()
    except Exception:
        pass


class TimebaseError(RuntimeError):
    """时基没钉住 ⇒ 本次读数**作废**:抛出、不落盘、非零退出(**不许静默降级**)。"""


class Timebase:
    """把仿真时基钉到 `FRAME_DT`,并让「没钉住」变成**会红的断言**(2026-09-22 时基单元)。

    坏在哪(上一单元实测,`docs/handoff/r25-b3-pregate-r1.md` §3.3):`_drive` 按
    `FRAME_DT` 自配速,但 Windows `time.sleep` 粒度(~15.6ms)使实际 tick 间隔
    p50 ≈ 21.8ms;而 `App._tick`(`neuropet/core/app.py:944`)的
    `dt = min(0.05, max(0.001, now - (self._next_t - FRAME_DT)))` 每 tick 只把
    `_next_t` 推进 1/60 ⇒ **持续落后**,86% 的 tick 顶到**上钳位 0.05**
    ⇒ 直驱口径下每 tick 推进 **3.00 倍**仿真时间:命名「20s」的窗口实走 56.846 仿真秒。

    钉法(自由选择,此处取「每 tick 前把 `_next_t` 置为当刻墙钟」):语义 =
    「上一帧**准时**发生」——即真实 mainloop 里 `root.after` 自校正后达到的状态
    (`app.py:942` 的 0.25s 重置门在正常运行时永不触发)。于是
    `dt = FRAME_DT + ε`,ε = 从本行到 `_tick` 内 `perf_counter()` 的微秒级差。

    判据(两臂都自证,谁都**不能**静默降级):
      - 钉住臂(`pin=True`):每个 tick 的 dt 必须落在 `[FRAME_DT, TB_DT_HARD]`,
        **首个越界 tick 立刻抛**(不等窗口结束,更不是「跑完再判」);
        窗口结束再校 `dt p99 ≤ FRAME_DT×TB_P99_X`。越界 = 钉子失效 ⇒ 读数作废。
      - 对照臂(`pin=False`,复现钳位):要求 `dt p50 ≥ TB_CLAMP_LO`
        ——**对照臂必须真的撞了钳位**,否则「A/B 对照」本身无效,同样作废。
      - 两臂都把 dt 分位写进产物 JSON(`timebase` 字段),供下游复核。
    """

    def __init__(self, app, pin: bool = True, negctrl: bool = False):
        self.app, self.pin, self.negctrl = app, pin, negctrl
        self.dts: list = []                 # 全部 tick 的 dt(累计;每次 _drive 不重置)
        self.marks: list = []               # (label, 起点下标, 墙钟起止)
        self.windows: list = []             # finish() 的判据结果(每窗口一条)
        self.violations: list = []          # (下标, dt, 说明)——**闭锁**,见 _step_frame
        self._raised_once = False
        self._orig_tick = app._tick
        self._orig_step_frame = app._step_frame
        app._tick = self._tick
        app._step_frame = self._step_frame

    # --- 安装自证 ---
    def _installed(self, name: str) -> bool:
        """该实例属性上挂的是**本对象**的包装吗(绑方法每次取值都是新对象,不能比 `is`)。"""
        m = getattr(self.app, name, None)
        return (getattr(m, "__func__", None) is getattr(type(self), name, None)
                and getattr(m, "__self__", None) is self)

    def _verify_installed(self) -> None:
        if not (self._installed("_tick") and self._installed("_step_frame")):
            raise TimebaseError("时基钉未生效:app._tick/_step_frame 已被替换(本次读数作废)")
        if self.pin and self.negctrl:
            raise TimebaseError("--negctrl 与钉子同时开:这是自相矛盾的组合(本次读数作废)")

    # --- 驱动侧 ---
    def _tick(self):
        if self.pin:
            self.app._next_t = time.perf_counter()      # ← 钉子
        return self._orig_tick()

    def _step_frame(self, dt):
        d = float(dt)
        if (self.pin and not self.negctrl) or ((not self.pin) and self.negctrl):
            if d > TB_DT_HARD or d < FRAME_DT * 0.99:
                n = len(self.dts)
                self.violations.append((n, d, "dt 越界"))
                # ⚠ `App._tick` 把 `_step_frame` 包在 `except Exception: log_exc` 里
                # (neuropet/core/app.py:948)——**唯一一次的 raise 会被它吞掉**。
                # 所以判据不能只靠 raise:违规**闭锁**在 self.violations,
                # `finish()` 一律重判 ⇒ 就算异常被吞,该次读数仍然作废。
                if not self._raised_once:
                    self._raised_once = True
                    raise TimebaseError(
                        f"时基钉失效:第 {n} 个 tick 的 dt={d:.6f}s 越界 "
                        f"[{FRAME_DT:.6f}, {TB_DT_HARD}] —— 本次读数作废(不落盘)")
        self.dts.append(d)
        return self._orig_step_frame(dt)

    # --- 窗口判据 ---
    def begin(self, label: str) -> int:
        self._verify_installed()
        self.marks.append([label, len(self.dts), time.perf_counter(), None])
        return len(self.dts)

    def finish(self, label: str) -> dict:
        i0 = self.marks[-1][1]
        self.marks[-1][3] = time.perf_counter()
        dts = self.dts[i0:]
        viol = [v for v in self.violations if v[0] >= i0]
        if viol:                    # 闭锁重判:raise 被 app 的 except 吞掉也照样作废
            raise TimebaseError(
                f"窗口 {label!r} 有 {len(viol)} 个 tick 的 dt 越界(首个 dt="
                f"{viol[0][1]:.6f}s @tick {viol[0][0]})——本次读数作废(不落盘)")
        if not dts:
            raise TimebaseError(f"窗口 {label!r} 未采到任何 tick(读数作废)")
        s = sorted(dts)
        p = lambda q: s[min(len(s) - 1, int(len(s) * q))]       # noqa: E731
        sim = sum(dts)
        wall = self.marks[-1][3] - self.marks[-1][2]
        out = {"label": label, "n": len(s), "dt_p50": round(p(.5), 6),
               "dt_p99": round(p(.99), 6), "dt_max": round(s[-1], 6),
               "dt_min": round(s[0], 6), "clamp_frac": round(
                   sum(1 for x in dts if x >= 0.0499) / len(dts), 5),
               "sim_sec": round(sim, 3), "wall_sec": round(wall, 3),
               "sim_over_wall": round(sim / wall, 3) if wall else None,
               "sim_over_nominal": round(sim / (len(dts) * FRAME_DT), 4),
               "mode": ("pin" if self.pin else "clamp") + ("+negctrl" if self.negctrl else "")}
        if self.pin and not self.negctrl:
            if out["dt_p99"] > FRAME_DT * TB_P99_X:
                raise TimebaseError(
                    f"窗口 {label!r} 的 dt p99={out['dt_p99']:.6f}s > "
                    f"{FRAME_DT * TB_P99_X:.6f}s(名义帧的 {TB_P99_X}×)⇒ 读数作废\n{out}")
            out["assertion"] = "pass(钉住:dt≡1/60,无钳位)"
        elif not self.pin and not self.negctrl:
            if out["dt_p50"] < TB_CLAMP_LO:
                raise TimebaseError(
                    f"对照臂 {label!r} 没复现钳位:dt p50={out['dt_p50']:.6f}s < "
                    f"{TB_CLAMP_LO} ⇒ A/B 对照本身无效(读数作废)\n{out}")
            out["assertion"] = "对照臂(clamp):已自证确实撞上 0.05 钳位"
        else:
            out["assertion"] = "负向对照:越界即红(见 TimebaseError)"
        self.windows.append(out)
        return out

    def summary(self) -> dict:
        """本次运行的时基自证块(写进产物 JSON;每个 _drive 窗口都校验过)。"""
        all_dts = sorted(self.dts)
        p = lambda q: all_dts[min(len(all_dts) - 1, int(len(all_dts) * q))]  # noqa: E731
        return {"mode": ("pin" if self.pin else "clamp") + ("+negctrl" if self.negctrl else ""),
                "n_ticks": len(self.dts),
                "dt_p50": round(p(.5), 6) if all_dts else None,
                "dt_p99": round(p(.99), 6) if all_dts else None,
                "dt_max": round(all_dts[-1], 6) if all_dts else None,
                "clamp_frac": round(sum(1 for x in self.dts if x >= 0.0499) /
                                    max(1, len(self.dts)), 5),
                "windows": self.windows}


def _timebase_of(app) -> Timebase:
    """本 app 的时基钉(首次调用时安装,幂等)。"""
    tb = getattr(app, "_u0_timebase", None)
    if tb is None:
        tb = Timebase(app, pin=_PIN_DT, negctrl=_NEGCTRL)
        app._u0_timebase = tb
    return tb


def _tb_meta(app) -> dict:
    tb = getattr(app, "_u0_timebase", None)
    if tb is None:
        return {"mode": "MISSING", "note": "本次运行未安装时基钉 ⇒ 读数不作数"}
    return tb.summary()


def _drive(app, seconds: float, hook=None, label: str = "") -> int:
    """以 `FRAME_DT` 推进**仿真时间**(时基钉见 `Timebase`),并断言本次读数合规。"""
    tb = _timebase_of(app)
    n = int(seconds / FRAME_DT)
    tb.begin(label or f"drive({seconds}s)")
    app._next_t = time.perf_counter()
    deadline = app._next_t
    for _ in range(n):
        app._tick()
        deadline = max(deadline + FRAME_DT, time.perf_counter())
        slack = deadline + FRAME_DT - time.perf_counter()
        if slack > 0:
            time.sleep(slack)
        if hook is not None:
            hook()
    tb.finish(label or f"drive({seconds}s)")
    return n


def _app_snapshot(app, appmod) -> dict:
    from neuropet.render import torso, torso_art
    from neuropet.core.windowing import working_set_mb
    tiers = {}
    for pid, h in app.pets.items():
        tiers[pid] = getattr(h, "frame_tier", None)
    return {
        "uploads": app._upload_count, "dedup_skips": app._dedup_skips,
        "render_stats": app.render_stats(),
        "ws_mb_end": round(working_set_mb(), 1),
        "ws_peak_mb": round(getattr(app, "_ws_peak", 0.0), 1),
        "mem_state": getattr(app, "_mem_state", None),
        "angle_step_deg": getattr(torso_art, "_angle_step", None),
        "rot_bytes": dict(torso_art._rot_bytes),
        "rot_entries": len(torso_art._rot),
        "masters": len(torso_art._masters),
        "l2_bytes": dict(torso._L2_bytes), "l2_entries": len(torso._L2),
        "panel_visible_at_end": bool(app._panel and
                                     app._panel.win.state() != "withdrawn"),
        "tiers_end": tiers, "pets": sorted(app.pets),
    }


def _write(tag: str, payload: dict) -> Path:
    OUT_DIR.mkdir(exist_ok=True)
    p = OUT_DIR / f"u0_{tag}.json"
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    return p


def _ratio(a, b):
    return round(a / b, 3) if b else None


# ---------------------------------------------------------------- steady
def run_steady(a) -> dict:
    import neuropet.core.instr as I
    if a.instr == "off":
        I.set_enabled(False)
    # 3 宠口径 = 蟑1 + 蝇 + 蟑1.5x → 同时覆盖「同物种多 traits/缩放」的桶分账稀释
    spec = ([("roach", 1.0), ("fly", 1.0), ("roach", 1.5)])[:a.pets]
    if a.pets == 2:
        spec = [("fly", 1.0), ("roach", 1.0)]
    tiers = {}
    own: list = []          # 与插桩无关的旁路采样:_frame_ms[-1](开关对照用)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        app, appmod = _make_app(tmp)
        pids = _add_pets(app, spec)
        if a.panel != "none":
            _make_panel(app, visible=(a.panel == "on"))
        if a.force == "walk":
            for h in app.pets.values():
                _force_walk(app, h, 1.0)

        def hook():
            for pid, h in app.pets.items():
                d = tiers.setdefault(pid, {})
                t = getattr(h, "frame_tier", 0)
                d[t] = d.get(t, 0) + 1
            if app._frame_ms:
                own.append(app._frame_ms[-1])

        _drive(app, a.warm, hook)                 # 预热(不进统计窗)
        I.reset()
        own.clear()
        up1, ded1 = app._upload_count, app._dedup_skips
        t_wall = time.perf_counter()
        _drive(app, a.sec, hook)
        wall = time.perf_counter() - t_wall
        rep = I.report()
        snap = _app_snapshot(app, appmod)
        snap["uploads_window"] = app._upload_count - up1
        snap["dedup_window"] = app._dedup_skips - ded1
        snap["upload_rate_hz"] = round(snap["uploads_window"] / wall, 2)
        snap["tiers_window"] = tiers
        snap["timebase"] = _tb_meta(app)
        _teardown(app)
    c, s = rep["counters"], rep["series"]
    rot_hit = c.get("rot_hit", 0) + c.get("rot_180", 0) + c.get("rot_slack", 0)
    rot_miss = c.get("rot_miss", 0)

    def _own_stats(xs):
        if not xs:
            return {"n": 0}
        z = sorted(xs)
        p = lambda q: z[min(len(z) - 1, int(len(z) * q))]
        return {"n": len(z), "p50": round(p(.5), 3), "p90": round(p(.9), 3),
                "p95": round(p(.95), 3), "p99": round(p(.99), 3),
                "mean": round(sum(z) / len(z), 3), "max": round(z[-1], 3)}
    out = {
        "own_frame_ms": _own_stats(own),
        "meta": {"tag": a.tag, "mode": "steady", "pets": a.pets, "sec": a.sec,
                 "warm": a.warm, "force": a.force, "panel": a.panel,
                 "instr": I.enabled(), "spec": spec, "wall_s": round(wall, 2),
                 "python": sys.version.split()[0], "ts": time.strftime("%F %T"),
                 "note": a.note},
        "series": s, "counters": c, "sets": rep["sets"], "notes": rep["notes"],
        "app": snap,
        "derived": {
            "rot_lookups": rot_hit + rot_miss,
            "rot_hit_rate": round(rot_hit / (rot_hit + rot_miss), 5) if (rot_hit + rot_miss) else None,
            "rot_miss_main": c.get("rot_miss_main", 0),
            "rot_miss_first": c.get("rot_miss_first", 0),
            "rot_miss_rebuild": c.get("rot_miss_rebuild", 0),
            "l2_hit_rate": round(c.get("l2_hit", 0) /
                                 max(1, c.get("l2_hit", 0) + c.get("l2_miss", 0)), 5),
            "interval_p95_over_p50": _ratio(s.get("interval_ms", {}).get("p95", 0),
                                            s.get("interval_ms", {}).get("p50", 0)),
            "interval_p99_over_p50": _ratio(s.get("interval_ms", {}).get("p99", 0),
                                            s.get("interval_ms", {}).get("p50", 0)),
            "dt_p95_over_p50": _ratio(s.get("dt_ms", {}).get("p95", 0),
                                      s.get("dt_ms", {}).get("p50", 0)),
            "dt_p99_over_p50": _ratio(s.get("dt_ms", {}).get("p99", 0),
                                      s.get("dt_ms", {}).get("p50", 0)),
        },
    }
    _print_steady(out)
    _write(a.tag, out)
    return out


def _print_steady(o: dict) -> None:
    s, c, d = o["series"], o["counters"], o["derived"]
    print(f"== [{o['meta']['tag']}] 稳态 {o['meta']['sec']}s 宠={o['meta']['pets']} "
          f"force={o['meta']['force']} panel={o['meta']['panel']} "
          f"instr={o['meta']['instr']} ==")
    for k in ("render_ms", "step_frame_ms", "step_ms", "panel_tick_ms",
              "interval_ms", "dt_ms", "rot_build_ms", "l2_build_ms", "ws_mb"):
        st = s.get(k)
        if not st or not st["n"]:
            continue
        print(f"  {k:15s} n={st['n']:<6d} p50={st['p50']:8.3f} p90={st['p90']:8.3f} "
              f"p95={st['p95']:8.3f} p99={st['p99']:8.3f} max={st['max']:8.3f}")
    print(f"  桶: rot hit={c.get('rot_hit',0)} 180={c.get('rot_180',0)} "
          f"slack={c.get('rot_slack',0)} miss={c.get('rot_miss',0)} "
          f"(main={d['rot_miss_main']} 首见={d['rot_miss_first']} "
          f"重建={d['rot_miss_rebuild']}) 命中率={d['rot_hit_rate']}")
    print(f"  L2: hit={c.get('l2_hit',0)} miss={c.get('l2_miss',0)} "
          f"命中率={d['l2_hit_rate']}  淘汰组={c.get('rot_evict_groups',0)} "
          f"兜底清空={c.get('rot_cache_cleared',0)}")
    mig = ", ".join(f"{k}={v}" for k, v in sorted(c.items())
                    if k.startswith("mem_state_"))
    print(f"  护栏: trim={c.get('mem_trim',0)} purge={c.get('mem_purge',0)} "
          f"状态迁移[{mig}] ws_end={o['app']['ws_mb_end']}MB "
          f"peak={o['app']['ws_peak_mb']}MB "
          f"angle_step={o['app']['angle_step_deg']}")
    print(f"  面板: tick_calls={c.get('panel_tick_calls',0)} "
          f"work={c.get('panel_tick_work',0)} hidden={c.get('panel_tick_hidden',0)}")
    print(f"  上传: window={o['app']['uploads_window']} "
          f"({o['app']['upload_rate_hz']}Hz) dedup={o['app']['dedup_window']} "
          f"tiers={o['app']['tiers_window']}")
    print(f"  旁路 _frame_ms(与开关无关): {o['own_frame_ms']}")


# ---------------------------------------------------------------- R1
def run_r1(a) -> dict:
    import neuropet.core.instr as I
    sp, ss, fl, nup = [], [], [], []   # 速度 / 舞台整数增量 / 状态浮点增量 / 每次上传的累计 tick 数
    tb = None
    with tempfile.TemporaryDirectory() as td:
        app, appmod = _make_app(Path(td))
        pids = _add_pets(app, [("fly", 1.0)])
        h = app.pets[pids[0]]
        _force_walk(app, h, a.intensity)
        tb = _timebase_of(app)
        last = app._upload_count
        _drive(app, 6.0)                      # 起速到稳态
        prev_c = None
        prev_p = None
        prev_n = None

        def hook():
            nonlocal last, prev_c, prev_p, prev_n
            if app._upload_count == last:
                return
            last = app._upload_count
            raw = app.stage._pet_raw.get(h.pet_id)
            if raw is None:
                return
            _, ix, iy = raw
            cs = (float(ix), float(iy))
            ps = (float(h.state.pos[0]), float(h.state.pos[1]))
            n_now = len(tb.dts)               # 本次上传时刻的累计 tick 数(取 Σdt 用)
            if prev_c is not None and len(ss) < 4000:
                ss.append(math.hypot(cs[0] - prev_c[0], cs[1] - prev_c[1]))
                fl.append(math.hypot(ps[0] - prev_p[0], ps[1] - prev_p[1]))
                sp.append(float(h.state.speed))
                nup.append((prev_n, n_now))
            prev_c, prev_p, prev_n = cs, ps, n_now

        _drive(app, a.sec, hook)
        speed_mean = sum(sp) / len(sp) if sp else 0.0
        # 每次上传覆盖的**仿真时间** Σdt(来自时基钉的逐 tick 实测 dt,不是推的)
        dt_up = [sum(tb.dts[a:b]) for a, b in nup]
        # 60fps 等效帧距 = 位移 ÷ 该段 Σdt × FRAME_DT(与显示节拍 k、dt 钳位无关)
        eq = [fl[i] / dt_up[i] * FRAME_DT for i in range(len(dt_up)) if dt_up[i] > 0]
        clk = getattr(h.body, "_gait", None)
        hz = clk.step_hz(h.body._speed) if clk else 0.0
        # 静止态去重:命令 IDLE,看 2s 内上传数
        from neuropet.core.contracts import Behavior, BehaviorCommand
        h.brain.decide = lambda _v: BehaviorCommand(Behavior.IDLE, intensity=0.0)
        _drive(app, 1.5)
        up_s = app._upload_count
        _drive(app, 2.0)
        static_uploads = app._upload_count - up_s
        tbm = _tb_meta(app)
        _teardown(app)

    def _stat(xs):
        if not xs:
            return {"n": 0}
        s = sorted(xs)
        mean = sum(s) / len(s)
        var = sum((x - mean) ** 2 for x in s) / len(s)
        p = lambda q: s[min(len(s) - 1, int(len(s) * q))]
        return {"n": len(s), "mean": round(mean, 4), "std": round(var ** 0.5, 4),
                "cv": round((var ** 0.5) / mean, 4) if mean else None,
                "p50": round(p(.5), 4), "p99": round(p(.99), 4),
                "max": round(s[-1], 4)}
    out = {"meta": {"tag": a.tag, "mode": "r1", "sec": a.sec,
                    "intensity": a.intensity, "species": "species.fruitfly",
                    "ts": time.strftime("%F %T"), "note": a.note},
           "timebase": tbm,
           "stage_int_increment": _stat(ss), "state_float_increment": _stat(fl),
           "speed_px_s": round(speed_mean, 2),
           # 60fps 等效帧距(实测):位移 ÷ 该段 Σdt × FRAME_DT。
           # ⚠ 已删除旧派生量 `px_per_frame_state = speed_mean/60`(双重失真:除数
           # 假设 1 帧 = 1/60s 而直驱口径下 dt 撞 0.05 钳位 ⇒ 错 3 倍;且 speed_mean
           # 混非稳态相位)。见 docs/handoff/r25-u0-timebase-r1.md。
           "per_frame_60hz_equiv": _stat(eq),
           "hz": round(hz, 3), "static_uploads_2s": static_uploads}
    print(f"== [{a.tag}] R1 慢速运动: 速度={out['speed_px_s']}px/s "
          f"显示帧数={len(ss)} 时基={tbm.get('mode')} dt_p50={tbm.get('dt_p50')} ==")
    print(f"  舞台整数坐标增量: {out['stage_int_increment']}")
    print(f"  状态浮点增量:     {out['state_float_increment']}")
    print(f"  60fps 等效帧距(位移/Σdt×FRAME_DT): {out['per_frame_60hz_equiv']}")
    print(f"  静止 2s 上传数={static_uploads}")
    _write(a.tag, out)
    return out


# ---------------------------------------------------------------- R4
def _hull_contains(pts, p) -> bool:
    """凸包包含判定(Andrew monotone chain + 叉积同号);<3 点视为不成立。"""
    if len(pts) < 3:
        return False
    xs = sorted(set(pts))
    if len(xs) < 3:
        return False

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for q in xs:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], q) <= 0:
            lower.pop()
        lower.append(q)
    upper = []
    for q in reversed(xs):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], q) <= 0:
            upper.pop()
        upper.append(q)
    hull = lower[:-1] + upper[:-1]
    if len(hull) < 3:
        return False
    # 点在凸多边形内(含边界):所有叉积同号
    signs = set()
    for i in range(len(hull)):
        o, q = hull[i], hull[(i + 1) % len(hull)]
        c = cross(o, q, p)
        signs.add(0 if abs(c) < 1e-9 else (1 if c > 0 else -1))
    return not ({-1, 1} <= signs)


def run_r4(a) -> dict:
    import neuropet.core.instr as I
    res = {}
    with tempfile.TemporaryDirectory() as td:
        app, appmod = _make_app(Path(td))
        pids = _add_pets(app, [("roach", 1.0), ("fly", 1.0)])
        for h in app.pets.values():
            _force_walk(app, h, 1.0)
        stats = {pid: {"frames": 0, "contain": 0, "air_max": 0, "air_sum": 0,
                       "contain_move": 0, "move_frames": 0} for pid in pids}

        def hook():
            for pid, h in app.pets.items():
                g = getattr(h.body, "_gait", None)
                if g is None:
                    continue
                d = stats[pid]
                n = len(g._feet)
                stance, air = [], 0
                for i in range(n):
                    try:
                        sw = g.is_swinging(i)
                    except Exception:
                        sw = False
                    if sw:
                        air += 1
                    else:
                        stance.append(tuple(g.foot_world(i)))
                ok = _hull_contains(stance, (float(h.state.pos[0]),
                                             float(h.state.pos[1])))
                d["frames"] += 1
                d["contain"] += int(ok)
                d["air_sum"] += air
                d["air_max"] = max(d["air_max"], air)
                if h.state.speed > 1.0:
                    d["move_frames"] += 1
                    d["contain_move"] += int(ok)

        _drive(app, 3.0)
        for pid in stats:
            stats[pid] = {k: 0 for k in stats[pid]}
        tbm = _tb_meta(app)
        _drive(app, a.sec, hook)
        _teardown(app)
    for pid, d in stats.items():
        res[pid] = dict(d)
        res[pid]["contain_rate"] = round(d["contain"] / max(1, d["frames"]), 4)
        res[pid]["contain_rate_moving"] = round(d["contain_move"] /
                                                max(1, d["move_frames"]), 4)
        res[pid]["air_mean"] = round(d["air_sum"] / max(1, d["frames"]), 3)
    out = {"meta": {"tag": a.tag, "mode": "r4", "sec": a.sec,
                    "centroid": "state.pos(躯干中心)", "n_legs": 6,
                    "ts": time.strftime("%F %T"), "note": a.note},
           "timebase": tbm,
           "per_pet": res}
    print(f"== [{a.tag}] R4 支撑多边形: {json.dumps(res, ensure_ascii=False)} ==")
    _write(a.tag, out)
    return out


# ---------------------------------------------------------------- R5
def _r5_arm(a, kind: str, hunger: float, fear: float, natural: bool) -> dict:
    with tempfile.TemporaryDirectory() as td:
        app, appmod = _make_app(Path(td))
        pid = _add_pets(app, [(kind, 1.0)])[0]
        h = app.pets[pid]
        if not natural:
            _force_walk(app, h, 1.0)
        b = h.brain

        def set_emo():
            for attr in ("_hunger",):
                if hasattr(b, attr):
                    setattr(b, attr, hunger)
            if hasattr(b, "emo") and isinstance(getattr(b, "emo"), list):
                b.emo[1] = hunger          # fly: E_HUNGER = 1
                b.emo[0] = fear            # fly: E_FEAR = 0
            if hasattr(b, "_fear"):
                b._fear = fear
            if hasattr(b, "emotion_"):
                try:
                    b.emotion_.hunger = hunger
                    b.emotion_.fear = fear
                except Exception:
                    pass

        _drive(app, 3.0)
        hz, spd, n_stop = [], [], 0

        def hook():
            nonlocal n_stop
            set_emo()                       # 注入在 tick 之前失效于脑内更新
            g = getattr(h.body, "_gait", None)
            if g is None:
                return
            hz.append(g.step_hz(h.body._speed))
            spd.append(float(h.state.speed))
            if h.state.speed < 0.05 * max(1.0, h.body.p.get("cruise", 1.0)):
                n_stop += 1

        def pre():
            set_emo()

        _drive(app, a.sec, pre)             # 用注入后的状态跑一段
        hz.clear(); spd.clear(); n_stop = 0
        tbm = _tb_meta(app)
        _drive(app, a.sec, hook)
        _teardown(app)
    n = max(1, len(hz))
    hz_m = sum(hz) / n
    spd_m = sum(spd) / n
    return {"species": kind, "hunger": hunger, "fear": fear, "natural": natural,
            "hz_mean": round(hz_m, 4), "speed_mean": round(spd_m, 3),
            "stride_px": round(spd_m / hz_m, 4) if hz_m else None,
            "stop_frac": round(n_stop / n, 4), "n": n, "timebase": tbm}


def run_r5(a) -> dict:
    import neuropet.core.instr as I
    arms = []
    for kind in ("roach", "fly"):
        for hunger in (0.10, 0.90):
            arms.append(_r5_arm(a, kind, hunger, 0.0, natural=False))
    for kind in ("roach", "fly"):           # fear:自然脑(停顿是决策门控)
        for fear in (0.0, 0.85):
            arms.append(_r5_arm(a, kind, 0.30, fear, natural=True))
    deltas = {}
    for kind in ("roach", "fly"):
        lo = next(x for x in arms if x["species"] == kind and x["hunger"] == 0.10)
        hi = next(x for x in arms if x["species"] == kind and x["hunger"] == 0.90)
        f0 = next(x for x in arms if x["species"] == kind and x["natural"]
                  and x["fear"] == 0.0)
        f1 = next(x for x in arms if x["species"] == kind and x["natural"]
                  and x["fear"] == 0.85)
        d = {}
        if lo["stride_px"] and hi["stride_px"]:
            d["stride_delta_pct"] = round(100 * (hi["stride_px"] / lo["stride_px"] - 1), 2)
        if lo["hz_mean"]:
            d["hz_delta_pct"] = round(100 * (hi["hz_mean"] / lo["hz_mean"] - 1), 2)
        d["stop_frac_delta_pp"] = round(100 * (f1["stop_frac"] - f0["stop_frac"]), 2)
        deltas[kind] = d
    out = {"meta": {"tag": a.tag, "mode": "r5", "sec": a.sec,
                    "ts": time.strftime("%F %T"), "note": a.note},
           "arms": arms, "deltas": deltas}
    print(f"== [{a.tag}] R5 情绪→步态 ==")
    for arm in arms:
        print(f"  {arm}")
    print(f"  Δ(hunger 0.10→0.90 / fear 0→0.85): {deltas}")
    _write(a.tag, out)
    return out


# ---------------------------------------------------------------- 冷启动
COLD_CHILD = r'''
import sys, time, os
t0 = time.perf_counter()
sys.path.insert(0, r"{root}")
import tempfile
from pathlib import Path
tmp = Path(tempfile.mkdtemp())
import neuropet.core.windowing as w
w.set_dpi_aware()
t_dpi = time.perf_counter()
import neuropet.core.app as appmod
appmod._SESSION_FILE = tmp / "session.json"
def _prof(pid):
    d = tmp / "profiles" / pid
    d.mkdir(parents=True, exist_ok=True)
    return d
appmod.profile_dir = _prof
t_imp = time.perf_counter()
app = appmod.App()
t_app = time.perf_counter()
app._pets_path = tmp / "pets.json"
app._cursor_getter = lambda: (-4000.0, -4000.0)   # 同 _pin_cursor:光标钉屏外

t_pre_run = time.perf_counter()
first = []
orig = appmod.App._tick
def probe(self):
    if not first:
        first.append(time.perf_counter() - t0)
    return orig(self)
appmod.App._tick = probe
# 时基自证:真 mainloop 的逐 tick dt 分布(证明冷启动读数**不在**直驱钳位口径上)
dts = []
_sf = appmod.App._step_frame
def probe_sf(self, dt):
    dts.append(float(dt))
    return _sf(self, dt)
appmod.App._step_frame = probe_sf
app.root.after({ms}, app.shutdown)
try:
    app.run()
except SystemExit:
    pass
_d = sorted(dts)
_p = lambda q: (round(_d[min(len(_d) - 1, int(len(_d) * q))], 5) if _d else None)
print("RESULT " + repr({{"t_first_tick": (first[0] if first else -1),
                        "t_import": t_imp - t0, "t_dpi": t_dpi - t0,
                        "t_app": t_app - t0, "t_pre_run": t_pre_run - t0,
                        "t_run_total": time.perf_counter() - t0,
                        "dt_n": len(_d), "dt_p50": _p(.5), "dt_p99": _p(.99),
                        "dt_max": _p(1.0),
                        "clamp_frac": (round(sum(1 for x in _d if x >= 0.0499)
                                             / len(_d), 4) if _d else None)}}))
'''


def run_coldstart(a) -> dict:
    child = ROOT / "logs" / "_u0_cold_child.py"
    child.write_text(COLD_CHILD.format(root=str(ROOT), ms=a.hold_ms), "utf-8")
    runs = []
    for i in range(a.runs):
        t = time.perf_counter()
        p = subprocess.run([sys.executable, str(child)], capture_output=True,
                           text=True, cwd=str(ROOT))
        wall = time.perf_counter() - t
        line = [l for l in p.stdout.splitlines() if l.startswith("RESULT ")]
        rec = {"wall_s": round(wall, 3), "rc": p.returncode}
        if line:
            rec.update(eval(line[-1][len("RESULT "):]))
        else:
            rec["err"] = (p.stderr or p.stdout)[-400:]
        runs.append(rec)
        print(f"  冷启动第{i+1}次: {rec}")
    ok = [r for r in runs if r.get("t_first_tick", -1) > 0]
    out = {"meta": {"tag": a.tag, "mode": "coldstart", "runs": a.runs,
                    "definition": "进程启动→首个 _tick 完成(含 roster/面板/托盘)",
                    "ts": time.strftime("%F %T"), "note": a.note},
           "runs": runs,
           "min_t_first_tick": min((r["t_first_tick"] for r in ok), default=None),
           "min_wall_s": min((r["wall_s"] for r in runs), default=None)}
    print(f"== [{a.tag}] 冷启动 min(首帧)={out['min_t_first_tick']}s "
          f"min(wall)={out['min_wall_s']}s ==")
    _write(a.tag, out)
    return out


# ---------------------------------------------------------------- all
def run_all(a) -> dict:
    """一键采完整套前值(**每个场景一个子进程**——与仓库「一套件一进程」同惯例,
    避免同进程反复建/销 Tk root 的干扰)。恢复/复跑只需这一条命令。"""
    plan = [
        ("steady", "s2", ["--pets", "2", "--sec", "60"]),
        ("steady", "s3", ["--pets", "3", "--sec", "60"]),
        ("steady", "s2walk", ["--pets", "2", "--sec", "60", "--force", "walk"]),
        ("steady", "s2panel", ["--pets", "2", "--sec", "60", "--panel", "on"]),
        ("steady", "s2off", ["--pets", "2", "--sec", "60", "--instr", "off"]),
        ("r1", "r1", ["--sec", "20"]),
        ("r4", "r4", ["--sec", "20"]),
        ("r5", "r5", ["--sec", "5"]),
        ("coldstart", "cold", ["--runs", "3"]),
    ]
    only = (a.only or "").split(",") if a.only else None
    outs = {}
    for mode, tag, extra in plan:
        if only and tag not in only:
            continue
        cmd = [sys.executable, str(Path(__file__).resolve()), mode,
               "--tag", tag, "--tag-suffix", a.tag_suffix] + extra
        if not _PIN_DT:
            cmd.append("--no-pin-dt")
        print(f"\n#### U0 {tag} : {' '.join(cmd[1:])}")
        p = subprocess.run(cmd, cwd=str(ROOT))
        if p.returncode != 0:
            print(f"!! [{tag}] 子进程 rc={p.returncode}(读数作废:见上文的 TimebaseError)")
        jp = OUT_DIR / f"u0_{tag}{a.tag_suffix}.json"
        if jp.exists():
            outs[tag] = json.loads(jp.read_text("utf-8"))
    # ---- 汇总(写进 docs/handoff/r25-u0-baseline.txt 的「前值」表) ----
    print("\n================ U0 前值汇总 ================")
    f2 = lambda v: f"{v:.2f}" if isinstance(v, (int, float)) else "n/a"
    for tag in ("s2", "s3", "s2walk", "s2panel", "s2off"):
        o = outs.get(tag)
        if not o:
            continue
        s, d, c = o["series"], o["derived"], o["counters"]
        rm = s.get("render_ms", {}) or {}
        own = o.get("own_frame_ms", {}) or {}
        pt = (s.get("panel_tick_ms", {}) or {}).get("p95")
        print(f"[{tag}] 帧耗(render_ms) n={rm.get('n', 0)} "
              f"p50={f2(rm.get('p50'))} p90={f2(rm.get('p90'))} "
              f"p99={f2(rm.get('p99'))} | 旁路 _frame_ms p50={f2(own.get('p50'))} "
              f"p90={f2(own.get('p90'))} p99={f2(own.get('p99'))} | "
              f"命中率={d.get('rot_hit_rate')} miss={c.get('rot_miss', 0)}"
              f"(重建={d.get('rot_miss_rebuild')}/主线程={d.get('rot_miss_main')}) "
              f"| L2={d.get('l2_hit_rate')} | trim={c.get('mem_trim', 0)} "
              f"purge={c.get('mem_purge', 0)} ws_end={o['app']['ws_mb_end']}MB "
              f"| panel_tick p95={f2(pt)} "
              f"work={c.get('panel_tick_work', 0)} hidden={c.get('panel_tick_hidden', 0)}")
    for tag in ("r1", "r4"):
        o = outs.get(tag)
        if o:
            print(f"[{tag}] " + json.dumps(
                {k: v for k, v in o.items() if k != "meta"}, ensure_ascii=False))
    o = outs.get("r5")
    if o:
        print(f"[r5] deltas={o['deltas']}")
        for arm in o["arms"]:
            print(f"     {arm}")
    print(f"[cold] min_first_tick={outs.get('cold', {}).get('min_t_first_tick')}s "
          f"min_wall={outs.get('cold', {}).get('min_wall_s')}s")
    _write(f"all{a.tag_suffix}", {"tags": sorted(outs), "note": a.note})
    return outs


# ---------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description="r25 U0 基线采集探针")
    ap.add_argument("mode", choices=["steady", "r1", "r4", "r5", "coldstart", "all"])
    ap.add_argument("--tag", default=None)
    ap.add_argument("--pets", type=int, default=2)
    ap.add_argument("--sec", type=float, default=60.0)
    ap.add_argument("--warm", type=float, default=5.0)
    ap.add_argument("--force", choices=["natural", "walk"], default="natural")
    ap.add_argument("--panel", choices=["none", "on", "off"], default="none")
    ap.add_argument("--instr", choices=["on", "off"], default="on")
    ap.add_argument("--intensity", type=float, default=0.4)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--hold-ms", type=int, default=1500)
    ap.add_argument("--note", default="")
    ap.add_argument("--only", default="",
                    help="all 模式子集(逗号分隔 tag,如 s2,s2walk)")
    ap.add_argument("--tag-suffix", default="",
                    help="产物名后缀(避免覆盖既有 logs/u0_*.json)")
    ap.add_argument("--no-pin-dt", action="store_true",
                    help="**对照臂**:关闭时基钉,复现 dt 撞 0.05 钳位的旧口径"
                         "(产物标 mode=clamp;该臂自带「确实撞了钳位」的自证)")
    ap.add_argument("--negctrl", action="store_true",
                    help="负向对照(判据红绿构造用):断言按钉住口径执行但**不装钉子**"
                         "⇒ 必抛 TimebaseError。仅用于验证判据不是纸板")
    a = ap.parse_args()
    global _PIN_DT, _NEGCTRL
    if a.no_pin_dt:
        _PIN_DT = False
    if a.negctrl:
        _NEGCTRL = True
        _PIN_DT = False
    if a.tag is None:
        a.tag = f"{a.mode}_{a.pets}p_{int(a.sec)}s"
    a.tag = a.tag + a.tag_suffix
    if a.instr == "on":
        os.environ["NEUROPET_INSTR"] = "1"      # 须在 neuropet 首次导入前
    else:
        os.environ.pop("NEUROPET_INSTR", None)
    t0 = time.perf_counter()
    if a.mode == "all":
        run_all(a)
    elif a.mode == "steady":
        run_steady(a)
    elif a.mode == "r1":
        run_r1(a)
    elif a.mode == "r4":
        run_r4(a)
    elif a.mode == "r5":
        run_r5(a)
    else:
        run_coldstart(a)
    print(f"[u0] 用时 {time.perf_counter() - t0:.1f}s → logs/u0_{a.tag}.json")


if __name__ == "__main__":
    main()
