import unittest
import copy
from datetime import date, timedelta
from decimal import Decimal as D
from ep_engine import simulate_case as baseline_case, simulate_portfolio as baseline_portfolio
from ep_event_engine import (simulate_case, simulate_portfolio, simulate_cohort_portfolio,
                             timestamp, utc_minute, ALLOWED_CLASSIFICATION)

V = {'exit_mode': 'fixed10', 'fraction': 1, 'cost_bps': 25}
PROVIDER = 'provider_event_series_assumption'


def fixture(n=76):
    days = []
    day = date(2025, 12, 15)
    while len(days) < n + 10:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    calendar = [{'date': d, 'open': '09:30', 'close': '16:00', 'settlement_date': days[i+1]}
                for i, d in enumerate(days[:-1])]
    market = {s['date']: {'AAA': {str(m): {'t': timestamp(s, m), 'o': 10, 'h': 10.1,
                                         'l': 9.9, 'c': 10, 'v': 100} for m in range(390)}}
              for s in calendar}
    daily = {'AAA': {s['date']: {'c': 10} for s in calendar}}
    candidate = {'candidate_id': days[10]+'__AAA', 'date': days[10], 'symbol': 'AAA',
                 'market_gate_status': 'signal_and_entry_reference_ready',
                 'opening30': {'volume_ratio': 2},
                 'signal': {'minute_offset': 30, 'close': 10, 'close_exact': '10'},
                 'entry_reference': {'minute_offset': 32, 'stop_exact': '8'}}
    verified = {'days': {s['date']: {'AAA': {'request_complete': True,
                 'regular_window_covered': True, 'request_ids': ['fixture-'+s['date']],
                 'bars_returned': 390}} for s in calendar}}
    return candidate, market, daily, calendar, verified


class EventTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = fixture()

    def setUp(self):
        self.c, self.m, self.d, self.cal, self.verified = copy.deepcopy(self.base)
        self.day = self.c['date']
        self.next = self.cal[11]['date']
        self.cut = self.cal[-1]['date']
        self.evidence = {'rows': []}

    def allow(self, offset, index=10, classification=ALLOWED_CLASSIFICATION, symbol='AAA'):
        self.evidence['rows'].append({'symbol': symbol, 'time': utc_minute(timestamp(self.cal[index], offset)),
                                     'point_id': 'synthetic-'+str(index)+'-'+str(offset),
                                     'classification': classification})

    def run_case(self, mode='evidence_gated', v=None, actions=None, cutoff=None):
        return simulate_case(self.c, v or V, self.m, self.d, self.cal, actions or {},
                             cutoff or self.cut, mode=mode, gap_evidence=self.evidence,
                             verified_symbol_sessions=self.verified)

    def port(self, candidates=None, v=None, evidence=None, coverage=None, cutoff=None):
        return simulate_portfolio(candidates or [self.c], v or V, self.m, self.d, self.cal, {},
                    evidence or {self.c['candidate_id']: {'pass_registered_news_gate': True}},
                    coverage, start_date=self.day, cutoff=cutoff or self.cut,
                    mode=PROVIDER, gap_evidence=self.evidence, verified_symbol_sessions=self.verified)

    def cohort(self, candidates=None, v=None, cutoff=None):
        return simulate_cohort_portfolio(candidates or [self.c], v or V, self.m, self.d, self.cal,
                    {}, 'market73', start_date=self.day, cutoff=cutoff or self.cut,
                    mode=PROVIDER, gap_evidence=self.evidence, verified_symbol_sessions=self.verified)

    def test_complete_old_cases_regress_exact_financial_fields(self):
        for exit_mode in ('fixed10', 'ma10_max63'):
            for fraction in ('.5', '1'):
                for bps in (25, 50, 100):
                    v = dict(exit_mode=exit_mode, fraction=fraction, cost_bps=bps)
                    old = baseline_case(self.c, v, self.m, self.d, self.cal, {}, self.cut)
                    for mode in ('evidence_gated', PROVIDER):
                        new = self.run_case(mode, v)
                        for key in ('status', 'ending_equity', 'profit', 'cash_settled', 'terminal_receivables',
                                    'max_daily_liquidation_drawdown', 'milestones', 'decisions'):
                            self.assertEqual(new[key], old[key], (mode, v, key))

    def test_unregistered_missing_stays_unknown(self):
        del self.m[self.day]['AAA']['40']
        r = self.run_case()
        self.assertEqual(r['failure']['reason'], 'held_minute_unresolved')
        self.assertEqual(r['skipped_minute_count'], 0)
        self.assertIsNone(r['profit'])

    def test_exact_evidence_allows_only_that_coordinate(self):
        del self.m[self.day]['AAA']['40']
        del self.m[self.day]['AAA']['41']
        self.allow(40)
        r = self.run_case()
        self.assertEqual(r['skipped_minute_count'], 1)
        self.assertEqual(r['failure']['time'], timestamp(self.cal[10], 41))
        self.assertEqual(r['gap_spans'][0]['point_ids'], ['synthetic-10-40'])

    def test_adjacent_supported_gaps_merge_without_prices(self):
        for offset in (40, 41, 43):
            del self.m[self.day]['AAA'][str(offset)]
            self.allow(offset)
        r = self.run_case()
        self.assertEqual(r['status'], 'complete')
        self.assertEqual([x['minute_count'] for x in r['gap_spans']], [2, 1])
        self.assertTrue(all(x['execution_price_created'] is False for x in r['gap_spans']))
        self.assertEqual(r['gap_spans'][0]['end_time_exclusive'], timestamp(self.cal[10], 42))

    def test_wrong_evidence_classification_not_permission(self):
        del self.m[self.day]['AAA']['40']
        self.allow(40, classification='target_has_no_trades_unknown')
        self.assertEqual(self.run_case()['status'], 'incomplete')

    def test_other_symbol_and_date_not_permission(self):
        del self.m[self.day]['AAA']['40']
        self.allow(40, symbol='OTHER')
        self.allow(40, index=11)
        self.assertEqual(self.run_case()['status'], 'incomplete')

    def test_evidence_timestamp_requires_exact_aware_minute(self):
        for value in ('2026-01-01T10:10:00', '2026-01-01T10:10:00.000000001+00:00',
                      '2026-01-01T10:10:01+00:00'):
            with self.assertRaises(ValueError):
                utc_minute(value)

    def test_provider_can_advance_verified_absence(self):
        del self.m[self.day]['AAA']['40']
        r = self.run_case(PROVIDER)
        self.assertEqual(r['status'], 'complete')
        self.assertEqual(r['skipped_minute_count'], 1)
        self.assertEqual(r['gap_spans'][0]['request_ids'], ['fixture-'+self.day])
        self.assertTrue(r['model_is_conditional_aggregate_event_sensitivity'])
        self.assertTrue(r['conditional_on_input_coverage'])
        self.assertTrue(r['daily'][0]['equity_certified'])
        self.assertEqual(r['daily'][0]['conditional_gap_minutes_to_date'], 1)

    def test_provider_requires_both_strict_true_coverage_flags(self):
        del self.m[self.day]['AAA']['40']
        for key in ('request_complete', 'regular_window_covered'):
            for invalid in (False, None, 1, 'true'):
                self.verified['days'][self.day]['AAA'][key] = invalid
                self.assertEqual(self.run_case(PROVIDER)['status'], 'incomplete')
            self.verified['days'][self.day]['AAA'][key] = True

    def test_missing_provider_session_is_unknown(self):
        del self.m[self.day]['AAA']['40']
        del self.verified['days'][self.day]['AAA']
        self.assertEqual(self.run_case(PROVIDER)['status'], 'incomplete')

    def test_malformed_record_never_uses_absence_permission(self):
        for malformed in (None, {}, {'t': timestamp(self.cal[10], 40), 'o': -1},
                          {'t': timestamp(self.cal[10], 40), 'o': 10, 'h': 9, 'l': 8, 'c': 10}):
            self.m[self.day]['AAA']['40'] = malformed
            r = self.run_case(PROVIDER)
            self.assertEqual(r['status'], 'incomplete')
            self.assertEqual(r['failure']['bar_reason'], 'invalid_minute')
            self.assertEqual(r['skipped_minute_count'], 0)

    def test_entry_missing_cannot_use_evidence_or_provider(self):
        del self.m[self.day]['AAA']['32']
        self.allow(32)
        for mode in ('evidence_gated', PROVIDER):
            r = self.run_case(mode)
            self.assertEqual(r['failure']['reason'], 'missing_or_invalid_selected_entry')
            self.assertEqual(r['trades'], [])
            self.assertIsNotNone(r['pending_order'])

    def test_fixed10_exit_exact_missing_does_not_defer(self):
        del self.m[self.cal[19]['date']]['AAA']['389']
        self.allow(389, index=19)
        for mode in ('evidence_gated', PROVIDER):
            r = self.run_case(mode)
            self.assertEqual(r['failure']['reason'], 'missing_required_execution_bar')
            self.assertEqual(r['failure']['execution_intents'], ['fixed10'])
            self.assertEqual(len(r['trades']), 1)

    def test_max63_exit_exact_missing_does_not_defer(self):
        del self.m[self.cal[72]['date']]['AAA']['389']
        r = self.run_case(PROVIDER, {**V, 'exit_mode': 'ma10_max63'})
        self.assertEqual(r['failure']['execution_intents'], ['max63'])
        self.assertEqual(len(r['trades']), 1)

    def test_ma_next_open_missing_no_later_favorable_price(self):
        self.d['AAA'][self.day]['c'] = 9
        del self.m[self.next]['AAA']['0']
        self.m[self.next]['AAA']['1']['o'] = 100
        self.allow(0, index=11)
        for mode in ('evidence_gated', PROVIDER):
            r = self.run_case(mode, {**V, 'exit_mode': 'ma10_max63'})
            self.assertEqual(r['failure']['execution_intents'], ['ma10_next_open'])
            self.assertEqual(r['failure']['time'], timestamp(self.cal[11], 0))
            self.assertEqual(len(r['trades']), 1)

    def test_available_post_gap_open_below_stop_uses_lower_price(self):
        del self.m[self.day]['AAA']['40']
        self.m[self.day]['AAA']['41'] = {'t': timestamp(self.cal[10], 41), 'o': 7}
        self.allow(40)
        r = self.run_case()
        self.assertEqual(r['trades'][-1]['reference_price'], '7')
        self.assertEqual(r['trades'][-1]['reasons'], ['gap_stop'])
        self.assertEqual(r['trades'][-1]['time'], timestamp(self.cal[10], 41))

    def test_protective_stop_is_interval_not_claimed_actual_time(self):
        self.m[self.day]['AAA']['40']['l'] = 7
        r = self.run_case()
        self.assertEqual(r['trades'][-1]['reference_price'], '8')
        self.assertEqual(r['trades'][-1]['time_semantics'], 'source_bar_interval_start_not_known_execution_time')
        self.assertEqual(r['trades'][-1]['source_bar_interval_end'], timestamp(self.cal[10], 41))
        self.assertEqual(r['ledger'][-1]['time_semantics'], r['trades'][-1]['time_semantics'])

    def test_stale_close_mark_not_fresh_or_in_dd_or_milestones(self):
        self.m[self.day]['AAA']['388'].update(o=30, h=31, l=29, c=30)
        del self.m[self.day]['AAA']['389']
        r = self.run_case(PROVIDER, cutoff=self.day)
        self.assertEqual(r['status'], 'censored_open_position')
        self.assertIsNone(r['ending_equity'])
        self.assertIsNone(r['profit'])
        self.assertFalse(r['terminal_equity_certified'])
        self.assertEqual(r['last_known_mark_time'], timestamp(self.cal[10], 389))
        self.assertFalse(r['daily'][0]['equity_certified'])
        self.assertEqual(r['daily'][0]['mark_stale_minutes'], 1)
        self.assertEqual(r['daily_drawdown_evaluated_snapshot_count'], 0)
        self.assertEqual(r['daily_drawdown_excluded_snapshot_count'], 1)
        self.assertIsNone(r['observed_fresh_daily_liquidation_drawdown'])
        self.assertTrue(all(x is None for x in r['milestones'].values()))
        self.assertGreater(D(r['last_observed_equity']), 1000)

    def test_later_fresh_mark_does_not_remove_gap_condition(self):
        del self.m[self.day]['AAA']['389']
        r = self.run_case(PROVIDER, cutoff=self.next)
        self.assertEqual(r['status'], 'censored_open_position')
        self.assertTrue(r['terminal_equity_certified'])
        self.assertTrue(r['model_is_conditional_aggregate_event_sensitivity'])
        self.assertEqual(r['skipped_minute_count'], 1)
        self.assertEqual(r['daily_drawdown_evaluated_snapshot_count'], 1)
        self.assertEqual(r['daily_drawdown_excluded_snapshot_count'], 1)

    def test_empty_day_fails_at_close_without_invented_price(self):
        self.m[self.next]['AAA'] = {}
        for mode in ('evidence_gated', PROVIDER):
            r = self.run_case(mode)
            self.assertEqual(r['failure']['reason'], 'empty_held_session_unresolved')
            self.assertEqual(r['failure']['time'], timestamp(self.cal[11], 390))
            self.assertEqual(len(r['trades']), 1)
            self.assertIsNone(r['ending_equity'])

    def test_empty_day_ma_execution_deadline_precedes_close(self):
        self.m[self.next]['AAA'] = {}
        self.d['AAA'][self.day]['c'] = 9
        r = self.run_case(PROVIDER, {**V, 'exit_mode': 'ma10_max63'})
        self.assertEqual(r['failure']['reason'], 'missing_required_execution_bar')
        self.assertEqual(r['failure']['time'], timestamp(self.cal[11], 0))

    def test_empty_scheduled_exit_session_fails_exact_exit_minute(self):
        self.m[self.cal[19]['date']]['AAA'] = {}
        r = self.run_case(PROVIDER)
        self.assertEqual(r['failure']['time'], timestamp(self.cal[19], 389))
        self.assertEqual(r['failure']['execution_intents'], ['fixed10'])

    def test_held_corporate_action_precedes_empty_or_gap(self):
        self.m[self.next]['AAA'] = {}
        for action_type in ('cash_dividend', 'forward_split'):
            actions = {'AAA': [{'type': action_type, 'ex_date': self.next}]}
            r = self.run_case(PROVIDER, actions=actions)
            self.assertEqual(r['failure']['reason'], 'unsupported_held_corporate_event')
            self.assertEqual(r['failure']['time'], timestamp(self.cal[11], 0))
            self.assertEqual(r['position']['quantity'], 49)

    def test_early_close_uses_correct_final_minute(self):
        self.cal[19]['close'] = '13:00'
        del self.m[self.cal[19]['date']]['AAA']['209']
        r = self.run_case(PROVIDER)
        self.assertEqual(r['failure']['time'], timestamp(self.cal[19], 209))
        self.assertEqual(r['failure']['execution_intents'], ['fixed10'])

    def test_original_unknown_news_guard_not_relaxed(self):
        r = self.port(evidence={self.c['candidate_id']: {'pass_registered_news_gate': False}})
        self.assertEqual(r['failure']['reason'], 'unresolved_candidate_precedence')
        self.assertEqual(r['trades'], [])

    def test_original_universe_guard_not_relaxed(self):
        r = self.port(coverage={'days': {self.day: {'initial_screen_complete': False}}})
        self.assertEqual(r['failure']['reason'], 'initial_screen_coverage_unknown')
        self.assertIsNone(r['observed_fresh_daily_liquidation_drawdown'])

    def test_retrospective_cohort_labeled_and_does_not_mutate(self):
        before = copy.deepcopy(self.c)
        r = self.cohort()
        self.assertEqual(self.c, before)
        self.assertTrue(r['retrospective_membership_condition'])
        self.assertFalse(r['historical_implementability_claim'])
        self.assertTrue(r['original_news_evidence_not_overridden'])
        self.assertEqual(r['status'], 'complete')

    def test_retrospective_orders_by_intent_then_relative_volume_then_symbol(self):
        other = copy.deepcopy(self.c)
        other.update(symbol='ZZZ', candidate_id=self.day+'__ZZZ')
        other['opening30']['volume_ratio'] = 3
        self.m[self.day]['ZZZ'] = copy.deepcopy(self.m[self.day]['AAA'])
        r = self.cohort([self.c, other], cutoff=self.day)
        self.assertEqual(r['trades'][0]['symbol'], 'ZZZ')
        other['opening30']['volume_ratio'] = 2
        self.assertEqual(self.cohort([other, self.c], cutoff=self.day)['trades'][0]['symbol'], 'AAA')
        other['signal']['minute_offset'] = 31
        other['entry_reference']['minute_offset'] = 33
        other['opening30']['volume_ratio'] = 100
        self.assertEqual(self.cohort([other, self.c], cutoff=self.day)['trades'][0]['symbol'], 'AAA')

    def test_unaffordable_cohort_first_does_not_try_alternate(self):
        self.m[self.day]['AAA']['32']['o'] = 10.3
        other = copy.deepcopy(self.c)
        other.update(symbol='ZZZ', candidate_id=self.day+'__ZZZ')
        other['signal']['minute_offset'] = 31
        other['entry_reference']['minute_offset'] = 33
        self.m[self.day]['ZZZ'] = copy.deepcopy(self.m[self.day]['AAA'])
        r = self.cohort([other, self.c], cutoff=self.day)
        self.assertEqual(r['trades'], [])
        self.assertEqual(r['decisions'][-1]['status'], 'skipped_next_open_unaffordable')
        self.assertEqual(r['ending_equity'], '500')

    def test_cohort_no_exit_day_reentry(self):
        second = copy.deepcopy(self.c)
        second['date'] = self.cal[19]['date']
        second['candidate_id'] = second['date']+'__AAA'
        r = self.cohort([self.c, second])
        self.assertEqual(len(r['trades']), 2)
        self.assertIn(second['candidate_id'], [row for row in r['decisions'] if row['date'] == second['date']][-1]['candidate_ids'])

    def test_cohort_receivable_not_spendable_before_settlement(self):
        self.cal[10]['settlement_date'] = self.cal[12]['date']
        self.m[self.day]['AAA']['32']['l'] = 7
        second = copy.deepcopy(self.c)
        second['date'] = self.next
        second['candidate_id'] = self.next+'__AAA'
        r = self.cohort([self.c, second], cutoff=self.cal[12]['date'])
        self.assertEqual(len(r['trades']), 2)
        self.assertEqual(r['terminal_receivables'], [])
        second_intent = [row for row in r['decisions'] if row.get('candidate_id') == second['candidate_id']]
        self.assertEqual(second_intent[0]['status'], 'skipped_unaffordable_intent')
        for day in r['daily']:
            self.assertEqual(D(day['last_observed_equity']), sum(D(day[key]) for key in ('cash_settled', 'cash_unsettled', 'marked_position')))

    def test_unregistered_mode_and_cohort_rejected(self):
        with self.assertRaises(ValueError):
            self.run_case('optimistic')
        with self.assertRaises(ValueError):
            simulate_cohort_portfolio([self.c], V, self.m, self.d, self.cal, {}, 'winners')


if __name__ == '__main__':
    unittest.main()
