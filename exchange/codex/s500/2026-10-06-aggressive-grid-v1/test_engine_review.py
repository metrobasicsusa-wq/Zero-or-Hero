"""Independent adversarial tests of cash, causal decisions and corporate actions."""
import copy
import unittest

from engine import action_index, ordinary_dividend, run_path

DAYS = ['2026-01-02', '2026-01-05', '2026-01-06', '2026-01-07']
SIGNAL = '2025-12-31'


def bar(o=100, c=None):
    c = o if c is None else c
    return {'o': o, 'h': max(o, c) * 1.01, 'l': min(o, c) * .99, 'c': c, 'v': 1000000}


def candidate(symbol='A', date=SIGNAL, close=100, prior_close=None, liquidity=1e8, strength=.2):
    return {'symbol': symbol, 'date': date, 'close': close,
            'prior_close': close if prior_close is None else prior_close,
            'liquidity': liquidity, 'signals': {'momentum20': strength}}


def market_for(symbol='A', prices=None):
    prices = [100] * len(DAYS) if prices is None else prices
    return {symbol: {SIGNAL: bar(100), **{d: bar(p) for d, p in zip(DAYS, prices)}}}


def dividend(ex_date=DAYS[1], payable_date=DAYS[3], **extra):
    return {'type': 'cash_dividend', 'symbol': 'A', 'ex_date': ex_date,
            'payable_date': payable_date, 'cash_rate': 1., 'currency': None,
            'foreign': False, 'special': False, **extra}


def indexed(rows):
    return action_index({'rows': rows})


def run(market=None, dates=None, candidates=None, actions=None, hold=3, fraction=1., bps=0):
    return run_path(market_for() if market is None else market, DAYS if dates is None else dates,
                    [candidate()] if candidates is None else candidates,
                    {} if actions is None else actions,
                    'momentum20', 'prior_liquidity', hold, fraction, bps)


