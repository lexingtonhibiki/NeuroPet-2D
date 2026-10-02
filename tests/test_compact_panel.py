"""Drive actual compact-panel controls against a real, isolated App."""
import sys
from pathlib import Path
import tempfile
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(scope="module")
def real_app():
    # One Tk interpreter per process, as in production. Repeated destruction and
    # creation of Tcl interpreters intermittently fails loading Windows ttk files.
    with tempfile.TemporaryDirectory(prefix="neuropet-panel-test-") as temp, pytest.MonkeyPatch.context() as patch:
        from neuropet.core import config
        data = Path(temp)
        patch.setattr(config, "DATA_DIR", data)
        patch.setattr(config, "CONFIG_PATH", data / "config.json")
        patch.setattr(config, "PROFILES_DIR", data / "profiles")
        import neuropet.core.app as appmod
        patch.setattr(appmod, "_PETS_FILE", data / "pets.json")
        patch.setattr(appmod, "_SESSION_FILE", data / "session.json")
        app = appmod.App()
        try:
            yield app
        finally:
            app.shutdown()


@pytest.fixture
def panel_app(real_app):
    from neuropet.ui.compact_panel import ControlPanel
    app = real_app
    for pid in list(app.pets) + list(app.hidden_pets()):
        app.remove_pet(pid)
    panel = ControlPanel(app)
    app._panel = panel
    panel.set_on_drop_request(app._on_panel_drop)
    app.root.update()
    try:
        yield panel, app
    finally:
        panel.win.destroy()
        app._panel = None
        app.root.update()


def test_add_ten_pets_from_visible_buttons(panel_app):
    panel, app = panel_app
    assert panel.add_roach_btn.winfo_ismapped()
    assert panel.add_fly_btn.winfo_ismapped()
    for _ in range(5):
        panel.add_roach_btn.invoke()
        panel.add_fly_btn.invoke()
    app.root.update()
    assert len(app.pets) == 10
    assert len(panel.tree.get_children()) == 10
    assert panel.win.winfo_height() <= 650
    for button in (panel.feed_now_btn, panel.freeze_btn, panel.hide_btn,
                   panel.memory_btn, panel.settings_btn, panel.pause_all_btn,
                   panel.recall_all_btn):
        assert button.winfo_ismapped()
        assert button.winfo_rooty() + button.winfo_height() <= panel.win.winfo_rooty() + panel.win.winfo_height(), button.cget("text")
    assert "disabled" in panel.add_roach_btn.state()
    selected = panel.selected()
    panel.tick(1)
    assert panel.selected() == selected


def test_feed_pause_hide_recall_without_changing_tabs(panel_app):
    panel, app = panel_app
    panel.add_roach_btn.invoke()
    pid = panel.selected()
    panel.feed_now_btn.invoke()
    assert len(app.world.foods) == 1
    panel.freeze_btn.invoke()
    assert app.pets[pid].state.frozen
    panel.hide_btn.invoke()
    assert pid in app.hidden_pets()
    assert panel.selected() == pid
    assert panel.hide_btn.cget("text") == "召回"
    panel.hide_btn.invoke()
    assert pid in app.pets
    panel.pause_all_btn.invoke()
    assert not app.pets[pid].state.frozen


def test_optional_views_are_lazy_and_hidden_panel_does_no_work(panel_app):
    panel, app = panel_app
    panel.add_fly_btn.invoke()
    assert not panel.extra_windows
    panel.memory_btn.invoke()
    memory = panel.extra_windows["memory"]
    assert memory.winfo_exists()
    memory.destroy()
    app.root.update()
    panel.memory_btn.invoke()
    assert panel.extra_windows["memory"].winfo_exists()
    panel.settings_btn.invoke()
    assert panel.extra_windows["settings"].winfo_exists()
    calls = panel.refresh_count
    panel.hide()
    app.root.update()
    for _ in range(60):
        panel.tick(1/60)
    assert panel.refresh_count == calls
    panel.show()
    app.root.update()
    assert panel.win.state() == "normal"


def test_empty_state_actions_are_disabled(panel_app):
    panel, app = panel_app
    assert "disabled" in panel.feed_now_btn.state()
    assert "disabled" in panel.freeze_btn.state()
    assert "添加" in panel.status_var.get()

@pytest.mark.parametrize("dpi", [120, 144])
def test_controls_fit_at_supported_dpi(panel_app, dpi):
    from neuropet.ui.compact_panel import ControlPanel
    original, app = panel_app
    prior = float(app.root.tk.call("tk", "scaling"))
    original.win.withdraw()
    app.root.tk.call("tk", "scaling", dpi/72)
    panel = ControlPanel(app)
    app._panel = panel
    try:
        app.root.update()
        assert panel.win.winfo_height() <= app.root.winfo_screenheight()-100
        for button in (panel.add_roach_btn, panel.feed_now_btn, panel.memory_btn,
                       panel.settings_btn, panel.pause_all_btn, panel.recall_all_btn):
            assert button.winfo_ismapped()
            assert button.winfo_rooty()+button.winfo_height() <= panel.win.winfo_rooty()+panel.win.winfo_height()
    finally:
        panel.win.destroy()
        app._panel = original
        app.root.tk.call("tk", "scaling", prior)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
