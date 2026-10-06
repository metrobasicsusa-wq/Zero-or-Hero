"""Offline verification of the fixed parent raw-bar archives and session coverage."""
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parent
PRIOR = ROOT.parent / 's500-ep-paths-20261006'
NY = ZoneInfo('America/New_York')


def read(path): return json.loads(Path(path).read_text())
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def digest(obj): return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
def save(name, obj): (ROOT / name).write_text(json.dumps(obj, indent=2) + '\n')


def main():
    design = read(ROOT / 'study-design.json')
    manifest = read(PRIOR / 'holding-input-manifest.json')
    assert sha(PRIOR / 'holding-input-manifest.json') == design['input_sha256']['paths/holding-input-manifest.json']
    assert manifest['all_pages_complete'] and not manifest['failures']
    assert sha(PRIOR / 'holding-market.json') == design['input_sha256']['paths/holding-market.json']
    plan = read(PRIOR / 'holding-input-design.json')
    calendar = {r['date']: r for r in plan['full_calendar']}
    expected = {(date, c['symbol']) for c in plan['cases'] for date in c['session_dates']}
    market = read(PRIOR / 'holding-market.json')
    coverage, verified_pages, records = {}, [], 0
    for request in manifest['records']:
        day = request['date']; session = calendar[day]
        params = request['parameters']; symbols = params['symbols'].split(',')
        assert len(set(symbols)) == len(symbols)
        opening = datetime.fromisoformat(day + 'T' + session['open']).replace(tzinfo=NY)
        closing = datetime.fromisoformat(day + 'T' + session['close']).replace(tzinfo=NY)
        assert request['route'] == '/v2/stocks/bars' and request['pages_complete']
        assert params['feed'] == 'sip' and params['adjustment'] == 'raw' and params['timeframe'] == '1Min'
        assert params['sort'] == 'asc' and params['limit'] == 10000
        assert datetime.fromisoformat(params['start']) == opening
        assert datetime.fromisoformat(params['end']) == closing - timedelta(minutes=1)
        base = digest({'route': request['route'], 'parameters': params})
        assert request['request_sha256'] == base
        page_params, tokens, seen, counts = dict(params), set(), set(), Counter()
        for number, page in enumerate(request['pages']):
            path = PRIOR / 'raw-bars' / request['task_id'] / page['name']
            assert path.stat().st_size == page['bytes'] and sha(path) == page['sha256']
            stored = read(path)
            assert stored['page_number'] == number and stored['base_request_sha256'] == base
            assert stored['page_request_sha256'] == digest({'route': request['route'], 'parameters': page_params})
            payload = stored['response']
            assert isinstance(payload['bars'], dict) and set(payload['bars']) <= set(symbols)
            assert sum(len(v) for v in payload['bars'].values()) == page['records']
            for symbol, bars in payload['bars'].items():
                for bar in bars:
                    assert re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00(?:\.0{1,9})?(?:Z|[+-]\d{2}:\d{2})', bar['t']), 'non_exact_minute_timestamp'
                    t = datetime.fromisoformat(bar['t'].replace('Z', '+00:00'))
                    offset = (t - opening).total_seconds() / 60
                    assert int(offset) == offset and opening <= t < closing
                    key = (symbol, int(offset)); assert key not in seen
                    seen.add(key)
                    assert market[day][symbol][str(int(offset))] == bar
                    counts[symbol] += 1; records += 1
            token = payload['next_page_token']
            assert token is None or isinstance(token, str) and token
            if token is None:
                assert number == len(request['pages']) - 1
            else:
                assert token not in tokens
                tokens.add(token); page_params = {**params, 'page_token': token}
            verified_pages.append({'task_id': request['task_id'], **page})
        assert token is None
        for symbol in symbols:
            assert len(market.get(day, {}).get(symbol, {})) == counts[symbol]
            target = coverage.setdefault(day, {})
            assert symbol not in target
            target[symbol] = {'request_complete': True, 'regular_window_covered': True,
                'request_ids': [request['task_id']], 'bars_returned': counts[symbol],
                'scheduled_minutes': int((closing - opening).total_seconds() / 60)}
    actual = {(day, symbol) for day, items in coverage.items() for symbol in items}
    assert actual == expected
    assert records == manifest['data_records']
    result = {'days': coverage, 'unique_symbol_sessions': len(actual),
        'planned_case_session_pairs': sum(len(c['session_dates']) for c in plan['cases']),
        'verified_raw_pages': len(verified_pages), 'verified_bar_records': records,
        'unreturned_symbol_minute_slots': sum(r['scheduled_minutes'] - r['bars_returned'] for items in coverage.values() for r in items.values()),
        'all_requested_windows_and_cached_assembly_verified': True,
        'source_archive_exhaustiveness_proven': False,
        'scope': 'Request/page integrity only; absence of a bar is not proof of no trading, no stop risk, no halt or point-in-time identity.',
        'input_sha256': {'parent_manifest': sha(PRIOR / 'holding-input-manifest.json'),
                         'parent_holding_market': sha(PRIOR / 'holding-market.json'),
                         'parent_holding_plan': sha(PRIOR / 'holding-input-design.json'),
                         'study_design': sha(ROOT / 'study-design.json'), 'preparation_code': sha(ROOT / 'prepare_event_inputs.py')}}
    save('verified-symbol-sessions.json', result)
    save('source-verification.json', {'verified_pages': verified_pages,
        'coverage_sha256': sha(ROOT / 'verified-symbol-sessions.json'), 'original_parent_bytes_changed': False,
        'known_source_limits': ['Current asof/default symbol mapping', 'No historical archive completeness proof',
                               'SIP/participant time documentation conflict', 'Original unsupported corporate actions retained']})
    print(json.dumps({k: v for k, v in result.items() if k not in ['days', 'input_sha256']}, indent=2))


if __name__ == '__main__': main()
