# Helper scripts

Verified against the working tree on 2026-11-22.

## Scripts

| Script | Behavior and side effects |
|---|---|
| [`team_mapper.py`](../scripts/team_mapper.py) | Fetches supported-league teams using the hard-coded 2024 API-Football season, extracts Polymarket names, automatically matches exact/normalized names, prompts for unresolved names, and writes `team_mapping.json` in the working directory. `fetch_polymarket_teams_sync` is an unimplemented stub. |
| [`check_prefixes.py`](../scripts/check_prefixes.py) | Reads `team_mapping.json` in the working directory and prints colon-prefixed keys that lack a standalone equivalent. |
| [`fetch_and_plot_trades.py`](../scripts/fetch_and_plot_trades.py) | Finds the configured Gamma market, fetches first-token price history and writes a CSV and plot in the working directory. Its `1 - price` second series is a calculated complement, not a separately fetched order book. |

These scripts can call external services, and some write files or prompt for input.
Inspect their constants and working directory before running them.
