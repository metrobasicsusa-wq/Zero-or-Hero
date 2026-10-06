"""Exploratory $500 whole-share multi-day matrix; no broker/order capabilities."""
from __future__ import annotations
import json, math, itertools, collections, datetime, hashlib
from pathlib import Path
ROOT = Path(__file__).resolve().parent
FAMILIES = ['breakout20','momentum20','trend_pullback','reclaim_down','acceleration3','volume_ignition']

def valid_date(d):
    try: return datetime.date.fromisoformat(d).isoformat() == d
    except (ValueError, TypeError): return False

def finite(x):
    return isinstance(x,(int,float)) and math.isfinite(x)

def action_index(document):
    duplicates = {i for group in document.get('semantic_duplicate_groups',[]) for i in group['row_indices']}
    out = collections.defaultdict(list)
    for i,row in enumerate(document['rows']):
        r = dict(row)
        r['_duplicate'] = i in duplicates
        if r.get('symbol') and valid_date(r.get('ex_date')):
            out[(r['symbol'],r['ex_date'])].append(r)
    return out

def valid_bar(b):
    return b and all(finite(b.get(k)) and b[k]>0 for k in ['o','h','l','c']) and b['l'] <= min(b['o'],b['c']) <= max(b['o'],b['c']) <= b['h']

def ordinary_dividend(a):
    return (not a.get('_duplicate') and a.get('foreign') is False and a.get('special') is False
            and not a.get('sub_type') and not a.get('due_bill_on_date') and not a.get('due_bill_off_date')
            and valid_date(a.get('ex_date')) and valid_date(a.get('payable_date'))
            and a['ex_date'] <= a['payable_date'] and finite(a.get('cash_rate')) and a['cash_rate']>=0
            and a.get('currency') in [None,'USD'])

