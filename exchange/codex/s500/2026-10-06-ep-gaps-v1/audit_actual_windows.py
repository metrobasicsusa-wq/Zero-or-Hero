"""Independent offline all-record audit. Does not import collector or analyzer."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib
import json
import re

ROOT=Path(__file__).resolve().parent
PRIOR=ROOT.parent/'s500-ep-paths-20261006'
D=Decimal
NS=1_000_000_000


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path):return json.loads(path.read_text())
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'))
def digest(value):return hashlib.sha256(canonical(value).encode()).hexdigest()


def ns(value):
    match=re.fullmatch(r'(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})',value)
    assert match,value
    date=datetime.fromisoformat(match[1]+'T'+match[2]+match[4].replace('Z','+00:00'))
    delta=date.astimezone(timezone.utc)-datetime(1970,1,1,tzinfo=timezone.utc)
    return (delta.days*86400+delta.seconds)*NS+int((match[3] or '').ljust(9,'0'))


class TableCells(HTMLParser):
    def __init__(self):super().__init__();self.rows=[];self.current=None;self.cell=None
    def handle_starttag(self,tag,attrs):
        if tag=='tr':self.current=[]
        elif self.current is not None and tag in ('td','th'):self.cell=[]
    def handle_data(self,value):
        if self.cell is not None:self.cell.append(value)
    def handle_endtag(self,tag):
        if tag in ('td','th') and self.cell is not None:
            self.current.append(''.join(self.cell));self.cell=None
        elif tag=='tr' and self.current is not None:self.rows.append(self.current);self.current=None


def main():
    design=read(ROOT/'study-design.json');points=read(ROOT/'points.json')['points']
    source=read(ROOT/'source-rules.json');manifest=read(ROOT/'input-manifest.json')
    result=read(ROOT/'gap-diagnostics.json');summary=read(ROOT/'gap-summary.json')
    baseline=read(ROOT/'independent-selection-audit.json')
    for name,want in baseline['parent_file_baseline_sha256'].items():assert sha(PRIOR/name)==want,name
    for name,want in design['input_sha256'].items():assert sha(PRIOR/name)==want,name
    assert sha(ROOT/'points.json')==design['points_sha256']
    for name,want in manifest['input_sha256'].items():assert sha(ROOT/name)==want,name
    for name,want in result['input_sha256'].items():assert sha(ROOT/name)==want,name
    assert summary['diagnostics_sha256']==sha(ROOT/'gap-diagnostics.json')
    docs_checked=0
    for doc in source['sources']:
        path=ROOT/'raw-docs'/(doc['doc_id']+'.raw')
        if doc.get('body_sha256'):
            assert sha(path)==doc['body_sha256']
            assert path.stat().st_size==doc['bytes']
            docs_checked+=1
    # Re-extract all minute update rows directly from the preserved official table.
    table=TableCells();table.feed((ROOT/'raw-docs'/'alpaca_faq.raw').read_text())
    symbols={'🟢':True,'🔴':False,'🟡':'update_only_if_first'}
    expected_rules=[]
    for row in table.rows:
        if len(row)!=7 or not row[2] or not set(row[2])<=set('ABCO') or 'M' not in row[3] or row[4] not in symbols:continue
        expected_rules.append({'condition':row[0],'meaning':row[1],'tapes':list(row[2]),'bar_type':'minute',
                               'open_close':symbols[row[4]],'high_low':symbols[row[5]],'volume':symbols[row[6]],'source_doc_id':'alpaca_faq'})
    assert expected_rules==source['minute_condition_rules'] and len(expected_rules)==32
    mapping={(tape,row['condition']):row for row in expected_rules for tape in row['tapes']}
    metadata_rows=read(ROOT/'condition-metadata.json')
    assert len(metadata_rows)==6 and {(r['kind'],r['tape']) for r in metadata_rows}=={(k,t) for k in ('trade','quote') for t in 'ABC'}
    assert all(r['status']=='complete' and isinstance(r['response'],dict) for r in metadata_rows)
    metadata={r['tape']:r['response'] for r in metadata_rows if r['kind']=='trade'}
    def trade_state(row):
        if row.get('u') in ('canceled','incorrect'):return 'provider_invalidated'
        if 'u' in row and row['u']!='corrected':return 'unknown_update_flag'
        try:
            if any(not D(str(row.get(f))).is_finite() or D(str(row.get(f)))<=0 for f in ('p','s')):return 'invalid_price_or_size'
        except Exception:return 'invalid_price_or_size'
        tape=row.get('z');conditions=row.get('c')
        if tape not in metadata or not isinstance(conditions,list) or not conditions or any(c not in metadata[tape] for c in conditions):return 'unknown_conditions'
        applicable=[mapping.get((tape,c)) for c in conditions]
        if any(rule and rule['open_close'] is False and rule['high_low'] is False for rule in applicable):return 'documented_price_excluded'
        if all(rule and rule['open_close'] is True and rule['high_low'] is True for rule in applicable):return 'documented_price_updating'
        return 'unknown_or_partial_price_rule'
    inputs={(r['point_id'],r['stream']):r for r in manifest['records']}
    outputs={r['point_id']:r for r in result['results']}
    assert len(points)==len(outputs)==38 and len(inputs)==len(manifest['records'])==114
    assert set(outputs)=={p['point_id'] for p in points}
    parent=read(PRIOR/'holding-market.json')
    totals=Counter();all_states=Counter();classes=Counter();checks=[];new_context_bars=[]
    for point in points:
        point_output=outputs[point['point_id']];target=ns(point['target_start_utc'])
        assert target==ns(point['missing_time']) and target%(60*NS)==0
        assert ns(point['target_end_exclusive_utc'])==target+60*NS
        assert ns(point['request_start_utc'])==target-120*NS
        assert ns(point['window_end_exclusive_utc'])==target+180*NS
        assert ns(point['request_end_inclusive_utc'])==target+180*NS-1
        bounds=(target-120*NS,target+180*NS);streams={};streamissues={}
        for stream in ('bars','trades','quotes'):
            r=inputs[(point['point_id'],stream)]
            route='/v2/stocks/'+point['symbol']+'/'+stream
            parameters={'start':point['request_start_utc'],'end':point['request_end_inclusive_utc'],'sort':'asc','limit':10000,'feed':'sip'}
            if stream=='bars':parameters.update(timeframe='1Min',adjustment='raw')
            assert r['route']==route and r['parameters']==parameters and r['symbol']==point['symbol']
            base=digest({'route':route,'parameters':parameters})
            assert r['request_sha256']==base and r['page_budget']==(1 if stream=='bars' else 10)
            assert 1<=len(r['pages'])<=r['page_budget']
            rows=[];request=dict(parameters);tokens=set()
            for i,item in enumerate(r['pages']):
                assert item['name']=='page-%04d.json'%i
                path=ROOT/('raw-'+stream)/point['point_id']/item['name']
                assert sha(path)==item['sha256'] and path.stat().st_size==item['bytes']
                page=read(path);assert page['base_request_sha256']==base and page['page_number']==i
                assert page['request_parameters']==request
                requesthash=digest({'route':route,'parameters':request})
                assert page['page_request_sha256']==item['request_sha256']==requesthash
                assert datetime.fromisoformat(page['retrieved_at'])>datetime.fromisoformat(design['registered_at'])
                payload=page['response'];assert payload['symbol']==point['symbol']
                assert payload[stream] is None or isinstance(payload[stream],list)
                data=payload[stream] or [];assert len(data)==item['records'];rows.extend(data)
                token=payload['next_page_token'];assert token is None or isinstance(token,str) and token
                assert item['has_next_token'] is (token is not None)
                assert item['next_token_sha256']==(digest(token) if token is not None else None)
                if token is None:assert i==len(r['pages'])-1
                else:
                    assert token not in tokens;tokens.add(token);request={**parameters,'page_token':token}
            assert token is None and r['status']=='complete' and r['pages_complete'] is True
            assert len(rows)==r['records'];totals[stream]+=len(rows);totals['pages']+=len(r['pages'])
            times=[ns(row['t']) for row in rows]
            assert times==sorted(times) and all(bounds[0]<=t<bounds[1] for t in times)
            if stream=='bars':assert all(t%(60*NS)==0 for t in times)
            if stream=='trades':
                identities=[(row['z'],row['x'],row['i']) for row in rows]
                assert len(identities)==len(set(identities)),point['point_id']
            streams[stream]=list(zip(times,rows))
            assert point_output['stream_integrity'][stream]=={'request_status':'complete','issues':{},'usable_complete':True}
        targets={stream:[r for t,r in rows if target<=t<target+60*NS] for stream,rows in streams.items()}
        trades=targets['trades'];states=Counter(trade_state(t) for t in trades)
        all_states.update(states);totals['target_trades']+=len(trades);totals['target_quotes']+=len(targets['quotes'])
        totals['targets_with_quotes']+=bool(targets['quotes'])
        assert not targets['bars'] and point_output['fresh_target_bars']==0
        # Actual sample has no correction flags, invalid prices or unknown condition states.
        assert set(states)<= {'documented_price_excluded'}
        expected_class='target_missing_price_excluded_trades_consistent_with_documented_rules' if trades else 'target_missing_provider_returned_no_trades'
        assert point_output['classification']==expected_class;classes[expected_class]+=1
        ts=point_output['target_trades']
        assert ts['records']==ts['current_valid_records']==len(trades) and ts['states']==dict(states)
        assert D(ts['current_valid_observed_share_volume'])==sum((D(str(t['s'])) for t in trades),D(0))
        if trades:
            assert D(ts['current_valid_min_price'])==min(D(str(t['p'])) for t in trades)
            assert D(ts['current_valid_max_price'])==max(D(str(t['p'])) for t in trades)
        else:assert ts['current_valid_min_price'] is None and ts['current_valid_max_price'] is None
        groups=defaultdict(list)
        for t in trades:groups[canonical({'tape':t['z'],'conditions':t['c'],'update_flag':t.get('u','__absent__'),'state':trade_state(t)})].append(t)
        expected_groups=[{**json.loads(key),'records':len(values),'observed_share_volume':str(sum((D(str(t['s'])) for t in values),D(0)))} for key,values in sorted(groups.items())]
        assert ts['condition_groups']==expected_groups
        qs=point_output['target_quotes'];quotes=targets['quotes']
        assert qs['records']==len(quotes)
        positive_non_crossed=sum(all(D(str(t[k])).is_finite() and D(str(t[k]))>0 for k in ('bp','ap','bs','as')) and D(str(t['bp']))<=D(str(t['ap'])) for t in quotes)
        assert qs['positive_prices_sizes_non_crossed']==positive_non_crossed and qs['other_geometry']==len(quotes)-positive_non_crossed
        assert qs['executable_capacity_or_fill_inferred'] is False and qs['continuous_market_or_no_halt_inferred'] is False
        for delta,minute in zip(range(-2,3),point_output['five_minute_observations']):
            samples={s:[row for t,row in values if target+delta*60*NS<=t<target+(delta+1)*60*NS] for s,values in streams.items()}
            assert minute=={'relative_minute':delta,'bars':len(samples['bars']),'trade_records':len(samples['trades']),'trade_states':dict(Counter(trade_state(r) for r in samples['trades'])),'quote_records':len(samples['quotes'])}
        local=datetime.fromisoformat(point['missing_time']).astimezone(ZoneInfo('America/New_York'))
        offset=local.hour*60+local.minute-570;old=parent.get(local.date().isoformat(),{}).get(point['symbol'],{})
        assert str(offset) not in old
        compares=[]
        for t,bar in streams['bars']:
            relative=(t-target)//(60*NS);prior=old.get(str(offset+relative))
            changed=[f for f in ('o','h','l','c','v','n','vw') if prior is not None and prior.get(f)!=bar.get(f)]
            compares.append({'minute_offset_relative_to_target':relative,'parent_present':prior is not None,'changed_fields':changed})
            if prior is not None:
                assert not changed;totals['unchanged_common_bars']+=1
            else:new_context_bars.append({'point_id':point['point_id'],'symbol':point['symbol'],'relative_minute':relative,'regular_session_offset':offset+relative,'reason':'outside_parent_regular_session_window' if offset+relative<0 else 'unclassified_new_context'})
        assert point_output['fresh_vs_parent_bars']==compares
        assert point_output['case_ids']==point['case_ids'] and point_output['variant_ids']==point['variant_ids']
        assert point_output['parent_pnl_recalculated'] is False and point_output['causal_bar_reconstruction_proven'] is False
        assert point_output['halt_status']=='not_certified_by_this_market_data_diagnostic'
        checks.append({'point_id':point['point_id'],'classification_verified':True,'exact_request_and_page_chain_verified':True,'all_returned_timestamps_in_window':True,'all_trade_identities_unique_within_window':True,'target_trades':len(trades),'target_quotes':len(quotes),'prior_result_not_modified':True})
    assert summary['points']==len(checks)==38 and summary['windows']==114
    assert summary['classification_counts']==dict(classes)
    assert summary['raw_records_by_stream']=={s:totals[s] for s in ('bars','trades','quotes')}
    assert summary['raw_pages']==totals['pages']==114
    assert summary['target_trade_records']==totals['target_trades']==1069
    assert summary['target_trade_state_counts']==dict(all_states)
    assert summary['target_quote_records']==totals['target_quotes']==823
    assert summary['targets_with_quote_records']==totals['targets_with_quotes']==35
    assert summary['unchanged_parent_common_bars']==totals['unchanged_common_bars']==144
    assert summary['fresh_target_bar_count']==summary['parent_common_bar_value_conflicts']==summary['source_integrity_issue_points']==0
    assert summary['prior_incomplete_paths_automatically_certified']==summary['new_strategy_tests']==summary['broker_orders_sent']==0
    assert not summary['new_scheduler_deployed']
    halts=read(ROOT/'halt-query-results.json')
    assert halts['classification']=={'REPL':'unknown','SPCE':'unknown'}
    assert halts['event_queries_per_symbol']=={'REPL':1,'SPCE':1}
    assert halts['halt_explanations_confirmed']==halts['no_halt_claims']==0
    assert len(halts['attempts'])==2
    for attempt in halts['attempts']:
        body=(ROOT/'raw-halts'/(attempt['symbol']+'__attempt1.raw')).read_bytes()
        assert hashlib.sha256(body).hexdigest()==attempt['body_sha256'] and len(body)==attempt['bytes']
        assert b'_Incapsula_Resource' in body and attempt['result_status']=='challenge_page_not_event_data'
        assert attempt['halt_at_target'] is None and attempt['parsed_event_records'] is None
        assert attempt['absence_of_halt_established'] is False
        assert attempt['transport_endpoint_confirmed_from_page'] is False
        matched=[p for p in points if p['symbol']==attempt['symbol'] and ns(p['missing_time'])==ns(attempt['target_minute_et'])]
        assert len(matched)==1
    assert len(halts['supporting_source_captures'])==3
    for capture in halts['supporting_source_captures']:
        body=ROOT/'raw-docs'/(capture['key']+'.raw')
        assert sha(body)==capture['body_sha256'] and body.stat().st_size==capture['bytes']
    assert sha(ROOT/'query_halts.py')==halts['code_sha256']
    assert sha(ROOT/'summarize_halts.py')==halts['summarizer_sha256']
    report={'audited_at':datetime.now(timezone.utc).isoformat(),'all_points':38,'all_windows':114,'all_pages':114,
            'raw_records_by_stream':{s:totals[s] for s in ('bars','trades','quotes')},'target_trade_records':1069,'target_quote_records':823,
            'all_38_source_classifications_and_190_minute_buckets_reconciled':True,'target_trade_states':dict(all_states),
            'classification_counts':dict(classes),'current_docs_hash_verified':docs_checked,'minute_rule_rows_reextracted':32,
            'all_12_parent_baseline_hashes_unchanged':True,'trade_identity_conflicts_observed':0,'new_context_bars':new_context_bars,
            'checks':checks,'asof_parameter_sent':False,'symbol_mapping_disabled_proven':False,
            'source_time_bucket_clock_conflict_retained':True,'no_trade_return_is_not_market_absence':True,'halt_status_certified':False,
            'halt_query_bodies_hash_verified':2,'halt_supporting_bodies_hash_verified':3,
            'halt_query_outcomes':'Both challenge pages; transport endpoint not verified; halt remains unknown.',
            'network_calls':0,'old_pnl_recalculated':False,'input_sha256':{name:sha(ROOT/name) for name in ['study-design.json','points.json','input-manifest.json','condition-metadata.json','source-rules.json','collect_windows.py','analyze_windows.py','gap-diagnostics.json','gap-summary.json','halt-query-results.json','audit_actual_windows.py']}}
    (ROOT/'independent-actual-audit.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('checks','input_sha256')},indent=2))


if __name__=='__main__':main()
