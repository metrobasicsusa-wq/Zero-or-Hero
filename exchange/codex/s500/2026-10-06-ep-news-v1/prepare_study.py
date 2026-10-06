"""Freeze every original EP stock-candidate window before new batch inputs."""
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
ORB = ROOT.parent / 's500-orb-20261006'
QUOTE = ROOT.parent / 's500-quote-20261006'
NY = ZoneInfo('America/New_York')


def dump(name, value):
    (ROOT / name).write_text(json.dumps(value, indent=2) + '\n')


def main():
    if (ROOT / 'study-design.json').exists():
        raise RuntimeError('frozen_design_already_exists')
    inventory = json.loads((ORB / 'ep-candidate-inventory.json').read_text())
    prep = json.loads((ORB / 'prepare.json').read_text())
    calendar = {r['date']: r for r in prep['full_calendar']}
    candidates = sorted((r for r in inventory['candidates'] if r['stock_research_eligible_initial']),
                        key=lambda r: (r['date'], r['symbol']))
    assert len(candidates) == 1079 and len({(r['date'], r['symbol']) for r in candidates}) == 1079
    by_day = defaultdict(list)
    for index, candidate in enumerate(candidates, 1):
        day, previous = candidate['date'], candidate['prior_session']
        opening = datetime.fromisoformat(day + 'T' + calendar[day]['open']).replace(tzinfo=NY)
        prior_close = datetime.fromisoformat(previous + 'T' + calendar[previous]['close']).replace(tzinfo=NY)
        assert opening > prior_close
        candidate.update({'candidate_id': day + '__' + candidate['symbol'], 'chronological_index': index,
            'news_request_start': prior_close.astimezone(timezone.utc).isoformat(),
            'news_request_end': opening.astimezone(timezone.utc).isoformat(),
            'news_comparison': 'strict previous regular close < created < regular open; updated must also be < regular open and >= created'})
        by_day[day].append(candidate['symbol'])
    dump('candidates.json', {'source_inventory_sha256': hashlib.sha256((ORB / 'ep-candidate-inventory.json').read_bytes()).hexdigest(),
                            'candidate_count': len(candidates), 'candidates': candidates})
    tasks = []
    for day, symbols in sorted(by_day.items()):
        for offset in range(0, len(symbols), 50):
            tasks.append({'task_id': day + '__' + str(offset).zfill(4), 'date': day,
                'symbols': sorted(symbols[offset:offset + 50]),
                'start': datetime.fromisoformat(day + 'T09:30:00').replace(tzinfo=NY).astimezone(timezone.utc).isoformat(),
                'end': datetime.fromisoformat(day + 'T11:02:00').replace(tzinfo=NY).astimezone(timezone.utc).isoformat()})
    dump('bar-tasks.json', {'tasks': tasks, 'scope': 'First30minute gates, signal minutes until10:59, entry reference through11:01; no exit/return model.'})
    design = {'id': 's500-full-EP-news-and-gates-v1', 'registered_at': datetime.now(timezone.utc).isoformat(),
        'frozen_before_new_batch_news_or_minute_data': True,
        'parent_quote_commit': '885645ae0a5ba00eff31c5ccf496586d5577ad9d',
        'parent_ORB_commit': '33f739cfdced198c618bcbe343b423b5c144c4da',
        'period': ['2026-01-02', '2026-10-05'], 'candidate_count': 1079,
        'candidate_selection': 'All original stock-research-eligible broad-universe price/gap candidates, chronological date/symbol; no winner or news keyword selection for retrieval.',
        'known_prior': 'First10primarynewsreviews known; only ALTverifiednews, ALTgap/volume pass butno breakout. Prior12ORBcapitalpaths incomplete; no EPreturnpaths validated.',
        'input_sha256': {'candidates.json': hashlib.sha256((ROOT / 'candidates.json').read_bytes()).hexdigest(),
                         'bar-tasks.json': hashlib.sha256((ROOT / 'bar-tasks.json').read_bytes()).hexdigest(),
                         'registry.json': hashlib.sha256((ORB / 'registry.json').read_bytes()).hexdigest(),
                         'prior_news_reviews': hashlib.sha256((QUOTE / 'ep-followup.json').read_bytes()).hexdigest()},
        'news_fetch': {'endpoint': 'https://data.alpaca.markets/v1beta1/news', 'symbols': 'one exact candidate symbol per window',
            'window': 'prior calendar regular close through current regular open; API bounds inclusive, analyzer stricter',
            'limit': 50, 'max_pages_per_window': 40, 'include_content': False, 'sort': 'asc',
            'retry_budget': 5, 'rate_interval_seconds': .45, 'workers': 4,
            'partial_failure': 'Retain checkpoint and mark retrieval_incomplete; do not claim absence or coverage.'},
        'article_rules': ['Timestamp parsing uses exact integer nanoseconds and explicit timezone; never truncate a future submicrosecond article into the decision time.',
            'Exact symbol must be in article symbols; created strictly after previousclose and strictly before open.',
            'updated must be present, valid, >=created and strictly before open. Missing/malformed or later versions cannot certify contemporaneous metadata.',
            'Group identical newsID records across all windows; identical payload duplicates collapse. Any conflicting payload versions under one ID are quarantined, never cherry-pick an earlier favorable headline.',
            'Time-clean aggregator metadata is only evidence_ready_for_review, not independently verified catalyst or historical version archive.',
            'No articles means no supplied news hit, not no market event. Keyword hints never establish earnings/guidance/contract/regulatory announcement proof.'],
        'market_rules': ['Evaluate original first30volume and actualregularopening gap for every candidate irrespective news hit.',
            'Require complete valid09:30..09:59 bars, exact-price gap>=10pct, first30volume>=prior20meanfullsessionvolume; never fill missing bars with zero.',
            'First completed-minute close above first30high during10:00..10:59 with one full minute latency: m+2 entry reference. Missing preceding minute is unknown; future gap afterfirstsignal never changes it.',
            'Entry at/below known OR30low is ambiguous gap-through exposure, not a favorable stop fill or retroactive cancellation.',
            'No exits, PnL, dailyresetwealth, compounding, fundedrestarts, or10000target estimate in this stage.'],
        'primary_review_queue': 'After full market gates, inspect earliest chronological signal candidates with time-clean aggregator evidence, excluding already-reviewed duplicates; initially first5cases with bounded primarysource attempts. Keep every deferred case.',
        'publication': 'All candidates, retrieval failures, article-derived time/ID/version flags, sourceURLs/hashes and gates; raw articles/headline corpus and full rawmarket files remain private. Do not publish rawfullfeed exports.',
        'limitations': ['Current universe, non-PIT classifications, priorcorporate-action identity ambiguity and defaultasofaliases remain.',
            'Headline/news URL associations are not causal proof and current payload metadata is not a complete revision archive.',
            'Market gates and quote availability do not establish executable trades.'],
        'broker_orders_sent': 0, 'new_scheduler_deployed': False}
    dump('study-design.json', design)
    print(json.dumps({'candidates': len(candidates), 'unique_symbols': len({r['symbol'] for r in candidates}),
        'dates': len(by_day), 'bar_request_chunks': len(tasks), 'news_request_windows': len(candidates),
        'registered_at': design['registered_at']}, indent=2))


if __name__ == '__main__':
    main()
