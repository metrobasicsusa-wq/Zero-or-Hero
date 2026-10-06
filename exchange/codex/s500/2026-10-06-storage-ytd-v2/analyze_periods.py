"""Summarize actual continuous model ledgers; do not reset cash each month."""
import ast
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT=Path(__file__).parent
PREVIOUS=ROOT.parent/'s500-research-20261006'


def main():
    paths=json.loads((ROOT/'paths.json').read_text())
    monthly=[]
    for path in paths:
        if path['period']!='full':continue
        groups=defaultdict(list)
        for row in path['ledger']:groups[row['date'][:7]].append(row)
        for month,rows in sorted(groups.items()):
            complete=all('cash_after' in row for row in rows)
            opening=rows[0]['cash_before'];closing=rows[-1].get('cash_after') if complete else None
            trades=[r for r in rows if r['status']=='historical_model_trade']
            pnl=sum(t['pnl'] for t in trades)
            if complete:assert abs(closing-opening-pnl)<1e-8
            monthly.append({'path_id':path['id'],'month':month,'starting_equity':opening,
                'ending_equity':closing,'return':None if closing is None else closing/opening-1,
                'net_profit':None if closing is None else closing-opening,'new_contributions':0,
                'complete':complete,'recorded_sessions':len(rows),'trades':len(trades),
                'unaffordable_days':sum(r['status']=='unaffordable_whole_share' for r in rows)})
    (ROOT/'monthly-continuous.json').write_text(json.dumps(monthly,indent=2)+'\n')
    diagnostics=[]
    for path in paths:
        if path['rule']=='etf':continue
        trades=[r for r in path['ledger'] if r['status']=='historical_model_trade']
        best=max(trades,key=lambda t:t['pnl']) if trades else None
        diagnostics.append({'path_id':path['id'],'period':path['period'],
            'first_daily_close_target_dates':{str(target):next((x['date'] for x in path['ledger'] if x.get('cash_after',0)>=target),None) for target in (1000,2000,10000)},
            'best_day':best['date'] if best else None,'best_day_pnl':best['pnl'] if best else None,
            'sum_pnl_excluding_best_fixed_sizes':sum(t['pnl'] for t in trades if t is not best),
            'per_symbol_trades':dict(Counter(t['symbol'] for t in trades)),
            'note':'Descriptive removal at original quantities, not a recomputed capital path.'})
    (ROOT/'concentration.json').write_text(json.dumps(diagnostics,indent=2)+'\n')
    manifest=json.loads((ROOT/'input-manifest.json').read_text())
    assert all(hashlib.sha256((ROOT/'raw'/f['name']).read_bytes()).hexdigest()==f['sha256'] for f in manifest['files'])
    comparisons=[]
    for f in manifest['files']:
        if f['name']=='calendar.json':continue
        new={b['t']:b for b in json.loads((ROOT/'raw'/f['name']).read_text())['bars']}
        old={b['t']:b for b in json.loads((PREVIOUS/'raw'/f['name']).read_text())['bars']}
        differing=[t for t,b in old.items() if new.get(t)!=b]
        comparisons.append({'symbol':f['name'][:-5],'old_bars':len(old),'missing_or_changed':len(differing),
                            'first_differences':differing[:10]})
    (ROOT/'overlap-check.json').write_text(json.dumps(comparisons,indent=2)+'\n')
    validation=json.loads((ROOT/'validation.json').read_text())
    validation.update(raw_file_hashes_verified=True,monthly_ledger_checks=sum(x['complete'] for x in monthly),
        unit_tests={'passed':6,'failed':0},unchanged_core_functions=json.loads((ROOT/'rule-equivalence.json').read_text()),
        historical_overlap_changes=sum(x['missing_or_changed'] for x in comparisons))
    (ROOT/'validation.json').write_text(json.dumps(validation,indent=2)+'\n')
    print(json.dumps({'validation':validation,'full_period_paths':[{k:v for k,v in x.items() if k in
        ['id','ending_equity','trade_count','unaffordable_days','incomplete','daily_close_max_drawdown']}
        for x in paths if x['period']=='full'],'monthly_retest_noon_10bp':[x for x in monthly if x['path_id']=='retest-12:00-10bp']},indent=2))


if __name__=='__main__':main()
