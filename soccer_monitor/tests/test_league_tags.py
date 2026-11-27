"""
Regression test: per-league thresholds silently fell back to the global one.

A market's league was "the first tag that is not Soccer/Sports/Featured/Games". Gamma
does not guarantee tag order, so that could land on a tag which is not a league at all;
match_league_name then returned "" and get_threshold fell through to the global value.
Nothing logged, and the whole per-league threshold feature in the Telegram bot quietly
did nothing for that market.

The league is now resolved by matching the supported-league table rather than by tag
position, and the canonical name is stored so get_threshold matches exactly.

Run: python tests/test_league_tags.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config_manager import RuntimeConfig
from soccer_monitor import MarketDiscovery


class Config:
    """Borrows the real matcher without touching config_state.json on disk."""
    match_league_name = RuntimeConfig.match_league_name

    def __init__(self):
        self.global_threshold = 0.10
        self.league_thresholds = {"Premier League": 0.04}

    get_threshold = RuntimeConfig.get_threshold


def build():
    return MarketDiscovery(session=None, live_match_tracker=None,
                           runtime_config=Config())


def test_league_found_regardless_of_tag_position():
    discovery = build()
    first = ["Premier League", "Soccer", "Sports"]
    buried = ["Soccer", "Sports", "Featured", "Games", "Premier League"]

    assert discovery._league_for_tags(first) == "Premier League"
    assert discovery._league_for_tags(buried) == "Premier League", (
        "tag order must not decide whether the league resolves")


def test_a_non_league_tag_in_front_no_longer_wins():
    """The exact failure: a stray tag ahead of the league tag."""
    discovery = build()
    tags = ["Weekly", "Soccer", "Premier League"]

    assert discovery._league_for_tags(tags) == "Premier League"
    assert discovery.runtime_config.get_threshold(
        discovery._league_for_tags(tags)) == 0.04, (
        "the per-league threshold must be used, not the global one")


def test_an_alias_tag_resolves_to_the_canonical_name():
    discovery = build()
    assert discovery._league_for_tags(["Soccer", "EPL"]) == "Premier League"
    assert discovery._league_for_tags(["Soccer", "ucl"]) == "Champions League"
    assert discovery._league_for_tags(["Soccer", "sea"]) == "Serie A"
    assert discovery._league_for_tags(["Soccer", "uel"]) == "Europa League"
    assert discovery._league_for_tags(["Soccer", "uecl"]) == "Conference League"


def test_unrecognised_tags_keep_a_readable_name_and_the_global_threshold():
    discovery = build()
    league = discovery._league_for_tags(["Soccer", "Sports", "Kickabout League"])

    assert league == "Kickabout League", "the alert text still needs a name"
    assert discovery.runtime_config.get_threshold(league) == 0.10


def test_no_tags_at_all():
    discovery = build()
    assert discovery._league_for_tags([]) == "General Soccer"
    assert discovery._league_for_tags(["Soccer", "Sports"]) == "General Soccer"


if __name__ == "__main__":
    test_league_found_regardless_of_tag_position()
    test_a_non_league_tag_in_front_no_longer_wins()
    test_an_alias_tag_resolves_to_the_canonical_name()
    test_unrecognised_tags_keep_a_readable_name_and_the_global_threshold()
    test_no_tags_at_all()
    print("OK: the league resolves by matching the table, not by tag order")
