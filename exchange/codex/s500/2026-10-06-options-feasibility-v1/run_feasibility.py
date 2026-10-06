"""Keep current indicative arithmetic separate from historical trade-price proxies."""
from pathlib import Path
from collections import Counter,defaultdict
from datetime import datetime,timezone
import hashlib,json

ROOT=Path(__file__).resolve().parent
def read(n):return json.loads((ROOT/n).read_text())
def sha(n):return hashlib.sha256((ROOT/n).read_bytes()).hexdigest()
def save(n,o):
    p=ROOT/n;assert not p.exists(),n;p.write_text(json.dumps(o,separators=(',',':'))+'\n')
def key(r):return r['case_id'],r['type']

def main():
    from option_feasibility import evaluate_quote,evaluate_trade_price_proxy
    validation=read('pre-analysis-validation.json')
    assert validation['failed_tests']==0 and validation['tests_passed']>0
    for n,expected in validation['bound_sha256'].items():assert sha(n)==expected,n
    design=read('study-design.json');selected=read('selected-contracts.json')
    assert len(selected['historical'])==800 and len(selected['current'])==80
    events={key(r):r for r in read('historical-option-events.json')['rows']}
    quotes={key(r):r for r in read('current-indicative-quotes.json')['rows']}
    assert len(events)==800 and len(quotes)==80
    policy={'budget':'500','roundtrip_reserves_per_contract':['0','0.10','1'],
      'max_age_seconds':'5','max_relative_spread_mid':'0.10','study_cutoff_date':'2026-10-05'}
    historical=[];current=[]
    for c in selected['historical']:
        e=events[key(c)];trade=e.get('first_trade');price=trade.get('p') if isinstance(trade,dict) else None
        result=evaluate_trade_price_proxy(c.get('contract') or {},price,
          {'received_at':None,'feed':'historical_trade_api_documented_default'},policy)
        historical.append({'case_id':c['case_id'],'date':c['date'],'underlying_symbol':c['underlying_symbol'],'type':c['type'],
          'selection_status':c['status'],'source_event_evidence':e,'analysis':result,
          'first_returned_record_condition_and_correction_semantics_not_independently_verified':True,
          'historical_metadata_was_retrieved_after_sample_date':True,
          'expiry_calendar_reference':{
            'expiration_on_sample_date':None if not c.get('contract') else c['contract']['expiration_date']==c['date'],
            'expired_before_sample_date':None if not c.get('contract') else c['contract']['expiration_date']<c['date'],
            'timestamp_role':'Calendar comparison only. Historical receipt remains unknown; event timestamp is not observation time.',
            'historical_listing_or_tradability_certified':False}})
    for c in selected['current']:
        q=quotes[key(c)];contract=c.get('contract') or {};symbol=contract.get('symbol')
        observation={'received_at':q.get('received_at'),'feed':'indicative','quote_symbol':symbol,
          'real_provider_bid_ask':False,'market_open_at_observation':False,
          'market_closed_basis':'Recorded Oct6 retrieval after16:15ET, beyond regular stock/ETF option sessions; not a live market clock query.',
          'quote_size_units':'unverified_alpaca_mapping','size_units_verified':False,'condition_mapping_verified':False}
        result=evaluate_quote(contract,q.get('quote'),observation,policy)
        assert result['primary_quote_quality_eligible'] is False
        current.append({'case_id':c['case_id'],'date':c['date'],'underlying_symbol':c['underlying_symbol'],'type':c['type'],
          'selection_status':c['status'],'source_quote_evidence':q,'observation_assumptions':observation,'analysis':result})
    for n,rows in [('historical-reference-results.json',historical),('current-indicative-results.json',current)]:
        save(n,{'generated_at':datetime.now(timezone.utc).isoformat(),'rows':rows,
          'input_sha256':{n:sha(n) for n in ['study-design.json','selected-contracts.json','historical-option-events.json',
            'current-indicative-quotes.json','option_feasibility.py','pre-analysis-validation.json','run_feasibility.py']},
          'broker_orders_sent':0,'strategy_returns_computed':False,'actual_fills':False})
    def summarize(rows):
        scenarios=[]
        for i,reserve in enumerate(policy['roundtrip_reserves_per_contract']):
            values=[r['analysis']['affordability_scenarios'][i] for r in rows]
            scenarios.append({'roundtrip_reserve_per_contract':reserve,'total_rows':len(rows),
              'one_contract_reference_affordable':sum(v['one_contract_affordable'] is True for v in values),
              'one_contract_reference_unaffordable':sum(v['one_contract_affordable'] is False for v in values),
              'unknown_price_or_multiplier':sum(v['one_contract_affordable'] is None for v in values),
              'not_actual_quote_affordability':True})
        return {'rows':len(rows),'selection_statuses':dict(Counter(r['selection_status'] for r in rows)),
          'contract_structures':dict(Counter(r['analysis']['contract_analysis']['contract_structure'] for r in rows)),
          'scenarios':scenarios,'primary_quote_quality_passed':sum(r['analysis']['primary_quote_quality_eligible'] for r in rows)}
    monthly=[]
    for month in sorted({r['date'][:7] for r in historical}):
        group=[r for r in historical if r['date'].startswith(month)]
        monthly.append({'month':month,**summarize(group),
          'rows_with_bar_records':sum(r['source_event_evidence'].get('bars_count',0) is not None and r['source_event_evidence']['bars_count']>0 for r in group),
          'rows_with_trade_records':sum(r['source_event_evidence'].get('trades_count',0) is not None and r['source_event_evidence']['trades_count']>0 for r in group),
          'historical_bid_ask_rows_verified':0})
    summary={'generated_at':datetime.now(timezone.utc).isoformat(),'period':design['period'],'current_snapshot_date':design['current_snapshot_date'],
      'broad_directory_current_optionable_symbols':3916,'sample_underlyings':40,'monthly_sample_dates':design['history_dates'],
      'all_ytd_sessions_indexed':190,'historical_sessions_sampled':10,'historical_sessions_not_sampled':180,
      'historical':summarize(historical),'current_indicative':summarize(current),'monthly':monthly,
      'current_quote_quality_failures':dict(Counter(reason for r in current for reason in r['analysis']['quality_reasons'])),
      'historical_quote_capability':'No official historical option quote route identified; explicit queriedroute404. Bars/trades not substitutions.',
      'current_opra_blocker':'HTTP403: OPRA agreement is not signed; no replay until authorisation state change.',
      'historical_quote_rows_verified':0,'strategy_wealth_paths_run':0,'broker_orders_sent':0,'new_scheduler_deployed':False,
      'interpretation':'Reference-price budget arithmetic only. A pass is not a verified tradable ask or order; unknown is not zero wealth. No500-to10000 evidence.',
      'output_sha256':{n:sha(n) for n in ['historical-reference-results.json','current-indicative-results.json']}}
    save('run-summary.json',summary)
    print(json.dumps({k:summary[k] for k in ['historical','current_indicative','historical_quote_rows_verified','strategy_wealth_paths_run']},indent=2))

if __name__=='__main__':main()
