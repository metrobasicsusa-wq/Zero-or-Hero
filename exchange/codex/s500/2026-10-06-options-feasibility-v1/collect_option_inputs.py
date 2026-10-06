"""Frozen, bounded options feasibility GET collection. No account/orders/returns."""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timedelta,timezone
from decimal import Decimal,InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib,json,os,re,threading,time
import urllib.request,urllib.parse,urllib.error

ROOT=Path(__file__).resolve().parent;NY=ZoneInfo('America/New_York')
LOCK=threading.Lock();NEXT=0.0
CONTRACT_KEYS=('symbol','name','status','tradable','expiration_date','root_symbol','underlying_symbol','type','style',
 'strike_price','multiplier','size','ppind','open_interest','open_interest_date','close_price','close_price_date')
DELIVERABLE_KEYS=('symbol','type','amount','allocation_percentage','settlement_type','settlement_method','delayed_settlement')
QUOTE_KEYS=('t','ap','as','ax','bp','bs','bx','c')
TRADE_KEYS=('t','p','s','c','x','u')

def read(file):return json.loads(Path(file).read_text())
def sha(file):return hashlib.sha256(Path(file).read_bytes()).hexdigest()
def digest(obj):return hashlib.sha256(json.dumps(obj,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def now():return datetime.now(timezone.utc).isoformat()
def save(name,obj,immutable=False):
 file=ROOT/name;file.parent.mkdir(parents=True,exist_ok=True)
 if immutable and file.exists():
  old=read(file);assert old==obj,'immutable_artifact_mismatch:'+name;return
 tmp=file.with_suffix(file.suffix+'.tmp');tmp.write_text(json.dumps(obj,ensure_ascii=False,separators=(',',':'))+'\n');tmp.replace(file)
def positive(value):
 if isinstance(value,bool)or value is None:return None
 try:d=Decimal(str(value))
 except(InvalidOperation,ValueError):return None
 return d if d.is_finite()and d>0 else None
def timestamp_ns(value):
 if not isinstance(value,str):raise ValueError('timestamp_not_string')
 match=re.fullmatch(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})',value)
 if not match:raise ValueError('timestamp_not_strict_timezone_ns')
 base=datetime.fromisoformat(match[1]+match[3].replace('Z','+00:00')).astimezone(timezone.utc)
 delta=base-datetime(1970,1,1,tzinfo=timezone.utc)
 return(delta.days*86400+delta.seconds)*1000000000+int((match[2]or'').ljust(9,'0'))
def local_stamp(day,hm):return datetime.fromisoformat(day+'T'+hm).replace(tzinfo=NY).astimezone(timezone.utc)
def fmt(dt):return dt.strftime('%Y-%m-%dT%H:%M:%SZ')

class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):raise RuntimeError('redirect_rejected')

def validate_request(host,path,params):
 common={'page_token'}
 if host=='paper-api.alpaca.markets'and path=='/v2/options/contracts':
  assert set(params)<={'underlying_symbols','status','expiration_date','expiration_date_gte','expiration_date_lte','show_deliverables','limit'}|common
  assert params['status']in('active','inactive')and params['show_deliverables']=='true'and params['limit']==1000
  assert re.fullmatch('[A-Z]+',params['underlying_symbols'])
 elif host=='data.alpaca.markets'and path=='/v2/stocks/bars':
  assert set(params)<={'symbols','timeframe','start','end','feed','adjustment','limit','sort'}|common
  assert params['symbols']=='SPY'and params['timeframe']=='1Day'and params['feed']=='sip'and params['adjustment']=='raw'
  assert params['sort']=='asc'and params['limit']==10000
 elif host=='data.alpaca.markets'and path in('/v1beta1/options/bars','/v1beta1/options/trades'):
  assert set(params)<={'symbols','timeframe','start','end','limit','sort'}|common
  assert params['sort']=='asc'and params['limit']==10000 and'feed'not in params
  if path.endswith('/bars'):assert params['timeframe']=='1Min'
  else:assert'timeframe'not in params
  symbols=params['symbols'].split(',');assert 1<=len(symbols)<=100 and all(re.fullmatch('[A-Z0-9]+',s)for s in symbols)
 elif host=='data.alpaca.markets'and path=='/v1beta1/options/quotes/latest':
  assert set(params)=={'symbols','feed'}and params['feed']=='indicative'
  symbols=params['symbols'].split(',');assert 1<=len(symbols)<=100 and all(re.fullmatch('[A-Z0-9]+',s)for s in symbols)
 else:raise ValueError('route_not_authorized_for_feasibility')

