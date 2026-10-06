"""Independent actual-output accounting checks; no simulation, network, or orders."""
from decimal import Decimal,ROUND_FLOOR
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from collections import Counter

D=Decimal
NY=ZoneInfo('America/New_York')

def audit_path(result,market,daily,calendar):
    """Read one saved result and reconcile against raw inputs without running engine."""
    r=result;cal={s['date']:s for s in calendar};dates=sorted(cal);cost=D(str(r['variant']['cost_bps']))/10000
    fraction=D(str(r['variant']['fraction']));trades=r['trades'];rows=r['daily'];decisions=r['decisions']
    assert D(r['initial_cash'])==500 and D(r['funding_injections_after_initial'])==0 and r['restart_count']==0
    assert [x['date'] for x in rows]==sorted(set(x['date'] for x in rows))
    assert [datetime.fromisoformat(t['time']) for t in trades]==sorted(datetime.fromisoformat(t['time']) for t in trades)
    decisions_by_id={d['candidate_id']:d for d in decisions if d.get('status')=='entered'}
    def bar_at(symbol,stamp):
        t=datetime.fromisoformat(stamp).astimezone(NY);day=t.date().isoformat();session=cal[day]
        start=datetime.fromisoformat(day+'T'+session['open']).replace(tzinfo=NY)
        offset=(t-start).total_seconds()/60;assert offset==int(offset)
        b=market[day][symbol][str(int(offset))];assert datetime.fromisoformat(b['t'].replace('Z','+00:00'))==t
        return b,int(offset)
    buys=[];sales=[];position=None
    for t in trades:
        assert t['modeled_not_actual_fill'] is True and isinstance(t['quantity'],int) and not isinstance(t['quantity'],bool) and t['quantity']>0
        q=D(t['quantity']);price=D(t['reference_price']);bar,offset=bar_at(t['symbol'],t['time'])
        assert price>0 and D(t['cost'])==q*price*cost
        if t['side']=='BUY':
            assert position is None;decision=decisions_by_id[t['candidate_id']]
            assert decision['entry_time']==t['time'] and D(decision['intended_quantity'])==q
            assert datetime.fromisoformat(decision['entry_time'])-datetime.fromisoformat(decision['signal_time'])==timedelta(minutes=1)
            signalbar,_=bar_at(t['symbol'],(datetime.fromisoformat(decision['signal_time'])-timedelta(minutes=1)).isoformat())
            assert D(str(signalbar['c']))==D(decision['signal_close'])
            cash_before=D(500)-sum(D(x['cash_amount']) for x in buys)+sum(D(x['cash_amount']) for x in sales if x['settlement_date']<=t['date'])
            budget=cash_before*fraction;assert D(decision['budget'])==budget
            assert q==int((budget/(D(decision['signal_close'])*(1+cost))).to_integral_value(rounding=ROUND_FLOOR))
            assert price==D(str(bar['o'])) and price>D(decision['stop'])
            assert D(t['cash_amount'])==q*price*(1+cost)<=budget<=cash_before
            buys.append(t);position={**t,'stop':D(decision['stop'])}
        else:
            assert t['side']=='SELL' and position is not None
            assert t['candidate_id']==position['candidate_id'] and t['quantity']==position['quantity']
            assert t['settlement_date']==cal[t['date']]['settlement_date']>t['date']
            assert D(t['cash_amount'])==q*price*(1-cost)
            reasons=t['reasons'];assert reasons
            if reasons==['protective_stop']:
                assert price==position['stop'] and D(str(bar['l']))<=price<D(str(bar['o']))
            else:
                assert price==D(str(bar['o']))
                if 'gap_stop' in reasons:assert price<=position['stop']
                days_held=dates.index(t['date'])-dates.index(position['date'])+1
                start=datetime.fromisoformat(t['date']+'T'+cal[t['date']]['open']);close=datetime.fromisoformat(t['date']+'T'+cal[t['date']]['close'])
                last=int((close-start).total_seconds()/60)-1
                if 'fixed10' in reasons:assert days_held==10 and offset==last
                if 'max63' in reasons:assert days_held==63 and offset==last
                if 'ma10_next_open' in reasons:
                    assert offset==0;prior=dates[dates.index(t['date'])-1]
                    marks=[l for l in r['ledger'] if l['type']=='ma_close_decision' and l['date']==prior]
                    assert len(marks)==1 and marks[0]['decision']=='exit_next_open'
            sales.append(t);position=None
    assert len([t for t in trades if t['side']=='BUY'])==len(decisions_by_id)
    assert [l['candidate_id'] for l in r['ledger'] if l['type']=='purchase']==[t['candidate_id'] for t in buys]
    assert [l['candidate_id'] for l in r['ledger'] if l['type']=='sale_receivable']==[t['candidate_id'] for t in sales]
    settlements=[l for l in r['ledger'] if l['type']=='settlement']
    for l in settlements:
        source=[t for t in sales if t['date']==l['origin_date'] and t['settlement_date']==l['date']]
        assert len(source)==1 and D(l['amount'])==D(source[0]['cash_amount'])
    assert len({(l['origin_date'],l['date']) for l in settlements})==len(settlements)
    settled=sum(D(l['amount']) for l in settlements)
    assert D(r['cash_settled'])==D(500)-sum(D(x['cash_amount']) for x in buys)+settled
    assert sum(D(x['amount']) for x in r['terminal_receivables'])==sum(D(x['cash_amount']) for x in sales)-settled
    peak=D(500);dd=D(0);milestones={str(v):None for v in (1000,2000,5000,10000)}
    for row in rows:
        day=row['date'];b=[t for t in buys if t['date']<=day];s=[t for t in sales if t['date']<=day]
        cash=D(500)-sum(D(t['cash_amount']) for t in b)+sum(D(t['cash_amount']) for t in s if t['settlement_date']<=day)
        un=sum(D(t['cash_amount']) for t in s if t['settlement_date']>day)
        open_b=[t for t in b if not any(z['candidate_id']==t['candidate_id'] for z in s)]
        assert len(open_b)<=1;held=D(0)
        if open_b:
            t=open_b[0];q=D(t['quantity']);assert row['quantity']==t['quantity'] and row['symbol']==t['symbol']
            if row['mark_time']==t['time']:mark=D(t['reference_price'])
            else:
                bar,_=bar_at(t['symbol'],(datetime.fromisoformat(row['mark_time'])-timedelta(minutes=1)).isoformat());mark=D(str(bar['c']))
            held=q*mark
        else:assert row['quantity']==0 and row['symbol'] is None
        assert D(row['cash_settled'])==cash and D(row['cash_unsettled'])==un and D(row['marked_position'])==held
        equity=cash+un+held;liq=cash+un+held*(1-cost)
        assert D(row['last_observed_equity'])==equity and D(row['last_observed_liquidation_equity'])==liq
        if row['equity_certified']:
            peak=max(peak,liq);dd=max(dd,1-liq/peak)
            for level in milestones:
                if liq>=D(level) and milestones[level] is None:milestones[level]=day
    assert D(r['max_daily_liquidation_drawdown'])==dd and r['milestones']==milestones
    ma_count=0
    for l in r['ledger']:
        if l['type']!='ma_close_decision':continue
        i=dates.index(l['date']);wanted=dates[i-9:i+1];assert l['source_dates']==wanted and len(wanted)==10
        # Position can be closed later; identify latest buy by this date.
        symbol=[t for t in buys if t['date']<=l['date']][-1]['symbol'];values=[D(str(daily[symbol][d]['c'])) for d in wanted]
        assert D(l['vendor_close'])==values[-1] and D(l['ma10'])==sum(values)/10
        assert l['decision']==('exit_next_open' if values[-1]<sum(values)/10 else 'hold');ma_count+=1
    if r['status']=='incomplete':
        assert r['failure'] and r['ending_equity'] is None and r['profit'] is None
        if r['last_known_mark_time']:assert datetime.fromisoformat(r['failure']['time'])>=datetime.fromisoformat(r['last_known_mark_time'])
    elif r['status']=='censored_open_position':
        assert position is not None and r['position'] and r['profit'] is None and r['failure'] is None
        assert D(r['ending_equity'])==D(r['last_observed_equity'])
    else:
        assert r['status']=='complete' and position is None and r['position'] is None and r['failure'] is None
        assert D(r['ending_equity'])==D(500)+sum(D(t['cash_amount']) for t in sales)-sum(D(t['cash_amount']) for t in buys)
        assert D(r['profit'])==D(r['ending_equity'])-500
    assert rows and D(r['last_observed_equity'])==D(rows[-1]['last_observed_equity'])
    return {'status':r['status'],'trades_checked':len(trades),'buys_checked':len(buys),'sales_checked':len(sales),
            'daily_snapshots_checked':len(rows),'ma_decisions_checked':ma_count,'funding_injections':0,'source_prices_and_exact_cash_reconciled':True}


