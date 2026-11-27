import json
import unittest
from unittest.mock import patch
import httpx
import test_bot
from test_bot import fill, order, NOW
from operator_ui import Operator

class OperatorTests(unittest.TestCase):
    setUp = test_bot.CopyTests.setUp
    tearDown = test_bot.CopyTests.tearDown
    buy = test_bot.CopyTests.buy
    def ui(self, respond):
        http=httpx.Client(transport=httpx.MockTransport(respond))
        self.addCleanup(http.close)
        return Operator(self.db,http,'test-secret',42)
    def test_private_chat_owner_only_and_durable_pause(self):
        self.buy()
        self.bot.cycle([fill()],[order()],self.source)
        def respond(req):
            if req.url.path.endswith('getUpdates'):
                return httpx.Response(200,json={'ok':True,'result':[
                    {'update_id':1,'message':{'chat':{'id':42,'type':'group'},'from':{'id':99},'text':'/pause'}},
                    {'update_id':2,'message':{'chat':{'id':42,'type':'private'},'from':{'id':42},'text':'/pause'}}]})
            return httpx.Response(200,json={'ok':True,'result':{}})
        ui=self.ui(respond)
        self.assertTrue(ui.poll())
        self.assertTrue(self.db.paused)
        self.bot.cycle([fill('new-buy')],[],self.source)
        self.assertEqual(len(self.broker.sent),2)
        self.assertEqual(self.broker.cancelled,[])
        self.db.close()
        from copy_bot import Store
        self.db=Store(__import__('pathlib').Path(self.tmp.name)/'state.db','0x'+'1'*40,'0x'+'2'*40)
        self.assertTrue(self.db.paused)
    def test_submission_and_fill_notifications_are_distinct_and_deduplicated(self):
        self.buy()
        notices=list(self.db.db.execute('SELECT * FROM notices'))
        text='\n'.join(r['text'] for r in notices)
        self.assertIn('Submitted',text)
        self.assertIn('Confirmed fill',text)
        self.assertIn('0.5',text)
        count=len(notices)
        self.bot.cycle([fill()],[],self.source)
        self.assertEqual(self.db.db.execute('SELECT COUNT(*) FROM notices').fetchone()[0],count)
    def test_direct_submit_and_cancel_are_blocked_while_paused(self):
        ui=self.ui(lambda r:httpx.Response(200,json={'ok':True,'result':[]}))
        ui.command('/pause',1)
        self.bot.submit({'kind':'market','side':'BUY','asset_id':'123','amount':'10'})
        self.bot.cancel({'follower_id':'existing'})
        self.assertEqual(self.broker.sent,[])
        self.assertEqual(self.broker.cancelled,[])
    def test_status_unknown_open_orders_are_not_shown_as_zero(self):
        ui=self.ui(lambda r:httpx.Response(200,json={'ok':True,'result':[]}))
        self.assertIn('unknown',ui.status().lower())
        ui.checked(3)
        self.assertIn('3',ui.status())
        self.assertIn('Last successful',ui.status())
    def test_notification_outage_does_not_leak_token_and_queue_survives(self):
        self.buy()
        ui=self.ui(lambda r:httpx.Response(503))
        self.assertFalse(ui.poll())
        self.assertFalse(ui.flush())
        self.assertGreater(self.db.db.execute('SELECT COUNT(*) FROM notices WHERE sent=0').fetchone()[0],0)
    def test_only_explicit_resume_confirmation_catches_up(self):
        ui=self.ui(lambda r:httpx.Response(200,json={'ok':True,'result':[]}))
        ui.command('/pause',1)
        ui.command('/resume',2)
        self.assertTrue(self.db.paused)
        ui.command('/resume confirm',3)
        self.assertFalse(self.db.paused)
    def test_pause_reply_precedes_trade_backlog(self):
        self.buy()
        sent=[]
        def respond(req):
            sent.append(json.loads(req.content))
            return httpx.Response(200,json={'ok':True,'result':{}})
        ui=self.ui(respond)
        for n in range(10):
            ui.queue('backlog:'+str(n),'old trade')
        ui.command('/pause',999)
        ui.flush()
        self.assertIn('PAUSED',sent[0]['text'])
    def test_covered_sell_reports_source_and_follower_prices(self):
        self.buy()
        self.bot.cycle([fill()],[order()],self.source)
        sold=fill('source-sell','SELL','40','sell1',NOW+2)
        follower=fill('follower-sell','SELL','20','follower-2',NOW+2)
        follower.price=__import__('decimal').Decimal('.8')
        self.broker.confirmed.append(follower)
        self.broker.positions['123']=__import__('decimal').Decimal('30')
        self.bot.cycle([fill(),sold],[order(matched='40')],self.source)
        row=self.db.db.execute("SELECT text FROM notices WHERE id='covered:source-sell'").fetchone()
        self.assertIn('0.800000',row['text'])
        self.assertIn('No extra sale',row['text'])
    def test_zero_size_sell_with_prior_credit_is_reported_without_division(self):
        from decimal import Decimal as D
        self.buy()
        self.bot.cycle([fill()],[order()],self.source)
        self.broker.confirmed.append(fill('credit','SELL','20','follower-2',NOW+2))
        self.broker.positions['123']=D('30')
        sale=fill('dust','SELL','1','small-sale',NOW+3)
        sale.source_before=D('1000000000')
        self.bot.cycle([fill(),sale],[order(matched='40')],self.source)
        self.assertTrue(self.db.seen('dust'))
        self.assertIn('precision',self.db.db.execute("SELECT text FROM notices WHERE id='skip:dust'").fetchone()['text'])
    def test_connection_failure_and_recovery_alerts_do_not_repeat(self):
        ui=self.ui(lambda r:httpx.Response(200,json={'ok':True,'result':[]}))
        ui.connection_failed('TimeoutError')
        ui.connection_failed('TimeoutError')
        ui.checked(1)
        ui.checked(1)
        rows=list(self.db.db.execute('SELECT text FROM notices'))
        self.assertEqual(sum('Connection lost' in r['text'] for r in rows),1)
        self.assertEqual(sum('Connection restored' in r['text'] for r in rows),1)



