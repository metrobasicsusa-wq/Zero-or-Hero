"""Exact-symbol historical market GETs; bounded pagination, private raw archives."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
LOCK = threading.Lock()
NEXT = 0.0


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def atomic(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(obj, separators=(',', ':')) + '\n')
    tmp.replace(path)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError('market_redirect_rejected')


def get_json(route, params):
    global NEXT
    match = re.fullmatch(r'/v2/stocks/([A-Z0-9.\-]+)/(bars|trades|quotes)', route)
    meta = re.fullmatch(r'/v2/stocks/meta/conditions/(trade|quote)', route)
    if meta:
        assert set(params) == {'tape'} and params['tape'] in ['A', 'B', 'C']
    else:
        assert match is not None, 'read_only_route_allowlist'
        allowed = {'start', 'end', 'sort', 'limit', 'feed', 'page_token'}
        if match[2] == 'bars':
            allowed |= {'timeframe', 'adjustment'}
            assert params['timeframe'] == '1Min' and params['adjustment'] == 'raw'
        assert set(params) <= allowed and params['feed'] == 'sip'
        assert params['sort'] == 'asc' and params['limit'] == 10000
    for attempt in range(5):
        with LOCK:
            delay = max(0, NEXT - time.monotonic())
            NEXT = max(NEXT, time.monotonic()) + .45
        if delay:
            time.sleep(delay)
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
        if attempt < 4:
            time.sleep(min(2 ** attempt, 8))
    raise RuntimeError('bounded_retries_exhausted')


def request_spec(point, stream):
    assert stream in ['bars', 'trades', 'quotes']
    assert re.fullmatch(r'[A-Z0-9.\-]+', point['symbol'])
    params = {'start': point['request_start_utc'], 'end': point['request_end_inclusive_utc'],
              'sort': 'asc', 'limit': 10000, 'feed': 'sip'}
    if stream == 'bars':
        params.update(timeframe='1Min', adjustment='raw')
    return '/v2/stocks/' + point['symbol'] + '/' + stream, params


def validate(payload, point, stream):
    assert isinstance(payload, dict) and payload.get('symbol') == point['symbol'], 'wrong_symbol_or_schema'
    assert stream in payload and (payload[stream] is None or isinstance(payload[stream], list)), 'missing_stream'
    assert 'next_page_token' in payload and (payload['next_page_token'] is None or
        isinstance(payload['next_page_token'], str) and len(payload['next_page_token']) > 0), 'invalid_continuation'
    return len(payload[stream] or [])


def verify_cached_outcome(point, stream, old):
    """Replay immutable page bytes and every request/token; never trust a cached status."""
    route, params = request_spec(point, stream)
    base = digest({'route': route, 'parameters': params})
    limit = 1 if stream == 'bars' else 10
    expected = {'point_id': point['point_id'], 'stream': stream, 'symbol': point['symbol'],
                'route': route, 'parameters': params, 'request_sha256': base, 'page_budget': limit}
    for key, value in expected.items():
        assert old[key] == value, 'cached_outcome_' + key
    pages = old['pages']
    assert isinstance(pages, list) and len(pages) <= limit, 'cached_page_budget'
    folder = ROOT / ('raw-' + stream) / point['point_id']
    request_params, seen, count_total = dict(params), set(), 0
    derived_status = 'failed'
    for number, item in enumerate(pages):
        expected_name = 'page-%04d.json' % number
        assert item['name'] == expected_name, 'cached_page_name_or_order'
        path = folder / expected_name
        body = path.read_bytes()
        assert len(body) == item['bytes'], 'cached_page_bytes'
        assert hashlib.sha256(body).hexdigest() == item['sha256'], 'cached_page_sha256'
        saved = json.loads(body)
        page_hash = digest({'route': route, 'parameters': request_params})
        assert saved['base_request_sha256'] == base, 'cached_page_base'
        assert saved['page_request_sha256'] == item['request_sha256'] == page_hash, 'cached_page_request_hash'
        assert saved['request_parameters'] == request_params, 'cached_page_parameters'
        assert saved['page_number'] == number, 'cached_page_number'
        count = validate(saved['response'], point, stream)
        token = saved['response']['next_page_token']
        assert item['records'] == count, 'cached_page_count'
        assert item['has_next_token'] is (token is not None), 'cached_page_continuation_flag'
        assert item['next_token_sha256'] == (digest(token) if token is not None else None), 'cached_next_token_hash'
        count_total += count
        if token is None:
            assert number == len(pages) - 1, 'cached_pages_after_terminal'
            derived_status = 'complete'
            break
        if token in seen:
            assert number == len(pages) - 1, 'cached_pages_after_cycle'
            derived_status = 'failed'
            break
        seen.add(token)
        request_params = {**params, 'page_token': token}
        if number == limit - 1:
            derived_status = 'truncated'
    assert old['records'] == count_total, 'cached_total_count'
    assert old['status'] == derived_status, 'cached_completion_status'
    assert old['pages_complete'] is (derived_status == 'complete'), 'cached_completion_flag'
    if derived_status == 'failed':
        assert isinstance(old['error'], str) and old['error'], 'cached_missing_failure_reason'
    else:
        assert old['error'] is None, 'cached_unexpected_failure_reason'
    return old


def fetch(point, stream):
    route, params = request_spec(point, stream)
    base = digest({'route': route, 'parameters': params})
    folder = ROOT / ('raw-' + stream) / point['point_id']
    outcome = folder / 'outcome.json'
    if outcome.exists():
        old = json.loads(outcome.read_text())
        return verify_cached_outcome(point, stream, old)
    pages, seen, request_params = [], set(), dict(params)
    limit = 1 if stream == 'bars' else 10
    status, error = 'truncated', None
    for number in range(limit):
        path = folder / ('page-%04d.json' % number)
        page_hash = digest({'route': route, 'parameters': request_params})
        try:
            if path.exists():
                saved = json.loads(path.read_text())
                assert saved['base_request_sha256'] == base and saved['page_request_sha256'] == page_hash
                assert saved['request_parameters'] == request_params, 'partial_cached_page_parameters'
                assert saved['page_number'] == number
                payload = saved['response']
            else:
                payload = get_json(route, request_params)
                # Keep even malformed responses privately for diagnosis.
                atomic(path, {'base_request_sha256': base, 'page_request_sha256': page_hash,
                    'page_number': number, 'retrieved_at': datetime.now(timezone.utc).isoformat(),
                    'request_parameters': request_params, 'response': payload})
            count = validate(payload, point, stream)
            token = payload['next_page_token']
            pages.append({'name': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                          'bytes': path.stat().st_size, 'records': count, 'request_sha256': page_hash,
                          'has_next_token': bool(token), 'next_token_sha256': digest(token) if token else None})
            if not token:
                status = 'complete'
                break
            if token in seen:
                raise RuntimeError('pagination_cycle')
            seen.add(token)
            request_params = {**params, 'page_token': token}
        except Exception as exc:
            status = 'failed'
            error = type(exc).__name__ + ':' + str(exc)[:160]
            break
    result = {'point_id': point['point_id'], 'stream': stream, 'symbol': point['symbol'],
              'route': route, 'parameters': params, 'request_sha256': base, 'pages': pages,
              'page_budget': limit, 'status': status, 'pages_complete': status == 'complete',
              'error': error, 'records': sum(p['records'] for p in pages),
              'finished_at': datetime.now(timezone.utc).isoformat()}
    atomic(outcome, result)
    return result


def main():
    design = json.loads((ROOT / 'study-design.json').read_text())
    points = json.loads((ROOT / 'points.json').read_text())['points']
    assert len(points) == 38 and hashlib.sha256((ROOT / 'points.json').read_bytes()).hexdigest() == design['points_sha256']
    metadata = ROOT / 'condition-metadata.json'
    if not metadata.exists():
        rows = []
        for kind in ['trade', 'quote']:
            for tape in ['A', 'B', 'C']:
                try:
                    value = get_json('/v2/stocks/meta/conditions/' + kind, {'tape': tape})
                    rows.append({'kind': kind, 'tape': tape, 'response': value, 'retrieved_at': datetime.now(timezone.utc).isoformat(), 'status': 'complete'})
                except Exception as exc:
                    rows.append({'kind': kind, 'tape': tape, 'status': 'failed', 'error': type(exc).__name__ + ':' + str(exc)[:120]})
        atomic(metadata, rows)
    records = []
    started = datetime.now(timezone.utc).isoformat()
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(fetch, point, stream) for point in points for stream in ['bars', 'trades', 'quotes']]
        for future in as_completed(futures):
            records.append(future.result())
            if len(records) % 15 == 0 or len(records) == len(futures):
                progress = {'windows_done': len(records), 'windows_total': len(futures),
                            'incomplete': sum(r['status'] != 'complete' for r in records),
                            'records': sum(r['records'] for r in records), 'updated_at': datetime.now(timezone.utc).isoformat()}
                atomic(ROOT / 'download-progress.json', progress)
                print(json.dumps(progress), flush=True)
    atomic(ROOT / 'input-manifest.json', {'started_at': started, 'finished_at': datetime.now(timezone.utc).isoformat(),
        'records': sorted(records, key=lambda r: (r['point_id'], r['stream'])), 'windows': len(records),
        'all_pages_complete': all(r['pages_complete'] for r in records),
        'input_sha256': {n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest() for n in ['study-design.json', 'points.json', 'collect_windows.py', 'condition-metadata.json']}})


if __name__ == '__main__':
    main()
