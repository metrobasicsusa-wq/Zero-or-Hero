"""Frozen offline feature and contribution anatomy; parent execution is unchanged."""
from pathlib import Path
from collections import Counter,defaultdict
from fractions import Fraction
from decimal import Decimal
from datetime import datetime,timezone
import json,gzip,io,hashlib,statistics
from prefix_features import first30_features
from concentration import summarize_concentration
from vendor.normalize_bars import normalize_provider_bars
from vendor.return_distribution import compute_distribution
ROOT=Path(__file__).parent;PARENT=ROOT.parent/'s500-relative-flush-20261007';OLD=ROOT.parent/'s500-flush-rebound-20261007'
PRIMARY=('0.02','REBOUND','60m',10)
VARIANTS=tuple((d,f,h,c) for d in ['0.02','0.03'] for f in ['FIXED','REBOUND'] for h in ['30m','60m','close_minus_10m'] for c in [5,10,25])
GROUPS=('market_down','market_not_down','unknown')
FEATURES=('gap_to_previous_close','close29_return','min_close_return','max_close_return','t_min','first30_volume','first30_high_low_range')
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.write_text(json.dumps(x,ensure_ascii=False,separators=(',',':'))+'\n')
def stream(p):
    p.parent.mkdir(parents=True,exist_ok=True)
    return io.TextIOWrapper(gzip.GzipFile(filename=str(p),mode='wb',mtime=0),encoding='utf8')
def emit(f,x):f.write(json.dumps(x,separators=(',',':'))+'\n')
def rows(p):
    with gzip.open(p,'rt') as f:
        for line in f:yield json.loads(line)
def describe(values):
    v=[float(x) for x in values if x is not None]
    return {'n':len(v),'mean':statistics.fmean(v) if v else None,'median':statistics.median(v) if v else None,'minimum':min(v) if v else None,'maximum':max(v) if v else None}
def price_bin(numerator,denominator):
    if numerator is None or denominator is None:return 'unknown'
    n=Fraction(Decimal(numerator));d=Fraction(Decimal(denominator))
    if n<=0 or d<=0:raise ValueError('Price bins need positive prices')
    r=n/d-1
    return 'le_minus_2pct' if r<=Fraction(-2,100) else ('minus_2pct_to_below_zero' if r<0 else 'zero_or_positive')
