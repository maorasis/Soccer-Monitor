# Football_Under_Monitor

For maintainers, see the [developer documentation](docs/README.md), including
helper scripts.

`live_under_monitor.py` polls Polymarket every 15 seconds during its configured daily window, which defaults to 14:00 up to 23:00 Israel local time. It looks for the lowest ask, the cheapest offer to sell, on these full-game Under markets in live matches from the enabled leagues.

| Full-game total | Default alert limit |
| --- | ---: |
| Under 5.5 goals | 80¢ |
| Under 6.5 goals | 85¢ |
| Under 7.5 goals | 85¢ |

The first poll at or below the limit triggers a Telegram alert. Tap **Rearm in 15 min** to allow one follow-up for that exact line. After the delay, the first scheduled poll that finds the market still live and at or below your current limit sends the follow-up. Use **Alert Cooldown** in the bot menu to change the delay shown on future alerts.

**Live Markets** fetches markets and asks when you open the view or change pages. Keep the SQLite alert history file when moving the service to a new host so scheduled rearms survive the move.

## Daily report

On days when at least one alert was sent, the bot sends a summary the next morning at 09:00 in the monitoring timezone. It lists each alert with its time, match, line and ask, and whether the Under won, lost or is still pending. It also totals the result as if one share had been bought at each alerted ask. No report is sent for a day without alerts.

Use **Daily Report** in the bot menu to switch the report off or back on. Alerts are still recorded while it is off. A report missed while it was off is not sent later, except that switching it back on the same day still sends the previous day's report.

## Setup & Configuration

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in the soccer monitor’s `soccer_monitor/.env` file. Market prices and match status come from Polymarket’s public APIs.

## Changing a threshold in Telegram

1. Send `/menu` to your bot.
2. Tap **Edit Thresholds**.
3. Choose a league.
4. Choose an Under line.
5. Enter the limit in cents, for example `80`.

**Manage Leagues** controls which leagues the monitor checks, including Portugal's Primeira Liga and the Dutch Eredivisie. A new installation enables both by default; an existing `config_state.json` keeps its saved league list, so enable them in **Manage Leagues**. League selections and limits are saved in `config_state.json` and apply without a restart.

The main menu also offers **View Thresholds**, including defaults and custom values, and **Live Markets**, showing only active games in enabled leagues whose freshly fetched asks are at or below their current limits. Markets above the limit or without an ask are hidden. The timestamp indicates when prices were fetched; opening the view does not send market alerts. **Send Test Notification** checks the configured alert delivery channel without creating a market alert.

Use **Monitoring Schedule** to set daily start and end times and an IANA timezone (for example `14:00 23:00 Asia/Jerusalem`). Overnight schedules are supported. Changes are saved and take effect on the next poll; the schedule screen shows the next start when monitoring is inactive.

To set one Under line for every supported league at once, choose **All leagues** in the threshold menu, select the line, and enter the limit in cents. This replaces existing limits for that line, including in disabled leagues; other lines are unchanged.

## Running the Daemon

Dependencies are declared in the repository root’s `pyproject.toml` and pinned in `uv.lock`. Run these commands from the repository root:

```bash
uv sync
cd soccer_monitor
uv run python live_under_monitor.py
```

## Deployment

For `systemd` deployment on an Oracle Cloud VM, see
[`oracle_daemon_guide.md`](oracle_daemon_guide.md) for provisioning, service installation, log
access, and day-to-day operations.

## Wiki source

The [wiki page](wiki/Home.md) contains the consolidated soccer monitor guide, prepared for a future update. To publish it, paste its contents into the GitHub wiki Home page, or copy `Home.md` into a checkout of `https://github.com/maorasis/Soccer-Monitor.wiki.git` and commit and push there. Existing published wiki pages must be deleted separately if you want Home to be the only page. The local content was based on wiki commit `3462cb9`.

## Tests and helper scripts

Run all tests from this folder:

```bash
uv run python -m unittest
```

They run offline and send no messages. GitHub Actions runs them on every push and pull request.
