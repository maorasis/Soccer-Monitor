import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace as NS

from copy_bot import buy_amount, sell_shares, source_fills, SourceReader, Store, CopyBot, Halt

NOW = 1_800_000_000
WALLET = '0x' + '1' * 40

def fill(key='buy1', side='BUY', size='100', order='source-buy', timestamp=NOW + 1):
    return NS(id=key, asset='123', side=side, shares=D(size), price=D('.5'), order_id=order, timestamp=timestamp, source_before=D('100'))

def order(key='sell1', price='.8', size='100', matched='0', created=NOW + 1):
    return NS(id=key, asset_id='123', side='SELL', price=D(price), original_size=D(size),
              size_matched=D(matched), created_at=datetime.fromtimestamp(created, timezone.utc), expires_at=None,
              maker_address=WALLET, order_type='GTC')

class Broker:
    def __init__(self):
        self.sent = []
        self.cancelled = []
        self.confirmed = []
        self.cash = D('500')
        self.positions = {}
        self.equity = D('1000')
        self.fail = False
        self.rejected = False
        self.reserved = {}
    def snapshot(self):
        return NS(cash=self.cash, equity=self.equity, positions=self.positions)
    def fills(self, start):
        return self.confirmed
    def prepare(self, intent):
        return intent
    def post(self, intent):
        self.sent.append(intent)
        if self.fail:
            raise TimeoutError('ambiguous delivery')
        if self.rejected:
            return NS(ok=False, code='not_enough_balance', message='not enough balance')
        return NS(ok=True, order_id='follower-' + str(len(self.sent)))
    def cancel(self, order_id):
        self.cancelled.append(order_id)
    def unsettled(self, order_ids):
        return False
    def open_reservations(self, owned):
        return self.reserved

class CopyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Store(Path(self.tmp.name) / 'state.db', WALLET, '0x' + '2' * 40, NOW)
        self.broker = Broker()
        self.bot = CopyBot(self.db, self.broker)
        self.source = NS(cash=D('100'), equity=D('2000'), positions={'123': D('100')})
    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()
    def buy(self):
        self.bot.cycle([fill()], [], self.source)
        self.broker.confirmed = [fill('follower-fill', order='follower-1', size='50')]
        self.broker.positions = {'123': D('50')}
        self.bot.cycle([fill()], [], self.source)
    def test_buy_uses_total_equity_not_cash(self):
        self.assertEqual(buy_amount(D('100'), D('2000'), D('1000')), D('50.00'))
        self.bot.cycle([fill()], [], self.source)
        self.assertEqual(self.broker.sent[0]['amount'], '25.00')
    def test_nonfinite_or_zero_equity_is_rejected(self):
        for value in [D('0'), D('-1'), D('NaN'), D('Infinity')]:
            with self.assertRaises(ValueError):
                buy_amount(D('100'), value, D('1000'))
    def test_proportional_partial_and_full_sells(self):
        self.assertEqual(sell_shares(D('40'), D('100'), D('25')), D('10.000000'))
        self.assertEqual(sell_shares(D('100'), D('100'), D('25')), D('25.000000'))
        with self.assertRaises(ValueError):
            sell_shares(D('101'), D('100'), D('25'))
    def test_new_trade_once_across_restart_and_old_trades_ignored(self):
        self.bot.cycle([fill('old', timestamp=NOW - 1), fill()], [], self.source)
        self.db.close()
        self.db = Store(Path(self.tmp.name) / 'state.db', WALLET, '0x' + '2' * 40, NOW + 100)
        self.bot = CopyBot(self.db, self.broker)
        self.bot.cycle([fill()], [], self.source)
        self.assertEqual(len(self.broker.sent), 1)
        self.assertEqual(self.db.started, NOW)
    def test_partial_sell_uses_position_fraction_and_only_managed_shares(self):
        self.buy()
        self.broker.positions['123'] = D('1050')  # 1,000 unrelated shares must stay untouched.
        self.source.positions['123'] = D('60')
        self.bot.cycle([fill(), fill('sell', 'SELL', '40', 'market-sell', NOW + 2)], [], self.source)
        self.assertEqual(self.broker.sent[-1]['shares'], '20.000000')
    def test_sell_fraction_uses_balance_at_sale_even_if_source_later_transferred_tokens(self):
        self.buy()
        self.source.positions['123'] = D('0')
        sale = fill('sell', 'SELL', '40', 'market-sell', NOW + 2)
        sale.source_before = D('100')
        self.bot.cycle([fill(), sale], [], self.source)
        self.assertEqual(self.broker.sent[-1]['shares'], '20.000000')

    def test_unverifiable_source_sell_balance_cannot_use_a_later_snapshot(self):
        self.buy()
        sale = fill('sell', 'SELL', '40', 'market-sell', NOW + 2)
        sale.source_before=None
        with self.assertRaises(Halt):
            self.bot.cycle([fill(), sale], [], self.source)
        self.assertEqual(len(self.broker.sent),1)

    def test_same_timestamp_buy_and_sell_follow_chain_order_not_uuid_order(self):
        buy=fill('z-buy', 'BUY', '100', 'buy')
        buy.chain_sequence=(10,0)
        sale=fill('a-sell', 'SELL', '100', 'sale')
        sale.chain_sequence=(10,1)
        self.source.positions['123']=D('0')
        self.bot.cycle([sale,buy],[],self.source)
        self.assertEqual(self.broker.sent[0]['side'],'BUY')
        self.broker.confirmed=[fill('follower-buy',order='follower-1',size='50')]
        self.broker.positions['123']=D('50')
        self.bot.cycle([sale,buy],[],self.source)
        self.assertEqual(self.broker.sent[-1]['side'],'SELL')
        self.assertEqual(self.broker.sent[-1]['shares'],'50.000000')

    def test_unordered_same_timestamp_opposite_sides_pause_before_copying(self):
        with self.assertRaises(Halt):
            self.bot.cycle([fill('z-buy'),fill('a-sell','SELL')],[],self.source)
        self.assertEqual(self.broker.sent,[])

    def test_same_timestamp_sell_funds_buy_of_another_asset(self):
        self.buy()
        self.broker.cash=D('0')
        sale=fill('z-sale','SELL','100','sale',NOW+2)
        sale.chain_sequence=(10,0)
        buy=fill('a-buy','BUY','100','new-buy',NOW+2)
        buy.asset='456'
        buy.chain_sequence=(10,1)
        self.source.positions={'123':D('0'),'456':D('100')}
        trades=[fill(),buy,sale]
        self.bot.cycle(trades,[],self.source)
        self.assertEqual(self.broker.sent[-1]['side'],'SELL')
        self.broker.confirmed.append(fill('follower-sale','SELL','50','follower-2',NOW+2))
        self.broker.positions['123']=D('0')
        self.broker.cash=D('25')
        self.bot.cycle(trades,[],self.source)
        self.assertEqual(self.broker.sent[-1]['asset_id'],'456')
        self.assertEqual(self.broker.sent[-1]['side'],'BUY')

    def test_unmanaged_existing_position_is_not_sold(self):
        self.broker.positions['123'] = D('200')
        self.source.positions['123'] = D('0')
        self.bot.cycle([fill('sell', 'SELL', '100')], [], self.source)
        self.assertEqual(self.broker.sent, [])
    def test_sell_limit_placement_and_cancellation(self):
        self.buy()
        self.bot.cycle([fill()], [order(size='40')], self.source)
        intent = self.broker.sent[-1]
        self.assertEqual((intent['kind'], intent['side'], intent['price'], intent['size']), ('limit', 'SELL', '0.8', '20.000000'))
        self.bot.cycle([fill()], [], self.source)
        self.assertEqual(self.broker.cancelled, ['follower-2'])
    def test_source_order_changes_cancel_before_replacement(self):
        self.buy()
        self.bot.cycle([fill()], [order()], self.source)
        self.bot.cycle([fill()], [order(price='.9')], self.source)
        self.assertEqual(self.broker.cancelled, ['follower-2'])
        self.bot.cycle([fill()], [order(price='.9')], self.source)
        self.assertEqual(self.broker.sent[-1]['price'], '0.9')
    def test_limit_fill_is_not_copied_again_as_market_sell(self):
        self.buy()
        self.bot.cycle([fill()], [order()], self.source)
        sold = fill('source-sell-fill', 'SELL', '40', 'sell1', NOW + 2)
        self.source.positions['123'] = D('60')
        self.broker.confirmed.append(fill('follower-sell', 'SELL', '20', 'follower-2', NOW + 2))
        self.broker.positions['123'] = D('30')
        self.bot.cycle([fill(), sold], [order(matched='40')], self.source)
        self.assertEqual(len(self.broker.sent), 2)
    def test_source_fill_cancels_unfilled_mirror_then_catches_up(self):
        self.buy()
        self.bot.cycle([fill()], [order()], self.source)
        self.source.positions['123'] = D('60')
        trades = [fill(), fill('sell', 'SELL', '40', 'sell1', NOW + 2)]
        self.bot.cycle(trades, [order(matched='40')], self.source)
        self.assertEqual(self.broker.cancelled, ['follower-2'])
        self.bot.cycle(trades, [order(matched='40')], self.source)
        self.assertEqual(self.broker.sent[-1]['shares'], '20.000000')

    def test_source_pending_fills_block_new_orders_but_not_cancellations(self):
        self.bot.cycle([fill()], [], self.source, source_pending=True)
        self.assertEqual(self.broker.sent, [])
        self.buy()
        self.bot.cycle([fill()], [order()], self.source)
        self.bot.cycle([fill()], [], self.source, source_pending=True)
        self.assertEqual(self.broker.cancelled, ['follower-2'])

    def test_follower_limit_filled_ahead_of_source_is_not_sold_twice(self):
        self.buy()
        self.bot.cycle([fill()], [order()], self.source)
        self.broker.confirmed.append(fill('follower-full-sell','SELL','50','follower-2',NOW+2))
        self.broker.positions['123']=D('0')
        self.source.positions['123']=D('60')
        first=fill('sell-first','SELL','40','sell1',NOW+2)
        self.bot.cycle([fill(),first],[order(matched='40')],self.source)
        self.source.positions['123']=D('0')
        second=fill('sell-rest','SELL','60','sell1',NOW+3)
        second.source_before=D('60')
        self.bot.cycle([fill(),first,second],[],self.source) # cancel the now closed source order
        self.bot.cycle([fill(),first,second],[],self.source)
        self.assertEqual(len(self.broker.sent),2)
        self.assertEqual(self.db.credit('123'),D('0'))

    def test_old_limit_orders_are_not_adopted(self):
        self.buy()
        self.bot.cycle([fill()], [order(created=NOW - 1)], self.source)
        self.assertEqual(len(self.broker.sent), 1)
    def test_missing_cash_stops_instead_of_using_cash_percentage(self):
        self.broker.cash = D('1')
        with self.assertRaises(Halt):
            self.bot.cycle([fill()], [], self.source)
        self.assertEqual(self.broker.sent, [])
    def test_ambiguous_submission_is_persisted_and_never_retried(self):
        self.broker.fail = True
        with self.assertRaises(Halt):
            self.bot.cycle([fill()], [], self.source)
        with self.assertRaises(Halt):
            self.bot.cycle([fill()], [], self.source)
        self.assertEqual(len(self.broker.sent), 1)
    def test_rejected_order_is_not_recorded_as_a_success(self):
        self.broker.rejected = True
        with self.assertRaises(Halt):
            self.bot.cycle([fill()], [], self.source)
        self.assertFalse(self.db.seen('buy1'))
    def test_different_wallets_cannot_reuse_state(self):
        with self.assertRaises(Halt):
            Store(Path(self.tmp.name) / 'state.db', '0x' + '3' * 40, '0x' + '2' * 40, NOW)
    def test_follower_unsettled_fills_delay_new_orders(self):
        self.broker.unsettled = lambda ids: True
        self.bot.cycle([fill()], [], self.source)
        self.assertEqual(self.broker.sent, [])
    def test_multiple_sells_use_source_position_before_each_fill(self):
        self.buy()
        self.source.positions['123'] = D('0')
        trades = [fill(), fill('s1', 'SELL', '40', 's1', NOW + 2), fill('s2', 'SELL', '60', 's2', NOW + 3)]
        self.bot.cycle(trades, [], self.source)
        self.assertEqual(self.broker.sent[-1]['shares'], '20.000000')

class SourceTests(unittest.TestCase):
    def test_maker_trade_uses_own_side_asset_and_size_not_taker_values(self):
        maker = NS(order_id='our-sell', asset_id='456', maker_address=WALLET, side='SELL', matched_amount=D('4'), price=D('.7'))
        other = NS(order_id='other', asset_id='123', maker_address='0x'+'9'*40, side='BUY', matched_amount=D('100'), price=D('.3'))
        trade = NS(id='trade', trader_side='MAKER', status='CONFIRMED', maker_orders=[maker, other],
                   asset_id='123', side='BUY', size=D('104'), price=D('.3'), matched_at=datetime.fromtimestamp(NOW, timezone.utc))
        rows = source_fills([trade], WALLET)
        self.assertEqual([(r.side, r.asset, r.shares, r.order_id) for r in rows], [('SELL', '456', D('4'), 'our-sell')])
        trade.status='FAILED'
        self.assertEqual(source_fills([trade], WALLET), [])

if __name__ == '__main__':
    unittest.main()
