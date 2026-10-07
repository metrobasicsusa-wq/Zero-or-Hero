"""Independently recompute every summary cell, daily row and date-cluster interval."""
from pathlib import Path
from collections import Counter,defaultdict
import gzip,hashlib,json,math,random,statistics,sys
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parent
VECTORS=['returns','matched_excesses','SPY_returns','SPY_excesses','QQQ_returns','QQQ_excesses']
COUNTERS=['counts','signal_status_counts','missing_stage_counts','missing_reason_counts','paired_control_status_counts']
BASECOUNTS=['selected_cases','eligible_risk_set','ineligible_or_unknown_risk_set','flush_true','flush_false','flush_unknown','complete_returns','signal_return_missing','complete_matched_excess']
STATUSES=['signal','no_flush','no_rebound','unknown']
NAMESPACE='s500-flush-rebound-20261007-date-mean-excess-v1'
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def eq(a,b,label=''):
    if isinstance(a,dict):
        assert set(a)==set(b),(label,set(a)^set(b))
        for k in a:eq(a[k],b[k],label+'/'+str(k))
    elif isinstance(a,list):
        assert len(a)==len(b),(label,len(a),len(b))
        for i,(x,y) in enumerate(zip(a,b)):eq(x,y,label+'/'+str(i))
    elif isinstance(a,float) or isinstance(b,float):
        assert a is not None and b is not None and math.isclose(float(a),float(b),abs_tol=1e-12,rel_tol=1e-10),(label,a,b)
    else:assert a==b,(label,a,b)
