import unittest
import copy
import json
from datetime import date, timedelta
from decimal import Decimal as D
from early_gates import evaluate_candidate
from market_gates import evaluate_candidate as old_gate
from early_engine import simulate_case, simulate_portfolio, simulate_cohort_portfolio
from ep_event_engine import simulate_case as old_case, ALLOWED_CLASSIFICATION, utc_minute
from ep_engine import timestamp

V = {'exit_mode': 'fixed10', 'fraction': 1, 'cost_bps': 25}
PROVIDER = 'provider_event_series_assumption'


def gate_fixture(n=5):
    session = {'date': '2026-01-06', 'open': '09:30', 'close': '16:00'}
    candidate = {'date': session['date'], 'symbol': 'AAA', 'prior_session': '2026-01-05',
                 'prior_close_adjusted': 10, 'daily_open_reference': 11,
                 'prior20_median_dollar_volume': 10000000,
                 'prior20_mean_adjusted_daily_volume': 3000}
    bars = {str(m): {'t': timestamp(session, m), 'o': 11, 'h': 11.1,
                     'l': 10.9, 'c': 11, 'v': 100} for m in range(92)}
    bars[str(n)].update(o=11.1, h=11.3, l=11, c=11.2)
    bars[str(n+2)].update(o=11.2, h=11.3, l=11.1, c=11.2)
    return candidate, bars, session


def account_fixture(n=5):
    dates = []
    day = date(2025, 12, 15)
    while len(dates) < 31:
        if day.weekday() < 5:
            dates.append(day.isoformat())
        day += timedelta(days=1)
    cal = [{'date': d, 'open': '09:30', 'close': '16:00', 'settlement_date': dates[i+1]}
           for i, d in enumerate(dates[:-1])]
    market = {s['date']: {'AAA': {str(m): {'t': timestamp(s, m), 'o': 10, 'h': 10.1,
                                        'l': 9.9, 'c': 10, 'v': 100} for m in range(390)}} for s in cal}
    daily = {'AAA': {s['date']: {'c': 10} for s in cal}}
    candidate = {'candidate_id': dates[10]+'__AAA', 'date': dates[10], 'symbol': 'AAA',
                 'family': 'OR'+str(n), 'opening_range_minutes': n,
                 'market_gate_status': 'signal_and_entry_reference_ready', 'market_gate_pass': True,
                 'opening_range': {'low': 8, 'volume_ratio': 2,
                                   'exact_values': {'volume': '6000', 'prior20_mean_adjusted_daily_volume': '3000'}},
                 'signal': {'minute_offset': n, 'close': 10, 'close_exact': '10', 'planned_entry_offset': n+2},
                 'entry_reference': {'minute_offset': n+2, 'stop_exact': '8'}}
    verified = {'days': {s['date']: {'AAA': {'request_complete': True,
                 'regular_window_covered': True, 'request_ids': ['synthetic-'+s['date']],
                 'bars_returned': 390}} for s in cal}}
    return candidate, market, daily, cal, verified


