"""Bounded offline execution of every registered case/variant and fixed-time controls."""
from pathlib import Path
from collections import defaultdict,Counter
from decimal import Decimal,localcontext
from datetime import datetime,timezone
import gzip,io,json,hashlib
from flush_engine import prepare_bars,evaluate_prefix,evaluate_day,evaluate_window
from normalize_bars import normalize_provider_bars

ROOT=Path(__file__).resolve().parent
D=Decimal

def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def read(name):return json.loads((ROOT/name).read_text())
def write(name,value):
 p=ROOT/name;assert not p.exists(),name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value,separators=(',',':'))+'\n')
def output_stream(path):
 path.parent.mkdir(parents=True,exist_ok=True)
 return io.TextIOWrapper(gzip.GzipFile(filename=str(path),mode='wb',mtime=0),encoding='utf8',newline='\n')
def emit(stream,row):stream.write(json.dumps(row,separators=(',',':'))+'\n')
def peer_selection(target,cases,prefixes,threshold):
 """Uses only prior liquidity and completed first30 prefix, never exit availability."""
 if target['symbol'] in ('SPY','QQQ') or not target['stock_eligible']:return []
 liq=D(target['selection_metrics']['median_prior20_daily_close_times_volume']);ranked=[]
 with localcontext() as ctx:
  ctx.prec=42
  for c in cases:
   s=c['symbol']
   if s==target['symbol'] or s in ('SPY','QQQ') or not c['stock_eligible']:continue
   if prefixes[s][threshold]['is_flush'] is not False:continue
   other=D(c['selection_metrics']['median_prior20_daily_close_times_volume'])
   distance=abs((other/liq).ln());ranked.append((distance,s))
 return [{'symbol':s,'absolute_log_liquidity_distance':str(distance)} for distance,s in sorted(ranked)[:3]]
def canonical_signal(status):
 return {'signal':'signal','no_flush':'no_flush','valid_no_rebound':'no_rebound'}.get(status,'unknown')
def evaluate_case(c,all_cases,prepared,prefixes,session_minutes,normalization):
 symbol=c['symbol'];stock=symbol not in ('SPY','QQQ');p=prepared[symbol]
 peers={t:peer_selection(c,all_cases,prefixes,t) for t in ['0.02','0.03']}
 decisions={};rows=[];cache={}
 def window(s,entry,exit,cost):
  key=(s,entry,exit,cost)
  if key not in cache:cache[key]=evaluate_window(prepared[s],entry,exit,cost,session_minutes=session_minutes)
  return cache[key]
 for e in evaluate_day(p,session_minutes=session_minutes):
  t=e['threshold'];decision=e['decision'];signal=canonical_signal(decision['status']);entry=e['entry_minute'];exit=e['exit_minute'];cost=int(e['per_side_cost_bps']);value=e['net_return']
  decisions[t+'|'+e['family']]=decision
  controls=[];benchmarks={};excess=None;paired_status='not_applicable'
  if signal=='signal':
   if stock:
    for selection in peers[t]:
     cw=window(selection['symbol'],entry,exit,cost)
     controls.append({**selection,'status':'complete' if cw['status']=='ok' else cw['status'],
       'net_return':cw['net_return'],'entry_minute':entry,'exit_minute':exit,
       'entry_open':cw['entry_open'],'exit_open':cw['exit_open'],
       'missing_minutes':(cw.get('coverage') or {}).get('missing_minutes',[]),
       'invalid_minutes':(cw.get('coverage') or {}).get('invalid_minutes',{})})
    paired_status='insufficient_candidates' if len(controls)!=3 else 'missing_return'
    if len(controls)==3 and value is not None and all(q['status']=='complete' and q['net_return'] is not None for q in controls):
     with localcontext() as ctx:
      ctx.prec=42;excess=str(D(value)-sum(D(q['net_return']) for q in controls)/D(3))
     paired_status='complete_three'
   for s in ['SPY','QQQ']:
    bw=window(s,entry,exit,cost);br=bw['net_return'];be=None
    if value is not None and br is not None:
     with localcontext() as ctx:ctx.prec=42;be=str(D(value)-D(br))
    benchmarks[s]={'status':'complete' if bw['status']=='ok' else bw['status'],'net_return':br,'excess_return':be,
      'entry_minute':entry,'exit_minute':exit,'entry_open':bw['entry_open'],'exit_open':bw['exit_open']}
  prefix=prefixes[symbol][t];window_result=e['window'];coverage=(window_result or {}).get('coverage') or {}
  missing_stage='signal' if signal=='unknown' else ('return' if signal=='signal' and value is None else None)
  reason=decision['status'] if missing_stage=='signal' else (e['status'] if missing_stage=='return' else None)
  rows.append({'case_id':c['case_id'],'date':c['date'],'symbol':symbol,'target_role':'stock' if stock else 'benchmark',
    'current_etf_classification':c['current_etf_classification'],'sample_roles':c['roles'],
    'drop_threshold':t,'family':e['family'],'exit_horizon':e['horizon'],'cost_bps_per_side':cost,
    'risk_set_eligible':bool(c['stock_eligible'] and prefix['is_flush'] is not None),
    'flush_detected':prefix['is_flush'],'signal_status':signal,'engine_signal_status':decision['status'],
    'signal_minute':decision['signal_minute'],'decision_minute':decision['decision_minute'],
    'entry_minute':entry,'exit_minute':exit,'net_return':value,'gross_return':e['gross_return'],
    'entry_open':(window_result or {}).get('entry_open'),'exit_open':(window_result or {}).get('exit_open'),
    'window_status':(window_result or {}).get('status'),'missing_stage':missing_stage,'missing_reason':reason,
    'missing_holding_minutes':coverage.get('missing_minutes',[]),'invalid_holding_minutes':coverage.get('invalid_minutes',{}),
    'paired_controls':controls,'paired_control_status':paired_status,'matched_excess':excess,'benchmarks':benchmarks,
    'actual_fill':False,'reference_prices':'retrospective_SIP_minute_open_proxies','wealth_path':False})
 diagnostics={'case_id':c['case_id'],'date':c['date'],'symbol':symbol,'target_role':'stock' if stock else 'benchmark',
  'prior_liquidity':(c['selection_metrics'] or {}).get('median_prior20_daily_close_times_volume'),
  'normalization_issues':normalization.get('issues',[]),'prefixes':prefixes[symbol],'family_decisions':decisions,
  'preselected_peers':peers,'no_peer_replacement_after_future_data_check':True}
 return rows,diagnostics

