# ⚽ Football_Under_Monitor Wiki

Welcome! This guide explains what the live soccer bot does and how to use its Telegram controls. The bot must already be installed and running, and your Telegram chat must be configured by the person hosting it. For installation, see the [Oracle deployment guide](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor/oracle_daemon_guide.md).

## 👀 What the bot does

The bot watches eligible **live soccer matches** from your enabled leagues on Polymarket. Approximately every 15 seconds during your monitoring schedule, it checks the cheapest available offer to sell (the **lowest ask**) for these full-game markets:

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

Supported leagues include Portugal's **Primeira Liga** 🇵🇹 and the Dutch **Eredivisie** 🇳🇱. If your bot was set up before they were added, they start disabled; tap them to enable them.

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

Tap **Live Markets** to fetch active games in your enabled leagues. Only Under markets with a valid lowest ask **at or below the current limit for that league and line** appear. Markets above the limit or without an ask are hidden. For example, with an 80¢ limit, a market at 79¢ or 80¢ appears; one at 82¢ does not. The screen shows the current limit and fetch time.

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

## 🗓 Daily report

On days when the bot sent at least one alert, it sends a summary the next morning at **09:00** in your monitoring timezone. For each alert it shows the time, match, line, alerted ask, and whether the Under **won ✅, lost ❌ or is still pending ⏳**. It also totals the result as if one share had been bought at each alerted ask: a win earns 100¢ minus the ask, a loss costs the ask. No report is sent for a day without alerts.

**Example:** You received an Under 5.5 alert at 80¢ that won and an Under 6.5 alert at 85¢ that lost. The report shows +20¢ and −85¢, a total of −65¢ on 165¢. 🗓

Tap **Daily Report** in `/menu` to switch it **Off** or back **On**; the button shows the current state. Alerts are still recorded while it is off. A report missed while it was off is not sent later, except that switching it back on the same day still sends the previous day's report.

## 🔔 Check status and test delivery

- **Status** shows the active league count, schedule, default limits, custom-limit count, whether the daily report is on, and recorded API health.
- **Send Test Notification** sends a labeled test message using the alert channel. It does not count as a market alert. The bot reports whether sending succeeded, failed, or was uncertain.

If alerts are missing, check that the server is running, your league is enabled, the schedule is active, and an eligible market has an ask at or below its limit. Check **Live Markets**, its timestamp, and **Status**; the same market may already have alerted. Use the test notification to check delivery.

## System overview

Verified against the working tree on 2026-11-27.

The soccer folder has one daemon, [`live_under_monitor.py`](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor/live_under_monitor.py).
It polls live full-game Under markets, compares asks with per-league limits, sends
alerts, and sends a daily report. External systems: Polymarket Gamma and CLOB HTTP
APIs, and Telegram.

| Module | Responsibility |
|---|---|
| [`live_under_monitor.py`](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor/live_under_monitor.py) | Market discovery, order-book polling, alert claims and rearms, and `main`. |
| [`daily_report.py`](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor/daily_report.py) | Alert log, outcome lookups and the once-a-day report. |
| [`bot_ui.py`](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor/bot_ui.py) | Telegram control panel. |
| [`config_manager.py`](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor/config_manager.py) | Supported leagues and persisted settings. |
| [`common.py`](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor/common.py) | Telegram delivery, API health, error alerts and market-title parsing. |

An earlier websocket-based monitor (`soccer_monitor.py`, with API-Football and team
name mapping) was removed on 2026-11-27. It remains in the Git history.

### Configuration and state

- `RuntimeConfig` loads `config_state.json` by default and saves by writing a
  temporary file and replacing the original. Its active league list, per-league
  Under limits, alert cooldown, polling schedule and daily report switch are held in
  memory. Setters restore their previous in-memory values if saving fails. The file
  is not watched for external edits. Fields left by the removed websocket monitor
  (`global_threshold`, `league_thresholds`) are ignored and dropped on the next save.
  Source: [`config_manager.py`](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor/config_manager.py), `RuntimeConfig`.
- `live_under_alerts.sqlite3`, beside the config file, holds claimed token IDs,
  scheduled rearms, the alert log and the days whose report was sent.