def desc(v):
    n=len(v);s=sorted(v)
    median=(s[n//2] if n%2 else (s[n//2-1]+s[n//2])/2) if n else None
    return {'n':n,'mean':math.fsum(v)/n if n else None,'median':median,'positive_fraction':sum(x>0 for x in v)/n if n else None,
        'zero_count':sum(x==0 for x in v),'negative_count':sum(x<0 for x in v),'minimum':min(v) if n else None,'maximum':max(v) if n else None}
def daynew():
    return {'counts':Counter({k:0 for k in BASECOUNTS}),'signal_status_counts':Counter({k:0 for k in STATUSES}),
        **{k:Counter() for k in COUNTERS[2:]},**{k:[] for k in VECTORS}}
def slice_stats(days,dates):
    counters={k:Counter() for k in COUNTERS};vectors={k:[] for k in VECTORS};datevectors={k:[] for k in VECTORS}
    for d in dates:
        row=days.get(d,daynew())
        for k in COUNTERS:counters[k].update(row[k])
        for k in VECTORS:
            vectors[k]+=row[k]
            if row[k]:datevectors[k].append(math.fsum(row[k])/len(row[k]))
    return {'calendar_dates_in_slice':len(dates),**{k:dict(v) for k,v in counters.items()},
        'event_weighted':{k:desc(v) for k,v in vectors.items()},'date_weighted':{k:desc(v) for k,v in datevectors.items()}}
def main():
    summary_path=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'statistics/summary.json'
    summary=read(summary_path);calendar=[r['date'] for r in read(ROOT/'calendar-2026-ytd.json')]
    eq(summary['calendar_dates'],calendar)
    collected=defaultdict(dict);cases=set();rows=0
    for path in sorted((ROOT/'results').glob('*.gz')):
        with gzip.open(path,'rt') as f:
            for line in f:
                r=json.loads(line);rows+=1;cases.add(r['case_id'])
                group='stock' if r['target_role']=='stock' else r['symbol']
                key=(group,r['drop_threshold'],r['family'],r['exit_horizon'],r['cost_bps_per_side'])
                d=collected[key].setdefault(r['date'],daynew());n=d['counts'];n['selected_cases']+=1
                n['eligible_risk_set' if r['risk_set_eligible'] else 'ineligible_or_unknown_risk_set']+=1
                n['flush_true' if r['flush_detected'] is True else 'flush_false' if r['flush_detected'] is False else 'flush_unknown']+=1
                d['signal_status_counts'][r['signal_status']]+=1
                for field in ['missing_stage','missing_reason']:
                    if r[field]:d[field+'_counts'][r[field]]+=1
                d['paired_control_status_counts'][r['paired_control_status']]+=1
                if r['net_return'] is not None:d['returns'].append(float(r['net_return']));n['complete_returns']+=1
                elif r['signal_status']=='signal':n['signal_return_missing']+=1
                if r['matched_excess'] is not None:d['matched_excesses'].append(float(r['matched_excess']));n['complete_matched_excess']+=1
                for s in ['SPY','QQQ']:
                    b=r['benchmarks'].get(s,{})
                    if b.get('net_return') is not None:d[s+'_returns'].append(float(b['net_return']))
                    if b.get('excess_return') is not None:d[s+'_excesses'].append(float(b['excess_return']))
    eq(summary['selected_case_count'],len(cases));eq(summary['ledger_row_count'],rows);eq(rows,702684)
    checked=Counter();indexed={}
    for result in summary['variants']:
        v=result['variant'];key=(result['group'],v['drop_threshold'],v['family'],v['exit_horizon'],v['cost_bps_per_side'])
        assert key not in indexed;indexed[key]=result;days=collected[key]
        eq(result['all'],slice_stats(days,calendar),str(key)+'/all');checked['all_summaries']+=1
        for month,item in result['months'].items():eq(item,slice_stats(days,[d for d in calendar if d[:7]==month]),str(key)+'/'+month);checked['monthly_summaries']+=1
        for name,item in result['periods'].items():
            subset=[d for d in calendar if (d<'2026-07-01')==(name=='H1')]
            eq(item,slice_stats(days,subset),str(key)+'/'+name);checked['period_summaries']+=1
        b=result['matched_excess_date_cluster_bootstrap']
        values=[math.fsum(days[d]['matched_excesses'])/len(days[d]['matched_excesses']) for d in sorted(days) if days[d]['matched_excesses']]
        seedhash=hashlib.sha256((NAMESPACE+'|'+result['group']+'|'+result['variant_id']).encode()).hexdigest();seed=int(seedhash[:16],16)
        eq(b['seed_label_sha256'],seedhash);eq(b['seed_integer'],seed);eq(b['n_dates'],len(values));eq(b['replicates'],2000)
        eq(b['point_estimate'],math.fsum(values)/len(values) if values else None)
        if len(values)<2:eq(b['status'],'insufficient_dates');eq(b['lower'],None);eq(b['upper'],None)
        else:
            rng=random.Random(seed);n=len(values);replicates=[]
            for _ in range(2000):replicates.append(math.fsum(values[rng.randrange(n)] for j in range(n))/n)
            replicates.sort()
            for field,q in [('lower',.025),('upper',.975)]:
                at=(len(replicates)-1)*q;lo=int(at);hi=math.ceil(at);expected=replicates[lo]+(replicates[hi]-replicates[lo])*(at-lo)
                eq(b[field],expected,str(key)+'/'+field)
            eq(b['status'],'computed')
        checked['bootstrap_records']+=1
    assert len(indexed)==108 and set(indexed)==set(collected)
    with (summary_path.parent/'daily-statistics.jsonl').open() as f:
        seen=set()
        for line in f:
            r=json.loads(line);match=[(key,result) for key,result in indexed.items() if result['group']==r['group'] and result['variant_id']==r['variant_id']]
            assert len(match)==1;key,_=match[0];ident=(key,r['date']);assert ident not in seen;seen.add(ident)
            day=collected[key].get(r['date'],daynew())
            for name in COUNTERS:eq(r[name],dict(day[name]),str(ident)+'/'+name)
            for name in VECTORS:eq(r[name],desc(day[name]),str(ident)+'/'+name)
            checked['daily_records']+=1
    assert len(seen)==108*191
    primary=[x for x in summary['variants'] if x['is_registered_primary']];assert len(primary)==1
    eq(read(summary_path.parent/'primary-inference.json'),primary[0])
    result={'audit_version':'independent_summary_audit_v1','completed_at_utc':datetime.now(timezone.utc).isoformat(),'passed':True,'checked':dict(checked),
        'ledger_rows_read':rows,'unique_cases':len(cases),'summary_sha256':sha(summary_path),'auditor_code_sha256':sha(Path(__file__)),
        'independent_reconstruction':'All count/status denominators; all means, medians, signs, extrema in108groupvariants xall/months/halves;20628dailyrows; everyseedand2000whole-datebootstrapCI. No production statistic imports.',
        'tolerance':'Float summaries compared abs1e-12 and rel1e-10 against exact-decimal-derived ledger; signal and money arithmetic verified separately with exactDecimal.',
        'limitations':['Date clustering handles within-date co-occurrence, not necessarily serial dependence across dates.','Intervals remain descriptive after retrospective hypothesis formation and multivariant exploration.']}
    (ROOT/'independent-summary-audit.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result),flush=True)
if __name__=='__main__':main()
