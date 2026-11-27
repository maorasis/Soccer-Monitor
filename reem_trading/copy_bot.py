"""Copy confirmed Polymarket fills and mirror resting SELL orders for one account."""
from __future__ import annotations

import argparse
import base64
import dataclasses
import fcntl
import hashlib
import hmac
import json
import logging
import math
import os
import signal
import sqlite3
import time
from contextlib import ExitStack
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from types import SimpleNamespace

logger = logging.getLogger('ReemTrading')
D = Decimal
ZERO = D(0)
CENT = D('.01')
SHARE = D('.000001')

class Halt(RuntimeError):
    """Stop copying until an execution or configuration problem is resolved."""


def positive(value):
    value = D(value)
    if not value.is_finite() or value <= 0:
        raise ValueError('Amounts and account equity must be finite and positive')
    return value


def buy_amount(source_cost, source_equity, follower_equity):
    return (positive(source_cost) / positive(source_equity) * positive(follower_equity)).quantize(CENT, rounding=ROUND_DOWN)


def sell_shares(source_sold, source_before, follower_before):
    sold, before = positive(source_sold), positive(source_before)
    if sold > before:
        raise ValueError('Source sell exceeds the reconstructed source position')
    return (sold / before * positive(follower_before)).quantize(SHARE, rounding=ROUND_DOWN)


@dataclasses.dataclass(frozen=True)
class Fill:
    id: str
    asset: str
    side: str
    shares: Decimal
    price: Decimal
    order_id: str
    timestamp: float
    tx_hash: str | None = None
    source_before: Decimal | None = None
    chain_sequence: tuple[int, int] | None = None


def source_fills(trades, wallet):
    """Maker fills can have a different side and asset from the enclosing taker trade."""
    fills = []
    for trade in trades:
        if trade.status != 'CONFIRMED':
            continue
        if trade.trader_side == 'TAKER':
            legs = [(trade.taker_order_id, trade.asset_id, trade.side, trade.size, trade.price)]
        else:
            legs = [(m.order_id, m.asset_id, m.side, m.matched_amount, m.price)
                    for m in trade.maker_orders if m.maker_address.lower() == wallet.lower()]
        for order, asset, side, shares, price in legs:
            positive(shares)
            if not ZERO < positive(price) <= 1:
                raise Halt('Invalid source fill price')
            fills.append(Fill(f'{trade.id}:{order}:{asset}', str(asset), side, shares, price,
                              order, trade.matched_at.timestamp(), getattr(trade, 'transaction_hash', None)))
    return sorted(fills, key=lambda f: (f.timestamp, f.id))


