"""Offline point-in-time quote-cost feasibility; no simulated trade or wealth path."""
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR
import hashlib
import json
import math
from pathlib import Path
import re
from calendar import timegm
from quote_data import load_window, atomic

ROOT = Path(__file__).resolve().parent


def timestamp_ns(text):
    if not isinstance(text, str):
        raise ValueError('timestamp_not_string')
    match = re.fullmatch(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})', text)
    if not match:
        raise ValueError('invalid_nanosecond_timestamp')
    base, fraction, offset = match.groups()
    parsed = datetime.fromisoformat(base + ('+00:00' if offset == 'Z' else offset)).astimezone(timezone.utc)
    return timegm(parsed.utctimetuple()) * 1_000_000_000 + int((fraction or '').ljust(9, '0'))


def positive(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def evaluate_latest(quotes, decision_time, max_age_seconds, condition_maps):
    if max_age_seconds not in (1, 5):
        raise ValueError('unregistered_quote_age')
    decision = timestamp_ns(decision_time)
    start = decision - 60_000_000_000
    eligible = []
    invalid_times = 0
    future = 0
    older = 0
    for row in quotes:
        try:
            at = timestamp_ns(row.get('t'))
        except (TypeError, AttributeError, ValueError):
            invalid_times += 1
            continue
        if at > decision:
            future += 1
        elif at < start:
            older += 1
        else:
            eligible.append((at, row))
    result = {'decision_time': decision_time, 'max_age_seconds': max_age_seconds,
              'quote_rows': len(quotes), 'future_rows_ignored': future,
              'older_than_requested_lookback_rows': older, 'invalid_timestamp_rows': invalid_times,
              'accepted': False, 'latest_quote': None, 'api_reported_age_ns': None}
    if invalid_times:
        result['reason'] = 'unparseable_timestamp_cannot_certify_latest'
        return result
    if not eligible:
        result['reason'] = 'no_quote_in_requested_lookback'
        return result
    at = max(t for t, row in eligible)
    latest = [row for t, row in eligible if t == at]
    unique = {json.dumps({k: v for k, v in row.items() if k != 't'}, sort_keys=True, separators=(',', ':')) for row in latest}
    result.update({'latest_timestamp_ns': at, 'latest_timestamp_rows': len(latest),
                   'latest_distinct_states': len(unique), 'api_reported_age_ns': decision - at})
    if len(unique) != 1:
        result['reason'] = 'ambiguous_latest_timestamp'
        return result
    row = latest[0]
    result['latest_quote'] = dict(row)
    reasons = []
    if not positive(row.get('bp')) or not positive(row.get('ap')):
        reasons.append('nonpositive_or_invalid_bid_ask')
    elif row['ap'] < row['bp']:
        reasons.append('crossed_quote')
    if not positive(row.get('bs')) or not positive(row.get('as')):
        reasons.append('nonpositive_or_invalid_displayed_size')
    tape = row.get('z')
    label = condition_maps.get(tape, {}).get('R')
    if tape not in ['A', 'B', 'C'] or label not in ['Regular Market Maker Open', 'Regular Two Sided Open']:
        reasons.append('unverified_tape_regular_condition_mapping')
    if row.get('c') != ['R']:
        reasons.append('unsupported_or_missing_quote_condition')
    if decision - at > max_age_seconds * 1_000_000_000:
        reasons.append('stale_api_reported_timestamp')
    result['rejection_reasons'] = reasons
    result['accepted'] = not reasons
    result['reason'] = reasons[0] if reasons else 'accepted_for_displayed_quote_diagnostic_only'
    return result


def metrics(quote, metadata):
    bid, ask = Decimal(str(quote['bp'])), Decimal(str(quote['ap']))
    atrs = {Decimal(str(m['atr14'])) for m in metadata}
    if len(atrs) != 1 or next(iter(atrs)) <= 0:
        raise ValueError('inconsistent_or_invalid_prior_ATR')
    atr = next(iter(atrs))
    spread = ask - bid
    stop_distance = atr / 10
    mid = (ask + bid) / 2
    out = {'bid': float(bid), 'ask': float(ask), 'spread_dollars': float(spread),
           'spread_bps_of_mid': float(spread / mid * 10000),
           'prior_ATR14': float(atr), 'stop_distance_0_1ATR': float(stop_distance),
           'spread_to_stop_distance': float(spread / stop_distance),
           'spread_at_least_stop_distance': spread >= stop_distance,
           'locked_quote': spread == 0,
           'displayed_size_raw': {'bid': quote['bs'], 'ask': quote['as']},
           'displayed_size_unit': 'unverified_Alpaca_API_representation',
           'execution_capacity_verified': False,
           'budget_only': []}
    for budget in (250, 500):
        qty = int((Decimal(budget) / ask).to_integral_value(rounding=ROUND_FLOOR))
        debit = qty * ask
        out['budget_only'].append({'budget': budget, 'whole_shares_at_displayed_ask': qty,
            'hypothetical_ask_notional': float(debit), 'cash_remainder': float(Decimal(budget) - debit),
            'same_quote_two_sided_crossing_cost_no_fees': float(qty * spread),
            'capacity_or_fill_claim': False})
    entry_refs = {Decimal(str(m['original_minute_entry_open'])) for m in metadata if 'original_minute_entry_open' in m}
    if entry_refs:
        assert len(entry_refs) == 1
        ref = next(iter(entry_refs))
        out['original_minute_entry_reference'] = float(ref)
        out['ask_minus_minute_entry_reference'] = float(ask - ref)
        out['ask_minus_minute_entry_reference_bps'] = float((ask / ref - 1) * 10000)
        out['reference_comparability'] = 'First trade in that minute may be after exact point; not a synchronized fill comparison.'
    return out


def distribution(values):
    if not values:
        return {'count': 0, 'median': None, 'min': None, 'max': None, 'mean': None}
    values = sorted(values)
    n = len(values)
    median = values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2
    return {'count': n, 'median': median, 'min': values[0], 'max': values[-1], 'mean': sum(values) / n}


def main():
    sample = json.loads((ROOT / 'sample.json').read_text())
    manifest = json.loads((ROOT / 'quote-input-manifest.json').read_text())
    design = json.loads((ROOT / 'study-design.json').read_text())
    assert manifest['all_pages_complete'] and not manifest['failures']
    assert manifest['sample_sha256'] == hashlib.sha256((ROOT / 'sample.json').read_bytes()).hexdigest()
    assert design['sample_sha256'] == manifest['sample_sha256']
    assert manifest['design_sha256'] == hashlib.sha256((ROOT / 'study-design.json').read_bytes()).hexdigest()
    assert {p['id'] for p in sample['points']} == {r['point_id'] for r in manifest['records']}
    records = {r['point_id']: r for r in manifest['records']}
    conditions = {row['tape']: row['response'] for row in json.loads((ROOT / 'quote-condition-metadata.json').read_text())}
    rows = []
    for point in sample['points']:
        record = records[point['id']]
        assert record == json.loads((ROOT / 'raw-quotes' / point['id'] / 'complete.json').read_text())
        assert record['route'] == '/v2/stocks/' + point['symbol'] + '/quotes'
        assert record['parameters'] == {'start': point['request_start'], 'end': point['request_end'],
                                         'limit': 10000, 'feed': 'sip', 'sort': 'asc'}
        quotes = load_window(point['id'])
        for age in [1, 5]:
            result = evaluate_latest(quotes, point['decision_time'], age, conditions)
            result.update({'point_id': point['id'], 'date': point['date'], 'symbol': point['symbol'],
                           'cohorts': point['cohorts'], 'metadata': point['metadata']})
            result['metrics'] = metrics(result['latest_quote'], point['metadata']) if result['accepted'] else None
            rows.append(result)
    source_files = ['sample.json', 'study-design.json', 'quote-input-manifest.json',
                    'quote-condition-metadata.json', 'quote_data.py', 'analyze_quotes.py']
    result = {'study': 's500-quote-feasibility-v1', 'generated_at': datetime.now(timezone.utc).isoformat(),
              'source_sha256': {f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest() for f in source_files},
              'point_count': len(sample['points']), 'age_variant_records': len(rows), 'rows': rows,
              'broker_orders_sent': 0, 'wealth_path_generated': False, 'simulated_trades_generated': False}
    atomic(ROOT / 'quote-results.json', result)
    summaries = []
    cohorts = ['all_points', *sample['cohort_points']]
    for cohort in cohorts:
        for age in [1, 5]:
            selected = [r for r in rows if r['max_age_seconds'] == age and (cohort == 'all_points' or cohort in r['cohorts'])]
            accepted = [r for r in selected if r['accepted']]
            summaries.append({'cohort': cohort, 'max_age_seconds': age, 'total_points': len(selected),
                'accepted_points': len(accepted), 'reason_counts': dict(Counter(r['reason'] for r in selected)),
                'all_rejection_reason_counts': dict(Counter(v for r in selected for v in r.get('rejection_reasons', []))),
                'spread_bps_of_mid': distribution([r['metrics']['spread_bps_of_mid'] for r in accepted]),
                'spread_to_stop_distance': distribution([r['metrics']['spread_to_stop_distance'] for r in accepted]),
                'spread_at_least_stop_distance': sum(r['metrics']['spread_at_least_stop_distance'] for r in accepted),
                'budget_only_unaffordable_250': sum(r['metrics']['budget_only'][0]['whole_shares_at_displayed_ask'] == 0 for r in accepted),
                'budget_only_unaffordable_500': sum(r['metrics']['budget_only'][1]['whole_shares_at_displayed_ask'] == 0 for r in accepted)})
    atomic(ROOT / 'quote-summary.json', {'generated_at': result['generated_at'], 'groups': summaries,
        'raw_quote_count': manifest['raw_quote_count'], 'request_windows': len(sample['points']),
        'api_size_capacity_verified': False, 'point_results_sha256': hashlib.sha256((ROOT / 'quote-results.json').read_bytes()).hexdigest(),
        'no_returns_or_wealth_claim': True,
        'limitations': ['Event/API reported quote age is not receive latency or proof quote was observable by a trader then.',
            'Samples are conditional and known-failure diagnostics, not an unbiased full-year market sample.',
            'Latestquote safety gates and1s/5s age thresholds are explicit conservative assumptions, not claims all other quotes are unusable in reality.',
            'Quoted spread, integer budget and positive size cannot prove fills, depth, fees, routing, halts or client permissions.']})
    print(json.dumps(summaries, indent=2))


if __name__ == '__main__':
    main()
