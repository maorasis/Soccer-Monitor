"""Telegram delivery, API health tracking and market-title parsing for the live monitor.

These helpers record the results of calls the daemon already makes; nothing here
probes an upstream on its own.
"""

import asyncio
import html
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import aiohttp
from dotenv import load_dotenv

# Variables that are already set (for example by systemd or a test) take precedence.
load_dotenv(Path(__file__).with_name(".env"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
# A failing call site alerts at most once per this many seconds.
ERROR_ALERT_COOLDOWN = int(os.getenv("ERROR_ALERT_COOLDOWN", "3600"))
# An upstream that keeps failing this long pushes one Telegram alert. Below it the fault
# is logged and shown in the status menu but not pushed: a blip on Polymarket's side is
# not something to be woken for, a 30-minute outage during a match is.
SUSTAINED_OUTAGE_ALERT = int(os.getenv("SUSTAINED_OUTAGE_ALERT", "1800"))

logger = logging.getLogger("SoccerMonitor")

# Attach to a log call that reports a failure in sending to Telegram, so that failure
# does not itself get forwarded to Telegram.
NO_TELEGRAM = {"no_telegram": True}

# The faults that are the network's rather than ours. Anything else arriving at a broad
# handler is a bug in this code and still pages at once.
NETWORK_ERRORS = (aiohttp.ClientError, asyncio.TimeoutError)

DEP_GAMMA = "Polymarket Gamma"
DEP_BOOKS = "Polymarket order books"
DEP_TELEGRAM = "Telegram"
# Display order for the status menu.
DEPENDENCIES = (DEP_GAMMA, DEP_BOOKS, DEP_TELEGRAM)


class ErrorAlertHandler(logging.Handler):
    """Queues every ERROR-level log record for delivery to Telegram.

    Deduped by call site rather than message text: a fault that repeats with a changing
    value (a status code, an exception string) still alerts once per cooldown. Sending
    happens in error_alert_loop -- emit() is called from synchronous logging code and
    must not block or await.
    """

    def __init__(self, queue: "asyncio.Queue[str]"):
        super().__init__(level=logging.ERROR)
        self.queue = queue
        self.last_sent: Dict[str, float] = {}

    def emit(self, record: logging.LogRecord):
        if getattr(record, "no_telegram", False):
            return

        site = f"{record.pathname}:{record.lineno}"
        now = time.time()
        if now - self.last_sent.get(site, 0) < ERROR_ALERT_COOLDOWN:
            return
        self.last_sent[site] = now

        message = (
            f"⚠️ <b>SOCCER MONITOR ERROR</b>\n\n"
            f"{html.escape(record.getMessage())}\n\n"
            f"<i>{html.escape(record.name)} — "
            f"{html.escape(os.path.basename(record.pathname))}:{record.lineno}</i>"
        )
        try:
            self.queue.put_nowait(message)
        except asyncio.QueueFull:
            pass  # already backed up with errors; one more adds nothing


async def error_alert_loop(sender: "TelegramSender", queue: "asyncio.Queue[str]"):
    """Deliver what ErrorAlertHandler queued."""
    while True:
        await sender.send_text(await queue.get())


def _ago(ts: Optional[float]) -> str:
    """"3h ago" / "2m ago" -- coarse is the point, this is a glance, not a log."""
    if ts is None:
        return "never"
    secs = max(0, int(time.time() - ts))
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


@dataclass
class DependencyHealth:
    ok: Optional[bool] = None  # None until the daemon has actually called it
    last_ok: Optional[float] = None
    last_fail: Optional[float] = None
    detail: str = ""
    failing_since: Optional[float] = None
    alerted: bool = False


class ApiHealth:
    """Last-known state of each upstream, recorded from calls the daemon already makes.

    status_lines() reads recorded state; it never probes.
    """

    def __init__(self, dependencies=DEPENDENCIES):
        self.dependencies = dependencies
        self.deps: Dict[str, DependencyHealth] = {}

    def _dep(self, name: str) -> DependencyHealth:
        return self.deps.setdefault(name, DependencyHealth())

    def ok(self, name: str, detail: str = ""):
        dep = self._dep(name)
        dep.ok = True
        dep.last_ok = time.time()
        dep.detail = detail
        dep.failing_since = None
        dep.alerted = False

    def fail(self, name: str, detail: str,
             exc: Optional[BaseException] = None) -> Dict[str, bool]:
        """Record a failure and return the `extra` for the log call reporting it.

        NO_TELEGRAM while the fault still looks transient, so an upstream blip does not
        push. An empty dict -- which lets ErrorAlertHandler through -- once the outage
        has lasted SUSTAINED_OUTAGE_ALERT, and immediately when `exc` is one of ours
        rather than the network's, because silencing our own bugs is not the goal.
        """
        now = time.time()
        dep = self._dep(name)
        dep.ok = False
        dep.last_fail = now
        dep.detail = detail
        if dep.failing_since is None:
            dep.failing_since = now

        if exc is not None and not isinstance(exc, NETWORK_ERRORS):
            return {}

        if not dep.alerted and now - dep.failing_since >= SUSTAINED_OUTAGE_ALERT:
            dep.alerted = True  # one push per outage, not one per poll
            return {}

        return NO_TELEGRAM

    def status_lines(self) -> List[str]:
        """One display line per dependency, safe to drop into a Markdown message."""
        lines = []
        for name in self.dependencies:
            dep = self.deps.get(name)
            if dep is None or dep.ok is None:
                lines.append(f"▫️ {name} — not called yet")
                continue

            # Details carry status codes and exception text; a stray _ or * would break
            # the whole message's Markdown parse.
            detail = re.sub(r'[_*`\[\]]', '', dep.detail)
            if dep.ok:
                extra = f" · {detail}" if detail else ""
                lines.append(f"✅ {name} — ok {_ago(dep.last_ok)}{extra}")
            else:
                down = _ago(dep.failing_since).replace(" ago", "").replace("just now", "<1m")
                lines.append(
                    f"❌ {name} — {detail or 'failing'}, down {down} "
                    f"(ok {_ago(dep.last_ok)})"
                )
        return lines


def parse_game_total_line(question: str) -> Optional[float]:
    """Return the goal line for a whole-game total, or None for any other market.

    Whole-game totals are titled "<Home> vs. <Away>: O/U <line>". Anything else
    after the colon is a per-team, per-half or corners line ("Viking FK O/U 1.5",
    "1st Half O/U 2.5", "O/U 3.5 Corners").
    """
    match = re.fullmatch(r'o/u (\d+(?:\.\d+)?)', question.lower().split(': ')[-1].strip())
    return float(match.group(1)) if match else None


class TelegramSender:
    """Send HTML messages to the configured chat and record Telegram health."""

    def __init__(self, session: aiohttp.ClientSession, health: Optional[ApiHealth] = None,
                 bot_token: Optional[str] = None, chat_id: Optional[str] = None):
        self.health = health or ApiHealth()
        self.session = session
        self.bot_token = TELEGRAM_BOT_TOKEN if bot_token is None else bot_token
        self.chat_id = TELEGRAM_CHAT_ID if chat_id is None else chat_id

    async def send_text(self, message: str, *, report_uncertain: bool = False,
                        reply_markup: Optional[dict] = None) -> Optional[bool]:
        """Send HTML to the configured chat.

        Returns True on success and False on an explicit failure. With report_uncertain,
        a timeout or server error returns None: Telegram may already have delivered it.
        """
        if not (self.bot_token and self.chat_id):
            logger.info("Telegram credentials not configured. Skipping Telegram notification.")
            return False

        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "HTML",
            }
            if reply_markup is not None:
                payload["reply_markup"] = reply_markup
            async with self.session.post(url, json=payload) as response:
                if response.status != 200:
                    self.health.fail(DEP_TELEGRAM, f"HTTP {response.status}")
                    logger.error(f"Failed to send Telegram message: {response.status}", extra=NO_TELEGRAM)
                    if report_uncertain and (response.status >= 500 or response.status == 408):
                        return None
                    return False
                self.health.ok(DEP_TELEGRAM)
                return True
        except Exception as e:
            # The exception text can contain the request URL, which holds the bot token.
            detail = f"{type(e).__name__}: {e}".replace(self.bot_token, "[REDACTED]")
            self.health.fail(DEP_TELEGRAM, detail, exc=e)
            logger.error(f"Exception sending Telegram message: {detail}", extra=NO_TELEGRAM)
            return None if report_uncertain else False
