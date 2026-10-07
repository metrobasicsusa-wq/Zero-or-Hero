"""Offline, exhaustive stratification of a frozen parent event ledger."""
from pathlib import Path
from collections import defaultdict, Counter
from decimal import Decimal
from datetime import datetime, timezone
import json, gzip, io, hashlib, statistics, random, math
from return_distribution import compute_distribution

ROOT=Path(__file__).parent;PARENT=ROOT.parent/'s500-flush-rebound-20261007'
VARIANTS=tuple((d,f,h,c) for d in ['0.02','0.03'] for f in ['FIXED','REBOUND'] for h in ['30m','60m','close_minus_10m'] for c in [5,10,25])
PRIMARY=('0.02','REBOUND','60m',10);GROUPS=('market_down','market_not_down','unknown');BENCHMARKS=('SPY','QQQ')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text())
def save(p,x):p.write_text(json.dumps(x,ensure_ascii=False,separators=(',',':'))+'\n')
def vid(v):return '|'.join(map(str,v))
def stream(p):
    p.parent.mkdir(parents=True,exist_ok=True)
    return io.TextIOWrapper(gzip.GzipFile(filename=str(p),mode='wb',mtime=0),encoding='utf8')
def emit(f,row):f.write(json.dumps(row,separators=(',',':'))+'\n')
def rows(p):
    with gzip.open(p,'rt') as f:
        for line in f:yield json.loads(line)
def desc(xs):
    a=[float(x) for x in xs];n=len(a)
    return {'n':n,'mean':statistics.fmean(a) if n else None,'median':statistics.median(a) if n else None,'positive_fraction':sum(x>0 for x in a)/n if n else None,'minimum':min(a) if n else None,'maximum':max(a) if n else None}
def percentile(a,p):
    x=(len(a)-1)*p;i=math.floor(x);j=math.ceil(x)
    return a[i]+(a[j]-a[i])*(x-i)
def bootstrap(values,label):
    vals=[float(values[d]) for d in sorted(values)];n=len(vals)
    seedtext='s500-relative-flush-v1|'+label;digest=hashlib.sha256(seedtext.encode()).hexdigest();seed=int(digest[:16],16)
    out={'n_dates':n,'point_estimate':statistics.fmean(vals) if n else None,'lower':None,'upper':None,'confidence_level':0.95,'replicates':2000,'seed_label':seedtext,'seed_sha256':digest,'seed_integer':seed,'status':'insufficient_dates' if n<2 else 'computed','interpretation':'descriptive_date_cluster_not_serial_or_multiplicity_adjusted'}
    if n>=2:
        rng=random.Random(seed);sample=sorted(statistics.fmean(vals[rng.randrange(n)] for _ in range(n)) for _ in range(2000))
        out.update(lower=percentile(sample,.025),upper=percentile(sample,.975))
    return out
def negative_runs(calendar,means):
    runs=[];active=[];missing=[];zeros=[]
    for day in calendar:
        value=means.get(day)
        if value is None:missing.append(day)
        if value==0:zeros.append(day)
        if value is not None and value<0:active.append(day)
        elif active:runs.append(active);active=[]
    if active:runs.append(active)
    return {'maximum_observed_consecutive_negative_dates':max(map(len,runs),default=0),'negative_runs':runs,'missing_dates_break_runs':missing,'zero_dates_break_runs':zeros,'scope':'calendar-adjacent date_equal_target_net_means; not trade_loss_streak or account_drawdown'}
