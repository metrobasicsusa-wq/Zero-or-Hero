"""Verify every acquired response, day-normalization, and registered denominator."""
import hashlib,json
from pathlib import Path
from datetime import datetime,timezone
from collections import Counter
ROOT=Path(__file__).parent;PRIVATE=ROOT/'private-inputs';FIELDS=('t','o','h','l','c','v','n','vw')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p):return json.loads(p.read_text())
def main():
 manifest=load(ROOT/'minute-source-manifest.json');registry=load(ROOT/'selected-case-registry.json')['cases'];scopes=load(ROOT/'daily-scope.json')['daily'];coverage=load(ROOT/'symbol-session-coverage.json')['rows'];scope_by_date={s['date']:s for s in scopes};cases={r['case_id'] for r in registry}
 assert len(cases)==len(registry)==19519
 assert len(coverage)==19519 and {r['case_id'] for r in coverage}==cases
 assert len(manifest['days'])==191 and {r['date'] for r in manifest['days']}==set(scope_by_date)
 binding=load(ROOT/'acquisition-authorization.json')
 for filename,expected in binding['bound_files'].items():assert sha(ROOT/filename)==expected,(filename,'binding')
 artifacts=[];request_counts=Counter();bar_count=0;normalized_count=0
 for day in manifest['days']:
  date=day['date'];scope=scope_by_date[date];daily_meta=PRIVATE/'day-manifests'/f'{date}.json';assert load(daily_meta)==day
  daily_coverage=PRIVATE/'day-coverage'/f'{date}.json';assert load(daily_coverage)==[r for r in coverage if r['date']==date]
  artifacts.extend([{'kind':'day_manifest','name':daily_meta.name,'sha256':sha(daily_meta),'bytes':daily_meta.stat().st_size},{'kind':'day_coverage','name':daily_coverage.name,'sha256':sha(daily_coverage),'bytes':daily_coverage.stat().st_size}])
  reconstructed={s:[] for s in scope['selected_symbols']}
  for req in day['requests']:
   request_counts[str(req['status'])]+=1
   if 'response_file' not in req:continue
   page=PRIVATE/'minute-pages'/req['response_file'];assert sha(page)==req['response_sha256'];assert page.stat().st_size==req['response_bytes']
   artifacts.append({'kind':'response','name':page.name,'sha256':sha(page),'bytes':page.stat().st_size})
   if req['status']==200:
    raw=load(page);assert sum(len(v) for v in raw['bars'].values())==req['bar_count'];assert bool(raw.get('next_page_token'))==req['next_page_exists']
    for symbol,bars in raw['bars'].items():
     assert symbol in reconstructed
     reconstructed[symbol]+=[{k:b[k] for k in FIELDS if k in b} for b in bars]
  if 'normalized_file' in day:
   normal=PRIVATE/'minute-days'/day['normalized_file'];assert sha(normal)==day['normalized_sha256'];assert normal.stat().st_size==day['normalized_bytes'];obj=load(normal)
   assert obj['bars']==reconstructed;assert obj['requested_symbols']==scope['selected_symbols'];assert obj['request_complete']==day['request_complete'];assert obj['status']==day['status']
   assert sum(len(v) for v in reconstructed.values())==day['returned_bar_count'];bar_count+=day['returned_bar_count'];normalized_count+=1
   artifacts.append({'kind':'normalized_day','name':normal.name,'sha256':sha(normal),'bytes':normal.stat().st_size})
  else:assert not day['request_complete']
 assert sum(r['returned_bars'] for r in coverage)==bar_count
 assert sum(r['requested_symbol_count'] for r in manifest['days'])==19519
 output={'checked_at':datetime.now(timezone.utc).isoformat(),'passed':True,'failed_checks':0,'binding_files_checked':len(binding['bound_files']),'dates':191,'registered_and_covered_symbol_sessions':19519,'unique_registered_symbols':len({r['symbol'] for r in registry}),'response_status_counts':dict(request_counts),'normalizations_reconstructed_exactly':normalized_count,'response_to_normalized_bar_count':bar_count,'artifact_hashes_verified':len(artifacts),'bytes_hashed':sum(a['bytes'] for a in artifacts),'public_artifact_note':'Raw market bodies and normalized price files remain private; this verification publishes only source filenames, byte counts and hashes.','artifacts':artifacts}
 (ROOT/'input-integrity-verification.json').write_text(json.dumps(output,indent=2)+'\n');print(json.dumps({k:v for k,v in output.items() if k!='artifacts'}))
if __name__=='__main__':main()
