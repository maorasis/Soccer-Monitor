"""
Websocket-based soccer Under price-dip monitor and shared alert infrastructure.

This daemon discovers markets using Polymarket and API-Football fixtures, then
tracks sell-side order books over a websocket. live_under_monitor imports its
health, notification and market-title helpers but implements a separate polling
strategy and persistent alert history.
"""

import asyncio
import json
import logging
import os
import re
import signal
import time
import difflib
import html
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from dataclasses import dataclass
from typing import Dict, List, Optional

# pyrefly: ignore [missing-import]
import aiohttp
# pyrefly: ignore [missing-import]
import websockets
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv

from config_manager import RuntimeConfig
from bot_ui import start_bot_polling

# Load environment variables
load_dotenv()

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
ASK_PRICE_THRESHOLD = float(os.getenv("ASK_PRICE_THRESHOLD", "0.10"))
ASK_PRICE_MIN = float(os.getenv("ASK_PRICE_MIN", "0.01"))
POLL_INTERVAL_MARKETS = int(os.getenv("POLL_INTERVAL_MARKETS", "900"))
WS_RECONNECT_DELAY = int(os.getenv("WS_RECONNECT_DELAY", "5"))
# A failing call site alerts at most once per this many seconds.
ERROR_ALERT_COOLDOWN = int(os.getenv("ERROR_ALERT_COOLDOWN", "3600"))
# An upstream that keeps failing this long pushes one Telegram alert. Below it the fault
# is logged and shown in the status menu but not pushed: a blip on Polymarket's side is
# not something to be woken for, a 30-minute outage during a match is.
SUSTAINED_OUTAGE_ALERT = int(os.getenv("SUSTAINED_OUTAGE_ALERT", "1800"))
# A failed daily-schedule fetch is retried on the next scan, up to this many attempts.
# Each attempt spends 2 of API-Football's 100 daily calls, and discovery_loop runs every
# 15 minutes across a 12-hour window, so an unbounded retry would spend the whole quota.
MAX_SCHEDULE_FETCH_ATTEMPTS = int(os.getenv("MAX_SCHEDULE_FETCH_ATTEMPTS", "5"))
# A crossing is confirmed this many seconds later. A market a goal has just settled
# keeps falling: within seconds its asks are swept to the minimum tick (0.001 on these
# markets) or pulled altogether, and neither is a dip worth buying.
ALERT_CONFIRM_DELAY = float(os.getenv("ALERT_CONFIRM_DELAY", "10"))
SIMULATION_MODE = os.getenv("SIMULATION_MODE", "0") == "1"
# Only scan events kicking off within this window of now; a match older than
# KICKOFF_WINDOW_BEFORE_H has finished.
KICKOFF_WINDOW_BEFORE_H = float(os.getenv("KICKOFF_WINDOW_BEFORE_H", "4"))
KICKOFF_WINDOW_AFTER_H = float(os.getenv("KICKOFF_WINDOW_AFTER_H", "1"))

