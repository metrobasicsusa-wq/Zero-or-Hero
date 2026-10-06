"""GET-only corporate-action collection; sanitized derivative preserves ambiguities."""
import hashlib
import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT.parent / 's500-broad-20261006'))
from fetch_daily import get

PARAMS = {'start': '2026-01-01', 'end': '2026-10-05',
          'types': 'forward_split,reverse_split,cash_dividend', 'limit': 1000}
WARMUP_PARAMS = {'start': '2025-11-01', 'end': '2025-12-31',
                 'types': 'forward_split,reverse_split', 'limit': 1000}
KINDS = {'forward_splits': 'forward_split', 'reverse_splits': 'reverse_split',
         'cash_dividends': 'cash_dividend'}


def symbol_status(value):
    if not isinstance(value, str) or not value:
        return None, 'missing_symbol'
    if re.fullmatch(r'[A-Z0-9*@#]{8}[0-9]', value):
        return None, 'cusip_shaped_symbol_redacted'
    if not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,14}', value):
        return None, 'unsupported_symbol_format_redacted'
    return value, 'ticker_format_only_not_identity_proof'


def main():
    raw = ROOT / 'raw-actions'
    raw.mkdir(exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()
    params = dict(PARAMS)
    pages = []
    seen_tokens = set()
    source_rows = []
    fields = defaultdict(Counter)
    page_number = 0
    while True:
        page_number += 1
        path = raw / f'page-{page_number:04d}.json'
        if path.exists():
            response = json.loads(path.read_text())
        else:
            response = get('data.alpaca.markets', '/v1/corporate-actions', params)
            path.write_text(json.dumps(response, separators=(',', ':')))
        assert set(response) <= {'corporate_actions', 'next_page_token'}
        counts = {}
        for kind, entries in response['corporate_actions'].items():
            assert kind in KINDS and isinstance(entries, list)
            counts[kind] = len(entries)
            for row in entries:
                fields[kind].update(row.keys())
                source_rows.append((kind, row, page_number, 'evaluation_process_dates'))
        pages.append({'name': path.name, 'request_group': 'evaluation_process_dates', 'bytes': path.stat().st_size,
                      'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                      'counts': counts, 'has_next_page': bool(response.get('next_page_token'))})
        print(json.dumps({'page': page_number, 'source_rows': len(source_rows), 'counts': counts}), flush=True)
        token = response.get('next_page_token')
        if not token:
            break
        assert token not in seen_tokens, 'pagination token cycle'
        seen_tokens.add(token)
        params['page_token'] = token
        if page_number >= 500:
            raise RuntimeError('unexpected_page_limit_do_not_treat_as_complete')

    params = dict(WARMUP_PARAMS)
    seen_tokens = set()
    page_number = 0
    while True:
        page_number += 1
        path = raw / f'warmup-page-{page_number:04d}.json'
        if path.exists():
            response = json.loads(path.read_text())
        else:
            response = get('data.alpaca.markets', '/v1/corporate-actions', params)
            path.write_text(json.dumps(response, separators=(',', ':')))
        counts = {}
        for kind, entries in response['corporate_actions'].items():
            assert kind in ('forward_splits', 'reverse_splits')
            counts[kind] = len(entries)
            for row in entries:
                fields[kind].update(row.keys())
                source_rows.append((kind, row, page_number, 'warmup_process_dates'))
        pages.append({'name': path.name, 'request_group': 'warmup_process_dates', 'bytes': path.stat().st_size,
                      'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                      'counts': counts, 'has_next_page': bool(response.get('next_page_token'))})
        print(json.dumps({'warmup_page': page_number, 'source_rows': len(source_rows), 'counts': counts}), flush=True)
        token = response.get('next_page_token')
        if not token:
            break
        assert token not in seen_tokens, 'warmup pagination token cycle'
        seen_tokens.add(token)
        params['page_token'] = token
        if page_number >= 100:
            raise RuntimeError('unexpected_warmup_page_limit_do_not_treat_as_complete')

    universe = {x['symbol'] for x in json.loads((ROOT.parent / 's500-broad-20261006' / 'universe.json').read_text())}
    rows = []
    quality = Counter()
    raw_id_counts = Counter()
    semantic_groups = defaultdict(list)
    for index, (source_kind, source, page, request_group) in enumerate(source_rows):
        kind = KINDS[source_kind]
        symbol, mapping_status = symbol_status(source.get('symbol'))
        row = {'type': kind, 'symbol': symbol, 'mapping_status': mapping_status,
               'in_research_universe': symbol in universe if symbol else False,
               'source_request_group': request_group}
        if source.get('id'):
            raw_id_counts[source['id']] += 1
        for key in ('ex_date', 'record_date', 'payable_date', 'process_date', 'due_bill_redemption_date', 'due_bill_on_date', 'due_bill_off_date'):
            if key in source:
                row[key] = source[key]
        if 'new_symbol' in source:
            row['new_symbol'], row['new_symbol_mapping_status'] = symbol_status(source['new_symbol'])
        if kind in ('forward_split', 'reverse_split'):
            row['old_rate'] = source.get('old_rate')
            row['new_rate'] = source.get('new_rate')
            valid = all(isinstance(row[k], (int, float)) and math.isfinite(row[k]) and row[k] > 0 for k in ('old_rate', 'new_rate'))
            row['new_shares_per_old_share'] = row['new_rate'] / row['old_rate'] if valid else None
            if not valid:
                quality['invalid_split_rate_rows'] += 1
        else:
            row['cash_rate'] = source.get('rate')
            # The source response does not expose a currency field.
            row['currency'] = source.get('currency')
            row['currency_source_status'] = 'provided' if row['currency'] else 'not_provided_by_api'
            for key in ('foreign', 'special', 'sub_type'):
                if key in source:
                    row[key] = source[key]
            if not isinstance(row['cash_rate'], (int, float)) or not math.isfinite(row['cash_rate']) or row['cash_rate'] < 0:
                quality['invalid_cash_rate_rows'] += 1
            if any(row.get(key) for key in ('due_bill_on_date', 'due_bill_off_date')):
                quality['cash_dividend_due_bill_rows'] += 1
            if row.get('special'):
                quality['special_cash_dividend_rows'] += 1
            if row.get('foreign'):
                quality['foreign_cash_dividend_rows'] += 1
        ex_date = row.get('ex_date')
        request = WARMUP_PARAMS if request_group == 'warmup_process_dates' else PARAMS
        row['ex_date_in_requested_range'] = isinstance(ex_date, str) and request['start'] <= ex_date <= request['end']
        row['ex_date_in_evaluation_range'] = isinstance(ex_date, str) and PARAMS['start'] <= ex_date <= PARAMS['end']
        if not row['ex_date_in_requested_range']:
            quality['ex_date_outside_requested_range_or_missing'] += 1
        quality[mapping_status] += 1
        if row['in_research_universe']:
            quality['rows_in_research_universe'] += 1
        if row.get('new_symbol') and row['new_symbol'] != row['symbol']:
            quality['different_new_symbol_rows'] += 1
        # Only mapped tickers participate; redacted unknown instruments must not be merged.
        if symbol:
            semantic_groups[json.dumps(row, sort_keys=True)].append(index)
        rows.append(row)

    duplicate_groups = []
    for indices in semantic_groups.values():
        if len(indices) > 1:
            group = len(duplicate_groups) + 1
            duplicate_groups.append({'group': group, 'row_indices': indices, 'count': len(indices)})
            for index in indices:
                rows[index]['semantic_duplicate_group'] = group
    result = {'source': 'Alpaca Market Data corporate-actions v1', 'requests': [PARAMS, WARMUP_PARAMS],
              'evaluation_range': [PARAMS['start'], PARAMS['end']],
              'status': 'all_pages_retrieved_unverified_point_in_time_availability',
              'notes': ['All source rows are retained, including semantic duplicates and redacted mapping failures.',
                        'Corporate-action identifiers and CUSIPs are omitted; CUSIP-shaped symbol values are redacted.',
                        'Ticker formatting is not an identity mapping guarantee; current universe membership is only a diagnostic.',
                        'Dividend responses do not specify currency. Do not silently claim the API supplied USD.',
                        'Observed request filtering follows process dates, not necessarily ex-dates. Some dividends have ex-dates outside this range, and future-process dividends can be missing even if their ex-date is inside this range.',
                        'Special dividends, due-bill dates and subtype values are retained and require explicit entitlement treatment.',
                        'Actions retrieved after the evaluation window may contain revisions; announcement availability is not established.',
                        'Split quantity and cash-in-lieu treatment, ex-date entitlement and payable-date dividends require explicit simulator policy.'],
              'counts': dict(Counter(row['type'] for row in rows)), 'quality': dict(quality),
              'semantic_duplicate_groups': duplicate_groups, 'rows': rows}
    derived = ROOT / 'company-actions.json'
    derived.write_text(json.dumps(result, indent=2) + '\n')
    manifest = {'source': 'https://data.alpaca.markets/v1/corporate-actions', 'requests': [PARAMS, WARMUP_PARAMS],
                'started_at': started, 'finished_at': datetime.now(timezone.utc).isoformat(),
                'pagination_complete': True, 'pages': pages, 'source_row_count': len(source_rows),
                'fields_by_source_type': {kind: dict(counter) for kind, counter in fields.items()},
                'counts': result['counts'], 'quality': dict(quality),
                'duplicate_raw_id_count': sum(value > 1 for value in raw_id_counts.values()),
                'duplicate_raw_id_extra_rows': sum(value - 1 for value in raw_id_counts.values() if value > 1),
                'semantic_duplicate_group_count': len(duplicate_groups),
                'semantic_duplicate_extra_rows': sum(group['count'] - 1 for group in duplicate_groups),
                'derived_file': {'name': derived.name, 'bytes': derived.stat().st_size,
                                 'sha256': hashlib.sha256(derived.read_bytes()).hexdigest()}}
    (ROOT / 'action-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({k: v for k, v in manifest.items() if k not in ('pages', 'fields_by_source_type')}, indent=2))


if __name__ == '__main__':
    main()
