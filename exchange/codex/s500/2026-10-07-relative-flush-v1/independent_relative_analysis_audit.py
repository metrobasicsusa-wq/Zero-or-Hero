"""Independent base-ledger integrity, strata statistics, contrasts and date-risk audit.

The separate independent payoff auditor verifies all target distribution ratios
and tails. This module never imports the production classifier or summarizer.
"""
from pathlib import Path
from collections import Counter,defaultdict
from datetime import datetime,timezone
from itertools import zip_longest
import gzip,hashlib,json,math,random,statistics
ROOT=Path(__file__).resolve().parent;OLD=ROOT.parent/'s500-flush-rebound-20261007'
PRIMARY=('0.02','REBOUND','60m',10);BENCHMARKS=['SPY','QQQ'];GROUPS=['market_down','market_not_down','unknown']
read=lambda p:json.loads(p.read_text());sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def lines(p):
    with gzip.open(p,'rt') as f:
        for line in f:
            if line.strip():yield json.loads(line)
def equal(a,b,label=''):
    if isinstance(a,dict):
        assert set(a)==set(b),(label,set(a)^set(b))
        for k in a:equal(a[k],b[k],label+'/'+str(k))
    elif isinstance(a,list):
        assert len(a)==len(b),(label,len(a),len(b))
        for i,(x,y) in enumerate(zip(a,b)):equal(x,y,label+'/'+str(i))
    elif isinstance(a,float) or isinstance(b,float):
        assert a is not None and b is not None and math.isclose(float(a),float(b),abs_tol=1e-12,rel_tol=1e-10),(label,a,b)
    else:assert a==b,(label,a,b)
