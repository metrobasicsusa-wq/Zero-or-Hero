"""Synthetic checks for weighting, absent outcomes, and strict paired controls."""
import math
import copy
import unittest

import summarize_flush as sf


class DescriptiveTests(unittest.TestCase):
    def test_empty_is_missing_not_zero(self):
        result = sf.describe([])
        self.assertEqual(result["n"], 0)
        self.assertIsNone(result["mean"])
        self.assertIsNone(result["positive_fraction"])

    def test_zero_counts_are_not_positive(self):
        result = sf.describe([-0.1, 0.0, 0.2])
        self.assertAlmostEqual(result["mean"], 1 / 30)
        self.assertEqual(result["median"], 0)
        self.assertEqual(result["positive_fraction"], 1 / 3)
        self.assertEqual(result["zero_count"], 1)

    def test_nonfinite_and_boolean_rejected(self):
        for value in [math.nan, math.inf, -math.inf, True, "abc", "NaN", "Infinity"]:
            with self.assertRaises(ValueError):
                sf.finite_number(value)

    def test_null_not_silently_ignored_inside_observed_vector(self):
        with self.assertRaises(ValueError):
            sf.describe([0.1, None])

    def test_decimal_string_is_fraction_not_percent(self):
        self.assertEqual(sf.finite_number("0.01"), 0.01)


class BootstrapTests(unittest.TestCase):
    def test_date_means_have_equal_weight(self):
        # A date containing 100 events at +10% still weighs as one date;
        # another date containing one event at -10% also weighs as one.
        result = sf.date_cluster_bootstrap({"2026-01-02": 0.1, "2026-01-05": -0.1}, "primary")
        self.assertAlmostEqual(result["point_estimate"], 0)
        self.assertEqual(result["n_dates"], 2)
        self.assertEqual(result["lower"], -0.1)
        self.assertEqual(result["upper"], 0.1)

    def test_single_date_has_no_interval(self):
        result = sf.date_cluster_bootstrap({"2026-01-02": 0.03}, "primary")
        self.assertEqual(result["status"], "insufficient_dates")
        self.assertEqual(result["point_estimate"], 0.03)
        self.assertIsNone(result["lower"])
        self.assertIsNone(result["upper"])

    def test_empty_has_no_estimate(self):
        result = sf.date_cluster_bootstrap({}, "primary")
        self.assertIsNone(result["point_estimate"])
        self.assertEqual(result["n_dates"], 0)

    def test_order_and_repeated_run_reproducible(self):
        a = {"2026-01-05": 0.01, "2026-01-02": -0.01, "2026-01-06": 0.02}
        b = dict(reversed(list(a.items())))
        self.assertEqual(sf.date_cluster_bootstrap(a, "primary"), sf.date_cluster_bootstrap(b, "primary"))

    def test_horizons_have_separate_seed(self):
        values = {"2026-01-02": 0.01, "2026-01-05": 0.02}
        a = sf.date_cluster_bootstrap(values, "exit30")
        b = sf.date_cluster_bootstrap(values, "exit60")
        self.assertNotEqual(a["seed_integer"], b["seed_integer"])

    def test_null_date_mean_rejected(self):
        with self.assertRaises(ValueError):
            sf.date_cluster_bootstrap({"2026-01-02": None}, "primary")


class MatchedTests(unittest.TestCase):
    def setUp(self):
        self.controls = [
            {"symbol": "AAA", "status": "complete", "net_return": 0.01},
            {"symbol": "BBB", "status": "complete", "net_return": 0.02},
            {"symbol": "CCC", "status": "complete", "net_return": 0.03},
        ]

    def test_three_control_equal_mean(self):
        value, status = sf.matched_excess(0.05, self.controls)
        self.assertEqual(status, "complete")
        self.assertAlmostEqual(value, 0.03)

    def test_missing_control_not_replaced_by_two_control_mean(self):
        self.controls[2]["net_return"] = None
        value, status = sf.matched_excess(0.05, self.controls)
        self.assertIsNone(value)
        self.assertEqual(status, "one_or_more_control_outcomes_incomplete")

    def test_duplicate_control_not_counted_twice(self):
        self.controls[2]["symbol"] = "AAA"
        value, status = sf.matched_excess(0.05, self.controls)
        self.assertIsNone(value)
        self.assertEqual(status, "duplicate_control_symbol")

    def test_three_controls_required(self):
        for controls in [self.controls[:2], self.controls + [self.controls[0]]]:
            self.assertIsNone(sf.matched_excess(0.05, controls)[0])

    def test_target_missing_is_not_zero(self):
        self.assertIsNone(sf.matched_excess(None, self.controls)[0])


def synthetic_case(date, symbol, value):
    rows = []
    for variant in sf.VARIANTS:
        rows.append({
            "case_id": date + "__" + symbol, "date": date, "symbol": symbol,
            "target_role": "stock", **sf.variant_object(variant),
            "risk_set_eligible": True, "flush_detected": value is not None,
            "signal_status": "signal" if value is not None else "no_flush",
            "entry_minute": 61 if value is not None else None,
            "exit_minute": 121 if value is not None else None,
            "net_return": str(value) if value is not None else None,
            "missing_stage": None, "missing_reason": None,
            "paired_controls": [{"symbol": control, "status": "complete", "net_return": "0"}
                                for control in ("X", "Y", "Z")] if value is not None else [],
            "paired_control_status": "complete_three" if value is not None else "not_applicable",
            "matched_excess": str(value) if value is not None else None,
            "benchmarks": {},
        })
    return rows


