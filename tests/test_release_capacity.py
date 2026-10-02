"""Release capacity, legacy configuration, and real roster persistence."""
import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def release_data(tmp_path, monkeypatch):
    from neuropet.core import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config, "PETS_PATH", tmp_path / "pets.json")
    monkeypatch.setattr(config, "PROFILES_DIR", tmp_path / "profiles")
    import neuropet.core.app as appmod
    monkeypatch.setattr(appmod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(appmod, "_PETS_FILE", tmp_path / "pets.json")
    monkeypatch.setattr(appmod, "_SESSION_FILE", tmp_path / "session.json")
    return tmp_path


def test_old_three_pet_limit_is_migrated(release_data):
    from neuropet.core.config import load_config
    path = release_data / "config.json"
    path.write_text(json.dumps({"app": {"max_pets": 3, "fps": 30}}), encoding="utf-8")
    settings = load_config()
    assert settings.max_pets >= 10
    assert settings.fps == 30
    assert json.loads(path.read_text())["app"]["max_pets"] == 3  # load never destroys the original


def test_ten_pets_and_restart_keep_identical_roster(release_data):
    from neuropet.core.app import App
    app = App()
    try:
        app._roster_live = True
        ids = [app.add_pet("species.cockroach" if i % 2 == 0 else "species.fruitfly") for i in range(10)]
        assert len(app.pets) == 10
        assert len({tuple(h.state.pos) for h in app.pets.values()}) >= 8
        app.pets[ids[0]].brain.on_event("fed", {"food_kind": "crumb"})
        memory_before = app.pets[ids[0]].brain.save()
        with pytest.raises(RuntimeError, match="10"):
            app.add_pet("species.cockroach")
        app.shutdown()
        restored = App()
        try:
            restored._load_startup_roster()
            assert list(restored.pets) == ids
            assert restored.pets[ids[0]].brain.save() == memory_before
            assert len(list((release_data / "profiles").iterdir())) == 10
        finally:
            restored.shutdown()
    finally:
        if app._running:
            app.shutdown()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