logging.basicConfig(
    level=logging.INFO,
    format='%(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("SoccerMonitor")

# Israel keeps UTC+3 in summer and UTC+2 in winter. A fixed UTC+3 ran the 13:00-01:00
# active window and the midnight alert flush an hour early for roughly half the year.
try:
    ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")
except (ZoneInfoNotFoundError, OSError):
    ISRAEL_TZ = timezone(timedelta(hours=3))
    logger.error(
        "Asia/Jerusalem is unavailable (no tzdata installed?) — falling back to a fixed "
        "UTC+3, which runs an hour off outside daylight saving time."
    )

# Attach to a log call that reports a failure in sending to Telegram, so that failure
# does not itself get forwarded to Telegram.
NO_TELEGRAM = {"no_telegram": True}


class ErrorAlertHandler(logging.Handler):
    """Queues every ERROR-level log record for delivery to Telegram.

    Deduped by call site rather than message text: a fault that repeats with a changing
    value (a status code, an exception string) still alerts once per cooldown. Sending
    happens in error_alert_loop — emit() is called from synchronous logging code and
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
            f"\u26a0\ufe0f <b>SOCCER MONITOR ERROR</b>\n\n"
            f"{html.escape(record.getMessage())}\n\n"
            f"<i>{html.escape(record.name)} \u2014 "
            f"{html.escape(os.path.basename(record.pathname))}:{record.lineno}</i>"
        )
        try:
            self.queue.put_nowait(message)
        except asyncio.QueueFull:
            pass  # already backed up with errors; one more adds nothing


async def error_alert_loop(alert_engine: "AlertEngine", queue: "asyncio.Queue[str]"):
    """Delivers what ErrorAlertHandler queued."""
    while True:
        await alert_engine.send_text(await queue.get())


# The faults that are the network's rather than ours. Anything else arriving at one of
# the broad `except Exception` handlers is a bug in this file and still pages at once.
NETWORK_ERRORS = (aiohttp.ClientError, asyncio.TimeoutError, websockets.WebSocketException)

# Display order for the status menu. Insertion order would depend on which upstream
# happened to answer first.
DEP_API_FOOTBALL = "API-Football"
DEP_GAMMA = "Polymarket Gamma"
DEP_WS = "Market websocket"
DEP_TELEGRAM = "Telegram"
DEPENDENCIES = (DEP_API_FOOTBALL, DEP_GAMMA, DEP_WS, DEP_TELEGRAM)


def _ago(ts: Optional[float]) -> str:
    """"3h ago" / "2m ago" — coarse is the point, this is a glance, not a log."""
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

    Costs no API calls of its own, which is the whole design: every dependency is
    contacted on its own schedule anyway — API-Football daily, Gamma every scan, the
    websocket continuously, Telegram on every send — so health is a by-product of those
    calls. status_lines() reads recorded state; it never probes.
    """

    def __init__(self, dependencies=DEPENDENCIES):
        self.dependencies = dependencies
        self.deps: Dict[str, DependencyHealth] = {}
        self.quota_remaining: Optional[int] = None  # API-Football calls left today

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
        """Records a failure and returns the `extra` for the log call reporting it.

        NO_TELEGRAM while the fault still looks transient, so an upstream blip no longer
        pushes. An empty dict — which lets ErrorAlertHandler through — once the outage
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
            dep.alerted = True  # one push per outage, not one per scan
            return {}

        return NO_TELEGRAM

    def note_quota(self, response):
        """API-Football reports the day's remaining allowance on every response.

        Never raises. This is a diagnostic sitting in the middle of the fetch path, and
        an unreadable header must not become a failed schedule fetch.
        """
        try:
            headers = getattr(response, "headers", None) or {}
            self.quota_remaining = int(headers.get("x-ratelimit-requests-remaining"))
        except (AttributeError, TypeError, ValueError):
            pass

    def status_lines(self) -> List[str]:
        """One display line per dependency, safe to drop into a Markdown message."""
        lines = []
        for name in self.dependencies:
            dep = self.deps.get(name)
            if dep is None or dep.ok is None:
                lines.append(f"\u25ab\ufe0f {name} \u2014 not called yet")
                continue

            # Details carry status codes and exception text; a stray _ or * would break
            # the whole message's Markdown parse.
            detail = re.sub(r'[_*`\[\]]', '', dep.detail)
            if dep.ok:
                extra = f" \u00b7 {detail}" if detail else ""
                lines.append(f"\u2705 {name} \u2014 ok {_ago(dep.last_ok)}{extra}")
            else:
                down = _ago(dep.failing_since).replace(" ago", "").replace("just now", "<1m")
                lines.append(
                    f"\u274c {name} \u2014 {detail or 'failing'}, down {down} "
                    f"(ok {_ago(dep.last_ok)})"
                )

        if self.quota_remaining is not None:
            lines.append(f"\U0001f4b3 API-Football quota: {self.quota_remaining}/100 left today")
        return lines


def parse_game_total_line(question: str) -> Optional[float]:
    """Returns the goal line for a whole-game total, or None for any other market.

    Whole-game totals are titled "<Home> vs. <Away>: O/U <line>". Anything else
    after the colon is a per-team, per-half or corners line ("Viking FK O/U 1.5",
    "1st Half O/U 2.5", "O/U 3.5 Corners").
    """
    match = re.fullmatch(r'o/u (\d+(?:\.\d+)?)', question.lower().split(': ')[-1].strip())
    return float(match.group(1)) if match else None


@dataclass
class MarketInfo:
    """Discovery metadata associated with an Under token and optional fixture."""
    event_title: str
    event_slug: str
    league: str
    market_question: str
    under_token_id: str
    fixture_id: Optional[int] = None


class LiveMatchTracker:
    """Cache API-Football fixtures and fetch scores only for candidate alerts.

    Cached fixtures are filtered against current league settings on read, so a
    Telegram league toggle can take effect without another daily schedule fetch.
    """
    def __init__(self, session: aiohttp.ClientSession, runtime_config: RuntimeConfig,
                 health: Optional["ApiHealth"] = None):
        self.session = session
        self.runtime_config = runtime_config
        self.health = health or ApiHealth()
        self.api_key = API_FOOTBALL_KEY
        self.base_url = "https://v3.football.api-sports.io/"
        self.headers = {'x-apisports-key': self.api_key} if self.api_key else {}
        self.daily_fixtures = []  # every league worldwide; read via active_fixtures()
        self.last_fetch_date = None  # YYYY-MM-DD string, set only once a fetch succeeds
        self.fetch_attempt_date = None  # the day fetch_attempts is counting
        self.fetch_attempts = 0

    async def fetch_daily_schedule(self):
        """Fetches daily schedule for today and tomorrow and filters locally."""
        now_idt = datetime.now(ISRAEL_TZ)

        if SIMULATION_MODE:
            now_utc = datetime.now(timezone.utc)
            self.daily_fixtures = [
                {
                    "fixture_id": 123456,
                    "home_team": "Mock FC",
                    "away_team": "Simulator United",
                    "start_time": now_utc - timedelta(minutes=10) # Started 10 minutes ago
                }
            ]
            logger.info("SIMULATION: Populated mock daily schedule.")
            self.last_fetch_date = now_idt.strftime('%Y-%m-%d')
            return

        if not self.api_key:
            logger.error("API-Football key not configured. Cannot fetch daily schedule.")
            return

        dates_to_fetch = [
            now_idt.strftime('%Y-%m-%d'),
            (now_idt + timedelta(days=1)).strftime('%Y-%m-%d')
        ]

        today = now_idt.strftime('%Y-%m-%d')
        if self.fetch_attempt_date != today:
            self.fetch_attempt_date = today
            self.fetch_attempts = 0
        self.fetch_attempts += 1

        fetched_fixtures = []
        global_fixture_count = 0  # raw count, including entries dropped as malformed
        dates_ok = 0  # dates API-Football actually answered for
        terminal = False  # an errors block is quota or a bad key; retrying cannot help
        will_retry = False

        try:
            for date_str in dates_to_fetch:
                url = f"{self.base_url}fixtures"
                params = {'date': date_str}
                logger.info(f"API-Football: Fetching global daily schedule for {date_str}...")
                
                async with self.session.get(url, headers=self.headers, params=params) as response:
                    if response.status != 200:
                        logger.error(
                            f"API-Football error: {response.status}",
                            extra=self.health.fail(DEP_API_FOOTBALL, f"HTTP {response.status}")
                        )
                        continue

                    self.health.note_quota(response)
                    data = await response.json()
                    
                    # API-Football may return 200 with an errors block inside the JSON
                    api_errors = data.get("errors", {})
                    if api_errors:
                        # Recorded so the status panel shows this red rather than stale
                        # green. The log call keeps its default extra: a spent quota is
                        # our problem, not the network's, and still pages immediately.
                        self.health.fail(DEP_API_FOOTBALL, f"API error: {api_errors}")
                        logger.error(f"API-Football response errors: {api_errors}")
                        terminal = True
                        continue

                    dates_ok += 1
                    self.health.ok(DEP_API_FOOTBALL)
                    fixtures_list = data.get("response", [])
                    global_fixture_count += len(fixtures_list)
                    logger.info(f"Retrieved {len(fixtures_list)} total global fixtures for {date_str}.")

                    for item in fixtures_list:
                        league_id = item.get("league", {}).get("id")
                        fixture_info = item.get("fixture", {})
                        fixture_id = fixture_info.get("id")
                        timestamp = fixture_info.get("timestamp")
                        teams_info = item.get("teams", {})
                        home_name = teams_info.get("home", {}).get("name", "")
                        away_name = teams_info.get("away", {}).get("name", "")

                        if fixture_id and timestamp and home_name and away_name:
                            start_time_utc = datetime.fromtimestamp(timestamp, timezone.utc)
                            fetched_fixtures.append({
                                "fixture_id": fixture_id,
                                "league_id": league_id,
                                "home_team": home_name,
                                "away_team": away_name,
                                "start_time": start_time_utc
                            })

            self.daily_fixtures = fetched_fixtures
            active_count = len(self.active_fixtures())
            will_retry = (not dates_ok and not terminal
                          and self.fetch_attempts < MAX_SCHEDULE_FETCH_ATTEMPTS)
            if active_count:
                logger.info(
                    f"Cached {len(fetched_fixtures)} fixtures, "
                    f"{active_count} in the active leagues."
                )
            elif global_fixture_count:
                # The active leagues are simply idle today — an off-season Saturday looks
                # exactly like this, and API-Football is plainly answering.
                logger.warning(
                    f"None of the {global_fixture_count} fixtures on {' and '.join(dates_to_fetch)} "
                    f"belong to the active leagues. Nothing to monitor today."
                )
            elif will_retry:
                # A transient upstream failure. Say so at WARNING and try again on the
                # next scan rather than staying blind for the rest of the day.
                logger.warning(
                    f"Daily schedule fetch failed (attempt {self.fetch_attempts} of "
                    f"{MAX_SCHEDULE_FETCH_ATTEMPTS}). Retrying on the next scan."
                )
            else:
                # No fixtures for any league worldwide is not an off-season lull, it is a
                # failed fetch — and it leaves the daemon blind until tomorrow, saying so
                # only at INFO. Worth waking someone for.
                logger.error(
                    f"API-Football returned no fixtures at all for {' and '.join(dates_to_fetch)} — "
                    f"no market will be monitored until the next daily fetch. "
                    f"Usually the daily quota."
                )

        except Exception as e:
            logger.error(
                f"Exception fetching daily schedule: {e}",
                extra=self.health.fail(DEP_API_FOOTBALL, f"{type(e).__name__}: {e}", exc=e)
            )
            will_retry = self.fetch_attempts < MAX_SCHEDULE_FETCH_ATTEMPTS

        # Only a day that actually produced a schedule counts as fetched. Marking a
        # failed day as fetched left the daemon blind until tomorrow over a single
        # non-200; retrying without a bound would spend the daily quota instead.
        if not will_retry:
            self.last_fetch_date = today

    def active_fixtures(self) -> list:
        """Cached fixtures belonging to the currently active leagues.

        Filtered on read rather than at fetch time: API-Football returns every
        league in the same response either way, so a league toggled on in Telegram
        takes effect on the next scan instead of the next day.
        """
        if SIMULATION_MODE:
            return self.daily_fixtures
        active = self.runtime_config.get_active_api_ids()
        return [f for f in self.daily_fixtures if f.get("league_id") in active]

    def get_live_team_names(self) -> set:
        """Returns a set of all team names currently playing based on daily schedule."""
        now_utc = datetime.now(timezone.utc)
        live_teams = set()
        active_count = 0
        
        for fixture in self.active_fixtures():
            start_time = fixture["start_time"]
            # Active window: kickoff <= current_time <= kickoff + 150 minutes
            end_time = start_time + timedelta(minutes=150)
            if start_time <= now_utc <= end_time:
                live_teams.add(fixture["home_team"])
                live_teams.add(fixture["away_team"])
                active_count += 1
                
        logger.info(f"Checked daily schedule. Found {active_count} active match(es) right now.")
        return live_teams

    async def fetch_live_score_on_demand(self, fixture_id: int) -> str:
        """Queries API-Football for a single fixture's live score on demand."""
        if SIMULATION_MODE:
            if self.api_key == "SIMULATE_LIMIT":
                return "[Error: API-Football daily quota of 100 calls reached]"
            return "2-1 (65' 2H)"

        if not self.api_key:
            return "N/A (API-Football key not configured)"

        try:
            url = f"{self.base_url}fixtures"
            params = {'id': str(fixture_id)}
            
            async with self.session.get(url, headers=self.headers, params=params) as response:
                if response.status == 429:
                    logger.warning("API-Football returned 429. Daily quota exceeded.")
                    return "[Error: API-Football daily quota of 100 calls reached]"
                
                if response.status != 200:
                    logger.error(
                        f"API-Football score query HTTP error: {response.status}",
                        extra=self.health.fail(DEP_API_FOOTBALL, f"HTTP {response.status}")
                    )
                    return f"[Error: API-Football HTTP {response.status}]"

                self.health.ok(DEP_API_FOOTBALL)

                # Check rate limit headers
                self.health.note_quota(response)
                remaining = response.headers.get('x-ratelimit-requests-remaining')
                if remaining == '0':
                    logger.warning("API-Football requests remaining is 0. Daily quota reached.")
                    return "[Error: API-Football daily quota of 100 calls reached]"

                data = await response.json()
                
                api_errors = data.get("errors", {})
                if api_errors:
                    logger.warning(f"API-Football returned error: {api_errors}")
                    error_str = str(api_errors).lower()
                    if "limit" in error_str or "quota" in error_str or "request" in error_str:
                        return "[Error: API-Football daily quota of 100 calls reached]"
                    return f"[Error: API-Football - {api_errors}]"

                fixtures = data.get("response", [])
                if not fixtures:
                    return "Match not found"

                fixture_data = fixtures[0]
                goals = fixture_data.get("goals", {})
                home_goals = goals.get("home") or 0
                away_goals = goals.get("away") or 0
                status = fixture_data.get("fixture", {}).get("status", {}).get("short", "")
                elapsed = fixture_data.get("fixture", {}).get("status", {}).get("elapsed", "")
                
                return f"{home_goals}-{away_goals} ({elapsed}' {status})"

        except Exception as e:
            logger.error(
                f"Exception fetching live score for fixture {fixture_id}: {e}",
                extra=self.health.fail(DEP_API_FOOTBALL, f"{type(e).__name__}: {e}", exc=e)
            )
            return f"[Error: Exception - {str(e)}]"


class AlertEngine:
    """Send Telegram messages and track websocket-daemon token alerts in memory.

    The websocket history is cleared at the Israel-local date change; the polling
    daemon instead claims tokens in its own SQLite database.
    """
    def __init__(self, session: aiohttp.ClientSession, health: Optional["ApiHealth"] = None):
        self.health = health or ApiHealth()
        self.session = session
        self.bot_token = TELEGRAM_BOT_TOKEN
        self.chat_id = TELEGRAM_CHAT_ID
        self.last_alert_time: Dict[str, float] = {}
        self.last_flush_date: Optional[str] = None

    def clear_alert_history(self, date_str: str):
        self.last_alert_time.clear()
        self.last_flush_date = date_str
        logger.info(f"New day detected: Alert history flushed for {date_str} at midnight Israel time.")

    async def send_alert(self, market: MarketInfo, current_ask: float, score: str):
        if market.under_token_id in self.last_alert_time:
            logger.debug(f"Alert for {market.event_title} already sent today.")
            return

        self.last_alert_time[market.under_token_id] = time.time()

        message = (
            f"🚨 <b>UNDER PRICE DIP DETECTED</b>\n\n"
            f"⚽ <b>{html.escape(market.event_title)}</b>\n"
            f"🏆 {html.escape(market.league)}\n"
            f"📊 Market: {html.escape(market.market_question)}\n"
            f"💰 Lowest ASK: ${current_ask:.3f}\n"
            f"🎯 Score: {html.escape(score)}\n"
            f"🔗 https://polymarket.com/event/{market.event_slug}"
        )

        logger.info(f"--- ALERT TRIGGERED ---\n{message}\n-----------------------")

        if await self.send_text(message):
            logger.info("Telegram alert sent successfully.")

    async def send_text(self, message: str, *, report_uncertain: bool = False) -> Optional[bool]:
        """Send HTML to the configured chat. Optionally return None for uncertain delivery."""
        if not (self.bot_token and self.chat_id):
            logger.info("Telegram credentials not configured. Skipping Telegram notification.")
            return False

        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "HTML"
            }
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
            self.health.fail(DEP_TELEGRAM, f"{type(e).__name__}: {e}", exc=e)
            logger.error(f"Exception sending Telegram message: {e}", extra=NO_TELEGRAM)
            return None if report_uncertain else False


