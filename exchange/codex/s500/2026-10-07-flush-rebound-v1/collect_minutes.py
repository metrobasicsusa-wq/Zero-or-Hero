"""Bounded GET-only SIP minute acquisition for an immutable prior-session sample."""
import hashlib,json,os,re,time,threading,urllib.request,urllib.parse,urllib.error
from pathlib import Path
from datetime import datetime,timezone,timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal,InvalidOperation
from concurrent.futures import ThreadPoolExecutor,as_completed
from collections import Counter

ROOT=Path(__file__).parent;PRIVATE=ROOT/'private-inputs';ET=ZoneInfo('America/New_York')
LOCK=threading.Lock();WRITE_LOCK=threading.Lock();LAST_REQUEST=0.;STARTED=0.;AUTHORIZATION_STOP=threading.Event()
MAX_SECONDS=7200;MAX_PAGES=20;MAX_ATTEMPTS=4;MIN_REQUEST_SPACING=.5
FIELDS=('t','o','h','l','c','v','n','vw')

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def now():return datetime.now(timezone.utc).isoformat()
def write(p,obj):
 tmp=p.with_suffix(p.suffix+'.tmp');tmp.write_text(json.dumps(obj,ensure_ascii=False,separators=(',',':'))+'\n');tmp.replace(p)
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):raise RuntimeError('redirect_blocked')

def get_page(day,page,params):
 global LAST_REQUEST
 attempts=[]
 for attempt in range(1,MAX_ATTEMPTS+1):
  if time.monotonic()-STARTED>MAX_SECONDS:return None,attempts,'global_deadline_reached'
  if AUTHORIZATION_STOP.is_set():return None,attempts,'not_attempted_authorization_circuit_open'
  with LOCK:
   if AUTHORIZATION_STOP.is_set():return None,attempts,'not_attempted_authorization_circuit_open'
   delay=MIN_REQUEST_SPACING-(time.monotonic()-LAST_REQUEST)
   if delay>0:time.sleep(delay)
   if AUTHORIZATION_STOP.is_set():return None,attempts,'not_attempted_authorization_circuit_open'
   LAST_REQUEST=time.monotonic()
  reqid=f'{day}-page{page:02d}-attempt{attempt}'
  at=now();public_params={k:v for k,v in params.items() if k!='page_token'}
  rec={'request_id':reqid,'method':'GET','host':'data.alpaca.markets','path':'/v2/stocks/bars','params':public_params,'page_token_sha256':hashlib.sha256(params['page_token'].encode()).hexdigest() if 'page_token'in params else None,'requested_at':at}
  req=urllib.request.Request('https://data.alpaca.markets/v2/stocks/bars?'+urllib.parse.urlencode(params),headers={'APCA-API-KEY-ID':os.environ['ALPACA_500_API_KEY'],'APCA-API-SECRET-KEY':os.environ['ALPACA_500_SECRET_KEY']})
  try:
   with urllib.request.build_opener(NoRedirect).open(req,timeout=40) as response:body=response.read();status=response.status
   path=PRIVATE/'minute-pages'/f'{reqid}.json';path.write_bytes(body)
   rec.update({'status':status,'received_at':now(),'response_file':path.name,'response_bytes':len(body),'response_sha256':sha(path)})
   obj=json.loads(body);assert isinstance(obj,dict) and isinstance(obj.get('bars'),dict)
   rec['bar_count']=sum(len(v) for v in obj['bars'].values());rec['next_page_exists']=bool(obj.get('next_page_token'));attempts.append(rec)
   return obj,attempts,None
  except urllib.error.HTTPError as error:
   body=error.read();path=PRIVATE/'minute-pages'/f'{reqid}-error.json';path.write_bytes(body)
   rec.update({'status':error.code,'received_at':now(),'error':'http_error','response_file':path.name,'response_bytes':len(body),'response_sha256':sha(path)});attempts.append(rec)
   if error.code in (401,403):
    AUTHORIZATION_STOP.set();return None,attempts,'http_'+str(error.code)+'_authorization_denied'
   if error.code not in (429,500,502,503,504):return None,attempts,'http_'+str(error.code)
  except (TimeoutError,urllib.error.URLError):
   rec.update({'status':None,'received_at':now(),'error':'timeout_or_network_error'});attempts.append(rec)
  except Exception as error:
   rec.update({'status':None,'received_at':now(),'error':type(error).__name__});attempts.append(rec);return None,attempts,'invalid_response'
  if attempt<MAX_ATTEMPTS:time.sleep(min(2**(attempt-1),8))
 return None,attempts,'read_retry_exhausted'