def summarize(subset):
    counts=Counter();status=Counter();pair=Counter();net=[];excess=[];bydate=defaultdict(lambda:{'net':[],'excess':[]});entry=[];trough=[]
    for r in subset:
        counts['selected_cases']+=1;status[r['signal_status']]+=1;pair[r['paired_control_status']]+=1
        counts['eligible_risk_set']+=r['risk_set_eligible'];counts['flush_true']+=r['flush_detected'] is True;counts['flush_false']+=r['flush_detected'] is False;counts['flush_unknown']+=r['flush_detected'] is None
        if r['net_return'] is not None:
            net.append({'case_id':r['case_id'],'net_return':r['net_return']});bydate[r['date']]['net'].append(r['net_return'])
        if r['matched_excess'] is not None:excess.append(r['matched_excess']);bydate[r['date']]['excess'].append(r['matched_excess'])
        if r['signal_status']=='signal':
            if r['entry_minute'] is not None:entry.append(r['entry_minute'])
            if r['t_min'] is not None:trough.append(r['t_min'])
            counts['signal_return_missing']+=r['net_return'] is None
    dates={k:{'net_mean':desc(v['net'])['mean'],'net_n':len(v['net']),'matched_excess_mean':desc(v['excess'])['mean'],'matched_excess_n':len(v['excess'])} for k,v in sorted(bydate.items())}
    counts['complete_target_returns']=len(net);counts['complete_matched_excess']=len(excess)
    return {'counts':dict(counts),'signal_status_counts':dict(status),'paired_control_status_counts':dict(pair),
        'target_net_distribution':compute_distribution(net),'matched_excess_distribution':{**desc(excess),'interpretation':'positive_excess_is_not_positive_trade_profit'},
        'date_equal_target_net':desc(v['net_mean'] for v in dates.values() if v['net_mean'] is not None),
        'date_equal_matched_excess':desc(v['matched_excess_mean'] for v in dates.values() if v['matched_excess_mean'] is not None),
        'signal_timing_descriptive':{'trough_minute':desc(trough),'entry_minute':desc(entry),'minute_zero':'09:30ET','limitation':'differentstocktroughtimes can produce different marketgroups even on samedate'},
        'date_means':dates}
def contrast(calendar,down,notdown,label,with_ci):
    dates=[];deltas={};count=Counter()
    for day in calendar:
        d=down.get(day,{});n=notdown.get(day,{})
        dv=d.get('matched_excess_mean');nv=n.get('matched_excess_mean')
        availability='both_groups' if dv is not None and nv is not None else ('down_only' if dv is not None else ('not_down_only' if nv is not None else 'neither_group'))
        count[availability]+=1;delta=nv-dv if availability=='both_groups' else None
        if delta is not None:deltas[day]=delta
        dates.append({'date':day,'status':availability,'down_complete_pairs':d.get('matched_excess_n',0),'not_down_complete_pairs':n.get('matched_excess_n',0),'down_mean':dv,'not_down_mean':nv,'difference_not_down_minus_down':delta})
    return {'date_status_counts':dict(count),'date_difference_distribution':desc(deltas.values()),'dates':dates,'bootstrap':bootstrap(deltas,label) if with_ci else None,'interpretation':'same-date paired-excess group difference; conditions on bothgroups withcompleteoutcomes; notcausal'}
