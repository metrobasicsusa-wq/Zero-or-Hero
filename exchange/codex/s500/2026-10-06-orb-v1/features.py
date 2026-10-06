"""Read-only ORB preprocessing; no current-day daily price/volume in screening."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BROAD = ROOT.parent / 's500-broad-20261006'
AGGRESSIVE = ROOT.parent / 's500-aggressive-20261006'
REGISTRY = ROOT.parent / 's500-web-research-20261006' / 'experiment-registry.json'
_spec = importlib.util.spec_from_file_location('_s500_prior_features', AGGRESSIVE / 'features.py')
_prior = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_prior)
valid_bar = _prior.valid_bar
index_splits = _prior.index_splits
adjusted_window = _prior.adjusted_window
LAST_RANKING_DIAGNOSTICS = {}


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _prepare(market, full_calendar, universe, classifications, action_rows,
             start='2026-01-01', end='2026-10-05'):
    """Pure-data helper used by prepare_daily and independent synthetic tests."""
    dates = [r['date'] for r in full_calendar]
    if dates != sorted(set(dates)):
        raise ValueError('calendar_must_be_unique_and_ordered')
    universe = sorted(universe)
    if len(set(universe)) != len(universe):
        raise ValueError('duplicate_universe_symbol')
    classes = defaultdict(list)
    for r in classifications:
        classes[r['symbol']].append(r)
    excluded = {s for s, rows in classes.items() if any(r.get('etf') == 'Y' or r.get('test_issue') == 'Y' for r in rows)}
    unknown = {s for s in universe if not classes[s] or not all(r.get('etf') == 'N' and r.get('test_issue') == 'N' for r in classes[s])}
    split_index, split_diagnostics = index_splits(action_rows)
    good = {s: {d for d, b in market.get(s, {}).items() if valid_bar(b)} for s in universe}
    candidates = {}; requests = defaultdict(set); histories = {}; daily_diag = []
    exclusions = []; totals = Counter(); calendar = []
    for i, session in enumerate(full_calendar):
        day = session['date']
        if not start <= day <= end:
            continue
        if i < 15:
            raise ValueError('ATR14_requires_15_prior_sessions')
        needed = dates[i-15:i]
        prior_opening = dates[i-14:i]
        histories[day] = prior_opening
        reasons = Counter(); rows = []
        calendar.append({k: session[k] for k in ('date', 'open', 'close', 'settlement_date')})
        for symbol in universe:
            if symbol in excluded:
                reasons['current_official_etf_or_test_issue'] += 1
                continue
            if any(d not in good[symbol] for d in needed):
                reasons['missing_or_invalid_prior15_daily_bars'] += 1
                continue
            series = market[symbol]
            relevant = [a for a in split_index.get(symbol, []) if a.get('screen_date') and needed[0] < a['screen_date'] <= day]
            if relevant:
                bars, applied, issues = adjusted_window(symbol, needed, series, day, split_index)
                if issues:
                    reasons['unresolved_split_window'] += 1
                    exclusions.append({'date': day, 'symbol': symbol, 'reasons': issues})
                    continue
            else:
                bars = [series[d] for d in needed]; applied = []
            mean_volume = sum(b['v'] for b in bars[-14:]) / 14
            if mean_volume < 1_000_000:
                reasons['prior14_mean_volume_below_1000000'] += 1
                continue
            true_ranges = [max(bars[j]['h']-bars[j]['l'], abs(bars[j]['h']-bars[j-1]['c']),
                               abs(bars[j]['l']-bars[j-1]['c'])) for j in range(1, 15)]
            atr = sum(true_ranges) / 14
            if atr <= .50:
                reasons['prior14_ATR_not_above_050'] += 1
                continue
            row = {'symbol': symbol, 'atr14': atr, 'mean_volume14': mean_volume,
                   'prior_close': bars[-1]['c'], 'classification_unknown': symbol in unknown,
                   'history_first_date': needed[0], 'history_last_date': needed[-1],
                   'split_adjustments': applied}
            rows.append(row)
            for request_day in [day, *prior_opening]:
                requests[request_day].add(symbol)
        candidates[day] = rows
        daily_diag.append({'date': day, 'daily_eligible_count': len(rows),
                           'classification_unknown_count': sum(r['classification_unknown'] for r in rows),
                           'exclusion_counts': dict(reasons)})
        totals.update(reasons)
    return {'calendar': calendar, 'full_calendar': full_calendar,
            'daily_candidates': candidates,
            'opening_requests': {d: sorted(s) for d,s in sorted(requests.items())},
            'prior_opening_dates': histories, 'split_index': split_index,
            'diagnostics': {'status': 'daily_screen_complete_opening_data_not_yet_evaluated',
                'evaluation_sessions': len(calendar), 'universe_symbols': len(universe),
                'daily_candidate_rows': sum(len(v) for v in candidates.values()),
                'ever_daily_eligible_symbols': len({r['symbol'] for rr in candidates.values() for r in rr}),
                'opening_symbol_date_requests': sum(len(v) for v in requests.values()),
                'opening_request_dates': len(requests), 'exclusion_counts': dict(totals),
                'action_ambiguity_exclusions': exclusions,
                'split_source_diagnostics': split_diagnostics, 'daily': daily_diag,
                'current_day_daily_bar_used_for_selection': False,
                'top500_limit_applied': False,
                'limitations': ['Current universe and classification are not historical point-in-time.',
                  'Split source revisions/coverage and unknown identity mappings remain unresolved input risks.',
                  'No current-day daily open/close/high/low/volume filters are applied here; actual opening price is evaluated only from first5-minute input.',
                  'Calendar settlement_date is source data and still requires explicit engine cash-settlement accounting.']}}


def prepare_daily():
    """Return calendar, prior-only daily candidates and union opening requests."""
    market, _ = _prior.load_market(source=BROAD)
    full_calendar = json.loads((BROAD / 'raw/calendar.json').read_text())
    universe = [r['symbol'] for r in json.loads((BROAD / 'universe.json').read_text())]
    classifications = json.loads((BROAD / 'current-instrument-classification.json').read_text())['rows']
    actions = json.loads((AGGRESSIVE / 'company-actions.json').read_text())['rows']
    prepared = _prepare(market, full_calendar, universe, classifications, actions)
    prepared['input_sha256'] = {'registry': _sha(REGISTRY), 'universe': _sha(BROAD/'universe.json'),
                               'classification': _sha(BROAD/'current-instrument-classification.json'),
                               'calendar': _sha(BROAD/'raw/calendar.json'),
                               'company_actions': _sha(AGGRESSIVE/'company-actions.json'),
                               'daily_input_manifest': _sha(BROAD/'input-manifest.json')}
    return prepared


def _volume_factor(symbol, bar_date, decision_date, split_index):
    factor = 1.
    for action in split_index.get(symbol, []):
        effective = action.get('screen_date')
        if effective and bar_date < effective <= decision_date:
            if action['issues']:
                raise ValueError('unresolved_split_in_opening_volume_history')
            factor *= action['new_rate'] / action['old_rate']
    return factor


def rank_openings(prepared, opening_market, diagnostics_path=ROOT/'ranking-diagnostics.json'):
    """Return dict[date] of top20 rows BEFORE filtering candle direction.

    opening_market[date][symbol] is exactly that day's completed09:30-09:35
    OHLCV, keyed o/h/l/c/v. Missing candidate inputs flag potential ranking gaps;
    callers must inspect prepared['ranking_diagnostics'] before simulation.
    Retains available rankings with ranking_complete=False, never silently
    certifies a partial day. Also updates LAST_RANKING_DIAGNOSTICS.
    """
    global LAST_RANKING_DIAGNOSTICS
    output = {}; by_day = {}; missing = []; rejection_counts = Counter()
    all_qualified = []
    for session in prepared['calendar']:
        day = session['date']; qualified = []; gaps = []; rejected = Counter()
        prior_dates = prepared['prior_opening_dates'][day]
        if len(prior_dates) != 14:
            raise ValueError('opening_rv_requires14prior_sessions')
        for candidate in prepared['daily_candidates'][day]:
            symbol = candidate['symbol']; today = opening_market.get(day, {}).get(symbol)
            if not valid_bar(today):
                gaps.append({'date': day, 'symbol': symbol, 'reason': 'missing_or_invalid_current_opening'})
                continue
            if today['o'] <= 5:
                rejected['opening_price_not_above5'] += 1
                continue
            volumes = []; unavailable = []
            for previous in prior_dates:
                bar = opening_market.get(previous, {}).get(symbol)
                if not valid_bar(bar):
                    unavailable.append(previous)
                else:
                    volumes.append(bar['v']*_volume_factor(symbol, previous, day, prepared['split_index']))
            if unavailable:
                gaps.append({'date': day, 'symbol': symbol, 'reason': 'missing_or_invalid_prior_openings',
                             'missing_dates': unavailable})
                continue
            mean_opening = sum(volumes) / 14
            if mean_opening <= 0:
                gaps.append({'date': day, 'symbol': symbol, 'reason': 'zero_prior14_opening_volume_mean'})
                continue
            rv = today['v']/mean_opening
            if rv < 1:
                rejected['relative_volume_below1'] += 1
                continue
            qualified.append({**candidate, 'date': day, 'rv': rv, 'relative_volume': rv,
                              'mean_opening_volume14': mean_opening,
                              'opening_volume': today['v'], 'or_open': today['o'],
                              'or_close': today['c'], 'or_high': today['h'], 'or_low': today['l'],
                              'long_eligible': today['c'] > today['o']})
        qualified.sort(key=lambda r: (-r['rv'], r['symbol']))
        complete = not gaps
        for rank, row in enumerate(qualified, 1):
            row['rank'] = rank
            row['ranking_complete'] = complete
        output[day] = qualified[:20]
        all_qualified.extend(qualified)
        missing.extend(gaps)
        rejected['eligible_outside_top20'] += max(0, len(qualified)-20)
        rejection_counts.update(rejected)
        by_day[day] = {'ranking_complete': complete,
                       'daily_eligible_candidates': len(prepared['daily_candidates'][day]),
                       'valid_rv_qualified': len(qualified), 'ranked_top20_count': min(20,len(qualified)),
                       'top20_long_eligible': sum(r['long_eligible'] for r in qualified[:20]),
                       'input_gap_count': len(gaps), 'rejection_counts': dict(rejected)}
    coverage = {d: {'ranking_complete': row['ranking_complete'],
                    'ranking_missing': [x for x in missing if x['date'] == d]}
                for d, row in by_day.items()}
    diagnostics = {'coverage': coverage, 'days': by_day, 'complete_ranking_days': sum(r['ranking_complete'] for r in by_day.values()),
                   'incomplete_ranking_days': sum(not r['ranking_complete'] for r in by_day.values()),
                   'input_gaps': missing, 'rejection_counts': dict(rejection_counts),
                   'all_rv_qualified': all_qualified,
                   'no_direction_backfill': 'Top20 ranking precedes bullish direction; no replacement of a red/doji top20 name with rank21.',
                   'gap_policy': 'Available rows are discovery rankings only when ranking_complete=false; caller must explicitly mark corresponding results incomplete or unsupported, not silently trade as full-universe evidence.'}
    prepared['ranking_diagnostics'] = diagnostics
    LAST_RANKING_DIAGNOSTICS = diagnostics
    if diagnostics_path is not None:
        Path(diagnostics_path).write_text(json.dumps(diagnostics,separators=(',',':'))+'\n')
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--output', type=Path, default=ROOT/'prepare.json')
    args = parser.parse_args()
    result = prepare_daily()
    args.output.write_text(json.dumps(result,separators=(',',':'))+'\n')
    print(json.dumps({k:v for k,v in result['diagnostics'].items() if k not in ('daily','action_ambiguity_exclusions','limitations')},indent=2))


if __name__ == '__main__':
    main()
