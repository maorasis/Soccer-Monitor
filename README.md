# Polymarket Live Soccer Under Monitor

For maintainers, see the [developer documentation](docs/README.md), including
the separate websocket soccer monitor, stock bot, and helper scripts.

`live_under_monitor.py` polls Polymarket once a minute during its configured daily window, which defaults to 14:00 up to 23:00 Israel local time. It looks for the lowest ask, the cheapest offer to sell, on these full-game Under markets in live matches from the enabled leagues.

| Full-game total | Default alert limit |
| --- | ---: |
| Under 5.5 goals | 80¢ |
| Under 6.5 goals | 85¢ |
| Under 7.5 goals | 85¢ |

The first poll at or below the limit triggers a Telegram alert. The monitor stores alert history in `live_under_alerts.sqlite3` so each market alerts at most once across restarts. Keep that file when moving the service to a new host.

## Setup & Configuration

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in the project’s `.env` file. Market prices and match status come from Polymarket’s public APIs.

## Changing a threshold in Telegram

1. Send `/menu` to your bot.
2. Tap **Edit Thresholds**.
3. Choose a league.
4. Choose an Under line.
5. Enter the limit in cents, for example `80`.

**Manage Leagues** controls which leagues the monitor checks. League selections and limits are saved in `config_state.json` and apply without a restart.

The main menu also offers **View Thresholds**, including defaults and custom values, and **Live Markets**, showing asks from the last successful poll alongside current limits. The timestamp indicates when prices were checked; opening the view does not fetch new prices. **Send Test Notification** checks the configured alert delivery channel without creating a market alert.

Use **Monitoring Schedule** to set daily start and end times and an IANA timezone (for example `14:00 23:00 Asia/Jerusalem`). Overnight schedules are supported. Changes are saved and take effect on the next poll; the schedule screen shows the next start when monitoring is inactive.

To set one Under line for every supported league at once, choose **All leagues** in the threshold menu, select the line, and enter the limit in cents. This replaces existing limits for that line, including in disabled leagues; other lines are unchanged.

## Running the Daemon

Dependencies are declared in `pyproject.toml` and pinned in `uv.lock`.

```bash
uv sync
uv run python live_under_monitor.py
```

## Deployment

Both daemons run under `systemd` on an Oracle Cloud Always Free VM. See
[`oracle_daemon_guide.md`](oracle_daemon_guide.md) for provisioning, service installation, log
access, and day-to-day operations.
