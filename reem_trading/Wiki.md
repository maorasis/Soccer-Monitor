# 🤖 Reem Trading Bot Wiki

Reem’s bot follows one Polymarket account and copies its new buys, completed sells, and resting sell orders into Reem’s account. This guide explains the Telegram controls, notifications, and trading rules.

**Before using it:** the host must configure both accounts and a dedicated Telegram bot for Reem. Live trading has not yet been validated against real accounts. The default connection check does not place trades.

## 🧭 Quick navigation

| I want to… | Go to… |
| --- | --- |
| Open the controls | [📱 Telegram controls](#controls) |
| Check whether copying is working | [📊 Status](#status) |
| Stop trading actions | [⏸️ Pause](#pause) |
| Resume after a break | [▶️ Resume](#resume) |
| Understand messages | [🔔 Notifications](#notifications) |
| Understand trade sizes | [💰 Trading rules](#sizing) |
| Set up the bot | [🛠️ Setup](#setup) |
| Resolve a problem | [🧰 Troubleshooting](#troubleshooting) |

<a id="controls"></a>

## 📱 Telegram controls

1. Open the dedicated Reem bot’s **private conversation** in Telegram.
2. Send `/start`, `/menu`, or `/status`.
3. Use the command buttons below the reply.

| Command | What it does |
| --- | --- |
| `/status` | Shows operating state, last successful account check, and the last known open-order count. |
| `/pause` | Saves Pause and stops new trading actions. |
| `/resume` | Explains what resuming will do; does not resume yet. |
| `/resume confirm` | Explicitly accepts catch-up and resumes copying. |

Only the configured Telegram user can control the bot, from that user’s private chat. Commands from other users, groups, and channels are ignored. Reem does not need server access to use these controls.

<a id="status"></a>

## 📊 Read the status screen

An example response looks like this:

```text
Reem copy bot: RUNNING
Last successful account check: 2026-11-27T20:00:00+00:00
Open orders (all Reem orders): 2 (last successful scan)
Existing orders can still fill while paused.
Pending notifications: 0
```

Times use **UTC**, shown by `+00:00`. The open-order count covers all open orders returned for Reem’s account, including orders placed outside this bot. It is a snapshot, not a continuously live exchange view. Before the first successful scan, the count is **unknown**, rather than zero.

| State | Meaning | What to do |
| --- | --- | --- |
| 🟢 **RUNNING** | Copying is enabled. | Check the last successful account-check time. |
| ⏸️ **PAUSED** | New trading actions are blocked. | Manage existing positions and orders directly on Polymarket if needed. |
| 🔴 **STOPPED BY ERROR** | A trading or configuration error requires review. | Ask the host to inspect logs and saved operations. Resume cannot clear the error. |
| 🟠 **CONNECTION PROBLEM** | Telegram controls or account checks are unavailable. | New trading actions are suspended until checks succeed again. |

**RUNNING does not mean an order is being placed right now.** The bot may be waiting for new source activity or settlement confirmation.

<a id="pause"></a>

## ⏸️ Pause copying

1. Send `/pause`.
2. Wait for the **PAUSED** acknowledgement.

Once Pause is processed, the bot does **not** place orders, change orders, cancel orders, or close positions. It continues reading account information and serving status and notifications. Pause survives a server restart.

> ⚠️ **Existing orders remain on Polymarket and may still fill.** Pause is not a “cancel all orders” button. A request already submitted before Pause is processed cannot be undone. Slow network calls can delay the acknowledgement.

**Example:** Reem has an open sell order for 20 shares at 85¢. She pauses the bot. The bot leaves that order alone. Polymarket can still execute it if a buyer matches it; Reem can cancel it manually in Polymarket if she wishes.

If the process is stopped while paused, it also leaves those orders alone.

<a id="resume"></a>

## ▶️ Resume copying

1. Send `/resume`.
2. Read the catch-up warning.
3. Send `/resume confirm` only if you accept copying activity missed while paused.
4. Wait for confirmation.

**The current resume mode catches up from the saved history.** It can place delayed trades. A “new activity only” resume mode is not implemented. Merely pressing `/resume` does not enable copying.

**Example:** The source bought a position during Reem’s break. After `/resume confirm`, the bot can attempt the corresponding buy at the price available when it processes that activity, not the source’s original price.

Do not delete the state database to skip missed activity or clear an error. That can break the record of which positions and orders the bot manages.

<a id="notifications"></a>

## 🔔 Understand notifications

Notifications are recorded locally before delivery and retried when Telegram is reachable. Each notice includes its recorded UTC time. Submission notices and confirmed-fill notices are different events.

### 📨 Copied order submitted

```text
Submitted copy (not yet a confirmed fill)
Source: BUY 100 shares at $0.50
Reem: market BUY for $25.00
Reem order: …
```

The bot records the source side, size, price, and order reference, together with Reem’s requested order and reference. These examples simplify the message formatting; actual messages also include the asset identifier—the code identifying the specific market outcome.

### ✅ Confirmed execution

```text
Confirmed fill: BUY 50 shares at $0.50 per share.
Asset: …; order: …
```

This reports the actual confirmed follower fill price and share count. An order can generate several fill notices. Match the order references to compare the source submission details with Reem’s execution.

### 🔁 Sale already covered

```text
Source SELL already covered by confirmed mirrored fills
Source: 40 shares at $0.85
Reem: 20 credited shares at average price $0.850000
No extra sale placed.
```

A copied sell order may fill before the source’s completed sale appears. The bot credits that earlier execution instead of selling the same shares twice. Older state records created before fill prices were stored may show the historical price as unavailable.

### ⏭️ Skipped activity

The bot explains cases such as a source sale with no bot-managed holdings, or a sell order whose expiry is too close to recreate. Some conditions, such as insufficient cash or an uncertain submission, **stop copying for review** instead of silently skipping the trade.

### ❌ Failed or uncertain copying

A rejection or unknown submission outcome produces an error notification. An unknown outcome means the exchange may have received the order even though the bot did not receive a reliable response. The bot retains its record and does not blindly submit it again.

Ask the host to reconcile Reem’s orders and history before restarting. `/resume confirm` cannot override a trading error in the running process.

### 📡 Connection alerts

- **Connection lost:** new trading actions wait for a complete successful check.
- **Still unavailable:** reminders are limited to approximately once every five minutes.
- **Connection restored:** the full account-check and copying cycle has succeeded again.
- **Telegram unavailable:** new trading actions are suspended because Reem cannot reliably control the bot. Notices are delivered when Telegram becomes reachable again.

Control acknowledgements and trading-error notices are prioritized over routine trade messages. Notifications are retried independently of order submission. Telegram can display a duplicate notification after an ambiguous delivery timeout; that does not mean the bot placed a duplicate trade.

> ⚠️ A disconnected server cannot immediately send a Telegram alert. A power failure, force-killed process, or total server outage needs separate external monitoring; this bot cannot guarantee an alert about its own disappearance.

<a id="sizing"></a>

## 💰 How trade sizes work

### 🟢 Completed buys

The bot uses the source trade’s share of **total account value**, including invested positions—not just available cash.

```text
Source account value: $2,000
Source buy:             $100 → 5% of the account
Reem account value:   $1,000
Reem requested buy:     $50 → 5% of her account
```

Account values are sampled when the copy is processed. Dollar amounts are rounded down to cents. Insufficient available cash stops copying rather than silently shrinking the requested allocation.

### 🔴 Partial and full sells

The bot follows the fraction of the source position sold, using only the shares it manages for Reem.

```text
Source sells 40 of 100 shares → sells 40%
Reem has 50 managed shares   → requested sale is 20 shares
```

A full source exit requests a full exit of the corresponding managed position. Unrelated initial holdings are not automatically adopted.

### 📋 Resting sell orders

A resting sell order waits on Polymarket for a matching buyer. The bot mirrors new source sell orders at their source limit prices and follows observed changes, cancellations, and expiries while copying is enabled. Reem’s order can execute at a different time from the source order.

Orders and positions that already existed before the first live start are not automatically adopted. Non-trading actions such as transfers, redemptions, splits, and merges are not copied.

### 🕒 Timing and price differences

Polling defaults to two seconds **plus network and processing time**. Settlement waits, history scans, and queued operations add delay. This is not a guaranteed two-second execution time.

The proposed **10% maximum increase for copied buys is not implemented yet**; behavior above that cap still needs a decision. Current market copies have no extra percentage price cap. Do not enable live trading on the assumption that the proposed cap is active.

<a id="setup"></a>

## 🛠️ Setup for the host

Use the [technical README](README.md) for the full account-access and deployment details.

1. Open the `reem_trading/` folder.
2. Run `uv sync --frozen`.
3. Copy `.env.example` to `.env`.
4. Restrict the file with `chmod 600 .env`.
5. Fill in account credentials using a local editor.
6. Add `REEM_TELEGRAM_BOT_TOKEN` for a dedicated Telegram bot.
7. Add `REEM_TELEGRAM_USER_ID` for Reem’s numeric user ID.
8. Run `uv run --frozen python copy_bot.py` for the read-only account connection check.
9. Compare the reported account values and source sell-order count with Polymarket.
10. Start live copying with `uv run --frozen python copy_bot.py --live` only when the account setup and current trading rules are acceptable.
11. Open the Telegram private chat and send `/status`.

The source needs authenticated order-reading credentials to expose unfilled sell orders. Reem’s funded account needs signing credentials and exchange approvals. The read-only check does not test Telegram delivery, signing, approvals, or live execution.

Use a dedicated Telegram bot, without an active webhook or a second process consuming its updates. Use a dedicated Reem trading account: mixing manual trading or transfers with managed shares can make inventory tracking inaccurate.

**Never share private keys, API secrets, or Telegram bot tokens in chat or Git.** None of these credentials belong in this wiki.

## 💾 Saved history and restarts

`copy_state.sqlite3` keeps processed trades, copied orders, managed fills, Pause state, Telegram update progress, and queued notices. The process lock prevents two local instances from using the same folder.

1. Stop the old instance before moving servers.
2. Preserve the database and any SQLite sidecar files.
3. Copy credentials through a private channel.
4. Verify only one instance is live for the account.

A normal restart retains pending source activity and Pause. A normal process stop while unpaused attempts to cancel tracked copied sell orders only when controls are reachable and the process has no recorded trading error. A pause, failed cancellation, crash, or trading error may leave orders open; inspect Polymarket directly.

<a id="troubleshooting"></a>

## 🧰 Troubleshooting

| What I see | What to do |
| --- | --- |
| No Telegram response | Check that the process is running, credentials are configured, and you are the allowed user in the private chat. |
| Open orders says “unknown” | Wait for the first complete account check; inspect any connection warning. |
| No copied trade | Check Pause, last successful check time, settlement status, and notifications. Pre-start activity is not adopted. |
| Order submitted, no fill notice | Submission is not settlement. Check the order directly on Polymarket. |
| Error says outcome is unknown | Do not repeatedly restart or delete state. Ask the host to reconcile the saved operation. |
| A sale happens while paused | An already open exchange order can still fill. Inspect its order reference. |
| Resume does nothing | `/resume` is a warning step. `/resume confirm` enables catch-up only when there is no recorded trading error. |
| Server is gone | Use the hosting console or contact the host. Telegram controls cannot repair a stopped server. |

## 📚 More information

- [Setup, limitations, and Oracle service](README.md)
- [Credential field examples](.env.example)
- [Football monitor wiki used as the format reference](../soccer_monitor/wiki/Home.md)
