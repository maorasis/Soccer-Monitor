"""
Regression test: the WebSocket reconnected on every discovery poll, not just when
the monitored markets actually changed.

set_markets() fired markets_changed_event unconditionally, and discovery_loop calls
it once per poll with a freshly built dict. The run loop saw the event, logged
"Markets changed, reconnecting WS..." and tore down a perfectly healthy socket every
cycle -- roughly every 8 minutes in production -- losing the book updates sent while
it reconnected. The socket subscribes by token id alone, so only a changed key set
is worth a reconnect.

Verifies the event fires on a real membership change and stays quiet otherwise,
including when only market metadata changed.
Run: python tests/test_ws_reconnect_churn.py
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from soccer_monitor import MarketInfo, OrderBookMonitor

logging.basicConfig(level=logging.WARNING)


def market(token_id, question="Over/Under 2.5 Goals"):
    return MarketInfo(
        event_title="Ajax vs PSV",
        event_slug="ajax-vs-psv",
        league="Eredivisie",
        market_question=question,
        under_token_id=token_id,
    )


async def main():
    event = asyncio.Event()
    # set_markets touches only the lock, the dict and the event.
    monitor = OrderBookMonitor(None, None, event, None)

    # 1. First real market list: nothing was subscribed, so the socket must connect.
    await monitor.set_markets({"A": market("A"), "B": market("B")})
    assert event.is_set(), "first market list should trigger a connect"

    # 2. The same two tokens again -- this is what discovery_loop does every poll.
    event.clear()
    await monitor.set_markets({"A": market("A"), "B": market("B")})
    assert not event.is_set(), (
        "an unchanged token set must not reconnect the socket -- this is the churn "
        "the fix removes"
    )

    # 3. Same tokens, different insertion order. dict.keys() compares as a set, so
    #    Gamma returning the events in a different order must not reconnect either.
    event.clear()
    await monitor.set_markets({"B": market("B"), "A": market("A")})
    assert not event.is_set(), "token order must not count as a change"

    # 4. Same tokens, changed metadata. process_dip reads monitored_markets under the
    #    lock, so the new values are already live; a reconnect would buy nothing.
    event.clear()
    await monitor.set_markets({"A": market("A", "Over/Under 3.5 Goals"),
                               "B": market("B")})
    assert not event.is_set(), "a metadata-only change must not reconnect"
    async with monitor.markets_lock:
        assert monitor.monitored_markets["A"].market_question == "Over/Under 3.5 Goals", \
            "the new metadata must still be visible without a reconnect"

    # 5. A token swapped out: the subscription is now wrong, so reconnect.
    event.clear()
    await monitor.set_markets({"A": market("A"), "C": market("C")})
    assert event.is_set(), "a changed token set must reconnect"

    # 6. Dropping to no markets at the end of the active window.
    event.clear()
    await monitor.set_markets({})
    assert event.is_set(), "clearing the markets must reconnect"

    # 7. ...and staying empty overnight must stay quiet.
    event.clear()
    await monitor.set_markets({})
    assert not event.is_set(), "an already-empty market list must not reconnect"

    print("PASS: the socket reconnects on membership changes only")


asyncio.run(main())
