"""Run the frozen complete grid, including all unknowns and conditional cohorts."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from statistics import mean, median
import hashlib
import json

ROOT = Path(__file__).resolve().parent
PATHS = ROOT.parent / 's500-ep-paths-20261006'
GAPS = ROOT.parent / 's500-ep-gaps-20261006'
NEWS = ROOT.parent / 's500-news-20261006'


def read(path): return json.loads(Path(path).read_text())
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(name, obj): (ROOT / name).write_text(json.dumps(obj, separators=(',', ':')) + '\n')


def summaries(rows):
    grouped = defaultdict(list)
    for r in rows:
        grouped[(r['data_mode'], r['variant_id'], r['primary_news_gate'] is True)].append(r)
    out = []
    for (mode, variant, news), items in sorted(grouped.items()):
        entered = [r for r in items if any(t['side'] == 'BUY' for t in r['trades'])]
        closed = [r for r in entered if r['status'] == 'complete' and any(t['side'] == 'SELL' for t in r['trades'])]
        pnl = [float(r['profit']) for r in closed]
        out.append({'data_mode': mode, 'variant_id': variant, 'primary_news_gate_pass': news,
            'cases': len(items), 'statuses': dict(Counter(r['status'] for r in items)),
            'entered': len(entered), 'closed_model_trades': len(closed),
            'profitable_closed': sum(p > 0 for p in pnl), 'losing_closed': sum(p < 0 for p in pnl),
            'mean_closed_pnl': mean(pnl) if pnl else None, 'median_closed_pnl': median(pnl) if pnl else None,
            'best_closed_pnl': max(pnl) if pnl else None, 'worst_closed_pnl': min(pnl) if pnl else None,
            'failure_reasons': dict(Counter(r['failure']['reason'] for r in items if r['failure'])),
            'no_trade_complete': sum(r['status'] == 'complete' and not r['trades'] for r in items),
            'not_continuous_wealth': True, 'overlapping_events_and_coverage_selected_completes': True})
    return out


def main():
    from ep_event_engine import simulate_case, simulate_portfolio, simulate_cohort_portfolio
    design = read(ROOT / 'study-design.json')
    directories = {'paths': PATHS, 'gaps': GAPS, 'news': NEWS}
    for location, expected in design['input_sha256'].items():
        prefix, name = location.split('/', 1)
        assert sha(directories[prefix] / name) == expected, location
    for name, expected in design['new_input_sha256'].items():
        assert sha(ROOT / name) == expected, name
    assert not (ROOT / 'isolated-event-results.json').exists(), 'Preserve any earlier run before replacement'
    tests = read(ROOT / 'pre-run-validation.json')
    assert tests['implementation_tests_passed'] > 0 and tests['independent_tests_passed'] > 0
    assert tests['failed_tests'] == 0 and tests['engine_sha256'] == sha(ROOT / 'ep_event_engine.py')
    original = read(NEWS / 'market-gates.json')['rows']
    cases = sorted((r for r in original if r['market_gate_pass'] is True), key=lambda r: r['candidate_id'])
    candidates = read(PATHS / 'repaired-market-gates.json')['rows']
    evidence = {r['candidate_id']: {'pass_registered_news_gate': r['primary_gate_pass'],
        'news_status': r['primary_news_status'], 'all_possible_registered_catalysts_excluded': False}
        for r in read(NEWS / 'candidate-readiness.json')['candidates']}
    for r in read(PATHS / 'all-new-primary-reviews.json')['reviews']:
        evidence[r['candidate_id']] = {**r, 'all_possible_registered_catalysts_excluded': False}
    cohorts = read(ROOT / 'retrospective-cohorts.json')
    assert cohorts['market73'] == sorted(r['candidate_id'] for r in cases)
    assert cohorts['verified_news32'] == sorted(r['candidate_id'] for r in cases if evidence[r['candidate_id']]['pass_registered_news_gate'] is True)
    market = read(PATHS / 'holding-market.json'); daily = read(PATHS / 'daily-market-private.json')
    actions = read(PATHS / 'corporate-events.json'); calendar = read(PATHS / 'holding-input-design.json')['full_calendar']
    coverage = read(PATHS / 'portfolio-coverage.json')
    gap_evidence = read(ROOT / 'gap-evidence.json'); sessions = read(ROOT / 'verified-symbol-sessions.json')
    modes = list(design['modes'])
    variants = [{'exit_mode': mode, 'fraction': fraction, 'cost_bps': cost,
        'variant_id': mode + '__f' + str(fraction) + '__c' + str(cost)}
        for mode, fraction, cost in product(('fixed10', 'ma10_max63'), (.5, 1.0), (25, 50, 100))]
    inputs = {n: sha(ROOT / n) for n in ['study-design.json', 'ep_engine.py', 'ep_event_engine.py',
        'gap-evidence.json', 'retrospective-cohorts.json', 'verified-symbol-sessions.json',
        'source-verification.json', 'pre-run-validation.json', 'run_event_paths.py']}
    started = datetime.now(timezone.utc).isoformat()
    isolated = []
    for mode in modes:
        for case in cases:
            for variant in variants:
                result = simulate_case(case, variant, market, daily, calendar, actions,
                    mode=mode, gap_evidence=gap_evidence, verified_symbol_sessions=sessions)
                assert result['data_mode'] == mode
                result.update(variant_id=variant['variant_id'], date=case['date'], symbol=case['symbol'],
                    primary_news_gate=evidence[case['candidate_id']]['pass_registered_news_gate'],
                    primary_news_status=evidence[case['candidate_id']]['news_status'])
                isolated.append(result)
            if len(isolated) % 120 == 0 or len(isolated) == 1752:
                progress = {'isolated_results_done': len(isolated), 'isolated_total': 1752,
                            'updated_at': datetime.now(timezone.utc).isoformat()}
                save('progress.json', progress); print(json.dumps(progress), flush=True)
    save('isolated-event-results.json', {'started_at': started, 'finished_at': datetime.now(timezone.utc).isoformat(),
        'input_sha256': inputs, 'results': isolated, 'case_count': 73, 'variants': 12, 'data_modes': 2,
        'independent_hypothetical500_cases_not_continuous_wealth': True, 'broker_orders_sent': 0})
    portfolios = []
    for mode in modes:
        for scope in ['full_universe_strict', 'original_cohort_guard', 'retrospective_market73', 'retrospective_verified_news32']:
            for variant in variants:
                kwargs = {'mode': mode, 'gap_evidence': gap_evidence, 'verified_symbol_sessions': sessions}
                if scope.startswith('retrospective_'):
                    name = scope.removeprefix('retrospective_')
                    selected = [c for c in cases if c['candidate_id'] in cohorts[name]]
                    result = simulate_cohort_portfolio(selected, variant, market, daily, calendar, actions,
                        cohort_name=name, **kwargs)
                    result['original_registry_reference'] = 'all-candidate-registry.json'
                    result['cohort_selection_was_retrospective'] = True
                else:
                    result = simulate_portfolio(candidates, variant, market, daily, calendar, actions, evidence,
                        coverage=coverage if scope == 'full_universe_strict' else {'cohort_complete': False}, **kwargs)
                result.update(variant_id=variant['variant_id'], universe_scope=scope)
                portfolios.append(result)
    assert len(isolated) == 1752 and len(portfolios) == 96
    save('portfolio-event-results.json', {'started_at': started, 'finished_at': datetime.now(timezone.utc).isoformat(),
        'input_sha256': inputs, 'results': portfolios, 'broker_orders_sent': 0,
        'no_scope_replaces_original_full_market_unknown_paths': True})
    save('all-candidate-registry.json', {'candidate_count': len(candidates), 'rows': [
        {'candidate_id': c['candidate_id'], 'date': c['date'], 'symbol': c['symbol'],
         'market_gate_status': c['market_gate_status'], 'market_gate_pass': c['market_gate_pass'],
         'news_gate_pass': evidence[c['candidate_id']]['pass_registered_news_gate'],
         'news_status': evidence[c['candidate_id']]['news_status'],
         'member_market73': c['candidate_id'] in cohorts['market73'],
         'member_verified_news32': c['candidate_id'] in cohorts['verified_news32'],
         'unseen_news_absence_proven': False} for c in candidates]})
    parent = { (r['candidate_id'], r['variant_id']): r for r in read(PATHS / 'isolated-case-results.json')['results'] }
    transitions = []
    for mode in modes:
        group = [r for r in isolated if r['data_mode'] == mode]
        transitions.append({'mode': mode, 'status_transitions': dict(Counter(
            parent[(r['candidate_id'], r['variant_id'])]['status'] + ' -> ' + r['status'] for r in group)),
            'original_complete_cash_or_trade_changes': [r['candidate_id'] + '::' + r['variant_id'] for r in group
                if parent[(r['candidate_id'], r['variant_id'])]['status'] == 'complete'
                and (parent[(r['candidate_id'], r['variant_id'])]['profit'] != r['profit']
                     or [(t['side'], t['time'], t['quantity'], t['reference_price'], t['cash_amount']) for t in parent[(r['candidate_id'], r['variant_id'])]['trades']]
                     != [(t['side'], t['time'], t['quantity'], t['reference_price'], t['cash_amount']) for t in r['trades']]) ]})
    save('v1-v2-transitions.json', {'parent_results_sha256': sha(PATHS / 'isolated-case-results.json'), 'modes': transitions,
        'changed_results_are_a_new_model_not_repaired_original_fills': True})
    summary = {'generated_at': datetime.now(timezone.utc).isoformat(), 'total_results': 1848,
        'isolated_results': 1752, 'portfolio_results': 96,
        'isolated_statuses': {mode: dict(Counter(r['status'] for r in isolated if r['data_mode'] == mode)) for mode in modes},
        'isolated_groups': summaries(isolated),
        'portfolios': [{k: r.get(k) for k in ['data_mode', 'universe_scope', 'variant_id', 'status', 'profit',
            'ending_equity', 'cash_settled', 'failure', 'max_daily_liquidation_drawdown', 'drawdown_scope',
            'observed_fresh_daily_liquidation_drawdown', 'daily_drawdown_evaluated_snapshot_count',
            'daily_drawdown_excluded_snapshot_count',
            'terminal_equity_certified', 'milestones', 'skipped_minute_count']} | {
            'buys': sum(t['side'] == 'BUY' for t in r['trades']), 'sales': sum(t['side'] == 'SELL' for t in r['trades'])}
            for r in portfolios],
        'parent_example_cases': [{k: r.get(k) for k in ['candidate_id', 'data_mode', 'variant_id', 'status',
            'profit', 'failure', 'skipped_minute_count', 'trades']} for r in isolated
            if r['candidate_id'] in ['2026-01-06__IMSR', '2026-01-20__IBRX', '2026-01-23__LIF']
            and r['variant_id'] == 'fixed10__f1.0__c25'],
        'input_output_sha256': {n: sha(ROOT / n) for n in ['isolated-event-results.json', 'portfolio-event-results.json', 'all-candidate-registry.json', 'v1-v2-transitions.json']},
        'broker_orders_sent': 0, 'new_capital_injections': 0,
        'limits': ['All models use previously observed historical data; not independent or forward validation.',
                  'Provider mode assumes the aggregate event series is complete; no claim that all absent minutes are normal.',
                  'Retrospective73/32 cohorts are not complete point-in-time universe strategies.',
                  'Cost-stressed prices and fresh model marks are not executable fills or certified real wealth.',
                  'Isolated cases reset hypothetical500 only for diagnostics, not restart-funded portfolios.']}
    save('run-summary.json', summary)
    print(json.dumps({'total_results': 1848, 'isolated_statuses': summary['isolated_statuses'],
                      'portfolio_statuses': dict(Counter(r['status'] for r in portfolios))}, indent=2), flush=True)


if __name__ == '__main__': main()
