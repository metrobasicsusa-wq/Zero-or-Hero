"""Independent offline checks; mocks all data fetching and writes only temp dirs."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import market_data
from engine import run_path


def candle(o=100.,h=101.,l=99.9,c=100.,v=1000.):
    return {'o':o,'h':h,'l':l,'c':c,'v':v}


def fixture(symbols=('A',), days=('2026-01-05',)):
    ranked={};market={};cal=[];cov={}
    for day in days:
        settle='2026-01-06' if day=='2026-01-05' else '2026-01-07'
        cal.append({'date':day,'open':'09:30','close':'16:00','settlement_date':settle})
        ranked[day]=[];market[day]={};cov[day]={'ranking_complete':True,'ranking_missing':[]}
        for i,s in enumerate(symbols):
            ranked[day].append({'symbol':s,'rv':3-i,'rank':i+1,'or_open':99.95,'or_close':100.,'or_high':101.,'or_low':99.9,'atr14':2.})
            bars={m:candle() for m in range(390)};bars[0]=candle(o=99.95)
            bars[5]=candle(o=100,h=102.2,l=99.9,c=102)
            bars[389]=candle(o=110,h=112,l=109,c=111)
            market[day][s]=bars
    return ranked,cal,market,cov


class DownloadAudit(unittest.TestCase):
    def test_DST_conversion_and_credential_redirect_guard(self):
        self.assertEqual(market_data.stamp('2026-01-05','09:30'),'2026-01-05T14:30:00+00:00')
        self.assertEqual(market_data.stamp('2026-07-06','09:30'),'2026-07-06T13:30:00+00:00')
        with self.assertRaises(RuntimeError):market_data.NoRedirect().redirect_request(None,None,None,None,None,None)
        with self.assertRaises(ValueError):market_data.get({'symbols':'A','timeframe':'1Day'})
        with self.assertRaises(ValueError):market_data.get({'symbols':'A','timeframe':'1Min','url':'https://example.com'})

    def test_page_checkpoint_resume_without_refetching_first_page(self):
        page0={'bars':{'A':[{'t':'2026-01-05T14:30:00Z',**candle()}]},'next_page_token':'next1'}
        page1={'bars':{'A':[{'t':'2026-01-05T14:31:00Z',**candle()}]},'next_page_token':None}
        with tempfile.TemporaryDirectory() as td:
            with patch.object(market_data,'get',side_effect=[page0,RuntimeError('simulated_interrupt')]):
                with self.assertRaises(RuntimeError):market_data.fetch_window(['A'],'2026-01-05','09:30','09:35','1Min',td)
            self.assertTrue((Path(td)/'page-0000.json').exists())
            self.assertFalse((Path(td)/'complete.json').exists())
            with patch.object(market_data,'get',return_value=page1) as mock:
                result=market_data.fetch_window(['A'],'2026-01-05','09:30','09:35','1Min',td)
                self.assertEqual(mock.call_count,1)
                self.assertEqual(mock.call_args.args[0]['page_token'],'next1')
            self.assertEqual(len(result['pages']),2)
            self.assertEqual(len(market_data.load_window(td)['A']),2)
            with patch.object(market_data,'get',side_effect=AssertionError('cache_must_not_fetch')):
                self.assertEqual(market_data.fetch_window(['A'],'2026-01-05','09:30','09:35','1Min',td)['request_sha256'],result['request_sha256'])

    def test_complete_cache_detects_tampering(self):
        response={'bars':{'A':[{'t':'2026-01-05T14:30:00Z',**candle()}]},'next_page_token':None}
        with tempfile.TemporaryDirectory() as td:
            with patch.object(market_data,'get',return_value=response):market_data.fetch_window(['A'],'2026-01-05','09:30','09:35','1Min',td)
            file=Path(td)/'page-0000.json';x=json.loads(file.read_text());x['response']['bars']['A'][0]['c']=99.95;file.write_text(json.dumps(x))
            with self.assertRaises(AssertionError):market_data.load_window(td)

    def test_pagination_cycle_and_duplicate_bar_rejected(self):
        row={'t':'2026-01-05T14:30:00Z',**candle()}
        with tempfile.TemporaryDirectory() as td:
            with patch.object(market_data,'get',return_value={'bars':{'A':[row]},'next_page_token':'cycle'}):
                with self.assertRaisesRegex(RuntimeError,'pagination_cycle'):market_data.fetch_window(['A'],'2026-01-05','09:30','09:35','1Min',td)
        with tempfile.TemporaryDirectory() as td:
            with patch.object(market_data,'get',return_value={'bars':{'A':[row,row]},'next_page_token':None}):market_data.fetch_window(['A'],'2026-01-05','09:30','09:35','1Min',td)
            with self.assertRaisesRegex(RuntimeError,'duplicate_bar'):market_data.load_window(td)


class EngineAudit(unittest.TestCase):
    def test_latency_entry_minute_stop_and_cost_identity(self):
        ranked,cal,mkt,cov=fixture();mkt[cal[0]['date']]['A'][7]=candle(o=100,h=100.2,l=99.5,c=99.9)
        p=run_path(ranked,cal,mkt,1.,25,cov);t=p['trades'][0]
        self.assertEqual((t['signal_minute_offset'],t['entry_offset'],t['exit_offset']),(5,7,7))
        self.assertEqual(t['exit_reason'],'stop_touch_in_minute')
        self.assertAlmostEqual(t['exit_price'],99.8)
        self.assertAlmostEqual(p['final_equity'],500+t['pnl'])
        self.assertAlmostEqual(p['unsettled_cash'],t['exit_credit'])
        self.assertLess(p['settled_cash'],500)

    def test_selected_gap_unaffordable_has_no_runner_up(self):
        ranked,cal,mkt,cov=fixture(('A','B'));day=cal[0]['date'];mkt[day]['A'][7]=candle(o=1000,h=1001,l=999,c=1000)
        p=run_path(ranked,cal,mkt,1.,25,cov)
        self.assertEqual(p['trades'],[])
        self.assertEqual(p['final_equity'],500)
        self.assertEqual(p['skips'][0]['symbol'],'A')
        self.assertEqual(p['skips'][0]['reason'],'selected_open_unaffordable_no_runner_up')

    def test_earliest_signal_beats_later_higher_RV(self):
        ranked,cal,mkt,cov=fixture(('A','B'));day=cal[0]['date'];mkt[day]['A'][5]=candle();mkt[day]['A'][6]=candle(o=100,h=103,l=99.9,c=102)
        p=run_path(ranked,cal,mkt,1.,25,cov)
        self.assertEqual(p['entries'][0]['symbol'],'B')

    def test_missing_ranking_and_selected_entry_are_incomplete(self):
        ranked,cal,mkt,cov=fixture();day=cal[0]['date'];cov[day]['ranking_complete']=False
        p=run_path(ranked,cal,mkt,1.,25,cov)
        self.assertEqual(p['incomplete']['reason'],'incomplete_ranking_inputs')
        cov[day]['ranking_complete']=True;del mkt[day]['A'][7]
        p=run_path(ranked,cal,mkt,1.,25,cov)
        self.assertEqual(p['incomplete']['reason'],'missing_selected_entry_open')
        self.assertIsNone(p['final_equity'])

    def test_future_gap_after_stop_does_not_change_trade(self):
        ranked,cal,mkt,cov=fixture();day=cal[0]['date'];mkt[day]['A'][7]=candle(o=100,h=100.2,l=99.5,c=99.9)
        first=run_path(ranked,cal,mkt,1.,25,cov);del mkt[day]['A'][100]
        second=run_path(ranked,cal,mkt,1.,25,cov)
        self.assertEqual(first['trades'],second['trades'])
        self.assertEqual(first['final_equity'],second['final_equity'])

    def test_calendar_settlement_paid_before_next_session_budget(self):
        ranked,cal,mkt,cov=fixture(days=('2026-01-05','2026-01-06'))
        p=run_path(ranked,cal,mkt,1.,25,cov)
        self.assertEqual(len(p['trades']),2)
        self.assertAlmostEqual(p['daily'][1]['settlement_cash_paid'],p['trades'][0]['exit_credit'])
        self.assertAlmostEqual(p['daily'][1]['settled_cash_before_signals'],500+p['trades'][0]['pnl'])
        self.assertTrue(all(d['entries']<=1 and d['trades']<=1 for d in p['daily']))
        self.assertAlmostEqual(p['final_equity'],500+sum(t['pnl'] for t in p['trades']))

    def test_later_settlement_is_not_spendable_early(self):
        ranked,cal,mkt,cov=fixture(days=('2026-01-05','2026-01-06'));cal[0]['settlement_date']='2026-01-07'
        p=run_path(ranked,cal,mkt,1.,25,cov)
        self.assertEqual(len(p['trades']),1)
        self.assertEqual(p['daily'][1]['settlement_cash_paid'],0)
        self.assertEqual(p['signals'][-1]['status'],'unaffordable_at_first_signal')
        self.assertAlmostEqual(p['final_equity'],500+p['trades'][0]['pnl'])

    def test_scheduled_exit_uses_open_not_eventual_close(self):
        ranked,cal,mkt,cov=fixture();first=run_path(ranked,cal,mkt,1.,25,cov)
        mkt[cal[0]['date']]['A'][389]=candle(o=110,h=1001,l=1,c=1000)
        second=run_path(ranked,cal,mkt,1.,25,cov)
        self.assertEqual(first['final_equity'],second['final_equity'])
        self.assertEqual(second['trades'][0]['exit_price'],110)

    def test_early_close_last_minute_1259(self):
        ranked,cal,mkt,cov=fixture();cal[0]['close']='13:00';mkt[cal[0]['date']]['A'][209]=candle(o=105,h=106,l=104,c=105)
        p=run_path(ranked,cal,mkt,1.,25,cov)
        self.assertEqual(p['trades'][0]['exit_offset'],209)
        self.assertIn('12:59:00',p['trades'][0]['exit_time'])



class ResumeChainAudit(unittest.TestCase):
    def test_partial_cache_rejects_changed_previous_next_token(self):
        page0={'bars':{},'next_page_token':'original-next'}
        page1={'bars':{},'next_page_token':'last-page'}
        with tempfile.TemporaryDirectory() as td:
            with patch.object(market_data,'get',side_effect=[page0,page1,RuntimeError('interrupt')]):
                with self.assertRaises(RuntimeError):market_data.fetch_window(['A'],'2026-01-05','09:30','09:35','1Min',td)
            path=Path(td)/'page-0000.json';saved=json.loads(path.read_text())
            saved['response']['next_page_token']='changed-next';path.write_text(json.dumps(saved))
            with patch.object(market_data,'get',side_effect=AssertionError('must_not_fetch')):
                with self.assertRaisesRegex(RuntimeError,'resume_page_chain_mismatch'):
                    market_data.fetch_window(['A'],'2026-01-05','09:30','09:35','1Min',td)

    def test_legacy_nonzero_page_is_not_trusted(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(market_data,'get',side_effect=[{'bars':{},'next_page_token':'two'}, {'bars':{},'next_page_token':'three'}, RuntimeError('interrupt')]):
                with self.assertRaises(RuntimeError):market_data.fetch_window(['A'],'2026-01-05','09:30','09:35','1Min',td)
            path=Path(td)/'page-0001.json';saved=json.loads(path.read_text());saved.pop('page_request_sha256')
            path.write_text(json.dumps(saved))
            with self.assertRaisesRegex(RuntimeError,'legacy_resume_page_chain_unverified'):
                market_data.fetch_window(['A'],'2026-01-05','09:30','09:35','1Min',td)

if __name__=='__main__':unittest.main()
