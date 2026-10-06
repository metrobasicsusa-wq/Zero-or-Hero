"""Derived missing-bar diagnostics, not historical fill reconstruction or PnL."""
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib
import json
import re
from collect_windows import verify_cached_outcome

ROOT = Path(__file__).resolve().parent
PRIOR = ROOT.parent / 's500-ep-paths-20261006'
NY = ZoneInfo('America/New_York')
NS = 1_000_000_000


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path): return json.loads(Path(path).read_text())
def digest(obj): return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
def save(name, obj): (ROOT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')


def timestamp_ns(value):
    m = re.fullmatch(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})', value)
    if not m:
        raise ValueError('timestamp_format')
    dt = datetime.fromisoformat(m[1] + m[3].replace('Z', '+00:00'))
    seconds = (dt - datetime(1970, 1, 1, tzinfo=timezone.utc)) // timedelta(seconds=1)
    return seconds * NS + int((m[2] or '').ljust(9, '0'))


def positive(value):
    try:
        v = Decimal(str(value))
        return v if v.is_finite() and v > 0 else None
    except (InvalidOperation, ValueError):
        return None


def condition_state(row, metadata, rules):
    """Price eligibility only; unknown combinations never default to regular."""
    tape, conditions = row.get('z'), row.get('c')
    if tape not in metadata or not isinstance(conditions, list) or not conditions:
        return 'unknown_conditions'
    if any(not isinstance(c, str) or c not in metadata[tape] for c in conditions):
        return 'unknown_conditions'
    applicable = []
    for condition in conditions:
        match = [r for r in rules if r['condition'] == condition and tape in r['tapes']]
        if len(match) > 1:
            raise ValueError('ambiguous_rule_mapping')
        applicable.append(match[0] if match else None)
    # Most restrictive condition controls. One documented all-price exclusion
    # suffices even if another known condition lacks a parsed aggregation rule.
    if any(r is not None and r['open_close'] is False and r['high_low'] is False for r in applicable):
        return 'documented_price_excluded'
    if all(r is not None and r['open_close'] is True and r['high_low'] is True for r in applicable):
        return 'documented_price_updating'
    return 'unknown_or_partial_price_rule'


def trade_state(row, metadata, rules):
    flag = row.get('u', '__absent__')
    if flag in ['canceled', 'incorrect']:
        return 'provider_invalidated'
    if flag not in ['__absent__', 'corrected']:
        return 'unknown_update_flag'
    if positive(row.get('p')) is None or positive(row.get('s')) is None:
        return 'invalid_price_or_size'
    return condition_state(row, metadata, rules)


def load_window(point, record):
    stream = record['stream']
    verify_cached_outcome(point, stream, record)
    folder = ROOT / ('raw-' + stream) / point['point_id']
    base = {'route': record['route'], 'parameters': record['parameters']}
    assert digest(base) == record['request_sha256']
    request = dict(record['parameters'])
    rows, seen = [], set()
    for number, item in enumerate(record['pages']):
        path = folder / item['name']
        assert path.stat().st_size == item['bytes'] and sha(path) == item['sha256']
        saved = read(path)
        assert saved['page_number'] == number and saved['base_request_sha256'] == digest(base)
        assert saved['request_parameters'] == request
        assert saved['page_request_sha256'] == digest({'route': record['route'], 'parameters': request})
        payload = saved['response']
        assert payload['symbol'] == point['symbol'] and stream in payload and 'next_page_token' in payload
        data = payload[stream] or []
        assert isinstance(data, list) and len(data) == item['records']
        rows.extend(data)
        token = payload['next_page_token']
        assert token is None or isinstance(token, str) and token
        if token is not None:
            assert token not in seen
            seen.add(token)
            request = {**record['parameters'], 'page_token': token}
        else:
            assert number == len(record['pages']) - 1
    assert len(rows) == record['records']
    if record['pages_complete']:
        assert record['status'] == 'complete' and record['pages'] and token is None
    elif record['status'] == 'truncated':
        assert len(record['pages']) == record['page_budget'] and token is not None
    lo, hi = timestamp_ns(point['request_start_utc']), timestamp_ns(point['window_end_exclusive_utc'])
    inside, issues, previous, identities = [], Counter(), None, {}
    for row in rows:
        if stream == 'trades':
            if any(k not in row for k in ['z', 'x', 'i']):
                issues['trade_identity_missing'] += 1
            else:
                identity = (row['z'], row['x'], row['i'])
                body_hash = digest(row)
                if identity in identities:
                    issues['duplicate_trade_identity'] += 1
                    if identities[identity] != body_hash:
                        issues['conflicting_trade_identity'] += 1
                identities[identity] = body_hash
        try:
            t = timestamp_ns(row['t'])
        except (KeyError, TypeError, ValueError):
            issues['invalid_timestamp'] += 1
            continue
        if previous is not None and t < previous:
            issues['not_ascending'] += 1
        previous = t
        if not lo <= t < hi:
            issues['outside_requested_window'] += 1
            continue
        if stream == 'bars' and t % (60 * NS):
            issues['bar_not_minute_aligned'] += 1
            continue
        inside.append((t, row))
    return inside, dict(issues)


