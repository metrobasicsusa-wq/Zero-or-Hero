"""Independent Fraction oracle and adversarial inputs for the prospective selector.

This file imports only the public production entry point. Its reference arithmetic
uses fractions/statistics rather than the selector's Decimal helpers. All prices
here are invented fixtures, including the 6,633-symbol completeness fixture.
"""
import copy
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction
import hashlib
import heapq
import random
import statistics
import unittest

from prospect_selection import selection


SESSION = "2026-10-08"
PRIOR = []
cursor = date(2026, 10, 7)
while len(PRIOR) < 20:
    if cursor.weekday() < 5:
        PRIOR.append(cursor.isoformat())
    cursor -= timedelta(days=1)
PRIOR.reverse()
CONTROL = {"SPY", "QQQ"}
STORAGE = ["MU", "STX", "WDC", "SNDK", "NTAP", "RMBS", "SIMO", "P"]


def fixture(symbols, price="20", volume="2000000"):
    universe = [{"symbol": s, "etf_classification": "N"} for s in symbols]
    daily = {s: [{"session_date": d, "c": price, "v": volume} for d in PRIOR]
             for s in symbols}
    return universe, daily


def oracle(universe, daily):
    """Reference for structurally valid fixtures, not production error handling."""
    values, qualifies = {}, []
    for item in universe:
        symbol = item["symbol"]
        if symbol in CONTROL or item["etf_classification"] == "Y":
            continue
        bars = {b["session_date"]: b for b in daily.get(symbol, [])}
        if any(d not in bars for d in PRIOR):
            continue
        try:
            pairs = [(Fraction(str(bars[d]["c"])), Fraction(str(bars[d]["v"])))
                     for d in PRIOR]
        except (KeyError, ValueError, ZeroDivisionError):
            continue
        if any(c <= 0 or v < 0 for c, v in pairs):
            continue
        median = statistics.median([c * v for c, v in pairs])
        close = pairs[-1][0]
        values[symbol] = (close, median)
        if close >= 5 and median >= 20_000_000:
            qualifies.append(symbol)
    top = heapq.nsmallest(64, qualifies, key=lambda s: (-values[s][1], s))
    pool = [s for s in qualifies if s not in set(top)]
    draw = heapq.nsmallest(32, pool, key=lambda s: (
        hashlib.sha256(("s500_flush_v1|" + SESSION + "|" + s).encode("ascii")).digest(), s))
    overlay = [s for s in STORAGE if s in qualifies]
    return {"metrics": values, "eligible": sorted(qualifies), "top": top,
            "draw": draw, "overlay": overlay,
            "selected": sorted(set(top) | set(draw) | set(overlay) | CONTROL)}


