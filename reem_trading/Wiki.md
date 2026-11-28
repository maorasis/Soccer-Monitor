# 🤖 Reem Trading Bot

The bot follows one Polymarket account (the **source**) and copies its new buys, completed sells and resting sell orders into Reem's account.

> ⚠️ The host must configure both accounts and a dedicated Telegram bot first. **Live trading has not yet been validated against real accounts.** The default connection check places no trades.

| I want to… | Go to |
| --- | --- |
| Use the controls | [📱 Controls](#controls) |
| Check that copying works | [📊 Status](#status) |
| Stop trading actions | [⏸️ Pause](#pause) |
| Resume after a break | [▶️ Resume](#resume) |
| Understand messages | [🔔 Notifications](#notifications) |
| Understand trade sizes | [💰 Trading rules](#sizing) |
| Set up the bot | [🛠️ Setup](#setup) |
| Fix a problem | [🧰 Troubleshooting](#troubleshooting) |

<a id="controls"></a>

## 📱 Telegram controls

Open the Reem bot's **private chat** and send `/start`, `/menu` or `/status`. Command buttons appear below the reply.

| Command | Effect |
| --- | --- |
| `/status` | State, last successful account check, last known open-order count |
| `/pause` | Saves Pause and stops new trading actions |
| `/resume` | Explains what resuming will do. Does **not** resume. |
| `/resume confirm` | Accepts catch-up and resumes copying |

Only the configured Telegram user can control the bot, from their private chat. Other users, groups and channels are ignored. Reem doesn't need server access.

<a id="status"></a>

## 📊 Status

```text
Reem copy bot: RUNNING
Last successful account check: 2026-11-27T20:00:00+00:00
Open orders (all Reem orders): 2 (last successful scan)
Existing orders can still fill while paused.
Pending notifications: 0
```

- Times are **UTC** (`+00:00`).
- **Open orders** counts **all** of Reem's open orders, including ones placed outside the bot. It's a snapshot from the last scan, not a live view. Before the first scan it shows **unknown**, not zero.

| State | Meaning | Action |
| --- | --- | --- |
| 🟢 **RUNNING** | Copying is on | Check the last account-check time |
| ⏸️ **PAUSED** | New trading actions blocked | Manage existing orders on Polymarket if needed |
| 🔴 **STOPPED BY ERROR** | A trading or config error needs review | Host inspects logs and saved operations. Resume can't clear it. |
| 🟠 **CONNECTION PROBLEM** | Telegram or account checks unavailable | Trading is suspended until checks succeed |

**RUNNING ≠ trading right now.** The bot may be waiting for source activity or settlement.

<a id="pause"></a>

## ⏸️ Pause

Send `/pause` and wait for the **PAUSED** acknowledgement. Slow networks can delay it.

- **While paused:** no new, changed or cancelled orders, and no closed positions. Account reads, status and notifications continue.
- **Survives** server restarts and process stops. A stop while paused leaves orders alone.

> ⚠️ **Pause is not "cancel all".** Existing orders stay on Polymarket and **can still fill**. A request submitted before the pause was processed can't be undone.
> *Example:* an open sell for 20 shares at 85¢ can still fill while paused if a buyer matches it. Reem can cancel it manually on Polymarket.

<a id="resume"></a>

## ▶️ Resume

1. Send `/resume` and read the catch-up warning.
2. Send `/resume confirm` only if you accept copying the activity missed while paused.
3. Wait for confirmation.

- Resume **catches up from saved history** and can place delayed trades, at the price available **when processed**, not the source's original price. A "new activity only" mode doesn't exist.
  *Example:* the source bought during the break. After `/resume confirm`, the bot attempts that buy at today's price.
- **Never delete the state database** to skip missed activity or clear an error. That breaks the record of which positions and orders the bot manages.

<a id="notifications"></a>

## 🔔 Notifications

Notices are saved locally first, retried until Telegram is reachable, and stamped with UTC time. They also include the asset identifier (the specific market outcome). The examples below are simplified.

| Notice | Example | Meaning |
| --- | --- | --- |
| 📨 **Copy submitted** | `Source: BUY 100 shares at $0.50` / `Reem: market BUY for $25.00` | The order was sent. **Not a fill yet.** Shows both sides with their order references. |
| ✅ **Confirmed fill** | `BUY 50 shares at $0.50 per share` | Actual fill price and shares. One order can produce several. Match references to compare with the source. |
| 🔁 **Sale already covered** | `Source: 40 shares at $0.85` / `Reem: 20 credited shares… No extra sale placed.` | Reem's mirrored sell filled before the source's sale appeared, so it's credited rather than sold twice. Older records may show the price as unavailable. |
| ⏭️ **Skipped** | | E.g. a source sale with no bot-managed holdings, or a sell order expiring too soon to recreate. Insufficient cash or an uncertain submission **stops copying for review** instead. |
| ❌ **Failed / uncertain** | | Rejected, or the outcome is unknown (the exchange may have the order). The bot keeps the record and **doesn't blindly resubmit**. The host must reconcile Reem's orders and history before a restart. `/resume confirm` can't override it. |

**Connection notices:**
- **Lost:** trading waits for a full successful check.
- **Still unavailable:** reminder roughly every 5 minutes.
- **Restored:** a full check-and-copy cycle succeeded.
- **Telegram unavailable:** trading is suspended, since Reem can't control the bot. Notices are delivered once Telegram is back.

Control and error notices take priority over routine ones. Notifications retry independently of orders. A duplicate Telegram message after a timeout is **not** a duplicate trade.

> ⚠️ A dead server can't alert. A power failure, a killed process or a full outage needs external monitoring.

<a id="sizing"></a>

## 💰 Trading rules

**🟢 Buys:** same percentage of **total account value** (positions included, not just cash).

```text
Source: $100 of $2,000 → 5%  →  Reem: 5% of $1,000 = $50
```

Account values are sampled at processing time, and amounts are rounded down to cents. Insufficient cash **stops copying**. It never shrinks the buy.

**💲 Price cap:** copied buys may pay at most **10% above the source fill price**, rounded down to the market's price increment (an 80¢ source buy allows up to 88¢).
- If the lowest ask is higher, the buy is **permanently skipped** and a Telegram notice is sent.
- The signed order also enforces the cap if the price moves during submission.
- Fees are separate.
- A price move or thin liquidity can still cause a fill-or-kill rejection, which **stops copying for review**.

**🔴 Sells:** same **fraction** of the position, applied only to shares the bot manages.

```text
Source sells 40 of 100 (40%)  →  Reem sells 40% of 50 managed = 20
```

A full source exit closes the whole managed position. Unrelated holdings aren't adopted.

**📋 Resting sell orders:** new source sell orders are mirrored at the source's limit price. Edits, cancellations and expiries are followed while copying is on. Reem's order may fill at a different time from the source's.

**Not copied:** orders and positions from before the first live start, and transfers, redemptions, splits and merges.

**🕒 Timing:** polling every 2 s **plus** network and processing time. Settlement waits, history scans and queues add more. There's no guaranteed 2-second execution.

<a id="setup"></a>

## 🛠️ Setup (host)

Full details are in the [technical README](README.md).

1. In `reem_trading/`, run `uv sync --frozen`.
2. Copy `.env.example` to `.env` and run `chmod 600 .env`.
3. Fill in the account credentials locally, plus `REEM_TELEGRAM_BOT_TOKEN` (dedicated bot) and `REEM_TELEGRAM_USER_ID` (Reem's numeric ID).
4. Run the read-only check: `uv run --frozen python copy_bot.py`. Compare the account values and the source sell-order count with Polymarket.
5. Go live only when the setup and rules are acceptable: `uv run --frozen python copy_bot.py --live`.
6. Send `/status` in the private chat.

- **Credentials:** the source needs authenticated order-reading credentials, so its unfilled sell orders are visible. Reem's funded account needs signing credentials and exchange approvals.
- **The read-only check does not test** Telegram, signing, approvals or live execution.
- **Telegram bot:** use a dedicated one, with no webhook and no other process reading its updates.
- **Trading account:** use a dedicated one. Manual trades or transfers can make the share tracking inaccurate.

> 🔐 Never share private keys, API secrets or bot tokens in chat or Git, and never put them in this wiki.

## 💾 History and restarts

`copy_state.sqlite3` stores processed trades, copied orders, managed fills, the Pause state, Telegram update progress and queued notices. A process lock blocks a second instance in the same folder.

**Moving servers:**
1. Stop the old instance.
2. Keep the database and its SQLite sidecar files.
3. Copy the credentials privately.
4. Confirm only one live instance per account.

**Restart behavior:**
- A normal restart keeps pending activity and the Pause state.
- An unpaused normal stop tries to cancel tracked copied sell orders, but only if controls are reachable and there's no recorded trading error.
- A pause, failed cancellation, crash or trading error can leave orders open. Check Polymarket.

<a id="troubleshooting"></a>

## 🧰 Troubleshooting

| Symptom | Action |
| --- | --- |
| No Telegram response | Check the process is running, the credentials are set, and you're the allowed user in the private chat |
| Open orders "unknown" | Wait for the first full account check. Check for connection warnings. |
| No copied trade | Check Pause, the last check time, settlement and notifications. Pre-start activity isn't adopted. |
| Submitted, no fill notice | Submission isn't settlement. Check the order on Polymarket. |
| Outcome unknown | Don't restart repeatedly or delete state. The host must reconcile. |
| Sale while paused | An open exchange order filled. Check its reference. |
| Resume does nothing | `/resume` is only the warning. `/resume confirm` works only with no recorded trading error. |
| Server is gone | Use the hosting console or contact the host. Telegram can't fix a stopped server. |

## 📚 More

- [Setup, limitations and Oracle service](README.md)
- [Credential field examples](.env.example)
- [Soccer Monitor wiki](../soccer_monitor/wiki/Home.md)
