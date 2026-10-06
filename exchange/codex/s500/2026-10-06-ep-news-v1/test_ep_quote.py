"""Offline EP-specific risk, budget and inherited as-of validation tests."""
import unittest
from decimal import Decimal
import ep_quote as ep


class EPQuoteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _, cls.prior = ep.load_prior()
        cls.conditions = {'A': {'R': 'Regular Market Maker Open'},
                          'C': {'R': 'Regular Two Sided Open'}}

    def quote(self, **changes):
        row = {'t': '2026-01-02T15:41:00Z', 'bp': 9.98, 'ap': 10,
               'bs': 1, 'as': 1, 'z': 'A', 'c': ['R']}
        row.update(changes)
        return row

    def point(self, **changes):
        row = {'known_or30_low_exact': '9', 'original_minute_entry_open_exact': '10',
               'minute_reference_gap_through_stop': False}
        row.update(changes)
        return row

    def test_or30_distance_not_orb_atr(self):
        result = ep.metrics(self.quote(bp=10, ap=10.5), self.point(known_or30_low_exact='8.5'))
        self.assertEqual(result['ask_minus_or30_low'], 2)
        self.assertEqual(result['spread_to_ask_minus_or30_low'], .25)
        self.assertNotIn('prior_ATR14', result)

    def test_equal_stop_has_no_ratio_and_no_trigger_claim(self):
        result = ep.metrics(self.quote(bp=9.99), self.point(known_or30_low_exact='10'))
        self.assertIsNone(result['spread_to_ask_minus_or30_low'])
        self.assertIsNone(result['spread_at_least_positive_risk_distance'])
        self.assertTrue(result['ask_at_or_below_known_stop'])
        self.assertTrue(result['bid_at_or_below_known_stop'])
        self.assertFalse(result['stop_trigger_or_fill_claim'])

    def test_negative_risk_retained(self):
        result = ep.metrics(self.quote(), self.point(known_or30_low_exact='11'))
        self.assertEqual(result['ask_minus_or30_low'], -1)
        self.assertIsNone(result['spread_to_ask_minus_or30_low'])
        self.assertEqual(len(result['budget_only']), 2)

    def test_bid_below_stop_and_positive_ask_distance(self):
        result = ep.metrics(self.quote(bp=9, ap=10), self.point(known_or30_low_exact='9.5'))
        self.assertTrue(result['bid_at_or_below_known_stop'])
        self.assertFalse(result['ask_at_or_below_known_stop'])
        self.assertEqual(result['spread_to_ask_minus_or30_low'], 2)

    def test_decimal_budget_boundary(self):
        result = ep.metrics(self.quote(bp=.09, ap=.1), self.point(known_or30_low_exact='.08'))
        first, second = result['budget_only']
        self.assertEqual(first['whole_shares_at_displayed_ask'], 2500)
        self.assertEqual(second['whole_shares_at_displayed_ask'], 5000)
        self.assertEqual(first['cash_remainder'], 0)
        self.assertEqual(first['hypothetical_ask_notional'], 250)

    def test_exact_budget_affordable_but_small_excess_not(self):
        exact = ep.metrics(self.quote(bp=249, ap=250), self.point())['budget_only'][0]
        excess = ep.metrics(self.quote(bp=249, ap=250.0000001), self.point())['budget_only'][0]
        self.assertEqual(exact['whole_shares_at_displayed_ask'], 1)
        self.assertEqual(excess['whole_shares_at_displayed_ask'], 0)
        self.assertTrue(excess['unaffordable_at_displayed_ask'])

    def test_unaffordable_retained_both_budgets(self):
        result = ep.metrics(self.quote(bp=599, ap=600), self.point())
        self.assertTrue(all(r['unaffordable_at_displayed_ask'] for r in result['budget_only']))
        self.assertTrue(all(r['cash_remainder'] == r['budget'] for r in result['budget_only']))

    def test_locked_quote_allowed_but_capacity_unverified(self):
        result = ep.metrics(self.quote(bp=10), self.point())
        self.assertEqual(result['spread_to_ask_minus_or30_low'], 0)
        self.assertTrue(result['locked_quote'])
        self.assertFalse(result['execution_capacity_verified'])

    def test_newest_invalid_no_older_fallback(self):
        rows = [self.quote(t='2026-01-02T15:40:59.900Z'), self.quote(ap=0)]
        result = self.prior.evaluate_latest(rows, '2026-01-02T15:41:00Z', 1, self.conditions)
        self.assertFalse(result['accepted'])
        self.assertEqual(result['latest_quote']['ap'], 0)

    def test_future_one_nanosecond_ignored(self):
        rows = [self.quote(t='2026-01-02T15:40:59.999999999Z'),
                self.quote(t='2026-01-02T15:41:00.000000001Z', ap=0)]
        result = self.prior.evaluate_latest(rows, '2026-01-02T15:41:00Z', 1, self.conditions)
        self.assertTrue(result['accepted'])
        self.assertEqual(result['future_rows_ignored'], 1)
        self.assertEqual(result['api_reported_age_ns'], 1)

    def test_tied_conflicting_latest_rejected(self):
        result = self.prior.evaluate_latest([self.quote(), self.quote(ap=10.01)],
            '2026-01-02T15:41:00Z', 5, self.conditions)
        self.assertEqual(result['reason'], 'ambiguous_latest_timestamp')
        self.assertFalse(result['accepted'])

    def test_primary_stale_sensitivity_accepted(self):
        rows = [self.quote(t='2026-01-02T15:40:58Z')]
        self.assertFalse(self.prior.evaluate_latest(rows, '2026-01-02T15:41:00Z', 1, self.conditions)['accepted'])
        self.assertTrue(self.prior.evaluate_latest(rows, '2026-01-02T15:41:00Z', 5, self.conditions)['accepted'])

    def test_empty_retained(self):
        result = self.prior.evaluate_latest([], '2026-01-02T15:41:00Z', 1, self.conditions)
        self.assertEqual(result['reason'], 'no_quote_in_requested_lookback')

    def test_nonfinite_and_invalid_prices_rejected(self):
        for stop in ('NaN', '-1', 'Infinity', True):
            with self.subTest(stop=stop), self.assertRaises(ValueError):
                ep.metrics(self.quote(), self.point(known_or30_low_exact=stop))


if __name__ == '__main__':
    unittest.main()
