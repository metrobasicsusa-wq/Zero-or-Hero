"""Run every registered variant; primary classifications are locked before results."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from statistics import mean, median
import hashlib
import json
import time

from ep_engine import simulate_case, simulate_portfolio

ROOT=Path(__file__).resolve().parent
PRIOR=ROOT.parent/'s500-news-20261006'


def read(path): return json.loads(Path(path).read_text())
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(name,data): (ROOT/name).write_text(json.dumps(data,separators=(',',':'))+'\n')


def evidence_join():
    base=read(PRIOR/'candidate-readiness.json')['candidates']
    evidence={r['candidate_id']:{'pass_registered_news_gate':r['primary_gate_pass'],
        'news_status':r['primary_news_status'],'all_possible_registered_catalysts_excluded':False,
        'source_phase':'carried_parent'} for r in base}
    for r in read(ROOT/'all-new-primary-reviews.json')['reviews']:
        evidence[r['candidate_id']]={**r,'source_phase':'new_full_primary_stage',
            'all_possible_registered_catalysts_excluded':False}
    return evidence


def summarize(rows):
    groups=defaultdict(list)
    for r in rows: groups[(r['variant_id'],r['primary_news_gate'] is True)].append(r)
    output=[]
    for (variant,news),items in sorted(groups.items()):
        entered=[r for r in items if any(t['side']=='BUY' for t in r['trades'])]
        closed=[r for r in entered if r['status']=='complete' and any(t['side']=='SELL' for t in r['trades'])]
        pnl=[float(r['profit']) for r in closed]
        output.append({'variant_id':variant,'news_primary_gate_pass':news,'cases':len(items),
            'status_counts':dict(Counter(r['status'] for r in items)),
            'entered_cases':len(entered),'closed_model_trades':len(closed),
            'profitable_closed_cases':sum(x>0 for x in pnl),'losing_closed_cases':sum(x<0 for x in pnl),
            'mean_closed_case_pnl':mean(pnl) if pnl else None,'median_closed_case_pnl':median(pnl) if pnl else None,
            'best_closed_case_pnl':max(pnl) if pnl else None,'worst_closed_case_pnl':min(pnl) if pnl else None,
            'failure_reasons':dict(Counter(r['failure']['reason'] for r in items if r['failure'])),
            'skipped_intents':dict(Counter(d['status'] for r in items for d in r['decisions'] if d.get('status','').startswith('skipped'))),
            'not_continuous_wealth':True,'complete_cases_are_a_selected_coverage_subset':True})
    return output


def main():
    design=read(ROOT/'study-design.json');lock=read(ROOT/'primary-classification-lock.json')
    assert lock['all68_dispositions_sha256']==sha(ROOT/'all-new-primary-reviews.json')
    assert lock['classification_code_sha256']==sha(ROOT/'complete_primary_reviews.py')
    original=read(PRIOR/'market-gates.json')['rows']
    cases=sorted((x for x in original if x['market_gate_pass'] is True),key=lambda x:x['candidate_id'])
    planned={x['candidate_id'] for x in read(ROOT/'holding-input-design.json')['cases']}
    assert len(cases)==73 and planned=={x['candidate_id'] for x in cases}
    market=read(ROOT/'holding-market.json');daily=read(ROOT/'daily-market-private.json')
    calendar=read(ROOT/'holding-input-design.json')['full_calendar'];actions=read(ROOT/'corporate-events.json')
    evidence=evidence_join()
    variants=[{'exit_mode':mode,'fraction':fraction,'cost_bps':cost,
        'variant_id':mode+'__f'+str(fraction)+'__c'+str(cost)}
        for mode,fraction,cost in product(('fixed10','ma10_max63'),(.5,1.0),(25,50,100))]
    source_names=['study-design.json','primary-classification-lock.json','all-new-primary-reviews.json',
        'holding-input-manifest.json','holding-market.json','daily-market-private.json','corporate-events.json',
        'repaired-market-gates.json','ep_engine.py','run_paths.py']
    hashes={n:sha(ROOT/n) for n in source_names}
    started=datetime.now(timezone.utc).isoformat();rows=[]
    for i,case in enumerate(cases,1):
        for variant in variants:
            result=simulate_case(case,variant,market,daily,calendar,actions)
            result.update(variant_id=variant['variant_id'],date=case['date'],symbol=case['symbol'],
                primary_news_gate=evidence[case['candidate_id']]['pass_registered_news_gate'],
                primary_news_status=evidence[case['candidate_id']]['news_status'],
                entry_quote_1s_accepted=next(r['quote_1s_accepted'] for r in read(PRIOR/'candidate-readiness.json')['candidates'] if r['candidate_id']==case['candidate_id']))
            rows.append(result)
        if i%10==0 or i==len(cases):
            progress={'cases_done':i,'cases_total':73,'variant_results':len(rows),'updated_at':datetime.now(timezone.utc).isoformat()}
            save('paths-progress.json',progress);print(json.dumps(progress),flush=True)
    save('isolated-case-results.json',{'generated_at':datetime.now(timezone.utc).isoformat(),'started_at':started,
        'input_sha256':hashes,'case_count':73,'variant_count':12,'results_count':len(rows),'results':rows,
        'all_cases_independent500_not_continuous_wealth':True,'orders_sent':0})
    portfolios=[];candidates=read(ROOT/'repaired-market-gates.json')['rows'];coverage=read(ROOT/'portfolio-coverage.json')
    for universe in ['full_universe_strict','original_cohort_conditional']:
        for variant in variants:
            result=simulate_portfolio(candidates,variant,market,daily,calendar,actions,evidence,
                coverage=coverage if universe=='full_universe_strict' else {'cohort_complete':False})
            result.update(variant_id=variant['variant_id'],universe_scope=universe)
            portfolios.append(result)
    save('portfolio-results.json',{'generated_at':datetime.now(timezone.utc).isoformat(),'input_sha256':hashes,
        'original_registered_full_universe_variants':12,'additional_conditional_scope_diagnostics':12,
        'results':portfolios,'broker_orders_sent':0})
    save('decision-evidence.json',{'candidate_count':len(candidates),'rows':[{'candidate_id':c['candidate_id'],
        'date':c['date'],'symbol':c['symbol'],'market_gate_pass':c['market_gate_pass'],
        'market_gate_status':c['market_gate_status'],'signal':c.get('signal'),
        'news_gate_pass':evidence[c['candidate_id']]['pass_registered_news_gate'],
        'news_status':evidence[c['candidate_id']]['news_status'],
        'absence_of_qualifying_news_proven':False} for c in candidates]})
    summary={'generated_at':datetime.now(timezone.utc).isoformat(),'isolated_cases':73,'isolated_variant_results':len(rows),
        'isolated_status_counts':dict(Counter(r['status'] for r in rows)),
        'primary_news_gate_pass_among73':sum(evidence[c['candidate_id']]['pass_registered_news_gate'] is True for c in cases),
        'groups':summarize(rows),
        'portfolio_status_counts':dict(Counter(r['status'] for r in portfolios)),
        'portfolios':[{'variant_id':r['variant_id'],'universe_scope':r['universe_scope'],'status':r['status'],
            'failure':r['failure'],'ending_equity':r['ending_equity'],'profit':r['profit']} for r in portfolios],
        'parent_three_evidence_cases':[{'candidate_id':r['candidate_id'],'variant_id':r['variant_id'],'status':r['status'],
            'profit':r['profit'],'failure':r['failure'],'decisions':r['decisions'],
            'model_trades':r['trades']} for r in rows if r['candidate_id'] in ['2026-01-06__IMSR','2026-01-20__IBRX','2026-01-23__LIF']],
        'input_output_sha256':{'isolated-case-results.json':sha(ROOT/'isolated-case-results.json'),
            'portfolio-results.json':sha(ROOT/'portfolio-results.json')},
        'limits':['Independent500 cases cannot be concatenated into a wealth path.',
            'Complete cases are conditional on observed coverage and action/source assumptions, not a bias-free sample.',
            'Original cost stresses are not verified fills; contemporary reception and exit quotes remain unverified.',
            'Unknown news does not imply no event or a zero-return day.'],
        'new_restart_paths':0,'additional_external_contributions':0,'broker_orders_sent':0}
    save('run-summary.json',summary)
    print(json.dumps({k:summary[k] for k in ['isolated_cases','isolated_variant_results','isolated_status_counts','primary_news_gate_pass_among73','portfolio_status_counts']},indent=2),flush=True)


if __name__=='__main__':main()