class MarketDiscovery:
    """Combine Gamma soccer markets with cached fixtures and team-name mappings.

    A failed scan returns None so the caller can retain the last subscriptions;
    a successful scan may return an empty dictionary.
    """
    def __init__(self, session: aiohttp.ClientSession, live_match_tracker: LiveMatchTracker,
                 runtime_config: RuntimeConfig, health: Optional["ApiHealth"] = None):
        self.health = health or ApiHealth()
        self.session = session
        self.live_match_tracker = live_match_tracker
        self.runtime_config = runtime_config
        self.base_url = "https://gamma-api.polymarket.com/events/keyset"

    def load_team_mapping(self) -> Dict[str, str]:
        if os.path.exists("team_mapping.json"):
            try:
                with open("team_mapping.json", "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading team_mapping.json: {e}")
        return {}

    def _event_has_live_team(self, event_title: str, live_teams: set, team_mapping: Dict[str, str]) -> bool:
        """Checks if any mapped team in the event title matches a live team exactly."""
        if not live_teams:
            return False
            
        # Strip out side-market suffixes (e.g., " - Exact Score")
        clean_title = event_title.split(' - ')[0]
            
        # Split "Team A vs. Team B"
        parts = clean_title.split(' vs. ')
        if len(parts) != 2:
            parts = clean_title.split(' vs ')
            
        if len(parts) != 2:
            return False
        
        for part in parts:
            poly_team = part.strip()
            
            if poly_team not in team_mapping:
                logger.debug(f"Unmapped Polymarket team checked: '{poly_team}'")
                
            # Try to get the API-Football equivalent from our deterministic mapping
            api_team = team_mapping.get(poly_team, poly_team)
            
            if api_team in live_teams:
                return True
                
        return False

    def _league_for_tags(self, tags: List[str]) -> str:
        """Resolve a Gamma event's tags to a supported league name.

        Picking "the first tag that is not Soccer/Sports/Featured/Games" relied on tag
        order, which Gamma does not guarantee. Landing on a non-league tag made
        match_league_name return "", so get_threshold silently fell back to the global
        threshold and the whole per-league threshold feature did nothing for that
        market. Every event that reaches here has already matched an active league's
        tag, so the scan below resolves.
        """
        for tag in tags:
            matched = self.runtime_config.match_league_name(tag)
            if matched:
                return matched

        # Nothing recognised: keep a readable name for the alert text. get_threshold
        # will not match it, which is correct -- there is no per-league value to use.
        league_tags = [t for t in tags
                       if t not in ('Soccer', 'Sports', 'Featured', 'Games')]
        return league_tags[0] if league_tags else "General Soccer"

    def find_matching_fixture_id(self, event_title: str, team_mapping: Dict[str, str]) -> Optional[int]:
        """Matches Polymarket event title against cached daily fixtures deterministically using the mapping."""
        fixtures = self.live_match_tracker.active_fixtures()
        if not fixtures:
            return None

        # Strip out side-market suffixes (e.g., " - Exact Score")
        clean_title = event_title.split(' - ')[0]

        parts = clean_title.split(' vs. ')
        if len(parts) != 2:
            parts = clean_title.split(' vs ')
        if len(parts) != 2:
            return None

        poly_home = parts[0].strip()
        poly_away = parts[1].strip()
        
        api_home = team_mapping.get(poly_home, poly_home)
        api_away = team_mapping.get(poly_away, poly_away)

        for fixture in fixtures:
            f_home = fixture["home_team"]
            f_away = fixture["away_team"]

            # Exact match (order doesn't matter)
            if (api_home == f_home and api_away == f_away) or (api_home == f_away and api_away == f_home):
                return fixture["fixture_id"]

        return None

    async def fetch_markets(self, live_teams: set) -> Optional[Dict[str, MarketInfo]]:
        """Returns the live market set, or None if the scan failed.

        None means "I do not know", not "there are no markets": a fault partway
        through pagination leaves a truncated dict that looks authoritative to
        set_markets, which would unsubscribe the websocket from live games.
        """
        if SIMULATION_MODE:
            logger.info("SIMULATION: Returning mock markets.")
            return {
                "MOCK_TOKEN_1": MarketInfo(
                    event_title="Mock FC vs Simulator United",
                    event_slug="mock-fc-simulator-united",
                    league="Simulation League",
                    market_question="Will there be over 2.5 goals?",
                    under_token_id="MOCK_TOKEN_1",
                    fixture_id=123456
                )
            }

        logger.info("Fetching active soccer markets from Gamma API...")
        team_mapping = self.load_team_mapping()
        markets_dict = {}
        skipped_not_live = 0
        
        now = datetime.now(timezone.utc)
        start_time_min = now - timedelta(hours=KICKOFF_WINDOW_BEFORE_H)
        start_time_max = now + timedelta(hours=KICKOFF_WINDOW_AFTER_H)

        try:
            limit = 100
            cursor = None
            while True:
                params = {
                    'closed': 'false',
                    'tag_slug': 'soccer',
                    'limit': str(limit),
                    'start_time_min': start_time_min.strftime('%Y-%m-%dT%H:%M:%SZ'),
                    'start_time_max': start_time_max.strftime('%Y-%m-%dT%H:%M:%SZ')
                }
                # Keyset, not offset: Gamma rejects offset > 2000 with a 422.
                if cursor:
                    params['after_cursor'] = cursor

                async with self.session.get(self.base_url, params=params) as response:
                    if response.status != 200:
                        body = (await response.text())[:300]
                        logger.error(
                            f"Gamma API error: {response.status} - {body}",
                            extra=self.health.fail(DEP_GAMMA, f"HTTP {response.status}")
                        )
                        return None

                    self.health.ok(DEP_GAMMA)
                    payload = await response.json()
                    events = payload.get('events') or []
                    cursor = payload.get('next_cursor')
                    if not events:
                        break

                    allowed_leagues = self.runtime_config.get_active_polymarket_tags()
                    
                    for event in events:
                        tags = [tag.get('label', '') for tag in event.get('tags', [])]
                        lower_tags = [t.lower() for t in tags]
                        
                        if not any(al in lower_tags for al in allowed_leagues):
                            continue

                        event_title = event.get('title', 'Unknown Match')
                        if not self._event_has_live_team(event_title, live_teams, team_mapping):
                            skipped_not_live += 1
                            continue

                        league = self._league_for_tags(tags)
                        
                        for market in event.get('markets', []):
                            line = parse_game_total_line(market.get('question', ''))
                            if line is not None:
                                # Only monitor 0.5 and 1.5 O/U markets as requested by user
                                if line not in (0.5, 1.5):
                                    continue
                                
                                # Removed Market-level gameStartTime check as API-Football is now source of truth
                                
                                tokens = market.get('clobTokenIds')
                                outcomes = market.get('outcomes')
                                if not tokens or not outcomes:
                                    continue
                                
                                try:
                                    token_list = tokens if isinstance(tokens, list) else json.loads(tokens)
                                    outcome_list = outcomes if isinstance(outcomes, list) else json.loads(outcomes)
                                    if len(token_list) == len(outcome_list):
                                        under_idx = -1
                                        for idx, outcome in enumerate(outcome_list):
                                            if outcome.lower() in ("under", "no"):
                                                under_idx = idx
                                                break
                                        
                                        if under_idx != -1:
                                            under_token = token_list[under_idx]
                                            fixture_id = self.find_matching_fixture_id(event_title, team_mapping)
                                            markets_dict[under_token] = MarketInfo(
                                                event_title=event.get('title', 'Unknown Match'),
                                                event_slug=event.get('slug', ''),
                                                league=league,
                                                market_question=market.get('question', ''),
                                                under_token_id=under_token,
                                                fixture_id=fixture_id
                                            )
                                except json.JSONDecodeError as e:
                                    logger.warning(f"Malformed JSON in market '{market.get('question', '?')}': {e}")
                    
                    if not cursor:
                        break

        except Exception as e:
            logger.error(
                f"Exception fetching markets: {e}",
                extra=self.health.fail(DEP_GAMMA, f"{type(e).__name__}: {e}", exc=e)
            )
            return None
            
        logger.info(
            f"Found {len(markets_dict)} live Over/Under markets "
            f"(skipped {skipped_not_live} events not currently live according to API-Football)."
        )
        return markets_dict



