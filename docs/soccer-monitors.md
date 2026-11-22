# Soccer monitor internals

Verified against the working tree on 2026-11-22.

## Live Under polling

[`live_under_monitor.py`](../live_under_monitor.py) composes `RuntimeConfig`, a
shared HTTP session, SQLite alert history, `ApiHealth`, error delivery, and the
Telegram panel in `main`. `run_polls` checks the saved schedule at minute
boundaries. The daily start and end are interpreted in the configured IANA time
zone, with an end earlier than the start representing an overnight window.

`fetch_live_markets` follows Gamma keyset pages and returns tokens for enabled
leagues, live events within four hours of kickoff, open full-game totals and
supported Under lines. `poll_once` fetches CLOB books in batches of 100 tokens;
`lowest_ask` ignores invalid or nonpositive-sized asks. The alert limit comes from
`RuntimeConfig.get_under_threshold` (default or league override). The first observed
ask at or below that limit qualifies, even if the first observation is already low.

Before sending, `poll_once` inserts the token into the SQLite `alerted` table. A
successful send keeps the claim; an explicit failure removes it for a later retry;
an uncertain result keeps it. This favors duplicate avoidance when delivery is
ambiguous, but a crash or ambiguous failure can mean the alert is never delivered.
The persisted key is the token ID, with no daily expiration in this implementation.

The optional UI snapshot is updated after a successful market/book fetch and is
not persisted. The live-market screen displays that snapshot's timestamp, filters
out leagues currently disabled, and looks up current thresholds at display time;
it does not trigger another fetch. A failed poll leaves the earlier snapshot in
place. Sources: `poll_once`, `bot_ui.py:cb_live_markets`.

## Websocket price dips

[`soccer_monitor.py`](../soccer_monitor.py) composes `LiveMatchTracker`,
`MarketDiscovery`, `OrderBookMonitor`, and `AlertEngine` in `main`. The tracker
fetches fixtures for today and tomorrow, retains a global fixture cache, and
filters it against enabled leagues on read. Discovery scans Gamma and matches
fixtures using team mappings. When a discovery scan fails, `None` tells the caller
to retain the current subscriptions instead of clearing them.

`OrderBookMonitor` applies full book snapshots and sell-side level changes. A
token is armed only after its ask is seen at or above the configured threshold;
an initial ask below the threshold does not itself count as a dip. A later ask
below the threshold starts a confirmation task without blocking websocket reads.
After the delay, an empty book or an ask at or below `ASK_PRICE_MIN` suppresses
the alert. A score lookup can suppress a market whose Under outcome can no longer
win. A confirmed dip is sent through `AlertEngine`; its per-token history is
in memory and cleared when `main` observes a new Israel-local date.

Book state is discarded on reconnect because updates may have been missed during
the gap. `set_markets` reconnects when token IDs change, but metadata-only changes
do not require a new subscription. Source: `OrderBookMonitor`.

## Control panel and health

[`bot_ui.py`](../bot_ui.py) authenticates both messages and callbacks using the
configured Telegram chat ID. `start_bot_polling` refuses to start if the ID is
missing or invalid. Aiogram states hold pending threshold and schedule inputs;
`/cancel` clears the active edit. Under prices are entered in cents, converted
to dollar fractions and persisted by `RuntimeConfig`. Bulk edits write the chosen
line for all supported leagues, including disabled ones.

`ApiHealth` records results of calls the daemons already make; status display does
not probe upstream services. `ErrorAlertHandler` queues eligible ERROR logs and
applies a call-site cooldown. Telegram send errors are marked to avoid recursively
reporting a Telegram failure through Telegram. Source: `soccer_monitor.py:ApiHealth`,
`ErrorAlertHandler`, and `error_alert_loop`.
