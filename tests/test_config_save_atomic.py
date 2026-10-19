"""Settings saves must not destroy the last good configuration on write failure.

Run with ``python tests/test_config_save_atomic.py``; stdlib only, no GUI, and
all files live in a temporary directory (never the user's saves).
"""
from __future__ import annotations

import errno
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from neuropet.core import config


class PartialWrite:
    """Exercise a real partial write before reporting a full disk."""

    def __init__(self, stream):
        self.stream = stream

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.stream.__exit__(*args)

    def write(self, text):
        self.stream.write(text[:12])
        self.stream.flush()
        raise OSError(errno.ENOSPC, "injected full disk after partial write")


class ConfigSaveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.path = self.directory / "config.json"
        for name, value in (("DATA_DIR", self.directory), ("CONFIG_PATH", self.path)):
            patcher = patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.previous = config.AppConfig(language="en", panel_visible=False,
                                         crawl_speed=6.0, trail_enabled=False)
        config.save_config(self.previous)
        self.original = self.path.read_bytes()

    def assert_previous_survives(self):
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(config.load_config(), self.previous)
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_partial_write_preserves_settings_on_restart(self):
        real_open = io.open

        def failing_open(file, mode="r", *args, **kwargs):
            stream = real_open(file, mode, *args, **kwargs)
            return PartialWrite(stream) if "w" in mode else stream

        updated = config.AppConfig(language="zh-CN", crawl_speed=8.0)
        with patch("io.open", side_effect=failing_open):
            with self.assertRaises(OSError):
                config.save_config(updated)
        self.assert_previous_survives()

    def test_successful_save_replaces_settings_and_leaves_no_temp_file(self):
        updated = config.AppConfig(language="zh-CN", crawl_speed=8.0,
                                   panel_visible=True, max_pets=12)
        config.save_config(updated)
        self.assertEqual(config.load_config(), updated)
        self.assertEqual(json.loads(self.path.read_text("utf-8"))["app"]["language"], "zh-CN")
        self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_first_save_creates_data_directory(self):
        directory = self.directory / "new-data"
        path = directory / "config.json"
        with patch.object(config, "DATA_DIR", directory), \
                patch.object(config, "CONFIG_PATH", path):
            config.save_config(self.previous)
            self.assertEqual(config.load_config(), self.previous)
        self.assertEqual(list(directory.iterdir()), [path])

    def test_serialization_failure_preserves_previous_settings(self):
        invalid = config.AppConfig(language=object())
        with self.assertRaises(TypeError):
            config.save_config(invalid)
        self.assert_previous_survives()

    def test_temp_creation_failure_preserves_previous_settings(self):
        with patch("io.open", side_effect=PermissionError("injected open failure")):
            with self.assertRaises(PermissionError):
                config.save_config(self.previous)
        self.assert_previous_survives()

    def test_replace_failure_preserves_previous_settings_and_unrelated_file(self):
        unrelated = self.directory / ".config-unrelated.tmp"
        unrelated.write_text("not ours", encoding="utf-8")
        with patch.object(config.os, "replace", side_effect=PermissionError("injected replace failure")):
            with self.assertRaises(PermissionError):
                config.save_config(config.AppConfig(language="zh-CN"))
        self.assertEqual(unrelated.read_text("utf-8"), "not ours")
        unrelated.unlink()
        self.assert_previous_survives()

    def test_close_failure_preserves_previous_settings(self):
        real_open = io.open

        class FailedClose(PartialWrite):
            def write(self, text):
                return self.stream.write(text)

            def close(self):
                if not self.stream.closed:
                    self.stream.close()
                    raise OSError(errno.ENOSPC, "injected close failure")

            def __exit__(self, *args):
                self.close()

        def failing_open(file, mode="r", *args, **kwargs):
            stream = real_open(file, mode, *args, **kwargs)
            return FailedClose(stream) if "w" in mode else stream

        with patch("io.open", side_effect=failing_open):
            with self.assertRaises(OSError):
                config.save_config(self.previous)
        self.assert_previous_survives()

    def test_stream_is_closed_before_replace(self):
        real_open, real_replace = io.open, config.os.replace
        streams = []

        def recording_open(file, mode="r", *args, **kwargs):
            stream = real_open(file, mode, *args, **kwargs)
            if "w" in mode:
                streams.append(stream)
            return stream

        def checked_replace(source, destination):
            self.assertTrue(streams)
            self.assertTrue(all(stream.closed for stream in streams))
            self.assertEqual(Path(source).parent, self.path.parent)
            return real_replace(source, destination)

        with patch("io.open", side_effect=recording_open), \
                patch.object(config.os, "replace", side_effect=checked_replace):
            config.save_config(self.previous)
        self.assert_previous_survives()

    def test_unicode_serialization_is_unchanged(self):
        # AppConfig historically permits strings in fields without validation.
        # Keep the UTF-8 JSON format; do not add a schema migration in this fix.
        cfg = config.AppConfig(language="en", feeding_timeout_s="中文")
        config.save_config(cfg)
        self.assertIn("中文", self.path.read_text("utf-8"))
        self.assertEqual(config.load_config(), cfg)

    def test_unknown_keys_keep_existing_load_and_save_semantics(self):
        # Unknown app fields historically cause fallback; unknown top-level
        # fields are ignored. Atomic saving must not change either behavior.
        self.path.write_text('{"app": {"future_field": 1}}', encoding="utf-8")
        loaded = config.load_config()
        self.assertEqual(loaded.crawl_speed, config.AppConfig().crawl_speed)
        self.path.write_text('{"app": {"language": "en", "crawl_speed": 6}, '
                             '"future_section": {"value": 1}}', encoding="utf-8")
        loaded = config.load_config()
        self.assertEqual(loaded.crawl_speed, 6)
        config.save_config(loaded)
        self.assertEqual(set(json.loads(self.path.read_text("utf-8"))), {"app"})

    def test_legacy_config_still_loads_without_rewriting(self):
        legacy = '{"app": {"max_pets": 3, "language": "en", "panel_visible": false}}'
        self.path.write_text(legacy, encoding="utf-8")
        loaded = config.load_config()
        self.assertEqual(loaded.max_pets, 10)
        self.assertEqual(loaded.language, "en")
        self.assertFalse(loaded.panel_visible)
        self.assertEqual(self.path.read_text("utf-8"), legacy)


if __name__ == "__main__":
    unittest.main(verbosity=2)
