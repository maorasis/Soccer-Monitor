# 🔄 Reload the bots on Oracle Cloud after Git changes

Use this guide after your changes have been **committed and pushed to GitHub**.
A push updates GitHub; the Oracle server needs a pull and a service restart to run the new code.

| Setting | Value |
| --- | --- |
| Server | `151.145.94.231` |
| SSH shortcut on your computer | `polymarket-new` |
| Repository on Oracle | `/home/ubuntu/soccer_monitor` |
| Soccer service | `soccer_monitor` |
| SMA service | `sma_crossover_bot` |
| Reem service, once installed | `reem_trading` |

The soccer and SMA services use the root **`venv/`**. Reem uses **`reem_trading/.venv/`**.
The root `.venv/` that `uv` uses locally is different from the server's `venv/`.

## 1. 💻 Connect from your computer

1. Open a terminal on your computer.
2. Connect to Oracle:

   ```bash
   ssh polymarket-new
   ```

   If that shortcut is unavailable, use your private key file:

   ```bash
   ssh -i ~/.ssh/oracle_polymarket_new ubuntu@151.145.94.231
   ```

3. Open the checkout **inside the SSH session**:

   ```bash
   cd /home/ubuntu/soccer_monitor
   ```

All remaining commands run **on Oracle**, not on your computer.

## 2. 📦 One-time update for the current Oracle checkout

**Run this section once for the deployment checked on November 27, 2026.** Its folders were copied manually before the reorganization was pushed to GitHub. Git therefore reports deleted old files and untracked new folders.

After this section succeeds, use section 3 for future updates. This procedure updates soccer and SMA only; it does not install or start Reem.

1. Stop both alert services:

   ```bash
   sudo systemctl stop soccer_monitor sma_crossover_bot
   ```

2. Create a private backup folder:

   ```bash
   umask 077
   reload_backup=$(mktemp -d /home/ubuntu/soccer-reload.XXXXXXXX)
   ```

3. Back up the bot folders, including local credentials and stopped databases:

   ```bash
   cp -a soccer_monitor sma_bot reem_trading "$reload_backup/"
   ```

   **Stop if the copy fails.** The backup contains secrets; keep it private and outside Git.

4. Display the backup location:

   ```bash
   printf '%s\n' "$reload_backup"
   ```

5. Save the manually copied code in a local Git stash:

   ```bash
   git stash push --include-untracked -m "Oracle manual layout before first Git update"
   ```

   A **stash** is a local saved copy of edits. This command excludes ignored credentials, virtual environments and runtime state. The private folder backup also protects those files. Do not use `--all`.

6. Pull the published layout:

   ```bash
   git pull --ff-only origin main
   ```

   **Stop if this fails.** Do not run `git reset --hard` or `git clean`. Do not apply the stash after a successful pull: it contains the older manually copied layout.

7. Refresh soccer and SMA dependencies:

   ```bash
   ./venv/bin/python -m pip install -r requirements.txt
   ```

8. Install the updated service files:

   ```bash
   sudo install -m 644 soccer_monitor/soccer_monitor.service sma_bot/sma_crossover_bot.service /etc/systemd/system/
   ```

9. Reload systemd, the Linux service manager:

   ```bash
   sudo systemctl daemon-reload
   ```

10. Start both alert services:

    ```bash
    sudo systemctl start soccer_monitor sma_crossover_bot
    ```

11. Check their status:

    ```bash
    systemctl status soccer_monitor sma_crossover_bot --no-pager
    ```

12. Read their recent logs:

    ```bash
    journalctl -u soccer_monitor -u sma_crossover_bot -n 50 --no-pager
    ```

Keep the stash and private backup until the bots are working. This section does not restore older trading state or replace any `.env` file.

## 3. 🚀 Routine soccer and SMA updates

Use this section after the one-time procedure. Run each step separately; **stop on an error**. If Reem is also running, pause and stop it using section 4 before pulling changes into this shared checkout.

1. Open the checkout:

   ```bash
   cd /home/ubuntu/soccer_monitor
   ```

2. Check for local code edits:

   ```bash
   git status --short
   ```

   Expected: no output. If files appear, preserve and review them before pulling. Do not discard them to make the update work.

3. Stop both services:

   ```bash
   sudo systemctl stop soccer_monitor sma_crossover_bot
   ```

   They share dependencies, so stop both before updating that environment.

4. Pull GitHub changes:

   ```bash
   git pull --ff-only origin main
   ```

5. Refresh dependencies:

   ```bash
   ./venv/bin/python -m pip install -r requirements.txt
   ```

