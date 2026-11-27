"""Failures reach Telegram without turning into a flood.

A repeating fault alerts once per cooldown, two different call sites are never collapsed
into one, and a failure to reach Telegram is not itself announced over Telegram.
"""
import asyncio
import logging
import unittest

from common import NO_TELEGRAM, ErrorAlertHandler


def drain(queue):
    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


class ErrorAlertTests(unittest.TestCase):
    def setUp(self):
        self.queue = asyncio.Queue(maxsize=100)
        self.handler = ErrorAlertHandler(self.queue)
        self.log = logging.getLogger("ErrorAlertTest")
        self.log.propagate = False
        self.log.addHandler(self.handler)
        self.addCleanup(self.log.removeHandler, self.handler)

    def test_a_repeated_fault_alerts_once_and_names_its_call_site(self):
        for _ in range(10):
            self.log.error("Polymarket Gamma poll failed: HTTP 500")
        sent = drain(self.queue)
        self.assertEqual(len(sent), 1)
        self.assertIn("test_error_alerts.py", sent[0])

    def test_varying_values_from_one_call_site_alert_once(self):
        for status in (500, 502, 503):
            self.log.error(f"Gamma API error: {status}")
        self.assertEqual(len(drain(self.queue)), 1)

    def test_distinct_call_sites_alert_separately(self):
        self.log.error("first fault")
        self.log.error("second fault")
        self.assertEqual(len(drain(self.queue)), 2)

    def test_telegram_failures_and_routine_logs_are_not_sent(self):
        self.log.error("Failed to send Telegram message: 429", extra=NO_TELEGRAM)
        self.log.warning("Uncertain Telegram delivery")
        self.log.info("Polled 3 live Under markets")
        self.assertEqual(drain(self.queue), [])

    def test_a_fault_re_alerts_after_the_cooldown(self):
        self.log.error("still broken")
        self.handler.last_sent.clear()
        self.log.error("still broken")
        self.assertEqual(len(drain(self.queue)), 2)

    def test_a_flood_is_bounded_by_the_queue(self):
        small = asyncio.Queue(maxsize=2)
        flooder = ErrorAlertHandler(small)
        for i in range(10):
            flooder.emit(logging.LogRecord("x", logging.ERROR, f"/app/f{i}.py", i, "boom", None, None))
        self.assertEqual(small.qsize(), 2)


if __name__ == "__main__":
    unittest.main()
