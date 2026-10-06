"""Independent offline reconciliation of EP quotes and chronological review join."""
from collections import Counter
from fractions import Fraction
import hashlib,json,math
from pathlib import Path
from statistics import mean,median
from datetime import datetime,timezone
import re
import unittest
import ep_quote
ROOT=Path(__file__).resolve().parent
PRIOR=ROOT.parent/'s500-quote-20261006'
def read(path):return json.loads(Path(path).read_text())
def fh(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def oh(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def ns(t):
 m=re.fullmatch(r'(.*T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})',t)
 assert m
 base,frac,zone=m.groups();dt=datetime.fromisoformat(base+zone.replace('Z','+00:00'))-datetime(1970,1,1,tzinfo=timezone.utc)
 return (dt.days*86400+dt.seconds)*10**9+int((frac or '').ljust(9,'0'))
def eq(a,b):
 if a is None or b is None:assert a is b,(a,b)
 elif isinstance(a,(int,float)) and isinstance(b,(int,float)):assert math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-10),(a,b)
 else:assert a==b,(a,b)
def pos(x):return type(x) in (float,int) and math.isfinite(x) and x>0

class IndependentSyntheticTests(unittest.TestCase):
 def test_risk_depends_only_on_OR30low_and_preserves_negative_risk(self):
  quote={'bp':10,'ap':10.5,'bs':100,'as':100}
  p={'known_or30_low_exact':'8.5','original_minute_entry_open_exact':'10.4','minute_reference_gap_through_stop':False,'atr14':999999}
  r=ep_quote.metrics(quote,p);self.assertEqual(r['spread_to_ask_minus_or30_low'],.25)
  p['known_or30_low_exact']='11';r=ep_quote.metrics(quote,p)
  self.assertEqual(r['ask_minus_or30_low'],-.5);self.assertIsNone(r['spread_to_ask_minus_or30_low']);self.assertEqual(len(r['budget_only']),2)
 def test_latest_withdrawal_tie_and_future_one_ns(self):
  _,a=ep_quote.load_prior();maps={'C':{'R':'Regular Two Sided Open'}}
  q={'t':'2026-01-05T15:00:00Z','bp':10,'ap':10.5,'bs':10,'as':10,'c':['R'],'z':'C'}
  old={**q,'t':'2026-01-05T14:59:59Z'}
  self.assertFalse(a.evaluate_latest([old,{**q,'bs':0}],q['t'],1,maps)['accepted'])
  self.assertEqual(a.evaluate_latest([q,{**q,'as':20}],q['t'],1,maps)['reason'],'ambiguous_latest_timestamp')
  r=a.evaluate_latest([q,{**q,'t':'2026-01-05T15:00:00.000000001Z','bs':0}],q['t'],1,maps)
  self.assertTrue(r['accepted']);self.assertEqual(r['future_rows_ignored'],1)
 def test_exact_budget_source_decimal_boundary(self):
  q={'bp':249.99,'ap':250.0000001,'bs':1,'as':1};p={'known_or30_low_exact':'240','original_minute_entry_open_exact':'250','minute_reference_gap_through_stop':False}
  r=ep_quote.metrics(q,p);self.assertEqual([b['whole_shares_at_displayed_ask'] for b in r['budget_only']],[0,1])
  self.assertFalse(r['execution_capacity_verified'])


def audit():
 design=read(ROOT/'ep-quote-design.json');result=read(ROOT/'ep-quote-results.json');summary=read(ROOT/'ep-quote-summary.json')
 cache=ROOT/'raw-ep-quote-cache';manifest=read(cache/'input-manifest.json')
 market=read(ROOT/'market-gates.json');markets={r['candidate_id']:r for r in market['rows']};minutes=read(ROOT/'minute-market.json')
 maps={r['tape']:r['response'] for r in read(PRIOR/'quote-condition-metadata.json')}
 assert manifest==result['input_manifest'];assert manifest['registered_design_sha256']==fh(ROOT/'ep-quote-design.json')
 assert manifest['points_sha256']==design['points_sha256']==oh(design['points'])
 assert summary['point_results_sha256']==fh(ROOT/'ep-quote-results.json')
 for group,bindings in result['source_sha256'].items():
  base=PRIOR if group=='prior_quote_v1' else ROOT
  for name,h in bindings.items():assert fh(base/name)==h,(group,name)
 assert result['source_sha256']['current_stage']==design['source_sha256']['current_stage']
 assert result['source_sha256']['prior_quote_v1']==design['source_sha256']['prior_quote_v1']
 expectedpoints=sorted([m for m in market['rows'] if m['market_gate_pass'] is True],key=lambda r:(r['date'],r['symbol']))
 assert len(expectedpoints)==len(design['points'])==design['point_count']==result['point_count']==73
 assert [p['id'] for p in design['points']]==[m['candidate_id'] for m in expectedpoints]
 records={r['point_id']:r for r in manifest['records']};assert len(records)==len(manifest['records'])==73 and not manifest['failures']
 rows={(r['point_id'],r['max_age_seconds']):r for r in result['rows']};assert len(rows)==len(result['rows'])==146
 countpages=countbytes=countquotes=goodcount=budgets=0
 for p,m in zip(design['points'],expectedpoints):
  assert p['market_gate_row_sha256']==oh(m)
  assert p['date']==m['date'] and p['symbol']==m['symbol']
  decision=ns(p['decision_time']);assert decision==ns(m['signal']['planned_entry_time_utc'])==ns(m['entry_reference']['time_utc'])
  assert decision-ns(m['signal']['completed_at_utc'])==60*10**9
  assert ns(p['request_end'])==decision and decision-ns(p['request_start'])==60*10**9
  series=minutes[p['date']][p['symbol']]
  low=min(Fraction(str(series[str(i)]['l'])) for i in range(30));high=max(Fraction(str(series[str(i)]['h'])) for i in range(30))
  assert Fraction(p['known_or30_low_exact'])==low==Fraction(m['opening30']['exact_values']['or30_low'])
  assert Fraction(p['known_or30_high_exact'])==high
  assert Fraction(p['original_minute_entry_open_exact'])==Fraction(str(series[str(m['entry_reference']['minute_offset'])]['o']))
  record=records[p['id']];folder=cache/'raw-quotes'/p['id'];assert read(folder/'complete.json')==record
  route='/v2/stocks/'+p['symbol']+'/quotes';params={'start':p['request_start'],'end':p['request_end'],'limit':10000,'feed':'sip','sort':'asc'}
  assert record['route']==route and record['parameters']==params and record['pages_complete']
  basehash=oh({'route':route,'parameters':params});assert record['request_sha256']==basehash
  request=dict(params);quotes=[];tokens=set()
  for i,page in enumerate(record['pages']):
   raw=(folder/page['name']).read_bytes();assert len(raw)==page['bytes'] and hashlib.sha256(raw).hexdigest()==page['sha256']
   saved=json.loads(raw);assert saved['base_request_sha256']==basehash and saved['page_number']==i
   assert saved['page_request_sha256']==oh({'route':route,'parameters':request})
   assert ns(saved['retrieved_at'])>=ns(design['registered_at'])
   payload=saved['response'];assert payload.get('symbol',p['symbol'])==p['symbol'];part=payload.get('quotes') or []
   assert len(part)==page['quotes'];quotes.extend(part)
   token=payload.get('next_page_token')
   if i==len(record['pages'])-1:assert not token
   else:assert token and token not in tokens
   if token:tokens.add(token);request['page_token']=token
   countpages+=1;countbytes+=len(raw)
  countquotes+=len(quotes)
  timed=[(ns(q['t']),q) for q in quotes];eligible=[(t,q) for t,q in timed if ns(p['request_start'])<=t<=decision]
  latest_at=max((t for t,q in eligible),default=None);latest=[q for t,q in eligible if t==latest_at]
  states={json.dumps({k:v for k,v in q.items() if k!='t'},sort_keys=True,separators=(',',':')) for q in latest}
  for age in (1,5):
   r=rows[(p['id'],age)];reasons=[]
   assert r['quote_rows']==len(quotes);assert r['future_rows_ignored']==sum(t>decision for t,q in timed)
   assert r['older_than_requested_lookback_rows']==sum(t<ns(p['request_start']) for t,q in timed)
   assert r['invalid_timestamp_rows']==0
   if not latest:expected='no_quote_in_requested_lookback';accepted=False
   elif len(states)!=1:expected='ambiguous_latest_timestamp';accepted=False
   else:
    q=latest[0];assert r['latest_quote']==q and r['api_reported_age_ns']==decision-latest_at
    if not(pos(q.get('bp')) and pos(q.get('ap'))):reasons.append('nonpositive_or_invalid_bid_ask')
    elif q['ap']<q['bp']:reasons.append('crossed_quote')
    if not(pos(q.get('bs')) and pos(q.get('as'))):reasons.append('nonpositive_or_invalid_displayed_size')
    if q.get('z') not in ('A','B','C') or maps.get(q.get('z'),{}).get('R') not in ('Regular Market Maker Open','Regular Two Sided Open'):reasons.append('unverified_tape_regular_condition_mapping')
    if q.get('c')!=['R']:reasons.append('unsupported_or_missing_quote_condition')
    if decision-latest_at>age*10**9:reasons.append('stale_api_reported_timestamp')
    expected=reasons[0] if reasons else 'accepted_for_displayed_quote_diagnostic_only';accepted=not reasons
    assert r['rejection_reasons']==reasons
   assert r['reason']==expected and r['accepted']==accepted
   if not accepted:assert r['metrics'] is None;continue
   goodcount+=1;v=r['metrics'];a,b=Fraction(str(q['ap'])),Fraction(str(q['bp']));spread=a-b;risk=a-low
   for k,value in {'bid':b,'ask':a,'known_or30_low':low,'spread_dollars':spread,'spread_bps_of_mid':spread/((a+b)/2)*10000,'ask_minus_or30_low':risk}.items():eq(v[k],float(value))
   eq(v['spread_to_ask_minus_or30_low'],float(spread/risk) if risk>0 else None)
   assert v['spread_at_least_positive_risk_distance']==(spread>=risk if risk>0 else None)
   assert v['ask_at_or_below_known_stop']==(a<=low) and v['bid_at_or_below_known_stop']==(b<=low)
   assert v['ask_above_known_stop']==(risk>0) and v['stop_trigger_or_fill_claim'] is False
   assert 'prior_ATR14' not in v and 'spread_to_stop_distance' not in v
   for k,value in {'bid':b,'ask':a,'known_or30_low':low,'spread':spread,'ask_minus_or30_low':risk}.items():assert Fraction(v['exact_prices'][k])==value
   assert v['execution_capacity_verified'] is False
   for budget,br in zip((250,500),v['budget_only']):
    qty=budget//a;assert br['budget']==budget and br['whole_shares_at_displayed_ask']==qty
    eq(br['hypothetical_ask_notional'],float(qty*a));eq(br['cash_remainder'],float(budget-qty*a));eq(br['same_quote_two_sided_crossing_cost_no_fees'],float(qty*spread))
    assert br['unaffordable_at_displayed_ask']==(qty==0) and br['capacity_or_fill_claim'] is False;budgets+=1
   ref=Fraction(p['original_minute_entry_open_exact']);eq(v['ask_minus_minute_reference'],float(a-ref));eq(v['ask_minus_minute_reference_bps'],float((a/ref-1)*10000))
  assert not rows[(p['id'],1)]['accepted'] or rows[(p['id'],5)]['accepted']
 assert countquotes==manifest['raw_quote_count']==summary['raw_quote_count']
 assert countbytes==manifest['raw_response_bytes']==summary['raw_response_bytes']
 for g in summary['groups']:
  selected=[r for r in result['rows'] if r['max_age_seconds']==g['max_age_seconds']];good=[r for r in selected if r['accepted']];risk=[r for r in good if r['metrics']['ask_above_known_stop']]
  assert g['total_points']==len(selected) and g['accepted_points']==len(good)
  assert g['reason_counts']==dict(Counter(r['reason'] for r in selected))
  assert g['all_rejection_reason_counts']==dict(Counter(t for r in selected for t in r.get('rejection_reasons',[])))
  for target,field,pool in [('spread_bps_of_mid','spread_bps_of_mid',good),('spread_to_positive_ask_minus_or30_low','spread_to_ask_minus_or30_low',risk)]:
   values=[r['metrics'][field] for r in pool];d={'count':len(values),'median':median(values) if values else None,'min':min(values) if values else None,'max':max(values) if values else None,'mean':mean(values) if values else None}
   for k,value in d.items():eq(g[target][k],value)
  for k in ['ask_at_or_below_known_stop','bid_at_or_below_known_stop']:assert g[k]==sum(r['metrics'][k] for r in good)
  assert g['spread_at_least_positive_risk_distance']==sum(r['metrics']['spread_at_least_positive_risk_distance'] for r in risk)
  for i,budget in enumerate((250,500)):assert g['budget_only_unaffordable_'+str(budget)]==sum(r['metrics']['budget_only'][i]['unaffordable_at_displayed_ask'] for r in good)
 joined=read(ROOT/'joined-candidates.json');queue=read(ROOT/'primary-review-queue.json');cohort=read(ROOT/'candidates.json')['candidates'];news={n['candidate_id']:n for n in read(ROOT/'news-enriched.json')['candidates']};previous={r['date']+'__'+r['symbol']:r for r in read(PRIOR/'ep-followup.json')['candidate_reviews']}
 assert len(joined['candidates'])==joined['candidate_count']==1079
 assert queue['input_sha256']==joined['input_sha256']
 for name,h in joined['input_sha256'].items():assert fh(PRIOR/'ep-followup.json' if name=='prior_ep_followup' else ROOT/name)==h
 assert [(c['date'],c['symbol']) for c in cohort]==sorted((c['date'],c['symbol']) for c in cohort)
 expectedeligible=[];both=0;oldcount=oldpass=oldpassmarket=nomatch=0
 for c,j in zip(cohort,joined['candidates']):
  cid=c['candidate_id'];m,n=markets[cid],news[cid];old=previous.get(cid)
  assert j['candidate_id']==cid and j['signal']==m['signal'] and j['entry_reference']==m['entry_reference']
  ready=n['status']=='metadata_ready_for_review' and n['fetch_complete'] is True
  combined=m['market_gate_pass'] is True and ready
  assert j['metadata_ready_for_primary_review']==ready and j['market_and_metadata_ready']==combined
  assert j['already_primary_reviewed']==(old is not None)
  assert j['prior_primary_gate_pass']==bool(old and old['pass_registered_news_gate'])
  assert j['clean_article_count']==n['metadata_ready_unique_payloads']
  assert j['new_primary_catalyst_confirmed'] is False and j['trade_or_return_claim'] is False
  both+=combined;oldcount+=old is not None;oldpass+=j['prior_primary_gate_pass'];oldpassmarket+=j['prior_primary_gate_pass'] and m['market_gate_pass'] is True;nomatch+=m['market_gate_pass'] is True and n['status']=='source_no_articles'
  if combined and old is None:expectedeligible.append(cid)
 assert both==70 and oldcount==10 and oldpass==1 and oldpassmarket==0 and nomatch==3
 assert queue['all_eligible_ids']==expectedeligible and len(expectedeligible)==69
 assert [r['candidate_id'] for r in queue['selected']]==expectedeligible[:5] and queue['deferred_ids']==expectedeligible[5:]
 jmap={r['candidate_id']:r for r in joined['candidates']}
 assert queue['selected']==[jmap[cid] for cid in expectedeligible[:5]]
 assert joined['counts']=={'market_pass':73,'market_and_metadata_ready':70,'market_pass_source_no_articles':3,'previously_primary_reviewed':10,'prior_primary_pass':1,'prior_primary_and_market_pass':0,'new_primary_review_ready':69,'new_primary_review_selected':5}
 return {'quote_points':73,'age_records':146,'raw_pages':countpages,'raw_quotes':countquotes,'raw_bytes':countbytes,'accepted_metric_records':goodcount,'budget_rows_checked':budgets,'primary1s_accepted':51,'sensitivity5s_accepted':67,'all_raw_page_hash_size_chains_verified':True,'frozen_all73market_pass_points_verified':True,'OR30low_independently_recomputed_from_first30bars':True,'joined_candidates_preserved':1079,'market_and_metadata_ready':both,'previous10excluded_from_new_queue':True,'new_review_eligible':69,'selected_ids':expectedeligible[:5],'deferred_count':64,'full_quote_and_join_summary_reconciliation_passed':True,'no_catalyst_execution_or_return_validation':True}

if __name__=='__main__':
 suite=unittest.defaultTestLoader.loadTestsFromTestCase(IndependentSyntheticTests);run=unittest.TextTestRunner(verbosity=2).run(suite)
 assert run.wasSuccessful()
 actual=audit();sources=['ep_quote.py','test_ep_quote.py','ep-quote-design.json','ep-quote-results.json','ep-quote-summary.json','join_candidates.py','joined-candidates.json','primary-review-queue.json','audit_ep_quote_join.py']
 payload={'audited_at':datetime.now(timezone.utc).isoformat(),'independent_synthetic_tests':{'count':run.testsRun,'passed':run.testsRun},'actual':actual,'source_sha256':{n:fh(ROOT/n) for n in sources},'findings':[],'limitations':['Event/API quote time is not receive latency; raw sizes are not verified execution capacity.','Ask-OR30low is a different and wider risk denominator than prior ORB0.1ATR; lower spread ratios do not establish a better strategy.','250/500 budgets only restrict notional, not maximum loss.','70 joins are metadata plus market gates, not verified catalysts or executable trades.','First5 queue follows chronological candidate-days; repeat issuer dates remain distinct cases.']}
 (ROOT/'ep-quote-join-audit.json').write_text(json.dumps(payload,indent=2)+'\n');print(json.dumps(actual,indent=2))
