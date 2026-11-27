"""
Regression test: one failed HTTP request blinded the daemon for the rest of the day.
last_fetch_date was assigned after the date loop whatever happened, so a non-200 still
marked the day fetched and discovery_loop never retried. An exception skipped that
assignment and *did* retry, so a network blip recovered but an HTTP 500 did not.

The day now counts as fetched only once a fetch actually returns a schedule. The retry
is bounded, because discovery_loop scans every 15 minutes across a 12-hour window and
each attempt costs 2 of API-Football's 100 daily calls.

A quota error is terminal rather than retryable: it arrives as a 200 with an errors
block, and retrying cannot make the quota come back.

Run: python tests/test_fetch_retry.py
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import soccer_monitor
from soccer_monitor import LiveMatchTracker, MAX_SCHEDULE_FETCH_ATTEMPTS

logging.basicConfig(level=logging.CRITICAL)  # the daemon's own logging is not under test


class BaseSession:
    """aiohttp session stub. Counts the calls a real one would bill."""

    status = 200

    def __init__(self):
        self.calls = 0

    def get(self, url, headers=None, params=None):
        self.calls += 1
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return {"response": []}


class ServerErrorSession(BaseSession):
    """The failure that used to blind the daemon: a plain HTTP 500."""
    status = 500


class QuotaSession(BaseSession):
    async def json(self):
        return {"errors": {"requests": "You have reached the request limit for the day"}}


class ExplodingSession(BaseSession):
    def get(self, url, headers=None, params=None):
        self.calls += 1
        raise ConnectionResetError("connection reset by peer")


class WorkingSession(BaseSession):
    async def json(self):
        return {"response": [{
            "league": {"id": 39},
            "fixture": {"id": 7, "timestamp": 1756400000},
            "teams": {"home": {"name": "Arsenal"}, "away": {"name": "Chelsea"}},
        }]}


class OneLeagueConfig:
    def get_active_api_ids(self):
        return [39]


def build(session):
    tracker = LiveMatchTracker(session, OneLeagueConfig())
    tracker.api_key = "test-key"
    return tracker


async def test_http_500_retries_then_gives_up():
    session = ServerErrorSession()
    tracker = build(session)

    await tracker.fetch_daily_schedule()
    assert tracker.last_fetch_date is None, "a failed fetch must not count as today's fetch"

    while tracker.fetch_attempts < MAX_SCHEDULE_FETCH_ATTEMPTS:
        assert tracker.last_fetch_date is None, "should still be retrying"
        await tracker.fetch_daily_schedule()

    assert tracker.last_fetch_date is not None, "must stop retrying once the budget is spent"
    assert session.calls == 2 * MAX_SCHEDULE_FETCH_ATTEMPTS, (
        f"spent {session.calls} calls of the 100/day quota on a dead upstream")


async def test_quota_is_terminal_on_the_first_attempt():
    session = QuotaSession()
    tracker = build(session)

    await tracker.fetch_daily_schedule()

    assert tracker.last_fetch_date is not None, "quota will not recover; do not retry into it"
    assert tracker.fetch_attempts == 1
    assert session.calls == 2, "exactly the two dates, then stop"


async def test_exception_is_retryable_but_bounded():
    session = ExplodingSession()
    tracker = build(session)

    await tracker.fetch_daily_schedule()
    assert tracker.last_fetch_date is None, "a network blip should be retried"

    for _ in range(MAX_SCHEDULE_FETCH_ATTEMPTS):
        await tracker.fetch_daily_schedule()

    assert tracker.last_fetch_date is not None, "an unbounded retry would spend the quota"


async def test_success_marks_the_day_and_stops():
    session = WorkingSession()
    tracker = build(session)

    await tracker.fetch_daily_schedule()

    assert tracker.last_fetch_date is not None
    assert tracker.fetch_attempts == 1
    assert [f["home_team"] for f in tracker.active_fixtures()] == ["Arsenal", "Arsenal"]


async def test_a_later_success_still_marks_the_day():
    """The retry has to be able to win, not just to happen."""
    tracker = build(ServerErrorSession())
    await tracker.fetch_daily_schedule()
    assert tracker.last_fetch_date is None

    tracker.session = WorkingSession()  # upstream comes back
    await tracker.fetch_daily_schedule()

    assert tracker.last_fetch_date is not None
    assert tracker.active_fixtures(), "the recovered fetch must actually be cached"


async def main():
    await test_http_500_retries_then_gives_up()
    await test_quota_is_terminal_on_the_first_attempt()
    await test_exception_is_retryable_but_bounded()
    await test_success_marks_the_day_and_stops()
    await test_a_later_success_still_marks_the_day()
    print("OK: a failed fetch retries, a quota failure does not, and neither runs forever")


if __name__ == "__main__":
    asyncio.run(main())
