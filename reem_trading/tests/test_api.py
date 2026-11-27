import base64
import hashlib
import hmac
import unittest
from decimal import Decimal as D
from types import SimpleNamespace as NS
from unittest.mock import patch

import httpx
from copy_bot import SourceReader, PolymarketBroker, account_snapshot, collateral_balance, token_balance, source_sale_before, resolve_fill_order, Fill, Halt

WALLET = '0x' + '1' * 40

class APIContractTests(unittest.TestCase):
    def test_authenticated_reads_sign_the_path_without_query_and_follow_every_page(self):
        requests = []
        def respond(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(200, json={'data': [{'id': 'first'}], 'next_cursor': 'page2'})
            return httpx.Response(200, json={'data': [{'id': 'second'}], 'next_cursor': 'LTE='})
        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            reader = SourceReader(http, wallet=WALLET, signer=WALLET, key='test-key', secret='c2VjcmV0', passphrase='pass')
            with patch('copy_bot.time.time', return_value=1000):
                rows = reader.pages('/data/trades', {'after': '123'})
        self.assertEqual([r['id'] for r in rows], ['first', 'second'])
        self.assertEqual(requests[1].url.params['next_cursor'], 'page2')
        expected = base64.urlsafe_b64encode(hmac.new(b'secret', b'1000GET/data/trades', hashlib.sha256).digest()).decode()
        self.assertEqual(requests[0].headers['POLY_SIGNATURE'], expected)
        self.assertEqual(requests[0].headers['POLY_ADDRESS'], WALLET)
        self.assertEqual([r.method for r in requests], ['GET', 'GET'])
    def test_partial_source_scan_never_becomes_an_empty_order_snapshot(self):
        def respond(request):
            if 'next_cursor' not in request.url.params:
                return httpx.Response(200, json={'data': [{'id': 'first'}], 'next_cursor': 'page2'})
            return httpx.Response(503)
        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            reader = SourceReader(http, wallet=WALLET, signer=WALLET, key='k', secret='c2VjcmV0', passphrase='p')
            with self.assertRaises(httpx.HTTPStatusError):
                reader.pages('/data/orders')
    def test_pagination_loop_fails_instead_of_cancelling_orders(self):
        with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={'data': [], 'next_cursor': 'same'}))) as http:
            reader = SourceReader(http, wallet=WALLET, signer=WALLET, key='k', secret='c2VjcmV0', passphrase='p')
            with self.assertRaises(Halt):
                reader.pages('/data/orders')
    def test_account_equity_includes_cash_and_the_full_positions_value(self):
        public = NS(list_positions=lambda **kw: [NS(items=[NS(asset_id='123', current_size=D('20'))])],
                    get_portfolio_value=lambda **kw: NS(value=D('900')))
        snap = account_snapshot(public, WALLET, D('100'))
        self.assertEqual((snap.equity, snap.cash, snap.positions['123']), (D('1000'), D('100'), D('20')))
    def test_collateral_units_and_contract_read(self):
        requests=[]
        def respond(request):
            requests.append(request)
            return httpx.Response(200, json={'result': hex(123_450_000)})
        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            amount=collateral_balance(http, WALLET, 'https://rpc.example')
        self.assertEqual(amount, D('123.45'))
        self.assertIn(('70a08231' + WALLET[2:].zfill(64)).encode(), requests[0].content)
    def test_source_position_balance_reads_onchain_shares_instead_of_lagging_activity(self):
        requests=[]
        def respond(request):
            requests.append(request)
            return httpx.Response(200,json={'result':hex(20_000_000)})
        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            self.assertEqual(token_balance(http,WALLET,str(2**80),'https://rpc.example'),D('20'))
        self.assertIn(b'00fdd58e',requests[0].content)
        self.assertIn(b'4D97DCd97eC945f40cF65F87097ACe5EA0476045',requests[0].content)

    def test_sell_denominator_uses_historical_balance_and_prior_same_block_transfers(self):
        from eth_utils import keccak
        import json
        topic='0x'+keccak(text='TransferSingle(address,address,address,uint256,uint256)').hex()
        asset=str(2**80)
        wallet_topic='0x'+WALLET[2:].zfill(64)
        other='0x'+('2'*40).zfill(64)
        def log(index, sender, recipient, amount, tx):
            return {'topics':[topic,other,sender,recipient],
                    'data':'0x'+f'{int(asset):064x}'+f'{amount*1_000_000:064x}',
                    'transactionHash':tx,'logIndex':hex(index)}
        logs=[log(0,other,wallet_topic,20,'0xearlier'),
              log(1,wallet_topic,other,40,'0xsale'),
              log(2,wallet_topic,other,60,'0xlater-transfer')]
        def respond(request):
            data=json.loads(request.content)
            if data['method']=='eth_getTransactionReceipt':
                result={'blockNumber':'0x10','status':'0x1'}
            elif data['method']=='eth_getLogs':
                result=logs
            else:
                self.assertEqual(data['params'][1],'0xf')
                result=hex(80_000_000)
            return httpx.Response(200,json={'result':result})
        sale=NS(asset=asset,shares=D('40'),tx_hash='0xsale')
        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            self.assertEqual(source_sale_before(http,WALLET,sale,'https://rpc.example'),D('100'))

    def test_tied_mixed_trades_get_receipt_order(self):
        import json
        buy=Fill('z','456','BUY',D('100'),D('.5'),'buy',10,'0xbuy')
        sale=Fill('a','123','SELL',D('100'),D('.5'),'sale',10,'0xsell')
        def respond(request):
            tx=json.loads(request.content)['params'][0]
            return httpx.Response(200,json={'result':{'status':'0x1','blockNumber':'0x10','transactionIndex':'0x0' if tx=='0xbuy' else '0x1'}})
        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            rows=resolve_fill_order(http,[sale,buy],'https://rpc.example')
        self.assertEqual(rows[0].chain_sequence,(16,1))
        self.assertEqual(rows[1].chain_sequence,(16,0))

    def test_buy_above_cap_is_blocked_before_signing(self):
        from unittest.mock import Mock
        create = Mock(return_value={})
        client = NS(get_order_book=lambda **kw: NS(tick_size=D('.01'), asks=[NS(price=D('.89'), size=D('100'))]),
                    create_market_order=create)
        broker = PolymarketBroker(client, None, None, WALLET, None)
        with self.assertRaisesRegex(Halt, '10%'):
            broker.prepare(dict(kind='market', asset_id='123', side='BUY', amount='10', max_price='0.88'))
        create.assert_not_called()

    def test_buy_below_cap_keeps_signed_bound_and_rounds_down_to_tick(self):
        from unittest.mock import Mock
        for ask, maximum, tick, expected in [('.85','.88','.01','.88'), ('.88','.8833','.01','.88'),
                                              ('.99','1.045','.01','.99'), ('.8775','.88','.0025','.8800')]:
            with self.subTest(ask=ask, maximum=maximum):
                create = Mock(return_value='signed')
                client = NS(get_order_book=lambda **kw: NS(tick_size=D(tick), asks=[NS(price=D(ask), size=D('100'))]),
                            create_market_order=create)
                broker = PolymarketBroker(client, None, None, WALLET, None)
                result = broker.prepare(dict(kind='market', asset_id='123', side='BUY', amount='10', max_price=maximum))
                self.assertEqual(result, 'signed')
                self.assertEqual(D(create.call_args.kwargs['max_price']), D(expected))
                self.assertEqual(create.call_args.kwargs['order_type'], 'FOK')

    def test_source_wallet_must_belong_to_configured_owner_signer(self):
        from copy_bot import validate_source_wallet
        from eth_account import Account
        from polymarket.environments import PRODUCTION
        from polymarket._internal.wallet import derive_proxy_wallet_address
        signer = Account.create()
        wallet = derive_proxy_wallet_address(signer.address, PRODUCTION._config.wallet_derivation)
        validate_source_wallet(signer.address, wallet)
        with self.assertRaisesRegex(Halt, 'SOURCE_WALLET_ADDRESS'):
            validate_source_wallet(signer.address, '0x' + '1'*40)

    def test_exchange_error_on_cancel_must_not_be_treated_as_success(self):
        client=NS(cancel_order=lambda **kw: NS(canceled=()), get_order=lambda **kw: NS(status='LIVE'))
        broker=PolymarketBroker(client,None,None,WALLET,None)
        with self.assertRaises(Halt):
            broker.cancel('our-order')
    def test_delayed_follower_order_blocks_more_copying(self):
        client=NS(get_order=lambda **kw: NS(status='DELAYED',size_matched=D('0'),original_size=D('10'),order_type='FOK'))
        broker=PolymarketBroker(client,None,None,WALLET,None)
        self.assertTrue(broker.unsettled({'our-order'}))
    def test_accepted_partial_market_fill_cannot_silently_finish_a_copy(self):
        client=NS(get_order=lambda **kw: NS(status='MATCHED',size_matched=D('4'),original_size=D('10'),order_type='FOK'))
        broker=PolymarketBroker(client,None,None,WALLET,None)
        with self.assertRaises(Halt):
            broker.unsettled({'our-order'})

    def test_zero_filled_completed_market_order_must_not_count_as_copied(self):
        client=NS(get_order=lambda **kw: NS(status='CANCELED',size_matched=D('0'),original_size=D('10'),order_type='FOK'))
        broker=PolymarketBroker(client,None,None,WALLET,None)
        with self.assertRaises(Halt):
            broker.unsettled({'our-order'})

if __name__=='__main__':
    unittest.main()
