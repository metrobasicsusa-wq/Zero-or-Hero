"""Synthetic economic integration checks; no new market inputs or network."""
from pathlib import Path
import copy, gzip, json, sys, tempfile, unittest
from datetime import datetime, timedelta, timezone
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'deploy/research/s500'))
from analyze_session import analyze, VARIANTS, price_bin, fixed_bins
from vendor.flush_engine import evaluate_window
from vendor.normalize_bars import normalize_provider_bars


def fixture(minutes=390):
    day='2026-10-08';opening=datetime(2026,10,8,13,30,tzinfo=timezone.utc)
    protocol={'id':'fixture','sessions':[{'date':day,'open_et':'09:30','open_utc':opening.isoformat(),
       'close_utc':(opening+timedelta(minutes=minutes)).isoformat()}],
       'inherited_variants':[list(v) for v in VARIANTS],'primary_variant':['0.02','REBOUND','60m',10]}
    symbols=['AAA','BBB','CCC','DDD','SPY','QQQ'];rows=[]
    for i,s in enumerate(symbols+['UNSELECTED']):
        rows.append({'case_id':day+'__'+s,'date':day,'symbol':s,'selected':s in symbols,
          'stock_eligible':s not in ('SPY','QQQ'),'etf_classification':'Y' if s in ('SPY','QQQ') else 'N',
          'roles':['fixture'],'selection_metrics':None if s in ('SPY','QQQ') else
          {'prior_close':'100','median_prior20_daily_close_times_volume':str(20000000+i*1000000)}})
    seal={'date':day,'accepted':True,'selection':{'status':'ready','selected_symbols':symbols,'rows':rows}}
    bars={}
    for symbol in symbols:
        b=[]
        for t in range(minutes):
            price=100
            if symbol=='AAA':price=97 if 20<=t<30 else (99 if t>=30 else 100)
            elif symbol in ('SPY','QQQ'):price=99 if t>=20 else 100
            b.append({'t':(opening+timedelta(minutes=t)).isoformat(),'o':str(price),'h':str(price+1),
                      'l':str(price-1),'c':str(price),'v':'100'})
        bars[symbol]=b
    return protocol,seal,{'date':day,'requested_symbols':symbols,'bars':bars,'source_complete':True,'status':'complete'}


def readrows(path):
    with gzip.open(path,'rt') as f:return [json.loads(line) for line in f]


