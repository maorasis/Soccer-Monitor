"""
Fetch the price of a stock at a specific date/time using yfinance intraday data.

Usage:
    python scripts/fetch_price_at_time.py ADP "2026-06-02 16:30" --tz Asia/Jerusalem
"""

import argparse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import yfinance as yf


def fetch_price_at_time(ticker: str, target_dt: datetime) -> None:
    """Download 1-minute intraday data around the target time and find the closest bar."""
    target_utc = target_dt.astimezone(ZoneInfo("UTC"))
    # yfinance needs start/end in UTC date for intraday; fetch the whole day
    day_start = target_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    print(f"Ticker:      {ticker}")
    print(f"Target time: {target_dt.strftime('%Y-%m-%d %H:%M %Z')}")
    print(f"             {target_utc.strftime('%Y-%m-%d %H:%M UTC')}")
    print()

    data = yf.download(
        ticker,
        start=day_start.strftime("%Y-%m-%d"),
        end=day_end.strftime("%Y-%m-%d"),
        interval="1m",
        progress=False,
    )

    if data.empty:
        print("No intraday data returned. The market may have been closed on this date,")
        print("or the date may be too far in the past (yfinance keeps ~30 days of 1m data).")
        return

    # Find the bar closest to our target time
    # yfinance index is tz-aware (US/Eastern typically); convert target to match
    idx_tz = data.index.tz
    target_localized = target_dt.astimezone(idx_tz)

    # Get the bar at or just before the target time
    mask = data.index <= target_localized
    if not mask.any():
        print(f"No data found at or before {target_localized.strftime('%H:%M %Z')}.")
        print(f"Earliest bar: {data.index[0]}")
        return

    closest = data.loc[mask].iloc[-1]
    bar_time = data.loc[mask].index[-1]

    print(f"Closest bar: {bar_time.strftime('%Y-%m-%d %H:%M %Z')}")
    # Handle both single-ticker (flat columns) and multi-level columns
    if isinstance(closest.index, type(data.columns)) and isinstance(data.columns, type(closest.index)):
        try:
            price = closest["Close"] if "Close" in closest.index else closest[(ticker, "Close")]
        except (KeyError, TypeError):
            price = closest.iloc[3]  # Close is typically the 4th column
    else:
        price = closest["Close"]

    # Extract scalar values (yfinance may return single-element Series with multi-level columns)
    def _scalar(val):
        """Safely extract a scalar from a value that might be a single-element Series."""
        try:
            return float(val.iloc[0])
        except (AttributeError, IndexError):
            return float(val)

    print(f"Price:       ${_scalar(price):.2f}")
    print(f"Open:        ${_scalar(closest['Open']):.2f}")
    print(f"High:        ${_scalar(closest['High']):.2f}")
    print(f"Low:         ${_scalar(closest['Low']):.2f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch stock price at a specific time")
    parser.add_argument("ticker", help="Stock ticker symbol (e.g. ADP)")
    parser.add_argument("time", help='Target datetime as "YYYY-MM-DD HH:MM"')
    parser.add_argument("--tz", default="Asia/Jerusalem", help="Timezone of the target time (default: Asia/Jerusalem)")
    args = parser.parse_args()

    tz = ZoneInfo(args.tz)
    target = datetime.strptime(args.time, "%Y-%m-%d %H:%M").replace(tzinfo=tz)

    fetch_price_at_time(args.ticker, target)
