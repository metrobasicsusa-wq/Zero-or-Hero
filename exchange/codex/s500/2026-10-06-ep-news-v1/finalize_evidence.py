"""Preserve every candidate and join evidence levels without creating simulated fills."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def read(name):
    return json.loads((ROOT / name).read_text())


def write(name, data):
    (ROOT / name).write_text(json.dumps(data, indent=2) + '\n')


def main():
    joined = read('joined-candidates.json')
    queue = read('primary-review-queue.json')
    new = [r for name in ['primary-imsr-lif.json', 'primary-glue-ibrx.json'] for r in read(name)['reviews']]
    assert len(new) == len({r['candidate_id'] for r in new}) == 5
    assert {r['candidate_id'] for r in new} == {r['candidate_id'] for r in queue['selected']}
    by_id = {r['candidate_id']: r for r in new}
    quote_rows = read('ep-quote-results.json')['rows']
    quotes = {(r['point_id'], r['max_age_seconds']): r for r in quote_rows}
    assert len(quotes) == len(quote_rows) == 146
    rows = []
    for base in joined['candidates']:
        key = base['candidate_id']
        review = by_id.get(key)
        primary = review or base['prior_primary_result']
        primary_pass = primary['pass_registered_news_gate'] if primary else None
        assert primary_pass is None or isinstance(primary_pass, bool)
        q = quotes.get((key, 1))
        q5 = quotes.get((key, 5))
        combined = (base['market_and_metadata_ready'] is True and primary_pass is True
                    and q is not None and q['accepted'] is True)
        # Pending/missing evidence is not a strategy loss, an excluded trade or a zero-return day.
        row = {'candidate_id': key, 'date': base['date'], 'symbol': base['symbol'],
            'chronological_index': base['chronological_index'],
            'market_gate_pass': base['market_gate_pass'], 'market_gate_status': base['market_gate_status'],
            'news_metadata_status': base['news_metadata_status'],
            'market_and_metadata_ready': base['market_and_metadata_ready'],
            'primary_review_phase': 'new_first5' if review else 'carried_first10' if primary else 'unreviewed',
            'primary_news_status': primary['news_status'] if primary else 'unreviewed',
            'primary_gate_pass': primary_pass,
            'primary_event_category': primary.get('event_category') if primary else None,
            'new_primary_rationale': review['rationale'] if review else None,
            'quote_1s_accepted': q['accepted'] if q else None,
            'quote_1s_reason': q['reason'] if q else 'not_in_market_pass_quote_sample',
            'quote_5s_accepted': q5['accepted'] if q5 else None,
            'all_current_entry_evidence_layers_pass': combined,
            'entry_quote_metrics_if_accepted': q['metrics'] if q and q['accepted'] else None,
            'observability_identity_execution_and_exit_unresolved': True,
            'simulated_or_actual_fill': False, 'strategy_return': None}
        rows.append(row)
    assert len(rows) == len({r['candidate_id'] for r in rows}) == 1079
    counts = {'candidates': len(rows),
        'market_pass': sum(r['market_gate_pass'] is True for r in rows),
        'market_fail': sum(r['market_gate_pass'] is False for r in rows),
        'market_unknown': sum(r['market_gate_pass'] is None for r in rows),
        'market_and_news_metadata_ready': sum(r['market_and_metadata_ready'] for r in rows),
        'market_metadata_and_quote_1s_accepted': sum(r['market_and_metadata_ready'] and r['quote_1s_accepted'] is True for r in rows),
        'new_primary_reviews': len(new),
        'new_primary_pass': sum(r['pass_registered_news_gate'] is True for r in new),
        'new_primary_fail': sum(r['pass_registered_news_gate'] is False for r in new),
        'new_primary_ambiguous': sum(r['pass_registered_news_gate'] is None for r in new),
        'total_primary_review_cases': sum(r['primary_review_phase'] != 'unreviewed' for r in rows),
        'total_primary_gate_pass': sum(r['primary_gate_pass'] is True for r in rows),
        'new_primary_eligible_deferred': len(queue['deferred_ids']),
        'quote_1s_accepted': sum(r['quote_1s_accepted'] is True for r in rows),
        'quote_5s_accepted': sum(r['quote_5s_accepted'] is True for r in rows),
        'all_current_entry_evidence_layers_pass': sum(r['all_current_entry_evidence_layers_pass'] for r in rows)}
    hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in [
        'joined-candidates.json', 'primary-review-queue.json', 'primary-imsr-lif.json',
        'primary-glue-ibrx.json', 'ep-quote-results.json', 'finalize_evidence.py']}
    generated = datetime.now(timezone.utc).isoformat()
    payload = {'generated_at': generated, 'input_sha256': hashes, 'counts': counts, 'candidates': rows,
        'meaning': 'Entry-evidence layers only; not historical trader observability, causation, executable orders, a selected portfolio or returns.',
        'all_candidates_preserved': True, 'broker_orders_sent': 0}
    write('candidate-readiness.json', payload)
    summary = {'generated_at': generated, 'input_sha256': hashes, 'counts': counts,
        'candidate_readiness_sha256': hashlib.sha256((ROOT / 'candidate-readiness.json').read_bytes()).hexdigest(),
        'new_primary_reviews': [{'candidate_id': r['candidate_id'], 'news_status': r['news_status'],
            'pass_registered_news_gate': r['pass_registered_news_gate'], 'event_category': r['event_category'],
            'rationale': r['rationale']} for r in sorted(new, key=lambda r: r['candidate_id'])],
        'entry_evidence_cases': [r['candidate_id'] for r in rows if r['all_current_entry_evidence_layers_pass']],
        'news_article_occurrences': read('news-summary.json')['input_article_rows'],
        'news_distinct_observed_ids': read('news-summary.json')['global_unique_news_ids'],
        'raw_quote_count': read('ep-quote-summary.json')['raw_quote_count'],
        'candidate_period': ['2026-01-02', '2026-10-05'],
        'complete_wealth_paths': 0, 'new_strategy_returns': None,
        'broker_orders_sent': 0, 'new_scheduler_deployed': False,
        'prior_primary_ALT_pass_but_no_market_entry_preserved': True,
        'limits': ['Current/non-PIT universe and historical identity bias remain.',
            'Unseen news revisions and contemporaneous page observability remain unknown.',
            '64 deferred primary reviews are not confirmed events or zero-return days.',
            'Entry evidence is not execution, exit evidence, portfolio selection or a return result.']}
    write('final-summary.json', summary)
    print(json.dumps({'counts': counts, 'cases': summary['entry_evidence_cases']}, indent=2))


if __name__ == '__main__':
    main()
