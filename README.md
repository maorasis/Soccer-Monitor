# Trading and market alert bots

Each bot keeps its source, service configuration, examples, and documentation in its own folder.

| Folder | Purpose | Start from the repository root |
| --- | --- | --- |
| [`soccer_monitor/`](soccer_monitor/README.md) | Live Polymarket soccer Under alerts and the older websocket monitor | `cd soccer_monitor && uv run python live_under_monitor.py` |
| [`sma_bot/`](sma_bot/README.md) | S&P 500 150-day simple moving average crossover alerts | `cd sma_bot && uv run python sma_crossover_bot.py` |
| [`reem_trading/`](reem_trading/README.md) | Polymarket copy trading, including resting sell orders | `cd reem_trading && uv run --frozen python copy_bot.py` (read-only check) |

Soccer and SMA dependencies remain shared: `pyproject.toml`, `uv.lock`, and the exported `requirements.txt` live at the root. Run `uv sync` here once; `uv run` inside either bot folder finds this project automatically. Reem has its own `pyproject.toml` and `uv.lock`; run `uv sync --frozen` inside `reem_trading/` for its separate environment.

Put each bot's `.env` and runtime state in its own folder. See the [soccer deployment guide](soccer_monitor/oracle_daemon_guide.md) before updating an existing server: its working directories and local-state locations have changed.
