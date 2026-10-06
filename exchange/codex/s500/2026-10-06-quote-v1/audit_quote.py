"""Independent offline quote tests and frozen-input audit; never fetches network."""
import copy
import hashlib
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import quote_data
import analyze_quotes as analyzer

ROOT=Path(__file__).resolve().parent


def point():
    return {'id':'test-A','symbol':'A','date':'2026-01-05',
            'decision_time':'2026-01-05T14:35:00+00:00',
            'request_start':'2026-01-05T14:34:00+00:00',
            'request_end':'2026-01-05T14:35:00+00:00',
            'cohorts':['test'], 'metadata':[{'atr14':2.0}]}


def quote(t='2026-01-05T14:35:00Z',**kw):
    q={'t':t,'bp':10.,'ap':10.01,'bs':100,'as':100,'bx':'Q','ax':'Q','c':['R'],'z':'C'}
    q.update(kw);return q


class DownloaderAudit(unittest.TestCase):
    def test_only_fixed_read_market_routes(self):
        for route in ('https://evil.example/quotes','/v2/orders','/v2/account','/v2/stocks/A/quotes/extra'):
            with self.assertRaises(ValueError):quote_data.get_json(route,{})
        with self.assertRaises(ValueError):quote_data.get_json('/v2/stocks/A/quotes',{'feed':'iex'})
        with self.assertRaises(ValueError):quote_data.get_json('/v2/stocks/meta/conditions/quote',{'tape':'D'})
        with self.assertRaises(ValueError):quote_data.fetch_window({**point(),'symbol':'A/../../orders'})
        with self.assertRaises(RuntimeError):quote_data.NoRedirect().redirect_request(None,None,None,None,None,None)

    def test_partial_resume_uses_bound_page_token(self):
        a={'symbol':'A','quotes':[quote('2026-01-05T14:34:59Z')],'next_page_token':'page1'}
        b={'symbol':'A','quotes':[quote()],'next_page_token':None}
        with tempfile.TemporaryDirectory() as td,patch.object(quote_data,'ROOT',Path(td)):
            with patch.object(quote_data,'get_json',side_effect=[a,RuntimeError('interrupt')]):
                with self.assertRaises(RuntimeError):quote_data.fetch_window(point())
            with patch.object(quote_data,'get_json',return_value=b) as fetch:
                result=quote_data.fetch_window(point())
                self.assertEqual(fetch.call_count,1);self.assertEqual(fetch.call_args.args[1]['page_token'],'page1')
            self.assertEqual(len(result['pages']),2);self.assertEqual(len(quote_data.load_window(point()['id'])),2)
            with patch.object(quote_data,'get_json',side_effect=AssertionError('must_not_fetch')):
                self.assertEqual(quote_data.fetch_window(point())['request_sha256'],result['request_sha256'])

    def test_complete_cache_hash_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as td,patch.object(quote_data,'ROOT',Path(td)):
            with patch.object(quote_data,'get_json',return_value={'symbol':'A','quotes':[quote()],'next_page_token':None}):
                quote_data.fetch_window(point())
            p=Path(td)/'raw-quotes'/point()['id']/'page-0000.json';q=json.loads(p.read_text());q['response']['quotes'][0]['ap']=99;p.write_text(json.dumps(q))
            with self.assertRaises(AssertionError):quote_data.load_window(point()['id'])
            with self.assertRaises(AssertionError):quote_data.fetch_window(point())

    def test_resume_chain_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as td,patch.object(quote_data,'ROOT',Path(td)):
            with patch.object(quote_data,'get_json',side_effect=[{'quotes':[],'next_page_token':'p1'},{'quotes':[],'next_page_token':'p2'},RuntimeError('interrupt')]):
                with self.assertRaises(RuntimeError):quote_data.fetch_window(point())
            p=Path(td)/'raw-quotes'/point()['id']/'page-0000.json';q=json.loads(p.read_text());q['response']['next_page_token']='wrong-p1';p.write_text(json.dumps(q))
            with self.assertRaises(AssertionError):quote_data.fetch_window(point())

    def test_cache_parameters_cannot_change(self):
        with tempfile.TemporaryDirectory() as td,patch.object(quote_data,'ROOT',Path(td)):
            with patch.object(quote_data,'get_json',return_value={'quotes':[],'next_page_token':None}):
                quote_data.fetch_window(point())
            for change in ({'symbol':'B'},{'request_end':'2026-01-05T14:35:01+00:00'}):
                with self.assertRaises(AssertionError):quote_data.fetch_window({**point(),**change})

    def test_cycle_and_wrong_symbol_and_schema_rejected(self):
        for payload,error in (({'symbol':'B','quotes':[]},AssertionError),({'symbol':'A','quotes':42},AssertionError)):
            with tempfile.TemporaryDirectory() as td,patch.object(quote_data,'ROOT',Path(td)),patch.object(quote_data,'get_json',return_value=payload):
                with self.assertRaises(error):quote_data.fetch_window(point())
        with tempfile.TemporaryDirectory() as td,patch.object(quote_data,'ROOT',Path(td)),patch.object(quote_data,'get_json',return_value={'quotes':[],'next_page_token':'repeat'}):
            with self.assertRaisesRegex(RuntimeError,'pagination_cycle'):quote_data.fetch_window(point())

    def test_page_cap_retains_partial_failure(self):
        with tempfile.TemporaryDirectory() as td,patch.object(quote_data,'ROOT',Path(td)),patch.object(quote_data,'get_json',side_effect=[{'quotes':[],'next_page_token':f'p{i}'} for i in range(50)]):
            with self.assertRaisesRegex(RuntimeError,'page_budget_reached'):quote_data.fetch_window(point())
            self.assertFalse((Path(td)/'raw-quotes'/point()['id']/'complete.json').exists())


