"""Offline source/scope binding only. No new feature or outcome calculations."""
import gzip,hashlib,json
from pathlib import Path
from collections import Counter,defaultdict
from datetime import datetime,timezone
ROOT=Path(__file__).parent
PARENT=ROOT.parent/'s500-relative-flush-20261007'
GRANDPARENT=ROOT.parent/'s500-flush-rebound-20261007'
EXPECTED_PARENT_COMMIT='a73297d47a6076e4f4a4725eab494f0308d7fe33'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p):return json.loads(p.read_text())
def save(name,obj):(ROOT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
def info(path,ancestor,name):return {'ancestor':ancestor,'name':name,'sha256':sha(path),'bytes':path.stat().st_size}
def main():
 publication=load(PARENT/'publication-verification.json');assert publication['commit']==EXPECTED_PARENT_COMMIT
 assert publication['force'] is False and publication['only_expected_paths_changed'] is True and publication['exact_readback_files']==81 and len(publication['files'])==81
 public={r['path']:r for r in publication['files']}
 for record in publication['files']:
  f=PARENT/'publication'/record['path'];assert record['exact_readback'] is True and sha(f)==record['sha256'] and f.stat().st_size==record['bytes']
 prefix='exchange/codex/s500/2026-10-07-market-relative-flush-v1/'
 # Derive the actual prefix from a unique published method filename.
 methodpaths=[p for p in public if p.endswith('/classify_inputs.py')];assert len(methodpaths)==1;prefix=methodpaths[0][:-len('classify_inputs.py')]
 def verify_parent(name):
  published_name=name if prefix+name in public else name+'.gz'
  f=PARENT/'publication'/prefix/published_name;content=f.read_bytes()
  if published_name.endswith('.gz') and not name.endswith('.gz'):content=gzip.decompress(content)
  assert content==(PARENT/name).read_bytes(),(name,'local/published mismatch')
  out=info(PARENT/name,'parent',name);out['verified_publication_path']=prefix+published_name;out['published_sha256']=public[prefix+published_name]['sha256'];return out
 sources=[verify_parent(n) for n in ['classifications.jsonl.gz','classification-output-manifest.json','primary-results.json','reuse-input-manifest.json','analysis-output-manifest.json','return_distribution.py']]
 prior_reuse=load(PARENT/'reuse-input-manifest.json');old_sources={r['name']:r for r in prior_reuse['source_files']}
 grand_names=['analysis-output-manifest.json','selected-case-registry.json','calendar-2026-ytd.json','minute-source-manifest.json','normalize_bars.py','flush_engine.py']
 grand_names += [r['path'] for r in load(GRANDPARENT/'analysis-output-manifest.json')['files'] if r['path'].startswith('results/')]
 assert sum(n.startswith('results/') for n in grand_names)==10
 for name in grand_names:
  f=GRANDPARENT/name;expected=old_sources[name];assert sha(f)==expected['sha256'] and f.stat().st_size==expected['bytes'];row=info(f,'grandparent',name);row['verified_against_parent_reuse_manifest']=True
  if name.startswith('results/'):row['use']='Hash binding only; no new outcome values or groups are read in this preparation.'
  sources.append(row)
 primary=load(PARENT/'primary-results.json');group=next(g for g in primary['groups'] if g['benchmark']=='SPY' and g['group']=='market_down' and g['variant']==['0.02','REBOUND','60m',10])
 parent_dates=group['all']['date_means'];focus_dates=sorted(day for day,row in parent_dates.items() if row['net_n']>0);assert len(focus_dates)==11
 calendar=load(GRANDPARENT/'calendar-2026-ytd.json');registry=load(GRANDPARENT/'selected-case-registry.json')['cases'];assert len(calendar)==191 and len(registry)==19519
 cases={r['case_id']:r for r in registry};assert len(cases)==19519
 classifications={}
 with gzip.open(PARENT/'classifications.jsonl.gz','rt') as stream:
  for line in stream:
   row=json.loads(line);assert row['case_id'] not in classifications;classifications[row['case_id']]=row
 assert set(classifications)==set(cases)
 daily=defaultdict(list)
 for row in classifications.values():daily[row['date']].append(row)
 annual=[];extra=[];focused=[]
 for session in calendar:
  day=session['date'];allrows=daily[day];stock=[r for r in allrows if r['target_role']=='stock'];bench=[r for r in allrows if r['target_role']=='benchmark'];counts={b:Counter(r['classification_by_benchmark'][b]['classification'] or 'unknown' for r in stock) for b in ['SPY','QQQ']}
  known=parent_dates.get(day);known_complete=known['net_n'] if known else 0;known_pairs=known['matched_excess_n'] if known else 0
  included=day in focus_dates
  reason=None if included else ('no_SPY_down_selected_stock_case' if counts['SPY']['market_down']==0 else 'SPY_down_selected_cases_but_no_complete_primary_target_return_in_parent_summary')
  record={'date':day,'session_open_et':session['open'],'session_close_et':session['close'],'selected_cases':len(allrows),'selected_stock_cases':len(stock),'selected_benchmark_cases':len(bench),'classification_counts_by_benchmark':{b:{label:counts[b][label] for label in ['market_down','market_not_down','unknown']} for b in ['SPY','QQQ']},'known_parent_SPY_down_primary_complete_target_returns':known_complete,'known_parent_SPY_down_primary_complete_matched_pairs':known_pairs,'parent_summary_reference':{'name':'primary-results.json','group':'SPY|market_down|0.02|REBOUND|60m|10','date_means_key':day if known else None,'no_entry_interpretation':'No complete target return reported for this date; does not assert no selected cases, no signals, or successful hypothetical trades.'},'included_in_focus_dates':included,'nonfocus_reason':reason}
  annual.append(record)
  if counts['SPY']['market_down']>0 and known_complete==0:extra.append(record)
  if included:
   for c in sorted(allrows,key=lambda r:r['symbol']):
    focused.append({**cases[c['case_id']],'target_role':c['target_role'],'parent_classification_by_benchmark':c['classification_by_benchmark'],'focus_date_selection_reason':'At least one complete SPY-down primary target return was previously reported on this date; all same-day selected groups and benchmarks retained.'})
 assert sum(r['known_parent_SPY_down_primary_complete_target_returns'] for r in annual)==group['all']['counts']['complete_target_returns']==153
 assert sum(r['known_parent_SPY_down_primary_complete_matched_pairs'] for r in annual)==group['all']['counts']['complete_matched_excess']==134
 assert sum(r['classification_counts_by_benchmark']['SPY']['market_down'] for r in annual)==group['all']['counts']['selected_cases']==363
 minute=load(GRANDPARENT/'minute-source-manifest.json');days={r['date']:r for r in minute['days']};bound_days=[]
 old_normalized={r['date']:r for r in prior_reuse['normalized_days']}
 for day in focus_dates:
  d=days[day];name='private-inputs/minute-days/'+d['normalized_file'];f=GRANDPARENT/name
  assert sha(f)==d['normalized_sha256']==old_normalized[day]['sha256'] and f.stat().st_size==d['normalized_bytes']==old_normalized[day]['bytes']
  bound_days.append({'date':day,**info(f,'grandparent',name),'request_complete':d['request_complete'],'request_status':d['status'],'requested_symbol_count':d['requested_symbol_count']})
 vendor=ROOT/'vendor';vendor.mkdir(exist_ok=True);vendor_rows=[]
 for name,folder,ancestor in [('normalize_bars.py',GRANDPARENT,'grandparent'),('flush_engine.py',GRANDPARENT,'grandparent'),('return_distribution.py',PARENT,'parent')]:
  source=folder/name;target=vendor/name
  if target.exists():assert target.read_bytes()==source.read_bytes()
  else:target.write_bytes(source.read_bytes())
  vendor_rows.append({'name':'vendor/'+name,'sha256':sha(target),'bytes':target.stat().st_size,'source_ancestor':ancestor,'source_name':name,'byte_identical':True})
 now=datetime.now(timezone.utc).isoformat()
 annual_doc={'created_at':now,'calendar_sessions':191,'all_selected_cases':19519,'annual_selected_stock_cases':sum(r['selected_stock_cases'] for r in annual),'parent_known_complete_counts_only':True,'focus_selection_is_retrospective':True,'rows':annual}
 save('annual-date-coverage.json',annual_doc)
 save('focus-case-registry.json',{'created_at':now,'focus_dates':focus_dates,'case_count':len(focused),'stock_cases':sum(r['target_role']=='stock' for r in focused),'benchmark_cases':sum(r['target_role']=='benchmark' for r in focused),'all_same_day_groups_retained':True,'cases':focused})
 focus={'id':'s500_flush_anatomy_retrospective_focus_scope_v1','created_at':now,'selection_basis':'Dates in parent SPY market_down primary all.date_means with net_n>0; dates are selected using previously observed outcome completeness, not a prospective market-day filter.','parent_primary_variant':['0.02','REBOUND','60m',10],'focus_dates':focus_dates,'focus_date_count':11,'focus_selected_cases':len(focused),'focus_stock_cases':sum(r['target_role']=='stock' for r in focused),'focus_benchmark_cases':sum(r['target_role']=='benchmark' for r in focused),'focus_unique_symbols':len({r['symbol'] for r in focused}),'parent_known_complete_target_returns':153,'parent_known_complete_matched_pairs':134,'annual_calendar_sessions':191,'annual_SPY_down_selected_stock_cases':363,'annual_dates_with_any_SPY_down_selected_stock':sum(r['classification_counts_by_benchmark']['SPY']['market_down']>0 for r in annual),'extra_SPY_down_selected_dates_without_complete_primary_return':[r['date'] for r in extra],'extra_dates_details':extra,'nonfocus_date_count':191-11,'all_groups_on_focus_dates_included':True,'new_features_computed':False,'new_returns_computed':False,'source_primary_summary_sha256':sha(PARENT/'primary-results.json'),'source_classifications_sha256':sha(PARENT/'classifications.jsonl.gz'),'limitations':['The 11 focus dates were chosen from a prior result and are not an untouched evaluation sample.','All 191 dates remain in annual coverage; dates with SPY-down selected cases but no completed primary return are explicitly listed rather than silently omitted.','No new feature or outcome computation has been performed by this scope preparation.','Original current-directory/survivorship, missing-minute, timestamp and retrospective historical-source limitations persist.']}
 save('focus-scope.json',focus)
 manifest={'id':'s500_flush_anatomy_offline_reuse_manifest_v1','verified_at':now,'parent_stage':'s500-relative-flush-20261007','grandparent_stage':'s500-flush-rebound-20261007','parent_publication':{**info(PARENT/'publication-verification.json','parent','publication-verification.json'),'commit':publication['commit'],'repository':publication['repository'],'branch':publication['branch'],'local_publication_files_reverified':81,'new_remote_requests':0},'source_files':sources,'normalized_focus_days':bound_days,'vendor_files':vendor_rows,'generated_scope_files':[info(ROOT/n,'current',n) for n in ['focus-scope.json','focus-case-registry.json','annual-date-coverage.json']],'verification':{'passed':True,'source_files':len(sources),'normalized_focus_days':len(bound_days),'parent_ledger_files_hash_checked':10,'classified_cases_matched_to_registry':19519,'new_market_api_requests':0,'raw_market_input_files_copied':False,'new_features_computed':False,'new_returns_computed':False},'method_sha256':sha(Path(__file__))}
 save('reuse-input-manifest.json',manifest)
 print(json.dumps({k:v for k,v in focus.items() if k not in ['extra_dates_details','limitations']}),flush=True)
if __name__=='__main__':main()