def request_page(task,params,index):
 global NEXT
 host,path=task['host'],task['path'];validate_request(host,path,params)
 request_hash=digest({'method':'GET','host':host,'path':path,'parameters':params})
 folder=ROOT/'raw-option-inputs'/task['task_id'];folder.mkdir(parents=True,exist_ok=True)
 checkpoint=folder/('page-%03d.record.json'%index)
 if checkpoint.exists():
  record=read(checkpoint);assert record['page_request_sha256']==request_hash
  for a in record['attempts']:
   assert sha(ROOT/a['body_file'])==a['body_sha256'] and sha(ROOT/a['metadata_file'])==a['metadata_sha256']
  body=(ROOT/record['attempts'][-1]['body_file']).read_bytes()
  try:return record,json.loads(body)
  except (json.JSONDecodeError,UnicodeDecodeError):return record,None
 attempts=[];payload=None
 for attempt in range(5):
  with LOCK:
   wait=max(0.0,NEXT-time.monotonic());NEXT=max(NEXT,time.monotonic())+.5
  if wait:time.sleep(wait)
  requested=now();status=None;error=None;body=b''
  req=urllib.request.Request('https://'+host+path+'?'+urllib.parse.urlencode(params),method='GET',headers={
      'APCA-API-KEY-ID':os.environ['ALPACA_500_API_KEY'],'APCA-API-SECRET-KEY':os.environ['ALPACA_500_SECRET_KEY']})
  try:
   with urllib.request.build_opener(NoRedirect).open(req,timeout=40)as response:status=response.status;body=response.read()
  except urllib.error.HTTPError as exc:status=exc.code;body=exc.read()
  except(urllib.error.URLError,TimeoutError,RuntimeError)as exc:error=type(exc).__name__
  received=now();stem='page-%03d-attempt-%02d'%(index,attempt)
  body_file=folder/(stem+'.body');body_file.write_bytes(body)
  meta={'method':'GET','host':host,'path':path,'parameters':params,'status':status,'error_type':error,
        'requested_at':requested,'received_at':received,'page_request_sha256':request_hash,
        'response_body_sha256':hashlib.sha256(body).hexdigest()}
  meta_file=folder/(stem+'.json');meta_file.write_text(json.dumps(meta,separators=(',',':'))+'\n')
  attempts.append({'status':status,'error_type':error,'requested_at':requested,'received_at':received,
    'body_file':str(body_file.relative_to(ROOT)),'body_sha256':sha(body_file),'bytes':len(body),
    'metadata_file':str(meta_file.relative_to(ROOT)),'metadata_sha256':sha(meta_file)})
  try:payload=json.loads(body)
  except(json.JSONDecodeError,UnicodeDecodeError):payload=None
  if status not in(None,429,500,502,503,504):break
  if attempt<4:time.sleep(min(2**attempt,8))
 record={'page_number':index,'page_request_sha256':request_hash,'status':attempts[-1]['status'],
    'requested_at':attempts[0]['requested_at'],'received_at':attempts[-1]['received_at'],'attempts':attempts}
 checkpoint.write_text(json.dumps(record,separators=(',',':'))+'\n')
 return record,payload

