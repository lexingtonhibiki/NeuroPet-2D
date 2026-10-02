"""An overloaded desktop must not repeatedly simulate the scheduler's time debt."""
import sys
import math
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

def test_late_frames_advance_by_elapsed_wall_time(monkeypatch):
    import neuropet.core.app as module
    app = module.App.__new__(module.App)
    clock = SimpleNamespace(now=1000.)
    monkeypatch.setattr(module, "time", SimpleNamespace(perf_counter=lambda: clock.now))
    app._running = True
    app._last_tick = app._next_t = clock.now
    app.root = SimpleNamespace(after=lambda *args: None)
    dts = []
    app._step_frame = dts.append
    for _ in range(5):
        clock.now += .03
        app._tick()
    assert all(math.isclose(dt, .03, abs_tol=1e-8) for dt in dts), dts
    assert math.isclose(sum(dts), .15, abs_tol=1e-8)

if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
