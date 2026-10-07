"""Independent reconstruction of historical supporting/diagnostic scope only."""
import gzip,hashlib,json
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parent
PRIOR=ROOT.parent/'s500-relative-flush-20261007'
BASE=ROOT.parent/'s500-flush-rebound-20261007'
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 primary=read(PRIOR/'primary-results.json')
 group=next(x for x in primary['groups'] if x['benchmark']=='SPY' and x['group']=='market_down')
 support={d for d,x in group['all']['date_means'].items() if x['net_n']>0}
 with gzip.open(PRIOR/'classifications.jsonl.gz','rt') as stream: classes={r['case_id']:r for r in map(json.loads,stream)}
 old=read(BASE/'selected-case-registry.json')['cases'];oldby={r['case_id']:r for r in old}
 calendar=read(BASE/'calendar-2026-ytd.json');days=[r['date'] for r in calendar]
 daily=defaultdict(list)
 for r in classes.values():daily[r['date']].append(r)
 selected={r['date'] for r in classes.values() if r['target_role']=='stock' and r['classification_by_benchmark']['SPY']['classification']=='market_down'}
 diagnostic=selected-support;all_dates=support|diagnostic
 assert len(support)==11 and diagnostic=={'2026-06-18'}
 combined=read(ROOT/'anatomy-case-registry.json');rows=combined['cases'];ids={r['case_id'] for r in rows}
 expected={r['case_id'] for r in old if r['date'] in all_dates};assert ids==expected and len(rows)==len(ids)==1227
 for r in rows:
  original=oldby[r['case_id']]
  for k,v in original.items():assert r[k]==v,(r['case_id'],k)
  c=classes[r['case_id']]
  assert r['target_role']==c['target_role'] and r['parent_classification_by_benchmark']==c['classification_by_benchmark']
  assert r['date_role']==('outcome_support' if r['date'] in support else 'no_complete_outcome_diagnostic')
 assert Counter(r['target_role'] for r in rows)=={'stock':1203,'benchmark':24}
 assert set(combined['outcome_support_dates'])==support and set(combined['no_complete_outcome_diagnostic_dates'])==diagnostic
 focus=read(ROOT/'focus-case-registry.json')['cases'];assert {r['case_id'] for r in focus}=={r['case_id'] for r in rows if r['date'] in support}
 annual=read(ROOT/'annual-date-coverage.json')['rows'];assert [r['date'] for r in annual]==days
 reasons=Counter()
 for r in annual:
  d=r['date'];c=daily[d];s=[v for v in c if v['target_role']=='stock']
  assert r['selected_cases']==len(c) and r['selected_stock_cases']==len(s)
  assert r['selected_benchmark_cases']==len(c)-len(s)
  for b in ['SPY','QQQ']:
   counts=Counter(v['classification_by_benchmark'][b]['classification'] or 'unknown' for v in s)
   assert r['classification_counts_by_benchmark'][b]=={k:counts[k] for k in ['market_down','market_not_down','unknown']}
  known=group['all']['date_means'].get(d,{})
  assert r['known_parent_SPY_down_primary_complete_target_returns']==known.get('net_n',0)
  assert r['known_parent_SPY_down_primary_complete_matched_pairs']==known.get('matched_excess_n',0)
  assert r['included_in_focus_dates']==(d in support)
  reasons['support' if d in support else 'diagnostic' if d in diagnostic else 'no_selected_SPY_down']+=1
 manifest=read(ROOT/'reuse-input-manifest.json');bound=manifest['normalized_anatomy_days'];assert {r['date'] for r in bound}==all_dates
 for rec in bound:
  f=BASE/rec['name'];assert sha(f)==rec['sha256'] and f.stat().st_size==rec['bytes']
  # Hash-only source audit: do not calculate unregistered new features.
 for rec in manifest['vendor_files']:
  f=ROOT/rec['name'];source=(BASE if rec['source_ancestor']=='grandparent' else PRIOR)/rec['source_name']
  assert sha(f)==rec['sha256'] and f.read_bytes()==source.read_bytes()
 result={'passed':True,'created_at':datetime.now(timezone.utc).isoformat(),'new_features_or_feature_return_splits_read':False,'annual_dates_reconstructed':len(days),'source_classification_rows_reconciled':len(classes),'main_support_dates':sorted(support),'diagnostic_dates':sorted(diagnostic),'annual_date_roles':dict(reasons),'case_counts':{'all':len(rows),'stock':1203,'benchmark':24,'main_support':len(focus),'diagnostic':len(rows)-len(focus)},'expected_all36_result_rows':len(rows)*36,'source_days_sha256_verified':len(bound),'original_registry_values_and_classifications_preserved':True,'files':[{'name':n,'sha256':sha(ROOT/n)} for n in ['anatomy-case-registry.json','focus-case-registry.json','focus-scope.json','annual-date-coverage.json','reuse-input-manifest.json','independent_scope_audit.py']]}
 (ROOT/'independent-scope-audit.json').write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps({k:v for k,v in result.items() if k not in ['files','main_support_dates']},sort_keys=True))
if __name__=='__main__':main()
