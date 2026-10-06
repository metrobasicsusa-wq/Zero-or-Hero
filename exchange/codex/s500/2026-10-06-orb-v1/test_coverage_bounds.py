import copy
from fractions import Fraction
import unittest
from features import rank_openings
from coverage_bounds import certify


def candle(v, o=10):
    return {'o':o,'h':11,'l':9,'c':10.5,'v':v}


def fixture(unknown='ZZZ', current=100, known_count=20, historical=100):
    day='2026-01-30'; prior=[f'2026-01-{x:02}' for x in range(1,15)]
    symbols=[f'K{i:02}' for i in range(known_count)]+[unknown]
    prep={'calendar':[{'date':day}], 'prior_opening_dates':{day:prior}, 'split_index':{},
          'daily_candidates':{day:[{'symbol':s,'atr14':1} for s in symbols]}}
    market={d:{s:candle(historical) for s in symbols} for d in [*prior,day]}
    for s in symbols:market[day][s]['v']=200
    market[day][unknown]['v']=current
    del market[prior[-1]][unknown]
    return prep,market,day,prior


def run(prep,market):
    ranked=rank_openings(prep,market,diagnostics_path=None)
    diag=prep['ranking_diagnostics']
    before=copy.deepcopy((prep,market,ranked,diag))
    result=certify(prep,market,ranked,diag)
    assert (prep,market,ranked,diag)==before, 'must_not_mutate_inputs'
    return result


class BoundsTest(unittest.TestCase):
    def test_below1_certified_with_fewer_than20(self):
        p,m,d,_=fixture(current=50,known_count=2)
        r=run(p,m);self.assertTrue(r['coverage'][d]['ranking_complete'])
        self.assertEqual(r['proofs'][0]['proof_reason'],'RV_upper_bound_below1')
        self.assertEqual(len(r['coverage'][d]['original_ranking_missing']),1)
        self.assertFalse(r['coverage'][d]['source_inputs_complete'])

    def test_lower_bound_sortkey_after20(self):
        p,m,d,_=fixture(current=150)
        r=run(p,m);self.assertTrue(r['coverage'][d]['ranking_complete'])
        self.assertEqual(r['proofs'][0]['proof_reason'],'RV_upper_bound_sortkey_after_known20th')

    def test_tie_after_cutoff_excluded(self):
        p,m,d,prior=fixture(current=26,historical=14)
        for s,b in m[d].items():
            if s!='ZZZ': b['v']=28
        r=run(p,m);self.assertTrue(r['coverage'][d]['ranking_complete'])
        self.assertEqual(r['proofs'][0]['relative_volume_upper_bound']['decimal'],2)

    def test_tie_before_cutoff_cannot_certify(self):
        p,m,d,_=fixture(unknown='AAA',current=26,historical=14)
        for s,b in m[d].items():
            if s!='AAA':b['v']=28
        r=run(p,m);self.assertFalse(r['coverage'][d]['ranking_complete'])

    def test_equal_threshold_with_fewer_than20_not_excluded(self):
        p,m,d,_=fixture(current=13,historical=14,known_count=19)
        r=run(p,m);self.assertFalse(r['coverage'][d]['ranking_complete'])
        self.assertEqual(r['proofs'][0]['relative_volume_upper_bound']['decimal'],1)

    def test_missing_current_never_bounded(self):
        p,m,d,_=fixture(current=1)
        del m[d]['ZZZ'];r=run(p,m)
        self.assertFalse(r['coverage'][d]['ranking_complete'])
        self.assertEqual(r['proofs'][0]['proof_reason'],'missing_or_invalid_current_opening')

    def test_all_missing_history_positive_and_zero_current_unresolved(self):
        for current in (100,0):
            p,m,d,prior=fixture(current=current)
            for date in prior:m[date].pop('ZZZ',None)
            r=run(p,m);self.assertFalse(r['coverage'][d]['ranking_complete'])
            self.assertIsNone(r['proofs'][0]['relative_volume_upper_bound'])

    def test_zero_current_with_positive_known_history_excluded(self):
        p,m,d,_=fixture(current=0)
        self.assertTrue(run(p,m)['coverage'][d]['ranking_complete'])

    def test_split_adjustment_effective_and_no_future(self):
        p,m,d,prior=fixture(current=200)
        split={'screen_date':'2026-01-20','new_rate':3,'old_rate':2,'issues':[]}
        p['split_index']['ZZZ']=[split,{'screen_date':'2026-02-01','new_rate':100,'old_rate':1,'issues':[]}]
        r=run(p,m)
        self.assertTrue(r['coverage'][d]['ranking_complete'])
        q=r['proofs'][0]['relative_volume_upper_bound']
        self.assertEqual(Fraction(q['numerator'],q['denominator']),Fraction(56,39))

    def test_reverse_split_volume_reduction_can_prevent_exclusion(self):
        p,m,d,prior=fixture(current=100)
        p['split_index']['ZZZ']=[{'screen_date':'2026-01-20','new_rate':1,'old_rate':2,'issues':[]}]
        r=run(p,m)
        self.assertFalse(r['coverage'][d]['ranking_complete'])
        q=r['proofs'][0]['relative_volume_upper_bound']
        self.assertEqual(Fraction(q['numerator'],q['denominator']),Fraction(28,13))

    def test_invalid_prior_OHLC_is_unknown_not_trusted_volume(self):
        p,m,d,prior=fixture(current=180)
        m[prior[0]]['ZZZ']['h']=1
        r=run(p,m)
        self.assertEqual(r['proofs'][0]['known_prior_volume_count'],12)
        self.assertFalse(r['coverage'][d]['ranking_complete'])

    def test_proven_exclusion_exhaustive_nonnegative_completions(self):
        p,m,d,prior=fixture(current=150)
        result=run(p,m);self.assertTrue(result['coverage'][d]['ranking_complete'])
        original=[x['symbol'] for x in rank_openings(p,m,None)[d]]
        for v in (0,.01,1,100,10000,1e12):
            m[prior[-1]]['ZZZ']=candle(v)
            self.assertEqual([x['symbol'] for x in rank_openings(p,m,None)[d]],original)

    def test_mismatching_strict_ranking_denies_certification(self):
        p,m,d,_=fixture(current=50)
        ranked=rank_openings(p,m,None);ranked[d]=list(reversed(ranked[d]))
        r=certify(p,m,ranked,p['ranking_diagnostics'])
        self.assertFalse(r['coverage'][d]['ranking_complete'])
        self.assertIn('exact_vs_strict_top20_disagreement',[x['reason'] for x in r['coverage'][d]['ranking_missing']])


if __name__=='__main__':unittest.main()
