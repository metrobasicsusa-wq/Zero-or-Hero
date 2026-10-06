"""Independent offline option source audit. Never imports collectors or pricing code."""
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation
from collections import Counter, defaultdict
import hashlib
import json
import re
import sys
import time

ROOT = Path(__file__).resolve().parent
NY = ZoneInfo('America/New_York')
CONTRACT = ('symbol','name','status','tradable','expiration_date','root_symbol','underlying_symbol','type','style','strike_price','multiplier','size','ppind','open_interest','open_interest_date','close_price','close_price_date')
DELIVERABLE = ('symbol','type','amount','allocation_percentage','settlement_type','settlement_method','delayed_settlement')
QUOTE = ('t','ap','as','ax','bp','bs','bx','c')
TRADE = ('t','p','s','c','x','u')


def read(name):
    return json.loads((ROOT/name).read_bytes())


def sha(name):
    return hashlib.sha256((ROOT/name).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def require(value, label):
    if not value:
        raise AssertionError(label)


def ns(value):
    if not isinstance(value,str):
        raise ValueError('timestamp not string')
    m = re.fullmatch(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})',value)
    if not m:
        raise ValueError('timestamp not exact timezone-aware ISO nanoseconds')
    dt = datetime.fromisoformat(m[1]+m[3].replace('Z','+00:00')).astimezone(timezone.utc)
    delta = dt-datetime(1970,1,1,tzinfo=timezone.utc)
    return (delta.days*86400+delta.seconds)*10**9+int((m[2] or '').ljust(9,'0'))


def positive(value):
    if isinstance(value,bool) or value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and number>0 else None


def save(name,value):
    target=ROOT/name
    require(not target.exists(),'refuse overwrite '+name)
    target.write_text(json.dumps(value,indent=2)+'\n')


def verified_record(record,task,registered_at):
    """Verify every attempt, raw byte, request/token chain and terminal status."""
    for key,value in task.items():
        require(record[key]==value,'task binding '+task['task_id']+' '+key)
    base={'host':task['host'],'path':task['path'],'parameters':task['parameters']}
    if record['pages']:
        require(record['request_sha256']==digest(base),'base request hash')
    request=dict(task['parameters']); tokens=set(); payloads=[]; rows=[]
    complete=False; reason=None; attempts=byte_count=0
    require(len(record['pages'])<=task['max_pages'],'page budget')
    for index,page in enumerate(record['pages']):
        require(page['page_number']==index and page['attempts'],'page numbering/attempt presence')
        expected={'method':'GET','host':task['host'],'path':task['path'],'parameters':request}
        request_hash=digest(expected)
        require(page['page_request_sha256']==request_hash,'page request hash')
        require(len(page['attempts'])<=5,'attempt budget')
        for attempt in page['attempts']:
            raw=(ROOT/attempt['body_file']).read_bytes(); meta=read(attempt['metadata_file'])
            require(len(raw)==attempt['bytes'] and hashlib.sha256(raw).hexdigest()==attempt['body_sha256'],'raw bytes/hash')
            require(sha(attempt['metadata_file'])==attempt['metadata_sha256'],'metadata hash')
            require(all(meta[k]==v for k,v in expected.items()),'actual GET parameters')
            require(meta['page_request_sha256']==request_hash and meta['response_body_sha256']==attempt['body_sha256'],'metadata request/body chain')
            require(all(meta[k]==attempt[k] for k in ('status','error_type','requested_at','received_at')),'attempt metadata')
            require(ns(registered_at)<=ns(attempt['requested_at'])<=ns(attempt['received_at']),'request chronology')
            rows.append(attempt);attempts+=1;byte_count+=len(raw)
        require(page['status']==attempt['status'] and page['requested_at']==page['attempts'][0]['requested_at'] and page['received_at']==attempt['received_at'],'page timestamps/status')
        try: payload=json.loads(raw)
        except (json.JSONDecodeError,UnicodeDecodeError):payload=None
        if page['status']!=200:reason='http_'+str(page['status'])
        elif not isinstance(payload,dict):reason='response_not_json_object'
        else:
            field=task['response_field'];value=payload.get(field)
            if not isinstance(value,list if field=='option_contracts' else dict):reason='invalid_response_'+field
            elif field!='option_contracts' and not set(value)<=set(task['parameters'].get('symbols','').split(',')):reason='unexpected_response_symbols'
            elif task.get('unpaginated'):complete=True
            elif 'next_page_token' not in payload:reason='missing_pagination_marker'
            else:
                token=payload['next_page_token']
                if token is None:complete=True
                elif not isinstance(token,str) or not token:reason='invalid_pagination_token'
                elif token in tokens:reason='pagination_cycle'
                else:tokens.add(token);request={**task['parameters'],'page_token':token}
        if isinstance(payload,dict) and page['status']==200:payloads.append(payload)
        if complete or reason:
            require(index==len(record['pages'])-1,'pages after terminal response')
    if not record['pages']:
        require(not record['pages_complete'] and record['failure_reason'].startswith('collector_exception:'),'empty task classification')
        reason=record['failure_reason']
    elif not complete and reason is None:
        require(len(record['pages'])==task['max_pages'],'unterminated before page cap')
        reason='page_cap_reached'
    require(record['pages_complete'] is complete and record['failure_reason']==reason,'independent completion classification')
    return payloads if complete else [], {'pages':len(record['pages']),'attempts':attempts,'bytes':byte_count}, rows


