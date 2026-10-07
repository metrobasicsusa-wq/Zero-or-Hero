"""Synthetic causal-feature boundaries, independent of historical outcomes."""

import copy
from decimal import Decimal, localcontext
import unittest

from prefix_features import first30_features


def bars(price="100", count=100):
    return [{"t": t, "o": price, "h": price, "l": price, "c": price, "v": "1"} for t in range(count)]


def close_at(data, minute, value):
    row = next(row for row in data if row["t"] == minute)
    row.update(c=value, h=str(max(Decimal(row["o"]), Decimal(value))),
               l=str(min(Decimal(row["o"]), Decimal(value))))


class PrefixFeatureTests(unittest.TestCase):
    def test_flat_prefix_complete_without_flush_filter(self):
        result = first30_features(bars(), "100")
        self.assertEqual(result["status"], "complete")
        self.assertTrue(result["prefix_valid"])
        self.assertEqual(result["t_min"], 0)
        self.assertEqual(result["first30_volume"], "30")
        for field in ("close29_return", "min_close_return", "max_close_return", "first30_high_low_range", "gap_to_previous_close"):
            self.assertEqual(Decimal(result[field]), Decimal(0))

    def test_exact_features_and_units(self):
        data = bars()
        close_at(data, 8, "97")
        close_at(data, 15, "103")
        close_at(data, 29, "98")
        data[6]["h"], data[7]["l"] = "105", "95"
        result = first30_features(data, "125")
        expected = {"open0": "100", "close29": "98", "min_close": "97", "max_close": "103",
                    "close29_return": "-.02", "min_close_return": "-.03", "max_close_return": ".03",
                    "first30_high_low_range": ".1", "gap_to_previous_close": "-.2"}
        for field, value in expected.items():
            self.assertEqual(Decimal(result[field]), Decimal(value))
        self.assertEqual(result["t_min"], 8)

    def test_range_is_high_minus_low_over_open_not_ratio(self):
        data = bars()
        data[10]["h"], data[20]["l"] = "120", "80"
        result = first30_features(data, "100")
        self.assertEqual(Decimal(result["first30_high_low_range"]), Decimal(".4"))
        self.assertNotEqual(Decimal(result["first30_high_low_range"]), Decimal(".5"))
        self.assertEqual(Decimal(result["close29_return"]), Decimal(0))

    def test_earliest_tied_minimum(self):
        data = bars()
        for t in (20, 3, 10):
            close_at(data, t, "97")
        self.assertEqual(first30_features(data, "100")["t_min"], 3)

    def test_low_does_not_choose_minimum_close(self):
        data = bars()
        data[2]["l"] = "10"
        close_at(data, 10, "99")
        self.assertEqual(first30_features(data, "100")["t_min"], 10)

    def test_open0_anchor_not_first_close(self):
        data = bars()
        close_at(data, 0, "98")
        result = first30_features(data, "125")
        self.assertEqual(Decimal(result["min_close_return"]), Decimal("-.02"))
        self.assertEqual(Decimal(result["gap_to_previous_close"]), Decimal("-.2"))

    def test_final_prefix_bar_included_next_bar_excluded(self):
        data = bars()
        close_at(data, 29, "99")
        close_at(data, 30, "1")
        data[30]["v"] = "1000000"
        result = first30_features(data, "100")
        self.assertEqual(result["t_min"], 29)
        self.assertEqual(result["min_close"], "99")
        self.assertEqual(result["first30_volume"], "30")

    def test_volume_sum_all_first30_only(self):
        data = bars()
        for t in range(30):
            data[t]["v"] = str(Decimal(t) / 10)
        expected = sum((Decimal(t) / 10 for t in range(30)), Decimal(0))
        self.assertEqual(Decimal(first30_features(data, "100")["first30_volume"]), expected)

    def test_zero_volume_valid(self):
        data = bars()
        for row in data:
            row["v"] = "0"
        self.assertEqual(first30_features(data, "100")["first30_volume"], "0")

    def test_missing_prefix_unknown_all_market_features(self):
        data = [row for row in bars() if row["t"] != 12]
        result = first30_features(data, "100")
        self.assertEqual(result["status"], "unknown_prefix")
        self.assertEqual(result["coverage"]["missing_minutes"], [12])
        for field in ("open0", "close29", "min_close", "t_min", "max_close", "first30_volume", "first30_high_low_range", "gap_to_previous_close"):
            self.assertIsNone(result[field])
        self.assertTrue(result["previous_close_valid"])

    def test_duplicate_prefix_not_overwritten(self):
        data = bars()
        data.append(dict(data[5]))
        result = first30_features(data, "100")
        self.assertEqual(result["status"], "unknown_prefix")
        self.assertEqual(result["coverage"]["invalid_minutes"], [{"minute": 5, "reason": "duplicate_minute"}])

    def test_future_bad_bar_and_duplicate_cannot_erase_features(self):
        data = bars()
        before = first30_features(data, "100")
        data[30] = {"t": 30}
        data[50]["c"] = "NaN"
        data.append(dict(data[70]))
        self.assertEqual(before, first30_features(data, "100"))

    def test_negative_minute_ignored(self):
        data = bars()
        before = first30_features(data, "100")
        data.append({"t": -1, "o": "bad"})
        self.assertEqual(before, first30_features(data, "100"))

    def test_unlocatable_timestamp_conservatively_unknown(self):
        data = bars()
        data.append({"t": "not-a-minute"})
        result = first30_features(data, "100")
        self.assertEqual(result["status"], "unknown_prefix")
        self.assertTrue(result["coverage"]["global_errors"])

    def test_source_complete_requires_literal_true(self):
        for flag in (False, None, 1, "true"):
            result = first30_features(bars(), "100", source_complete=flag)
            self.assertEqual(result["status"], "unknown_prefix")
            self.assertFalse(result["coverage"]["source_complete"])

    def test_missing_previous_close_only_gap_unknown(self):
        result = first30_features(bars(), None)
        self.assertTrue(result["prefix_valid"])
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["close29"], "100")
        self.assertFalse(result["previous_close_valid"])
        self.assertIsNone(result["gap_to_previous_close"])
        self.assertEqual(result["gap_reason"], "previous_close_missing")

    def test_bad_previous_close_only_gap_unknown(self):
        for previous in (0, "-1", "NaN", "Infinity", True, False, "bad", []):
            with self.subTest(previous=previous):
                result = first30_features(bars(), previous)
                self.assertEqual(result["status"], "complete")
                self.assertIsNone(result["gap_to_previous_close"])
                self.assertFalse(result["previous_close_valid"])
                self.assertIsNotNone(result["previous_close_reason"])

    def test_no_false_previous_close_fallback_to_open(self):
        result = first30_features(bars(), None)
        self.assertIsNone(result["previous_close"])
        self.assertIsNone(result["gap_to_previous_close"])

    def test_fractional_previous_close_kept(self):
        result = first30_features(bars("1.5"), ".75")
        self.assertEqual(Decimal(result["gap_to_previous_close"]), Decimal(1))

    def test_decimal_precision_42(self):
        result = first30_features(bars("100"), "3")
        with localcontext() as context:
            context.prec = 42
            expected = Decimal(100) / Decimal(3) - 1
        self.assertEqual(Decimal(result["gap_to_previous_close"]), expected)

    def test_invalid_ohlcv_prefix_unknown(self):
        for key, value in (("h", "1"), ("l", "0"), ("c", "NaN"), ("o", "Infinity"), ("v", "-1"), ("c", True)):
            data = bars()
            data[4][key] = value
            self.assertEqual(first30_features(data, "100")["status"], "unknown_prefix")

    def test_order_invariance_and_no_mutation(self):
        data = bars()
        close_at(data, 3, "99")
        before_data = copy.deepcopy(data)
        self.assertEqual(first30_features(data, "100"), first30_features(list(reversed(data)), "100"))
        self.assertEqual(data, before_data)

    def test_price_scale_invariance(self):
        data = bars()
        close_at(data, 3, "97")
        close_at(data, 15, "102")
        close_at(data, 29, "99")
        before = first30_features(data, "120")
        for row in data:
            for field in ("o", "h", "l", "c"):
                row[field] = str(Decimal(row[field]) * 7)
        after = first30_features(data, "840")
        for field in ("t_min", "close29_return", "min_close_return", "max_close_return", "first30_high_low_range", "gap_to_previous_close", "first30_volume"):
            self.assertEqual(before[field], after[field])

    def test_no_future_entry_or_rvol_fields(self):
        result = first30_features(bars(), "100")
        self.assertEqual(result["available_at_minute"], 30)
        self.assertEqual(result["observation_end_minute"], 29)
        self.assertFalse(any("rvol" in key.lower() or "entry" in key.lower() for key in result))

    def test_invalid_container_unknown(self):
        for data in (None, "bad", 42, {"t": 0}):
            self.assertEqual(first30_features(data, "100")["status"], "unknown_prefix")


if __name__ == "__main__":
    unittest.main()
