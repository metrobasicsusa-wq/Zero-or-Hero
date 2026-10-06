"""Point-of-decision feature construction for the exploratory S500 matrix.

No network, orders or portfolio decisions. Market bars remain RAW; only feature
windows are adjusted for unambiguous splits effective by the decision date.
"""
import argparse
from collections import Counter, defaultdict
from datetime import date as Date, datetime
import hashlib
import json
import math
from pathlib import Path
import statistics
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent
SOURCE = ROOT.parent / 's500-broad-20261006'
NY = ZoneInfo('America/New_York')
FAMILIES = ('breakout20', 'momentum20', 'trend_pullback', 'reclaim_down', 'acceleration3', 'volume_ignition')
LAST_DIAGNOSTICS = {}


def valid_date(value):
    try:
        return isinstance(value, str) and Date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def valid_bar(bar):
    return isinstance(bar, dict) and all(isinstance(bar.get(k), (int, float)) and math.isfinite(bar[k]) and bar[k] > 0 for k in ('o', 'h', 'l', 'c')) and isinstance(bar.get('v'), (int, float)) and math.isfinite(bar['v']) and bar['v'] >= 0 and bar['l'] <= min(bar['o'], bar['c']) <= max(bar['o'], bar['c']) <= bar['h']


def ge(a, b):
    """Include mathematically exact percentage boundaries despite float noise."""
    return a > b or math.isclose(a, b, rel_tol=0, abs_tol=1e-12)


def le(a, b):
    return a < b or math.isclose(a, b, rel_tol=0, abs_tol=1e-12)


def signal_features(history, today):
    """Only completed, already-adjusted history and decision-day bar are visible."""
    if len(history) != 20 or not all(valid_bar(b) for b in history):
        raise ValueError('twenty_valid_history_bars_required')
    prior_close = history[-1]['c']
    volume = statistics.median(b['v'] for b in history)
    row = {'prior_close': prior_close,
           'liquidity': statistics.median(b['c'] * b['v'] for b in history),
           'prior20_high': max(b['h'] for b in history),
           'prior20_mean_close': statistics.mean(b['c'] for b in history),
           'prior20_median_volume': volume,
           'signals': {}, 'flags': []}
    if not valid_bar(today):
        row['flags'].append('missing_or_invalid_decision_bar')
        return row
    close = today['c']
    ret = close / prior_close - 1
    ret20 = close / history[0]['c'] - 1
    ret3 = close / history[-3]['c'] - 1
    rv = today['v'] / volume if volume > 0 else None
    row.update(close=close, daily_return=ret, return20=ret20, return3=ret3,
               relative_volume=rv, previous_high=history[-1]['h'])
    if abs(ret) > .25:
        row['flags'].append('large_adjusted_daily_move_gt25pct_not_excluded')
    if rv is None:
        row['flags'].append('zero_prior_median_volume')
    liquid_volume = rv is not None and ge(rv, 1.5)
    if close > row['prior20_high'] and liquid_volume:
        row['signals']['breakout20'] = close / row['prior20_high'] - 1
    if ge(ret20, .10) and ge(close, row['prior20_mean_close']):
        row['signals']['momentum20'] = ret20
    if ge(ret20, .10) and ge(ret, -.08) and le(ret, -.02) and ge(close, row['prior20_mean_close']):
        row['signals']['trend_pullback'] = -ret
    if ge(ret, -.15) and le(ret, -.05) and liquid_volume and close > (today['h'] + today['l']) / 2:
        row['signals']['reclaim_down'] = -ret
    if ge(ret3, .10) and close > history[-1]['h'] and liquid_volume:
        row['signals']['acceleration3'] = ret3
    if ge(ret, .05) and rv is not None and ge(rv, 2):
        row['signals']['volume_ignition'] = ret * rv
    return row


