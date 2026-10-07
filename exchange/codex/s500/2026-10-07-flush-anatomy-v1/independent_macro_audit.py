"""Offline independent calendar reconstruction; no network or market outcomes."""
import hashlib,json,re
from pathlib import Path
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from collections import Counter,defaultdict
from html.parser import HTMLParser
ROOT=Path(__file__).resolve().parent
class Text(HTMLParser):
 def __init__(self):super().__init__();self.parts=[]
 def handle_data(self,data):self.parts.append(data)
def squash(s):return ' '.join(s.split())
def main():
 review=json.loads((ROOT/'macro-calendar-review.json').read_text());manifest=json.loads((ROOT/'macro-source-manifest.json').read_text())
 assert hashlib.sha256((ROOT/'macro-source-manifest.json').read_bytes()).hexdigest()==review['source_manifest_sha256']
 sources={};normalized={}
 for r in manifest['requests']:
  body=(ROOT/r['raw_relative_path']).read_bytes();assert len(body)==r['body_bytes'] and hashlib.sha256(body).hexdigest()==r['body_sha256']
  assert r['host'] in ['www.bls.gov','www.bea.gov','apps.bea.gov','www.federalreserve.gov'] and r['status']==200
  assert r['requested_url'].startswith('https://');sources[r['document_id']]=r
  parser=Text();parser.feed(body.decode('utf-8'));normalized[r['document_id']]=squash(''.join(parser.parts))
 assert len(manifest['requests'])==review['request_control']['actual_get_requests_including_redirects']<=20
 for host in {r['host'] for r in manifest['requests']}:
  times=sorted(datetime.fromisoformat(r['started_at_utc']) for r in manifest['requests'] if r['host']==host)
  assert all((b-a).total_seconds()>=1 for a,b in zip(times,times[1:]))
 inventory=defaultdict(list)
 # Tokenize full text from raw HTML, independently of production .txt artifacts.
 bls=normalized['bls-annual']
 rx=r'((?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), [A-Z][a-z]+ \d{1,2}, 2026) (\d{2}:\d{2} [AP]M) (Consumer Price Index|Producer Price Index|Employment Situation) for\b'
 for m in re.finditer(rx,bls):
  key={'Consumer Price Index':'BLS_CPI','Producer Price Index':'BLS_PPI','Employment Situation':'BLS_EmploymentSituation'}[m[3]]
  inventory[key].append(datetime.strptime(m[1],'%A, %B %d, %Y').date().isoformat())
 bea=normalized['bea-full-current'];assert 'Year 2026' in bea
 date_rx=r'((?:January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}) (\d{1,2}:\d{2} [AP]M) News (.*?) (?=(?:View |January |February |March |April |May |June |July |August |September |October |November |December |$))'
 for m in re.finditer(date_rx,bea):
  title=m[3];category=None
  if title.startswith('Personal Income and Outlays'):category='BEA_PersonalIncomeAndOutlays'
  elif title.startswith('GDP (') or (title.startswith('Gross Domestic Product,') and not title.startswith('Gross Domestic Product by')):category='BEA_GDP'
  if category:inventory[category].append(datetime.strptime(m[1]+' 2026','%B %d %Y').date().isoformat())
 fed=normalized['fed-calendar'].split('2026 FOMC Meetings',1)[1].split('2025 FOMC Meetings',1)[0]
 meetings=list(re.finditer(r'(January|February|March|April|May|June|July|August|September|October|November|December) (\d{1,2})-(\d{1,2})\*?',fed))
 for index,m in enumerate(meetings):
  month=datetime.strptime(m[1],'%B').month
  inventory['Fed_FOMCMeeting'] += [f'2026-{month:02d}-{day:02d}' for day in range(int(m[2]),int(m[3])+1)]
  part=fed[m.end():meetings[index+1].start() if index+1<len(meetings) else len(fed)]
  if 'Statement:' in part:inventory['Fed_FOMCStatement'].append(f'2026-{month:02d}-{int(m[3]):02d}')
  release=re.search(r'\(Released ([A-Z][a-z]+ \d{1,2}, 2026)\)',part)
  if release:inventory['Fed_FOMCMinutes'].append(datetime.strptime(release[1],'%B %d, %Y').date().isoformat())
 for category,row in review['calendar_inventory_for_audit'].items():
  assert sorted(inventory[category])==row['dates'],(category,inventory[category],row['dates'])
  assert len(inventory[category])==row['parsed_rows']
 dates=review['scope']['dates'];categories=review['scope']['categories'];seen=set();counts=Counter();events=[];quotes=0
 def evidence(e):
  nonlocal quotes
  s=sources[e['source_id']]
  assert e['url']==s['requested_url'] and e['retrieved_at_utc']==s['retrieved_at_utc'] and e['body_sha256']==s['body_sha256']
  if 'short_quote_whitespace_normalized' in e:
   assert squash(e['short_quote_whitespace_normalized']) in normalized[e['source_id']],e
   quotes+=1
 for row in review['rows']:
  pair=row['date'],row['category'];assert pair not in seen;seen.add(pair)
  expected=row['date'] in inventory[row['category']]
  assert row['status']==('listed_in_captured_official_calendar' if expected else 'not_listed_in_captured_official_calendar')
  assert row['no_event_claim'] is False and row['unknown_if_outside_limited_category_or_absent_from_current_calendar'] is True
  assert row['date_role']==review['scope']['date_roles'][row['date']];evidence(row['calendar_evidence'])
  assert bool(row['scheduled_events'])==expected
  counts['listed' if expected else 'not_listed']+=1
  for event in row['scheduled_events']:
   assert event['date']==row['date'];evidence(event['schedule_evidence'])
   if 'machine_readable_time_evidence' in event:evidence(event['machine_readable_time_evidence'])
   time=event['scheduled_time_et'];stamp=datetime.fromisoformat(event['date']+'T'+time).replace(tzinfo=ZoneInfo('America/New_York'))
   if 'scheduled_time_utc' in event:assert stamp.astimezone(timezone.utc).isoformat()==event['scheduled_time_utc']
   expected_bucket='before_09_30' if time<'09:30:00' else '09_30_to_before_10_00' if time<'10:00:00' else 'exactly_10_00' if time=='10:00:00' else 'after_10_00'
   assert event['scheduled_time_bucket_et']==expected_bucket
   assert event['actual_first_publication_timestamp'] is None and event['historical_received_at'] is None and event['information_available_to_strategy_then']=='unknown'
   assert event['causes_stock_return']=='not_evaluated'
   annotation=event['release_document_annotation'];evidence(annotation['evidence']);assert annotation['date']==event['date'] and annotation['time_et']==time
   assert annotation['type']=='embargo_release_time_label_not_observed_publication_or_receipt'
   if row['category']=='BEA_GDP':assert event['gdp_estimate_vintage'] in ['Advance','Second','Third'] and event['gdp_estimate_vintage'] in event['title']
   events.append(event)
 assert seen=={(d,c) for d in dates for c in categories} and len(seen)==96
 for e in review['short_calendar_evidence']:evidence(e)
 summary=review['summary'];assert summary['listed_category_date_cells']==counts['listed']==5 and summary['not_listed_in_current_captured_calendar_cells']==counts['not_listed']==91
 assert summary['scheduled_events']==len(events)==5 and summary['positive_dates']==sorted({e['date'] for e in events})
 assert summary['actual_publication_or_strategy_receipt_verified']==0
 assert {k:v for k,v in summary['time_buckets_et'].items() if v}==dict(Counter(e['scheduled_time_bucket_et'] for e in events))
 result={'passed':True,'audited_at':datetime.now(timezone.utc).isoformat(),'offline_only':True,'network_requests_by_auditor':0,'source_bodies_verified':len(sources),'calendar_categories_independently_reconstructed':len(inventory),'date_category_cells':len(seen),'scheduled_events':len(events),'evidence_quotes_matched_to_raw_body':quotes,'actual_publication_receipt_unverified':True,'FOMC_minutes_extra_background_not_return_subgroup':True,'files':[{'name':n,'sha256':hashlib.sha256((ROOT/n).read_bytes()).hexdigest()} for n in ['macro-calendar-review.json','macro-source-manifest.json','independent_macro_audit.py']]}
 (ROOT/'independent-macro-audit.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
if __name__=='__main__':main()
