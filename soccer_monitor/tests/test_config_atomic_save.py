"""Regression test: an interrupted config save must not corrupt config_state.json.

config_state.json was once written in place, so a crash between truncation and the
final byte left a half-written file. load() then failed to parse it and silently fell
back to the default league list. The save now writes a temporary file and renames it
over the target, which is atomic.
"""
import json
import logging
import os
import tempfile
import unittest
from pathlib import Path

from config_manager import RuntimeConfig


class AtomicSaveTests(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "config_state.json")
        self.config = RuntimeConfig(self.path)

    def tearDown(self):
        logging.disable(logging.NOTSET)
        self.tmp.cleanup()

    def test_a_failed_save_leaves_the_previous_file_intact(self):
        self.config.active_leagues = ["Premier League", "La Liga"]
        self.assertTrue(self.config.save())
        good = Path(self.path).read_text()

        # A value json.dump cannot serialise: it raises partway through writing.
        self.config.under_thresholds = {"Premier League": {1, 2, 3}}
        self.assertFalse(self.config.save())

        self.assertEqual(Path(self.path).read_text(), good)
        self.assertEqual(json.loads(good)["active_leagues"], ["Premier League", "La Liga"])

    def test_no_temporary_files_are_left_behind(self):
        self.config.under_thresholds = {"Premier League": {1, 2}}
        self.config.save()  # fails
        self.assertEqual(os.listdir(self.tmp.name), ["config_state.json"])

    def test_a_normal_save_round_trips(self):
        self.config.set_under_threshold("Serie A", 5.5, 0.75)
        self.config.set_daily_report(False)
        reloaded = RuntimeConfig(self.path)
        self.assertEqual(reloaded.get_under_threshold("Serie A", 5.5), 0.75)
        self.assertFalse(reloaded.daily_report_enabled)

    def test_daily_report_setting_rolls_back_when_saving_fails(self):
        self.config.save = lambda: False
        self.assertFalse(self.config.set_daily_report(False))
        self.assertTrue(self.config.daily_report_enabled)

    def test_legacy_websocket_fields_are_dropped(self):
        with open(self.path, "w") as f:
            json.dump({"active_leagues": ["Serie A"], "global_threshold": 0.1,
                       "league_thresholds": {"Serie A": 0.05}}, f)
        config = RuntimeConfig(self.path)
        self.assertEqual(config.active_leagues, ["Serie A"])
        self.assertTrue(config.daily_report_enabled)
        config.save()
        data = json.loads(Path(self.path).read_text())
        self.assertNotIn("global_threshold", data)
        self.assertNotIn("league_thresholds", data)


if __name__ == "__main__":
    unittest.main()
