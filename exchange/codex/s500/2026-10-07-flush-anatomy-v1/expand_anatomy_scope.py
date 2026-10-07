"""Pre-freeze diagnostic-day extension; preserve the initial scope and outcomes."""
import gzip,hashlib,json
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).parent;PARENT=ROOT.parent/'s500-relative-flush-20261007';GRANDPARENT=ROOT.parent/'s500-flush-rebound-20261007'
INITIAL_NAMES=['focus-scope.json','focus-case-registry.json','annual-date-coverage.json','reuse-input-manifest.json']
DIAGNOSTIC_DATE='2026-06-18'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p):return json.loads(p.read_text())
def save(name,obj):(ROOT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
def info(p,name):return {'name':name,'sha256':sha(p),'bytes':p.stat().st_size}
def main():
 archive=ROOT/'scope-initial';archive.mkdir(exist_ok=True)
 historical=[]
 for name in INITIAL_NAMES:
  original=ROOT/name;saved=archive/name
  if not saved.exists():saved.write_bytes(original.read_bytes())
  historical.append(info(saved,'scope-initial/'+name))
 initial=load(archive/'focus-scope.json');manifest=load(archive/'reuse-input-manifest.json');focus=load(ROOT/'focus-case-registry.json');annual=load(ROOT/'annual-date-coverage.json')
 assert sha(ROOT/'focus-case-registry.json')==sha(archive/'focus-case-registry.json')
 assert sha(ROOT/'annual-date-coverage.json')==sha(archive/'annual-date-coverage.json')
 assert initial['focus_date_count']==11 and initial['focus_selected_cases']==1125
 assert initial['extra_SPY_down_selected_dates_without_complete_primary_return']==[DIAGNOSTIC_DATE]
 for source in manifest['source_files']:
  folder=PARENT if source['ancestor']=='parent' else GRANDPARENT;file=folder/source['name'];assert sha(file)==source['sha256'] and file.stat().st_size==source['bytes']
 registry={r['case_id']:r for r in load(GRANDPARENT/'selected-case-registry.json')['cases']};diagnostic=[]
 with gzip.open(PARENT/'classifications.jsonl.gz','rt') as stream:
  for line in stream:
   c=json.loads(line)
   if c['date']==DIAGNOSTIC_DATE:
    diagnostic.append({**registry[c['case_id']],'target_role':c['target_role'],'parent_classification_by_benchmark':c['classification_by_benchmark'],'date_role':'no_complete_outcome_diagnostic','diagnostic_date_selection_reason':'This date has SPY-down selected cases but no complete primary SPY-down target outcome in the parent summary; include all its selected cases to diagnose the omitted denominator.'})
 diagnostic.sort(key=lambda r:r['symbol']);assert len(diagnostic)==102 and sum(r['target_role']=='stock' for r in diagnostic)==100
 diagnostic_down_count=sum(r['target_role']=='stock' and r['parent_classification_by_benchmark']['SPY']['classification']=='market_down' for r in diagnostic);assert diagnostic_down_count==3
 combined=[{**r,'date_role':'outcome_support'} for r in focus['cases']]+diagnostic;combined.sort(key=lambda r:(r['date'],r['symbol']))
 assert len(combined)==len({r['case_id'] for r in combined})==1227
 assert sum(r['target_role']=='stock' for r in combined)==1203
 anatomy_dates=sorted(set(initial['focus_dates']+[DIAGNOSTIC_DATE]));assert len(anatomy_dates)==12
 source_day=next(r for r in load(GRANDPARENT/'minute-source-manifest.json')['days'] if r['date']==DIAGNOSTIC_DATE)
 old_reuse=load(PARENT/'reuse-input-manifest.json');old_day=next(r for r in old_reuse['normalized_days'] if r['date']==DIAGNOSTIC_DATE)
 source_name='private-inputs/minute-days/'+source_day['normalized_file'];source_file=GRANDPARENT/source_name
 assert sha(source_file)==source_day['normalized_sha256']==old_day['sha256']
 assert source_file.stat().st_size==source_day['normalized_bytes']==old_day['bytes']
 diagnostic_source={'date':DIAGNOSTIC_DATE,'ancestor':'grandparent',**info(source_file,source_name),'request_complete':source_day['request_complete'],'request_status':source_day['status'],'requested_symbol_count':source_day['requested_symbol_count']}
 anatomy_days=sorted(manifest['normalized_focus_days']+[diagnostic_source],key=lambda r:r['date']);assert len(anatomy_days)==12 and {d['date'] for d in anatomy_days}==set(anatomy_dates)
 for d in anatomy_days:
  file=GRANDPARENT/d['name'];assert sha(file)==d['sha256'] and file.stat().st_size==d['bytes']
 now=datetime.now(timezone.utc).isoformat()
 save('scope-initial-manifest.json',{'archived_at':now,'reason':'Preserve the initial 11-date scope before a pre-freeze extension to include the explicit no-complete-outcome diagnostic date. No feature/outcome analysis preceded this extension.','files':historical,'immutable_initial_focus_registry_and_annual_coverage':True,'new_market_api_requests':0})
 save('diagnostic-case-registry.json',{'created_at':now,'diagnostic_dates':[DIAGNOSTIC_DATE],'date_role':'no_complete_outcome_diagnostic','case_count':102,'stock_cases':100,'benchmark_cases':2,'all_same_day_groups_retained':True,'known_parent_SPY_down_selected_stock_cases':diagnostic_down_count,'known_parent_SPY_down_complete_primary_target_returns':0,'known_parent_SPY_down_complete_primary_pairs':0,'cases':diagnostic})
 save('anatomy-case-registry.json',{'created_at':now,'anatomy_dates':anatomy_dates,'outcome_support_dates':initial['focus_dates'],'no_complete_outcome_diagnostic_dates':[DIAGNOSTIC_DATE],'case_count':1227,'stock_cases':1203,'benchmark_cases':24,'all_same_day_groups_retained':True,'case_date_role_counts':{'outcome_support':1125,'no_complete_outcome_diagnostic':102},'cases':combined})
 scope={**initial,'scope_extended_at':now,'anatomy_dates':anatomy_dates,'anatomy_date_count':12,'anatomy_selected_cases':1227,'anatomy_stock_cases':1203,'anatomy_benchmark_cases':24,'anatomy_unique_symbols':len({r['symbol'] for r in combined}),'diagnostic_dates':[DIAGNOSTIC_DATE],'diagnostic_selected_cases':102,'diagnostic_stock_cases':100,'diagnostic_benchmark_cases':2,'anatomy_extension_reason':'Include the extra SPY-down-selected date without a complete primary outcome so the diagnostic denominator does not silently exclude noncompletion or no-signal cases. This reduces omission in the anatomy description; it does not remove the retrospective nature of choosing these dates.','anatomy_concentration_scope':'All SPY-down primary signal records on all 12 anatomy dates must be retained; no-signal cases remain cases, and missing outcomes must never be replaced by zero.','all_36_parent_variants_on_all_anatomy_dates_required':True,'initial_scope_archive_manifest':'scope-initial-manifest.json','new_features_computed':False,'new_returns_computed':False}
 scope['limitations']=initial['limitations']+['The diagnostic extension adds June 18 before anatomy features are computed; it does not establish a prospective date-selection rule or eliminate outcome-informed scope selection.']
 save('focus-scope.json',scope)
 manifest['scope_extended_at']=now;manifest['normalized_anatomy_days']=anatomy_days;manifest['normalized_diagnostic_days']=[diagnostic_source];manifest['runner_input_days_field']='normalized_anatomy_days';manifest['scope_history_manifest']=info(ROOT/'scope-initial-manifest.json','scope-initial-manifest.json');manifest['verification'].update({'normalized_anatomy_days':12,'normalized_diagnostic_days':1,'anatomy_registered_cases':1227,'initial_focus_case_registry_unchanged':True,'initial_annual_date_coverage_unchanged':True})
 manifest['generated_scope_files']=[{'ancestor':'current',**info(ROOT/n,n)} for n in ['focus-scope.json','focus-case-registry.json','annual-date-coverage.json','diagnostic-case-registry.json','anatomy-case-registry.json','scope-initial-manifest.json']]
 manifest['extension_method_sha256']=sha(Path(__file__));save('reuse-input-manifest.json',manifest)
 print(json.dumps({'anatomy_dates':anatomy_dates,'cases':1227,'stock_cases':1203,'benchmark_cases':24,'unique_symbols':scope['anatomy_unique_symbols'],'diagnostic_cases':102,'normalized_days':len(anatomy_days),'focus_registry_unchanged':True,'annual_coverage_unchanged':True,'new_features_computed':False,'new_returns_computed':False}),flush=True)
if __name__=='__main__':main()
