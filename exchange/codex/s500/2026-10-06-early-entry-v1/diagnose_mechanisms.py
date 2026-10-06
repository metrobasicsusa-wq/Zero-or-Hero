"""Descriptive paired diagnostics, never a rule or parameter optimizer."""
from pathlib import Path
from datetime import datetime,timezone
from decimal import Decimal
from itertools import combinations
from statistics import median,mean
import json,hashlib

ROOT=Path(__file__).resolve().parent
def read(n):return json.loads((ROOT/n).read_text())
def sha(n):return hashlib.sha256((ROOT/n).read_bytes()).hexdigest()
def save(n,o):(ROOT/n).write_text(json.dumps(o,indent=2)+'\n')
def register():
    assert not (ROOT/'isolated-results.json').exists()
    assert not (ROOT/'diagnostic-plan.json').exists()
    save('diagnostic-plan.json',{'registered_at':datetime.now(timezone.utc).isoformat(),
      'purpose':'Describe paired mechanism changes and complete-case selection, not choose strategies.',
      'paired_scope':'All three pairwise family combinations, ready in both; report all statuses before conditional both-closed comparison.',
      'paired_variants':'Every frozen fraction/cost and both data modes, no best subset.',
      'gate_mechanisms':['shared_membership','signal lead minutes','stop distance as fraction of signal close','prior-full-day-volume ratio'],
      'interpretation':'Pairs share stock-days and overlapping events, so not independent observations; paired ready and closed sets are retrospective selection. No p-value or holdout claim.',
      'design_sha256':sha('study-design.json'),'code_sha256':sha('diagnose_mechanisms.py')})

def main():
    plan=read('diagnostic-plan.json');gates=read('market-gates.json')['families']
    rows=read('isolated-results.json')['results']
    indexed={(r['family'],r['candidate_id'],r['data_mode'],r['variant_id']):r for r in rows}
    byfamily={f:{c['candidate_id']:c for c in d['rows'] if c['market_gate_pass'] is True} for f,d in gates.items()}
    pairs=[]
    for a,b in combinations(('OR5','OR15','OR30'),2):
        shared=sorted(set(byfamily[a])&set(byfamily[b]));metrics=[]
        for cid in shared:
            x,y=byfamily[a][cid],byfamily[b][cid]
            def width(c):return (Decimal(c['signal']['close_exact'])-Decimal(c['entry_reference']['stop_exact']))/Decimal(c['signal']['close_exact'])
            metrics.append({'candidate_id':cid,'signal_offset_a':x['signal']['minute_offset'],'signal_offset_b':y['signal']['minute_offset'],
              'earlier_signal_minutes_a_vs_b':y['signal']['minute_offset']-x['signal']['minute_offset'],
              'stop_width_signal_fraction_a':str(width(x)),'stop_width_signal_fraction_b':str(width(y))})
        finances=[]
        variants=sorted({(r['data_mode'],r['variant_id']) for r in rows})
        for mode,variant in variants:
            outcomes=[];closed_deltas=[]
            for cid in shared:
                x=indexed[(a,cid,mode,variant)];y=indexed[(b,cid,mode,variant)]
                both_closed=all(z['status']=='complete' and any(t['side']=='SELL' for t in z['trades']) for z in (x,y))
                delta=str(Decimal(x['profit'])-Decimal(y['profit'])) if both_closed else None
                if delta is not None:closed_deltas.append(float(delta))
                outcomes.append({'candidate_id':cid,'status_a':x['status'],'status_b':y['status'],'profit_a':x['profit'],'profit_b':y['profit'],
                  'both_entered_and_closed':both_closed,'paired_profit_a_minus_b':delta})
            finances.append({'data_mode':mode,'variant_id':variant,'shared_ready':len(shared),'both_entered_closed':len(closed_deltas),
              'paired_mean_pnl_difference_a_minus_b':mean(closed_deltas) if closed_deltas else None,
              'paired_median_pnl_difference_a_minus_b':median(closed_deltas) if closed_deltas else None,
              'a_better':sum(d>0 for d in closed_deltas),'a_worse':sum(d<0 for d in closed_deltas),'same':sum(d==0 for d in closed_deltas),
              'rows':outcomes})
        pairs.append({'family_a':a,'family_b':b,'shared_ready':len(shared),'only_a_ready':len(set(byfamily[a])-set(byfamily[b])),
          'only_b_ready':len(set(byfamily[b])-set(byfamily[a])),'median_signal_lead_minutes':median(m['earlier_signal_minutes_a_vs_b'] for m in metrics) if metrics else None,
          'gate_metrics':metrics,'paired_financials':finances})
    save('paired-diagnostics.json',{'generated_at':datetime.now(timezone.utc).isoformat(),'plan_sha256':sha('diagnostic-plan.json'),
      'gate_sha256':sha('market-gates.json'),'result_sha256':sha('isolated-results.json'),'pairs':pairs,
      'retrospective_complete_case_selection':True,'independent_validation':False,'new_rules_selected':False})

if __name__=='__main__':
    import sys
    register() if '--register' in sys.argv else main()
