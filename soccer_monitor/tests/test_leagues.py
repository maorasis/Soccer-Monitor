import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from config_manager import DEFAULT_ACTIVE_LEAGUES, SUPPORTED_LEAGUES, RuntimeConfig
from live_under_monitor import fetch_live_markets
from tests.test_live_under_monitor import NOW, MarketAPI, event


class LeagueMatchingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.config = RuntimeConfig(Path(self.tmp.name) / "config.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_portuguese_and_dutch_leagues_are_supported_and_enabled_by_default(self):
        for league in ("Primeira Liga", "Eredivisie"):
            self.assertIn(league, SUPPORTED_LEAGUES)
            self.assertIn(league, DEFAULT_ACTIVE_LEAGUES)

    def test_tag_labels_match_exactly_and_case_insensitively(self):
        expected = {
            "Primeira Liga": "Primeira Liga", "Liga Portugal": "Primeira Liga", "POR": "Primeira Liga",
            "Eredivisie": "Eredivisie", "ere": "Eredivisie",
            "Portugal": "", "Netherlands": "", "Soccer": "",
        }
        for label, league in expected.items():
            with self.subTest(label=label):
                self.assertEqual(self.config.match_league_name(label), league)

    def test_live_markets_in_the_new_leagues_are_found(self):
        api = MarketAPI()
        api.events = [
            event(5.5, "porto", tags=[{"label": "Soccer"}, {"label": "Primeira Liga"}]),
            event(6.5, "psv", tags=[{"label": "Soccer"}, {"label": "Eredivisie"}]),
        ]
        markets = asyncio.run(fetch_live_markets(api, self.config, NOW))
        self.assertEqual(markets["porto"].league, "Primeira Liga")
        self.assertEqual(markets["psv"].league, "Eredivisie")

        self.config.toggle_league("Eredivisie")
        self.assertNotIn("psv", asyncio.run(fetch_live_markets(api, self.config, NOW)))


if __name__ == "__main__":
    unittest.main()
