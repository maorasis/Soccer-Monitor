"""
Checks that failures reach Telegram without turning into a flood.

Every logger.error in the daemon routes through ErrorAlertHandler, so the guarantees
that matter are: a repeating fault alerts once per cooldown, two different faults are
never collapsed into one, and a failure to reach Telegram is not itself announced over
Telegram (which would loop).

Run: python tests/test_error_alerts.py
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import soccer_monitor
from soccer_monitor import NO_TELEGRAM, ErrorAlertHandler

QUOTA_ERROR = "API-Football response errors: {'requests': 'You have reached the request limit for the day'}"


class QuotaExhaustedSession:
    """aiohttp session stub: answers 200 with the body API-Football returns when spent."""

    def get(self, url, headers=None, params=None):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    status = 200

    async def json(self):
        return {"errors": {"requests": "You have reached the request limit for the day"}}


class OffSeasonSession(QuotaExhaustedSession):
    """Answers normally, but every fixture belongs to a league we do not follow."""

    async def json(self):
        return {"response": [{
            "league": {"id": 999},
            "fixture": {"id": 1, "timestamp": 1756400000,
                        "status": {"short": "NS", "elapsed": None}},
            "teams": {"home": {"name": "Some FC"}, "away": {"name": "Other FC"}},
        }]}


class OneLeagueConfig:
    def get_active_api_ids(self):
        return [39]


def drain(queue):
    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


async def main():
    queue = asyncio.Queue(maxsize=100)
    handler = ErrorAlertHandler(queue)
    log = logging.getLogger("ErrorAlertTest")
    log.propagate = False  # keep the root handlers out of it
    log.addHandler(handler)

    # The quota fault repeats on every scan; it should alert once.
    for _ in range(10):
        log.error(QUOTA_ERROR)
    sent = drain(queue)
    assert len(sent) == 1, f"expected 1 alert for a repeated fault, got {len(sent)}"
    assert "You have reached the request limit" in sent[0], sent[0]
    assert "test_error_alerts.py" in sent[0], "alert should name the call site"

    # Interpolated values differ per call, but the call site does not — still one alert.
    for status in (500, 502, 503):
        log.error(f"Gamma API error: {status}")
    assert len(drain(queue)) == 1, "a call site logging varying values should alert once"

    # A different call site is a different fault.
    log.error("WebSocket error: keepalive ping timeout. Reconnecting...")
    assert len(drain(queue)) == 1, "a second call site should alert on its own"

    # Sending to Telegram failed: announcing that over Telegram would recurse.
    log.error("Failed to send Telegram message: 429", extra=NO_TELEGRAM)
    assert drain(queue) == [], "no_telegram records must never be queued"

    # Below ERROR is routine and stays in the journal.
    log.warning("WebSocket connection closed. Reconnecting...")
    log.info("No markets to monitor. Waiting...")
    assert drain(queue) == [], "only ERROR and above should alert"

    # Once the cooldown lapses, an unresolved fault speaks up again.
    handler.last_sent.clear()
    log.error(QUOTA_ERROR)
    assert len(drain(queue)) == 1, "fault should re-alert after the cooldown"

    # A flood must not grow without bound.
    small = asyncio.Queue(maxsize=2)
    flooder = ErrorAlertHandler(small)
    flooder.last_sent = {}
    for i in range(10):
        record = logging.LogRecord("x", logging.ERROR, f"/app/f{i}.py", i, "boom", None, None)
        flooder.emit(record)
    assert small.qsize() == 2, f"queue should cap at 2, holds {small.qsize()}"

    # End to end: the fault that started this — API-Football out of quota — should
    # reach the queue through the real fetch_daily_schedule, with nothing stubbed but
    # the HTTP response itself.
    root = logging.getLogger()
    live_queue = asyncio.Queue(maxsize=100)
    live_handler = ErrorAlertHandler(live_queue)
    root.addHandler(live_handler)
    try:
        tracker = soccer_monitor.LiveMatchTracker(QuotaExhaustedSession(), OneLeagueConfig())
        tracker.api_key = "test-key"
        await tracker.fetch_daily_schedule()
    finally:
        root.removeHandler(live_handler)

    alerts = drain(live_queue)
    assert tracker.daily_fixtures == [], "no fixtures should survive a quota failure"
    # Two dates fail at one call site (1 alert), then the empty schedule reports (1 alert).
    assert len(alerts) == 2, f"expected the quota error and the empty schedule, got {len(alerts)}"
    assert any("request limit" in a for a in alerts), alerts
    assert any("no fixtures at all" in a for a in alerts), alerts

    # The off-season case must stay silent: the active leagues have nothing on, but
    # API-Football is answering, so there is no fault to report.
    quiet_queue = asyncio.Queue(maxsize=100)
    quiet_handler = ErrorAlertHandler(quiet_queue)
    root.addHandler(quiet_handler)
    try:
        idle = soccer_monitor.LiveMatchTracker(OffSeasonSession(), OneLeagueConfig())
        idle.api_key = "test-key"
        await idle.fetch_daily_schedule()
    finally:
        root.removeHandler(quiet_handler)

    assert idle.active_fixtures() == [], "a league we do not follow must not be monitored"
    assert idle.daily_fixtures, "the raw fetch is still cached, so toggling that league on takes effect at once"
    assert drain(quiet_queue) == [], "an idle league is not a failure and must not alert"

    print("OK: repeated faults alert once, distinct faults alert separately, no send loop")
    print("OK: an exhausted API-Football quota reaches Telegram end to end")
    print("OK: active leagues being idle stays silent")


if __name__ == "__main__":
    asyncio.run(main())
