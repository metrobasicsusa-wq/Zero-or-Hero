"""EP entry-point quote diagnostic only; no orders, fills, exits, or returns.

Reuses the unchanged quote downloader and validator published in quote-v1.
Commands: freeze (offline), fetch (explicitly invoked), analyze (offline), all.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent
PRIOR = ROOT.parent / 's500-quote-20261006'
CACHE = ROOT / 'raw-ep-quote-cache'
PARENT_COMMIT = '885645ae0a5ba00eff31c5ccf496586d5577ad9d'


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    temp.replace(path)


def digest_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, separators=(',', ':'), sort_keys=True).encode()).hexdigest()


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def load_prior():
    # Explicit names preserve analyze_quotes.py's import of quote_data.
    for name in ('quote_data', 'analyze_quotes'):
        spec = importlib.util.spec_from_file_location(name, PRIOR / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    downloader = sys.modules['quote_data']
    downloader.ROOT = CACHE
    return downloader, sys.modules['analyze_quotes']


def dec(value):
    if isinstance(value, bool):
        raise ValueError('boolean_is_not_a_price')
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('invalid_decimal_price') from None
    if not result.is_finite():
        raise ValueError('nonfinite_decimal_price')
    return result


def build_points(market):
    points = []
    for row in sorted(market['rows'], key=lambda r: (r['date'], r['symbol'])):
        if row['market_gate_pass'] is not True:
            continue
        signal, opening, reference = row['signal'], row['opening30'], row['entry_reference']
        at = datetime.fromisoformat(signal['planned_entry_time_utc']).astimezone(timezone.utc)
        assert signal['planned_entry_time_utc'] == reference['time_utc']
        stop = dec(opening['exact_values']['or30_low'])
        assert stop > 0 and stop == dec(reference['stop_exact'])
        points.append({'id': row['date'] + '__' + row['symbol'], 'date': row['date'],
            'symbol': row['symbol'], 'decision_time': at.isoformat(),
            'request_start': (at - timedelta(seconds=60)).isoformat(), 'request_end': at.isoformat(),
            'signal_completed_at_utc': signal['completed_at_utc'],
            'known_or30_low_exact': str(stop), 'known_or30_high_exact': opening['exact_values']['or30_high'],
            'original_minute_entry_open_exact': reference['open_exact'],
            'minute_reference_gap_through_stop': reference['gap_through_stop'],
            'market_gate_row_sha256': digest(row)})
    assert len({p['id'] for p in points}) == len(points)
    return points


def sources():
    return {'current_stage': {name: digest_file(ROOT / name) for name in (
        'market-gates.json', 'market-summary.json', 'study-design.json')},
        'prior_quote_v1': {name: digest_file(PRIOR / name) for name in (
            'quote_data.py', 'analyze_quotes.py', 'quote-condition-metadata.json')}}


def freeze():
    path = ROOT / 'ep-quote-design.json'
    market = json.loads((ROOT / 'market-gates.json').read_text())
    summary = json.loads((ROOT / 'market-summary.json').read_text())
    points = build_points(market)
    assert len(points) == summary['market_gate_pass'] == 73
    assert {p['id'] for p in points} == set(summary['passed_candidate_ids'])
    assert summary['full_result_sha256'] == digest_file(ROOT / 'market-gates.json')
    if path.exists():
        result = json.loads(path.read_text())
        assert result['points'] == points and result['source_sha256'] == sources()
        return result
    assert not (CACHE / 'raw-quotes').exists(), 'cannot register after this stage fetched quotes'
    design = {'id': 's500-EP-entry-quotes-v1', 'registered_at': utcnow(),
        'frozen_before_this_stage_quote_fetch': True, 'prior_quote_stage_commit': PARENT_COMMIT,
        'point_count': len(points), 'points_sha256': digest(points), 'points': points,
        'source_sha256': sources(),
        'selection': 'All 73 market-gate passes, chronological date/symbol, without news or future-return selection.',
        'point_policy': 'Quote as of planned entry after first qualifying completed-minute signal plus one full minute; this does not establish a trade.',
        'fetch': {'endpoint': 'https://data.alpaca.markets/v2/stocks/{symbol}/quotes',
            'feed': 'sip', 'lookback_seconds': 60, 'inclusive_end': True, 'limit': 10000,
            'max_pages': 50, 'workers': 4, 'global_interval_seconds': 0.45, 'method': 'GET'},
        'validation': {'primary_max_age_seconds': 1, 'sensitivity_max_age_seconds': 5,
            'reuse': 'Exact prior timestamp_ns and evaluate_latest functions, source hashes above.',
            'latest_policy': 'No older fallback after an invalid newest state or conflicting states at the latest timestamp.',
            'quote_condition': 'Only sole R condition with verified prior tape A/B/C R metadata mapping.',
            'display_size': 'Positive raw size required; unit and executable capacity remain unverified.'},
        'metric_policy': {'stop': 'Known first30minute low; never 0.1ATR.',
            'risk_distance': 'displayed ask minus known OR30 low',
            'spread_to_risk': 'spread / (ask-OR30low) only when ask>OR30low; otherwise null with explicit flag.',
            'bid_at_or_below_stop': 'Diagnostic only, never proof a broker stop would trigger or fill.',
            'whole_share_budgets': [250, 500], 'rounding': 'Decimal from source strings, floor shares; no fees.',
            'reference_comparison': 'Minute open may be a trade occurring after the exact decision point and is not a synchronized fill.'},
        'retention': 'Every point retained under both age thresholds, including empty, stale, invalid, failed fetch, unaffordable, and gap-through cases.',
        'limitations': ['Conditioned on known market-gate passes, not an unbiased market-wide quote sample.',
            'News qualification and issuer identity are independent unresolved gates; no all73 trade list.',
            'API/event timestamps do not prove historical receive latency or historical trader observability.',
            'Displayed spread and notional arithmetic do not establish liquidity depth, fees, routing, fills or client permissions.'],
        'orders_sent': 0, 'returns_or_wealth_or_exit_analysis': False}
    atomic(path, design)
    return design


def validate_design():
    design = json.loads((ROOT / 'ep-quote-design.json').read_text())
    assert design['source_sha256'] == sources()
    assert design['points_sha256'] == digest(design['points'])
    assert design['points'] == build_points(json.loads((ROOT / 'market-gates.json').read_text()))
    return design


def metrics(quote, point):
    bid, ask, stop = dec(quote['bp']), dec(quote['ap']), dec(point['known_or30_low_exact'])
    if bid <= 0 or ask < bid or stop <= 0:
        raise ValueError('invalid_bid_ask_or_stop')
    spread, risk = ask - bid, ask - stop
    midpoint = (ask + bid) / 2
    out = {'bid': float(bid), 'ask': float(ask), 'known_or30_low': float(stop),
        'exact_prices': {'bid': str(bid), 'ask': str(ask), 'known_or30_low': str(stop),
            'spread': str(spread), 'ask_minus_or30_low': str(risk)},
        'spread_dollars': float(spread), 'spread_bps_of_mid': float(spread / midpoint * 10000),
        'ask_minus_or30_low': float(risk), 'ask_above_known_stop': risk > 0,
        'spread_to_ask_minus_or30_low': float(spread / risk) if risk > 0 else None,
        'spread_at_least_positive_risk_distance': spread >= risk if risk > 0 else None,
        'ask_at_or_below_known_stop': ask <= stop,
        'bid_at_or_below_known_stop': bid <= stop,
        'stop_trigger_or_fill_claim': False, 'locked_quote': spread == 0,
        'displayed_size_raw': {'bid': quote['bs'], 'ask': quote['as']},
        'displayed_size_unit': 'unverified_Alpaca_API_representation', 'execution_capacity_verified': False,
        'budget_only': []}
    for budget in (250, 500):
        cash = Decimal(budget)
        shares = int((cash / ask).to_integral_value(rounding=ROUND_FLOOR))
        debit = shares * ask
        out['budget_only'].append({'budget': budget, 'whole_shares_at_displayed_ask': shares,
            'hypothetical_ask_notional': float(debit), 'cash_remainder': float(cash - debit),
            'same_quote_two_sided_crossing_cost_no_fees': float(shares * spread),
            'unaffordable_at_displayed_ask': shares == 0, 'capacity_or_fill_claim': False})
    ref = dec(point['original_minute_entry_open_exact'])
    if ref <= 0:
        raise ValueError('invalid_minute_reference')
    out.update({'minute_entry_open_reference': float(ref),
        'minute_reference_gap_through_stop': point['minute_reference_gap_through_stop'],
        'ask_minus_minute_reference': float(ask - ref),
        'ask_minus_minute_reference_bps': float((ask / ref - 1) * 10000),
        'reference_comparability': 'First trade in the minute may occur after exact quote decision point; not a synchronized fill.'})
    return out


def fetch():
    design = validate_design()
    downloader, _ = load_prior()
    records, failures = [], []
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = {pool.submit(downloader.fetch_window, p): p for p in design['points']}
        for i, future in enumerate(as_completed(pending), 1):
            point = pending[future]
            try:
                records.append(future.result())
            except Exception as error:
                # Downloader errors contain a safe bounded status, never request headers.
                failures.append({'point_id': point['id'], 'error_type': type(error).__name__,
                    'error': str(error)[:180]})
            if i % 20 == 0 or i == len(pending):
                progress = {'completed_windows': i, 'total_windows': len(pending),
                    'failures': failures, 'elapsed_seconds': round(time.monotonic() - started), 'updated_at': utcnow()}
                atomic(CACHE / 'download-progress.json', progress)
                print(json.dumps(progress), flush=True)
    manifest = {'registered_design_sha256': digest_file(ROOT / 'ep-quote-design.json'),
        'points_sha256': design['points_sha256'], 'point_count': len(design['points']),
        'records': sorted(records, key=lambda r: r['point_id']),
        'failures': sorted(failures, key=lambda r: r['point_id']),
        'all_pages_complete': not failures, 'raw_quote_count': sum(p['quotes'] for r in records for p in r['pages']),
        'raw_response_bytes': sum(p['bytes'] for r in records for p in r['pages']),
        'completed_at': utcnow(), 'quote_data_sha256': digest_file(PRIOR / 'quote_data.py')}
    atomic(CACHE / 'input-manifest.json', manifest)
    return manifest


def analyze():
    design = validate_design()
    downloader, prior = load_prior()
    manifest = json.loads((CACHE / 'input-manifest.json').read_text())
    assert manifest['registered_design_sha256'] == digest_file(ROOT / 'ep-quote-design.json')
    assert manifest['points_sha256'] == design['points_sha256']
    records = {r['point_id']: r for r in manifest['records']}
    failures = {r['point_id']: r for r in manifest['failures']}
    assert not records.keys() & failures.keys()
    assert records.keys() | failures.keys() == {p['id'] for p in design['points']}
    conditions = {row['tape']: row['response'] for row in json.loads((PRIOR / 'quote-condition-metadata.json').read_text())}
    rows = []
    for point in design['points']:
        quotes = None
        if point['id'] in records:
            record = records[point['id']]
            assert record == json.loads((CACHE / 'raw-quotes' / point['id'] / 'complete.json').read_text())
            assert record['route'] == '/v2/stocks/' + point['symbol'] + '/quotes'
            assert record['parameters'] == {'start': point['request_start'], 'end': point['request_end'],
                'limit': 10000, 'feed': 'sip', 'sort': 'asc'}
            quotes = downloader.load_window(point['id'])
        for age in (1, 5):
            result = prior.evaluate_latest(quotes, point['decision_time'], age, conditions) if quotes is not None else {
                'decision_time': point['decision_time'], 'max_age_seconds': age, 'accepted': False,
                'reason': 'quote_request_incomplete', 'fetch_failure': failures[point['id']],
                'latest_quote': None, 'quote_rows': None}
            result.update({'point_id': point['id'], 'date': point['date'], 'symbol': point['symbol'],
                'known_or30_low_exact': point['known_or30_low_exact'],
                'original_minute_entry_open_exact': point['original_minute_entry_open_exact'],
                'minute_reference_gap_through_stop': point['minute_reference_gap_through_stop'],
                'metrics': metrics(result['latest_quote'], point) if result['accepted'] else None})
            rows.append(result)
    result = {'id': design['id'], 'generated_at': utcnow(), 'point_count': len(design['points']),
        'age_variant_records': len(rows), 'rows': rows, 'input_manifest': manifest,
        'source_sha256': {**design['source_sha256'], 'this_diagnostic': {name: digest_file(ROOT / name)
            for name in ('ep-quote-design.json', 'ep_quote.py')}},
        'orders_sent': 0, 'returns_or_wealth_or_exit_analysis': False,
        'all_points_retained': True, 'news_qualification_claimed': False}
    atomic(ROOT / 'ep-quote-results.json', result)
    groups = []
    for age in (1, 5):
        selected = [r for r in rows if r['max_age_seconds'] == age]
        good = [r for r in selected if r['accepted']]
        valid_risk = [r for r in good if r['metrics']['ask_above_known_stop']]
        groups.append({'max_age_seconds': age, 'total_points': len(selected), 'accepted_points': len(good),
            'reason_counts': dict(Counter(r['reason'] for r in selected)),
            'all_rejection_reason_counts': dict(Counter(v for r in selected for v in r.get('rejection_reasons', []))),
            'spread_bps_of_mid': prior.distribution([r['metrics']['spread_bps_of_mid'] for r in good]),
            'spread_to_positive_ask_minus_or30_low': prior.distribution([r['metrics']['spread_to_ask_minus_or30_low'] for r in valid_risk]),
            'ask_at_or_below_known_stop': sum(r['metrics']['ask_at_or_below_known_stop'] for r in good),
            'bid_at_or_below_known_stop': sum(r['metrics']['bid_at_or_below_known_stop'] for r in good),
            'spread_at_least_positive_risk_distance': sum(r['metrics']['spread_at_least_positive_risk_distance'] for r in valid_risk),
            'budget_only_unaffordable_250': sum(r['metrics']['budget_only'][0]['unaffordable_at_displayed_ask'] for r in good),
            'budget_only_unaffordable_500': sum(r['metrics']['budget_only'][1]['unaffordable_at_displayed_ask'] for r in good)})
    summary = {'generated_at': result['generated_at'], 'point_count': len(design['points']),
        'age_variant_records': len(rows), 'groups': groups, 'all_pages_complete': manifest['all_pages_complete'],
        'fetch_failures': manifest['failures'], 'raw_quote_count': manifest['raw_quote_count'],
        'raw_response_bytes': manifest['raw_response_bytes'],
        'point_results_sha256': digest_file(ROOT / 'ep-quote-results.json'),
        'all_points_retained': True, 'no_trade_return_wealth_or_capacity_claim': True,
        'limitations': design['limitations']}
    atomic(ROOT / 'ep-quote-summary.json', summary)
    print(json.dumps(summary, indent=2), flush=True)
    return result, summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['freeze', 'fetch', 'analyze', 'all'])
    parser.add_argument('--prior-quote-dir', type=Path)
    args = parser.parse_args()
    if args.prior_quote_dir:
        PRIOR = args.prior_quote_dir
    if args.command in ('freeze', 'all'):
        design = freeze()
        print(json.dumps({'point_count': design['point_count'], 'registered_at': design['registered_at'],
            'design_sha256': digest_file(ROOT / 'ep-quote-design.json')}), flush=True)
    if args.command in ('fetch', 'all'):
        fetch()
    if args.command in ('analyze', 'all'):
        analyze()
