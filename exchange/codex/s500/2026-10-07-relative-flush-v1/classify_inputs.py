"""Frozen-gate offline classifications. No return grouping, prices, or API acquisition."""
import gzip,hashlib,io,json
from pathlib import Path
from datetime import datetime,timezone,timedelta
from collections import Counter,defaultdict
from vendor.normalize_bars import normalize_provider_bars
ROOT=Path(__file__).parent;PARENT=ROOT.parent/'s500-flush-rebound-20261007';BENCHMARKS=('SPY','QQQ')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def now():return datetime.now(timezone.utc).isoformat()
def write(p,obj):p.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
def relevant_issues(normalized):
 out=[]
 for issue in normalized['issues']:
  bar=normalized['bars'][issue['index']];t=bar.get('t')
  if not isinstance(t,int) or isinstance(t,bool) or 0<=t<=29:out.append(issue)
 return out

def main():
 gate=json.loads((ROOT/'classification-authorization.json').read_text());assert gate['classification_authorized'] is True
 for name,expected in gate['bound_files'].items():assert sha(ROOT/name)==expected,(name,'classification binding changed')
 from classify_relative import classify_case
 manifest=json.loads((ROOT/'reuse-input-manifest.json').read_text());assert manifest['passed'] is True
 assert manifest['scope']['calendar_sessions']==191 and manifest['scope']['registered_cases']==19519
 for source in manifest['source_files']:
  file=PARENT/source['name'];assert sha(file)==source['sha256'] and file.stat().st_size==source['bytes'],source['name']
 publication=manifest['source_publication'];assert sha(PARENT/publication['name'])==publication['sha256']
 for vendor in manifest['vendor_files']:assert sha(ROOT/vendor['name'])==vendor['sha256']
 registry=json.loads((PARENT/'selected-case-registry.json').read_text())['cases'];cases_by_day=defaultdict(list)
 for row in registry:cases_by_day[row['date']].append(row)
 assert len(registry)==19519 and len({r['case_id'] for r in registry})==19519 and len(cases_by_day)==191
 dates={r['date']:r for r in json.loads((PARENT/'calendar-2026-ytd.json').read_text())}
 class_counts={b:Counter() for b in BENCHMARKS};status_counts={b:Counter() for b in BENCHMARKS};roles=Counter();rows_written=0;day_records=[];started=now()
 output=ROOT/'classifications.jsonl.gz';temporary=ROOT/'classifications.jsonl.gz.inprogress'
 with temporary.open('wb') as rawstream:
  with gzip.GzipFile(filename='',fileobj=rawstream,mode='wb',mtime=0) as compressed:
   with io.TextIOWrapper(compressed,encoding='utf-8',newline='\n') as writer:
    for source in sorted(manifest['normalized_days'],key=lambda r:r['date']):
     day=source['date'];file=PARENT/source['source_name'];assert sha(file)==source['sha256'] and file.stat().st_size==source['bytes']
     obj=json.loads(file.read_text());assert obj['date']==day and obj['request_complete']==source['request_complete']
     registered=sorted(cases_by_day[day],key=lambda r:r['symbol']);assert set(obj['requested_symbols'])=={r['symbol'] for r in registered}
     normalized={s:normalize_provider_bars(obj['bars'].get(s,[]),day,dates[day]['open']) for s in obj['requested_symbols']}
     assert all(b in normalized for b in BENCHMARKS)
     open_local=datetime.fromisoformat(day+'T'+dates[day]['open']);early_close_local=open_local+timedelta(minutes=30)
     for parentcase in registered:
      symbol=parentcase['symbol'];role='benchmark' if symbol in BENCHMARKS else 'stock';roles[role]+=1
      classification={}
      for benchmark in BENCHMARKS:
       if role=='stock':
        classified=classify_case(normalized[symbol]['bars'],normalized[benchmark]['bars'],source_complete=obj['request_complete'] is True)
       else:classified={'classification':'not_stock_target','status':'not_stock_target','reason':'SPY and QQQ are benchmark controls, not primary stock targets.','target_valid':None,'benchmark_valid':None,'t_min':None,'target_return':None,'benchmark_return':None,'relative_return':None}
       classification[benchmark]=classified;class_counts[benchmark][classified['classification'] or 'unknown']+=1;status_counts[benchmark][classified['status']]+=1
      record={'case_id':parentcase['case_id'],'date':day,'symbol':symbol,'target_role':role,'classification_by_benchmark':classification,'parent_case':{'current_etf_classification':parentcase['current_etf_classification'],'sample_roles':parentcase['roles'],'stock_eligible':parentcase['stock_eligible'],'prior_session_date':parentcase['prior_session_date'],'selection_metrics':parentcase['selection_metrics'],'known_current_directory_survivorship_bias':parentcase['known_current_directory_survivorship_bias']},'early_window':{'session_open_et':dates[day]['open'],'first_minute':0,'last_minute':29,'window_complete_at_et':early_close_local.strftime('%H:%M'),'t_min_is_bar_start_minute_and_ties_choose_earliest':True,'benchmark_return_uses_target_t_min':True,'classification_has_no_entry_or_exit_return':True},'source':{'normalized_day_name':file.name,'normalized_day_sha256':source['sha256'],'request_complete':obj['request_complete'] is True,'parent_registry_sha256':next(r['sha256'] for r in manifest['source_files'] if r['name']=='selected-case-registry.json')},'normalization_issues_relevant_or_unlocatable':{'target':relevant_issues(normalized[symbol]),'benchmarks':{b:relevant_issues(normalized[b]) for b in BENCHMARKS}},'actual_fill':False,'wealth_path':False}
      writer.write(json.dumps(record,ensure_ascii=False,separators=(',',':'))+'\n');rows_written+=1
     day_records.append({'date':day,'cases':len(registered),'normalized_day_sha256':source['sha256'],'request_complete':source['request_complete']})
     if len(day_records)%20==0 or len(day_records)==191:print(json.dumps({'completed_dates':len(day_records),'written_cases':rows_written}),flush=True)
 assert rows_written==19519 and roles=={'stock':19137,'benchmark':382}
 temporary.replace(output)
 # Read back the entire derived output and verify one row for every parent case.
 read_ids=[];read_counts={b:Counter() for b in BENCHMARKS}
 with gzip.open(output,'rt') as stream:
  for line in stream:
   record=json.loads(line);read_ids.append(record['case_id'])
   for b in BENCHMARKS:read_counts[b][record['classification_by_benchmark'][b]['classification'] or 'unknown']+=1
 assert len(read_ids)==len(set(read_ids))==19519 and set(read_ids)=={r['case_id'] for r in registry};assert read_counts==class_counts
 summary={'started_at':started,'completed_at':now(),'rows':rows_written,'dates':len(day_records),'roles':dict(roles),'classification_counts_by_benchmark':{b:dict(class_counts[b]) for b in BENCHMARKS},'status_counts_by_benchmark':{b:dict(status_counts[b]) for b in BENCHMARKS},'classification_input_reuse_manifest_sha256':sha(ROOT/'reuse-input-manifest.json'),'authorization_sha256':sha(ROOT/'classification-authorization.json'),'runner_sha256':sha(Path(__file__)),'classifier_sha256':sha(ROOT/'classify_relative.py'),'output':{'name':output.name,'bytes':output.stat().st_size,'sha256':sha(output)},'source_days':day_records,'return_groups_read':False,'new_market_api_requests':0,'raw_prices_copied':False,'readback_case_denominator_verified':True,'passed':True}
 write(ROOT/'classification-output-manifest.json',summary)
 print(json.dumps({k:v for k,v in summary.items() if k!='source_days'}),flush=True)
if __name__=='__main__':main()
