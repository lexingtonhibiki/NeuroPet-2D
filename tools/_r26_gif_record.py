# -*- coding: utf-8 -*-
"""r26 GIF U1 录制链路(arch docs/handoff/r26-visible-arch-r1.md §3.2 + §3.4-U1)。

口径:
- 单宠纯净舞台:无面板/无第二宠/光标钉屏外 (-4000,-4000)。
- 按产品真实单帧更新路径 `app._step_frame(dt)` 录制,固定 `dt=1/60`。
  渲染负载不会改变模拟时间或 GIF 播放速度。
- 输出 GIF 平均 15fps(70/70/60ms 循环)、2~6s(30~90 帧);帧尺寸 = 精灵原生尺寸
  (与现 assets/demos/ 同档);roach ≤600KB、fly ≤200KB。
- 固定 seed,打印 seed + 版本 sha。
- roach 强制行走 ≥1 体长段(EXPLORE 恒定目标);fly 录 FLY_WANDER saccade 飞行段。

用法:
    python tools/_r26_gif_record.py --species roach --seed 20260923
    python tools/_r26_gif_record.py --species fly --seed 20260923 --out scratch/x.gif

只新增本文件;不动 neuropet/ tests/ 现有文件;默认落 scratch/,不进 assets/demos/。
"""
from __future__ import annotations

import argparse
import math
import random
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PIN_CURSOR = (-4000.0, -4000.0)   # 屏外:防 §11.4③ 光标污染
BG = (250, 250, 246)              # 与现 demos 实测角落底色一致
GIF_DURATIONS_MS = (70, 70, 60)  # GIF 以 10ms 为单位;三帧平均正好 15fps
RECORD_S = 2.0                   # 默认短片:2s × 15fps = 30 帧
SAMPLE_EVERY = 4                  # 60Hz tick 每 4 拍取 1 帧 = 15fps
SPECIES = {"roach": "species.cockroach", "fly": "species.fruitfly"}
SIZE_CAP = {"roach": 600 * 1024, "fly": 200 * 1024}


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True,
                              cwd=str(ROOT)).stdout.strip()
    except Exception:
        return "unknown"


def record(species: str, seed: int, out: Path,
           seconds: float = RECORD_S) -> dict:
    from PIL import Image

    random.seed(seed)
    tmp = Path(tempfile.mkdtemp(prefix=f"r26gif_{species}_"))
    import neuropet.core.app as appmod
    appmod._SESSION_FILE = tmp / "session.json"
    _pdir = tmp / "profiles"

    def _tp(pet_id: str) -> Path:
        d = _pdir / pet_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    appmod.profile_dir = _tp
    app = appmod.App()
    app._pets_path = tmp / "pets.json"
    app._cursor_getter = lambda: PIN_CURSOR   # 光标钉屏外(无面板/无第二宠)
    pid = app.add_pet(SPECIES[species])
    h = app.pets[pid]

    from neuropet.core.contracts import Behavior, BehaviorCommand
    st0 = h.state
    p0 = (float(st0.pos[0]), float(st0.pos[1]))
    base_h = float(st0.heading)
    fly_mode = (species == "fly")
    sim_t = 0.0

    def _cmd(view):
        nonlocal sim_t
        t = sim_t
        sim_t += 1.0 / 60.0
        if fly_mode:
            ang = base_h + 1.0 * math.sin(t * 0.7) + 0.15 * t
        else:
            ang = base_h + 0.45 * math.sin(t * 0.25) + 0.025 * t
        tgt = (st0.pos[0] + 400.0 * math.cos(ang),
               st0.pos[1] + 400.0 * math.sin(ang))
        beh = Behavior.FLY_WANDER if fly_mode else Behavior.EXPLORE
        return BehaviorCommand(beh, target=tgt, intensity=0.9, reason="r26-gif")

    h.brain.decide = _cmd

    # 固定仿真时钟:调用产品 mainloop 最终使用的单帧更新函数,避免本机
    # 渲染耗时/调度延迟改变录制的模拟时间与 GIF 播放速度。
    raws: list = []
    n_ticks = int(seconds * 60)
    app._prewarm_on = False
    for _ in range(n_ticks):
        h.last_render = -1e9
        app._step_frame(1.0 / 60.0)
        raw = app.stage._pet_raw.get(pid)
        if raw is not None:
            raws.append(raw[0].copy())
    p1 = (float(h.state.pos[0]), float(h.state.pos[1]))
    disp = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
    try:
        app._running = False
        app.stage.destroy()
    except Exception:
        pass
    try:
        app.root.destroy()
    except Exception:
        pass

    sampled = raws[::SAMPLE_EVERY][:90]   # 15fps,上限 90 帧
    if len(sampled) < 30:
        raise RuntimeError(f"有效帧不足: {len(sampled)} < 30(口径 30~90 帧)")
    w, hh = sampled[0].size
    composed = []
    for t in sampled:
        b = Image.new("RGB", (w, hh), BG)
        if t.size != (w, hh):             # 精灵尺寸变化则按首帧画布居中
            c = Image.new("RGBA", (w, hh), (0, 0, 0, 0))
            c.alpha_composite(t, ((w - t.size[0]) // 2, (hh - t.size[1]) // 2))
            t = c
        b.paste(t, (0, 0), t)
        composed.append(b.quantize(colors=128, method=Image.MEDIANCUT,
                                   dither=Image.NONE))
    out.parent.mkdir(parents=True, exist_ok=True)
    durations = [GIF_DURATIONS_MS[i % len(GIF_DURATIONS_MS)]
                 for i in range(len(composed))]
    composed[0].save(out, save_all=True, append_images=composed[1:],
                     duration=durations, loop=0, optimize=True)
    size = out.stat().st_size
    cap = SIZE_CAP[species]
    body_len = 115.0 if species == "roach" else 30.0
    return {"out": str(out), "frames": len(composed), "size": size,
            "canvas": (w, hh), "disp_px": round(disp, 1),
            "body_len": body_len, "walk_ok": disp >= body_len,
            "size_ok": size <= cap, "seed": seed, "sha": _git_sha()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--species", choices=("roach", "fly"), default="roach")
    ap.add_argument("--seed", type=int, default=20260923)
    ap.add_argument("--seconds", type=float, default=RECORD_S)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    out = Path(a.out) if a.out else ROOT / "scratch" / f"_r26_gif_{a.species}_trial.gif"
    r = record(a.species, a.seed, out, a.seconds)
    print(f"seed={r['seed']} sha={r['sha']}")
    print(f"out={r['out']} frames={r['frames']} size={r['size']}B "
          f"canvas={r['canvas'][0]}x{r['canvas'][1]}")
    print(f"disp={r['disp_px']}px body_len={r['body_len']} walk_ok={r['walk_ok']} "
          f"size_ok={r['size_ok']}")
    if a.species == "roach" and not r["walk_ok"]:
        print("FAIL: roach 行走不足 1 体长")
        return 1
    if not r["size_ok"]:
        print("FAIL: 体积超限")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
