"""Offline independent checks of frozen option feasibility outputs.

No analyzer import, network access, order/account calls or strategy simulation.
Run after the parent's pure arithmetic driver finishes. Source page audit is a
separate independent-source-audit.json artifact.
"""
from pathlib import Path
from collections import Counter
from datetime import datetime,timezone
from decimal import Decimal,InvalidOperation,ROUND_FLOOR
import hashlib,json,re

ROOT=Path(__file__).resolve().parent
D=Decimal

def read(name):return json.loads((ROOT/name).read_text())
def sha(name):return hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
def positive(value):
    if isinstance(value,bool) or not isinstance(value,(int,float,str,D)):return None
    try:r=D(str(value))
    except InvalidOperation:return None
    return r if r.is_finite() and r>0 else None

def decimal_or_none(value):return None if value is None else D(str(value))
def key(row):return row['case_id'],row['type']
def index(rows,n):
    result={key(x):x for x in rows}
    assert len(result)==len(rows)==n,('duplicate_or_missing_rows',len(result),len(rows),n)
    return result

def time_ns(value):
    if not isinstance(value,str):return None
    m=re.fullmatch(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})',value)
    if not m:return None
    base,frac,offset=m.groups()
    if offset!='Z' and (int(offset[1:3])>23 or int(offset[4:6])>59):return None
    try:t=datetime.fromisoformat(base+('+00:00' if offset=='Z' else offset)).astimezone(timezone.utc)
    except ValueError:return None
    delta=t-datetime(1970,1,1,tzinfo=timezone.utc)
    return (delta.days*86400+delta.seconds)*10**9+int((frac or '').ljust(9,'0') or '0')

def records_observed(value):
    return type(value) is int and value>0

def equal_decimal(actual,expected,label):assert decimal_or_none(actual)==expected,(label,actual,expected)

def scenarios(analysis,multiplier,ask,bid,current):
    rows=analysis['affordability_scenarios'];assert len(rows)==3
    for row,reserve in zip(rows,[D(0),D('.10'),D(1)]):
        equal_decimal(row['roundtrip_reserve_per_contract'],reserve,'reserve')
        equal_decimal(row['budget'],D(500),'budget')
        base=None if multiplier is None or ask is None else multiplier*ask
        total=None if base is None else base+reserve
        quantity=None if total is None else int((D(500)/total).to_integral_value(rounding=ROUND_FLOOR))
        friction=None if base is None or bid is None or bid>ask else (ask-bid)*multiplier+reserve
        equal_decimal(row['premium_per_contract'],base,'premium')
        equal_decimal(row['one_contract_total_including_reserve'],total,'total')
        assert row['one_contract_affordable'] is (None if total is None else total<=500)
        assert row['whole_contracts_budget_only']==quantity
        equal_decimal(row['budget_used_including_reserve'],None if total is None else quantity*total,'used')
        equal_decimal(row['budget_remaining'],None if total is None else D(500)-quantity*total,'remaining')
        equal_decimal(row['equal_instant_bid_liquidation_friction_one_contract'],friction,'friction')
        equal_decimal(row['friction_fraction_of_one_contract_total'],None if friction is None else friction/total,'friction_fraction')
        assert row['displayed_ask_size_capped_quantity'] is None
        assert row['displayed_two_sided_size_capped_quantity'] is None
        for field in ['reserve_is_research_hypothesis_not_actual_broker_fee','arithmetic_only_not_order_or_actual_return','displayed_size_not_guaranteed_execution_capacity']:assert row[field] is True
        if quantity is not None:
            assert quantity>=0 and quantity*total<=500 and (quantity+1)*total>500

def summary_check(summary,rows):
    assert summary['rows']==len(rows)
    assert summary['selection_statuses']==dict(Counter(x['selection_status'] for x in rows))
    assert summary['contract_structures']==dict(Counter(x['analysis']['contract_analysis']['contract_structure'] for x in rows))
    assert summary['primary_quote_quality_passed']==0
    for i,out in enumerate(summary['scenarios']):
        assert out['total_rows']==len(rows)
        values=[x['analysis']['affordability_scenarios'][i]['one_contract_affordable'] for x in rows]
        assert out['one_contract_reference_affordable']==sum(v is True for v in values)
        assert out['one_contract_reference_unaffordable']==sum(v is False for v in values)
        assert out['unknown_price_or_multiplier']==sum(v is None for v in values)
        assert out['not_actual_quote_affordability'] is True
        assert sum(out[k] for k in ['one_contract_reference_affordable','one_contract_reference_unaffordable','unknown_price_or_multiplier'])==len(rows)

