import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from checkpoint import build_checkpoint
from analyze_session import VARIANTS, PRIMARY


class CheckpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protocol = json.loads((ROOT / 'protocol.json').read_text())
        cls.days = [s['date'] for s in cls.protocol['sessions']]

    def bundle(self, day, cases):
        rows = []
        for i, spec in enumerate(cases):
            if not isinstance(spec, dict):
                spec = {'net': spec}
            symbol = spec.get('symbol', 'C' + str(i))
            for threshold, family, horizon, cost in VARIANTS:
                rows.append({'case_id': day + '__' + symbol, 'date': day, 'symbol': symbol,
                    'target_role': 'benchmark' if symbol in ('SPY','QQQ') else 'stock',
                    'current_etf_classification': spec.get('etf', 'N'),
                    'stock_eligible': spec.get('eligible', symbol not in ('SPY','QQQ')),
                    'drop_threshold': threshold, 'family': family,
                    'exit_horizon': horizon, 'cost_bps_per_side': cost,
                    'net_return': spec.get('net'), 'matched_excess': spec.get('excess'),
                    'paired_control_status': 'complete_three' if spec.get('excess') is not None else 'missing_return',
                    'signal_status': spec.get('status', 'signal'),
                    'SPY_group': 'not_stock_target' if symbol in ('SPY','QQQ') else spec.get('SPY_group', 'market_down'),
                    'QQQ_group': 'not_stock_target' if symbol in ('SPY','QQQ') else spec.get('QQQ_group', 'market_not_down'),
                    'fixed_bins': {'gap': 'le_minus_2pct', 'close29': 'minus_2pct_to_below_zero', 'trough': 'minute10_19'},
                    'actual_fill': False, 'wealth_path': False})
        return {'summary': {'protocol_id': self.protocol['id'], 'date': day,
                            'analysis_scope': 'prospective_sealed_postclose_stock_proxy',
                            'source': {'source_complete': True},
                            'counts': {'selected_cases': len(cases), 'result_rows': len(rows)}}, 'rows': rows}

    def build(self, sessions=None, ordinal=20, ledger=None, asof=None):
        return build_checkpoint(self.protocol, ordinal, sessions or {}, ledger or [],
            asof or self.protocol['sessions'][ordinal-1]['review_at_utc'])

    def test_empty_calendar_keeps_missing_without_zero_imputation(self):
        result = self.build()
        self.assertEqual(result['coverage']['planned_dates'], 20)
        self.assertEqual(result['coverage']['analysis_dates'], 0)
        self.assertEqual(len(result['calendar']), 20)
        self.assertTrue(all(d['primary_counts'] is None for d in result['calendar']))
        self.assertIsNone(result['primary_endpoint']['date_equal_net_mean'])
        self.assertEqual(result['coverage']['ledger_status_counts'], {'unknown_calendar_status': 20})
        self.assertEqual(len(result['variant_group_summaries']), 216)
        self.assertEqual(len(result['fixed_feature_bins']), 12)
        json.dumps(result, allow_nan=False)

    def test_zero_observation_missing_signal_and_no_signal_are_distinct(self):
        day = self.days[0]
        result = self.build({day: self.bundle(day, ['0', None,
              {'net': None, 'status': 'no_flush'}, {'net': None, 'status': 'no_rebound'},
              {'net': None, 'status': 'unknown', 'SPY_group': 'unknown'}])})
        c = result['primary_SPY_down']['counts']
        self.assertEqual(c['selected_cases'], 4)
        self.assertEqual(c['complete_net'], 1)
        self.assertEqual(c['signal_return_missing'], 1)
        self.assertEqual((c['no_flush'], c['no_rebound']), (1, 1))
        self.assertEqual(result['primary_endpoint']['date_equal_net_mean'], '0')
        self.assertEqual(result['primary_endpoint']['complete_outcome_dates'], 1)
        unknown = next(t['summary'] for t in result['variant_group_summaries']
                       if t['variant'] == list(PRIMARY) and t['benchmark'] == 'SPY' and t['group'] == 'unknown')
        self.assertEqual(unknown['counts']['unknown_signals'], 1)

    def test_date_equal_mean_is_not_event_equal(self):
        d1, d2 = self.days[:2]
        result = self.build({d1: self.bundle(d1, ['0.2'] * 9), d2: self.bundle(d2, ['-0.4'])})
        self.assertEqual(result['primary_endpoint']['date_equal_net_mean'], '-0.1')
        self.assertEqual(result['primary_SPY_down']['target_net_distribution']['mean'], '0.14')
        self.assertEqual(result['primary_endpoint']['complete_outcome_dates'], 2)

    def test_final_interval_resamples_whole_dates_not_individual_events(self):
        d1, d2 = self.days[:2]
        result = self.build({d1: self.bundle(d1, ['1'] * 100), d2: self.bundle(d2, ['-1'])}, ordinal=59)
        endpoint = result['primary_endpoint']
        self.assertEqual(endpoint['date_equal_net_mean'], '0')
        self.assertEqual(endpoint['interval']['interval'], {'level': '0.95', 'lower': '-1', 'upper': '1'})
        self.assertEqual(endpoint['interval']['draws'], 2000)
        self.assertFalse(endpoint['interval']['serial_independence_proven'])
        again = self.build({d2: self.bundle(d2, ['-1']), d1: self.bundle(d1, ['1'] * 100)}, ordinal=59)
        self.assertEqual(endpoint['interval'], again['primary_endpoint']['interval'])

    def test_final_only_interval_and_one_date_limit(self):
        d1, d2 = self.days[:2]
        data = {d1: self.bundle(d1, ['0.1']), d2: self.bundle(d2, ['0.2'])}
        for ordinal in (20, 40):
            interval = self.build(data, ordinal)['primary_endpoint']['interval']
            self.assertIsNone(interval['interval'])
            self.assertEqual(interval['draws'], 0)
            self.assertEqual(interval['reason'], 'registered_final_checkpoint_only')
        for data in ({}, {d1: self.bundle(d1, ['0.1'])}):
            interval = self.build(data, 59)['primary_endpoint']['interval']
            self.assertIsNone(interval['interval'])
            self.assertEqual(interval['reason'], 'fewer_than_two_complete_outcome_dates')

    def test_review_exact_boundary_and_before_rejected(self):
        due = self.protocol['sessions'][19]['review_at_utc']
        self.assertEqual(self.build(asof=due)['registered_review_at'], due)
        with self.assertRaisesRegex(ValueError, 'not_due'):
            self.build(asof='2026-11-04T22:29:59.999999+00:00')
        with self.assertRaisesRegex(ValueError, 'timezone_required'):
            self.build(asof='2026-11-04T22:30:00')

    def test_sample_size_twenty_boundary_is_label_not_activation(self):
        for n in (19, 20):
            data = {d: self.bundle(d, ['-0.99']) for d in self.days[:n]}
            result = self.build(data)
            label = result['primary_endpoint']['sample_size_label']
            self.assertEqual(label, 'insufficient_sample' if n < 20 else 'at_least_20_complete_outcome_dates')
            self.assertFalse(result['trading_activation'])
            self.assertIsNone(result['performance_pass_fail'])

    def test_etfs_and_controls_excluded_without_discarding_rows(self):
        d = self.days[0]
        result = self.build({d: self.bundle(d, [
            {'symbol': 'SPY', 'net': '10'}, {'symbol': 'QQQ', 'net': '10'},
            {'symbol': 'ETF', 'net': '10', 'etf': 'Y'},
            {'symbol': 'BAD', 'net': '10', 'eligible': False}, {'symbol': 'YES', 'net': '-0.1'}])})
        self.assertEqual(result['coverage']['selected_case_variant_rows'], 180)
        self.assertEqual(result['coverage']['stock_case_variant_rows'], 36)
        self.assertEqual(result['primary_endpoint']['date_equal_net_mean'], '-0.1')

    def test_costs_preserved_and_both_tails_remain(self):
        day = self.days[0]
        result = self.build({day: self.bundle(day, ['-0.25', '0', '1'])})
        dist = result['primary_SPY_down']['target_net_distribution']
        self.assertEqual(dist['mean'], '0.25')
        self.assertEqual((dist['positive_count'], dist['negative_count'], dist['zero_count']), (1, 1, 1))
        self.assertEqual(dist['minimum'], '-0.25')
        self.assertEqual(dist['maximum'], '1')

    def test_future_supplied_days_excluded_and_unknown_calendar_rejected(self):
        later = self.days[20]
        result = self.build({later: self.bundle(later, ['100'])})
        self.assertEqual(result['coverage']['later_supplied_dates_excluded'], [later])
        self.assertIsNone(result['primary_endpoint']['date_equal_net_mean'])
        with self.assertRaisesRegex(ValueError, 'unregistered_session_date'):
            self.build({'2026-10-07': self.bundle('2026-10-07', ['1'])})
        with self.assertRaisesRegex(ValueError, 'unregistered_ledger_date'):
            self.build(ledger=[{'date':'2026-10-07', 'status':'unknown'}])

    def test_ledger_statuses_preserved_input_not_mutated(self):
        d = self.days[0]
        ledger = [{'date': d, 'status': 'postclose_failed', 'reason': 'permission_denied', 'returns': None}]
        original = copy.deepcopy(ledger)
        result = self.build(ledger=ledger)
        self.assertEqual(result['calendar'][0]['ledger'], ledger[0])
        result['calendar'][0]['ledger']['reason'] = 'changed_result'
        self.assertEqual(ledger, original)
        with self.assertRaisesRegex(ValueError, 'duplicate_ledger_date'):
            self.build(ledger=ledger * 2)

    def test_duplicate_or_partial_variants_rejected(self):
        d = self.days[0]
        data = self.bundle(d, ['0.1'])
        data['rows'].append(copy.deepcopy(data['rows'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate_case_variant'):
            self.build({d: data})
        data = self.bundle(d, ['0.1'])
        data['rows'].pop()
        with self.assertRaisesRegex(ValueError, 'missing_case_variants'):
            self.build({d: data})

    def test_historical_scope_and_nonfinite_and_nonsignal_returns_rejected(self):
        d = self.days[0]
        data = self.bundle(d, ['1'])
        data['summary']['analysis_scope'] = 'historical_replay'
        with self.assertRaisesRegex(ValueError, 'nonprospective'):
            self.build({d: data})
        for value in ('NaN', 'Infinity', 0.1):
            with self.assertRaises(ValueError):
                self.build({d: self.bundle(d, [value])})
        with self.assertRaisesRegex(ValueError, 'non_signal_has_return'):
            self.build({d: self.bundle(d, [{'net':'0', 'status':'unknown'}])})

    def test_invalid_checkpoint_or_calendar_rejected(self):
        for ordinal in (1, 19, 21, 60, True):
            with self.assertRaisesRegex(ValueError, 'registered_checkpoint_required'):
                self.build(ordinal=ordinal, asof='2027-01-01T00:00:00+00:00')
        protocol = copy.deepcopy(self.protocol)
        protocol['sessions'].pop()
        with self.assertRaisesRegex(ValueError, '59_session_calendar'):
            build_checkpoint(protocol, 20, {}, [], '2027-01-01T00:00:00+00:00')

    def test_schema_corruption_rejected_before_any_summary(self):
        d = self.days[0]
        data = self.bundle(d, ['0.1'])
        data['rows'][1]['SPY_group'] = 'unknown'
        with self.assertRaisesRegex(ValueError, 'case_metadata_varies'):
            self.build({d: data})
        data = self.bundle(d, ['0.1'])
        data['summary']['counts']['selected_cases'] = 100
        with self.assertRaisesRegex(ValueError, 'summary_row_count_mismatch'):
            self.build({d: data})


if __name__ == '__main__':
    unittest.main()