def chain_plan(name):
    current=read(name)
    effective=current['registered_at']
    if 'supersedes' in current:
        prior=current['supersedes'];require(sha(prior['artifact'])==prior['sha256'],'archived plan hash')
        old=read(prior['artifact']);effective=old['registered_at']
        keys=('tasks','references') if 'supplemental' in name else ('contract_tasks','selection_policy','historical_market_policy','current_quote_policy','transport')
        require(all(current[k]==old[k] for k in keys),'corrective plan changed scope or policy')
    return current,effective


def supplement_audit():
    main=read('independent-sampling-review.json')
    preserved={n:sha(n) for n in ('study-design.json','sample-universe.json','session-coverage-plan.json','current-underlying-references.json','independent-sampling-review.json','independent-sampling-review.md')}
    for name in ('study-design.json','sample-universe.json','session-coverage-plan.json','current-underlying-references.json'):
        require(preserved[name]==main['input_sha256'][name],'original sampling artifact changed '+name)
    plan,effective=chain_plan('supplemental-reference-plan.json');acq,_=chain_plan('acquisition-plan.json')
    for name,h in plan['input_sha256'].items():require(sha(name)==h,'supplement input '+name)
    manifest=read('supplemental-reference-manifest.json');supp=read('supplemental-underlying-references.json')
    require(manifest['plan_sha256']==supp['plan_sha256']==acq['supplemental_reference_plan_sha256']==sha('supplemental-reference-plan.json'),'supplement plan chain')
    sample=read('sample-universe.json');current=read('current-underlying-references.json');design=read('study-design.json')
    expected=[dict(x,reference_scope='historical') for x in sample['underlying_references'] if x['prior_close_reference'] is None]
    expected += [dict(x,case_id=design['current_snapshot_date']+'__'+x['symbol'],date=design['current_snapshot_date'],reference_scope='current') for x in current['rows'] if x['prior_close_reference'] is None]
    require(plan['references']==expected and len(expected)==11 and {x['symbol'] for x in expected}=={'SPY'},'exact missing SPY references')
    dates=[s['date'] for s in json.loads((ROOT.parent/'s500-ep-paths-20261006/holding-input-design.json').read_bytes())['full_calendar']]
    for row in expected:
        require(max(d for d in dates if d<row['date'])==row['prior_session'],'previous calendar session')
    require({x['prior_session'] for x in expected}==set(main['independent_reconstruction']['expected_SPY_supplement_prior_sessions']),'eleven prior dates')
    tasks={x['task_id']:x for x in plan['tasks']};records={x['task_id']:x for x in manifest['records']}
    require(len(tasks)==len(records)==11 and set(tasks)==set(records),'supplement task coverage')
    values={};stats=Counter();all_attempts=[]
    for tid,task in tasks.items():
        day=task['reference_day'];start=datetime.fromisoformat(day+'T00:00:00').replace(tzinfo=NY);stop=start+timedelta(days=1);p=task['parameters']
        require(task['host']=='data.alpaca.markets' and task['path']=='/v2/stocks/bars' and task['max_pages']==2,'supplement route/budget')
        require({k:p[k] for k in ('symbols','timeframe','feed','adjustment','limit','sort')}=={'symbols':'SPY','timeframe':'1Day','feed':'sip','adjustment':'raw','limit':10000,'sort':'asc'},'supplement fixed parameters')
        require(ns(p['start'])==ns(start.isoformat()) and ns(p['end'])==ns(stop.isoformat())-1,'nanosecond full local calendar day')
        payloads,count,attempts=verified_record(records[tid],task,effective);stats.update(count);all_attempts.extend(attempts)
        require(records[tid]['pages_complete'],'supplement unexpectedly incomplete')
        bars=[]
        for payload in payloads:
            require(set(payload['bars'])=={'SPY'},'unexpected supplement symbol');bars.extend(payload['bars']['SPY'])
        require(len(bars)==1,'ambiguous supplement daily bar')
        bar=bars[0];require(ns(p['start'])<=ns(bar['t'])<=ns(p['end']),'daily source outside exact day')
        require(datetime.fromisoformat(bar['t'].replace('Z','+00:00')).astimezone(NY).date().isoformat()==day,'daily local date')
        price=positive(bar['c']);require(price is not None,'daily positive finite close');values[day]=(str(price),bar['t'],tid)
    actual={x['case_id']:x for x in supp['rows']};require(len(actual)==len(supp['rows'])==11,'derived supplement count')
    for row in expected:
        item=actual[row['case_id']];value,t,tid=values[row['prior_session']]
        require(all(item[k]==row[k] for k in ('case_id','date','symbol','prior_session','reference_scope')),'supplement identity')
        require(item['prior_close_reference']==value and item['source_timestamp']==t and item['request_ids']==[tid],'supplement raw value mapping')
        require(item['original_prior_close_reference'] is None and item['original_status']=='missing' and item['status']=='supplemented_prior_close','original missing retained')
        require(item['request_complete'] is True and item['matching_source_bars']==1 and item['point_in_time_or_split_adjusted_identity_proven'] is False,'supplement evidence limits')
    require(ns(supp['generated_at'])>=max(ns(x['received_at']) for x in all_attempts),'supplement after source responses')
    require(preserved=={n:sha(n) for n in preserved},'sampling artifacts modified during audit')
    report={'status':'passed','reviewed_at':datetime.now(timezone.utc).isoformat(),'references':11,'historical_cells':10,'current_cells':1,'prior_sessions':sorted(values),'raw_verification':dict(stats),
      'all_raw_hash_bytes_GET_parameters_page_chains_and_values_match':True,'original_sample_and_missing_reference_files_byte_unchanged':True,'only_original_missing_SPY_references_supplemented':True,
      'original_plan_registered_at':effective,'corrective_plan_registered_at':plan['registered_at'],'supplement_generated_at':supp['generated_at'],
      'corrective_plan':'Archived version retained; request tasks, reference membership and contract-selection policy unchanged. Code corrections affect documented deliverable names and derived event-count names only.',
      'chronology_limit':'Supplement precedes final selected-contract freeze by collector ordering; the full audit separately checks actual frozen-selection and option-price request timestamps.',
      'limitations':['Raw prior daily closes are not PIT availability, adjusted identity or ATM-at-10:00 certification.','The forty-underlying universe and 400/40 discovery-cell denominators remain unchanged.'],
      'preserved_sha256':preserved,'input_sha256':{n:sha(n) for n in ('supplemental-reference-plan.json','supplemental-reference-manifest.json','supplemental-underlying-references.json','acquisition-plan.json','collect_option_inputs.py')},
      'network_requests':0,'orders':0,'strategy_simulations':0}
    return report,manifest,supp


