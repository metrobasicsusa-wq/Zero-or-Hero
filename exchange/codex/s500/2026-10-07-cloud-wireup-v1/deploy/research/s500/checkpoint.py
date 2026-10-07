"""Offline, fixed-calendar research checkpoints; never activation decisions.

The caller verifies immutable source/seal receipts before supplying the registered
analyzer's rows. This module does not retrieve data, change selection, charge
costs again, replace missing outcomes or write state. Dates, not stock events,
are the resampling unit of the one final descriptive interval.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
import hashlib
import random

from analyze_session import VARIANTS, PRIMARY, GROUPS, BIN_LABELS, summarize, variant
from vendor.return_distribution import finite_decimal, quantile, render

SCOPE = 'prospective_sealed_postclose_stock_proxy'


def _time(value):
    if not isinstance(value, str):
        raise ValueError('timestamp_string_required')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('timezone_required')
    return parsed


def _stock(row):
    # Controls and known ETFs are never stock statistics, even if target_role
    # in an inherited economic row describes every nonbenchmark as a stock.
    return (row['target_role'] == 'stock' and row['symbol'] not in ('SPY', 'QQQ')
            and row.get('stock_eligible', True) is True
            and row.get('current_etf_classification') != 'Y')


def _validate_rows(protocol, day, bundle):
    if not isinstance(bundle, dict):
        raise ValueError('session_bundle_required')
    summary, rows = bundle.get('summary'), bundle.get('rows')
    if not isinstance(summary, dict) or not isinstance(rows, list):
        raise ValueError('session_summary_and_rows_required')
    if summary.get('date') != day or summary.get('protocol_id') != protocol['id']:
        raise ValueError('session_identity_mismatch')
    if summary.get('analysis_scope') != SCOPE:
        raise ValueError('nonprospective_analysis_scope')
    identities = set()
    by_case = defaultdict(set)
    case_metadata = {}
    for row in rows:
        if not isinstance(row, dict) or row.get('date') != day:
            raise ValueError('row_date_mismatch')
        symbol = row.get('symbol')
        if not isinstance(symbol, str) or not symbol or row.get('case_id') != day + '__' + symbol:
            raise ValueError('row_case_identity_mismatch')
        if row.get('actual_fill') is not False or row.get('wealth_path') is not False:
            raise ValueError('stock_proxy_scope_required')
        v = variant(row)
        key = (row['case_id'], v)
        if v not in VARIANTS or key in identities:
            raise ValueError('invalid_or_duplicate_case_variant')
        identities.add(key)
        by_case[row['case_id']].add(v)
        metadata = (row.get('target_role'), row.get('current_etf_classification'),
                    row.get('stock_eligible', True), row.get('SPY_group'), row.get('QQQ_group'),
                    tuple((axis, (row.get('fixed_bins') or {}).get(axis)) for axis in BIN_LABELS))
        if row['case_id'] in case_metadata and case_metadata[row['case_id']] != metadata:
            raise ValueError('case_metadata_varies_between_variants')
        case_metadata[row['case_id']] = metadata
        if row.get('target_role') not in ('stock', 'benchmark'):
            raise ValueError('invalid_target_role')
        if symbol in ('SPY', 'QQQ') and row['target_role'] != 'benchmark':
            raise ValueError('benchmark_role_mismatch')
        allowed_groups = GROUPS + ('not_stock_target',) if symbol in ('SPY', 'QQQ') else GROUPS
        if any(row.get(b + '_group') not in allowed_groups for b in ('SPY', 'QQQ')):
            raise ValueError('invalid_market_group')
        if any((row.get('fixed_bins') or {}).get(axis) not in labels for axis, labels in BIN_LABELS.items()):
            raise ValueError('invalid_fixed_bin')
        status = row.get('signal_status')
        if status not in ('signal', 'unknown', 'no_flush', 'no_rebound'):
            raise ValueError('invalid_signal_status')
        for field in ('net_return', 'matched_excess'):
            value = row.get(field)
            if value is not None:
                finite_decimal(value)
                if status != 'signal':
                    raise ValueError('non_signal_has_return')
        if row.get('matched_excess') is not None and row.get('paired_control_status') != 'complete_three':
            raise ValueError('matched_excess_without_complete_pair')
        if not isinstance(row.get('paired_control_status'), str):
            raise ValueError('pair_status_required')
    if any(values != set(VARIANTS) for values in by_case.values()):
        raise ValueError('missing_case_variants')
    counts = summary.get('counts') or {}
    if counts.get('selected_cases') != len(by_case) or counts.get('result_rows') != len(rows):
        raise ValueError('summary_row_count_mismatch')
    return rows


def _date_means(rows):
    daily = defaultdict(list)
    for row in rows:
        if row['net_return'] is not None:
            daily[row['date']].append(Fraction(finite_decimal(row['net_return'])))
    return [(day, sum(values, Fraction()) / len(values), len(values))
            for day, values in sorted(daily.items())]


def _interval(protocol_id, means, final):
    info = {'interval': None, 'reason': None, 'descriptive_only': True,
            'unit': 'complete_nonempty_date_mean', 'draws': 0,
            'serial_independence_proven': False,
            'multiplicity_adjusted': False}
    if not final:
        info['reason'] = 'registered_final_checkpoint_only'
        return info
    seed_text = protocol_id + '|primary_final'
    seed_hex = hashlib.sha256(seed_text.encode()).hexdigest()[:16]
    info.update(seed_namespace=seed_text, seed_first16hex=seed_hex,
                percentile_method='linear_interpolation_at_(n_minus_1)_times_probability')
    if len(means) < 2:
        info['reason'] = 'fewer_than_two_complete_outcome_dates'
        return info
    values = [value for _, value, _ in means]
    rng = random.Random(int(seed_hex, 16))
    draws = sorted(sum((values[rng.randrange(len(values))] for _ in values), Fraction())
                   / len(values) for _ in range(2000))
    info.update(draws=2000, interval={'level': '0.95',
                'lower': render(quantile(draws, Fraction(25, 1000))),
                'upper': render(quantile(draws, Fraction(975, 1000)))})
    return info


def build_checkpoint(protocol, through_ordinal, sessions, ledger, as_of):
    """Return one JSON-safe 20/40/59 checkpoint from immutable daily outputs.

    ``sessions`` maps dates to ``{'summary': analysis_summary, 'rows': rows}``.
    ``ledger`` may include all 59 rows; missing calendar statuses are explicit.
    Supplied dates after this checkpoint are ignored and listed, never included.
    This function enforces the registered review clock even for manual calls.
    """
    if type(through_ordinal) is not int or through_ordinal not in (20, 40, 59):
        raise ValueError('registered_checkpoint_required')
    calendar = protocol.get('sessions')
    if (not isinstance(calendar, list) or len(calendar) != 59
            or [s.get('ordinal') for s in calendar] != list(range(1, 60))
            or len({s.get('date') for s in calendar}) != 59):
        raise ValueError('registered_59_session_calendar_required')
    if (protocol.get('review_ordinals') != [20, 40, 59]
            or tuple(map(tuple, protocol['inherited_variants'])) != VARIANTS
            or tuple(protocol['primary_variant']) != PRIMARY):
        raise ValueError('registered_checkpoint_configuration_changed')
    checkpoint = calendar[through_ordinal - 1]
    if _time(as_of) < _time(checkpoint['review_at_utc']):
        raise ValueError('checkpoint_review_not_due')
    if not isinstance(sessions, dict) or not isinstance(ledger, list):
        raise ValueError('sessions_mapping_and_ledger_list_required')
    all_dates = {s['date'] for s in calendar}
    if set(sessions) - all_dates:
        raise ValueError('unregistered_session_date')
    ledger_by_date = {}
    for row in ledger:
        if not isinstance(row, dict) or row.get('date') not in all_dates:
            raise ValueError('unregistered_ledger_date')
        if row['date'] in ledger_by_date:
            raise ValueError('duplicate_ledger_date')
        ledger_by_date[row['date']] = row
    selected_calendar = calendar[:through_ordinal]
    rows, dates = [], []
    for session in selected_calendar:
        day = session['date']
        observed = day in sessions
        daily_rows = _validate_rows(protocol, day, sessions[day]) if observed else []
        rows.extend(daily_rows)
        daily_primary = [r for r in daily_rows if _stock(r) and variant(r) == PRIMARY
                         and r['SPY_group'] == 'market_down']
        inherited_ledger = deepcopy(ledger_by_date.get(day, {'date': day, 'status': 'unknown_calendar_status'}))
        daily_means = _date_means(daily_primary)
        dates.append({'date': day, 'ordinal': session['ordinal'],
                      'ledger': inherited_ledger, 'analysis_present': observed,
                      'source_complete': (sessions[day]['summary'].get('source') or {}).get('source_complete')
                                         if observed else None,
                      'primary_counts': summarize(daily_primary)['counts'] if observed else None,
                      'primary_complete_mean': render(daily_means[0][1]) if daily_means else None})
    stock_rows = [r for r in rows if _stock(r)]
    grouped = defaultdict(list)
    for row in stock_rows:
        for benchmark in ('SPY', 'QQQ'):
            grouped[(variant(row), benchmark, row[benchmark + '_group'])].append(row)
    tables = [{'variant': list(v), 'benchmark': b, 'group': g,
               'summary': summarize(grouped[(v, b, g)])}
              for v in VARIANTS for b in ('SPY', 'QQQ') for g in GROUPS]
    main = grouped[(PRIMARY, 'SPY', 'market_down')]
    bins = [{'axis': axis, 'bin': label,
             'summary': summarize([r for r in main if r['fixed_bins'][axis] == label])}
            for axis, labels in BIN_LABELS.items() for label in labels]
    means = _date_means(main)
    n_dates = len(means)
    return {'protocol_id': protocol['id'], 'through_ordinal': through_ordinal,
            'through_date': checkpoint['date'], 'as_of': as_of,
            'registered_review_at': checkpoint['review_at_utc'],
            'purpose': 'final_primary_endpoint' if through_ordinal == 59 else 'descriptive_progress_no_retuning',
            'analysis_scope': SCOPE, 'calendar': dates,
            'coverage': {'planned_dates': through_ordinal,
                         'analysis_dates': sum(d['analysis_present'] for d in dates),
                         'missing_analysis_dates': [d['date'] for d in dates if not d['analysis_present']],
                         'ledger_status_counts': dict(Counter(d['ledger'].get('status', 'unknown_calendar_status') for d in dates)),
                         'selected_case_variant_rows': len(rows),
                         'stock_case_variant_rows': len(stock_rows),
                         'later_supplied_dates_excluded': sorted(set(sessions) - {s['date'] for s in selected_calendar})},
            'primary_SPY_down': summarize(main), 'variant_group_summaries': tables,
            'fixed_feature_bins': bins,
            'primary_endpoint': {'variant': list(PRIMARY), 'benchmark': 'SPY', 'group': 'market_down',
                                 'date_equal_net_mean': render(sum((x[1] for x in means), Fraction()) / n_dates) if n_dates else None,
                                 'complete_outcome_dates': n_dates,
                                 'date_means': [{'date': d, 'net_mean': render(v), 'complete_events': n} for d, v, n in means],
                                 'sample_size_label': 'insufficient_sample' if n_dates < 20 else 'at_least_20_complete_outcome_dates',
                                 'sample_size_label_scope': 'operational_only_not_independence_power_or_significance',
                                 'interval': _interval(protocol['id'], means, through_ordinal == 59)},
            'actual_fill': False, 'wealth_path': False, 'option_return_claim': False,
            'trading_activation': False, 'performance_pass_fail': None,
            'costs_already_net_not_deducted_twice': True,
            'empty_dates_and_no_signals_are_not_zero_returns': True,
            'limitations': list(protocol.get('limitations', [])) + [
                'Whole-date resampling preserves within-date event dependence, not independence between trading dates.',
                'Missing dates/outcomes remain explicit; complete-case conditioning can bias the estimate.',
                'All nonprimary variants, market groups, bins and tails are descriptive; none is promoted by ranking.']}
