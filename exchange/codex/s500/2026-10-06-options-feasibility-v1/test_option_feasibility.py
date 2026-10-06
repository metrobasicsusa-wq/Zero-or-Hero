import copy
import json
import unittest
from decimal import Decimal as D
from option_feasibility import (analyze_contract, evaluate_quote, evaluate_trade_price_proxy,
                                exact_timestamp_ns, sanitized_contract, number)


def fixture():
    contract = {'id': 'private-contract-uuid', 'symbol': 'SPY261009C00700000',
        'root_symbol': 'SPY', 'underlying_symbol': 'SPY', 'underlying_asset_id': 'private-asset-uuid',
        'type': 'call', 'style': 'american', 'status': 'active', 'tradable': True,
        'expiration_date': '2026-10-09', 'strike_price': '700', 'multiplier': '100', 'size': '100',
        'deliverables': [{'type': 'equity', 'symbol': 'SPY', 'asset_id': 'private-deliverable-uuid',
            'amount': '100', 'allocation_percentage': '100', 'settlement_type': 'T+1',
            'settlement_method': 'CCC', 'delayed_settlement': False}]}
    quote = {'t': '2026-10-06T13:59:59.500000000Z', 'bp': '.95', 'ap': '1',
             'bs': 10, 'as': 5, 'bx': 'X', 'ax': 'Q', 'c': ' '}
    observation = {'received_at': '2026-10-06T14:00:00.000000000Z', 'feed': 'opra',
        'real_provider_bid_ask': True, 'market_open_at_observation': True,
        'quote_size_units': 'contracts', 'size_units_verified': True,
        'condition_mapping_verified': True, 'quote_symbol': contract['symbol']}
    return contract, quote, observation


