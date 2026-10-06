"""Bounded, resumable research requests. Only a fixed market-data GET route."""
import json,os,time,threading,hashlib,urllib.request,urllib.parse,urllib.error
from pathlib import Path
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
ROOT=Path(__file__).parent
NY=ZoneInfo('America/New_York')
_lock=threading.Lock();_next=0.0
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise RuntimeError('market_redirect_rejected')

def get(params):
    global _next
    allowed={'symbols','timeframe','start','end','limit','adjustment','feed','sort','page_token'}
    if not set(params)<=allowed:raise ValueError('invalid_request_parameter')
    if params.get('timeframe') not in ['1Min','5Min']:raise ValueError('invalid_timeframe')
    for attempt in range(5):
        with _lock:
            pause=max(0,_next-time.monotonic());_next=max(_next,time.monotonic())+.42
        if pause:time.sleep(pause)
        req=urllib.request.Request('https://data.alpaca.markets/v2/stocks/bars?'+urllib.parse.urlencode(params),headers={'APCA-API-KEY-ID':os.environ['ALPACA_500_API_KEY'],'APCA-API-SECRET-KEY':os.environ['ALPACA_500_SECRET_KEY']},method='GET')
        try:
            with urllib.request.build_opener(NoRedirect).open(req,timeout=40) as response:return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code not in [429,500,502,503,504]:raise RuntimeError('market_http_'+str(error.code)) from None
            time.sleep(min(2**attempt,12))
        except (TimeoutError,urllib.error.URLError):time.sleep(min(2**attempt,12))
    raise RuntimeError('market_retry_exhausted')

def stamp(day,clock):return datetime.fromisoformat(day+'T'+clock+':00').replace(tzinfo=NY).astimezone(timezone.utc).isoformat()
def local_time(bar):return datetime.fromisoformat(bar['t'].replace('Z','+00:00')).astimezone(NY)
def atomic(path,payload):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_suffix(path.suffix+'.tmp');temp.write_text(json.dumps(payload,separators=(',',':'))+'\n');temp.replace(path)
def fetch_window(symbols,day,start,end,timeframe,folder):
    symbols=sorted(set(symbols));params={'symbols':','.join(symbols),'timeframe':timeframe,'start':stamp(day,start),'end':stamp(day,end),'limit':10000,'adjustment':'raw','feed':'sip','sort':'asc'}
    digest=hashlib.sha256(json.dumps(params,sort_keys=True).encode()).hexdigest();folder=Path(folder);meta=folder/'complete.json'
    if meta.exists():
        result=json.loads(meta.read_text());assert result['request_sha256']==digest
        for part in result['pages']:
            assert hashlib.sha256((folder/part['name']).read_bytes()).hexdigest()==part['sha256']
        return result
    pages=[];page=0;seen=set();request=dict(params)
    while True:
        path=folder/f'page-{page:04d}.json'
        if path.exists():
            stored=json.loads(path.read_text());assert stored['base_request_sha256']==digest and stored['page_number']==page
            request_hash=hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest()
            if stored.get('page_request_sha256') is None:
                if page!=0:raise RuntimeError('legacy_resume_page_chain_unverified')
            elif stored['page_request_sha256']!=request_hash:raise RuntimeError('resume_page_chain_mismatch')
            response=stored['response']
        else:
            response=get(request);assert isinstance(response.get('bars'),dict) and set(response['bars'])<=set(symbols)
            atomic(path,{'base_request_sha256':digest,'page_number':page,'page_request_sha256':hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest(),'retrieved_at':datetime.now(timezone.utc).isoformat(),'response':response})
        pages.append({'name':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size,'bar_count':sum(len(v) for v in response['bars'].values())})
        token=response.get('next_page_token');page+=1
        if not token:break
        if token in seen:raise RuntimeError('pagination_cycle')
        if page>50:raise RuntimeError('pagination_budget_cap')
        seen.add(token);request['page_token']=token
    result={'parameters':params,'request_sha256':digest,'pages':pages,'symbols':symbols,'finished_at':datetime.now(timezone.utc).isoformat()}
    atomic(meta,result);return result

def load_window(folder):
    folder=Path(folder);meta=json.loads((folder/'complete.json').read_text());combined={s:[] for s in meta['symbols']}
    for part in meta['pages']:
        path=folder/part['name'];assert hashlib.sha256(path.read_bytes()).hexdigest()==part['sha256']
        response=json.loads(path.read_text())['response']
        for symbol,bars in response['bars'].items():combined[symbol].extend(bars)
    for symbol,rows in combined.items():
        keys=[r['t'] for r in rows]
        if len(keys)!=len(set(keys)):raise RuntimeError('duplicate_bar_'+symbol)
    return combined
