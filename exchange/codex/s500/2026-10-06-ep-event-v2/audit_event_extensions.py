"""Independent v2 source, gap, stale-value, cohort and summary audit. Offline."""
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
    values=candidate['opening30']['exact_values']
    return (candidate['signal']['minute_offset']+1,-D(values['volume'])/D(values['prior20_mean_adjusted_daily_volume']),candidate['symbol'])


def audit_source_coverage(market,calendar):
    manifest=read(PARENT/'holding-input-manifest.json');plan=read(PARENT/'holding-input-design.json')
    coverage=read(ROOT/'verified-symbol-sessions.json');calendar={s['date']:s for s in calendar}
    assert manifest['all_pages_complete'] and not manifest['failures']
    expected_pairs={(day,c['symbol']) for c in plan['cases'] for day in c['session_dates']}
    actual_pairs={(day,sym) for day,values in coverage['days'].items() for sym in values}
    assert expected_pairs==actual_pairs and len(actual_pairs)==3823
    source_report=read(ROOT/'source-verification.json');assert source_report['coverage_sha256']==sha(ROOT/'verified-symbol-sessions.json')
    declared_pages=[];counts=Counter();pages=0;bars=0
    for request in manifest['records']:
        session=calendar[request['date']];day=session['date'];symbols=request['parameters']['symbols'].split(',')
        assert len(symbols)==len(set(symbols));opening=datetime.fromisoformat(stamp(session,0));closing=datetime.fromisoformat(stamp(session,duration(session)))
        route='/v2/stocks/bars';params=request['parameters']
        assert request['route']==route and request['pages_complete']
        assert params['feed']=='sip' and params['adjustment']=='raw' and params['timeframe']=='1Min' and params['sort']=='asc' and params['limit']==10000
        assert datetime.fromisoformat(params['start'])==opening and datetime.fromisoformat(params['end'])==closing-timedelta(minutes=1)
        base=digest({'route':route,'parameters':params});assert request['request_sha256']==base
        parameters=dict(params);tokens=set();seen=set();local=Counter()
        for number,item in enumerate(request['pages']):
            path=PARENT/'raw-bars'/request['task_id']/item['name']
            assert sha(path)==item['sha256'] and path.stat().st_size==item['bytes']
            page=read(path);assert page['base_request_sha256']==base and page['page_number']==number
            assert page['page_request_sha256']==digest({'route':route,'parameters':parameters})
            payload=page['response'];assert isinstance(payload['bars'],dict) and set(payload['bars'])<=set(symbols)
            pagecount=0
            for symbol,records in payload['bars'].items():
                for bar in records:
                    assert re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00(?:\.0{1,9})?(?:Z|[+-]\d{2}:\d{2})',bar['t'])
                    time=datetime.fromisoformat(bar['t'].replace('Z','+00:00'));off=(time-opening).total_seconds()/60
                    assert off==int(off) and opening<=time<closing
                    identity=(symbol,int(off));assert identity not in seen;seen.add(identity)
                    assert market[day][symbol][str(int(off))]==bar
                    pagecount+=1;local[symbol]+=1
            assert pagecount==item['records'];bars+=pagecount;pages+=1
            token=payload['next_page_token'];assert token is None or isinstance(token,str) and token
            if token is None:assert number==len(request['pages'])-1
            else:assert token not in tokens;tokens.add(token);parameters={**params,'page_token':token}
            declared_pages.append({'task_id':request['task_id'],**item})
        assert token is None
        for symbol in symbols:
            row=coverage['days'][day][symbol]
            assert row=={'request_complete':True,'regular_window_covered':True,'request_ids':[request['task_id']],
                         'bars_returned':local[symbol],'scheduled_minutes':duration(session)}
            assert len(market.get(day,{}).get(symbol,{}))==local[symbol]
            counts['unreturned_slots']+=duration(session)-local[symbol]
    assert source_report['verified_pages']==declared_pages
    assert pages==coverage['verified_raw_pages']==246 and bars==manifest['data_records']==coverage['verified_bar_records']==1353239
    assert counts['unreturned_slots']==coverage['unreturned_symbol_minute_slots']==137731
    assert coverage['source_archive_exhaustiveness_proven'] is False
    assert coverage['input_sha256']=={'parent_manifest':sha(PARENT/'holding-input-manifest.json'),'parent_holding_market':sha(PARENT/'holding-market.json'),
        'parent_holding_plan':sha(PARENT/'holding-input-design.json'),'study_design':sha(ROOT/'study-design.json'),'preparation_code':sha(ROOT/'prepare_event_inputs.py')}
    return {'symbol_sessions':3823,'raw_pages':pages,'bars':bars,'unreturned_minute_slots':137731,'raw_page_chains_and_assembly_verified':True}


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
    if r.get('universe_scope','').startswith('retrospective_'):
        name=r['cohort_name'];assert r['supplied_membership']==cohorts[name]
        assert r['candidate_count_supplied']==len(cohorts[name])
        assert r['retrospective_membership_condition'] and not r['historical_implementability_claim'] and r['original_news_evidence_not_overridden']
        members=[candidates[cid] for cid in cohorts[name]]
        selections=[d for d in r['decisions'] if d['status']=='retrospective_frozen_membership_selection']
        for row in r['daily']:
            day=row['date'];scheduled=sorted((c for c in members if c['date']==day),key=intent_key)
            held_start=[b for b in buys if b['date']<day and (b['candidate_id'] not in sales or sales[b['candidate_id']]['date']>=day)]
            selected=[d for d in selections if d['date']==day]
            if scheduled and not held_start:
                assert len(selected)==1
                assert selected[0]['ranked_candidate_ids']==[c['candidate_id'] for c in scheduled]
                assert selected[0]['selected_candidate_id']==scheduled[0]['candidate_id']
                intents=[d for d in r['decisions'] if d.get('date')==day and 'intended_quantity' in d]
                assert len(intents)==1 and intents[0]['candidate_id']==scheduled[0]['candidate_id']
            else:assert not selected
        assert all(t['candidate_id'] in cohorts[name] for t in r['trades'])
    return {'skipped_minutes_checked':skipped,'gap_spans_checked':len(r['gap_spans']),'stale_snapshots_checked':stale,
            'eligible_snapshots_checked':evaluated,'excluded_snapshots_checked':excluded,
            'retrospective_membership_and_every_daily_selection_checked':r.get('universe_scope','').startswith('retrospective_')}


