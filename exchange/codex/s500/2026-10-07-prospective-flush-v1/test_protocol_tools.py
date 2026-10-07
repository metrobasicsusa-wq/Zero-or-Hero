"""Synthetic receipt gates; no future market observations are fabricated."""
import unittest,copy,json,tempfile
from pathlib import Path
from protocol_tools import digest,check_clock,seal_selection,validate_postclose,initial_ledger,write_once
ROOT=Path(__file__).parent
def fixture():
    p=json.loads((ROOT/'protocol.json').read_text());p['status']='registered';p['registered_at']='2026-10-07T17:00:00Z'
    panel=[{'symbol':'AAA','etf_classification':'N'},{'symbol':'BBB','etf_classification':'unknown'}]
    p['fixed_panel_symbols']=2;p['fixed_panel_content_digest']=digest(panel)
    s=p['sessions'][0];day=s['date'];t='2026-10-08T13:00:00Z'
    b={'protocol_id':p['id'],'date':day,'panel_content_digest':digest(panel),'prior20_dates':s['prior20_dates'],'feed':'sip','adjustment':'raw','timeframe':'1Day','source_complete':True,'requested_symbols':['AAA','BBB'],'daily':{r['symbol']:[{'session_date':d,'c':'10','v':'3000000'} for d in s['prior20_dates']] for r in panel},'requests':[{'requested_at':t,'received_at':'2026-10-08T13:10:00Z','http_status':200,'complete':True,'body_sha256':'a'*64,'requested_symbols':['AAA','BBB']}]}
    return p,panel,b
def receipt(p,seal):
    s=p['sessions'][0]
    return {'requested_at':'2026-10-08T20:15:00Z','received_at':'2026-10-08T21:00:00Z','source_complete':True,'http_status':200,'body_sha256':'b'*64,'requested_symbols':list(seal['selection']['selected_symbols']),'feed':'sip','adjustment':'raw','timeframe':'1Min','session_date':s['date'],'bar_window_start_utc':s['open_utc'],'bar_window_end_exclusive_utc':s['close_utc']}