class Store:
    def __init__(self, path, source_wallet, follower_wallet, started=None):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS identity (source TEXT, follower TEXT, started REAL);
            CREATE TABLE IF NOT EXISTS controls (name TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS notices (id TEXT PRIMARY KEY, text TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY, credit TEXT NOT NULL DEFAULT '0', asset TEXT);
            CREATE TABLE IF NOT EXISTS operations (
                id INTEGER PRIMARY KEY, intent TEXT NOT NULL, signed TEXT, state TEXT NOT NULL,
                follower_id TEXT UNIQUE, source_order TEXT, fingerprint TEXT, active INTEGER DEFAULT 0,
                error TEXT
            );
            CREATE TABLE IF NOT EXISTS fills (id TEXT PRIMARY KEY, order_id TEXT, asset TEXT, side TEXT, shares TEXT, price TEXT);
        ''')
        if 'price' not in {r[1] for r in self.db.execute('PRAGMA table_info(fills)')}:
            self.db.execute('ALTER TABLE fills ADD COLUMN price TEXT')
        identity = self.db.execute('SELECT * FROM identity').fetchone()
        if identity is None:
            with self.db:
                self.db.execute('INSERT INTO identity VALUES (?,?,?)',
                                (source_wallet.lower(), follower_wallet.lower(), started if started is not None else math.ceil(time.time())))
            identity = self.db.execute('SELECT * FROM identity').fetchone()
        if (identity['source'], identity['follower']) != (source_wallet.lower(), follower_wallet.lower()):
            self.db.close()
            raise Halt('State belongs to different wallets; use a separate state file')
        self.started = identity['started']
    @property
    def paused(self):
        row = self.db.execute("SELECT value FROM controls WHERE name='paused'").fetchone()
        return row is not None and row[0] == '1'
    def notice(self, key, text):
        self.db.execute('INSERT OR IGNORE INTO notices (id,text) VALUES (?,?)',
                        (key, datetime.now(timezone.utc).isoformat() + '\n' + text))
    def close(self):
        self.db.close()
    def seen(self, event):
        return self.db.execute('SELECT 1 FROM seen WHERE id=?', (event,)).fetchone() is not None
    def mark(self, event, asset=None, credit=ZERO):
        self.db.execute('INSERT OR IGNORE INTO seen VALUES (?,?,?)', (event, str(credit), asset))
    def operations(self):
        return list(self.db.execute('SELECT * FROM operations ORDER BY id'))
    def owned(self):
        return {r['follower_id'] for r in self.operations() if r['follower_id']}
    def ingest(self, fills):
        owned = self.owned()
        with self.db:
            for fill in fills:
                if fill.order_id in owned:
                    inserted = self.db.execute('INSERT OR IGNORE INTO fills (id,order_id,asset,side,shares,price) VALUES (?,?,?,?,?,?)',
                                               (fill.id, fill.order_id, fill.asset, fill.side, str(fill.shares), str(fill.price))).rowcount
                    if inserted:
                        self.notice('fill:' + fill.id, f'Confirmed fill: {fill.side} {fill.shares} shares at ${fill.price} per share. Asset {fill.asset}; order {fill.order_id}.')
    def inventory(self, asset):
        return sum((D(r['shares']) * (1 if r['side'] == 'BUY' else -1)
                    for r in self.db.execute('SELECT side,shares FROM fills WHERE asset=?', (asset,))), ZERO)
    def credit(self, asset):
        limits = {r['follower_id'] for r in self.operations() if json.loads(r['intent'])['kind'] == 'limit'}
        sold = sum((D(r['shares']) for r in self.db.execute("SELECT * FROM fills WHERE asset=? AND side='SELL'", (asset,))
                    if r['order_id'] in limits), ZERO)
        used = sum((D(r['credit']) for r in self.db.execute('SELECT credit FROM seen WHERE asset=?', (asset,))), ZERO)
        return max(ZERO, sold - used)

    def credited_price(self, asset, shares):
        used = sum((D(r['credit']) for r in self.db.execute('SELECT credit FROM seen WHERE asset=?', (asset,))), ZERO)
        limits = {r['follower_id'] for r in self.operations() if json.loads(r['intent'])['kind'] == 'limit'}
        remaining, cost = shares, ZERO
        for row in self.db.execute("SELECT * FROM fills WHERE asset=? AND side='SELL' ORDER BY rowid", (asset,)):
            if row['order_id'] not in limits:
                continue
            quantity = D(row['shares'])
            skipped = min(used, quantity)
            used -= skipped
            quantity = min(quantity - skipped, remaining)
            if quantity:
                if row['price'] is None:
                    return 'not recorded before this upgrade'
                cost += quantity * D(row['price'])
                remaining -= quantity
            if remaining == 0:
                return '$' + str((cost / shares).quantize(D('.000001')))
        return 'unknown'


def fingerprint(order):
    return json.dumps([str(order.price), str(order.original_size),
                       order.expires_at.isoformat() if order.expires_at else None])


class CopyBot:
    def __init__(self, store, broker, sale_position_reader=None):
        self.store, self.broker = store, broker
        self.sale_position_reader = sale_position_reader
    def cancel(self, operation):
        if self.store.paused:
            return
        self.broker.cancel(operation['follower_id'])
        with self.store.db:
            self.store.db.execute('UPDATE operations SET active=0 WHERE id=?', (operation['id'],))
            self.store.notice(f"cancel:{operation['id']}", f"Cancelled copied sell order {operation['follower_id']} because the source order changed, closed, or its shares were needed for an exit.")
    def submit(self, intent, *, event=None, credit=ZERO, source_order=None, order_fingerprint=None, source=None):
        if self.store.paused:
            return
        # The durable claim precedes POST. Unknown outcomes are never blindly replayed.
        signed = self.broker.prepare(intent)
        payload = dataclasses.asdict(signed) if dataclasses.is_dataclass(signed) else signed
        with self.store.db:
            cursor = self.store.db.execute('''INSERT INTO operations
                (intent,signed,state,source_order,fingerprint) VALUES (?,?,'pending',?,?)''',
                (json.dumps(intent), json.dumps(payload), source_order, order_fingerprint))
            operation_id = cursor.lastrowid
        try:
            response = self.broker.post(signed)
        except Exception as exc:
            raise Halt(f'Submission outcome unknown for operation {operation_id}; reconcile it before restarting') from exc
        if not response.ok:
            with self.store.db:
                self.store.db.execute("UPDATE operations SET state='rejected',error=? WHERE id=?",
                                      (response.code, operation_id))
                self.store.notice(f'rejected:{operation_id}', f'Copy rejected; operation {operation_id}. Source: {source}. No successful copy recorded; review local logs.')
            raise Halt(f'Order rejected ({response.code}); operation {operation_id} needs review')
        with self.store.db:
            self.store.db.execute("UPDATE operations SET state='accepted',follower_id=?,active=? WHERE id=?",
                                  (str(response.order_id), int(intent['kind'] == 'limit'), operation_id))
            if event is not None:
                self.store.mark(event, intent['asset_id'], credit)
            self.store.notice(f'submitted:{operation_id}', f'Submitted copy (not yet a confirmed fill): {intent}. Source: {source}. Reem order {response.order_id}.')
        logger.info('Copied %s %s; order %s', intent['kind'], intent['side'], response.order_id)
    def cycle(self, source_trades, source_orders, source, *, source_pending=False):
        if self.store.paused:
            return
        operations = self.store.operations()
        if any(r['state'] != 'accepted' for r in operations):
            raise Halt('An unresolved submission exists in the state database; automatic replay is disabled')
        own = self.store.owned()
        self.store.ingest(self.broker.fills(self.store.started))
        orders = {o.id: o for o in source_orders if o.side == 'SELL'}
        active = [r for r in operations if r['active']]
        # Only a complete successful source scan is allowed to drive cancellations.
        for operation in active:
            current = orders.get(operation['source_order'])
            if current is None or fingerprint(current) != operation['fingerprint']:
                self.cancel(operation)
                return
        if source_pending:
            logger.info("Waiting for source fills to settle")
            return
        if self.broker.unsettled(own):
            logger.info('Waiting for follower fills to settle')
            return
        follower = self.broker.snapshot()
        trades = [f for f in source_trades if f.timestamp >= self.store.started and not self.store.seen(f.id)]
        groups = {}
        for fill in trades:
            groups.setdefault(fill.timestamp, []).append(fill)
        for group in groups.values():
            if len({f.side for f in group}) > 1:
                sequences = [getattr(f, 'chain_sequence', None) for f in group]
                if None in sequences or len(set(sequences)) != len(sequences):
                    raise Halt('Mixed BUY/SELL fills have ambiguous execution order; no trades copied')
        trades.sort(key=lambda f: (f.timestamp, getattr(f, 'chain_sequence', None) or (0, 0), f.id))
        for trade in trades:
            if self.store.seen(trade.id):
                continue
            credit_used = ZERO
            if trade.side == 'BUY':
                amount = buy_amount(trade.shares * trade.price, source.equity, follower.equity)
                if amount < CENT:
                    raise Halt('Proportional BUY is below one cent; cannot copy without changing its allocation')
                if amount > follower.cash:
                    raise Halt('Insufficient available cash for the total-account-sized BUY')
                intent = dict(kind='market', asset_id=trade.asset, side='BUY', amount=str(amount))
            else:
                managed = min(max(ZERO, self.store.inventory(trade.asset)), follower.positions.get(trade.asset, ZERO))
                credit = self.store.credit(trade.asset)
                if managed + credit == 0:
                    with self.store.db:
                        self.store.mark(trade.id)
                        self.store.notice('skip:' + trade.id, f'Skipped source SELL: no bot-managed holdings or prior sell credit. Asset {trade.asset}; source {trade.shares} shares at ${trade.price}.')
                    continue
                source_before = getattr(trade, 'source_before', None)
                if source_before is None and self.sale_position_reader is not None:
                    source_before = self.sale_position_reader(trade)
                if source_before is None:
                    raise Halt('Missing historical source balance for SELL; copying is paused')
                desired = sell_shares(trade.shares, source_before, managed + credit)
                if desired == 0:
                    with self.store.db:
                        self.store.mark(trade.id)
                        self.store.notice('skip:' + trade.id, f'Skipped source SELL {trade.id}: proportional size rounds below share precision. Source {trade.shares} shares at ${trade.price}.')
                    continue
                credit_used = min(credit, desired)
                shares = desired - credit_used
                if shares == 0:
                    price = self.store.credited_price(trade.asset, credit_used)
                    with self.store.db:
                        self.store.mark(trade.id, trade.asset, credit_used)
                        self.store.notice('covered:' + trade.id, f'Source SELL already covered by confirmed mirrored fills: source {trade.shares} shares at ${trade.price}; Reem {credit_used} credited shares at average price {price}. No extra sale placed. Asset {trade.asset}.')
                    continue
                # Release copied sell reservations before a market catch-up sale.
                for operation in active:
                    if json.loads(operation['intent'])['asset_id'] == trade.asset:
                        self.cancel(operation)
                        return
                reserved = self.broker.open_reservations(own).get(trade.asset, ZERO)
                if shares > managed - reserved:
                    raise Halt('Unrelated open sell orders reserve shares needed for a copied SELL')
                intent = dict(kind='market', asset_id=trade.asset, side='SELL', shares=str(shares))
            self.submit(intent, event=trade.id, credit=credit_used, source=f'{trade.side} {trade.shares} shares at ${trade.price}; order {trade.order_id}')
            return  # Refresh fills and balances before deciding the next order.
        reserved = self.broker.open_reservations(own)
        for order in sorted(orders.values(), key=lambda o: (o.created_at, o.id)):
            if order.created_at.timestamp() < self.store.started or any(r['source_order'] == order.id for r in active):
                continue
            managed = min(max(ZERO, self.store.inventory(str(order.asset_id))), follower.positions.get(str(order.asset_id), ZERO))
            source_shares = source.positions.get(str(order.asset_id), ZERO)
            remaining = order.original_size - order.size_matched
            available = max(ZERO, managed - reserved.get(str(order.asset_id), ZERO))
            if not available or not source_shares or remaining <= 0:
                continue
            if order.expires_at and order.expires_at.timestamp() < time.time() + 185:
                with self.store.db:
                    self.store.notice('expiry:' + order.id, f'Skipped source SELL order {order.id}: less than the SDK minimum expiry time remains.')
                logger.warning('Source GTD order %s is too close to expiry to recreate', order.id)
                continue
            size = min(available, sell_shares(min(remaining, source_shares), source_shares, managed))
            intent = dict(kind='limit', asset_id=str(order.asset_id), side='SELL', price=str(order.price), size=str(size))
            if order.expires_at:
                intent['expiration'] = int(order.expires_at.timestamp())
            self.submit(intent, source_order=order.id, order_fingerprint=fingerprint(order), source=f'SELL limit {remaining} shares at ${order.price}; order {order.id}')
            return


class SourceReader:
    """Authenticated GET requests only; source private keys are never needed."""
    def __init__(self, http, *, wallet, signer, key, secret, passphrase):
        self.http, self.wallet, self.signer = http, wallet, signer
        self.key, self.secret, self.passphrase = key, secret, passphrase
    def get(self, path, params=None):
        timestamp = str(int(time.time()))
        secret = base64.urlsafe_b64decode(self.secret + '=' * (-len(self.secret) % 4))
        signature = base64.urlsafe_b64encode(hmac.new(secret, f'{timestamp}GET{path}'.encode(), hashlib.sha256).digest()).decode()
        response = self.http.get('https://clob.polymarket.com' + path, params=params, headers={
            'POLY_ADDRESS': self.signer, 'POLY_API_KEY': self.key, 'POLY_PASSPHRASE': self.passphrase,
            'POLY_TIMESTAMP': timestamp, 'POLY_SIGNATURE': signature,
        })
        response.raise_for_status()
        return response.json()
    def pages(self, path, params=None):
        params = dict(params or {})
        rows, visited = [], set()
        while True:
            payload = self.get(path, params)
            if not isinstance(payload.get('data'), list):
                raise Halt('Malformed authenticated source response')
            rows.extend(payload['data'])
            cursor = payload.get('next_cursor')
            if not cursor or cursor == 'LTE=':
                return rows
            if cursor in visited:
                raise Halt('Repeated source pagination cursor')
            visited.add(cursor)
            params['next_cursor'] = cursor
    def orders(self):
        from polymarket.models.clob import OpenOrder
        orders = [OpenOrder.model_validate(row) for row in self.pages('/data/orders')]
        if any(o.maker_address.lower() != self.wallet.lower() for o in orders):
            raise Halt('Source credentials returned orders for a different wallet')
        return orders
    def trades(self, start):
        # shortcut: scan saved-start history; add reconciled checkpoints when history makes scans slow.
        from polymarket.models.clob import ClobTrade
        trades = [ClobTrade.model_validate(row) for row in self.pages('/data/trades', {'after': str(int(start) - 1)})]
        return source_fills(trades, self.wallet), trades


class PolymarketBroker:
    def __init__(self, client, reader, public, wallet, cash_reader, position_reader=None):
        self.client, self.reader, self.public = client, reader, public
        self.wallet, self.cash_reader = wallet, cash_reader
        self.position_reader = position_reader
        self._trades, self._orders = [], []
    def fills(self, start):
        fills, self._trades = self.reader.trades(start)
        self._orders = self.reader.orders()
        return fills
    def snapshot(self):
        snapshot = account_snapshot(self.public, self.wallet, self.cash_reader())
        if self.position_reader is not None:
            for asset in {f.asset for f in source_fills(self._trades, self.wallet)}:
                snapshot.positions[asset] = self.position_reader(asset)
        snapshot.cash -= sum(((o.original_size - o.size_matched) * o.price for o in self._orders if o.side == 'BUY'), ZERO)
        return snapshot
    def unsettled(self, owned):
        from polymarket.errors import RequestRejectedError
        pending = False
        for order_id in owned:
            trades = [t for t in self._trades if t.taker_order_id == order_id or any(m.order_id == order_id for m in t.maker_orders)]
            if any(t.status == 'FAILED' for t in trades):
                raise Halt(f'Follower settlement failed for {order_id}')
            if any(t.status != 'CONFIRMED' for t in trades):
                pending = True
            try:
                order = self.client.get_order(order_id=order_id)
            except RequestRejectedError as exc:
                raise Halt(f'Cannot reconcile follower order {order_id}') from exc
            settled = sum((f.shares for f in source_fills(trades, self.wallet) if f.order_id == order_id), ZERO)
            if order.order_type in ('FAK', 'FOK') and order.status.upper() != 'DELAYED':
                if order.size_matched == 0 or order.size_matched < order.original_size:
                    raise Halt(f'Incomplete market copy {order_id}; reconcile before continuing')
            if order.size_matched > settled or order.status.upper() == 'DELAYED':
                pending = True
        return pending
    def open_reservations(self, owned):
        reservations = {}
        for order in self._orders:
            if order.side == 'SELL':
                asset = str(order.asset_id)
                reservations[asset] = reservations.get(asset, ZERO) + order.original_size - order.size_matched
        return reservations
    def prepare(self, intent):
        args = {k: v for k, v in intent.items() if k != 'kind'}
        if intent['kind'] == 'market':
            # No user slippage cap: the SDK resolves a current executable bound.
            return self.client.create_market_order(**args, order_type='FOK')
        return self.client.create_limit_order(**args)
    def post(self, signed):
        return self.client.post_order(signed)
    def cancel(self, order_id):
        result = self.client.cancel_order(order_id=order_id)
        if order_id not in result.canceled:
            order = self.client.get_order(order_id=order_id)
            if order.status.upper() not in ('CANCELED', 'CANCELLED', 'MATCHED', 'EXPIRED'):
                raise Halt(f'Could not confirm cancellation of {order_id}')


def account_snapshot(public, wallet, cash):
    cash = D(cash)
    if not cash.is_finite() or cash < 0:
        raise Halt('Invalid collateral balance')
    positions = {str(p.asset_id): p.current_size for page in public.list_positions(user=wallet, full_history=True)
                 for p in page.items if p.current_size > 0}
    value = public.get_portfolio_value(user=wallet).value
    if not value.is_finite() or value < 0:
        raise Halt('Invalid positions value')
    return SimpleNamespace(cash=cash, equity=cash + value, positions=positions)


def collateral_balance(http, wallet, rpc_url):
    # pUSD on Polygon, from the official production SDK's environment configuration.
    response = http.post(rpc_url, json={'jsonrpc': '2.0', 'id': 1, 'method': 'eth_call', 'params': [{
        'to': '0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB',
        'data': '0x70a08231' + wallet[2:].lower().zfill(64),
    }, 'latest']})
    response.raise_for_status()
    payload = response.json()
    if 'error' in payload or not isinstance(payload.get('result'), str):
        raise Halt('Could not read the on-chain account collateral balance')
    return D(int(payload['result'], 16)) / 1_000_000


def position_contract(asset):
    # The pinned SDK defines the V2 identifier namespace used by the exchange.
    from polymarket._internal.protocol import is_v2_position_id
    return ('0x006F54F7f9A22e0000CC2AB60031000000ae9fEF' if is_v2_position_id(asset)
            else '0x4D97DCd97eC945f40cF65F87097ACe5EA0476045')


def rpc_call(http, rpc_url, method, params):
    response = http.post(rpc_url, json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})
    response.raise_for_status()
    payload = response.json()
    if 'error' in payload or 'result' not in payload:
        raise Halt(f'Polygon {method} failed; no trade will be sized from missing chain data')
    return payload['result']


def asset_number(asset):
    number = int(asset, 0) if asset[:2].lower() in ('0x', '0b', '0o') else int(asset)
    if not 0 <= number < 2**256:
        raise Halt('Invalid source asset identifier')
    return number


def token_balance(http, wallet, asset, rpc_url, block='latest'):
    result = rpc_call(http, rpc_url, 'eth_call', [{
        'to': position_contract(asset),
        'data': '0x00fdd58e' + wallet[2:].lower().zfill(64) + f'{asset_number(asset):064x}',
    }, block])
    return D(int(result, 16)) / 1_000_000


def source_sale_before(http, wallet, sale, rpc_url):
    """Reconstruct the source balance immediately before its actual share transfer.

    Later transfers, merges or redemptions cannot change the original sell ratio.
    Ambiguous same-transaction fills stop copying rather than guessing a log match.
    """
    from eth_abi import decode
    from eth_utils import keccak
    if not sale.tx_hash:
        raise Halt('A confirmed source SELL has no settlement hash')
    receipt = rpc_call(http, rpc_url, 'eth_getTransactionReceipt', [sale.tx_hash])
    if not receipt or receipt.get('status') != '0x1':
        raise Halt('Source SELL settlement is not available on this Polygon node')
    block = receipt['blockNumber']
    single = '0x' + keccak(text='TransferSingle(address,address,address,uint256,uint256)').hex()
    batch = '0x' + keccak(text='TransferBatch(address,address,address,uint256[],uint256[])').hex()
    logs = rpc_call(http, rpc_url, 'eth_getLogs', [{
        'address': position_contract(sale.asset), 'fromBlock': block, 'toBlock': block,
        'topics': [[single, batch]],
    }])
    wallet_topic = '0x' + wallet[2:].lower().zfill(64)
    asset = asset_number(sale.asset)
    movements = []
    for log in logs:
        topics = [t.lower() for t in log['topics']]
        if len(topics) != 4 or wallet_topic not in (topics[2], topics[3]):
            continue
        if log.get('removed'):
            raise Halt('Source SELL block was reorganized; wait for a stable chain snapshot')
        raw = bytes.fromhex(log['data'][2:])
        if topics[0] == single.lower():
            token, quantity = decode(['uint256', 'uint256'], raw)
            amount = quantity if token == asset else 0
        elif topics[0] == batch.lower():
            tokens, quantities = decode(['uint256[]', 'uint256[]'], raw)
            amount = sum(q for t, q in zip(tokens, quantities, strict=True) if t == asset)
        else:
            continue
        if amount and topics[2] != topics[3]:
            movements.append((int(log['logIndex'], 16), log['transactionHash'].lower(),
                              topics[2] == wallet_topic, D(amount) / 1_000_000))
    matches = [m for m in movements if m[1] == sale.tx_hash.lower() and m[2] and m[3] == sale.shares]
    if len(matches) != 1:
        raise Halt('Cannot uniquely match a source SELL to its share transfer; manual reconciliation is required')
    target_index = matches[0][0]
    before = token_balance(http, wallet, sale.asset, rpc_url, hex(int(block, 16) - 1))
    for index, _, outgoing, quantity in movements:
        if index < target_index:
            before += -quantity if outgoing else quantity
    if before < sale.shares or before <= 0:
        raise Halt('Historical source position does not cover its confirmed SELL')
    return before


def resolve_fill_order(http, fills, rpc_url):
    """Use chain transaction order when mixed-side match times have equal precision."""
    groups = {}
    for fill in fills:
        groups.setdefault(fill.timestamp, []).append(fill)
    sequences = {}
    for group in groups.values():
        if len({f.side for f in group}) < 2:
            continue
        for fill in group:
            if not fill.tx_hash:
                raise Halt('Cannot order tied source fills without settlement hashes')
            if fill.tx_hash not in sequences:
                receipt = rpc_call(http, rpc_url, 'eth_getTransactionReceipt', [fill.tx_hash])
                if not receipt or receipt.get('status') != '0x1':
                    raise Halt('Tied source fills do not have stable settlement receipts')
                sequences[fill.tx_hash] = (int(receipt['blockNumber'], 16), int(receipt['transactionIndex'], 16))
        if len({sequences[f.tx_hash] for f in group}) != len(group):
            raise Halt('Same-transaction mixed-side fills require manual reconciliation')
    return [dataclasses.replace(f, chain_sequence=sequences[f.tx_hash]) if f.tx_hash in sequences else f for f in fills]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Enable real trading; default only checks read access and balances')
    args = parser.parse_args()
    from dotenv import load_dotenv
    from eth_utils import is_address
    from polymarket import PublicClient, SecureClient
    from polymarket.errors import TransportError, RateLimitError
    import httpx
    folder = Path(__file__).resolve().parent
    load_dotenv(folder / '.env')
    def required(name):
        value = os.getenv(name, '').strip()
        if not value:
            raise Halt(f'Missing {name}; configure reem_trading/.env first')
        return value
    source_wallet, follower_wallet = required('SOURCE_WALLET_ADDRESS'), required('REEM_WALLET_ADDRESS')
    signer = required('SOURCE_SIGNER_ADDRESS')
    if not all(is_address(a) for a in (source_wallet, follower_wallet, signer)) or source_wallet.lower() == follower_wallet.lower():
        raise Halt('Configure valid, different source and Reem wallet addresses, and the source signer address')
    stopped = False
    def stop(*_):
        nonlocal stopped
        stopped = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    os.umask(0o077)
    with ExitStack() as stack:
        http = stack.enter_context(httpx.Client(timeout=15))
        public = stack.enter_context(PublicClient())
        source_reader = SourceReader(http, wallet=source_wallet, signer=signer, key=required('SOURCE_API_KEY'),
                                     secret=required('SOURCE_API_SECRET'), passphrase=required('SOURCE_API_PASSPHRASE'))
        rpc = os.getenv('POLYGON_RPC_URL', 'https://polygon.drpc.org')
        source_cash = lambda: collateral_balance(http, source_wallet, rpc)
        follower_cash = lambda: collateral_balance(http, follower_wallet, rpc)
        if not args.live:
            if int(rpc_call(http, rpc, 'eth_chainId', []), 16) != 137:
                raise Halt('POLYGON_RPC_URL must point to Polygon mainnet, chain 137')
            orders = source_reader.orders()
            source = account_snapshot(public, source_wallet, source_cash())
            follower = account_snapshot(public, follower_wallet, follower_cash())
            logger.info('Source total equity: %s; Reem total equity: %s; source sell orders: %d', source.equity, follower.equity,
                        sum(o.side == 'SELL' for o in orders))
            logger.info('Read-only check finished; no orders signed, submitted, or cancelled')
            return
        lock = stack.enter_context(open(folder / 'copy_bot.lock', 'a'))
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Halt('Another copy bot already owns this state directory') from exc
        from operator_ui import Operator
        store = Store(folder / 'copy_state.sqlite3', source_wallet, follower_wallet)
        stack.callback(store.close)
        ui = Operator(store, http, required('REEM_TELEGRAM_BOT_TOKEN'), required('REEM_TELEGRAM_USER_ID'))
        ui.queue(f'start:{time.time_ns()}', 'Reem copy bot started. Send /status for controls. ' +
                 ('Saved PAUSE is still active.' if store.paused else 'Copying is enabled.'))
        try:
            client = stack.enter_context(SecureClient.create(private_key=required('REEM_PRIVATE_KEY'), wallet=follower_wallet))
        except Exception as exc:
            ui.halt('Signing client startup failed (' + type(exc).__name__ + ')')
            ui.flush()
            raise Halt('Signing client startup failed; check local credentials and connectivity') from None
        credentials = client.credentials
        follower_reader = SourceReader(http, wallet=follower_wallet, signer=str(client.signer),
                                       key=credentials.key, secret=credentials.secret, passphrase=credentials.passphrase)
        broker = PolymarketBroker(client, follower_reader, public, follower_wallet, follower_cash,
                                  position_reader=lambda asset: token_balance(http, follower_wallet, asset, rpc))
        bot = CopyBot(store, broker, sale_position_reader=lambda sale: source_sale_before(http, source_wallet, sale, rpc))
        interval = float(os.getenv('POLL_SECONDS', '2'))
        if not 0.25 <= interval <= 60:
            raise Halt('POLL_SECONDS must be between 0.25 and 60')
        chain_verified = False
        try:
            while not stopped:
                if ui.poll() and ui.flush() and ui.error is None:
                    try:
                        if not chain_verified:
                            if int(rpc_call(http, rpc, 'eth_chainId', []), 16) != 137:
                                raise Halt('POLYGON_RPC_URL must point to Polygon mainnet, chain 137')
                            chain_verified = True
                        trades, raw_trades = source_reader.trades(store.started)
                        orders = source_reader.orders()
                        source = account_snapshot(public, source_wallet, source_cash())
                        for asset in {str(o.asset_id) for o in orders if o.side == 'SELL'}:
                            source.positions[asset] = token_balance(http, source_wallet, asset, rpc)
                        broker.fills(store.started)
                        store.ingest(source_fills(broker._trades, follower_wallet))
                        broker.snapshot()
                        pending_source = any(t.status not in ('CONFIRMED', 'FAILED') for t in raw_trades)
                        ready_for_copy = not store.paused
                        if not pending_source and ready_for_copy:
                            trades = resolve_fill_order(http, [f for f in trades if f.timestamp >= store.started and not store.seen(f.id)], rpc)
                        # Drain Pause commands after slow reads, before any trading action.
                        if ui.poll() and ui.flush():
                            if ready_for_copy and not store.paused and not stopped:
                                bot.cycle(trades, orders, source, source_pending=pending_source)
                            ui.checked(len(broker._orders))
                    except (httpx.HTTPError, TransportError, RateLimitError) as exc:
                        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (401, 403):
                            ui.halt('Account authentication failed; check local API credentials')
                        else:
                            ui.connection_failed(type(exc).__name__)
                        logger.warning('Read failed (%s); retaining state', type(exc).__name__)
                    except Exception as exc:
                        detail = str(exc) if isinstance(exc, (Halt, ValueError)) else type(exc).__name__
                        for name, value in os.environ.items():
                            if value and any(word in name for word in ('TOKEN', 'KEY', 'SECRET', 'PASSPHRASE')):
                                detail = detail.replace(value, '[REDACTED]')
                        ui.halt(detail)
                        logger.error('Copying stopped (%s); review saved operations', type(exc).__name__)
                ui.flush()
                deadline = time.monotonic() + interval
                while not stopped and time.monotonic() < deadline:
                    time.sleep(min(.25, max(0, deadline - time.monotonic())))
        finally:
            # A paused account must not submit even cleanup cancellations.
            if not store.paused and ui.online and ui.error is None:
                for operation in store.operations():
                    if operation['active']:
                        try:
                            bot.cancel(operation)
                        except Exception:
                            logger.error('Could not cancel tracked order %s; check it in Reem’s account', operation['follower_id'])
            ui.queue(f'stop:{time.time_ns()}', 'Copy bot process stopped. No more copying; inspect any outstanding orders in Polymarket.')
            ui.flush()

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    logging.getLogger('httpx').setLevel(logging.WARNING)
    try:
        main()
    except (Halt, ValueError) as exc:
        logger.error('%s', exc)
        raise SystemExit(1)
