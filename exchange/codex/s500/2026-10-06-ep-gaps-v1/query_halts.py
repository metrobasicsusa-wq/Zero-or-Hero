#!/usr/bin/env python3
"""Two bounded public historical halt queries; no broker credentials or mutation."""
from pathlib import Path
import concurrent.futures
import datetime as dt
import hashlib
import json
import urllib.request
import urllib.error

ROOT=Path(__file__).resolve().parent
RAW=ROOT/'raw-halts'
URL='https://www.nasdaqtrader.com/RPCHandler.axd'
TARGETS=[('REPL','2026-06-01T09:31:00-04:00'),('SPCE','2026-06-01T10:16:00-04:00')]

def query(target):
    symbol, time=target
    # This method and parameter ordering are observed in the official search page.
    # The conventional RPC transport route is a hypothesis because its linked
    # rpcclient.axd returned "Request is not valid."; do not assert it is verified.
    payload={'id':1,'method':'BL_TradeHalt.SearchTradeHaltsNEW','params':[symbol,'','','','','','']}
    data=json.dumps(payload,separators=(',',':')).encode()
    meta={'symbol':symbol,'target_minute_et':time,'url':URL,'method':'POST',
      'query_parameters':payload,'query_scope':'Exact symbol, other official search filters empty; vendor says last year.',
      'retrieved_at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'authorization_headers_sent':False,'tls_verification':True,
      'method_parameter_source':'https://www.nasdaqtrader.com/Trader.aspx?id=TradingHaltSearch',
      'transport_endpoint_confirmed_from_page':False,
      'halt_at_target':None,'absence_of_halt_established':False,'event_query_attempt':1}
    try:
        request=urllib.request.Request(URL,data=data,method='POST',headers={
          'Content-Type':'application/json','User-Agent':'Public historical halt research (Python urllib)',
          'Referer':'https://www.nasdaqtrader.com/Trader.aspx?id=TradingHaltSearch'})
        with urllib.request.urlopen(request,timeout=45) as response:
            body=response.read(); meta.update(http_status=response.status,final_url=response.url,
              content_type=response.headers.get('Content-Type'))
    except urllib.error.HTTPError as e:
        body=e.read(); meta.update(http_status=e.code,error_type='HTTPError')
    except Exception as e:
        body=b''; meta.update(http_status=None,error_type=type(e).__name__)
    meta.update(body_sha256=hashlib.sha256(body).hexdigest(),bytes=len(body))
    (RAW/(symbol+'__attempt1.raw')).write_bytes(body)
    (RAW/(symbol+'__attempt1.meta.json')).write_text(json.dumps(meta,indent=2)+'\n')
    return meta

if __name__=='__main__':
    RAW.mkdir(parents=True,exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        records=list(pool.map(query,TARGETS))
    (ROOT/'halt-query-attempts.json').write_text(json.dumps({'attempts':records},indent=2)+'\n')
    print(json.dumps([{'symbol':r['symbol'],'status':r['http_status'],'bytes':r['bytes']} for r in records]))
