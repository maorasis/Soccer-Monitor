"""
Two regressions, both "a failed scan looked like an answer".

1. Gamma /events caps offset at 2000, so the old offset loop silently truncated the
   market scan (422 -> break). We now traverse every page via /events/keyset and still
   find markets past the old cap.
2. A fault partway through pagination returned the markets gathered so far. discovery_loop
   feeds that to set_markets as the authoritative live set, so a 500 on page 2 read as
   "those games ended" and unsubscribed the websocket mid-match. Failure now returns None.

Run: python tests/test_gamma_pagination.py
"""
import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from soccer_monitor import MarketDiscovery

logging.basicConfig(level=logging.INFO, format='%(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("GammaPaginationTest")

OFFSET_CAP = 2000
TOTAL_EVENTS = 2500
TARGET_INDEX = 2400  # deliberately past the cap
TARGET_TOKEN = "TARGET_UNDER_TOKEN"


def make_event(i):
    """Filler event that passes the league filter but yields no Under market."""
    return {
        "id": str(i),
        "title": f"Filler {i} vs. Padding {i}",
        "slug": f"filler-{i}",
        "tags": [{"label": "Premier League"}],
        "markets": [],
    }


def make_target_event():
    return {
        "id": str(TARGET_INDEX),
        "title": "Arsenal vs. Liverpool",
        "slug": "arsenal-liverpool",
        "tags": [{"label": "Premier League"}],
        "markets": [{
            "question": "Arsenal vs. Liverpool: O/U 1.5",
            "clobTokenIds": json.dumps(["OVER_TOKEN", TARGET_TOKEN]),
            "outcomes": json.dumps(["Over", "Under"]),
        }],
    }


ALL_EVENTS = [make_event(i) for i in range(TOTAL_EVENTS)]
ALL_EVENTS[TARGET_INDEX] = make_target_event()


class FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def json(self):
        return self._payload

    async def text(self):
        return json.dumps(self._payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeGammaSession:
    """Mimics Gamma: /events rejects offset > 2000, /events/keyset paginates by cursor."""

    def __init__(self):
        self.requests = []

    def get(self, url, params=None):
        params = params or {}
        self.requests.append((url, dict(params)))
        limit = int(params.get("limit", 100))

        if url.endswith("/events/keyset"):
            start = int(params.get("after_cursor", 0))
            page = ALL_EVENTS[start:start + limit]
            nxt = start + limit
            body = {"events": page}
            if nxt < len(ALL_EVENTS):
                body["next_cursor"] = str(nxt)
            return FakeResponse(200, body)

        offset = int(params.get("offset", 0))
        if offset > OFFSET_CAP:
            return FakeResponse(422, {
                "type": "validation error",
                "error": "offset too large, use /events/keyset for deeper pagination",
            })
        return FakeResponse(200, ALL_EVENTS[offset:offset + limit])


class MidScanFailureSession(FakeGammaSession):
    """Answers page 1, then 500s — the Gamma outage seen in production on 2026-09-02."""

    def get(self, url, params=None):
        params = params or {}
        if params.get("after_cursor"):
            self.requests.append((url, dict(params)))
            return FakeResponse(500, {"type": "internal error",
                                      "error": "internal server error"})
        return super().get(url, params)


class FakeTracker:
    daily_fixtures = []

    def active_fixtures(self):
        return self.daily_fixtures


class FakeConfig:
    def get_active_polymarket_tags(self):
        return {"premier league"}

    def match_league_name(self, tag):
        return "Premier League" if tag.lower() in ("premier league", "epl") else ""

    def get_threshold(self, league_name):
        return 0.10


async def main():
    session = FakeGammaSession()
    discovery = MarketDiscovery(session, FakeTracker(), FakeConfig())

    markets = await discovery.fetch_markets({"Arsenal", "Liverpool"})

    failures = []

    if TARGET_TOKEN not in markets:
        failures.append(
            f"Market past the offset cap (event #{TARGET_INDEX}) was not found. "
            f"Scan returned {len(markets)} market(s) — pagination truncated."
        )

    hit_capped_endpoint = any(
        not url.endswith("/keyset") and int(p.get("offset", 0)) > OFFSET_CAP
        for url, p in session.requests
    )
    if hit_capped_endpoint:
        failures.append("Still requesting /events with offset beyond the 2000 cap.")

    # The daemon logs the 500 at ERROR; that is the alert working, not a test failure.
    logging.getLogger("SoccerMonitor").setLevel(logging.CRITICAL)

    failing_session = MidScanFailureSession()
    failing_discovery = MarketDiscovery(failing_session, FakeTracker(), FakeConfig())
    result = await failing_discovery.fetch_markets({"Arsenal", "Liverpool"})

    if result is not None:
        failures.append(
            f"A 500 partway through the scan returned {len(result)} market(s) instead of "
            "None. set_markets cannot tell that dict apart from a real empty scan, so it "
            "would unsubscribe the websocket from every live game."
        )

    if failures:
        for f in failures:
            logger.error(f"FAIL: {f}")
        sys.exit(1)

    logger.info(
        f"PASS: traversed {len(session.requests)} page(s) and found the market "
        f"at event #{TARGET_INDEX}; a mid-scan 500 returned None."
    )


if __name__ == "__main__":
    asyncio.run(main())
