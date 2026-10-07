"""Independent synthetic concentration semantics; no historical data read.

Only the public production entry point is imported. Expected values use a
separate exact-rational oracle with explicit original row weights, and freshly
selected remaining rows for each leave-out. Peer metadata is synthetic.
"""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
import hashlib
import json
import random
import unittest

from concentration import summarize_concentration

ROOT = Path(__file__).resolve().parent
METRICS = ('net_return', 'matched_excess')
DAYS = ('2026-01-02', '2026-01-05', '2026-01-06', '2026-01-07')


def row(cid, day, symbol, net, excess):
    return dict(case_id=cid, date=day, symbol=symbol,
                net_return=net, matched_excess=excess)


def q(value):
    return None if value is None else Fraction(Decimal(value))


def exact_stats(records, metric):
    present = [(item['date'], q(item[metric])) for item in records if item[metric] is not None]
    days = sorted(set(day for day, value in present))
    counts = {day: sum(day == item_day for item_day, value in present) for day in days}
    means = {day: sum((value for item_day, value in present if day == item_day), Fraction(0)) / counts[day] for day in days}
    total = sum((value for day, value in present), Fraction(0))
    return {
        'complete_count': len(present), 'missing_count': len(records) - len(present),
        'positive_count': sum(value > 0 for day, value in present),
        'negative_count': sum(value < 0 for day, value in present),
        'zero_count': sum(value == 0 for day, value in present),
        'event_sum': total, 'event_mean': total / len(present) if present else None,
        'observed_date_count': len(days), 'observed_dates': days,
        'date_means': means, 'date_counts': counts,
        'date_equal_mean': sum(means.values(), Fraction(0)) / len(days) if days else None,
    }


def entity(items, value):
    return next(item for item in items if item['entity'] == value)


def dropped(items, value):
    return next(item for item in items if item['removed_entity'] == value)


