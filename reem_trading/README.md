# Reem Polymarket copy trader

Copies one source account into Reem's account. Default execution is a read-only connection check; real trading requires `--live`.

See the [🤖 user wiki](Wiki.md) for Telegram controls, examples, and troubleshooting.

## Trading rules

- A BUY uses `source trade cost / source total equity × Reem total equity`, rounded down to cents. Total equity is on-chain pUSD cash (Polymarket’s dollar collateral) plus the Data API's current position value, including invested money. It is not free cash. For example, a $100 buy in a $2,000 account requests $50 in a $1,000 account. Equity is sampled when the copy is processed, not a historical reconstruction of equity at the original fill.
- A SELL follows the fraction of the source position sold. Selling 40 of 100 source shares sells 40% of the shares managed by this bot. Shares are rounded down to six decimals and may be rounded further by exchange precision rules.
- New resting SELL orders are mirrored at the source limit price. Cancellation, replacement and expiry are followed on successful scans. Existing orders placed before the first live start are not adopted. Existing positions are not bought automatically.
- The bot credits confirmed mirrored SELL fills before executing a source sale. If the source sells first, it cancels copied reservations and submits a market catch-up sale. This avoids selling the same shares twice while still following a source exit.
- Confirmed source fills are copied; failed settlements are ignored. Newly observed source or follower fills that are still settling pause new placements, while source cancellations remain observable.
- No extra trade-size cap or slippage-percentage cap is configured. Market copies use fill-or-kill: fill the complete exchange order or reject it. Insufficient cash, liquidity, minimum order sizes or failed settlement stop the bot for review rather than silently changing the allocation.

This uses the current official `polymarket-client` SDK, separately from the soccer/SMA environment because their websocket dependencies differ. Both older outcome tokens and Polymarket V2 position identifiers use the SDK's protocol rules. Non-trading operations such as redemptions, transfers, splits, merges, and Combo trading are not copied.

## Required account access

A public source wallet address is insufficient to see resting limit orders. Configure the source signer's existing **CLOB API key, secret and passphrase**—credentials for Polymarket’s order-book service—, plus its public signer address and the account wallet address. No source private key is used. The source reader only issues authenticated GET requests.

Credentials must cover the orders actually placed by your trading interface. Session-key credentials are scoped; a successful connection with an empty order list does not establish that it can see a known website order. Before enabling copying, compare the reported sell-order count with your account. Do not swap credentials or wallet identities while reusing a state file.

Reem needs an existing funded account, its correct signing private key, and the trading approvals required by Polymarket. The read-only check does not test Reem's private key, signing, approvals, or order submission.

Never send private keys or API secrets in chat, commit them, or paste them into shell command arguments.

## Local setup

1. Change into `reem_trading/`.
2. Run `uv sync --frozen`.
3. Copy `.env.example` to `.env`.
4. Run `chmod 600 .env`.
5. Fill in `.env` using a local editor.
6. Configure `REEM_TELEGRAM_BOT_TOKEN` for a dedicated bot and `REEM_TELEGRAM_USER_ID` for Reem’s positive numeric Telegram user ID. Controls accept only that user in the private chat.
7. Run `uv run --frozen python copy_bot.py`.
8. Compare the reported source sell-order count and both account totals with the accounts.
9. When ready for real trading, run `uv run --frozen python copy_bot.py --live`.

To generate the source account's three CLOB credentials, run `uv run --frozen python setup_credentials.py source` from this folder. Enter **your source account's** signing private key at the hidden terminal prompt, not Reem's key. The helper signs an authentication message, creates or retrieves credentials, and saves them atomically to `.env`; it never saves the source key or places trades. A configured source signer must match the supplied key. It uses the pinned SDK's authentication helpers without initializing a trading client or deploying a wallet.

