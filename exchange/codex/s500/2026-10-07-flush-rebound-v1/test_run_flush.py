import unittest,copy
from decimal import Decimal
from flush_engine import prepare_bars,evaluate_prefix
from run_flush import peer_selection,evaluate_case,load_day_input
from test_flush_engine import bars,flush_bars,close_at

def fixture():
 names=['A','B','C','D','E','SPY','QQQ'];liq={'A':'100000000','B':'100000000','C':'101000000','D':'102000000','E':'900000000'}
 cases=[{'symbol':s,'case_id':'2026-01-02__'+s,'date':'2026-01-02','stock_eligible':s in liq,
   'selection_metrics':{'median_prior20_daily_close_times_volume':liq[s]} if s in liq else None,
   'roles':['synthetic'],'current_etf_classification':'synthetic'} for s in names]
 raw={s:bars() for s in names};raw['A']=flush_bars();close_at(raw['A'],30,'97.97')
 return cases,raw

def inputs(raw):
 p={s:prepare_bars(b) for s,b in raw.items()};prefix={s:{t:evaluate_prefix(v,t) for t in ['0.02','0.03']} for s,v in p.items()}
 return p,prefix

class RunnerTests(unittest.TestCase):
 def test_failed_day_without_file_keeps_unknown_risk_set(self):
  cases,_=fixture();m=load_day_input({'request_complete':False,'status':'collector_exception'},cases);self.assertEqual(set(m['requested_symbols']),{c['symbol'] for c in cases});self.assertFalse(m['request_complete']);self.assertEqual(m['bars'],{})
  with self.assertRaises(AssertionError):load_day_input({'request_complete':True},cases)
 def test_peers_selected_without_future_data(self):
  cases,raw=fixture();p,f=inputs(raw);a=peer_selection(cases[0],cases,f,'0.02');self.assertEqual([v['symbol'] for v in a],['B','C','D'])
  del raw['D'][92];p,f=inputs(raw);self.assertEqual(peer_selection(cases[0],cases,f,'0.02'),a)
 def test_incomplete_selected_peer_not_replaced(self):
  cases,raw=fixture();del raw['D'][92];p,f=inputs(raw);rows,_=evaluate_case(cases[0],cases,p,f,390,{'issues':[]})
  r=next(x for x in rows if x['drop_threshold']=='0.02' and x['family']=='REBOUND' and x['exit_horizon']=='60m' and x['cost_bps_per_side']==10)
  self.assertEqual(r['signal_status'],'signal');self.assertIsNotNone(r['net_return']);self.assertEqual([x['symbol'] for x in r['paired_controls']],['B','C','D']);self.assertIsNone(r['matched_excess']);self.assertEqual(r['paired_control_status'],'missing_return')
 def test_controls_have_same_times_and_cost(self):
  cases,raw=fixture();p,f=inputs(raw);rows,_=evaluate_case(cases[0],cases,p,f,390,{'issues':[]});self.assertEqual(len(rows),36)
  for r in rows:
   if r['signal_status']=='signal':
    for c in r['paired_controls']+list(r['benchmarks'].values()):self.assertEqual((c['entry_minute'],c['exit_minute']),(r['entry_minute'],r['exit_minute']))
    rate=Decimal(r['cost_bps_per_side'])/10000
    expected=(1-rate)/(1+rate)-1
    self.assertLess(abs(Decimal(r['paired_controls'][0]['net_return'])-expected),Decimal('1e-25'))
 def test_signal_remains_when_holding_window_missing(self):
  cases,raw=fixture();del raw['A'][92];p,f=inputs(raw);rows,_=evaluate_case(cases[0],cases,p,f,390,{'issues':[]});r=next(x for x in rows if x['drop_threshold']=='0.02' and x['family']=='REBOUND' and x['exit_horizon']=='60m' and x['cost_bps_per_side']==10)
  self.assertEqual(r['signal_status'],'signal');self.assertEqual(r['entry_minute'],32);self.assertIsNone(r['net_return']);self.assertEqual(r['missing_stage'],'return')
 def test_benchmark_null_metrics_not_stock_matched(self):
  cases,raw=fixture();p,f=inputs(raw);rows,diag=evaluate_case(cases[-1],cases,p,f,390,{'issues':[]});self.assertEqual(len(rows),36);self.assertIsNone(diag['prior_liquidity']);self.assertTrue(all(r['target_role']=='benchmark' and not r['paired_controls'] for r in rows))
 def test_flushed_stock_not_control_and_earlier_gap_excluded(self):
  cases,raw=fixture();raw['B']=flush_bars();del raw['C'][12];p,f=inputs(raw);self.assertEqual([x['symbol'] for x in peer_selection(cases[0],cases,f,'0.02')],['D','E'])

if __name__=='__main__':unittest.main()