def index_splits(rows):
    """Keep ambiguity; no silent duplicate split application or ticker remapping."""
    indexed = defaultdict(list)
    diagnostics = Counter()
    for source_index, action in enumerate(rows):
        if action.get('type') not in ('forward_split', 'reverse_split'):
            continue
        item = dict(action)
        item['source_row_index'] = source_index
        symbol = item.get('symbol')
        new_symbol = item.get('new_symbol')
        issues = []
        if not symbol:
            issues.append('unmapped_old_symbol')
        if 'new_symbol' in item and not new_symbol:
            issues.append('unmapped_new_symbol')
        if new_symbol and new_symbol != symbol:
            issues.append('unresolved_symbol_change')
        if not valid_date(item.get('ex_date')):
            issues.append('invalid_or_missing_ex_date')
        rates = (item.get('old_rate'), item.get('new_rate'))
        if not all(isinstance(v, (float, int)) and math.isfinite(v) and v > 0 for v in rates):
            issues.append('invalid_split_rates')
        if item.get('semantic_duplicate_group'):
            issues.append('semantic_duplicate_split')
        item['issues'] = issues
        # A missing ex-date cannot be made valid from a process date. That date
        # merely bounds a conservative, explicitly unresolved screening flag.
        item['screen_date'] = item.get('ex_date') if valid_date(item.get('ex_date')) else (item.get('process_date') if valid_date(item.get('process_date')) else None)
        if item['screen_date'] is None:
            diagnostics['unplaceable_split_date_rows'] += 1
        symbols = {s for s in (symbol, new_symbol) if s}
        if not symbols:
            diagnostics['split_rows_without_any_mappable_symbol'] += 1
        for s in symbols:
            indexed[s].append(item)
        diagnostics.update(issues)
    for symbol, actions in indexed.items():
        same_ex = Counter(a['ex_date'] for a in actions if valid_date(a.get('ex_date')))
        for action in actions:
            if valid_date(action.get('ex_date')) and same_ex[action['ex_date']] > 1:
                action['issues'] = sorted(set(action['issues'] + ['multiple_split_rows_same_symbol_date']))
        actions.sort(key=lambda a: (a.get('screen_date') or '', a['source_row_index']))
    return dict(indexed), dict(diagnostics)


def adjusted_window(symbol, history_dates, series, decision_date, split_index):
    """Return (20 adjusted bars, action flags, exclusion reasons).

    Future effective splits are ignored. An unresolved action only excludes a
    window crossing its effective date (or stated process-date proxy); a wholly
    unplaceable action is disclosed globally, never projected into past windows.
    """
    history = [dict(series[d]) for d in history_dates]
    flags, excluded = [], []
    for action in split_index.get(symbol, []):
        effective = action.get('screen_date')
        if effective is None or effective > decision_date or effective <= history_dates[0]:
            continue
        if action['issues']:
            excluded.extend(action['issues'])
            continue
        factor = action['old_rate'] / action['new_rate']
        for d, bar in zip(history_dates, history):
            if d < effective:
                for key in ('o', 'h', 'l', 'c'):
                    bar[key] *= factor
                bar['v'] /= factor
        flags.append({'type': action['type'], 'ex_date': effective,
                      'old_rate': action['old_rate'], 'new_rate': action['new_rate']})
    return history, flags, sorted(set(excluded))


def construct_candidates(market, calendar, universe, classifications, actions, protocol):
    """Pure historical screening helper; returns candidate rows and diagnostics."""
    screen = protocol['screen']
    required = screen['prior_sessions_required']
    if required != 20:
        raise ValueError('signal_definitions_require_20_sessions')
    classes = defaultdict(list)
    for row in classifications:
        classes[row['symbol']].append(row)
    excluded = {s for s, rows in classes.items() if any(r.get('etf') == 'Y' or r.get('test_issue') == 'Y' for r in rows)}
    classification_unknown = {s for s in universe if not classes[s] or not all(r.get('etf') == 'N' and r.get('test_issue') == 'N' for r in classes[s])}
    split_index, split_diagnostics = index_splits(actions)
    start = protocol['scope']['start']
    end = protocol['scope']['end']
    first_eval = next(i for i, d in enumerate(calendar) if d >= start)
    first_signal = calendar[first_eval - 1]
    candidates, daily, exclusion_records = [], [], []
    total_reasons = Counter()
    for i, day in enumerate(calendar):
        if day < first_signal or day > end:
            continue
        previous = calendar[i-required:i]
        if len(previous) != required:
            raise ValueError('insufficient_calendar_warmup')
        reasons = Counter()
        screened = []
        for symbol in universe:
            if symbol in excluded:
                reasons['current_official_etf_or_test_issue'] += 1
                continue
            series = market.get(symbol, {})
            if any(d not in series or not valid_bar(series[d]) for d in previous):
                reasons['prior_history_missing_or_invalid'] += 1
                continue
            history, applied, action_issues = adjusted_window(symbol, previous, series, day, split_index)
            if action_issues:
                reasons['unresolved_split_window'] += 1
                exclusion_records.append({'date': day, 'symbol': symbol, 'reasons': action_issues})
                continue
            f = signal_features(history, series.get(day))
            if not ge(f['prior_close'], screen['min_prior_close']):
                reasons['prior_close_below_minimum'] += 1
                continue
            if not ge(f['liquidity'], screen['minimum_prior20_median_dollar_volume']):
                reasons['prior_liquidity_below_minimum'] += 1
                continue
            row = {'date': day, 'symbol': symbol,
                   'classification_unknown': symbol in classification_unknown,
                   **f, 'applied_splits': applied}
            screened.append(row)
        screened.sort(key=lambda row: (-row['liquidity'], row['symbol']))
        reasons['eligible_outside_top_pool'] = max(0, len(screened)-screen['pool_top_liquidity'])
        chosen = screened[:screen['pool_top_liquidity']]
        for rank, row in enumerate(chosen, 1):
            row['rank'] = rank
            candidates.append(row)
        daily.append({'date': day, 'eligible_before_top_pool': len(screened), 'candidate_count': len(chosen),
                      'classification_unknown_in_pool': sum(r['classification_unknown'] for r in chosen),
                      'missing_decision_bar_in_pool': sum('missing_or_invalid_decision_bar' in r['flags'] for r in chosen),
                      'signal_counts': {family: sum(family in r['signals'] for r in chosen) for family in FAMILIES},
                      'exclusion_counts': dict(reasons)})
        total_reasons.update(reasons)
    return candidates, {'scope': 'entry screening only; never removes held-position losses',
                        'first_signal_date': first_signal, 'last_signal_date': end,
                        'signal_dates': len(daily), 'candidate_rows': len(candidates),
                        'signals_total': {family: sum(family in r['signals'] for r in candidates) for family in FAMILIES},
                        'excluded_classified_symbol_count': len(set(universe) & excluded),
                        'split_source_diagnostics': split_diagnostics,
                        'unresolved_action_window_exclusions': exclusion_records,
                        'exclusion_counts': dict(total_reasons), 'daily': daily,
                        'limitations': ['Current instrument directories are not point-in-time.',
                          'Corporate actions fetched after the window can be revised; announcement-time availability is unverified.',
                          'Unmapped split symbols and wholly unplaceable split dates remain explicit coverage risks; their affected instruments/windows cannot be identified.',
                          'Ambiguous splits with no ex-date use process date only to locate an unresolved screening interval, not to apply a price adjustment.',
                          'Cash dividends do not adjust price signals; portfolio entitlement is handled separately.',
                          'Adjusted daily moves over25pct are flagged, never silently removed.']}


