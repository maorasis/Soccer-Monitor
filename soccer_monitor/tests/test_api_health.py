"""
Two guarantees about the API health tracker.

Noise: an upstream fault that is not ours no longer pushes to Telegram on every scan.
It stays silent while it still looks transient, pushes once if it is still failing after
SUSTAINED_OUTAGE_ALERT, and pushes immediately when the exception is a bug in our own
code rather than the network's — silencing those is not the point.

Cost: rendering the status panel makes no HTTP call. API-Football allows 100 a day, so a
panel that probed on every menu-open would spend the budget it reports on.

Run: python tests/test_api_health.py
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp

import soccer_monitor
from soccer_monitor import (
    ApiHealth, DEP_API_FOOTBALL, DEP_GAMMA, DEP_WS, NO_TELEGRAM, SUSTAINED_OUTAGE_ALERT,
)

logging.basicConfig(level=logging.CRITICAL)  # the daemon's own logging is not under test

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


class FakeClock:
    """Drives ApiHealth's clock so a 30-minute outage does not take 30 minutes."""

    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now

    def advance(self, secs):
        self.now += secs


class ExplodingSession:
    """Any HTTP call through this is a bug: the panel must read recorded state only."""

    def get(self, *a, **kw):
        raise AssertionError("status panel made an HTTP GET")

    def post(self, *a, **kw):
        raise AssertionError("status panel made an HTTP POST")


class FakeResponse:
    def __init__(self, headers):
        self.headers = headers


def test_transient_fault_is_silent(clock):
    health = ApiHealth()
    extra = health.fail(DEP_GAMMA, "HTTP 500")
    check(extra is NO_TELEGRAM,
          "A first upstream 500 still pushes to Telegram; it should stay silent.")

    # Every scan for the next 29 minutes keeps failing. None of them may push.
    for _ in range(10):
        clock.advance(SUSTAINED_OUTAGE_ALERT / 12)
        extra = health.fail(DEP_GAMMA, "HTTP 500")
    check(extra is NO_TELEGRAM,
          "A fault under the sustained threshold pushed; only a long outage should.")


def test_sustained_outage_pushes_once(clock):
    health = ApiHealth()
    health.fail(DEP_GAMMA, "HTTP 500")

    clock.advance(SUSTAINED_OUTAGE_ALERT + 1)
    check(health.fail(DEP_GAMMA, "HTTP 500") == {},
          f"Still failing after {SUSTAINED_OUTAGE_ALERT}s did not push. A real outage "
          "during a match would be invisible unless the menu is opened.")

    pushes = 0
    for _ in range(5):
        clock.advance(600)
        if health.fail(DEP_GAMMA, "HTTP 500") == {}:
            pushes += 1
    check(pushes == 0,
          f"The same outage pushed {pushes} more time(s). One per outage, not one per scan.")


def test_recovery_starts_a_fresh_window(clock):
    health = ApiHealth()
    health.fail(DEP_GAMMA, "HTTP 500")
    clock.advance(SUSTAINED_OUTAGE_ALERT + 1)
    health.fail(DEP_GAMMA, "HTTP 500")  # consumes the one push

    health.ok(DEP_GAMMA)
    clock.advance(60)
    check(health.fail(DEP_GAMMA, "HTTP 500") is NO_TELEGRAM,
          "A fault after a recovery pushed immediately; recovery must reset the clock.")


def test_our_own_bugs_still_page(clock):
    health = ApiHealth()
    check(health.fail(DEP_GAMMA, "TypeError", exc=TypeError("boom")) == {},
          "A TypeError was silenced as an upstream fault. Broad `except Exception` "
          "handlers catch our bugs too, and those must still page.")
    check(health.fail(DEP_WS, "conn", exc=aiohttp.ClientError()) is NO_TELEGRAM,
          "A network error was treated as our bug and pushed.")


def test_panel_costs_no_api_call(clock):
    health = ApiHealth()
    health.ok(DEP_API_FOOTBALL)
    health.note_quota(FakeResponse({"x-ratelimit-requests-remaining": "71"}))
    health.fail(DEP_GAMMA, "HTTP 500")

    session = ExplodingSession()  # raises if the panel reaches for the network
    lines = health.status_lines()
    del session

    text = "\n".join(lines)
    check(len(lines) == len(soccer_monitor.DEPENDENCIES) + 1,
          f"Expected a line per dependency plus quota, got {len(lines)}:\n{text}")
    check("71/100" in text, f"Quota remaining missing from the panel:\n{text}")
    check("not called yet" in text,
          f"An uncontacted dependency should not read as broken:\n{text}")
    check(DEP_GAMMA in text and "down" in text, f"The failing upstream is not shown:\n{text}")


def test_panel_survives_markdown_in_detail(clock):
    health = ApiHealth()
    health.fail(DEP_GAMMA, "KeyError: '_id' *odd* [x]")
    text = "\n".join(health.status_lines())
    check(not any(c in text.split("🔌")[-1] for c in "_*`[]"),
          f"Unescaped Markdown in a detail string would break the whole message:\n{text}")


def main():
    clock = FakeClock()
    soccer_monitor.time.time = clock  # ApiHealth reads the clock through this module

    try:
        for name, fn in sorted(globals().items()):
            if name.startswith("test_"):
                clock.now += 10_000  # each test starts from its own point in time
                fn(clock)
    finally:
        soccer_monitor.time.time = __import__("time").time

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        sys.exit(1)
    print("PASS: upstream blips stay silent, a 30-minute outage pushes once, our own "
          "bugs still page, and the panel makes no API call.")


if __name__ == "__main__":
    main()