class IndependentConcentrationTests(unittest.TestCase):
    def number(self, actual, expected):
        if expected is None:
            self.assertIsNone(actual)
        else:
            self.assertIsInstance(actual, str)
            value = Decimal(actual)
            self.assertTrue(value.is_finite())
            self.assertLessEqual(abs(Fraction(value) - expected), Fraction(1, 10**45) * max(1, abs(expected)))

    def core(self, actual, expected):
        for name, value in expected.items():
            if isinstance(value, Fraction) or value is None:
                self.number(actual[name], value)
            elif name == 'date_means':
                self.assertEqual(set(actual[name]), set(value))
                for day in value:
                    self.number(actual[name][day], value[day])
            else:
                self.assertEqual(actual[name], value)

    def oracle(self, records):
        result = summarize_concentration(records)
        ordered = sorted(records, key=lambda item: (item['date'], item['symbol'], item['case_id']))
        overall = {metric: exact_stats(records, metric) for metric in METRICS}
        self.assertEqual(result['signal_count'], len(records))
        self.assertEqual(result['signal_date_count'], len(set(item['date'] for item in records)))
        self.assertEqual(result['symbol_count'], len(set(item['symbol'] for item in records)))
        for metric in METRICS:
            self.core(result['overall'][metric], overall[metric])
        axes = [('date', 'by_date'), ('symbol', 'by_symbol'), ('date_symbol', 'date_symbol_matrix_observed_cells')]
        for axis, field in axes:
            selector = (lambda item: (item['date'], item['symbol'])) if axis == 'date_symbol' else (lambda item: item[axis])
            values = sorted(set(selector(item) for item in records))
            self.assertEqual([item['entity'] for item in result[field]], [list(value) if isinstance(value, tuple) else value for value in values])
            contribution = {metric: {'pooled': {}, 'date_equal': {}} for metric in METRICS}
            for value in values:
                chosen = [item for item in ordered if selector(item) == value]
                actual = entity(result[field], list(value) if isinstance(value, tuple) else value)
                self.assertEqual(actual['case_ids'], [item['case_id'] for item in chosen])
                self.assertEqual(actual['signals'], len(chosen))
                self.assertEqual(actual['signal_dates'], sorted(set(item['date'] for item in chosen)))
                for metric in METRICS:
                    own = exact_stats(chosen, metric)
                    base = overall[metric]
                    self.core(actual['metrics'][metric], own)
                    n, d = base['complete_count'], base['observed_date_count']
                    pooled = own['event_sum'] / n if n else None
                    weighted_rows = [q(item[metric]) * Fraction(1, base['date_counts'][item['date']] * d)
                                     for item in chosen if item[metric] is not None]
                    date_equal = sum(weighted_rows, Fraction(0)) if d else None
                    self.number(actual['metrics'][metric]['contribution_to_original_pooled_mean'], pooled)
                    self.number(actual['metrics'][metric]['contribution_to_original_date_equal_mean'], date_equal)
                    self.assertEqual(actual['metrics'][metric]['original_complete_denominator'], n)
                    self.assertEqual(actual['metrics'][metric]['original_observed_date_denominator'], d)
                    contribution[metric]['pooled'][value] = pooled
                    contribution[metric]['date_equal'][value] = date_equal
            for metric in METRICS:
                for weight, mean_field in [('pooled', 'event_mean'), ('date_equal', 'date_equal_mean')]:
                    expected = overall[metric][mean_field]
                    check = result['contribution_identity_checks'][axis][metric][weight]
                    self.number(check['sum_of_entity_contributions'], expected)
                    self.number(check['original_mean'], expected)
                    self.assertEqual(check['exact_fraction_identity_holds'], True if expected is not None else None)
                    self.assertEqual(check['status'], 'verified_exact' if expected is not None else 'undefined_no_complete_outcomes')
                    if expected is not None:
                        self.assertEqual(sum(contribution[metric][weight].values(), Fraction(0)), expected)
            if axis == 'date_symbol':
                continue
            frequency = Counter(selector(item) for item in records)
            self.assertEqual(result['rankings'][axis]['signal_frequency_all_entities'], sorted(frequency, key=lambda value: (-frequency[value], value)))
            for metric in METRICS:
                observed = [value for value in values if any(selector(item) == value and item[metric] is not None for item in records)]
                for weight in ('pooled', 'date_equal'):
                    scores = contribution[metric][weight]
                    rank = result['rankings'][axis]['metrics'][metric][weight]
                    self.assertEqual(rank['positive_contributors_descending'], sorted([value for value in observed if scores[value] > 0], key=lambda value: (-scores[value], value)))
                    self.assertEqual(rank['negative_contributors_ascending'], sorted([value for value in observed if scores[value] < 0], key=lambda value: (scores[value], value)))
                    self.assertEqual(rank['zero_contributors'], [value for value in observed if scores[value] == 0])
                    self.assertEqual(rank['no_complete_outcome_entities'], [value for value in values if value not in observed])
            leaveouts = result['leave_one_' + axis + '_out']
            self.assertEqual([item['removed_entity'] for item in leaveouts], values)
            for value in values:
                removed = [item for item in records if selector(item) == value]
                remaining = [item for item in records if selector(item) != value]
                actual = dropped(leaveouts, value)
                self.assertEqual(actual['removed_signal_count'], len(removed))
                self.assertEqual(actual['remaining_signal_count'], len(remaining))
                original_days = set(item['date'] for item in records)
                remaining_days = set(item['date'] for item in remaining)
                self.assertEqual(actual['remaining_signal_dates'], sorted(remaining_days))
                self.assertEqual(actual['lost_signal_dates'], sorted(original_days - remaining_days))
                for metric in METRICS:
                    remaining_stats = exact_stats(remaining, metric)
                    removed_stats = exact_stats(removed, metric)
                    actual_metric = actual['metrics'][metric]
                    self.core(actual_metric, remaining_stats)
                    self.assertEqual(actual_metric['removed_complete_count'], removed_stats['complete_count'])
                    self.assertEqual(actual_metric['removed_missing_count'], removed_stats['missing_count'])
                    self.number(actual_metric['removed_event_sum'], removed_stats['event_sum'])
                    self.assertEqual(actual_metric['lost_observed_dates'], sorted(set(overall[metric]['observed_dates']) - set(remaining_stats['observed_dates'])))
        return result

    def test_hand_worked_unequal_date_weights_and_independent_metric_coverage(self):
        result = self.oracle([row('a', DAYS[0], 'A', '0.2', None), row('b', DAYS[0], 'B', '-0.4', '0.6'),
                              row('c', DAYS[1], 'A', '0.8', None), row('d', DAYS[2], 'C', None, '-0.2')])
        self.number(result['overall']['net_return']['event_mean'], Fraction(1, 5))
        self.number(result['overall']['net_return']['date_equal_mean'], Fraction(7, 20))
        self.number(result['overall']['matched_excess']['date_equal_mean'], Fraction(1, 5))
        self.assertEqual(result['overall']['net_return']['observed_dates'], list(DAYS[:2]))
        self.assertEqual(result['overall']['matched_excess']['observed_dates'], [DAYS[0], DAYS[2]])

    def test_pooled_and_date_equal_entity_rankings_can_reverse(self):
        rows = [row('a', DAYS[0], 'A', '.8', None), row('b', DAYS[1], 'B', '.4', None)]
        rows += [row('z' + str(i), DAYS[0], 'Z' + str(i), '0', None) for i in range(3)]
        result = self.oracle(rows)['rankings']['symbol']['metrics']['net_return']
        self.assertEqual(result['pooled']['positive_contributors_descending'], ['A', 'B'])
        self.assertEqual(result['date_equal']['positive_contributors_descending'], ['B', 'A'])

    def test_one_symbols_pooled_and_date_equal_contributions_can_change_sign(self):
        rows = [row('a1', DAYS[0], 'A', '4', None), row('a2', DAYS[1], 'A', '-2', None)]
        rows += [row('z' + str(i), DAYS[0], 'Z', '0', None) for i in range(3)]
        result = self.oracle(rows)
        a = entity(result['by_symbol'], 'A')['metrics']['net_return']
        self.assertGreater(q(a['contribution_to_original_pooled_mean']), 0)
        self.assertLess(q(a['contribution_to_original_date_equal_mean']), 0)

    def test_leave_target_symbol_keeps_other_targets_original_peer_excess(self):
        rows = [row('a', DAYS[0], 'A', '.1', '.123'), row('b', DAYS[0], 'B', '-.2', '-.5')]
        rows[0]['original_peer_symbols'] = ['B', 'P', 'Q']
        rows[0]['original_peer_returns'] = ['9', '8', '7']
        result = self.oracle(rows)
        removed_b = dropped(result['leave_one_symbol_out'], 'B')
        self.number(removed_b['metrics']['matched_excess']['event_mean'], Fraction(123, 1000))
        self.assertEqual(removed_b['remaining_signal_count'], 1)

    def test_signal_date_can_survive_while_one_metrics_observed_date_disappears(self):
        result = self.oracle([row('a', DAYS[0], 'A', '.1', None), row('b', DAYS[0], 'B', None, '.2'),
                              row('c', DAYS[1], 'C', '-.1', '-.3')])
        leave = dropped(result['leave_one_symbol_out'], 'A')
        self.assertEqual(leave['lost_signal_dates'], [])
        self.assertEqual(leave['metrics']['net_return']['lost_observed_dates'], [DAYS[0]])
        self.assertEqual(leave['metrics']['matched_excess']['lost_observed_dates'], [])

    def test_leaveout_reweights_surviving_dates_not_just_original_contributions(self):
        result = self.oracle([row('a', DAYS[0], 'A', '1', '1'), row('b', DAYS[0], 'B', '-1', '-1'),
                              row('c', DAYS[1], 'B', '3', '3')])
        original = q(result['overall']['net_return']['date_equal_mean'])
        contribution = q(entity(result['by_symbol'], 'A')['metrics']['net_return']['contribution_to_original_date_equal_mean'])
        remainder = q(dropped(result['leave_one_symbol_out'], 'A')['metrics']['net_return']['date_equal_mean'])
        self.assertEqual(remainder, 1)
        self.assertNotEqual(remainder, original - contribution)

    def test_duplicate_date_symbol_cell_keeps_all_distinct_case_ids(self):
        result = self.oracle([row('z', DAYS[0], 'A', '.3', None), row('a', DAYS[0], 'A', '-.1', '.2')])
        cell = result['date_symbol_matrix_observed_cells'][0]
        self.assertEqual(cell['signals'], 2)
        self.assertEqual(cell['case_ids'], ['a', 'z'])
        self.number(cell['metrics']['net_return']['event_mean'], Fraction(1, 10))

    def test_low_win_positive_mean_and_high_win_negative_mean_are_both_retained(self):
        for values, expected in [(['.9', '-.1', '-.1', '-.1', '-.1'], Fraction(1, 10)),
                                 (['.1', '.1', '.1', '.1', '-1'], Fraction(-3, 25))]:
            result = self.oracle([row(str(i), DAYS[0], str(i), value, None) for i, value in enumerate(values)])
            self.assertEqual(result['signal_count'], 5)
            self.assertEqual(len(result['leave_one_symbol_out']), 5)
            self.number(result['overall']['net_return']['event_mean'], expected)
            self.assertTrue(result['method']['no_win_rate_gate'])

    def test_positive_relative_peer_return_is_independent_of_target_loss(self):
        result = self.oracle([row('a', DAYS[0], 'A', '-.4', '.3')])
        self.assertEqual(result['overall']['net_return']['negative_count'], 1)
        self.assertEqual(result['overall']['matched_excess']['positive_count'], 1)

    def test_entirely_missing_metric_has_null_denominators_but_keeps_signal_entities(self):
        result = self.oracle([row('a', DAYS[0], 'A', None, '.1'), row('b', DAYS[1], 'B', None, None)])
        self.assertEqual(result['overall']['net_return']['observed_date_count'], 0)
        self.assertIsNone(result['overall']['net_return']['date_equal_mean'])
        self.assertEqual(result['rankings']['symbol']['metrics']['net_return']['pooled']['no_complete_outcome_entities'], ['A', 'B'])

    def test_signed_zero_and_cancellation_are_observed_not_missing(self):
        result = self.oracle([row('a', DAYS[0], 'A', '-0.0000', '0'), row('b', DAYS[1], 'B', '.7', None),
                              row('c', DAYS[1], 'B', '-.7', None), row('d', DAYS[2], 'C', None, None)])
        self.assertEqual(result['overall']['net_return']['complete_count'], 3)
        self.assertEqual(result['overall']['net_return']['zero_count'], 1)
        self.assertEqual(result['overall']['net_return']['observed_date_count'], 2)
        self.assertEqual(result['rankings']['symbol']['metrics']['net_return']['pooled']['zero_contributors'], ['A', 'B'])

    def test_empty_and_single_all_missing_sample_are_distinct(self):
        empty = self.oracle([])
        missing = self.oracle([row('a', DAYS[0], 'A', None, None)])
        self.assertEqual(empty['signal_count'], 0)
        self.assertEqual(missing['signal_count'], 1)
        self.assertEqual(len(missing['leave_one_date_out']), 1)
        self.assertIsNone(missing['leave_one_date_out'][0]['metrics']['net_return']['event_mean'])

    def test_generator_order_invariance_and_no_input_or_peer_mutation(self):
        rows = [row('c', DAYS[1], 'B', Decimal('.2'), -1), row('a', DAYS[0], 'A', 1, None)]
        rows[0]['original_peers'] = {'symbols': ['A', 'C', 'D'], 'values': ['1', '2', '3']}
        before = deepcopy(rows)
        result = self.oracle(rows)
        self.assertEqual(rows, before)
        self.assertEqual(result, summarize_concentration(item for item in reversed(rows)))

    def test_negative_ranking_ties_use_identifiers_and_precise_values(self):
        tiny_smaller = '-1.' + '0' * 60 + '1'
        result = self.oracle([row('a', DAYS[0], 'A', '-1', None), row('b', DAYS[0], 'B', tiny_smaller, None),
                              row('c', DAYS[0], 'C', '-1', None)])
        ranks = result['rankings']['symbol']['metrics']['net_return']['pooled']
        self.assertEqual(ranks['negative_contributors_ascending'], ['B', 'A', 'C'])

    def test_invalid_raw_metrics_reject_even_when_other_metric_is_missing(self):
        for metric in METRICS:
            for invalid in (True, False, .1, float('nan'), 'NaN', 'Infinity', '-Infinity', 'bad'):
                with self.subTest(metric=metric, invalid=repr(invalid)):
                    item = row('a', DAYS[0], 'A', None, None)
                    item[metric] = invalid
                    with self.assertRaises(ValueError):
                        summarize_concentration([item])

    def test_duplicate_identifier_rejects_even_across_different_dates_symbols(self):
        with self.assertRaises(ValueError):
            summarize_concentration([row('same', DAYS[0], 'A', None, '.1'), row('same', DAYS[1], 'B', '.2', None)])

    def test_deterministic_sparse_grid_all_entities_metrics_and_leaveouts(self):
        rng = random.Random(19071983)
        values = [None, None, '0', '-0', '.1', '-.3', '.7', '-2.125', '1.0000000000000000000000000000000000000000000000000000000000001']
        for scenario in range(40):
            rows = [row('s%02d_c%03d' % (scenario, i), rng.choice(DAYS), rng.choice(['A', 'B', 'C', 'D', 'E']),
                        rng.choice(values), rng.choice(values)) for i in range(scenario)]
            rng.shuffle(rows)
            with self.subTest(scenario=scenario):
                self.oracle(rows)


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(IndependentConcentrationTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    binding = {
        'completed_at_utc': datetime.now(timezone.utc).isoformat(),
        'passed': result.wasSuccessful(), 'tests_run': result.testsRun,
        'failures': len(result.failures), 'errors': len(result.errors), 'skipped': len(result.skipped),
        'deterministic_synthetic_grid_scenarios': 40,
        'actual_new_features_or_stratified_outcomes_used': False,
        'production_import_scope': 'summarize_concentration public entry point only; no production numeric helpers used in expected values',
        'independent_expected_arithmetic': 'exact Fraction from synthetic decimal values, explicit original row weights, freshly selected remaining samples',
        'peer_scope': 'synthetic extra peer metadata confirms this API preserves supplied matched_excess; actual runner peer immutability requires separate full-data audit',
        'bound_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                         for name in ('concentration.py', 'independent_concentration_tests.py')},
    }
    (ROOT / 'independent-concentration-test-binding.json').write_text(json.dumps(binding, indent=2) + '\n')
    raise SystemExit(0 if result.wasSuccessful() else 1)
