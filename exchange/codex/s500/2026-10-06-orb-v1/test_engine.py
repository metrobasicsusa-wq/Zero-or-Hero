import copy
import unittest
from engine import run_path

D1='2026-01-02';D2='2026-01-05';D3='2026-01-06'


def bar(o=101,c=None,h=None,l=None,v=1000):
    c=o if c is None else c
    return {'o':o,'c':c,'h':max(o,c) if h is None else h,'l':min(o,c) if l is None else l,'v':v}


def row(symbol='A',rv=2):
    return {'symbol':symbol,'rank':1,'rv':rv,'or_open':100,'or_close':101,'or_high':102,'or_low':99,'atr14':2}


def market(symbols=('A',),n=10,signal=5):
    data={}
    for symbol in symbols:
        series={m:bar(101) for m in range(n)}
        series[0]=bar(100,l=99)
        series[4]=bar(101,h=102)
        if signal is not None:
            series[signal]=bar(101,c=103)
            for m in range(signal+1,n):series[m]=bar(103)
        data[symbol]=series
    return data


def cal(day=D1,close='09:40',settles=D2):
    return {'date':day,'open':'09:30','close':close,'settlement_date':settles}


def run(data=None,rows=None,calendar=None,fraction=1,bps=25,coverage=None,policy='stop'):
    return run_path({D1:[row()]} if rows is None else rows,
                    [cal()] if calendar is None else calendar,
                    {D1:market()} if data is None else data,fraction,bps,coverage,
                    pre_entry_gap_policy=policy)


