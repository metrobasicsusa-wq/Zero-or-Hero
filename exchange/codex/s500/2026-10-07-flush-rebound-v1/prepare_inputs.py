"""Read-only prior-session scope preparation. Minute prices are never loaded here."""
import csv, hashlib, json, os, statistics, urllib.request, urllib.parse
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal
from collections import Counter

ROOT=Path(__file__).parent
OLD=ROOT.parent/'s500-broad-20261006'
STORE=['MU','STX','WDC','SNDK','NTAP','RMBS','SIMO','P']

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(n,obj): (ROOT/n).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs): raise RuntimeError('redirect_blocked')

def main():
 manifest=json.loads((OLD/'input-manifest.json').read_text()); source=[]; daily={}; symbol_source={}
 for item in manifest['files']:
  p=OLD/'raw'/item['name']; assert sha(p)==item['sha256'], item['name'];source.append({'source':'prior_broad_daily','name':item['name'],'sha256':sha(p),'bytes':p.stat().st_size})
  if item['name'].startswith('batch-'):
   obj=json.loads(p.read_text());assert obj['input']['feed']=='sip' and obj['input']['adjustment']=='raw'
   for symbol,bars in obj['bars'].items():
    assert symbol not in daily
    daymap={}
    for b in bars:
     date=b['t'][:10]
     assert date not in daymap
     daymap[date]=b
    daily[symbol]=daymap;symbol_source[symbol]=item['name']
 universe=json.loads((OLD/'universe.json').read_text()); classifications={}
 for name,key in [('nasdaqlisted.txt','Symbol'),('otherlisted.txt','ACT Symbol')]:
  p=OLD/name;source.append({'source':'current_exchange_etf_directory','name':name,'sha256':sha(p),'bytes':p.stat().st_size})
  for row in csv.DictReader(p.read_text().splitlines(),delimiter='|'):
   symbol=row.get(key);flag=row.get('ETF')
   if symbol and flag in ('Y','N'):
    classifications.setdefault(symbol,set()).add(flag)
 def cls(symbol):
  flags=classifications.get(symbol,set())
  return 'explicit_current_ETF_Y' if 'Y' in flags else ('explicit_current_ETF_N' if 'N' in flags else 'current_ETF_classification_unknown')
 source.append({'source':'current_universe','name':'universe.json','sha256':sha(OLD/'universe.json'),'bytes':(OLD/'universe.json').stat().st_size})
 calendar=json.loads((OLD/'raw/calendar.json').read_text())
 calfile=ROOT/'private-inputs/calendar-2026-10-06.json'
 if not calfile.exists():
  params={'start':'2026-10-06','end':'2026-10-06'}
  req=urllib.request.Request('https://paper-api.alpaca.markets/v2/calendar?'+urllib.parse.urlencode(params),headers={'APCA-API-KEY-ID':os.environ['ALPACA_500_API_KEY'],'APCA-API-SECRET-KEY':os.environ['ALPACA_500_SECRET_KEY']})
  at=datetime.now(timezone.utc).isoformat()
  with urllib.request.build_opener(NoRedirect).open(req,timeout=40) as response:
   raw=response.read();assert response.status==200
  calfile.write_bytes(raw)
  save('calendar-extension-source.json',{'requested_at':at,'received_at':datetime.now(timezone.utc).isoformat(),'method':'GET','host':'paper-api.alpaca.markets','path':'/v2/calendar','params':params,'status':200,'response_sha256':sha(calfile),'response_bytes':len(raw),'private_response_name':calfile.name})
 extra=json.loads(calfile.read_text());assert len(extra)==1 and extra[0]['date']=='2026-10-06'
 assert calendar[-1]['date']=='2026-10-05'
 calendar+=extra
 source.append({'source':'calendar_extension','name':calfile.name,'sha256':sha(calfile),'bytes':calfile.stat().st_size})
 all_dates=[c['date'] for c in calendar]; assertsessions=[c for c in calendar if c['date'].startswith('2026-')];assert len(assertsessions)==191
 scopes=[];registry=[];unique=set();counts=Counter();population=[]
 for u in universe:
  symbol=u['symbol'];population.append({'symbol':symbol,'current_etf_classification':cls(symbol),'source_daily_file':symbol_source.get(symbol),'has_options_in_current_directory':'has_options' in u.get('attributes',[])})
 for c in assertsessions:
  day=c['date'];index=all_dates.index(day);priors=all_dates[index-20:index];assert len(priors)==20
  eligible=[];exclusions={};metrics={}
  for u in universe:
   symbol=u['symbol'];reason=None
   if cls(symbol)=='explicit_current_ETF_Y':reason='explicit_current_ETF_Y'
   bars=daily.get(symbol,{})
   if reason is None and any(p not in bars for p in priors):reason='missing_one_or_more_prior20_session_daily_bars'
   if reason is None:
    observed=[bars[p] for p in priors]
    try:
     valid=all(Decimal(str(b['c'])).is_finite() and Decimal(str(b['v'])).is_finite() and Decimal(str(b['c']))>0 and Decimal(str(b['v']))>=0 for b in observed)
    except Exception:valid=False
    if not valid:reason='invalid_prior20_close_or_volume'
   if reason is None:
    prior_close=Decimal(str(bars[priors[-1]]['c']));median=statistics.median([Decimal(str(b['c']))*Decimal(str(b['v'])) for b in observed])
    if prior_close<5:reason='prior_close_below_5'
    elif median<20_000_000:reason='prior20_median_dollar_volume_below_20m'
   if reason:
    exclusions.setdefault(reason,[]).append(symbol);continue
   eligible.append(symbol);metrics[symbol]={'prior_close':str(prior_close),'median_prior20_daily_close_times_volume':str(median)}
  ranked=sorted(eligible,key=lambda s:(-Decimal(metrics[s]['median_prior20_daily_close_times_volume']),s))
  top=ranked[:64];rest=[s for s in eligible if s not in set(top)]
  sampled=sorted(rest,key=lambda s:(hashlib.sha256(f's500_flush_v1|{day}|{s}'.encode()).hexdigest(),s))[:32]
  overlay=[s for s in STORE if s in metrics];selected=sorted(set(top+sampled+overlay+['SPY','QQQ']))
  for symbol in selected:
   roles=[]
   if symbol in top:roles.append('prior20_dollar_volume_top64')
   if symbol in sampled:roles.append('remaining_eligible_sha256_first32')
   if symbol in overlay:roles.append('eligible_storage_overlay')
   if symbol in ['SPY','QQQ']:roles.append('forced_ETF_control')
   registry.append({'case_id':day+'__'+symbol,'date':day,'symbol':symbol,'session_open_et':c['open'],'session_close_et':c['close'],'roles':roles,'stock_eligible':symbol in metrics,'current_etf_classification':cls(symbol),'prior_session_date':priors[-1],'prior20_dates':priors,'selection_metrics':metrics.get(symbol),'daily_source_file':symbol_source.get(symbol),'known_current_directory_survivorship_bias':True})
  scopes.append({'date':day,'open_et':c['open'],'close_et':c['close'],'prior20_dates':priors,'eligible_count':len(eligible),'eligible_symbols':sorted(eligible),'excluded_by_reason':{k:sorted(v) for k,v in sorted(exclusions.items())},'selected_count':len(selected),'selected_symbols':selected,'selected_top64':top,'selected_hash32':sampled,'selected_storage':overlay,'forced_controls':['SPY','QQQ']})
  unique.update(selected);counts['selected_cases']+=len(selected);counts['eligible_cases']+=len(eligible)
 save('calendar-2026-ytd.json',assertsessions)
 save('input-source-hashes.json',{'created_at':datetime.now(timezone.utc).isoformat(),'sources':source,'selection_inputs_include_no_same_day_intraday_prices':True})
 save('universe-classification.json',{'asof_directory':'2026-10-06','classification_is_point_in_time':False,'population':population})
 save('daily-scope.json',{'calendar_sessions':191,'universe_size':len(universe),'selection_rule':'prior20 complete calendar sessions; previous close >=5; median(close*volume)>=20000000; current explicit ETF Y excluded except forced controls; top64 + SHA first32 remaining + eligible storage + SPY/QQQ','daily':scopes})
 save('selected-case-registry.json',{'created_at':datetime.now(timezone.utc).isoformat(),'cases':registry})
 summary={'created_at':datetime.now(timezone.utc).isoformat(),'daily_input_files_verified':104,'source_daily_bar_count':sum(len(x) for x in daily.values()),'calendar_sessions':191,'selected_case_count':len(registry),'selected_unique_symbols':len(unique),'daily_selected_min':min(s['selected_count'] for s in scopes),'daily_selected_max':max(s['selected_count'] for s in scopes),'eligible_symbol_sessions':counts['eligible_cases'],'daily_eligible_min':min(s['eligible_count'] for s in scopes),'daily_eligible_max':max(s['eligible_count'] for s in scopes),'directory_classification_counts':dict(Counter(cls(u['symbol']) for u in universe)),'expected_maximum_regular_session_bars':sum(len(s['selected_symbols'])*((int(s['close_et'][:2])*60+int(s['close_et'][3:]))-(int(s['open_et'][:2])*60+int(s['open_et'][3:]))) for s in scopes),'scope_sha256':sha(ROOT/'daily-scope.json'),'registry_sha256':sha(ROOT/'selected-case-registry.json'),'source_hashes_sha256':sha(ROOT/'input-source-hashes.json'),'minute_fetch_started':False}
 save('input-preparation-summary.json',summary);print(json.dumps(summary),flush=True)

if __name__=='__main__':main()
