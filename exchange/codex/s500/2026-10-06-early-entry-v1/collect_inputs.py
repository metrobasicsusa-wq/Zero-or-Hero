"""Hash-audited immutable-source collection for the frozen fixed10 union."""
from concurrent.futures import ThreadPoolExecutor,as_completed
from collections import defaultdict
from datetime import datetime,timedelta,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib
import importlib.util
import json
import re
import time

ROOT=Path(__file__).resolve().parent;BASE=ROOT.parent
NEWS=BASE/'s500-news-20261006';PARENT=BASE/'s500-ep-paths-20261006';NY=ZoneInfo('America/New_York')
FIELDS=('t','o','h','l','c','v','n','vw')

def read(path):return json.loads(Path(path).read_text())
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,obj):
    path=Path(path);tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(obj,ensure_ascii=False,separators=(',',':'))+'\n');tmp.replace(path)
def utc(day,hm):return datetime.fromisoformat(day+'T'+hm+':00').replace(tzinfo=NY).astimezone(timezone.utc).isoformat()
def regular_bounds(calendar,day):
    row=calendar[day]
    close=datetime.fromisoformat(day+'T'+row['close']).replace(tzinfo=NY)
    start=datetime.fromisoformat(day+'T'+row['open']).replace(tzinfo=NY)
    return start,close,int((close-start).total_seconds()//60)

def verify_record(folder,record,calendar,full_session):
    params=record['parameters'];day=datetime.fromisoformat(params['start']).astimezone(NY).date().isoformat()
    symbols=params['symbols'].split(',');start,close,minutes=regular_bounds(calendar,day)
    lo=datetime.fromisoformat(params['start']);hi=datetime.fromisoformat(params['end'])
    canonical=json.dumps({'route':record['route'],'parameters':params},sort_keys=True,separators=(',',':')).encode()
    assert record['request_sha256']==hashlib.sha256(canonical).hexdigest()
    assert record['route']=='/v2/stocks/bars' and params['feed']=='sip' and params['adjustment']=='raw' and params['timeframe']=='1Min'
    assert record['pages_complete'] and 'asof' not in params
    if full_session:assert lo==start and hi==close-timedelta(minutes=1)
    else:assert lo==start and hi==start+timedelta(minutes=92)
    market={s:{}for s in symbols};total=0;seen_tokens=set();request=dict(params)
    for index,page in enumerate(record['pages']):
        file=folder/'raw-bars'/record['task_id']/page['name'];data=file.read_bytes()
        assert hashlib.sha256(data).hexdigest()==page['sha256'] and len(data)==page['bytes']
        stored=json.loads(data)
        assert stored['base_request_sha256']==record['request_sha256'] and stored['page_number']==index
        request_hash=hashlib.sha256(json.dumps({'route':record['route'],'parameters':request},sort_keys=True,separators=(',',':')).encode()).hexdigest()
        assert stored['page_request_sha256']==request_hash
        response=stored['response'];assert set(response['bars'])<=set(symbols)
        count=0
        for symbol,bars in response['bars'].items():
            for bar in bars:
                assert re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00(?:\.0{1,9})?(?:Z|[+-]\d{2}:\d{2})',bar['t'])
                stamp=datetime.fromisoformat(bar['t'].replace('Z','+00:00'));assert lo<=stamp<=hi
                offset=str(int((stamp-start).total_seconds()//60));assert offset not in market[symbol]
                market[symbol][offset]=bar;count+=1
        assert count==page['records'];total+=count
        token=response['next_page_token']
        if index==len(record['pages'])-1:assert token is None
        else:
            assert isinstance(token,str)and token and token not in seen_tokens
            seen_tokens.add(token);request['page_token']=token
    return day,market,{'request_complete':True,'regular_window_covered':full_session,
        'request_ids':[record['task_id']],'scheduled_minutes':minutes},total

def verify_parent_sources(plan,calendar):
    expected=plan['input_sha256']
    source_files={'news_prefix_market':NEWS/'minute-market.json','news_prefix_manifest':NEWS/'bars-input-manifest.json',
      'parent_holding_market':PARENT/'holding-market.json','parent_holding_manifest':PARENT/'holding-input-manifest.json'}
    for key,file in source_files.items():assert sha(file)==expected[key],key
    prefix=read(NEWS/'minute-market.json');holding=read(PARENT/'holding-market.json')
    parent_meta={};audit={}
    for alias,folder,manifest_name,assembled,full in [('prefix',NEWS,'bars-input-manifest.json',prefix,False),
            ('parent_holding',PARENT,'holding-input-manifest.json',holding,True)]:
        manifest=read(folder/manifest_name);assert manifest['all_pages_complete'] and not manifest['failures']
        total=0;pages=0;seen=set()
        for record in manifest['records']:
            day,markets,meta,count=verify_record(folder,record,calendar,full);total+=count;pages+=len(record['pages'])
            for symbol,series in markets.items():
                assert(day,symbol)not in seen;seen.add((day,symbol))
                assert assembled.get(day,{}).get(symbol,{})==series
                if full:parent_meta[(day,symbol)]={**meta,'bars_returned':len(series),'source_dataset':'parent_ep_paths'}
        assert total==manifest['data_records']
        audit[alias]={'requests':len(manifest['records']),'pages_sha256_verified':pages,
           'raw_bars_verified_against_assembled':total,'requested_symbol_sessions':len(seen),
           'manifest_sha256':sha(folder/manifest_name),'terminal_tokens_verified_null':True}
    return prefix,holding,parent_meta,audit

def prepare_daily_actions(plan):
    wanted={r['symbol']for r in plan['cases']};broad=BASE/'s500-broad-20261006'
    inventory=read(BASE/'s500-orb-20261006/ep-candidate-inventory.json');daily={};daily_sources={}
    for file in sorted((broad/'raw').glob('batch-*.json')):
        key='raw/'+file.name;h=sha(file);assert h==inventory['inputs_sha256'][key];daily_sources[key]=h
        for symbol,bars in read(file)['bars'].items():
            if symbol not in wanted:continue
            assert symbol not in daily;series=daily.setdefault(symbol,{})
            for bar in bars:
                day=datetime.fromisoformat(bar['t'].replace('Z','+00:00')).astimezone(NY).date().isoformat()
                assert day not in series;series[day]=bar
    assert len(daily_sources)==104
    action_file=BASE/'s500-aggressive-20261006/company-actions.json';actions=read(action_file)
    assert sha(action_file)==read(PARENT/'cached-source-manifest.json')['parent_actions_sha256']
    by_symbol={s:[]for s in sorted(wanted)};unmapped=[]
    for i,action in enumerate(actions['rows']):
        symbols={s for s in [action.get('symbol'),action.get('new_symbol')]if s}
        if not symbols:unmapped.append({'source_row_index':i,'type':action['type'],'ex_date':action.get('ex_date')})
        for symbol in symbols&wanted:by_symbol[symbol].append({**action,'source_row_index':i})
    save(ROOT/'daily-market-private.json',daily);save(ROOT/'corporate-events.json',by_symbol)
    audit={'daily_raw_inputs_sha256':daily_sources,'daily_symbols':len(daily),'wanted_symbols':len(wanted),
       'missing_daily_symbols':sorted(wanted-set(daily)),'daily_bars':sum(len(v)for v in daily.values()),
       'daily-market-private.json':sha(ROOT/'daily-market-private.json'),'corporate-events.json':sha(ROOT/'corporate-events.json'),
       'parent_actions_sha256':sha(action_file),'corporate_action_source_rows':len(actions['rows']),
       'unmapped_actions_not_assigned_to_an_issuer':unmapped,'corporate_source_limitations':actions['notes'],
       'not_a_point_in_time_or_complete_market_universe':True}
    save(ROOT/'cached-source-manifest.json',audit);return audit

def collect_new(plan,core):
    tasks=[{**r,'start':utc(r['date'],r['start']),'end':utc(r['date'],r['end'])}for r in plan['tasks']]
    records=[];failures=[];began=time.monotonic()
    with ThreadPoolExecutor(max_workers=4)as pool:
        futures={pool.submit(core.fetch_task,'bars',task):task for task in tasks}
        for done,future in enumerate(as_completed(futures),1):
            task=futures[future]
            try:records.append({**future.result(),'date':task['date']})
            except Exception as error:failures.append({'task_id':task['task_id'],'error_type':type(error).__name__,'reason':str(error)[:140]})
            if done%20==0 or done==len(tasks):
                progress={'stage':'new_holding_collection','completed':done,'total':len(tasks),'failure_count':len(failures),
                    'elapsed_seconds':round(time.monotonic()-began),'updated_at':datetime.now(timezone.utc).isoformat()}
                save(ROOT/'input-progress.json',progress);print(json.dumps(progress),flush=True)
    result={'records':sorted(records,key=lambda r:r['task_id']),'failures':failures,'all_pages_complete':not failures,
        'request_tasks':len(tasks),'data_records':sum(p['records']for r in records for p in r['pages']),
        'raw_bytes':sum(p['bytes']for r in records for p in r['pages']),
        'completed_at':datetime.now(timezone.utc).isoformat(),'holding_input_design_sha256':sha(ROOT/'holding-input-design.json')}
    save(ROOT/'holding-input-manifest.json',result);return result

def main():
    plan=read(ROOT/'holding-input-design.json');expected=plan['input_sha256']
    for key in ['study-design.json','market-gates.json','prepare_inputs.py','collect_inputs.py']:
        assert sha(ROOT/key)==expected[key],key
    assert sha(NEWS/'download_inputs.py')==expected['read_only_transport']
    calendar={r['date']:r for r in plan['full_calendar']}
    save(ROOT/'input-progress.json',{'stage':'verify_parent_inputs','updated_at':datetime.now(timezone.utc).isoformat()})
    prefix,parent,parent_meta,parent_audit=verify_parent_sources(plan,calendar)
    cached_audit=prepare_daily_actions(plan)
    spec=importlib.util.spec_from_file_location('early_readonly_transport',NEWS/'download_inputs.py')
    core=importlib.util.module_from_spec(spec);spec.loader.exec_module(core);core.ROOT=ROOT
    manifest=collect_new(plan,core)
    wanted={(r['date'],r['symbol'])for r in plan['required_symbol_sessions']}
    market={};verified={};scope={};new_total=0;new_pages=0
    for binding in plan['required_symbol_sessions']:
        if binding['source']!='parent_ep_paths':continue
        day,symbol=binding['date'],binding['symbol'];market.setdefault(day,{})[symbol]=parent.get(day,{}).get(symbol,{})
        verified.setdefault(day,{})[symbol]=parent_meta[(day,symbol)];scope[(day,symbol)]='parent_ep_paths'
    for record in manifest['records']:
        day,series_by_symbol,meta,count=verify_record(ROOT,record,calendar,True);new_total+=count;new_pages+=len(record['pages'])
        for symbol,series in series_by_symbol.items():
            assert(day,symbol)in wanted and(day,symbol)not in scope
            market.setdefault(day,{})[symbol]=series;verified.setdefault(day,{})[symbol]={**meta,'bars_returned':len(series),'source_dataset':'new_full_session'}
            scope[(day,symbol)]='new_full_session'
    assert new_total==manifest['data_records']
    conflicts=[];overlap_sessions=0;overlap_offsets=0
    for day,symbol in sorted(wanted&set(scope)):
        if symbol not in prefix.get(day,{}):continue
        overlap_sessions+=1;old=prefix[day][symbol];new=market[day][symbol];differences=[]
        offsets={int(k)for k in old}|{int(k)for k in new if 0<=int(k)<=92}
        for offset in sorted(offsets):
            overlap_offsets+=1;a=old.get(str(offset));b=new.get(str(offset))
            if a is None or b is None:
                if a!=b:differences.append({'offset':offset,'difference':'added_in_holding'if a is None else'missing_in_holding'})
            else:
                fields=[k for k in FIELDS if(k in a)!=(k in b)or a.get(k)!=b.get(k)]
                if fields:differences.append({'offset':offset,'difference':'field_revision','fields':fields})
        if differences:conflicts.append({'conflict_id':day+'__'+symbol,'date':day,'symbol':symbol,
            'holding_source':scope[(day,symbol)],'differences':differences})
    conflict_pairs={(r['date'],r['symbol'])for r in conflicts};missing_pairs=wanted-set(scope);flags={}
    for case in plan['cases']:
        required={(d,case['symbol'])for d in case['session_dates']};bad=required&conflict_pairs;missing=required&missing_pairs
        if bad or missing:flags[case['candidate_id']]={'source_revision_conflict':bool(bad),
            'source_input_incomplete':bool(missing),'conflict_ids':[d+'__'+s for d,s in sorted(bad)],
            'incomplete_sessions':[{'date':d,'symbol':s}for d,s in sorted(missing)]}
    save(ROOT/'holding-market.json',market)
    save(ROOT/'verified-symbol-sessions.json',{'days':verified,'not_proof_of_dense_bars_or_all_historical_events':True})
    save(ROOT/'input-conflicts.json',{'candidate_flags':flags,'symbol_session_conflicts':conflicts,
        'compared_fields':list(FIELDS),'overlap_sessions':overlap_sessions,'overlap_offsets':overlap_offsets,
        'policy':plan['conflict_policy'],'returns_computed_before_classification':False})
    audit={'completed_at':datetime.now(timezone.utc).isoformat(),'parent_source_audits':parent_audit,
        'new_source_audit':{'requests':len(manifest['records']),'pages_sha256_verified':new_pages,'bar_records_verified':new_total,
          'terminal_tokens_verified_null':True,'request_failures':manifest['failures']},
        'required_symbol_sessions':len(wanted),'verified_symbol_sessions':len(scope),
        'reused_parent_symbol_sessions':sum(v=='parent_ep_paths'for v in scope.values()),
        'new_symbol_sessions':sum(v=='new_full_session'for v in scope.values()),
        'missing_symbol_sessions':[{'date':d,'symbol':s}for d,s in sorted(missing_pairs)],
        'overlap_sessions_checked':overlap_sessions,'overlap_offsets_checked':overlap_offsets,
        'conflicting_symbol_sessions':len(conflicts),'affected_candidate_flags':len(flags),
        'all_required_request_scopes_verified':not missing_pairs,'source_conflicts_resolved_by_replacing_prices':False,
        'daily_cache_audit':cached_audit,
        'input_sha256':{'holding-input-design.json':sha(ROOT/'holding-input-design.json'),**expected},
        'output_sha256':{n:sha(ROOT/n)for n in ['holding-market.json','daily-market-private.json','corporate-events.json',
            'verified-symbol-sessions.json','input-conflicts.json','holding-input-manifest.json','cached-source-manifest.json']},
        'returns_computed':False,'broker_orders_sent':0}
    save(ROOT/'source-verification.json',audit)
    save(ROOT/'input-progress.json',{'stage':'complete','completed_at':audit['completed_at'],
        'required_symbol_sessions':len(wanted),'new_bar_records':new_total,'conflicting_symbol_sessions':len(conflicts),
        'missing_symbol_sessions':len(missing_pairs)})
    print(json.dumps({'complete':True,'new_bars':new_total,'conflicting_sessions':len(conflicts),'affected_cases':len(flags)}),flush=True)

if __name__=='__main__':main()
