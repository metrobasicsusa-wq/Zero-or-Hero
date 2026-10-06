"""Independently reconcile frozen gate outputs and stored source bytes offline.

No production gate function or wealth simulator is imported.
"""
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parent
NEWS=ROOT.parent/'s500-news-20261006'
NY=ZoneInfo('America/New_York')

def read(p): return json.loads(p.read_text())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def digest(obj): return hashlib.sha256(json.dumps(obj,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def value(v):
    assert not isinstance(v,bool) and isinstance(v,(int,float))
    n=D(str(v));assert n.is_finite();return n

def bar(series,offset,day,open_only=False):
    if offset in series and str(offset) in series:return None
    b=series.get(str(offset),series.get(offset))
    if not isinstance(b,dict):return None
    try:
        assert re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00(?:\.0{1,9})?(?:Z|[+-]\d{2}:\d{2})',b['t'])
        t=datetime.fromisoformat(b['t'].replace('Z','+00:00'))
        expected=datetime.fromisoformat(day+'T09:30:00').replace(tzinfo=NY)+timedelta(minutes=offset)
        assert t.tzinfo and t==expected
        v={k:value(b[k]) for k in (['o'] if open_only else ['o','h','l','c','v'])}
        assert v['o']>0
        if not open_only:
            assert min(v[k] for k in ['o','h','l','c'])>0 and v['v']>=0
            assert v['l']<=min(v['o'],v['c'])<=max(v['o'],v['c'])<=v['h']
        return v
    except (AssertionError,KeyError,TypeError,ValueError):return None

def expected(c,series,n):
    prior=value(c['prior_close_adjusted']);liq=value(c['prior20_median_dollar_volume']);mean=value(c['prior20_mean_adjusted_daily_volume'])
    assert c['prior_session']<c['date'] and prior>0 and liq>0 and mean>0
    if prior<2 or liq<10000000:return 'prior_liquidity_gate_failed',False,None,None
    opening=[bar(series,m,c['date']) for m in range(n)]
    if any(v is None for v in opening):return 'opening_range_data_unknown',None,None,None
    high=max(b['h'] for b in opening);low=min(b['l'] for b in opening);vol=sum((b['v'] for b in opening),D(0))
    meta={'volume':vol,'high':high,'low':low,'open':opening[0]['o'],'close':opening[-1]['c']}
    if meta['open']<prior*D('1.10'):return 'opening_gap_gate_failed',False,meta,None
    if vol*30<mean*n:return 'opening_range_volume_gate_failed',False,meta,None
    for m in range(n,90):
        b=bar(series,m,c['date'])
        if b is None:return 'signal_data_unknown',None,meta,None
        if b['c']<=high:continue
        sig={'offset':m,'close':b['c']}
        ent=bar(series,m+2,c['date'],True)
        if ent is None:return 'entry_reference_unknown',None,meta,sig
        sig['entry']=ent['o']
        if ent['o']<=low:return 'incomplete_gap_through_entry_stop',None,meta,sig
        return 'signal_and_entry_reference_ready',True,meta,sig
    return 'no_breakout_before_11',False,meta,None

def main():
    design=read(ROOT/'study-design.json');gate=read(ROOT/'market-gates.json');summary=read(ROOT/'gate-summary.json')
    candidates=read(NEWS/'candidates.json')['candidates'];market=read(NEWS/'minute-market.json');manifest=read(NEWS/'bars-input-manifest.json')
    assert len(candidates)==len({c['candidate_id'] for c in candidates})==1079
    assembled={};pages=0;records=0
    for task in manifest['records']:
        folder=NEWS/'raw-bars'/task['task_id'];params=dict(task['parameters']);base=digest({'route':task['route'],'parameters':params})
        assert base==task['request_sha256']
        stored=read(folder/'complete.json')
        for key in ['stage','task_id','route','parameters','request_sha256','pages','pages_complete']:assert task[key]==stored[key]
        day=task['task_id'].split('__')[0];seen=set();token=None
        for i,page in enumerate(task['pages']):
            p=folder/page['name'];assert sha(p)==page['sha256'] and p.stat().st_size==page['bytes']
            data=read(p);assert data['base_request_sha256']==base and data['page_number']==i
            assert data['page_request_sha256']==digest({'route':task['route'],'parameters':params})
            response=data['response'];count=sum(len(v) for v in response['bars'].values());assert count==page['records']
            for sym,bars in response['bars'].items():
                assert sym in task['parameters']['symbols'].split(',')
                for b in bars:
                    t=datetime.fromisoformat(b['t'].replace('Z','+00:00')).astimezone(NY)
                    assert t.date().isoformat()==day and t.second==t.microsecond==0
                    off=t.hour*60+t.minute-570;assert 0<=off<=92
                    dest=assembled.setdefault(day,{}).setdefault(sym,{})
                    assert str(off) not in dest;dest[str(off)]=b
            token=response['next_page_token'];pages+=1;records+=count
            if token is None:assert i==len(task['pages'])-1
            else:
                assert isinstance(token,str) and token and token not in seen;seen.add(token);params['page_token']=token
        assert token is None and task['pages_complete'] is True
    assert assembled==market
    counters={};passed_union=set();checked=0;or30_checks=0
    old={r['candidate_id']:r for r in read(NEWS/'market-gates.json')['rows']}
    for n in [5,15,30]:
        family='OR'+str(n);rows=gate['families'][family]['rows'];assert len(rows)==1079
        assert [r['candidate_id'] for r in rows]==[c['candidate_id'] for c in candidates]
        statuses=Counter();counts=Counter()
        for c,r in zip(candidates,rows):
            expected_status,passed,opening,sig=expected(c,market[c['date']][c['symbol']],n)
            assert r['market_gate_status']==expected_status and r['market_gate_pass'] is passed,(family,c['candidate_id'])
            assert r['family']==family and r['opening_range_minutes']==n
            statuses[expected_status]+=1;counts['unknown' if passed is None else ('pass' if passed else 'fail')]+=1
            if passed:passed_union.add(c['candidate_id'])
            if opening:
                ex=r['opening_range']['exact_values']
                for key,v in [('volume',opening['volume']),('or_range_high',opening['high']),('or_range_low',opening['low']),('regular_open',opening['open']),('or_range_close',opening['close'])]:assert D(ex[key])==v
                assert r['opening_range']['source_timestamps']==[market[c['date']][c['symbol']][str(m)]['t'] for m in range(n)]
            if sig:
                s=r['signal'];assert s['minute_offset']==sig['offset'] and D(s['close_exact'])==sig['close']
                t=datetime.fromisoformat(c['date']+'T09:30:00').replace(tzinfo=NY)+timedelta(minutes=sig['offset']+1)
                assert datetime.fromisoformat(s['completed_at_utc'])==t
                assert s['planned_entry_offset']==sig['offset']+2
                if 'entry' in sig:
                    assert D(r['entry_reference']['open_exact'])==sig['entry'] and D(r['entry_reference']['stop_exact'])==opening['low']
                assert [b['offset'] for b in r['observed_signal_bars']]==list(range(n,sig['offset']+1))
            else:assert r['signal'] is None
            if n==30:
                p=old[c['candidate_id']];assert p['market_gate_pass'] is passed
                for section,keys in [('signal',['minute_offset','close_exact','completed_at_utc','planned_entry_time_utc']),('entry_reference',['minute_offset','open_exact','stop_exact','time_utc','gap_through_stop'])]:
                    assert (p[section] is None)==(r[section] is None)
                    if p[section]:
                        for key in keys:assert p[section][key]==r[section][key]
                or30_checks+=1
            checked+=1
        assert dict(statuses)==gate['families'][family]['status_counts']==summary['families'][family]['status_counts']
        assert dict(counts)==gate['families'][family]['counts']==summary['families'][family]['counts'];counters[family]=dict(counts)
    assert checked==3237 and len(passed_union)==275==summary['all_ready_union_count']
    validation=read(ROOT/'gate-pre-run-validation.json')
    assert datetime.fromisoformat(design['registered_at'])<datetime.fromisoformat(gate['generated_at'])
    assert summary['gate_sha256']==sha(ROOT/'market-gates.json')
    for name,want in gate['input_sha256'].items():assert sha(ROOT/name)==want
    result={'audited_at':datetime.now(timezone.utc).isoformat(),'status':'passed','gate_rows_checked':checked,'source_pages_rehashed_and_request_chain_checked':pages,'source_records_and_assembly_checked':records,'families':counters,'union_ready':len(passed_union),'or30_parent_signal_entry_comparisons':or30_checks,'news_filter_used':False,'new_returns_computed':False,'new_network_calls':0,'source_scope':'Current historical returned-time data; no point-in-time completeness assertion','sha256':{n:sha(ROOT/n) for n in ['study-design.json','market-gates.json','gate-summary.json','gate-pre-run-validation.json','early_gates.py','independent_actual_gates.py']}}
    (ROOT/'independent-actual-gate-audit.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))

if __name__=='__main__':main()
