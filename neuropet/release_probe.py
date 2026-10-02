"""Real Tk mainloop resource probe; all pet files live in a temporary directory."""
from __future__ import annotations
import argparse
import ctypes
import json
import math
import random
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def memory():
    import ctypes.wintypes as wt
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]
    counter = Counters()
    counter.cb = ctypes.sizeof(counter)
    kernel = ctypes.windll.kernel32
    kernel.GetCurrentProcess.restype = wt.HANDLE
    psapi = ctypes.windll.psapi
    psapi.GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.POINTER(Counters), wt.DWORD]
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counter), counter.cb):
        raise ctypes.WinError()
    return {"working_set_mib": counter.WorkingSetSize / 1048576,
            "private_mib": counter.PrivateUsage / 1048576,
            "peak_working_set_mib": counter.PeakWorkingSetSize / 1048576}


def image_bytes(value, seen):
    from PIL import Image
    if isinstance(value, Image.Image):
        if id(value) in seen:
            return 0
        seen.add(id(value))
        return value.width * value.height * len(value.getbands())
    if isinstance(value, dict):
        return sum(image_bytes(v, seen) for v in value.values())
    if isinstance(value, (tuple, list)):
        return sum(image_bytes(v, seen) for v in value)
    return 0


def cache_snapshot():
    from neuropet.render import renderer as render, torso, torso_art
    caches = {"masters": torso_art._masters, "rot": torso_art._rot, "l2": torso._L2,
              "shadow_patches": render._shadow_patches, "shadow_sprites": render._shadow_sprites,
              "shadow_masks": render._SHADOW_MASK_CACHE, "snap": render._SNAP_CACHE,
              "whole_frames": render._OPT7_CACHE}
    seen = set()
    result = {}
    for name, cache in caches.items():
        # Copy while the prewarm thread might change its mapping.
        for attempt in range(3):
            try:
                entries = list(cache.values())
                break
            except RuntimeError:
                if attempt == 2:
                    entries = []
        result[name] = {"entries": len(entries),
                        "unique_mib": round(image_bytes(entries, seen) / 1048576, 3)}
    result["total_unique_mib"] = round(sum(v["unique_mib"] for v in result.values()), 3)
    return result


def percentile(values, q):
    ordered = sorted(values)
    return round(ordered[min(len(ordered)-1, int((len(ordered)-1) * q))], 3) if ordered else 0


def _pin_cursor(app):
    app._cursor_getter = lambda: (-4000.0, -4000.0)


def _force_walk(app, handle):
    from neuropet.core.contracts import Behavior, BehaviorCommand
    state = handle.state
    def decide(view):
        return BehaviorCommand(Behavior.EXPLORE,
            target=(state.pos[0]+400*math.cos(state.heading),
                    state.pos[1]+400*math.sin(state.heading)),
            intensity=1.0, reason="release-probe")
    handle.brain.decide = decide


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pets", type=int, default=2)
    parser.add_argument("--seconds", type=float, default=20)
    parser.add_argument("--panel", choices=("on", "off"), default="on")
    parser.add_argument("--output", required=True)
    parser.add_argument("--scale", type=float, default=1.0)
    args = parser.parse_args()
    random.seed(20261002)
    with tempfile.TemporaryDirectory(prefix="neuropet-release-") as temp:
        from neuropet.core import config
        data = Path(temp)
        config.DATA_DIR = data
        config.CONFIG_PATH = data / "config.json"
        config.PETS_PATH = data / "pets.json"
        config.PROFILES_DIR = data / "profiles"
        from neuropet.core.windowing import set_dpi_aware
        set_dpi_aware()
        from neuropet.core.app import App
        phases = {"modules": memory()}
        app = App()
        phases["stage"] = memory()
        app.cfg.max_pets = max(10, args.pets)
        _pin_cursor(app)
        for i in range(args.pets):
            species = "species.cockroach" if i % 2 == 0 else "species.fruitfly"
            width, height = app.world.screen
            pid = app.add_pet(species, pos=(width * (0.15 + 0.17 * (i % 5)),
                                           height * (0.32 + 0.3 * (i // 5))))
            h = app.pets[pid]
            if args.scale != 1.0:
                app.set_pet_scale(pid, args.scale)
            h.state.heading = 0.4 * i
            _force_walk(app, h)
        phases["pets"] = memory()
        try:
            from neuropet.ui.compact_panel import ControlPanel
        except ImportError:
            from neuropet.ui.panel import ControlPanel
        app._panel = ControlPanel(app)
        app._panel.set_on_drop_request(app._on_panel_drop)
        phases["panel"] = memory()
        if args.panel == "off":
            app._panel.hide()
        app._prewarm_torso()
        frames, costs, uploads, samples = [], [], [], []
        started = time.perf_counter()
        cpu_started = time.process_time()
        last = started
        next_due = started
        sim = 0.0
        def frame():
            nonlocal last, next_due, sim
            now = time.perf_counter()
            dt = min(0.05, max(0.0001, now-last))
            last = now
            sim += dt
            before = app._upload_count
            t0 = time.perf_counter()
            app._step_frame(dt)
            cost = (time.perf_counter()-t0)*1000
            if now-started >= 3:
                frames.append(now)
                costs.append(cost)
                uploads.append(app._upload_count-before)
            if not samples or now-started >= samples[-1]["elapsed_s"] + 1:
                samples.append({"elapsed_s": round(now-started, 3), **memory()})
            if now-started >= args.seconds:
                report = {"pets": len(app.pets), "panel": args.panel,
                          "scale": args.scale, "workload": "forced walking, alternating species, cursor excluded",
                          "cpu_one_core_percent": round(100*(time.process_time()-cpu_started)/(now-started), 2),
                          "seconds": round(now-started, 3), "screen": app.world.screen,
                          "memory_end": memory(), "memory_samples": samples,
                          "memory_phases": phases,
                          "frame_cost_ms": {"p50": percentile(costs, .5), "p95": percentile(costs, .95),
                                            "p99": percentile(costs, .99)},
                          "loop_fps": round((len(frames)-1)/(frames[-1]-frames[0]), 2) if len(frames)>1 else 0,
                          "sprite_updates_per_pet_s": round(sum(uploads)/(max(.001, now-started-3)*args.pets),2),
                          "cache": cache_snapshot()}
                output = Path(args.output)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(report, indent=2), encoding="utf-8")
                print(json.dumps({k:v for k,v in report.items() if k != "memory_samples"}, indent=2), flush=True)
                app.shutdown()
                return
            next_due += 1/60
            if next_due < now-.25:
                next_due = now
            app.root.after(max(1, int((next_due-time.perf_counter())*1000)), frame)
        app.root.after(1, frame)
        app.root.mainloop()


if __name__ == "__main__":
    main()