class ReceiptTests(unittest.TestCase):
    def test_valid_full_pool(self):
        p,u,b=fixture();r=seal_selection(p,b['date'],u,b,'2026-10-08T13:19:00Z');self.assertTrue(r['accepted']);self.assertEqual(r['selection']['selected_symbols'],['AAA','BBB','QQQ','SPY'])
    def test_source_incomplete_blocks_whole_pool(self):
        p,u,b=fixture();b['source_complete']=False;self.assertFalse(seal_selection(p,b['date'],u,b,'2026-10-08T13:19:00Z')['accepted'])
    def test_omitted_unselected_batch_is_not_complete(self):
        p,u,b=fixture();b['requests'][0]['requested_symbols']=['AAA'];self.assertEqual(seal_selection(p,b['date'],u,b,'2026-10-08T13:19:00Z')['reason'],'batch_coverage_not_exactly_once')
    def test_duplicate_batch_is_blocked(self):
        p,u,b=fixture();b['requests']*=2;self.assertFalse(seal_selection(p,b['date'],u,b,'2026-10-08T13:19:00Z')['accepted'])
    def test_late_final_page_receipt(self):
        p,u,b=fixture();b['requests'][0]['received_at']='2026-10-08T13:20:00.000001Z';self.assertEqual(seal_selection(p,b['date'],u,b,'2026-10-08T13:20:00Z')['reason'],'preopen_deadline_missed')
    def test_seal_deadline_exact_then_late(self):
        p,u,b=fixture();self.assertTrue(seal_selection(p,b['date'],u,b,'2026-10-08T13:20:00Z')['accepted']);self.assertFalse(seal_selection(p,b['date'],u,b,'2026-10-08T13:20:00.000001Z')['accepted'])
    def test_receipt_time_order(self):
        p,u,b=fixture();b['requests'][0]['requested_at']='2026-10-08T13:15:00Z';self.assertEqual(seal_selection(p,b['date'],u,b,'2026-10-08T13:19:00Z')['reason'],'receipt_chronology_invalid')
    def test_no_naive_timestamp(self):
        p,u,b=fixture();self.assertFalse(seal_selection(p,b['date'],u,b,'2026-10-08T09:19:00')['accepted'])
    def test_future_or_previous_receipt_date(self):
        p,u,b=fixture();b['requests'][0]['requested_at']='2026-10-07T13:00:00Z';self.assertFalse(seal_selection(p,b['date'],u,b,'2026-10-08T13:19:00Z')['accepted'])
    def test_same_day_return_cannot_enter_ranking(self):
        p,u,b=fixture();b['daily']['AAA'].append({'session_date':b['date'],'c':'500','v':'900000000'});self.assertFalse(seal_selection(p,b['date'],u,b,'2026-10-08T13:19:00Z')['accepted'])
    def test_wrong_prior20_subset(self):
        p,u,b=fixture();b['prior20_dates']=b['prior20_dates'][1:];self.assertEqual(seal_selection(p,b['date'],u,b,'2026-10-08T13:19:00Z')['reason'],'not_exact_registered_prior20')
    def test_panel_digest_and_registered_panel(self):
        p,u,b=fixture();u[0]['symbol']='ZZZ';self.assertFalse(seal_selection(p,b['date'],u,b,'2026-10-08T13:19:00Z')['accepted'])
    def test_source_failed_batch(self):
        p,u,b=fixture();b['requests'][0]['http_status']=403;self.assertFalse(seal_selection(p,b['date'],u,b,'2026-10-08T13:19:00Z')['accepted'])
    def test_missing_price_history_is_observed_exclusion(self):
        p,u,b=fixture();b['daily']['AAA']=[];r=seal_selection(p,b['date'],u,b,'2026-10-08T13:19:00Z');self.assertTrue(r['accepted']);self.assertNotIn('AAA',r['selection']['selected_symbols'])
    def test_holiday_not_registered(self):
        p,_,_=fixture()
        with self.assertRaises(ValueError):check_clock(p,'2026-11-26','2026-11-26T14:00:00Z','preopen')
    def test_dst_and_early_closes(self):
        p,_,_=fixture();d={r['date']:r for r in p['sessions']}
        self.assertIn('13:30',d['2026-10-30']['open_utc']);self.assertIn('14:30',d['2026-11-02']['open_utc'])
        self.assertIn('18:00',d['2026-11-27']['close_utc']);self.assertEqual(d['2026-12-24']['close_et'],'13:00')
    def test_no_capture_before_target_date(self):
        p,_,_=fixture()
        with self.assertRaisesRegex(ValueError,'target_date'):check_clock(p,'2026-10-08','2026-10-07T17:00:00Z','preopen')
    def test_no_unregistered_capture(self):
        p,_,_=fixture();p['status']='draft'
        with self.assertRaisesRegex(ValueError,'not_registered'):check_clock(p,'2026-10-08','2026-10-08T13:00:00Z','preopen')
    def test_receipts_before_same_day_registration(self):
        p,u,b=fixture();p['registered_at']='2026-10-08T13:18:00Z';self.assertEqual(seal_selection(p,b['date'],u,b,'2026-10-08T13:19:00Z')['reason'],'capture_before_registration')
    def test_malformed_input_is_rejected(self):
        p,u,b=fixture();self.assertFalse(seal_selection(p,b['date'],u,None,'2026-10-08T13:19:00Z')['accepted']);b['requests']=[None];self.assertFalse(seal_selection(p,b['date'],u,b,'2026-10-08T13:19:00Z')['accepted'])
    def test_postclose_on_time_source_review_later(self):
        p,u,b=fixture();seal=seal_selection(p,b['date'],u,b,'2026-10-08T13:19:00Z');r=receipt(p,seal);self.assertTrue(validate_postclose(p,b['date'],seal,r,'2026-10-08T21:30:00Z')['accepted'])
    def test_postclose_late_arrival_not_saved_by_old_bar_time(self):
        p,u,b=fixture();seal=seal_selection(p,b['date'],u,b,'2026-10-08T13:19:00Z');r=receipt(p,seal);r['received_at']='2026-10-08T21:15:00.000001Z';self.assertFalse(validate_postclose(p,b['date'],seal,r,'2026-10-08T21:30:00Z')['accepted'])
    def test_postclose_future_receipt(self):
        p,u,b=fixture();seal=seal_selection(p,b['date'],u,b,'2026-10-08T13:19:00Z');r=receipt(p,seal);self.assertFalse(validate_postclose(p,b['date'],seal,r,'2026-10-08T20:50:00Z')['accepted'])
    def test_postclose_no_preopen_seal(self):
        p,_,b=fixture();self.assertFalse(validate_postclose(p,b['date'],{}, {},'2026-10-08T21:30:00Z')['accepted'])
    def test_postclose_no_symbol_replacement(self):
        p,u,b=fixture();seal=seal_selection(p,b['date'],u,b,'2026-10-08T13:19:00Z');r=receipt(p,seal);r['requested_symbols'][0]='ZZZ';self.assertFalse(validate_postclose(p,b['date'],seal,r,'2026-10-08T21:30:00Z')['accepted'])
    def test_ledger_unknown_is_not_zero(self):
        p,_,_=fixture();r=initial_ledger(p,'2026-10-07T17:00:00Z');self.assertEqual(len(r['rows']),59);self.assertTrue(all(x['status']=='not_due' and x['signals'] is None and x['mean_return'] is None for x in r['rows']));self.assertEqual(r['observed_future_dates'],0)
    def test_seal_selection_tampering(self):
        p,u,b=fixture();seal=seal_selection(p,b['date'],u,b,'2026-10-08T13:19:00Z');r=receipt(p,seal);seal['selection']['selected_symbols'][0]='ZZZ';self.assertEqual(validate_postclose(p,b['date'],seal,r,'2026-10-08T21:30:00Z')['reason'],'preopen_seal_content_changed')
    def test_ledger_missed_day_not_removed(self):
        p,_,_=fixture();r=initial_ledger(p,'2026-10-08T21:30:00Z');self.assertEqual(len(r['rows']),59);self.assertEqual(r['rows'][0]['status'],'missing_preopen_seal')
    def test_exclusive_write(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'record.json';write_once(p,{'first':True})
            with self.assertRaises(FileExistsError):write_once(p,{'first':False})
            self.assertTrue(json.loads(p.read_text())['first'])
if __name__=='__main__':unittest.main()