def clean_contract(row):
    out={k:row[k] for k in CONTRACT if k in row}
    if 'deliverables' in row:
        if not isinstance(row['deliverables'],list):out.update(deliverables=None,deliverables_schema_unknown=True)
        else:
            out['deliverables']=[]
            for item in row['deliverables']:
                if not isinstance(item,dict):out['deliverables'].append({'schema_unknown':True});continue
                value={k:item[k] for k in DELIVERABLE if k in item};symbol=value.get('symbol')
                if isinstance(symbol,str) and re.fullmatch('[A-Z0-9]{9}',symbol) and any(x.isdigit() for x in symbol):
                    value.pop('symbol');value['symbol_identity_unresolved']=True
                out['deliverables'].append(value)
    return out


def full_audit():
    began=time.monotonic();sup_report,sup_manifest,supp=supplement_audit()
    plan,effective=chain_plan('acquisition-plan.json');design=read('study-design.json');sample=read('sample-universe.json');current=read('current-underlying-references.json')
    for name,h in plan['source_sha256'].items():require(sha(name)==h,'acquisition input hash '+name)
    listing=read('contract-list-manifest.json');selected=read('selected-contracts.json');coverage=read('contract-list-coverage.json');mp=read('market-request-plan.json');mm=read('option-market-manifest.json');source=read('source-manifest.json')
    require(listing['plan_sha256']==sha('acquisition-plan.json'),'listing plan hash')
    require(selected['source_sha256']=={n:sha(n) for n in ('acquisition-plan.json','contract-list-manifest.json','supplemental-underlying-references.json')},'selection source hashes')
    require(mp['selected_contracts_sha256']==sha('selected-contracts.json') and mp['acquisition_plan_sha256']==sha('acquisition-plan.json') and mm['plan_sha256']==sha('market-request-plan.json'),'market request plan hashes')
    require(ns(supp['generated_at'])<=ns(selected['selection_frozen_at'])<=ns(mp['registered_at']),'supplement/selection/market plan chronology')
    tasks={t['task_id']:t for t in plan['contract_tasks']};records={r['task_id']:r for r in listing['records']}
    require(len(tasks)==len(records)==440 and set(tasks)==set(records),'440 exact listing tasks')
    expected_cells={('historical',d,s) for d in design['history_dates'] for s in sample['sample_symbols']}|{('current',design['current_snapshot_date'],s) for s in sample['sample_symbols']}
    require({(t['scope'],t['date'],t['underlying_symbol']) for t in tasks.values()}==expected_cells,'400 historical plus 40 current cells')
    references={('historical',r['case_id']):r for r in sample['underlying_references']}
    references.update({('current',design['current_snapshot_date']+'__'+r['symbol']):r for r in current['rows']})
    supers={(r['reference_scope'],r['case_id']):r for r in supp['rows']};rebuilt={'historical':[],'current':[]};summaries=[];listing_stats=Counter();listing_attempts=[];contract_count=0
    for tid in sorted(tasks):
        task=tasks[tid];record=records[tid];params=task['parameters'];scope=task['scope'];day=task['date'];symbol=task['underlying_symbol']
        require(task['host']=='paper-api.alpaca.markets' and task['path']=='/v2/options/contracts' and task['max_pages']==10,'listing route/budget')
        wanted={'underlying_symbols':symbol,'status':'inactive' if scope=='historical' else 'active','show_deliverables':'true','limit':1000}
        if scope=='historical':wanted.update(expiration_date_gte=day,expiration_date_lte=min((datetime.fromisoformat(day)+timedelta(days=7)).date().isoformat(),design['period'][1]))
        else:wanted['expiration_date']='2026-10-09'
        require(params==wanted,'listing parameter scope')
        payloads,count,attempts=verified_record(record,task,effective);listing_stats.update(count);listing_attempts.extend(attempts)
        contracts=[c for p in payloads for c in p['option_contracts']];contract_count+=len(contracts);issues=[];seen=set()
        for c in contracts:
            if not isinstance(c,dict):issues.append('non_object_contract');continue
            sym=c.get('symbol');expiry=c.get('expiration_date')
            if not isinstance(sym,str) or not re.fullmatch('[A-Z0-9]+',sym):issues.append('invalid_contract_symbol')
            if sym in seen:issues.append('duplicate_contract_symbol')
            seen.add(sym)
            if c.get('underlying_symbol')!=symbol:issues.append('unexpected_underlying')
            if c.get('status')!=params['status']:issues.append('unexpected_current_status')
            if c.get('type') not in ('call','put'):issues.append('unknown_contract_type')
            if positive(c.get('strike_price')) is None:issues.append('invalid_strike')
            try:
                valid=datetime.fromisoformat(expiry).date().isoformat()==expiry
                valid=valid and (day<=expiry<=params['expiration_date_lte'] if scope=='historical' else expiry==params['expiration_date'])
                if not valid:issues.append('expiration_outside_scope_or_invalid')
            except (TypeError,ValueError):issues.append('expiration_outside_scope_or_invalid')
        expiry=min((c['expiration_date'] for c in contracts),default=None) if not issues else None
        key=(scope,task['case_id']);original=references[key];ref=original.get('prior_close_reference');ref_source='original_frozen_reference'
        if ref is None and key in supers:ref=supers[key]['prior_close_reference'];ref_source='supplemental_SPY_exact_prior_session'
        close=positive(ref)
        summaries.append({'task_id':tid,'scope':scope,'case_id':task['case_id'],'pages_complete':record['pages_complete'],'failure_reason':record['failure_reason'],'returned_contract_count':len(contracts),'validation_issues':sorted(set(issues)),'earliest_expiration':expiry,'prior_reference_available':close is not None})
        for kind in ('call','put'):
            reasons=[];choice=None
            if not record['pages_complete']:reasons.append('contract_listing_incomplete_or_failed')
            if issues:reasons.append('contract_listing_validation_unknown')
            if not contracts:reasons.append('no_contracts_returned')
            if close is None:reasons.append('missing_or_invalid_prior_reference')
            if not reasons:
                eligible=[c for c in contracts if c['expiration_date']==expiry and c['type']==kind]
                if not eligible:reasons.append('no_type_at_earliest_expiration')
                else:choice=min(eligible,key=lambda c:(abs(positive(c['strike_price'])-close),c['symbol']))
            rebuilt[scope].append({'case_id':task['case_id'],'date':day,'underlying_symbol':symbol,'type':kind,'status':'selected' if choice else 'unknown_or_unavailable','reasons':reasons,'contract':clean_contract(choice) if choice else None,'request_ids':[tid],'prior_close_reference':str(close) if close is not None else None,'prior_session':original['prior_session'],'original_prior_close_reference':original.get('prior_close_reference'),'reference_source':ref_source,'earliest_expiration_in_listing':expiry,'historical_listing_availability_proven':False})
    require(summaries==coverage['rows'],'listing coverage reconstruction')
    for scope,n in [('historical',800),('current',80)]:require(len(rebuilt[scope])==n and rebuilt[scope]==selected[scope],'all selected/unknown contract rows '+scope)
    require(max(ns(x['received_at']) for x in listing_attempts)<=ns(selected['selection_frozen_at']),'selection before completed listing')
    print('All 440 raw listings and 880 selected/unavailable rows independently reconstructed.',flush=True)
    byday=defaultdict(set)
    for row in selected['historical']:
        if row['contract']:byday[row['date']].add(row['contract']['symbol'])
    wanted_market={}
    for day,symbols in sorted(byday.items()):
        start=datetime.fromisoformat(day+'T10:00:00').replace(tzinfo=NY).astimezone(timezone.utc)
        for i in range(0,len(symbols),100):
            batch=sorted(symbols)[i:i+100]
            for field in ('bars','trades'):
                tid='history_'+field+'__'+day+'__%03d'%(i//100);p={'symbols':','.join(batch),'start':start.strftime('%Y-%m-%dT%H:%M:%SZ'),'end':(start+timedelta(seconds=59)).strftime('%Y-%m-%dT%H:%M:%S')+'.999999999Z','sort':'asc','limit':10000}
                if field=='bars':p['timeframe']='1Min'
                wanted_market[tid]={'task_id':tid,'host':'data.alpaca.markets','path':'/v1beta1/options/'+field,'parameters':p,'response_field':field,'max_pages':20,'date':day,'scope':'historical'}
    current_symbols=sorted({x['contract']['symbol'] for x in selected['current'] if x['contract']})
    for i in range(0,len(current_symbols),100):
        tid='current_indicative__%03d'%(i//100);wanted_market[tid]={'task_id':tid,'host':'data.alpaca.markets','path':'/v1beta1/options/quotes/latest','parameters':{'symbols':','.join(current_symbols[i:i+100]),'feed':'indicative'},'response_field':'quotes','max_pages':1,'scope':'current','unpaginated':True}
    require({t['task_id']:t for t in mp['tasks']}==wanted_market,'exact market task derivation')
    market_records={x['task_id']:x for x in mm['records']};require(set(market_records)==set(wanted_market),'all market tasks retained')
    mapping={};events={};quotes={};market_stats=Counter();market_attempts=[]
    for tid,task in wanted_market.items():
        record=market_records[tid];payloads,count,attempts=verified_record(record,task,mp['registered_at']);market_stats.update(count);market_attempts.extend(attempts)
        field=task['response_field']
        for symbol in task['parameters']['symbols'].split(','):
            key=(symbol,'quotes') if field=='quotes' else (task['date'],symbol,field);require(key not in mapping,'market request scope overlap');mapping[key]=record
        for payload in payloads:
            for symbol,value in payload[field].items():
                key=(symbol,'quotes') if field=='quotes' else (task['date'],symbol,field)
                if field=='quotes':require(key not in quotes,'duplicate latest quote');quotes[key]=value
                else:events.setdefault(key,[]).extend(value)
    require(all(ns(selected['selection_frozen_at'])<=ns(x['requested_at']) for x in market_attempts),'option price requested before frozen selection')
    historical=[]
    for row in selected['historical']:
        out={k:row[k] for k in ('case_id','date','underlying_symbol','type')};out.update(contract_symbol=row['contract']['symbol'] if row['contract'] else None,selection_status=row['status'],first_trade=None)
        if not row['contract']:out.update(bars_status='not_requested_no_selected_contract',trades_status='not_requested_no_selected_contract',bars_count=None,trades_count=None,request_ids=[])
        else:
            symbol=row['contract']['symbol'];lo=ns(datetime.fromisoformat(row['date']+'T10:00:00').replace(tzinfo=NY).isoformat());ids=[]
            for field,singular in [('bars','bar'),('trades','trade')]:
                key=(row['date'],symbol,field);record=mapping[key];ids.append(record['task_id']);valid=[];invalid=outside=0
                for item in events.get(key,[]):
                    try: stamp=ns(item.get('t'))
                    except (ValueError,TypeError,AttributeError):invalid+=1;continue
                    if lo<=stamp<lo+60*10**9:valid.append((stamp,item))
                    else:outside+=1
                out[field+'_count']=len(valid) if record['pages_complete'] else None
                out[field+'_status']='request_incomplete_or_failed' if not record['pages_complete'] else 'invalid_timestamp_or_outside_interval' if invalid or outside else 'events_observed' if valid else 'no_events_returned'
                out[singular+'_invalid_timestamp_count']=invalid;out[singular+'_outside_interval_count']=outside
                if field=='trades' and valid:
                    first=min(enumerate(valid),key=lambda x:(x[1][0],x[0]))[1][1];out['first_trade']={k:first[k] for k in TRADE if k in first}
            out['request_ids']=ids
        out.update(historical_bid_ask_available=False,trade_reference_is_execution_evidence=False);historical.append(out)
    actual_historical=read('historical-option-events.json');require(historical==actual_historical['rows'],'every historical derived event and first-trade row')
    actual_current=[]
    for row in selected['current']:
        out={k:row[k] for k in ('case_id','date','underlying_symbol','type')};out.update(selection_status=row['status'],contract_symbol=row['contract']['symbol'] if row['contract'] else None,feed='indicative',actual_opra_quote=False)
        if not row['contract']:out.update(status='not_requested_no_selected_contract',quote=None,source_request_id=None,requested_at=None,received_at=None)
        else:
            key=(row['contract']['symbol'],'quotes');record=mapping[key];quote=quotes.get(key);pages=record['pages']
            out.update(status='request_incomplete_or_failed' if not record['pages_complete'] else 'quote_observed' if isinstance(quote,dict) else 'quote_missing_or_invalid',quote={k:quote[k] for k in QUOTE if k in quote} if isinstance(quote,dict) else None,source_request_id=record['task_id'],requested_at=pages[0]['requested_at'] if pages else None,received_at=pages[-1]['received_at'] if pages else None)
        actual_current.append(out)
    require(actual_current==read('current-indicative-quotes.json')['rows'],'every current indicative quote/source row')
    records=sup_manifest['records']+listing['records']+mm['records'];require(source['records']==records,'all source manifest records')
    require(source['request_tasks']==len(records),'source task count')
    totals=Counter(sup_report['raw_verification'])+listing_stats+market_stats
    require(source['http_attempts']==totals['attempts'] and source['raw_response_bytes']==totals['bytes'],'source aggregate attempt/bytes')
    require(source['failed_or_incomplete_tasks']==sum(not x['pages_complete'] for x in records),'source failure count')
    for name,h in source['input_sha256'].items():require(sha(name)==h,'source input hash '+name)
    for name,h in source['output_sha256'].items():require(sha(name)==h,'source output hash '+name)
    report={'status':'passed','completed_at':datetime.now(timezone.utc).isoformat(),'scope':'Independent raw attempts, request/pagination provenance, supplemental references, all contract selection and source event/quote derivation. No affordability math or returns.',
      'supplement':sup_report,'counts':{'historical_discovery_cells':400,'current_discovery_cells':40,'historical_selected_or_unknown_rows':800,'current_selected_or_unknown_rows':80,'returned_contract_metadata_rows':contract_count,'historical_selected':sum(x['contract'] is not None for x in selected['historical']),'current_selected':sum(x['contract'] is not None for x in selected['current']),'market_tasks':len(wanted_market),'total_tasks':len(records),'raw_pages':totals['pages'],'http_attempts':totals['attempts'],'raw_bytes':totals['bytes'],'failed_or_incomplete_tasks':sum(not x['pages_complete'] for x in records)},
      'all_attempt_body_metadata_SHA_bytes_and_GET_params_verified':True,'all_page_request_chains_and_completion_statuses_verified':True,'all_880_contract_selection_rows_reproduced_without_option_price_or_quality_selection':True,'all_historical_event_counts_statuses_and_first_trade_rows_match':True,'all_current_indicative_quotes_and_request_timestamps_match':True,'all_final_source_output_hashes_verified':True,
      'chronology':{'supplement_generated_at':supp['generated_at'],'selection_frozen_at':selected['selection_frozen_at'],'market_plan_registered_at':mp['registered_at'],'first_option_price_request_at':min(x['requested_at'] for x in market_attempts)},
      'network_requests_by_audit':0,'orders_by_audit':0,'strategy_or_affordability_simulations_by_audit':0,'collector_or_math_module_imported':False,
      'limitations':['Current directory and retrieved contract metadata are retrospective capability inputs, not PIT historical chains.','Historical bars/trades provide observed price events, not historical bid/ask, fills or profitable paths. Current indicative values are not actual executable OPRA.','HTTP/pagination completeness establishes the returned request scope, not a complete historical market archive.'],
      'code_sha256':sha('independent-source-audit.py'),'input_sha256':{n:sha(n) for n in ('study-design.json','acquisition-plan.json','supplemental-reference-plan.json','source-manifest.json','contract-list-manifest.json','selected-contracts.json','market-request-plan.json','option-market-manifest.json','historical-option-events.json','current-indicative-quotes.json')},'elapsed_seconds':round(time.monotonic()-began,3)}
    save('independent-source-audit.json',report)
    (ROOT/'independent-source-audit.md').write_text('# Independent option source audit\n\nPassed. All raw attempt bodies and metadata, byte counts, GET parameter hashes, pagination chains, completion/failure status, 440 contract-discovery cells, 880 selected/unavailable rows, historical event counts and first-trade references, current indicative quotes, and final source hashes were independently reconstructed.\n\nThe SPY supplement affects only eleven originally missing prior-close references. Original study/sample/reference artifacts remain unchanged. Actual request timestamps place new option-price requests after the frozen contract selection.\n\nNo collector or mathematical analyzer was imported, and no network, order, affordability simulation or return simulation was run. Historical events are not bid/ask or fill evidence; indicative quotes remain distinct from OPRA. Current directory/metadata and returned request completeness do not establish PIT or archive completeness. Exact counts, chronology and hashes are in independent-source-audit.json.\n')
    print(json.dumps({'status':'passed','counts':report['counts'],'chronology':report['chronology'],'json_sha256':sha('independent-source-audit.json'),'elapsed_seconds':report['elapsed_seconds']},indent=2),flush=True)


if __name__=='__main__':
    if '--supplement' in sys.argv:
        report,_,_=supplement_audit();save('independent-supplement-review.json',report)
        (ROOT/'independent-supplement-review.md').write_text('# Independent SPY supplemental reference review\n\nPassed: all eleven exact prior-session references, including 2025-12-31, match complete raw SPY daily responses. Body/metadata hashes, byte counts, GET parameters, nanosecond local-day bounds, terminal pagination and derived close/timestamp mappings were independently verified.\n\nThe original study, sample universe, missing reference rows, calendar and earlier sampling review remain byte-identical. Archived pre-price plans and code retain the earlier version; the corrective plan changes documented deliverable/event-count fields only. The supplement does not change the forty-symbol universe. Full option-price/selection chronology is separately verified in the full source audit.\n\nRaw prior closes are capability references, not PIT discovery, adjusted moneyness, real quotes or fills. See independent-supplement-review.json for exact hashes.\n')
        print(json.dumps({'status':'passed','references':11,'raw_verification':report['raw_verification'],'json_sha256':sha('independent-supplement-review.json')},indent=2))
    else:full_audit()