For Telegram, create a dedicated bot with [@BotFather](https://t.me/BotFather) using `/newbot`, and save its token locally as `REEM_TELEGRAM_BOT_TOKEN`. Ask Reem to send `/setup_reem` in that bot's private chat. Run `uv run --frozen python setup_credentials.py telegram` to save the unique sender's numeric user ID. Verify the sender is Reem; the helper does not send messages or start copying. It refuses ambiguous senders or a mismatch with an already configured user ID.

The first live start establishes the persistent start boundary at the next whole second. Restarting keeps that boundary and catches up later source activity. It does not start over from the current time.

Do not run a second live instance in another checkout or host using the same Reem account. The local lock prevents duplicate instances only within this folder.

## Telegram controls and notifications

Live mode requires a dedicated Telegram bot without a webhook or another update consumer. `/start`, `/menu`, and `/status` show running/paused/error state, the last complete successful account check, the last known count of all Reem open orders, and pending notices. These values are snapshots; counts remain unknown before a successful scan.

`/pause` persists across restarts and prevents new trading actions, including cancellations and shutdown cleanup. Existing exchange orders and positions remain untouched and may still fill. Pause takes effect when processed; slow reads or an already submitted request can delay acknowledgement. Read-only account checks and confirmed-fill notifications continue while paused.

`/resume` only shows a warning. `/resume confirm` explicitly accepts catching up on activity missed while paused; it does not offer a new-activity-only mode or bypass errors. A resume processed after account reads waits for the next fresh cycle before copying.

Notifications distinguish submissions from confirmed fills and report source details, actual follower fill prices, cancellations, skips, rejections, covered sells, and runtime halts. They are queued in SQLite with UTC timestamps. Replies and error alerts take priority over routine notices. Retrying an ambiguous Telegram delivery can duplicate a notice; it does not retry the trade.

Failed account checks generate outage alerts, five-minute reminders, and a recovery notice after a complete cycle succeeds. Trading errors leave controls alive in an error state for inspection; they require reconciliation and a process restart. Loss of Telegram controls blocks new trading actions until polling recovers. Telegram outages delay their own alerts; force kills, power loss, or a dead server require an external monitor for immediate notification.

## State, uncertainty and stopping

`copy_state.sqlite3` contains source-event deduplication, linked orders, confirmed follower fills, pause state, Telegram command progress, queued notices, and signed submission records. Keep it when moving hosts; stop the old instance before copying the database and any SQLite sidecars. It is ignored by Git and created with owner-only permissions.

The durable submission claim is written before sending an order. An ambiguous submission is not retried automatically: Telegram-style retry-after-timeout logic could place the trade twice. The bot stops and reports the operation number; inspect Reem's open orders and trade history before reconciling that record. Do not delete the database to clear an error. Reconciliation of an ambiguous order currently requires manual inspection and assistance with the stored record.

SIGINT/SIGTERM stops polling and, only when unpaused with reachable Telegram controls and no recorded trading error, attempts to cancel this bot's tracked resting SELL orders. A crash or unreachable exchange may leave an order open; inspect the account if cancellation could not be confirmed. The supplied service does not restart a failed process automatically; runtime trading errors keep the process alive for status and review without placing more trades.

Use a dedicated Reem account for this strategy. The bot tracks its own fills, but shares of the same outcome are fungible: mixing manual trading, transfers or redemptions into bot-managed positions can invalidate its inventory accounting.

## Timing and limitations

Polling defaults to two seconds plus request and processing time. Cancels and placements are not instantaneous. Orders placed and cancelled entirely between scans may never be mirrored, and Reem's independently resting order can fill before the source order. Equal prices, fill times, or returns cannot be guaranteed. Good Till Date (GTD) orders, which have an expiry, with less than roughly three minutes left cannot be recreated through the SDK and are logged rather than converted into an order with a different expiry.

The bot reads on-chain share balances for traded assets. For a copied SELL, it reconstructs the source balance immediately before its share transfer, including earlier transfers in that block; later transfers or redemptions cannot change that sell fraction. This requires a Polygon RPC endpoint—a server that reads blockchain data—that serves recent historical balances and logs. Mixed-side fills sharing a timestamp use chain transaction order, including sales that fund buys of other assets. Ambiguous batched transfers (for example, multiple identical-size source sells of the same asset in one transaction, or mixed BUY/SELL fills in one transaction) pause copying rather than guessing. Position *valuations* still depend on the Data API. These reads and the source fill feed are not an atomic historical snapshot, so rapid concurrent activity and price changes can affect BUY allocations. Small exchange rounding differences and fee differences are possible.

History is scanned from the saved start boundary, including confirmed follower fills. This is intentionally simple; long histories increase API work and latency. Introduce a reconciled checkpoint before using it for sustained high-volume trading.

## Oracle installation

The service assumes the checkout root is `/home/ubuntu/soccer_monitor` and the bot lives in its `reem_trading/` subfolder. It has its own `.venv`; it does not use the existing root `venv`.

1. Install `uv` and a supported Python on the server.
2. Change into `/home/ubuntu/soccer_monitor/reem_trading`.
3. Run `uv sync --frozen`.
4. Configure `.env` with mode `600`.
5. Run the default read-only check.
6. Install `reem_trading.service` into `/etc/systemd/system/`.
7. Run `sudo systemctl daemon-reload`.
8. Run `sudo systemctl enable --now reem_trading` only when real trading is intended.
9. Inspect logs with `journalctl -u reem_trading -f`.

Starting the service places real orders. No service installation or activation is performed by this repository change.

## Offline tests

Run `uv run --frozen python -m unittest discover -s tests -v` from this folder. Tests use temporary SQLite files and simulated network boundaries; they do not connect accounts or submit orders.

Official references: [Python SDK](https://github.com/Polymarket/py-sdk), [order access](https://docs.polymarket.com/trading/manage-orders), [wallet authentication](https://docs.polymarket.com/trading/wallets-auth), [order types](https://docs.polymarket.com/trading/place-orders).
