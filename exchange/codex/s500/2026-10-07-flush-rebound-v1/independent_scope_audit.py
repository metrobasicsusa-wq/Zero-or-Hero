"""Independent reconstruction of selected symbol-days using only prior daily inputs."""
import csv, hashlib, json, statistics
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT=Path(__file__).resolve().parent
SOURCE=ROOT.parent/'s500-broad-20261006'
STORAGE={'MU','STX','WDC','SNDK','NTAP','RMBS','SIMO','P'}
def read(path): return json.loads(path.read_text())
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    source_manifest=read(SOURCE/'input-manifest.json')
    daily={}; verified=[]
    for record in source_manifest['files']:
        path=SOURCE/'raw'/record['name']
        assert digest(path)==record['sha256'], ('source_hash',record['name'])
        verified.append(record['name'])
        if not record['name'].startswith('batch-'): continue
        obj=read(path)
        assert obj['input']['feed']=='sip' and obj['input']['adjustment']=='raw'
        for symbol,bars in obj['bars'].items():
            assert symbol not in daily
            daily[symbol]={}
            for bar in bars:
                day=bar['t'][:10]
                assert day not in daily[symbol]
                daily[symbol][day]=bar
    populations=read(SOURCE/'universe.json')
    symbols=[r['symbol'] for r in populations]
    assert len(symbols)==len(set(symbols))
    classes={}
    for filename,symbolfield in [('nasdaqlisted.txt','Symbol'),('otherlisted.txt','ACT Symbol')]:
        for record in csv.DictReader((SOURCE/filename).read_text().splitlines(),delimiter='|'):
            if record.get('ETF') in ('Y','N'):
                classes.setdefault(record.get(symbolfield),set()).add(record['ETF'])
    oldcalendar=read(SOURCE/'raw/calendar.json')
    extra=read(ROOT/'private-inputs/calendar-2026-10-06.json')
    fullcalendar=oldcalendar+extra
    dates=[r['date'] for r in fullcalendar]
    assert len(dates)==len(set(dates)) and dates==sorted(dates)
    supplied=read(ROOT/'daily-scope.json')
    registry=read(ROOT/'selected-case-registry.json')['cases']
    rmap={r['case_id']:r for r in registry}
    assert len(rmap)==len(registry)
    assert [r['date'] for r in supplied['daily']]==[d for d in dates if d.startswith('2026-')]
    checked=Counter(); all_ids=set(); monthly=Counter(); wrong_time=[]
    for actual in supplied['daily']:
        day=actual['date']; position=dates.index(day); previous=dates[position-20:position]
        assert len(previous)==20 and max(previous)<day
        assert actual['prior20_dates']==previous
        metrics={}; exclusions={}
        for symbol in symbols:
            reason=None
            if 'Y' in classes.get(symbol,set()): reason='explicit_current_ETF_Y'
            series=daily.get(symbol,{})
            if reason is None and any(d not in series for d in previous): reason='missing_one_or_more_prior20_session_daily_bars'
            if reason is None:
                try:
                    values=[(Decimal(str(series[d]['c'])),Decimal(str(series[d]['v']))) for d in previous]
                    if not all(c.is_finite() and v.is_finite() and c>0 and v>=0 for c,v in values):
                        reason='invalid_prior20_close_or_volume'
                except Exception: reason='invalid_prior20_close_or_volume'
            if reason is None:
                last=values[-1][0]
                # Calculate even-N median directly instead of calling production statistics code.
                volumes=sorted(c*v for c,v in values)
                median=(volumes[9]+volumes[10])/Decimal(2)
                if last<5: reason='prior_close_below_5'
                elif median<20000000: reason='prior20_median_dollar_volume_below_20m'
            if reason is None: metrics[symbol]=(last,median)
            else: exclusions.setdefault(reason,[]).append(symbol)
        eligible=sorted(metrics)
        assert actual['eligible_symbols']==eligible and actual['eligible_count']==len(eligible)
        assert actual['excluded_by_reason']=={k:sorted(v) for k,v in exclusions.items()}
        assert len(eligible)+sum(map(len,exclusions.values()))==len(symbols)
        liquid=sorted(eligible,key=lambda s:(-metrics[s][1],s))[:64]
        leftover=[s for s in eligible if s not in set(liquid)]
        random_sample=sorted(leftover,key=lambda s:(hashlib.sha256(('s500_flush_v1|'+day+'|'+s).encode()).hexdigest(),s))[:32]
        storage=sorted(STORAGE.intersection(eligible))
        selected=set(liquid+random_sample+storage+['SPY','QQQ'])
        assert actual['selected_top64']==liquid and actual['selected_hash32']==random_sample
        assert sorted(actual['selected_storage'])==storage
        assert actual['selected_symbols']==sorted(selected) and actual['selected_count']==len(selected)
        for symbol in selected:
            key=day+'__'+symbol; all_ids.add(key); row=rmap[key]
            assert row['date']==day and row['symbol']==symbol and row['prior20_dates']==previous
            assert row['prior_session_date']==previous[-1] and row['known_current_directory_survivorship_bias'] is True
            assert row['session_open_et']==actual['open_et'] and row['session_close_et']==actual['close_et']
            expected=[]
            if symbol in liquid: expected.append('prior20_dollar_volume_top64')
            if symbol in random_sample: expected.append('remaining_eligible_sha256_first32')
            if symbol in storage: expected.append('eligible_storage_overlay')
            if symbol in ['SPY','QQQ']: expected.append('forced_ETF_control')
            assert row['roles']==expected
            assert row['stock_eligible']==(symbol in metrics)
            if symbol in metrics:
                assert Decimal(row['selection_metrics']['prior_close'])==metrics[symbol][0]
                assert Decimal(row['selection_metrics']['median_prior20_daily_close_times_volume'])==metrics[symbol][1]
            else: assert row['selection_metrics'] is None
            checked['registry_rows']+=1; monthly[day[:7]]+=1
        checked['universe_symbol_days']+=len(symbols)
        checked['eligible_symbol_days']+=len(metrics)
        checked['sessions']+=1
    assert all_ids==set(rmap)
    result={
        'audit_version':'independent_scope_v1','completed_at_utc':datetime.now(timezone.utc).isoformat(),
        'passed':True,'checks':dict(checked),'monthly_selected_cases':dict(sorted(monthly.items())),
        'source_files_sha256_verified':len(verified),'current_directory_universe_size':len(symbols),
        'scope_sha256':digest(ROOT/'daily-scope.json'),'registry_sha256':digest(ROOT/'selected-case-registry.json'),
        'auditor_code_sha256':digest(Path(__file__)),
        'method':'Rebuilt all day eligibility and exclusions, 20-session medians, top64, deterministic SHA32, storage overlays, ETF controls and registry metrics from independent raw daily input reads.',
        'limitations':['Current directory and ETF classification are not point-in-time; this audit verifies stated selection, not historical completeness.','Raw cached SIP daily inputs are provider revisions observed after their historical dates.','No minute outcomes or strategy returns were read for this scope audit.']}
    (ROOT/'independent-scope-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False),flush=True)

if __name__=='__main__': main()
