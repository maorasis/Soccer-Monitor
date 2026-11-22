import asyncio
import sqlite3
import sys
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram import Bot
from aiogram.types import Chat, Message

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bot_ui
from config_manager import RuntimeConfig, SUPPORTED_LEAGUES
from live_under_monitor import ApiHealth, fetch_live_markets, is_active, poll_once, prepare_alert_history


NOW = datetime(2026, 11, 20, 17, tzinfo=timezone.utc)


def event(line, token, **changes):
    return {
        "title": "Arsenal vs. Chelsea",
        "slug": "epl-ars-che-2026-11-20-more-markets",
        "tags": [{"label": "Premier League"}],
        "startTime": "2026-11-20T16:00:00Z",
        "live": True,
        "ended": False,
        "markets": [{
            "question": f"Arsenal vs. Chelsea: O/U {line}",
            "sportsMarketType": "totals",
            "acceptingOrders": True,
            "outcomes": '["Over", "Under"]',
            "clobTokenIds": f'["over-{token}", "{token}"]',
        }],
        **changes,
    }


class Response:
    def __init__(self, data):
        self.data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    def raise_for_status(self):
        pass

    async def json(self):
        return self.data


class MarketAPI:
    def __init__(self):
        self.events = [
            event(5.5, "five"), event(6.5, "six"), event(7.5, "seven"),
            event(5.5, "empty"), event(7.5, "eight-asks"),
            event(5.5, "upcoming", live=False), event(5.5, "finished", ended=True),
            event(5.5, "stale", startTime="2026-11-19T16:00:00Z"),
            event(5.5, "disabled", tags=[{"label": "Champions League"}]),
            event(3.5, "other-line"), event(6.5, "first-half"), event(7.5, "closed"),
        ]
        self.events[-2]["markets"][0]["question"] = "Arsenal vs. Chelsea: 1st Half O/U 6.5"
        self.events[-1]["markets"][0]["closed"] = True
        self.requested = []

    def get(self, url, *, params):
        if params.get("after_cursor"):
            return Response({"events": self.events[2:]})
        return Response({"events": self.events[:2], "next_cursor": "page-2"})

    def post(self, url, *, json):
        self.requested = [item["token_id"] for item in json]
        return Response([
            {"asset_id": token, "asks": [{"price": price, "size": "10"} for price in prices]}
            for token, prices in {
                "five": ["0.90", "0.80"], "six": ["0.85"], "seven": ["0.851"],
                "empty": [], "eight-asks": ["0.86"],
            }.items() if token in self.requested
        ])


