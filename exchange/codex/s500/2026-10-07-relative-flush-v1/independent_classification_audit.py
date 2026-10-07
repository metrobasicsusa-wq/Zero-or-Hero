"""Independent fullcase reconstruction from original minute inputs; no classifier imports."""
from pathlib import Path
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from decimal import Decimal,localcontext
from fractions import Fraction
from collections import Counter
import gzip,hashlib,json,re
ROOT=Path(__file__).resolve().parent;OLD=ROOT.parent/'s500-flush-rebound-20261007';ET=ZoneInfo('America/New_York');D=Decimal
read=lambda p:json.loads(p.read_text())
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def parse_prefix(bars,date,opening,complete):
    anchor=datetime.fromisoformat(date+'T'+opening).replace(tzinfo=ET).astimezone(timezone.utc)
    good={};bad=set();seen=set();global_bad=False
    for row in bars:
        try:
            t=row['t'];m=re.fullmatch(r'(\d{4}-\d\d-\d\d)[Tt](\d\d):(\d\d):(\d\d)(?:\.([0-9]{1,9}))?([Zz]|[+-]\d\d:\d\d)',t)
            assert m and int(m[4])==0 and (not m[5] or int(m[5])==0)
            offset=m[6];assert offset!='-00:00'
            if offset not in ('Z','z'):assert int(offset[1:3])<=23 and int(offset[4:6])<=59
            stamp=datetime.fromisoformat(t[:-1]+'+00:00' if offset in ('Z','z') else t)
            diff=stamp.astimezone(timezone.utc)-anchor;seconds=diff.days*86400+diff.seconds
            assert not diff.microseconds and seconds%60==0;minute=seconds//60
        except Exception:global_bad=True;continue
        if minute<0 or minute>=30:continue
        if minute in seen:bad.add(minute);good.pop(minute,None);continue
        seen.add(minute)
        try:
            x=[]
            for field in ['o','h','l','c','v']:
                assert not isinstance(row[field],bool) and row[field] is not None
                value=D(str(row[field]));assert value.is_finite();x.append(value)
            o,h,l,c,v=x;assert min(o,h,l,c)>0 and v>=0 and l<=min(o,c)<=max(o,c)<=h
            good[minute]=(o,c)
        except Exception:bad.add(minute)
    valid=complete is True and not global_bad and len(good)==30 and not bad
    return {'valid':valid,'bars':good,'bad':bad,'global_bad':global_bad,'missing':[i for i in range(30) if i not in good and i not in bad]}

def eqnum(actual,expected):
    assert (actual is None)==(expected is None),(actual,expected)
    if actual is not None:assert D(actual)==expected,(actual,expected)

