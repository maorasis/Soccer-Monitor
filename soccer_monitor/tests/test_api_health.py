"""The API health tracker stays quiet during blips and never probes upstreams.

An upstream fault that is not ours stays silent while it still looks transient, pushes
once if it is still failing after SUSTAINED_OUTAGE_ALERT, and pushes immediately when
the exception is a bug in our own code rather than the network's.
"""
import unittest
from unittest.mock import patch

import aiohttp

import common
from common import ApiHealth, DEP_BOOKS, DEP_GAMMA, DEP_TELEGRAM, NO_TELEGRAM, SUSTAINED_OUTAGE_ALERT


class FakeClock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now

    def advance(self, secs):
        self.now += secs


class ApiHealthTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        patcher = patch.object(common.time, "time", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_transient_fault_is_silent(self):
        health = ApiHealth()
        self.assertIs(health.fail(DEP_GAMMA, "HTTP 500"), NO_TELEGRAM)
        for _ in range(10):
            self.clock.advance(SUSTAINED_OUTAGE_ALERT / 12)
            extra = health.fail(DEP_GAMMA, "HTTP 500")
        self.assertIs(extra, NO_TELEGRAM)

    def test_sustained_outage_pushes_once(self):
        health = ApiHealth()
        health.fail(DEP_GAMMA, "HTTP 500")
        self.clock.advance(SUSTAINED_OUTAGE_ALERT + 1)
        self.assertEqual(health.fail(DEP_GAMMA, "HTTP 500"), {})
        for _ in range(5):
            self.clock.advance(600)
            self.assertIs(health.fail(DEP_GAMMA, "HTTP 500"), NO_TELEGRAM)

    def test_recovery_starts_a_fresh_window(self):
        health = ApiHealth()
        health.fail(DEP_GAMMA, "HTTP 500")
        self.clock.advance(SUSTAINED_OUTAGE_ALERT + 1)
        health.fail(DEP_GAMMA, "HTTP 500")  # consumes the one push
        health.ok(DEP_GAMMA)
        self.clock.advance(60)
        self.assertIs(health.fail(DEP_GAMMA, "HTTP 500"), NO_TELEGRAM)

    def test_our_own_bugs_still_page(self):
        health = ApiHealth()
        self.assertEqual(health.fail(DEP_GAMMA, "TypeError", exc=TypeError("boom")), {})
        self.assertIs(health.fail(DEP_BOOKS, "conn", exc=aiohttp.ClientError()), NO_TELEGRAM)

    def test_status_lines_cover_every_dependency(self):
        health = ApiHealth()
        health.ok(DEP_GAMMA, "3 live Under markets")
        health.fail(DEP_BOOKS, "HTTP 500")
        text = "\n".join(health.status_lines())
        self.assertEqual(len(health.status_lines()), 3)
        self.assertIn("3 live Under markets", text)
        self.assertIn(f"{DEP_TELEGRAM} \u2014 not called yet", text)
        self.assertIn("down", text)

    def test_markdown_in_details_is_removed(self):
        health = ApiHealth()
        health.fail(DEP_GAMMA, "KeyError: '_id' *odd* [x]")
        self.assertFalse(any(c in "\n".join(health.status_lines()) for c in "_*`[]"))


if __name__ == "__main__":
    unittest.main()
