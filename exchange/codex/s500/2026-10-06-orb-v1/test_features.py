"""Synthetic causal, split, threshold and opening-RV tests, no network."""
import copy
from datetime import date, timedelta
import unittest
from features import _prepare, rank_openings


def bar(c=10., v=2_000_000., width=1., o=None):
    return {'o':c if o is None else o, 'h':c+width/2, 'l':c-width/2, 'c':c, 'v':v}


def calendar(n=18):
    dd=[(date(2026,1,1)+timedelta(days=i)).isoformat() for i in range(n+1)]
    return [{'date':dd[i],'open':'09:30','close':'16:00','settlement_date':dd[i+1]} for i in range(n)]


def prepared(symbols=('A',), actions=None, classifications=None):
    cal=calendar(); market={s:{r['date']:bar() for r in cal} for s in symbols}
    return _prepare(market,cal,list(symbols),classifications or [],actions or [],start=cal[15]['date'],end=cal[16]['date']),market,cal


def openings(p):
    return {d:{s:bar(v=1000) for s in symbols} for d,symbols in p['opening_requests'].items()}


class OrbFeatures(unittest.TestCase):
    def test_ATR_prior15_only_and_current_daily_not_used(self):
        p,m,cal=prepared()
        self.assertEqual(p['daily_candidates'][cal[15]['date']][0]['atr14'],1)
        self.assertEqual(len(p['prior_opening_dates'][cal[15]['date']]),14)
        m['A'][cal[15]['date']]=bar(c=999999,v=10**15)
        q=_prepare(m,cal,['A'],[],[],start=cal[15]['date'],end=cal[15]['date'])
        self.assertEqual(p['daily_candidates'][cal[15]['date']],q['daily_candidates'][cal[15]['date']])
        del m['A'][cal[15]['date']]
        q=_prepare(m,cal,['A'],[],[],start=cal[15]['date'],end=cal[15]['date'])
        self.assertEqual(len(q['daily_candidates'][cal[15]['date']]),1)

    def test_true_range_includes_prior_close_gap(self):
        p,m,cal=prepared();m['A'][cal[14]['date']]=bar(c=15,v=2e6,width=1)
        q=_prepare(m,cal,['A'],[],[],start=cal[15]['date'],end=cal[15]['date'])
        self.assertAlmostEqual(q['daily_candidates'][cal[15]['date']][0]['atr14'],(13*1+5.5)/14)

    def test_thresholds_and_classification(self):
        cal=calendar();spec=[('PASS',1e6,1),('THIN',999999,1),('ATR',2e6,.5),('ETF',2e6,1),('UNKNOWN',2e6,1)]
        market={s:{r['date']:bar(v=v,width=w) for r in cal} for s,v,w in spec}
        q=_prepare(market,cal,list(market),[{'symbol':'ETF','etf':'Y','test_issue':'N'}],[],start=cal[15]['date'],end=cal[15]['date'])
        self.assertEqual({x['symbol'] for x in q['daily_candidates'][cal[15]['date']]},{'PASS','UNKNOWN'})
        self.assertTrue(q['daily_candidates'][cal[15]['date']][0]['classification_unknown'])

    def test_request_union_contains_every_prior14_and_current(self):
        p,_,cal=prepared();day=cal[15]['date']
        for d in [day,*p['prior_opening_dates'][day]]:self.assertIn('A',p['opening_requests'][d])
        self.assertEqual(len(p['opening_requests']),16)

    def test_split_adjusts_ATR_and_opening_relative_volume(self):
        cal=calendar();dd=[x['date'] for x in cal]
        action={'type':'forward_split','symbol':'A','ex_date':dd[10],'old_rate':1,'new_rate':2}
        market={'A':{d:bar(c=20 if i<10 else 10,v=1e6 if i<10 else 2e6,width=2 if i<10 else 1) for i,d in enumerate(dd)}}
        p=_prepare(market,cal,['A'],[],[action],start=dd[15],end=dd[15])
        self.assertEqual(p['daily_candidates'][dd[15]][0]['atr14'],1)
        self.assertEqual(p['daily_candidates'][dd[15]][0]['mean_volume14'],2e6)
        oo={d:{'A':bar(c=20 if d<dd[10] else 10,v=1000 if d<dd[10] else 2000)} for d in p['opening_requests']}
        ranked=rank_openings(p,oo,diagnostics_path=None)
        self.assertEqual(ranked[dd[15]][0]['rv'],1)
        self.assertEqual(ranked[dd[15]][0]['mean_opening_volume14'],2000)

    def test_reverse_split_volume_neutral_and_future_split_ignored(self):
        cal=calendar();dd=[x['date'] for x in cal]
        action={'type':'reverse_split','symbol':'A','ex_date':dd[10],'old_rate':2,'new_rate':1}
        market={'A':{d:bar(c=10 if i<10 else 20,v=2e6 if i<10 else 1e6,width=1 if i<10 else 2) for i,d in enumerate(dd)}}
        p=_prepare(market,cal,['A'],[],[action],start=dd[15],end=dd[15])
        self.assertEqual(p['daily_candidates'][dd[15]][0]['atr14'],2)
        self.assertEqual(p['daily_candidates'][dd[15]][0]['mean_volume14'],1e6)
        oo={d:{'A':bar(c=10 if d<dd[10] else 20,v=2000 if d<dd[10] else 1000)} for d in p['opening_requests']}
        first=rank_openings(p,oo,diagnostics_path=None)
        future={'type':'forward_split','symbol':'A','ex_date':dd[16],'old_rate':1,'new_rate':100}
        q=_prepare(market,cal,['A'],[],[action,future],start=dd[15],end=dd[15])
        second=rank_openings(q,oo,diagnostics_path=None)
        self.assertEqual(first,second)
        self.assertEqual(first[dd[15]][0]['rv'],1)

    def test_missing_prior_is_not_zero_filled_or_hidden(self):
        p,_,cal=prepared(('A','B'));oo=openings(p);del oo[p['prior_opening_dates'][cal[15]['date']][0]]['B']
        ranked=rank_openings(p,oo,diagnostics_path=None)
        self.assertEqual(len(ranked[cal[15]['date']]),1)
        self.assertFalse(ranked[cal[15]['date']][0]['ranking_complete'])
        gap=p['ranking_diagnostics']['coverage'][cal[15]['date']]['ranking_missing'][0]
        self.assertEqual(gap['reason'],'missing_or_invalid_prior_openings')

    def test_missing_today_has_distinct_reason(self):
        p,_,cal=prepared();oo=openings(p);del oo[cal[15]['date']]['A']
        ranked=rank_openings(p,oo,diagnostics_path=None)
        self.assertEqual(ranked[cal[15]['date']],[])
        self.assertEqual(p['ranking_diagnostics']['coverage'][cal[15]['date']]['ranking_missing'][0]['reason'],'missing_or_invalid_current_opening')

    def test_top20_before_bullish_direction_not_backfilled(self):
        syms=[f'S{i:02}' for i in range(21)];p,_,cal=prepared(syms);oo=openings(p);day=cal[15]['date']
        for i,s in enumerate(syms):oo[day][s]=bar(c=10,v=(22-i)*1000,width=2,o=10.5 if i<20 else 9.5)
        ranked=rank_openings(p,oo,diagnostics_path=None)[day]
        self.assertEqual(len(ranked),20)
        self.assertNotIn('S20',{r['symbol'] for r in ranked})
        self.assertTrue(all(not r['long_eligible'] for r in ranked))

    def test_opening_price_strict_and_rv_threshold(self):
        p,_,cal=prepared(('AT5','PASS','LOWRV'));oo=openings(p);day=cal[15]['date']
        oo[day]['AT5']=bar(c=5,v=1000)
        oo[day]['LOWRV']=bar(v=999)
        ranked=rank_openings(p,oo,diagnostics_path=None)[day]
        self.assertEqual([r['symbol'] for r in ranked],['PASS'])
        self.assertTrue(ranked[0]['ranking_complete'])

    def test_future_openings_do_not_change_previous_rank(self):
        p,_,cal=prepared(('A','B'));oo=openings(p);first=rank_openings(copy.deepcopy(p),oo,diagnostics_path=None)
        oo[cal[16]['date']]['B']=bar(c=1000,v=10**10)
        second=rank_openings(copy.deepcopy(p),oo,diagnostics_path=None)
        self.assertEqual(first[cal[15]['date']],second[cal[15]['date']])

    def test_ambiguous_split_window_is_recorded(self):
        cal=calendar();action={'type':'reverse_split','symbol':'A','new_symbol':'B','ex_date':cal[10]['date'],'old_rate':10,'new_rate':1}
        p,_,_=prepared(actions=[action])
        self.assertEqual(p['daily_candidates'][cal[15]['date']],[])
        self.assertTrue(p['diagnostics']['action_ambiguity_exclusions'])


if __name__=='__main__':unittest.main()
