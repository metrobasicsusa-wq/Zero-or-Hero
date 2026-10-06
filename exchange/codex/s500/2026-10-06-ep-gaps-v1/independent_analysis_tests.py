"""Independent boundary/condition tests for the post-outcome gap diagnostic."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

import analyze_windows as analysis
import collect_windows as collect


META = {
    'A': {' ': 'regular', '@': 'known but unmapped', 'I': 'odd lot', 'B': 'average price', 'F': 'sweep'},
    'B': {' ': 'regular', 'I': 'odd lot'},
    'C': {'@': 'regular', 'I': 'odd lot', 'B': 'bunched', 'F': 'sweep'},
}
RULES = [
    {'condition': ' ', 'tapes': ['A', 'B'], 'open_close': True, 'high_low': True, 'volume': True},
    {'condition': '@', 'tapes': ['C'], 'open_close': True, 'high_low': True, 'volume': True},
    {'condition': 'I', 'tapes': ['A', 'B', 'C'], 'open_close': False, 'high_low': False, 'volume': True},
    {'condition': 'B', 'tapes': ['A'], 'open_close': False, 'high_low': False, 'volume': True},
    {'condition': 'B', 'tapes': ['C'], 'open_close': True, 'high_low': True, 'volume': True},
    {'condition': 'F', 'tapes': ['A', 'C'], 'open_close': True, 'high_low': True, 'volume': True},
]
POINT = {'point_id': 'fixture', 'symbol': 'TEST', 'missing_time': '2026-01-06T14:29:00-05:00',
         'target_start_utc': '2026-01-06T19:29:00Z', 'case_ids': ['fixture'], 'variant_ids': ['fixture']}


def trade(**changes):
    return {'p': 10, 's': 25, 'z': 'C', 'c': ['I'], 't': '2026-01-06T19:29:00Z', **changes}


class AnalysisAudit(unittest.TestCase):
    def cached_trade_rows(self, rows):
        point={**POINT,'request_start_utc':'2026-01-06T19:27:00Z',
               'request_end_inclusive_utc':'2026-01-06T19:31:59.999999999Z',
               'window_end_exclusive_utc':'2026-01-06T19:32:00Z'}
        with TemporaryDirectory() as folder,patch.object(collect,'ROOT',Path(folder)),patch.object(analysis,'ROOT',Path(folder)):
            with patch.object(collect,'get_json',return_value={'symbol':'TEST','trades':rows,'next_page_token':None}):
                record=collect.fetch(point,'trades')
            with patch.object(collect,'get_json',side_effect=AssertionError('no_network')):
                return analysis.load_window(point,record)

    def point_result(self, trades=(), bars=(), quotes=(), issues=None):
        samples={'bars':list(bars),'trades':list(trades),'quotes':list(quotes)}
        inputs={('fixture',stream):{'stream':stream,'status':'complete','pages_complete':True} for stream in samples}
        def load(point,record):
            return [(analysis.timestamp_ns(r['t']),r) for r in samples[record['stream']]], (issues or {}).get(record['stream'],{})
        with patch.object(analysis,'load_window',side_effect=load):
            return analysis.analyze_point(POINT,inputs,{},META,RULES)

    def test_nanosecond_precision_and_offset_equivalence(self):
        base=analysis.timestamp_ns('2026-01-06T19:29:00Z')
        self.assertEqual(base,analysis.timestamp_ns('2026-01-06T14:29:00-05:00'))
        self.assertEqual(analysis.timestamp_ns('2026-01-06T19:29:59.999999999Z')-base,60_000_000_000-1)
        self.assertEqual(analysis.timestamp_ns('2026-07-06T14:29:00-04:00'),analysis.timestamp_ns('2026-07-06T18:29:00Z'))

    def test_naive_or_excess_precision_timestamp_rejected(self):
        for value in ['2026-01-06T19:29:00','2026-01-06T19:29:00.1234567890Z','2026-01-06T19:29:60Z']:
            with self.subTest(value=value),self.assertRaises(ValueError):analysis.timestamp_ns(value)

    def test_target_minute_is_half_open_not_neighbor_observations(self):
        result=self.point_result(trades=[trade(t='2026-01-06T19:28:59.999999999Z'),trade(),trade(t='2026-01-06T19:29:59.999999999Z'),trade(t='2026-01-06T19:30:00Z')])
        self.assertEqual(result['target_trades']['records'],2)
        self.assertEqual([r['trade_records'] for r in result['five_minute_observations']],[0,1,2,1,0])

    def test_neighbor_regular_trade_does_not_explain_target(self):
        result=self.point_result(trades=[trade(t='2026-01-06T19:30:00Z',c=['@'])])
        self.assertEqual(result['classification'],'target_missing_provider_returned_no_trades')
        self.assertFalse(result['causal_bar_reconstruction_proven'])
        self.assertEqual(result['halt_status'],'not_certified_by_this_market_data_diagnostic')

    def test_oddlot_still_has_observed_shares_and_price(self):
        result=self.point_result(trades=[trade()])
        self.assertEqual(result['target_trades']['current_valid_observed_share_volume'],'25')
        self.assertEqual(result['target_trades']['current_valid_min_price'],'10')
        self.assertIn('price_excluded',result['classification'])
        self.assertFalse(result['parent_pnl_recalculated'])

    def test_multiple_conditions_take_restrictive_documented_rule(self):
        self.assertEqual(analysis.trade_state(trade(c=['@','F','I']),META,RULES),'documented_price_excluded')

    def test_same_code_changes_meaning_by_tape(self):
        self.assertEqual(analysis.trade_state(trade(c=['B'],z='A'),META,RULES),'documented_price_excluded')
        self.assertEqual(analysis.trade_state(trade(c=['B'],z='C'),META,RULES),'documented_price_updating')

    def test_unknown_tape_condition_and_empty_never_become_regular(self):
        for row in [trade(z='unknown',c=['@']),trade(c=[]),trade(c=['bad']),trade(c=['I','bad'])]:
            with self.subTest(row=row):self.assertEqual(analysis.trade_state(row,META,RULES),'unknown_conditions')
        self.assertEqual(analysis.trade_state(trade(z='A',c=['@']),META,RULES),'unknown_or_partial_price_rule')

    def test_invalidated_and_unknown_update_flags_are_separate(self):
        for flag in ['incorrect','canceled']:
            self.assertEqual(analysis.trade_state(trade(c=['@'],u=flag),META,RULES),'provider_invalidated')
        self.assertEqual(analysis.trade_state(trade(u=None),META,RULES),'unknown_update_flag')
        result=self.point_result(trades=[trade(u='corrected')])
        self.assertEqual(result['target_trades']['current_valid_records'],1)
        self.assertFalse(result['target_trades']['historical_point_in_time_version_proven'])

    def test_invalid_price_size_not_valid_trade(self):
        for field in ['p','s']:
            for value in [0,-1,True,'NaN','Infinity',None]:
                with self.subTest(field=field,value=value):
                    self.assertEqual(analysis.trade_state(trade(**{field:value}),META,RULES),'invalid_price_or_size')

    def test_bad_neighbor_timestamp_blocks_absence_classification(self):
        result=self.point_result(issues={'trades':{'invalid_timestamp':1}})
        self.assertEqual(result['classification'],'source_integrity_or_pagination_unresolved')

    def test_quote_geometry_does_not_infer_halt_or_fill(self):
        result=analysis.summarize_quotes([{'bp':10,'ap':10,'bs':2,'as':3},{'bp':11,'ap':10,'bs':2,'as':3},{'bp':0,'ap':10,'bs':2,'as':3}])
        self.assertEqual(result['positive_prices_sizes_non_crossed'],1)
        self.assertFalse(result['executable_capacity_or_fill_inferred'])
        self.assertFalse(result['continuous_market_or_no_halt_inferred'])

    def test_reappearing_bar_is_version_discrepancy_not_repair(self):
        result=self.point_result(bars=[{'t':'2026-01-06T19:29:00Z','o':10,'h':10,'l':10,'c':10,'v':25}])
        self.assertEqual(result['classification'],'target_bar_reappeared_version_discrepancy')
        self.assertFalse(result['parent_pnl_recalculated'])

    def test_exact_duplicate_trade_identity_is_flagged(self):
        row=trade(i=1,x='Q')
        rows,issues=self.cached_trade_rows([row,dict(row)])
        self.assertEqual(len(rows),2)  # Raw observations retained, not silently discarded.
        self.assertEqual(issues,{'duplicate_trade_identity':1})

    def test_conflicting_trade_identity_is_flagged(self):
        rows,issues=self.cached_trade_rows([trade(i=1,x='Q'),trade(i=1,x='Q',p=11)])
        self.assertEqual(issues,{'duplicate_trade_identity':1,'conflicting_trade_identity':1})

    def test_context_endpoint_and_malformed_timestamp_are_quarantined(self):
        rows,issues=self.cached_trade_rows([trade(i=1,x='Q',t='2026-01-06T19:31:59.999999999Z'),
                                           trade(i=2,x='Q',t='2026-01-06T19:32:00Z'),
                                           trade(i=3,x='Q',t='malformed')])
        self.assertEqual(len(rows),1)
        self.assertEqual(issues,{'outside_requested_window':1,'invalid_timestamp':1})


if __name__=='__main__':unittest.main()