def audit_frozen_sample():
    prior=ROOT.parent/'s500-orb-20261006'
    sample=json.loads((ROOT/'sample.json').read_text())
    design=json.loads((ROOT/'study-design.json').read_text())
    assert hashlib.sha256((ROOT/'sample.json').read_bytes()).hexdigest()==design['sample_sha256']
    for name,digest in design['prior_input_sha256'].items():
        assert hashlib.sha256((prior/name).read_bytes()).hexdigest()==digest
    episodes=json.loads((prior/'daily-diagnostics.json').read_text())['episodes']
    ranked=json.loads((prior/'ranked.json').read_text())
    coverage=json.loads((prior/'bounded-coverage.json').read_text())['coverage']
    first10=[d for d,c in sorted(coverage.items()) if c['ranking_complete']][:10]
    assert sample['certified_dates']==first10
    cohort=lambda label:[p for p in sample['points'] if label in p['cohorts']]
    rankedpoints=cohort('first10_certified_days_all_top20')
    assert {(p['date'],p['symbol']) for p in rankedpoints}=={(d,r['symbol']) for d in first10 for r in ranked[d]}
    entries={(e['date'],e['symbol'],e['entry_time']) for result in episodes for e in result['entries']}
    from datetime import datetime,timezone
    utc=lambda t:datetime.fromisoformat(t).astimezone(timezone.utc).isoformat()
    assert {(p['date'],p['symbol'],p['decision_time']) for p in cohort('all_prior_selected_entries_including_held_failures')}=={(d,s,utc(t)) for d,s,t in entries}
    gaps={(r['incomplete']['date'],r['incomplete']['symbol'],utc(r['incomplete']['time'])) for r in episodes if r.get('incomplete') and r.get('open_position')}
    assert {(p['date'],p['symbol'],p['decision_time']) for p in cohort('all_prior_held_minute_failures')}==gaps
    assert len(sample['points'])==len({p['id'] for p in sample['points']})==221
    for p in sample['points']:
        assert p['request_end']==p['decision_time']
        assert (datetime.fromisoformat(p['request_end'])-datetime.fromisoformat(p['request_start'])).total_seconds()==60
    return {'sample_windows':221,'all_first10_top20_points':len(rankedpoints),'all_unique_entry_points':len(entries),'all_held_failure_points':len(gaps),'sample_and_prior_inputs_hash_verified':True,'complete_and_failed_entry_samples_retained':True}



