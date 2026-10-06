"""Run the frozen technical opening-range grid and preserve every result."""
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter, defaultdict
from itertools import product
from statistics import mean, median
import hashlib
import json

ROOT=Path(__file__).resolve().parent
PARENT=ROOT.parent/'s500-ep-event-v2-20261006'
PATHS=ROOT.parent/'s500-ep-paths-20261006'
NEWS=ROOT.parent/'s500-news-20261006'
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(n,o):(ROOT/n).write_text(json.dumps(o,separators=(',',':'))+'\n')

def group_summary(rows):
    groups=defaultdict(list)
    for r in rows:groups[(r['family'],r['data_mode'],r['variant_id'])].append(r)
    out=[]
    for (family,mode,variant),items in sorted(groups.items()):
        entered=[r for r in items if any(t['side']=='BUY' for t in r['trades'])]
        closed=[r for r in entered if r['status']=='complete' and any(t['side']=='SELL' for t in r['trades'])]
        pnls=[float(r['profit']) for r in closed]
        out.append({'family':family,'data_mode':mode,'variant_id':variant,'cases':len(items),
          'statuses':dict(Counter(r['status'] for r in items)),'entered':len(entered),'closed':len(closed),
          'wins':sum(x>0 for x in pnls),'losses':sum(x<0 for x in pnls),'flat':sum(x==0 for x in pnls),
          'mean_closed_pnl':mean(pnls) if pnls else None,'median_closed_pnl':median(pnls) if pnls else None,
          'best_closed_pnl':max(pnls) if pnls else None,'worst_closed_pnl':min(pnls) if pnls else None,
          'no_trade_complete':sum(r['status']=='complete' and not r['trades'] for r in items),
          'failure_reasons':dict(Counter(r['failure']['reason'] for r in items if r['failure'])),
          'independent_cases_not_continuous_wealth':True,'complete_cases_are_coverage_selected':True})
    return out

