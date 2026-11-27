"""Record delivered alerts and send a once-a-day summary of their outcomes.

A report for a calendar day (in the configured monitoring timezone) is sent the next
morning at REPORT_TIME, and only when at least one alert was delivered that day. The
Telegram menu can switch the report off; alerts are still recorded while it is off, and
switching it back on before the next morning's run sends the report for the previous day.

Outcomes come from Polymarket's CLOB market endpoint, which marks the winning token once
a market resolves. Results are counted per alert, as if one share had been bought at the
alerted ask: a win earns 1 - ask and a loss costs the ask.
"""

import asyncio
import html
import logging
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import aiohttp

logger = logging.getLogger("DailyReport")

CLOB_MARKET_URL = "https://clob.polymarket.com/markets/{}"
REPORT_TIME = time(9, 0)
# If outcomes cannot be fetched, keep retrying until this local time, then send the
# report with those alerts marked as pending rather than not at all.
REPORT_DEADLINE = time(10, 0)
CHECK_INTERVAL_SECONDS = 60
# Telegram rejects messages over 4096 characters; list at most this many alerts.
MAX_LISTED_ALERTS = 40

WON, LOST = "won", "lost"


def prepare_report_tables(db: sqlite3.Connection) -> None:
    """Create the alert log and report bookkeeping without touching existing rows."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS alert_log (
            id INTEGER PRIMARY KEY,
            token_id TEXT NOT NULL,
            condition_id TEXT NOT NULL,
            sent_at REAL NOT NULL,
            title TEXT NOT NULL,
            league TEXT NOT NULL,
            line REAL NOT NULL,
            ask REAL NOT NULL,
            outcome TEXT
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS alert_log_sent_at ON alert_log (sent_at)")
    db.execute("CREATE TABLE IF NOT EXISTS daily_reports (day TEXT PRIMARY KEY, sent_at REAL NOT NULL)")


def record_alert(db: sqlite3.Connection, token: str, market, ask: float, sent_at: datetime) -> None:
    """Log one delivered (or possibly delivered) alert for the daily report."""
    with db:
        db.execute("""
            INSERT INTO alert_log (token_id, condition_id, sent_at, title, league, line, ask)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (token, market.condition_id, sent_at.timestamp(), market.title, market.league, market.line, ask))


def day_bounds(day: date, zone: ZoneInfo) -> tuple[float, float]:
    """Return the UTC timestamps of a local calendar day's first and next day's first instant."""
    start = datetime.combine(day, time(0), tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=zone)
    return start.timestamp(), end.timestamp()


def alerts_for_day(db: sqlite3.Connection, day: date, zone: ZoneInfo) -> list[dict]:
    start, end = day_bounds(day, zone)
    rows = db.execute("""
        SELECT id, token_id, condition_id, sent_at, title, league, line, ask, outcome
        FROM alert_log WHERE sent_at >= ? AND sent_at < ? ORDER BY sent_at, id
    """, (start, end)).fetchall()
    keys = ("id", "token_id", "condition_id", "sent_at", "title", "league", "line", "ask", "outcome")
    return [dict(zip(keys, row)) for row in rows]


def report_due(config, db: sqlite3.Connection, now: datetime) -> date | None:
    """Return the day whose report should be sent now, or None.

    Only the previous local day is considered, from REPORT_TIME onward, so a report
    switched off at that time is skipped rather than delivered days later.
    """
    if not config.daily_report_enabled:
        return None
    local = now.astimezone(ZoneInfo(config.monitor_timezone))
    if local.time() < REPORT_TIME:
        return None
    day = local.date() - timedelta(days=1)
    if db.execute("SELECT 1 FROM daily_reports WHERE day = ?", (day.isoformat(),)).fetchone():
        return None
    if not alerts_for_day(db, day, ZoneInfo(config.monitor_timezone)):
        return None
    return day


async def fetch_outcome(session, condition_id: str, token: str) -> str | None:
    """Return WON or LOST once the market has a winning token, otherwise None."""
    async with session.get(CLOB_MARKET_URL.format(condition_id)) as response:
        response.raise_for_status()
        market = await response.json()
    tokens = market.get("tokens") or []
    if not any(item.get("winner") is True for item in tokens):
        return None
    ours = next((item for item in tokens if str(item.get("token_id")) == token), None)
    if ours is None:
        raise ValueError("Resolved market does not list the alerted token")
    return WON if ours.get("winner") is True else LOST


