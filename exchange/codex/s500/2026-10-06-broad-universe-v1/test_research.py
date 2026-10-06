import unittest
from research import feature,run_path


def bar(o=100,h=101,l=99,c=100,v=1_000_000):return dict(o=o,h=h,l=l,c=c,v=v)


class DailyResearchTests(unittest.TestCase):
    def test_breakout_uses_prior_high_and_current_volume(self):
        history=[bar() for _ in range(20)]
        result=feature(history,bar(h=105,c=104,v=2_000_000))
        self.assertTrue(result['breakout20'])
        self.assertFalse(feature(history,bar(h=105,c=104,v=1_000_000))['breakout20'])

    def test_reversal_requires_late_recovery(self):
        history=[bar() for _ in range(20)]
        self.assertTrue(feature(history,bar(o=94,h=96,l=90,c=94,v=2_000_000))['reversal'])
        self.assertFalse(feature(history,bar(o=94,h=96,l=90,c=91,v=2_000_000))['reversal'])

    def test_today_volume_does_not_improve_prior_liquidity_rank(self):
        history=[bar() for _ in range(20)]
        a=feature(history,bar(v=1_000_000));b=feature(history,bar(v=10**12))
        self.assertEqual(a['median_prior20_dollar_volume'],b['median_prior20_dollar_volume'])

    def test_incomplete_history_and_extreme_changes_preserved(self):
        self.assertIsNone(feature([bar()]*19,bar()))
        self.assertEqual(feature([bar()]*20,bar(h=150,c=140))['status'],'extreme_move_excluded')
        self.assertEqual(feature([bar()]*20,None)['status'],'today_missing')

    def test_missing_first_pick_stops_path(self):
        signals={'2026-01-02':{'breakout20':[{'symbol':'A','signal_date':'2025-12-31','rank':1},{'symbol':'B','signal_date':'2025-12-31','rank':2}]}}
        data={'A':{},'B':{'2026-01-02':bar()}}
        result=run_path(['2026-01-02'],signals,data,'breakout20',10)
        self.assertIsNone(result['ending_equity'])
        self.assertEqual(result['incomplete']['symbol'],'A')

    def test_no_fractional_share_and_no_runner_up(self):
        signals={'2026-01-02':{'breakout20':[{'symbol':'A','signal_date':'2025-12-31','rank':1},{'symbol':'B','signal_date':'2025-12-31','rank':2}]}}
        data={'A':{'2026-01-02':bar(o=600,h=700,l=590,c=650)},'B':{'2026-01-02':bar()}}
        result=run_path(['2026-01-02'],signals,data,'breakout20',10)
        self.assertEqual(result['ending_equity'],500)
        self.assertEqual(result['unaffordable_days'],1)

    def test_next_session_fill_accounting(self):
        signals={'2026-01-02':{'breakout20':[{'symbol':'A','signal_date':'2025-12-31','rank':1}]}}
        data={'A':{'2026-01-02':bar(o=100,h=115,c=110)}}
        result=run_path(['2026-01-02'],signals,data,'breakout20',10)
        self.assertEqual(result['ledger'][0]['qty'],4)
        self.assertAlmostEqual(result['ending_equity'],500+4*(109.89-100.1))


if __name__=='__main__':unittest.main()
