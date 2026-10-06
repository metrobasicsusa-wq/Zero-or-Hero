"""Independent synthetic causal tests; no local actual data or network use."""
import copy
import unittest
from datetime import datetime, timedelta, timezone

from early_gates import evaluate_candidate
from market_gates import evaluate_candidate as parent_evaluate


START = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)


def candidate():
    return {'date': '2026-01-02', 'symbol': 'TEST', 'prior_session': '2025-12-31',
            'prior_close_adjusted': 10, 'daily_open_reference': 11,
            'prior20_median_dollar_volume': 10000000,
            'prior20_mean_adjusted_daily_volume': 3000}


def series(signal=5):
    rows = {}
    for m in range(93):
        rows[str(m)] = {'t': (START+timedelta(minutes=m)).isoformat(),
                        'o': 11, 'h': 11.2, 'l': 10.8, 'c': 11.1, 'v': 100}
    if signal is not None:
        rows[str(signal)].update(h=11.4, c=11.3)
    return rows


class IndependentGateTests(unittest.TestCase):
    def test_all_three_exact_volume_and_gap_thresholds(self):
        for n in [5, 15, 30]:
            with self.subTest(n=n):
                out = evaluate_candidate(candidate(), series(n), n)
                self.assertTrue(out['market_gate_pass'])
                self.assertEqual(out['opening_range']['volume'], n*100)
                self.assertEqual(out['signal']['minute_offset'], n)
                self.assertEqual(out['entry_reference']['minute_offset'], n+2)

    def test_any_fractional_volume_below_threshold_fails(self):
        for n in [5, 15, 30]:
            bars = series(n); bars['0']['v'] = 99.999
            self.assertIs(evaluate_candidate(candidate(), bars, n)['market_gate_pass'], False)

    def test_actual_regular_gap_not_daily_reference(self):
        c = candidate(); c['daily_open_reference'] = 1000
        bars = series(); bars['0']['o'] = 10.99
        self.assertEqual(evaluate_candidate(c, bars, 5)['market_gate_status'], 'opening_gap_gate_failed')

    def test_no_future_or30_high_or_low_or_volume(self):
        original = evaluate_candidate(candidate(), series(), 5)
        bars = series()
        for m in range(8, 30):
            bars[str(m)].update(o=500, h=1000, l=0.01, c=900, v=9999999)
        changed = evaluate_candidate(candidate(), bars, 5)
        self.assertEqual(original, changed)
        self.assertEqual(changed['entry_reference']['stop_exact'], '10.8')

    def test_signal_window_starts_after_range(self):
        bars = series(None); bars['4'].update(h=50, c=50)
        out = evaluate_candidate(candidate(), bars, 5)
        self.assertEqual(out['market_gate_status'], 'no_breakout_before_11')

    def test_close_equal_range_high_is_not_breakout(self):
        bars = series(None); bars['5']['c'] = 11.2
        self.assertEqual(evaluate_candidate(candidate(), bars, 5)['market_gate_status'], 'no_breakout_before_11')

    def test_latency_one_full_minute_after_completion(self):
        out = evaluate_candidate(candidate(), series(), 5)
        self.assertEqual(out['signal']['completed_at_utc'], '2026-01-02T14:36:00+00:00')
        self.assertEqual(out['entry_reference']['time_utc'], '2026-01-02T14:37:00+00:00')

    def test_missing_range_is_unknown_not_zero(self):
        bars = series(); del bars['3']
        out = evaluate_candidate(candidate(), bars, 5)
        self.assertEqual(out['market_gate_status'], 'opening_range_data_unknown')
        self.assertIsNone(out['market_gate_pass'])

    def test_missing_preceding_signal_minute_blocks_later_breakout(self):
        bars = series(7); del bars['5']
        out = evaluate_candidate(candidate(), bars, 5)
        self.assertEqual(out['market_gate_status'], 'signal_data_unknown')
        self.assertIsNone(out['signal'])

    def test_future_missing_does_not_retract_signal(self):
        out = evaluate_candidate(candidate(), series(), 5)
        bars = series(); del bars['20']; del bars['60']
        self.assertEqual(out, evaluate_candidate(candidate(), bars, 5))

    def test_entry_price_requires_exact_minute_no_substitution(self):
        bars = series(); del bars['7']
        out = evaluate_candidate(candidate(), bars, 5)
        self.assertEqual(out['market_gate_status'], 'entry_reference_unknown')
        self.assertEqual(out['signal']['minute_offset'], 5)

    def test_entry_reference_does_not_look_at_future_entry_minute_hlc(self):
        bars = series(); bars['7'] = {'t': bars['7']['t'], 'o': 11}
        self.assertTrue(evaluate_candidate(candidate(), bars, 5)['market_gate_pass'])

    def test_gap_through_known_stop_is_unresolved(self):
        bars = series(); bars['7']['o'] = 10.8
        out = evaluate_candidate(candidate(), bars, 5)
        self.assertEqual(out['market_gate_status'], 'incomplete_gap_through_entry_stop')
        self.assertIsNone(out['market_gate_pass'])

    def test_last_allowed_signal_and_first_disallowed_signal(self):
        out = evaluate_candidate(candidate(), series(89), 5)
        self.assertEqual(out['entry_reference']['minute_offset'], 91)
        self.assertEqual(evaluate_candidate(candidate(), series(90), 5)['market_gate_status'], 'no_breakout_before_11')

    def test_submicrosecond_future_timestamp_not_truncated(self):
        bars = series(); bars['5']['t'] = '2026-01-02T14:35:00.000000001Z'
        self.assertEqual(evaluate_candidate(candidate(), bars, 5)['market_gate_status'], 'signal_data_unknown')

    def test_same_offset_int_and_string_is_ambiguous(self):
        bars = series(); bars[0] = copy.deepcopy(bars['0'])
        self.assertEqual(evaluate_candidate(candidate(), bars, 5)['market_gate_status'], 'opening_range_data_unknown')

    def test_news_status_does_not_change_technical_gate(self):
        a = candidate(); b = candidate(); a['news_status'] = 'verified'; b['news_status'] = 'missing'
        b['news_evidence'] = [{'pass_registered_news_gate': False}]
        self.assertEqual(evaluate_candidate(a, series(), 5), evaluate_candidate(b, series(), 5))

    def test_or30_synthetic_parent_reference_equivalence(self):
        for m in [None, 30, 57, 89]:
            with self.subTest(signal=m):
                old = parent_evaluate(candidate(), series(m)); new = evaluate_candidate(candidate(), series(m), 30)
                self.assertEqual(old['market_gate_pass'], new['market_gate_pass'])
                self.assertEqual(old['market_gate_status'], new['market_gate_status'])
                if old['signal']:
                    for key in ['minute_offset','close_exact','completed_at_utc','planned_entry_time_utc']:
                        self.assertEqual(old['signal'][key], new['signal'][key])
                    for key in ['minute_offset','open_exact','stop_exact','gap_through_stop']:
                        self.assertEqual(old['entry_reference'][key], new['entry_reference'][key])

    def test_unregistered_or_boolean_range_rejected(self):
        for n in [True, 1, 10, 60]:
            with self.assertRaises(ValueError):
                evaluate_candidate(candidate(), series(), n)

    def test_prior_session_cannot_equal_current(self):
        c = candidate(); c['prior_session'] = c['date']
        self.assertEqual(evaluate_candidate(c, series(), 5)['market_gate_status'], 'candidate_reference_unknown')


if __name__ == '__main__':
    unittest.main(verbosity=2)
