import asyncio
import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot_ui
from config_manager import RuntimeConfig
from live_under_monitor import ApiHealth, UnderMarket, poll_once, prepare_alert_history
from tests.test_live_under_monitor import MarketAPI, NOW


class BotFeatureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.json"
        self.config = RuntimeConfig(self.path)
        self.old = bot_ui.config, bot_ui.market_fetcher, bot_ui.test_sender
        bot_ui.config = self.config
        bot_ui.market_fetcher = None
        bot_ui.test_sender = None

    def tearDown(self):
        bot_ui.config, bot_ui.market_fetcher, bot_ui.test_sender = self.old
        self.tmp.cleanup()

    def callback(self, data):
        return SimpleNamespace(data=data, message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())

    def test_threshold_view_identifies_custom_values_and_disabled_leagues(self):
        self.config.set_under_threshold("La Liga", 5.5, .75)
        self.config.active_leagues.remove("La Liga")
        callback = self.callback("view_thresholds:0")
        asyncio.run(bot_ui.cb_view_thresholds(callback))
        text = callback.message.edit_text.await_args.args[0]
        self.assertIn("La Liga (disabled)", text)
        self.assertIn("Under 5.5: 75¢ ✏️", text)
        self.assertIn("Under 6.5: 85¢", text)

    def test_snapshot_records_poll_and_live_view_uses_current_limit(self):
        snapshot = {}
        with sqlite3.connect(":memory:") as db:
            prepare_alert_history(db)
            asyncio.run(poll_once(MarketAPI(), self.config, db, AsyncMock(return_value=True), ApiHealth(), NOW, snapshot))
        self.assertEqual(snapshot["at"], NOW)
        bot_ui.market_fetcher = AsyncMock(return_value=snapshot)
        self.config.set_under_threshold("Premier League", 5.5, .81)
        callback = self.callback("live_markets:0")
        asyncio.run(bot_ui.cb_live_markets(callback))
        text = callback.message.edit_text.await_args.args[0]
        self.assertIn("Ask: 80¢ · Current limit: 81¢", text)
        self.assertNotIn("No ask", text)
        self.assertIn("Fetched:", text)
        bot_ui.market_fetcher.assert_awaited_once()

    def test_live_view_only_shows_enabled_leagues_at_or_below_each_limit(self):
        self.config.active_leagues = ["Premier League"]
        self.config.set_under_threshold("Premier League", 5.5, .77)
        rows = [
            (UnderMarket("Premier League", 5.5, "Below limit", "below", ""), .76),
            (UnderMarket("Premier League", 5.5, "At limit", "equal", ""), .77),
            (UnderMarket("Premier League", 5.5, "Above limit", "above", ""), .78),
            (UnderMarket("Premier League", 6.5, "Other line above", "six", ""), .86),
            (UnderMarket("Premier League", 5.5, "Missing ask", "missing", ""), None),
            (UnderMarket("Premier League", 5.5, "Invalid ask", "invalid", ""), 0),
            (UnderMarket("La Liga", 5.5, "Disabled league", "disabled", ""), .50),
        ]
        bot_ui.market_fetcher = AsyncMock(return_value={"at": NOW, "markets": rows})
        callback = self.callback("live_markets:9")
        asyncio.run(bot_ui.cb_live_markets(callback))
        text = callback.message.edit_text.await_args.args[0]
        for title in ("Below limit", "At limit"):
            self.assertIn(title, text)
        for title in ("Above limit", "Other line above", "Missing ask", "Invalid ask", "Disabled league"):
            self.assertNotIn(title, text)
        self.config.set_under_threshold("Premier League", 5.5, .75)
        asyncio.run(bot_ui.cb_live_markets(callback))
        text = callback.message.edit_text.await_args.args[0]
        self.assertIn("No live markets at or below your limits in enabled leagues.", text)
        self.assertNotIn("Below limit", text)

    def test_live_view_fetches_on_each_use_and_reports_failures(self):
        market = UnderMarket("Premier League", 5.5, "Match", "match", "")
        bot_ui.market_fetcher = AsyncMock(side_effect=[
            {"at": NOW, "markets": [(market, .8)]},
            {"at": NOW, "markets": [(market, .7)]},
            OSError("offline"),
        ])
        callback = self.callback("live_markets:0")
        for expected in ("Ask: 80¢", "Ask: 70¢", "Could not fetch current markets"):
            asyncio.run(bot_ui.cb_live_markets(callback))
            self.assertIn(expected, callback.message.edit_text.await_args.args[0])
        self.assertEqual(bot_ui.market_fetcher.await_count, 3)

    def test_cooldown_setting_and_rearm_callback(self):
        state = AsyncMock()
        message = SimpleNamespace(text="1", answer=AsyncMock())
        asyncio.run(bot_ui.process_alert_cooldown(message, state))
        self.assertEqual(self.config.alert_cooldown_minutes, 15)
        state.clear.assert_not_awaited()

        message.text = "20"
        asyncio.run(bot_ui.process_alert_cooldown(message, state))
        self.assertEqual(RuntimeConfig(self.path).alert_cooldown_minutes, 20)
        state.clear.assert_awaited_once()

        button_id = "a" * 32
        callback = self.callback(f"rearm_alert:{button_id}")
        with patch.object(bot_ui, "rearm_alert", return_value=15) as rearm:
            asyncio.run(bot_ui.cb_rearm_alert(callback))
        self.assertEqual(rearm.call_args.args[0], button_id)
        self.assertIn("15 minutes", callback.answer.await_args.args[0])

    def test_test_notification_reports_delivery_outcomes(self):
        for result, expected in [(True, "sent"), (False, "failed"), (None, "uncertain")]:
            bot_ui.test_sender = AsyncMock(return_value=result)
            callback = self.callback("test_notification")
            asyncio.run(bot_ui.cb_test_notification(callback))
            self.assertIn(expected, callback.answer.await_args.args[0])
            self.assertIn("Test notification", bot_ui.test_sender.await_args.args[0])

    def test_schedule_persistence_overnight_and_timezone(self):
        self.assertTrue(self.config.set_monitor_schedule("22:30", "02:15", "Europe/London"))
        config = RuntimeConfig(self.path)
        self.assertTrue(config.monitoring_active(datetime(2026, 1, 1, 23, tzinfo=timezone.utc)))
        self.assertTrue(config.monitoring_active(datetime(2026, 1, 2, 2, 14, tzinfo=timezone.utc)))
        self.assertFalse(config.monitoring_active(datetime(2026, 1, 2, 2, 15, tzinfo=timezone.utc)))
        self.assertEqual(config.next_monitor_start(datetime(2026, 1, 2, 2, 15, tzinfo=timezone.utc)).hour, 22)
        with patch.object(config, "save", return_value=False):
            self.assertFalse(config.set_monitor_schedule("10:00", "11:00", "UTC"))
        self.assertEqual(config.monitor_start, "22:30")
        with self.assertRaises(ValueError):
            config.set_monitor_schedule("12:00", "12:00", "UTC")

    def test_schedule_telegram_validation_and_success(self):
        message = SimpleNamespace(text="25:00 10:00 UTC", answer=AsyncMock())
        state = AsyncMock()
        asyncio.run(bot_ui.process_schedule(message, state))
        state.clear.assert_not_awaited()
        message.text = "09:30 18:15 UTC"
        asyncio.run(bot_ui.process_schedule(message, state))
        state.clear.assert_awaited_once()
        self.assertEqual(RuntimeConfig(self.path).monitor_end, "18:15")


if __name__ == "__main__":
    unittest.main()
