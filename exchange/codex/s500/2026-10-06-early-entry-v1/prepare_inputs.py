"""Freeze all-ready fixed10 input union before new holding data or returns."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json

ROOT=Path(__file__).resolve().parent
NEWS=ROOT.parent/'s500-news-20261006'
PARENT=ROOT.parent/'s500-ep-paths-20261006'

def read(path): return json.loads(Path(path).read_text())
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,obj):
    path=Path(path);tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(obj,ensure_ascii=False,separators=(',',':'))+'\n');tmp.replace(path)

def prepare():
    design=read(ROOT/'study-design.json');gates=read(ROOT/'market-gates.json')
    assert design['variant_grid']['exit_mode']==['fixed10']
    assert design['period'][1]=='2026-10-05' and gates['returns_computed'] is False
    expected=design['input_sha256']
    for key,file in [('news/candidates.json',NEWS/'candidates.json'),('news/minute-market.json',NEWS/'minute-market.json'),
                     ('news/bars-input-manifest.json',NEWS/'bars-input-manifest.json'),
                     ('news/download_inputs.py',NEWS/'download_inputs.py'),
                     ('paths/holding-input-design.json',PARENT/'holding-input-design.json'),
                     ('paths/holding-input-manifest.json',PARENT/'holding-input-manifest.json'),
                     ('paths/holding-market.json',PARENT/'holding-market.json')]:
        assert sha(file)==expected[key],key
    calendar=read(PARENT/'holding-input-design.json')['full_calendar'];dates=[r['date']for r in calendar]
    calendar_by_date={r['date']:r for r in calendar}
    original=read(NEWS/'candidates.json')['candidates'];original_keys={(r['date'],r['symbol'])for r in original}
    assert len(original)==len(original_keys)==1079
    ready=defaultdict(list);family_counts={}
    for family in ('OR5','OR15','OR30'):
        rows=gates['families'][family]['rows']
        assert len(rows)==1079 and {(r['date'],r['symbol'])for r in rows}==original_keys
        family_counts[family]=sum(r['market_gate_pass']is True for r in rows)
        for row in rows:
            if row['market_gate_pass']is True:ready[(row['date'],row['symbol'])].append(family)
    required=set();cases=[]
    for (day,symbol),families in sorted(ready.items()):
        index=dates.index(day);window=[d for d in dates[index:index+10] if d<=design['period'][1]]
        required.update((d,symbol)for d in window)
        cases.append({'candidate_id':day+'__'+symbol,'symbol':symbol,'entry_date':day,
                      'ready_families':families,'session_dates':window,'entry_session_counts_as':1,
                      'cutoff_censored':len(window)<10})
    parent_manifest=read(PARENT/'holding-input-manifest.json');assert parent_manifest['all_pages_complete']
    parent_pairs={}
    for row in parent_manifest['records']:
        assert row['pages_complete'];day=row['date'];params=row['parameters']
        end=(datetime.fromisoformat(day+'T'+calendar_by_date[day]['close'])-timedelta(minutes=1)).strftime('%H:%M')
        # Previous verified collector issued full regular windows; confirm scope.
        from zoneinfo import ZoneInfo
        ny=ZoneInfo('America/New_York')
        assert datetime.fromisoformat(params['start']).astimezone(ny).strftime('%H:%M')==calendar_by_date[day]['open']
        assert datetime.fromisoformat(params['end']).astimezone(ny).strftime('%H:%M')==end
        for symbol in params['symbols'].split(','):
            assert (day,symbol)not in parent_pairs;parent_pairs[(day,symbol)]=row['task_id']
    missing=required-set(parent_pairs);new_by_day=defaultdict(list)
    for day,symbol in sorted(missing):new_by_day[day].append(symbol)
    tasks=[];new_ids={}
    for day,symbols in sorted(new_by_day.items()):
        end=(datetime.fromisoformat(day+'T'+calendar_by_date[day]['close'])-timedelta(minutes=1)).strftime('%H:%M')
        for i in range(0,len(symbols),25):
            task_id='early__'+day+'__%03d'%(i//25)
            tasks.append({'task_id':task_id,'date':day,'symbols':symbols[i:i+25],
                          'start':calendar_by_date[day]['open'],'end':end,'timeframe':'1Min'})
            for symbol in symbols[i:i+25]:new_ids[(day,symbol)]=task_id
    bindings=[{'date':d,'symbol':s,'source':'parent_ep_paths' if(d,s)in parent_pairs else'new_full_session',
               'request_id':parent_pairs.get((d,s),new_ids.get((d,s)))}for d,s in sorted(required)]
    input_hashes={'study-design.json':sha(ROOT/'study-design.json'),'market-gates.json':sha(ROOT/'market-gates.json'),
       'news_candidates':sha(NEWS/'candidates.json'),'news_prefix_market':sha(NEWS/'minute-market.json'),
       'news_prefix_manifest':sha(NEWS/'bars-input-manifest.json'),'parent_holding_market':sha(PARENT/'holding-market.json'),
       'parent_holding_manifest':sha(PARENT/'holding-input-manifest.json'),'parent_calendar_design':sha(PARENT/'holding-input-design.json'),
       'read_only_transport':sha(NEWS/'download_inputs.py'),'prepare_inputs.py':sha(ROOT/'prepare_inputs.py'),
       'collect_inputs.py':sha(ROOT/'collect_inputs.py')}
    result={'schema_version':1,'registered_at':datetime.now(timezone.utc).isoformat(),
       'status':'fixed_before_new_holding_requests_and_returns','cutoff':design['period'][1],
       'full_calendar':calendar,'cases':cases,'required_symbol_sessions':bindings,'tasks':tasks,
       'case_counts_by_family':family_counts,'unique_ready_cases':len(cases),'holding_sessions':10,
       'required_pair_count':len(required),'reused_parent_pair_count':len(required&set(parent_pairs)),
       'new_pair_count':len(missing),'new_task_count':len(tasks),'input_sha256':input_hashes,
       'source_version_policy':'Keep immutable parent full-session sources. New requests only missing complete symbol-sessions; preserve same SIP/raw/default-asof parameter semantics. Never merge alternate minute versions.',
       'conflict_policy':{'compare':'All corresponding cached prefix offsets0..92, t/o/h/l/c/v/n/vw; addition/deletion counts as a conflict.',
          'candidate_flags':'input-conflicts.json candidate_flags[date__symbol].source_revision_conflict=true for any conflicting session in its predeclared10session window; preserve every ready member and fail it explicitly before simulated entry.',
          'incomplete_requests':'source_input_incomplete=true for any session with no successful complete response; preserve raw partial pages and fail affected cases explicitly.',
          'selection':'No outcome-based selection or favorable source replacement; conflict classification precedes any return run.'}}
    file=ROOT/'holding-input-design.json'
    if file.exists():
        existing=read(file);assert existing['input_sha256']==input_hashes and existing['required_symbol_sessions']==bindings
        return existing
    save(file,result)
    return result

if __name__=='__main__':
    r=prepare();print(json.dumps({k:r[k]for k in ['unique_ready_cases','required_pair_count','reused_parent_pair_count','new_pair_count','new_task_count','registered_at']}))
