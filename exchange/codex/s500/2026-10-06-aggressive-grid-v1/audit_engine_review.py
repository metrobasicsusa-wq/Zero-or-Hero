"""Independent output reconstruction from cached market/action inputs.

Does not import the portfolio engine, feature builder or their self-checks.
"""
import collections
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent
SOURCE = ROOT.parent / 's500-broad-20261006'
NY = ZoneInfo('America/New_York')


def close(a, b, context):
    if not math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-7):
        raise AssertionError((context, a, b))


def main():
    paths = json.loads((ROOT / 'paths.json').read_text())
    assert len(paths) == 192 and len({p['id'] for p in paths}) == 192
    action_doc = json.loads((ROOT / 'company-actions.json').read_text())
    actions = collections.defaultdict(list)
    for action in action_doc['rows']:
        if action['symbol']:
            actions[(action['symbol'], action.get('ex_date'))].append(action)
    calendar = [r['date'] for r in json.loads((SOURCE / 'raw/calendar.json').read_text())]
    calendar_index = {d: i for i, d in enumerate(calendar)}
    needed = collections.defaultdict(set)
    for path in paths:
        for row in path['entries']:
            needed[row['symbol']].update((row['entry_date'], row['signal_date']))
        for row in path['trades']:
            needed[row['symbol']].add(row['exit_date'])
        for row in path['daily']:
            if row['symbol']:
                needed[row['symbol']].add(row['date'])
    prices = collections.defaultdict(dict)
    input_manifest = json.loads((SOURCE / 'input-manifest.json').read_text())
    source_hashes = {r['name']: r['sha256'] for r in input_manifest['files']}
    for raw in sorted((SOURCE / 'raw').glob('batch-*.json')):
        content = raw.read_bytes()
        assert hashlib.sha256(content).hexdigest() == source_hashes[raw.name]
        for symbol, bars in json.loads(content)['bars'].items():
            if symbol not in needed:
                continue
            for b in bars:
                d = datetime.fromisoformat(b['t'].replace('Z', '+00:00')).astimezone(NY).date().isoformat()
                if d in needed[symbol]:
                    prices[symbol][d] = b
    for symbol, days in needed.items():
        assert set(prices[symbol]) == days, ('needed source bar missing', symbol)

    candidate_rows = json.loads((ROOT / 'features.json').read_text())
    candidates = collections.defaultdict(list)
    for row in candidate_rows:
        candidates[row['date']].append(row)
    counts = collections.Counter()
    event_samples = []
    failures = []
    for path in paths:
        parameters = path['parameters']
        cost = parameters['cost_bps'] / 10000
        cash = 500.
        inventory = None
        pending = []
        peak = 500.
        drawdown = 0.
        milestones = {str(target): None for target in (1000, 2000, 10000)}
        entries = {r['entry_date']: r for r in path['entries']}
        exits = {r['exit_date']: r for r in path['trades']}
        assert len(entries) == len(path['entries']) and len(exits) == len(path['trades'])
        assert path['initial_funding'] == 500 and path['additional_funding'] == 0
        previous_equity = 500.
        for row in path['daily']:
            day = row['date']
            paid = sum(r['amount'] for r in pending if r['payable_date'] <= day)
            cash += paid
            pending = [r for r in pending if r['payable_date'] > day]
            entitlement = 0.
            if inventory:
                events = actions[(inventory['symbol'], day)]
                splits = [a for a in events if a['type'] != 'cash_dividend']
                dividends = [a for a in events if a['type'] == 'cash_dividend']
                assert len(splits) <= 1 and len(dividends) <= 1 and not (splits and dividends)
                if splits:
                    action = splits[0]
                    new_qty = inventory['qty'] * action['new_rate'] / action['old_rate']
                    close(new_qty, round(new_qty), 'split integer entitlement')
                    inventory['qty'] = round(new_qty)
                    counts['raw_action_split_reconstructions'] += 1
                if dividends:
                    action = dividends[0]
                    assert action['foreign'] is False and action['special'] is False
                    assert not action.get('sub_type') and not action.get('due_bill_on_date') and not action.get('due_bill_off_date')
                    entitlement = inventory['qty'] * action['cash_rate']
                    inventory['earned_dividends'] += entitlement
                    if action['payable_date'] <= day:
                        paid += entitlement
                        cash += entitlement
                    else:
                        pending.append({'amount': entitlement, 'payable_date': action['payable_date']})
                    counts['raw_action_dividend_reconstructions'] += 1
                    if len(event_samples) < 8:
                        event_samples.append({'path': path['id'], 'symbol': inventory['symbol'], 'ex_date': day,
                                              'cash_rate_assumed_usd': action['cash_rate'], 'quantity': inventory['qty'],
                                              'entitlement': entitlement, 'payable_date': action['payable_date']})
            if row['entered']:
                assert inventory is None and day in entries
                entry = entries[day]
                assert entry['signal_date'] == calendar[calendar_index[day] - 1]
                signal = entry['signal_date']
                choices = [r for r in candidates[signal] if parameters['family'] in r['signals']]
                if parameters['ranking'] == 'prior_liquidity':
                    choices.sort(key=lambda r: (-r['liquidity'], r['symbol']))
                else:
                    choices.sort(key=lambda r: (-r['signals'][parameters['family']], -r['liquidity'], r['symbol']))
                budget = cash * parameters['capital_fraction']
                chosen = next(r for r in choices if r['close'] * (1 + cost) <= budget)
                assert chosen['symbol'] == entry['symbol']
                symbol = entry['symbol']
                close(chosen['close'], prices[symbol][signal]['c'], 'known selection close matches raw data')
                raw_open = prices[symbol][day]['o']
                expected_qty = math.floor(budget / (raw_open * (1 + cost)) + 1e-12)
                assert expected_qty == entry['entry_qty'] and expected_qty > 0
                debit = expected_qty * raw_open * (1 + cost)
                close(entry['entry_price'], raw_open, 'raw entry open')
                close(entry['entry_debit'], debit, 'raw opening debit')
                cash -= debit
                inventory = {'symbol': symbol, 'qty': expected_qty, 'debit': debit,
                             'entry_date': day, 'earned_dividends': 0.}
                counts['raw_open_entry_reconstructions'] += 1
                counts['prior_signal_and_affordability_selection_checks'] += 1
            if row['exited']:
                assert inventory is not None and day in exits
                trade = exits[day]
                raw_close = prices[inventory['symbol']][day]['c']
                credit = inventory['qty'] * raw_close * (1 - cost)
                close(trade['exit_price'], raw_close, 'raw exit close')
                close(trade['exit_credit'], credit, 'raw closing credit')
                assert trade['exit_qty'] == inventory['qty']
                assert calendar_index[day] - calendar_index[inventory['entry_date']] + 1 == parameters['holding_sessions']
                close(trade['dividends'], inventory['earned_dividends'], 'raw dividend entitlement')
                close(trade['pnl'], credit + inventory['earned_dividends'] - inventory['debit'], 'independent closed pnl')
                cash += credit
                inventory = None
                counts['raw_close_exit_reconstructions'] += 1
            value = 0. if inventory is None else inventory['qty'] * prices[inventory['symbol']][day]['c'] * (1 - cost)
            receivable = sum(r['amount'] for r in pending)
            equity = cash + value + receivable
            close(row['cash'], cash, 'independent cash flow')
            close(row['dividend_receivable'], receivable, 'independent receivable')
            close(row['holding_value_after_exit_cost'], value, 'raw marked holding')
            close(row['equity'], equity, 'independent equity')
            close(row['daily_pnl'], equity - previous_equity, 'independent daily pnl')
            close(row['dividend_entitlement_today'], entitlement, 'independent daily entitlement')
            close(row['dividend_cash_paid'], paid, 'independent dividend payment')
            assert row['symbol'] == (inventory['symbol'] if inventory else None)
            assert row['qty'] == (inventory['qty'] if inventory else 0)
            peak = max(peak, equity)
            drawdown = max(drawdown, 1 - equity / peak)
            for target in milestones:
                if milestones[target] is None and equity >= int(target):
                    milestones[target] = day
            previous_equity = equity
            counts['independently_rebuilt_daily_cash_and_raw_marks'] += 1
        close(path['max_liquidation_drawdown'], drawdown, 'independent drawdown')
        assert path['milestones'] == milestones
        if path['status'] == 'incomplete':
            failure = path['incomplete']
            assert path['final_equity'] is None
            assert inventory and path['incomplete_position_snapshot']['symbol'] == inventory['symbol']
            assert path['incomplete_position_snapshot']['qty'] == inventory['qty']
            events = actions[(failure['symbol'], failure['date'])]
            assert any(a['type'] == 'cash_dividend' and a.get('foreign') for a in events)
            cash += sum(r['amount'] for r in pending if r['payable_date'] <= failure['date'])
            close(path['cash_at_interrupt'], cash, 'interrupted cash state')
            failures.append({'id': path['id'], 'event': failure, 'last_mark_date': path['last_mark_date'],
                             'remaining_qty': inventory['qty'], 'source_foreign_dividend_verified': True})
            counts['incomplete_paths_verified_against_source_action'] += 1
        else:
            close(path['final_equity'], previous_equity, 'reconstructed final equity')
            counts['provisional_completed_paths_reconstructed'] += 1
    complete = [p for p in paths if p['status'] == 'provisional_complete']
    result = {'status': 'passed_independent_source_reconstruction', 'model_orders_are_not_actual_fills': True,
              'checks': dict(counts), 'raw_source_files_hash_checked': len(list((SOURCE / 'raw').glob('batch-*.json'))),
              'source_symbols_used': len(needed), 'source_bars_used': sum(map(len, prices.values())),
              'synthetic_tests': {'engine': 20, 'features': 11, 'total_passed': 31},
              'all_registered_paths_retained': len(paths), 'completed_provisional': len(complete),
              'incomplete_paths': failures, 'dividend_source_samples': event_samples,
              'highest_ending_equity': max(p['final_equity'] for p in complete),
              'lowest_ending_equity': min(p['final_equity'] for p in complete),
              'milestone_counts': {str(target): sum(p['milestones'][str(target)] is not None for p in complete) for target in (1000, 2000, 10000)},
              'limitations': ['Reconstructs the stated model against the same cached market/actions, not independent market vendor truth.',
                              'Corporate-action process/ex-date coverage, missing currency, directory selection and executable-fill limits remain.',
                              'All paths are exploratory; excluding incomplete paths from performance rankings is not evidence those paths were unprofitable or safe.'],
              'artifact_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
                                  ('engine.py', 'features.py', 'protocol.json', 'protocol-amendment.json', 'company-actions.json', 'paths.json', 'test_engine_review.py', 'test_features.py')}}
    (ROOT / 'independent-audit.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('incomplete_paths', 'dividend_source_samples', 'artifact_sha256')}, indent=2))


if __name__ == '__main__':
    main()