class IndependentSelectorTests(unittest.TestCase):
    def execute(self, universe, daily, **changes):
        args = {"date": SESSION, "calendar_prior_dates": PRIOR,
                "universe": universe, "daily": daily, "source_complete": True}
        args.update(changes)
        return selection(**args)

    def compare_oracle(self, universe, daily):
        actual = self.execute(universe, daily)
        expected = oracle(universe, daily)
        self.assertEqual(actual["status"], "ready")
        for name, reference in [("eligible_symbols", "eligible"),
                                ("selected_top64", "top"),
                                ("selected_hash32", "draw"),
                                ("selected_storage", "overlay"),
                                ("selected_symbols", "selected")]:
            self.assertEqual(actual[name], expected[reference], name)
        self.assertEqual(set(actual["metrics"]), set(expected["metrics"]))
        for symbol, (prior_close, median) in expected["metrics"].items():
            self.assertEqual(Fraction(actual["metrics"][symbol]["prior_close"]), prior_close)
            self.assertEqual(Fraction(actual["metrics"][symbol]["median_prior20_daily_close_times_volume"]), median)
        all_symbols = {u["symbol"] for u in universe} | CONTROL
        self.assertEqual([r["symbol"] for r in actual["rows"]], sorted(all_symbols))
        self.assertEqual(actual["counts"]["universe"], len(universe))
        self.assertEqual(actual["counts"]["eligible_stocks"], len(expected["eligible"]))
        self.assertEqual(actual["counts"]["selected"], len(expected["selected"]))
        self.assertEqual(actual["counts"]["selected_stocks"], len(expected["selected"]) - 2)
        excluded = [s for symbols in actual["excluded_by_reason"].values() for s in symbols]
        self.assertEqual(len(excluded), len(set(excluded)))
        self.assertEqual(set(excluded) | set(actual["eligible_symbols"]), {u["symbol"] for u in universe})
        return actual

    def assert_no_pool(self, result, status="blocked_invalid_inputs"):
        self.assertEqual(result["status"], status)
        for name in ("rows", "selected_symbols", "eligible_symbols", "selected_top64", "selected_hash32"):
            self.assertEqual(result[name], [])
        self.assertEqual(result["metrics"], {})

    def test_seeded_fraction_reference_across_16_invented_pools(self):
        rng = random.Random(20261008)
        for iteration in range(16):
            with self.subTest(iteration=iteration):
                symbols = ["R" + str(i).zfill(4) for i in range(137)] + STORAGE + ["SPY", "QQQ"]
                u, d = fixture(symbols)
                for item in u:
                    item["etf_classification"] = rng.choices(["N", "Y", "unknown"], [8, 1, 1])[0]
                    for bar in d[item["symbol"]]:
                        bar["c"] = f"{rng.randrange(1, 120)}.{rng.randrange(10000):04d}"
                        bar["v"] = f"{rng.randrange(0, 5000000)}.{rng.randrange(100):02d}"
                for s in rng.sample(symbols, 5):
                    d[s].pop(rng.randrange(20))
                self.compare_oracle(u, d)

    def test_full_6633_symbol_synthetic_pool_retains_every_denominator(self):
        symbols = ["K" + str(i).zfill(5) for i in range(6623)] + STORAGE + ["SPY", "QQQ"]
        u, d = fixture(symbols)
        for i, item in enumerate(u):
            if i % 31 == 0:
                item["etf_classification"] = "Y"
            elif i % 17 == 0:
                item["etf_classification"] = "unknown"
            for bar in d[item["symbol"]]:
                bar["v"] = 1_000_000 + (i % 47) * 100_000
        self.assertEqual(len(u), 6633)
        got = self.compare_oracle(u, d)
        self.assertEqual(len(got["rows"]), 6633)
        self.assertGreater(sum(r["stock_eligible"] and not r["selected"] for r in got["rows"]), 6000)

    def test_exact_boundary_and_adjacent_rational_values(self):
        u, d = fixture(["AT", "BELOW", "ABOVE", "LOWPRICE"], "5", "4000000")
        for bar in d["BELOW"]:
            bar["v"] = "3999999.999999999999999999999999999999"
        for bar in d["ABOVE"]:
            bar["v"] = "4000000.000000000000000000000000000001"
        d["LOWPRICE"][-1]["c"] = "4.999999999999999999999999999999999999"
        got = self.compare_oracle(u, d)
        self.assertEqual(got["eligible_symbols"], ["ABOVE", "AT"])
        self.assertEqual(got["selected_top64"], ["ABOVE", "AT"])

    def test_middle_two_products_not_product_of_medians_or_mean(self):
        u, d = fixture(["NEGPAIR", "OUTLIER"])
        for i, b in enumerate(d["NEGPAIR"]):
            b["c"], b["v"] = ("5", "10000000") if i < 10 else ("50", "100000")
        d["OUTLIER"][0]["v"] = "999999999999999999999"
        got = self.compare_oracle(u, d)
        self.assertEqual(Fraction(got["metrics"]["NEGPAIR"]["median_prior20_daily_close_times_volume"]), 27_500_000)
        self.assertEqual(Fraction(got["metrics"]["OUTLIER"]["median_prior20_daily_close_times_volume"]), 40_000_000)

    def test_hash_selection_and_top_ties_invariant_to_input_order(self):
        u, d = fixture(["T" + str(i).zfill(4) for i in range(175)] + STORAGE)
        original = copy.deepcopy((u, d))
        expected = self.compare_oracle(u, d)
        self.assertEqual((u, d), original)
        u.reverse()
        d = dict(reversed(list(d.items())))
        for rows in d.values():
            rows.reverse()
        self.assertEqual(self.compare_oracle(u, d), expected)

    def test_known_etfs_controls_and_unknowns_are_explicit(self):
        u, d = fixture(["AAA", "ETF", "UNKN", "SPY", "QQQ"])
        u[1]["etf_classification"] = "Y"
        u[2]["etf_classification"] = "unknown"
        d.pop("SPY")
        d.pop("QQQ")
        got = self.compare_oracle(u, d)
        rows = {r["symbol"]: r for r in got["rows"]}
        self.assertEqual(got["eligible_symbols"], ["AAA", "UNKN"])
        self.assertTrue(rows["UNKN"]["classification_unknown"])
        for s in CONTROL:
            self.assertEqual(rows[s]["roles"], ["forced_ETF_control"])
            self.assertEqual(rows[s]["target_role"], "benchmark")
            self.assertFalse(rows[s]["stock_eligible"])
        self.assertFalse(rows["ETF"]["selected"])

    def test_forced_controls_unique_when_absent_from_directory(self):
        got = self.compare_oracle(*fixture(["AAA"]))
        self.assertEqual(got["counts"]["selected_controls"], 2)
        self.assertEqual(len(got["rows"]), 3)
        self.assertTrue(all(not r["in_supplied_universe"] for r in got["rows"] if r["symbol"] in CONTROL))

    def test_partial_source_is_never_ranked_even_with_all_bars(self):
        u, d = fixture(["AAA", "BBB"])
        for flag in (False, 0, 1, None, "True", {}, []):
            with self.subTest(flag=flag):
                self.assert_no_pool(self.execute(u, d, source_complete=flag), "blocked_source_incomplete")

    def test_future_contamination_in_unranked_extra_symbol_blocks_pool(self):
        for bad_date in (SESSION, "2026-10-09", "2036-01-01"):
            u, d = fixture(["AAA"])
            d["OUTSIDE"] = [{"session_date": bad_date, "c": "0.01", "v": "0"}]
            self.assert_no_pool(self.execute(u, d))

    def test_bad_calendar_order_duplicate_or_current_day_blocks_pool(self):
        u, d = fixture(["AAA"])
        for prior in (PRIOR[::-1], PRIOR[:-1], PRIOR + [SESSION],
                      PRIOR[:-1] + [SESSION], PRIOR[:-1] + [PRIOR[-2]],
                      PRIOR[:-1] + ["2026-10-7"]):
            with self.subTest(prior=prior):
                self.assert_no_pool(self.execute(u, d, calendar_prior_dates=prior))

    def test_missing_session_not_repaired_by_stale_huge_daily_bar(self):
        u, d = fixture(["AAA", "BBB"])
        del d["AAA"][9]
        d["AAA"].append({"session_date": "2020-01-02", "c": "999999", "v": "999999999"})
        got = self.compare_oracle(u, d)
        self.assertEqual(got["eligible_symbols"], ["BBB"])
        self.assertEqual(got["diagnostics"]["ignored_older_daily_rows"], 1)

    def test_nan_infinity_boolean_negative_and_non_numeric_excluded(self):
        for field, bad in [(f, x) for f in ("c", "v") for x in
                           ("NaN", "sNaN", "Infinity", "-Infinity", True, False,
                            None, [], {}, "", "word", float("nan"), Decimal("Infinity"), -1)]:
            with self.subTest(field=field, bad=bad):
                u, d = fixture(["AAA", "BBB"])
                d["AAA"][7][field] = bad
                got = self.execute(u, d)
                self.assertEqual(got["status"], "ready")
                self.assertEqual(got["eligible_symbols"], ["BBB"])
                self.assertIn("AAA", got["excluded_by_reason"]["invalid_prior20_close_or_volume"])

    def test_extreme_finite_exponents_cannot_crash_or_silently_rank(self):
        for field in ("c", "v"):
            for value in ("1e1000000", "1e999999999", "1e-999999999"):
                with self.subTest(field=field, value=value):
                    u, d = fixture(["AAA", "BBB"])
                    for row in d["AAA"]:
                        row[field] = value
                    got = self.execute(u, d)
                    self.assertEqual(got["status"], "ready")
                    self.assertEqual(got["eligible_symbols"], ["BBB"])
                    self.assertIn("AAA", got["excluded_by_reason"]["invalid_prior20_close_or_volume"])

    def test_long_exact_decimal_products_and_medians_match_fraction(self):
        u, d = fixture(["AAA", "BBB"])
        for i, row in enumerate(d["AAA"]):
            row["c"] = "12.34567890123456789012345678901234567890123456789"
            row["v"] = "2345678.90123456789012345678901234567890123456789" if i < 10 else "2345678.9"
        self.compare_oracle(u, d)

    def test_duplicate_universe_or_daily_bar_blocks_whole_pool(self):
        u, d = fixture(["AAA", "BBB"])
        self.assert_no_pool(self.execute(u + [copy.deepcopy(u[0])], d))
        d["AAA"].append(copy.deepcopy(d["AAA"][0]))
        self.assert_no_pool(self.execute(u, d))

    def test_calendar_window_off_session_bar_rejected_not_used(self):
        u, d = fixture(["AAA", "BBB"])
        d["AAA"].append({"session_date": "2026-09-26", "c": 10, "v": 2000000})
        self.assert_no_pool(self.execute(u, d))


if __name__ == "__main__":
    unittest.main()
