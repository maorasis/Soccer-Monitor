# Stock scanner and helper scripts

Verified against the working tree on 2026-11-22.

## Stock crossover scanner

[`sma_crossover_bot.py`](../sma_crossover_bot.py) downloads the S&P 500 symbols
from Wikipedia, then requests price history from yfinance in batches. In
`detect_crossovers`, a candidate must have enough closing prices and the current
price must be above the selected 150-day average while the preceding close was
strictly below that same average. The selected bar differs depending on whether
today's bar is present; data older than three days is skipped.

`scan_stocks` filters tickers recorded for today, renders PNG charts and sends
Telegram notifications, then records the candidates. Delivery helpers log failures
but do not return success to `scan_stocks`, so a candidate may still be recorded
after a failed notification. `--dry-run` logs without sending; `--scan-now` sends
but does not persist state. The scheduler triggers at 16:35 and 18:30 weekdays in
`Asia/Jerusalem`; the separate `IDT` constant used for dates and captions is fixed
at UTC+3, including winter. Sources: `detect_crossovers`, `scan_stocks`,
`notify_crossovers`, `run_scheduler`, `main`.

## Scripts

| Script | Behavior and side effects |
|---|---|
| [`team_mapper.py`](../scripts/team_mapper.py) | Fetches supported-league teams using the hard-coded 2024 API-Football season, extracts Polymarket names, automatically matches exact/normalized names, prompts for unresolved names, and writes `team_mapping.json` in the working directory. `fetch_polymarket_teams_sync` is an unimplemented stub. |
| [`check_prefixes.py`](../scripts/check_prefixes.py) | Reads `team_mapping.json` in the working directory and prints colon-prefixed keys that lack a standalone equivalent. |
| [`fetch_and_plot_trades.py`](../scripts/fetch_and_plot_trades.py) | Finds the configured Gamma market, fetches first-token price history and writes a CSV and plot in the working directory. Its `1 - price` second series is a calculated complement, not a separately fetched order book. |
| [`fetch_price_at_time.py`](../scripts/fetch_price_at_time.py) | Requests one-minute yfinance data for a UTC day and prints the latest available bar at or before a timezone-aware target. |

These scripts can call external services, and some write files or prompt for input.
Inspect their constants and working directory before running them.
