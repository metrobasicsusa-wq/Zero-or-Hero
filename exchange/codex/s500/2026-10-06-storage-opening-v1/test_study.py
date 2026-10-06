import copy
import unittest
from study import opening_signal, simulate


def bar(o=102,h=103,l=101,c=102):return dict(o=o,h=h,l=l,c=c)


class CausalityAndPortfolioTests(unittest.TestCase):
    def test_breakout_waits_for_completed_bar_and_five_minute_latency(self):
        bars={m:bar() for m in range(570,660,5)}
        bars[585]=bar(h=105,c=104)
        event=opening_signal(bars,100,'breakout')
        self.assertEqual((event['decision_minute'],event['entry_minute']),(590,595))

    def test_touch_without_close_is_not_breakout(self):
        bars={m:bar(h=106,c=102) for m in range(585,660,5)}
        bars.update({m:bar() for m in [570,575,580]})
        self.assertEqual(opening_signal(bars,100,'breakout')['status'],'no_trigger')

    def test_retest_requires_a_distinct_later_bar(self):
        bars={m:bar() for m in range(570,660,5)}
        bars[585]=bar(h=105,c=104)
        bars[590]=bar(h=105,l=102,c=104)
        event=opening_signal(bars,100,'retest')
        self.assertEqual(event['entry_minute'],600)
        del bars[590]
        self.assertEqual(opening_signal(bars,100,'retest')['status'],'missing_signal_bar')

    def test_expensive_first_candidate_does_not_swap_to_future_winner(self):
        date='2026-07-01';calendar=[{'date':date,'close':'16:00'}]
        data={'A':{date:{595:bar(o=600),720:bar(o=900)}},
              'B':{date:{600:bar(o=10),720:bar(o=20)}}}
        candidates={(date,'A','breakout'):{'status':'signal','gap':.03,'decision_minute':590,'entry_minute':595},
                    (date,'B','breakout'):{'status':'signal','gap':.02,'decision_minute':595,'entry_minute':600}}
        path=simulate(data,calendar,candidates,['A','B'],'breakout',720,10,[date])
        self.assertEqual(path['ending_equity'],500)
        self.assertEqual(path['unaffordable_days'],1)
        self.assertEqual(path['trade_count'],0)

    def test_missing_selected_exit_invalidates_path_not_candidate_selection(self):
        date='2026-07-01';calendar=[{'date':date,'close':'16:00'}]
        data={'A':{date:{595:bar(o=50)}},'B':{date:{600:bar(o=10),720:bar(o=20)}}}
        candidates={(date,'A','breakout'):{'status':'signal','gap':.03,'decision_minute':590,'entry_minute':595},
                    (date,'B','breakout'):{'status':'signal','gap':.02,'decision_minute':595,'entry_minute':600}}
        path=simulate(data,calendar,candidates,['A','B'],'breakout',720,10,[date])
        self.assertIsNone(path['ending_equity'])
        self.assertEqual(path['incomplete']['symbol'],'A')

    def test_no_same_day_reuse_whole_shares_and_costs(self):
        date='2026-07-01';calendar=[{'date':date,'close':'16:00'}]
        data={'A':{date:{595:bar(o=100),720:bar(o=110)}}}
        candidates={(date,'A','breakout'):{'status':'signal','gap':.03,'decision_minute':590,'entry_minute':595}}
        path=simulate(data,calendar,candidates,['A'],'breakout',720,10,[date])
        self.assertEqual(path['ledger'][0]['qty'],4)
        self.assertAlmostEqual(path['ending_equity'],500+4*(109.89-100.1))


if __name__=='__main__':unittest.main()
