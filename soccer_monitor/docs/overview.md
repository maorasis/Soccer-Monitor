# Football_Under_Monitor system overview

Verified against the working tree on 2026-11-27.

The soccer folder has one daemon, [`live_under_monitor.py`](../live_under_monitor.py).
It polls live full-game Under markets, compares asks with per-league limits, sends
alerts, and sends a daily report. External systems: Polymarket Gamma and CLOB HTTP
APIs, and Telegram.

| Module | Responsibility |
|---|---|
| [`live_under_monitor.py`](../live_under_monitor.py) | Market discovery, order-book polling, alert claims and rearms, and `main`. |
| [`daily_report.py`](../daily_report.py) | Alert log, outcome lookups and the once-a-day report. |
| [`bot_ui.py`](../bot_ui.py) | Telegram control panel. |
| [`config_manager.py`](../config_manager.py) | Supported leagues and persisted settings. |
| [`common.py`](../common.py) | Telegram delivery, API health, error alerts and market-title parsing. |

An earlier websocket-based monitor (`soccer_monitor.py`, with API-Football and team
name mapping) was removed on 2026-11-27. It remains in the Git history.

## Configuration and state

- `RuntimeConfig` loads `config_state.json` by default and saves by writing a
  temporary file and replacing the original. Its active league list, per-league
  Under limits, alert cooldown, polling schedule and daily report switch are held in
  memory. Setters restore their previous in-memory values if saving fails. The file
  is not watched for external edits. Fields left by the removed websocket monitor
  (`global_threshold`, `league_thresholds`) are ignored and dropped on the next save.
  Source: [`config_manager.py`](../config_manager.py), `RuntimeConfig`.
- `live_under_alerts.sqlite3`, beside the config file, holds claimed token IDs,
  scheduled rearms, the alert log and the days whose report was sent.
- Environment variables are loaded with `python-dotenv` from `soccer_monitor/.env`
  by [`common.py`](../common.py). Already-set variables take precedence. Do not commit
  credentials. The daemon requires `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

The config save is atomic for a single replacement, but there is no inter-process
locking or automatic reload. Do not run two daemons with the same config path or bot
token.

## Execution and deployment

Dependencies and Python compatibility are declared in [pyproject.toml](../../pyproject.toml),
with resolved versions in [uv.lock](../../uv.lock). The checked-in
[`soccer_monitor.service`](../soccer_monitor.service) starts the daemon. See the
[deployment guide](../oracle_daemon_guide.md) for operational instructions.

## Tests

All tests are `unittest` classes under [`tests/`](../tests/) and run offline with
simulated HTTP and Telegram boundaries. Run them from this folder:

```bash
uv run python -m unittest
```

GitHub Actions runs the same command, and the Reem test suite, on every push and pull
request; see [`.github/workflows/tests.yml`](../../.github/workflows/tests.yml).
