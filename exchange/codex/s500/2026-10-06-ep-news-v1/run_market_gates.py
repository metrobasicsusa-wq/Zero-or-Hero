"""Evaluate all original candidates regardless of news or subsequent returns."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from download_inputs import atomic
from market_gates import evaluate_candidate

ROOT = Path(__file__).resolve().parent


def main():
    candidates = json.loads((ROOT / 'candidates.json').read_text())['candidates']
    design = json.loads((ROOT / 'study-design.json').read_text())
    manifest = json.loads((ROOT / 'bars-input-manifest.json').read_text())
    market = json.loads((ROOT / 'minute-market.json').read_text())
    prep = json.loads((ROOT.parent / 's500-orb-20261006' / 'prepare.json').read_text())
    dates = [r['date'] for r in prep['full_calendar']]
    assert len(candidates) == 1079
    assert design['input_sha256']['candidates.json'] == hashlib.sha256((ROOT / 'candidates.json').read_bytes()).hexdigest()
    assert manifest['input_sha256']['study-design.json'] == hashlib.sha256((ROOT / 'study-design.json').read_bytes()).hexdigest()
    rows = []
    for candidate in candidates:
        assert candidate['prior_session'] == dates[dates.index(candidate['date']) - 1]
        result = evaluate_candidate(candidate, market.get(candidate['date'], {}).get(candidate['symbol'], {}))
        result.update({'candidate_id': candidate['candidate_id'], 'chronological_index': candidate['chronological_index']})
        rows.append(result)
    names = ['candidates.json', 'study-design.json', 'bars-input-manifest.json', 'minute-market.json',
             'market_gates.py', 'run_market_gates.py']
    result = {'generated_at': datetime.now(timezone.utc).isoformat(), 'candidate_count': len(rows),
        'input_sha256': {n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest() for n in names},
        'all_pages_complete': manifest['all_pages_complete'], 'fetch_failures': manifest['failures'],
        'rows': rows, 'no_news_selection_applied': True, 'no_cross_candidate_winner_selected': True,
        'no_returns_or_wealth_computed': True, 'broker_orders_sent': 0}
    atomic(ROOT / 'market-gates.json', result)
    summary = {'generated_at': result['generated_at'], 'candidates': len(rows),
        'status_counts': dict(Counter(r['market_gate_status'] for r in rows)),
        'market_gate_pass': sum(r['market_gate_pass'] is True for r in rows),
        'market_gate_fail': sum(r['market_gate_pass'] is False for r in rows),
        'market_gate_unknown': sum(r['market_gate_pass'] is None for r in rows),
        'opening_reference_mismatch': sum(r['opening30'] is not None and r['opening30']['daily_reference_open_matches_regular_open'] is False for r in rows),
        'passed_candidate_ids': [r['candidate_id'] for r in rows if r['market_gate_pass'] is True],
        'full_result_sha256': hashlib.sha256((ROOT / 'market-gates.json').read_bytes()).hexdigest(),
        'meaning': 'Market gates only; news/identity/executable quote qualifications remain separate. No trade or performance claim.'}
    atomic(ROOT / 'market-summary.json', summary)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
