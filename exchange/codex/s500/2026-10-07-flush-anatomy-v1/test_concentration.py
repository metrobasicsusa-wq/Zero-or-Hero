from decimal import Decimal
from fractions import Fraction
import json
import unittest

from concentration import summarize_concentration, parse_number, render


def record(case, day, symbol, net, excess):
    return {"case_id": case, "date": day, "symbol": symbol,
            "net_return": net, "matched_excess": excess}


def find(items, entity):
    return next(item for item in items if item["entity"] == entity)


def excluded(items, entity):
    return next(item for item in items if item["removed_entity"] == entity)


class ContributionTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            record("a1", "2026-01-02", "A", "0.1", "0.02"),
            record("b1", "2026-01-02", "B", "-0.1", None),
            record("c1", "2026-01-02", "C", None, None),
            record("a2", "2026-01-05", "A", "0.3", "0.04"),
            record("b3", "2026-01-06", "B", "0", None),
            record("c4", "2026-01-07", "C", None, None),
        ]

    def test_full_signal_and_separate_denominators(self):
        result = summarize_concentration(self.records)
        self.assertEqual(result["signal_count"], 6)
        net, excess = result["overall"]["net_return"], result["overall"]["matched_excess"]
        self.assertEqual((net["complete_count"], net["missing_count"], net["observed_date_count"]), (4, 2, 3))
        self.assertEqual((excess["complete_count"], excess["missing_count"], excess["observed_date_count"]), (2, 4, 2))
        self.assertEqual(net["event_mean"], "0.075")
        self.assertEqual(net["date_equal_mean"], "0.1")
        self.assertEqual(excess["event_mean"], "0.03")
        self.assertEqual(excess["date_equal_mean"], "0.03")

    def test_every_partition_exactly_adds_back_both_means(self):
        result = summarize_concentration(self.records)
        for axis in ("date", "symbol", "date_symbol"):
            for metric in ("net_return", "matched_excess"):
                for weight in ("pooled", "date_equal"):
                    check = result["contribution_identity_checks"][axis][metric][weight]
                    self.assertTrue(check["exact_fraction_identity_holds"])
                    self.assertEqual(check["sum_of_entity_contributions"], check["original_mean"])

    def test_symbol_date_equal_uses_global_date_counts(self):
        result = summarize_concentration(self.records)
        a = find(result["by_symbol"], "A")["metrics"]["net_return"]
        b = find(result["by_symbol"], "B")["metrics"]["net_return"]
        self.assertEqual(a["contribution_to_original_pooled_mean"], "0.1")
        self.assertEqual(b["contribution_to_original_pooled_mean"], "-0.025")
        self.assertEqual(a["contribution_to_original_date_equal_mean"], render(Fraction(7, 60)))
        self.assertEqual(b["contribution_to_original_date_equal_mean"], render(Fraction(-1, 60)))
        self.assertNotEqual(a["date_equal_mean"], a["contribution_to_original_date_equal_mean"])

    def test_missing_only_date_retained_but_not_zero_return_day(self):
        result = summarize_concentration(self.records)
        day = find(result["by_date"], "2026-01-07")
        self.assertEqual(day["signals"], 1)
        self.assertEqual(day["metrics"]["net_return"]["complete_count"], 0)
        self.assertIsNone(day["metrics"]["net_return"]["event_mean"])
        self.assertEqual(day["metrics"]["net_return"]["contribution_to_original_pooled_mean"], "0")
        self.assertNotIn("2026-01-07", result["overall"]["net_return"]["observed_dates"])

    def test_actual_zero_return_counts_as_observed_date(self):
        result = summarize_concentration(self.records)
        self.assertIn("2026-01-06", result["overall"]["net_return"]["observed_dates"])
        self.assertEqual(result["overall"]["net_return"]["zero_count"], 1)

    def test_observed_matrix_retains_missing_cells_without_fabrication(self):
        result = summarize_concentration(self.records)
        cells = result["date_symbol_matrix_observed_cells"]
        self.assertEqual(len(cells), 6)
        self.assertEqual(sum(cell["signals"] for cell in cells), 6)
        missing = find(cells, ["2026-01-07", "C"])
        self.assertEqual(missing["metrics"]["net_return"]["missing_count"], 1)
        self.assertIsNone(missing["metrics"]["net_return"]["event_mean"])

    def test_order_invariant(self):
        self.assertEqual(summarize_concentration(self.records), summarize_concentration(reversed(self.records)))


class LeaveOneOutTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            record("a1", "2026-01-02", "A", "0.1", "0.01"),
            record("b1", "2026-01-02", "B", "-0.1", None),
            record("a2", "2026-01-05", "A", "0.3", "0.03"),
            record("b2", "2026-01-05", "B", None, None),
            record("c3", "2026-01-06", "C", None, None),
        ]

    def test_leave_symbol_recomputes_day_mean_instead_of_subtracting_contribution(self):
        result = summarize_concentration(self.records)
        dropped = excluded(result["leave_one_symbol_out"], "A")
        net = dropped["metrics"]["net_return"]
        self.assertEqual(dropped["remaining_signal_count"], 3)
        self.assertEqual(net["complete_count"], 1)
        self.assertEqual(net["observed_date_count"], 1)
        self.assertEqual(net["event_mean"], "-0.1")
        self.assertEqual(net["date_equal_mean"], "-0.1")
        self.assertEqual(net["lost_observed_dates"], ["2026-01-05"])
        self.assertEqual(dropped["lost_signal_dates"], [])

    def test_leave_symbol_different_metric_lost_dates(self):
        result = summarize_concentration(self.records)
        dropped = excluded(result["leave_one_symbol_out"], "A")
        self.assertEqual(dropped["metrics"]["matched_excess"]["lost_observed_dates"], ["2026-01-02", "2026-01-05"])
        self.assertIsNone(dropped["metrics"]["matched_excess"]["date_equal_mean"])

    def test_leave_date_drops_positive_negative_and_missing_together(self):
        result = summarize_concentration(self.records)
        dropped = excluded(result["leave_one_date_out"], "2026-01-02")
        self.assertEqual(dropped["removed_signal_count"], 2)
        self.assertEqual(dropped["metrics"]["net_return"]["removed_event_sum"], "0")
        self.assertEqual(dropped["metrics"]["net_return"]["event_mean"], "0.3")
        self.assertEqual(dropped["metrics"]["net_return"]["date_equal_mean"], "0.3")

    def test_drop_missing_only_entity_does_not_change_returns(self):
        result = summarize_concentration(self.records)
        dropped = excluded(result["leave_one_symbol_out"], "C")
        self.assertEqual(dropped["removed_signal_count"], 1)
        self.assertEqual(dropped["metrics"]["net_return"]["removed_complete_count"], 0)
        self.assertEqual(dropped["metrics"]["net_return"]["event_mean"], result["overall"]["net_return"]["event_mean"])
        self.assertEqual(dropped["lost_signal_dates"], ["2026-01-06"])
        self.assertEqual(dropped["metrics"]["net_return"]["lost_observed_dates"], [])

    def test_single_entity_leaves_no_samples_not_zero(self):
        result = summarize_concentration([record("one", "2026-01-02", "A", "-1", "0")])
        for field in ("leave_one_date_out", "leave_one_symbol_out"):
            dropped = result[field][0]
            self.assertEqual(dropped["remaining_signal_count"], 0)
            for metric in ("net_return", "matched_excess"):
                self.assertEqual(dropped["metrics"][metric]["complete_count"], 0)
                self.assertIsNone(dropped["metrics"][metric]["event_mean"])
                self.assertIsNone(dropped["metrics"][metric]["date_equal_mean"])

    def test_all_entities_including_negative_and_missing_receive_leave_out(self):
        result = summarize_concentration(self.records)
        self.assertEqual([x["removed_entity"] for x in result["leave_one_symbol_out"]], ["A", "B", "C"])
        self.assertEqual(len(result["leave_one_date_out"]), 3)
        self.assertEqual(result["signal_count"], 5)