def fetch_task(task):
 params=dict(task['parameters']);pages=[];tokens=set();complete=False;reason=None
 for index in range(task['max_pages']):
  record,payload=request_page(task,params,index);pages.append(record)
  if record['status']!=200:reason='http_'+str(record['status']);break
  if not isinstance(payload,dict):reason='response_not_json_object';break
  field=task['response_field'];value=payload.get(field)
  if not isinstance(value,list if field=='option_contracts'else dict):reason='invalid_response_'+field;break
  if field!='option_contracts'and not set(value)<=set(task['parameters'].get('symbols','').split(',')):
   reason='unexpected_response_symbols';break
  if task.get('unpaginated'):
   complete=True;break
  if'next_page_token'not in payload:reason='missing_pagination_marker';break
  token=payload['next_page_token']
  if token is None:complete=True;break
  if not isinstance(token,str)or not token:reason='invalid_pagination_token';break
  if token in tokens:reason='pagination_cycle';break
  tokens.add(token);params={**task['parameters'],'page_token':token}
 else:reason='page_cap_reached'
 result={**task,'request_sha256':digest({'host':task['host'],'path':task['path'],'parameters':task['parameters']}),
  'pages':pages,'pages_complete':complete,'failure_reason':reason,'finished_at':now()}
 save('raw-option-inputs/'+task['task_id']+'/complete.json',result)
 return result

def payloads(record):
 for page in record['pages']:
  for attempt in page['attempts']:
   assert sha(ROOT/attempt['body_file'])==attempt['body_sha256']
   assert sha(ROOT/attempt['metadata_file'])==attempt['metadata_sha256']
  last=page['attempts'][-1]
  if last['status']==200:
   body=(ROOT/last['body_file']).read_bytes()
   try:yield json.loads(body)
   except(json.JSONDecodeError,UnicodeDecodeError):continue

def parallel(tasks,stage):
 records=[];started=time.monotonic()
 with ThreadPoolExecutor(max_workers=4)as pool:
  futures={pool.submit(fetch_task,t):t for t in tasks}
  for count,future in enumerate(as_completed(futures),1):
   task=futures[future]
   try:records.append(future.result())
   except Exception as error:records.append({**task,'pages':[],'pages_complete':False,'failure_reason':'collector_exception:'+type(error).__name__,'finished_at':now()})
   if count%20==0 or count==len(tasks):
    progress={'stage':stage,'completed':count,'total':len(tasks),'failed_tasks':sum(not r['pages_complete']for r in records),
       'elapsed_seconds':round(time.monotonic()-started),'updated_at':now()}
    save('acquisition-progress.json',progress);print(json.dumps(progress),flush=True)
 return sorted(records,key=lambda r:r['task_id'])

def task(task_id,host,path,params,field,cap,**extra):return{'task_id':task_id,'host':host,'path':path,
 'parameters':params,'response_field':field,'max_pages':cap,**extra}

