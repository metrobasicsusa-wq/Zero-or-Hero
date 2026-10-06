"""Independent early-entry gap, stale-value, cohort and summary audit. Offline."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from itertools import product
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo
import hashlib
import json
import math
import re

ROOT=Path(__file__).resolve().parent
PARENT=ROOT.parent/'s500-ep-paths-20261006'
NEWS=ROOT.parent/'s500-news-20261006'
NY=ZoneInfo('America/New_York')
D=Decimal


def read(path):return json.loads(path.read_text())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def stamp(session,offset):return (datetime.fromisoformat(session['date']+'T'+session['open']).replace(tzinfo=NY)+timedelta(minutes=offset)).isoformat()
def duration(session):return int((datetime.fromisoformat(session['date']+'T'+session['close'])-datetime.fromisoformat(session['date']+'T'+session['open'])).total_seconds()/60)
def intent_key(candidate):
    values=candidate['opening_range']['exact_values']
    return (candidate['signal']['minute_offset']+1,-D(values['volume'])/D(values['prior20_mean_adjusted_daily_volume']),candidate['symbol'])


def audit_extensions(result,market,calendar,evidence,coverage,candidates,cohorts):
    r=result;cal={s['date']:s for s in calendar};dates=sorted(cal);mode=r['data_mode']
    assert r['conditional_on_input_coverage'] is True
    assert r['model_is_conditional_aggregate_event_sensitivity'] is True and r['model_is_forward_or_independent_validation'] is False
    buys=[t for t in r['trades'] if t['side']=='BUY'];sales={t['candidate_id']:t for t in r['trades'] if t['side']=='SELL'}
    all_gaps=set();skipped=0
    for gap in r['gap_spans']:
        day=gap['date'];s=cal[day];first=gap['first_offset'];last=gap['last_offset_inclusive'];symbol=gap['symbol']
        assert isinstance(first,int) and first<=last and 0<=first<=last<duration(s)
        assert gap['minute_count']==last-first+1 and gap['start_time']==stamp(s,first) and gap['end_time_exclusive']==stamp(s,last+1)
        assert gap['execution_price_created'] is False
        active=[b for b in buys if datetime.fromisoformat(b['time'])<=datetime.fromisoformat(gap['start_time']) and
                (b['candidate_id'] not in sales or datetime.fromisoformat(sales[b['candidate_id']]['time'])>datetime.fromisoformat(gap['start_time']))]
        assert len(active)==1 and active[0]['symbol']==symbol
        buy=active[0];entry=datetime.fromisoformat(buy['time']);held_days=dates.index(day)-dates.index(buy['date'])+1
        gap_point_ids=set()
        for offset in range(first,last+1):
            coordinate=(day,symbol,offset);assert coordinate not in all_gaps;all_gaps.add(coordinate)
            assert str(offset) not in market.get(day,{}).get(symbol,{}) and offset not in market.get(day,{}).get(symbol,{})
            instant=datetime.fromisoformat(stamp(s,offset));assert instant>entry
            if held_days==(10 if r['variant']['exit_mode']=='fixed10' else 63):assert offset!=duration(s)-1
            if offset==0:
                prior=dates[dates.index(day)-1]
                assert not any(l['type']=='ma_close_decision' and l['date']==prior and l['decision']=='exit_next_open' for l in r['ledger'])
            if mode=='evidence_gated':
                key=(symbol,instant.astimezone(timezone.utc).isoformat());assert key in evidence
                gap_point_ids.add(evidence[key]['point_id'])
            else:
                source=coverage['days'][day][symbol]
                assert source['request_complete'] is True and source['regular_window_covered'] is True
        if mode=='evidence_gated':
            assert set(gap['point_ids'])==gap_point_ids and not gap['request_ids'] and gap['basis']=='registered_exact_coordinate_evidence'
        else:
            assert gap['request_ids']==coverage['days'][day][symbol]['request_ids'] and not gap['point_ids']
            assert gap['basis']=='provider_complete_event_series_assumption'
        skipped+=gap['minute_count']
    assert skipped==r['skipped_minute_count']
    evaluated=excluded=stale=0
    for row in r['daily']:
        day=row['date'];close=stamp(cal[day],duration(cal[day]));held=row['quantity']>0
        fresh=not held or row['mark_time']==close
        age=int((datetime.fromisoformat(close)-datetime.fromisoformat(row['mark_time'])).total_seconds()/60) if held else None
        assert row['mark_fresh'] is fresh and row['mark_stale_minutes']==age
        failed=bool(r['failure'] and r['failure']['date']==day)
        eligible=fresh and not failed
        assert row['equity_certified'] is eligible and row['drawdown_and_milestone_eligible'] is eligible
        assert row['conditional_gap_minutes_to_date']==sum(g['minute_count'] for g in r['gap_spans'] if g['date']<=day)
        if eligible:evaluated+=1
        else:excluded+=1
        stale+=held and not fresh
    assert r['daily_drawdown_evaluated_snapshot_count']==evaluated and r['daily_drawdown_excluded_snapshot_count']==excluded
    assert r['observed_fresh_daily_liquidation_drawdown']==(r['max_daily_liquidation_drawdown'] if evaluated else None)
    assert r['terminal_equity_certified'] is r['daily'][-1]['equity_certified']
    if not r['terminal_equity_certified']:assert r['ending_equity'] is None
    if r['status']=='censored_open_position':assert r['profit'] is None and r['position'] is not None
    return {'skipped_minutes_checked':skipped,'gap_spans_checked':len(r['gap_spans']),'stale_snapshots_checked':stale,
            'eligible_snapshots_checked':evaluated,'excluded_snapshots_checked':excluded,
            'account_event_extension_checked':True}


def audit_selection(r,candidates,screen_coverage):
    if 'universe_scope' not in r:return 0
    retrospective=r['universe_scope']=='retrospective_ready_cohort'
    supplied=[c for c in candidates if not retrospective or c['market_gate_pass'] is True]
    assert r['candidate_count_supplied']==len(supplied)
    assert r['news_selection_used'] is False
    assert [x['candidate_id'] for x in r['candidate_register']]==[c['candidate_id'] for c in supplied]
    if retrospective:
        assert r['retrospective_membership_condition'] is True and r['historical_implementability_claim'] is False
        assert r['unknown_market_candidates_omitted_by_diagnostic_design'] is True
        assert r['supplied_membership']==[c['candidate_id'] for c in supplied]
    buys=[t for t in r['trades'] if t['side']=='BUY'];sales={t['candidate_id']:t for t in r['trades'] if t['side']=='SELL'}
    checked=0
    for row in r['daily']:
        day=row['date'];held_start=[b for b in buys if b['date']<day and (b['candidate_id'] not in sales or sales[b['candidate_id']]['date']>=day)]
        intents=[d for d in r['decisions'] if d.get('date')==day and 'intended_quantity' in d]
        if held_start:
            assert not intents and not any(t['date']==day for t in buys)
            assert any(d.get('date')==day and d.get('status')=='no_entry_position_or_exit_day' for d in r['decisions'])
            checked+=1;continue
        if r['universe_scope']=='full_universe_strict' and screen_coverage['days'].get(day,{}).get('initial_screen_complete') is False:
            assert r['failure']['date']==day and r['failure']['reason']=='initial_screen_coverage_unknown' and not intents
            checked+=1;continue
        members=[c for c in supplied if c['date']==day]
        ready=sorted([c for c in members if c['market_gate_pass'] is True],key=intent_key)
        chosen=ready[0] if ready else None
        unresolved=[c for c in members if c['market_gate_pass'] is None]
        blockers=[c['candidate_id'] for c in unresolved if chosen is None or not c.get('signal') or intent_key(c)<=intent_key(chosen)]
        if blockers:
            assert not intents and r['failure']['date']==day
            assert r['failure']['reason']=='unresolved_candidate_precedence' and r['failure']['blocking_candidates']==blockers
        elif chosen:
            assert len(intents)==1 and intents[0]['candidate_id']==chosen['candidate_id']
        else:
            assert not intents
            assert any(d.get('date')==day and d.get('status')=='no_confirmed_technical_intent' for d in r['decisions'])
        checked+=1
    return checked


def audit_groups(rows,summary):
    groups=defaultdict(list)
    for r in rows:groups[(r['family'],r['data_mode'],r['variant_id'])].append(r)
    assert len(groups)==len(summary['isolated_groups'])==24
    for g in summary['isolated_groups']:
        rs=groups[(g['family'],g['data_mode'],g['variant_id'])]
        entered=[r for r in rs if any(t['side']=='BUY' for t in r['trades'])]
        closed=[r for r in entered if r['status']=='complete' and any(t['side']=='SELL' for t in r['trades'])]
        values=[D(r['profit']) for r in closed]
        expected={'cases':len(rs),'statuses':dict(Counter(r['status'] for r in rs)),
                  'entered':len(entered),'closed':len(closed),'wins':sum(v>0 for v in values),
                  'losses':sum(v<0 for v in values),'flat':sum(v==0 for v in values),
                  'no_trade_complete':sum(r['status']=='complete' and not r['trades'] for r in rs),
                  'failure_reasons':dict(Counter(r['failure']['reason'] for r in rs if r['failure']))}
        for k,v in expected.items():assert g[k]==v,(g['family'],g['variant_id'],k)
        for k,v in {'mean_closed_pnl':sum(values)/len(values) if values else None,'median_closed_pnl':median(values) if values else None,
                    'best_closed_pnl':max(values) if values else None,'worst_closed_pnl':min(values) if values else None}.items():
            assert g[k] is None if v is None else math.isclose(g[k],float(v),rel_tol=1e-12,abs_tol=1e-10)
        assert g['independent_cases_not_continuous_wealth'] and g['complete_cases_are_coverage_selected']
    return len(groups)


def audit_paired(rows,gate):
    p=ROOT/'paired-diagnostics.json'
    if not p.exists():return {'status':'not_yet_generated'}
    result=read(p);assert result['gate_sha256']==sha(ROOT/'market-gates.json') and result['result_sha256']==sha(ROOT/'isolated-results.json')
    assert result['retrospective_complete_case_selection'] and result['independent_validation'] is False and result['new_rules_selected'] is False
    idx={(r['family'],r['candidate_id'],r['data_mode'],r['variant_id']):r for r in rows}
    ready={f:{c['candidate_id']:c for c in d['rows'] if c['market_gate_pass'] is True} for f,d in gate['families'].items()}
    assert len(result['pairs'])==3;checks=0
    for pair in result['pairs']:
        a,b=pair['family_a'],pair['family_b'];shared=sorted(set(ready[a])&set(ready[b]))
        assert pair['shared_ready']==len(shared)
        assert pair['only_a_ready']==len(set(ready[a])-set(ready[b])) and pair['only_b_ready']==len(set(ready[b])-set(ready[a]))
        leads=[]
        assert [m['candidate_id'] for m in pair['gate_metrics']]==shared
        for m in pair['gate_metrics']:
            x,y=ready[a][m['candidate_id']],ready[b][m['candidate_id']]
            assert m['signal_offset_a']==x['signal']['minute_offset'] and m['signal_offset_b']==y['signal']['minute_offset']
            lead=y['signal']['minute_offset']-x['signal']['minute_offset'];assert m['earlier_signal_minutes_a_vs_b']==lead;leads.append(lead)
            for suffix,c in [('a',x),('b',y)]:
                width=(D(c['signal']['close_exact'])-D(c['entry_reference']['stop_exact']))/D(c['signal']['close_exact'])
                assert D(m['stop_width_signal_fraction_'+suffix])==width
        assert pair['median_signal_lead_minutes']==(median(leads) if leads else None)
        assert len(pair['paired_financials'])==8
        for s in pair['paired_financials']:
            assert [v['candidate_id'] for v in s['rows']]==shared;deltas=[]
            for v in s['rows']:
                x=idx[(a,v['candidate_id'],s['data_mode'],s['variant_id'])];y=idx[(b,v['candidate_id'],s['data_mode'],s['variant_id'])]
                both=all(z['status']=='complete' and any(t['side']=='SELL' for t in z['trades']) for z in [x,y])
                assert v['status_a']==x['status'] and v['status_b']==y['status'] and v['profit_a']==x['profit'] and v['profit_b']==y['profit']
                assert v['both_entered_and_closed'] is both
                if both:
                    delta=D(x['profit'])-D(y['profit']);assert D(v['paired_profit_a_minus_b'])==delta;deltas.append(delta)
                else:assert v['paired_profit_a_minus_b'] is None
                checks+=1
            assert s['shared_ready']==len(shared) and s['both_entered_closed']==len(deltas)
            assert s['a_better']==sum(v>0 for v in deltas) and s['a_worse']==sum(v<0 for v in deltas) and s['same']==sum(v==0 for v in deltas)
            for key,expected in [('paired_mean_pnl_difference_a_minus_b',sum(deltas)/len(deltas) if deltas else None),('paired_median_pnl_difference_a_minus_b',median(deltas) if deltas else None)]:
                assert s[key] is None if expected is None else math.isclose(s[key],float(expected),rel_tol=1e-12,abs_tol=1e-10)
    return {'status':'passed','paired_rows_checked':checks,'sha256':sha(p)}


def main():
    design=read(ROOT/'study-design.json');a=read(ROOT/'isolated-results.json');b=read(ROOT/'portfolio-results.json')
    rows=a['results'];port=b['results'];summary=read(ROOT/'run-summary.json');tests=read(ROOT/'pre-run-validation.json')
    assert datetime.fromisoformat(design['registered_at'])<datetime.fromisoformat(tests['validated_at'])<datetime.fromisoformat(a['started_at'])
    assert tests['failed_tests']==0 and tests['tests_passed']==80
    for name,want in tests['bound_sha256'].items():assert sha(ROOT/name)==want,name
    assert a['input_sha256']==b['input_sha256']
    for name,want in a['input_sha256'].items():assert sha(ROOT/name)==want,name
    for name,want in summary['output_sha256'].items():assert sha(ROOT/name)==want,name
    bases={'news':NEWS,'paths':PARENT,'event_v2':ROOT.parent/'s500-ep-event-v2-20261006'}
    for name,want in design['input_sha256'].items():
        group,rest=name.split('/',1);assert sha(bases[group]/rest)==want,name
    gate=read(ROOT/'market-gates.json');modes=design['modes'];variants={'fixed10__f'+str(f)+'__c'+str(c) for f,c in product((.5,1.0),(25,100))}
    wanted={(family,mode,c['candidate_id'],variant) for family,data in gate['families'].items() for c in data['rows'] if c['market_gate_pass'] is True for mode,variant in product(modes,variants)}
    assert len(rows)==len(wanted)==3840 and {(r['family'],r['data_mode'],r['candidate_id'],r['variant_id']) for r in rows}==wanted
    scopes=design['registered_outputs']['portfolios_per_family_mode_variant'];assert len(port)==72
    assert {(r['family'],r['data_mode'],r['variant_id'],r['universe_scope']) for r in port}==set(product(gate['families'],modes,variants,scopes))
    market=read(ROOT/'holding-market.json');calendar=read(ROOT/'holding-input-design.json')['full_calendar']
    coverage=read(ROOT/'verified-symbol-sessions.json');screen=read(PARENT/'portfolio-coverage.json')
    conflicts=read(ROOT/'input-conflicts.json');assert not conflicts['candidate_flags'],'Nonzero source conflicts require explicit auditor reconciliation'
    ev=read(bases['event_v2']/'gap-evidence.json')['rows'];evidence={(r['symbol'],r['time']):r for r in ev};assert len(evidence)==36
    checks=[];selections=0
    for r in rows+port:
        assert r['news_selection_used'] is False and r['source_revision_quarantine_applied'] is False
        assert 'not verified-catalyst EP' in r['strategy_scope']
        candidates=gate['families'][r['family']]['rows']
        if r.get('universe_scope')=='retrospective_ready_cohort':candidates=sorted(candidates,key=lambda c:c['candidate_id'])
        selections+=audit_selection(r,candidates,screen)
        ident={k:r[k] for k in ('family','candidate_id','data_mode','variant_id','universe_scope') if k in r}
        checks.append({**ident,**audit_extensions(r,market,calendar,evidence,coverage,None,None)})
    registry=read(ROOT/'all-candidate-registry.json')['rows'];assert len(registry)==3237
    news={r['candidate_id']:{'primary_gate_pass':r['primary_gate_pass'],'status':r['primary_news_status']} for r in read(NEWS/'candidate-readiness.json')['candidates']}
    for r in read(PARENT/'all-new-primary-reviews.json')['reviews']:news[r['candidate_id']]={'primary_gate_pass':r['pass_registered_news_gate'],'status':r['news_status']}
    gates={(f,c['candidate_id']):c for f,d in gate['families'].items() for c in d['rows']}
    for r in registry:
        c=gates[(r['family'],r['candidate_id'])]
        for key in ['date','symbol','family','market_gate_status','market_gate_pass']:assert r[key]==c[key]
        assert r['news_used_for_selection'] is False and r['descriptive_news']==news[r['candidate_id']]
        assert r['member_ready_diagnostic_cohort'] is (c['market_gate_pass'] is True)
    for r in rows:assert r['descriptive_news']==news[r['candidate_id']]
    group_count=audit_groups(rows,summary)
    assert summary['isolated_statuses']==dict(Counter(r['status'] for r in rows))
    assert summary['portfolio_statuses']==dict(Counter(r['status'] for r in port))
    idx={(r['family'],r['data_mode'],r['universe_scope'],r['variant_id']):r for r in port}
    for s in summary['portfolios']:
        r=idx[(s['family'],s['data_mode'],s['universe_scope'],s['variant_id'])]
        for key,v in s.items():
            if key in ['buys','sales']:assert v==sum(t['side']==('BUY' if key=='buys' else 'SELL') for t in r['trades'])
            else:assert r[key]==v,key
    old={(r['candidate_id'],r['variant_id'],r['data_mode']):r for r in read(bases['event_v2']/'isolated-event-results.json')['results']}
    regression=read(ROOT/'or30-parent-regression.json');assert regression['compared']==584 and not regression['mismatches']
    compared=[]
    for r in rows:
        if r['family']!='OR30':continue
        previous=old[(r['candidate_id'],r['variant_id'],r['data_mode'])]
        for key in regression['financial_fields']:assert r[key]==previous[key],(r['candidate_id'],key)
        compared.append({'candidate_id':r['candidate_id'],'variant_id':r['variant_id'],'data_mode':r['data_mode'],'changed_fields':[]})
    assert regression['checks']==compared
    assert summary['total_results']==3912 and summary['isolated_results']==3840 and summary['portfolio_results']==72
    assert summary['or30_parent_financial_regressions']=={'compared':584,'mismatches':0}
    paired=audit_paired(rows,gate)
    report={'audited_at':datetime.now(timezone.utc).isoformat(),'status':'passed','results_checked':3912,'isolated_grid_checked':3840,'portfolios_checked':72,'portfolio_daily_selection_checks':selections,'group_numeric_summaries_checked':group_count,'retained_gate_registry_rows':3237,'unchanged_or30_financial_cases':584,'unchanged_parent_hashes':len(design['input_sha256']),'protocol_and_80_tests_precede_returns':True,'descriptive_news_never_selection':True,'source_revision_conflicts':0,'paired':paired,'extension_checks':checks,'aggregate_extension_counts':{k:sum(x[k] for x in checks) for k in ['skipped_minutes_checked','gap_spans_checked','stale_snapshots_checked','eligible_snapshots_checked','excluded_snapshots_checked']},'input_sha256':{n:sha(ROOT/n) for n in ['study-design.json','early_engine.py','run_early_paths.py','pre-run-validation.json','verified-symbol-sessions.json','source-verification.json','isolated-results.json','portfolio-results.json','run-summary.json','or30-parent-regression.json','all-candidate-registry.json','independent_extension_audit.py']},'network_calls':0,'new_orders_or_pnl_simulations':0}
    (ROOT/'independent-extension-audit.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ['extension_checks','input_sha256']},indent=2))


if __name__=='__main__':main()

