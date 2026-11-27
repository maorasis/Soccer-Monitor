# Helper scripts

Verified against the working tree on 2026-11-27.

| Script | Behavior and side effects |
|---|---|
| [`fetch_and_plot_trades.py`](../scripts/fetch_and_plot_trades.py) | Finds the configured Gamma market, fetches first-token price history and writes a CSV and plot in the working directory. Its `1 - price` second series is a calculated complement, not a separately fetched order book. |

The script calls external services and writes files. Inspect its constants and
working directory before running it.

The team-name mapping tools (`team_mapper.py`, `check_prefixes.py` and
`team_mapping.json`) served only the removed websocket monitor and were removed with it.
