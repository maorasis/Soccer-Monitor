"""
Regression test: config_state.json was written in place with a plain open(..., 'w'),
so a crash between truncation and the final byte left a half-written file. load() then
failed to parse it, logged, and silently fell back to the default league list -- so a
bad moment quietly changed which markets the daemon watches.

The save is now written to a temporary file and renamed over the target, which is
atomic, so an interrupted save leaves the previous config intact.

Run: python tests/test_config_atomic_save.py
"""
import json
import logging
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config_manager import RuntimeConfig

logging.basicConfig(level=logging.CRITICAL)


def load_config(path):
    cfg = RuntimeConfig.__new__(RuntimeConfig)
    cfg.filepath = path
    cfg.global_threshold = 0.10
    cfg.active_leagues = ["Premier League"]
    cfg.league_thresholds = {}
    return cfg


def test_a_failed_save_leaves_the_previous_file_intact():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "config_state.json")

        cfg = load_config(path)
        cfg.active_leagues = ["Premier League", "La Liga"]
        cfg.save()

        good = open(path).read()
        assert json.loads(good)["active_leagues"] == ["Premier League", "La Liga"]

        # A value json.dump cannot serialise: it raises partway through writing.
        cfg.league_thresholds = {"Premier League": {1, 2, 3}}
        cfg.save()  # must swallow the error, having changed nothing

        assert open(path).read() == good, "the previous config must survive a failed save"
        assert json.loads(open(path).read())["active_leagues"] == [
            "Premier League", "La Liga"]


def test_no_temporary_files_are_left_behind():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "config_state.json")
        cfg = load_config(path)

        cfg.save()
        cfg.league_thresholds = {"Premier League": {1, 2}}
        cfg.save()  # fails

        leftovers = [n for n in os.listdir(tmp) if n != "config_state.json"]
        assert leftovers == [], f"left temp files behind: {leftovers}"


def test_a_normal_save_round_trips():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "config_state.json")
        cfg = load_config(path)
        cfg.global_threshold = 0.07
        cfg.league_thresholds = {"Serie A": 0.05}
        cfg.save()

        data = json.load(open(path))
        assert data["global_threshold"] == 0.07
        assert data["league_thresholds"] == {"Serie A": 0.05}


if __name__ == "__main__":
    test_a_failed_save_leaves_the_previous_file_intact()
    test_no_temporary_files_are_left_behind()
    test_a_normal_save_round_trips()
    print("OK: an interrupted save cannot corrupt config_state.json")
