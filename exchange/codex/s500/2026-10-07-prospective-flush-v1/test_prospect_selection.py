"""Synthetic selector checks: boundaries, temporal gates, denominator retention and ties."""
import copy
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
import random
import unittest

from prospect_selection import selection


DAY = "2026-10-08"
PRIORS = [(date(2026, 9, 10) + timedelta(days=i)).isoformat() for i in range(20)]


def daily_for(symbols, close="10", volume="2000000"):
    return {s: [{"session_date": d, "c": close, "v": volume} for d in PRIORS] for s in symbols}


def universe_for(symbols, flag="N"):
    return [{"symbol": s, "etf_classification": flag} for s in symbols]


class SelectionTests(unittest.TestCase):
    def run_selection(self, symbols=("AAA",), **kwargs):
        defaults = dict(date=DAY, calendar_prior_dates=PRIORS,
                        universe=universe_for(symbols), daily=daily_for(symbols))
        defaults.update(kwargs)
        return selection(**defaults)

    def assert_blocked(self, result, status="blocked_invalid_inputs"):
        self.assertEqual(result["status"], status)
        for key in ("selected_symbols", "eligible_symbols", "selected_top64", "selected_hash32", "rows"):
            self.assertEqual(result[key], [])
        self.assertEqual(result["metrics"], {})

    def test_inclusive_price_and_liquidity_boundaries(self):
        got = self.run_selection(daily=daily_for(["AAA"], "5", "4000000"))
        self.assertEqual(got["eligible_symbols"], ["AAA"])
        self.assertEqual(got["metrics"]["AAA"]["median_prior20_daily_close_times_volume"], "20000000")

    def test_price_just_under_threshold(self):
        got = self.run_selection(daily=daily_for(["AAA"], "4.999999999999999999999", "5000000"))
        self.assertEqual(got["eligible_symbols"], [])
        self.assertEqual(got["excluded_by_reason"]["prior_close_below_5"], ["AAA"])

    def test_volume_just_under_threshold(self):
        got = self.run_selection(daily=daily_for(["AAA"], "10", "1999999.99999999999999"))
        self.assertEqual(got["excluded_by_reason"]["prior20_median_dollar_volume_below_20m"], ["AAA"])

    def test_median_is_not_mean(self):
        bars = daily_for(["AAA"], "10", "1")
        bars["AAA"][0]["v"] = "1000000000"
        got = self.run_selection(daily=bars)
        self.assertEqual(got["metrics"]["AAA"]["median_prior20_daily_close_times_volume"], "10")
        self.assertEqual(got["eligible_symbols"], [])

    def test_even_median_averages_middle_two(self):
        bars = daily_for(["AAA"])
        for i, bar in enumerate(bars["AAA"]):
            bar["v"] = "1999999" if i < 10 else "2000001"
        self.assertEqual(self.run_selection(daily=bars)["eligible_symbols"], ["AAA"])

    def test_zero_volume_allowed_but_zero_price_not(self):
        bars = daily_for(["AAA", "BBB"])
        bars["AAA"][0]["v"] = 0
        bars["BBB"][0]["c"] = 0
        got = self.run_selection(["AAA", "BBB"], daily=bars)
        self.assertEqual(got["eligible_symbols"], ["AAA"])
        self.assertEqual(got["excluded_by_reason"]["invalid_prior20_close_or_volume"], ["BBB"])

    def test_missing_prior_session_excluded_even_with_extra_old_row(self):
        bars = daily_for(["AAA"])
        bars["AAA"].pop(2)
        bars["AAA"].append({"session_date": "2026-01-01", "c": 999, "v": 999999999})
        got = self.run_selection(daily=bars)
        self.assertEqual(got["excluded_by_reason"]["missing_one_or_more_prior20_session_daily_bars"], ["AAA"])
        self.assertEqual(got["diagnostics"]["ignored_older_daily_rows"], 1)

    def test_invalid_numeric_prior_values_exclude_not_rank(self):
        for value in ("NaN", "Infinity", float("inf"), None, True, {}, "bad", -1):
            with self.subTest(value=value):
                bars = daily_for(["AAA"])
                bars["AAA"][4]["v"] = value
                got = self.run_selection(daily=bars)
                self.assertEqual(got["status"], "ready")
                self.assertEqual(got["excluded_by_reason"]["invalid_prior20_close_or_volume"], ["AAA"])

    def test_unknown_classification_retained_and_marked(self):
        got = self.run_selection(universe=universe_for(["AAA"], "unknown"))
        self.assertEqual(got["eligible_symbols"], ["AAA"])
        row = next(x for x in got["rows"] if x["symbol"] == "AAA")
        self.assertTrue(row["classification_unknown"])

    def test_explicit_etf_excluded_even_if_huge_volume(self):
        got = self.run_selection(universe=universe_for(["AAA"], "Y"),
                                 daily=daily_for(["AAA"], 1000, 100000000))
        self.assertEqual(got["eligible_symbols"], [])
        self.assertEqual(got["excluded_by_reason"]["explicit_ETF_Y"], ["AAA"])

    def test_controls_never_become_stocks_if_classified_n(self):
        got = self.run_selection(["AAA", "SPY", "QQQ"])
        self.assertEqual(got["eligible_symbols"], ["AAA"])
        self.assertEqual(got["selected_top64"], ["AAA"])
        self.assertEqual(got["selected_symbols"], ["AAA", "QQQ", "SPY"])
        for row in got["rows"]:
            if row["symbol"] in ("QQQ", "SPY"):
                self.assertFalse(row["stock_eligible"])
                self.assertEqual(row["roles"], ["forced_ETF_control"])

    def test_missing_controls_forced_once_without_daily_data(self):
        got = self.run_selection(symbols=(), universe=[], daily={})
        self.assertEqual(got["selected_symbols"], ["QQQ", "SPY"])
        self.assertEqual(got["counts"]["selected_controls"], 2)
        self.assertTrue(all(not row["in_supplied_universe"] for row in got["rows"]))

    def test_top64_and_sha32_exact_and_disjoint(self):
        symbols = [f"S{i:03d}" for i in range(120)]
        got = self.run_selection(symbols)
        self.assertEqual(got["selected_top64"], symbols[:64])
        expected = sorted(symbols[64:], key=lambda s: (sha256(f"s500_flush_v1|{DAY}|{s}".encode()).hexdigest(), s))[:32]
        self.assertEqual(got["selected_hash32"], expected)
        self.assertEqual(len(got["selected_symbols"]), 98)
        self.assertEqual(len(got["rows"]), 122)
        unselected = [r for r in got["rows"] if r["stock_eligible"] and not r["selected"]]
        self.assertEqual(len(unselected), 24)
        self.assertTrue(all(not r["roles"] for r in unselected))

    def test_liquidity_rank_beats_symbol(self):
        bars = daily_for(["AAA", "ZZZ"])
        for bar in bars["ZZZ"]:
            bar["v"] = 3000000
        self.assertEqual(self.run_selection(["AAA", "ZZZ"], daily=bars)["selected_top64"], ["ZZZ", "AAA"])

    def test_liquidity_rank_preserves_digits_beyond_default_decimal_context(self):
        bars = daily_for(["AAA", "ZZZ"])
        for bar in bars["ZZZ"]:
            bar["v"] = "2000000.0000000000000000000000001"
        self.assertEqual(self.run_selection(["AAA", "ZZZ"], daily=bars)["selected_top64"], ["ZZZ", "AAA"])

    def test_storage_overlay_is_eligible_only_and_undeduplicated_roles(self):
        symbols = ["MU", "STX", "WDC", "SNDK", "NTAP", "RMBS", "SIMO", "P"]
        bars = daily_for(symbols)
        bars["MU"] = []
        got = self.run_selection(symbols, daily=bars)
        self.assertEqual(got["selected_storage"], symbols[1:])
        self.assertNotIn("MU", got["selected_symbols"])
        self.assertEqual(len(got["selected_symbols"]), 9)
        row = next(r for r in got["rows"] if r["symbol"] == "STX")
        self.assertEqual(row["roles"], ["prior20_dollar_volume_top64", "eligible_storage_overlay"])

    def test_storage_outside_top_and_hash_still_included(self):
        symbols = [f"A{i:03d}" for i in range(300)] + ["MU"]
        # Ensure MU is eligible but does not enter the two base components.
        got = self.run_selection(symbols)
        self.assertNotIn("MU", got["selected_top64"])
        self.assertNotIn("MU", got["selected_hash32"])
        self.assertIn("MU", got["selected_symbols"])

    def test_input_order_does_not_change_output_or_mutate_input(self):
        symbols = [f"S{i:03d}" for i in range(120)]
        universe, daily = universe_for(symbols), daily_for(symbols)
        original = copy.deepcopy((universe, daily))
        baseline = self.run_selection(symbols, universe=universe, daily=daily)
        self.assertEqual((universe, daily), original)
        rng = random.Random(24)
        rng.shuffle(universe)
        for bars in daily.values():
            rng.shuffle(bars)
        daily = dict(reversed(list(daily.items())))
        self.assertEqual(self.run_selection(symbols, universe=universe, daily=daily), baseline)

    def test_source_incomplete_never_ranks_partial_pool(self):
        for flag in (False, None, 1, "true"):
            with self.subTest(flag=flag):
                self.assert_blocked(self.run_selection(source_complete=flag), "blocked_source_incomplete")

    def test_calendar_wrong_length_duplicate_unordered_current_future(self):
        calendars = [PRIORS[:-1], PRIORS + ["2026-09-30"], PRIORS[:-1] + [PRIORS[-2]],
                     list(reversed(PRIORS)), PRIORS[:-1] + [DAY], PRIORS[:-1] + ["2026-10-09"],
                     PRIORS[:-1] + ["2026-9-30"], "bad"]
        for calendar in calendars:
            with self.subTest(calendar=calendar):
                self.assert_blocked(self.run_selection(calendar_prior_dates=calendar))

    def test_same_day_future_and_off_calendar_rows_block_whole_pool(self):
        for day in (DAY, "2026-10-09", "2026-10-01"):
            with self.subTest(day=day):
                bars = daily_for(["AAA", "BBB"])
                bars["AAA"].append({"session_date": day, "c": 10, "v": 2000000})
                self.assert_blocked(self.run_selection(["AAA", "BBB"], daily=bars))

    def test_duplicate_daily_dates_even_equal_values_block(self):
        bars = daily_for(["AAA"])
        bars["AAA"].append(copy.deepcopy(bars["AAA"][0]))
        self.assert_blocked(self.run_selection(daily=bars))

    def test_duplicate_old_dates_block(self):
        bars = daily_for(["AAA"])
        bars["AAA"].extend([{"session_date": "2026-01-01", "c": 1, "v": 1}] * 2)
        self.assert_blocked(self.run_selection(daily=bars))

    def test_old_prices_never_pollute_prior20(self):
        baseline = self.run_selection()
        bars = daily_for(["AAA"])
        bars["AAA"].append({"session_date": "2026-01-01", "c": "NaN", "v": "Infinity"})
        got = self.run_selection(daily=bars)
        self.assertEqual(got["metrics"], baseline["metrics"])
        self.assertEqual(got["selected_symbols"], baseline["selected_symbols"])

    def test_duplicate_or_illegal_universe_blocks_whole_pool(self):
        for universe in (universe_for(["AAA", "AAA"]), universe_for(["AAA", "aapl"]),
                         universe_for(["AAA", "A/B"]), universe_for(["AAA", "A" * 11]),
                         universe_for(["AAA", "1AAA"]), universe_for(["AAA", "A\n"]),
                         [{"symbol": "AAA", "etf_classification": None}]):
            with self.subTest(universe=universe):
                self.assert_blocked(self.run_selection(universe=universe))

    def test_valid_symbol_grammar(self):
        got = self.run_selection(["BRK.B", "ABC-D", "A1"])
        self.assertEqual(got["eligible_symbols"], ["A1", "ABC-D", "BRK.B"])

    def test_malformed_structural_inputs_block(self):
        cases = [{"daily": []}, {"universe": {}}, {"universe": [None]},
                 {"date": "20261008"}, {"date": None}, {"daily": {"AAA": None}},
                 {"daily": {"AAA": [None]}}, {"daily": {"AAA": [{"session_date": None}]}},
                 {"daily": {"AAA": [{"session_date": "2026-02-30"}]}},
                 {"daily": {"aapl": []}}]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                self.assert_blocked(self.run_selection(**kwargs))

    def test_extra_daily_symbols_never_enter_universe(self):
        got = self.run_selection(daily=daily_for(["AAA", "ZZZ"]))
        self.assertEqual(got["eligible_symbols"], ["AAA"])
        self.assertEqual(got["diagnostics"]["daily_symbols_outside_universe"], ["ZZZ"])

    def test_extra_daily_future_row_still_blocks_contaminated_input(self):
        bars = daily_for(["AAA", "ZZZ"])
        bars["ZZZ"].append({"session_date": DAY, "c": 1, "v": 1})
        self.assert_blocked(self.run_selection(daily=bars))

    def test_decimal_input_is_accepted_without_float_rounding(self):
        bars = daily_for(["AAA"], Decimal("5"), Decimal("4000000"))
        self.assertEqual(self.run_selection(daily=bars)["eligible_symbols"], ["AAA"])

    def test_extreme_finite_encodings_exclude_stock_without_overflow_or_underflow(self):
        values = ("1e1000000", "1e999999999", "1e-1000000", "1e-999999999",
                  "1e101", "1e-101", "9" * 81)
        for field in ("c", "v"):
            for value in values:
                with self.subTest(field=field, value=value):
                    bars = daily_for(["AAA", "BBB"])
                    for bar in bars["AAA"]:
                        bar[field] = value
                    got = self.run_selection(["AAA", "BBB"], daily=bars)
                    self.assertEqual(got["status"], "ready")
                    self.assertEqual(got["eligible_symbols"], ["BBB"])
                    self.assertEqual(got["excluded_by_reason"]["invalid_prior20_close_or_volume"], ["AAA"])
                    self.assertNotIn("AAA", got["metrics"])

    def test_numeric_safety_limit_boundaries_admit_large_finite_inputs(self):
        for value in ("1e100", "9" * 80):
            with self.subTest(value=value):
                got = self.run_selection(daily=daily_for(["AAA"], value, "20000000"))
                self.assertEqual(got["eligible_symbols"], ["AAA"])
                self.assertNotIn("invalid_prior20_close_or_volume", got["excluded_by_reason"])

    def test_small_allowed_volume_remains_positive_and_hits_economic_threshold(self):
        got = self.run_selection(daily=daily_for(["AAA"], "10", "1e-100"))
        value = Decimal(got["metrics"]["AAA"]["median_prior20_daily_close_times_volume"])
        self.assertEqual(value, Decimal("1e-99"))
        self.assertGreater(value, 0)
        self.assertEqual(got["excluded_by_reason"]["prior20_median_dollar_volume_below_20m"], ["AAA"])

    def test_small_allowed_close_remains_positive_and_hits_price_threshold(self):
        got = self.run_selection(daily=daily_for(["AAA"], "1e-100", "1e100"))
        self.assertEqual(Decimal(got["metrics"]["AAA"]["median_prior20_daily_close_times_volume"]), Decimal(1))
        self.assertEqual(got["excluded_by_reason"]["prior_close_below_5"], ["AAA"])


if __name__ == "__main__":
    unittest.main()