def register():
 design=read(ROOT/'study-design.json');sample=read(ROOT/'sample-universe.json');current=read(ROOT/'current-underlying-references.json')
 assert len(sample['sample_symbols'])==40 and len(sample['underlying_references'])==400
 for name,value in design['input_sha256'].items():assert sha(ROOT/name)==value,name
 missing=[{**r,'reference_scope':'historical'}for r in sample['underlying_references']if r['symbol']=='SPY'and r['prior_close_reference']is None]
 missing +=[{**r,'case_id':design['current_snapshot_date']+'__SPY','date':design['current_snapshot_date'],'reference_scope':'current'}for r in current['rows']if r['symbol']=='SPY'and r['prior_close_reference']is None]
 assert len(missing)==11 and len({r['prior_session']for r in missing})==11
 supplemental_tasks=[]
 for r in missing:
  day=r['prior_session'];start=local_stamp(day,'00:00:00');end=start+timedelta(days=1,seconds=-1)
  params={'symbols':'SPY','timeframe':'1Day','start':fmt(start),'end':end.strftime('%Y-%m-%dT%H:%M:%S')+'.999999999Z',
      'feed':'sip','adjustment':'raw','limit':10000,'sort':'asc'}
  supplemental_tasks.append(task('supplement_SPY__'+day,'data.alpaca.markets','/v2/stocks/bars',params,'bars',2,reference_day=day))
 supplements={'id':'SPY_exact11_missing_prior_references_v1','registered_at':now(),'references':missing,'tasks':supplemental_tasks,
  'reason':'SPY control is absent from original broad-cache daily reference set. Fill only explicit missing prior-session references causally before option metadata/price selection.',
  'original_missing_status_preserved':True,'supersedes_reference_policy':'For these11cases only, a verified complete exact-SPY prior-day raw SIP daily close may supplement the original missing reference. The original sample/current reference files remain byte-unchanged; failure stays unknown, no fallback.',
  'input_sha256':{'study-design.json':sha(ROOT/'study-design.json'),'sample-universe.json':sha(ROOT/'sample-universe.json'),
      'current-underlying-references.json':sha(ROOT/'current-underlying-references.json'),'collect_option_inputs.py':sha(Path(__file__))}}
 contract_tasks=[]
 for day in design['history_dates']:
  end=min((datetime.fromisoformat(day)+timedelta(days=7)).date().isoformat(),design['period'][1])
  for symbol in sample['sample_symbols']:
   params={'underlying_symbols':symbol,'status':'inactive','expiration_date_gte':day,'expiration_date_lte':end,'show_deliverables':'true','limit':1000}
   contract_tasks.append(task('historical_contracts__'+day+'__'+symbol,'paper-api.alpaca.markets','/v2/options/contracts',params,'option_contracts',10,
      scope='historical',case_id=day+'__'+symbol,date=day,underlying_symbol=symbol))
 for symbol in sample['sample_symbols']:
  params={'underlying_symbols':symbol,'status':'active','expiration_date':'2026-10-09','show_deliverables':'true','limit':1000}
  contract_tasks.append(task('current_contracts__'+symbol,'paper-api.alpaca.markets','/v2/options/contracts',params,'option_contracts',10,
      scope='current',case_id=design['current_snapshot_date']+'__'+symbol,date=design['current_snapshot_date'],underlying_symbol=symbol))
 if(ROOT/'supplemental-reference-plan.json').exists():
  old=read(ROOT/'supplemental-reference-plan.json');assert old['input_sha256']==supplements['input_sha256']and old['tasks']==supplemental_tasks
 else:save('supplemental-reference-plan.json',supplements,immutable=True)
 plan={'id':'s500_options_frozen_acquisition_v1','registered_at':now(),'supplemental_reference_plan_sha256':sha(ROOT/'supplemental-reference-plan.json'),
    'contract_tasks':contract_tasks,'historical_cells':400,'current_cells':40,'expected_selected_history_rows':800,'expected_selected_current_rows':80,
    'historical_market_policy':{'window_et':['10:00:00','10:00:59.999999999'],'end_local_filter':'10:01:00 exclusive',
       'symbols_per_batch':100,'limit':10000,'max_pages':20,'feed_parameter':None},
    'current_quote_policy':{'feed':'indicative','symbols_per_batch':100,'opra_retry':False,'historical_quotes_probe':False},
    'transport':{'method':'GET','workers':4,'global_spacing_seconds':0.5,'attempts_per_page':5,'redirects':'reject'},
    'selection_policy':'Complete validated listing required. Earliest expiration across all returned call/put contracts, then separately nearest positive strike to frozen/supplemented prior close, contract symbol tiebreak. No option price/quality/returns consulted. Missing-reference/type/contract retained.',
    'source_sha256':{n:sha(ROOT/n)for n in ['study-design.json','sample-universe.json','current-underlying-references.json',
       'options-source-rules.json','options-doc-manifest.json','collect_option_inputs.py']},
    'market_request_plan':'Written only after immutable selected-contracts.json, before any new historicaloption event/currentquote collection.',
    'account_requests':0,'order_requests':0,'profit_backtest':False}
 if(ROOT/'acquisition-plan.json').exists():
  old=read(ROOT/'acquisition-plan.json');assert old['source_sha256']==plan['source_sha256']and old['contract_tasks']==contract_tasks
 else:save('acquisition-plan.json',plan,immutable=True)
 print(json.dumps({'registered':True,'supplemental_stock_tasks':11,'contract_list_tasks':440}),flush=True)

