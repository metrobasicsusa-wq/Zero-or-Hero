"""Verify every fetched page, source join and retained coverage denominator."""
from pathlib import Path
from collections import Counter
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
import hashlib,json,re

ROOT=Path(__file__).resolve().parent
ET=ZoneInfo('America/New_York')
FIELDS={'t','o','h','l','c','v','n','vw'}
def read(path):return json.loads(path.read_text())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def stamp(dt):return dt.astimezone(timezone.utc).isoformat().replace('+00:00','Z')

def main():
    manifest=read(ROOT/'minute-source-manifest.json')
    scopes={s['date']:s for s in read(ROOT/'daily-scope.json')['daily']}
    registry=read(ROOT/'selected-case-registry.json')['cases']
    cases={r['case_id'] for r in registry}
    cover={r['case_id']:r for r in read(ROOT/'symbol-session-coverage.json')['rows']}
    assert set(cover)==cases and len(cover)==len(registry)
    assert {r['date'] for r in manifest['days']}==set(scopes)
    counts=Counter(); statuses=Counter(); allrequests=[]
    for record in manifest['days']:
        date=record['date'];scope=scopes[date]
        statuses[record['status']]+=1
        if 'normalized_file' not in record:
            assert record['request_complete'] is False
            for symbol in scope['selected_symbols']:
                row=cover[date+'__'+symbol]
                assert row['request_complete'] is False and row['returned_bars']==0
            counts['explicit_all_missing_dates']+=1
            continue
        normalized_path=ROOT/'private-inputs/minute-days'/record['normalized_file']
        assert sha(normalized_path)==record['normalized_sha256']
        assert normalized_path.stat().st_size==record['normalized_bytes']
        obj=read(normalized_path)
        assert obj['date']==date and obj['requested_symbols']==scope['selected_symbols']
        assert obj['request_complete']==record['request_complete'] and obj['status']==record['status']
        start=datetime.fromisoformat(date+'T'+scope['open_et']).replace(tzinfo=ET)
        end=datetime.fromisoformat(date+'T'+scope['close_et']).replace(tzinfo=ET)
        expected=[stamp(start+timedelta(minutes=i)) for i in range(int((end-start).total_seconds()/60))]
        expected_set=set(expected)
        original={symbol:[] for symbol in scope['selected_symbols']}
        successful=0
        for request in record['requests']:
            allrequests.append(request['request_id'])
            assert request['method']=='GET' and request['host']=='data.alpaca.markets' and request['path']=='/v2/stocks/bars'
            params=request['params']
            assert params['feed']=='sip' and params['adjustment']=='raw' and params['timeframe']=='1Min'
            assert params['symbols'].split(',')==scope['selected_symbols']
            assert params['start']==stamp(start)
            assert params['end']==(end.astimezone(timezone.utc)-timedelta(seconds=1)).strftime('%Y-%m-%dT%H:%M:%S')+'.999999999Z'
            assert datetime.fromisoformat(request['requested_at'])<datetime.fromisoformat(request['received_at'])
            if request.get('response_file'):
                rawpath=ROOT/'private-inputs/minute-pages'/request['response_file']
                assert sha(rawpath)==request['response_sha256'] and rawpath.stat().st_size==request['response_bytes']
                counts['raw_response_hashes_verified']+=1
                if request['status']==200:
                    successful+=1;page=read(rawpath)
                    assert sum(map(len,page['bars'].values()))==request['bar_count']
                    for symbol,bars in page['bars'].items():
                        if symbol in original:
                            original[symbol].extend([{k:v for k,v in bar.items() if k in FIELDS} for bar in bars])
            counts['requests']+=1
        assert obj['source_request_ids']==[r['request_id'] for r in record['requests']]
        assert obj['bars']==original, ('raw_normalization_not_exact',date)
        assert sum(map(len,obj['bars'].values()))==record['returned_bar_count']
        for symbol,bars in original.items():
            row=cover[date+'__'+symbol]
            counts['coverage_rows']+=1
            assert row['returned_bars']==len(bars)
            assert row['request_complete']==obj['request_complete'] and row['request_status']==obj['status']
            actual=[];nonminute=0
            for bar in bars:
                t=bar['t']
                dt=datetime.fromisoformat(t.replace('Z','+00:00'))
                assert dt.tzinfo is not None
                frac=re.search(r'\d{2}:\d{2}:\d{2}\.([0-9]+)',t)
                fractional_nonzero=bool(frac and int(frac.group(1)))
                if dt.second or fractional_nonzero:nonminute+=1
                else:actual.append(stamp(dt))
            present=set(actual)
            # Non-minute observations are not silently counted as exact bars.
            assert row['early30_present']==sum(t in present for t in expected[:30])
            assert row['search90_present']==sum(t in present for t in expected[:90])
            assert row['missing_regular_session_timestamps']==[t for t in expected if t not in present]
            assert len(row['nonminute_timestamps'])==nonminute
            assert row['no_forward_fill_or_deduplication'] is True
            counts['normalized_bars']+=len(bars)
        counts['normalized_day_hashes_verified']+=1
    assert len(allrequests)==len(set(allrequests))
    authorization=read(ROOT/'acquisition-authorization.json')
    assert sha(ROOT/'acquisition-authorization.json')==manifest['authorization_sha256']
    assert sha(ROOT/'collect_minutes.py')==manifest['collector_sha256']
    for filename,expected in authorization['bound_files'].items():assert sha(ROOT/filename)==expected
    result={'audit_version':'independent_source_audit_v1','completed_at_utc':datetime.now(timezone.utc).isoformat(),'passed':True,
        'counts':dict(counts),'date_statuses':dict(statuses),'registered_case_count':len(registry),
        'minute_source_manifest_sha256':sha(ROOT/'minute-source-manifest.json'),
        'coverage_sha256':sha(ROOT/'symbol-session-coverage.json'),'auditor_code_sha256':sha(Path(__file__)),
        'checks':['Every downloaded success/error body hash and bytecount','All raw successful page bars joined without change into normalized daily files','Every registered symbol-day retained in source coverage including all-missing failures','Whole requested regular session windows use correct New York DST and exclude close timestamp','Early30 and search90 missing timestamps independently reconstructed; no fractional-nanosecond rounding into exact bar','Read-only documented SIP raw minute requests; no account/order paths'],'limitations':['Source hashes establish downloaded bytes and reproducibility joins, not correctness of provider historical prices.','No verified realtime arrival, quote spread or executable fill.']}
    (ROOT/'independent-source-audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result),flush=True)
if __name__=='__main__':main()