def main():
    from early_engine import simulate_case,simulate_portfolio,simulate_cohort_portfolio
    assert not (ROOT/'isolated-results.json').exists(),'Never overwrite an earlier run'
    design=read(ROOT/'study-design.json');validation=read(ROOT/'pre-run-validation.json')
    parent_roots={'news':NEWS,'paths':PATHS,'event_v2':PARENT}
    for name,expected in design['input_sha256'].items():
        group,relative=name.split('/',1)
        assert sha(parent_roots[group]/relative)==expected,name
    assert validation['failed_tests']==0 and validation['tests_passed']>0
    for name,expected in validation['bound_sha256'].items():assert sha(ROOT/name)==expected,name
    assert validation['design_sha256']==sha(ROOT/'study-design.json')
    gate=read(ROOT/'market-gates.json')
    conflicts=read(ROOT/'input-conflicts.json').get('candidate_flags',{})
    for data in gate['families'].values():
        for c in data['rows']:
            if conflicts.get(c['candidate_id'],{}).get('source_revision_conflict'):
                c['source_revision_conflict']=True
                c['source_revision_conflict_details']=conflicts[c['candidate_id']]
    market=read(ROOT/'holding-market.json');daily=read(ROOT/'daily-market-private.json')
    actions=read(ROOT/'corporate-events.json');calendar=read(ROOT/'holding-input-design.json')['full_calendar']
    sessions=read(ROOT/'verified-symbol-sessions.json');gap=read(PARENT/'gap-evidence.json')
    coverage=read(PATHS/'portfolio-coverage.json')
    # Descriptive news evidence never changes any gate, ranking or account.
    news={r['candidate_id']:{'primary_gate_pass':r['primary_gate_pass'],'status':r['primary_news_status']} for r in read(NEWS/'candidate-readiness.json')['candidates']}
    for r in read(PATHS/'all-new-primary-reviews.json')['reviews']:
        news[r['candidate_id']]={'primary_gate_pass':r['pass_registered_news_gate'],'status':r['news_status']}
    variants=[{'exit_mode':'fixed10','fraction':f,'cost_bps':c,'variant_id':'fixed10__f'+str(f)+'__c'+str(c)} for f,c in product((.5,1.0),(25,100))]
    started=datetime.now(timezone.utc).isoformat()
    expected_isolated=sum(d['counts']['pass'] for d in gate['families'].values())*len(variants)*len(design['modes'])
    input_hash={n:sha(ROOT/n) for n in ['study-design.json','market-gates.json','holding-input-design.json','source-verification.json','verified-symbol-sessions.json','input-conflicts.json','ep_engine.py','ep_event_engine.py','early_engine.py','early_gates.py','pre-run-validation.json','run_early_paths.py']}
    all_cases=[];portfolios=[];registry=[]
    for family_spec in design['families']:
        family=family_spec['id'];n=family_spec['opening_minutes'];rows=gate['families'][family]['rows']
        cases=sorted([c for c in rows if c['market_gate_pass'] is True],key=lambda c:c['candidate_id'])
        for c in rows:
            registry.append({k:c.get(k) for k in ['candidate_id','date','symbol','family','market_gate_status','market_gate_pass']}|{
              'descriptive_news':news[c['candidate_id']],'news_used_for_selection':False,
              'member_ready_diagnostic_cohort':c['market_gate_pass'] is True})
        for mode in design['modes']:
            kwargs={'mode':mode,'gap_evidence':gap,'verified_symbol_sessions':sessions}
            for case in cases:
                for variant in variants:
                    result=simulate_case(case,variant,market,daily,calendar,actions,**kwargs)
                    result.update(family=family,variant_id=variant['variant_id'],date=case['date'],symbol=case['symbol'],descriptive_news=news[case['candidate_id']])
                    all_cases.append(result)
                if len(all_cases)%120==0:
                    progress={'isolated_done':len(all_cases),'isolated_total':expected_isolated,'updated_at':datetime.now(timezone.utc).isoformat()}
                    save('progress.json',progress);print(json.dumps(progress),flush=True)
            for scope in design['registered_outputs']['portfolios_per_family_mode_variant']:
                for variant in variants:
                    if scope=='retrospective_ready_cohort':
                        result=simulate_cohort_portfolio(cases,variant,market,daily,calendar,actions,opening_range_minutes=n,cohort_name='retrospective_market_pass',**kwargs)
                    else:
                        result=simulate_portfolio(rows,variant,market,daily,calendar,actions,opening_range_minutes=n,
                          coverage=coverage if scope=='full_universe_strict' else {'cohort_complete':False},**kwargs)
                    result.update(family=family,universe_scope=scope,variant_id=variant['variant_id'])
                    portfolios.append(result)
    assert len(all_cases)==expected_isolated and len(portfolios)==72 and len(registry)==3237
    completed=datetime.now(timezone.utc).isoformat()
    for name,rows in [('isolated-results.json',all_cases),('portfolio-results.json',portfolios)]:
        save(name,{'started_at':started,'finished_at':completed,'input_sha256':input_hash,'results':rows,'broker_orders_sent':0,'actual_fills':False})
    save('all-candidate-registry.json',{'rows':registry,'total_family_candidate_rows':3237,'original_unique_candidates':1079})
    parent={(r['candidate_id'],r['variant_id'],r['data_mode']):r for r in read(PARENT/'isolated-event-results.json')['results']}
    compared=[];mismatches=[]
    keys=['status','profit','ending_equity','cash_settled','trades','daily','failure']
    for r in all_cases:
        if r['family']!='OR30':continue
        old=parent[(r['candidate_id'],r['variant_id'],r['data_mode'])]
        changed=[k for k in keys if r[k]!=old[k]]
        compared.append({'candidate_id':r['candidate_id'],'variant_id':r['variant_id'],'data_mode':r['data_mode'],'changed_fields':changed})
        if changed:mismatches.append(compared[-1])
    save('or30-parent-regression.json',{'compared':len(compared),'financial_fields':keys,'mismatches':mismatches,'checks':compared})
    summary={'generated_at':completed,'registered_at':design['registered_at'],'total_results':len(all_cases)+len(portfolios),
      'isolated_results':len(all_cases),'portfolio_results':len(portfolios),'families':{f:d['counts'] for f,d in gate['families'].items()},
      'isolated_groups':group_summary(all_cases),'isolated_statuses':dict(Counter(r['status'] for r in all_cases)),
      'portfolio_statuses':dict(Counter(r['status'] for r in portfolios)),
      'portfolios':[{k:r.get(k) for k in ['family','data_mode','universe_scope','variant_id','status','profit','ending_equity','cash_settled','terminal_receivables','failure','milestones','observed_fresh_daily_liquidation_drawdown','daily_drawdown_evaluated_snapshot_count','daily_drawdown_excluded_snapshot_count','terminal_equity_certified','skipped_minute_count']}|{
        'buys':sum(t['side']=='BUY' for t in r['trades']),'sales':sum(t['side']=='SELL' for t in r['trades'])} for r in portfolios],
      'or30_parent_financial_regressions':{'compared':len(compared),'mismatches':len(mismatches)},
      'input_sha256':input_hash,'output_sha256':{n:sha(ROOT/n) for n in ['isolated-results.json','portfolio-results.json','all-candidate-registry.json','or30-parent-regression.json']},
      'broker_orders_sent':0,'new_capital_injections':0,'new_scheduler_deployed':False,
      'exploratory_previously_observed_history':True,'not_point_in_time_complete_market':True,'news_filter_used':False}
    save('run-summary.json',summary)
    print(json.dumps({k:summary[k] for k in ['total_results','isolated_statuses','portfolio_statuses','or30_parent_financial_regressions']},indent=2),flush=True)
    assert not mismatches,'Preserve run for investigation; do not silently overwrite'

if __name__=='__main__':main()
