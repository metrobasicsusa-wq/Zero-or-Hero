"""Offline byte-level source reuse verification; never interprets return groups."""
import gzip,hashlib,json
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).parent;PARENT=ROOT.parent/'s500-flush-rebound-20261007'
def sha_bytes(b):return hashlib.sha256(b).hexdigest()
def sha(p):return sha_bytes(p.read_bytes())
def info(p,name=None):return {'name':name or p.name,'bytes':p.stat().st_size,'sha256':sha(p)}
def main():
 pfile=PARENT/'publication-verification.json';publication=json.loads(pfile.read_text());assert publication['force'] is False and publication['only_expected_paths_changed'] is True
 assert publication['exact_readback_files']==117 and len(publication['files'])==117
 pubfiles={r['path']:r for r in publication['files']}
 for record in publication['files']:
  path=PARENT/'publication'/record['path'];assert record['exact_readback'] is True;assert sha(path)==record['sha256'] and path.stat().st_size==record['bytes']
 prefix='exchange/codex/s500/2026-10-07-flush-rebound-v1/'
 sources=[]
 for name in ['analysis-output-manifest.json','selected-case-registry.json','calendar-2026-ytd.json','minute-source-manifest.json','normalize_bars.py','flush_engine.py']:
  f=PARENT/name;publicname=name if prefix+name in pubfiles else name+'.gz';published=PARENT/'publication'/prefix/publicname
  original=f.read_bytes();published_content=published.read_bytes()
  if publicname.endswith('.gz'):published_content=gzip.decompress(published_content)
  assert original==published_content,(name,'local/public mismatch')
  item=info(f,name);item['matches_verified_publication_path']=prefix+publicname;item['public_artifact_sha256']=pubfiles[prefix+publicname]['sha256'];sources.append(item)
 analysis=json.loads((PARENT/'analysis-output-manifest.json').read_text());results=[r for r in analysis['files'] if r['path'].startswith('results/')];assert len(results)==10
 for result in results:
  file=PARENT/result['path'];assert sha(file)==result['sha256'] and file.stat().st_size==result['bytes'];assert sha(file)==pubfiles[prefix+result['path']]['sha256'];sources.append({'name':result['path'],'sha256':result['sha256'],'bytes':result['bytes'],'use':'hash binding only before classification; returns not parsed or regrouped'})
 minute_manifest=json.loads((PARENT/'minute-source-manifest.json').read_text());days=minute_manifest['days'];assert len(days)==191
 normalized=[]
 for day in days:
  name='private-inputs/minute-days/'+day['normalized_file'];file=PARENT/name;assert sha(file)==day['normalized_sha256'] and file.stat().st_size==day['normalized_bytes']
  normalized.append({'date':day['date'],'source_name':name,'sha256':day['normalized_sha256'],'bytes':day['normalized_bytes'],'request_complete':day['request_complete'],'request_status':day['status'],'requested_symbol_count':day['requested_symbol_count']})
 calendar=json.loads((PARENT/'calendar-2026-ytd.json').read_text());registry=json.loads((PARENT/'selected-case-registry.json').read_text())['cases'];assert len(calendar)==191 and len(registry)==19519
 assert {r['date'] for r in calendar}=={r['date'] for r in normalized}=={r['date'] for r in registry}
 vendor=ROOT/'vendor';vendor.mkdir(exist_ok=True)
 vendor_files=[]
 for name in ['normalize_bars.py','flush_engine.py']:
  source=PARENT/name;target=vendor/name
  if target.exists():assert target.read_bytes()==source.read_bytes()
  else:target.write_bytes(source.read_bytes())
  vendor_files.append({'name':'vendor/'+name,'sha256':sha(target),'bytes':target.stat().st_size,'parent_source':name,'byte_identical':True})
 output={'id':'s500_relative_flush_offline_reuse_inputs_v1','verified_at':datetime.now(timezone.utc).isoformat(),'source_stage':'s500-flush-rebound-20261007','source_publication':{**info(pfile),'commit':publication['commit'],'repository':publication['repository'],'branch':publication['branch'],'previous_exact_readback_files':117,'local_publication_bytes_reverified':117},'publications_verification_limits':'Existing local publication receipts and all 117 staged file bytes were checked offline. No new remote request was made.','source_files':sources,'normalized_days':normalized,'vendor_files':vendor_files,'scope':{'calendar_sessions':191,'registered_cases':19519,'unique_symbols':len({r['symbol'] for r in registry}),'normalized_days':len(normalized),'return_files_hash_checked':10,'return_groups_read':False,'new_market_api_requests':0,'raw_prices_copied_into_new_stage':False},'passed':True,'classification_executed':False,'method_sha256':sha(Path(__file__))}
 (ROOT/'reuse-input-manifest.json').write_text(json.dumps(output,indent=2)+'\n');print(json.dumps({k:v for k,v in output.items() if k not in ['source_files','normalized_days','vendor_files']}),flush=True)
if __name__=='__main__':main()
