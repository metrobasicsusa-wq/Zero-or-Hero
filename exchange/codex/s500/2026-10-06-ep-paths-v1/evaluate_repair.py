"""Compare exact-symbol re-fetches without filling gaps or choosing a favorable revision."""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import copy
import hashlib
import importlib.util
import json

ROOT = Path(__file__).resolve().parent
PRIOR = ROOT.parent / 's500-news-20261006'
spec = importlib.util.spec_from_file_location('prior_market_gates', PRIOR / 'market_gates.py')
gates = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gates)


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(name, value):
    (ROOT / name).write_text(json.dumps(value, separators=(',', ':')) + '\n')


def main():
    original = read(PRIOR / 'market-gates.json')['rows']
    candidates = {c['candidate_id']: c for c in read(PRIOR / 'candidates.json')['candidates']}
    oldmarket = read(PRIOR / 'minute-market.json')
    fresh = read(ROOT / 'repair-market.json')
    manifest = read(ROOT / 'repair-input-manifest.json')
    complete = {r['task_id'].removeprefix('repair__') for r in manifest['records']}
    rows, checks, newpasses = [], [], []
    for old in original:
        row = copy.deepcopy(old)
        if old['market_gate_pass'] is None:
            key, day, symbol = old['candidate_id'], old['date'], old['symbol']
            before = oldmarket.get(day, {}).get(symbol, {})
            after = fresh.get(day, {}).get(symbol, {})
            conflicts = [offset for offset in sorted(before.keys() & after.keys(), key=int)
                         if any(before[offset].get(k) != after[offset].get(k) for k in ['o', 'h', 'l', 'c', 'v', 'n', 'vw'])]
            fetched = key in complete
            evaluation = gates.evaluate_candidate(candidates[key], after) if fetched else None
            comparison = {'candidate_id': key, 'old_status': old['market_gate_status'],
                'fresh_fetch_complete': fetched, 'old_bar_count': len(before), 'fresh_bar_count': len(after),
                'new_minute_offsets': sorted(after.keys() - before.keys(), key=int),
                'missing_from_fresh_offsets': sorted(before.keys() - after.keys(), key=int),
                'common_bar_value_conflicts': conflicts,
                'fresh_evaluation_status': evaluation['market_gate_status'] if evaluation else None}
            if fetched and not conflicts:
                row = evaluation
                row.update(candidate_id=key, chronological_index=old['chronological_index'])
                comparison['decision'] = 'fresh_window_evaluated_without_merging_or_zero_filling'
                if row['market_gate_pass'] is True:
                    newpasses.append(key)
            elif conflicts:
                row['market_gate_pass'] = None
                row['market_gate_status'] = 'source_revision_conflict_unknown'
                row['issues'].append({'fresh_source_conflicts': conflicts})
                comparison['decision'] = 'preserve_unknown_no_favorable_revision_selection'
            else:
                comparison['decision'] = 'fetch_failed_original_unknown_retained'
            row['repair_evidence'] = comparison
            checks.append(comparison)
        rows.append(row)
    assert len(checks) == 112 and len(rows) == 1079
    payload = {'generated_at': datetime.now(timezone.utc).isoformat(), 'rows': rows,
        'input_sha256': {'prior_market_gates': sha(PRIOR / 'market-gates.json'),
            'prior_minute_market': sha(PRIOR / 'minute-market.json'),
            'repair_manifest': sha(ROOT / 'repair-input-manifest.json'),
            'repair_market': sha(ROOT / 'repair-market.json'), 'evaluate_repair.py': sha(ROOT / 'evaluate_repair.py')},
        'newly_market_pass_with_exit_and_primary_inputs_pending': newpasses,
        'original73_diagnostic_cohort_unchanged': True, 'all_candidates_retained': True}
    save('repaired-market-gates.json', payload)
    save('repair-summary.json', {'generated_at': payload['generated_at'], 'checked': len(checks), 'comparisons': checks,
        'status_counts': dict(Counter(r['market_gate_status'] for r in rows)),
        'market_pass': sum(r['market_gate_pass'] is True for r in rows),
        'market_fail': sum(r['market_gate_pass'] is False for r in rows),
        'market_unknown': sum(r['market_gate_pass'] is None for r in rows),
        'newly_market_pass_with_exit_and_primary_inputs_pending': newpasses,
        'source_conflict_cases': sum(bool(r['common_bar_value_conflicts']) for r in checks),
        'all_refetch_requests_completed': manifest['all_pages_complete'],
        'source_sha256': sha(ROOT / 'repaired-market-gates.json'), 'zero_filled_bars': 0})
    coverage = read(ROOT / 'cached-source-manifest.json')
    calendar = read(ROOT / 'holding-input-design.json')['full_calendar']
    full = {'cohort_complete': False, 'label': 'full_universe_coverage_not_certified',
        'reasons': ['current_not_point_in_time_universe', 'daily_open_screen_can_omit_regular_open_gappers',
                    'prior_history_and_action_mapping_gaps'],
        'daily_screen_counts': coverage['daily_screen_counts'],
        'days': {s['date']: {'initial_screen_complete': False} for s in calendar if s['date'] >= '2026-01-02'}}
    save('portfolio-coverage.json', full)
    print(json.dumps({'repaired_cases': len(checks), 'newpasses': newpasses,
        'pass': sum(r['market_gate_pass'] is True for r in rows),
        'unknown': sum(r['market_gate_pass'] is None for r in rows),
        'conflicts': sum(bool(r['common_bar_value_conflicts']) for r in checks)}, indent=2))


if __name__ == '__main__':
    main()