async def resolve_outcomes(session, db: sqlite3.Connection, alerts: list[dict]) -> bool:
    """Fill in outcomes of unresolved alerts. Return False if any lookup failed."""
    complete = True
    cache: dict[tuple[str, str], str | None] = {}
    for alert in alerts:
        if alert["outcome"] is not None or not alert["condition_id"]:
            continue
        key = (alert["condition_id"], alert["token_id"])
        if key not in cache:
            try:
                cache[key] = await fetch_outcome(session, *key)
            except (aiohttp.ClientError, asyncio.TimeoutError, KeyError, TypeError, ValueError) as exc:
                logger.warning("Could not fetch the outcome of %s: %s", alert["title"], type(exc).__name__)
                complete = False
                continue
        alert["outcome"] = cache[key]
        if alert["outcome"] is not None:
            with db:
                db.execute("UPDATE alert_log SET outcome = ? WHERE id = ?", (alert["outcome"], alert["id"]))
    return complete


def _cents(value: float) -> str:
    return f"{value * 100:+.1f}".rstrip("0").rstrip(".") + "¢"


def format_report(day: date, alerts: list[dict], zone: ZoneInfo) -> str:
    """Build the HTML report text for one day's alerts."""
    won = [a for a in alerts if a["outcome"] == WON]
    lost = [a for a in alerts if a["outcome"] == LOST]
    pending = len(alerts) - len(won) - len(lost)
    matches = len({a["title"] for a in alerts})
    profit = sum(1 - a["ask"] for a in won) - sum(a["ask"] for a in lost)
    staked = sum(a["ask"] for a in won + lost)

    lines = [
        f"🗓 <b>Daily report · {day.isoformat()}</b>",
        "",
        f"Alerts sent: {len(alerts)} ({matches} match{'es' if matches != 1 else ''})",
        f"✅ Won: {len(won)} · ❌ Lost: {len(lost)} · ⏳ Pending: {pending}",
    ]
    if staked:
        lines.append(f"Result at the alerted ask, 1 share per alert: {_cents(profit)} on "
                     f"{staked * 100:g}¢ ({profit / staked * 100:+.1f}%)")
    if pending:
        lines.append("Pending alerts are not counted in the result.")
    lines.append("")

    for alert in alerts[:MAX_LISTED_ALERTS]:
        sent = datetime.fromtimestamp(alert["sent_at"], timezone.utc).astimezone(zone).strftime("%H:%M")
        if alert["outcome"] == WON:
            result = f"✅ {_cents(1 - alert['ask'])}"
        elif alert["outcome"] == LOST:
            result = f"❌ {_cents(-alert['ask'])}"
        else:
            result = "⏳ pending"
        lines.append(f"• {sent} {html.escape(alert['title'])} · Under {alert['line']:g} "
                     f"@ {alert['ask'] * 100:g}¢ → {result}")
    if len(alerts) > MAX_LISTED_ALERTS:
        lines.append(f"…and {len(alerts) - MAX_LISTED_ALERTS} more")
    return "\n".join(lines)


async def send_due_report(session, config, db: sqlite3.Connection, send_text, now: datetime) -> bool:
    """Send the previous day's report if it is due. Return True if one was sent."""
    day = report_due(config, db, now)
    if day is None:
        return False
    zone = ZoneInfo(config.monitor_timezone)
    alerts = alerts_for_day(db, day, zone)
    complete = await resolve_outcomes(session, db, alerts)
    if not complete and now.astimezone(zone).time() < REPORT_DEADLINE:
        return False  # retry the failed lookups on the next check

    delivered = await send_text(format_report(day, alerts, zone))
    if delivered is False:
        return False  # explicit failure: retry on the next check
    # Delivered, or uncertain: Telegram may already have shown it, so do not repeat it.
    with db:
        db.execute("INSERT OR IGNORE INTO daily_reports VALUES (?, ?)", (day.isoformat(), now.timestamp()))
    logger.info("Daily report for %s %s", day, "sent" if delivered else "delivery uncertain")
    return True


async def run_daily_reports(session, config, db: sqlite3.Connection, send_text) -> None:
    """Check once a minute whether a daily report is due."""
    while True:
        try:
            await send_due_report(session, config, db, send_text, datetime.now(timezone.utc))
        except Exception:
            logger.exception("Daily report failed")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
