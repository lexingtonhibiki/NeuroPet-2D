"""A frame time budget must never permanently starve half a pet population."""
from collections import Counter, deque
from types import SimpleNamespace as NS
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

@pytest.fixture
def crowded_scene(monkeypatch):
    import neuropet.core.app as module
    from neuropet.core.contracts import Behavior, MovementMode
    from neuropet.render import renderer
    clock = NS(now=1000.)
    monkeypatch.setattr(module, "time", NS(perf_counter=lambda: clock.now))
    def render(pose, traits):
        clock.now += .014  # One realistic expensive pose exhausts the time budget.
        return object()
    monkeypatch.setattr(renderer, "render_pose", render)
    app = module.App.__new__(module.App)
    app.pets = {}
    for i in range(10):
        state = NS(speed=10, pos=(100,100), mode=MovementMode.CRAWL,
                   held=False, frozen=False, activity=Behavior.EXPLORE,
                   species_id="species.fruitfly")
        app.pets[str(i)] = NS(pet_id=str(i), state=state,
            body=NS(_turn_norm=0, pose=lambda: {}), frame_tier=1, tier_since=0,
            last_cost_ms=0, cost_backoff=0, last_render=0, skip_streak=0)
    serviced = []
    app.stage = NS(begin_frame=lambda: None, end_frame=lambda: None,
        move_pets=lambda positions: None, fit_viewport=lambda: None,
        update_pet=lambda pid, *args: serviced.append(pid))
    app._kernel = NS(run_stage=lambda stage: None, drag_overlay=lambda h: None)
    app._render_traits = lambda h: {}
    app._prewarm_on = app._static_dirty = False
    app._render_cursor = app._upload_count = 0
    app._frame_ms = deque(maxlen=120)
    return app, clock, serviced

def test_every_pet_gets_pose_updates_when_time_budget_runs_out(crowded_scene):
    app, clock, serviced = crowded_scene
    for _ in range(30):
        app._render(.05)
        clock.now += .05
    counts = Counter(serviced)
    assert set(counts) == set(app.pets), counts
    assert max(counts.values()) - min(counts.values()) <= 1

def test_next_frame_starts_after_last_serviced_slot_including_skips(crowded_scene):
    app, clock, serviced = crowded_scene
    for _ in range(2):
        app.pets["0"].last_render = app.pets["2"].last_render = clock.now
        app._render(.05)
        clock.now += .05
    assert serviced == ["1", "3"], serviced

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