def main():
    design=read('study-design.json');validation=read('pre-analysis-validation.json')
    assert validation['failed_tests']==0 and validation['tests_passed']>=62
    for name,digest in validation['bound_sha256'].items():assert sha(name)==digest,('pre_analysis_binding',name)
    selected=read('selected-contracts.json');events=index(read('historical-option-events.json')['rows'],800)
    quotes=index(read('current-indicative-quotes.json')['rows'],80)
    selected_h=index(selected['historical'],800);selected_c=index(selected['current'],80)
    hf=read('historical-reference-results.json');cf=read('current-indicative-results.json')
    historical=hf['rows'];current=cf['rows'];hi=index(historical,800);ci=index(current,80)
    assert set(hi)==set(selected_h)==set(events)
    assert set(ci)==set(selected_c)==set(quotes)
    for container in [hf,cf]:
        for n,h in container['input_sha256'].items():assert sha(n)==h,('output_binding',n)
        assert container['broker_orders_sent']==0
        assert container['strategy_returns_computed'] is False and container['actual_fills'] is False
    current_age_counts=Counter()
    for rows,selected_rows,sources,is_current in [(historical,selected_h,events,False),(current,selected_c,quotes,True)]:
        for row in rows:
            selected_row=selected_rows[key(row)];source=sources[key(row)]
            assert row['selection_status']==selected_row['status']
            for f in ['case_id','date','underlying_symbol','type']:assert row[f]==selected_row[f]
            contract=selected_row.get('contract') or {};a=row['analysis'];ca=a['contract_analysis']
            mult=positive(contract.get('multiplier'))
            equal_decimal(ca['explicit_premium_multiplier'],mult,'explicit_multiplier')
            assert ca['deliverables_independently_verified'] is False
            assert ca['historical_membership_or_listability_verified'] is False
            assert ca['last_trading_time_or_exercise_cutoff_verified'] is False
            assert ca['exercise_assignment_or_residual_share_funding_modeled'] is False
            assert ca['delta'] is None
            assert a['primary_quote_quality_eligible'] is False
            assert a['historical_decision_time_receipt_or_availability_verified'] is False
            assert a['actual_orders_sent']==0 and a['strategy_returns_computed'] is False
            if is_current:
                assert row['source_quote_evidence']==source
                ob=row['observation_assumptions'];q=source.get('quote') or {}
                assert ob['feed']=='indicative' and a['feed']=='indicative'
                assert ob['received_at']==source.get('received_at')
                assert ob['quote_symbol']==contract.get('symbol')
                for f in ['real_provider_bid_ask','market_open_at_observation','size_units_verified','condition_mapping_verified']:assert ob[f] is False
                if source.get('received_at') is not None:
                    assert time_ns(source['received_at'])>=time_ns('2026-10-06T20:15:00Z')
                bid,ask=positive(q.get('bp')),positive(q.get('ap'))
                equal_decimal(a['quote']['bid'],bid,'bid');equal_decimal(a['quote']['ask'],ask,'ask')
                qns,rns=time_ns(q.get('t')),time_ns(source.get('received_at'))
                age=None if qns is None or rns is None else rns-qns
                assert a['age_nanoseconds']==age
                assert a['quote_event_time_ns']==qns and a['observation_received_time_ns']==rns
                equal_decimal(a['age_seconds'],None if age is None else D(age)/10**9,'age')
                midpoint=None if bid is None or ask is None or bid>ask else (bid+ask)/2
                equal_decimal(a['midpoint'],midpoint,'midpoint')
                equal_decimal(a['spread_fraction_of_midpoint'],None if midpoint is None else (ask-bid)/midpoint,'spread')
                assert a['affordable_primary_quote_available'] is False
                assert a['market_clock_source_independently_verified'] is False
                assert a['quality_reasons']==[k for k,v in a['quality_criteria'].items() if v is not True]
                assert a['quality_criteria']['feed_is_opra'] is False
                assert a['quality_criteria']['age_nonnegative_and_at_most_five_seconds'] is (None if age is None else 0<=age<=5*10**9)
                current_age_counts['unknown' if age is None else 'future' if age<0 else 'fresh_5s' if age<=5*10**9 else 'stale_5s']+=1
            else:
                assert row['source_event_evidence']==source
                first=source.get('first_trade');ask=positive(first.get('p')) if isinstance(first,dict) else None;bid=None
                equal_decimal(a['reference_price'],ask,'historical_first_trade_proxy')
                assert a['is_executable_premium_reference'] is False
                assert a['quote_age_spread_size_or_exit_coverage_verified'] is False
                assert row['first_returned_record_condition_and_correction_semantics_not_independently_verified'] is True
                assert row['historical_metadata_was_retrieved_after_sample_date'] is True
                expiry=row['expiry_calendar_reference']
                assert expiry['expiration_on_sample_date'] is (None if not contract else contract['expiration_date']==row['date'])
                assert expiry['expired_before_sample_date'] is (None if not contract else contract['expiration_date']<row['date'])
                assert expiry['historical_listing_or_tradability_certified'] is False
            scenarios(a,mult,ask,bid,is_current)
    summary=read('run-summary.json')
    summary_check(summary['historical'],historical);summary_check(summary['current_indicative'],current)
    assert summary['all_ytd_sessions_indexed']==190 and summary['historical_sessions_sampled']==10 and summary['historical_sessions_not_sampled']==180
    assert summary['broad_directory_current_optionable_symbols']==3916 and summary['sample_underlyings']==40
    assert summary['historical_quote_rows_verified']==0 and summary['strategy_wealth_paths_run']==0 and summary['broker_orders_sent']==0
    assert summary['new_scheduler_deployed'] is False
    assert summary['current_quote_quality_failures']==dict(Counter(reason for r in current for reason in r['analysis']['quality_reasons']))
    assert len(summary['monthly'])==10
    for month in summary['monthly']:
        rows=[x for x in historical if x['date'][:7]==month['month']]
        assert len(rows)==80;summary_check(month,rows)
        for outfield,infield in [('rows_with_bar_records','bars_count'),('rows_with_trade_records','trades_count')]:assert month[outfield]==sum(records_observed(x['source_event_evidence'].get(infield)) for x in rows)
        assert month['historical_bid_ask_rows_verified']==0
    for name,h in summary['output_sha256'].items():assert sha(name)==h
    correction=read('summary-null-correction.json')
    assert correction['first_real_arithmetic_already_run'] is True and correction['initial_summary_completed'] is False
    for name,h in correction['preserved_files'].items():assert sha('correction-history/'+name)==h,('correction_history',name)
    assert correction['new_code_sha256']==sha('run_feasibility.py')
    for name in ['historical-reference-results.json','current-indicative-results.json']:
        old=read('correction-history/'+name);new=read(name)
        assert old['rows']==new['rows'],('summary_fix_changed_arithmetic_rows',name)
        assert time_ns(old['generated_at'])<time_ns(validation['registered_at'])<time_ns(new['generated_at'])
    original_validation=read('correction-history/pre-analysis-validation.json')
    assert time_ns(original_validation['registered_at'])<time_ns(read('correction-history/historical-reference-results.json')['generated_at'])
    assert validation['actual_budget_math_started'] is True
    missing_count_rows=sum(r['source_event_evidence'].get('bars_count') is None for r in historical)
    assert missing_count_rows>0
    for r in historical:
        if r['source_event_evidence'].get('trades_count') is None:
            assert r['analysis']['reference_price'] is None
            assert all(x['whole_contracts_budget_only'] is None for x in r['analysis']['affordability_scenarios'])
    text=json.dumps([hf,cf])
    for forbidden in ['account_id','underlying_asset_id','"id":',chr(47)+'workspace'+chr(47),chr(47)+'tmp'+chr(47)]:
        assert forbidden not in text,('public_data_leak',forbidden)
    names=['study-design.json','pre-analysis-validation.json','selected-contracts.json','historical-option-events.json','current-indicative-quotes.json','historical-reference-results.json','current-indicative-results.json','run-summary.json','option_feasibility.py','run_feasibility.py','summary-null-correction.json',Path(__file__).name]
    result={'checked_at':datetime.now(timezone.utc).isoformat(),'status':'pass','historical_rows_checked':800,'current_indicative_rows_checked':80,'fee_scenarios_checked':2640,'monthly_groups_checked':10,'current_quote_age_counts':dict(current_age_counts),'independent_of_production_analyzer':True,'source_chain_audit_separate':'independent-source-audit.json','current_primary_quote_quality_passed':0,'historical_bid_ask_verified':0,'strategy_wealth_paths_or_orders':0,'summary_null_fix':{'all_880_rows_identical_to_first_execution':True,'missing_bar_count_rows_preserved':missing_count_rows,'first_arithmetic_preceded_second_binding':True,'unknown_trade_count_not_imputed_as_zero_price':True},'sha256':{n:sha(n) for n in names},'limitations':['Checks source-bound outputs and exact arithmetic, not historical contract membership, actual OPRA quotes, fills or strategy performance.','Independent full-page source reconstruction is reported separately.']}
    (ROOT/'independent-actual-audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