class SimulatedWebsocket:
    """Emit a small synthetic book/update sequence for SIMULATION_MODE."""
    """A mock websocket to simulate price drops for the mock market."""
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    async def send(self, message):
        pass

    async def __aiter__(self):
        # 1. Send an initial book snapshot above threshold
        yield json.dumps({
            "event_type": "book",
            "asset_id": "MOCK_TOKEN_1",
            "asks": [{"price": "0.15", "size": "100"}]
        })
        await asyncio.sleep(5)
        # 2. Simulate a price dip to trigger alert
        logger.info("SIMULATION: Sending mock price drop to 0.05...")
        yield json.dumps({
            "event_type": "price_change",
            "price_changes": [
                {"asset_id": "MOCK_TOKEN_1", "side": "SELL", "price": "0.05", "size": "50"}
            ]
        })
        # 3. Wait a bit, then simulate price going back up
        await asyncio.sleep(5)
        logger.info("SIMULATION: Removing mock price drop...")
        yield json.dumps({
            "event_type": "price_change",
            "price_changes": [
                {"asset_id": "MOCK_TOKEN_1", "side": "SELL", "price": "0.05", "size": "0"}
            ]
        })
        # 4. Stay connected idly
        while True:
            await asyncio.sleep(60)

class OrderBookMonitor:
    """Maintain ask books and detect confirmed downward threshold crossings.

    Tokens arm after an observed ask at or above their threshold. Confirmation runs
    concurrently with websocket reads, allowing subsequent updates to invalidate a
    dip; score checks suppress Under outcomes already defeated by the score.
    """
    def __init__(self, alert_engine: AlertEngine, live_match_tracker: LiveMatchTracker,
                 markets_changed_event: asyncio.Event, runtime_config: RuntimeConfig,
                 health: Optional["ApiHealth"] = None):
        self.health = health or ApiHealth()
        self.ws_url = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
        self.alert_engine = alert_engine
        self.live_match_tracker = live_match_tracker
        self.runtime_config = runtime_config
        self.monitored_markets: Dict[str, MarketInfo] = {}
        self.book_state: Dict[str, Dict[float, float]] = {} # token_id -> {price: size}
        # Tokens seen at or above their threshold at least once. A market that is
        # already below the line when we first subscribe (markets are discovered up
        # to POLL_INTERVAL_MARKETS after kickoff, so that is the usual case for a
        # game starting just under it) never fell there while we were watching, so
        # it is not a dip. Survives reconnects on purpose, unlike book_state.
        self.armed: set = set()
        # Tokens inside their confirmation wait, and the tasks running it. asyncio holds
        # only a weak reference to a running task, so the set is what keeps them alive.
        self.confirming: set = set()
        self.dip_tasks: set = set()
        self._idle_logged = False  # so "No markets to monitor" is said once, not every 10s
        self.markets_lock = asyncio.Lock()
        self.markets_changed_event = markets_changed_event

    async def set_markets(self, markets: Dict[str, MarketInfo]):
        """Replace discovery metadata and signal a reconnect only if tokens change."""
        async with self.markets_lock:
            # The socket subscribes by token id alone, so only a changed key set is
            # worth a reconnect. Firing unconditionally tore down a healthy connection
            # on every discovery poll and lost the book updates sent while it came back.
            # A metadata-only change needs no reconnect: process_dip reads
            # monitored_markets under this lock, so it already sees the new values.
            changed = markets.keys() != self.monitored_markets.keys()
            self.monitored_markets = markets
        if changed:
            self.markets_changed_event.set()

    async def process_dip(self, token_id: str, current_ask: float):
        """Confirm an armed crossing, check its score, and send at most one daily alert."""
        async with self.markets_lock:
            market = self.monitored_markets.get(token_id)
        if not market:
            return

        if token_id not in self.armed:
            # Below the line since the first book we saw. Wait for it to trade back
            # above the threshold before a move below counts as a dip.
            return

        # Every dip tick lands here, but a token alerts at most once a day. Check that
        # before spending an API-Football call: the free plan allows only 100 a day.
        if token_id in self.alert_engine.last_alert_time:
            return

        if token_id in self.confirming:
            # This crossing is already waiting out its confirmation. Every further tick
            # below the line belongs to the same dip, not to a new one.
            return
        self.confirming.add(token_id)
        try:
            confirmed_ask = await self._confirm_dip(token_id)
        finally:
            self.confirming.discard(token_id)
        if confirmed_ask is None:
            return
        current_ask = confirmed_ask

        score = "N/A"
        if market.fixture_id:
            score = await self.live_match_tracker.fetch_live_score_on_demand(market.fixture_id)
        else:
            logger.warning(f"No fixture ID found to query score for: {market.event_title}")
            score = "Fixture not mapped"

        # Check for dead market threshold protection
        if score and not score.startswith("[Error") and score != "N/A" and score != "Fixture not mapped":
            # Parse goals scored so far
            score_match = re.match(r'^(\d+)-(\d+)', score)
            if score_match:
                home_goals = int(score_match.group(1))
                away_goals = int(score_match.group(2))
                total_goals = home_goals + away_goals
                
                # Parse goal threshold from question
                threshold = parse_game_total_line(market.market_question)
                if threshold is not None:
                    if total_goals >= threshold:
                        # Goals only go up, so this market stays dead for the rest of the
                        # day. Record it like a sent alert so later ticks stop re-querying.
                        self.alert_engine.last_alert_time[token_id] = time.time()
                        logger.info(
                            f"Suppressing false dip alert for {market.event_title} ({market.market_question}). "
                            f"Current score is {score} (total {total_goals} goals), which meets/exceeds the "
                            f"threshold of {threshold}. Market is mathematically dead (Under outcome cannot win)."
                        )
                        return

        await self.alert_engine.send_alert(market, current_ask, score)

    async def _confirm_dip(self, token_id: str) -> Optional[float]:
        """Waits out ALERT_CONFIRM_DELAY, then returns the ask to alert on, or None.

        A resolved market shows up two ways in the book, both seen in live Polymarket
        data: the losing side is swept down to the minimum tick (0.001 there, well under
        ASK_PRICE_MIN), or its asks are pulled and the book goes empty. Either way there
        is nothing left to buy, so the crossing was the market dying, not a dip.
        """
        await asyncio.sleep(ALERT_CONFIRM_DELAY)

        book = self.book_state.get(token_id)
        if not book:
            logger.info(
                f"Dropping dip alert for {token_id}: every ask was pulled within "
                f"{ALERT_CONFIRM_DELAY:.0f}s. Market is resolved, not cheap."
            )
            return None

        lowest_ask = min(book)
        if lowest_ask <= ASK_PRICE_MIN:
            logger.info(
                f"Dropping dip alert for {token_id}: ask fell to ${lowest_ask:.3f} within "
                f"{ALERT_CONFIRM_DELAY:.0f}s. Market is resolved, not cheap."
            )
            return None

        return lowest_ask

    async def _check_ask(self, asset_id: str):
        """Arm on an ask at or above the threshold, alert on one below it."""
        if not self.book_state.get(asset_id):
            # An empty book is no price at all. Treating the 1.0 default as
            # above-threshold would arm a token whose asks were merely pulled.
            return

        lowest_ask = min(self.book_state[asset_id].keys())

        market = self.monitored_markets.get(asset_id)
        threshold = ASK_PRICE_THRESHOLD
        if market:
            threshold = self.runtime_config.get_threshold(market.league)

        if lowest_ask >= threshold:
            self.armed.add(asset_id)
        elif ASK_PRICE_MIN < lowest_ask:
            # Off the read loop on purpose: process_dip waits ALERT_CONFIRM_DELAY before
            # sending, and awaiting it here would stall the very book updates it waits for.
            task = asyncio.create_task(self.process_dip(asset_id, lowest_ask))
            self.dip_tasks.add(task)
            task.add_done_callback(self.dip_tasks.discard)

    async def _process_event(self, data: dict):
        """Apply a full book snapshot or sell-side level changes to cached asks."""
        if not isinstance(data, dict):
            return
            
        event_type = data.get("event_type")
        
        if event_type == "book":
            # Full snapshot
            asset_id = data.get("asset_id")
            asks = data.get("asks", [])
            
            if asset_id:
                # A snapshot with no asks means the book emptied, not that there is
                # nothing to say. Skipping it used to leave the previous levels in
                # place, and the removed low ask would then fire a dip alert on a
                # price that no longer exists.
                self.book_state[asset_id] = {float(ask.get("price", "1.0")): float(ask.get("size", "0.0")) for ask in asks}
                await self._check_ask(asset_id)

        elif event_type == "price_change":
            # Incremental update
            changes = data.get("price_changes", [])
            for change in changes:
                asset_id = change.get("asset_id")
                side = change.get("side")
                price = float(change.get("price", "1.0"))
                size = float(change.get("size", "0.0"))
                
                if side == "SELL": # Asks
                    if asset_id not in self.book_state:
                        self.book_state[asset_id] = {}
                        
                    if size > 0:
                        self.book_state[asset_id][price] = size
                    else:
                        self.book_state[asset_id].pop(price, None)
                        
                    await self._check_ask(asset_id)

    async def _handle_message(self, message: str):
        try:
            data = json.loads(message)
            if isinstance(data, list):
                for item in data:
                    await self._process_event(item)
            else:
                await self._process_event(data)
        except Exception as e:
            logger.error(f"Error handling WS message: {e}")

    async def _read_ws(self, ws):
        async for message in ws:
            await self._handle_message(message)

    async def run(self):
        """Subscribe to current tokens, reconnect on changes, and discard stale books."""
        while True:
            async with self.markets_lock:
                if not self.monitored_markets:
                    have_markets = False
                else:
                    have_markets = True
                    token_ids = list(self.monitored_markets.keys())

            if not have_markets:
                # Say it once per idle stretch. At one line every 10 seconds this filled
                # the journal through the whole overnight gap for no added information.
                if not self._idle_logged:
                    logger.info("No markets to monitor. Waiting...")
                    self._idle_logged = True
                await asyncio.sleep(10)
                continue

            self._idle_logged = False
            
            subscription_message = {
                "assets_ids": token_ids,
                "type": "market",
                "custom_feature_enabled": True
            }

            self.markets_changed_event.clear()

            try:
                logger.info(f"Connecting to WS with {len(token_ids)} tokens...")
                
                if SIMULATION_MODE:
                    logger.info("SIMULATION: Using SimulatedWebsocket.")
                    ws_context = SimulatedWebsocket()
                else:
                    ws_context = websockets.connect(self.ws_url, ping_interval=10, ping_timeout=10)

                async with ws_context as ws:
                    await ws.send(json.dumps(subscription_message))
                    logger.info("Subscribed to market channels.")
                    self.health.ok(DEP_WS, f"{len(token_ids)} tokens")

                    # Anything cached from before this connection is stale: level
                    # removals that happened during the gap were never delivered, so a
                    # withdrawn low ask would survive the reconnect and alert on a price
                    # that is gone. This also drops books for tokens that set_markets
                    # has since dropped, which otherwise accumulate for the process's
                    # lifetime.
                    self.book_state.clear()
                    
                    ws_task = asyncio.create_task(self._read_ws(ws))
                    event_task = asyncio.create_task(self.markets_changed_event.wait())
                    tasks = [ws_task, event_task]

                    try:
                        done, _ = await asyncio.wait(
                            tasks,
                            return_when=asyncio.FIRST_COMPLETED
                        )
                    finally:
                        # Cancel and reap both children on every exit path, this
                        # coroutine being cancelled at shutdown included. An orphaned
                        # task's exception is reported by asyncio at ERROR when it is
                        # collected, and ErrorAlertHandler sits on the root logger, so
                        # a plain systemd restart would page as a daemon error.
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)

                    # Re-raise whatever finished the wait. Without this a disconnect
                    # stays parked on the task object and never reaches the handlers
                    # below, so the socket silently reconnects with nothing logged.
                    for task in done:
                        task.result()

                    if self.markets_changed_event.is_set():
                        logger.info("Markets changed, reconnecting WS...")
                        continue # Reconnect
                        
            except websockets.ConnectionClosed as e:
                self.health.fail(DEP_WS, "disconnected", exc=e)
                logger.warning("WebSocket connection closed. Reconnecting...")
            except Exception as e:
                logger.error(
                    f"WebSocket error: {e}. Reconnecting...",
                    extra=self.health.fail(DEP_WS, f"{type(e).__name__}: {e}", exc=e)
                )
                
            await asyncio.sleep(WS_RECONNECT_DELAY)