class IndependentEngineReviewTests(unittest.TestCase):
    def test_costs_cash_remainder_and_whole_share_identity(self):
        p = run(dates=DAYS[:1], hold=1, bps=10)
        trade = p['trades'][0]
        self.assertEqual(trade['entry_qty'], 4)
        self.assertAlmostEqual(trade['entry_debit'], 400.4)
        self.assertAlmostEqual(trade['exit_credit'], 399.6)
        self.assertAlmostEqual(p['final_equity'], 499.2)
        self.assertEqual(p['additional_funding'], 0)

    def test_half_allocation_reserves_cash(self):
        p = run(dates=DAYS[:1], hold=3, fraction=.5)
        self.assertEqual(p['daily'][0]['cash'], 300.)
        self.assertEqual(p['daily'][0]['qty'], 2)
        self.assertEqual(p['final_equity'], 500.)

    def test_signal_day_close_controls_affordability(self):
        m = market_for('A'); m.update(market_for('B'))
        candidates = [candidate('A', close=600, prior_close=100, liquidity=2e8), candidate('B', close=100)]
        p = run(market=m, candidates=candidates, dates=DAYS[:1], hold=1)
        self.assertEqual(p['entries'][0]['symbol'], 'B')

    def test_gap_unaffordable_does_not_switch_to_runner_up(self):
        m = market_for('A', [600, 600, 600, 600]); m.update(market_for('B'))
        p = run(market=m, candidates=[candidate('A', liquidity=2e8), candidate('B')], dates=DAYS[:1], hold=1)
        self.assertEqual(p['entries'], [])
        self.assertEqual(p['skips'][0]['symbol'], 'A')
        self.assertEqual(p['final_equity'], 500.)

    def test_current_close_cannot_change_entry_choice_or_share_count(self):
        first = market_for('A'); first.update(market_for('B'))
        second = copy.deepcopy(first)
        second['A'][DAYS[0]] = bar(100, 10000)
        second['B'][DAYS[0]] = bar(100, .01)
        picks = [candidate('A', liquidity=2e8), candidate('B')]
        a = run(market=first, candidates=picks, dates=DAYS[:1], hold=1)
        b = run(market=second, candidates=picks, dates=DAYS[:1], hold=1)
        self.assertEqual(a['entries'], b['entries'])
        self.assertNotEqual(a['final_equity'], b['final_equity'])

    def test_holding_period_and_exit_day_cannot_reenter(self):
        m = market_for('A'); m.update(market_for('B'))
        picks = [candidate('A'), candidate('B', date=DAYS[0]), candidate('B', date=DAYS[1])]
        p = run(market=m, candidates=picks, hold=2)
        self.assertEqual([r['entry_date'] for r in p['entries']], [DAYS[0], DAYS[2]])
        self.assertEqual(p['trades'][0]['exit_date'], DAYS[1])
        self.assertEqual(p['trades'][0]['holding_sessions'], 2)

    def test_marked_drawdown_includes_open_position(self):
        p = run(market=market_for(prices=[100, 50, 100, 100]), hold=3)
        self.assertEqual(p['final_equity'], 500.)
        self.assertEqual(p['max_liquidation_drawdown'], .5)

    def test_dividend_receivable_paid_after_exit_is_not_double_counted(self):
        p = run(market=market_for(prices=[100, 99, 99, 99]), actions=indexed([dividend()]), hold=3)
        self.assertEqual(p['daily'][1]['dividend_receivable'], 5.)
        self.assertEqual(p['daily'][2]['cash'], 495.)
        self.assertEqual(p['daily'][2]['dividend_receivable'], 5.)
        self.assertEqual(p['daily'][3]['cash'], 500.)
        self.assertEqual(p['daily'][3]['dividend_receivable'], 0.)
        self.assertEqual(p['trades'][0]['pnl'], 0.)
        self.assertEqual(p['final_equity'], 500.)

    def test_buying_on_ex_date_does_not_receive_dividend(self):
        p = run(actions=indexed([dividend(ex_date=DAYS[0], payable_date=DAYS[0])]), hold=1)
        self.assertEqual(sum(r['dividend_entitlement_today'] for r in p['daily']), 0.)
        self.assertEqual(p['final_equity'], 500.)

    def test_unpaid_dividend_at_cutoff_is_receivable_not_spendable_cash(self):
        p = run(market=market_for(prices=[100, 99, 99, 99]), dates=DAYS[:2],
                actions=indexed([dividend(payable_date='2026-02-02')]), hold=2)
        self.assertEqual(p['final_equity'], 500.)
        self.assertEqual(p['daily'][-1]['cash'], 495.)
        self.assertEqual(p['daily'][-1]['dividend_receivable'], 5.)
        self.assertEqual(p['closed_pnl'], 0.)

    def test_forward_split_changes_quantity_not_wealth(self):
        a = {'type': 'forward_split', 'symbol': 'A', 'ex_date': DAYS[1], 'old_rate': 1, 'new_rate': 2}
        p = run(market=market_for(prices=[100, 50, 55, 55]), actions=indexed([a]), hold=3)
        self.assertEqual(p['daily'][1]['qty'], 10)
        self.assertEqual(p['daily'][1]['equity'], 500.)
        self.assertEqual(p['trades'][0]['entry_qty'], 5)
        self.assertEqual(p['trades'][0]['exit_qty'], 10)
        self.assertEqual(p['trades'][0]['pnl'], 50.)
        self.assertEqual(p['final_equity'], 550.)

    def test_fractional_reverse_split_stops_instead_of_inventing_cash(self):
        a = {'type': 'reverse_split', 'symbol': 'A', 'ex_date': DAYS[1], 'old_rate': 2, 'new_rate': 1}
        p = run(actions=indexed([a]))
        self.assertIsNone(p['final_equity'])
        self.assertEqual(p['status'], 'incomplete')
        self.assertEqual(p['incomplete']['reason'], 'fractional_split_cash_in_lieu_unknown')
        self.assertEqual(p['last_mark_date'], DAYS[0])
        self.assertEqual(p['incomplete_position_snapshot']['qty'], 5)

    def test_missing_held_bar_keeps_failure_and_last_known_position(self):
        m = market_for(); del m['A'][DAYS[1]]
        p = run(market=m)
        self.assertIsNone(p['final_equity'])
        self.assertEqual(p['incomplete']['reason'], 'missing_or_invalid_held_mark_or_exit')
        self.assertEqual(p['last_mark_date'], DAYS[0])
        self.assertEqual(p['incomplete_position_snapshot']['qty'], 5)
        self.assertEqual(p['closed_trades'], 0)

    def test_unsupported_dividend_is_incomplete_not_ignored(self):
        for extra in ({'foreign': True}, {'special': True}, {'sub_type': 'return_of_capital'},
                      {'due_bill_on_date': DAYS[0]}, {'currency': 'EUR'}):
            with self.subTest(extra=extra):
                p = run(actions=indexed([dividend(**extra)]))
                self.assertIsNone(p['final_equity'])
                self.assertEqual(p['incomplete']['reason'], 'unsupported_dividend')

    def test_semantic_duplicate_dividend_cannot_double_credit(self):
        doc = {'rows': [dividend(), dividend()], 'semantic_duplicate_groups': [{'row_indices': [0, 1]}]}
        p = run(actions=action_index(doc))
        self.assertIsNone(p['final_equity'])
        self.assertIn('dividend', p['incomplete']['reason'])
        self.assertEqual(p['last_marked_equity'], 500.)

    def test_distinct_same_date_dividend_records_require_reconciliation(self):
        p = run(actions=indexed([dividend(cash_rate=1.), dividend(cash_rate=1.00001)]))
        self.assertIsNone(p['final_equity'])
        self.assertIn('dividend', p['incomplete']['reason'])

    def test_explicit_unmapped_new_symbol_is_not_an_ordinary_split(self):
        a = {'type': 'forward_split', 'symbol': 'A', 'ex_date': DAYS[1],
             'old_rate': 1, 'new_rate': 2, 'new_symbol': None,
             'new_symbol_mapping_status': 'unsupported_symbol_format_redacted'}
        p = run(actions=indexed([a]))
        self.assertIsNone(p['final_equity'])
        self.assertEqual(p['incomplete']['reason'], 'unresolved_split')

    def test_missing_foreign_or_special_field_is_not_verified_false(self):
        for field in ('foreign', 'special'):
            a = dividend(); del a[field]
            with self.subTest(field=field):
                self.assertFalse(ordinary_dividend(a))

    def test_terminal_position_is_censored_and_marked_with_exit_cost(self):
        p = run(dates=DAYS[:1], hold=10, bps=10)
        self.assertEqual(p['trades'], [])
        self.assertIsNotNone(p['open_position_censored'])
        self.assertAlmostEqual(p['final_equity'], 499.2)
        self.assertAlmostEqual(p['open_liquidation_pnl'], -.8)

    def test_future_action_cannot_change_earlier_marked_days(self):
        m = market_for(prices=[100, 110, 120, 120])
        a = {'type': 'forward_split', 'symbol': 'A', 'ex_date': '2027-01-05', 'old_rate': 1, 'new_rate': 100}
        before = run(market=m)
        after = run(market=m, actions=indexed([a]))
        self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