def is_valid(b):
 try:
  o,h,l,c,v=[Decimal(str(b[k])) for k in ('o','h','l','c','v')]
  return all(x.is_finite() for x in (o,h,l,c,v)) and min(o,h,l,c)>0 and v>=0 and l<=min(o,c)<=max(o,c)<=h
 except (KeyError,TypeError,ValueError,InvalidOperation):return False

def coverage(day,symbol,bars,start,end,complete,status):
 expected=[(start+timedelta(minutes=i)).astimezone(timezone.utc).isoformat().replace('+00:00','Z') for i in range(int((end-start).total_seconds()/60))]
 expectedset=set(expected);counts=Counter();invalid=[];unparseable=[];nonminute=[];outsession=[];sourceorder=[];last=None
 for i,b in enumerate(bars):
  t=b.get('t')
  try:
   dt=datetime.fromisoformat(t.replace('Z','+00:00'));assert dt.tzinfo is not None
   stamp=dt.astimezone(timezone.utc).isoformat().replace('+00:00','Z')
   fraction=re.search(r'T\d{2}:\d{2}:\d{2}\.(\d+)',t)
   has_subsecond=bool(fraction and any(d!='0' for d in fraction.group(1)))
   if dt.second or has_subsecond:nonminute.append(t)
   if has_subsecond:stamp=t
   if stamp not in expectedset:outsession.append(t)
   counts[stamp]+=1
   if last and dt<last:sourceorder.append(i)
   last=dt
  except Exception:unparseable.append(t);stamp=str(t)
  if not is_valid(b):invalid.append(stamp)
 present=set(counts)
 return {'case_id':day+'__'+symbol,'date':day,'symbol':symbol,'request_complete':complete,'request_status':status,'returned_bars':len(bars),'expected_regular_session_minutes':len(expected),'regular_session_timestamps_present':len(present&expectedset),'missing_regular_session_timestamps':[t for t in expected if t not in present],'early30_expected':30,'early30_present':sum(t in present for t in expected[:30]),'search90_expected':min(90,len(expected)),'search90_present':sum(t in present for t in expected[:90]),'duplicate_timestamps':{t:n for t,n in counts.items() if n>1},'invalid_ohlcv_timestamps':invalid,'unparseable_timestamps':unparseable,'nonminute_timestamps':nonminute,'outside_regular_session_timestamps':outsession,'out_of_order_bar_indices':sourceorder,'no_forward_fill_or_deduplication':True}

def collect_day(scope):
 day=scope['date'];symbols=scope['selected_symbols'];start=datetime.fromisoformat(day+'T'+scope['open_et']).replace(tzinfo=ET);end=datetime.fromisoformat(day+'T'+scope['close_et']).replace(tzinfo=ET)
 # End is exclusive via exact previous nanosecond to prevent close-auction bar from entering.
 utcend=end.astimezone(timezone.utc);lastsec=utcend-timedelta(seconds=1)
 params={'symbols':','.join(symbols),'timeframe':'1Min','start':start.astimezone(timezone.utc).isoformat().replace('+00:00','Z'),'end':lastsec.strftime('%Y-%m-%dT%H:%M:%S')+'.999999999Z','adjustment':'raw','feed':'sip','sort':'asc','limit':10000}
 initial=dict(params);combined={s:[] for s in symbols};requests=[];seen=set();complete=False;status='not_started';unexpectedsymbols=[]
 for page in range(1,MAX_PAGES+1):
  response,attempts,error=get_page(day,page,params);requests+=attempts
  if error:status=error;break
  for symbol,bars in response['bars'].items():
   if symbol not in combined:unexpectedsymbols.append(symbol);continue
   combined[symbol].extend([{k:b[k] for k in FIELDS if k in b} for b in bars])
  token=response.get('next_page_token')
  if not token:complete=True;status='complete';break
  if token in seen:status='repeated_page_token';break
  seen.add(token);params['page_token']=token
 else:status='page_limit_reached'
 if unexpectedsymbols:status='unexpected_response_symbols';complete=False
 file=PRIVATE/'minute-days'/f'{day}.json'
 output={'date':day,'requested_symbols':symbols,'input':initial,'bars':combined,'request_complete':complete,'status':status,'source_request_ids':[r['request_id'] for r in requests],'unexpected_response_symbols':unexpectedsymbols,'created_at':now()};write(file,output)
 covered=[coverage(day,s,combined[s],start,end,complete,status) for s in symbols]
 result={'date':day,'requested_symbol_count':len(symbols),'returned_bar_count':sum(len(v) for v in combined.values()),'status':status,'request_complete':complete,'requests':requests,'normalized_file':file.name,'normalized_sha256':sha(file),'normalized_bytes':file.stat().st_size}
 write(PRIVATE/'day-manifests'/f'{day}.json',result)
 write(PRIVATE/'day-coverage'/f'{day}.json',covered)
 return result,covered