def supplement_references(records):
 plan=read(ROOT/'supplemental-reference-plan.json');by_day={r['reference_day']:r for r in records};rows=[]
 for source in plan['references']:
  record=by_day[source['prior_session']];bars=[]
  if record['pages_complete']:
   for payload in payloads(record):bars.extend(payload.get('bars',{}).get('SPY',[]))
  matched=[]
  for bar in bars:
   try:
    moment=datetime.fromisoformat(bar['t'].replace('Z','+00:00'))
    if moment.tzinfo is not None and moment.astimezone(NY).date().isoformat()==source['prior_session']:matched.append(bar)
   except(KeyError,ValueError,TypeError):pass
  value=positive(matched[0].get('c'))if len(matched)==1 else None
  status='supplemented_prior_close'if record['pages_complete']and value is not None else'unknown_supplement_failed_or_ambiguous'
  rows.append({k:source[k]for k in ['case_id','date','symbol','prior_session','reference_scope']}|{
    'original_prior_close_reference':None,'original_status':'missing','status':status,
    'prior_close_reference':str(value)if status=='supplemented_prior_close'else None,
    'source_timestamp':matched[0].get('t')if len(matched)==1 else None,'request_ids':[record['task_id']],
    'request_complete':record['pages_complete'],'matching_source_bars':len(matched),
    'point_in_time_or_split_adjusted_identity_proven':False})
 output={'generated_at':now(),'plan_sha256':sha(ROOT/'supplemental-reference-plan.json'),'rows':rows,
    'original_reference_files_unchanged':True,'selection_not_yet_run':True}
 save('supplemental-underlying-references.json',output)
 return rows

def sanitize_contract(row):
 result={k:row[k]for k in CONTRACT_KEYS if k in row}
 if'deliverables'in row:
  if not isinstance(row['deliverables'],list):result['deliverables']=None;result['deliverables_schema_unknown']=True
  else:
   result['deliverables']=[]
   for item in row['deliverables']:
    if not isinstance(item,dict):result['deliverables'].append({'schema_unknown':True});continue
    clean={k:item[k]for k in DELIVERABLE_KEYS if k in item}
    symbol=clean.get('symbol')
    if isinstance(symbol,str)and re.fullmatch('[A-Z0-9]{9}',symbol)and any(c.isdigit()for c in symbol):
     clean.pop('symbol');clean['symbol_identity_unresolved']=True
    result['deliverables'].append(clean)
 return result

