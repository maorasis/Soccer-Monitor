"""
Regression test: API-Football's free plan allows 100 calls a day, and on 2026-08-30
one fixture consumed 106 of them in ten minutes. process_dip fetched the live score
on every order-book tick, before the once-a-day dedup in send_alert could stop it,
and a market suppressed as mathematically dead was never recorded at all — so it
re-queried forever.

Verifies each token now costs at most one score call per day.
Run: python tests/test_score_call_budget.py
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import soccer_monitor
from soccer_monitor import AlertEngine, MarketInfo, OrderBookMonitor

logging.basicConfig(level=logging.WARNING)

soccer_monitor.ALERT_CONFIRM_DELAY = 0  # the confirmation wait is tested elsewhere

TICKS = 50


class CountingTracker:
    """Stands in for LiveMatchTracker, counting the calls the real one would bill."""

    def __init__(self, score):
        self.score = score
        self.calls = 0

    async def fetch_live_score_on_demand(self, fixture_id):
        self.calls += 1
        return self.score


def build(score):
    alert_engine = AlertEngine(session=None)
    alert_engine.bot_token = None  # log the alert, never hit Telegram
    tracker = CountingTracker(score)
    monitor = OrderBookMonitor(alert_engine, tracker, asyncio.Event(), runtime_config=None)
    monitor.monitored_markets = {
        "TOKEN": MarketInfo(
            event_title="Viking FK vs. Aalesunds FK",
            event_slug="viking-fk-aalesunds-fk",
            league="Eliteserien",
            market_question="Viking FK vs. Aalesunds FK: O/U 2.5",
            under_token_id="TOKEN",
            fixture_id=1557379,
        )
    }
    # This test drives process_dip directly, so arm the token the way an
    # above-threshold book would: the call budget is what is under test here.
    monitor.armed.add("TOKEN")
    # process_dip confirms against the live book before it spends a call, so it needs
    # one: an empty book is a resolved market and would never reach the score fetch.
    monitor.book_state["TOKEN"] = {0.05: 100.0}
    return monitor, tracker


async def main():
    # Live market: 1-1 is under the 2.5 line, so the first tick alerts.
    monitor, tracker = build("1-1 (65' 2H)")
    for _ in range(TICKS):
        await monitor.process_dip("TOKEN", 0.05)
    assert tracker.calls == 1, f"live market spent {tracker.calls} calls over {TICKS} ticks"
    assert "TOKEN" in monitor.alert_engine.last_alert_time

    # Dead market: 3 goals past a 2.5 line, Under cannot win. Suppressed, and stays
    # suppressed without re-querying.
    monitor, tracker = build("2-1 (65' 2H)")
    for _ in range(TICKS):
        await monitor.process_dip("TOKEN", 0.05)
    assert tracker.calls == 1, f"dead market spent {tracker.calls} calls over {TICKS} ticks"

    # A new day flushes the history, so the market is eligible again.
    monitor.alert_engine.clear_alert_history("2026-08-31")
    await monitor.process_dip("TOKEN", 0.05)
    assert tracker.calls == 2, f"expected one call on the new day, got {tracker.calls}"

    print(f"OK: 1 score call per token per day, over {TICKS} ticks (was {TICKS})")


if __name__ == "__main__":
    asyncio.run(main())