def simple(values):
    x=[float(v) for v in values];n=len(x);s=sorted(x)
    return {'n':n,'mean':math.fsum(x)/n if n else None,'median':s[n//2] if n%2 else (s[n//2-1]+s[n//2])/2 if n else None,
      'positive_fraction':sum(v>0 for v in x)/n if n else None,'minimum':min(x) if n else None,'maximum':max(x) if n else None}
def expected_summary(records):
    counts=Counter();signals=Counter();pairs=Counter();nets=[];excesses=[];bydate=defaultdict(lambda:{'net':[],'excess':[]});trough=[];entry=[]
    for r in records:
        counts['selected_cases']+=1;signals[r['signal_status']]+=1;pairs[r['paired_control_status']]+=1
        counts['eligible_risk_set']+=int(r['risk_set_eligible']);counts['flush_true']+=int(r['flush_detected'] is True);counts['flush_false']+=int(r['flush_detected'] is False);counts['flush_unknown']+=int(r['flush_detected'] is None)
        if r['net_return'] is not None:nets.append(r['net_return']);bydate[r['date']]['net'].append(r['net_return'])
        if r['matched_excess'] is not None:excesses.append(r['matched_excess']);bydate[r['date']]['excess'].append(r['matched_excess'])
        if r['signal_status']=='signal':
            counts['signal_return_missing']+=int(r['net_return'] is None)
            if r['entry_minute'] is not None:entry.append(r['entry_minute'])
            if r['t_min'] is not None:trough.append(r['t_min'])
    counts['complete_target_returns']=len(nets);counts['complete_matched_excess']=len(excesses)
    dates={d:{'net_mean':simple(v['net'])['mean'],'net_n':len(v['net']),'matched_excess_mean':simple(v['excess'])['mean'],'matched_excess_n':len(v['excess'])} for d,v in bydate.items()}
    return {'counts':dict(counts),'signal_status_counts':dict(signals),'paired_control_status_counts':dict(pairs),
       'matched_excess_distribution':{**simple(excesses),'interpretation':'positive_excess_is_not_positive_trade_profit'},
       'date_equal_target_net':simple(v['net_mean'] for v in dates.values() if v['net_mean'] is not None),
       'date_equal_matched_excess':simple(v['matched_excess_mean'] for v in dates.values() if v['matched_excess_mean'] is not None),
       'signal_timing_descriptive':{'trough_minute':simple(trough),'entry_minute':simple(entry),'minute_zero':'09:30ET','limitation':'differentstocktroughtimes can produce different marketgroups even on samedate'},'date_means':dates}
def check_summary(actual,records,label):
    expected=expected_summary(records)
    for field,value in expected.items():equal(actual[field],value,label+'/'+field)
    equal(actual['target_net_distribution']['n'],len([r for r in records if r['net_return'] is not None]),label+'/target_n')
    return expected

def audit_bootstrap(actual,values,label):
    seedtext='s500-relative-flush-v1|'+label;digest=hashlib.sha256(seedtext.encode()).hexdigest();seed=int(digest[:16],16)
    x=[float(values[d]) for d in sorted(values)];n=len(x)
    assert actual['seed_label']==seedtext and actual['seed_sha256']==digest and actual['seed_integer']==seed
    assert actual['n_dates']==n and actual['replicates']==2000 and actual['confidence_level']==.95
    equal(actual['point_estimate'],math.fsum(x)/n if n else None)
    if n<2:assert actual['status']=='insufficient_dates' and actual['lower'] is None and actual['upper'] is None;return
    assert actual['status']=='computed';rng=random.Random(seed);draws=[]
    for _ in range(2000):draws.append(math.fsum(x[rng.randrange(n)] for j in range(n))/n)
    draws.sort()
    for name,q in [('lower',.025),('upper',.975)]:
        point=(len(draws)-1)*q;i=math.floor(point);j=math.ceil(point);expected=draws[i]+(draws[j]-draws[i])*(point-i)
        equal(actual[name],expected)
def audit_runs(actual,calendar,means):
    runs=[];segment=[];missing=[];zeros=[]
    for date in calendar:
        if date not in means:missing.append(date);negative=False
        else:
            value=means[date];negative=value<0
            if value==0:zeros.append(date)
        if negative:segment.append(date)
        elif segment:runs.append(segment);segment=[]
    if segment:runs.append(segment)
    assert actual['negative_runs']==runs and actual['missing_dates_break_runs']==missing and actual['zero_dates_break_runs']==zeros
    assert actual['maximum_observed_consecutive_negative_dates']==max(map(len,runs),default=0)

def main():
    classification={r['case_id']:r for r in lines(ROOT/'classifications.jsonl.gz')};assert len(classification)==19519
    parentmanifest=read(OLD/'analysis-output-manifest.json');output=read(ROOT/'analysis-output-manifest.json');protocol=read(ROOT/'study-design.json')
    for name,digest in protocol['parent_bound_sha256'].items():assert sha(OLD/name)==digest
    for item in output['files']:assert sha(ROOT/item['path'])==item['sha256'] and (ROOT/item['path']).stat().st_size==item['bytes']
    calendar=[c['date'] for c in read(OLD/'calendar-2026-ytd.json')];buckets=defaultdict(list);seen=defaultdict(set);counts=Counter()
    basekeys=['case_id','date','symbol','target_role','signal_status','paired_control_status','risk_set_eligible','flush_detected','net_return','matched_excess','entry_minute','exit_minute','missing_stage','missing_reason']
    for file in sorted((x for x in parentmanifest['files'] if x['path'].startswith('results/')),key=lambda r:r['path']):
        source=OLD/file['path'];assert sha(source)==file['sha256'];target=ROOT/'enriched-results'/source.name
        for old,new in zip_longest(lines(source),lines(target)):
            assert old is not None and new is not None
            for key in basekeys:assert new[key]==old[key],('changed_base_value',old['case_id'],key)
            variant=(old['drop_threshold'],old['family'],old['exit_horizon'],old['cost_bps_per_side']);cid=old['case_id'];assert variant not in seen[cid];seen[cid].add(variant)
            assert new['variant']==list(variant) and new['variant_id']=='|'.join(map(str,variant))
            assert new['parent_result_file']==file['path'] and new['full_classification_case_id']==cid
            assert new['actual_fill'] is False and new['option_return'] is False and new['wealth_path'] is False
            case=classification[cid];assert new['date']==case['date'] and new['symbol']==case['symbol'] and new['target_role']==case['target_role']
            for benchmark in BENCHMARKS:
                c=case['classification_by_benchmark'][benchmark]
                equal(new['classification_by_benchmark'][benchmark],{k:c.get(k) for k in ['classification','status','t_min']})
                if old['target_role']=='stock':
                    group=c['classification'] or 'unknown';assert group in GROUPS
                    buckets[benchmark,group,variant].append({**{k:old[k] for k in basekeys},'t_min':c.get('t_min')})
                    counts['stock_group_memberships']+=1
            counts['inherited_rows_unchanged']+=1
            if old['target_role']!='stock':counts['retained_benchmark_rows']+=1
    assert len(seen)==19519 and all(len(v)==36 for v in seen.values()) and counts['inherited_rows_unchanged']==702684
    summary=read(ROOT/'summary.json');assert len(summary['groups'])==216 and len(summary['contrasts'])==72
    checked=Counter();all_summaries={};indexed={}
    for item in summary['groups']:
        key=(item['benchmark'],item['group'],tuple(item['variant']));assert key not in indexed;indexed[key]=item;data=buckets[key]
        assert item['is_primary_variant']==(key[-1]==PRIMARY)
        all_summaries[key]=check_summary(item['all'],data,str(key));checked['all_group_summaries']+=1
        for month,s in item['months'].items():check_summary(s,[r for r in data if r['date'].startswith(month)],str(key)+'/'+month);checked['monthly_summaries']+=1
        for name,s in item['periods'].items():check_summary(s,[r for r in data if (r['date']<'2026-07-01')==(name=='H1')],str(key)+'/'+name);checked['halfyear_summaries']+=1
        if key[-1]==PRIMARY:
            dates=all_summaries[key]['date_means'];net={d:v['net_mean'] for d,v in dates.items() if v['net_mean'] is not None};exc={d:v['matched_excess_mean'] for d,v in dates.items() if v['matched_excess_mean'] is not None}
            inference=item['primary_inference'];audit_bootstrap(inference['net_date_bootstrap'],net,key[0]+'|'+key[1]+'|net');audit_bootstrap(inference['matched_excess_date_bootstrap'],exc,key[0]+'|'+key[1]+'|excess');audit_runs(inference['negative_day_runs'],calendar,net)
            checked['primary_group_bootstraps']+=2;checked['negative_day_run_records']+=1
        else:assert 'primary_inference' not in item
    for item in summary['contrasts']:
        b=item['benchmark'];v=tuple(item['variant']);down=all_summaries[b,'market_down',v]['date_means'];notdown=all_summaries[b,'market_not_down',v]['date_means'];statuses=Counter();differences={};dates=[]
        for day in calendar:
            d=down.get(day,{});n=notdown.get(day,{});dv=d.get('matched_excess_mean');nv=n.get('matched_excess_mean')
            status='both_groups' if dv is not None and nv is not None else 'down_only' if dv is not None else 'not_down_only' if nv is not None else 'neither_group';statuses[status]+=1
            delta=nv-dv if status=='both_groups' else None
            if delta is not None:differences[day]=delta
            dates.append({'date':day,'status':status,'down_complete_pairs':d.get('matched_excess_n',0),'not_down_complete_pairs':n.get('matched_excess_n',0),'down_mean':dv,'not_down_mean':nv,'difference_not_down_minus_down':delta})
        equal(item['dates'],dates);equal(item['date_status_counts'],dict(statuses));equal(item['date_difference_distribution'],simple(differences.values()))
        if v==PRIMARY:audit_bootstrap(item['bootstrap'],differences,b+'|primary_contrast');checked['primary_contrast_bootstraps']+=1
        else:assert item['bootstrap'] is None
        checked['fullcalendar_contrasts']+=1
    dailyseen=set();dailybuckets={}
    for key,data in buckets.items():
        grouped=defaultdict(list)
        for record in data:grouped[record['date']].append(record)
        dailybuckets[key]=grouped
    # Empty groups are real registered groups even when they have no members.
    for r in lines(ROOT/'date-statistics.jsonl.gz'):
        candidates=[key for key in indexed if key[0]==r['benchmark'] and key[1]==r['group'] and '|'.join(map(str,key[2]))==r['variant_id']]
        assert len(candidates)==1;key=candidates[0];ident=(key,r['date']);assert ident not in dailyseen;dailyseen.add(ident)
        data=dailybuckets.get(key,{}).get(r['date'],[]);date=all_summaries[key]['date_means'].get(r['date'],{})
        assert r['selected_cases']==len(data) and r['signal_status_counts']==dict(Counter(x['signal_status'] for x in data))
        assert r['complete_target_returns']==date.get('net_n',0) and r['complete_matched_excess']==date.get('matched_excess_n',0)
        equal(r['net_mean'],date.get('net_mean'));equal(r['matched_excess_mean'],date.get('matched_excess_mean'));checked['daily_records']+=1
    assert len(dailyseen)==216*191
    primary=read(ROOT/'primary-results.json');equal(primary['groups'],[x for x in summary['groups'] if x['is_primary_variant']]);equal(primary['contrasts'],[x for x in summary['contrasts'] if x['is_primary_variant']])
    result={'audit_version':'independent_relative_analysis_v1','completed_at_utc':datetime.now(timezone.utc).isoformat(),'passed':True,'counts':dict(counts),'checked':dict(checked),'cases':len(seen),'summary_sha256':sha(ROOT/'summary.json'),'auditor_code_sha256':sha(Path(__file__)),
      'method':'Every enrichedrow comparedto frozen parent row; independent strata counts/simple-return/timing/date summaries, allcalendar group contrasts, allpredeclared datebootstrap draws and date-adjacent negative runs. Productionanalyzer not imported.',
      'payoff_scope':'Full conditionalwin/loss/ratios/quantiles/taildiagnostics independently audited separately in independent-payoff-audit.json.',
      'limits':['Stocktrough-time classification has timingselection/confounding; doublegroupdayrestriction changes targetdatepopulation.','Lowwinrate not excluded, but linearstock netreturns cannot prove0DTE optionpayoff or500capital survival.']}
    (ROOT/'independent-analysis-audit.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result),flush=True)
if __name__=='__main__':main()
