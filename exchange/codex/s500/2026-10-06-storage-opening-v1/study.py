"""Frozen storage opening study. All trades are historical model observations."""
import copy
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT=Path(__file__).parent
NY=ZoneInfo('America/New_York')


def minute(value):
    h,m=map(int,value.split(':'));return h*60+m


def time_text(value):return f'{value//60:02}:{value%60:02}'


def opening_signal(bars, previous_close, rule):
    opening=[bars.get(m) for m in [570,575,580]]
    if not previous_close or not all(opening):return {'status':'missing_prior_or_opening'}
    gap=opening[0]['o']/previous_close-1
    if not .01 <= gap <= .20:return {'status':'gap_outside_fixed_range','gap':gap}
    high=max(b['h'] for b in opening)
    broken=None
    for m in range(585,660,5):
        if m not in bars:return {'status':'missing_signal_bar','gap':gap}
        b=bars[m]
        if rule=='breakout' and m<=625 and b['c']>high:
            return {'status':'signal','gap':gap,'opening_high':high,'signal_bar_start':m,
                    'decision_minute':m+5,'entry_minute':m+10}
        if rule=='retest' and broken is not None and b['l']<=high<b['c']:
            return {'status':'signal','gap':gap,'opening_high':high,'signal_bar_start':m,
                    'decision_minute':m+5,'entry_minute':m+10}
        if m<=625 and b['c']>high and broken is None:broken=m
        if rule=='breakout' and m==625:break
    return {'status':'no_trigger','gap':gap,'opening_high':high}


def load_inputs(protocol):
    data={};quality=[]
    calendar=json.loads((ROOT/'raw/calendar.json').read_text())
    for symbol in protocol['universe']+protocol['comparators']:
        days=defaultdict(dict)
        for b in json.loads((ROOT/'raw'/f'{symbol}.json').read_text())['bars']:
            t=datetime.fromisoformat(b['t'].replace('Z','+00:00')).astimezone(NY)
            m=t.hour*60+t.minute
            if not 570<=m<960:continue
            date=t.date().isoformat()
            assert m not in days[date], 'Duplicate bar'
            assert all(math.isfinite(b[k]) and b[k]>0 for k in ['o','h','l','c'])
            assert b['l']<=min(b['o'],b['c'])<=max(b['o'],b['c'])<=b['h']
            assert t.second==0 and m%5==0
            days[date][m]=b
        for session in calendar:
            date=session['date'];closing=minute(session['close'])
            missing=sorted(set(range(570,closing,5))-set(days[date]))
            quality.append({'symbol':symbol,'date':date,'expected_bars':(closing-570)//5,
                'available_session_bars':sum(570<=m<closing for m in days[date]),
                'missing_minutes':missing})
        data[symbol]=days
    return data,calendar,quality


def simulate(data, calendar, candidates, symbols, rule, exit_minute, cost, dates):
    cash=500.;peak=500.;drawdown=0.;ledger=[];trades=[];incomplete=None
    session_by_date={x['date']:x for x in calendar}
    for date in dates:
        row={'date':date,'cash_before':cash,'contributions':0.,'status':'no_signal'}
        if exit_minute>=minute(session_by_date[date]['close']):
            row['status']='known_early_close_exit_unavailable'
        else:
            if rule=='etf':
                choices=[{'symbol':symbols[0],'entry_minute':590,'gap':0.,'decision_minute':585}]
            else:
                choices=[dict(candidates[(date,symbol,rule)],symbol=symbol) for symbol in symbols
                         if candidates[(date,symbol,rule)]['status']=='signal']
                choices.sort(key=lambda x:(x['entry_minute'],-x['gap'],x['symbol']))
            if choices:
                selected=choices[0];symbol=selected['symbol'];bars=data[symbol][date]
                row.update(symbol=symbol,decision_time=time_text(selected['decision_minute']),
                           entry_time=time_text(selected['entry_minute']),exit_time=time_text(exit_minute))
                entry=bars.get(selected['entry_minute']);exit_bar=bars.get(exit_minute)
                if entry is None:
                    incomplete={'date':date,'symbol':symbol,'reason':'missing_selected_entry'}
                    row['status']='path_incomplete';ledger.append(row);break
                buy=entry['o']*(1+cost/10000)
                qty=math.floor(cash/buy)
                row.update(model_entry_price=buy,qty=qty)
                if qty==0:
                    row['status']='unaffordable_whole_share'
                elif exit_bar is None:
                    incomplete={'date':date,'symbol':symbol,'reason':'missing_selected_exit'}
                    row['status']='path_incomplete';ledger.append(row);break
                else:
                    sell=exit_bar['o']*(1-cost/10000)
                    pnl=qty*(sell-buy);cash+=pnl
                    row.update(status='historical_model_trade',model_exit_price=sell,pnl=pnl,
                               asset_return=sell/buy-1,entry_notional=qty*buy)
                    trades.append(dict(row))
        peak=max(peak,cash);drawdown=max(drawdown,1-cash/peak)
        row['cash_after']=cash;ledger.append(row)
    return {'ending_equity':None if incomplete else cash,'observed_equity_before_gap':cash,
        'total_contributions':500.,'retired_residuals':0.,'net_profit':None if incomplete else cash-500,
        'target_10000_reached':any(x.get('cash_after',0)>=10000 for x in ledger),
        'daily_close_max_drawdown':drawdown,'trade_count':len(trades),
        'winning_trades':sum(t['pnl']>0 for t in trades),
        'unaffordable_days':sum(x['status']=='unaffordable_whole_share' for x in ledger),
        'modeled_cost_total':sum((t['model_entry_price']/(1+cost/10000)+
                                 t['model_exit_price']/(1-cost/10000))*t['qty']*cost/10000 for t in trades),
        'incomplete':incomplete,'ledger':ledger}


