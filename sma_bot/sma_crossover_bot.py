"""
S&P 500 — 150-Day SMA Crossover Alert Bot

Scans all S&P 500 stocks for price crossovers above the 150-day Simple Moving
Average. Sends Telegram alerts with mini-charts. Runs 2× daily on weekdays.

Schedule: 16:35 and 18:30 Asia/Jerusalem, Monday–Friday. Alert date and caption
timestamps use the fixed UTC+3 IDT constant, which differs in Israel's winter.
"""

import argparse
import html
import io
import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf
import matplotlib
matplotlib.use("Agg")  # Non-interactive backend — must be set before pyplot import
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
load_dotenv()

SMA_TELEGRAM_BOT_TOKEN = os.getenv("SMA_TELEGRAM_BOT_TOKEN")
SMA_TELEGRAM_CHAT_ID = os.getenv("SMA_TELEGRAM_CHAT_ID")
SMA_ALERT_COOLDOWN_DAYS = int(os.getenv("SMA_ALERT_COOLDOWN_DAYS", "1"))
SMA_WINDOW = 150  # days
CHART_LOOKBACK = 30  # trading days shown in the mini-chart
STATE_FILE = Path(__file__).parent / "sma_state.json"

IDT = timezone(timedelta(hours=3))  # Israel Daylight Time (UTC+3)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("SMA_Crossover")

# ---------------------------------------------------------------------------
# S&P 500 ticker list (cached for the process lifetime)
# ---------------------------------------------------------------------------
_ticker_cache: list[str] | None = None
_ticker_cache_date: str | None = None


def fetch_sp500_tickers() -> list[str]:
    """Fetch the current S&P 500 constituent list from Wikipedia."""
    global _ticker_cache, _ticker_cache_date

    today = datetime.now(IDT).strftime("%Y-%m-%d")
    if _ticker_cache and _ticker_cache_date == today:
        logger.info(f"Using cached S&P 500 ticker list ({len(_ticker_cache)} tickers).")
        return _ticker_cache

    logger.info("Fetching S&P 500 ticker list from Wikipedia...")
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    headers = {"User-Agent": "SMA-Crossover-Bot/1.0 (Python/requests)"}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    tables = pd.read_html(io.StringIO(resp.text))
    df = tables[0]
    tickers = df["Symbol"].tolist()
    # Yahoo Finance uses hyphens where the index uses dots (e.g. BRK.B → BRK-B)
    tickers = [t.replace(".", "-") for t in tickers]
    _ticker_cache = tickers
    _ticker_cache_date = today
    logger.info(f"Retrieved {len(tickers)} S&P 500 tickers.")
    return tickers


# ---------------------------------------------------------------------------
# Price data & SMA crossover detection
# ---------------------------------------------------------------------------