def main():
    design=read(ROOT/'study-design.json');a=read(ROOT/'isolated-event-results.json');b=read(ROOT/'portfolio-event-results.json')
    rows=a['results'];port=b['results'];summary=read(ROOT/'run-summary.json');tests=read(ROOT/'pre-run-validation.json')
    assert datetime.fromisoformat(design['registered_at'])<datetime.fromisoformat(tests['validated_at'])<datetime.fromisoformat(a['started_at'])
    assert tests['engine_sha256']==sha(ROOT/'ep_event_engine.py') and tests['implementation_tests_passed']==32 and tests['independent_tests_passed']==20 and tests['failed_tests']==0
    assert tests['log_sha256']==sha(ROOT/'pre-run-tests.log')
    for name,want in tests['test_code_sha256'].items():assert sha(ROOT/name)==want
    assert a['input_sha256']==b['input_sha256']
    for name,want in a['input_sha256'].items():assert sha(ROOT/name)==want,name
    for name,want in summary['input_output_sha256'].items():assert sha(ROOT/name)==want,name
    baseline=read(ROOT/'audit-plan.json')['parent_baseline_sha256']
    for stage,root in [('paths-v1',PARENT),('gaps-v1',ROOT.parent/'s500-ep-gaps-20261006')]:
        for name,want in baseline[stage].items():assert sha(root/name)==want,(stage,name)
    cohorts=read(ROOT/'retrospective-cohorts.json');modes=list(design['modes'])
    variants={m+'__f'+str(f)+'__c'+str(c) for m,f,c in product(('fixed10','ma10_max63'),(.5,1.0),(25,50,100))}
    assert len(rows)==1752 and {(r['data_mode'],r['candidate_id'],r['variant_id']) for r in rows}==set(product(modes,cohorts['market73'],variants))
    scopes=('full_universe_strict','original_cohort_guard','retrospective_market73','retrospective_verified_news32')
    assert len(port)==96 and {(r['data_mode'],r['universe_scope'],r['variant_id']) for r in port}==set(product(modes,scopes,variants))
    market=read(PARENT/'holding-market.json');daily=read(PARENT/'daily-market-private.json');calendar=read(PARENT/'holding-input-design.json')['full_calendar']
    coverage=read(ROOT/'verified-symbol-sessions.json');source=audit_source_coverage(market,calendar)
    candidates={c['candidate_id']:c for c in read(PARENT/'repaired-market-gates.json')['rows']}
    evidence={(r['symbol'],r['time']):r for r in read(ROOT/'gap-evidence.json')['rows']};assert len(evidence)==36
    checks=[]
    for r in rows+port:
        ident={k:r[k] for k in ('data_mode','candidate_id','variant_id','universe_scope') if k in r}
        checks.append({**ident,**audit_extensions(r,market,calendar,evidence,coverage,candidates,cohorts)})
    registry=read(ROOT/'all-candidate-registry.json');assert registry['candidate_count']==len(registry['rows'])==len(candidates)==1079
    newsev={r['candidate_id']:(r['primary_gate_pass'],r['primary_news_status']) for r in read(NEWS/'candidate-readiness.json')['candidates']}
    for r in read(PARENT/'all-new-primary-reviews.json')['reviews']:newsev[r['candidate_id']]=(r['pass_registered_news_gate'],r['news_status'])
    for r in registry['rows']:
        c=candidates[r['candidate_id']]
        for k in ('date','symbol','market_gate_status','market_gate_pass'):assert r[k]==c[k]
        assert (r['news_gate_pass'],r['news_status'])==newsev[r['candidate_id']]
        assert r['member_market73'] is (r['candidate_id'] in cohorts['market73']) and r['member_verified_news32'] is (r['candidate_id'] in cohorts['verified_news32'])
        assert r['unseen_news_absence_proven'] is False
    groups=defaultdict(list)
    for r in rows:
        assert (r['primary_news_gate'],r['primary_news_status'])==newsev[r['candidate_id']]
        groups[(r['data_mode'],r['variant_id'],r['primary_news_gate'] is True)].append(r)
    assert len(groups)==len(summary['isolated_groups'])==48
    for g in summary['isolated_groups']:
        rs=groups[(g['data_mode'],g['variant_id'],g['primary_news_gate_pass'])]
        entered=[r for r in rs if any(t['side']=='BUY' for t in r['trades'])]
        closed=[r for r in entered if r['status']=='complete' and any(t['side']=='SELL' for t in r['trades'])]
        values=[D(r['profit']) for r in closed]
        expected={'cases':len(rs),'statuses':dict(Counter(r['status'] for r in rs)),'entered':len(entered),'closed_model_trades':len(closed),
                  'profitable_closed':sum(v>0 for v in values),'losing_closed':sum(v<0 for v in values),
                  'failure_reasons':dict(Counter(r['failure']['reason'] for r in rs if r['failure'])),'no_trade_complete':sum(r['status']=='complete' and not r['trades'] for r in rs)}
        for k,v in expected.items():assert g[k]==v,(g['variant_id'],k)
        numeric={'mean_closed_pnl':sum(values)/len(values) if values else None,'median_closed_pnl':median(values) if values else None,
                 'best_closed_pnl':max(values) if values else None,'worst_closed_pnl':min(values) if values else None}
        for k,v in numeric.items():assert g[k] is None if v is None else math.isclose(g[k],float(v),rel_tol=1e-12,abs_tol=1e-10)
        assert g['not_continuous_wealth'] is True and g['overlapping_events_and_coverage_selected_completes'] is True
    assert summary['isolated_statuses']=={mode:dict(Counter(r['status'] for r in rows if r['data_mode']==mode)) for mode in modes}
    indexed={(r['data_mode'],r['universe_scope'],r['variant_id']):r for r in port}
    assert len(summary['portfolios'])==96
    for small in summary['portfolios']:
        full=indexed[(small['data_mode'],small['universe_scope'],small['variant_id'])]
        for key,value in small.items():
            if key in ('buys','sales'):assert value==sum(t['side']==('BUY' if key=='buys' else 'SELL') for t in full['trades'])
            else:assert value==full[key],key
    old={(r['candidate_id'],r['variant_id']):r for r in read(PARENT/'isolated-case-results.json')['results']}
    transitions=read(ROOT/'v1-v2-transitions.json');assert transitions['parent_results_sha256']==sha(PARENT/'isolated-case-results.json')
    for record in transitions['modes']:
        rs=[r for r in rows if r['data_mode']==record['mode']]
        assert record['status_transitions']==dict(Counter(old[(r['candidate_id'],r['variant_id'])]['status']+' -> '+r['status'] for r in rs))
        assert record['original_complete_cash_or_trade_changes']==[]
        prior_complete=0
        for r in rs:
            previous=old[(r['candidate_id'],r['variant_id'])]
            if previous['status']!='complete':continue
            prior_complete+=1
            for key in ('status','cash_settled','profit','ending_equity','terminal_receivables'):assert r[key]==previous[key]
            for i,trade in enumerate(previous['trades']):
                for key,value in trade.items():assert r['trades'][i][key]==value
            assert len(r['trades'])==len(previous['trades'])
        assert prior_complete==428
    guards=[r for r in port if r['universe_scope'] in ('full_universe_strict','original_cohort_guard')]
    assert len(guards)==48 and all(r['status']=='incomplete' and r['profit'] is None and r['ending_equity'] is None and not r['trades'] for r in guards)
    for r in guards:
        assert r['failure']['date']=='2026-01-02'
        if r['universe_scope']=='full_universe_strict':assert r['failure']['reason']=='initial_screen_coverage_unknown'
        else:assert r['failure']['blocking_candidates']==['2026-01-02__BIDU']
    closed=[r for r in port if r['data_mode']=='provider_event_series_assumption' and r['universe_scope']=='retrospective_verified_news32']
    censored=[r for r in port if r['data_mode']=='provider_event_series_assumption' and r['universe_scope']=='retrospective_market73']
    assert len(closed)==len(censored)==12 and all(r['status']=='complete' for r in closed)
    assert all(r['status']=='censored_open_position' and r['profit'] is None for r in censored)
    ledger=read(ROOT/'independent-ledger-audit.json');assert ledger['results_checked']==ledger['passed']==1848 and ledger['failed']==0
    output={'audited_at':datetime.now(timezone.utc).isoformat(),'result_count':1848,'source_coverage':source,
            'all_gap_spans_freshness_and_portfolio_selections_checked':True,'all_48_group_numeric_summaries_checked':True,'all_1079_original_candidates_retained_and_joined':True,
            'parent_428_complete_cases_identical_in_each_mode':True,'all_21_parent_baseline_hashes_unchanged':True,
            'protocol_and_52_tests_precede_v2_outcomes':True,'original_guard_results_still_incomplete':48,
            'provider_market73_open_positions_not_realized_profit':12,
            'provider_news32_closed_conditional_equity_range':[str(min(D(r['ending_equity']) for r in closed)),str(max(D(r['ending_equity']) for r in closed))],
            'extension_checks':checks,'aggregate_extension_counts':{k:sum(x[k] for x in checks) for k in ('skipped_minutes_checked','gap_spans_checked','stale_snapshots_checked','eligible_snapshots_checked','excluded_snapshots_checked')},
            'input_sha256':{name:sha(ROOT/name) for name in ['study-design.json','ep_event_engine.py','run_event_paths.py','pre-run-validation.json','verified-symbol-sessions.json','source-verification.json','isolated-event-results.json','portfolio-event-results.json','run-summary.json','v1-v2-transitions.json','all-candidate-registry.json','audit_event_extensions.py']},
            'network_calls':0,'new_orders_or_pnl_simulations':0}
    (ROOT/'independent-extension-audit.json').write_text(json.dumps(output,indent=2)+'\n')
    print(json.dumps({k:v for k,v in output.items() if k not in ('extension_checks','input_sha256')},indent=2))


if __name__=='__main__':main()