class OrbCashEngineTests(unittest.TestCase):
    def test_latency_and_integer_cash_costs(self):
        p=run();t=p['trades'][0]
        self.assertEqual(t['signal_minute_offset'],5)
        self.assertEqual(t['entry_offset'],7)
        self.assertEqual(t['signal_completed_at'],'2026-01-02T09:36:00-05:00')
        self.assertEqual(t['entry_time'],'2026-01-02T09:37:00-05:00')
        self.assertEqual(t['qty'],4)
        self.assertAlmostEqual(t['entry_debit'],413.03)
        self.assertAlmostEqual(t['exit_credit'],410.97)
        self.assertAlmostEqual(p['final_equity'],497.94)
        self.assertEqual(p['initial_funding'],500)
        self.assertEqual(p['additional_funding'],0)

    def test_half_budget_leaves_cash(self):
        p=run(fraction=.5)
        self.assertEqual(p['entries'][0]['qty'],2)
        self.assertAlmostEqual(p['settled_cash'],293.485)

    def test_entry_minute_stop_is_active(self):
        m=market();m['A'][7]=bar(103,c=103,l=102)
        p=run(data={D1:m});t=p['trades'][0]
        self.assertEqual(t['exit_offset'],7)
        self.assertAlmostEqual(t['exit_price'],102.8)
        self.assertIsNone(t['exit_time'])
        self.assertEqual(t['exit_reason'],'stop_touch_in_minute')

    def test_gap_stop_fills_lower_open(self):
        m=market();m['A'][8]=bar(100)
        p=run(data={D1:m});t=p['trades'][0]
        self.assertEqual(t['exit_price'],100)
        self.assertEqual(t['exit_reason'],'stop_gap_open')
        self.assertLess(t['exit_price'],t['stop_price'])

    def test_last_minute_exit_uses_open_before_future_low(self):
        m=market();m['A'][9]=bar(110,c=80,h=111,l=70)
        p=run(data={D1:m})
        self.assertEqual(p['trades'][0]['exit_price'],110)
        self.assertEqual(p['trades'][0]['exit_reason'],'scheduled_last_minute_open')

    def test_same_time_ranking_is_rv_then_symbol(self):
        m=market(('A','B'));rows=[row('A',2),row('B',3)]
        p=run(data={D1:m},rows={D1:rows})
        self.assertEqual(p['entries'][0]['symbol'],'B')
        rows[0]['rv']=3
        q=run(data={D1:m},rows={D1:list(reversed(rows))})
        self.assertEqual(q['entries'][0]['symbol'],'A')

    def test_unaffordable_first_signal_consumed_then_other_stock(self):
        m=market(('A','B'),n=15,signal=None)
        m['A'][5]=bar(101,c=600)
        m['A'][6]=bar(101)
        m['A'][8]=bar(101,c=103)
        m['B'][8]=bar(101,c=103)
        for k in range(9,15):m['B'][k]=bar(103)
        p=run(data={D1:m},rows={D1:[row('A',3),row('B',2)]},calendar=[cal(close='09:45')])
        self.assertEqual(p['entries'][0]['symbol'],'B')
        self.assertEqual(p['entries'][0]['entry_offset'],10)
        self.assertEqual(len([s for s in p['signals'] if s['symbol']=='A']),1)
        self.assertEqual(p['signals'][0]['status'],'unaffordable_at_first_signal')

    def test_selected_open_unaffordable_never_switches(self):
        m=market(('A','B'));m['A'][7]=bar(600)
        p=run(data={D1:m},rows={D1:[row('A',3),row('B',2)]})
        self.assertEqual(p['entries'],[])
        self.assertEqual(p['final_equity'],500)
        self.assertEqual(p['skips'][0]['reason'],'selected_open_unaffordable_no_runner_up')

    def test_unsettled_sale_proceeds_cannot_fund_next_session(self):
        calendars=[cal(D1,settles=D3),cal(D2,settles=D3),cal(D3,settles='2026-01-07')]
        p=run(data={d:market() for d in [D1,D2,D3]},rows={d:[row()] for d in [D1,D2,D3]},calendar=calendars)
        self.assertEqual([x['entry_date'] for x in p['entries']],[D1,D3])
        self.assertAlmostEqual(p['daily'][1]['settled_cash_before_signals'],86.97)
        self.assertAlmostEqual(p['daily'][1]['unsettled_cash'],410.97)
        self.assertAlmostEqual(p['daily'][2]['settlement_cash_paid'],410.97)
        self.assertTrue(p['terminal_receivables'])
        self.assertEqual(p['terminal_receivables'][0]['settlement_date'],'2026-01-07')

    def test_missing_selected_entry_keeps_pending_intent(self):
        m=market();del m['A'][7]
        p=run(data={D1:m})
        self.assertEqual(p['incomplete']['reason'],'missing_selected_entry_open')
        self.assertIsNone(p['final_equity'])
        self.assertIsNotNone(p['pending_entry'])
        self.assertEqual(p['entries'],[])

    def test_missing_held_bar_preserves_cash_and_position(self):
        m=market();del m['A'][8]
        p=run(data={D1:m})
        self.assertEqual(p['incomplete']['reason'],'missing_or_invalid_held_minute')
        self.assertEqual(p['open_position']['qty'],4)
        self.assertAlmostEqual(p['settled_cash'],86.97)
        self.assertIsNone(p['final_equity'])
        self.assertEqual(p['trades'],[])
        self.assertEqual(p['open_position']['last_observed_time'],'2026-01-02T09:38:00-05:00')

    def test_missing_competitor_bar_prevents_later_winner_selection(self):
        m=market(('A','B'));del m['A'][5]
        p=run(data={D1:m},rows={D1:[row('A'),row('B',3)]})
        self.assertEqual(p['incomplete']['reason'],'unresolved_preselection_signal_gap')
        self.assertEqual(p['entries'],[])
        self.assertIsNone(p['final_equity'])
        self.assertTrue(all(r['status']=='unresolved_signal_gap' for r in p['daily'][0]['candidate_outcomes']))

    def test_wait_minute_has_no_position_and_is_not_retroactive_signal(self):
        m=market();del m['A'][6]
        p=run(data={D1:m})
        self.assertEqual(p['status'],'provisional_complete')
        self.assertEqual(p['entries'][0]['entry_offset'],7)
        self.assertEqual(p['coverage']['candidate_sessions_with_missing_bars'],1)

    def test_early_close_is_actual_last_regular_minute(self):
        m=market(n=210)
        p=run(data={D1:m},calendar=[cal(close='13:00')])
        self.assertEqual(p['trades'][0]['exit_offset'],209)
        self.assertEqual(p['trades'][0]['exit_time'],'2026-01-02T12:59:00-05:00')

    def test_offset_89_included_and_90_excluded(self):
        a=run(data={D1:market(n=390,signal=89)},calendar=[cal(close='16:00')])
        b=run(data={D1:market(n=390,signal=90)},calendar=[cal(close='16:00')])
        self.assertEqual(a['entries'][0]['entry_offset'],91)
        self.assertEqual(b['entries'],[])

    def test_future_prices_cannot_change_entry_choice_or_quantity(self):
        m=market();changed=copy.deepcopy(m)
        changed['A'][8]=bar(1000)
        a=run(data={D1:m});b=run(data={D1:changed})
        self.assertEqual(a['entries'],b['entries'])
        self.assertEqual(a['signals'],b['signals'])

    def test_incomplete_rank_input_is_not_treated_as_no_signal(self):
        p=run(coverage={D1:{'ranking_complete':False,'ranking_missing':['B']}})
        self.assertEqual(p['incomplete']['reason'],'incomplete_ranking_inputs')
        self.assertEqual(p['entries'],[])

    def test_unknown_ranking_coverage_is_disclosed(self):
        p=run()
        self.assertEqual(p['daily'][0]['ranking_coverage'],'unknown')
        self.assertEqual(p['coverage']['ranking_verified_days'],0)

    def test_missing_ranked_day_is_not_empty_candidate_day(self):
        p=run(rows={})
        self.assertEqual(p['incomplete']['reason'],'missing_ranked_day')

    def test_opening_feature_must_match_completed_first_five_minutes(self):
        r=row();r['or_high']=105
        p=run(rows={D1:[r]})
        self.assertEqual(p['incomplete']['reason'],'opening_feature_bar_mismatch')

    def test_rejects_same_day_cash_settlement(self):
        p=run(calendar=[cal(settles=D1)])
        self.assertEqual(p['incomplete']['reason'],'invalid_or_missing_t_plus_one_settlement_date')
        self.assertEqual(p['entries'],[])

    def test_empty_calendar_cannot_masquerade_as_completed_research(self):
        with self.assertRaisesRegex(ValueError,'empty_evaluation_calendar'):
            run(calendar=[])

    def test_no_reentry_after_early_stop(self):
        m=market(('A','B'),n=20,signal=None)
        m['A'][5]=bar(101,c=103);m['A'][7]=bar(103,l=100)
        m['B'][9]=bar(101,c=103)
        p=run(data={D1:m},rows={D1:[row('A',3),row('B',2)]},calendar=[cal(close='09:50')])
        self.assertEqual(len(p['entries']),1)
        self.assertEqual(len(p['trades']),1)
        self.assertEqual(p['entries'][0]['symbol'],'A')

    def test_guard_skips_whole_rank_gap_day_without_replacement(self):
        cov={D1:{'ranking_complete':False,'ranking_missing':[{'symbol':'B','missing_dates':['2025-12-24']}]},
             D2:{'ranking_complete':True}}
        p=run(data={D1:market(),D2:market()},rows={D1:[row()],D2:[row()]},
              calendar=[cal(),cal(D2,settles=D3)],coverage=cov,policy='skip_session')
        self.assertEqual([e['entry_date'] for e in p['entries']],[D2])
        self.assertEqual(p['daily'][0]['status'],'skipped_data_gap')
        self.assertEqual(p['daily'][0]['equity'],500)
        self.assertEqual(p['aborted_sessions'][0]['gap']['ranking_missing'],cov[D1]['ranking_missing'])
        self.assertEqual(p['coverage']['aborted_pre_entry_sessions'],1)
        self.assertTrue(p['id'].endswith('__guarded-skip-session'))

    def test_guard_preserves_cancelled_intent_missing_entry_and_next_day_cash(self):
        m=market();del m['A'][7]
        p=run(data={D1:m,D2:market()},rows={D1:[row()],D2:[row()]},
              calendar=[cal(),cal(D2,settles=D3)],policy='skip_session')
        self.assertEqual(p['aborted_sessions'][0]['cancelled_pending_entry']['symbol'],'A')
        self.assertEqual(p['aborted_sessions'][0]['observed_at'],'2026-01-02T09:37:00-05:00')
        self.assertEqual(p['daily'][1]['settled_cash_before_signals'],500)
        self.assertIsNone(p['pending_entry'])
        self.assertAlmostEqual(p['final_equity'],497.94)

    def test_guard_keeps_held_gap_incomplete_and_exposure(self):
        m=market();del m['A'][8]
        p=run(data={D1:m,D2:market()},rows={D1:[row()],D2:[row()]},
              calendar=[cal(),cal(D2,settles=D3)],policy='skip_session')
        self.assertEqual(p['incomplete']['reason'],'missing_or_invalid_held_minute')
        self.assertEqual(len(p['daily']),1)
        self.assertEqual(p['aborted_sessions'],[])
        self.assertEqual(p['open_position']['qty'],4)
        self.assertAlmostEqual(p['settled_cash'],86.97)

    def test_guard_does_not_disguise_invalid_settlement_as_data_gap(self):
        p=run(calendar=[cal(settles=D1)],policy='skip_session')
        self.assertEqual(p['status'],'incomplete')
        self.assertEqual(p['aborted_sessions'],[])

    def test_guard_preserves_unsettled_cash_across_skipped_day(self):
        p=run(data={d:market() for d in [D1,D2,D3]},rows={d:[row()] for d in [D1,D2,D3]},
              calendar=[cal(settles=D3),cal(D2,settles=D3),cal(D3,settles='2026-01-07')],
              coverage={D2:{'ranking_complete':False,'ranking_missing':['A']}},policy='skip_session')
        self.assertAlmostEqual(p['daily'][1]['settled_cash'],86.97)
        self.assertAlmostEqual(p['daily'][1]['unsettled_cash'],410.97)
        self.assertAlmostEqual(p['daily'][1]['equity'],497.94)
        self.assertAlmostEqual(p['daily'][2]['settlement_cash_paid'],410.97)
        self.assertEqual([e['entry_date'] for e in p['entries']],[D1,D3])

    def test_guard_preselection_gap_does_not_select_other_winner(self):
        m=market(('A','B'));del m['A'][5]
        p=run(data={D1:m},rows={D1:[row('A'),row('B',3)]},policy='skip_session')
        self.assertEqual(p['final_equity'],500)
        self.assertEqual(p['entries'],[])
        self.assertEqual(p['aborted_sessions'][0]['gap']['symbols'],['A'])
        self.assertEqual(p['aborted_sessions'][0]['observed_at'],'2026-01-02T09:36:00-05:00')

    def test_volume_and_relative_volume_metadata_verified(self):
        r={**row(),'opening_volume':5000,'mean_opening_volume14':2500}
        p=run(rows={D1:[r]})
        self.assertEqual(p['coverage']['opening_volume_verified_candidates'],1)
        self.assertEqual(p['coverage']['opening_volume_unknown_candidates'],0)

    def test_reported_volume_cannot_disagree_with_minute_sum(self):
        r={**row(),'opening_volume':6000,'mean_opening_volume14':3000}
        p=run(rows={D1:[r]})
        self.assertEqual(p['incomplete']['reason'],'opening_volume_bar_mismatch')
        self.assertEqual(p['entries'],[])
        q=run(rows={D1:[r]},policy='skip_session')
        self.assertEqual(q['aborted_sessions'][0]['gap']['minute_sum'],5000)

    def test_relative_volume_rank_cannot_disagree_with_reported_volumes(self):
        r={**row(),'opening_volume':5000,'mean_opening_volume14':1000}
        p=run(rows={D1:[r]})
        self.assertEqual(p['incomplete']['reason'],'relative_volume_feature_mismatch')

    def test_partial_or_invalid_volume_metadata_cannot_be_ignored(self):
        for fields in [{'opening_volume':5000}, {'mean_opening_volume14':2500},
                       {'opening_volume':5000,'mean_opening_volume14':0},
                       {'opening_volume':5000,'mean_opening_volume14':float('nan')}]:
            with self.subTest(fields=fields):
                p=run(rows={D1:[{**row(),**fields}]})
                self.assertEqual(p['incomplete']['reason'],'opening_volume_metadata_invalid')

    def test_synthetic_without_volume_metadata_is_explicitly_unknown(self):
        p=run()
        self.assertEqual(p['coverage']['opening_volume_unknown_candidates'],1)
        self.assertIn('synthetic',p['daily'][0]['candidate_outcomes'][0]['opening_volume_validation'])

    def test_only_registered_gap_policies_permitted(self):
        with self.assertRaisesRegex(ValueError,'unregistered_pre_entry_gap_policy'):
            run(policy='pick_another_stock')

if __name__=='__main__':unittest.main()