def summarize_trades(rows, metadata, rules):
    states = Counter(trade_state(r, metadata, rules) for r in rows)
    current = [r for r in rows if trade_state(r, metadata, rules) not in ['provider_invalidated', 'unknown_update_flag', 'invalid_price_or_size']]
    prices = [positive(r['p']) for r in current]
    sizes = [positive(r['s']) for r in current]
    conditions = defaultdict(lambda: {'count': 0, 'observed_share_volume': Decimal(0)})
    for r in rows:
        key = json.dumps({'tape': r.get('z'), 'conditions': r.get('c'), 'update_flag': r.get('u', '__absent__'), 'state': trade_state(r, metadata, rules)}, sort_keys=True)
        conditions[key]['count'] += 1
        if positive(r.get('s')) is not None:
            conditions[key]['observed_share_volume'] += positive(r['s'])
    return {'records': len(rows), 'states': dict(states), 'current_valid_records': len(current),
            'current_valid_observed_share_volume': str(sum(sizes, Decimal(0))),
            'current_valid_min_price': str(min(prices)) if prices else None,
            'current_valid_max_price': str(max(prices)) if prices else None,
            'condition_groups': [{**json.loads(k), 'records': v['count'], 'observed_share_volume': str(v['observed_share_volume'])} for k, v in sorted(conditions.items())],
            'historical_point_in_time_version_proven': False}


def summarize_quotes(rows):
    # Basic geometry is not an executable quote validator or stop trigger.
    valid = [r for r in rows if positive(r.get('bp')) is not None and positive(r.get('ap')) is not None
             and Decimal(str(r['bp'])) <= Decimal(str(r['ap'])) and positive(r.get('bs')) is not None and positive(r.get('as')) is not None]
    return {'records': len(rows), 'positive_prices_sizes_non_crossed': len(valid),
            'other_geometry': len(rows) - len(valid),
            'condition_groups': [{'tape': k[0], 'conditions': json.loads(k[1]), 'records': n}
                                 for k, n in sorted(Counter((str(r.get('z')), json.dumps(r.get('c'))) for r in rows).items())],
            'size_multiplier_applied': 1, 'executable_capacity_or_fill_inferred': False,
            'continuous_market_or_no_halt_inferred': False}


