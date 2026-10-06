"""Daily broad-universe discovery. No broker orders; future data never rank signals."""
import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT=Path(__file__).parent
NY=ZoneInfo('America/New_York')


def feature(history,today):
    if len(history)!=20:return None
    dollars=statistics.median(x['c']*x['v'] for x in history)
    price=history[-1]['c']
    if not 5<=price<=500 or dollars<20_000_000:return None
    row={'previous_close':price,'median_prior20_dollar_volume':dollars,
         'prior20_high':max(x['h'] for x in history),
         'median_prior20_volume':statistics.median(x['v'] for x in history),
         'breakout20':False,'reversal':False,'status':'today_missing'}
    if today is None:return row
    ret=today['c']/price-1
    row.update(daily_return=ret,volume_ratio=today['v']/row['median_prior20_volume'] if row['median_prior20_volume'] else None,status='no_trigger')
    if abs(ret)>.20:row['status']='extreme_move_excluded';return row
    volume_pass=row['volume_ratio'] is not None and row['volume_ratio']>=1.5
    row['breakout20']=volume_pass and today['c']>row['prior20_high']
    row['reversal']=volume_pass and ret<=-.05 and today['c']>(today['h']+today['l'])/2
    if row['breakout20'] or row['reversal']:row['status']='signal'
    return row


def run_path(dates,signals,data,rule,cost):
    cash=500.;peak=500.;dd=0.;rows=[];incomplete=None
    for date in dates:
        choices=signals[date][rule]
        row={'date':date,'cash_before':cash,'status':'no_signal','new_contributions':0}
        if choices:
            pick=choices[0];symbol=pick['symbol'];row.update(symbol=symbol,signal_date=pick['signal_date'],prior_liquidity_rank=pick['rank'])
            b=data[symbol].get(date)
            if b is None:
                row['status']='missing_selected_execution_bar';incomplete={'date':date,'symbol':symbol};rows.append(row);break
            buy=b['o']*(1+cost/10000);qty=math.floor(cash/buy)
            row.update(qty=qty,model_entry_price=buy)
            if qty==0:row['status']='unaffordable_whole_share'
            else:
                sell=b['c']*(1-cost/10000);pnl=qty*(sell-buy);cash+=pnl
                row.update(status='historical_model_trade',model_exit_price=sell,pnl=pnl,notional=qty*buy)
        peak=max(peak,cash);dd=max(dd,1-cash/peak);row['cash_after']=cash;rows.append(row)
    trades=[r for r in rows if r['status']=='historical_model_trade']
    return {'id':rule+'-'+str(cost)+'bp','rule':rule,'cost_bps_each_side':cost,
        'start_equity':500,'ending_equity':cash if incomplete is None else None,
        'last_observed_equity':cash,'incomplete':incomplete,
        'total_contributions':500,'net_profit':cash-500 if incomplete is None else None,
        'trade_count':len(trades),'win_count':sum(r['pnl']>0 for r in trades),
        'unaffordable_days':sum(r['status']=='unaffordable_whole_share' for r in rows),
        'daily_close_max_drawdown':dd,'ledger':rows,
        'first_target_dates':{str(target):next((r['date'] for r in rows if r.get('cash_after',0)>=target),None) for target in (1000,2000,10000)}}


