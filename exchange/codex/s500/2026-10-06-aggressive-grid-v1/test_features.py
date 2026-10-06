"""Independent synthetic checks, no source prices or network required."""
import copy
from datetime import date, timedelta
import unittest
from features import adjusted_window, construct_candidates, index_splits, signal_features


def bar(c=100., v=1_000_000., h=None, l=None, o=None):
    return {'o': c if o is None else o, 'h': c+1 if h is None else h,
            'l': c-1 if l is None else l, 'c': c, 'v': v}


def dates(n=24):
    return [(date(2026, 1, 1)+timedelta(days=i)).isoformat() for i in range(n)]


def protocol(top=500):
    return {'scope': {'start': '2026-01-22', 'end': '2026-01-23'},
            'screen': {'prior_sessions_required': 20, 'min_prior_close': 2,
                       'minimum_prior20_median_dollar_volume': 10_000_000,
                       'pool_top_liquidity': top}}


class FeatureTests(unittest.TestCase):
    def test_positive_family_thresholds_and_strengths(self):
        history = [bar() for _ in range(20)]
        row = signal_features(history, bar(110, 2_000_000))
        self.assertEqual(set(row['signals']), {'breakout20','momentum20','acceleration3','volume_ignition'})
        self.assertAlmostEqual(row['signals']['breakout20'], 110/101-1)
        self.assertAlmostEqual(row['signals']['momentum20'], .10)
        self.assertAlmostEqual(row['signals']['acceleration3'], .10)
        self.assertAlmostEqual(row['signals']['volume_ignition'], .20)

    def test_pullback_and_reclaim_cover_all_six_families(self):
        history = [bar() for _ in range(19)] + [bar(120)]
        row = signal_features(history, bar(114))
        self.assertIn('trend_pullback', row['signals'])
        self.assertAlmostEqual(row['signals']['trend_pullback'], .05)
        history = [bar() for _ in range(20)]
        row = signal_features(history, bar(95, 1_500_000, h=97, l=91))
        self.assertIn('reclaim_down', row['signals'])
        self.assertAlmostEqual(row['signals']['reclaim_down'], .05)

    def test_strict_breakout_and_reclaim_midpoint(self):
        history = [bar() for _ in range(20)]
        self.assertNotIn('breakout20', signal_features(history, bar(101, 2_000_000))['signals'])
        self.assertNotIn('reclaim_down', signal_features(history, bar(95, 2_000_000, h=97, l=93))['signals'])
        self.assertNotIn('reclaim_down', signal_features(history, bar(84, 2_000_000, h=85, l=80))['signals'])

    def test_volume_boundary_and_large_moves_are_not_removed(self):
        history = [bar() for _ in range(20)]
        self.assertIn('breakout20', signal_features(history, bar(110, 1_500_000))['signals'])
        self.assertNotIn('breakout20', signal_features(history, bar(110, 1_499_999))['signals'])
        row = signal_features(history, bar(150, 2_000_000))
        self.assertIn('volume_ignition', row['signals'])
        self.assertIn('large_adjusted_daily_move_gt25pct_not_excluded', row['flags'])

    def test_split_neutrality_forward_and_reverse(self):
        dd = dates()
        for old, new in ((1, 2), (10, 1)):
            ratio = new/old
            series = {d: bar(100 if i<10 else 100/ratio, 1_000_000 if i<10 else 1_000_000*ratio,
                             h=101 if i<10 else 101/ratio, l=99 if i<10 else 99/ratio)
                      for i,d in enumerate(dd)}
            saved = copy.deepcopy(series)
            idx,_ = index_splits([{'type':'forward_split' if ratio>1 else 'reverse_split', 'symbol':'A',
                                 'ex_date':dd[10], 'old_rate':old, 'new_rate':new}])
            adjusted, flags, bad = adjusted_window('A',dd[:20],series,dd[20],idx)
            self.assertFalse(bad)
            self.assertEqual(len(flags),1)
            row = signal_features(adjusted, series[dd[20]])
            self.assertAlmostEqual(row['daily_return'],0)
            self.assertAlmostEqual(row['return20'],0)
            self.assertAlmostEqual(row['relative_volume'],1)
            self.assertAlmostEqual(row['liquidity'],100_000_000)
            self.assertEqual(row['signals'],{})
            self.assertEqual(series,saved)

    def test_future_split_and_future_bars_do_not_change_old_features(self):
        dd = dates(); market = {'A': {d:bar() for d in dd}}
        original, _ = construct_candidates(market,dd,['A'],[],[],protocol())
        altered = copy.deepcopy(market)
        altered['A'][dd[21]]=bar(999999, 10**12)
        action={'type':'forward_split','symbol':'A','ex_date':dd[21],'old_rate':1,'new_rate':100}
        revised, _ = construct_candidates(altered,dd,['A'],[],[action],protocol())
        self.assertEqual([r for r in original if r['date'] < dd[21]],
                         [r for r in revised if r['date'] < dd[21]])
        self.assertNotEqual(original,revised)

    def test_prior_liquidity_ranking_not_signal_day_volume(self):
        dd=dates(); market={s:{d:bar(100,v) for d in dd} for s,v in [('A',2_000_000),('B',1_000_000)]}
        market['B'][dd[20]]=bar(110,10**12)
        rows,_=construct_candidates(market,dd,['A','B'],[],[],protocol(top=1))
        self.assertEqual(next(r for r in rows if r['date']==dd[20])['symbol'],'A')
        self.assertEqual(len(rows),3)
        self.assertTrue(all(r['rank']==1 for r in rows))

    def test_classification_missing_bar_and_no_signal_rows_retained(self):
        dd=dates(); market={s:{d:bar() for d in dd} for s in ['ETF','KNOWN','UNKNOWN']}
        del market['UNKNOWN'][dd[20]]
        classes=[{'symbol':'ETF','etf':'Y','test_issue':'N'},{'symbol':'KNOWN','etf':'N','test_issue':'N'}]
        rows,diag=construct_candidates(market,dd,list(market),classes,[],protocol())
        first=[r for r in rows if r['date']==dd[20]]
        self.assertEqual({r['symbol'] for r in first},{'KNOWN','UNKNOWN'})
        unknown=next(r for r in first if r['symbol']=='UNKNOWN')
        self.assertTrue(unknown['classification_unknown'])
        self.assertEqual(unknown['signals'],{})
        self.assertIn('missing_or_invalid_decision_bar',unknown['flags'])
        self.assertFalse(next(r for r in first if r['symbol']=='KNOWN')['classification_unknown'])
        self.assertEqual(diag['daily'][0]['missing_decision_bar_in_pool'],1)

    def test_price_and_liquidity_thresholds(self):
        dd=dates(); market={s:{d:bar(c,v,h=c+.01,l=c-.01) for d in dd}
                            for s,c,v in [('EDGE',2,5_000_000),('CHEAP',1.99,9_000_000),('THIN',2,4_999_999)]}
        rows,_=construct_candidates(market,dd,list(market),[],[],protocol())
        self.assertEqual({r['symbol'] for r in rows},{'EDGE'})

    def test_ambiguous_split_is_disclosed_and_not_adjusted(self):
        dd=dates(); series={d:bar() for d in dd}
        for rows in ([{'type':'reverse_split','symbol':'A','new_symbol':'B','ex_date':dd[10],'old_rate':10,'new_rate':1}],
                     [{'type':'forward_split','symbol':'A','ex_date':dd[10],'old_rate':1,'new_rate':2}]*2):
            idx,_=index_splits(rows)
            adjusted,flags,bad=adjusted_window('A',dd[:20],series,dd[20],idx)
            self.assertTrue(bad); self.assertEqual(flags,[])
            self.assertEqual(adjusted,[series[d] for d in dd[:20]])
        rows,diag=construct_candidates({'A':series},dd,['A'],[],rows,protocol())
        self.assertEqual(rows,[])
        self.assertTrue(diag['unresolved_action_window_exclusions'])

    def test_missing_history_not_filled_from_future(self):
        dd=dates(); series={d:bar() for d in dd}; del series[dd[5]]
        rows,diag=construct_candidates({'A':series},dd,['A'],[],[],protocol())
        self.assertEqual(rows,[])
        self.assertEqual(diag['exclusion_counts']['prior_history_missing_or_invalid'],3)


if __name__=='__main__':
    unittest.main()
