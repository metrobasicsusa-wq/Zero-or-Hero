"""Shared rate-limited, bounded market/news GET collection with hash checkpoints."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
LOCK = threading.Lock()
NEXT = 0.


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError('market_redirect_rejected')


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def atomic(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(obj, separators=(',', ':')) + '\n')
    temp.replace(path)


def get_json(route, params):
    global NEXT
    allowed = {'/v1beta1/news': {'symbols', 'start', 'end', 'sort', 'limit', 'include_content', 'exclude_contentless', 'page_token'},
               '/v2/stocks/bars': {'symbols', 'timeframe', 'start', 'end', 'limit', 'adjustment', 'feed', 'sort', 'page_token'}}
    if route not in allowed or not set(params) <= allowed[route]:
        raise ValueError('unapproved_read_only_market_route_or_parameter')
    if params.get('sort') != 'asc':
        raise ValueError('sort_must_be_ascending')
    if route == '/v1beta1/news':
        assert params.get('limit') == 50 and params.get('include_content') == 'false'
    else:
        assert params.get('limit') == 10000 and params.get('timeframe') == '1Min'
        assert params.get('adjustment') == 'raw' and params.get('feed') == 'sip'
    for attempt in range(5):
        with LOCK:
            wait = max(0., NEXT - time.monotonic())
            NEXT = max(NEXT, time.monotonic()) + .45
        if wait:
            time.sleep(wait)
        request = urllib.request.Request('https://data.alpaca.markets' + route + '?' + urllib.parse.urlencode(params),
            headers={'APCA-API-KEY-ID': os.environ['ALPACA_500_API_KEY'],
                     'APCA-API-SECRET-KEY': os.environ['ALPACA_500_SECRET_KEY']}, method='GET')
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=40) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code not in [429, 500, 502, 503, 504]:
                raise RuntimeError('market_http_' + str(error.code)) from None
        except (TimeoutError, urllib.error.URLError):
            pass
        time.sleep(min(2 ** attempt, 8))
    raise RuntimeError('bounded_retries_exhausted')


def task_spec(stage, task):
    if stage == 'news':
        return '/v1beta1/news', {'symbols': task['symbol'], 'start': task['news_request_start'],
            'end': task['news_request_end'], 'sort': 'asc', 'limit': 50, 'include_content': 'false',
            'exclude_contentless': 'false'}, task['candidate_id'], 40
    if stage == 'bars':
        return '/v2/stocks/bars', {'symbols': ','.join(task['symbols']), 'start': task['start'], 'end': task['end'],
            'sort': 'asc', 'limit': 10000, 'timeframe': '1Min', 'adjustment': 'raw', 'feed': 'sip'}, task['task_id'], 50
    raise ValueError('unknown_stage')


def validate_payload(stage, payload, task):
    if not isinstance(payload, dict) or 'next_page_token' not in payload:
        raise ValueError('invalid_paginated_response')
    if payload['next_page_token'] is not None and not isinstance(payload['next_page_token'], str):
        raise ValueError('invalid_page_token_type')
    if stage == 'news':
        if 'news' not in payload or not (payload['news'] is None or isinstance(payload['news'], list)):
            raise ValueError('invalid_news_response')
        return len(payload['news'] or [])
    if not isinstance(payload.get('bars'), dict) or not set(payload['bars']) <= set(task['symbols']):
        raise ValueError('invalid_market_symbols_or_schema')
    if not all(isinstance(v, list) for v in payload['bars'].values()):
        raise ValueError('invalid_bar_list')
    return sum(len(v) for v in payload['bars'].values())


def fetch_task(stage, task):
    route, params, task_id, budget = task_spec(stage, task)
    base = digest({'route': route, 'parameters': params})
    folder = ROOT / ('raw-' + stage) / task_id
    complete = folder / 'complete.json'
    if complete.exists():
        result = json.loads(complete.read_text())
        assert result['request_sha256'] == base and result['task_id'] == task_id and result['stage'] == stage
        for p in result['pages']:
            assert hashlib.sha256((folder / p['name']).read_bytes()).hexdigest() == p['sha256']
        return result
    request = dict(params)
    pages, seen = [], set()
    for i in range(budget):
        path = folder / ('page-%04d.json' % i)
        page_hash = digest({'route': route, 'parameters': request})
        if path.exists():
            stored = json.loads(path.read_text())
            assert stored['base_request_sha256'] == base and stored['page_request_sha256'] == page_hash and stored['page_number'] == i
            payload = stored['response']
        else:
            payload = get_json(route, request)
            validate_payload(stage, payload, task)
            atomic(path, {'base_request_sha256': base, 'page_request_sha256': page_hash,
                'page_number': i, 'retrieved_at': datetime.now(timezone.utc).isoformat(), 'response': payload})
        count = validate_payload(stage, payload, task)
        pages.append({'name': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                      'bytes': path.stat().st_size, 'records': count})
        token = payload['next_page_token']
        if not token:
            result = {'stage': stage, 'task_id': task_id, 'route': route, 'parameters': params,
                'request_sha256': base, 'pages': pages, 'pages_complete': True,
                'finished_at': datetime.now(timezone.utc).isoformat()}
            atomic(complete, result)
            return result
        if token in seen:
            raise RuntimeError('pagination_cycle')
        seen.add(token)
        request['page_token'] = token
    raise RuntimeError('page_budget_reached_partial_retained')


def load_task(stage, task_id):
    folder = ROOT / ('raw-' + stage) / task_id
    meta = json.loads((folder / 'complete.json').read_text())
    payloads = []
    for p in meta['pages']:
        data = (folder / p['name']).read_bytes()
        assert hashlib.sha256(data).hexdigest() == p['sha256']
        payloads.append(json.loads(data)['response'])
    return payloads


def download_stage(stage, tasks):
    records, failures = [], []
    started = time.monotonic()
    print(json.dumps({'stage': stage, 'total_tasks': len(tasks)}), flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch_task, stage, task): task for task in tasks}
        for i, future in enumerate(as_completed(futures), 1):
            task = futures[future]
            try:
                records.append(future.result())
            except Exception as error:
                failures.append({'task_id': task_spec(stage, task)[2], 'reason': type(error).__name__ + ':' + str(error)[:160]})
            if i % 50 == 0 or i == len(futures):
                progress = {'stage': stage, 'completed_tasks': i, 'total_tasks': len(tasks),
                    'failures': failures, 'elapsed_seconds': round(time.monotonic() - started),
                    'updated_at': datetime.now(timezone.utc).isoformat()}
                atomic(ROOT / (stage + '-progress.json'), progress)
                print(json.dumps(progress), flush=True)
    manifest = {'stage': stage, 'records': sorted(records, key=lambda r: r['task_id']), 'failures': failures,
        'all_pages_complete': not failures, 'request_tasks': len(tasks),
        'data_records': sum(p['records'] for r in records for p in r['pages']),
        'raw_bytes': sum(p['bytes'] for r in records for p in r['pages']),
        'completed_at': datetime.now(timezone.utc).isoformat(),
        'input_sha256': {n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest()
                         for n in ['candidates.json', 'bar-tasks.json', 'study-design.json']}}
    atomic(ROOT / (stage + '-input-manifest.json'), manifest)
    return manifest


def assemble_bars(manifest):
    market = {}
    ny = ZoneInfo('America/New_York')
    for record in manifest['records']:
        day = record['task_id'].split('__')[0]
        target = market.setdefault(day, {})
        for payload in load_task('bars', record['task_id']):
            for symbol, bars in payload['bars'].items():
                series = target.setdefault(symbol, {})
                for bar in bars:
                    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00(?:\.0{1,9})?(?:Z|[+-]\d{2}:\d{2})', bar['t']):
                        raise ValueError('bar_timestamp_not_exact_minute')
                    stamp = datetime.fromisoformat(bar['t'].replace('Z', '+00:00')).astimezone(ny)
                    offset = stamp.hour * 60 + stamp.minute - 570
                    assert stamp.date().isoformat() == day and stamp.second == stamp.microsecond == 0
                    assert 0 <= offset <= 92
                    if str(offset) in series:
                        raise ValueError('duplicate_market_minute')
                    series[str(offset)] = bar
    atomic(ROOT / 'minute-market.json', market)
    print(json.dumps({'bars_assembled': True, 'stock_days': sum(len(v) for v in market.values())}), flush=True)


def main():
    design = json.loads((ROOT / 'study-design.json').read_text())
    for name in ['candidates.json', 'bar-tasks.json']:
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == design['input_sha256'][name]
    bars = json.loads((ROOT / 'bar-tasks.json').read_text())['tasks']
    candidates = json.loads((ROOT / 'candidates.json').read_text())['candidates']
    manifest = download_stage('bars', bars)
    assemble_bars(manifest)
    download_stage('news', candidates)


if __name__ == '__main__':
    main()
