"""Focused v0.2.0 checks: real controls, isolated saves and bounded motion.

The App fixtures use temporary archives; registry operations use an in-memory
winreg substitute, so these checks never change Windows sign-in settings.
"""
import gc
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_compact_panel import panel_app, real_app


def settings(panel, app):
    if not app.pets:
        panel.add_roach_btn.invoke()
    panel.open_settings()
    app.root.update()
    gc.collect()
    app.root.update()
    combos = [entry[1] for entry in panel._settings_vars.values()
              if isinstance(entry, tuple)]
    return panel.extra_windows["settings"], combos


def choose(box, index, root):
    box.current(index)
    box.event_generate("<<ComboboxSelected>>")
    root.update()


def test_language_current_value_survives_gc_and_user_change_saves(panel_app):
    from neuropet.core.i18n import get_lang
    from neuropet.core import config
    panel, app = panel_app
    win, boxes = settings(panel, app)
    language = boxes[0]
    before = language.current()
    assert before >= 0 and language.get()
    target = 1 - before
    choose(language, target, app.root)
    wanted = ("zh-CN", "en")[target]
    assert get_lang() == wanted
    assert json.loads(config.CONFIG_PATH.read_text("utf-8"))["app"]["language"] == wanted
    assert win.winfo_exists()


def test_size_and_speed_user_events_apply_and_save_without_rebuilding_body(panel_app):
    from neuropet.core import config
    panel, app = panel_app
    win, boxes = settings(panel, app)
    pid = panel.selected()
    size, speed = boxes[2:4]
    assert size.get() and "disabled" not in size.state()
    choose(size, 3, app.root)
    assert app.pet_scale(pid) == 1.5
    body = app.pets[pid].body
    state = app.pets[pid].state
    pos, heading = state.pos, state.heading
    choose(speed, 6, app.root)
    assert app.crawl_speed() == 8.0
    assert app.pets[pid].body is body
    assert (state.pos, state.heading) == (pos, heading)
    assert json.loads(config.CONFIG_PATH.read_text("utf-8"))["app"]["crawl_speed"] == 8.0
    assert json.loads((config.PROFILES_DIR / pid / "scale.json").read_text("utf-8"))["scale"] == 1.5
    win.destroy()
    panel.open_settings()
    app.root.update()
    reopened = [entry[1] for entry in panel._settings_vars.values()
                if isinstance(entry, tuple)]
    assert reopened[2].get() == "1.5×"
    assert reopened[3].get() == "8×"


def test_real_dropdown_popup_is_in_feeding_passthrough_area(panel_app):
    panel, app = panel_app
    win, boxes = settings(panel, app)
    box = boxes[0]
    app.root.tk.call("ttk::combobox::Post", str(box))
    app.root.update()
    popup = app.root.tk.call("ttk::combobox::PopdownWindow", str(box))
    assert int(app.root.tk.call("winfo", "ismapped", popup))
    x = int(app.root.tk.call("winfo", "rootx", popup)) + 8
    y = int(app.root.tk.call("winfo", "rooty", popup)) + 8
    app._refresh_panel_rect()
    try:
        assert app._panel_contains(x, y)
    finally:
        app.root.tk.call("ttk::combobox::Unpost", str(box))


def test_settings_checkbox_applies_and_saves_live(panel_app):
    from neuropet.core import config
    from neuropet.core.i18n import t
    panel, app = panel_app
    win, boxes = settings(panel, app)
    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from descendants(child)
    checkbox = next(w for w in descendants(win)
                    if w.winfo_class() == "TCheckbutton" and w.cget("text") == t("settings.trails"))
    before = app.trails()
    checkbox.invoke()
    app.root.update()
    assert app.trails() is not before
    assert json.loads(config.CONFIG_PATH.read_text("utf-8"))["app"]["trail_enabled"] is not before


def test_settings_close_leaves_application_and_pets_alive(panel_app, monkeypatch):
    from neuropet.core.i18n import t
    panel, app = panel_app
    win, boxes = settings(panel, app)
    ids = set(app.pets)
    calls = []
    monkeypatch.setattr(app, "shutdown", lambda: calls.append("shutdown"))
    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from descendants(child)
    close = next(w for w in descendants(win)
                 if w.winfo_class() == "TButton" and w.cget("text") == t("settings.close"))
    close.invoke()
    app.root.update()
    assert not win.winfo_exists()
    assert not calls and set(app.pets) == ids
    assert panel.win.winfo_exists()


