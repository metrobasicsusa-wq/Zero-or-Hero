"""Independent raw-prefix and full inherited-row / descriptor-summary audit."""
import gzip,hashlib,json,re,math,statistics
from collections import Counter,defaultdict
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from fractions import Fraction
from decimal import Decimal
from pathlib import Path
ROOT=Path(__file__).resolve().parent;OLD=ROOT.parent/'s500-flush-rebound-20261007';PRIOR=ROOT.parent/'s500-relative-flush-20261007'
FEATURES=['gap_to_previous_close','close29_return','min_close_return','max_close_return','t_min','first30_volume','first30_high_low_range']
PRIMARY=('0.02','REBOUND','60m',10)
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def lines(p):
 with gzip.open(p,'rt') as s:
  for line in s:yield json.loads(line)
def variant(r):return (r['drop_threshold'],r['family'],r['exit_horizon'],r['cost_bps_per_side'])
def parse(raw,day,opening,complete):
 anchor=datetime.fromisoformat(day+'T'+opening).replace(tzinfo=ZoneInfo('America/New_York')).astimezone(timezone.utc)
 valid={};bad=set();seen=set();globalbad=False
 for row in raw:
  try:
   t=row['t'];m=re.fullmatch(r'(\d{4}-\d\d-\d\d)[Tt](\d\d):(\d\d):(\d\d)(?:\.([0-9]{1,9}))?([Zz]|[+-]\d\d:\d\d)',t)
   assert m and int(m[4])==0 and (not m[5] or int(m[5])==0)
   offset=m[6];assert offset!='-00:00'
   if offset not in ('Z','z'):assert int(offset[1:3])<=23 and int(offset[4:6])<=59
   stamp=datetime.fromisoformat(t[:-1]+'+00:00' if offset in ('Z','z') else t)
   delta=stamp.astimezone(timezone.utc)-anchor;seconds=delta.days*86400+delta.seconds
   assert delta.microseconds==0 and seconds%60==0;minute=seconds//60
  except Exception:globalbad=True;continue
  if minute<0 or minute>29:continue
  if minute in seen:bad.add(minute);valid.pop(minute,None);continue
  seen.add(minute)
  try:
   vals={}
   for key in ('o','h','l','c','v'):
    assert row[key] is not None and not isinstance(row[key],bool)
    dec=Decimal(str(row[key]));assert dec.is_finite();vals[key]=Fraction(dec)
   o,h,l,c,v=(vals[k] for k in ('o','h','l','c','v'))
   assert min(o,h,l,c)>0 and v>=0 and l<=min(o,c)<=max(o,c)<=h
   valid[minute]=vals
  except Exception:bad.add(minute)
 return {'valid':complete is True and len(valid)==30 and not bad and not globalbad,'bars':valid,'bad':bad,'globalbad':globalbad,'missing':[t for t in range(30) if t not in valid and t not in bad]}
def oracle(prefix,prior):
 fields=['open0','close29','min_close','t_min','max_close','close29_return','min_close_return','max_close_return','first30_volume','first30_high','first30_low','first30_high_low_range','gap_to_previous_close']
 out={k:None for k in fields};out['previous_close']=Fraction(Decimal(str(prior))) if prior is not None else None
 if not prefix['valid']:return out
 b=prefix['bars'];o=b[0]['o'];cs=[b[t]['c'] for t in range(30)];high=max(b[t]['h'] for t in range(30));low=min(b[t]['l'] for t in range(30))
 out.update(open0=o,close29=cs[-1],min_close=min(cs),t_min=cs.index(min(cs)),max_close=max(cs),close29_return=cs[-1]/o-1,min_close_return=min(cs)/o-1,max_close_return=max(cs)/o-1,first30_volume=sum(b[t]['v'] for t in range(30)),first30_high=high,first30_low=low,first30_high_low_range=(high-low)/o,gap_to_previous_close=o/out['previous_close']-1 if prior is not None else None)
 return out
def eqnum(a,b):
 assert (a is None)==(b is None),(a,b)
 if a is not None:assert abs(Fraction(str(a))-b)<=Fraction(1,10**38)*max(1,abs(b)),(a,b)
