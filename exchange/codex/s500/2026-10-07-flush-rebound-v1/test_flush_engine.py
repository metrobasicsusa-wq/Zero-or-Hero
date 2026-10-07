"""Synthetic economic-boundary tests; no market data or credentials."""

import copy
from decimal import Decimal, localcontext
import unittest

from flush_engine import evaluate_day, evaluate_prefix, evaluate_window, family_decision, prepare_bars


def bars(count=390, price="100"):
    return {t: {"t": t, "o": price, "h": price, "l": price, "c": price, "v": "1"} for t in range(count)}


def close_at(data, minute, price):
    row = data[minute]
    row["c"] = price
    row["l"] = str(min(Decimal(row["o"]), Decimal(price)))
    row["h"] = str(max(Decimal(row["o"]), Decimal(price)))


def flush_bars():
    data = bars()
    for t in range(1, 390):
        data[t].update(o="97", h="97", l="97", c="97")
    return data


class FlushEngineTests(unittest.TestCase):
    def test_exact_two_percent_flush(self):
        data = bars()
        close_at(data, 29, "98")
        self.assertTrue(evaluate_prefix(data, "0.02")["is_flush"])
        self.assertFalse(evaluate_prefix(data, "0.03")["is_flush"])

    def test_one_digit_above_threshold_does_not_flush(self):
        data = bars()
        close_at(data, 29, "98.00000000000001")
        self.assertFalse(evaluate_prefix(data, "0.02")["is_flush"])

    def test_low_is_not_close(self):
        data = bars()
        data[1]["l"] = "50"
        self.assertFalse(evaluate_prefix(data, "0.02")["is_flush"])

    def test_opening_anchor_not_first_close(self):
        data = bars()
        close_at(data, 0, "98")
        result = evaluate_prefix(data, "0.02")
        self.assertEqual(result["opening_open"], "100")
        self.assertTrue(result["is_flush"])

    def test_minimum_after_prefix_is_ignored(self):
        data = bars()
        close_at(data, 30, "20")
        self.assertFalse(evaluate_prefix(data, "0.02")["is_flush"])

    def test_missing_first_bar_cannot_reanchor(self):
        data = flush_bars()
        del data[0]
        result = evaluate_prefix(data, ".02")
        self.assertEqual(result["status"], "unknown_prefix")
        self.assertEqual(result["coverage"]["missing_minutes"], [0])

    def test_missing_midprefix_is_unknown(self):
        data = flush_bars()
        del data[15]
        self.assertIsNone(evaluate_prefix(data, ".02")["is_flush"])

    def test_fixed_entry_has_full_minute_delay(self):
        result = family_decision(flush_bars(), ".02", "FIXED")
        self.assertEqual((result["signal_minute"], result["decision_minute"], result["entry_minute"]), (29, 30, 31))

    def test_rebound_exact_boundary_first_possible(self):
        data = flush_bars()
        close_at(data, 30, "97.97")
        result = family_decision(data, ".03", "REBOUND")
        self.assertEqual((result["signal_minute"], result["entry_minute"]), (30, 32))
        self.assertEqual(result["prior_running_min_close"], "97")

    def test_rebound_below_boundary_does_not_trigger(self):
        data = flush_bars()
        close_at(data, 30, "97.969999999999999")
        self.assertEqual(family_decision(data, ".02", "REBOUND")["status"], "valid_no_rebound")

    def test_new_minimum_not_same_bar_rebound(self):
        data = flush_bars()
        close_at(data, 30, "96")
        close_at(data, 31, "96.96")
        self.assertEqual(family_decision(data, ".02", "REBOUND")["signal_minute"], 31)

    def test_future_minimum_does_not_move_signal(self):
        data = flush_bars()
        close_at(data, 35, "98")
        before = family_decision(data, ".02", "REBOUND")
        close_at(data, 300, "1")
        data[200]["c"] = "NaN"
        self.assertEqual(before, family_decision(data, ".02", "REBOUND"))

    def test_first_signal_is_retained(self):
        data = flush_bars()
        close_at(data, 33, "98")
        close_at(data, 45, "105")
        self.assertEqual(family_decision(data, ".02", "REBOUND")["signal_minute"], 33)

    def test_missing_before_rebound_not_skipped(self):
        data = flush_bars()
        del data[30]
        close_at(data, 31, "99")
        result = family_decision(data, ".02", "REBOUND")
        self.assertEqual(result["status"], "unknown_rebound_prefix")
        self.assertEqual(result["unknown_minute"], 30)

    def test_missing_after_signal_does_not_revoke(self):
        data = flush_bars()
        close_at(data, 30, "98")
        del data[31]
        self.assertEqual(family_decision(data, ".02", "REBOUND")["status"], "signal")

    def test_last_scan_minute_inclusive(self):
        data = flush_bars()
        close_at(data, 89, "98")
        result = family_decision(data, ".02", "REBOUND")
        self.assertEqual((result["signal_minute"], result["entry_minute"]), (89, 91))

    def test_minute_ninety_excluded(self):
        data = flush_bars()
        close_at(data, 90, "98")
        self.assertEqual(family_decision(data, ".02", "REBOUND")["status"], "valid_no_rebound")

    def test_no_flush_cannot_trigger_rebound(self):
        data = bars()
        close_at(data, 30, "105")
        self.assertEqual(family_decision(data, ".02", "REBOUND")["status"], "no_flush")

    def test_cost_formula_both_sides(self):
        data = bars()
        data[60].update(o="110", h="110", l="110", c="110")
        result = evaluate_window(data, 30, 60, 10)
        with localcontext() as context:
            context.prec = 42
            expected = Decimal(110) * Decimal(".999") / (Decimal(100) * Decimal("1.001")) - 1
        self.assertEqual(Decimal(result["net_return"]), expected)
        self.assertEqual(Decimal(result["gross_return"]), Decimal(".1"))
        self.assertFalse(result["verified_fill"])

    def test_flat_price_loses_cost(self):
        result = evaluate_window(bars(), 30, 60, 10)
        self.assertLess(Decimal(result["net_return"]), Decimal(0))

    def test_higher_cost_monotonically_reduces_return(self):
        values = [Decimal(evaluate_window(bars(), 30, 60, c)["net_return"]) for c in (5, 10, 25)]
        self.assertGreater(values[0], values[1])
        self.assertGreater(values[1], values[2])

    def test_zero_cost_equals_gross(self):
        result = evaluate_window(bars(), 30, 60, 0)
        self.assertEqual(result["net_return"], result["gross_return"])

    def test_entry_exit_and_interior_must_exist(self):
        for missing in (30, 45, 60):
            with self.subTest(missing=missing):
                data = bars()
                del data[missing]
                result = evaluate_window(data, 30, 60, 10)
                self.assertEqual(result["status"], "unknown_window")
                self.assertIsNone(result["net_return"])

    def test_outside_window_missing_does_not_affect_return(self):
        data = bars()
        before = evaluate_window(data, 30, 60, 10)
        del data[10]
        del data[61]
        self.assertEqual(before, evaluate_window(data, 30, 60, 10))

    def test_invalid_windows(self):
        for entry, exit_ in ((30, 30), (60, 30), (-1, 30), (30, 390), (True, 31), (30, None)):
            self.assertEqual(evaluate_window(bars(), entry, exit_, 10)["status"], "invalid_window")

    def test_early_close_horizon(self):
        data = flush_bars()
        rows = evaluate_day(data, session_minutes=210)
        fixed = [row for row in rows if row["family"] == "FIXED" and row["horizon"] == "close_minus_10m"]
        self.assertEqual(len(fixed), 6)
        self.assertTrue(all(row["exit_minute"] == 200 for row in fixed))

    def test_complete_grid_and_nonsignals_retained(self):
        rows = evaluate_day(bars())
        self.assertEqual(len(rows), 36)
        self.assertEqual(len({(r["threshold"], r["family"], r["horizon"], r["per_side_cost_bps"]) for r in rows}), 36)
        self.assertTrue(all(r["status"] == "no_flush" and r["net_return"] is None for r in rows))

    def test_missing_source_all_rows_retained(self):
        rows = evaluate_day(flush_bars(), source_complete=False)
        self.assertEqual(len(rows), 36)
        self.assertTrue(all(r["status"] == "unknown_source" for r in rows))

    def test_source_complete_requires_literal_true(self):
        for flag in ("true", 1, None, False):
            self.assertEqual(evaluate_prefix(bars(), ".02", source_complete=flag)["status"], "unknown_source")

    def test_prepared_false_cannot_be_reset(self):
        prepared = prepare_bars(bars(), source_complete=False)
        self.assertEqual(evaluate_window(prepared, 30, 60, 5)["status"], "unknown_source")

    def test_numeric_invalid_fields(self):
        for field, value in (("o", "NaN"), ("h", "Infinity"), ("l", "0"), ("c", True), ("v", "-1"), ("v", None)):
            with self.subTest(field=field, value=value):
                data = bars()
                data[2][field] = value
                self.assertEqual(evaluate_prefix(data, ".02")["status"], "unknown_prefix")

    def test_ohlc_relation_invalid(self):
        data = bars()
        data[2]["h"] = "99"
        self.assertEqual(evaluate_prefix(data, ".02")["status"], "unknown_prefix")

    def test_zero_volume_is_valid(self):
        data = bars()
        data[2]["v"] = "0"
        self.assertEqual(evaluate_prefix(data, ".02")["status"], "no_flush")

    def test_duplicate_minute_invalid_no_last_write_wins(self):
        data = list(bars().values())
        data.append(dict(data[1]))
        self.assertEqual(evaluate_prefix(data, ".02")["status"], "unknown_prefix")

    def test_duplicate_later_minute_not_retroactive(self):
        data = list(bars().values())
        data.append(dict(data[100]))
        self.assertEqual(evaluate_prefix(data, ".02")["status"], "no_flush")

    def test_unlocatable_minute_unknown_source(self):
        data = list(bars().values())
        data.append({"t": "300", "o": 100, "h": 100, "l": 100, "c": 100, "v": 1})
        self.assertEqual(evaluate_prefix(data, ".02")["status"], "unknown_source")

    def test_no_input_mutation(self):
        data = flush_bars()
        before = copy.deepcopy(data)
        evaluate_day(data)
        self.assertEqual(before, data)

    def test_invalid_parameters_raise(self):
        for value in (0, 1, "NaN", True):
            with self.assertRaises(ValueError):
                evaluate_prefix(bars(), value)
        for value in (-1, 10000, "Infinity", True):
            with self.assertRaises(ValueError):
                evaluate_window(bars(), 30, 60, value)
        with self.assertRaises(ValueError):
            family_decision(bars(), ".02", "BAD")
        with self.assertRaises(ValueError):
            family_decision(bars(), ".02", "FIXED", session_minutes=True)


if __name__ == "__main__":
    unittest.main()
