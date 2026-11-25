# ⚽ Soccer Monitor Wiki

Welcome! This guide explains what the live soccer bot does and how to use its Telegram controls. The bot must already be installed and running, and your Telegram chat must be configured by the person hosting it. For installation, see the [Oracle deployment guide](https://github.com/maorasis/Soccer-Monitor/blob/main/oracle_daemon_guide.md).

## 👀 What the bot does

The bot watches eligible **live soccer matches** from your enabled leagues on Polymarket. Approximately once a minute during your monitoring schedule, it checks the cheapest available offer to sell (the **lowest ask**) for these full-game markets:

| Market | Default alert limit |
| --- | ---: |
| Under 5.5 goals | 80¢ |
| Under 6.5 goals | 85¢ |
| Under 7.5 goals | 85¢ |

For example, **Under 5.5 goals** means the match ends with five or fewer total goals. If its lowest ask is 80¢ and your limit is 80¢, the bot sends an alert with match details and a link to Polymarket. A market can qualify on its first observation; it does not have to cross the limit while the bot is watching. The bot sends information—it does not place orders or make bets.

**Example alert:** A Premier League match has an Under 5.5 ask of 79¢ and an 80¢ limit. The bot can send a message showing the match, league, line, ask, limit, and a Polymarket link. ⚽

The bot normally alerts once per market token, including across restarts. You can rearm a line for a follow-up as described below. A sent alert does not mean the price will remain available: prices and order books can change between checks and when you open the link.

## 🚀 Open the control panel

1. Open your configured bot conversation in Telegram.
2. Send `/start` or `/menu`.
3. Tap a button to use the control panel. Use **Main menu** or **Back** to return.

While entering a threshold or schedule, send `/cancel` to stop the edit. Only the configured chat can control the bot; ask the host to check the chat ID if it does not respond.

## 🏆 Choose leagues

Tap **Manage Leagues**. Each supported league shows a ✅ when enabled or ❌ when disabled. Tap a league to switch it on or off.

**Example:** Leave Premier League enabled and tap La Liga to disable it. The bot will continue checking Premier League markets, while La Liga markets are excluded. ✅

The bot checks markets in enabled leagues only. Turning a league off does not erase its saved thresholds, so they can be used again when you re-enable it.

## 🎯 Change an alert limit

A threshold is the maximum ask that qualifies for an alert. Enter the value in **cents**, not dollars; for example, enter `80` for 80¢ or `85.5` for 85.5¢. Values must be greater than zero and no more than 100.

### ✏️ Change one league

1. Tap **Edit Thresholds**.
2. Choose the league, then choose an Under line.
3. Send the new limit, such as `80`.
4. Wait for the saved confirmation.

### 🌍 Change one line for all leagues

1. Tap **Edit Thresholds → All leagues**.
2. Choose the Under line.
3. Send the new limit and wait for confirmation.

**Example:** Set Under 6.5 to `88`. The limit becomes 88¢ for Under 6.5 in every supported league; Under 5.5 and Under 7.5 keep their existing limits. 🌍

This replaces existing limits for that line across every supported league, including disabled leagues. Other Under lines are unchanged.

### 📋 Check limits

Tap **View Thresholds** to see the effective limit for each league and line. The ✏️ marker indicates a custom setting; unmarked values are defaults. The display also shows whether each league is enabled. Use **Next** and **Previous** to browse the list.

## ⚽ View live markets

Tap **Live Markets** to fetch eligible live matches, Under markets and their latest lowest asks. The screen shows the current limit and fetch time. **No ask** means no valid sell offer was returned. For example, `Ask: 82¢ · Current limit: 80¢` means the ask was above the limit when fetched.

Opening the view or changing pages fetches again, even outside monitoring hours. Prices can change after the fetch. Opening the view does not send alerts; a qualifying market may already have alerted. If fetching fails, the bot displays an error.

## 🔁 Rearm an alert

Tap **Rearm in 15 min** on an alert to schedule one follow-up for that exact market line. The countdown starts when you tap. After it expires, the first scheduled poll that finds the market still live and its lowest ask at or below the current limit sends the follow-up. If the ask is above the limit, the rearm remains pending while the market is live. The follow-up has a new rearm button. Tapping the same button again does not restart the timer.

To change the delay on future alerts, choose **Alert Cooldown** in `/menu` and enter a whole number from **2 to 1,440 minutes**. Existing buttons keep their displayed delay, and scheduled rearms keep their deadline. Rearms are stored in `live_under_alerts.sqlite3` and survive restarts; retain that file when moving the service.

## 🕒 Set monitoring hours

Tap **Monitoring Schedule** to see the current hours and whether the bot is inside its monitoring window. Outside the window, the screen shows the next start. The default is **14:00–23:00, Asia/Jerusalem**.

To change it:

1. Tap **Change schedule**.
2. Send the start time, end time, and timezone together. For example:

   `14:00 23:00 Asia/Jerusalem`

3. Wait for the saved confirmation. Changes apply on the next poll.

Use 24-hour times and an IANA timezone name, such as `Europe/London`. Start and end must differ. Overnight windows such as `22:00 02:00 Asia/Jerusalem` are supported.

## 🔔 Check status and test delivery

- **Status** shows the active league count, schedule, default limits, custom-limit count, and recorded API health.
- **Send Test Notification** sends a labeled test message using the alert channel. It does not count as a market alert. The bot reports whether sending succeeded, failed, or was uncertain.

If alerts are missing, check that the server is running, your league is enabled, the schedule is active, and an eligible market has an ask at or below its limit. Check **Live Markets**, its timestamp, and **Status**; the same market may already have alerted. Use the test notification to check delivery.

## System overview

Verified against the working tree on 2026-11-22.

The repository contains separate Python entry points, not one combined daemon:

| Entry point | Responsibility | External systems |
|---|---|---|
| [`live_under_monitor.py`](https://github.com/maorasis/Soccer-Monitor/blob/main/live_under_monitor.py) | Poll live full-game Under markets, compare asks with per-league limits, and send alerts. | Polymarket Gamma and CLOB HTTP APIs; Telegram |
| [`soccer_monitor.py`](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor.py) | Discover soccer markets and detect downward ask crossings from websocket books. | API-Football, Polymarket Gamma and CLOB websocket, Telegram |

The two soccer implementations share [`bot_ui.py`](https://github.com/maorasis/Soccer-Monitor/blob/main/bot_ui.py),
[`config_manager.py`](https://github.com/maorasis/Soccer-Monitor/blob/main/config_manager.py), and health, error, title parsing and
Telegram helpers in `soccer_monitor.py`. `RuntimeConfig(under_totals=True)` selects
the live-Under control-panel behavior. See the technical sections below.

### Configuration and state

- `RuntimeConfig` loads `config_state.json` by default and saves by writing a
  temporary file and replacing the original. Its active league list, legacy
  global/league thresholds, per-league Under limits, alert cooldown, and polling schedule are
  held in memory. The Under limit and schedule setters restore their previous
  in-memory values if saving fails. The file is not watched for external edits.
  Source: [`config_manager.py`](https://github.com/maorasis/Soccer-Monitor/blob/main/config_manager.py), `RuntimeConfig`.
- The live-Under daemon records claimed token IDs and scheduled rearms in `live_under_alerts.sqlite3`
  beside the config file. The websocket daemon tracks token alerts in memory and
  clears that history on the Israel-local date change. Their alert behavior is
  different; see the technical sections below.
- The websocket daemon loads `team_mapping.json` to match Polymarket names to
  API-Football names. The mapper script can update it. Sources: `MarketDiscovery.load_team_mapping`,
  `scripts/team_mapper.py:save_mapping`.
- Environment variables are loaded with `python-dotenv`; see the constants at
  the top of the respective entry point. Do not commit credentials. The soccer
  processes use `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`; the websocket daemon
  also uses `API_FOOTBALL_KEY`.

The config save is atomic for a single replacement, but there is no inter-process
locking or automatic reload in `RuntimeConfig`. Running both soccer daemons with
the same config path can therefore lead to stale in-memory settings and overwritten
changes. The same Telegram bot token also cannot safely be assumed to support
independent simultaneous polling consumers.

### Execution and deployment

Dependencies and Python compatibility are declared in [pyproject.toml](https://github.com/maorasis/Soccer-Monitor/blob/main/pyproject.toml),
with resolved versions in [uv.lock](https://github.com/maorasis/Soccer-Monitor/blob/main/uv.lock). The checked-in
[`soccer_monitor.service`](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor.service) starts the live-Under entry
point. See the [deployment guide](https://github.com/maorasis/Soccer-Monitor/blob/main/oracle_daemon_guide.md) for the checked-in
operational instructions. Actual deployed versions and process topology are unknown.

Tests are a mixture of `unittest` classes and standalone Python scripts under
[`tests/`](https://github.com/maorasis/Soccer-Monitor/tree/main/tests/). Some scripts perform real network calls or send messages;
read their module docstrings and setup before running them. In particular,
`test_threshold_alert.py` explicitly sends a real Telegram message.

## Soccer monitor internals

Verified against the working tree on 2026-11-22.

### Live Under polling

[`live_under_monitor.py`](https://github.com/maorasis/Soccer-Monitor/blob/main/live_under_monitor.py) composes `RuntimeConfig`, a
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

Each alert carries a unique rearm button with the cooldown configured at send time. `schedule_rearm` records a deadline once per button in `alert_rearms`. On a scheduled poll after the deadline, `poll_once` sends a follow-up only if the market is still eligible and its current ask is at or below the current threshold. It rotates the button before sending; an explicit delivery failure restores the pending rearm, while uncertain delivery retains the claim. The next alert uses the then-current cooldown. SQLite preserves pending rearms across restarts. Sources: `live_under_monitor.py:prepare_alert_history`, `schedule_rearm`, `poll_once`; `config_manager.py:RuntimeConfig.set_alert_cooldown`.

The Live Markets screen independently fetches eligible markets and books on every open or page change, regardless of the monitoring schedule. It displays the fetch time and current thresholds, and reports a fetch failure. This read does not send market alerts. Sources: `live_under_monitor.py:fetch_ui_markets`, `fetch_market_books`; `bot_ui.py:cb_live_markets`.

### Websocket price dips

[`soccer_monitor.py`](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor.py) composes `LiveMatchTracker`,
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

### Control panel and health

[`bot_ui.py`](https://github.com/maorasis/Soccer-Monitor/blob/main/bot_ui.py) authenticates both messages and callbacks using the
configured Telegram chat ID. `start_bot_polling` refuses to start if the ID is
missing or invalid. Aiogram states hold pending threshold, schedule and alert cooldown inputs;
`/cancel` clears the active edit. Under prices are entered in cents, converted
to dollar fractions and persisted by `RuntimeConfig`. Bulk edits write the chosen
line for all supported leagues, including disabled ones.

`ApiHealth` records results of calls the daemons already make; status display does
not probe upstream services. `ErrorAlertHandler` queues eligible ERROR logs and
applies a call-site cooldown. Telegram send errors are marked to avoid recursively
reporting a Telegram failure through Telegram. Source: `soccer_monitor.py:ApiHealth`,
`ErrorAlertHandler`, and `error_alert_loop`.

## Soccer helper scripts

Verified against the working tree on 2026-11-22.

| Script | Behavior and side effects |
|---|---|
| [`team_mapper.py`](https://github.com/maorasis/Soccer-Monitor/blob/main/scripts/team_mapper.py) | Fetches supported-league teams using the hard-coded 2024 API-Football season, extracts Polymarket names, automatically matches exact/normalized names, prompts for unresolved names, and writes `team_mapping.json` in the working directory. `fetch_polymarket_teams_sync` is an unimplemented stub. |
| [`check_prefixes.py`](https://github.com/maorasis/Soccer-Monitor/blob/main/scripts/check_prefixes.py) | Reads `team_mapping.json` in the working directory and prints colon-prefixed keys that lack a standalone equivalent. |
| [`fetch_and_plot_trades.py`](https://github.com/maorasis/Soccer-Monitor/blob/main/scripts/fetch_and_plot_trades.py) | Finds the configured Gamma market, fetches first-token price history and writes a CSV and plot in the working directory. Its `1 - price` second series is a calculated complement, not a separately fetched order book. |

These scripts can call external services, and some write files or prompt for input.
Inspect their constants and working directory before running them.

## More information

- [Project README](https://github.com/maorasis/Soccer-Monitor/blob/main/README.md)
- [Oracle deployment guide](https://github.com/maorasis/Soccer-Monitor/blob/main/oracle_daemon_guide.md)