def main():
    """Reconcile all saved results and summaries, without importing the simulator."""
    from pathlib import Path
    from datetime import timezone
    from itertools import product
    from statistics import median
    from collections import defaultdict
    import hashlib
    import json
    import math
    import traceback

    root=Path(__file__).resolve().parent
    read=lambda name:json.loads((root/name).read_text())
    sha=lambda name:hashlib.sha256((root/name).read_bytes()).hexdigest()
    isolated=read('isolated-case-results.json');portfolios=read('portfolio-results.json')
    summary=read('run-summary.json');lock=read('primary-classification-lock.json')
    assert datetime.fromisoformat(lock['locked_at'])<datetime.fromisoformat(isolated['started_at'])
    assert lock['all68_dispositions_sha256']==sha('all-new-primary-reviews.json')
    assert lock['classification_code_sha256']==sha('complete_primary_reviews.py')
    assert isolated['input_sha256']==portfolios['input_sha256']
    for name,want in isolated['input_sha256'].items():assert sha(name)==want,name
    for name,want in summary['input_output_sha256'].items():assert sha(name)==want,name
    inputs=read('holding-input-design.json')
    ids={x['candidate_id'] for x in inputs['cases']}
    wanted_variants={m+'__f'+str(f)+'__c'+str(c) for m,f,c in product(('fixed10','ma10_max63'),(.5,1.0),(25,50,100))}
    rows=isolated['results'];portfolio_rows=portfolios['results']
    assert len(ids)==73 and len(rows)==876 and isolated['results_count']==876
    assert {(r['candidate_id'],r['variant_id']) for r in rows}==set(product(ids,wanted_variants))
    assert len(portfolio_rows)==24
    assert {(r['universe_scope'],r['variant_id']) for r in portfolio_rows}==set(product(('full_universe_strict','original_cohort_conditional'),wanted_variants))
    assert isolated['all_cases_independent500_not_continuous_wealth'] is True
    assert isolated['orders_sent']==portfolios['broker_orders_sent']==summary['broker_orders_sent']==0
    assert summary['additional_external_contributions']==summary['new_restart_paths']==0
    market=read('holding-market.json');daily=read('daily-market-private.json');calendar=inputs['full_calendar']
    checks=[];failures=[]
    for r in rows+portfolio_rows:
        ident={k:r[k] for k in ('kind','candidate_id','variant_id','universe_scope') if k in r}
        try:checks.append({**ident,**audit_path(r,market,daily,calendar)})
        except Exception as exc:failures.append({**ident,'error':str(exc),'traceback':traceback.format_exc()})
    # Public report stores no local paths or traceback internals on a successful run.
    report={'audited_at':datetime.now(timezone.utc).isoformat(),'checks':checks,'failures':failures,
            'input_hashes_verified':isolated['input_sha256'],'all_primary_classifications_locked_before_first_run':True,
            'rows_total':len(rows)+len(portfolio_rows),'rows_passed':len(checks),'rows_failed':len(failures),
            'audit_script_sha256':sha('audit_actual_paths.py')}
    (root/'actual-path-ledger-audit.json').write_text(json.dumps(report,indent=2)+'\n')
    assert not failures,'Actual path reconciliation failed; inspect saved audit details.'
    # Independently rejoin classifications and initial gates for every retained candidate.
    prior=json.loads((root.parent/'s500-news-20261006'/'candidate-readiness.json').read_text())['candidates']
    evidence={x['candidate_id']:(x['primary_gate_pass'],x['primary_news_status']) for x in prior}
    for x in read('all-new-primary-reviews.json')['reviews']:
        evidence[x['candidate_id']]=(x['pass_registered_news_gate'],x['news_status'])
    repaired={x['candidate_id']:x for x in read('repaired-market-gates.json')['rows']}
    decisions=read('decision-evidence.json')
    assert len(repaired)==len(decisions['rows'])==decisions['candidate_count']==1079
    assert {x['candidate_id'] for x in decisions['rows']}==set(repaired)
    for x in decisions['rows']:
        source=repaired[x['candidate_id']]
        assert (x['news_gate_pass'],x['news_status'])==evidence[x['candidate_id']]
        for key in ('date','symbol','market_gate_pass','market_gate_status','signal'):
            assert x.get(key)==source.get(key),(x['candidate_id'],key)
        assert x['absence_of_qualifying_news_proven'] is False
    assert len({r['candidate_id'] for r in rows if r['primary_news_gate'] is True})==summary['primary_news_gate_pass_among73']==32
    for r in rows:
        assert (r['primary_news_gate'],r['primary_news_status'])==evidence[r['candidate_id']]
    grouped=defaultdict(list)
    for r in rows:grouped[(r['variant_id'],r['primary_news_gate'] is True)].append(r)
    assert len(grouped)==len(summary['groups'])==24
    computed=[]
    for group in summary['groups']:
        selected=grouped[(group['variant_id'],group['news_primary_gate_pass'])]
        entered=[r for r in selected if any(t['side']=='BUY' for t in r['trades'])]
        closed=[r for r in entered if r['status']=='complete' and any(t['side']=='SELL' for t in r['trades'])]
        values=[D(r['profit']) for r in closed]
        expected={'cases':len(selected),'status_counts':dict(Counter(r['status'] for r in selected)),
                  'entered_cases':len(entered),'closed_model_trades':len(closed),
                  'profitable_closed_cases':sum(p>0 for p in values),'losing_closed_cases':sum(p<0 for p in values),
                  'failure_reasons':dict(Counter(r['failure']['reason'] for r in selected if r['failure'])),
                  'skipped_intents':dict(Counter(d['status'] for r in selected for d in r['decisions'] if d.get('status','').startswith('skipped')))}
        for key,value in expected.items():assert group[key]==value,(group['variant_id'],key)
        numeric={'mean_closed_case_pnl':sum(values)/len(values) if values else None,
                 'median_closed_case_pnl':median(values) if values else None,
                 'best_closed_case_pnl':max(values) if values else None,
                 'worst_closed_case_pnl':min(values) if values else None}
        for key,value in numeric.items():
            if value is None:assert group[key] is None
            else:assert math.isclose(group[key],float(value),rel_tol=1e-12,abs_tol=1e-10),(group['variant_id'],key)
        assert group['not_continuous_wealth'] is True and group['complete_cases_are_a_selected_coverage_subset'] is True
        computed.append({'variant_id':group['variant_id'],'primary_news_gate_pass':group['news_primary_gate_pass'],
                         **expected,**{key:None if value is None else str(value) for key,value in numeric.items()}})
    assert summary['isolated_status_counts']==dict(Counter(r['status'] for r in rows))
    assert summary['portfolio_status_counts']=={'incomplete':24}
    assert len(summary['portfolios'])==24
    portfolio_index={(r['universe_scope'],r['variant_id']):r for r in portfolio_rows}
    for short in summary['portfolios']:
        r=portfolio_index[(short['universe_scope'],short['variant_id'])]
        for key in short:assert short[key]==r[key],key
        assert r['status']=='incomplete' and r['ending_equity'] is None and r['profit'] is None
        assert not r['trades'] and r['failure']['date']=='2026-01-02'
        if r['universe_scope']=='full_universe_strict':assert r['failure']['reason']=='initial_screen_coverage_unknown'
        else:assert r['failure']['blocking_candidates']==['2026-01-02__BIDU']
    totals={key:sum(c[key] for c in checks) for key in ('trades_checked','buys_checked','sales_checked','daily_snapshots_checked','ma_decisions_checked')}
    outcome={'audited_at':datetime.now(timezone.utc).isoformat(),'result_records_checked':900,
             'independent_case_count':73,'variants_per_case':12,'independent_case_results':876,
             'continuous_portfolio_results':24,'all_continuous_portfolios_incomplete':True,
             'all_candidate_decision_evidence_rows_checked':1079,'new_primary_cases':68,'primary_gate_pass_cases':32,
             'summary_group_count_verified':24,'summary_numeric_tolerance':1e-10,
             'isolated_status_counts':summary['isolated_status_counts'],
             'complete_but_no_entry_cases':sum(r['status']=='complete' and not r['trades'] for r in rows),
             'complete_closed_cases':sum(r['status']=='complete' and any(t['side']=='SELL' for t in r['trades']) for r in rows),
             'all_closed_subset_group_means_negative':all(D(g['mean_closed_case_pnl'])<0 for g in computed if g['mean_closed_case_pnl'] is not None),
             'groups':computed,'ledger_totals':totals,
             'output_sha256':{name:sha(name) for name in ('isolated-case-results.json','portfolio-results.json','run-summary.json','decision-evidence.json')},
             'classification_lock_precedes_run':True,'actual_broker_fills_claimed':False,
             'limits':['The 12 models overlap the same 73 cases; 876 rows are not 876 independent observations.',
                       'Every isolated case starts with hypothetical500. There is no capital continuity across cases.',
                       'Cost variants can enter different subsets because whole-share budget thresholds and next-open prices differ.',
                       'Unconfirmed-news group is not verified absence of qualifying news.',
                       'Completed-case means exclude missing-data and still-held cases and cannot establish whole-cohort returns.']}
    (root/'actual-summary-independent-audit.json').write_text(json.dumps(outcome,indent=2)+'\n')
    print(json.dumps({key:outcome[key] for key in ('result_records_checked','isolated_status_counts','complete_but_no_entry_cases','complete_closed_cases','ledger_totals','all_continuous_portfolios_incomplete','all_closed_subset_group_means_negative')},indent=2))


if __name__=='__main__':main()