def load_market(root=ROOT, source=SOURCE):
    """Return raw market[symbol][date] and the full source calendar."""
    source = Path(source)
    calendar = [r['date'] for r in json.loads((source / 'raw/calendar.json').read_text())]
    if calendar != sorted(set(calendar)):
        raise ValueError('duplicate_or_unordered_calendar')
    universe = [r['symbol'] for r in json.loads((source / 'universe.json').read_text())]
    if len(universe) != len(set(universe)):
        raise ValueError('duplicate_universe_symbol')
    market = {}
    for path in sorted((source / 'raw').glob('batch-*.json')):
        for symbol, bars in json.loads(path.read_text())['bars'].items():
            if symbol in market:
                raise ValueError('duplicate_symbol_batch')
            series = {}
            for bar in bars:
                day = datetime.fromisoformat(bar['t'].replace('Z', '+00:00')).astimezone(NY).date().isoformat()
                if day in series:
                    raise ValueError('duplicate_daily_bar')
                series[day] = bar
            market[symbol] = series
    if set(market) != set(universe):
        raise ValueError('universe_market_key_mismatch')
    return market, calendar


def build_features(root=ROOT, source=SOURCE):
    """Return raw market[symbol][date], full calendar and candidate rows."""
    global LAST_DIAGNOSTICS
    root, source = Path(root), Path(source)
    protocol = json.loads((root / 'protocol.json').read_text())
    market, calendar = load_market(root=root, source=source)
    universe = [r['symbol'] for r in json.loads((source / 'universe.json').read_text())]
    classifications = json.loads((source / 'current-instrument-classification.json').read_text())['rows']
    actions = json.loads((root / 'company-actions.json').read_text())['rows']
    candidates, diagnostics = construct_candidates(market, calendar, universe, classifications, actions, protocol)
    diagnostics['input_sha256'] = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in ('protocol.json', 'company-actions.json')}
    diagnostics['classification_sha256'] = hashlib.sha256((source / 'current-instrument-classification.json').read_bytes()).hexdigest()
    LAST_DIAGNOSTICS = diagnostics
    return market, calendar, candidates


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--output', type=Path, default=ROOT/'features.json')
    args = parser.parse_args()
    market, calendar, candidates = build_features(args.root, args.source)
    args.output.write_text(json.dumps(candidates, separators=(',', ':'))+'\n')
    diagnostics_path = args.output.with_name('features-diagnostics.json')
    diagnostics_path.write_text(json.dumps(LAST_DIAGNOSTICS, indent=2)+'\n')
    print(json.dumps({'market_symbols': len(market), 'calendar_sessions': len(calendar), 'candidate_rows': len(candidates), 'signal_dates': LAST_DIAGNOSTICS['signal_dates'], 'signal_counts': LAST_DIAGNOSTICS['signals_total'], 'output': args.output.name, 'diagnostics': diagnostics_path.name}))


if __name__ == '__main__':
    main()