6. Install the service definitions:

   ```bash
   sudo install -m 644 soccer_monitor/soccer_monitor.service sma_bot/sma_crossover_bot.service /etc/systemd/system/
   ```

7. Reload systemd:

   ```bash
   sudo systemctl daemon-reload
   ```

8. Start both services:

   ```bash
   sudo systemctl start soccer_monitor sma_crossover_bot
   ```

9. Check their status:

   ```bash
   systemctl status soccer_monitor sma_crossover_bot --no-pager
   ```

10. Read their recent logs:

    ```bash
    journalctl -u soccer_monitor -u sma_crossover_bot -n 50 --no-pager
    ```

11. Send `/menu` to the soccer Telegram bot.

Soccer polling follows its saved daily schedule. SMA scans follow their scheduled times; starting the service does not force a scan. `active (running)` confirms the process is alive, not that market-data requests or Telegram delivery succeeded.

## 4. 🤖 Reem updates — only after its initial installation

**Reem was not installed or running on Oracle when this guide was written.** Use [Reem's installation instructions](reem_trading/README.md#oracle-installation) for its first deployment. Copied buys enforce a 10% price cap and permanently skip an already-overpriced buy; see Reem's README for the execution rules.

For an already installed Reem service:

1. Send `/pause` from Reem's authorized private Telegram chat.
2. Wait for the bot's pause acknowledgement.
3. Stop the service:

   ```bash
   sudo systemctl stop reem_trading
   ```

   Acknowledged pause prevents shutdown cancellations. Existing exchange orders can still fill. If you cannot get an acknowledgement, stopping an unpaused bot may cancel its tracked sell orders.

4. Back up `reem_trading/.env` and `reem_trading/copy_state.sqlite3`, including any `-wal` and `-shm` sidecars, to a private folder while the service is stopped.
5. Pull the new code using section 3's update procedure.

   If the same push changes Reem and the alert bots, pause and stop Reem **before** pulling any shared checkout changes.

6. Refresh Reem's separate environment:

   ```bash
   cd /home/ubuntu/soccer_monitor/reem_trading
   uv sync --frozen
   ```

7. Run its read-only account check:

   ```bash
   ./.venv/bin/python copy_bot.py
   ```

   This does not submit or cancel orders. It does not verify Telegram controls, Reem's signing key or trading approvals.

8. Install its service definition:

   ```bash
   sudo install -m 644 reem_trading.service /etc/systemd/system/
   ```

9. Reload systemd:

   ```bash
   sudo systemctl daemon-reload
   ```

10. Start the service with its saved pause still active:

    ```bash
    sudo systemctl start reem_trading
    ```

11. Check its status and logs:

    ```bash
    systemctl status reem_trading --no-pager
    journalctl -u reem_trading -n 50 --no-pager
    ```

12. Send `/status` and confirm it reports `PAUSED`.
13. Send `/resume` when ready to review the catch-up warning.
14. Send `/resume confirm` only if you accept copying activity missed during the pause.

Reem preserves its history and pause in SQLite. Never delete the database to clear an error. A trading error needs order reconciliation before restart; a running process may still report `STOPPED BY ERROR` and refuse trades. A restart without a saved pause can resume copying automatically.

## 5. 🔐 Files to preserve

| Local file | Why it matters |
| --- | --- |
| `soccer_monitor/.env` | Soccer credentials |
| `soccer_monitor/config_state.json` | Leagues, limits and schedule |
| `soccer_monitor/live_under_alerts.sqlite3` and sidecars | Sent alerts and scheduled rearms |
| `sma_bot/.env` | SMA credentials |
| `sma_bot/sma_state.json` | SMA alert history |
| `reem_trading/.env` | Account keys and Telegram credentials |
| `reem_trading/copy_state.sqlite3` and sidecars | Copied-trade history, order links and pause state |

These files are ignored by Git. Keep them in their bot folders. Do not overwrite them with `.env.example`, commit backups, or run two live Reem instances on different hosts.

## 6. 🛠️ When an update fails

| Symptom | Next action |
| --- | --- |
| Pull would overwrite local changes or untracked files | Preserve the changes; use section 2 only for the known initial manual layout |
| GitHub authentication error | Fix the server's GitHub access; logging in on your computer does not authenticate Oracle |
| Dependency installation fails | Keep affected services stopped until the environment is repaired |
| Service fails to start | Read `journalctl -u SERVICE_NAME -n 80 --no-pager` |
| Telegram conflict / HTTP 409 | Check for another process using the same bot token |
| Reem reports a trading error | Inspect actual orders and fills before reconciliation; do not delete state or blindly restart |

To leave the SSH session, run `exit`.
