"""
Regression test: WebSocket disconnects were swallowed and then resurfaced as bogus
Telegram alerts.

asyncio.wait() parks a failed task's exception on the task object. run() never called
.result() on the finished tasks, so a ConnectionClosed never reached the
`except websockets.ConnectionClosed` below it -- the socket just quietly reconnected
with nothing logged. Worse, asyncio logs an unretrieved exception at ERROR when the
task is garbage collected, and ErrorAlertHandler is installed on the *root* logger, so
that turned into a "SOCCER MONITOR ERROR" in Telegram citing an asyncio internal
file:line instead of the disconnect.

Verifies a dropped connection is caught and logged as the reconnect it is.
Run: python tests/test_ws_disconnect.py
"""
import asyncio
import gc
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import websockets

import soccer_monitor
from soccer_monitor import AlertEngine, MarketInfo, OrderBookMonitor


class ClosingWebsocket:
    """Connects and subscribes, then drops the connection mid-stream."""

    def __init__(self):
        self.subscribed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, payload):
        self.subscribed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise websockets.ConnectionClosed(None, None)


class Recorder(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


async def main():
    recorder = Recorder()
    root = logging.getLogger()
    root.addHandler(recorder)

    original_connect = websockets.connect
    original_delay = soccer_monitor.WS_RECONNECT_DELAY
    websockets.connect = lambda *a, **k: ClosingWebsocket()
    soccer_monitor.WS_RECONNECT_DELAY = 0.01

    try:
        monitor = OrderBookMonitor(AlertEngine(session=None), None,
                                   asyncio.Event(), runtime_config=None)
        monitor.monitored_markets = {
            "TOKEN": MarketInfo(
                event_title="Arsenal vs. Chelsea",
                event_slug="arsenal-chelsea",
                league="Premier League",
                market_question="Will there be over 2.5 goals?",
                under_token_id="TOKEN",
                fixture_id=1,
            )
        }

        task = asyncio.create_task(monitor.run())
        await asyncio.sleep(0.1)  # long enough for several connect/drop cycles
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    finally:
        websockets.connect = original_connect
        soccer_monitor.WS_RECONNECT_DELAY = original_delay
        root.removeHandler(recorder)

    gc.collect()  # an unretrieved exception would be reported here
    await asyncio.sleep(0)

    messages = [r.getMessage() for r in recorder.records]

    closed = [m for m in messages if "WebSocket connection closed" in m]
    assert closed, (
        "the disconnect was swallowed: run() must retrieve the finished task's "
        f"exception so the handler sees it. Logged instead: {messages}")

    unretrieved = [m for m in messages if "never retrieved" in m]
    assert not unretrieved, f"asyncio reported an unretrieved exception: {unretrieved}"

    errors = [r.getMessage() for r in recorder.records if r.levelno >= logging.ERROR]
    assert not errors, f"a plain reconnect must not page anyone: {errors}"

    print(f"OK: {len(closed)} disconnect(s) caught and logged as reconnects, no ERROR raised")


if __name__ == "__main__":
    asyncio.run(main())