class AnalyzerTests(unittest.TestCase):
    def run_analysis(self,p=None,s=None,b=None):
        defaults=fixture();p=p or defaults[0];s=s or defaults[1];b=b or defaults[2]
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        out=Path(temporary.name)/'analysis'
        result=analyze(p,s,b,out)
        return result,out,readrows(out/'results.jsonl.gz')

    def test_full_case_variant_grid_and_benchmark_separation(self):
        summary,out,rows=self.run_analysis()
        self.assertEqual(len(rows),216)
        self.assertEqual(summary['counts']['variant_group_summaries'],216)
        self.assertEqual(summary['counts']['stock_cases'],4)
        self.assertEqual(summary['primary_SPY_down']['counts']['signals'],1)
        self.assertEqual(summary['primary_SPY_down']['counts']['complete_pairs'],1)
        self.assertEqual(len(summary['fixed_feature_bins']),12)
        self.assertEqual(len(readrows(out/'features.jsonl.gz')),6)
        self.assertNotIn('UNSELECTED',{r['symbol'] for r in rows})
        self.assertTrue(all(not r['actual_fill'] and not r['wealth_path'] for r in rows))

    def test_incomplete_source_preserves_unknown_not_zero(self):
        p,s,b=fixture();b['source_complete']=False
        summary,out,rows=self.run_analysis(p,s,b)
        self.assertEqual(len(rows),216)
        self.assertEqual({r['signal_status'] for r in rows},{'unknown'})
        self.assertEqual({r['net_return'] for r in rows},{None})
        self.assertEqual({r['SPY_group'] for r in rows if r['target_role']=='stock'},{'unknown'})
        self.assertEqual(summary['counts']['prefix_complete'],0)

    def test_no_flush_is_not_zero_trade(self):
        summary,out,rows=self.run_analysis()
        peers=[r for r in rows if r['symbol']=='BBB']
        self.assertEqual({r['signal_status'] for r in peers},{'no_flush'})
        self.assertEqual({r['net_return'] for r in peers},{None})

    def test_missing_future_peer_is_not_replaced(self):
        p,s,b=fixture();b['bars']['BBB']=[r for i,r in enumerate(b['bars']['BBB']) if i!=60]
        _,out,rows=self.run_analysis(p,s,b)
        row=next(r for r in rows if r['symbol']=='AAA' and
                 (r['drop_threshold'],r['family'],r['exit_horizon'],r['cost_bps_per_side'])==('0.02','REBOUND','60m',10))
        self.assertEqual([r['symbol'] for r in row['paired_controls']],['BBB','CCC','DDD'])
        self.assertEqual(row['paired_control_status'],'missing_return')
        self.assertIsNone(row['matched_excess']);self.assertIsNotNone(row['net_return'])

    def test_unselected_stock_cannot_enter_peers(self):
        p,s,b=fixture();s['selection']['rows'][-1]['selection_metrics']['median_prior20_daily_close_times_volume']='20000000'
        _,out,rows=self.run_analysis(p,s,b)
        for row in rows:
            self.assertNotIn('UNSELECTED',[q['symbol'] for q in row['paired_controls']])

    def test_cost_is_engine_net_once(self):
        p,s,b=fixture();_,out,rows=self.run_analysis(p,s,b)
        selected=[r for r in rows if r['symbol']=='AAA' and r['net_return'] is not None]
        normalized=normalize_provider_bars(b['bars']['AAA'],b['date'])['bars']
        for r in selected:
            expected=evaluate_window(normalized,r['entry_minute'],r['exit_minute'],r['cost_bps_per_side'])
            self.assertEqual(r['net_return'],expected['net_return'])

    def test_half_day_close_horizon(self):
        p,s,b=fixture(210);_,out,rows=self.run_analysis(p,s,b)
        for row in rows:
            if row['symbol']=='AAA' and row['exit_horizon']=='close_minus_10m':
                self.assertEqual(row['exit_minute'],200)

    def test_missing_benchmark_does_not_destroy_target_price_return(self):
        p,s,b=fixture();b['bars']['SPY']=[];_,out,rows=self.run_analysis(p,s,b)
        target=[r for r in rows if r['symbol']=='AAA']
        self.assertEqual({r['SPY_group'] for r in target},{'unknown'})
        self.assertTrue(any(r['net_return'] is not None for r in target))

    def test_atomic_nonoverwrite(self):
        p,s,b=fixture();_,out,rows=self.run_analysis(p,s,b)
        originals={x.name:x.read_bytes() for x in out.iterdir()}
        with self.assertRaises(FileExistsError):analyze(p,s,b,out)
        self.assertEqual(originals,{x.name:x.read_bytes() for x in out.iterdir()})
        self.assertFalse(out.with_name(out.name+'.analysis-lock').exists())

    def test_source_scope_mismatch_rejected(self):
        p,s,b=fixture();b['requested_symbols']=b['requested_symbols'][:-1]
        with self.assertRaisesRegex(ValueError,'source_requested_symbols'):self.run_analysis(p,s,b)

    def test_changed_variants_rejected(self):
        p,s,b=fixture();p['inherited_variants'].pop()
        with self.assertRaisesRegex(ValueError,'economic_variants'):self.run_analysis(p,s,b)

    def test_unaccepted_seal_rejected(self):
        p,s,b=fixture();s['accepted']=False
        with self.assertRaisesRegex(ValueError,'accepted_preopen'):self.run_analysis(p,s,b)

    def test_duplicate_source_minute_retained_as_unknown(self):
        p,s,b=fixture();b['bars']['AAA'].append(dict(b['bars']['AAA'][0]))
        _,out,rows=self.run_analysis(p,s,b)
        self.assertEqual({r['signal_status'] for r in rows if r['symbol']=='AAA'},{'unknown'})

    def test_exact_bin_boundaries(self):
        self.assertEqual(price_bin('98','100'),'le_minus_2pct')
        self.assertEqual(price_bin('98.00000000000000000000001','100'),'minus_2pct_to_below_zero')
        self.assertEqual(price_bin('100','100'),'zero_or_positive')
        self.assertEqual(price_bin(None,'100'),'unknown')

    def test_failure_does_not_publish_partial_output(self):
        p,s,b=fixture();b['bars']['AAA']='malformed'
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);out=Path(temp.name)/'failed'
        with self.assertRaises(ValueError):analyze(p,s,b,out)
        self.assertFalse(out.exists())

    def test_gzip_is_deterministic(self):
        _,out,rows=self.run_analysis();_,other,otherrows=self.run_analysis()
        self.assertEqual((out/'results.jsonl.gz').read_bytes(),(other/'results.jsonl.gz').read_bytes())


if __name__=='__main__':unittest.main()