class LiveUnderMonitorTests(unittest.TestCase):
    def test_window_follows_israel_local_time(self):
        for month, offset in ((1, 2), (7, 3)):
            self.assertFalse(is_active(datetime(2026, month, 15, 13 - offset, 59, tzinfo=timezone.utc)))
            self.assertTrue(is_active(datetime(2026, month, 15, 14 - offset, tzinfo=timezone.utc)))
            self.assertTrue(is_active(datetime(2026, month, 15, 22 - offset, 59, tzinfo=timezone.utc)))
            self.assertFalse(is_active(datetime(2026, month, 15, 23 - offset, tzinfo=timezone.utc)))

    def test_uefa_nations_league_markets_are_included(self):
        with TemporaryDirectory() as tmp:
            config = RuntimeConfig(Path(tmp) / "config.json", under_totals=True)
            api = MarketAPI()
            league = "UEFA Nations League"
            api.events = [event(6.5, "nations-under", tags=[{"label": "Soccer"}, {"label": league}])]

            markets = asyncio.run(fetch_live_markets(api, config, NOW))

            self.assertEqual(markets["nations-under"].league, league)
            self.assertEqual(config.get_under_threshold(league, 6.5), 0.85)

    def test_alerts_on_first_qualifying_ask_once_across_restarts(self):
        with TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text('{"active_leagues": ["Premier League"], "league_thresholds": {"Premier League": 0.1}}')
            config = RuntimeConfig(config_path, under_totals=True)
            api = MarketAPI()
            sender = AsyncMock(return_value=True)
            history = Path(tmp) / "alerts.sqlite3"

            for _ in range(2):
                with closing(sqlite3.connect(history)) as db:
                    prepare_alert_history(db)
                    asyncio.run(poll_once(api, config, db, sender, ApiHealth(), NOW))

            self.assertEqual(api.requested, ["five", "six", "seven", "empty", "eight-asks"])
            self.assertEqual(sender.await_count, 2)
            self.assertIn("Under 5.5", sender.await_args_list[0].args[0])
            self.assertIn("Under 6.5", sender.await_args_list[1].args[0])

            config.set_under_threshold("Premier League", 7.5, 0.855)
            reloaded = RuntimeConfig(config_path, under_totals=True)
            self.assertEqual(reloaded.get_under_threshold("Premier League", 7.5), 0.855)
            self.assertEqual(reloaded.get_under_threshold("La Liga", 7.5), 0.85)
            self.assertEqual(reloaded.get_under_threshold("Premier League", 5.5), 0.80)
            self.assertEqual(reloaded.get_threshold("Premier League"), 0.10)
            with closing(sqlite3.connect(history)) as db:
                asyncio.run(poll_once(api, reloaded, db, sender, ApiHealth(), NOW))
            self.assertEqual(sender.await_count, 3)
            self.assertIn("Under 7.5", sender.await_args.args[0])

            previous = bot_ui.config
            bot_ui.config = reloaded
            try:
                buttons = bot_ui.get_thresholds_menu_kb().inline_keyboard
                self.assertEqual(buttons[0][0].callback_data, "under_league:Premier League")
            finally:
                bot_ui.config = previous

    def test_telegram_edits_the_selected_line_in_cents(self):
        with TemporaryDirectory() as tmp:
            config = RuntimeConfig(Path(tmp) / "config.json", under_totals=True)
            previous = bot_ui.config
            bot_ui.config = config
            try:
                state = AsyncMock()
                state.get_data.return_value = {"target_league": "La Liga", "target_under_line": 5.5}
                message = SimpleNamespace(text="NaN", answer=AsyncMock())
                asyncio.run(bot_ui.process_under_threshold(message, state))
                self.assertEqual(config.get_under_threshold("La Liga", 5.5), 0.80)
                state.clear.assert_not_awaited()

                message.text = "80.6"
                asyncio.run(bot_ui.process_under_threshold(message, state))
                self.assertEqual(config.get_under_threshold("La Liga", 5.5), 0.806)
                self.assertEqual(config.get_under_threshold("La Liga", 6.5), 0.85)
                self.assertEqual(config.get_under_threshold("Premier League", 5.5), 0.80)
                state.clear.assert_awaited_once()
            finally:
                bot_ui.config = previous

    def test_bulk_threshold_persists_all_leagues_and_preserves_other_lines(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            config = RuntimeConfig(path, under_totals=True)
            config.set_under_threshold("La Liga", 5.5, 0.7)
            config.set_under_threshold("La Liga", 6.5, 0.6)
            self.assertTrue(config.set_under_threshold_all_leagues(5.5, 0.82))
            reloaded = RuntimeConfig(path, under_totals=True)
            for league in SUPPORTED_LEAGUES:
                self.assertEqual(reloaded.get_under_threshold(league, 5.5), 0.82)
            self.assertEqual(reloaded.get_under_threshold("La Liga", 6.5), 0.6)
            with patch.object(config, "save", return_value=False):
                self.assertFalse(config.set_under_threshold_all_leagues(5.5, 0.9))
            self.assertEqual(config.get_under_threshold("La Liga", 5.5), 0.82)
            self.assertEqual(RuntimeConfig(path, under_totals=True).get_under_threshold("La Liga", 5.5), 0.82)

    def test_telegram_bulk_threshold_flow(self):
        with TemporaryDirectory() as tmp:
            config = RuntimeConfig(Path(tmp) / "config.json", under_totals=True)
            previous = bot_ui.config
            bot_ui.config = config
            try:
                self.assertTrue(any(row[0].callback_data == "under_all" for row in bot_ui.get_thresholds_menu_kb().inline_keyboard))
                callback = SimpleNamespace(data="under_all_edit:6.5", message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
                state = AsyncMock()
                asyncio.run(bot_ui.cb_under_all_edit(callback, state))
                state.update_data.assert_awaited_once_with(target_league=None, target_under_line=6.5)
                state.get_data.return_value = {"target_league": None, "target_under_line": 6.5}
                message = SimpleNamespace(text="88.5", answer=AsyncMock())
                asyncio.run(bot_ui.process_under_threshold(message, state))
                self.assertTrue(all(config.get_under_threshold(league, 6.5) == 0.885 for league in SUPPORTED_LEAGUES))
                state.clear.assert_awaited_once()
            finally:
                bot_ui.config = previous

    def test_only_confirmed_telegram_rejections_retry(self):
        with TemporaryDirectory() as tmp, closing(sqlite3.connect(":memory:")) as db:
            config = RuntimeConfig(Path(tmp) / "config.json", under_totals=True)
            api = MarketAPI()
            sender = AsyncMock(side_effect=[False, None, True])
            prepare_alert_history(db)

            for _ in range(3):
                asyncio.run(poll_once(api, config, db, sender, ApiHealth(), NOW))

            self.assertEqual(sender.await_count, 3)
            self.assertIn("Under 5.5", sender.await_args_list[0].args[0])
            self.assertIn("Under 6.5", sender.await_args_list[1].args[0])
            self.assertIn("Under 5.5", sender.await_args_list[2].args[0])

    def test_cancel_stops_an_under_threshold_edit(self):
        async def cancel():
            state = AsyncMock()
            state.get_state.return_value = bot_ui.BotStates.waiting_for_under_threshold.state
            async with Bot("12345:fake-token").context() as bot:
                message = Message(message_id=1, date=NOW, chat=Chat(id=1, type="private"), text="/cancel")
                with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
                    await bot_ui.router.propagate_event(
                        "message", message, bot=bot, state=state, raw_state=state.get_state.return_value
                    )
                state.clear.assert_awaited_once()
                self.assertIn("Cancelled", answer.await_args.args[0])

        asyncio.run(cancel())


if __name__ == "__main__":
    unittest.main()