def detect_crossovers(tickers: list[str]) -> list[dict]:
    """
    Download recent prices for *tickers*, compute the 150-day SMA, and return
    a list of dicts for every stock that crossed above its SMA today.

    Each dict: {ticker, name, close, sma, pct_above, history_df}
    """
    # yfinance 'period' is in calendar days, but the SMA rolling window
    # operates on trading days (~5 per 7 calendar days).  We need at least
    # SMA_WINDOW trading days, so multiply by 7/5 ≈ 1.4 and add a margin.
    # +10 is for holidays
    period = f"{int(SMA_WINDOW * 1.4) + 10}d"
    logger.info(f"Downloading price data for {len(tickers)} tickers (period={period})…")

    # Download in batches to avoid exhausting file descriptors on small VMs.
    # With only 2 vCPUs / ~1GB RAM, we keep batches small and sequential.
    BATCH_SIZE = 20
    frames = []
    for i in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[i : i + BATCH_SIZE]
        logger.info(f"  Batch {i // BATCH_SIZE + 1}/{-(-len(tickers) // BATCH_SIZE)}: downloading {len(batch)} tickers…")
        try:
            batch_data = yf.download(batch, period=period, group_by="ticker", progress=False, threads=False, auto_adjust=False)
            if not batch_data.empty:
                frames.append(batch_data)
        except Exception as e:
            logger.warning(f"  Batch {i // BATCH_SIZE + 1} failed: {e}")
        time.sleep(2)  # pause between batches to let FDs close

    if not frames:
        logger.error("yfinance returned no data across all batches.")
        return []

    data = pd.concat(frames, axis=1)

    # Guard against stale data on non-trading days (weekends/holidays).
    # yfinance always returns the last available trading day, so if the
    # latest date in the data is more than 3 calendar days old, the market
    # is likely closed and crossovers would be stale repeats.
    latest_date = data.index[-1]
    # Convert to offset-naive UTC for comparison if needed
    if hasattr(latest_date, 'tz') and latest_date.tz is not None:
        age_days = (datetime.now(timezone.utc) - latest_date).days
    else:
        age_days = (datetime.now(timezone.utc).replace(tzinfo=None) - latest_date).days
    if age_days > 3:
        logger.warning(f"Latest price data is {age_days} days old ({latest_date.date()}). Market may be closed. Skipping scan.")
        return []

    # Determine whether yfinance has included today's (partial) bar.
    # At market open (~9:30 ET / 16:30 IDT), the daily bar for today
    # often isn't present yet — iloc[-1] would be yesterday's close,
    # silently shifting all indices back by one day.
    # We compare dates in UTC which is safe because US market hours
    # (13:30–20:00 UTC summer) fall within a single UTC calendar day.
    today_utc = datetime.now(timezone.utc).date()
    if hasattr(latest_date, 'tz') and latest_date.tz is not None:
        latest_bar_date = latest_date.astimezone(timezone.utc).date()
    else:
        latest_bar_date = latest_date.date()

    has_today_bar = (latest_bar_date == today_utc)
    if has_today_bar:
        logger.info(f"Today's bar IS present in data (latest bar: {latest_bar_date}).")
    else:
        logger.info(f"Today's bar is NOT present (latest bar: {latest_bar_date}, today UTC: {today_utc}). "
                    f"Using end-of-day crossover detection on prior closes.")

    crossovers: list[dict] = []

    for ticker in tickers:
        try:
            # Multi-ticker download: columns are (Ticker, OHLCV)
            if len(tickers) == 1:
                close = data["Close"].dropna()
            else:
                close = data[(ticker, "Close")].dropna()

            if len(close) < SMA_WINDOW + 2:
                continue  # not enough history (need 150 + yesterday + today)

            sma = close.rolling(window=SMA_WINDOW).mean()

            if has_today_bar:
                # Today's intraday bar is iloc[-1]; yesterday's full close
                # is iloc[-2]. We use the SMA ending yesterday (iloc[-2])
                # to avoid mixing in today's partial bar.
                current_price = close.iloc[-1]
                yesterday_close = close.iloc[-2]
                sma_latest = sma.iloc[-2]
            else:
                # Today's bar is missing — iloc[-1] is the last completed
                # trading day ("yesterday") and iloc[-2] is the day before.
                # Compare yesterday's close against the SMA ending the day
                # before to detect yesterday's crossover.
                current_price = close.iloc[-1]    # yesterday's close
                yesterday_close = close.iloc[-2]  # day before yesterday
                sma_latest = sma.iloc[-1]          # SMA ending yesterday (bar is complete)
                # Note: this detects yesterday's crossover — still valid
                # since it's the most recent completed trading day.

            if pd.isna(sma_latest):
                continue

            # Crossover: current price is above the SMA, AND the prior
            # close was strictly below the SMA
            if current_price > sma_latest and yesterday_close < sma_latest:
                pct_above = ((current_price - sma_latest) / sma_latest) * 100

                # Try to get the company name from yfinance (fast info)
                try:
                    info = yf.Ticker(ticker).info
                    name = info.get("shortName") or info.get("longName") or ticker
                except Exception:
                    name = ticker

                # Brief pause between .info calls to avoid Yahoo rate limits
                time.sleep(0.3)

                crossovers.append({
                    "ticker": ticker,
                    "name": name,
                    "price": float(current_price),
                    "sma": float(sma_latest),
                    "pct_above": float(pct_above),
                    "close_series": close,
                    "sma_series": sma,
                })
        except Exception as e:
            logger.debug(f"Skipping {ticker}: {e}")

    logger.info(f"Crossover scan complete. Found {len(crossovers)} crossover(s).")
    return crossovers


# ---------------------------------------------------------------------------
# State management (daily cooldown)
# ---------------------------------------------------------------------------

