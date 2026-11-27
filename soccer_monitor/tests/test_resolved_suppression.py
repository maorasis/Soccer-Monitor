"""
Regression test: a crossing is only an opportunity if it survives ALERT_CONFIRM_DELAY.

A goal settles an Under market mid-flight. The ask falls through the threshold on its
way to the floor, which used to fire an alert for a market nobody can profit from. Live
Polymarket data shows a settled side two ways -- swept to the minimum tick (0.001) with
no bids left, or asks pulled and the book empty -- so both are suppressed.

Run: python tests/test_resolved_suppression.py
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
soccer_monitor.ALERT_CONFIRM_DELAY = 0.05


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
    await asyncio.gather(*list(monitor.dip_tasks))


async def test_a_confirmed_dip_still_alerts():
    """Guard against 'fixed' by never alerting at all."""
    monitor, alerts = build()
    await monitor._process_event(snapshot([0.14]))  # arms
    await monitor._process_event(snapshot([0.05]))  # crosses, and stays there
    await drain(monitor)
    assert alerts == [0.05], f"a dip that holds must still alert: {alerts}"


async def test_collapse_to_the_floor_is_silent():
    monitor, alerts = build()
    await monitor._process_event(snapshot([0.14]))
    await monitor._process_event(snapshot([0.05]))   # crosses on the way down
    await monitor._process_event(snapshot([0.001]))  # settled before the wait is out
    await drain(monitor)
    assert alerts == [], f"a market swept to the minimum tick must not alert: {alerts}"


async def test_an_emptied_book_is_silent():
    monitor, alerts = build()
    await monitor._process_event(snapshot([0.14]))
    await monitor._process_event(snapshot([0.05]))
    await monitor._process_event(snapshot([]))  # every ask pulled
    await drain(monitor)
    assert alerts == [], f"a book with nothing left to buy must not alert: {alerts}"


async def test_alerts_on_the_confirmed_price_not_the_crossing_one():
    monitor, alerts = build()
    await monitor._process_event(snapshot([0.14]))
    await monitor._process_event(snapshot([0.05]))
    await monitor._process_event(snapshot([0.03]))  # still a live dip, cheaper
    await drain(monitor)
    assert alerts == [0.03], f"the alert must quote the price it confirmed: {alerts}"


async def main():
    await test_a_confirmed_dip_still_alerts()
    await test_collapse_to_the_floor_is_silent()
    await test_an_emptied_book_is_silent()
    await test_alerts_on_the_confirmed_price_not_the_crossing_one()
    print("OK: a dip must survive the confirmation wait, and alerts quote the confirmed price")


if __name__ == "__main__":
    asyncio.run(main())