def getbins(expect):
 def price(a,b):
  if a is None or b is None:return 'unknown'
  r=a/b-1
  return 'le_minus_2pct' if r<=Fraction(-2,100) else 'minus_2pct_to_below_zero' if r<0 else 'zero_or_positive'
 t=expect['t_min']
 return {'gap':price(expect['open0'],expect['previous_close']),'close29':price(expect['close29'],expect['open0']),'trough':'unknown' if t is None else ['minute0_9','minute10_19','minute20_29'][t//10]}
def desc(values):
 vs=[Fraction(str(v)) for v in values if v is not None];xs=sorted(vs);n=len(xs)
 return {'n':n,'mean':sum(xs,Fraction())/n if n else None,'median':xs[n//2] if n%2 else (xs[n//2-1]+xs[n//2])/2 if n else None,'minimum':min(xs) if n else None,'maximum':max(xs) if n else None}
def checkdesc(a,values):
 b=desc(values);assert a['n']==b['n']
 for k in ['mean','median','minimum','maximum']:
  assert (a[k] is None)==(b[k] is None),(k,a,b)
  if a[k] is not None:assert math.isclose(float(a[k]),float(b[k]),rel_tol=1e-12,abs_tol=1e-14),(k,a,b)
def checksummary(a,rs):
 counts=dict(selected_cases=len(rs),signals=0,unknown_signals=0,no_flush=0,no_rebound=0,complete_net=0,complete_pairs=0,signal_return_missing=0)
 statuses=Counter();pairs=Counter();daily=defaultdict(lambda:{'net':[],'excess':[]})
 for r in rs:
  status=r['signal_status'];statuses[status]+=1;pairs[r['paired_control_status']]+=1
  if status=='signal':counts['signals']+=1;counts['signal_return_missing']+=r['net_return'] is None
  elif status=='unknown':counts['unknown_signals']+=1
  else:counts[status]=counts.get(status,0)+1
  if r['net_return'] is not None:counts['complete_net']+=1;daily[r['date']]['net'].append(r['net_return'])
  if r['matched_excess'] is not None:counts['complete_pairs']+=1;daily[r['date']]['excess'].append(r['matched_excess'])
 assert a['counts']==counts,(a['counts'],counts)
 assert a['signal_status_counts']==dict(statuses) and a['paired_control_status_counts']==dict(pairs)
 checkdesc(a['matched_excess'],[r['matched_excess'] for r in rs]);assert a['matched_excess']['scope']=='relativeperformance_nottradingwin'
 checkdesc(a['date_equal_net'],[desc(x['net'])['mean'] for x in daily.values()])
 checkdesc(a['date_equal_excess'],[desc(x['excess'])['mean'] for x in daily.values()])
 # Full net distribution/tail/identifiers are independently audited in the parallel concentration/distribution audit.
def main():
 manifest=read(ROOT/'analysis-output-manifest.json')
 for r in manifest['files']:
  p=ROOT/r['path'];assert sha(p)==r['sha256'] and p.stat().st_size==r['bytes']
 binding=read(ROOT/'pre-analysis-validation.json')
 for n,h in binding['bound_sha256'].items():assert sha(ROOT/n)==h
 cases=read(ROOT/'anatomy-case-registry.json')['cases'];registry={r['case_id']:r for r in cases};sources={r['date']:r for r in read(ROOT/'reuse-input-manifest.json')['normalized_anatomy_days']}
 feature_rows=list(lines(ROOT/'feature-records.jsonl.gz'));actual={r['case_id']:r for r in feature_rows};assert len(feature_rows)==len(actual)==1227 and set(actual)==set(registry)
 expected={};rawdays={};counts=Counter()
 for case in cases:
  day=case['date'];cid=case['case_id'];sym=case['symbol'];source=sources[day]
  if day not in rawdays:
   p=OLD/source['name'];assert sha(p)==source['sha256'];rawdays[day]=read(p)
   counts['raw_source_bars']+=sum(len(b) for b in rawdays[day]['bars'].values())
  raw=rawdays[day];prefix=parse(raw['bars'].get(sym,[]),day,case['session_open_et'],raw['request_complete'])
  prior=(case['selection_metrics'] or {}).get('prior_close');expect=oracle(prefix,prior);expected[cid]=expect
  row=actual[cid];f=row['features']
  for key in ('date','symbol','date_role','target_role'):assert row[key]==case[key]
  assert row['SPY_group']==(case['parent_classification_by_benchmark']['SPY']['classification'] or 'unknown')
  assert row['available_at_ET']=='10:00' and row['actual_fill'] is False and row['normalized_source_sha256']==source['sha256']
  assert f['prefix_valid']==prefix['valid'] and f['status']==('complete' if prefix['valid'] else 'unknown_prefix')
  assert f['previous_close_valid']==(prior is not None)
  assert f['previous_close_reason']==(None if prior is not None else 'previous_close_missing')
  assert f['gap_reason']==('prefix_incomplete_or_invalid' if not prefix['valid'] else None if prior is not None else 'previous_close_missing')
  assert f['reason']==(None if prefix['valid'] else 'prefix_incomplete_or_invalid')
  for key in ('observation_start_minute','observation_end_minute','available_at_minute'):assert f[key]=={'observation_start_minute':0,'observation_end_minute':29,'available_at_minute':30}[key]
  for key,value in expect.items():
   if key=='t_min':assert f[key]==value
   else:eqnum(f[key],value)
  assert f['coverage']['missing_minutes']==prefix['missing']
  assert bool(f['coverage']['global_errors'])==prefix['globalbad']
  assert {x['minute'] for x in f['coverage']['invalid_minutes']}==prefix['bad']
  assert row['fixed_bins']==getbins(expect)
  counts['complete_prefixes' if prefix['valid'] else 'unknown_prefixes']+=1
 # Context must remain direct same-date benchmark descriptors and all stock classifications.
 context=read(ROOT/'market-context.json');assert len(context['days'])==12
 for row in context['days']:
  day=row['date'];subset=[c for c in cases if c['date']==day]
  assert row['date_role']==subset[0]['date_role'] and row['information_time_ET']=='10:00' and row['not_a_causal_news_explanation'] is True
  assert row['benchmarks']=={c['symbol']:actual[c['case_id']]['features'] for c in subset if c['symbol'] in ['SPY','QQQ']}
  assert row['stock_group_counts']==dict(Counter(actual[c['case_id']]['SPY_group'] for c in subset if c['target_role']=='stock'))
 inherited={};newrows=[]
 for p in sorted((ROOT/'inherited-results').glob('*.gz')):
  for r in lines(p):
   key=(r['case_id'],variant(r));assert key not in inherited;inherited[key]=r;newrows.append(r)
 assert len(inherited)==44172
 oldseen=set();field_count=0
 for source in read(OLD/'analysis-output-manifest.json')['files']:
  if not source['path'].startswith('results/'):continue
  p=OLD/source['path'];assert sha(p)==source['sha256']
  for r in lines(p):
   if r['case_id'] not in registry:continue
   key=(r['case_id'],variant(r));assert key not in oldseen;oldseen.add(key);new=inherited[key];case=registry[r['case_id']]
   for k,v in r.items():assert new[k]==v,(key,k);field_count+=1
   added={'anatomy_date_role':case['date_role'],'anatomy_feature_case_id':r['case_id'],'SPY_group':actual[r['case_id']]['SPY_group'],'parent_result_file':source['path']}
   assert set(new)==set(r)|set(added)
   for k,v in added.items():assert new[k]==v
 assert oldseen==set(inherited)
 percase=Counter(cid for cid,v in inherited);assert set(percase)==set(registry) and set(percase.values())=={36}
 primary=list(lines(ROOT/'primary-case-ledger.jsonl.gz'));assert len(primary)==1227 and [r['case_id'] for r in primary]==sorted(registry)
 for r in primary:assert r==inherited[(r['case_id'],PRIMARY)]
 mainrows=[r for r in primary if r['target_role']=='stock' and r['SPY_group']=='market_down'];signals=[r for r in mainrows if r['signal_status']=='signal']
 summary=read(ROOT/'summary.json');assert summary['counts']=={'cases':1227,'stock_cases':1203,'benchmark_cases':24,'dates':12,'inherited_rows':44172,'variants':36,'variant_group_summaries':108,'primary_SPY_down_selected_cases':len(mainrows),'primary_SPY_down_signals':len(signals),'primary_SPY_down_complete_returns':153,'primary_SPY_down_complete_pairs':134}
 assert len(mainrows)==363 and sum(r['net_return'] is not None for r in mainrows)==153 and sum(r['matched_excess'] is not None for r in mainrows)==134
 seenvariant=set()
 for row in summary['variant_group_summaries']:
  key=(tuple(row['variant']),row['SPY_group']);assert key not in seenvariant;seenvariant.add(key)
  checksummary(row['summary'],[r for r in newrows if r['target_role']=='stock' and variant(r)==key[0] and r['SPY_group']==key[1]])
 assert len(seenvariant)==108
 seendaily=set()
 for row in summary['primary_date_group_summaries']:
  key=row['date'],row['SPY_group'];assert key not in seendaily;seendaily.add(key)
  checksummary(row['summary'],[r for r in primary if r['date']==key[0] and r['SPY_group']==key[1] and r['target_role']=='stock'])
 assert len(seendaily)==36
 for row in summary['fixed_feature_bins']:
  subset=[r for r in mainrows if actual[r['case_id']]['fixed_bins'][row['axis']]==row['bin']]
  checksummary(row['primary_SPY_down_all_selected_case_summary'],subset)
  for name in FEATURES:checkdesc(row['feature_summary'][name],[actual[r['case_id']]['features'][name] for r in subset])
 assert len(summary['fixed_feature_bins'])==12
 for axis in ['gap','close29','trough']:
  assert sum(r['primary_SPY_down_all_selected_case_summary']['counts']['selected_cases'] for r in summary['fixed_feature_bins'] if r['axis']==axis)==363
 for row in summary['outcome_conditioned_features']:
  def label(r):
   n=r['net_return'];return 'unknown_or_not_triggered' if n is None else 'positive_net' if Decimal(n)>0 else 'negative_net' if Decimal(n)<0 else 'zero_net'
  subset=[r for r in mainrows if label(r)==row['outcome_group']]
  
  expected_counts={'selected_cases':len(subset),'signals':0,'unknown_signals':0,'no_flush':0,'no_rebound':0,'complete_net':0,'complete_pairs':0,'signal_return_missing':0}
  for r in subset:
   status=r['signal_status']
   if status=='signal':expected_counts['signals']+=1;expected_counts['signal_return_missing']+=r['net_return'] is None
   elif status=='unknown':expected_counts['unknown_signals']+=1
   else:expected_counts[status]=expected_counts.get(status,0)+1
   expected_counts['complete_net']+=r['net_return'] is not None;expected_counts['complete_pairs']+=r['matched_excess'] is not None
  assert row['counts']==expected_counts
  for name in FEATURES:checkdesc(row['features'][name],[actual[r['case_id']]['features'][name] for r in subset])
 assert len(summary['outcome_conditioned_features'])==4
 diagnostic=[r for r in mainrows if r['date']=='2026-06-18'];assert summary['diagnostic_June18_SPY_down_cases']==diagnostic and len(diagnostic)==3
 timing=summary['realized_strategy_timing'];assert len(timing)==363
 byid={r['case_id']:r for r in mainrows}
 for r in timing:
  old=byid[r['case_id']]
  for k in ['case_id','date','symbol','signal_status','signal_minute','decision_minute','entry_minute','exit_minute']:assert r[k]==old[k]
  assert r['label']=='later_realized_strategy_timing_not_10am_feature'
 counts.update(feature_cases=1227,source_days=len(rawdays),inherited_rows=len(newrows),inherited_original_fields=field_count,variant_group_summaries=108,date_group_summaries=36,feature_bin_summaries=12,outcome_conditioned_feature_summaries=4,realized_timing_rows=363)
 result={'passed':True,'completed_at':datetime.now(timezone.utc).isoformat(),'counts':dict(counts),'no_production_module_imported':True,'exact_fraction_oracle_tolerance':'1e-38 relative for Decimal42 descriptors; 1e-12 relative and 1e-14 absolute for explicitly float descriptive summaries','all_original_row_fields_and_peer_controls_unchanged':True,'unknowns_and_diagnostic_day_retained':True,'all_new_inherited_rows_audited':True,'net_distribution_tail_full_audit_separate':'independent-concentration-audit.json','files':[{'name':n,'sha256':sha(ROOT/n)} for n in ['analysis-output-manifest.json','pre-analysis-validation.json','study-design.json','feature-records.jsonl.gz','summary.json','independent_actual_audit.py']]}
 (ROOT/'independent-actual-audit.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
if __name__=='__main__':main()
