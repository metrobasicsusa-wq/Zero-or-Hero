"""All-model diagnostics, preserving failures and dependent-model caveats."""
import json,collections,statistics,itertools,hashlib
from pathlib import Path
ROOT=Path(__file__).parent

def main():
    paths=json.loads((ROOT/'paths.json').read_text());complete=[p for p in paths if p['status']=='provisional_complete'];incomplete=[p for p in paths if p['status']=='incomplete']
    checks=collections.Counter();paired=[];rows=[]
    for p in paths:
        assert p['initial_funding']==500 and p['additional_funding']==0
        prev=500
        for r in p['daily']:
            assert abs(r['equity']-r['cash']-r['dividend_receivable']-r['holding_value_after_exit_cost'])<1e-7
            assert abs(r['daily_pnl']-(r['equity']-prev))<1e-7
            assert r['cash']>=-1e-7
            prev=r['equity'];checks['daily_funding_identity']+=1
        for t in p['trades']:
            assert abs(t['pnl']-(t['exit_credit']+t['dividends']-t['entry_debit']))<1e-7
            assert t['signal_date']<t['entry_date']<=t['exit_date']
            assert t['holding_sessions']==p['parameters']['holding_sessions']
            checks['trade_accounting']+=1
        for a,b in zip(p['entries'],p['entries'][1:]):
            priortrade=next(t for t in p['trades'] if t['entry_date']==a['entry_date'])
            assert priortrade['exit_date']<b['entry_date'];checks['no_overlap_or_exit_day_reentry']+=1
        if p['status']=='provisional_complete':
            assert abs(p['final_equity']-(500+p['closed_pnl']+p['open_liquidation_pnl']))<1e-6;checks['whole_path_funding']+=1
        monthly=p['monthly'];q=[]
        for quarter,g in itertools.groupby(monthly,key=lambda r:(int(r['month'][5:7])-1)//3+1):
            m=list(g);q.append({'quarter':quarter,'start_equity':m[0]['start_equity'],'end_equity':m[-1]['end_equity'],'pnl':m[-1]['end_equity']-m[0]['start_equity'],'partial_quarter':quarter==4 or p['status']=='incomplete' and m[-1]==monthly[-1]})
        wins=sorted([t for t in p['trades'] if t['pnl']>0],key=lambda t:-t['pnl']);losses=[t for t in p['trades'] if t['pnl']<0]
        contribution=sum(t['pnl'] for t in wins[:5]);closednet=sum(t['pnl'] for t in p['trades'])
        rows.append({'id':p['id'],'quarters_continuous_not_resets':q,'positive_full_months':sum(m['pnl']>0 for m in monthly if m['month']<'2026-10' and not(p['status']=='incomplete' and m==monthly[-1])),'profit_factor':sum(t['pnl'] for t in wins)/-sum(t['pnl'] for t in losses) if losses else None,'top5_fixed_trade_contribution':contribution,'remaining_fixed_closed_pnl':closednet-contribution,'attribution_is_not_counterfactual_rerun':True,'largest_wins':[{'symbol':t['symbol'],'entry_date':t['entry_date'],'exit_date':t['exit_date'],'pnl':t['pnl']} for t in wins[:5]],'flags':p['flags'],'skips':p['skips']})
    index={p['id']:p for p in paths}
    for p in paths:
        if p['parameters']['cost_bps']!=10:continue
        other=index[p['id'].removesuffix('c10')+'c25']
        paired.append({'id_10bps':p['id'],'id_25bps':other['id'],'status_10':p['status'],'status_25':other['status'],'equity_10':p['final_equity'],'equity_25':other['final_equity'],'both_complete_and_profitable':all(x['status']=='provisional_complete' and x['final_equity']>500 for x in [p,other]),'note':'Differences include integer sizing, affordability and later selections, not just fees.'})
    groups={}
    for family in sorted(set(p['parameters']['family'] for p in paths)):
        group=[p for p in paths if p['parameters']['family']==family];finished=[p for p in group if p['status']=='provisional_complete']
        groups[family]={'paths':len(group),'complete':len(finished),'profitable':sum(p['final_equity']>500 for p in finished),'best':max((p['final_equity'] for p in finished),default=None),'median':statistics.median([p['final_equity'] for p in finished]) if finished else None,'worst':min((p['final_equity'] for p in finished),default=None)}
    payload={'checks':dict(checks),'all_complete_results_are_provisional':True,'source_sha256':hashlib.sha256((ROOT/'paths.json').read_bytes()).hexdigest(),'incomplete_reasons':dict(collections.Counter(p['incomplete']['reason'] for p in incomplete)),'groups':groups,'paired_costs':paired,'all_path_diagnostics':rows,'top_complete_ids':[p['id'] for p in sorted(complete,key=lambda p:-p['final_equity'])[:12]],'bottom_complete_ids':[p['id'] for p in sorted(complete,key=lambda p:p['final_equity'])[:12]],'limitations':['Same historical sample and strongly dependent192candidate search. Best outcome is exploratory, not evidence of an edge.','Dividend source currency missing and process-date coverage incomplete. Market-data corporate actions and universe not certified point-in-time.','Incomplete paths never ranked using stale last marked equity.','Fixed-trade concentration arithmetic does not recalculate later trading or affordability.']}
    (ROOT/'diagnostics.json').write_text(json.dumps(payload,indent=2)+'\n')
    print(json.dumps({'checks':dict(checks),'incomplete_reasons':payload['incomplete_reasons'],'groups':groups,'both_cost_profitable_pairs':sum(p['both_complete_and_profitable'] for p in paired)},indent=2))
if __name__=='__main__':main()