def run_path(market, dates, candidates, actions, family, ranking, hold, fraction, bps):
    cost=bps/10000.0; cash=500.0; position=None; receivables=[]; trades=[]; entries=[]; daily=[]; skips=[]; flags=[]
    last_equity=peak=500.0; max_dd=0.0; incomplete=None
    milestones={str(x):None for x in [1000,2000,10000]}
    by_date=collections.defaultdict(list)
    for r in candidates:
        if family in r['signals']: by_date[r['date']].append(r)
    for rows in by_date.values():
        rows.sort(key=(lambda r:(-r['liquidity'],r['symbol'])) if ranking=='prior_liquidity' else (lambda r:(-r['signals'][family],-r['liquidity'],r['symbol'])))
    all_dates=sorted(set(dates) | set(by_date))
    prior={d:all_dates[i-1] for i,d in enumerate(all_dates) if i>0}
    for di,day in enumerate(dates):
        ex_events=[]; day_dividends=0.; payout=0.; split_factor=1.; entered=False; exited=False
        pending=[]
        for r in receivables:
            if r['payable_date']<=day: cash+=r['amount']; payout+=r['amount']
            else: pending.append(r)
        receivables=pending
        if position:
            symbol=position['symbol']; events=actions.get((symbol,day),[])
            splits=[a for a in events if a['type'] in ['forward_split','reverse_split']]
            dividends=[a for a in events if a['type']=='cash_dividend']
            if len(splits)>1 or len(dividends)>1 or (splits and dividends):
                incomplete={'date':day,'symbol':symbol,'reason':'ambiguous_multiple_actions_or_split_plus_dividend'}; break
            if splits:
                a=splits[0]; old=a.get('old_rate'); new=a.get('new_rate')
                if a.get('_duplicate') or not finite(old) or not finite(new) or old<=0 or new<=0 or (a.get('new_symbol') and a['new_symbol']!=symbol) or ('new_symbol' in a and not a['new_symbol']):
                    incomplete={'date':day,'symbol':symbol,'reason':'unresolved_split'};break
                split_factor=new/old; newqty=position['qty']*split_factor
                if abs(newqty-round(newqty))>1e-8:
                    incomplete={'date':day,'symbol':symbol,'reason':'fractional_split_cash_in_lieu_unknown','pre_split_qty':position['qty'],'ratio':split_factor};break
                position['qty']=int(round(newqty)); ex_events.append({'type':'split','ratio':split_factor,'qty_after':position['qty']})
            bad=[a for a in dividends if not ordinary_dividend(a)]
            if bad:
                incomplete={'date':day,'symbol':symbol,'reason':'unsupported_dividend','count':len(bad)};break
            for a in dividends:
                amount=position['qty']*a['cash_rate']; day_dividends+=amount; position['dividends']+=amount
                record={'symbol':symbol,'ex_date':day,'payable_date':a['payable_date'],'amount':amount,'currency_assumption':'USD_not_source_verified'}
                if a['payable_date']<=day: cash+=amount;payout+=amount
                else: receivables.append(record)
                ex_events.append({'type':'dividend_receivable','amount':amount,'payable_date':a['payable_date']})
        if not position:
            signal_day=prior.get(day)
            ranked=by_date.get(signal_day,[])
            budget=cash*fraction
            selected=next((r for r in ranked if r['close']*(1+cost)<=budget),None)
            if selected:
                symbol=selected['symbol']; bar=market.get(symbol,{}).get(day)
                if not valid_bar(bar): incomplete={'date':day,'symbol':symbol,'reason':'missing_or_invalid_selected_entry_bar'};break
                qty=int(math.floor(budget/(bar['o']*(1+cost))+1e-12))
                if qty<=0:
                    skips.append({'date':day,'signal_date':signal_day,'symbol':symbol,'reason':'next_open_unaffordable','budget':budget})
                else:
                    debit=qty*bar['o']*(1+cost); cash-=debit
                    position={'symbol':symbol,'signal_date':signal_day,'entry_date':day,'entry_index':di,'entry_qty':qty,'qty':qty,'entry_price':bar['o'],'entry_debit':debit,'dividends':0.,'signal_strength':selected['signals'][family], 'selection_known_close':selected['close']}
                    entries.append(dict(position)); entered=True
            elif ranked:
                skips.append({'date':day,'signal_date':signal_day,'reason':'no_prior_close_affordable_signal','signals':len(ranked),'budget':budget})
        marked_value=0.
        if position:
            symbol=position['symbol']; bar=market.get(symbol,{}).get(day)
            if not valid_bar(bar): incomplete={'date':day,'symbol':symbol,'reason':'missing_or_invalid_held_mark_or_exit'};break
            previous=market.get(symbol,{}).get(prior.get(day))
            if previous and previous.get('c',0)>0:
                # Split on an entry day also needs adjustment for this diagnostic.
                diagnostic_factor=1.
                for a in actions.get((symbol,day),[]):
                    if a['type'] in ['forward_split','reverse_split'] and finite(a.get('new_rate')) and finite(a.get('old_rate')) and a['old_rate']>0: diagnostic_factor*=a['new_rate']/a['old_rate']
                ret=bar['c']/(previous['c']/diagnostic_factor)-1
                if abs(ret)>.25: flags.append({'date':day,'symbol':symbol,'type':'absolute_adjusted_close_return_gt25pct','return':ret})
            marked_value=position['qty']*bar['c']*(1-cost)
            if di-position['entry_index']+1>=hold:
                cash+=marked_value
                trade=dict(position,exit_date=day,exit_price=bar['c'],exit_qty=position['qty'],exit_credit=marked_value,holding_sessions=di-position['entry_index']+1,pnl=marked_value+position['dividends']-position['entry_debit'])
                trades.append(trade);position=None;marked_value=0.;exited=True
        pending_total=sum(r['amount'] for r in receivables)
        equity=cash+pending_total+marked_value
        if cash < -1e-7: raise AssertionError('negative cash/implicit borrowing')
        peak=max(peak,equity);max_dd=max(max_dd,1-equity/peak)
        for target in milestones:
            if milestones[target] is None and equity>=int(target):milestones[target]=day
        daily.append({'date':day,'cash':cash,'dividend_receivable':pending_total,'holding_value_after_exit_cost':marked_value,'equity':equity,'daily_pnl':equity-last_equity,'symbol':position['symbol'] if position else None,'qty':position['qty'] if position else 0,'entered':entered,'exited':exited,'ex_events':ex_events,'dividend_entitlement_today':day_dividends,'dividend_cash_paid':payout})
        last_equity=equity
    monthly=[]
    for month,group in itertools.groupby(daily,key=lambda d:d['date'][:7]):
        rows=list(group);start=monthly[-1]['end_equity'] if monthly else 500.
        monthly.append({'month':month,'start_equity':start,'end_equity':rows[-1]['equity'],'pnl':rows[-1]['equity']-start,'return':rows[-1]['equity']/start-1 if start else None,'sessions':len(rows)})
    closed_pnl=sum(t['pnl'] for t in trades)
    open_pnl=0 if not position or incomplete else (daily[-1]['holding_value_after_exit_cost']+position['dividends']-position['entry_debit'])
    if not incomplete and abs(last_equity-(500+closed_pnl+open_pnl))>1e-6:raise AssertionError('funding identity failed')
    return {'id':f'{family}__{ranking}__h{hold}__a{fraction:g}__c{bps}', 'parameters':{'family':family,'ranking':ranking,'holding_sessions':hold,'capital_fraction':fraction,'cost_bps':bps}, 'status':'incomplete' if incomplete else 'provisional_complete','incomplete':incomplete,'final_equity':None if incomplete else last_equity,'last_marked_equity':last_equity,'last_mark_date':daily[-1]['date'] if daily else None,'max_liquidation_drawdown':max_dd,'milestones':milestones,'closed_trades':len(trades),'wins':sum(t['pnl']>0 for t in trades),'initial_funding':500,'additional_funding':0,'dividend_currency':'USD_assumed_not_verified','open_position_censored':position if not incomplete else None,'incomplete_position_snapshot':dict(position) if position and incomplete else None,'cash_at_interrupt':cash if incomplete else None,'closed_pnl':closed_pnl,'open_liquidation_pnl':open_pnl if not incomplete else None,'receivables':receivables,'trades':trades,'entries':entries,'daily':daily,'monthly':monthly,'skips':skips,'flags':flags}

