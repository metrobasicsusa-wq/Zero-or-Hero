"""Offline driver. Always retain original strict paths and prespecified gap sensitivity."""
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from engine import run_path

ROOT = Path(__file__).resolve().parent


def read(name):
    return json.loads((ROOT / name).read_text())


def main():
    prepared = read('prepare.json')
    ranked = read('ranked.json')
    strict = read('ranking-diagnostics.json')
    bounded = read('bounded-coverage.json')
    minutes = read('minute-market.json')
    calendar = prepared['calendar']
    expected = {r['date'] for r in calendar}
    assert set(ranked) == set(strict['coverage']) == set(bounded['coverage']) == expected
    assert len(calendar) == 190 and calendar[0]['date'] == '2026-01-02' and calendar[-1]['date'] == '2026-10-05'
    for day, rows in ranked.items():
        for row in rows:
            assert all(k in row for k in ['opening_volume', 'mean_opening_volume14', 'rv'])
            assert row['mean_opening_volume14'] > 0
    for name in ('openings-manifest.json', 'intraday-manifest.json'):
        assert read(name)['all_pages_complete'] is True
    source_names = ['registry.json', 'implementation-amendment.json', 'data-gap-amendment.json',
                    'prepare.json', 'ranked.json', 'ranking-diagnostics.json', 'bounded-coverage.json',
                    'opening-market.json', 'minute-market.json', 'engine.py', 'features.py',
                    'coverage_bounds.py', 'market_data.py', 'download_inputs.py', 'run_research.py']
    source_hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in source_names}
    paths = []
    for mode in ('original_strict', 'gap_guarded_sensitivity'):
        cov = strict['coverage'] if mode == 'original_strict' else bounded['coverage']
        for allocation in (.5, 1.):
            for cost in (25, 50, 100):
                result = run_path(ranked, calendar, minutes, allocation, cost, coverage=cov,
                                  pre_entry_gap_policy='stop' if mode == 'original_strict' else 'skip_session')
                result['source_id'] = result['id']
                result['id'] = mode + '__' + result['id']
                result['research_mode'] = mode
                result['source_sha256'] = source_hashes
                paths.append(result)
                target = ROOT / 'paths' / (result['id'] + '.json')
                target.parent.mkdir(exist_ok=True)
                target.write_text(json.dumps(result, separators=(',', ':')) + '\n')
    summary_rows = []
    for r in paths:
        skipped = [d for d in r['daily'] if d['status'] == 'skipped_data_gap']
        summary_rows.append({k: r[k] for k in ['id', 'research_mode', 'parameters', 'status', 'incomplete',
            'initial_funding', 'additional_funding', 'restart_count', 'final_equity', 'settled_cash',
            'unsettled_cash', 'last_observed_equity', 'max_daily_liquidation_drawdown',
            'max_observed_minute_liquidation_drawdown', 'milestones']} | {
            'sessions_accounted': len(r['daily']), 'trades': len(r['trades']),
            'profitable_trades': sum(t['pnl'] > 0 for t in r['trades']),
            'losing_trades': sum(t['pnl'] < 0 for t in r['trades']),
            'modeled_pnl': sum(t['pnl'] for t in r['trades']),
            'exit_reasons': dict(Counter(t['exit_reason'] for t in r['trades'])),
            'skipped_data_sessions': len(skipped),
        })
    summary = {'created_at': datetime.now(timezone.utc).isoformat(), 'study': 'ORB2026-YTD-v1',
        'period': ['2026-01-02', '2026-10-05'], 'evaluation_sessions': len(calendar),
        'original_paths': 6, 'gap_guarded_sensitivity_paths': 6,
        'source_sha256': source_hashes, 'paths': summary_rows,
        'ownership': 'Codex original implementation and simulations; published ORB article provides method inspiration only.',
        'no_complete_year_claim': '2026 year-to-date only; future sessions not evaluated.',
        'broker_orders_sent': 0, 'github_research_workflow_deployed': False}
    (ROOT / 'results.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