def main():
    protocol=json.loads((ROOT/'protocol.json').read_text());assets=json.loads((ROOT/'universe.json').read_text())
    calendar=json.loads((ROOT/'raw/calendar.json').read_text());dates=[d['date'] for d in calendar]
    evaluation=[d for d in dates if protocol['evaluation_start']<=d<=protocol['evaluation_end']]
    data={};invalid=[]
    for file in sorted((ROOT/'raw').glob('batch-*.json')):
        for symbol,bars in json.loads(file.read_text())['bars'].items():
            assert symbol not in data;series={}
            for b in bars:
                date=datetime.fromisoformat(b['t'].replace('Z','+00:00')).astimezone(NY).date().isoformat()
                if date in series:raise ValueError('duplicate_daily_bar')
                if not (all(math.isfinite(b[k]) and b[k]>0 for k in ['o','h','l','c']) and b['v']>=0
                        and b['l']<=min(b['o'],b['c'])<=max(b['o'],b['c'])<=b['h']):
                    invalid.append({'symbol':symbol,'date':date,'reason':'invalid_bar'});continue
                series[date]=b
            data[symbol]=series
    assert set(data)=={a['symbol'] for a in assets}
    coverage=[]
    for asset in assets:
        symbol=asset['symbol'];seen=sorted(d for d in data[symbol] if d in evaluation)
        coverage.append({'symbol':symbol,'directory_status_now':asset['status'],
            'evaluation_bars':len(seen),'first_date':seen[0] if seen else None,'last_date':seen[-1] if seen else None,
            'missing_dates':[d for d in evaluation if d not in data[symbol]],
            'historical_membership_known':False})
    signals={d:{'breakout20':[],'reversal':[]} for d in evaluation}
    candidates=[];daily=[];eligibility=Counter();trigger_symbols=set();future_checks=0
    for date in [d for d in dates if '2025-12-31'<=d<=protocol['evaluation_end']]:
        i=dates.index(date);window=dates[i-20:i]
        if len(window)!=20:continue
        ranked=[];reasons=Counter()
        for asset in assets:
            symbol=asset['symbol'];series=data[symbol]
            if any(d not in series for d in window):reasons['prior_history_incomplete']+=1;continue
            f=feature([series[d] for d in window],series.get(date))
            if f is None:reasons['price_or_liquidity_filtered']+=1;continue
            ranked.append({'symbol':symbol,'directory_status_now':asset['status'],**f})
        ranked.sort(key=lambda x:(-x['median_prior20_dollar_volume'],x['symbol']))
        reasons['eligible_outside_top100']=max(0,len(ranked)-100)
        top=ranked[:100];next_date=dates[i+1] if i+1<len(dates) else None
        for rank,row in enumerate(top,1):
            row.update(signal_date=date,rank=rank,execution_date=next_date)
            candidates.append(row);eligibility[row['symbol']]+=1
            if row['breakout20'] or row['reversal']:
                trigger_symbols.add(row['symbol'])
                # The kernel receives only the prior20 and signal-day bar.
                # Adding arbitrary next-day prices must not alter the signal.
                series=data[row['symbol']];history=[series[d] for d in window]
                first=feature(history,series.get(date));copied=dict(series)
                if next_date:
                    copied[next_date]={'o':1,'h':99999,'l':.01,'c':50000,'v':10**12}
                    assert first==feature([copied[d] for d in window],copied.get(date))
                    future_checks+=1
            if next_date in signals:
                for rule in signals[next_date]:
                    if row[rule]:signals[next_date][rule].append(row)
        daily.append({'signal_date':date,'eligible_count':len(ranked),'top_count':len(top),
                      'exclusion_counts':dict(reasons),
                      'breakout_signals':sum(x['breakout20'] for x in top),
                      'reversal_signals':sum(x['reversal'] for x in top)})
    paths=[run_path(evaluation,signals,data,rule,cost) for rule in protocol['signal_rules'] for cost in protocol['screen']['stock_costs_bps_each_side']]
    monthly=[];accounting=0
    for p in paths:
        groups=defaultdict(list)
        for row in p['ledger']:
            groups[row['date'][:7]].append(row)
            if row['status']=='historical_model_trade':
                assert abs(row['cash_after']-row['cash_before']-row['pnl'])<1e-8
                assert row['notional']<=row['cash_before']+1e-8 and isinstance(row['qty'],int)
                accounting+=1
        for month,rows in groups.items():
            end=rows[-1].get('cash_after');start=rows[0]['cash_before']
            if end is not None:assert abs(end-start-sum(r.get('pnl',0) for r in rows))<1e-8
            monthly.append({'path_id':p['id'],'month':month,'start':start,'end':end,
                            'return':None if end is None else end/start-1,'new_contributions':0,
                            'trades':sum(r['status']=='historical_model_trade' for r in rows)})
        if p['incomplete'] is None:assert abs(500+sum(r.get('pnl',0) for r in p['ledger'])-p['ending_equity'])<1e-8
    current=[c for c in candidates if c['signal_date']==protocol['evaluation_end']]
    summary={'id':protocol['id'],'sessions':len(evaluation),'directory_symbols':len(assets),
        'status_counts':dict(Counter(a['status'] for a in assets)),
        'symbols_with_ytd_data':sum(x['evaluation_bars']>0 for x in coverage),
        'symbols_no_ytd_data':sum(x['evaluation_bars']==0 for x in coverage),
        'inactive_with_ytd_data':sum(x['evaluation_bars']>0 and x['directory_status_now']=='inactive' for x in coverage),
        'symbols_ever_in_top100':len(eligibility),'symbols_ever_triggering':len(trigger_symbols),
        'top100_candidate_rows':len(candidates),'signal_rows':sum(x['breakout20'] or x['reversal'] for x in candidates),
        'missing_or_invalid_bar_count':len(invalid),'total_daily_bars':sum(len(x) for x in data.values()),
        'paths':[{k:v for k,v in p.items() if k!='ledger'} for p in paths],
        'future_perturbation_checks':future_checks,'accounting_checks':accounting,
        'not_point_in_time_universe':True,'not_options_returns':True,'broker_orders_sent':0}
    files={'summary.json':summary,'coverage.json':coverage,'invalid-bars.json':invalid,'candidates.json':candidates,
           'daily-screen.json':daily,'paths.json':paths,'monthly-continuous.json':monthly,
           'latest-completed-session-watchlist.json':current,
           'universe-participation.json':[{'symbol':s,'top100_days':n,'ever_triggered':s in trigger_symbols} for s,n in sorted(eligibility.items())]}
    for name,body in files.items():(ROOT/name).write_text(json.dumps(body,indent=2)+'\n')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
