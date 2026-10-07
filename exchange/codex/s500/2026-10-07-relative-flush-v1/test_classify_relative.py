"""Synthetic classification tests; no historical results are read."""

import copy
from decimal import Decimal
import unittest

from classify_relative import classify_case


def bars(price="100", count=100):
    return [{"t": t, "o": price, "h": price, "l": price, "c": price, "v": "1"} for t in range(count)]


def close_at(data, minute, value):
    bar = next(row for row in data if row["t"] == minute)
    bar["c"] = value
    bar["h"] = str(max(Decimal(bar["o"]), Decimal(value)))
    bar["l"] = str(min(Decimal(bar["o"]), Decimal(value)))


def target():
    data = bars()
    close_at(data, 10, "97")
    return data


class RelativeClassifierTests(unittest.TestCase):
    def test_inclusive_half_percent_boundary(self):
        benchmark = bars()
        close_at(benchmark, 10, "99.5")
        result = classify_case(target(), benchmark)
        self.assertEqual(result["classification"], "market_down")
        self.assertEqual(Decimal(result["benchmark_return"]), Decimal("-.005"))

    def test_just_above_boundary_not_down(self):
        benchmark = bars()
        close_at(benchmark, 10, "99.50000000000001")
        self.assertEqual(classify_case(target(), benchmark)["classification"], "market_not_down")

    def test_just_below_boundary_down(self):
        benchmark = bars()
        close_at(benchmark, 10, "99.49999999999999")
        self.assertEqual(classify_case(target(), benchmark)["classification"], "market_down")

    def test_precision_beyond_return_display_cannot_flip_boundary(self):
        benchmark = bars()
        close_at(benchmark, 10, "99.5" + "0" * 70 + "1")
        self.assertEqual(classify_case(target(), benchmark)["classification"], "market_not_down")

    def test_earliest_tie_uses_earliest_benchmark_minute(self):
        data, benchmark = target(), bars()
        close_at(data, 5, "97")
        close_at(benchmark, 5, "99.9")
        close_at(benchmark, 10, "99")
        result = classify_case(data, benchmark)
        self.assertEqual(result["t_min"], 5)
        self.assertEqual(result["classification"], "market_not_down")
        self.assertEqual(Decimal(result["benchmark_return"]), Decimal("-.001"))

    def test_benchmark_own_low_not_used(self):
        benchmark = bars()
        close_at(benchmark, 10, "99.9")
        close_at(benchmark, 20, "90")
        result = classify_case(target(), benchmark)
        self.assertEqual(result["classification"], "market_not_down")
        self.assertEqual(result["benchmark_close_at_target_minute"], "99.9")

    def test_target_low_not_close_ignored(self):
        data = target()
        data[3]["l"] = "10"
        self.assertEqual(classify_case(data, bars())["t_min"], 10)

    def test_open_anchor_instead_of_first_close(self):
        data = bars()
        close_at(data, 0, "97")
        result = classify_case(data, bars())
        self.assertEqual(result["t_min"], 0)
        self.assertEqual(Decimal(result["target_return"]), Decimal("-.03"))

    def test_final_prefix_minute_included(self):
        data = target()
        close_at(data, 29, "96")
        self.assertEqual(classify_case(data, bars())["t_min"], 29)

    def test_minute_thirty_is_excluded(self):
        data = target()
        close_at(data, 30, "1")
        self.assertEqual(classify_case(data, bars())["t_min"], 10)

    def test_known_future_bad_rows_cannot_erase_classification(self):
        data, benchmark = target(), bars()
        before = classify_case(data, benchmark)
        data[30] = {"t": 30}
        benchmark[70]["c"] = "NaN"
        data.append({"t": 95, "o": "bad"})
        self.assertEqual(before, classify_case(data, benchmark))

    def test_future_duplicate_does_not_erase_classification(self):
        data = target()
        data.append(dict(data[60]))
        self.assertEqual(classify_case(data, bars())["status"], "classified")

    def test_unlocatable_timestamp_conservatively_unknown(self):
        data = target()
        data.append({"t": "unlocatable"})
        result = classify_case(data, bars())
        self.assertEqual(result["status"], "unknown_target_prefix")
        self.assertIsNone(result["classification"])

    def test_target_missing_prefix_explicit(self):
        data = [row for row in target() if row["t"] != 2]
        result = classify_case(data, bars())
        self.assertFalse(result["target_valid"])
        self.assertTrue(result["benchmark_valid"])
        self.assertEqual(result["status"], "unknown_target_prefix")
        self.assertIsNone(result["t_min"])
        self.assertIsNone(result["relative_return"])

    def test_benchmark_full_prefix_required_even_after_comparison_minute(self):
        benchmark = [row for row in bars() if row["t"] != 29]
        result = classify_case(target(), benchmark)
        self.assertEqual(result["status"], "unknown_benchmark_prefix")
        self.assertEqual(result["t_min"], 10)
        self.assertIsNotNone(result["target_return"])
        self.assertIsNone(result["benchmark_return"])

    def test_both_missing_explicit(self):
        result = classify_case([], [])
        self.assertEqual(result["status"], "unknown_both_prefixes")
        self.assertIsNone(result["classification"])

    def test_duplicate_target_prefix_not_overwritten(self):
        data = target()
        data.append(dict(data[10]))
        result = classify_case(data, bars())
        self.assertEqual(result["status"], "unknown_target_prefix")
        self.assertEqual(result["coverage"]["target"]["invalid_minutes"], [{"minute": 10, "reason": "duplicate_minute"}])

    def test_duplicate_benchmark_prefix_not_overwritten(self):
        benchmark = bars()
        benchmark.append(dict(benchmark[10]))
        self.assertEqual(classify_case(target(), benchmark)["status"], "unknown_benchmark_prefix")

    def test_invalid_ohlc_and_volume_fail_closed(self):
        for key, value in (("h", "1"), ("l", "0"), ("c", "NaN"), ("o", "Infinity"), ("v", "-1"), ("c", True)):
            with self.subTest(key=key, value=value):
                data = target()
                data[3][key] = value
                self.assertEqual(classify_case(data, bars())["status"], "unknown_target_prefix")

    def test_zero_volume_allowed(self):
        data = target()
        data[3]["v"] = "0"
        self.assertEqual(classify_case(data, bars())["status"], "classified")

    def test_fraction_units_and_simple_subtraction(self):
        benchmark = bars("200")
        close_at(benchmark, 10, "199.1")
        result = classify_case(target(), benchmark)
        self.assertEqual(Decimal(result["target_return"]), Decimal("-.03"))
        self.assertEqual(Decimal(result["benchmark_return"]), Decimal("-.0045"))
        self.assertEqual(Decimal(result["relative_return"]), Decimal("-.0255"))
        self.assertEqual(result["classification"], "market_not_down")

    def test_relative_return_not_price_difference_or_ratio(self):
        benchmark = bars("200")
        close_at(benchmark, 10, "198")
        result = classify_case(target(), benchmark)
        self.assertEqual(Decimal(result["relative_return"]), Decimal("-.02"))

    def test_no_implicit_two_percent_signal_filter(self):
        result = classify_case(bars(), bars())
        self.assertEqual(result["status"], "classified")
        self.assertEqual(result["classification"], "market_not_down")
        self.assertEqual(result["t_min"], 0)

    def test_source_complete_requires_literal_true(self):
        for flag in (False, None, 1, "true"):
            result = classify_case(target(), bars(), source_complete=flag)
            self.assertEqual(result["status"], "unknown_both_prefixes")

    def test_order_invariance_and_no_mutation(self):
        data, benchmark = target(), bars()
        before_data, before_benchmark = copy.deepcopy(data), copy.deepcopy(benchmark)
        original = classify_case(data, benchmark)
        reversed_result = classify_case(list(reversed(data)), list(reversed(benchmark)))
        self.assertEqual(original, reversed_result)
        self.assertEqual(data, before_data)
        self.assertEqual(benchmark, before_benchmark)

    def test_price_scale_invariance(self):
        data, benchmark = target(), bars()
        close_at(benchmark, 10, "99.5")
        before = classify_case(data, benchmark)
        for rows, factor in ((data, Decimal("3")), (benchmark, Decimal("7"))):
            for row in rows:
                for field in ("o", "h", "l", "c"):
                    row[field] = str(Decimal(row[field]) * factor)
        after = classify_case(data, benchmark)
        for field in ("t_min", "target_return", "benchmark_return", "relative_return", "classification", "status"):
            self.assertEqual(before[field], after[field])

    def test_invalid_container_is_unknown(self):
        for value in (None, "bad", 42, {"t": 0}):
            self.assertEqual(classify_case(value, bars())["status"], "unknown_target_prefix")


if __name__ == "__main__":
    unittest.main()
