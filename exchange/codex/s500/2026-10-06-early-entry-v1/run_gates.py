"""Evaluate all registered candidates using only each family's causal prefix."""
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
import hashlib
import json

ROOT=Path(__file__).resolve().parent
NEWS=ROOT.parent/'s500-news-20261006'
def read(p): return json.loads(p.read_text())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(n,o): (ROOT/n).write_text(json.dumps(o,separators=(',',':'))+'\n')

def main():
    from early_gates import evaluate_candidate
    assert not (ROOT/'market-gates.json').exists()
    design=read(ROOT/'study-design.json')
    for n in ['candidates.json','minute-market.json','market-gates.json']:
        assert sha(NEWS/n)==design['input_sha256']['news/'+n]
    validation=read(ROOT/'gate-pre-run-validation.json')
    assert validation['failed_tests']==0 and validation['gate_tests_passed']>0
    assert validation['early_gates_sha256']==sha(ROOT/'early_gates.py')
    candidates=read(NEWS/'candidates.json')['candidates']
    assert len(candidates)==1079 and len({c['candidate_id'] for c in candidates})==1079
    market=read(NEWS/'minute-market.json')
    families={}
    for family in design['families']:
        rows=[]
        for candidate in candidates:
            row=evaluate_candidate(candidate,market.get(candidate['date'],{}).get(candidate['symbol'],{}),family['opening_minutes'])
            row.update(candidate_id=candidate['candidate_id'],chronological_index=candidate['chronological_index'])
            rows.append(row)
        families[family['id']]={'rows':rows,'counts':{
          'pass':sum(r['market_gate_pass'] is True for r in rows),
          'fail':sum(r['market_gate_pass'] is False for r in rows),
          'unknown':sum(r['market_gate_pass'] is None for r in rows)},
          'status_counts':dict(Counter(r['market_gate_status'] for r in rows))}
    # OR30 must preserve the parent qualification, signal and entry coordinates.
    old={r['candidate_id']:r for r in read(NEWS/'market-gates.json')['rows']}
    changes=[]
    for r in families['OR30']['rows']:
        parent=old[r['candidate_id']]
        if r['market_gate_pass']!=parent['market_gate_pass'] or (r.get('signal') or {}).get('minute_offset')!=(parent.get('signal') or {}).get('minute_offset'):
            changes.append(r['candidate_id'])
    assert not changes,changes
    save('market-gates.json',{'generated_at':datetime.now(timezone.utc).isoformat(),
      'families':families,'candidate_count_per_family':1079,'total_gate_rows':3237,
      'input_sha256':{'study-design.json':sha(ROOT/'study-design.json'),'early_gates.py':sha(ROOT/'early_gates.py'),
                      'run_gates.py':sha(ROOT/'run_gates.py'),'gate-pre-run-validation.json':sha(ROOT/'gate-pre-run-validation.json')},
      'or30_parent_qualification_signal_changes':changes,'returns_computed':False,'news_selection_applied':False,'broker_orders_sent':0})
    summary={'generated_at':datetime.now(timezone.utc).isoformat(),'families':{f:{k:v for k,v in d.items() if k!='rows'} for f,d in families.items()},
      'all_ready_union_count':len({r['candidate_id'] for d in families.values() for r in d['rows'] if r['market_gate_pass'] is True}),
      'or30_parent_qualification_signal_changes':changes,'gate_sha256':sha(ROOT/'market-gates.json')}
    save('gate-summary.json',summary)
    print(json.dumps(summary,indent=2))

if __name__=='__main__':main()
