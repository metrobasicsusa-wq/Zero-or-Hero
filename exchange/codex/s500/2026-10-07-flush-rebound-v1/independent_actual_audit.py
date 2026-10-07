"""Independent recomputation of every outcome and matched-time control from raw inputs.

Does not import the event engine, runner, normalizer or statistics implementation.
"""
from pathlib import Path
from collections import Counter,defaultdict
from datetime import datetime,timezone
from decimal import Decimal,localcontext
from itertools import groupby
from fractions import Fraction
from zoneinfo import ZoneInfo
import gzip,hashlib,json,math,random,re,statistics

ROOT=Path(__file__).resolve().parent;D=Decimal;ET=ZoneInfo('America/New_York')
THRESHOLDS=['0.02','0.03'];FAMILIES=['FIXED','REBOUND'];HORIZONS=['30m','60m','close_minus_10m'];COSTS=[5,10,25]
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def lines(p):
    with gzip.open(p,'rt') as f:
        for line in f:
            if line.strip():yield json.loads(line)
def equal_decimal(a,b):
    assert (a is None)==(b is None),(a,b)
    if a is not None:assert D(str(a))==D(str(b)),(a,b)

class Market:
    def __init__(self,raw,date,open_et,complete):
        self.good={};self.bad=set();self.global_bad=False;self.complete=complete
        opening=datetime.fromisoformat(date+'T'+open_et).replace(tzinfo=ET).astimezone(timezone.utc)
        seen=set()
        for item in raw:
            try:
                stamp=item['t']
                match=re.fullmatch(r'(\d{4}-\d\d-\d\d)[Tt](\d\d):(\d\d):(\d\d)(?:\.([0-9]{1,9}))?([Zz]|[+-]\d\d:\d\d)',stamp)
                assert match and int(match[4])==0 and (not match[5] or int(match[5])==0)
                offset=match[6]
                assert offset!='-00:00'
                if offset not in ('Z','z'):assert int(offset[1:3])<=23 and int(offset[4:6])<=59
                dt=datetime.fromisoformat(stamp[:-1]+'+00:00' if offset in ('Z','z') else stamp)
                delta=dt.astimezone(timezone.utc)-opening
                seconds=delta.days*86400+delta.seconds
                assert delta.microseconds==0 and seconds%60==0
                minute=seconds//60
            except Exception:
                self.global_bad=True;continue
            if minute in seen:
                self.bad.add(minute);self.good.pop(minute,None);continue
            seen.add(minute)
            try:
                values=[]
                for field in ['o','h','l','c','v']:
                    assert item[field] is not None and not isinstance(item[field],bool)
                    value=D(str(item[field]));assert value.is_finite();values.append(value)
                o,h,l,c,v=values
                assert min(o,h,l,c)>0 and v>=0 and l<=min(o,c)<=max(o,c)<=h
                self.good[minute]=(o,c)
            except Exception:self.bad.add(minute)
        self.prefixes={}
        for threshold in THRESHOLDS:
            if not complete or self.global_bad:self.prefixes[threshold]=(None,'unknown_source')
            elif any(i not in self.good for i in range(30)):self.prefixes[threshold]=(None,'unknown_prefix')
            else:
                flush=min(self.good[i][1] for i in range(30))<=self.good[0][0]*(1-D(threshold))
                self.prefixes[threshold]=(flush,'flush' if flush else 'no_flush')
        self.decisions={};self.windows={}
    def decision(self,threshold,family):
        if (threshold,family) in self.decisions:return self.decisions[threshold,family]
        flush,status=self.prefixes[threshold]
        signal=entry=None
        if flush is True:
            if family=='FIXED':status='signal';signal=29;entry=31
            else:
                running=min(self.good[i][1] for i in range(30));status='valid_no_rebound'
                for i in range(30,90):
                    if i not in self.good:status='unknown_rebound_prefix';break
                    c=self.good[i][1]
                    if c>=running*D('1.01'):status='signal';signal=i;entry=i+2;break
                    if c<running:running=c
        result=(status,signal,entry);self.decisions[threshold,family]=result;return result
    def window(self,entry,exit,cost,session):
        key=(entry,exit,cost,session)
        if key in self.windows:return self.windows[key]
        if entry is None or exit is None or not 0<=entry<exit<session:r=('invalid_window',None,None,None,None)
        elif not self.complete or self.global_bad:r=('unknown_source',None,None,None,None)
        elif any(i not in self.good for i in range(entry,exit+1)):r=('unknown_window',None,None,None,None)
        else:
            o=self.good[entry][0];x=self.good[exit][0]
            with localcontext() as ctx:
                ctx.prec=42;rate=D(cost)/10000;gross=x/o-1;net=x*(1-rate)/(o*(1+rate))-1
            r=('ok',o,x,gross,net)
        self.windows[key]=r;return r

