"""Collect fixed holding windows and one fresh exact-symbol request per unknown gate."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib
import importlib.util
import json
import re
import time

ROOT = Path(__file__).resolve().parent
PRIOR = ROOT.parent / 's500-news-20261006'
NY = ZoneInfo('America/New_York')
spec = importlib.util.spec_from_file_location('parent_readonly_transport', PRIOR / 'download_inputs.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
core.ROOT = ROOT


def read(path):
    return json.loads(Path(path).read_text())


def digest_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stamp(day, hm):
    return datetime.fromisoformat(day + 'T' + hm + ':00').replace(tzinfo=NY).astimezone(timezone.utc).isoformat()


def request_task(task, stage):
    return {**task, 'task_id': stage + '__' + task['task_id'],
            'start': stamp(task['date'], task['start']), 'end': stamp(task['date'], task['end'])}


def collect(stage, tasks):
    manifest_path = ROOT / (stage + '-input-manifest.json')
    requests = [request_task(t, stage) for t in tasks]
    records, failures = [], []
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(core.fetch_task, 'bars', t): t for t in requests}
        for n, future in enumerate(as_completed(futures), 1):
            task = futures[future]
            try:
                result = future.result()
                records.append({**result, 'date': task['date']})
            except Exception as e:
                failures.append({'task_id': task['task_id'], 'reason': type(e).__name__ + ':' + str(e)[:160]})
            if n % 25 == 0 or n == len(requests):
                progress = {'stage': stage, 'done': n, 'total': len(requests),
                    'failures': failures, 'elapsed_seconds': round(time.monotonic() - started),
                    'updated_at': datetime.now(timezone.utc).isoformat()}
                core.atomic(ROOT / (stage + '-progress.json'), progress)
                print(json.dumps(progress), flush=True)
    manifest = {'stage': stage, 'records': sorted(records, key=lambda r: r['task_id']),
        'failures': failures, 'all_pages_complete': not failures, 'requests': requests,
        'data_records': sum(p['records'] for r in records for p in r['pages']),
        'raw_bytes': sum(p['bytes'] for r in records for p in r['pages']),
        'completed_at': datetime.now(timezone.utc).isoformat(),
        'input_sha256': {'study-design.json': digest_file(ROOT / 'study-design.json'),
            'holding-input-design.json': digest_file(ROOT / 'holding-input-design.json'),
            'parent_download_inputs.py': digest_file(PRIOR / 'download_inputs.py'),
            'collect_inputs.py': digest_file(ROOT / 'collect_inputs.py')}}
    core.atomic(manifest_path, manifest)
    return manifest


def assemble(manifest):
    market = {}
    for record in manifest['records']:
        day = record['date']
        start = datetime.fromisoformat(day + 'T09:30:00').replace(tzinfo=NY)
        lo, hi = (datetime.fromisoformat(record['parameters'][k]) for k in ('start', 'end'))
        for payload in core.load_task('bars', record['task_id']):
            for symbol, bars in payload['bars'].items():
                series = market.setdefault(day, {}).setdefault(symbol, {})
                for bar in bars:
                    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00(?:\.0{1,9})?(?:Z|[+-]\d{2}:\d{2})', bar['t']):
                        raise ValueError('non_exact_minute_timestamp')
                    moment = datetime.fromisoformat(bar['t'].replace('Z', '+00:00'))
                    if not lo <= moment <= hi:
                        raise ValueError('bar_outside_bound_request')
                    offset = int((moment - start).total_seconds() // 60)
                    if str(offset) in series:
                        raise ValueError('duplicate_symbol_minute')
                    series[str(offset)] = bar
    core.atomic(ROOT / (manifest['stage'] + '-market.json'), market)
    return market


def prepare_cached_inputs():
    design = read(ROOT / 'holding-input-design.json')
    wanted = {x['symbol'] for x in design['cases']}
    broad = ROOT.parent / 's500-broad-20261006'
    inventory = read(ROOT.parent / 's500-orb-20261006/ep-candidate-inventory.json')
    daily, sources = {}, {}
    for path in sorted((broad / 'raw').glob('batch-*.json')):
        key = 'raw/' + path.name
        sha = digest_file(path)
        assert sha == inventory['inputs_sha256'][key]
        sources[key] = sha
        for symbol, bars in read(path)['bars'].items():
            if symbol not in wanted:
                continue
            assert symbol not in daily
            target = daily.setdefault(symbol, {})
            for bar in bars:
                day = datetime.fromisoformat(bar['t'].replace('Z', '+00:00')).astimezone(NY).date().isoformat()
                assert day not in target
                target[day] = bar
    action_path = ROOT.parent / 's500-aggressive-20261006/company-actions.json'
    actions = read(action_path)
    by_symbol = {s: [] for s in wanted}
    unmapped = []
    for i, action in enumerate(actions['rows']):
        symbols = {s for s in [action.get('symbol'), action.get('new_symbol')] if s}
        if not symbols:
            unmapped.append({'source_row_index': i, 'type': action['type'], 'ex_date': action.get('ex_date')})
        for symbol in symbols & wanted:
            by_symbol[symbol].append({**action, 'source_row_index': i})
    core.atomic(ROOT / 'daily-market-private.json', daily)
    core.atomic(ROOT / 'corporate-events.json', by_symbol)
    core.atomic(ROOT / 'cached-source-manifest.json', {'daily_raw_inputs_sha256': sources,
        'daily-market-private.json': digest_file(ROOT / 'daily-market-private.json'),
        'corporate-events.json': digest_file(ROOT / 'corporate-events.json'),
        'parent_actions_sha256': digest_file(action_path), 'daily_symbols': len(daily),
        'daily_bars': sum(len(v) for v in daily.values()),
        'unmapped_actions_not_assigned_to_an_issuer': unmapped,
        'corporate_source_limitations': actions['notes'],
        'daily_gate_coverage': inventory['coverage'],
        'daily_screen_counts': inventory['daily_screen_counts'],
        'not_a_point_in_time_or_complete_market_universe': True})


def main():
    design = read(ROOT / 'study-design.json')
    assert design['input_sha256']['holding-input-design.json'] == digest_file(ROOT / 'holding-input-design.json')
    inputs = read(ROOT / 'holding-input-design.json')
    prepare_cached_inputs()
    repair = collect('repair', inputs['unknown_gate_refetch_tasks'])
    assemble(repair)
    holding = collect('holding', inputs['tasks'])
    assemble(holding)
    print(json.dumps({'done': True, 'repair_bars': repair['data_records'], 'holding_bars': holding['data_records']}), flush=True)


if __name__ == '__main__':
    main()