class RankingTests(unittest.TestCase):
    def test_negative_positive_zero_and_missing_separate(self):
        records = [record(str(i), "2026-01-02", symbol, value, None)
                   for i, (symbol, value) in enumerate([("A", "-3"), ("B", "2"), ("C", "0"), ("D", None)])]
        result = summarize_concentration(records)
        rank = result["rankings"]["symbol"]["metrics"]["net_return"]["pooled"]
        self.assertEqual(rank["positive_contributors_descending"], ["B"])
        self.assertEqual(rank["negative_contributors_ascending"], ["A"])
        self.assertEqual(rank["zero_contributors"], ["C"])
        self.assertEqual(rank["no_complete_outcome_entities"], ["D"])

    def test_ties_use_stable_entity_identifier(self):
        records = [record("b", "2026-01-02", "B", "1", None), record("a", "2026-01-02", "A", "1", None)]
        result = summarize_concentration(records)
        self.assertEqual(result["rankings"]["symbol"]["metrics"]["net_return"]["pooled"]["positive_contributors_descending"], ["A", "B"])

    def test_ranking_uses_exact_values_not_display_rounding(self):
        # Distinction beyond 50 displayed significant digits must not become a tie.
        tiny_larger = "1." + "0" * 60 + "1"
        records = [record("a", "2026-01-02", "A", "1", None), record("b", "2026-01-02", "B", tiny_larger, None)]
        result = summarize_concentration(records)
        self.assertEqual(result["rankings"]["symbol"]["metrics"]["net_return"]["pooled"]["positive_contributors_descending"], ["B", "A"])


class ValidationTests(unittest.TestCase):
    def test_empty(self):
        result = summarize_concentration([])
        self.assertEqual(result["signal_count"], 0)
        self.assertIsNone(result["overall"]["net_return"]["event_mean"])
        self.assertEqual(result["leave_one_symbol_out"], [])
        self.assertEqual(result["by_date"], [])
        json.dumps(result, allow_nan=False)

    def test_all_missing(self):
        result = summarize_concentration([record("a", "2026-01-02", "A", None, None)])
        self.assertIsNone(result["by_symbol"][0]["metrics"]["net_return"]["contribution_to_original_pooled_mean"])
        self.assertIsNone(result["contribution_identity_checks"]["symbol"]["net_return"]["pooled"]["exact_fraction_identity_holds"])

    def test_nonfinite_boolean_and_float_rejected(self):
        for value in ("NaN", "Infinity", "-Infinity", True, .1, "bad"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_number(value)

    def test_duplicate_case_rejected(self):
        row = record("same", "2026-01-02", "A", "1", None)
        with self.assertRaises(ValueError):
            summarize_concentration([row, row])

    def test_invalid_date_rejected(self):
        for day in ("20260102", "2026-1-2", "2026-02-30", None):
            with self.subTest(day=day), self.assertRaises(ValueError):
                summarize_concentration([record("x", day, "A", "1", None)])

    def test_invalid_symbol_rejected(self):
        with self.assertRaises(ValueError):
            summarize_concentration([record("x", "2026-01-02", "", "1", None)])

    def test_decimal_exact_before_display(self):
        result = summarize_concentration([record("a", "2026-01-02", "A", Decimal("0.1"), "0.1"),
                                          record("b", "2026-01-02", "B", Decimal("0.2"), "0.2"),
                                          record("c", "2026-01-02", "C", Decimal("-0.3"), "-0.3")])
        self.assertEqual(result["overall"]["net_return"]["event_sum"], "0")
        self.assertEqual(result["overall"]["net_return"]["event_mean"], "0")


if __name__ == "__main__":
    unittest.main()