def main():
    from features import build_features, load_market
    # Feature cache preserves the exact screening inputs for all paths.
    cache=ROOT/'features.json'
    if cache.exists():
        diagnostic=json.loads((ROOT/'features-diagnostics.json').read_text())
        for name, expected in diagnostic['input_sha256'].items():
            if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=expected: raise ValueError('stale feature cache: '+name)
        classification=ROOT.parent/'s500-broad-20261006/current-instrument-classification.json'
        if hashlib.sha256(classification.read_bytes()).hexdigest()!=diagnostic['classification_sha256']: raise ValueError('stale classification cache')
        market,calendar=load_market()
        doc=json.loads(cache.read_text());candidates=doc['candidates'] if isinstance(doc,dict) else doc
    else: market,calendar,candidates=build_features()
    dates=[d for d in calendar if '2026-01-01'<=d<='2026-10-05']
    actions=action_index(json.loads((ROOT/'company-actions.json').read_text()))
    paths=[]
    for args in itertools.product(FAMILIES,['prior_liquidity','signal_strength'],[1,3,5,10],[.5,1.],[10,25]):
        paths.append(run_path(market,dates,candidates,actions,*args))
    (ROOT/'paths.json').write_text(json.dumps(paths,separators=(',',':'))+'\n')
    summaries=[{k:v for k,v in p.items() if k not in ['daily','monthly','trades','entries','receivables','skips','flags']} | {'skip_count':len(p['skips']),'flag_count':len(p['flags'])} for p in paths]
    complete=[p for p in paths if p['status']=='provisional_complete']
    summary={'created_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'period':[dates[0],dates[-1]],'sessions':len(dates),'paths':len(paths),'complete_provisional':len(complete),'incomplete':len(paths)-len(complete),'initial_funding_per_path':500,'additional_funding_all_paths':0,'candidate_rows':len(candidates),'unique_pool_symbols':len(set(r['symbol'] for r in candidates)),'signal_rows':sum(bool(r['signals']) for r in candidates),'milestone_counts':{x:sum(p['milestones'][x] is not None for p in complete) for x in ['1000','2000','10000']},'profitable_complete':sum(p['final_equity']>500 for p in complete),'best_complete_id':max(complete,key=lambda p:p['final_equity'])['id'] if complete else None,'results':summaries}
    (ROOT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps({k:v for k,v in summary.items() if k!='results'},indent=2))
if __name__=='__main__':main()