def load_state() -> dict:
    """Read ticker-to-last-alert-date state; missing or malformed files act empty."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_state(state: dict) -> None:
    """Write ticker alert dates to the local JSON state file."""
    STATE_FILE.write_text(json.dumps(state, indent=2))


def filter_already_alerted(crossovers: list[dict]) -> list[dict]:
    """Remove tickers that were already alerted today."""
    state = load_state()
    today = datetime.now(IDT).strftime("%Y-%m-%d")
    filtered = []
    for c in crossovers:
        last_alert = state.get(c["ticker"])
        if last_alert == today:
            logger.info(f"Suppressing duplicate alert for {c['ticker']} (already alerted today).")
            continue
        filtered.append(c)
    return filtered


def record_alerts(crossovers: list[dict]) -> None:
    """Mark tickers as alerted today."""
    state = load_state()
    today = datetime.now(IDT).strftime("%Y-%m-%d")

    # Flush stale entries (older than cooldown window)
    cutoff = (datetime.now(IDT) - timedelta(days=SMA_ALERT_COOLDOWN_DAYS)).strftime("%Y-%m-%d")
    state = {k: v for k, v in state.items() if v >= cutoff}

    for c in crossovers:
        state[c["ticker"]] = today
    save_state(state)


# ---------------------------------------------------------------------------
# Mini-chart generation
# ---------------------------------------------------------------------------

def generate_chart(crossover: dict) -> bytes:
    """
    Create a 30-day mini-chart (close price + SMA line) and return it as PNG
    bytes suitable for Telegram sendPhoto.
    """
    close = crossover["close_series"]
    sma = crossover["sma_series"]
    ticker = crossover["ticker"]

    # Last CHART_LOOKBACK trading days
    close_plot = close.iloc[-CHART_LOOKBACK:]
    sma_plot = sma.iloc[-CHART_LOOKBACK:]

    fig, ax = plt.subplots(figsize=(6, 3), dpi=120)
    fig.patch.set_facecolor("#1e1e2f")
    ax.set_facecolor("#1e1e2f")

    # Price line
    ax.plot(close_plot.index, close_plot.values, color="#00bfff", linewidth=1.8, label="Close")
    # SMA line
    ax.plot(sma_plot.index, sma_plot.values, color="#ff9800", linewidth=1.4, linestyle="--", label=f"{SMA_WINDOW}-day SMA")
    # Yesterday's close marker (second-to-last point)
    ax.scatter(close_plot.index[-2], close_plot.values[-2], color="#ff5252", s=50, zorder=5, edgecolors="white", linewidths=0.8, label="Prev close")
    # Crossover marker — current price (last point)
    ax.scatter(close_plot.index[-1], close_plot.values[-1], color="#00e676", s=60, zorder=5, edgecolors="white", linewidths=0.8, label="Current")

    ax.set_title(f"{ticker}", color="white", fontsize=12, fontweight="bold", pad=8)
    ax.tick_params(colors="white", labelsize=7)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
    fig.autofmt_xdate(rotation=30)

    for spine in ax.spines.values():
        spine.set_color("#444")
    ax.legend(fontsize=7, loc="upper left", facecolor="#1e1e2f", edgecolor="#444", labelcolor="white")
    ax.grid(axis="y", color="#333", linewidth=0.5)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.read()


# ---------------------------------------------------------------------------
# Telegram notifications
# ---------------------------------------------------------------------------

def send_telegram_photo(photo_bytes: bytes, caption: str) -> None:
    """Send a photo with caption via the Telegram Bot API."""
    if not SMA_TELEGRAM_BOT_TOKEN or not SMA_TELEGRAM_CHAT_ID:
        logger.info("Telegram credentials not configured — logging alert only.")
        logger.info(caption)
        return

    url = f"https://api.telegram.org/bot{SMA_TELEGRAM_BOT_TOKEN}/sendPhoto"
    files = {"photo": ("chart.png", photo_bytes, "image/png")}
    data = {
        "chat_id": SMA_TELEGRAM_CHAT_ID,
        "caption": caption,
        "parse_mode": "HTML",
    }

    try:
        resp = requests.post(url, data=data, files=files, timeout=30)
        if resp.status_code == 200:
            logger.info("Telegram photo alert sent successfully.")
        else:
            logger.error(f"Telegram sendPhoto failed: {resp.status_code} — {resp.text}")
    except Exception as e:
        logger.error(f"Exception sending Telegram photo: {e}")


def send_telegram_message(text: str) -> None:
    """Send a plain text message via the Telegram Bot API."""
    if not SMA_TELEGRAM_BOT_TOKEN or not SMA_TELEGRAM_CHAT_ID:
        logger.info("Telegram credentials not configured — logging message only.")
        logger.info(text)
        return

    url = f"https://api.telegram.org/bot{SMA_TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": SMA_TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }
    try:
        resp = requests.post(url, json=data, timeout=30)
        if resp.status_code != 200:
            logger.error(f"Telegram sendMessage failed: {resp.status_code} — {resp.text}")
    except Exception as e:
        logger.error(f"Exception sending Telegram message: {e}")


def notify_crossovers(crossovers: list[dict], dry_run: bool = False) -> None:
    """Send a Telegram alert (with chart) for each crossover."""
    now_idt = datetime.now(IDT).strftime("%H:%M")

    if len(crossovers) > 1:
        header = f"📈 <b>{len(crossovers)} SMA crossovers detected at {now_idt} IDT</b>"
        if dry_run:
            logger.info(f"[DRY RUN] {header}")
        else:
            send_telegram_message(header)

    for c in crossovers:
        # HTML-escape company name to avoid breaking Telegram parse_mode
        safe_name = html.escape(c['name'])
        caption = (
            f"📈 <b>SMA CROSSOVER DETECTED</b>\n\n"
            f"🏷️ <b>{c['ticker']}</b> — {safe_name}\n"
            f"💰 Price: ${c['price']:.2f}\n"
            f"📊 Prev-Day 150 SMA: ${c['sma']:.2f}\n"
            f"📈 Above SMA by: {c['pct_above']:.2f}%\n"
            f"🕐 Detected at: {now_idt} IDT"
        )

        # Telegram sendPhoto captions are limited to 1024 characters
        if len(caption) > 1024:
            caption = caption[:1021] + "…"

        chart_png = generate_chart(c)

        if dry_run:
            logger.info(f"[DRY RUN] Would send alert:\n{caption}")
        else:
            send_telegram_photo(chart_png, caption)


# ---------------------------------------------------------------------------
# Main scan
# ---------------------------------------------------------------------------

def scan_stocks(dry_run: bool = False, persist_state: bool = True) -> None:
    """Top-level function executed on each scheduled run.

    Args:
        dry_run: If True, log alerts instead of sending Telegram messages.
        persist_state: If False, skip reading/writing the state file (for
            testing with --scan-now without polluting scheduled runs).
    """
    logger.info("=" * 60)
    logger.info("Starting S&P 500 SMA crossover scan…")
    logger.info("=" * 60)

    try:
        tickers = fetch_sp500_tickers()
        crossovers = detect_crossovers(tickers)

        if not crossovers:
            logger.info("No crossovers detected this scan.")
            return

        if persist_state:
            crossovers = filter_already_alerted(crossovers)
            if not crossovers:
                logger.info("All crossovers were already alerted today. Nothing to send.")
                return

        notify_crossovers(crossovers, dry_run=dry_run)
        if not dry_run and persist_state:
            record_alerts(crossovers)
        elif dry_run:
            logger.info(f"[DRY RUN] Would have recorded {len(crossovers)} alert(s) in state file.")
        else:
            logger.info(f"[SCAN-NOW] Sent {len(crossovers)} alert(s) — state file not updated.")

    except Exception as e:
        logger.exception(f"Scan failed with exception: {e}")


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

def run_scheduler(dry_run: bool = False) -> None:
    """Start the APScheduler daemon with the two daily triggers."""
    scheduler = BlockingScheduler()

    # Two scheduled scans: 16:35, 18:30 IDT — Mon-Fri
    times = [("16", "35"), ("18", "30")]
    for hour, minute in times:
        trigger = CronTrigger(
            hour=hour,
            minute=minute,
            day_of_week="mon-fri",
            timezone="Asia/Jerusalem",
        )
        scheduler.add_job(
            scan_stocks,
            trigger=trigger,
            kwargs={"dry_run": dry_run},
            id=f"sma_scan_{hour}_{minute}",
            name=f"SMA scan at {hour}:{minute} IDT",
            misfire_grace_time=300,
        )
        logger.info(f"Scheduled scan at {hour}:{minute} IDT (Mon-Fri)")

    logger.info("SMA Crossover Bot started. Waiting for next scheduled run…")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="S&P 500 — 150-Day SMA Crossover Alert Bot")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run a single scan immediately and log alerts instead of sending Telegram messages.",
    )
    parser.add_argument(
        "--scan-now",
        action="store_true",
        help="Run a single scan immediately (sends real alerts), then exit.",
    )
    args = parser.parse_args()

    if args.dry_run:
        logger.info("Running in DRY RUN mode — no Telegram messages will be sent.")
        scan_stocks(dry_run=True)
    elif args.scan_now:
        logger.info("Running immediate scan (test mode — state file will not be modified)…")
        scan_stocks(dry_run=False, persist_state=False)
    else:
        run_scheduler()


if __name__ == "__main__":
    main()
