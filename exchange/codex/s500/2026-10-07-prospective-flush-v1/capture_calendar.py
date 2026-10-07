"""One GET to the trading calendar; no prices, accounts or orders."""
from pathlib import Path
from datetime import datetime,timezone
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.parse import urlencode
from urllib.error import HTTPError
import json,os,hashlib
ROOT=Path(__file__).parent
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise RuntimeError('redirect_blocked')
def main():
    output=ROOT/'private-inputs/calendar-response.json'
    assert not output.exists(),'Do not overwrite a captured source'
    params={'start':'2026-09-01','end':'2026-12-31'}
    record={'method':'GET','host':'paper-api.alpaca.markets','path':'/v2/calendar','params':params,'requested_at':datetime.now(timezone.utc).isoformat()}
    request=Request('https://paper-api.alpaca.markets/v2/calendar?'+urlencode(params),headers={'APCA-API-KEY-ID':os.environ['ALPACA_500_API_KEY'],'APCA-API-SECRET-KEY':os.environ['ALPACA_500_SECRET_KEY']})
    try:
        with build_opener(NoRedirect).open(request,timeout=40) as response:
            body=response.read();record['status']=response.status
    except HTTPError as error:
        body=error.read();record['status']=error.code
    record['received_at']=datetime.now(timezone.utc).isoformat()
    output.write_bytes(body);record.update({'response_sha256':hashlib.sha256(body).hexdigest(),'response_bytes':len(body),'private_response_name':output.name})
    (ROOT/'calendar-source.json').write_text(json.dumps(record,indent=2)+'\n')
    assert record['status']==200,'Calendar request failed; no unchanged authorization retry'
    rows=json.loads(body);assert isinstance(rows,list) and rows
    seen=set();sessions=[]
    for row in rows:
        day=row['date'];assert '2026-09-01'<=day<='2026-12-31' and day not in seen
        seen.add(day);sessions.append({'date':day,'open':row['open'],'close':row['close']})
    sessions.sort(key=lambda r:r['date'])
    (ROOT/'calendar-sessions.json').write_text(json.dumps(sessions,indent=2)+'\n')
    future=[r for r in sessions if r['date']>='2026-10-08']
    print(json.dumps({'calendar_status':record['status'],'future_sessions':len(future),'first':future[0],'last':future[-1],'review_dates':[future[19]['date'],future[39]['date'],future[-1]['date']],'short_sessions':[r for r in future if r['close']!='16:00'],'price_or_account_requests':0}))
if __name__=='__main__':main()