def analyze_point(point, inputs, parent, metadata, rules):
    streams, integrity = {}, {}
    for stream in ['bars', 'trades', 'quotes']:
        rows, issues = load_window(point, inputs[(point['point_id'], stream)])
        streams[stream] = rows
        integrity[stream] = {'request_status': inputs[(point['point_id'], stream)]['status'], 'issues': issues,
                             'usable_complete': inputs[(point['point_id'], stream)]['pages_complete'] and not issues}
    target = timestamp_ns(point['target_start_utc'])
    target_rows = {k: [r for t, r in rows if target <= t < target + 60 * NS] for k, rows in streams.items()}
    trade_summary = summarize_trades(target_rows['trades'], metadata, rules)
    quote_summary = summarize_quotes(target_rows['quotes'])
    target_dt = datetime.fromisoformat(point['missing_time']).astimezone(NY)
    day = target_dt.date().isoformat()
    old_series = parent.get(day, {}).get(point['symbol'], {})
    offset = target_dt.hour * 60 + target_dt.minute - 570
    assert str(offset) not in old_series, 'parent_target_was_not_missing'
    comparisons = []
    for t, bar in streams['bars']:
        minute_offset = offset + (t - target) // (60 * NS)
        old = old_series.get(str(minute_offset))
        changed = [k for k in ['o', 'h', 'l', 'c', 'v', 'n', 'vw'] if old is not None and old.get(k) != bar.get(k)]
        comparisons.append({'minute_offset_relative_to_target': (t - target) // (60 * NS), 'parent_present': old is not None, 'changed_fields': changed})
    states = trade_summary['states']
    fully_observed = all(v['usable_complete'] for v in integrity.values())
    if len(target_rows['bars']) == 1 and integrity['bars']['usable_complete']:
        classification = 'target_bar_reappeared_version_discrepancy'
    elif len(target_rows['bars']) > 1 or not fully_observed:
        classification = 'source_integrity_or_pagination_unresolved'
    elif not target_rows['trades']:
        classification = 'target_missing_provider_returned_no_trades'
    elif states.get('documented_price_excluded', 0) > 0 and set(states) <= {'documented_price_excluded', 'provider_invalidated'}:
        classification = 'target_missing_price_excluded_trades_consistent_with_documented_rules'
    elif states.get('documented_price_updating', 0) > 0:
        classification = 'target_missing_with_documented_price_updating_conditions_needs_time_or_source_reconciliation'
    else:
        classification = 'target_missing_conditions_or_source_coverage_unresolved'
    per_minute = []
    for delta in range(-2, 3):
        lo, hi = target + delta * 60 * NS, target + (delta + 1) * 60 * NS
        sample = {k: [r for t, r in rows if lo <= t < hi] for k, rows in streams.items()}
        per_minute.append({'relative_minute': delta, 'bars': len(sample['bars']),
                           'trade_records': len(sample['trades']),
                           'trade_states': dict(Counter(trade_state(r, metadata, rules) for r in sample['trades'])),
                           'quote_records': len(sample['quotes'])})
    return {'point_id': point['point_id'], 'symbol': point['symbol'], 'missing_time': point['missing_time'],
            'case_ids': point['case_ids'], 'variant_ids': point['variant_ids'],
            'classification': classification, 'parent_target_missing': True,
            'fresh_target_bars': len(target_rows['bars']), 'stream_integrity': integrity,
            'target_trades': trade_summary, 'target_quotes': quote_summary,
            'five_minute_observations': per_minute, 'fresh_vs_parent_bars': comparisons,
            'halt_status': 'not_certified_by_this_market_data_diagnostic',
            'causal_bar_reconstruction_proven': False, 'parent_pnl_recalculated': False}


def main():
    design = read(ROOT / 'study-design.json')
    for name, expected in design['input_sha256'].items():
        assert sha(PRIOR / name) == expected, 'parent_changed:' + name
    assert sha(ROOT / 'points.json') == design['points_sha256']
    points = read(ROOT / 'points.json')['points']
    manifest = read(ROOT / 'input-manifest.json')
    for name, expected in manifest['input_sha256'].items():
        assert sha(ROOT / name) == expected, 'manifest_input_changed:' + name
    inputs = {(r['point_id'], r['stream']): r for r in manifest['records']}
    assert len(inputs) == len(manifest['records']) == 114
    source_rules = read(ROOT / 'source-rules.json')
    rules = source_rules['minute_condition_rules']
    metadata = {r['tape']: r['response'] for r in read(ROOT / 'condition-metadata.json') if r['kind'] == 'trade' and r['status'] == 'complete'}
    parent = read(PRIOR / 'holding-market.json')
    results = [analyze_point(point, inputs, parent, metadata, rules) for point in points]
    provenance = {n: sha(ROOT / n) for n in ['study-design.json', 'points.json', 'input-manifest.json', 'condition-metadata.json', 'source-rules.json', 'analyze_windows.py']}
    save('gap-diagnostics.json', {'generated_at': datetime.now(timezone.utc).isoformat(), 'point_count': 38, 'input_sha256': provenance, 'results': results,
        'limits': ['Returned trade timestamps are used for half-open bucket counts; official documentation disagrees about SIP versus participant time.',
                  'These are current historical endpoint observations, not an immutable point-in-time tape or actual client receipt.',
                  'Requests did not set asof=-; the provider default current-symbol mapping is inherited, not verified historical instrument identity.',
                  'Price-excluded trades are still trades; current correction status is respected, without reconstructing original revisions.',
                  'No quotes or trades are treated as certified stop fills. No old result is repaired or recalculated.',
                  'All38 points are first failure coordinates, not all future gaps along414case/variant paths.']})
    summary = {'generated_at': datetime.now(timezone.utc).isoformat(), 'points': 38,
        'classification_counts': dict(Counter(r['classification'] for r in results)),
        'windows': len(inputs), 'window_status_counts': dict(Counter(r['status'] for r in inputs.values())),
        'raw_records_by_stream': {s: sum(r['records'] for r in inputs.values() if r['stream'] == s) for s in ['bars', 'trades', 'quotes']},
        'raw_pages': sum(len(r['pages']) for r in inputs.values()),
        'target_trade_records': sum(r['target_trades']['records'] for r in results),
        'target_trade_state_counts': dict(sum((Counter(r['target_trades']['states']) for r in results), Counter())),
        'target_quote_records': sum(r['target_quotes']['records'] for r in results),
        'targets_with_quote_records': sum(r['target_quotes']['records'] > 0 for r in results),
        'fresh_target_bar_count': sum(r['fresh_target_bars'] for r in results),
        'unchanged_parent_common_bars': sum(b['parent_present'] and not b['changed_fields'] for r in results for b in r['fresh_vs_parent_bars']),
        'parent_common_bar_value_conflicts': sum(bool(b['changed_fields']) for r in results for b in r['fresh_vs_parent_bars']),
        'source_integrity_issue_points': sum(any(s['issues'] for s in r['stream_integrity'].values()) for r in results),
        'prior_incomplete_paths_automatically_certified': 0, 'new_strategy_tests': 0, 'broker_orders_sent': 0,
        'new_scheduler_deployed': False, 'diagnostics_sha256': sha(ROOT / 'gap-diagnostics.json'),
        'source_scope': '38first_failure_coordinates_after_viewing_parent_outcomes; no full_year_bar_completeness_claim'}
    save('gap-summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
