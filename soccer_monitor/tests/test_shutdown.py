"""SIGTERM must stop the daemon promptly and gracefully.

systemd sends SIGTERM on stop and restart. main() cancels its gathered tasks on the
signal; without that the process hung until systemd's stop timeout and was SIGKILLed.
"""
import asyncio
import logging
import os
import signal
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import live_under_monitor
from config_manager import RuntimeConfig


class ShutdownTests(unittest.TestCase):
    def test_sigterm_cancels_every_task(self):
        cancelled = []

        async def forever(name):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(name)

        async def polls(*args, **kwargs):
            # The handler is installed before main awaits its tasks, so this is safe.
            os.kill(os.getpid(), signal.SIGTERM)
            await forever("polls")

        root = logging.getLogger()
        handlers = list(root.handlers)
        with TemporaryDirectory() as tmp, \
                patch.object(live_under_monitor, "TELEGRAM_BOT_TOKEN", "token"), \
                patch.object(live_under_monitor, "TELEGRAM_CHAT_ID", "1"), \
                patch.object(live_under_monitor, "RuntimeConfig", lambda: RuntimeConfig(Path(tmp) / "config.json")), \
                patch.object(live_under_monitor, "run_polls", polls), \
                patch.object(live_under_monitor, "run_daily_reports", lambda *a: forever("reports")), \
                patch.object(live_under_monitor, "start_bot_polling", lambda *a, **k: forever("bot")):
            try:
                asyncio.run(asyncio.wait_for(live_under_monitor.main(), 10))
            finally:
                root.handlers = handlers
        self.assertEqual(sorted(cancelled), ["bot", "polls", "reports"])


if __name__ == "__main__":
    unittest.main()
