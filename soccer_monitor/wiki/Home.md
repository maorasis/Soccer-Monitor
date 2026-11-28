# ⚽ Football Under Monitor

A Telegram bot that alerts you when live soccer **Under** markets on Polymarket get cheap. It only sends information. It never places orders.

**Before you start:** the bot must be running and your chat configured by the host. Installation: [Oracle deployment guide](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor/oracle_daemon_guide.md). Technical details: **Soccer Monitor internals** at the end of this page.

## 👀 How it works

About every **15 seconds** during your monitoring hours, the bot checks the **lowest ask** (cheapest sell offer) of live, full-game Under markets in your enabled leagues:

| Market | Wins if total goals are | Default limit |
| --- | --- | ---: |
| Under 5.5 | 5 or fewer | 80¢ |
| Under 6.5 | 6 or fewer | 85¢ |
| Under 7.5 | 7 or fewer | 85¢ |

- **Alert rule:** ask **at or below** the limit → alert with match, league, line, ask, limit and a Polymarket link.
  *Example:* Under 5.5 at 79¢, limit 80¢ → alert.
- **First look counts:** the price doesn't need to drop while the bot watches. Already low on the first check is enough.
- **Once per market:** each market alerts once, even across restarts, unless you [rearm](#-rearm-an-alert) it.
- **Prices move:** prices and order books can change between checks and before you open the link.

## 🚀 Control panel

1. Open the bot's chat in Telegram and send `/start` or `/menu`.
2. Tap buttons. **Main menu** / **Back** return.
3. Send `/cancel` to abort a threshold or schedule edit.

Only the configured chat can control the bot. No response? Ask the host to check the chat ID.

## 🏆 Leagues

**Manage Leagues** → tap a league to toggle it (✅ on / ❌ off).

- Only enabled leagues are checked. Disabling keeps the league's saved limits.
- Supported leagues include **Primeira Liga** 🇵🇹 and **Eredivisie** 🇳🇱. On bots set up before they were added, they start disabled.

## 🎯 Alert limits

The limit is the highest ask that triggers an alert. Enter it in **cents**: `80` = 80¢, `85.5` = 85.5¢. Allowed range: above 0, up to 100.

| Goal | Steps |
| --- | --- |
| One league | **Edit Thresholds** → league → line → send value → wait for confirmation |
| One line, all leagues | **Edit Thresholds → All leagues** → line → send value → wait for confirmation |
| View limits | **View Thresholds** (✏️ = custom, unmarked = default; also shows enabled state; **Next** / **Previous** to browse) |

"All leagues" overwrites that line in **every** league, disabled ones included. Other lines stay as they are.
*Example:* Under 6.5 → `88` sets 88¢ for Under 6.5 everywhere. Under 5.5 and 7.5 don't change.

## ⚽ Live Markets

**Live Markets** shows active games in enabled leagues, listing only Under markets whose ask is **at or below** that league-and-line limit. Markets above the limit or with no ask are hidden.
*Example:* with an 80¢ limit, 79¢ and 80¢ appear; 82¢ doesn't.

- Shows the current limit and the fetch time.
- Every open or page change fetches again, even outside monitoring hours. Prices can change after the fetch.
- Viewing never sends alerts. A listed market may already have alerted.
- A failed fetch shows an error.

## 🔁 Rearm an alert

Tap **Rearm in 15 min** on an alert to get **one** follow-up for that market line:

- The countdown starts when you tap. Tapping again doesn't restart it.
- After it ends, the first poll that finds the market live and the ask at or below the current limit sends the follow-up. Until then, it stays pending while the market is live.
- The follow-up has its own rearm button.

**Change the delay:** **Alert Cooldown** in `/menu` → whole number from **2 to 1,440** minutes. This applies to future alerts only. Existing buttons and scheduled rearms keep their timing.

Rearms survive restarts (stored in `live_under_alerts.sqlite3`). Keep that file when moving the server.

## 🕒 Monitoring hours

**Monitoring Schedule** shows the hours, whether monitoring is active now, and the next start if not. Default: **14:00–23:00 Asia/Jerusalem**.

To change: **Change schedule** → send `start end timezone`, e.g. `14:00 23:00 Asia/Jerusalem` → wait for confirmation. The change applies from the next poll.

- 24-hour times. IANA timezone name, e.g. `Europe/London`.
- Start and end must differ. Overnight works: `22:00 02:00 Asia/Jerusalem`.

## 🗓 Daily report

After a day with at least one alert, you get a summary at **09:00** (monitoring timezone). No alerts, no report.

- **Per alert:** time, match, line, alerted ask and result: **won ✅ / lost ❌ / pending ⏳**.
- **Total:** as if you'd bought one share at each alerted ask. A win earns 100¢ − ask. A loss costs the ask.
  *Example:* 80¢ win + 85¢ loss → +20¢ and −85¢ = **−65¢ on 165¢**.
- **On/off:** **Daily Report** in `/menu` (the button shows the current state). Alerts are still recorded while it's off. Reports missed while it's off aren't sent later. Exception: turning it back on the same day still sends the previous day's report.

## 🔔 Status, testing and troubleshooting

- **Status:** active leagues, schedule, default limits, number of custom limits, daily report on/off, and recorded API health.
- **Send Test Notification:** a labeled test through the alert channel. It doesn't count as an alert. It reports success, failure or uncertain.

**No alerts?** Check that:
1. The server is running.
2. The league is enabled.
3. You're within monitoring hours.
4. A market's ask is at or below its limit. Use **Live Markets** and its timestamp.
5. The market hasn't already alerted (see **Status**).
6. Delivery works. Use the test notification.

---

## 🛠️ Soccer Monitor internals

Verified against the code on 2026-11-27.

### Overview

One daemon, [`live_under_monitor.py`](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor/live_under_monitor.py), polls live full-game Under markets, compares asks with per-league limits, and sends alerts and a daily report. It talks to the **Polymarket Gamma and CLOB HTTP APIs** and **Telegram**.

| Module | Responsibility |
|---|---|
| [`live_under_monitor.py`](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor/live_under_monitor.py) | Market discovery, order-book polling, alert claims and rearms, `main` |
| [`daily_report.py`](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor/daily_report.py) | Alert log, outcome lookups, the once-a-day report |
| [`bot_ui.py`](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor/bot_ui.py) | Telegram control panel |
| [`config_manager.py`](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor/config_manager.py) | Supported leagues and persisted settings |
| [`common.py`](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor/common.py) | Telegram delivery, API health, error alerts, market-title parsing |

The earlier websocket monitor (`soccer_monitor.py`, with API-Football) and its team-mapping tools (`team_mapper.py`, `check_prefixes.py`, `team_mapping.json`) were removed on 2026-11-27. They remain in the Git history.

### Configuration and state

| Item | Details |
|---|---|
| `config_state.json` | Loaded by `RuntimeConfig`: active leagues, per-league Under limits, alert cooldown, schedule, daily-report switch. Held in memory and saved atomically (temp file + replace). A failed save restores the previous in-memory value. Not watched for external edits. Legacy fields `global_threshold` and `league_thresholds` are ignored and dropped on the next save. |
| `live_under_alerts.sqlite3` | Sits beside the config. Holds claimed token IDs, scheduled rearms, `alert_log`, and the days whose report was sent (`daily_reports`). |
| `soccer_monitor/.env` | Loaded with `python-dotenv` by `common.py`. Variables already set take precedence. Requires `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Never commit it. |

⚠️ There is no inter-process locking or automatic reload. Never run two daemons with the same config path or bot token.

### Live Under polling

- **Startup:** `main` wires `RuntimeConfig`, a shared HTTP session, the SQLite history, `ApiHealth`, error delivery and the Telegram panel, and runs the daily-report task beside the poll loop.
- **Schedule:** `run_polls` checks the saved schedule at 15-second boundaries. Start and end use the configured IANA timezone. An end earlier than the start means an overnight window.
- **Discovery:** `fetch_live_markets` follows Gamma keyset pages. It returns tokens for enabled leagues, live events within 4 hours of kickoff, open full-game totals, and supported Under lines.
- **Prices:** `poll_once` fetches CLOB books in batches of 100 tokens. `lowest_ask` ignores invalid or non-positive-size asks.
- **Limit:** comes from `RuntimeConfig.get_under_threshold`, either the default or the league override. The first observed ask at or below the limit qualifies.
- **Claims and rearms:** before sending, the alert is claimed by token in SQLite, and the message's rearm button is saved. A rearm schedules one follow-up after the delay printed on the button. That follow-up is sent if the market is still eligible and the ask meets the current limit.
- **Delivery outcome:**
  - An explicit failure releases the claim, or restores the pending rearm, so it can be retried.
  - An uncertain result keeps the claim, to avoid duplicates.
  - Delivered and uncertain alerts, follow-ups included, are written to `alert_log`.
- **Live Markets screen:** fetches markets and books on every open or page change, and reads limits from the current config. It shows only enabled leagues with active games and a valid ask at or below the league-and-line limit. A failed fetch shows an error.

### Daily report

[`daily_report.py`](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor/daily_report.py) checks once a minute.

1. **When:** from 09:00 (monitoring timezone) it looks at the **previous** local day only. It sends if the report is on, that day has at least one logged alert, and its report wasn't sent yet. A day skipped while the report was off is never sent later.
2. **Outcomes:** each unresolved alert is looked up at the CLOB market endpoint by condition ID (Gamma's `conditionId`). This alert's token marked `winner` = win. Another token marked `winner` = loss. No winner yet = pending.
3. **Retries:** failed lookups retry every minute until 10:00. After that, the report is sent with those alerts pending.
4. **Math:** one share at the alerted ask. A win earns `1 - ask`, a loss costs the ask. Pending alerts are excluded from the total.
5. **Delivery:** an explicit Telegram failure retries on the next check. A sent or uncertain report is recorded in `daily_reports` and not repeated.

The menu's **Daily Report** button toggles `RuntimeConfig.daily_report_enabled`. Alerts are logged either way.

### Control panel and health

- **Auth:** [`bot_ui.py`](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor/bot_ui.py) checks the configured chat ID on messages and callbacks. `start_bot_polling` refuses to start if the ID is missing or invalid.
- **Inputs:** Aiogram states hold pending threshold, schedule and cooldown inputs, and `/cancel` clears them. Prices are entered in cents, stored as dollar fractions, and saved by `RuntimeConfig`. Bulk edits write the line for all supported leagues, disabled ones included.
- **Health:** `ApiHealth` records the results of calls the daemon already makes. Status never probes services.
- **Error alerts:** `ErrorAlertHandler` queues eligible ERROR logs with a call-site cooldown. Telegram send errors are marked so a Telegram failure isn't reported through Telegram.
- **Source:** [`common.py`](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor/common.py): `ApiHealth`, `ErrorAlertHandler`, `error_alert_loop`, `TelegramSender`.

### Deployment and tests

- **Dependencies and Python compatibility:** [pyproject.toml](https://github.com/maorasis/Soccer-Monitor/blob/main/pyproject.toml), with versions locked in [uv.lock](https://github.com/maorasis/Soccer-Monitor/blob/main/uv.lock).
- **Service:** [`soccer_monitor.service`](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor/soccer_monitor.service) starts the daemon. Operations are covered in the [deployment guide](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor/oracle_daemon_guide.md).
- **Tests:** `unittest` classes in [`tests/`](https://github.com/maorasis/Soccer-Monitor/tree/main/soccer_monitor/tests/). They run offline, with HTTP and Telegram simulated. From `soccer_monitor/` run `uv run python -m unittest`. [GitHub Actions](https://github.com/maorasis/Soccer-Monitor/blob/main/.github/workflows/tests.yml) runs the same command, plus the Reem suite, on every push and pull request.

### Helper script

[`fetch_and_plot_trades.py`](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor/scripts/fetch_and_plot_trades.py) finds the configured Gamma market, fetches its first token's price history, and writes a CSV and a plot to the working directory. The second series (`1 - price`) is calculated, not a fetched order book. It calls external services and writes files, so check its constants and working directory before running it.

### More information

- [Soccer README](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor/README.md)
- [Oracle deployment guide](https://github.com/maorasis/Soccer-Monitor/blob/main/soccer_monitor/oracle_daemon_guide.md)