def main():
 global STARTED
 STARTED=time.monotonic()
 binding=json.loads((ROOT/'acquisition-authorization.json').read_text());assert binding['minute_fetch_authorized'] is True
 for name,expected in binding['bound_files'].items():assert sha(ROOT/name)==expected,(name,'sha mismatch')
 scopes=json.loads((ROOT/'daily-scope.json').read_text())['daily'];assert len(scopes)==191
 for name in ['minute-pages','minute-days','day-manifests','day-coverage']:(PRIVATE/name).mkdir(exist_ok=True)
 results=[];coverage_rows=[];started=now()
 with ThreadPoolExecutor(max_workers=4) as pool:
  future_days={pool.submit(collect_day,s):s['date'] for s in scopes}
  for future in as_completed(future_days):
   day=future_days[future]
   try:r,c=future.result()
   except Exception as error:
    status='collector_exception_'+type(error).__name__
    scope=next(s for s in scopes if s['date']==day)
    start=datetime.fromisoformat(day+'T'+scope['open_et']).replace(tzinfo=ET);end=datetime.fromisoformat(day+'T'+scope['close_et']).replace(tzinfo=ET)
    c=[coverage(day,symbol,[],start,end,False,status) for symbol in scope['selected_symbols']]
    r={'date':day,'status':status,'requested_symbol_count':len(scope['selected_symbols']),'request_complete':False,'requests':[],'returned_bar_count':0}
    write(PRIVATE/'day-manifests'/f'{day}.json',r);write(PRIVATE/'day-coverage'/f'{day}.json',c)
   results.append(r);coverage_rows+=c
   progress={'started_at':started,'updated_at':now(),'completed_dates':len(results),'total_dates':191,'statuses':dict(Counter(x['status'] for x in results)),'returned_bars':sum(x['returned_bar_count'] for x in results),'attempts':sum(len(x['requests']) for x in results),'elapsed_seconds':round(time.monotonic()-STARTED,1)};write(ROOT/'acquisition-progress.json',progress)
   if len(results)%10==0 or len(results)==191:print(json.dumps(progress),flush=True)
 results.sort(key=lambda x:x['date']);coverage_rows.sort(key=lambda x:(x['date'],x['symbol']))
 write(ROOT/'minute-source-manifest.json',{'started_at':started,'completed_at':now(),'authorization_sha256':sha(ROOT/'acquisition-authorization.json'),'collector_sha256':sha(Path(__file__)),'global_request_rate_limit_per_second':2,'max_concurrent_dates':4,'max_pages_per_date':20,'max_attempts_per_page':4,'max_elapsed_seconds':7200,'authorization_circuit_open':AUTHORIZATION_STOP.is_set(),'days':results})
 write(ROOT/'symbol-session-coverage.json',{'created_at':now(),'rows':coverage_rows})
 summary={'completed_at':now(),'authorization_circuit_open':AUTHORIZATION_STOP.is_set(),'days':len(results),'complete_days':sum(r['request_complete'] for r in results),'status_counts':dict(Counter(r['status'] for r in results)),'symbol_session_rows':len(coverage_rows),'requested_symbol_sessions':sum(s['selected_count'] for s in scopes),'returned_bars':sum(r['returned_bar_count'] for r in results),'complete_early30_rows':sum(r['early30_present']==30 for r in coverage_rows),'complete_search90_rows':sum(r['search90_present']==r['search90_expected'] for r in coverage_rows),'rows_with_missing_minutes':sum(bool(r['missing_regular_session_timestamps']) for r in coverage_rows),'rows_with_duplicates':sum(bool(r['duplicate_timestamps']) for r in coverage_rows),'rows_with_invalid_ohlcv':sum(bool(r['invalid_ohlcv_timestamps']) for r in coverage_rows),'elapsed_seconds':round(time.monotonic()-STARTED,1),'requests':sum(len(r['requests']) for r in results)}
 write(ROOT/'minute-acquisition-summary.json',summary);print(json.dumps(summary),flush=True)

if __name__=='__main__':main()
