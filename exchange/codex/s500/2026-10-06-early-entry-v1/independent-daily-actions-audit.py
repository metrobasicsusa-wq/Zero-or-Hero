"""Independent offline extraction audit; no collector imports or return runs."""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib
import json
import time

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parent
NY = ZoneInfo('America/New_York')


def read(path):
    return json.loads(path.read_bytes())


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    began = time.monotonic()
    preserved_names = ['independent-new-source-audit.json', 'source-verification.json',
                       'cached-source-manifest.json', 'holding-input-design.json',
                       'daily-market-private.json', 'corporate-events.json']
    preserved = {name: sha(ROOT / name) for name in preserved_names}
    design = read(ROOT / 'study-design.json')
    gate = read(ROOT / 'market-gates.json')
    plan = read(ROOT / 'holding-input-design.json')
    cached = read(ROOT / 'cached-source-manifest.json')
    source_verified = read(ROOT / 'source-verification.json')
    candidate_file = BASE / 's500-news-20261006/candidates.json'
    inventory_file = BASE / 's500-orb-20261006/ep-candidate-inventory.json'
    require(sha(candidate_file) == design['input_sha256']['news/candidates.json'], 'frozen original candidate file')
    candidates = read(candidate_file)
    require(sha(inventory_file) == candidates['source_inventory_sha256'], 'original inventory source chain')
    inventory = read(inventory_file)
    ready = {c['candidate_id']: c for family in gate['families'].values()
             for c in family['rows'] if c['market_gate_pass'] is True}
    wanted = {c['symbol'] for c in ready.values()}
    require(len(ready) == 275 and len(wanted) == 243, 'independent ready union')
    require({c['candidate_id'] for c in plan['cases']} == set(ready), 'plan membership')
    require({c['symbol'] for c in plan['cases']} == wanted, 'plan symbol membership')
    require(sha(ROOT / 'market-gates.json') == plan['input_sha256']['market-gates.json'], 'frozen gate source')
    broad = BASE / 's500-broad-20261006'
    expected_batches = {k: v for k, v in inventory['inputs_sha256'].items()
                        if k.startswith('raw/batch-') and k.endswith('.json')}
    actual_batches = {'raw/' + p.name: p for p in (broad / 'raw').glob('batch-*.json')}
    require(len(expected_batches) == len(actual_batches) == 104 and set(expected_batches) == set(actual_batches), '104 exact frozen broad batch files')
    daily = {}
    all_source_symbols = set()
    source_bars = source_bytes = 0
    for key, path in sorted(actual_batches.items()):
        raw = path.read_bytes()
        require(hashlib.sha256(raw).hexdigest() == expected_batches[key], 'broad raw SHA mismatch ' + key)
        payload = json.loads(raw)
        source_bytes += len(raw)
        for symbol, bars in payload['bars'].items():
            require(symbol not in all_source_symbols, 'duplicate symbol across broad source batches')
            all_source_symbols.add(symbol)
            source_bars += len(bars)
            if symbol not in wanted:
                continue
            target = {}
            for bar in bars:
                stamp = datetime.fromisoformat(bar['t'].replace('Z', '+00:00'))
                require(stamp.tzinfo is not None, 'daily source has naive timestamp')
                day = stamp.astimezone(NY).date().isoformat()
                require(day not in target, 'duplicate daily symbol/date')
                target[day] = bar
            daily[symbol] = target
    require(len(all_source_symbols) == inventory['coverage']['universe_codes'], 'source universe count')
    require(source_bars == inventory['coverage']['raw_daily_bars'], 'source daily bar count')
    require(set(daily) == wanted, 'all 243 daily symbols available')
    require(daily == read(ROOT / 'daily-market-private.json'), 'independent daily extraction differs')
    selected_daily_bars = sum(len(bars) for bars in daily.values())
    require(cached['daily_raw_inputs_sha256'] == expected_batches, 'cached daily provenance hashes')
    require(cached['daily_symbols'] == cached['wanted_symbols'] == 243 and cached['missing_daily_symbols'] == [], 'cached daily coverage')
    require(cached['daily_bars'] == selected_daily_bars, 'cached selected daily count')
    print('All 104 pinned raw daily files and all 243-symbol daily rows independently reconstructed.', flush=True)
    action_file = BASE / 's500-aggressive-20261006/company-actions.json'
    action_hash = sha(action_file)
    require(action_hash == inventory['inputs_sha256']['company-actions.json'] == cached['parent_actions_sha256'], 'full corporate-action frozen source hash')
    actions = read(action_file)
    require(len(actions['rows']) == 41181, 'corporate source row count')
    by_symbol = {symbol: [] for symbol in wanted}
    unmapped = []
    relevant_source_indexes = set()
    for index, action in enumerate(actions['rows']):
        symbols = {s for s in (action.get('symbol'), action.get('new_symbol')) if s}
        if not symbols:
            unmapped.append({'source_row_index': index, 'type': action['type'], 'ex_date': action.get('ex_date')})
        for symbol in symbols & wanted:
            by_symbol[symbol].append(dict(action, source_row_index=index))
            relevant_source_indexes.add(index)
    actual_actions = read(ROOT / 'corporate-events.json')
    require(set(actual_actions) == wanted, 'all 243 corporate-action symbol keys including empty lists')
    require(actual_actions == by_symbol, 'independent symbol/new_symbol union extraction differs')
    require(cached['unmapped_actions_not_assigned_to_an_issuer'] == unmapped, 'unmapped action rows not retained exactly')
    require(cached['corporate_action_source_rows'] == 41181, 'cached full action count')
    require(cached['corporate_source_limitations'] == actions['notes'], 'source limitations omitted or altered')
    require(cached['not_a_point_in_time_or_complete_market_universe'] is True, 'non-PIT coverage caveat')
    for name in ['daily-market-private.json', 'corporate-events.json']:
        require(sha(ROOT / name) == cached[name] == source_verified['output_sha256'][name], 'daily/action output hash chain ' + name)
    require(sha(ROOT / 'cached-source-manifest.json') == source_verified['output_sha256']['cached-source-manifest.json'], 'cached manifest source-verification binding')
    require({name: sha(ROOT / name) for name in preserved_names} == preserved, 'prebound input/audit changed during review')
    selected_action_rows = [a for rows in by_symbol.values() for a in rows]
    out = {'status': 'passed', 'completed_at': datetime.now(timezone.utc).isoformat(),
           'scope': 'Independent reconstruction from pinned broad daily and full corporate-action raw sources; no collector import, network request or strategy simulation.',
           'ready_union_candidates': len(ready), 'wanted_symbols': len(wanted),
           'daily': {'raw_batch_files': 104, 'every_batch_sha256_verified_against_original_inventory': True,
                     'source_bytes': source_bytes, 'source_symbols': len(all_source_symbols), 'source_bars': source_bars,
                     'selected_symbols': len(daily), 'selected_bars': selected_daily_bars,
                     'missing_wanted_symbols': [], 'all_selected_bars_and_fields_match': True,
                     'raw_batch_sha256': expected_batches},
           'corporate_actions': {'full_source_rows': len(actions['rows']), 'full_source_sha256': action_hash,
                                 'selected_symbol_keys': len(by_symbol),
                                 'selected_symbol_row_instances': len(selected_action_rows),
                                 'selected_unique_source_rows': len(relevant_source_indexes),
                                 'selected_rows_by_type': dict(Counter(a.get('type', 'unknown') for a in selected_action_rows)),
                                 'symbols_with_empty_cached_action_list': sum(not rows for rows in by_symbol.values()),
                                 'all_symbol_and_new_symbol_union_rows_match': True,
                                 'unmapped_source_rows_preserved': len(unmapped),
                                 'unmapped_source_row_details_match': True,
                                 'selected_row_instances_missing_ex_date': sum(not a.get('ex_date') for a in selected_action_rows),
                                 'original_source_limitations_preserved': True,
                                 'cached_empty_list_proves_no_historical_action': False},
           'limitations': ['All 243 symbol keys are present and every cached source row assigned by symbol or new_symbol is retained. This does not establish historical corporate-action source completeness, point-in-time identity mapping or announcement-time availability.',
                          'The 19 unmapped full-source action rows remain unassigned unknowns; this audit does not infer which issuer they concern or certify no events from a cached empty list.',
                          'The strategy uses fixed10 rather than MA daily-close exits. Complete corporate-action extraction still matters because unsupported held events must remain unresolved.'],
           'previous_source_audit_sha256_unchanged': preserved['independent-new-source-audit.json'],
           'prebound_files_preserved': preserved, 'source_limitations': actions['notes'],
           'network_requests_by_audit': 0, 'strategy_simulations_run': 0, 'outcome_files_parsed': 0,
           'collector_imported': False, 'elapsed_seconds': round(time.monotonic() - began, 3),
           'code_sha256': sha(Path(__file__)),
           'input_sha256': {'original_candidates': sha(candidate_file), 'original_inventory': sha(inventory_file),
                            'study-design.json': sha(ROOT / 'study-design.json'),
                            'market-gates.json': sha(ROOT / 'market-gates.json'),
                            'holding-input-design.json': sha(ROOT / 'holding-input-design.json'),
                            'collect_inputs.py_reviewed_not_imported': sha(ROOT / 'collect_inputs.py'),
                            'cached-source-manifest.json': sha(ROOT / 'cached-source-manifest.json'),
                            'daily-market-private.json': sha(ROOT / 'daily-market-private.json'),
                            'corporate-events.json': sha(ROOT / 'corporate-events.json')}}
    output = ROOT / 'independent-daily-actions-audit.json'
    require(not output.exists(), 'do not overwrite earlier independent audit')
    output.write_text(json.dumps(out, indent=2) + '\n')
    print(json.dumps({'status': out['status'], 'daily_raw_batches': 104, 'daily_selected_symbols': len(daily),
                      'daily_selected_bars': selected_daily_bars, 'actions': out['corporate_actions'],
                      'elapsed_seconds': out['elapsed_seconds'], 'output_sha256': sha(output)}, indent=2), flush=True)


if __name__ == '__main__':
    main()