- Environment variables are loaded with `python-dotenv` from `soccer_monitor/.env`
  by [`common.py`](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor/common.py). Already-set variables take precedence. Do not commit
  credentials. The daemon requires `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

The config save is atomic for a single replacement, but there is no inter-process
locking or automatic reload. Do not run two daemons with the same config path or bot
token.

### Execution and deployment

Dependencies and Python compatibility are declared in [pyproject.toml](https://github.com/maorasis/Soccer-Monitor/blob/main/pyproject.toml),
with resolved versions in [uv.lock](https://github.com/maorasis/Soccer-Monitor/blob/main/uv.lock). The checked-in
[`soccer_monitor.service`](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor/soccer_monitor.service) starts the daemon. See the
[deployment guide](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor/oracle_daemon_guide.md) for operational instructions.

### Tests

All tests are `unittest` classes under [`tests/`](https://github.com/maorasis/Soccer-Monitor/tree/main/soccer_monitor/tests/) and run offline with
simulated HTTP and Telegram boundaries. Run them from this folder:

```bash
uv run python -m unittest
```

GitHub Actions runs the same command, and the Reem test suite, on every push and pull
request; see [`.github/workflows/tests.yml`](https://github.com/maorasis/Soccer-Monitor/blob/main/.github/workflows/tests.yml).

## Live monitor internals

Verified against the working tree on 2026-11-27.

### Live Under polling

[`live_under_monitor.py`](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor/live_under_monitor.py) composes `RuntimeConfig`, a
shared HTTP session, SQLite alert history, `ApiHealth`, error delivery, and the
Telegram panel in `main`, and runs the daily report task beside the poll loop. `run_polls` checks the saved schedule at 15-second
boundaries. The daily start and end are interpreted in the configured IANA time
zone, with an end earlier than the start representing an overnight window.

`fetch_live_markets` follows Gamma keyset pages and returns tokens for enabled
leagues, live events within four hours of kickoff, open full-game totals and
supported Under lines. `poll_once` fetches CLOB books in batches of 100 tokens;
`lowest_ask` ignores invalid or nonpositive-sized asks. The alert limit comes from
`RuntimeConfig.get_under_threshold` (default or league override). The first observed
ask at or below that limit qualifies, even if the first observation is already low.

Before sending, poll_once claims the alert by token in SQLite and saves the
new message's rearm button. Tapping it schedules one follow-up for that token
after the delay printed on the button. A scheduled poll sends the follow-up
if the market is still eligible and the ask meets the current limit. An explicit
delivery failure releases the first claim or restores the pending rearm for
retry. Uncertain delivery keeps the claim to avoid duplicate notifications.
Delivered and uncertain alerts, including rearmed follow-ups, are added to the
`alert_log` table for the daily report.

The Live Markets screen fetches eligible markets and order books each time it
opens or changes page. It shows the fetch time and reads alert limits from the
current configuration. Only enabled leagues with active games and valid asks at
or below the league-and-line limit appear; missing asks and above-limit markets
are hidden. A failed fetch shows an error.

### Daily report

[`daily_report.py`](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor/daily_report.py) checks once a minute. From 09:00 in the
monitoring timezone it considers the previous local calendar day. It sends a report
only if the report is switched on, at least one alert was logged that day, and that
day's report has not been sent. Only the previous day is considered, so a report
skipped while switched off is not sent days later.

Before sending, it looks up each unresolved alert at the CLOB market endpoint by the
market's condition ID, which Gamma returns as `conditionId`. A token marked
`winner` is a win; another token marked `winner` is a loss; no winner yet is pending.
If a lookup fails, it retries each minute until 10:00, then sends with those alerts
pending. Results assume one share bought at the alerted ask: a win earns `1 - ask`
and a loss costs the ask. Pending alerts are excluded from the total.

An explicit Telegram failure retries the report on the next check. A sent or
uncertain report is recorded in `daily_reports` and not repeated. The menu's
**Daily Report** button switches `RuntimeConfig.daily_report_enabled`; alerts are
logged either way.

### Control panel and health

[`bot_ui.py`](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor/bot_ui.py) authenticates both messages and callbacks using the
configured Telegram chat ID. `start_bot_polling` refuses to start if the ID is
missing or invalid. Aiogram states hold pending threshold, schedule and alert cooldown inputs;
`/cancel` clears the active edit. Under prices are entered in cents, converted
to dollar fractions and persisted by `RuntimeConfig`. Bulk edits write the chosen
line for all supported leagues, including disabled ones.

`ApiHealth` records results of calls the daemon already makes; status display does
not probe upstream services. `ErrorAlertHandler` queues eligible ERROR logs and
applies a call-site cooldown. Telegram send errors are marked to avoid recursively
reporting a Telegram failure through Telegram. Source: [`common.py`](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor/common.py):
`ApiHealth`, `ErrorAlertHandler`, `error_alert_loop` and `TelegramSender`.

## Soccer helper scripts

Verified against the working tree on 2026-11-27.

| Script | Behavior and side effects |
|---|---|
| [`fetch_and_plot_trades.py`](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor/scripts/fetch_and_plot_trades.py) | Finds the configured Gamma market, fetches first-token price history and writes a CSV and plot in the working directory. Its `1 - price` second series is a calculated complement, not a separately fetched order book. |

The script calls external services and writes files. Inspect its constants and
working directory before running it.

The team-name mapping tools (`team_mapper.py`, `check_prefixes.py` and
`team_mapping.json`) served only the removed websocket monitor and were removed with it.

## More information

- [Project README](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor/README.md)
- [Oracle deployment guide](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor/oracle_daemon_guide.md)