def main():
    protocol=read(ROOT/'study-design.json');manifest=read(ROOT/'reuse-input-manifest.json');registry=read(OLD/'selected-case-registry.json')['cases']
    known={r['case_id']:r for r in registry};calendar={r['date']:r for r in read(OLD/'calendar-2026-ytd.json')}
    source={r['date']:r for r in manifest['normalized_days']};output_manifest=read(ROOT/'classification-output-manifest.json')
    path=ROOT/output_manifest['output']['name'];assert sha(path)==output_manifest['output']['sha256']
    seen=set();counts=Counter();groups={'SPY':Counter(),'QQQ':Counter()};statuses={'SPY':Counter(),'QQQ':Counter()};day=None
    with gzip.open(path,'rt') as f:
        for line in f:
            record=json.loads(line);case_id=record['case_id'];assert case_id in known and case_id not in seen;seen.add(case_id);case=known[case_id]
            assert record['symbol']==case['symbol'] and record['date']==case['date']
            if day!=case['date']:
                day=case['date'];s=source[day];marketpath=OLD/s['source_name'];assert sha(marketpath)==s['sha256']
                market=read(marketpath);assert market['request_complete']==s['request_complete'];assert market['date']==day
                prefixes={sym:parse_prefix(bars,day,calendar[day]['open'],market['request_complete']) for sym,bars in market['bars'].items()}
                counts['source_days']+=1;counts['original_bars_read']+=sum(map(len,market['bars'].values()))
            symbol=case['symbol'];is_stock=symbol not in ['SPY','QQQ'];assert record['target_role']==('stock' if is_stock else 'benchmark')
            assert record['actual_fill'] is False and record['wealth_path'] is False
            for name in ['current_etf_classification','stock_eligible','prior_session_date','selection_metrics','known_current_directory_survivorship_bias']:assert record['parent_case'][name]==case[name]
            assert record['parent_case']['sample_roles']==case['roles']
            assert record['early_window']['first_minute']==0 and record['early_window']['last_minute']==29 and record['early_window']['window_complete_at_et']=='10:00'
            assert record['source']['normalized_day_sha256']==source[day]['sha256']
            for benchmark in ['SPY','QQQ']:
                actual=record['classification_by_benchmark'][benchmark]
                if not is_stock:
                    assert actual['classification']==actual['status']=='not_stock_target'
                    groups[benchmark]['not_stock_target']+=1;statuses[benchmark]['not_stock_target']+=1;continue
                t=prefixes[symbol];b=prefixes[benchmark]
                assert actual['target_valid']==t['valid'] and actual['benchmark_valid']==b['valid']
                minimum=opening=tret=None;tmin=None
                if t['valid']:
                    opening=t['bars'][0][0];minimum=min(t['bars'][i][1] for i in range(30));tmin=min(i for i in range(30) if t['bars'][i][1]==minimum)
                    with localcontext() as context:context.prec=42;tret=minimum/opening-1
                assert actual['t_min']==tmin;eqnum(actual['target_open'],opening);eqnum(actual['target_min_close'],minimum);eqnum(actual['target_return'],tret)
                bopen=b['bars'][0][0] if b['valid'] else None;eqnum(actual['benchmark_open'],bopen)
                if not t['valid'] or not b['valid']:
                    status='unknown_both_prefixes' if not t['valid'] and not b['valid'] else 'unknown_target_prefix' if not t['valid'] else 'unknown_benchmark_prefix'
                    assert actual['classification'] is None and actual['status']==status
                    for field in ['benchmark_return','relative_return','benchmark_close_at_target_minute']:assert actual[field] is None
                    group='unknown'
                else:
                    bc=b['bars'][tmin][1]
                    exact_benchmark_return=Fraction(bc)/Fraction(bopen)-1
                    group='market_down' if exact_benchmark_return<=Fraction(-5,1000) else 'market_not_down'
                    with localcontext() as context:context.prec=42;bret=bc/bopen-1;relative=tret-bret
                    assert actual['classification']==group and actual['status']=='classified'
                    eqnum(actual['benchmark_close_at_target_minute'],bc);eqnum(actual['benchmark_return'],bret);eqnum(actual['relative_return'],relative)
                    status='classified'
                groups[benchmark][group]+=1;statuses[benchmark][status]+=1;counts['stock_benchmark_classifications']+=1
            counts['case_rows']+=1
    assert seen==set(known) and counts['case_rows']==19519 and counts['stock_benchmark_classifications']==38274
    assert output_manifest['classification_counts_by_benchmark']=={b:dict(v) for b,v in groups.items()}
    assert output_manifest['status_counts_by_benchmark']=={b:dict(v) for b,v in statuses.items()}
    result={'audit_version':'independent_relative_classification_v1','completed_at_utc':datetime.now(timezone.utc).isoformat(),'passed':True,'counts':dict(counts),'classification_counts':{b:dict(v) for b,v in groups.items()},'status_counts':{b:dict(v) for b,v in statuses.items()},'classification_output_sha256':sha(path),'classification_manifest_sha256':sha(ROOT/'classification-output-manifest.json'),'study_design_sha256':sha(ROOT/'study-design.json'),'auditor_code_sha256':sha(Path(__file__)),
        'method':'Independent original RFC3339/OHLC/duplicate/prefix parsing, earliesttargetlow, same-minute own-openbenchmarkreturn and exactFraction threshold classification. No productionclassifier or normalizer imports.','limitations':['Classifying with stock-specific trough times retains timingselection/confounding.','Stockprice states are not beta-residual oroptionpayoffs; no future return used in classification.']}
    (ROOT/'independent-classification-audit.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result),flush=True)
if __name__=='__main__':main()
