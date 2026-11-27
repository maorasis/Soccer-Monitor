# SMA crossover bot

Scans S&P 500 stocks for prices crossing above their 150-day simple moving average (SMA), then sends Telegram alerts with charts. Scheduled scans run Monday–Friday at 16:35 and 18:30 in `Asia/Jerusalem`.

## Setup and running

1. Run `uv sync` from the repository root.
2. Copy `sma_bot/.env.example` to `sma_bot/.env` and set the Telegram values.
3. Change into `sma_bot/`.
4. Run `uv run python sma_crossover_bot.py`.

For a single scan without Telegram messages or saved alert state, run `uv run python sma_crossover_bot.py --dry-run`. It still fetches live stock data. `--scan-now` sends real alerts without updating the saved state.

`sma_state.json` is local alert history and is ignored by Git. Keep it when moving the bot to avoid repeated alerts.

## Files

- `sma_crossover_bot.py`: stock discovery, price downloads, crossover detection, charts, Telegram delivery, and scheduling.
- `sma_crossover_bot.service`: systemd service for an Oracle Ubuntu checkout at `/home/ubuntu/soccer_monitor`, using its root `venv/` and `sma_bot/` as the working directory.
- `scripts/fetch_price_at_time.py`: stock-price lookup helper. From this folder, run `uv run python scripts/fetch_price_at_time.py ADP "2026-06-02 16:30" --tz Asia/Jerusalem`.

Dependencies and their lock file are shared at the repository root. The SMA bot uses its own `SMA_TELEGRAM_*` credentials, separate from the soccer bot.
