"""Independent offline news-source, time-state and frozen-cohort audit."""
from collections import Counter
import copy
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo
import download_inputs as downloader
import news_analysis as analyzer

ROOT=Path(__file__).resolve().parent
ORB=ROOT.parent/'s500-orb-20261006'
QUOTE=ROOT.parent/'s500-quote-20261006'


def read(path):return json.loads(Path(path).read_text())
def filehash(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def objhash(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def audit_frozen_cohort():
    design=read(ROOT/'study-design.json');payload=read(ROOT/'candidates.json')
    inventory=read(ORB/'ep-candidate-inventory.json');calendar=read(ORB/'prepare.json')['full_calendar']
    cmap={r['date']:r for r in calendar};dates=[r['date'] for r in calendar]
    assert payload['source_inventory_sha256']==filehash(ORB/'ep-candidate-inventory.json')
    sources={'candidates.json':ROOT/'candidates.json','bar-tasks.json':ROOT/'bar-tasks.json',
             'registry.json':ORB/'registry.json','prior_news_reviews':QUOTE/'ep-followup.json'}
    for key,path in sources.items():assert design['input_sha256'][key]==filehash(path)
    original=sorted((r for r in inventory['candidates'] if r['stock_research_eligible_initial']),key=lambda r:(r['date'],r['symbol']))
    actual=payload['candidates'];assert len(actual)==len(original)==1079
    assert payload['candidate_count']==design['candidate_count']==1079
    assert len({r['candidate_id'] for r in actual})==1079
    early_closes=0;weekend_or_holiday_windows=0
    for index,(a,b) in enumerate(zip(actual,original),1):
        for key,value in b.items():assert a[key]==value,(index,key)
        assert a['chronological_index']==index and a['candidate_id']==a['date']+'__'+a['symbol']
        day=a['date'];previous=a['prior_session'];assert dates[dates.index(day)-1]==previous
        local_open=datetime.fromisoformat(day+'T'+cmap[day]['open']).replace(tzinfo=ZoneInfo('America/New_York'))
        local_close=datetime.fromisoformat(previous+'T'+cmap[previous]['close']).replace(tzinfo=ZoneInfo('America/New_York'))
        assert datetime.fromisoformat(a['news_request_start'])==local_close
        assert datetime.fromisoformat(a['news_request_end'])==local_open
        assert local_close<local_open
        early_closes+=cmap[previous]['close']!='16:00'
        weekend_or_holiday_windows+=(datetime.fromisoformat(day)-datetime.fromisoformat(previous)).days>1
    tasks=read(ROOT/'bar-tasks.json')['tasks'];pairs=[]
    for task in tasks:
        assert len(task['symbols'])<=50 and len(task['symbols'])==len(set(task['symbols']))
        day=task['date'];pairs.extend((day,s) for s in task['symbols'])
        assert datetime.fromisoformat(task['start'])==datetime.fromisoformat(day+'T09:30:00').replace(tzinfo=ZoneInfo('America/New_York'))
        assert datetime.fromisoformat(task['end'])==datetime.fromisoformat(day+'T11:02:00').replace(tzinfo=ZoneInfo('America/New_York'))
    assert len(pairs)==len(set(pairs))==1079 and set(pairs)=={(r['date'],r['symbol']) for r in actual}
    return {'original_stock_candidates_preserved':1079,'unique_symbols':len({r['symbol'] for r in actual}),
            'candidate_dates':len({r['date'] for r in actual}),'bar_request_chunks':len(tasks),
            'candidate_windows_after_early_close':early_closes,'candidate_windows_spanning_weekend_or_holiday':weekend_or_holiday_windows,
            'all_source_hashes_verified':True,'chronology_and_prior_session_bounds_verified':True,
            'bar_tasks_cover_all_candidates_without_news_filter':True}



def candidate():
    return {'candidate_id':'2026-01-05__A','symbol':'A','date':'2026-01-05',
        'news_request_start':'2026-01-02T21:00:00+00:00','news_request_end':'2026-01-05T14:30:00+00:00'}


def article(**changes):
    row={'id':100,'headline':'Company announces new contract','created_at':'2026-01-05T13:00:00Z',
         'updated_at':'2026-01-05T13:00:01Z','symbols':['A'],'url':'https://example.com/100',
         'source':'test','author':'example','summary':'', 'content':'', 'images':[]}
    row.update(changes);return row


class DownloaderAudit(unittest.TestCase):
    def test_fixed_read_routes_and_guard(self):
        for route in ('/v2/account','/v2/orders','https://evil.example/news'):
            with self.assertRaises(ValueError):downloader.get_json(route,{})
        with self.assertRaises(ValueError):downloader.get_json('/v1beta1/news',{'sort':'desc'})
        with self.assertRaises(ValueError):downloader.get_json('/v1beta1/news',{'sort':'asc','content':'false'})
        with self.assertRaises(AssertionError):downloader.get_json('/v1beta1/news',{'sort':'asc','limit':1000,'include_content':'false'})
        with self.assertRaises(RuntimeError):downloader.NoRedirect().redirect_request(None,None,None,None,None,None)
        route,params,tid,budget=downloader.task_spec('news',candidate())
        self.assertEqual((route,tid,budget),('/v1beta1/news',candidate()['candidate_id'],40))
        self.assertEqual(params['include_content'],'false');self.assertEqual(params['exclude_contentless'],'false')
        self.assertEqual(params['limit'],50)

    def test_empty_page_with_token_still_fetches_next_and_no_created_early_stop(self):
        payloads=[{'news':[],'next_page_token':'p1'},
                  {'news':[article(created_at='2026-01-06T00:00:00Z')],'next_page_token':'p2'},
                  {'news':[article()],'next_page_token':None}]
        with tempfile.TemporaryDirectory() as td,patch.object(downloader,'ROOT',Path(td)),patch.object(downloader,'get_json',side_effect=payloads) as fetch:
            result=downloader.fetch_task('news',candidate())
            self.assertEqual(fetch.call_count,3);self.assertEqual(len(result['pages']),3)
            self.assertEqual(len(downloader.load_task('news',candidate()['candidate_id'])),3)

    def test_partial_resume_and_complete_cache_no_refetch(self):
        a={'news':[article()],'next_page_token':'p1'};b={'news':[article(id=101)],'next_page_token':None}
        with tempfile.TemporaryDirectory() as td,patch.object(downloader,'ROOT',Path(td)):
            with patch.object(downloader,'get_json',side_effect=[a,RuntimeError('interrupt')]):
                with self.assertRaises(RuntimeError):downloader.fetch_task('news',candidate())
            with patch.object(downloader,'get_json',return_value=b) as fetch:
                result=downloader.fetch_task('news',candidate());self.assertEqual(fetch.call_count,1)
                self.assertEqual(fetch.call_args.args[1]['page_token'],'p1')
            with patch.object(downloader,'get_json',side_effect=AssertionError('must_not_refetch')):
                self.assertEqual(downloader.fetch_task('news',candidate()),result)

    def test_partial_page_chain_tampering_and_complete_hash_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as td,patch.object(downloader,'ROOT',Path(td)):
            with patch.object(downloader,'get_json',side_effect=[{'news':[],'next_page_token':'p1'},{'news':[],'next_page_token':'p2'},RuntimeError('interrupt')]):
                with self.assertRaises(RuntimeError):downloader.fetch_task('news',candidate())
            p=Path(td)/'raw-news'/candidate()['candidate_id']/'page-0000.json';x=read(p);x['response']['next_page_token']='wrong';p.write_text(json.dumps(x))
            with self.assertRaises(AssertionError):downloader.fetch_task('news',candidate())
        with tempfile.TemporaryDirectory() as td,patch.object(downloader,'ROOT',Path(td)),patch.object(downloader,'get_json',return_value={'news':[article()],'next_page_token':None}):
            downloader.fetch_task('news',candidate())
            p=Path(td)/'raw-news'/candidate()['candidate_id']/'page-0000.json';x=read(p);x['response']['news'][0]['headline']='changed';p.write_text(json.dumps(x))
            with self.assertRaises(AssertionError):downloader.load_task('news',candidate()['candidate_id'])

    def test_cache_parameter_binding(self):
        with tempfile.TemporaryDirectory() as td,patch.object(downloader,'ROOT',Path(td)),patch.object(downloader,'get_json',return_value={'news':[],'next_page_token':None}):
            downloader.fetch_task('news',candidate())
            with self.assertRaises(AssertionError):downloader.fetch_task('news',{**candidate(),'symbol':'B'})
            with self.assertRaises(AssertionError):downloader.fetch_task('news',{**candidate(),'news_request_end':'2026-01-05T15:30:00Z'})

    def test_payload_failures_cycle_and_page_cap(self):
        for payload in ({'news':[]},{'news':42,'next_page_token':None},{'news':[],'next_page_token':3},[]):
            with self.assertRaises(ValueError):downloader.validate_payload('news',payload,candidate())
        with tempfile.TemporaryDirectory() as td,patch.object(downloader,'ROOT',Path(td)),patch.object(downloader,'get_json',return_value={'news':[],'next_page_token':'same'}):
            with self.assertRaisesRegex(RuntimeError,'pagination_cycle'):downloader.fetch_task('news',candidate())
        with tempfile.TemporaryDirectory() as td,patch.object(downloader,'ROOT',Path(td)),patch.object(downloader,'get_json',side_effect=[{'news':[],'next_page_token':str(i)} for i in range(40)]):
            with self.assertRaisesRegex(RuntimeError,'page_budget_reached'):downloader.fetch_task('news',candidate())
            self.assertFalse((Path(td)/'raw-news'/candidate()['candidate_id']/'complete.json').exists())



def exact_ns(value):
    import re
    match=re.fullmatch(r'(.*T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})',value)
    if not match:raise ValueError('bad timestamp')
    base,fraction,zone=match.groups();base=datetime.fromisoformat(base+zone.replace('Z','+00:00'))
    delta=base-datetime(1970,1,1,tzinfo=timezone.utc)
    return (delta.days*86400+delta.seconds)*1000000000+int((fraction or '').ljust(9,'0'))


def audit_download_stage(stage):
    design=read(ROOT/'study-design.json');manifest=read(ROOT/(stage+'-input-manifest.json'))
    rows=read(ROOT/'candidates.json')['candidates'] if stage=='news' else read(ROOT/'bar-tasks.json')['tasks']
    taskkey='candidate_id' if stage=='news' else 'task_id';tasks={r[taskkey]:r for r in rows}
    records={r['task_id']:r for r in manifest['records']};failures={r['task_id']:r for r in manifest['failures']}
    assert len(tasks)==len(rows) and len(records)==len(manifest['records']) and len(failures)==len(manifest['failures'])
    assert set(records).isdisjoint(failures) and set(tasks)==set(records)|set(failures)
    assert manifest['request_tasks']==len(rows)
    assert manifest['all_pages_complete']==(not failures)
    for name,h in manifest['input_sha256'].items():assert filehash(ROOT/name)==h
    allnews={};rawcount=pagecount=bytecount=exactbarcount=0
    for tid,record in records.items():
        task=tasks[tid];folder=ROOT/('raw-'+stage)/tid;assert read(folder/'complete.json')==record
        assert record['pages_complete'] and record['stage']==stage
        if stage=='news':
            route='/v1beta1/news';params={'symbols':task['symbol'],'start':task['news_request_start'],'end':task['news_request_end'],'sort':'asc','limit':50,'include_content':'false','exclude_contentless':'false'}
        else:
            route='/v2/stocks/bars';params={'symbols':','.join(task['symbols']),'start':task['start'],'end':task['end'],'sort':'asc','limit':10000,'timeframe':'1Min','adjustment':'raw','feed':'sip'}
        assert record['route']==route and record['parameters']==params
        base=objhash({'route':route,'parameters':params});assert record['request_sha256']==base
        request=dict(params);tokens=set();seen_bars=set();articles=[]
        for i,part in enumerate(record['pages']):
            data=(folder/part['name']).read_bytes();assert len(data)==part['bytes'] and hashlib.sha256(data).hexdigest()==part['sha256']
            saved=json.loads(data);assert saved['page_number']==i and saved['base_request_sha256']==base
            assert saved['page_request_sha256']==objhash({'route':route,'parameters':request})
            assert exact_ns(saved['retrieved_at'])>=exact_ns(design['registered_at'])
            payload=saved['response'];token=payload['next_page_token']
            if i==len(record['pages'])-1:assert not token
            else:assert token and token not in tokens
            if token:tokens.add(token);request['page_token']=token
            if stage=='news':
                assert isinstance(payload['news'],list) or payload['news'] is None
                count=len(payload['news'] or []);articles.extend(payload['news'] or [])
            else:
                assert isinstance(payload['bars'],dict) and set(payload['bars'])<=set(task['symbols'])
                count=sum(len(v) for v in payload['bars'].values())
                for symbol,bars in payload['bars'].items():
                    for bar in bars:
                        t=exact_ns(bar['t']);assert t%60000000000==0,'raw_market_bar_not_exact_minute'
                        assert exact_ns(task['start'])<=t<=exact_ns(task['end'])
                        assert (symbol,t) not in seen_bars;seen_bars.add((symbol,t));exactbarcount+=1
            assert count==part['records'];rawcount+=count;pagecount+=1;bytecount+=len(data)
        if stage=='news':allnews[tid]=articles
    assert rawcount==manifest['data_records'] and bytecount==manifest['raw_bytes']
    return {'stage':stage,'request_tasks':len(tasks),'completed_tasks':len(records),'failed_tasks':len(failures),
            'raw_pages_verified':pagecount,'raw_records_verified':rawcount,'raw_bytes_verified':bytecount,
            'exact_minute_bar_timestamps_verified':exactbarcount,'source_hashes_and_complete_request_chains_verified':True},allnews



class NewsAnalyzerAudit(unittest.TestCase):
    def evaluate(self,rows,extra=None,fetch=True,c=None):
        by={'one':rows}
        if extra is not None:by['two']=extra
        versions=analyzer.build_global_versions(by)
        return analyzer.analyze_candidate(c or candidate(),rows,versions,fetch_complete=fetch)

    def test_timestamp_exact_nanoseconds_timezone_and_invalid_offset(self):
        base=analyzer.timestamp_ns('2026-01-05T14:30:00Z')
        self.assertEqual(base,analyzer.timestamp_ns('2026-01-05T09:30:00-05:00'))
        self.assertEqual(base-1,analyzer.timestamp_ns('2026-01-05T14:29:59.999999999Z'))
        self.assertEqual(base+1,analyzer.timestamp_ns('2026-01-05T14:30:00.000000001Z'))
        for bad in (None,'2026-01-05T14:30:00','2026-01-05T14:30:00-00:00','2026-01-05T14:30:00+24:00','2026-01-05T14:30:00+00:60','2026-02-30T14:30:00Z','2026-01-05T14:30:60Z','2026-01-05T14:30:00.0000000001Z'):
            with self.assertRaises(ValueError):analyzer.timestamp_ns(bad)

    def test_created_strictly_after_close_and_before_open(self):
        for created,expected in (('2026-01-02T21:00:00Z',False),('2026-01-02T21:00:00.000000001Z',True),('2026-01-05T14:29:59.999999999Z',True),('2026-01-05T14:30:00Z',False),('2026-01-05T14:30:00.000000001Z',False)):
            r=self.evaluate([article(created_at=created,updated_at=created)])
            self.assertEqual(r['metadata_ready_unique_payloads']>0,expected)

    def test_updated_before_created_missing_malformed_and_future_quarantined(self):
        for value,reason in ((None,'missing_updated_at'),('bad','malformed_updated_at'),('2026-01-05T12:59:59.999999999Z','updated_before_created'),('2026-01-05T14:30:00Z','updated_not_strictly_before_open'),('2026-01-05T14:30:00.000000001Z','updated_not_strictly_before_open')):
            r=self.evaluate([article(updated_at=value)])
            self.assertEqual(r['metadata_ready_unique_payloads'],0)
            self.assertIn(reason,r['articles'][0]['reasons'])
        self.assertEqual(self.evaluate([article(updated_at='2026-01-05T13:00:00Z')])['metadata_ready_unique_payloads'],1)

    def test_exact_symbol_matching_no_substring_or_global_empty_pass(self):
        for tags in ([],['AA'],['a'],'A',[1],None):
            self.assertEqual(self.evaluate([article(symbols=tags)])['metadata_ready_unique_payloads'],0)
        self.assertEqual(self.evaluate([article(symbols=['B','A'])])['metadata_ready_unique_payloads'],1)

    def test_identical_payload_duplicates_collapse_but_rows_retained(self):
        rows=[article(),copy.deepcopy(article())]
        r=self.evaluate(rows)
        self.assertEqual((r['input_article_rows'],r['unique_payload_rows'],r['exact_duplicate_rows_collapsed']),(2,1,1))
        self.assertEqual(r['articles'][0]['input_row_indexes'],[0,1]);self.assertEqual(r['metadata_ready_input_rows'],2)

    def test_any_global_id_payload_conflict_quarantines_all_versions(self):
        old=article();changed=article(headline='Revised contract headline')
        for rows,other in (([old],[changed]),([changed],[old]),([old,changed],[])):
            r=self.evaluate(rows,other)
            self.assertEqual(r['metadata_ready_unique_payloads'],0)
            self.assertTrue(all('conflicting_global_news_id_versions' in a['reasons'] for a in r['articles']))

    def test_late_revision_cannot_choose_earlier_favorable_headline(self):
        older=article();late=article(updated_at='2026-01-05T15:00:00Z')
        r=self.evaluate([older],extra=[late]);self.assertEqual(r['status'],'no_metadata_ready')
        self.assertIn('conflicting_global_news_id_versions',r['articles'][0]['reasons'])

    def test_numeric_string_id_alias_is_conflict_not_two_news(self):
        r=self.evaluate([article(id=100),article(id='100')])
        self.assertEqual(r['metadata_ready_unique_payloads'],0)
        keys={a['article_key'] for a in r['articles']};self.assertEqual(len(keys),1)
        for value in (None,True,{},''):
            self.assertEqual(self.evaluate([article(id=value)])['metadata_ready_unique_payloads'],0)

    def test_missing_global_index_never_certifies_payload(self):
        r=analyzer.analyze_candidate(candidate(),[article()],{})
        self.assertIn('payload_missing_from_global_version_index',r['articles'][0]['reasons'])
        self.assertEqual(r['metadata_ready_unique_payloads'],0)

    def test_keyword_hints_do_not_verify_catalyst(self):
        r=self.evaluate([article(headline='FDA approved new contract and earnings guidance')])
        self.assertEqual(set(r['articles'][0]['keyword_hints']),{'earnings','guidance','contract','regulatory'})
        self.assertEqual(r['verified_catalyst_count'],0)
        self.assertFalse(r['articles'][0]['independently_verified_catalyst'])
        self.assertEqual(r['status'],'metadata_ready_for_review')

    def test_no_news_is_source_missing_not_no_event(self):
        r=self.evaluate([])
        self.assertEqual(r['status'],'source_no_articles');self.assertEqual(r['source_evidence_status'],'source_missing')
        self.assertTrue(r['no_articles_is_not_no_event']);self.assertEqual(r['verified_catalyst_count'],0)

    def test_partial_retrieval_keeps_evidence_but_not_candidate_ready(self):
        r=self.evaluate([article()],fetch=False)
        self.assertEqual(r['status'],'retrieval_incomplete');self.assertFalse(r['fetch_complete'])
        self.assertEqual(r['input_article_rows'],1)

    def test_malformed_article_and_invalid_window_stay_quarantined(self):
        r=self.evaluate([None,'string',1,[]])
        self.assertEqual(r['metadata_ready_unique_payloads'],0)
        self.assertTrue(all('article_payload_not_object' in a['reasons'] for a in r['articles']))
        bad={**candidate(),'news_request_start':candidate()['news_request_end']}
        self.assertEqual(self.evaluate([article()],c=bad)['metadata_ready_unique_payloads'],0)

    def test_public_evidence_does_not_emit_headline_or_summary_corpus(self):
        raw=article(headline='unique raw headline marker',summary='unique raw summary marker',content='unique full article text')
        r=self.evaluate([raw]);encoded=json.dumps(r)
        self.assertNotIn(raw['headline'],encoded);self.assertNotIn(raw['summary'],encoded);self.assertNotIn(raw['content'],encoded)
        self.assertEqual(r['articles'][0]['headline_sha256'],hashlib.sha256(raw['headline'].encode()).hexdigest())
        self.assertEqual(r['articles'][0]['source_payload_sha256'],objhash(raw))

    def test_empty_or_whitespace_headline_quarantined(self):
        for value in ('','   ',None,42):
            r=self.evaluate([article(headline=value)])
            self.assertEqual(r['metadata_ready_unique_payloads'],0)
            self.assertIn('missing_or_empty_headline',r['articles'][0]['reasons'])

    def test_inputs_unmodified(self):
        rows=[article(),article(id=101)];before=copy.deepcopy(rows);self.evaluate(rows)
        self.assertEqual(rows,before)



def audit_actual_news():
    from collections import defaultdict
    import re
    cohort=audit_frozen_cohort();newscheck,allnews=audit_download_stage('news')
    enriched=read(ROOT/'news-enriched.json');summary=read(ROOT/'news-summary.json')
    candidates=read(ROOT/'candidates.json')['candidates'];results=enriched['candidates']
    assert enriched['candidate_count']==len(results)==1079
    assert [r['candidate_id'] for r in results]==[c['candidate_id'] for c in candidates]
    assert summary['news_enriched_sha256']==filehash(ROOT/'news-enriched.json')
    for name,h in enriched['input_sha256'].items():assert filehash(ROOT/name)==h
    assert enriched['input_sha256']==summary['input_sha256']
    versions=defaultdict(set)
    def key(a):
        if not isinstance(a,dict):return None
        identity=a.get('id')
        if isinstance(identity,bool) or not isinstance(identity,(int,str)) or (isinstance(identity,str) and not identity.strip()):return None
        return hashlib.sha256(str(identity).encode()).hexdigest()
    for rows in allnews.values():
        for raw in rows:
            if key(raw) is not None:versions[key(raw)].add(objhash(raw))
    assert {k:set(v) for k,v in enriched['global_version_index'].items()}==dict(versions)
    reason_counts=Counter();status_counts=Counter();article_rows=unique_rows=duplicate_rows=ready_rows=ready_payloads=0
    keyword_patterns=enriched['keyword_patterns'];assert objhash(keyword_patterns)==enriched['keyword_patterns_sha256']==summary['keyword_patterns_sha256']
    for c,r in zip(candidates,results):
        rawrows=allnews[c['candidate_id']];groups={};lower,upper=exact_ns(c['news_request_start']),exact_ns(c['news_request_end'])
        for index,a in enumerate(rawrows):groups.setdefault(objhash(a),{'article':a,'indexes':[]})['indexes'].append(index)
        assert len(r['articles'])==len(groups)
        evidence={a['source_payload_sha256']:a for a in r['articles']};assert set(evidence)==set(groups)
        expected_reason_counts=Counter();ready_count=ready_occurrences=0
        for h,group in groups.items():
            raw=group['article'];e=evidence[h];reasons=[];identity=key(raw)
            if not isinstance(raw,dict):raw={};reasons.append('article_payload_not_object')
            if identity is None:reasons.append('invalid_news_id')
            elif len(versions[identity])>1:reasons.append('conflicting_global_news_id_versions')
            tags=raw.get('symbols')
            if not isinstance(tags,list) or not all(isinstance(t,str) for t in tags):reasons.append('invalid_symbol_tags')
            elif c['symbol'] not in tags:reasons.append('exact_symbol_tag_missing')
            stamps={}
            for field in ('created_at','updated_at'):
                value=raw.get(field)
                try:
                    if not isinstance(value,str) or value.endswith('-00:00'):raise ValueError('unknown timestamp')
                    stamps[field]=exact_ns(value)
                except (ValueError,TypeError):
                    stamps[field]=None;reasons.append(('missing_' if value is None else 'malformed_')+field)
            created,updated=stamps['created_at'],stamps['updated_at']
            if created is not None:
                if created<=lower:reasons.append('created_not_strictly_after_previous_close')
                if created>=upper:reasons.append('created_not_strictly_before_open')
            if updated is not None:
                if created is not None and updated<created:reasons.append('updated_before_created')
                if updated>=upper:reasons.append('updated_not_strictly_before_open')
            headline=raw.get('headline')
            if not isinstance(headline,str) or not headline.strip():reasons.append('missing_or_empty_headline')
            assert e['article_key']==identity and e['reasons']==reasons,(c['candidate_id'],reasons,e['reasons'])
            assert e['created_ns']==created and e['updated_ns']==updated
            assert e['input_row_indexes']==group['indexes'] and e['occurrences']==len(group['indexes'])
            assert e['metadata_status']==('metadata_ready_for_review' if not reasons else 'quarantined_metadata')
            assert e['independently_verified_catalyst'] is False
            assert not {'headline','summary','content'}&set(e)
            assert e['headline_sha256']==(hashlib.sha256(headline.encode()).hexdigest() if isinstance(headline,str) else None)
            assert e['source_url']==(raw.get('url') if isinstance(raw.get('url'),str) else None)
            text=' '.join(raw.get(k,'') for k in ('headline','summary') if isinstance(raw.get(k),str))
            expected_hints=[name for name,pattern in keyword_patterns.items() if re.search(pattern,text,re.IGNORECASE)]
            assert e['keyword_hints']==expected_hints
            for reason in reasons:expected_reason_counts[reason]+=len(group['indexes'])
            if not reasons:ready_count+=1;ready_occurrences+=len(group['indexes'])
        expectedstatus='source_no_articles' if not rawrows else 'metadata_ready_for_review' if ready_count else 'no_metadata_ready'
        assert r['fetch_complete'] is True and r['source_page_evidence']['fetch_complete'] is True
        assert r['status']==r['news_metadata_status']==expectedstatus
        assert r['input_article_rows']==len(rawrows) and r['unique_payload_rows']==len(groups)
        assert r['exact_duplicate_rows_collapsed']==len(rawrows)-len(groups)
        assert r['metadata_ready_unique_payloads']==ready_count and r['metadata_ready_input_rows']==ready_occurrences
        assert r['row_reason_counts']==dict(expected_reason_counts)
        assert r['no_articles_is_not_no_event'] is True and r['verified_catalyst_count']==0
        assert r['source_evidence_status']==('source_missing' if not rawrows else 'retrieved_metadata_only')
        reason_counts.update(expected_reason_counts);status_counts[expectedstatus]+=1
        article_rows+=len(rawrows);unique_rows+=len(groups);duplicate_rows+=len(rawrows)-len(groups);ready_rows+=ready_occurrences;ready_payloads+=ready_count
    assert summary['candidate_status_counts']==dict(status_counts)
    for field,value in {'candidates_preserved':1079,'input_article_rows':article_rows,'unique_payload_rows_within_windows':unique_rows,'exact_duplicate_rows_collapsed':duplicate_rows,'metadata_ready_unique_payloads_within_windows':ready_payloads,'global_unique_news_ids':len(versions),'global_conflicting_news_ids':sum(len(v)>1 for v in versions.values()),'verified_catalysts':0}.items():assert summary[field]==value,(field,value,summary[field])
    assert summary['quarantine_reasons_counted_per_input_row']==dict(reason_counts)
    assert enriched['verified_catalysts']==0 and enriched['broker_orders_sent']==0 and enriched['strategy_returns_computed'] is False
    return {'frozen_cohort':cohort,'news_download':newscheck,'candidate_results_checked':len(results),
            'raw_article_rows_checked':article_rows,'unique_payload_rows_within_windows_checked':unique_rows,
            'global_news_ids_checked':len(versions),'conflicting_news_ids':sum(len(v)>1 for v in versions.values()),
            'candidate_status_counts':dict(status_counts),'ready_article_occurrences':ready_rows,
            'quarantine_reason_occurrences':dict(reason_counts),'no_raw_headline_summary_or_content_in_public_article_evidence':True,
            'all_global_ids_timestamps_symbol_tags_duplicates_and_summary_counts_reconciled':True,
            'independently_verified_catalysts_claimed':0,'strategy_returns_computed':False}


if __name__=='__main__':unittest.main()