class GateTests(unittest.TestCase):
    def test_first_entry_clocks_and_complete_volume_threshold(self):
        for n, time in ((5, '09:37'), (15, '09:47'), (30, '10:02')):
            c, m, _ = gate_fixture(n)
            r = evaluate_candidate(c, m, n)
            self.assertTrue(r['market_gate_pass'])
            self.assertEqual(r['signal']['minute_offset'], n)
            self.assertEqual(r['entry_reference']['minute_offset'], n+2)
            self.assertIn('T'+time+':00', r['entry_reference']['time_et'])
            self.assertEqual(r['opening_range']['volume'], n*100)
            self.assertEqual(r['opening_range']['volume_threshold_daily_mean_fraction'], {'numerator': n, 'denominator': 30})
            self.assertEqual(r['opening_range']['exact_values']['or_range_low'], '10.9')

    def test_future_minutes_cannot_rescue_opening_volume(self):
        for n in (5, 15, 30):
            c, m, _ = gate_fixture(n)
            m[str(n-1)]['v'] = 99.999999999
            for i in range(n, 92):
                m[str(i)]['v'] = 1000000000
            r = evaluate_candidate(c, m, n)
            self.assertFalse(r['market_gate_pass'])
            self.assertEqual(r['market_gate_status'], 'opening_range_volume_gate_failed')
            self.assertIsNone(r['signal'])

    def test_range_high_and_low_do_not_use_future30minute_values(self):
        c, m, _ = gate_fixture(5)
        m['20'].update(o=11, h=1000, l=1, c=20, v=1000000000)
        r = evaluate_candidate(c, m, 5)
        self.assertTrue(r['market_gate_pass'])
        self.assertEqual(r['opening_range']['high'], 11.1)
        self.assertEqual(r['entry_reference']['stop_exact'], '10.9')
        self.assertEqual(r['signal']['minute_offset'], 5)

    def test_signal_close_equality_not_breakout(self):
        c, m, _ = gate_fixture(5)
        m['5']['c'] = 11.1
        m['7'].update(o=11, h=11.3, l=10.9, c=11.2)
        r = evaluate_candidate(c, m, 5)
        self.assertEqual(r['signal']['minute_offset'], 7)

    def test_signal_before_range_completion_is_not_eligible(self):
        c, m, _ = gate_fixture(15)
        m['5'].update(o=11, h=11.5, l=10.9, c=11.4)
        r = evaluate_candidate(c, m, 15)
        self.assertFalse(r['market_gate_pass'])
        self.assertEqual(r['market_gate_status'], 'no_breakout_before_11')
        self.assertEqual(r['opening_range']['high'], 11.5)

    def test_missing_earlier_signal_cannot_choose_later_breakout(self):
        c, m, _ = gate_fixture(5)
        m['5']['c'] = 11.1
        del m['6']
        r = evaluate_candidate(c, m, 5)
        self.assertIsNone(r['market_gate_pass'])
        self.assertEqual(r['market_gate_status'], 'signal_data_unknown')
        self.assertEqual(r['issues'][0]['offset'], 6)
        self.assertIsNone(r['signal'])

    def test_missing_after_ready_signal_and_entry_does_not_invalidate(self):
        c, m, _ = gate_fixture(5)
        del m['8']
        r = evaluate_candidate(c, m, 5)
        self.assertTrue(r['market_gate_pass'])
        self.assertEqual(r['coverage']['signal_minutes_observed'], 1)

    def test_signal89_permitted_and90_excluded(self):
        c, m, _ = gate_fixture(5)
        for b in m.values():
            b.update(o=11, h=11.1, l=10.9, c=11)
        m['89'].update(h=12, c=11.2)
        r = evaluate_candidate(c, m, 5)
        self.assertTrue(r['market_gate_pass'])
        self.assertEqual(r['signal']['minute_offset'], 89)
        self.assertEqual(r['entry_reference']['minute_offset'], 91)
        m['89']['c'] = 11
        m['90'].update(h=12, c=11.2)
        self.assertEqual(evaluate_candidate(c, m, 5)['market_gate_status'], 'no_breakout_before_11')

    def test_required_entry_no_later_substitute(self):
        c, m, _ = gate_fixture(5)
        del m['7']
        r = evaluate_candidate(c, m, 5)
        self.assertEqual(r['market_gate_status'], 'entry_reference_unknown')
        self.assertEqual(r['signal']['planned_entry_offset'], 7)
        self.assertIsNone(r['entry_reference'])

    def test_entry_at_stop_unknown_not_retroactive_cancel(self):
        c, m, _ = gate_fixture(5)
        m['7'] = {'t': m['7']['t'], 'o': 10.9}
        r = evaluate_candidate(c, m, 5)
        self.assertEqual(r['market_gate_status'], 'incomplete_gap_through_entry_stop')
        self.assertIsNone(r['market_gate_pass'])
        self.assertTrue(r['entry_reference']['gap_through_stop'])

    def test_prior_price_gap_and_volume_thresholds_inclusive(self):
        c, m, _ = gate_fixture(5)
        c['prior_close_adjusted'] = 10.00000001
        self.assertFalse(evaluate_candidate(c, m, 5)['market_gate_pass'])
        c['prior_close_adjusted'] = 10
        c['prior20_median_dollar_volume'] = 9999999.99
        self.assertEqual(evaluate_candidate(c, m, 5)['market_gate_status'], 'prior_liquidity_gate_failed')
        c['prior20_median_dollar_volume'] = 10000000
        c['prior_close_adjusted'] = 1.99
        self.assertEqual(evaluate_candidate(c, m, 5)['market_gate_status'], 'prior_liquidity_gate_failed')

    def test_missing_malformed_duplicate_and_submicro_timestamps_unknown(self):
        c, original, s = gate_fixture(5)
        variants = []
        m = copy.deepcopy(original); del m['2']; variants.append(m)
        m = copy.deepcopy(original); m['2']['v'] = -1; variants.append(m)
        m = copy.deepcopy(original); m['2']['o'] = True; variants.append(m)
        m = copy.deepcopy(original); m[2] = copy.deepcopy(m['2']); variants.append(m)
        m = copy.deepcopy(original); m['2']['t'] = m['2']['t'].replace('-05:00', '.000000001-05:00'); variants.append(m)
        m = copy.deepcopy(original); m['2']['h'] = 1; variants.append(m)
        for m in variants:
            r = evaluate_candidate(c, m, 5)
            self.assertEqual(r['market_gate_status'], 'opening_range_data_unknown')
            self.assertIsNone(r['market_gate_pass'])

    def test_prior_session_must_be_prior_not_current(self):
        c, m, _ = gate_fixture(5)
        c['prior_session'] = c['date']
        self.assertEqual(evaluate_candidate(c, m, 5)['market_gate_status'], 'candidate_reference_unknown')

    def test_or30_parent_equal_after_explicit_schema_rename(self):
        c, initial, _ = gate_fixture(30)
        variants = [copy.deepcopy(initial) for _ in range(6)]
        del variants[1]['2']
        del variants[2]['32']
        variants[3]['29']['v'] = 0
        variants[4]['30']['c'] = 11.1
        variants[5]['32']['o'] = 10
        for m in variants:
            old = old_gate(c, m)
            old = json.loads(json.dumps(old).replace('opening30', 'opening_range').replace('or30', 'or_range').replace('OR30', 'OR_N'))
            new = evaluate_candidate(c, m, 30)
            del new['family']; del new['opening_range_minutes']
            if new['opening_range']:
                for key in ('minutes', 'volume_threshold_daily_mean_fraction', 'volume_threshold_policy'):
                    del new['opening_range'][key]
            self.assertEqual(new, old)

    def test_unregistered_family_rejected(self):
        c, m, _ = gate_fixture(5)
        for n in (0, 4, 6, 20, True, 5.0, '5'):
            with self.assertRaises(ValueError):
                evaluate_candidate(c, m, n)


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = account_fixture(5)

    def setUp(self):
        self.c, self.m, self.d, self.cal, self.verified = copy.deepcopy(self.base)
        self.day = self.c['date']; self.next = self.cal[11]['date']; self.cut = self.cal[-1]['date']
        self.evidence = {'rows': []}

    def case(self, variant=None, mode=PROVIDER, cutoff=None, actions=None):
        return simulate_case(self.c, variant or V, self.m, self.d, self.cal, actions or {},
                             cutoff or self.cut, mode, self.evidence, self.verified)

    def port(self, candidates=None, coverage=None, cutoff=None):
        return simulate_portfolio(candidates or [self.c], V, self.m, self.d, self.cal, {},
                                  coverage, self.day, cutoff or self.cut,
                                  PROVIDER, self.evidence, self.verified, self.c['opening_range_minutes'])

    def cohort(self, candidates=None, cutoff=None):
        return simulate_cohort_portfolio(candidates or [self.c], V, self.m, self.d, self.cal, {},
                    start_date=self.day, cutoff=cutoff or self.cut, mode=PROVIDER,
                    gap_evidence=self.evidence, verified_symbol_sessions=self.verified,
                    opening_range_minutes=self.c['opening_range_minutes'])

    def test_early_families_allowed_with_exact_entry_time(self):
        for n in (5, 15, 30):
            self.c['opening_range_minutes'] = n; self.c['family'] = 'OR'+str(n)
            self.c['signal']['minute_offset'] = n; self.c['entry_reference']['minute_offset'] = n+2
            r = self.case()
            self.assertEqual(r['status'], 'complete')
            self.assertEqual(r['trades'][0]['time'], timestamp(self.cal[10], n+2))
            self.assertEqual(r['decisions'][0]['signal_time'], timestamp(self.cal[10], n+1))
            self.assertEqual(D(r['ending_equity']), D('497.550'))
            self.assertFalse(r['news_selection_used'])

    def test_outside_signal_window_and_wrong_latency_rejected(self):
        for minute, entry in ((4, 6), (90, 92), (5, 6), (5, 8), (5.1, 7)):
            self.c['signal']['minute_offset'] = minute
            self.c['entry_reference']['minute_offset'] = entry
            r = self.case()
            self.assertEqual(r['failure']['reason'], 'invalid_candidate_intent')
            self.assertEqual(r['trades'], [])

    def test_candidate_family_must_match_range(self):
        self.c['family'] = 'OR15'
        self.assertEqual(self.case()['failure']['reason'], 'invalid_candidate_intent')
        with self.assertRaises(ValueError):
            self.port()

    def test_unregistered_financial_variants_rejected(self):
        for variant in ({**V, 'cost_bps': 50}, {**V, 'exit_mode': 'ma10_max63'}, {**V, 'fraction': '.75'}):
            with self.assertRaises(ValueError):
                self.case(variant)

    def test_or30_financial_fields_equal_v2_all_frozen_variants_modes(self):
        self.c['opening_range_minutes'] = 30; self.c['family'] = 'OR30'
        self.c['signal']['minute_offset'] = 30; self.c['entry_reference']['minute_offset'] = 32
        old_candidate = copy.deepcopy(self.c)
        old_candidate['opening30'] = old_candidate.pop('opening_range')
        for fraction in ('.5', '1'):
            for cost in (25, 100):
                variant = {**V, 'fraction': fraction, 'cost_bps': cost}
                for mode in ('evidence_gated', PROVIDER):
                    old = old_case(old_candidate, variant, self.m, self.d, self.cal, {}, self.cut,
                                   mode, self.evidence, self.verified)
                    new = self.case(variant, mode)
                    for key in ('status', 'trades', 'ledger', 'cash_settled', 'terminal_receivables', 'decisions',
                                'ending_equity', 'profit', 'daily', 'max_daily_liquidation_drawdown', 'milestones'):
                        self.assertEqual(new[key], old[key], (fraction, cost, mode, key))

    def test_price_gap_unaffordable_no_resize(self):
        self.m[self.day]['AAA']['7']['o'] = 10.3
        r = self.case()
        self.assertEqual(r['trades'], [])
        self.assertEqual(r['decisions'][0]['intended_quantity'], 49)
        self.assertEqual(r['decisions'][0]['status'], 'skipped_next_open_unaffordable')
        self.assertEqual(r['ending_equity'], '500')

    def test_entry_missing_not_bypassed_by_modes(self):
        del self.m[self.day]['AAA']['7']
        self.evidence['rows'] = [{'symbol': 'AAA', 'time': utc_minute(timestamp(self.cal[10], 7)),
                                 'point_id': 'synthetic', 'classification': ALLOWED_CLASSIFICATION}]
        for mode in ('evidence_gated', PROVIDER):
            r = self.case(mode=mode)
            self.assertEqual(r['failure']['reason'], 'missing_or_invalid_selected_entry')
            self.assertIsNotNone(r['pending_order'])
            self.assertEqual(r['trades'], [])

    def test_held_modes_preserve_evidence_and_complete_source_distinction(self):
        del self.m[self.day]['AAA']['8']
        self.assertEqual(self.case(mode='evidence_gated')['status'], 'incomplete')
        provider = self.case()
        self.assertEqual(provider['status'], 'complete')
        self.assertEqual(provider['skipped_minute_count'], 1)
        self.verified['days'][self.day]['AAA']['request_complete'] = False
        self.assertEqual(self.case()['status'], 'incomplete')

    def test_early_stop_and_interval_semantics_inherited(self):
        self.m[self.day]['AAA']['7']['l'] = 7
        r = self.case()
        self.assertEqual(r['trades'][-1]['reference_price'], '8')
        self.assertEqual(r['trades'][-1]['time_semantics'], 'source_bar_interval_start_not_known_execution_time')
        self.assertEqual(D(r['ending_equity']), D('399.795'))

    def test_stale_terminal_equity_unknown_and_excluded_from_dd(self):
        del self.m[self.day]['AAA']['389']
        r = self.case(cutoff=self.day)
        self.assertEqual(r['status'], 'censored_open_position')
        self.assertIsNone(r['ending_equity'])
        self.assertIsNone(r['observed_fresh_daily_liquidation_drawdown'])
        self.assertEqual(r['daily_drawdown_excluded_snapshot_count'], 1)

    def test_required_tenth_day_exit_never_deferred(self):
        del self.m[self.cal[19]['date']]['AAA']['389']
        r = self.case()
        self.assertEqual(r['failure']['reason'], 'missing_required_execution_bar')
        self.assertEqual(r['failure']['execution_intents'], ['fixed10'])
        self.assertIsNone(r['profit'])

    def test_corporate_action_unknown_before_held_progress(self):
        self.m[self.next]['AAA'] = {}
        r = self.case(actions={'AAA': [{'type': 'forward_split', 'ex_date': self.next}]})
        self.assertEqual(r['failure']['reason'], 'unsupported_held_corporate_event')
        self.assertEqual(r['position']['quantity'], 49)

    def test_news_false_does_not_gate_technical_strategy_or_get_overwritten(self):
        self.c['pass_registered_news_gate'] = False
        before = copy.deepcopy(self.c)
        r = self.port(cutoff=self.day)
        self.assertEqual(len(r['trades']), 1)
        self.assertEqual(self.c, before)
        self.assertFalse(r['news_selection_used'])

    def test_unknown_technical_candidate_blocks_if_signal_unknown_or_earlier(self):
        unknown = copy.deepcopy(self.c)
        unknown.update(symbol='ZZZ', candidate_id=self.day+'__ZZZ', market_gate_pass=None,
                       market_gate_status='opening_range_data_unknown', signal=None)
        r = self.port([self.c, unknown])
        self.assertEqual(r['failure']['reason'], 'unresolved_candidate_precedence')
        self.assertEqual(r['trades'], [])
        unknown['signal'] = {'minute_offset': 4}
        self.assertEqual(self.port([self.c, unknown])['trades'], [])

    def test_later_unknown_signal_does_not_block_earlier_intent(self):
        unknown = copy.deepcopy(self.c)
        unknown.update(symbol='ZZZ', candidate_id=self.day+'__ZZZ', market_gate_pass=None,
                       market_gate_status='entry_reference_unknown')
        unknown['signal']['minute_offset'] = 8
        r = self.port([unknown, self.c], cutoff=self.day)
        self.assertEqual(r['trades'][0]['symbol'], 'AAA')

    def test_verified_failed_gate_excluded(self):
        failed = copy.deepcopy(self.c)
        failed.update(symbol='ZZZ', candidate_id=self.day+'__ZZZ', market_gate_pass=False,
                      market_gate_status='opening_range_volume_gate_failed', signal=None)
        r = self.port([failed, self.c], cutoff=self.day)
        self.assertEqual(r['trades'][0]['symbol'], 'AAA')
        self.assertEqual(r['decisions'][0]['status'], 'excluded_by_verified_market_gate')

    def test_full_universe_coverage_guard_unchanged(self):
        r = self.port(coverage={'days': {self.day: {'initial_screen_complete': False}}})
        self.assertEqual(r['failure']['reason'], 'initial_screen_coverage_unknown')
        self.assertEqual(r['trades'], [])

    def test_retrospective_omission_disclosed_and_no_false_news_pass(self):
        r = self.cohort(cutoff=self.day)
        self.assertTrue(r['unknown_market_candidates_omitted_by_diagnostic_design'])
        self.assertTrue(r['retrospective_membership_condition'])
        self.assertFalse(r['historical_implementability_claim'])
        self.assertFalse(r['news_selection_used'])

    def test_cohort_rejects_unknown_or_failed_member(self):
        for passed in (False, None):
            self.c['market_gate_pass'] = passed
            with self.assertRaises(ValueError):
                self.cohort()
            with self.assertRaises(ValueError):
                self.case()

    def test_no_exit_day_entry_and_tplus1_unspendable(self):
        second = copy.deepcopy(self.c)
        second['date'] = self.cal[19]['date']; second['candidate_id'] = second['date']+'__AAA'
        r = self.cohort([self.c, second])
        self.assertEqual(len(r['trades']), 2)
        self.cal[10]['settlement_date'] = self.cal[12]['date']
        self.m[self.day]['AAA']['7']['l'] = 7
        second['date'] = self.next; second['candidate_id'] = self.next+'__AAA'
        r = self.cohort([self.c, second], cutoff=self.cal[12]['date'])
        self.assertEqual(len(r['trades']), 2)
        decisions = [x for x in r['decisions'] if x.get('candidate_id') == second['candidate_id']]
        self.assertEqual(decisions[0]['status'], 'skipped_unaffordable_intent')
        self.assertEqual(r['terminal_receivables'], [])

    def test_selected_source_conflict_stays_in_membership_no_trade_or_alternate(self):
        self.c['source_revision_conflict'] = True
        other = copy.deepcopy(self.c)
        other.update(symbol='ZZZ', candidate_id=self.day+'__ZZZ', source_revision_conflict=False)
        self.m[self.day]['ZZZ'] = copy.deepcopy(self.m[self.day]['AAA'])
        r = self.cohort([other, self.c], cutoff=self.day)
        self.assertEqual(r['failure']['reason'], 'unresolved_candidate_precedence')
        self.assertEqual(r['failure']['time'], timestamp(self.cal[10], 0))
        self.assertEqual(r['failure']['source_conflict_blockers'], [self.c['candidate_id']])
        self.assertEqual(r['quarantine_scope'], 'any source-version conflict in planned input window; may include unused future data')
        self.assertTrue(r['source_revision_quarantine_applied'])
        self.assertEqual(r['candidate_count_supplied'], 2)
        self.assertEqual(r['trades'], [])
        self.assertIsNone(r['ending_equity'])
        self.assertEqual(self.case()['failure']['reason'], 'source_revision_conflict')

    def test_later_rank_or_failed_gate_source_conflict_blocks_earlier_known(self):
        other = copy.deepcopy(self.c)
        other.update(symbol='ZZZ', candidate_id=self.day+'__ZZZ', source_revision_conflict=True)
        other['signal']['minute_offset'] = 89
        for passed in (True, False, None):
            other['market_gate_pass'] = passed
            r = self.port([self.c, other], cutoff=self.day)
            self.assertEqual(r['failure']['reason'], 'unresolved_candidate_precedence')
            self.assertEqual(r['failure']['source_conflict_blockers'], [other['candidate_id']])
            self.assertEqual(r['trades'], [])
        other['market_gate_pass'] = True
        self.assertEqual(self.cohort([self.c, other], cutoff=self.day)['trades'], [])

    def test_selection_order_and_no_affordability_alternate(self):
        other = copy.deepcopy(self.c)
        other.update(symbol='ZZZ', candidate_id=self.day+'__ZZZ')
        self.m[self.day]['ZZZ'] = copy.deepcopy(self.m[self.day]['AAA'])
        self.verified['days'][self.day]['ZZZ'] = self.verified['days'][self.day]['AAA']
        other['opening_range']['exact_values']['volume'] = '6001'
        self.assertEqual(self.cohort([self.c, other], cutoff=self.day)['trades'][0]['symbol'], 'ZZZ')
        other['opening_range']['exact_values']['volume'] = '6000'
        self.assertEqual(self.cohort([other, self.c], cutoff=self.day)['trades'][0]['symbol'], 'AAA')
        self.m[self.day]['AAA']['7']['o'] = 10.3
        r = self.cohort([other, self.c], cutoff=self.day)
        self.assertEqual(r['trades'], [])
        self.assertEqual(r['decisions'][-1]['status'], 'skipped_next_open_unaffordable')


if __name__ == '__main__':
    unittest.main()