def load_day_input(source,cases):
 filename=source.get('normalized_file')
 if filename is None:
  assert source.get('request_complete') is not True,'Complete source must have a normalized file'
  return {'requested_symbols':[c['symbol'] for c in cases],'bars':{},'request_complete':False,'status':'unavailable_normalized_source'}
 path=ROOT/'private-inputs/minute-days'/filename
 assert sha(path)==source['normalized_sha256']
 return json.loads(path.read_text())

def main():
 validation=read('pre-analysis-validation.json');assert validation['failed_tests']==0 and validation['tests_passed']>0
 for n,h in validation['bound_sha256'].items():assert sha(ROOT/n)==h,n
 registry=read('selected-case-registry.json')['cases'];scopes={r['date']:r for r in read('daily-scope.json')['daily']}
 manifest=read('minute-source-manifest.json');days={r['date']:r for r in manifest['days']};assert len(days)==191
 bydate=defaultdict(list)
 for c in registry:bydate[c['date']].append(c)
 assert len(registry)==19519 and len(bydate)==191
 result_dir=ROOT/'results';diag_dir=ROOT/'case-diagnostics';assert not result_dir.exists() and not diag_dir.exists()
 handles={};diagnostics_handles={};counts=Counter();began=datetime.now(timezone.utc).isoformat()
 try:
  for day,cases in sorted(bydate.items()):
   source=days[day];market=load_day_input(source,cases)
   assert set(market['requested_symbols'])=={c['symbol'] for c in cases}
   start=datetime.fromisoformat(day+'T'+scopes[day]['open_et']);close=datetime.fromisoformat(day+'T'+scopes[day]['close_et']);session_minutes=int((close-start).total_seconds()//60)
   prepared={};prefixes={};normalized={}
   for c in cases:
    s=c['symbol'];normal=normalize_provider_bars(market['bars'].get(s,[]),day,session_open_et=scopes[day]['open_et']);normalized[s]=normal
    prepared[s]=prepare_bars(normal['bars'],source_complete=source['request_complete'] is True and market['request_complete'] is True)
    prefixes[s]={t:evaluate_prefix(prepared[s],t) for t in ['0.02','0.03']}
   month=day[:7]
   if month not in handles:
    handles[month]=output_stream(result_dir/(month+'.jsonl.gz'));diagnostics_handles[month]=output_stream(diag_dir/(month+'.jsonl.gz'))
   for c in cases:
    rows,diag=evaluate_case(c,cases,prepared,prefixes,session_minutes,normalized[c['symbol']]);assert len(rows)==36
    for row in rows:emit(handles[month],row)
    emit(diagnostics_handles[month],diag);counts['cases']+=1;counts['rows']+=len(rows)
   counts['dates']+=1
   progress={'began_at':began,'updated_at':datetime.now(timezone.utc).isoformat(),**counts,'last_completed_date':day}
   (ROOT/'analysis-progress.json').write_text(json.dumps(progress)+'\n')
   if counts['dates']%20==0:print(json.dumps(progress),flush=True)
 finally:
  for h in list(handles.values())+list(diagnostics_handles.values()):h.close()
 assert counts=={'cases':19519,'rows':702684,'dates':191}
 files=sorted(list(result_dir.glob('*.gz'))+list(diag_dir.glob('*.gz')))
 write('analysis-output-manifest.json',{'began_at':began,'completed_at':datetime.now(timezone.utc).isoformat(),'counts':dict(counts),
   'pre_analysis_binding_sha256':sha(ROOT/'pre-analysis-validation.json'),'files':[{'path':p.relative_to(ROOT).as_posix(),'bytes':p.stat().st_size,'sha256':sha(p)} for p in files],
   'economic_scope':'All selectedcases andregisteredvariants; normalizedstockreferencepriceeventreturns only. No portfolios, options oractualfills.'})
 print(json.dumps({'completed':True,**counts}),flush=True)

if __name__=='__main__':main()