def select_contracts(records,supplements):
 sample=read(ROOT/'sample-universe.json');current=read(ROOT/'current-underlying-references.json');design=read(ROOT/'study-design.json')
 references={('historical',r['case_id']):r for r in sample['underlying_references']}
 references.update({('current',design['current_snapshot_date']+'__'+r['symbol']):r for r in current['rows']})
 supplement_map={(r['reference_scope'],r['case_id']):r for r in supplements}
 selected={'historical':[],'current':[]};listing_summaries=[]
 for record in records:
  key=(record['scope'],record['case_id']);original=references[key];reference=original.get('prior_close_reference');source='original_frozen_reference'
  if reference is None and key in supplement_map:
   reference=supplement_map[key]['prior_close_reference'];source='supplemental_SPY_exact_prior_session'
  close=positive(reference);contracts=[];validation=[]
  if record['pages_complete']:
   for payload in payloads(record):contracts.extend(payload['option_contracts'])
  seen=set()
  for row in contracts:
   if not isinstance(row,dict):validation.append('non_object_contract');continue
   symbol=row.get('symbol');expiration=row.get('expiration_date')
   if not isinstance(symbol,str)or not re.fullmatch('[A-Z0-9]+',symbol):validation.append('invalid_contract_symbol')
   if symbol in seen:validation.append('duplicate_contract_symbol')
   seen.add(symbol)
   if row.get('underlying_symbol')!=record['underlying_symbol']:validation.append('unexpected_underlying')
   if row.get('status')!=record['parameters']['status']:validation.append('unexpected_current_status')
   if row.get('type')not in('call','put'):validation.append('unknown_contract_type')
   if positive(row.get('strike_price'))is None:validation.append('invalid_strike')
   try:
    assert datetime.fromisoformat(expiration).date().isoformat()==expiration
    if record['scope']=='historical':assert record['date']<=expiration<=record['parameters']['expiration_date_lte']
    else:assert expiration==record['parameters']['expiration_date']
   except(Exception):validation.append('expiration_outside_scope_or_invalid')
  first_expiry=min((r['expiration_date']for r in contracts),default=None)if not validation else None
  summary={'task_id':record['task_id'],'scope':record['scope'],'case_id':record['case_id'],'pages_complete':record['pages_complete'],
    'failure_reason':record['failure_reason'],'returned_contract_count':len(contracts),'validation_issues':sorted(set(validation)),
    'earliest_expiration':first_expiry,'prior_reference_available':close is not None}
  listing_summaries.append(summary)
  for kind in('call','put'):
   reasons=[];chosen=None
   if not record['pages_complete']:reasons.append('contract_listing_incomplete_or_failed')
   if validation:reasons.append('contract_listing_validation_unknown')
   if not contracts:reasons.append('no_contracts_returned')
   if close is None:reasons.append('missing_or_invalid_prior_reference')
   if not reasons:
    eligible=[r for r in contracts if r['expiration_date']==first_expiry and r['type']==kind]
    if not eligible:reasons.append('no_type_at_earliest_expiration')
    else:chosen=min(eligible,key=lambda r:(abs(Decimal(r['strike_price'])-close),r['symbol']))
   selected[record['scope']].append({'case_id':record['case_id'],'date':record['date'],
    'underlying_symbol':record['underlying_symbol'],'type':kind,'status':'selected'if chosen else'unknown_or_unavailable',
    'reasons':reasons,'contract':sanitize_contract(chosen)if chosen else None,'request_ids':[record['task_id']],
    'prior_close_reference':str(close)if close is not None else None,'prior_session':original['prior_session'],
    'original_prior_close_reference':original.get('prior_close_reference'),'reference_source':source,
    'earliest_expiration_in_listing':first_expiry,'historical_listing_availability_proven':False})
 selected['selection_frozen_at']=now();selected['source_sha256']={'acquisition-plan.json':sha(ROOT/'acquisition-plan.json'),
      'contract-list-manifest.json':sha(ROOT/'contract-list-manifest.json'),'supplemental-underlying-references.json':sha(ROOT/'supplemental-underlying-references.json')}
 save('selected-contracts.json',selected)
 save('contract-list-coverage.json',{'rows':listing_summaries,'historical_cells':400,'current_cells':40})
 return selected

