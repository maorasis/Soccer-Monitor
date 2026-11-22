# System overview

Verified against the working tree on 2026-11-22.

The repository contains separate Python entry points, not one combined daemon:

| Entry point | Responsibility | External systems |
|---|---|---|
| [`live_under_monitor.py`](../live_under_monitor.py) | Poll live full-game Under markets, compare asks with per-league limits, and send alerts. | Polymarket Gamma and CLOB HTTP APIs; Telegram |
| [`soccer_monitor.py`](../soccer_monitor.py) | Discover soccer markets and detect downward ask crossings from websocket books. | API-Football, Polymarket Gamma and CLOB websocket, Telegram |
| [`sma_crossover_bot.py`](../sma_crossover_bot.py) | Scan S&P 500 stocks for 150-day SMA crossovers and send charts. | Wikipedia, Yahoo Finance through yfinance, Telegram |

The two soccer implementations share [`bot_ui.py`](../bot_ui.py),
[`config_manager.py`](../config_manager.py), and health, error, title parsing and
Telegram helpers in `soccer_monitor.py`. `RuntimeConfig(under_totals=True)` selects
the live-Under control-panel behavior. The stock bot uses separate credentials and
does not use that control panel. See [soccer monitors](soccer-monitors.md) and
[stock and tools](stock-and-tools.md).

## Configuration and state

- `RuntimeConfig` loads `config_state.json` by default and saves by writing a
  temporary file and replacing the original. Its active league list, legacy
  global/league thresholds, per-league Under limits, and polling schedule are
  held in memory. The Under limit and schedule setters restore their previous
  in-memory values if saving fails. The file is not watched for external edits.
  Source: [`config_manager.py`](../config_manager.py), `RuntimeConfig`.
- The live-Under daemon records claimed token IDs in `live_under_alerts.sqlite3`
  beside the config file. The websocket daemon tracks token alerts in memory and
  clears that history on the Israel-local date change. Their alert behavior is
  different; see [soccer monitors](soccer-monitors.md).
- The websocket daemon loads `team_mapping.json` to match Polymarket names to
  API-Football names. The mapper script can update it. The stock bot stores
  ticker alert dates in `sma_state.json`. Sources: `MarketDiscovery.load_team_mapping`,
  `scripts/team_mapper.py:save_mapping`, `sma_crossover_bot.py:save_state`.
- Environment variables are loaded with `python-dotenv`; see the constants at
  the top of the respective entry point. Do not commit credentials. The soccer
  processes use `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`; the websocket daemon
  also uses `API_FOOTBALL_KEY`. The stock bot uses `SMA_TELEGRAM_BOT_TOKEN` and
  `SMA_TELEGRAM_CHAT_ID`.

The config save is atomic for a single replacement, but there is no inter-process
locking or automatic reload in `RuntimeConfig`. Running both soccer daemons with
the same config path can therefore lead to stale in-memory settings and overwritten
changes. The same Telegram bot token also cannot safely be assumed to support
independent simultaneous polling consumers.

## Execution and deployment

Dependencies and Python compatibility are declared in [pyproject.toml](../pyproject.toml),
with resolved versions in [uv.lock](../uv.lock). The checked-in
[`soccer_monitor.service`](../soccer_monitor.service) starts the live-Under entry
point, and [`sma_crossover_bot.service`](../sma_crossover_bot.service) starts the
stock bot. See the [deployment guide](../oracle_daemon_guide.md) for the checked-in
operational instructions. Actual deployed versions and process topology are unknown.

Tests are a mixture of `unittest` classes and standalone Python scripts under
[`tests/`](../tests/). Some scripts perform real network calls or send messages;
read their module docstrings and setup before running them. In particular,
`test_threshold_alert.py` explicitly sends a real Telegram message.