class RuntimeTests(unittest.TestCase):
    def run_runtime(self, resume=False):
        import itertools, os, tempfile
        from pathlib import Path
        from types import SimpleNamespace as NS
        from unittest.mock import MagicMock, patch
        import copy_bot
        with tempfile.TemporaryDirectory() as folder:
            http=MagicMock()
            http.post.return_value=NS(raise_for_status=lambda:None,json=lambda:{'ok':True,'result':[]})
            client=MagicMock()
            client.credentials=NS(key='k',secret='c2VjcmV0',passphrase='p')
            client.signer='0x'+'2'*40
            reads=[]
            def trades(start):
                reads.append(start)
                if resume and len(reads)>1:
                    raise KeyboardInterrupt()
                return [],[]
            reader=NS(trades=trades,orders=lambda:[])
            if resume:
                state=copy_bot.Store(Path(folder)/'copy_state.sqlite3','0x'+'1'*40,'0x'+'2'*40)
                with state.db:
                    state.db.execute("INSERT INTO controls VALUES ('paused','1')")
                state.close()
            polls=[]
            def poll(ui):
                polls.append(1)
                ui.online=True
                if len(polls)==2 and resume:
                    ui.command('/resume confirm',10)
                return True
            broker=test_bot.Broker()
            broker._orders=[]
            broker._trades=[]
            env={'SOURCE_WALLET_ADDRESS':'0x'+'1'*40,'REEM_WALLET_ADDRESS':'0x'+'2'*40,
                 'SOURCE_SIGNER_ADDRESS':'0x'+'1'*40,'SOURCE_API_KEY':'k','SOURCE_API_SECRET':'s',
                 'SOURCE_API_PASSPHRASE':'p','REEM_PRIVATE_KEY':'key','REEM_TELEGRAM_BOT_TOKEN':'test-secret',
                 'REEM_TELEGRAM_USER_ID':'42'}
            with patch.dict(os.environ,env), patch('sys.argv',['copy_bot','--live']), patch.object(copy_bot,'__file__',folder+'/copy_bot.py'), \
                 patch('httpx.Client') as httpcls, patch('polymarket.PublicClient'), patch('polymarket.SecureClient.create') as create, \
                 patch.object(copy_bot,'SourceReader',return_value=reader), patch.object(copy_bot,'rpc_call',return_value='0x89'), \
                 patch.object(copy_bot,'collateral_balance',return_value=100), patch.object(copy_bot,'account_snapshot',return_value=NS(positions={})), patch.object(copy_bot,'PolymarketBroker',return_value=broker), \
                 patch.object(copy_bot.CopyBot,'cycle',side_effect=[httpx.ConnectError('late'),httpx.ConnectError('late'),KeyboardInterrupt()]) as cycle, \
                 patch('operator_ui.Operator.poll',autospec=True,side_effect=poll) if resume else __import__('contextlib').nullcontext(), \
                 patch('copy_bot.time.monotonic',side_effect=itertools.count(0,3)):
                httpcls.return_value.__enter__.return_value=http
                create.return_value.__enter__.return_value=client
                with self.assertRaises(KeyboardInterrupt):
                    copy_bot.main()
            import sqlite3
            with sqlite3.connect(Path(folder)/'copy_state.sqlite3') as db:
                text='\n'.join(row[0] for row in db.execute('SELECT text FROM notices'))
                return text,cycle.call_count
    def test_late_read_failure_does_not_emit_false_recovery(self):
        text,count=self.run_runtime()
        self.assertEqual(text.count('Connection lost'),1)
        self.assertNotIn('Connection restored',text)
    def test_resume_during_second_poll_defers_copying_until_fresh_cycle(self):
        text,count=self.run_runtime(resume=True)
        self.assertEqual(count,0)
        self.assertIn('Resumed',text)

if __name__=="__main__": unittest.main()
