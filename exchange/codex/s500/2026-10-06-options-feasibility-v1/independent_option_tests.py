"""Independent synthetic option arithmetic, timestamp and evidence-boundary tests."""
import copy
import json
import unittest
from decimal import Decimal as D

import option_feasibility as option


def fixture():
    contract={'symbol':'AAPL261009C00100000','underlying_symbol':'AAPL','root_symbol':'AAPL',
              'status':'active','tradable':True,'type':'call','style':'american',
              'expiration_date':'2026-10-09','strike_price':'100','multiplier':'100','size':'100',
              'deliverables':[{'type':'equity','symbol':'AAPL','amount':'100','allocation_percentage':'100',
                               'delayed_settlement':False,'settlement_type':'T+1','settlement_method':'CCC'}]}
    quote={'t':'2026-10-06T14:00:00.000000000Z','bp':'1.95','ap':'2.00','bs':3,'as':4,'c':'A','bx':'A','ax':'B'}
    observation={'received_at':'2026-10-06T14:00:01.000000000Z','feed':'opra','real_provider_bid_ask':True,
                 'market_open_at_observation':True,'quote_size_units':'contracts','size_units_verified':True,
                 'condition_mapping_verified':True,'quote_symbol':contract['symbol']}
    return contract,quote,observation


class IndependentOptionTests(unittest.TestCase):
    def setUp(self):self.c,self.q,self.o=fixture()
    def result(self):return option.evaluate_quote(self.c,self.q,self.o)

    def test_valid_synthetic_reference_is_conditional_not_fill(self):
        r=self.result();self.assertTrue(r['primary_quote_quality_eligible'])
        self.assertTrue(r['quote_eligibility_is_not_order_fill_or_strategy_edge'])
        self.assertFalse(r['historical_decision_time_receipt_or_availability_verified'])
        self.assertFalse(r['exercise_and_liquidation_readiness_verified'])
        self.assertFalse(r['strategy_returns_computed']);self.assertEqual(r['actual_orders_sent'],0)

    def test_exact_ns_future_cannot_truncate_to_now(self):
        self.o['received_at']='2026-10-06T14:00:00Z';self.q['t']='2026-10-06T14:00:00.000000001Z'
        r=self.result();self.assertEqual(r['age_nanoseconds'],-1)
        self.assertFalse(r['primary_quote_quality_eligible'])

    def test_exact_five_seconds_boundary_and_one_ns_older(self):
        self.o['received_at']='2026-10-06T14:00:05Z'
        self.assertTrue(self.result()['primary_quote_quality_eligible'])
        self.q['t']='2026-10-06T13:59:59.999999999Z'
        self.assertEqual(self.result()['age_nanoseconds'],5000000001)
        self.assertFalse(self.result()['primary_quote_quality_eligible'])

    def test_offset_equivalence_preserves_ns(self):
        a=option.exact_timestamp_ns('2026-10-06T10:00:00.123456789-04:00')
        b=option.exact_timestamp_ns('2026-10-06T14:00:00.123456789Z')
        self.assertEqual(a,b)
        self.assertEqual(b-option.exact_timestamp_ns('2026-10-06T14:00:00Z'),123456789)

    def test_naive_overprecision_bad_date_and_numeric_timestamps_rejected(self):
        for value in ['2026-10-06T14:00:00','2026-10-06T14:00:00.0000000001Z','2026-02-30T14:00:00Z','2026-10-06T14:00:00+00:60',123,True,None]:
            with self.subTest(value=value),self.assertRaises(ValueError):option.exact_timestamp_ns(value)

    def test_indicative_cannot_become_primary_even_if_other_flags_true(self):
        self.o['feed']='indicative';r=self.result()
        self.assertFalse(r['primary_quote_quality_eligible']);self.assertTrue(r['affordability_scenarios'][0]['one_contract_affordable'])

    def test_market_closed_unknown_mapping_or_nonliteral_flags_block(self):
        for field in ['market_open_at_observation','size_units_verified','condition_mapping_verified','real_provider_bid_ask']:
            for value in [False,None,1,'true']:
                with self.subTest(field=field,value=value):
                    o={**self.o,field:value};self.assertFalse(option.evaluate_quote(self.c,self.q,o)['primary_quote_quality_eligible'])

    def test_stock_R_condition_and_array_not_accepted_as_option_regular(self):
        for condition in ['', 'R','F','I','T','X','Y',['A'],None]:
            self.q['c']=condition;self.assertFalse(self.result()['primary_quote_quality_eligible'])

    def test_zero_bid_crossed_and_nonfinite_cannot_certify(self):
        for bid,ask in [('0','2'),('3','2'),('NaN','2'),('1','Infinity'),('1',True)]:
            self.q.update(bp=bid,ap=ask);r=self.result();self.assertFalse(r['primary_quote_quality_eligible'])
            if bid=='3':self.assertIsNone(r['affordability_scenarios'][0]['equal_instant_bid_liquidation_friction_one_contract'])

    def test_latest_invalid_record_not_replaced_by_embedded_old_quote(self):
        self.q.update(bp=0,prior_valid_quote=fixture()[1]);r=self.result()
        self.assertFalse(r['primary_quote_quality_eligible']);self.assertIsNone(r['quote']['bid'])

    def test_positive_integer_contract_sizes_only(self):
        for size in [0,-1,0.5,True,'NaN']:
            self.q['as']=size;self.assertFalse(self.result()['primary_quote_quality_eligible'])

    def test_unverified_size_mapping_never_caps_quantity(self):
        self.o['size_units_verified']=False;r=self.result()
        for row in r['affordability_scenarios']:
            self.assertIsNone(row['displayed_ask_size_capped_quantity'])
            self.assertIsNone(row['displayed_two_sided_size_capped_quantity'])

    def test_fees_are_per_contract_not_once_per_position(self):
        self.q.update(ap='0.01',bp='0.01',**{'as':1000,'bs':1000})
        r=self.result();q=[row['whole_contracts_budget_only'] for row in r['affordability_scenarios']]
        self.assertEqual(q,[500,454,250])
        for row in r['affordability_scenarios']:
            self.assertLessEqual(D(row['budget_used_including_reserve']),500)
            self.assertEqual(D(row['budget_used_including_reserve'])+D(row['budget_remaining']),500)

    def test_exact_500_premium_and_positive_reserve_cross_budget(self):
        self.q.update(ap='5',bp='5');r=self.result()
        self.assertEqual([x['whole_contracts_budget_only'] for x in r['affordability_scenarios']],[1,0,0])
        self.assertEqual([x['one_contract_affordable'] for x in r['affordability_scenarios']],[True,False,False])

    def test_only_explicit_multiplier_used_never_size(self):
        self.c['multiplier']='10';self.c['size']='100';r=self.result()
        self.assertEqual(r['affordability_scenarios'][0]['premium_per_contract'],'20.00')
        self.assertFalse(r['primary_quote_quality_eligible'])
        del self.c['multiplier'];r=self.result()
        for row in r['affordability_scenarios']:self.assertIsNone(row['whole_contracts_budget_only'])

    def test_historical_trade_proxy_not_quote_or_return(self):
        r=option.evaluate_trade_price_proxy(self.c,'2',self.o)
        self.assertFalse(r['primary_quote_quality_eligible']);self.assertFalse(r['is_executable_premium_reference'])
        self.assertFalse(r['strategy_returns_computed']);self.assertTrue(r['affordability_scenarios'][0]['one_contract_affordable'])

    def test_inactive_metadata_can_have_arithmetic_but_not_current_primary(self):
        self.c.update(status='inactive',tradable=False);r=self.result()
        self.assertFalse(r['primary_quote_quality_eligible']);self.assertTrue(r['affordability_scenarios'][0]['one_contract_affordable'])
        self.assertFalse(r['contract_analysis']['historical_membership_or_listability_verified'])

    def test_delayed_delivery_is_not_standard_certified(self):
        self.c['deliverables'][0]['delayed_settlement']=True
        self.assertFalse(self.result()['primary_quote_quality_eligible'])

    def test_occ_expiry_type_and_strike_conflicts_remain_unknown(self):
        for field,value in [('expiration_date','2026-10-16'),('type','put'),('strike_price','101'),('root_symbol','AAPL1')]:
            c=copy.deepcopy(self.c);c[field]=value
            with self.subTest(field=field):self.assertFalse(option.evaluate_quote(c,self.q,self.o)['primary_quote_quality_eligible'])

    def test_quote_contract_join_mismatch_is_not_primary(self):
        self.o['quote_symbol']='MSFT261009C00100000'
        self.assertFalse(self.result()['primary_quote_quality_eligible'])

    def test_missing_quote_contract_join_is_unknown(self):
        del self.o['quote_symbol'];self.assertFalse(self.result()['primary_quote_quality_eligible'])
        self.q['symbol']=self.c['symbol'];self.assertTrue(self.result()['primary_quote_quality_eligible'])

    def test_raw_adjusted_proof_boolean_cannot_certify_missing_deliverables(self):
        self.c['deliverables']=None;self.c['adjusted_deliverable_proof_verified']=True
        r=self.result()
        self.assertFalse(r['primary_quote_quality_eligible'])
        self.assertFalse(r['contract_analysis']['deliverables_independently_verified'])
        self.assertTrue(r['contract_analysis']['provider_asserted_adjusted_proof'])

    def test_missing_or_nonpositive_explicit_fields_preserve_unknown(self):
        for field,value in [('multiplier',None),('multiplier',0),('strike_price',0),('underlying_symbol',None),('expiration_date',None)]:
            c=copy.deepcopy(self.c);c[field]=value
            self.assertFalse(option.evaluate_quote(c,self.q,self.o)['primary_quote_quality_eligible'])

    def test_expiry_uses_observation_ET_date(self):
        self.c['symbol']='AAPL261005C00100000';self.c['expiration_date']='2026-10-05'
        self.o['received_at']='2026-10-06T00:30:00Z';self.q['t']='2026-10-06T00:30:00Z'
        r=self.result();self.assertEqual(r['contract_analysis']['observation_date_et'],'2026-10-05')
        self.assertTrue(r['contract_analysis']['expiration_on_observation_date'])
        self.o['received_at']='2026-10-06T14:00:00Z';self.q['t']=self.o['received_at']
        self.assertFalse(self.result()['primary_quote_quality_eligible'])

    def test_private_uuid_fields_removed_at_every_level(self):
        self.c.update(id='private-contract-uuid',underlying_asset_id='private-asset-uuid',account_id='private-account-uuid')
        self.c['deliverables'][0].update(asset_id='private-nested-uuid',raw={'secret':'never_publish'})
        self.q['id']='private-quote-id'
        out=json.dumps(self.result())
        for text in ['private-contract-uuid','private-asset-uuid','private-account-uuid','private-nested-uuid','private-quote-id','never_publish']:
            self.assertNotIn(text,out)


if __name__=='__main__':unittest.main(verbosity=2)
