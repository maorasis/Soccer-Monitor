# Live monitor internals

Verified against the working tree on 2026-11-27.

## Live Under polling

[`live_under_monitor.py`](../live_under_monitor.py) composes `RuntimeConfig`, a
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

## Daily report

[`daily_report.py`](../daily_report.py) checks once a minute. From 09:00 in the
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

## Control panel and health

[`bot_ui.py`](../bot_ui.py) authenticates both messages and callbacks using the
configured Telegram chat ID. `start_bot_polling` refuses to start if the ID is
missing or invalid. Aiogram states hold pending threshold, schedule and alert cooldown inputs;
`/cancel` clears the active edit. Under prices are entered in cents, converted
to dollar fractions and persisted by `RuntimeConfig`. Bulk edits write the chosen
line for all supported leagues, including disabled ones.

`ApiHealth` records results of calls the daemon already makes; status display does
not probe upstream services. `ErrorAlertHandler` queues eligible ERROR logs and
applies a call-site cooldown. Telegram send errors are marked to avoid recursively
reporting a Telegram failure through Telegram. Source: [`common.py`](../common.py):
`ApiHealth`, `ErrorAlertHandler`, `error_alert_loop` and `TelegramSender`.
