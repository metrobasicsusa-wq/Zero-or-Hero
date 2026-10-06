"""Bounded, credential-safe read-only option capability probes; no account/orders."""
from pathlib import Path
from datetime import datetime,timezone
from decimal import Decimal
import hashlib,json,os,time,urllib.error,urllib.parse,urllib.request

ROOT=Path(__file__).resolve().parent
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise RuntimeError('redirect_rejected')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(n,o):
    p=ROOT/n;p.parent.mkdir(parents=True,exist_ok=True)
    assert not p.exists(), 'Preserve existing artifacts'
    p.write_text(json.dumps(o,separators=(',',':'))+'\n')

def get(name,host,path,params):
    allowed={
      'paper-api.alpaca.markets':{'/v2/options/contracts'},
      'data.alpaca.markets':{'/v1beta1/options/quotes/latest','/v1beta1/options/quotes','/v1beta1/options/bars','/v1beta1/options/trades'}}
    assert path in allowed.get(host,set())
    assert name.replace('_','').replace('-','').isalnum()
    url='https://'+host+path+'?'+urllib.parse.urlencode(params)
    requested=datetime.now(timezone.utc).isoformat();status=None;payload=None;error_type=None
    for attempt in range(3):
        time.sleep(.5)
        req=urllib.request.Request(url,method='GET',headers={
          'APCA-API-KEY-ID':os.environ['ALPACA_500_API_KEY'],
          'APCA-API-SECRET-KEY':os.environ['ALPACA_500_SECRET_KEY']})
        try:
            with urllib.request.build_opener(NoRedirect).open(req,timeout=30) as response:
                status=response.status;body=response.read()
                try:payload=json.loads(body)
                except json.JSONDecodeError:payload={'non_json_sha256':hashlib.sha256(body).hexdigest(),'bytes':len(body)}
        except urllib.error.HTTPError as error:
            status=error.code;body=error.read()
            try:payload=json.loads(body)
            except json.JSONDecodeError:payload={'non_json_sha256':hashlib.sha256(body).hexdigest(),'bytes':len(body)}
        except (urllib.error.URLError,TimeoutError,RuntimeError) as error:
            error_type=type(error).__name__
        if status not in (None,429,500,502,503,504):break
    envelope={'requested_at':requested,'received_at':datetime.now(timezone.utc).isoformat(),'host':host,'path':path,'parameters':params,
      'status':status,'transport_error_type':error_type,'response':payload}
    filename='raw-probes/'+name+'.json';save(filename,envelope)
    record={k:v for k,v in envelope.items() if k!='response'}
    record.update(raw_file=filename,raw_sha256=sha(ROOT/filename),raw_bytes=(ROOT/filename).stat().st_size)
    if isinstance(payload,dict):
        record['response_keys']=sorted(payload)
        if isinstance(payload.get('message'),str):
            message=payload['message'].lower()
            record['error_classification']=('opra_subscription_required' if 'subscription' in message and 'opra' in message else
              'permission_or_authentication' if status in (401,403) else 'not_found' if status==404 else 'other')
    return record,payload

def register():
    save('capability-plan.json',{'id':'s500_options_source_capability_v1','registered_at':datetime.now(timezone.utc).isoformat(),
      'scope':'API capability discovery only, no strategy signals, PnL, orders or account reads.',
      'historical_period':['2026-01-02','2026-10-05'],'snapshot_date':'2026-10-06',
      'contract_probes':[{'name':'expired','status':'inactive','expiration_date':'2026-10-02'},{'name':'current','status':'active','expiration_date':'2026-10-09'}],
      'underlying_for_schema_probe':'SPY','limit':1000,'maximum_pages_each':4,'show_deliverables':True,
      'contract_choice':'Within successfully enumerated SPY C and P contracts, choose middle sorted strike (floor index n/2), symbol tiebreak. This is a capability sample, not strategy or affordability filtering.',
      'market_probes':'On first discovered expired call: historical OPRA bars, trades and hypothesized quotes route over14:00-14:01UTC onOct2. On current call and put: OPRA/indicative latestquotes. One permission probe each, never promote indicative torealOPRA.',
      'historical_quote_route':'Previously404, absent from current official reference index as of initialdocreview; an explicit single probe checks currentlyavailable route, not an assumed supported endpoint.',
      'response_policy':'Preserve status/raw private/sourcehash and paging; a200emptyresponse is not coverage,403 not a missingcontract,404 not proof allhistoricaldata nonexistent.',
      'network':'Only fixed API GET contract-directory and marketdata routes, inherited proxy/TLS, no redirects, boundedretries.',
      'script_sha256':sha(Path(__file__)),'parent_commit':'ba999fff6c09ee41c45dffaf409c6d53375d9008'})

def main():
    plan=json.loads((ROOT/'capability-plan.json').read_text());assert plan['script_sha256']==sha(Path(__file__))
    records=[];selections={}
    for spec in plan['contract_probes']:
        params={'underlying_symbols':'SPY','status':spec['status'],'expiration_date':spec['expiration_date'],'show_deliverables':'true','limit':1000}
        rows=[];tokens=set();complete=False
        for page in range(plan['maximum_pages_each']):
            record,payload=get(spec['name']+'-contracts-'+str(page),'paper-api.alpaca.markets','/v2/options/contracts',params)
            records.append(record)
            if record['status']!=200 or not isinstance(payload,dict) or not isinstance(payload.get('option_contracts'),list):break
            rows.extend(payload['option_contracts']);token=payload.get('next_page_token')
            if not token:complete=True;break
            assert token not in tokens;tokens.add(token);params={**params,'page_token':token}
        chosen={}
        if complete:
            for kind in ('call','put'):
                eligible=sorted((r for r in rows if r.get('type')==kind),key=lambda r:(Decimal(r['strike_price']),r['symbol']))
                if eligible:chosen[kind]=eligible[len(eligible)//2]['symbol']
        selections[spec['name']]={'rows':len(rows),'pages_complete':complete,'selected_symbols':chosen}
    historical=selections['expired']['selected_symbols'].get('call')
    if historical:
        for endpoint in ('bars','trades','quotes'):
            params={'symbols':historical,'start':'2026-10-02T14:00:00Z','end':'2026-10-02T14:01:00Z','feed':'opra','limit':100,'sort':'asc'}
            if endpoint=='bars':params['timeframe']='1Min'
            record,_=get('historical-'+endpoint,'data.alpaca.markets','/v1beta1/options/'+endpoint,params);records.append(record)
    current=','.join(selections['current']['selected_symbols'].values())
    if current:
        for feed in ('opra','indicative'):
            record,_=get('latest-'+feed,'data.alpaca.markets','/v1beta1/options/quotes/latest',{'symbols':current,'feed':feed});records.append(record)
    save('capability-results.json',{'finished_at':datetime.now(timezone.utc).isoformat(),'plan_sha256':sha(ROOT/'capability-plan.json'),
      'records':records,'selection':selections,'broker_orders_sent':0,'account_requests':0,'strategy_returns_computed':False})
    print(json.dumps({'selection':selections,'requests':[{'path':r['path'],'status':r['status'],'error_classification':r.get('error_classification')} for r in records]},indent=2))

if __name__=='__main__':
    import sys
    register() if '--register' in sys.argv else main()