CONDITIONS={'A':{'R':'Regular Market Maker Open'},'B':{'R':'Regular Market Maker Open'},'C':{'R':'Regular Two Sided Open'}}


class AnalyzerAudit(unittest.TestCase):
    def evaluate(self,rows,age=1,at='2026-01-05T14:35:00Z',maps=None):
        return analyzer.evaluate_latest(rows,at,age,CONDITIONS if maps is None else maps)

    def test_nanosecond_timestamp_timezone_and_boundary(self):
        base=analyzer.timestamp_ns('2026-01-05T14:35:00Z')
        self.assertEqual(base,analyzer.timestamp_ns('2026-01-05T09:35:00-05:00'))
        self.assertEqual(base+1,analyzer.timestamp_ns('2026-01-05T14:35:00.000000001Z'))
        self.assertEqual(analyzer.timestamp_ns('2026-07-06T09:35:00-04:00'),analyzer.timestamp_ns('2026-07-06T13:35:00Z'))
        for bad in ('2026-01-05T14:35:00.1234567890Z','2026-01-05T14:35:00','invalid',None):
            with self.assertRaises(ValueError):analyzer.timestamp_ns(bad)

    def test_exact_decision_allowed_future1ns_not_allowed(self):
        result=self.evaluate([quote('2026-01-05T14:35:00.000000001Z')])
        self.assertFalse(result['accepted']);self.assertEqual(result['future_rows_ignored'],1)
        result=self.evaluate([quote(),quote('2026-01-05T14:35:00.000000001Z',bp=0)])
        self.assertTrue(result['accepted']);self.assertEqual(result['api_reported_age_ns'],0)

    def test_age1s_boundary_and_one_ns_stale(self):
        self.assertTrue(self.evaluate([quote('2026-01-05T14:34:59Z')])['accepted'])
        result=self.evaluate([quote('2026-01-05T14:34:58.999999999Z')])
        self.assertFalse(result['accepted']);self.assertEqual(result['api_reported_age_ns'],1000000001)

    def test_age5s_boundary_and_nested_eligibility(self):
        row=quote('2026-01-05T14:34:55Z')
        self.assertTrue(self.evaluate([row],5)['accepted']);self.assertFalse(self.evaluate([row],1)['accepted'])
        self.assertFalse(self.evaluate([quote('2026-01-05T14:34:54.999999999Z')],5)['accepted'])
        with self.assertRaises(ValueError):self.evaluate([quote()],2)

    def test_latest_invalid_never_fallback_older_valid(self):
        older=quote('2026-01-05T14:34:59.5Z')
        for changes in ({'bp':0},{'ap':0},{'bs':0},{'as':0},{'c':['N']},{'z':'Z'},{'bp':11}):
            with self.subTest(changes=changes):
                rows=[older,quote(**changes)]
                a=self.evaluate(rows);b=self.evaluate(list(reversed(rows)))
                self.assertFalse(a['accepted']);self.assertEqual(a,b)
                self.assertEqual(a['latest_quote']['t'],quote()['t'])

    def test_latest_timestamp_conflict_even_only_size_blocks(self):
        for changes in ({'as':101},{'bp':9.99},{'c':['N']},{'bx':'N'}):
            result=self.evaluate([quote(),quote(**changes)])
            self.assertFalse(result['accepted']);self.assertEqual(result['reason'],'ambiguous_latest_timestamp')
            self.assertEqual(result['latest_distinct_states'],2)

    def test_duplicate_same_state_different_time_format_collapses(self):
        result=self.evaluate([quote(),quote('2026-01-05T09:35:00-05:00')])
        self.assertTrue(result['accepted']);self.assertEqual(result['latest_distinct_states'],1)
        self.assertEqual(result['latest_timestamp_rows'],2)

    def test_latest_selection_order_independent_across_pages(self):
        rows=[quote('2026-01-05T14:34:59.9Z',ap=10.03),quote('2026-01-05T14:34:59Z'),quote('2026-01-05T14:34:59.8Z',ap=10.02)]
        for order in (rows,list(reversed(rows)),[rows[1],rows[0],rows[2]]):
            self.assertEqual(self.evaluate(order)['latest_quote']['ap'],10.03)

    def test_invalid_or_nonfinite_and_bool_numeric_rejected(self):
        for field in ('bp','ap','bs','as'):
            for value in (0,-1,None,True,'100',float('nan'),float('inf')):
                with self.subTest(field=field,value=value):
                    self.assertFalse(self.evaluate([quote(**{field:value})])['accepted'])

    def test_locked_quote_allowed_crossed_rejected(self):
        self.assertTrue(self.evaluate([quote(ap=10)])['accepted'])
        self.assertFalse(self.evaluate([quote(ap=9.99)])['accepted'])

    def test_condition_exact_and_tape_map_verified(self):
        for condition in (None,[],['N'],['R','N'],['R','R'],'R'):
            self.assertFalse(self.evaluate([quote(c=condition)])['accepted'])
        self.assertFalse(self.evaluate([quote()],maps={})['accepted'])
        self.assertFalse(self.evaluate([quote()],maps={'C':{'R':'Some other meaning'}})['accepted'])
        for tape in ('A','B','C'):self.assertTrue(self.evaluate([quote(z=tape)])['accepted'])

    def test_malformed_timestamp_blocks_certification(self):
        result=self.evaluate([quote(),quote(t='bad')])
        self.assertFalse(result['accepted']);self.assertEqual(result['reason'],'unparseable_timestamp_cannot_certify_latest')
        self.assertFalse(self.evaluate([quote(),None])['accepted'])

    def test_empty_and_outside60s_remain_no_observation(self):
        self.assertEqual(self.evaluate([])['reason'],'no_quote_in_requested_lookback')
        result=self.evaluate([quote('2026-01-05T14:33:59.999999999Z')])
        self.assertEqual(result['older_than_requested_lookback_rows'],1)
        self.assertEqual(result['reason'],'no_quote_in_requested_lookback')

    def test_budget_decimal_exact_boundary_and_below_one_share(self):
        for ask,counts in ((.1,(2500,5000)),(250,(1,2)),(250.01,(0,1)),(500.01,(0,0))):
            result=analyzer.metrics(quote(bp=ask,ap=ask),[{'atr14':2}])
            self.assertEqual(tuple(r['whole_shares_at_displayed_ask'] for r in result['budget_only']),counts)
            for b in result['budget_only']:
                self.assertGreaterEqual(b['cash_remainder'],0)
                self.assertLessEqual(b['hypothetical_ask_notional'],b['budget'])
                self.assertFalse(b['capacity_or_fill_claim'])

    def test_spread_to_stop_and_crossing_cost_not_profit(self):
        result=analyzer.metrics(quote(bp=9.5,ap=10),[{'atr14':2}])
        self.assertEqual(result['spread_dollars'],.5)
        self.assertEqual(result['spread_to_stop_distance'],2.5)
        self.assertTrue(result['spread_at_least_stop_distance'])
        self.assertEqual([r['same_quote_two_sided_crossing_cost_no_fees'] for r in result['budget_only']],[12.5,25.])
        self.assertFalse(result['execution_capacity_verified'])
        self.assertEqual(result['displayed_size_raw'],{'bid':100,'ask':100})

    def test_entry_reference_signed_and_not_synchronized(self):
        result=analyzer.metrics(quote(bp=9.8,ap=9.9),[{'atr14':2,'original_minute_entry_open':10}])
        self.assertEqual(result['ask_minus_minute_entry_reference'],-.1)
        self.assertEqual(result['ask_minus_minute_entry_reference_bps'],-100)
        self.assertIn('may be after',result['reference_comparability'])
        with self.assertRaises(ValueError):analyzer.metrics(quote(),[{'atr14':2},{'atr14':3}])

    def test_main_rejects_frozen_design_tampering_before_loading_quotes(self):
        for change in ({'id':'tampered'},{'sample_sha256':'wrong'}):
            with tempfile.TemporaryDirectory() as td,patch.object(analyzer,'ROOT',Path(td)):
                for name in ('sample.json','quote-input-manifest.json','study-design.json'):
                    (Path(td)/name).write_bytes((ROOT/name).read_bytes())
                p=Path(td)/'study-design.json';d=json.loads(p.read_text());d.update(change);p.write_text(json.dumps(d))
                with patch.object(analyzer,'load_window',side_effect=AssertionError('must_not_load')) as load:
                    with self.assertRaises(AssertionError):analyzer.main()
                    self.assertEqual(load.call_count,0)

    def test_main_rejects_point_cache_manifest_binding_mismatch(self):
        with tempfile.TemporaryDirectory() as td,patch.object(analyzer,'ROOT',Path(td)):
            for name in ('sample.json','quote-input-manifest.json','study-design.json','quote-condition-metadata.json'):
                (Path(td)/name).write_bytes((ROOT/name).read_bytes())
            first=json.loads((Path(td)/'sample.json').read_text())['points'][0]
            folder=Path(td)/'raw-quotes'/first['id'];folder.mkdir(parents=True)
            original=json.loads((ROOT/'raw-quotes'/first['id']/'complete.json').read_text());original['route']='/v2/stocks/WRONG/quotes'
            (folder/'complete.json').write_text(json.dumps(original))
            with patch.object(analyzer,'load_window',side_effect=AssertionError('must_not_load')) as load:
                with self.assertRaises(AssertionError):analyzer.main()
                self.assertEqual(load.call_count,0)

    def test_evaluation_and_metrics_do_not_modify_inputs(self):
        rows=[quote()];metadata=[{'atr14':2}];before=copy.deepcopy((rows,metadata))
        result=self.evaluate(rows);analyzer.metrics(result['latest_quote'],metadata)
        self.assertEqual((rows,metadata),before)



