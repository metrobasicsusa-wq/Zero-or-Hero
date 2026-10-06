"""Independent offline EP causal/ledger tests and source/output audits."""
import copy,json,hashlib,unittest
from decimal import Decimal as D
from datetime import date,timedelta,datetime,timezone
from pathlib import Path
from ep_engine import simulate_case,simulate_portfolio,timestamp
ROOT=Path(__file__).resolve().parent
V={'exit_mode':'fixed10','fraction':1,'cost_bps':25}


def fixture():
 dates=[];d=date(2025,12,15)
 while len(dates)<77:
  if d.weekday()<5:dates.append(d.isoformat())
  d+=timedelta(days=1)
 cal=[{'date':day,'open':'09:30','close':'16:00','settlement_date':dates[i+1]} for i,day in enumerate(dates[:-1])]
 m={s['date']:{'A':{str(i):{'t':timestamp(s,i),'o':10,'h':10.2,'l':9.9,'c':10,'v':100} for i in range(390)}} for s in cal}
 daily={'A':{s['date']:{'c':10} for s in cal}}
 c={'date':dates[10],'symbol':'A','candidate_id':dates[10]+'__A','market_gate_status':'signal_and_entry_reference_ready','market_gate_pass':True,
    'opening30':{'exact_values':{'volume':'200','prior20_mean_adjusted_daily_volume':'100'},'volume_ratio':2},
    'signal':{'minute_offset':30,'close':10,'close_exact':'10','planned_entry_offset':32},
    'entry_reference':{'minute_offset':32,'stop_exact':'8','stop_or30_low':8}}
 return c,m,daily,cal