def main():
    registry=read(ROOT/'selected-case-registry.json')['cases'];rmap={r['case_id']:r for r in registry}
    bydate=defaultdict(list)
    for row in registry:bydate[row['date']].append(row)
    scopes={r['date']:r for r in read(ROOT/'daily-scope.json')['daily']}
    source={r['date']:r for r in read(ROOT/'minute-source-manifest.json')['days']}
    output_manifest=read(ROOT/'analysis-output-manifest.json')
    for item in output_manifest['files']:
        path=ROOT/item['path'];assert sha(path)==item['sha256'] and path.stat().st_size==item['bytes']
    counts=Counter();statuses=Counter();allcases=set();primary=[];day=None
    expected_grid={(t,f,h,c) for t in THRESHOLDS for f in FAMILIES for h in HORIZONS for c in COSTS}
    for path in sorted((ROOT/'results').glob('*.jsonl.gz')):
        diagnostics=iter(lines(ROOT/'case-diagnostics'/path.name))
        for case_id,group in groupby(lines(path),key=lambda r:r['case_id']):
            rows=list(group);case=rmap[case_id];diag=next(diagnostics)
            assert diag['case_id']==case_id and case_id not in allcases
            allcases.add(case_id)
            assert len(rows)==36 and {(r['drop_threshold'],r['family'],r['exit_horizon'],r['cost_bps_per_side']) for r in rows}==expected_grid
            if day!=case['date']:
                day=case['date'];scope=scopes[day];srec=source[day]
                market=read(ROOT/'private-inputs/minute-days'/srec['normalized_file'])
                assert sha(ROOT/'private-inputs/minute-days'/srec['normalized_file'])==srec['normalized_sha256']
                session=int((datetime.fromisoformat(day+'T'+scope['close_et'])-datetime.fromisoformat(day+'T'+scope['open_et'])).total_seconds()/60)
                models={s:Market(b,day,scope['open_et'],srec['request_complete'] is True and market['request_complete'] is True) for s,b in market['bars'].items()}
                peers_cache={};counts['dates']+=1
            symbol=case['symbol'];model=models[symbol];is_stock=symbol not in ['SPY','QQQ']
            for threshold in THRESHOLDS:
                rankings=[]
                if is_stock and case['stock_eligible']:
                    liquidity=D(case['selection_metrics']['median_prior20_daily_close_times_volume']); liquidity_fraction=Fraction(liquidity)
                    with localcontext() as ctx:
                        ctx.prec=42
                        for other in bydate[day]:
                            osymbol=other['symbol']
                            if osymbol==symbol or osymbol in ['SPY','QQQ'] or not other['stock_eligible']:continue
                            if models[osymbol].prefixes[threshold][0] is not False:continue
                            other_fraction=Fraction(D(other['selection_metrics']['median_prior20_daily_close_times_volume']))
                            ratio=max(other_fraction/liquidity_fraction,liquidity_fraction/other_fraction)
                            rankings.append((ratio,osymbol))
                nearest=sorted(rankings)[:3]
                peers_cache[symbol,threshold]=[]
                for _,osymbol in nearest:
                    other=next(c for c in bydate[day] if c['symbol']==osymbol)
                    with localcontext() as ctx:
                        ctx.prec=42
                        distance=abs((D(other['selection_metrics']['median_prior20_daily_close_times_volume'])/liquidity).ln())
                    peers_cache[symbol,threshold].append((distance,osymbol))
                actual_peers=diag['preselected_peers'][threshold]
                assert [r['symbol'] for r in actual_peers]==[s for _,s in peers_cache[symbol,threshold]]
                for ap,(dist,_) in zip(actual_peers,peers_cache[symbol,threshold]):equal_decimal(ap['absolute_log_liquidity_distance'],dist)
                actual_prefix=diag['prefixes'][threshold]
                assert actual_prefix['is_flush']==model.prefixes[threshold][0] and actual_prefix['status']==model.prefixes[threshold][1]
            for row in rows:
                t=row['drop_threshold'];family=row['family'];horizon=row['exit_horizon'];cost=row['cost_bps_per_side']
                estatus,signal,entry=model.decision(t,family)
                canonical={'signal':'signal','no_flush':'no_flush','valid_no_rebound':'no_rebound'}.get(estatus,'unknown')
                assert row['engine_signal_status']==estatus and row['signal_status']==canonical
                assert row['signal_minute']==signal and row['entry_minute']==entry
                assert row['decision_minute']==(signal+1 if signal is not None else None)
                assert row['flush_detected']==model.prefixes[t][0]
                assert row['risk_set_eligible']==bool(case['stock_eligible'] and model.prefixes[t][0] is not None)
                assert row['target_role']==('stock' if is_stock else 'benchmark')
                assert row['actual_fill'] is False and row['wealth_path'] is False
                exit_=None if entry is None else (session-10 if horizon=='close_minus_10m' else entry+(30 if horizon=='30m' else 60))
                assert row['exit_minute']==exit_
                if canonical=='signal':window=model.window(entry,exit_,cost,session)
                else:window=(None,None,None,None,None)
                assert row['window_status']==window[0]
                for key,value in zip(['entry_open','exit_open','gross_return','net_return'],window[1:]):equal_decimal(row[key],value)
                expected_stage='signal' if canonical=='unknown' else ('return' if canonical=='signal' and window[-1] is None else None)
                assert row['missing_stage']==expected_stage
                expected_reason=estatus if expected_stage=='signal' else (window[0] if expected_stage=='return' else None)
                assert row['missing_reason']==expected_reason
                expected_peers=peers_cache[symbol,t] if canonical=='signal' and is_stock else []
                assert [q['symbol'] for q in row['paired_controls']]==[s for _,s in expected_peers]
                control_returns=[]
                for q,(_,s) in zip(row['paired_controls'],expected_peers):
                    cw=models[s].window(entry,exit_,cost,session)
                    assert q['entry_minute']==entry and q['exit_minute']==exit_
                    assert q['status']==('complete' if cw[0]=='ok' else cw[0])
                    equal_decimal(q['entry_open'],cw[1]);equal_decimal(q['exit_open'],cw[2]);equal_decimal(q['net_return'],cw[-1])
                    control_returns.append(cw[-1]);counts['peer_windows']+=1
                excess=None;paired='not_applicable'
                if canonical=='signal' and is_stock:
                    paired='insufficient_candidates' if len(control_returns)!=3 else 'missing_return'
                    if len(control_returns)==3 and all(x is not None for x in control_returns) and window[-1] is not None:
                        with localcontext() as ctx:ctx.prec=42;excess=window[-1]-sum(control_returns)/3
                        paired='complete_three'
                assert row['paired_control_status']==paired;equal_decimal(row['matched_excess'],excess)
                assert set(row['benchmarks'])==({'SPY','QQQ'} if canonical=='signal' else set())
                for s,q in row['benchmarks'].items():
                    bw=models[s].window(entry,exit_,cost,session)
                    assert q['entry_minute']==entry and q['exit_minute']==exit_
                    assert q['status']==('complete' if bw[0]=='ok' else bw[0])
                    equal_decimal(q['entry_open'],bw[1]);equal_decimal(q['exit_open'],bw[2]);equal_decimal(q['net_return'],bw[-1])
                    with localcontext() as ctx:ctx.prec=42;be=window[-1]-bw[-1] if window[-1] is not None and bw[-1] is not None else None
                    equal_decimal(q['excess_return'],be);counts['benchmark_windows']+=1
                if is_stock and (t,family,horizon,cost)==('0.02','REBOUND','60m',10):primary.append(row)
                counts['variant_rows']+=1;statuses[canonical]+=1
            counts['cases']+=1
        try:next(diagnostics);raise AssertionError('extra_case_diagnostic')
        except StopIteration:pass
        print(json.dumps({'audited_month':path.name,'counts':dict(counts)}),flush=True)
    assert allcases==set(rmap) and counts['variant_rows']==702684
    paired=[r for r in primary if r['matched_excess'] is not None];groups=defaultdict(list)
    for r in paired:groups[r['date']].append(float(r['matched_excess']))
    means={d:statistics.fmean(v) for d,v in sorted(groups.items())}
    summary={'stock_riskset_rows':len(primary),'primary_signal_count':sum(r['signal_status']=='signal' for r in primary),
        'primary_completed_return_count':sum(r['net_return'] is not None for r in primary),'complete_pairs':len(paired),
        'pair_dates':len(means),'pooled_matched_excess_mean':statistics.fmean(float(r['matched_excess']) for r in paired) if paired else None,
        'equally_weighted_date_matched_excess_mean':statistics.fmean(means.values()) if means else None,
        'primary_signal_status_counts':dict(Counter(r['signal_status'] for r in primary)),
        'primary_paired_control_status_counts':dict(Counter(r['paired_control_status'] for r in primary)),
        'complete_pair_symbols':len({r['symbol'] for r in paired}),'date_means':means}
    result={'audit_version':'independent_actual_result_reconstruction_v1','completed_at_utc':datetime.now(timezone.utc).isoformat(),'passed':True,
        'counts':dict(counts),'all_variant_signal_statuses':dict(statuses),'primary_independent_summary':summary,
        'auditor_code_sha256':sha(Path(__file__)),'analysis_output_manifest_sha256':sha(ROOT/'analysis-output-manifest.json'),
        'method':'Independent raw timestamp/OHLC validation, all signal decisions, exact42decimal open/open cost arithmetic, predeclared nonflushliquidity controls and same-minute benchmark reconstruction; no productionengine imports.',
        'limitations':['Checks stated calculations, not true bidask execution or provider price correctness.','Descriptive stockevent association in currentdirectory cohort; no optionreturns or500wealth simulation.']}
    (ROOT/'independent-actual-audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='primary_independent_summary'}),flush=True)
if __name__=='__main__':main()
