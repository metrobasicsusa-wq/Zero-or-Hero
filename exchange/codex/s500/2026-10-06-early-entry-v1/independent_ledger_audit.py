"""Independent early-entry accounting checks, adapted from the prior audited ledger verifier; no simulation, network, or orders. The censored endpoint permits only the new explicitly stale null ending value."""
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
        
        if r['terminal_equity_certified']:
            assert D(r['ending_equity'])==D(r['last_observed_equity'])
        else:
            assert r['ending_equity'] is None
    else:
        assert r['status']=='complete' and position is None and r['position'] is None and r['failure'] is None
        assert D(r['ending_equity'])==D(500)+sum(D(t['cash_amount']) for t in sales)-sum(D(t['cash_amount']) for t in buys)
        assert D(r['profit'])==D(r['ending_equity'])-500
    assert rows and D(r['last_observed_equity'])==D(rows[-1]['last_observed_equity'])
    return {'status':r['status'],'trades_checked':len(trades),'buys_checked':len(buys),'sales_checked':len(sales),
            'daily_snapshots_checked':len(rows),'ma_decisions_checked':ma_count,'funding_injections':0,'source_prices_and_exact_cash_reconciled':True}


def main():
    from pathlib import Path
    from datetime import timezone
    import hashlib
    import json
    root=Path(__file__).resolve().parent
    prior=root
    read=lambda path:json.loads(path.read_text())
    a=read(root/'isolated-results.json');b=read(root/'portfolio-results.json')
    market=read(prior/'holding-market.json');daily=read(prior/'daily-market-private.json')
    calendar=read(prior/'holding-input-design.json')['full_calendar'];checks=[]
    assert len(a['results'])==3840 and len(b['results'])==72
    for row in a['results']+b['results']:
        identity={key:row[key] for key in ('family','candidate_id','data_mode','variant_id','universe_scope') if key in row}
        checks.append({**identity,**audit_path(row,market,daily,calendar)})
    report={'audited_at':datetime.now(timezone.utc).isoformat(),'results_checked':len(checks),'passed':len(checks),'failed':0,
            'checks':checks,'failures':[],
            'audit_script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'input_sha256':{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in ('isolated-results.json','portfolio-results.json')}}
    (root/'independent-ledger-audit.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({key:report[key] for key in ('results_checked','passed','failed')}))


if __name__=='__main__':main()
