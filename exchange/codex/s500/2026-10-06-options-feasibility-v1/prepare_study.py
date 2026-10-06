"""Freeze a broad, bounded option data feasibility sample before its data reads."""
from pathlib import Path
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from statistics import median
from collections import defaultdict
from zoneinfo import ZoneInfo
import json,hashlib

ROOT=Path(__file__).resolve().parent
BROAD=ROOT.parent/'s500-broad-20261006'
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(n,o):
    p=ROOT/n;assert not p.exists(),n;p.write_text(json.dumps(o,separators=(',',':'))+'\n')

def main():
    universe=read(BROAD/'universe.json');eligible=[r for r in universe if 'has_options' in r.get('attributes',[])]
    calendar=read(ROOT.parent/'s500-ep-paths-20261006/holding-input-design.json')['full_calendar']
    dates=[r['date'] for r in calendar];pre=[d for d in dates if d<'2026-01-01'][-20:]
    ytd=[d for d in dates if '2026-01-01'<=d<='2026-10-05']
    monthly={}
    for d in ytd:monthly.setdefault(d[:7],d)
    sample=list(monthly.values());refdays={d:dates[dates.index(d)-1] for d in sample}
    inventory=read(ROOT.parent/'s500-orb-20261006/ep-candidate-inventory.json')
    daily={};sources={};wanted={r['symbol'] for r in eligible}|{'SPY','QQQ','MU','STX','WDC','SNDK','NTAP','RMBS','SIMO','P'}
    for path in sorted((BROAD/'raw').glob('batch-*.json')):
        key='raw/'+path.name;sources[key]=sha(path);assert sources[key]==inventory['inputs_sha256'][key]
        for symbol,bars in read(path)['bars'].items():
            if symbol not in wanted:continue
            assert symbol not in daily
            daily[symbol]={datetime.fromisoformat(b['t'].replace('Z','+00:00')).astimezone(ZoneInfo('America/New_York')).date().isoformat():b for b in bars}
    ranks=[]
    for r in eligible:
        symbol=r['symbol'];series=daily.get(symbol,{})
        if not all(d in series and Decimal(str(series[d]['c']))>0 and Decimal(str(series[d]['v']))>=0 for d in pre):continue
        liquidity=median(Decimal(str(series[d]['c']))*Decimal(str(series[d]['v'])) for d in pre)
        ranks.append((liquidity,symbol))
    ranks.sort(key=lambda x:(-x[0],x[1]));top=[s for _,s in ranks[:24]]
    others=sorted((r['symbol'] for r in eligible if r['symbol'] not in top),key=lambda s:hashlib.sha256(('s500-options-feasibility-v1|'+s).encode()).hexdigest())[:8]
    requested_storage=['MU','STX','WDC','SNDK','NTAP','RMBS','SIMO','P'];controls=['SPY','QQQ']
    symbols=sorted(set(top+others+requested_storage+controls));selected=[]
    by_symbol={r['symbol']:r for r in universe}
    for s in symbols:
        roles=[]
        for role,group in [('prior20_liquidity_top24',top),('fixed_hash8',others),('user_storage_interest',requested_storage),('index_etf_control',controls)]:
            if s in group:roles.append(role)
        selected.append({'symbol':s,'roles':roles,'current_directory_member':s in by_symbol,'has_options_current':s in {r['symbol'] for r in eligible},
          'pre2026_median_dollar_volume':next((str(v) for v,sym in ranks if sym==s),None),
          'historical_membership_proven':False})
    references=[]
    for day in sample:
        for s in symbols:
            raw=daily.get(s,{}).get(refdays[day]);value=None
            if raw and Decimal(str(raw['c']))>0:value=str(raw['c'])
            references.append({'case_id':day+'__'+s,'date':day,'symbol':s,'prior_session':refdays[day],'prior_close_reference':value,
              'reference_is_vendor_raw_prior_close_not_split_or_identity_certification':True})
    save('sample-universe.json',{'selected':selected,'sample_symbols':symbols,'current_optionable_directory_count':len(eligible),
      'selection_source_preperiod':pre,'ranks_with_complete_preperiod':len(ranks),'all_current_optionable_symbols':[r['symbol'] for r in eligible],
      'selection_not_based_on_option_prices_or_strategy_returns':True,'underlying_references':references,
      'source_sha256':{'universe.json':sha(BROAD/'universe.json'),'daily_raw':sources}})
    save('session-coverage-plan.json',{'period':['2026-01-02','2026-10-05'],'all_sessions':[
      {'date':d,'status':'planned_capability_sample' if d in sample else 'not_sampled','sample_symbols_count':len(symbols) if d in sample else 0} for d in ytd],
      'sample_days':sample,'total_sessions':len(ytd),'sampled_sessions':len(sample),'not_a_full_year_strategy_test':True})
    study={'id':'s500_options_execution_feasibility_v1','registered_at':datetime.now(timezone.utc).isoformat(),'status':'frozen_before_broad_contract_history_and_reference_math',
      'parent_commit':'ba999fff6c09ee41c45dffaf409c6d53375d9008','period':['2026-01-02','2026-10-05'],'current_snapshot_date':'2026-10-06',
      'known_capabilities_before_registration':'Expired/current SPY metadata200; historicalbars/trades200 after removingunsupportedfeed; hypotheticalhistoricalquotes404; latestOPRA403 agreementnot signed; latestindicative200. Preserveinitial400anddenials.',
      'purpose':'Data/whole-contract affordability feasibility, NOT a trading strategy, directional forecast, wealth backtest or500to10000 success test.',
      'underlying_scope':{'directory_optionable':len(eligible),'preperiod_liquidity_top':24,'fixed_hash_sample':8,'storage_overlay':requested_storage,'controls':controls,'selected_count':len(symbols),'sample_symbols':symbols,
        'limitations':'Currenthas_options/directory is not historicaloptionability. Monthlysample probes coverage, not whole-market orwholeyear strategy. Hashsample includesmissingpriorhistory, retainedunknown.'},
      'history_dates':sample,'history_contract_scope':'For every sampled underlying/date list inactive contracts with expiration in[date,min(date+7calendar days,Oct5)], show_deliverables=true, limit1000, maximum10pages each. Completepagination or explicitpartialunknown. No identity/status-at-past claim.',
      'current_contract_scope':'For each sampled underlying list active expirationOct9 contracts, samepages/deliverables. This is currentdiscovery separatefromhistoricalsample.',
      'contract_selection':'For eachhistoricalcase first availableexpiration inwindow, then eachcall/put closeststrike to prior-session rawvendorclose, symboltiebreak; missingpriorclose orcontract retainedunknown. Currentcase neareststrike toOct5priorclose. No selectionbyoptionprice/quotequality/returns.',
      'historical_requests':'For selected historicalcontracts on eachsampleday, batch<=100symbols, 10:00:00 through10:00:59.999999999 ET, historical1Minbars andtrades withdocumentedparams NOfeed, limit10000, max20pages. Retainallbars/trades asprivateinputs, deriveavailabilityandfirsttradepriceproxy. No historicalbidask inferred.',
      'current_quote_requests':'Batchselectedcurrentcontracts<=100 tolatestindicative only; OPRA alreadydenied,no repeatuntilauthstatechange. indicativeisnotactualOPRA; returnedtimestamp/age/statusretained. Afterhoursobservationnotexecutable.',
      'analyzer_policy':{'cash_usd':'500','roundtrip_reserve_per_contract_usd':['0','0.10','1.00'],'fees_are_hypothetical_sensitivity_not_actual_history':True,
        'multiplier':'Useonlyexplicitpositivefinitecontractmultiplier, never inferfromsizeorOCCsymbol; adjusted/deliverablesuncertainty separatelyreported.',
        'quantity':'floor500/(ask*verifiedpremium_multiplier+roundtrip_reserve); integercontracts. No borrowing/fractionalcontracts. Retainlowersizebound onlyifunitmappingverified.',
        'illustrative_only':'Indicativeaskandhistoricaltradeprice affordabilitycounterfactuals separatefromrealquotes; neither implies fills orprofit.',
        'primary_quote_quality':'Requireoprafeed, timestampnotfuture, <=5secageatobservation,positiveuncrossedbidask,positiveintegerbothsizes, verifiedconditionandunitmapping, blank/Acondition, spread/mid<=10%, verifiedstandarddeliverable/expiryvalid andmarket_open_at_observationtrue. Hypotheticalqualityconvention, not a liveconfig/riskrule.',
        'precision':'StrictISO timezoneaware nanosecond integer comparisons, no microsecondtruncation. No fallback frominvalidlatest to olderquote.',
        'missing':'Absent/denied/invalid/unverified remainunknownorblocked with reason, not0contractwealthorstrategyfailure.'},
      'cost_source_policy':'Readcurrentofficialfeeversion includingrounding; donotapplyOct2026feePDF toearly2026withoutversionproof. Hypotheticalreserve remains distinct fromchargedfees.',
      'expiry_and_spreads':'Thisphase onlylongpremiumaffordability math. Noexercise/assignment/expirationprofit, automaticexpiryexit, marginorpseudo-riskless spread assumption. Futureexecutionmodelneeds actualdeliverable,exerciseavoidance,settlementandmultilegquotes.',
      'outputs':'Allsamplecells/statuses plusall190sessioncoverage, allselectedcontracts includingnoevent/unknowns, requesthashes/code/tests/independentaudit/limitations. No simulatedFILL orwealthpath.',
      'input_sha256':{'sample-universe.json':sha(ROOT/'sample-universe.json'),'session-coverage-plan.json':sha(ROOT/'session-coverage-plan.json'),
        'capability-plan.json':sha(ROOT/'capability-plan.json'),'capability-results.json':sha(ROOT/'capability-results.json'),
        'corrected-probe-plan.json':sha(ROOT/'corrected-probe-plan.json'),'corrected-probe-results.json':sha(ROOT/'corrected-probe-results.json'),'prepare_study.py':sha(Path(__file__))},
      'broker_orders_sent':0,'account_requests':0,'new_scheduler_deployed':False}
    save('study-design.json',study)
    # Private small prior-close references for current listings; no full daily export.
    save('current-underlying-references.json',{'asof_prior_close':'2026-10-05','rows':[{'symbol':s,'prior_session':'2026-10-05',
      'prior_close_reference':str(daily[s]['2026-10-05']['c']) if daily.get(s,{}).get('2026-10-05') else None} for s in symbols]})
    print(json.dumps({'registered_at':study['registered_at'],'symbols':symbols,'count':len(symbols),'monthly_samples':len(sample),'historical_contract_cases':len(sample)*len(symbols),'current_directory_optionable':len(eligible)}))

if __name__=='__main__':main()
