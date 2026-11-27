"""
Regression test: a market that is ALREADY below its threshold the first time we see
its book is not a dip -- the game started there, the price did not fall there. Those
markets must stay silent until the ask trades back above the threshold and comes
down again.

Run: python tests/test_opening_below_threshold.py
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import soccer_monitor
from soccer_monitor import AlertEngine, MarketInfo, OrderBookMonitor

logging.basicConfig(level=logging.CRITICAL)

TOKEN = "TOKEN"
soccer_monitor.ALERT_CONFIRM_DELAY = 0  # the confirmation wait is tested elsewhere


class FixedThresholdConfig:
    def get_threshold(self, league):
        return 0.10


def build():
    monitor = OrderBookMonitor(AlertEngine(session=None), None,
                               asyncio.Event(), runtime_config=FixedThresholdConfig())
    monitor.monitored_markets = {
        TOKEN: MarketInfo(
            event_title="Arsenal vs. Chelsea",
            event_slug="arsenal-chelsea",
            league="Premier League",
            market_question="Will there be over 1.5 goals?",
            under_token_id=TOKEN,
            fixture_id=None,  # skips the score fetch; the dead-market check is tested elsewhere
        )
    }

    alerts = []

    async def record_alert(market, ask, score):
        alerts.append(ask)

    monitor.alert_engine.send_alert = record_alert
    return monitor, alerts


def snapshot(asks):
    return {"event_type": "book", "asset_id": TOKEN,
            "asks": [{"price": str(p), "size": "100"} for p in asks]}


async def drain(monitor):
    """A dip confirms off the read loop, so let those tasks finish before asserting."""
    await asyncio.gather(*list(monitor.dip_tasks))


async def test_opening_below_threshold_is_silent():
    monitor, alerts = build()
    await monitor._process_event(snapshot([0.05]))
    await monitor._process_event(snapshot([0.04]))
    await drain(monitor)
    assert alerts == [], f"a market that opened below the line must not alert: {alerts}"


async def test_crossing_up_then_back_down_alerts():
    monitor, alerts = build()
    await monitor._process_event(snapshot([0.05]))   # opens below -- silent
    await monitor._process_event(snapshot([0.14]))   # trades back above the line
    await monitor._process_event(snapshot([0.05]))   # real dip
    await drain(monitor)
    assert alerts == [0.05], f"a genuine crossing must alert: {alerts}"


async def test_an_emptied_book_does_not_arm():
    """All asks pulled is no price, not a price above the threshold."""
    monitor, alerts = build()
    await monitor._process_event(snapshot([0.05]))
    await monitor._process_event(snapshot([]))
    await monitor._process_event(snapshot([0.05]))
    await drain(monitor)
    assert alerts == [], f"an empty book must not count as trading above: {alerts}"


async def main():
    await test_opening_below_threshold_is_silent()
    await test_crossing_up_then_back_down_alerts()
    await test_an_emptied_book_does_not_arm()
    print("OK: opening below the line is silent, a real crossing still alerts")


if __name__ == "__main__":
    asyncio.run(main())