def bins(f):
    if not f['prefix_valid']:return {'gap':'unknown','close29':'unknown','trough':'unknown'}
    t=f['t_min'];assert type(t) is int and 0<=t<=29
    return {'gap':price_bin(f['open0'],f['previous_close']) if f['previous_close_valid'] else 'unknown','close29':price_bin(f['close29'],f['open0']),'trough':['minute0_9','minute10_19','minute20_29'][t//10]}
def group(c):return c['parent_classification_by_benchmark']['SPY']['classification'] or 'unknown'
def variant(r):return (r['drop_threshold'],r['family'],r['exit_horizon'],r['cost_bps_per_side'])
def summary(data):
    count=Counter(selected_cases=0,signals=0,unknown_signals=0,no_flush=0,no_rebound=0,complete_net=0,complete_pairs=0,signal_return_missing=0)
    statuses=Counter();pairs=Counter();net=[];excess=[];perdate=defaultdict(lambda:{'net':[],'excess':[]})
    for r in data:
        count['selected_cases']+=1;statuses[r['signal_status']]+=1;pairs[r['paired_control_status']]+=1
        if r['signal_status']=='signal':count['signals']+=1;count['signal_return_missing']+=r['net_return'] is None
        elif r['signal_status']=='unknown':count['unknown_signals']+=1
        else:count[r['signal_status']]+=1
        if r['net_return'] is not None:net.append({'case_id':r['case_id'],'net_return':r['net_return']});perdate[r['date']]['net'].append(r['net_return'])
        if r['matched_excess'] is not None:excess.append(r['matched_excess']);perdate[r['date']]['excess'].append(r['matched_excess'])
    count['complete_net']=len(net);count['complete_pairs']=len(excess)
    return {'counts':dict(count),'signal_status_counts':dict(statuses),'paired_control_status_counts':dict(pairs),'target_net_distribution':compute_distribution(net),'matched_excess':{**describe(excess),'scope':'relativeperformance_nottradingwin'},'date_equal_net':describe(describe(v['net'])['mean'] for v in perdate.values()),'date_equal_excess':describe(describe(v['excess'])['mean'] for v in perdate.values())}
def feature_summary(data,features):
    return {name:describe(features[r['case_id']]['features'][name] for r in data) for name in FEATURES}
def main():
    start=datetime.now(timezone.utc).isoformat();binding=read(ROOT/'pre-analysis-validation.json');assert binding['failed_tests']==0
    for n,h in binding['bound_sha256'].items():assert sha(ROOT/n)==h,n
    design=read(ROOT/'study-design.json')
    for n,h in design['scope_sha256'].items():assert sha(ROOT/n)==h,n
    sources=read(ROOT/'reuse-input-manifest.json')
    for source in sources['source_files']:
        root=PARENT if source['ancestor']=='parent' else OLD
        assert sha(root/source['name'])==source['sha256'],source['name']
    cases=read(ROOT/'anatomy-case-registry.json')['cases'];registry={r['case_id']:r for r in cases};assert len(cases)==len(registry)==1227
    bydate=defaultdict(list)
    for c in cases:bydate[c['date']].append(c)
    assert len(bydate)==12;features={};market=[]
    featpath=ROOT/'feature-records.jsonl.gz';assert not featpath.exists()
    with stream(featpath) as f:
        for source in sorted(sources['normalized_anatomy_days'],key=lambda x:x['date']):
            day=source['date'];path=OLD/source['name'];assert sha(path)==source['sha256'];obj=read(path);assert obj['date']==day and obj['request_complete'] is True
            assert set(obj['requested_symbols'])=={c['symbol'] for c in bydate[day]}
            bench={}
            for c in sorted(bydate[day],key=lambda c:c['symbol']):
                raw=obj['bars'].get(c['symbol'],[]);normal=normalize_provider_bars(raw,day,c['session_open_et']);prev=(c['selection_metrics'] or {}).get('prior_close')
                values=first30_features(normal['bars'],prev,source_complete=source['request_complete'] is True and obj['request_complete'] is True)
                item={'case_id':c['case_id'],'date':day,'symbol':c['symbol'],'date_role':c['date_role'],'target_role':c['target_role'],'SPY_group':group(c),'features':values,'fixed_bins':bins(values),'available_at_ET':'10:00','prior_close_source':'parent_registry_raw_prior_close' if prev is not None else 'not_supplied_for_forced_benchmark','normalized_source_sha256':source['sha256'],'actual_fill':False}
                features[c['case_id']]=item;emit(f,item)
                if c['symbol'] in ['SPY','QQQ']:bench[c['symbol']]=values
            market.append({'date':day,'date_role':bydate[day][0]['date_role'],'benchmarks':bench,'stock_group_counts':dict(Counter(group(c) for c in bydate[day] if c['target_role']=='stock')),'information_time_ET':'10:00','not_a_causal_news_explanation':True})
    save(ROOT/'market-context.json',{'days':market,'current_historical_provider_view':True})
    primary=[];selected=defaultdict(list);masks=defaultdict(int);total=0;output=ROOT/'inherited-results';assert not output.exists();output.mkdir()
    manifest=read(OLD/'analysis-output-manifest.json');result_sources=[x for x in manifest['files'] if x['path'].startswith('results/')]
    for source in sorted(result_sources,key=lambda x:x['path']):
        path=OLD/source['path'];assert sha(path)==source['sha256'];writer=None
        try:
            for r in rows(path):
                if r['case_id'] not in registry:continue
                v=variant(r);bit=1<<VARIANTS.index(v);assert not masks[r['case_id']]&bit;masks[r['case_id']]|=bit
                c=registry[r['case_id']];assert c['symbol']==r['symbol'] and c['date']==r['date'];total+=1
                if writer is None:writer=stream(output/path.name)
                emitted={**r,'anatomy_date_role':c['date_role'],'anatomy_feature_case_id':r['case_id'],'SPY_group':group(c),'parent_result_file':source['path']};emit(writer,emitted)
                if r['target_role']=='stock':selected[(v,group(c))].append(r)
                if v==PRIMARY:primary.append(emitted)
        finally:
            if writer:writer.close()
    assert total==44172 and len(masks)==1227 and all(m==(1<<36)-1 for m in masks.values()) and len(primary)==1227
    with stream(ROOT/'primary-case-ledger.jsonl.gz') as f:
        for r in sorted(primary,key=lambda r:r['case_id']):emit(f,r)
    maincases=[r for r in primary if r['target_role']=='stock' and r['SPY_group']=='market_down']
    main_signals=[r for r in maincases if r['signal_status']=='signal']
    concentration=summarize_concentration([{k:r[k] for k in ['case_id','date','symbol','net_return','matched_excess']} for r in main_signals])
    assert concentration['overall']['net_return']['complete_count']==153 and concentration['overall']['matched_excess']['complete_count']==134
    save(ROOT/'concentration.json',concentration)
    variants=[{'variant':list(v),'SPY_group':g,'summary':summary(selected[(v,g)])} for v in VARIANTS for g in GROUPS]
    date_tables=[{'date':day,'date_role':bydate[day][0]['date_role'],'SPY_group':g,'summary':summary([r for r in primary if r['date']==day and r['target_role']=='stock' and r['SPY_group']==g])} for day in sorted(bydate) for g in GROUPS]
    bin_tables=[]
    for axis,labels in [('gap',['le_minus_2pct','minus_2pct_to_below_zero','zero_or_positive','unknown']),('close29',['le_minus_2pct','minus_2pct_to_below_zero','zero_or_positive','unknown']),('trough',['minute0_9','minute10_19','minute20_29','unknown'])]:
        for label in labels:
            subset=[r for r in maincases if features[r['case_id']]['fixed_bins'][axis]==label]
            bin_tables.append({'axis':axis,'bin':label,'primary_SPY_down_all_selected_case_summary':summary(subset),'feature_summary':feature_summary(subset,features),'scope':'exploratory_fixedbins_notchosen_filters'})
    outcomes={key:[] for key in ['positive_net','negative_net','zero_net','unknown_or_not_triggered']}
    for r in maincases:
        value=r['net_return'];label='unknown_or_not_triggered' if value is None else ('positive_net' if Decimal(value)>0 else ('negative_net' if Decimal(value)<0 else 'zero_net'));outcomes[label].append(r)
    conditioned=[{'outcome_group':k,'counts':summary(v)['counts'],'features':feature_summary(v,features),'scope':'conditioned_on_known_outcome_not_a_predictive_test'} for k,v in outcomes.items()]
    diagnostic=[r for r in maincases if r['anatomy_date_role']=='no_complete_outcome_diagnostic']
    timing=[{'case_id':r['case_id'],'date':r['date'],'symbol':r['symbol'],'signal_status':r['signal_status'],'signal_minute':r['signal_minute'],'decision_minute':r['decision_minute'],'entry_minute':r['entry_minute'],'exit_minute':r['exit_minute'],'label':'later_realized_strategy_timing_not_10am_feature'} for r in maincases]
    save(ROOT/'summary.json',{'study':design['id'],'counts':{'cases':1227,'stock_cases':1203,'benchmark_cases':24,'dates':12,'inherited_rows':total,'variants':36,'variant_group_summaries':108,'primary_SPY_down_selected_cases':len(maincases),'primary_SPY_down_signals':len(main_signals),'primary_SPY_down_complete_returns':153,'primary_SPY_down_complete_pairs':134},'variant_group_summaries':variants,'primary_date_group_summaries':date_tables,'fixed_feature_bins':bin_tables,'outcome_conditioned_features':conditioned,'diagnostic_June18_SPY_down_cases':diagnostic,'realized_strategy_timing':timing,'benchmark_feature_gaps':'ForcedETFpreviousclose notprovided, gapunknown; othercompleteprefixfeaturesretained','scope':'Allresultsdescriptive; no new strategy,capital,options orcausalinference'})
    outputs=list(output.glob('*.gz'))+[featpath,ROOT/'market-context.json',ROOT/'primary-case-ledger.jsonl.gz',ROOT/'concentration.json',ROOT/'summary.json']
    save(ROOT/'analysis-output-manifest.json',{'began_at':start,'completed_at':datetime.now(timezone.utc).isoformat(),'cases':1227,'inherited_rows':total,'files':[{'path':p.relative_to(ROOT).as_posix(),'bytes':p.stat().st_size,'sha256':sha(p)} for p in outputs]})
    print(json.dumps({'completed':True,'cases':1227,'inherited_rows':total,'main_signals':len(main_signals),'complete_net':153,'complete_pairs':134,'diagnostic_cases':len(diagnostic)}),flush=True)
if __name__=='__main__':main()