def market_plan(selected):
 tasks=[];by_day=defaultdict(set)
 for row in selected['historical']:
  if row['contract']:by_day[row['date']].add(row['contract']['symbol'])
 for day,symbols in sorted(by_day.items()):
  start=local_stamp(day,'10:00:00');end=start+timedelta(seconds=59)
  for i in range(0,len(symbols),100):
   batch=sorted(symbols)[i:i+100]
   for kind in('bars','trades'):
    params={'symbols':','.join(batch),'start':fmt(start),'end':end.strftime('%Y-%m-%dT%H:%M:%S')+'.999999999Z','sort':'asc','limit':10000}
    if kind=='bars':params['timeframe']='1Min'
    tasks.append(task('history_'+kind+'__'+day+'__%03d'%(i//100),'data.alpaca.markets','/v1beta1/options/'+kind,params,kind,20,date=day,scope='historical'))
 current=sorted({r['contract']['symbol']for r in selected['current']if r['contract']})
 for i in range(0,len(current),100):
  tasks.append(task('current_indicative__%03d'%(i//100),'data.alpaca.markets','/v1beta1/options/quotes/latest',
   {'symbols':','.join(current[i:i+100]),'feed':'indicative'},'quotes',1,scope='current',unpaginated=True))
 plan={'registered_at':now(),'tasks':tasks,'selected_contracts_sha256':sha(ROOT/'selected-contracts.json'),
    'acquisition_plan_sha256':sha(ROOT/'acquisition-plan.json'),'no_price_response_yet_consulted_for_selection':True}
 save('market-request-plan.json',plan)
 return tasks

def derive_market(selected,records):
 by_request={r['task_id']:r for r in records};mapping={};event_sets={};quote_sets={}
 for record in records:
  field=record['response_field']
  for symbol in record['parameters']['symbols'].split(','):
   key=(record.get('date'),symbol,field)if field!='quotes'else(symbol,'quotes')
   assert key not in mapping;mapping[key]=record
  if not record['pages_complete']:continue
  target=quote_sets if field=='quotes'else event_sets
  for payload in payloads(record):
   for symbol,value in payload[field].items():
    key=(symbol,'quotes')if field=='quotes'else(record['date'],symbol,field)
    if field=='quotes':
     assert key not in target;target[key]=value
    else:target.setdefault(key,[]).extend(value)
 historical=[]
 for row in selected['historical']:
  output={k:row[k]for k in ['case_id','date','underlying_symbol','type','status']};output['contract_symbol']=row['contract']['symbol']if row['contract']else None
  output['selection_status']=output.pop('status');output['first_trade']=None
  if not row['contract']:
   output.update(bars_status='not_requested_no_selected_contract',trades_status='not_requested_no_selected_contract',bars_count=None,trades_count=None,request_ids=[])
  else:
   symbol=row['contract']['symbol'];start=local_stamp(row['date'],'10:00:00');lo=timestamp_ns(fmt(start));hi=lo+60000000000
   request_ids=[]
   for field,singular in [('bars','bar'),('trades','trade')]:
    key=(row['date'],symbol,field);record=mapping[key];request_ids.append(record['task_id']);values=event_sets.get(key,[])
    valid=[];invalid=0;outside=0
    for item in values:
     try:stamp=timestamp_ns(item.get('t'))
     except(ValueError,TypeError,AttributeError):invalid+=1;continue
     if lo<=stamp<hi:valid.append((stamp,item))
     else:outside+=1
    output[field+'_count']=len(valid)if record['pages_complete']else None
    output[field+'_status']=('request_incomplete_or_failed'if not record['pages_complete']else
      'invalid_timestamp_or_outside_interval'if invalid or outside else'events_observed'if valid else'no_events_returned')
    output[singular+'_invalid_timestamp_count']=invalid;output[singular+'_outside_interval_count']=outside
    if field=='trades'and valid:
     first=min(enumerate(valid),key=lambda v:(v[1][0],v[0]))[1][1]
     output['first_trade']={k:first[k]for k in TRADE_KEYS if k in first}
   output['request_ids']=request_ids
  output['historical_bid_ask_available']=False;output['trade_reference_is_execution_evidence']=False
  historical.append(output)
 current=[]
 for row in selected['current']:
  output={k:row[k]for k in ['case_id','date','underlying_symbol','type','status']};output['selection_status']=output.pop('status')
  output['contract_symbol']=row['contract']['symbol']if row['contract']else None;output['feed']='indicative';output['actual_opra_quote']=False
  if not row['contract']:output.update(status='not_requested_no_selected_contract',quote=None,source_request_id=None,requested_at=None,received_at=None)
  else:
   key=(row['contract']['symbol'],'quotes');record=mapping[key];quote=quote_sets.get(key);pages=record['pages']
   output.update(status='request_incomplete_or_failed'if not record['pages_complete']else'quote_observed'if isinstance(quote,dict)else'quote_missing_or_invalid',
      quote={k:quote[k]for k in QUOTE_KEYS if k in quote}if isinstance(quote,dict)else None,
      source_request_id=record['task_id'],requested_at=pages[0]['requested_at']if pages else None,received_at=pages[-1]['received_at']if pages else None)
  current.append(output)
 save('historical-option-events.json',{'rows':historical,'market_plan_sha256':sha(ROOT/'market-request-plan.json'),
    'selection_sha256':sha(ROOT/'selected-contracts.json'),'profit_or_wealth_computed':False})
 save('current-indicative-quotes.json',{'rows':current,'market_plan_sha256':sha(ROOT/'market-request-plan.json'),
    'source_warning':'Indicative derivatives, not actual OPRA. Observation may be afterhours; all quality and freshness gates remain separate.',
    'selection_sha256':sha(ROOT/'selected-contracts.json')})

def main():
 plan=read(ROOT/'acquisition-plan.json')
 for name,value in plan['source_sha256'].items():assert sha(ROOT/name)==value,name
 assert sha(ROOT/'supplemental-reference-plan.json')==plan['supplemental_reference_plan_sha256']
 supplemental=parallel(read(ROOT/'supplemental-reference-plan.json')['tasks'],'supplement_SPY_prior_references')
 save('supplemental-reference-manifest.json',{'records':supplemental,'plan_sha256':sha(ROOT/'supplemental-reference-plan.json')})
 supplements=supplement_references(supplemental)
 listings=parallel(plan['contract_tasks'],'contract_lists_400_history_40_current')
 save('contract-list-manifest.json',{'records':listings,'plan_sha256':sha(ROOT/'acquisition-plan.json')})
 selected=select_contracts(listings,supplements)
 market_tasks=market_plan(selected);market=parallel(market_tasks,'historical_events_and_current_indicative')
 save('option-market-manifest.json',{'records':market,'plan_sha256':sha(ROOT/'market-request-plan.json')})
 derive_market(selected,market)
 # Recheck every raw response and request metadata after the completed collection.
 records=supplemental+listings+market;attempt_count=0;raw_bytes=0
 for record in records:
  for page in record['pages']:
   for attempt in page['attempts']:
    assert sha(ROOT/attempt['body_file'])==attempt['body_sha256']and sha(ROOT/attempt['metadata_file'])==attempt['metadata_sha256']
    attempt_count+=1;raw_bytes+=attempt['bytes']
 for name in('sample-universe.json','current-underlying-references.json'):assert sha(ROOT/name)==plan['source_sha256'][name]
 output_names=['supplemental-underlying-references.json','supplemental-reference-manifest.json','contract-list-manifest.json',
   'contract-list-coverage.json','selected-contracts.json','market-request-plan.json','option-market-manifest.json',
   'historical-option-events.json','current-indicative-quotes.json']
 save('source-manifest.json',{'generated_at':now(),'records':records,'request_tasks':len(records),'http_attempts':attempt_count,
   'raw_response_bytes':raw_bytes,'failed_or_incomplete_tasks':sum(not r['pages_complete']for r in records),
   'every_raw_body_and_metadata_hash_verified':True,'input_sha256':{**plan['source_sha256'],
       'acquisition-plan.json':sha(ROOT/'acquisition-plan.json'),'supplemental-reference-plan.json':sha(ROOT/'supplemental-reference-plan.json')},
   'output_sha256':{name:sha(ROOT/name)for name in output_names},'original_reference_files_unchanged':True,
   'supplemental_references_before_contract_selection':True,'contract_selection_before_market_price_requests':True,
   'account_requests':0,'order_requests':0,'opra_quote_requests':0,'historical_quote_probe_requests':0,'profit_backtest':False})
 save('acquisition-progress.json',{'stage':'complete','completed_at':now(),'request_tasks':len(records),
    'failed_or_incomplete_tasks':sum(not r['pages_complete']for r in records),
    'selected_historical':sum(r['contract']is not None for r in selected['historical']),
    'selected_current':sum(r['contract']is not None for r in selected['current'])})
 print(json.dumps(read(ROOT/'acquisition-progress.json')),flush=True)

if __name__=='__main__':
 import sys
 register()if'--register'in sys.argv else main()
