"""Freeze the entire quote feasibility sample before fetching new quotes."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
PRIOR = ROOT.parent / 's500-orb-20261006'


def write(name, value):
    (ROOT / name).write_text(json.dumps(value, indent=2) + '\n')


def main():
    if (ROOT / 'study-design.json').exists():
        raise RuntimeError('design_exists_do_not_overwrite_preregistration')
    episodes = json.loads((PRIOR / 'daily-diagnostics.json').read_text())['episodes']
    ranked = json.loads((PRIOR / 'ranked.json').read_text())
    coverage = json.loads((PRIOR / 'bounded-coverage.json').read_text())['coverage']
    certified = [d for d, c in sorted(coverage.items()) if c['ranking_complete']][:10]
    points = {}

    def point(day, symbol, at, cohort, metadata):
        at = datetime.fromisoformat(at).astimezone(timezone.utc)
        key = (day, symbol, at.isoformat())
        if key not in points:
            points[key] = {'id': day + '__' + symbol + '__' + at.strftime('%H%M%S') + 'Z',
                           'date': day, 'symbol': symbol, 'decision_time': at.isoformat(),
                           'request_start': (at - timedelta(seconds=60)).isoformat(),
                           'request_end': at.isoformat(), 'cohorts': [], 'metadata': []}
        if cohort not in points[key]['cohorts']:
            points[key]['cohorts'].append(cohort)
        if metadata not in points[key]['metadata']:
            points[key]['metadata'].append(metadata)

    for day in certified:
        at = datetime.fromisoformat(day + 'T09:35:00').replace(tzinfo=ZoneInfo('America/New_York')).isoformat()
        for row in ranked[day]:
            point(day, row['symbol'], at, 'first10_certified_days_all_top20',
                  {'atr14': row['atr14'], 'rank': row['rank'], 'rv': row['rv'],
                   'long_eligible': row['long_eligible'], 'classification_unknown': row['classification_unknown']})
    entries = {}
    gaps = {}
    for result in episodes:
        for e in result['entries']:
            entries[(e['date'], e['symbol'], e['entry_time'])] = e
        gap = result.get('incomplete')
        if gap and result.get('open_position'):
            gaps[(gap['date'], gap['symbol'], gap['time'])] = (gap, result['open_position'])
    for (day, symbol, at), e in sorted(entries.items()):
        point(day, symbol, at, 'all_prior_selected_entries_including_held_failures',
              {'atr14': e['atr14'], 'rank': e['rank'], 'rv': e['rv'],
               'original_minute_entry_open': e['entry_price'], 'original_stop_price': e['stop_price'],
               'signal_completed_at': e['signal_completed_at'],
               'reference_warning': 'Minute opening trade may occur later than exact quote decision timestamp.'})
    for (day, symbol, at), (gap, e) in sorted(gaps.items()):
        point(day, symbol, at, 'all_prior_held_minute_failures',
              {'atr14': e['atr14'], 'original_stop_price': e['stop_price'],
               'gap_reason': gap['reason'], 'selected_after_observing_failure': True})
    rows = [points[k] for k in sorted(points)]
    write('sample.json', {'created_at': datetime.now(timezone.utc).isoformat(), 'points': rows,
        'cohort_points': {'first10_certified_days_all_top20': 200,
                          'all_prior_selected_entries_including_held_failures': len(entries),
                          'all_prior_held_minute_failures': len(gaps)},
        'certified_dates': certified, 'unique_request_windows': len(rows),
        'not_an_unbiased_market_sample': True})
    inputs = ['daily-diagnostics.json', 'ranked.json', 'bounded-coverage.json', 'results.json']
    design = {'id': 's500-quote-feasibility-v1', 'registered_at': datetime.now(timezone.utc).isoformat(),
        'parent_publication_commit': '33f739cfdced198c618bcbe343b423b5c144c4da',
        'frozen_before_new_quote_fetch': True,
        'research_type': 'execution-cost feasibility and data availability; not an investment backtest or new wealth path',
        'known_prior_results': 'Original12 continuous ORB paths incomplete. Seventeen completed independent-day trades and2 held-gap days already observed; every19 selected entry is included, never only winners.',
        'sample_sha256': hashlib.sha256((ROOT / 'sample.json').read_bytes()).hexdigest(),
        'prior_input_sha256': {f: hashlib.sha256((PRIOR / f).read_bytes()).hexdigest() for f in inputs},
        'sample_rules': ['First10 chronological mathematically certified top20 days: all20 names including red/doji, unaffordable and missing-minute cases.',
            'All unique selectedentry stock-days from prior six diagnostic variants, including both held-data failures.',
            'Both first held-data failure timestamps separately sampled; these are known-failure diagnostics, not random observations.'],
        'quote_rules': {'request': 'SIP raw historical stockquotes, inclusive [decision-60s, decision], all pages up to50/window.',
            'latest': 'Use maximum event nanosecond timestamp <= decision. Never use future quote or fall back from invalid/withdrawn latestquote to older validquote.',
            'ties': 'Conflicting latest-timestamp quote state without certified order is ambiguous; exact-state duplicates may collapse.',
            'validation': 'Both sides strictly positive, ask>=bid, both displayed numeric sizes>0, conditions exactly [R] conservative regular-only; unsupported condition quarantined even if price looks plausible.',
            'age_seconds': [1, 5], 'primary_age_seconds': 1,
            'size': 'Keep source bs/as numeric values. Do not infer shares/roundlots or claim available execution quantity without verified unit evidence.',
            'clock': 'Event time availability does not prove client receive time; no live latency/fill claim.',
            'fees': 'No fees/slippage or improvement in displayed-side hypothetical crossing metric; not net achievable profit.'},
        'metrics': ['Valid latestquote availability at1second and5second age thresholds, all invalid/missing/stale/tie reasons retained.',
            'Displayed absolute/relative spread and spread/(0.1*priorATR14).',
            'For budget250and500, floor(budget/ask) shares is budget-only, not displayed size capacity or a guaranteed fill; hypothetical samequote crossing cost=qty*(ask-bid).',
            'Signed ask-minus-original-minute-entry reference comparison, with explicit timestamp comparability limit.',
            'No trade exit, PnL, equity compounding, round restarts or10000target estimate generated.'],
        'limitations': ['Current/asof symbol mapping and possible alias duplicates remain unresolved.',
            'Chronological first10 certified days and prior selected19 entries are conditionally selected from a source-incomplete universe; do not extrapolate population prevalence.',
            'Quoted positive size and regular conditions do not certify routing, firm executable depth or order acceptance.',
            'Costs observed on prior selected signals cannot retroactively fix or validate the failed strategy.'],
        'broker_orders_sent': 0, 'new_scheduler_deployed': False}
    write('study-design.json', design)
    print(json.dumps({'points': len(rows), 'entry_points': len(entries), 'held_failure_points': len(gaps),
                      'certified_days': certified, 'registered_at': design['registered_at']}, indent=2))


if __name__ == '__main__':
    main()