class OptionTests(unittest.TestCase):
    def setUp(self):
        self.c, self.q, self.o = fixture()

    def evaluate(self, quote=None, observation=None):
        return evaluate_quote(self.c, self.q if quote is None else quote,
                              self.o if observation is None else observation)

    def test_standard_good_quote_has_conditional_quality_not_execution(self):
        r = self.evaluate()
        self.assertTrue(r['primary_quote_quality_eligible'])
        self.assertEqual(r['age_nanoseconds'], 500000000)
        self.assertEqual(r['age_seconds'], '0.5')
        self.assertFalse(r['historical_decision_time_receipt_or_availability_verified'])
        self.assertFalse(r['exercise_and_liquidation_readiness_verified'])
        self.assertFalse(r['strategy_returns_computed'])
        self.assertEqual(r['actual_orders_sent'], 0)

    def test_metadata_is_current_not_historical_listing(self):
        r = analyze_contract(self.c, self.o)
        self.assertTrue(r['standard_metadata_consistent'])
        self.assertFalse(r['historical_membership_or_listability_verified'])
        self.assertFalse(r['last_trading_time_or_exercise_cutoff_verified'])
        self.assertIsNone(r['delta'])

    def test_explicit_multiplier_not_inferred_from_size_or_symbol(self):
        del self.c['multiplier']
        r = self.evaluate()
        self.assertFalse(r['primary_quote_quality_eligible'])
        for row in r['affordability_scenarios']:
            self.assertIsNone(row['whole_contracts_budget_only'])
            self.assertIsNone(row['one_contract_total_including_reserve'])
            self.assertIsNone(row['one_contract_affordable'])
        self.assertEqual(r['contract_analysis']['contract']['size'], '100')

    def test_positive_non100_multiplier_preserved_for_math_but_nonstandard(self):
        self.c['multiplier'] = '10'
        r = self.evaluate()
        self.assertFalse(r['primary_quote_quality_eligible'])
        self.assertEqual(r['affordability_scenarios'][0]['whole_contracts_budget_only'], 50)
        self.assertEqual(r['affordability_scenarios'][0]['premium_per_contract'], '10')
        self.assertEqual(r['contract_analysis']['contract']['size'], '100')

    def test_bad_multiplier_types_and_nonpositive_remain_unknown(self):
        for value in (True, False, 0, -100, 'NaN', 'Infinity', None, [], {}):
            self.c['multiplier'] = value
            r = self.evaluate()
            self.assertFalse(r['primary_quote_quality_eligible'])
            self.assertIsNone(r['affordability_scenarios'][0]['whole_contracts_budget_only'])

    def test_adjusted_or_missing_deliverables_cannot_self_certify(self):
        for deliverables in (None, [], [{'type': 'cash', 'symbol': None, 'amount': '100',
                                        'allocation_percentage': '100'}]):
            self.c['deliverables'] = deliverables
            self.c['adjusted_deliverable_proof_verified'] = True
            r = self.evaluate()
            self.assertFalse(r['primary_quote_quality_eligible'])
            self.assertFalse(r['contract_analysis']['standard_metadata_consistent'])
            self.assertFalse(r['contract_analysis']['deliverables_independently_verified'])
            self.assertTrue(r['contract_analysis']['provider_asserted_adjusted_proof'])
            self.assertEqual(r['affordability_scenarios'][0]['whole_contracts_budget_only'], 5)

    def test_delayed_unknown_or_nonstandard_component_blocks_primary(self):
        for key, value in (('delayed_settlement', True), ('delayed_settlement', None),
                           ('amount', '150'), ('allocation_percentage', '50'),
                           ('symbol', 'QQQ'), ('settlement_method', None),
                           ('settlement_type', None)):
            self.c, self.q, self.o = fixture()
            self.c['deliverables'][0][key] = value
            self.assertFalse(self.evaluate()['primary_quote_quality_eligible'], (key, value))

    def test_option_encoded_right_expiry_strike_declared_root_conflicts(self):
        for key, value in (('type', 'put'), ('expiration_date', '2026-10-16'),
                           ('strike_price', '701'), ('root_symbol', 'QQQ')):
            self.c, self.q, self.o = fixture()
            self.c[key] = value
            r = self.evaluate()
            self.assertFalse(r['primary_quote_quality_eligible'])
            self.assertFalse(r['contract_analysis']['encoded_symbol_matches_expiry_right_strike_and_declared_root'])

    def test_underlying_mapping_not_guessed_from_root(self):
        self.c['symbol'] = 'SPY1261009C00700000'
        self.c['root_symbol'] = 'SPY1'
        self.o['quote_symbol'] = self.c['symbol']
        r = self.evaluate()
        self.assertTrue(r['contract_analysis']['encoded_symbol_matches_expiry_right_strike_and_declared_root'])
        self.assertEqual(r['contract_analysis']['contract']['underlying_symbol'], 'SPY')

    def test_contract_expiry_uses_observation_date_not_study_cutoff(self):
        self.c['expiration_date'] = '2026-10-05'
        self.c['symbol'] = 'SPY261005C00700000'
        self.o['quote_symbol'] = self.c['symbol']
        r = self.evaluate()['contract_analysis']
        self.assertTrue(r['expired_before_observation_date'])
        self.assertFalse(r['expired_before_study_cutoff_date'])
        self.assertTrue(r['expiration_on_study_cutoff_date'])
        self.assertFalse(r['current_contract_metadata_eligible'])

    def test_missing_observation_date_cannot_certify_current_expiry(self):
        r = analyze_contract(self.c, {})
        self.assertFalse(r['current_contract_metadata_eligible'])
        self.assertIsNone(r['expired_before_observation_date'])

    def test_current_inactive_or_nontradable_not_primary(self):
        self.c['status'] = 'inactive'
        self.assertFalse(self.evaluate()['primary_quote_quality_eligible'])
        self.c['status'] = 'active'
        self.c['tradable'] = 'true'
        self.assertFalse(self.evaluate()['primary_quote_quality_eligible'])

    def test_exact_timestamp_nsec_and_timezone_equivalence(self):
        self.assertEqual(exact_timestamp_ns('2026-10-06T14:00:00.000000001Z') -
                         exact_timestamp_ns('2026-10-06T14:00:00Z'), 1)
        self.assertEqual(exact_timestamp_ns('2026-10-06T10:00:00.123456789-04:00'),
                         exact_timestamp_ns('2026-10-06T14:00:00.123456789Z'))
        self.assertEqual(exact_timestamp_ns('1969-12-31T23:59:59.999999999Z'), -1)

    def test_invalid_or_naive_timestamps_not_silently_fixed(self):
        for value in ('2026-10-06T14:00:00', '2026-10-06 14:00:00Z',
                      '2026-10-06T14:00:00.0000000001Z', '2026-10-06T14:00:00+00:60',
                      '2026-10-06T14:00:00+24:00', '2026-10-06T14:00:60Z', True):
            with self.assertRaises(ValueError):
                exact_timestamp_ns(value)
            self.q['t'] = value
            r = self.evaluate()
            self.assertFalse(r['primary_quote_quality_eligible'])
            self.assertIsNone(r['age_nanoseconds'])

    def test_five_second_boundary_exact_inclusive_and_one_ns_stale(self):
        self.q['t'] = '2026-10-06T13:59:55.000000000Z'
        self.assertTrue(self.evaluate()['primary_quote_quality_eligible'])
        self.q['t'] = '2026-10-06T13:59:54.999999999Z'
        r = self.evaluate()
        self.assertEqual(r['age_nanoseconds'], 5000000001)
        self.assertFalse(r['primary_quote_quality_eligible'])

    def test_one_ns_future_is_rejected(self):
        self.q['t'] = '2026-10-06T14:00:00.000000001Z'
        r = self.evaluate()
        self.assertEqual(r['age_nanoseconds'], -1)
        self.assertFalse(r['primary_quote_quality_eligible'])

    def test_indicative_delayed_unknown_feeds_not_primary_even_if_fresh(self):
        for feed in ('indicative', 'delayed', 'unknown', None):
            self.o['feed'] = feed
            r = self.evaluate()
            self.assertFalse(r['primary_quote_quality_eligible'])
            self.assertEqual(r['affordability_scenarios'][0]['whole_contracts_budget_only'], 5)

    def test_after_hours_unknown_clock_or_nonprovider_prices_not_primary(self):
        for field in ('market_open_at_observation', 'real_provider_bid_ask'):
            for bad in (False, None, 1, 'true'):
                self.c, self.q, self.o = fixture()
                self.o[field] = bad
                self.assertFalse(self.evaluate()['primary_quote_quality_eligible'])

    def test_unit_and_condition_mapping_requires_verified_true(self):
        for field in ('condition_mapping_verified', 'size_units_verified'):
            for value in (False, None, 1, 'true'):
                self.c, self.q, self.o = fixture()
                self.o[field] = value
                r = self.evaluate()
                self.assertFalse(r['primary_quote_quality_eligible'])
                if field == 'size_units_verified':
                    self.assertIsNone(r['affordability_scenarios'][0]['displayed_ask_size_capped_quantity'])
        self.c, self.q, self.o = fixture()
        self.o['quote_size_units'] = 'shares'
        self.assertFalse(self.evaluate()['primary_quote_quality_eligible'])
        self.assertIsNone(self.evaluate()['affordability_scenarios'][0]['displayed_ask_size_capped_quantity'])

    def test_only_blank_or_A_conditions(self):
        for condition in (' ', 'A'):
            self.q['c'] = condition
            self.assertTrue(self.evaluate()['primary_quote_quality_eligible'])
        for condition in ('', None, 'B', 'R', '\t', ' A', True):
            self.q['c'] = condition
            self.assertFalse(self.evaluate()['primary_quote_quality_eligible'])

    def test_positive_uncrossed_and_locked_quotes(self):
        for key, bad in (('bp', 0), ('ap', 0), ('bp', -1), ('ap', 'NaN'),
                         ('bp', True), ('ap', False), ('ap', 'Infinity')):
            self.c, self.q, self.o = fixture()
            self.q[key] = bad
            self.assertFalse(self.evaluate()['primary_quote_quality_eligible'])
        self.c, self.q, self.o = fixture()
        self.q['bp'] = '1'
        r = self.evaluate()
        self.assertTrue(r['primary_quote_quality_eligible'])
        self.assertEqual(r['spread_fraction_of_midpoint'], '0')
        self.q['bp'] = '1.01'
        r = self.evaluate()
        self.assertFalse(r['primary_quote_quality_eligible'])
        self.assertIsNone(r['affordability_scenarios'][0]['equal_instant_bid_liquidation_friction_one_contract'])

    def test_spread_midpoint_threshold_not_ask_denominator(self):
        self.q.update(bp='.95', ap='1.05')
        self.assertTrue(self.evaluate()['primary_quote_quality_eligible'])
        self.q['ap'] = '1.050000001'
        self.assertFalse(self.evaluate()['primary_quote_quality_eligible'])
        self.q.update(bp='.90', ap='1.00')
        self.assertFalse(self.evaluate()['primary_quote_quality_eligible'])

    def test_positive_integer_sizes_both_sides_required(self):
        for key in ('bs', 'as'):
            for bad in (0, -1, '.5', True, False, 'NaN', None):
                self.c, self.q, self.o = fixture()
                self.q[key] = bad
                self.assertFalse(self.evaluate()['primary_quote_quality_eligible'])
        self.c, self.q, self.o = fixture()
        self.q.update(bs='1.0', **{'as': '2.000'})
        self.assertTrue(self.evaluate()['primary_quote_quality_eligible'])

    def test_wrong_symbol_join_blocks_without_private_raw_echo(self):
        self.o['quote_symbol'] = 'QQQ261009C00700000'
        self.assertFalse(self.evaluate()['primary_quote_quality_eligible'])
        del self.o['quote_symbol']
        self.q['symbol'] = 'private-wrong-symbol'
        r = self.evaluate()
        self.assertFalse(r['primary_quote_quality_eligible'])
        self.assertNotIn('private-wrong-symbol', json.dumps(r))

    def test_missing_explicit_quote_symbol_join_cannot_certify(self):
        del self.o['quote_symbol']
        self.assertFalse(self.evaluate()['primary_quote_quality_eligible'])
        self.q['symbol'] = self.c['symbol']
        self.assertTrue(self.evaluate()['primary_quote_quality_eligible'])

    def test_latest_bad_quote_is_not_replaced_with_embedded_previous(self):
        valid = copy.deepcopy(self.q)
        self.q['bp'] = 0
        self.q['previous_quote'] = valid
        r = self.evaluate()
        self.assertFalse(r['primary_quote_quality_eligible'])
        self.assertIsNone(r['quote']['bid'])
        r = self.evaluate(quote=[valid, self.q])
        self.assertFalse(r['primary_quote_quality_eligible'])
        self.assertIsNone(r['affordability_scenarios'][0]['whole_contracts_budget_only'])

    def test_absent_quote_retained_as_unknown_not_zero_wealth(self):
        r = evaluate_quote(self.c, None, self.o)
        self.assertFalse(r['primary_quote_quality_eligible'])
        self.assertIsNone(r['age_nanoseconds'])
        for row in r['affordability_scenarios']:
            self.assertIsNone(row['whole_contracts_budget_only'])
        self.assertNotIn('ending_equity', r)
        self.assertNotIn('profit', r)

    def test_exact_500_boundary_and_fee_reserves(self):
        self.q.update(bp='4.90', ap='5')
        rows = self.evaluate()['affordability_scenarios']
        self.assertEqual([r['whole_contracts_budget_only'] for r in rows], [1, 0, 0])
        self.assertEqual([r['one_contract_affordable'] for r in rows], [True, False, False])
        self.assertEqual(D(rows[0]['budget_remaining']), 0)
        self.assertEqual(D(rows[1]['one_contract_total_including_reserve']), D('500.10'))

    def test_decimal_fee_exact_boundary_no_float_tolerance(self):
        self.q.update(bp='4.9', ap='4.999')
        rows = self.evaluate()['affordability_scenarios']
        self.assertEqual([r['whole_contracts_budget_only'] for r in rows], [1, 1, 0])
        self.assertEqual(D(rows[1]['one_contract_total_including_reserve']), D('500'))
        self.assertEqual(D(rows[1]['budget_remaining']), 0)

    def test_whole_contracts_no_fractional_funding_and_budget_identity(self):
        self.q.update(bp='1.90', ap='2')
        rows = self.evaluate()['affordability_scenarios']
        self.assertTrue(all(type(r['whole_contracts_budget_only']) is int for r in rows))
        self.assertEqual([r['whole_contracts_budget_only'] for r in rows], [2, 2, 2])
        for row in rows:
            self.assertEqual(D(row['budget_remaining']) + D(row['budget_used_including_reserve']), 500)
            self.assertLessEqual(D(row['budget_used_including_reserve']), 500)
            self.assertGreater((row['whole_contracts_budget_only']+1)*D(row['one_contract_total_including_reserve']), 500)

    def test_size_caps_are_contract_units_not_assumed100_multiplier(self):
        self.q.update(bp='.49', ap='.5', bs=2, **{'as': 3})
        rows = self.evaluate()['affordability_scenarios']
        self.assertEqual(rows[0]['whole_contracts_budget_only'], 10)
        self.assertEqual(rows[0]['displayed_ask_size_capped_quantity'], 3)
        self.assertEqual(rows[0]['displayed_two_sided_size_capped_quantity'], 2)
        self.o['size_units_verified'] = False
        self.assertIsNone(self.evaluate()['affordability_scenarios'][0]['displayed_ask_size_capped_quantity'])

    def test_equal_instant_bid_friction_is_arithmetic_not_strategy_pnl(self):
        self.q.update(bp='.50', ap='.60')
        rows = self.evaluate()['affordability_scenarios']
        self.assertEqual([D(r['equal_instant_bid_liquidation_friction_one_contract']) for r in rows],
                         [D('10'), D('10.10'), D('11')])
        self.assertTrue(all(r['arithmetic_only_not_order_or_actual_return'] for r in rows))
        self.assertTrue(all(r['reserve_is_research_hypothesis_not_actual_broker_fee'] for r in rows))
        self.q['bp'] = 0
        self.assertIsNone(self.evaluate()['affordability_scenarios'][0]['equal_instant_bid_liquidation_friction_one_contract'])

    def test_historical_trade_proxy_is_separate_and_event_dated(self):
        self.c.update(symbol='SPY260102C00700000', expiration_date='2026-01-02', status='inactive', tradable=False)
        r = evaluate_trade_price_proxy(self.c, '4.99', {'received_at': '2026-01-02T15:00:01Z'})
        self.assertEqual(r['contract_analysis']['observation_date_et'], '2026-01-02')
        self.assertFalse(r['contract_analysis']['expired_before_observation_date'])
        self.assertTrue(r['contract_analysis']['expiration_on_observation_date'])
        self.assertFalse(r['primary_quote_quality_eligible'])
        self.assertFalse(r['is_executable_premium_reference'])
        self.assertFalse(r['quote_age_spread_size_or_exit_coverage_verified'])
        self.assertEqual(r['affordability_scenarios'][0]['whole_contracts_budget_only'], 1)
        self.assertTrue(all(x['equal_instant_bid_liquidation_friction_one_contract'] is None for x in r['affordability_scenarios']))

    def test_delta_not_fabricated_even_if_moneyness_is_obvious(self):
        self.c['delta'] = 1
        self.q['delta'] = '.99'
        r = self.evaluate()['contract_analysis']
        self.assertIsNone(r['delta'])
        self.assertEqual(r['delta_status'], 'unknown_not_inferred_from_strike_or_moneyness')

    def test_nested_privacy_allowlist_omits_all_ids(self):
        self.c['account_id'] = 'private-account-uuid'
        self.c['order_id'] = 'private-order-uuid'
        self.c['secret'] = 'private-secret-value'
        self.c['deliverables'][0]['account_id'] = 'private-nested-account-uuid'
        self.q.update(id='private-quote-id', order_id='private-quote-order')
        serialized = json.dumps(self.evaluate())
        for forbidden in ('private-contract-uuid', 'private-asset-uuid', 'private-deliverable-uuid',
                          'private-account-uuid', 'private-order-uuid', 'private-secret-value',
                          'private-nested-account-uuid', 'private-quote-id', 'private-quote-order'):
            self.assertNotIn(forbidden, serialized)
        self.assertNotIn('asset_id', serialized)
        self.assertNotIn('account_id', serialized)
        self.assertNotIn('order_id', serialized)

    def test_nonnumeric_strings_and_bool_not_money(self):
        for value in (True, False, [], {}, None, 'not-a-number', 'NaN', 'Infinity'):
            with self.assertRaises(ValueError):
                number(value)

    def test_unregistered_fee_budget_quality_overrides_rejected(self):
        for policy in ({'budget': '100'}, {'roundtrip_reserves_per_contract': ['0']},
                       {'max_age_seconds': '60'}, {'max_relative_spread_mid': '.5'}):
            with self.assertRaises(ValueError):
                evaluate_quote(self.c, self.q, self.o, policy)


if __name__ == '__main__':
    unittest.main()
