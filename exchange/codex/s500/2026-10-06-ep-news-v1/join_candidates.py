"""Join evidence levels without converting metadata into catalyst or trade proof."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from download_inputs import atomic

ROOT = Path(__file__).resolve().parent


def read(name):
    return json.loads((ROOT / name).read_text())


def main():
    candidates = read('candidates.json')['candidates']
    market = {r['candidate_id']: r for r in read('market-gates.json')['rows']}
    news = {r['candidate_id']: r for r in read('news-enriched.json')['candidates']}
    private = {r['candidate_id']: r for r in read('review-material-private.json')['candidates']}
    old_path = ROOT.parent / 's500-quote-20261006' / 'ep-followup.json'
    old = json.loads(old_path.read_text())
    previous = {r['date'] + '__' + r['symbol']: r for r in old['candidate_reviews']}
    expected = {r['candidate_id'] for r in candidates}
    assert set(market) == set(news) == set(private) == expected and len(expected) == 1079
    rows, eligible = [], []
    for c in candidates:
        key = c['candidate_id']
        m, n, p = market[key], news[key], previous.get(key)
        metadata_ready = n['status'] == 'metadata_ready_for_review' and n['fetch_complete'] is True
        review_ready = m['market_gate_pass'] is True and metadata_ready
        prior_pass = bool(p and p['pass_registered_news_gate'] is True)
        row = {'candidate_id': key, 'date': c['date'], 'symbol': c['symbol'],
            'chronological_index': c['chronological_index'], 'market_gate_status': m['market_gate_status'],
            'market_gate_pass': m['market_gate_pass'], 'news_metadata_status': n['status'],
            'news_fetch_complete': n['fetch_complete'], 'metadata_ready_for_primary_review': metadata_ready,
            'clean_article_count': n['metadata_ready_unique_payloads'],
            'keyword_hints': sorted({hint for a in n['articles'] if a['metadata_status'] == 'metadata_ready_for_review' for hint in a['keyword_hints']}),
            'already_primary_reviewed': p is not None, 'prior_primary_gate_pass': prior_pass,
            'prior_primary_result': None if not p else {'news_status': p['news_status'],
                'pass_registered_news_gate': p['pass_registered_news_gate'], 'event_category': p['event_category']},
            'market_and_metadata_ready': review_ready,
            'signal': m['signal'], 'entry_reference': m['entry_reference'],
            'new_primary_catalyst_confirmed': False, 'trade_or_return_claim': False}
        rows.append(row)
        if review_ready and not p:
            eligible.append(row)
    queue = eligible[:5]
    hashes = {n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest() for n in
              ['candidates.json', 'market-gates.json', 'news-enriched.json', 'join_candidates.py']}
    hashes['prior_ep_followup'] = hashlib.sha256(old_path.read_bytes()).hexdigest()
    joined = {'created_at': datetime.now(timezone.utc).isoformat(), 'input_sha256': hashes,
        'candidate_count': len(rows), 'candidates': rows,
        'counts': {'market_pass': sum(r['market_gate_pass'] is True for r in rows),
            'market_and_metadata_ready': sum(r['market_and_metadata_ready'] for r in rows),
            'market_pass_source_no_articles': sum(r['market_gate_pass'] is True and r['news_metadata_status'] == 'source_no_articles' for r in rows),
            'previously_primary_reviewed': sum(r['already_primary_reviewed'] for r in rows),
            'prior_primary_pass': sum(r['prior_primary_gate_pass'] for r in rows),
            'prior_primary_and_market_pass': sum(r['prior_primary_gate_pass'] and r['market_gate_pass'] is True for r in rows),
            'new_primary_review_ready': len(eligible), 'new_primary_review_selected': len(queue)},
        'meaning': 'Conditional evidence join, not a completed EP strategy or a cross-candidate order selection.',
        'all_unknowns_and_failures_preserved': True, 'broker_orders_sent': 0}
    atomic(ROOT / 'joined-candidates.json', joined)
    atomic(ROOT / 'primary-review-queue.json', {'created_at': joined['created_at'], 'input_sha256': hashes,
        'selection_rule': 'First5 chronological new market-pass and metadata-ready cases; no keyword/return ranking. Previous10primaryreviews retained separately.',
        'selected': queue, 'all_eligible_ids': [r['candidate_id'] for r in eligible],
        'deferred_ids': [r['candidate_id'] for r in eligible[5:]], 'no_verified_new_events_yet': True})
    atomic(ROOT / 'primary-review-material-private.json', {'publication': 'PRIVATE raw provider headlines/summaries; never publish',
        'selected': [{'candidate': row, 'news': private[row['candidate_id']]} for row in queue]})
    print(json.dumps({'counts': joined['counts'], 'queue': [r['candidate_id'] for r in queue]}, indent=2))


if __name__ == '__main__':
    main()