async def main():
    """Compose discovery, websocket, error delivery and optional Telegram tasks."""
    logger.info("Starting Soccer Over/Under Dip Detection Daemon...")
    
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("⚠️  Telegram credentials not configured — alerts will be logged only.")
    if not API_FOOTBALL_KEY and not SIMULATION_MODE:
        logger.error("❌ API-Football key is REQUIRED for live match detection. Exiting.")
        return
        
    markets_changed_event = asyncio.Event()
    runtime_config = RuntimeConfig()
    api_health = ApiHealth()
    error_queue: "asyncio.Queue[str]" = asyncio.Queue(maxsize=100)
    logging.getLogger().addHandler(ErrorAlertHandler(error_queue))
    
    async with aiohttp.ClientSession() as session:
        live_match_tracker = LiveMatchTracker(session, runtime_config, api_health)
        discovery = MarketDiscovery(session, live_match_tracker, runtime_config, api_health)
        alert_engine = AlertEngine(session, api_health)
        monitor = OrderBookMonitor(alert_engine, live_match_tracker, markets_changed_event,
                                   runtime_config, api_health)
        
        async def discovery_loop():
            while True:
                now_idt = datetime.now(ISRAEL_TZ)
                hour = now_idt.hour
                current_date_str = now_idt.strftime('%Y-%m-%d')

                # Flush alert history at midnight Israel time
                if alert_engine.last_flush_date != current_date_str:
                    alert_engine.clear_alert_history(current_date_str)

                # Active window: 13:00 - 01:00 Israel time (i.e., hour >= 13 OR hour < 1)
                if SIMULATION_MODE or hour >= 13 or hour < 1:
                    # Fetch daily schedule only starting at 13:00 Israel time
                    if SIMULATION_MODE or hour >= 13:
                        if live_match_tracker.last_fetch_date != current_date_str:
                            logger.info("Daily schedule not fetched today. Fetching daily schedule...")
                            await live_match_tracker.fetch_daily_schedule()

                    live_teams = live_match_tracker.get_live_team_names()

                    markets = await discovery.fetch_markets(live_teams)
                    # None = the scan failed. Keep the markets we already have
                    # rather than telling the monitor every game just ended.
                    if markets is not None:
                        await monitor.set_markets(markets)
                    await asyncio.sleep(POLL_INTERVAL_MARKETS)
                else:
                    # Outside match hours — clear markets and sleep until 13:00 Israel time
                    logger.info(f"Outside active hours ({now_idt.strftime('%H:%M')} Israel time). Sleeping until 13:00...")
                    await monitor.set_markets({})

                    # Calculate seconds until 13:00 Israel time today
                    next_start = now_idt.replace(hour=13, minute=0, second=0, microsecond=0)
                    if next_start <= now_idt:
                        next_start += timedelta(days=1)
                    # Subtracting two datetimes that share a tzinfo gives the
                    # wall-clock difference, ignoring the zone. On the night the clocks
                    # change that is an hour short and the daemon would resume at 12:00
                    # local, so compare the two instants in UTC instead.
                    sleep_seconds = (next_start.astimezone(timezone.utc)
                                     - now_idt.astimezone(timezone.utc)).total_seconds()
                    logger.info(f"Resuming in {sleep_seconds/3600:.1f} hours.")
                    await asyncio.sleep(sleep_seconds)
                
        gather_list = [discovery_loop(), monitor.run(), error_alert_loop(alert_engine, error_queue)]
        if TELEGRAM_BOT_TOKEN:
            gather_list.append(start_bot_polling(TELEGRAM_BOT_TOKEN, runtime_config,
                                                TELEGRAM_CHAT_ID, api_health))
            
        tasks = asyncio.gather(*gather_list)

        # Every loop above runs forever, so gather() only ever finished if one raised.
        # Nothing cancelled them on SIGTERM -- aiogram installed a handler that merely
        # stopped its own polling -- so systemd waited out its 90s stop timeout and
        # SIGKILLed the daemon on every restart, and the handler below was dead code.
        # Cancelling the gather propagates to each child, letting monitor.run() reap its
        # WS tasks in the finally it already has.
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, tasks.cancel)

        try:
            await tasks
        except asyncio.CancelledError:
            logger.info("Shutting down gracefully...")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Daemon stopped by user.")
