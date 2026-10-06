"""Independent offline source audit; does not import a collector or run returns."""
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib
import json
import re
import time

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parent
NEWS = BASE / 's500-news-20261006'
OLD = BASE / 's500-ep-paths-20261006'
NY = ZoneInfo('America/New_York')
FIELDS = ('t', 'o', 'h', 'l', 'c', 'v', 'n', 'vw')


def read(path):
    return json.loads(path.read_bytes())


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def check(value, label):
    if not value:
        raise AssertionError(label)


def scan(folder, manifest_name, assembled_name, calendar, full, expected_tasks=None):
    manifest = read(folder / manifest_name)
    check(manifest['all_pages_complete'] is True and not manifest['failures'], 'manifest failure ' + manifest_name)
    assembled = None if assembled_name is None else read(folder / assembled_name)
    assembled_pairs = set() if assembled is None else {(d, s) for d, syms in assembled.items() for s in syms}
    record_ids = [r['task_id'] for r in manifest['records']]
    check(len(record_ids) == len(set(record_ids)), 'duplicate request IDs')
    if expected_tasks is not None:
        check(set(record_ids) == set(expected_tasks), 'new requests differ from frozen task set')
    sessions = {}
    prefixes = {}
    pages = total_bars = total_bytes = 0
    for record in manifest['records']:
        params = record['parameters']
        check(record['route'] == '/v2/stocks/bars', 'unexpected route')
        check(set(params) == {'symbols', 'start', 'end', 'sort', 'limit', 'timeframe', 'adjustment', 'feed'}, 'unexpected parameter keys')
        check((params['sort'], params['limit'], params['timeframe'], params['adjustment'], params['feed']) == ('asc', 10000, '1Min', 'raw', 'sip'), 'parameter contract')
        lo = datetime.fromisoformat(params['start'])
        hi = datetime.fromisoformat(params['end'])
        check(lo.tzinfo is not None and hi.tzinfo is not None, 'naive request timestamp')
        day = lo.astimezone(NY).date().isoformat()
        cal = calendar[day]
        start = datetime.fromisoformat(day + 'T' + cal['open']).replace(tzinfo=NY)
        close = datetime.fromisoformat(day + 'T' + cal['close']).replace(tzinfo=NY)
        check(lo == start and hi == (close - timedelta(minutes=1) if full else start + timedelta(minutes=92)), 'request window')
        if 'date' in record:
            check(record['date'] == day, 'record date mismatch')
        symbols = params['symbols'].split(',')
        check(len(symbols) == len(set(symbols)) and all(symbols), 'duplicate/empty requested symbol')
        if expected_tasks is not None:
            task = expected_tasks[record['task_id']]
            check(task['date'] == day and task['symbols'] == symbols, 'request not bound to planned symbols')
            check(task['start'] == cal['open'] and task['end'] == hi.astimezone(NY).strftime('%H:%M'), 'planned request bounds differ')
        request = dict(params)
        base_hash = canonical({'route': record['route'], 'parameters': params})
        check(record['request_sha256'] == base_hash, 'base request hash')
        check(record['pages_complete'] is True and len(record['pages']) > 0, 'empty or incomplete page chain')
        result = {s: {} for s in symbols}
        last_offset = {}
        seen_tokens = set()
        count_record = 0
        for index, page in enumerate(record['pages']):
            file = folder / 'raw-bars' / record['task_id'] / page['name']
            payload = file.read_bytes()
            check(len(payload) == page['bytes'] and hashlib.sha256(payload).hexdigest() == page['sha256'], 'page byte/hash mismatch ' + str(file))
            saved = json.loads(payload)
            check(saved['base_request_sha256'] == base_hash and saved['page_number'] == index, 'stored page base/number')
            check(saved['page_request_sha256'] == canonical({'route': record['route'], 'parameters': request}), 'page token request chain')
            response = saved['response']
            check(set(response['bars']) <= set(symbols), 'unexpected returned symbol')
            count_page = 0
            for symbol, bars in response['bars'].items():
                for bar in bars:
                    check(re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00(?:\.0{1,9})?(?:Z|[+-]\d{2}:\d{2})', bar['t']) is not None, 'non-minute timestamp')
                    stamp = datetime.fromisoformat(bar['t'].replace('Z', '+00:00'))
                    check(lo <= stamp <= hi, 'returned timestamp outside request')
                    elapsed = (stamp - start).total_seconds()
                    check(elapsed % 60 == 0, 'non-integral offset')
                    offset = int(elapsed / 60)
                    check(offset > last_offset.get(symbol, -1), 'out-of-order or duplicate symbol-minute')
                    last_offset[symbol] = offset
                    result[symbol][str(offset)] = bar
                    count_page += 1
            check(count_page == page['records'], 'page record count')
            token = response['next_page_token']
            if index == len(record['pages']) - 1:
                check(token is None, 'nonterminal last page')
            else:
                check(isinstance(token, str) and token and token not in seen_tokens, 'invalid/repeated continuation token')
                seen_tokens.add(token)
                request['page_token'] = token
            pages += 1
            total_bytes += len(payload)
            count_record += count_page
        total_bars += count_record
        for symbol, series in result.items():
            key = (day, symbol)
            check(key not in sessions, 'overlapping request scope')
            if assembled is not None:
                check(assembled.get(day, {}).get(symbol, {}) == series, 'raw/assembled mismatch ' + str(key))
            sessions[key] = {'canonical_sha256': canonical(series), 'bars': len(series), 'request_id': record['task_id'],
                             'scheduled_minutes': int((close - start).total_seconds() / 60)}
            if not full:
                prefixes[key] = series
    check(assembled_pairs <= set(sessions), 'assembled data outside verified request scope')
    check(total_bars == manifest['data_records'] and total_bytes == manifest['raw_bytes'], 'manifest aggregate counts')
    if 'request_tasks' in manifest:
        check(manifest['request_tasks'] == len(record_ids), 'manifest request count')
    return sessions, prefixes, {'requests': len(record_ids), 'pages': pages, 'bars': total_bars, 'bytes': total_bytes,
                              'symbol_sessions': len(sessions), 'manifest_sha256': digest(folder / manifest_name)}


def main():
    began = time.monotonic()
    design = read(ROOT / 'study-design.json')
    plan = read(ROOT / 'holding-input-design.json')
    gate = read(ROOT / 'market-gates.json')
    calendar = {s['date']: s for s in plan['full_calendar']}
    dates = [s['date'] for s in plan['full_calendar']]
    check(dates == sorted(set(dates)), 'calendar order/uniqueness')
    original = read(NEWS / 'candidates.json')['candidates']
    original_ids = {c['candidate_id'] for c in original}
    check(len(original) == len(original_ids) == 1079, 'original registry')
    family_counts = {}
    ready = {}
    for family, value in gate['families'].items():
        check(len(value['rows']) == 1079 and {c['candidate_id'] for c in value['rows']} == original_ids, 'family full registry')
        family_counts[family] = sum(c['market_gate_pass'] is True for c in value['rows'])
        for c in value['rows']:
            if c['market_gate_pass'] is True:
                ready[c['candidate_id']] = c
    check(family_counts == {'OR5': 270, 'OR15': 137, 'OR30': 73}, 'frozen family count')
    cases = {c['candidate_id']: c for c in plan['cases']}
    check(len(plan['cases']) == len(cases) == 275 and set(cases) == set(ready), 'all ready union')
    wanted = set()
    case_pairs = 0
    censored = 0
    for cid, c in ready.items():
        i = dates.index(c['date'])
        window = [d for d in dates[i:i+10] if d <= design['period'][1]]
        check(cases[cid]['session_dates'] == window, 'first ten sessions ' + cid)
        check(cases[cid]['symbol'] == c['symbol'] and cases[cid]['entry_date'] == c['date'], 'case identity')
        wanted.update((d, c['symbol']) for d in window)
        case_pairs += len(window)
        censored += len(window) < 10
    bindings = {(b['date'], b['symbol']): b for b in plan['required_symbol_sessions']}
    check(len(bindings) == len(plan['required_symbol_sessions']) == 2584 and set(bindings) == wanted, 'exact required bindings')
    tasks = {t['task_id']: t for t in plan['tasks']}
    check(len(tasks) == len(plan['tasks']) == 205, 'task uniqueness/count')
    parent_roots = {'news': NEWS, 'paths': OLD, 'event_v2': BASE / 's500-ep-event-v2-20261006'}
    for name, expected in design['input_sha256'].items():
        # Only hash prior outcome files listed in the design; never parse their contents.
        group, relative = name.split('/', 1)
        check(digest(parent_roots[group] / relative) == expected, 'frozen parent hash ' + name)
    for name in ('study-design.json', 'market-gates.json', 'prepare_inputs.py', 'collect_inputs.py'):
        check(digest(ROOT / name) == plan['input_sha256'][name], 'plan binding ' + name)
    print('Frozen family registry, 275-case windows and plan bindings verified.', flush=True)
    prefix_meta, prefixes, prefix_audit = scan(NEWS, 'bars-input-manifest.json', 'minute-market.json', calendar, False)
    check(set(prefix_meta) == {(c['date'], c['symbol']) for c in original}, 'all original prefixes')
    print('All original prefix raw pages verified.', flush=True)
    old_meta, _, old_audit = scan(OLD, 'holding-input-manifest.json', 'holding-market.json', calendar, True)
    reusable = wanted & set(old_meta)
    check(len(reusable) == 703, 'verified old reuse count')
    check({k for k, b in bindings.items() if b['source'] == 'parent_ep_paths'} == reusable, 'old reuse exact scopes')
    for k in reusable:
        check(bindings[k]['request_id'] == old_meta[k]['request_id'], 'reused request identity')
    print('All parent full-session raw pages and assembly verified.', flush=True)
    new_meta, _, new_audit = scan(ROOT, 'holding-input-manifest.json', None, calendar, True, tasks)
    check(set(new_meta) == wanted - reusable and len(new_meta) == 1881, 'new exact scope complement')
    for k, meta in new_meta.items():
        check(bindings[k]['source'] == 'new_full_session' and bindings[k]['request_id'] == meta['request_id'], 'new request binding')
    print('All new raw pages, bytes and token chains verified.', flush=True)
    market = read(ROOT / 'holding-market.json')
    verified = read(ROOT / 'verified-symbol-sessions.json')['days']
    check({(d, s) for d, syms in market.items() for s in syms} == wanted, 'combined assembly scope')
    check({(d, s) for d, syms in verified.items() for s in syms} == wanted, 'verified metadata scope')
    differences = {}
    overlap_sessions = overlap_offsets = 0
    for key in sorted(wanted):
        day, symbol = key
        expected = old_meta[key] if key in reusable else new_meta[key]
        series = market[day][symbol]
        check(canonical(series) == expected['canonical_sha256'], 'mixed/replaced session source ' + str(key))
        meta = verified[day][symbol]
        check(meta['request_complete'] is True and meta['regular_window_covered'] is True, 'verified flags')
        check(meta['request_ids'] == [expected['request_id']] and meta['bars_returned'] == expected['bars'], 'verified metadata provenance')
        check(meta['scheduled_minutes'] == expected['scheduled_minutes'] and meta['source_dataset'] == bindings[key]['source'], 'verified session/source metadata')
        if key not in prefixes:
            continue
        overlap_sessions += 1
        prefix = prefixes[key]
        offsets = {int(k) for k in prefix} | {int(k) for k in series if 0 <= int(k) <= 92}
        bad = []
        for off in sorted(offsets):
            overlap_offsets += 1
            a, b = prefix.get(str(off)), series.get(str(off))
            if a is None or b is None:
                if a != b:
                    bad.append(off)
            elif any((field in a) != (field in b) or a.get(field) != b.get(field) for field in FIELDS):
                bad.append(off)
        if bad:
            differences[key] = bad
    conflict_document = read(ROOT / 'input-conflicts.json')
    check(overlap_sessions == conflict_document['overlap_sessions'] and overlap_offsets == conflict_document['overlap_offsets'], 'prefix overlap counts')
    reported = {(c['date'], c['symbol']): [x['offset'] for x in c['differences']] for c in conflict_document['symbol_session_conflicts']}
    check(reported == differences, 'independent prefix conflicts')
    flags = {}
    for cid, c in cases.items():
        bad = {(d, c['symbol']) for d in c['session_dates']} & set(differences)
        if bad:
            flags[cid] = {'source_revision_conflict': True, 'source_input_incomplete': False,
                          'conflict_ids': [d+'__'+s for d, s in sorted(bad)], 'incomplete_sessions': []}
    check(conflict_document['candidate_flags'] == flags, 'whole-planned-window quarantine flags')
    source_audit = read(ROOT / 'source-verification.json')
    check(source_audit['required_symbol_sessions'] == source_audit['verified_symbol_sessions'] == 2584, 'source audit scope counts')
    check(source_audit['reused_parent_symbol_sessions'] == 703 and source_audit['new_symbol_sessions'] == 1881, 'source audit reuse counts')
    check(source_audit['all_required_request_scopes_verified'] is True and source_audit['missing_symbol_sessions'] == [], 'coverage claim')
    for name, expected in source_audit['output_sha256'].items():
        check(digest(ROOT / name) == expected, 'source output hash ' + name)
    check(source_audit['input_sha256']['holding-input-design.json'] == digest(ROOT / 'holding-input-design.json'), 'source plan hash')
    output = {'status': 'passed', 'completed_at': datetime.now(timezone.utc).isoformat(),
              'audit_scope': 'Independent offline raw-page integrity, request chaining, complete-session source assembly, frozen union and source-conflict classification; no strategy simulation.',
              'independently_reconstructed': {'family_ready_counts': family_counts, 'unique_ready_cases': 275,
                  'unique_symbols': len({c['symbol'] for c in ready.values()}), 'case_session_pairs': case_pairs,
                  'required_symbol_sessions': 2584, 'old_verified_reuse': 703, 'new_verified_sessions': 1881,
                  'new_tasks': 205, 'cutoff_censored_case_windows': censored, 'isolated_results_expected': 3840,
                  'portfolio_results_expected': 72, 'total_results_expected': 3912},
              'raw_audits': {'prefix': prefix_audit, 'parent_holding': old_audit, 'new_holding': new_audit},
              'prefix_comparison': {'sessions': overlap_sessions, 'offsets': overlap_offsets, 'conflicting_sessions': len(differences), 'affected_candidates': len(flags)},
              'all_page_sha256_bytes_record_counts_verified': True, 'all_base_and_page_request_hash_chains_verified': True,
              'all_terminal_tokens_null': True, 'all_assembled_sessions_match_one_immutable_raw_source': True,
              'all_source_output_hashes_verified': True, 'whole_planned_ten_session_revision_quarantine': True,
              'limitations': ['Source-version quarantine examines the whole predeclared ten-session window and can conservatively block an entry due to a future session the position would not use; this is retrospective input quarantine, not contemporaneous conflict detection.',
                             'A missing requested session should fail only when the simulated path needs it; no such missing requests are present in this input archive.',
                             'Provider request exhaustion does not prove historical archive completeness, contemporaneous data receipt, identity correctness, or executable prices.',
                             'Daily/action outputs are hash-bound here; their full extraction semantics are outside this raw-minute audit.'],
              'network_requests_by_audit': 0, 'strategy_simulations_run': 0, 'outcome_files_parsed': 0,
              'code_sha256': digest(Path(__file__)),
              'input_sha256': {name: digest(ROOT / name) for name in ['study-design.json', 'market-gates.json', 'holding-input-design.json', 'collect_inputs.py', 'prepare_inputs.py', 'source-verification.json', 'input-conflicts.json', 'verified-symbol-sessions.json']},
              'elapsed_seconds': round(time.monotonic() - began, 3)}
    (ROOT / 'independent-new-source-audit.json').write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps({k: output[k] for k in ['status', 'independently_reconstructed', 'raw_audits', 'prefix_comparison', 'elapsed_seconds']}, indent=2), flush=True)


if __name__ == '__main__':
    main()