def test_autostart_on_and_off_report_actual_state_without_registry_writes(real_app, monkeypatch):
    from neuropet.core import autostart
    values = {"OtherApp": '"C:\\Other App\\other.exe"'}
    class Key:
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            pass
    def query(key, name):
        if name not in values:
            raise FileNotFoundError(name)
        return values[name], 1
    def delete(key, name):
        if name not in values:
            raise FileNotFoundError(name)
        del values[name]
    fake = SimpleNamespace(HKEY_CURRENT_USER=1, KEY_SET_VALUE=2, REG_SZ=1,
                           OpenKey=lambda *a: Key(), CreateKeyEx=lambda *a: Key(),
                           QueryValueEx=query, DeleteValue=delete,
                           SetValueEx=lambda key, name, reserved, kind, value: values.__setitem__(name, value))
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(autostart.sys, "frozen", True, raising=False)
    monkeypatch.setattr(autostart.sys, "executable", "C:\\测试 程序\\NeuroPet-2D.exe")
    app = real_app
    assert app.set_autostart(True) is True
    assert values[autostart.VALUE_NAME] == '"C:\\测试 程序\\NeuroPet-2D.exe" --autostart'
    assert app.set_autostart(False) is False
    assert values == {"OtherApp": '"C:\\Other App\\other.exe"'}


@pytest.mark.parametrize("rect, heading, edge", [
    ((0, 0, 1000, 740), math.pi / 2, "bottom"),
    ((0, 60, 1000, 800), -math.pi / 2, "top"),
    ((60, 0, 1000, 800), math.pi, "left"),
    ((0, 0, 940, 800), 0.0, "right"),
])
def test_all_taskbar_edges_bound_motion_without_position_jumps(rect, heading, edge):
    from neuropet.core.contracts import PetState
    from neuropet.core.world import WorldModel
    from neuropet.species.cockroach import AmericanCockroach
    world = WorldModel(1000, 800)
    world.desktop._rect = rect
    state = PetState("test", "species.cockroach", "test", (500, 400))
    body = AmericanCockroach().create_body(state)
    view = world.snapshot("test", 0.0)
    x0, y0, x1, y1 = body._bounds(view)
    state.pos = {"bottom": (500, y1 - 1), "top": (500, y0 + 1),
                 "left": (x0 + 1, 400), "right": (x1 - 1, 400)}[edge]
    state.heading = heading
    body._speed = 3000.0
    for _ in range(12):
        before = state.pos
        body._integrate(1 / 60, view)
        assert math.dist(before, state.pos) <= 3000 / 60 + 1e-6
        assert x0 <= state.pos[0] <= x1 and y0 <= state.pos[1] <= y1
    state.pos = (500, y1 + 40)
    body._speed = 100.0
    before = state.pos
    body._integrate(1 / 60, view)
    assert math.dist(before, state.pos) <= 100 / 60 + 1e-6
    assert state.pos[1] > y1


def test_cursor_approach_pressure_and_escape_speed_increase_together():
    from neuropet.core.contracts import CursorKinematics, PetState, Behavior, BehaviorCommand
    from neuropet.core.world import WorldModel
    from neuropet.perception.mouse import closing_pressure
    from neuropet.species.cockroach import AmericanCockroach
    def cursor(vx):
        return CursorKinematics(x=400, y=400, vx=vx, speed=abs(vx))
    pos = (500, 400)
    assert closing_pressure(cursor(-1800), pos) == 0
    assert closing_pressure(cursor(0), pos) == 0
    assert closing_pressure(cursor(1800), pos) > closing_pressure(cursor(500), pos) > 0
    speeds = []
    for vx in (0, 1800):
        world = WorldModel(2000, 1200)
        world.cursor = cursor(vx)
        state = PetState("test", "species.cockroach", "test", pos)
        body = AmericanCockroach().create_body(state)
        body.set_speed_multiplier(1.0)
        body._esc = {"phase": "sprint", "t": 0.0, "dur": 10.0}
        view = world.snapshot("test", 0.0)
        for _ in range(90):
            body._apply_escape(BehaviorCommand(Behavior.ESCAPE), view, 1 / 60)
        speeds.append(body._speed)
    assert speeds[1] > speeds[0]


def test_trail_length_cap_and_cleanup_on_real_canvas(real_app):
    from neuropet.render.trail import TrailLayer, MAX_LEN, MAX_SEGMENTS, HISTORY
    trail = TrailLayer(real_app.stage.canvas, tag="verification-trail")
    lengths = []
    for speed in (500, 3600):
        for i in range(10):
            trail.update("test", 400 + speed * i / 60, 300, speed, 60, 1 / 60, i / 60)
        lengths.append(sum(math.dist(real_app.stage.canvas.coords(item)[:2],
                                    real_app.stage.canvas.coords(item)[2:])
                           for item in trail._items["test"]
                           if real_app.stage.canvas.itemcget(item, "state") != "hidden"))
        assert len(trail._items["test"]) <= MAX_SEGMENTS
        assert len(trail._hist["test"]) <= HISTORY
        trail.remove("test")
    assert lengths[0] < lengths[1] <= MAX_LEN + 1e-6
    assert not trail._items and not trail._hist
