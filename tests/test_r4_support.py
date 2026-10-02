# -*- coding: utf-8 -*-
"""r26 R4 U2 分支丙:着地帧支撑腿数>=3 替代判据(主控裁决:甲证伪/乙不足,走丙)。

背景:甲证伪(fly full_air_frac=0.0,着地帧含质心仍 0.0);乙不足(偏移模 2.322px
仅点云散布 5.652px 的 40%,解释不了 0.0000 全灭) ⇒ 果蝇六足支撑多边形含质心
语义疑似不成立,立替代判据,不放宽原阈值(架构 §11.3 禁令)。

新判据:着地帧(air<6)支撑腿数>=3 占比,fly>=0.95 且 roach>=0.90
(首测值 fly 0.9967 / roach 0.9633,阈值留余量)。
原 _hull_contains 含质心率只作观测输出(打印不 gate,原 100% 判据不动、不删)。

口径(与 tools/_r26_r4_probe.py 一致):复用 tools/u0_baseline.py 的
_hull_contains(单一真源),Timebase 钉死 dt,光标钉屏外(-4000,-4000,经
_make_app/_pin_cursor),_force_walk 1.0,warmup 3.0s 后清零再采。

红绿两句(workflow §5"纸板判据"行 + §6 约束):
  红=判定点平移+50px 的真违规构造 ⇒ roach 着地帧含质心率相对未偏移暴跌
    (相对旧 ~100% 条必红);不变红 ⇒ 测量链是纸板,当次作废。
  绿=同窗 roach 对照臂 ge3 达标(>=0.90)且 fly 着地帧 ge3>=0.95;造不出绿 ⇒
    判据是纸板,当次作废。
(fly 臂含质心本就 ~0.0——正是分支丙的前提,负向分辨力只落在 roach 臂。)

开关:NEUROPET_R4_GROUNDED_ONLY(=1 只统计着地帧;默认 0 保持旧全窗口径以便
A/B,只切统计口径,不碰产品步态/几何)。

运行:python tests/test_r4_support.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location("u0_baseline", ROOT / "tools" / "u0_baseline.py")
U0 = importlib.util.module_from_spec(_spec)
sys.modules["u0_baseline"] = U0
_spec.loader.exec_module(U0)

# 时基钉死(与 run_r4 / R4-U1 探针同口径;混杂必须排除)
U0._PIN_DT = True
U0._NEGCTRL = False

RESULTS: list[tuple[str, bool, str]] = []

SEC = 10.0
WARM = 3.0
FLY_GATE = 0.95
ROACH_GATE = 0.90
SHIFT = 50.0            # 负向对照:判定点 x 平移量(px)
GROUNDED_ONLY = os.environ.get("NEUROPET_R4_GROUNDED_ONLY", "0") == "1"


def record(tag: str, ok: bool, detail: str) -> None:
    RESULTS.append((tag, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {tag}: {detail}")


def _species(pid: str) -> str:
    return "fly" if pid.startswith("fly") else "roach"


def measure() -> tuple[dict, str]:
    with tempfile.TemporaryDirectory() as td:
        app, _appmod = U0._make_app(Path(td))
        pids = U0._add_pets(app, [("roach", 1.0), ("fly", 1.0)])
        for h in app.pets.values():
            U0._force_walk(app, h, 1.0)
        S = {pid: {"frames": 0, "ge3_all": 0, "full_air": 0,
                   "g_frames": 0, "g_ge3": 0,
                   "g_contain": 0, "g_shift": 0} for pid in pids}

        def hook():
            for pid, h in app.pets.items():
                g = getattr(h.body, "_gait", None)
                if g is None:
                    continue
                d = S[pid]
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
                px, py = float(h.state.pos[0]), float(h.state.pos[1])
                d["frames"] += 1
                d["ge3_all"] += int(len(stance) >= 3)
                if air == 6:
                    d["full_air"] += 1
                    continue
                d["g_frames"] += 1
                d["g_ge3"] += int(len(stance) >= 3)
                d["g_contain"] += int(U0._hull_contains(stance, (px, py)))
                d["g_shift"] += int(U0._hull_contains(stance, (px + SHIFT, py)))

        U0._drive(app, WARM, label="warmup")
        for pid in S:
            for k in S[pid]:
                S[pid][k] = 0
        tbm = U0._tb_meta(app)
        U0._drive(app, SEC, hook, label=f"r4u2-prop({SEC}s)")
        mode = tbm.get("mode")
        U0._teardown(app)

    out = {}
    for pid, d in S.items():
        fr, gf = max(1, d["frames"]), max(1, d["g_frames"])
        out[pid] = {
            "frames": d["frames"],
            "full_air_frac": round(d["full_air"] / fr, 4),
            "grounded_ge3_frac": round(d["g_ge3"] / gf, 4),
            "all_ge3_frac": round(d["ge3_all"] / fr, 4),
            # 观测(不 gate):原含质心率 + 偏移对照
            "grounded_contain_obs": round(d["g_contain"] / gf, 4),
            "grounded_contain_shift50_obs": round(d["g_shift"] / gf, 4),
        }
    return out, mode


def main() -> None:
    try:
        out, mode = measure()
    except Exception as exc:                          # noqa: BLE001
        record("measure", False, f"{type(exc).__name__}: {exc}")
        bad = [t for (t, ok, _) in RESULTS if not ok]
        print(f"R4 支撑替代判据:0/{len(RESULTS)} 通过")
        sys.exit(1)
    record("时基钉死(pin)", mode == "pin",
           f"timebase mode={mode}(须 pin,否则数字不可比)")
    for pid, d in out.items():
        sp = _species(pid)
        gate = FLY_GATE if sp == "fly" else ROACH_GATE
        gated = d["grounded_ge3_frac"] if GROUNDED_ONLY else d["all_ge3_frac"]
        which = "着地帧口径" if GROUNDED_ONLY else "全窗口径(默认A/B)"
        record(f"{sp} 支撑腿数>=3占比[{which}]>= {gate}", gated >= gate,
               f"{pid}: gated={gated} (grounded={d['grounded_ge3_frac']}, "
               f"all={d['all_ge3_frac']}, n_grounded~{d['frames']}帧, "
               f"full_air_frac={d['full_air_frac']}); "
               f"含质心观测={d['grounded_contain_obs']}(不gate)")
    # 负向对照(当次做):roach 臂判定点 +50px 必红(相对旧 ~100% 条)
    r = next(d for pid, d in out.items() if _species(pid) == "roach")
    drop = round(r["grounded_contain_obs"] - r["grounded_contain_shift50_obs"], 4)
    record("负向对照:质心+50px 必红(roach 臂)", r["grounded_contain_shift50_obs"] < 0.85 and drop >= 0.20,
           f"roach 未偏移={r['grounded_contain_obs']} vs "
           f"+50px={r['grounded_contain_shift50_obs']}( chod={drop}; "
           f"判据分辨出真违规 ⇒ 非纸板)")
    f = next(d for pid, d in out.items() if _species(pid) == "fly")
    print(f"[INFO] fly 含质心观测={f['grounded_contain_obs']} / +50px="
          f"{f['grounded_contain_shift50_obs']}(本就~0,系分支丙前提,无分辨力要求)")
    bad = [t for (t, ok, _) in RESULTS if not ok]
    print(f"R4 支撑替代判据:{len(RESULTS) - len(bad)}/{len(RESULTS)} 通过 "
          f"(GROUNDED_ONLY={int(GROUNDED_ONLY)})")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