def main():
    protocol=json.loads((ROOT/'protocol.json').read_text());data,calendar,quality=load_inputs(protocol)
    candidates={};rows=[];checks=[]
    dates=[x['date'] for x in calendar if '2026-07-01'<=x['date']<='2026-10-02']
    previous={calendar[i]['date']:calendar[i-1] for i in range(1,len(calendar))}
    for date in dates:
        for symbol in protocol['universe']:
            prior_session=previous[date];prior=data[symbol][prior_session['date']].get(minute(prior_session['close'])-5)
            prior_close=prior['c'] if prior else None
            for rule in protocol['hypotheses']:
                outcome=opening_signal(data[symbol][date],prior_close,rule)
                candidates[(date,symbol,rule)]=outcome
                rows.append({'date':date,'symbol':symbol,'rule':rule,**outcome})
                if outcome['status']=='signal':
                    altered=copy.deepcopy(data[symbol][date])
                    for m,b in altered.items():
                        if m>=outcome['decision_minute']:
                            for k in ['o','h','l','c']:b[k]*=7
                    assert opening_signal(altered,prior_close,rule)==outcome
                    checks.append({'date':date,'symbol':symbol,'rule':rule,'future_perturbation_passed':True})
    results=[]
    periods={'full':['2026-07-01','2026-10-02'],**protocol['periods']}
    for period,(start,end) in periods.items():
        selected_dates=[d for d in dates if start<=d<=end]
        variants=[(rule,protocol['universe']) for rule in protocol['hypotheses']]
        variants += [('etf',[s]) for s in protocol['comparators']]
        for rule,symbols in variants:
            for exit_text in protocol['exit_times']:
                for cost in protocol['cost_bps_each_side']:
                    result=simulate(data,calendar,candidates,symbols,rule,minute(exit_text),cost,selected_dates)
                    key=(symbols[0] if rule=='etf' else rule)+'-'+exit_text+'-'+str(cost)+'bp'
                    results.append({'id':key,'period':period,'rule':rule,'symbols':symbols,
                        'exit_time':exit_text,'cost_bps_each_side':cost,**result})
    summary={
        'method':'storage-opening-v1','protocol_sha256':hashlib.sha256((ROOT/'protocol.json').read_bytes()).hexdigest(),
        'input_manifest_sha256':hashlib.sha256((ROOT/'input-manifest.json').read_bytes()).hexdigest(),
        'calendar_sessions':len(dates),'cash_comparator_equity':500.,
        'all_candidate_count':len(rows),'signal_count':sum(x['status']=='signal' for x in rows),
        'paths':[{k:v for k,v in x.items() if k!='ledger'} for x in results],
        'all_results_including_failures_retained':True,'genuine_out_of_sample':False,'broker_orders_sent':0}
    (ROOT/'candidates.json').write_text(json.dumps(rows,indent=2)+'\n')
    (ROOT/'quality.json').write_text(json.dumps(quality,indent=2)+'\n')
    (ROOT/'paths.json').write_text(json.dumps(results,indent=2)+'\n')
    (ROOT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    validation={'input_bars_well_formed':True,'future_perturbation_count':len(checks),
                'future_perturbation_all_passed':True,'paths_count':len(results),
                'accounting_checks':0,'selected_signal_checks':0}
    for p in results:
        for row in p['ledger']:
            if row.get('status')=='historical_model_trade':
                assert abs(row['cash_after']-row['cash_before']-row['pnl'])<1e-8
                assert row['entry_notional']<=row['cash_before']+1e-8
                assert isinstance(row['qty'],int) and row['qty']>0
                validation['accounting_checks']+=1
                if p['rule']!='etf':
                    c=candidates[(row['date'],row['symbol'],p['rule'])]
                    assert c['decision_minute']<minute(row['entry_time'])
                    validation['selected_signal_checks']+=1
        if not p['incomplete']:
            assert abs(500+sum(x.get('pnl',0) for x in p['ledger'])-p['ending_equity'])<1e-8
    (ROOT/'validation.json').write_text(json.dumps(validation,indent=2)+'\n')
    print(json.dumps({'sessions':len(dates),'signals':summary['signal_count'],'validation':validation,
                     'summary':[{k:v for k,v in p.items() if k in ('id','period','ending_equity','trade_count','unaffordable_days','incomplete','daily_close_max_drawdown')} for p in results]},indent=2))


if __name__=='__main__':main()