def audit_actual():
    """Reconcile every raw page, latest state and accepted arithmetic independently."""
    from collections import Counter
    from datetime import datetime,timezone
    from fractions import Fraction
    from statistics import median,mean
    import math,re
    def read(name):return json.loads((ROOT/name).read_text())
    def digest_file(name):return hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
    def digest_obj(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    def nanos(stamp):
        match=re.fullmatch(r'(.*T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})',stamp)
        if not match:raise ValueError('bad timestamp')
        base,fraction,zone=match.groups();base=datetime.fromisoformat(base+zone.replace('Z','+00:00'))
        delta=base-datetime(1970,1,1,tzinfo=timezone.utc)
        return (delta.days*86400+delta.seconds)*1000000000+int((fraction or '').ljust(9,'0'))
    def equal(a,b):
        if a is None or b is None:assert a is b
        elif isinstance(a,(float,int)) and isinstance(b,(float,int)):assert math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-10),(a,b)
        else:assert a==b,(a,b)
    def validnum(v):return type(v) in (int,float) and math.isfinite(v) and v>0
    sample=read('sample.json');design=read('study-design.json');manifest=read('quote-input-manifest.json')
    result=read('quote-results.json');summary=read('quote-summary.json')
    maps={r['tape']:r['response'] for r in read('quote-condition-metadata.json')}
    frozen=audit_frozen_sample()
    assert manifest['all_pages_complete'] and not manifest['failures']
    assert manifest['sample_sha256']==digest_file('sample.json')
    assert manifest['design_sha256']==digest_file('study-design.json')
    assert summary['point_results_sha256']==digest_file('quote-results.json')
    for name,h in result['source_sha256'].items():assert digest_file(name)==h,(name,'result_source_changed')
    records={r['point_id']:r for r in manifest['records']}
    assert len(records)==len(manifest['records'])==len(sample['points'])==221
    pointresults={(r['point_id'],r['max_age_seconds']):r for r in result['rows']}
    assert len(pointresults)==len(result['rows'])==442
    countquotes=countpages=countbytes=acceptedchecks=0;rawconditions=Counter();statuses=Counter();latesttiepoints=0;futuretotal=0
    for pointrow in sample['points']:
        pid=pointrow['id'];record=records[pid];folder=ROOT/'raw-quotes'/pid
        cached=json.loads((folder/'complete.json').read_text());assert cached==record
        expectedparams={'start':pointrow['request_start'],'end':pointrow['request_end'],'limit':10000,'feed':'sip','sort':'asc'}
        route='/v2/stocks/'+pointrow['symbol']+'/quotes'
        assert record['route']==route and record['parameters']==expectedparams and record['pages_complete']
        request=dict(expectedparams);basehash=digest_obj({'route':route,'parameters':request});assert record['request_sha256']==basehash
        rows=[];tokens=set()
        for i,page in enumerate(record['pages']):
            raw=(folder/page['name']).read_bytes();assert len(raw)==page['bytes'] and hashlib.sha256(raw).hexdigest()==page['sha256']
            saved=json.loads(raw);assert saved['page_number']==i and saved['base_request_sha256']==basehash
            assert saved['page_request_sha256']==digest_obj({'route':route,'parameters':request})
            assert nanos(saved['retrieved_at'])>=nanos(design['registered_at'])
            response=saved['response'];assert response.get('symbol',pointrow['symbol'])==pointrow['symbol']
            quotes=response.get('quotes') or [];assert len(quotes)==page['quotes'];rows.extend(quotes)
            token=response.get('next_page_token')
            if i==len(record['pages'])-1:assert not token
            else:assert token and token not in tokens
            if token:tokens.add(token);request['page_token']=token
            countpages+=1;countbytes+=len(raw)
        countquotes+=len(rows)
        decision=nanos(pointrow['decision_time']);start=nanos(pointrow['request_start'])
        stamps=[(nanos(q['t']),q) for q in rows]
        usable=[(t,q) for t,q in stamps if start<=t<=decision]
        future=sum(t>decision for t,q in stamps);older=sum(t<start for t,q in stamps);futuretotal+=future
        latest_at=max((t for t,q in usable),default=None)
        latest=[q for t,q in usable if t==latest_at]
        states={json.dumps({k:v for k,v in q.items() if k!='t'},sort_keys=True,separators=(',',':')) for q in latest}
        if len(states)>1:latesttiepoints+=1
        for age in (1,5):
            r=pointresults[(pid,age)]
            assert r['date']==pointrow['date'] and r['symbol']==pointrow['symbol'] and r['metadata']==pointrow['metadata']
            assert r['quote_rows']==len(rows) and r['future_rows_ignored']==future and r['older_than_requested_lookback_rows']==older
            assert r['invalid_timestamp_rows']==0
            expectedreasons=[]
            if not latest:expected='no_quote_in_requested_lookback';accepted=False
            elif len(states)!=1:expected='ambiguous_latest_timestamp';accepted=False
            else:
                q=latest[0];assert r['latest_quote']==q
                assert r['latest_timestamp_ns']==latest_at and r['api_reported_age_ns']==decision-latest_at
                if not(validnum(q.get('bp')) and validnum(q.get('ap'))):expectedreasons.append('nonpositive_or_invalid_bid_ask')
                elif q['ap']<q['bp']:expectedreasons.append('crossed_quote')
                if not(validnum(q.get('bs')) and validnum(q.get('as'))):expectedreasons.append('nonpositive_or_invalid_displayed_size')
                if q.get('z') not in ('A','B','C') or maps.get(q.get('z'),{}).get('R') not in ('Regular Market Maker Open','Regular Two Sided Open'):expectedreasons.append('unverified_tape_regular_condition_mapping')
                if q.get('c')!=['R']:expectedreasons.append('unsupported_or_missing_quote_condition')
                if decision-latest_at>age*1000000000:expectedreasons.append('stale_api_reported_timestamp')
                accepted=not expectedreasons;expected=expectedreasons[0] if expectedreasons else 'accepted_for_displayed_quote_diagnostic_only'
                assert r['rejection_reasons']==expectedreasons
            assert r['reason']==expected and r['accepted']==accepted
            statuses[(age,expected)]+=1
            if not accepted:assert r['metrics'] is None;continue
            acceptedchecks+=1;q=latest[0];m=r['metrics'];F=lambda v:Fraction(str(v))
            b,a=F(q['bp']),F(q['ap']);spread=a-b
            atrset={F(x['atr14']) for x in pointrow['metadata']};assert len(atrset)==1;atr=next(iter(atrset));stop=atr/10
            for key,value in {'bid':b,'ask':a,'spread_dollars':spread,'spread_bps_of_mid':spread/((a+b)/2)*10000,'prior_ATR14':atr,'stop_distance_0_1ATR':stop,'spread_to_stop_distance':spread/stop}.items():equal(m[key],float(value))
            assert m['spread_at_least_stop_distance']==(spread>=stop)
            assert m['locked_quote']==(spread==0)
            assert m['displayed_size_raw']=={'bid':q['bs'],'ask':q['as']}
            assert m['execution_capacity_verified'] is False and m['displayed_size_unit']=='unverified_Alpaca_API_representation'
            assert len(m['budget_only'])==2
            for budget,row in zip((250,500),m['budget_only']):
                qty=budget//a
                assert row['budget']==budget and row['whole_shares_at_displayed_ask']==qty
                assert qty*a<=budget<(qty+1)*a
                equal(row['hypothetical_ask_notional'],float(qty*a));equal(row['cash_remainder'],float(budget-qty*a))
                equal(row['same_quote_two_sided_crossing_cost_no_fees'],float(qty*spread))
                assert row['capacity_or_fill_claim'] is False
            refs={F(md['original_minute_entry_open']) for md in pointrow['metadata'] if 'original_minute_entry_open' in md}
            if refs:
                assert len(refs)==1;ref=next(iter(refs));equal(m['original_minute_entry_reference'],float(ref))
                equal(m['ask_minus_minute_entry_reference'],float(a-ref));equal(m['ask_minus_minute_entry_reference_bps'],float((a/ref-1)*10000))
        assert not pointresults[(pid,1)]['accepted'] or pointresults[(pid,5)]['accepted']
    assert countquotes==manifest['raw_quote_count']==summary['raw_quote_count']
    assert countbytes==manifest['raw_response_bytes']
    for group in summary['groups']:
        cohort=group['cohort'];age=group['max_age_seconds']
        selected=[r for r in result['rows'] if r['max_age_seconds']==age and(cohort=='all_points' or cohort in r['cohorts'])]
        good=[r for r in selected if r['accepted']]
        assert group['total_points']==len(selected) and group['accepted_points']==len(good)
        assert group['reason_counts']==dict(Counter(r['reason'] for r in selected))
        assert group['all_rejection_reason_counts']==dict(Counter(v for r in selected for v in r.get('rejection_reasons',[])))
        for name in ('spread_bps_of_mid','spread_to_stop_distance'):
            vals=[r['metrics'][name] for r in good]
            expected={'count':len(vals),'median':median(vals) if vals else None,'min':min(vals) if vals else None,'max':max(vals) if vals else None,'mean':mean(vals) if vals else None}
            for k,v in expected.items():equal(group[name][k],v)
        assert group['spread_at_least_stop_distance']==sum(r['metrics']['spread_at_least_stop_distance'] for r in good)
        for i,budget in enumerate((250,500)):assert group['budget_only_unaffordable_'+str(budget)]==sum(r['metrics']['budget_only'][i]['whole_shares_at_displayed_ask']==0 for r in good)
    assert result['wealth_path_generated'] is False and result['simulated_trades_generated'] is False and result['broker_orders_sent']==0
    assert summary['no_returns_or_wealth_claim'] is True and summary['api_size_capacity_verified'] is False
    return {'frozen_sample':frozen,'raw_pages_verified':countpages,'raw_response_bytes_verified':countbytes,'raw_quotes_verified':countquotes,
            'point_age_records_checked':len(pointresults),'accepted_record_metrics_checked':acceptedchecks,'budget_rows_checked':acceptedchecks*2,
            'summary_groups_checked':len(summary['groups']),'latest_ambiguous_points':latesttiepoints,'future_rows_quarantined':futuretotal,
            'primary_1s_accepted':sum(r['accepted'] for r in result['rows'] if r['max_age_seconds']==1),
            'sensitivity_5s_accepted':sum(r['accepted'] for r in result['rows'] if r['max_age_seconds']==5),
            'all_page_chains_and_frozen_design_binding_verified':True,'wealth_or_fill_claims_detected':False}


if __name__=='__main__':unittest.main()
