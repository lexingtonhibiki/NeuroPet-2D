"""Headless regression: hidden archives must survive a full visible roster.

Run with: python tests/test_hidden_restart_capacity.py
Uses the real App lifecycle, species, bodies, brains and JSON persistence. Only
window drawing and work-area discovery are replaced; no desktop is opened.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from neuropet.core import app as appmod, config
from neuropet.core.bus import EventBus
from neuropet.core.plugin import PluginRegistry
from neuropet.core.world import WorldModel


class HeadlessStage:
    def __init__(self):
        self.items = set()

    def ensure_pet_item(self, pet_id):
        self.items.add(pet_id)

    def remove_pet(self, pet_id):
        self.items.discard(pet_id)


def make_app(data_dir):
    app = appmod.App.__new__(appmod.App)
    app.cfg = config.AppConfig()
    app.bus = EventBus()
    app.registry = PluginRegistry()
    app._register_builtin_plugins()
    app._species_insts = {}
    app.world = WorldModel(1920, 1080)
    app.stage = HeadlessStage()
    app.pets = {}
    app._hidden = {}
    app._pending_hidden_records = []
    app._fx = {}
    app._speed_mult = {}
    app._crawl_speed = app.cfg.crawl_speed
    app._panel = None
    app._roster_live = False
    app._pets_path = data_dir / "pets.json"
    app._apply_memory_guard = lambda: None
    return app


class HiddenRestartCapacityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.data = Path(temp.name)
        for target, value in (
            ("neuropet.core.config.PROFILES_DIR", self.data / "profiles"),
            ("neuropet.core.app._SESSION_FILE", self.data / "session.json"),
        ):
            self.enterContext(patch(target, value))
        self.enterContext(patch.object(appmod.App, "_startup_scale_override", return_value=None))
        self.enterContext(patch("neuropet.core.app.time.time", return_value=1234567890.0))
        self.enterContext(patch("neuropet.core.desktop.monitor_work_area",
                                side_effect=lambda screen: (0, 0, *screen)))

    def seed_session(self, visible_count=10):
        app = make_app(self.data)
        app._roster_live = True
        hidden_ids = []
        for species in ("species.cockroach", "species.fruitfly"):
            pid = app.add_pet(species, pet_id="hidden-" + species)
            handle = app.pets[pid]
            handle.state.age_s = 321.5
            handle.brain.on_event("fed", {"food_kind": "crumb"})
            handle.brain.on_event("story", {"about": "cold", "valence": 0.8,
                                           "text": "remember this after restart"})
            app.set_pet_scale(pid, 1.5)
            app._consume_food_effects(handle, "purple")
            app._consume_food_effects(handle, "blue")
            app.hide_pet(pid)
            hidden_ids.append(pid)
        visible_ids = [app.add_pet("species.cockroach", pet_id=f"visible-{i}")
                       for i in range(visible_count)]
        app.save_all()
        expected = {pid: app._hidden[pid].brain.save() for pid in hidden_ids}
        return visible_ids, hidden_ids, expected

    def test_hidden_pets_survive_restart_with_ten_visible_pets(self):
        visible_ids, hidden_ids, expected = self.seed_session()
        profile_bytes = {p.relative_to(self.data): p.read_bytes()
                         for p in (self.data / "profiles").rglob("*.json")}
        app = make_app(self.data)
        # This is the production run() ordering, not a test-specific shortcut.
        app._load_startup_roster()
        app._restore_hidden()
        self.assertEqual(list(app.pets), visible_ids)
        self.assertEqual(set(app._hidden), set(hidden_ids))
        self.assertEqual(set(app.world.pets), set(visible_ids))
        self.assertEqual(app.stage.items, set(visible_ids))
        self.assertEqual(app.cfg.max_pets, 10)
        saved = json.loads((self.data / "session.json").read_text("utf-8"))
        self.assertEqual({rec["pet_id"] for rec in saved["hidden"]}, set(hidden_ids))
        for pid in hidden_ids:
            self.assertEqual(app._hidden[pid].brain.save(), expected[pid])
            self.assertEqual(app._hidden[pid].scale, 1.5)
            self.assertTrue(app._hidden[pid].chibi)
            self.assertEqual(app._speed_mult[pid], 1.3)
        for path, data in profile_bytes.items():
            self.assertEqual((self.data / path).read_bytes(), data)
        # Restoring archives must not weaken the visible capacity rule.
        with self.assertRaises(RuntimeError):
            app.add_pet("species.fruitfly")
        self.assertFalse(app.recall_pet(hidden_ids[0]))
        app.remove_pet(visible_ids[0])
        self.assertTrue(app.recall_pet(hidden_ids[0]))
        self.assertEqual(len(app.pets), 10)
        self.assertEqual(app.pets[hidden_ids[0]].brain.save(), expected[hidden_ids[0]])
        self.assertIn(hidden_ids[0], app.stage.items)
        self.assertIn(hidden_ids[0], app.world.pets)

    def assert_restart_below_capacity(self, visible_count):
        visible_ids, hidden_ids, expected = self.seed_session(visible_count)
        app = make_app(self.data)
        app._load_startup_roster()
        app._restore_hidden()
        self.assertEqual(list(app.pets), visible_ids)
        self.assertEqual(set(app._hidden), set(hidden_ids))
        for pid in hidden_ids:
            self.assertEqual(app._hidden[pid].brain.save(), expected[pid])
        self.assertTrue(app.recall_pet(hidden_ids[0]))
        self.assertEqual(len(app.pets), visible_count + 1)

    def test_hidden_restart_on_empty_desktop(self):
        self.assert_restart_below_capacity(0)

    def test_hidden_restart_with_one_visible_slot_available(self):
        self.assert_restart_below_capacity(9)

    def test_repeated_restarts_preserve_rosters_and_profile_payloads(self):
        visible_ids, hidden_ids, expected = self.seed_session()
        for _ in range(3):
            app = make_app(self.data)
            app._load_startup_roster()
            app._restore_hidden()
            app._roster_live = True
            app.save_all()
            self.assertEqual(list(app.pets), visible_ids)
            self.assertEqual(set(app._hidden), set(hidden_ids))
            for pid in hidden_ids:
                self.assertEqual(app._hidden[pid].brain.save(), expected[pid])
            saved = json.loads((self.data / "session.json").read_text("utf-8"))
            self.assertEqual(len(saved["hidden"]), len(hidden_ids))

    def test_failed_restore_is_retained_for_later_restart(self):
        # Keep one visible slot free to isolate failure-safe persistence from
        # the separate full-capacity regression.
        visible_ids, hidden_ids, expected = self.seed_session(9)
        failed_id = hidden_ids[1]
        before = {p: p.read_bytes() for p in
                  (self.data / "profiles" / failed_id).glob("*.json")}
        app = make_app(self.data)
        app._load_startup_roster()
        original_species = app.species

        def unavailable_species(species_id):
            if species_id == "species.fruitfly":
                raise RuntimeError("temporary species construction failure")
            return original_species(species_id)

        with patch.object(app, "species", side_effect=unavailable_species):
            app._restore_hidden()
        self.assertEqual(set(app._hidden), {hidden_ids[0]})
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)
        # A later ordinary lifecycle write and shutdown save must retain the
        # unresolved record, not just the final write inside restoration.
        app.hide_pet(visible_ids[0])
        app._roster_live = True
        app.save_all()
        saved = json.loads((self.data / "session.json").read_text("utf-8"))
        self.assertEqual({r["pet_id"] for r in saved["hidden"]},
                         set(hidden_ids + [visible_ids[0]]))
        restarted = make_app(self.data)
        restarted._load_startup_roster()
        restarted._restore_hidden()
        self.assertIn(failed_id, restarted._hidden)
        self.assertEqual(restarted._hidden[failed_id].brain.save(), expected[failed_id])
        self.assertEqual(restarted._pending_hidden_records, [])

    def test_malformed_records_do_not_crash_or_create_replacement_pets(self):
        visible_ids, hidden_ids, _ = self.seed_session(9)
        session = self.data / "session.json"
        saved = json.loads(session.read_text("utf-8"))
        malformed = [{"species_id": "species.cockroach"},
                     {"pet_id": "", "species_id": "species.fruitfly"},
                     {"pet_id": "bad-species", "species_id": None},
                     {"pet_id": 42, "species_id": "species.cockroach"}]
        saved["hidden"].extend(malformed + [None, "not a record"])
        session.write_text(json.dumps(saved), "utf-8")
        profile_dirs = set((self.data / "profiles").iterdir())
        app = make_app(self.data)
        app._load_startup_roster()
        app._restore_hidden()
        app.save_all()
        self.assertEqual(list(app.pets), visible_ids)
        self.assertEqual(set(app._hidden), set(hidden_ids))
        self.assertEqual(set((self.data / "profiles").iterdir()), profile_dirs)
        self.assertEqual(app._pending_hidden_records, malformed)
        rewritten = json.loads(session.read_text("utf-8"))["hidden"]
        self.assertEqual(rewritten[-len(malformed):], malformed)

    def test_restore_does_not_write_an_incomplete_intermediate_session(self):
        _, hidden_ids, _ = self.seed_session()
        app = make_app(self.data)
        app._load_startup_roster()
        with patch.object(appmod, "_SESSION_FILE", wraps=self.data / "session.json") as session:
            app._restore_hidden()
            self.assertEqual(session.write_text.call_count, 1)
            written = json.loads(session.write_text.call_args.args[0])
        self.assertEqual({r["pet_id"] for r in written["hidden"]}, set(hidden_ids))

    def test_hidden_restore_never_borrows_stage_world_or_profile_write_paths(self):
        visible_ids, hidden_ids, _ = self.seed_session()
        app = make_app(self.data)
        app._load_startup_roster()
        for obj, name in ((app.stage, "ensure_pet_item"),
                          (app.stage, "remove_pet"),
                          (app.world, "upsert_pet"),
                          (app.world, "remove_pet"),
                          (app, "_save_profile")):
            self.enterContext(patch.object(obj, name, side_effect=AssertionError(name)))
        app._restore_hidden()
        self.assertEqual(list(app.pets), visible_ids)
        self.assertEqual(set(app._hidden), set(hidden_ids))
        self.assertEqual(app.stage.items, set(visible_ids))
        self.assertEqual(set(app.world.pets), set(visible_ids))

    def test_late_profile_failure_leaves_no_partial_handle_or_effect_state(self):
        visible_ids, hidden_ids, expected = self.seed_session()
        failed_id = hidden_ids[0]
        profile_bytes = {p: p.read_bytes() for p in
                         (self.data / "profiles" / failed_id).glob("*.json")}
        app = make_app(self.data)
        app._load_startup_roster()
        original_load = app._load_profile

        def load_then_fail(handle):
            original_load(handle)
            if handle.pet_id == failed_id:
                raise RuntimeError("late profile loading failure")

        with patch.object(app, "_load_profile", side_effect=load_then_fail):
            app._restore_hidden()
        self.assertEqual(list(app.pets), visible_ids)
        self.assertEqual(set(app._hidden), {hidden_ids[1]})
        self.assertEqual(set(app.world.pets), set(visible_ids))
        self.assertEqual(app.stage.items, set(visible_ids))
        self.assertNotIn(failed_id, app._fx)
        self.assertNotIn(failed_id, app._speed_mult)
        for path, data in profile_bytes.items():
            self.assertEqual(path.read_bytes(), data)
        app.save_all()
        saved = json.loads((self.data / "session.json").read_text("utf-8"))["hidden"]
        self.assertEqual(sorted(r["pet_id"] for r in saved), sorted(hidden_ids))
        restarted = make_app(self.data)
        restarted._load_startup_roster()
        restarted._restore_hidden()
        self.assertEqual(set(restarted._hidden), set(hidden_ids))
        self.assertEqual(restarted._hidden[failed_id].brain.save(), expected[failed_id])

    def test_duplicate_hidden_records_do_not_create_new_ids_or_profile_directories(self):
        visible_ids, hidden_ids, expected = self.seed_session()
        session = self.data / "session.json"
        records = json.loads(session.read_text("utf-8"))["hidden"]
        records.extend([dict(records[0]), dict(records[0])])
        session.write_text(json.dumps({"hidden": records}), "utf-8")
        dirs = set((self.data / "profiles").iterdir())
        for _ in range(2):
            app = make_app(self.data)
            app._load_startup_roster()
            app._restore_hidden()
            self.assertEqual(list(app.pets), visible_ids)
            self.assertEqual(set(app._hidden), set(hidden_ids))
            self.assertEqual(app._pending_hidden_records, [])
            self.assertEqual(set((self.data / "profiles").iterdir()), dirs)
            self.assertEqual(app._hidden[hidden_ids[0]].brain.save(), expected[hidden_ids[0]])
            saved = json.loads(session.read_text("utf-8"))["hidden"]
            self.assertEqual(len(saved), len(hidden_ids))

    def test_visible_and_conflicting_hidden_ids_remain_unresolved_without_renaming(self):
        visible_ids, hidden_ids, _ = self.seed_session()
        session = self.data / "session.json"
        records = json.loads(session.read_text("utf-8"))["hidden"]
        conflicts = [{"pet_id": visible_ids[0], "species_id": "species.cockroach"},
                     {"pet_id": hidden_ids[0], "species_id": "species.fruitfly"}]
        records.extend(conflicts)
        session.write_text(json.dumps({"hidden": records}), "utf-8")
        dirs = set((self.data / "profiles").iterdir())
        for _ in range(2):
            app = make_app(self.data)
            app._load_startup_roster()
            app._restore_hidden()
            self.assertEqual(list(app.pets), visible_ids)
            self.assertEqual(set(app._hidden), set(hidden_ids))
            self.assertEqual(app._pending_hidden_records, conflicts)
            self.assertEqual(set((self.data / "profiles").iterdir()), dirs)


if __name__ == "__main__":
    unittest.main()
