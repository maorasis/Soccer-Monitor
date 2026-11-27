import asyncio
import sqlite3
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp

import bot_ui
import daily_report
from config_manager import RuntimeConfig
from daily_report import record_alert, report_due, send_due_report
from live_under_monitor import ApiHealth, UnderMarket, poll_once, prepare_alert_history
from tests.test_live_under_monitor import NOW, MarketAPI, Response

# NOW is 19:00 in Israel (UTC+2 in November); the report is due from 09:00 the next day.
BEFORE_REPORT = datetime(2026, 11, 21, 6, 59, tzinfo=timezone.utc)
AT_REPORT = datetime(2026, 11, 21, 7, 0, tzinfo=timezone.utc)
AFTER_DEADLINE = datetime(2026, 11, 21, 8, 0, tzinfo=timezone.utc)


def market(line, condition, title="Arsenal vs. Chelsea"):
    return UnderMarket("Premier League", line, title, "slug", "1-1", condition)


class OutcomeAPI:
    """CLOB market lookups: winner flags per condition ID, or a failure."""

    def __init__(self, winners, fail=False):
        self.winners, self.fail, self.requested = winners, fail, []

    def get(self, url):
        condition = url.rsplit("/", 1)[1]
        self.requested.append(condition)
        if self.fail:
            raise aiohttp.ClientError("offline")
        return Response({"tokens": [
            {"token_id": token, "winner": winner} for token, winner in self.winners[condition].items()
        ]})


class DailyReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.config = RuntimeConfig(Path(self.tmp.name) / "config.json")
        self.db = sqlite3.connect(":memory:")
        prepare_alert_history(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def record_day(self):
        record_alert(self.db, "won", market(5.5, "c-won"), 0.80, NOW)
        record_alert(self.db, "lost", market(6.5, "c-lost"), 0.85, NOW + timedelta(minutes=5))
        record_alert(self.db, "open", market(7.5, "c-open", "Lazio vs. Roma"), 0.84, NOW + timedelta(minutes=9))
        return OutcomeAPI({
            "c-won": {"over-won": False, "won": True},
            "c-lost": {"over-lost": True, "lost": False},
            "c-open": {"over-open": False, "open": False},
        })

    def test_report_is_due_the_next_morning_only_after_alerts(self):
        self.assertIsNone(report_due(self.config, self.db, AT_REPORT))  # no alerts that day
        self.record_day()
        self.assertIsNone(report_due(self.config, self.db, BEFORE_REPORT))
        self.assertEqual(report_due(self.config, self.db, AT_REPORT).isoformat(), "2026-11-20")
        # Two days later only the previous day is considered, and it had no alerts.
        self.assertIsNone(report_due(self.config, self.db, AT_REPORT + timedelta(days=1)))

    def test_disabled_report_is_not_sent(self):
        self.record_day()
        self.config.set_daily_report(False)
        sender = AsyncMock(return_value=True)
        self.assertFalse(asyncio.run(send_due_report(OutcomeAPI({}), self.config, self.db, sender, AT_REPORT)))
        sender.assert_not_awaited()

    def test_report_counts_outcomes_and_is_sent_once(self):
        api = self.record_day()
        sender = AsyncMock(return_value=True)
        self.assertTrue(asyncio.run(send_due_report(api, self.config, self.db, sender, AT_REPORT)))
        text = sender.await_args.args[0]
        self.assertIn("Daily report · 2026-11-20", text)
        self.assertIn("Alerts sent: 3 (2 matches)", text)
        self.assertIn("✅ Won: 1 · ❌ Lost: 1 · ⏳ Pending: 1", text)
        self.assertIn("-65¢ on 165¢ (-39.4%)", text)
        self.assertIn("• 19:00 Arsenal vs. Chelsea · Under 5.5 @ 80¢ → ✅ +20¢", text)
        self.assertIn("Under 6.5 @ 85¢ → ❌ -85¢", text)
        self.assertIn("Lazio vs. Roma · Under 7.5 @ 84¢ → ⏳ pending", text)
        self.assertEqual(self.db.execute("SELECT outcome FROM alert_log ORDER BY id").fetchall(),
                         [("won",), ("lost",), (None,)])

        self.assertFalse(asyncio.run(send_due_report(api, self.config, self.db, sender, AT_REPORT)))
        self.assertEqual(sender.await_count, 1)

    def test_explicit_delivery_failure_is_retried(self):
        api = self.record_day()
        sender = AsyncMock(side_effect=[False, None])
        self.assertFalse(asyncio.run(send_due_report(api, self.config, self.db, sender, AT_REPORT)))
        # Uncertain delivery counts as sent so the report is not duplicated.
        self.assertTrue(asyncio.run(send_due_report(api, self.config, self.db, sender, AT_REPORT)))
        self.assertIsNone(report_due(self.config, self.db, AT_REPORT))

    def test_failed_outcome_lookups_wait_until_the_deadline(self):
        self.record_day()
        offline = OutcomeAPI({}, fail=True)
        sender = AsyncMock(return_value=True)
        self.assertFalse(asyncio.run(send_due_report(offline, self.config, self.db, sender, AT_REPORT)))
        sender.assert_not_awaited()
        self.assertTrue(asyncio.run(send_due_report(offline, self.config, self.db, sender, AFTER_DEADLINE)))
        self.assertIn("⏳ Pending: 3", sender.await_args.args[0])

    def test_long_days_are_truncated(self):
        for minute in range(daily_report.MAX_LISTED_ALERTS + 5):
            record_alert(self.db, "won", market(5.5, "c-won"), 0.80, NOW + timedelta(minutes=minute))
        sender = AsyncMock(return_value=True)
        asyncio.run(send_due_report(self.record_day(), self.config, self.db, sender, AT_REPORT))
        text = sender.await_args.args[0]
        self.assertIn("…and 8 more", text)
        self.assertLess(len(text), 4096)


class AlertLogTests(unittest.TestCase):
    def test_poll_logs_delivered_and_uncertain_alerts_only(self):
        with TemporaryDirectory() as tmp, closing(sqlite3.connect(Path(tmp) / "alerts.sqlite3")) as db:
            config = RuntimeConfig(Path(tmp) / "config.json")
            prepare_alert_history(db)
            api = MarketAPI()
            api.events[0]["markets"][0]["conditionId"] = "0xfive"
            sender = AsyncMock(side_effect=[True, False])
            asyncio.run(poll_once(api, config, db, sender, ApiHealth(), NOW))
            self.assertEqual(db.execute("SELECT token_id, condition_id, ask, line FROM alert_log").fetchall(),
                             [("five", "0xfive", 0.8, 5.5)])

            sender = AsyncMock(return_value=None)
            asyncio.run(poll_once(api, config, db, sender, ApiHealth(), NOW))
            self.assertEqual([row[0] for row in db.execute("SELECT token_id FROM alert_log ORDER BY id")],
                             ["five", "six"])


class ReportToggleTests(unittest.TestCase):
    def test_menu_button_switches_and_persists_the_report(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            config = RuntimeConfig(path)
            previous = bot_ui.config
            bot_ui.config = config
            try:
                labels = lambda: [row[0].text for row in bot_ui.get_main_menu_kb().inline_keyboard]
                self.assertIn("🗓 Daily Report · On", labels())
                callback = SimpleNamespace(message=SimpleNamespace(edit_reply_markup=AsyncMock()), answer=AsyncMock())
                asyncio.run(bot_ui.cb_toggle_daily_report(callback))
                self.assertFalse(RuntimeConfig(path).daily_report_enabled)
                self.assertIn("🗓 Daily Report · Off", labels())
                self.assertIn("Daily report off", callback.answer.await_args.args[0])

                asyncio.run(bot_ui.cb_toggle_daily_report(callback))
                self.assertTrue(RuntimeConfig(path).daily_report_enabled)
            finally:
                bot_ui.config = previous


if __name__ == "__main__":
    unittest.main()
