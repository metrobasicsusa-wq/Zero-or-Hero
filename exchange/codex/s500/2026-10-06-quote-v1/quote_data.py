"""Bounded, checkpointed historical market-data GET requests only."""
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
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parent
LOCK = threading.Lock()
NEXT = 0.


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError('market_redirect_rejected')


def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, separators=(',', ':')) + '\n')
    temp.replace(path)


def get_json(path, params):
    global NEXT
    is_quotes = re.fullmatch(r'/v2/stocks/[A-Z0-9.\-]+/quotes', path) is not None
    is_conditions = path == '/v2/stocks/meta/conditions/quote'
    if not (is_quotes or is_conditions):
        raise ValueError('unapproved_market_data_route')
    allowed = {'start', 'end', 'limit', 'feed', 'sort', 'page_token'} if is_quotes else {'tape'}
    if not set(params) <= allowed:
        raise ValueError('unapproved_market_data_parameter')
    if is_quotes and (params.get('feed') != 'sip' or params.get('sort') != 'asc' or params.get('limit') != 10000):
        raise ValueError('unsupported_quote_request')
    if is_conditions and params.get('tape') not in ['A', 'B', 'C']:
        raise ValueError('invalid_tape')
    for attempt in range(5):
        with LOCK:
            delay = max(0, NEXT - time.monotonic())
            NEXT = max(NEXT, time.monotonic()) + .45
        if delay:
            time.sleep(delay)
        request = urllib.request.Request('https://data.alpaca.markets' + path + '?' + urllib.parse.urlencode(params),
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
    raise RuntimeError('market_retry_exhausted')


def fetch_window(point):
    symbol = point['symbol']
    if not re.fullmatch(r'[A-Z0-9.\-]+', symbol):
        raise ValueError('invalid_symbol')
    route = '/v2/stocks/' + symbol + '/quotes'
    params = {'start': point['request_start'], 'end': point['request_end'],
              'limit': 10000, 'feed': 'sip', 'sort': 'asc'}
    digest = sha({'route': route, 'parameters': params})
    folder = ROOT / 'raw-quotes' / point['id']
    meta = folder / 'complete.json'
    if meta.exists():
        result = json.loads(meta.read_text())
        assert result['request_sha256'] == digest
        for p in result['pages']:
            assert hashlib.sha256((folder / p['name']).read_bytes()).hexdigest() == p['sha256']
        return result
    request_params = dict(params)
    pages = []
    seen = set()
    for i in range(50):
        path = folder / ('page-%04d.json' % i)
        request_hash = sha({'route': route, 'parameters': request_params})
        if path.exists():
            stored = json.loads(path.read_text())
            assert stored['page_request_sha256'] == request_hash and stored['page_number'] == i
            assert stored['base_request_sha256'] == digest
            payload = stored['response']
        else:
            payload = get_json(route, request_params)
            assert payload.get('symbol', symbol) == symbol
            assert isinstance(payload.get('quotes'), list) or payload.get('quotes') is None
            atomic(path, {'page_request_sha256': request_hash, 'base_request_sha256': digest,
                          'page_number': i, 'retrieved_at': datetime.now(timezone.utc).isoformat(), 'response': payload})
        pages.append({'name': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                      'bytes': path.stat().st_size, 'quotes': len(payload.get('quotes') or [])})
        token = payload.get('next_page_token')
        if not token:
            result = {'point_id': point['id'], 'route': route, 'parameters': params,
                      'request_sha256': digest, 'pages': pages,
                      'finished_at': datetime.now(timezone.utc).isoformat(), 'pages_complete': True}
            atomic(meta, result)
            return result
        if token in seen:
            raise RuntimeError('pagination_cycle')
        seen.add(token)
        request_params['page_token'] = token
    raise RuntimeError('page_budget_reached')


def load_window(point_id):
    folder = ROOT / 'raw-quotes' / point_id
    meta = json.loads((folder / 'complete.json').read_text())
    rows = []
    for p in meta['pages']:
        data = (folder / p['name']).read_bytes()
        assert hashlib.sha256(data).hexdigest() == p['sha256']
        rows.extend(json.loads(data)['response'].get('quotes') or [])
    return rows


def main():
    sample = json.loads((ROOT / 'sample.json').read_text())
    conditions = []
    for tape in ['A', 'B', 'C']:
        result = get_json('/v2/stocks/meta/conditions/quote', {'tape': tape})
        conditions.append({'tape': tape, 'retrieved_at': datetime.now(timezone.utc).isoformat(), 'response': result})
    atomic(ROOT / 'quote-condition-metadata.json', conditions)
    records, failures = [], []
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch_window, p): p for p in sample['points']}
        for i, future in enumerate(as_completed(futures), 1):
            p = futures[future]
            try:
                records.append(future.result())
            except Exception as e:
                failures.append({'point_id': p['id'], 'error': type(e).__name__ + ':' + str(e)[:180]})
            if i % 25 == 0 or i == len(futures):
                progress = {'completed_windows': i, 'total_windows': len(futures), 'failures': failures,
                            'elapsed_seconds': round(time.monotonic() - start),
                            'updated_at': datetime.now(timezone.utc).isoformat()}
                atomic(ROOT / 'download-progress.json', progress)
                print(json.dumps(progress), flush=True)
    manifest = {'sample_sha256': hashlib.sha256((ROOT / 'sample.json').read_bytes()).hexdigest(),
                'design_sha256': hashlib.sha256((ROOT / 'study-design.json').read_bytes()).hexdigest(),
                'records': sorted(records, key=lambda x: x['point_id']), 'failures': failures,
                'all_pages_complete': not failures, 'raw_quote_count': sum(p['quotes'] for r in records for p in r['pages']),
                'raw_response_bytes': sum(p['bytes'] for r in records for p in r['pages']),
                'completed_at': datetime.now(timezone.utc).isoformat()}
    atomic(ROOT / 'quote-input-manifest.json', manifest)
    print(json.dumps({k: v for k, v in manifest.items() if k != 'records'}), flush=True)
    if failures:
        raise RuntimeError('quote_windows_incomplete')


if __name__ == '__main__':
    main()