def main():
    began=datetime.now(timezone.utc).isoformat();binding=read(ROOT/'pre-analysis-validation.json')
    assert binding['failed_tests']==0
    for n,h in binding['bound_sha256'].items():assert sha(ROOT/n)==h,n
    design=read(ROOT/'study-design.json')
    for n,h in design['parent_bound_sha256'].items():assert sha(PARENT/n)==h,n
    cmanifest=read(ROOT/'classification-output-manifest.json');classified=list(rows(ROOT/'classifications.jsonl.gz'))
    expected_class_sha=cmanifest['output']['sha256'];assert sha(ROOT/'classifications.jsonl.gz')==expected_class_sha
    classifications={c['case_id']:c for c in classified};assert len(classifications)==19519
    calendar=[c['date'] for c in read(PARENT/'calendar-2026-ytd.json')]
    inputs=[f for f in read(PARENT/'analysis-output-manifest.json')['files'] if f['path'].startswith('results/')]
    outdir=ROOT/'enriched-results';assert not outdir.exists();outdir.mkdir()
    buckets=defaultdict(list);masks=defaultdict(int);rowcount=0;benchcount=0
    for src in sorted(inputs,key=lambda x:x['path']):
        path=PARENT/src['path'];assert sha(path)==src['sha256'];dest=outdir/path.name
        with stream(dest) as handle:
            for r in rows(path):
                v=(r['drop_threshold'],r['family'],r['exit_horizon'],r['cost_bps_per_side']);bit=1<<VARIANTS.index(v);cid=r['case_id']
                assert not masks[cid]&bit;masks[cid]|=bit
                c=classifications[cid];assert c['date']==r['date'] and c['symbol']==r['symbol'];rowcount+=1
                base={k:r[k] for k in ['case_id','date','symbol','target_role','signal_status','paired_control_status','risk_set_eligible','flush_detected','net_return','matched_excess','entry_minute','exit_minute','missing_stage','missing_reason']}
                extra={**base,'variant_id':vid(v),'variant':list(v),'classification_by_benchmark':{b:{k:x.get(k) for k in ['classification','status','t_min']} for b,x in c['classification_by_benchmark'].items()},'full_classification_case_id':cid,'parent_result_file':src['path'],'actual_fill':False,'option_return':False,'wealth_path':False}
                emit(handle,extra)
                if r['target_role']!='stock':benchcount+=1;continue
                for b in BENCHMARKS:
                    cl=c['classification_by_benchmark'][b];group=cl['classification'] or 'unknown';assert group in GROUPS
                    buckets[(b,group,v)].append({**base,'t_min':cl.get('t_min')})
        save(ROOT/'analysis-progress.json',{'updated_at':datetime.now(timezone.utc).isoformat(),'parent_rows_read':rowcount,'last_file':src['path']})
        print(json.dumps({'parent_rows_read':rowcount,'last_file':src['path']}),flush=True)
    assert rowcount==702684 and benchcount==13752 and len(masks)==19519 and all(m==(1<<36)-1 for m in masks.values())
    summary=[];daily_path=ROOT/'date-statistics.jsonl.gz';primary=[];contrasts=[]
    with stream(daily_path) as daily:
        for b in BENCHMARKS:
            for v in VARIANTS:
                group_summaries={}
                for g in GROUPS:
                    data=buckets[(b,g,v)];by_month=defaultdict(list);by_half=defaultdict(list);by_date=defaultdict(list)
                    for r in data:by_month[r['date'][:7]].append(r);by_half['H1' if r['date']<'2026-07-01' else 'H2_to_cutoff'].append(r);by_date[r['date']].append(r)
                    total=summarize(data);group_summaries[g]=total
                    result={'benchmark':b,'group':g,'variant':list(v),'variant_id':vid(v),'is_primary_variant':v==PRIMARY,'all':total,
                      'months':{m:summarize(by_month[m]) for m in sorted(set(d[:7] for d in calendar))},'periods':{p:summarize(by_half[p]) for p in ['H1','H2_to_cutoff']}}
                    if v==PRIMARY:
                        nm={d:x['net_mean'] for d,x in total['date_means'].items() if x['net_mean'] is not None};em={d:x['matched_excess_mean'] for d,x in total['date_means'].items() if x['matched_excess_mean'] is not None}
                        result['primary_inference']={'net_date_bootstrap':bootstrap(nm,b+'|'+g+'|net'),'matched_excess_date_bootstrap':bootstrap(em,b+'|'+g+'|excess'),'negative_day_runs':negative_runs(calendar,nm)}
                        primary.append(result)
                    summary.append(result)
                    for day in calendar:
                        records=by_date[day];stats=total['date_means'].get(day,{})
                        emit(daily,{'benchmark':b,'group':g,'variant_id':vid(v),'date':day,'selected_cases':len(records),'signal_status_counts':dict(Counter(r['signal_status'] for r in records)),'complete_target_returns':stats.get('net_n',0),'complete_matched_excess':stats.get('matched_excess_n',0),'net_mean':stats.get('net_mean'),'matched_excess_mean':stats.get('matched_excess_mean')})
                ct=contrast(calendar,group_summaries['market_down']['date_means'],group_summaries['market_not_down']['date_means'],b+'|primary_contrast',v==PRIMARY)
                contrasts.append({'benchmark':b,'variant':list(v),'variant_id':vid(v),'is_primary_variant':v==PRIMARY,**ct})
        print(json.dumps({'group_summaries':len(summary),'contrasts':len(contrasts)}),flush=True)
    save(ROOT/'summary.json',{'study':design['id'],'created_at':datetime.now(timezone.utc).isoformat(),'groups':summary,'contrasts':contrasts,'parent_rows':rowcount,'classification_sha256':expected_class_sha,'variants_per_group':36,'group_count':len(summary),'interpretation':'Exploratory stockprice-proxy distribution, notoptions orfundedcapital; allfailures/unknowns retained.'})
    save(ROOT/'primary-results.json',{'groups':primary,'contrasts':[c for c in contrasts if c['is_primary_variant']]})
    outputs=list(outdir.glob('*.gz'))+[daily_path,ROOT/'summary.json',ROOT/'primary-results.json']
    save(ROOT/'analysis-output-manifest.json',{'began_at':began,'completed_at':datetime.now(timezone.utc).isoformat(),'parent_rows':rowcount,'cases':len(masks),'stock_rows':rowcount-benchcount,'stock_group_memberships':sum(len(x) for x in buckets.values()),'group_summary_count':len(summary),'contrasts':len(contrasts),'files':[{'path':p.relative_to(ROOT).as_posix(),'bytes':p.stat().st_size,'sha256':sha(p)} for p in outputs]})
if __name__=='__main__':main()