class StreamingSummaryTests(unittest.TestCase):
    def setUp(self):
        self.calendar = ["2026-01-02", "2026-01-05", "2026-01-06"]
        self.rows = synthetic_case("2026-01-02", "AAA", .1) + synthetic_case("2026-01-02", "BBB", .3)
        self.rows += synthetic_case("2026-01-05", "CCC", -.1) + synthetic_case("2026-01-06", "DDD", None)

    def test_pooled_date_weight_and_no_signal_denominators(self):
        summary, daily, primary = sf.build_summary(iter(self.rows), self.calendar)
        self.assertEqual(summary["ledger_row_count"], 144)
        self.assertEqual(summary["registered_variant_count"], 36)
        self.assertEqual(len(summary["variants"]), 108)
        self.assertAlmostEqual(primary["all"]["event_weighted"]["returns"]["mean"], .1)
        self.assertAlmostEqual(primary["all"]["date_weighted"]["returns"]["mean"], .05)
        self.assertEqual(primary["all"]["date_weighted"]["returns"]["n"], 2)
        self.assertAlmostEqual(primary["matched_excess_date_cluster_bootstrap"]["point_estimate"], .05)
        self.assertEqual(primary["all"]["counts"]["selected_cases"], 4)
        self.assertEqual(primary["all"]["signal_status_counts"]["no_flush"], 1)
        self.assertEqual(primary["periods"]["H2_to_cutoff"]["event_weighted"]["returns"]["n"], 0)
        no_signal = next(row for row in daily if row["date"] == "2026-01-06" and row["group"] == "stock")
        self.assertIsNone(no_signal["returns"]["mean"])

    def test_time_horizon_never_mixed(self):
        for row in self.rows:
            if row["exit_horizon"] == "30m" and row["net_return"] is not None:
                row["net_return"] = row["matched_excess"] = "0.9"
        summary, daily, primary = sf.build_summary(self.rows, self.calendar)
        self.assertAlmostEqual(primary["all"]["event_weighted"]["returns"]["mean"], .1)
        variant30 = next(item for item in summary["variants"] if item["group"] == "stock"
                         and item["variant"] == sf.variant_object(("0.02", "REBOUND", "30m", 10)))
        self.assertAlmostEqual(variant30["all"]["event_weighted"]["returns"]["mean"], .9)

    def test_duplicate_case_variant_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate case/variant"):
            sf.build_summary(self.rows + [self.rows[0]], self.calendar)

    def test_missing_variant_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing registered variants"):
            sf.build_summary(self.rows[:-1], self.calendar)

    def test_registry_missing_case_rejected(self):
        with self.assertRaisesRegex(ValueError, "frozen registry"):
            sf.build_summary(self.rows, self.calendar, ["fictional_case"])

    def test_spy_cannot_enter_stock_sample(self):
        self.rows[0]["symbol"] = "SPY"
        with self.assertRaisesRegex(ValueError, "primary targets"):
            sf.build_summary(self.rows, self.calendar)

    def test_runner_matched_excess_independently_checked(self):
        self.rows[0]["matched_excess"] = "0.9"
        with self.assertRaisesRegex(ValueError, "arithmetic mismatch"):
            sf.build_summary(self.rows, self.calendar)

    def test_self_control_rejected(self):
        self.rows[0]["paired_controls"][0]["symbol"] = "AAA"
        with self.assertRaisesRegex(ValueError, "own matched control"):
            sf.build_summary(self.rows, self.calendar)

    def test_no_signal_cannot_be_zero_return(self):
        self.rows[-1]["net_return"] = "0"
        with self.assertRaisesRegex(ValueError, "without a signal"):
            sf.build_summary(self.rows, self.calendar)

    def test_incomplete_pair_cannot_supply_excess(self):
        self.rows[0]["paired_control_status"] = "missing_return"
        with self.assertRaisesRegex(ValueError, "Incomplete pair"):
            sf.build_summary(self.rows, self.calendar)

    def test_unknown_date_rejected(self):
        self.rows[0]["date"] = "2026-01-07"
        with self.assertRaisesRegex(ValueError, "registered calendar"):
            sf.build_summary(self.rows, self.calendar)

    def test_null_missing_target_stays_out_of_means(self):
        for row in self.rows:
            if row["symbol"] == "CCC":
                row["net_return"] = row["matched_excess"] = None
                row["missing_stage"] = "exit"
                row["paired_control_status"] = "missing_return"
        summary, daily, primary = sf.build_summary(self.rows, self.calendar)
        self.assertEqual(primary["all"]["counts"]["signal_return_missing"], 1)
        self.assertEqual(primary["all"]["date_weighted"]["returns"]["n"], 1)
        self.assertAlmostEqual(primary["all"]["event_weighted"]["returns"]["mean"], .2)


if __name__ == "__main__":
    unittest.main()
