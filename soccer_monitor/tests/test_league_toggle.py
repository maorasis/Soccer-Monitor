"""
Regression test: toggling a league on in the Telegram bot used to do nothing until
the next day. The daily fixture cache was filtered by the leagues active at *fetch*
time, and fetch runs once per day, so a league enabled afterwards had no fixtures
cached — no team ever matched as live and no market was ever monitored. Toggling a
league off worked immediately, which made the asymmetry look random.

The cache now stores every league's fixtures (the API returns them all in the same
response regardless) and filters on read, so a toggle takes effect on the next scan.

Verifies a toggle is visible immediately, in both directions, without a re-fetch.
Run: python tests/test_league_toggle.py
"""
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from soccer_monitor import LiveMatchTracker, MarketDiscovery

logging.basicConfig(level=logging.WARNING)

PREMIER_LEAGUE = 39
SERIE_A = 135


class ToggleableConfig:
    """Stands in for RuntimeConfig; the Telegram bot mutates it in place."""

    def __init__(self, active):
        self.active = set(active)

    def get_active_api_ids(self):
        return self.active


def build(active):
    config = ToggleableConfig(active)
    tracker = LiveMatchTracker(session=None, runtime_config=config)

    kickoff = datetime.now(timezone.utc) - timedelta(minutes=10)  # in play
    tracker.daily_fixtures = [
        {
            "fixture_id": 1,
            "league_id": PREMIER_LEAGUE,
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "start_time": kickoff,
        },
        {
            "fixture_id": 2,
            "league_id": SERIE_A,
            "home_team": "Inter",
            "away_team": "Milan",
            "start_time": kickoff,
        },
    ]
    tracker.last_fetch_date = "2026-08-30"  # already fetched today; no re-fetch allowed
    return config, tracker


def test_toggle_on_is_visible_without_refetch():
    config, tracker = build({PREMIER_LEAGUE})
    assert tracker.get_live_team_names() == {"Arsenal", "Chelsea"}

    config.active.add(SERIE_A)  # the user taps Serie A in /menu

    assert tracker.get_live_team_names() == {"Arsenal", "Chelsea", "Inter", "Milan"}
    assert tracker.last_fetch_date == "2026-08-30", "must not need a new API call"


def test_toggle_off_is_visible_without_refetch():
    config, tracker = build({PREMIER_LEAGUE, SERIE_A})
    assert tracker.get_live_team_names() == {"Arsenal", "Chelsea", "Inter", "Milan"}

    config.active.discard(PREMIER_LEAGUE)

    assert tracker.get_live_team_names() == {"Inter", "Milan"}


def test_fixture_matching_follows_the_same_filter():
    """A disabled league's fixture must not resolve to a fixture_id either."""
    config, tracker = build({PREMIER_LEAGUE})
    discovery = MarketDiscovery(session=None, live_match_tracker=tracker,
                                runtime_config=config)

    assert discovery.find_matching_fixture_id("Arsenal vs. Chelsea", {}) == 1
    assert discovery.find_matching_fixture_id("Inter vs. Milan", {}) is None

    config.active.add(SERIE_A)

    assert discovery.find_matching_fixture_id("Inter vs. Milan", {}) == 2


def test_no_active_leagues_matches_nothing():
    config, tracker = build(set())
    assert tracker.get_live_team_names() == set()
    assert tracker.active_fixtures() == []


if __name__ == "__main__":
    test_toggle_on_is_visible_without_refetch()
    test_toggle_off_is_visible_without_refetch()
    test_fixture_matching_follows_the_same_filter()
    test_no_active_leagues_matches_nothing()
    print("All league-toggle tests passed.")
