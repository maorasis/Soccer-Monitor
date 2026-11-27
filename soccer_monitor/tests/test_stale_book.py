"""
Regression test: a stale order book could fire a dip alert on a price that no longer
existed. This is the one path in the daemon that produced a *wrong* alert rather than
a missed one.

Two causes, both here:

  * the snapshot handler was guarded by `if asset_id and asks:`, so a snapshot with an
    empty ask list was ignored and the previous levels stayed in place -- the withdrawn
    low ask remained the minimum and alerted;
  * book_state was never cleared on reconnect, so incremental price_change events were
    applied on top of a book from before the gap, and level removals missed during the
    disconnect never happened.

Run: python tests/test_stale_book.py
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import websockets

import soccer_monitor
from soccer_monitor import AlertEngine, MarketInfo, OrderBookMonitor

logging.basicConfig(level=logging.CRITICAL)

TOKEN = "TOKEN"


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
            market_question="Will there be over 2.5 goals?",
            under_token_id=TOKEN,
            fixture_id=1,
        )
    }

    dips = []

    async def record_dip(token_id, ask):
        dips.append((token_id, ask))

    monitor.process_dip = record_dip
    return monitor, dips


def snapshot(asks):
    return {"event_type": "book", "asset_id": TOKEN,
            "asks": [{"price": str(p), "size": str(s)} for p, s in asks]}


async def drain(monitor):
    """A dip is handled off the read loop, so let those tasks finish before asserting."""
    await asyncio.gather(*list(monitor.dip_tasks))


async def test_a_dip_still_alerts():
    """Guard against 'fixed' by breaking the feature."""
    monitor, dips = build()
    await monitor._process_event(snapshot([(0.05, 100), (0.30, 50)]))
    await drain(monitor)
    assert dips == [(TOKEN, 0.05)], dips


async def test_emptied_book_does_not_alert_on_a_withdrawn_ask():
    monitor, dips = build()

    await monitor._process_event(snapshot([(0.05, 100)]))
    await drain(monitor)
    assert dips == [(TOKEN, 0.05)], "the initial dip should alert"
    dips.clear()

    # Every ask is pulled. The book is now empty, not unchanged.
    await monitor._process_event(snapshot([]))
    await drain(monitor)

    assert monitor.book_state[TOKEN] == {}, "an empty snapshot must empty the book"
    assert dips == [], "must not alert on an ask that was withdrawn"


async def test_reconnect_drops_the_previous_book():
    """The stale low ask must not survive into the next connection."""

    class ClosingWebsocket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def send(self, payload):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise websockets.ConnectionClosed(None, None)

    monitor, _ = build()
    monitor.book_state[TOKEN] = {0.05: 100.0}  # cached before the drop

    original_connect = websockets.connect
    original_delay = soccer_monitor.WS_RECONNECT_DELAY
    websockets.connect = lambda *a, **k: ClosingWebsocket()
    soccer_monitor.WS_RECONNECT_DELAY = 0.01
    try:
        task = asyncio.create_task(monitor.run())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    finally:
        websockets.connect = original_connect
        soccer_monitor.WS_RECONNECT_DELAY = original_delay

    assert monitor.book_state == {}, (
        f"reconnect must not inherit the old book, kept {monitor.book_state}")


async def main():
    await test_a_dip_still_alerts()
    await test_emptied_book_does_not_alert_on_a_withdrawn_ask()
    await test_reconnect_drops_the_previous_book()
    print("OK: an emptied book alerts nobody, and a reconnect starts from a clean book")


if __name__ == "__main__":
    asyncio.run(main())
