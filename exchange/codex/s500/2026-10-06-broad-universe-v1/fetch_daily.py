"""GET-only broad research fetch with bounded retries and resumable batches."""
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).parent


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise RuntimeError('redirect_blocked')


def get(host,path,params):
    for attempt in range(5):
        time.sleep(.35)
        req=urllib.request.Request('https://'+host+path+'?'+urllib.parse.urlencode(params),headers={
            'APCA-API-KEY-ID':os.environ['ALPACA_500_API_KEY'],
            'APCA-API-SECRET-KEY':os.environ['ALPACA_500_SECRET_KEY']})
        try:
            with urllib.request.build_opener(NoRedirect).open(req,timeout=40) as response:return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code not in (429,500,502,503,504):raise RuntimeError('http_'+str(error.code)) from None
            time.sleep(min(2**attempt,16))
        except (TimeoutError,urllib.error.URLError):
            time.sleep(min(2**attempt,16))
    raise RuntimeError('read_retry_exhausted')


def main():
    p=json.loads((ROOT/'protocol.json').read_text());universe=json.loads((ROOT/'universe.json').read_text())
    raw=ROOT/'raw';raw.mkdir(exist_ok=True)
    manifest={'started_at':datetime.now(timezone.utc).isoformat(),'protocol_sha256':hashlib.sha256((ROOT/'protocol.json').read_bytes()).hexdigest(),'files':[]}
    def register(name,body=None):
        path=raw/name
        if body is not None:path.write_text(json.dumps(body,separators=(',',':')))
        manifest['files'].append({'name':name,'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    calendar=get('paper-api.alpaca.markets','/v2/calendar',{'start':p['input']['start'][:10],'end':p['evaluation_end']})
    register('calendar.json',calendar)
    for offset in range(0,len(universe),64):
        symbols=[x['symbol'] for x in universe[offset:offset+64]];name=f'batch-{offset:05d}.json'
        if (raw/name).exists():
            d=json.loads((raw/name).read_text())
            assert d['requested_symbols']==symbols and d['input']==p['input']
            register(name);continue
        params={**p['input'],'symbols':','.join(symbols),'limit':10000,'sort':'asc'}
        combined={s:[] for s in symbols};seen=set();pages=0
        while True:
            response=get('data.alpaca.markets','/v2/stocks/bars',params);pages+=1
            for symbol,bars in response.get('bars',{}).items():
                assert symbol in combined;combined[symbol].extend(bars)
            token=response.get('next_page_token')
            if not token:break
            assert token not in seen;seen.add(token);params['page_token']=token
        register(name,{'requested_symbols':symbols,'input':p['input'],'bars':combined,'pages':pages,
                       'retrieved_at':datetime.now(timezone.utc).isoformat()})
        if offset%256==0 or offset+64>=len(universe):
            print(json.dumps({'symbols_completed':min(offset+64,len(universe)),'total':len(universe)}),flush=True)
    manifest['finished_at']=datetime.now(timezone.utc).isoformat()
    (ROOT/'input-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')


if __name__=='__main__':main()
