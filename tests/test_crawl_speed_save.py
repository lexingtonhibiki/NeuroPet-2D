"""Crawling-speed settings stay consistent when persistence fails.

Run directly with Python; no GUI is opened and all saves are temporary.
Uses real App setters, species bodies and atomic config persistence.
"""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from neuropet.core import app as appmod, config
from test_hidden_restart_capacity import make_app


class CrawlSpeedSaveTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        for name, value in (("DATA_DIR", self.data),
                            ("CONFIG_PATH", self.data / "config.json"),
                            ("PROFILES_DIR", self.data / "profiles")):
            self.enterContext(patch.object(config, name, value))
        self.enterContext(patch.object(appmod, "_SESSION_FILE", self.data / "session.json"))
        self.enterContext(patch.object(appmod.App, "_startup_scale_override", return_value=None))
        self.enterContext(patch("neuropet.core.desktop.monitor_work_area",
                                side_effect=lambda screen: (0, 0, *screen)))
        self.app = make_app(self.data)
        self.visible = self.app.add_pet("species.cockroach", pet_id="visible")
        self.hidden = self.app.add_pet("species.fruitfly", pet_id="hidden")
        self.app.hide_pet(self.hidden)
        self.app._speed_mult[self.hidden] = 1.3
        self.app._sync_speed_multipliers()
        self.cfg = self.app.cfg
        config.save_config(self.cfg)
        self.saved = config.CONFIG_PATH.read_bytes()
        self.events = []
        self.app.bus.subscribe("user/crawl_speed", lambda topic, data: self.events.append(data))

    def assert_speed(self, value):
        self.assertIs(self.app.cfg, self.cfg)
        self.assertEqual(self.app.crawl_speed(), value)
        self.assertEqual(self.cfg.crawl_speed, value)
        self.assertAlmostEqual(self.app.pets[self.visible].body.speed_multiplier, value)
        self.assertAlmostEqual(self.app._hidden[self.hidden].body.speed_multiplier, value * 1.3)

    def test_replace_failure_keeps_runtime_and_saved_speed_then_retry_succeeds(self):
        bodies = (self.app.pets[self.visible].body, self.app._hidden[self.hidden].body)
        with patch.object(config.os, "replace", side_effect=PermissionError("injected replace failure")):
            with self.assertRaises(PermissionError):
                self.app.set_crawl_speed(6.0)
        self.assert_speed(2.0)
        self.assertEqual(config.CONFIG_PATH.read_bytes(), self.saved)
        self.assertEqual(self.events, [])
        self.assertEqual(self.app.set_crawl_speed(6.0), 6.0)
        self.assert_speed(6.0)
        self.assertEqual(config.load_config().crawl_speed, 6.0)
        self.assertEqual(self.events, [{"mult": 6.0}])
        self.assertIs(self.app.pets[self.visible].body, bodies[0])
        self.assertIs(self.app._hidden[self.hidden].body, bodies[1])

    def test_failed_speed_is_not_persisted_by_an_unrelated_later_save(self):
        with patch("io.open", side_effect=OSError("injected full disk")):
            with self.assertRaises(OSError):
                self.app.set_crawl_speed(8.0)
        self.cfg.panel_visible = not self.cfg.panel_visible
        config.save_config(self.cfg)
        self.assertEqual(config.load_config().crawl_speed, 2.0)
        self.assert_speed(2.0)
        self.assertEqual(self.events, [])

    def test_success_uses_snapped_value_without_changing_other_settings(self):
        self.cfg.panel_visible = False
        self.assertEqual(self.app.set_crawl_speed(5.9), 6.0)
        self.assert_speed(6.0)
        self.assertFalse(self.cfg.panel_visible)
        self.assertFalse(config.load_config().panel_visible)
        self.assertEqual(self.events, [{"mult": 6.0}])


if __name__ == "__main__":
    unittest.main()