class IndependentPathsAudit(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.base=fixture()
 def setUp(self):self.c,self.m,self.d,self.cal=copy.deepcopy(self.base);self.day=self.c['date'];self.next=self.cal[11]['date']
 def case(self,v=None,actions=None,cutoff=None):return simulate_case(self.c,v or V,self.m,self.d,self.cal,actions or {},cutoff or self.cal[-1]['date'])
 def alt(self,symbol='B',day=None,offset=30,ratio='2'):
  c=copy.deepcopy(self.c);c.update(symbol=symbol,date=day or self.day,candidate_id=(day or self.day)+'__'+symbol)
  c['signal'].update(minute_offset=offset,planned_entry_offset=offset+2);c['entry_reference']['minute_offset']=offset+2
  c['opening30']['exact_values']['volume']=str(D(ratio)*100)
  for ss in self.m.values():ss[symbol]=copy.deepcopy(ss['A'])
  self.d[symbol]=copy.deepcopy(self.d['A']);return c
 def port(self,cs,ev=None,cutoff=None,coverage=None,v=None):
  if ev is None:ev={c['candidate_id']:{'pass_registered_news_gate':True} for c in cs}
  return simulate_portfolio(cs,v or V,self.m,self.d,self.cal,{},ev,coverage,start_date=self.day,cutoff=cutoff or self.cal[21]['date'])
 def assert_ledger(self,r):
  buys=sum((D(t['cash_amount']) for t in r['trades'] if t['side']=='BUY'),D(0));sales=sum((D(t['cash_amount']) for t in r['trades'] if t['side']=='SELL'),D(0))
  settled=sum((D(l['amount']) for l in r['ledger'] if l['type']=='settlement'),D(0))
  self.assertEqual(D(r['cash_settled']),D(500)-buys+settled)
  self.assertEqual(sum((D(a['amount']) for a in r['terminal_receivables']),D(0)),sales-settled)
  for row in r['daily']:
   self.assertEqual(D(row['last_observed_equity']),sum(D(row[k]) for k in ('cash_settled','cash_unsettled','marked_position')))
  self.assertEqual(r['funding_injections_after_initial'],'0');self.assertEqual(r['restart_count'],0)

 def test_price_fall_does_not_increase_signal_quantity(self):
  self.m[self.day]['A']['32'].update(o=9,l=8.9)
  r=self.case();self.assertEqual(r['trades'][0]['quantity'],49)
  self.assertEqual(D(r['trades'][0]['cash_amount']),D('442.1025'));self.assert_ledger(r)
 def test_half_fraction_budget_binding_even_when_cash_available(self):
  self.m[self.day]['A']['32'].update(o=10.4,h=10.5,c=10.4)
  r=self.case({**V,'fraction':.5});self.assertEqual(r['decisions'][0]['status'],'skipped_next_open_unaffordable')
  self.assertEqual(r['trades'],[]);self.assertEqual(D(r['ending_equity']),500)
 def test_first_signal_unaffordable_intent_does_not_replace(self):
  b=self.alt(offset=31);self.c['signal']['close_exact']='1000'
  r=self.port([b,self.c],cutoff=self.day);self.assertEqual(r['trades'],[])
  self.assertEqual(r['decisions'][0]['candidate_id'],self.c['candidate_id'])
 def test_first_selected_missing_entry_not_replaced(self):
  b=self.alt(offset=31);del self.m[self.day]['A']['32'];r=self.port([b,self.c])
  self.assertEqual(r['failure']['reason'],'missing_or_invalid_selected_entry');self.assertIsNotNone(r['pending_order']);self.assertEqual(r['trades'],[])
 def test_signal_sort_RV_then_symbol_independent_input_order(self):
  b=self.alt(ratio='3');r=self.port([self.c,b],cutoff=self.day);self.assertEqual(r['trades'][0]['symbol'],'B')
  b['opening30']['exact_values']['volume']='200';r=self.port([b,self.c],cutoff=self.day);self.assertEqual(r['trades'][0]['symbol'],'A')
 def test_earlier_intent_beats_later_higher_RV(self):
  b=self.alt(offset=31,ratio='100');r=self.port([b,self.c],cutoff=self.day);self.assertEqual(r['trades'][0]['symbol'],'A')
 def test_unknown_same_time_better_RV_blocks_but_lower_RV_does_not(self):
  b=self.alt(ratio='3');ev={self.c['candidate_id']:{'pass_registered_news_gate':True}}
  self.assertEqual(self.port([self.c,b],ev)['failure']['reason'],'unresolved_candidate_precedence')
  b['opening30']['exact_values']['volume']='100';r=self.port([b,self.c],ev,cutoff=self.day);self.assertEqual(r['trades'][0]['symbol'],'A')
 def test_outside_category_is_unknown_not_negative_news(self):
  r=self.port([self.c],{self.c['candidate_id']:{'pass_registered_news_gate':False,'event_category':'outside_category'}})
  self.assertEqual(r['failure']['reason'],'unresolved_candidate_precedence');self.assertIsNone(r['ending_equity'])
 def test_strict_pool_coverage_gap_blocks_before_intent(self):
  r=self.port([self.c],coverage={'cohort_complete':False,'days':{self.day:{'initial_screen_complete':False}}})
  self.assertEqual(r['failure']['reason'],'initial_screen_coverage_unknown');self.assertEqual(r['trades'],[])
 def test_entry_stop_costs_and_T1_next_session_quantity(self):
  self.m[self.day]['A']['32']['l']=7
  c2=self.alt(symbol='A',day=self.next)
  r=self.port([self.c,c2],cutoff=self.next)
  buys=[t for t in r['trades'] if t['side']=='BUY'];self.assertEqual([t['quantity'] for t in buys],[49,39])
  self.assertEqual(D([t for t in r['trades'] if t['side']=='SELL'][0]['reference_price']),8);self.assert_ledger(r)
 def test_delayed_settlement_not_spent_until_actual_calendar_day(self):
  self.m[self.day]['A']['32']['l']=7;self.cal[10]['settlement_date']=self.cal[12]['date']
  c2=self.alt(symbol='A',day=self.next);c3=self.alt(symbol='A',day=self.cal[12]['date'])
  r=self.port([self.c,c2,c3],cutoff=self.cal[12]['date'])
  buys=[t for t in r['trades'] if t['side']=='BUY'];self.assertEqual([t['quantity'] for t in buys],[49,39])
  self.assertEqual([d['status'] for d in r['decisions'] if d.get('candidate_id')==c2['candidate_id']],['skipped_unaffordable_intent']);self.assert_ledger(r)
 def test_exit_day_has_no_reentry_even_if_later_signal(self):
  s=self.cal[19];c2=self.alt(symbol='B',day=s['date'],offset=80)
  r=self.port([self.c,c2]);self.assertEqual(len([t for t in r['trades'] if t['side']=='BUY']),1);self.assert_ledger(r)
 def test_held_gap_precedes_later_apparent_stop(self):
  del self.m[self.day]['A']['40'];self.m[self.day]['A']['41']['l']=1
  r=self.case();self.assertEqual(len(r['trades']),1);self.assertEqual(r['position']['quantity'],49);self.assertIsNone(r['ending_equity']);self.assert_ledger(r)
 def test_future_gap_and_future_action_after_stop_do_not_rewrite_exit(self):
  self.m[self.day]['A']['32']['l']=7;baseline=self.case()
  self.m[self.next]['A']={};changed=self.case(actions={'A':[{'type':'cash_dividend','ex_date':self.next}]})
  self.assertEqual(baseline['trades'],changed['trades']);self.assertEqual(baseline['ending_equity'],changed['ending_equity'])
 def test_gap_below_stop_uses_lower_open_only(self):
  self.m[self.next]['A']['0']={'t':timestamp(self.cal[11]),'o':6}
  r=self.case();self.assertEqual(D(r['trades'][-1]['reference_price']),6);self.assertEqual(r['trades'][-1]['reasons'],['gap_stop']);self.assert_ledger(r)
 def test_fixed10_last_open_and_early_close_ignore_future_HLC(self):
  s=self.cal[19];s['close']='13:00';self.m[s['date']]['A']['209']={'t':timestamp(s,209),'o':11}
  r=self.case();self.assertEqual(len(r['daily']),10);self.assertIn('12:59',r['trades'][-1]['time']);self.assertEqual(D(r['trades'][-1]['reference_price']),11)
 def test_MA_decision_uses_current_completed_day_and_next_open_once(self):
  self.d['A'][self.day]['c']=9;self.m[self.next]['A']['0']={'t':timestamp(self.cal[11]),'o':7}
  r=self.case({**V,'exit_mode':'ma10_max63'});self.assertEqual(len(r['trades']),2)
  self.assertEqual(set(r['trades'][-1]['reasons']),{'ma10_next_open','gap_stop'});self.assert_ledger(r)
 def test_MA_future_daily_close_not_used_for_today(self):
  self.d['A'][self.next]['c']=.01;r=self.case({**V,'exit_mode':'ma10_max63'},cutoff=self.day)
  self.assertEqual(r['ledger'][-1]['decision'],'hold');self.assertEqual(r['status'],'censored_open_position');self.assertIsNone(r['profit'])
 def test_MA_missing_daily_failure_not_before_last_observed_mark(self):
  del self.d['A'][self.cal[2]['date']];r=self.case({**V,'exit_mode':'ma10_max63'})
  self.assertIsNotNone(r['position']);self.assertIsNone(r['profit'])
  self.assertGreaterEqual(datetime.fromisoformat(r['failure']['time']),datetime.fromisoformat(r['last_known_mark_time']))
 def test_max63_counts_entry_as_session1_and_exit_open_only(self):
  s=self.cal[72];self.m[s['date']]['A']['389']={'t':timestamp(s,389),'o':12}
  r=self.case({**V,'exit_mode':'ma10_max63'});self.assertEqual(len(r['daily']),63);self.assertEqual(r['trades'][-1]['date'],s['date']);self.assertEqual(r['trades'][-1]['reasons'],['max63'])
 def test_held_CA_stops_before_unknown_cash_or_share_adjustment(self):
  for kind in ('cash_dividend','forward_split','reverse_split','merger'):
   r=self.case(actions={'A':[{'type':kind,'ex_date':self.next}]})
   self.assertEqual(r['status'],'incomplete');self.assertEqual(r['position']['quantity'],49);self.assertEqual(len(r['trades']),1);self.assertIsNone(r['ending_equity']);self.assert_ledger(r)
 def test_cutoff_censor_retains_open_position_without_sale(self):
  r=self.case(cutoff=self.next);self.assertEqual(r['status'],'censored_open_position');self.assertEqual(len(r['trades']),1);self.assertIsNone(r['profit']);self.assert_ledger(r)
 def test_variant_costs_exact_and_monotonic_given_identical_constant_prices(self):
  values=[]
  for cost in (25,50,100):
   r=self.case({**V,'cost_bps':cost});self.assert_ledger(r);values.append(D(r['ending_equity']))
  self.assertEqual(values,sorted(values,reverse=True))


def audit_sources():
 from zoneinfo import ZoneInfo
 import re
 ny=ZoneInfo('America/New_York');prior=ROOT.parent/'s500-news-20261006';broad=ROOT.parent/'s500-broad-20261006'
 read=lambda p:json.loads(Path(p).read_text())
 sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
 objsha=lambda o:hashlib.sha256(json.dumps(o,sort_keys=True,separators=(',',':')).encode()).hexdigest()
 design=read(ROOT/'study-design.json');holding=read(ROOT/'holding-input-design.json')
 assert sha(ROOT/'holding-input-design.json')==design['input_sha256']['holding-input-design.json']
 cal={x['date']:x for x in holding['full_calendar']};dates=sorted(cal)
 expectedcases={r['candidate_id'] for r in read(prior/'market-gates.json')['rows'] if r['market_gate_pass'] is True}
 assert len(holding['cases'])==73 and {r['candidate_id'] for r in holding['cases']}==expectedcases
 required=set()
 for c in holding['cases']:
  i=dates.index(c['entry_date']);wanted=[d for d in dates[i:i+63] if d<=holding['cutoff']]
  assert c['session_dates']==wanted and c['entry_session_counts_as']==1
  assert c['cutoff_censored']==(len(wanted)<63)
  required.update((d,c['symbol']) for d in wanted)
 requested={(t['date'],symbol) for t in holding['tasks'] for symbol in t['symbols']}
 assert required==requested
 summaries=[]
 for stage,key in [('repair','unknown_gate_refetch_tasks'),('holding','tasks')]:
  manifest=read(ROOT/(stage+'-input-manifest.json'));market=read(ROOT/(stage+'-market.json'))
  for name,h in manifest['input_sha256'].items():assert sha(prior/'download_inputs.py' if name=='parent_download_inputs.py' else ROOT/name)==h
  tasks={stage+'__'+t['task_id']:t for t in holding[key]};requests={t['task_id']:t for t in manifest['requests']};records={r['task_id']:r for r in manifest['records']}
  assert set(tasks)==set(requests)==set(records) and len(records)==len(manifest['records'])
  assert not manifest['failures'] and manifest['all_pages_complete']
  pages=bars=totalbytes=0;seen=set()
  for tid,t in tasks.items():
   request=requests[tid];record=records[tid];folder=ROOT/'raw-bars'/tid
   assert {k:v for k,v in record.items() if k!='date'}==read(folder/'complete.json')
   assert record['date']==t['date'] and record['stage']=='bars' and record['pages_complete']
   start=datetime.fromisoformat(t['date']+'T'+t['start']).replace(tzinfo=ny).astimezone(timezone.utc)
   end=datetime.fromisoformat(t['date']+'T'+t['end']).replace(tzinfo=ny).astimezone(timezone.utc)
   assert datetime.fromisoformat(request['start'])==start and datetime.fromisoformat(request['end'])==end
   assert request['symbols']==t['symbols']
   route='/v2/stocks/bars';params={'symbols':','.join(t['symbols']),'start':request['start'],'end':request['end'],'sort':'asc','limit':10000,'timeframe':'1Min','adjustment':'raw','feed':'sip'}
   assert record['route']==route and record['parameters']==params
   base=objsha({'route':route,'parameters':params});assert base==record['request_sha256']
   req=dict(params);tokens=set();opening=datetime.fromisoformat(t['date']+'T09:30:00').replace(tzinfo=ny)
   for i,part in enumerate(record['pages']):
    raw=(folder/part['name']).read_bytes();assert len(raw)==part['bytes'] and hashlib.sha256(raw).hexdigest()==part['sha256']
    saved=json.loads(raw);assert saved['base_request_sha256']==base and saved['page_number']==i
    assert saved['page_request_sha256']==objsha({'route':route,'parameters':req})
    assert datetime.fromisoformat(saved['retrieved_at'])>=datetime.fromisoformat(design['registered_at'])
    payload=saved['response'];assert isinstance(payload['bars'],dict) and set(payload['bars'])<=set(t['symbols'])
    count=0
    for symbol,series in payload['bars'].items():
     for bar in series:
      assert re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00(?:\.0{1,9})?(?:Z|[+-]\d{2}:\d{2})',bar['t'])
      dt=datetime.fromisoformat(bar['t'].replace('Z','+00:00'));assert start<=dt<=end
      offset=int((dt-opening).total_seconds()/60)
      identity=(t['date'],symbol,offset);assert identity not in seen;seen.add(identity)
      assert market[t['date']][symbol][str(offset)]==bar
      count+=1
    assert count==part['records'];bars+=count;pages+=1;totalbytes+=len(raw)
    token=payload['next_page_token']
    if i==len(record['pages'])-1:assert not token
    else:assert token and token not in tokens
    if token:tokens.add(token);req['page_token']=token
   assert record['request_sha256']==objsha({'route':route,'parameters':params})
  assert bars==manifest['data_records']==sum(len(b) for day in market.values() for b in day.values())
  assert totalbytes==manifest['raw_bytes']
  summaries.append({'stage':stage,'tasks_verified':len(tasks),'pages_verified':pages,'bars_verified':bars,'raw_bytes_verified':totalbytes,'all_page_hash_size_request_chains_and_assembled_bars_verified':True})
  del market
 cache=read(ROOT/'cached-source-manifest.json');daily=read(ROOT/'daily-market-private.json');events=read(ROOT/'corporate-events.json')
 assert sha(ROOT/'daily-market-private.json')==cache['daily-market-private.json'] and sha(ROOT/'corporate-events.json')==cache['corporate-events.json']
 wanted={c['symbol'] for c in holding['cases']};expected_daily={};dailyfiles=0
 inventory=read(ROOT.parent/'s500-orb-20261006'/'ep-candidate-inventory.json')
 for name,h in cache['daily_raw_inputs_sha256'].items():
  path=broad/name;assert sha(path)==h==inventory['inputs_sha256'][name];dailyfiles+=1
  for symbol,rows in read(path)['bars'].items():
   if symbol in wanted:
    assert symbol not in expected_daily
    expected_daily[symbol]={datetime.fromisoformat(b['t'].replace('Z','+00:00')).astimezone(ny).date().isoformat():b for b in rows}
    assert len(expected_daily[symbol])==len(rows)
 assert daily==expected_daily
 actionpath=ROOT.parent/'s500-aggressive-20261006'/'company-actions.json';assert sha(actionpath)==cache['parent_actions_sha256']
 original_actions=read(actionpath)['rows'];expected_events={s:[] for s in wanted}
 for i,a in enumerate(original_actions):
  for symbol in {s for s in (a.get('symbol'),a.get('new_symbol')) if s}&wanted:expected_events[symbol].append({**a,'source_row_index':i})
 assert events==expected_events
 coverage=read(ROOT/'portfolio-coverage.json');assert coverage['cohort_complete'] is False
 assert coverage['daily_screen_counts']==inventory['daily_screen_counts']
 assert len(coverage['days'])==190 and all(v['initial_screen_complete'] is False for v in coverage['days'].values())
 repair=read(ROOT/'repair-summary.json');assert repair['checked']==112 and repair['zero_filled_bars']==0 and repair['source_conflict_cases']==0
 assert all(not c['new_minute_offsets'] and not c['missing_from_fresh_offsets'] and not c['common_bar_value_conflicts'] and c['old_status']==c['fresh_evaluation_status'] for c in repair['comparisons'])
 return {'holding_cases_retained':73,'required_symbol_session_pairs':len(required),'stages':summaries,
         'daily_raw_files_hash_verified':dailyfiles,'daily_symbols':len(daily),'daily_bars':sum(len(v) for v in daily.values()),
         'daily_extract_and_action_mapping_independently_matched':True,'full_universe_guard_false_sessions':190,
         'repair_unknown_cases_unchanged':112,'zero_filled_bars':0,'newly_market_pass_cases':repair['newly_market_pass_with_exit_and_primary_inputs_pending']}


if __name__=='__main__':unittest.main()
