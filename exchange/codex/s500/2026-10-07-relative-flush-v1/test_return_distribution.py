import json
import unittest
from decimal import Decimal
from fractions import Fraction

from return_distribution import compute_distribution, finite_decimal, render
from build_hypothetical_payoff import build_examples


def rows(values):
    return [{"case_id": f"case-{index:04d}", "net_return": str(value)}
            for index, value in enumerate(values)]


class DistributionTests(unittest.TestCase):
    def test_low_win_rate_can_have_positive_expectancy(self):
        result = compute_distribution(rows([3] * 3 + [-1] * 7))
        self.assertEqual(result["win_rate_all_n"], "0.3")
        self.assertEqual(result["mean"], "0.2")
        self.assertEqual(result["payoff_ratio_average_win_to_average_absolute_loss"], "3")
        self.assertEqual(result["break_even_win_rate_among_nonzero"], "0.25")
        self.assertTrue(result["expectancy_identity"]["exact_rational_identity_holds"])

    def test_win_rate_above_40_percent_can_have_negative_expectancy(self):
        result = compute_distribution(rows([1] * 6 + [-2] * 4))
        self.assertEqual(result["win_rate_all_n"], "0.6")
        self.assertEqual(result["mean"], "-0.2")

    def test_zero_in_all_n_but_not_conditional_break_even(self):
        result = compute_distribution(rows([3, -1, 0, 0]))
        self.assertEqual(result["win_rate_all_n"], "0.25")
        self.assertEqual(result["win_rate_among_nonzero"], "0.5")
        self.assertEqual(result["zero_count"], 2)
        self.assertEqual(result["mean"], "0.5")
        self.assertEqual(result["break_even_win_rate_among_nonzero"], "0.25")
        self.assertEqual(result["expectancy_identity"]["positive_term"], "0.75")
        self.assertEqual(result["expectancy_identity"]["negative_term"], "0.25")

    def test_all_positive_undefined_loss_ratios_are_null(self):
        result = compute_distribution(rows([1, 2, 3]))
        self.assertEqual(result["mean"], "2")
        self.assertIsNone(result["conditional_average_absolute_loss"])
        self.assertIsNone(result["profit_factor_total_positive_to_total_absolute_loss"])
        self.assertIsNone(result["payoff_ratio_average_win_to_average_absolute_loss"])
        self.assertIsNone(result["break_even_win_rate_among_nonzero"])
        self.assertTrue(result["expectancy_identity"]["exact_rational_identity_holds"])
        self.assertEqual(result["undefined_statistic_reasons"]["conditional_average_absolute_loss"], "no_negative_observations")

    def test_all_negative_profit_factor_zero_payoff_undefined(self):
        result = compute_distribution(rows([-1, -2, -3]))
        self.assertEqual(result["profit_factor_total_positive_to_total_absolute_loss"], "0")
        self.assertEqual(result["win_rate_all_n"], "0")
        self.assertIsNone(result["conditional_average_win"])
        self.assertIsNone(result["payoff_ratio_average_win_to_average_absolute_loss"])
        self.assertIsNone(result["break_even_win_rate_among_nonzero"])
        self.assertEqual(result["expectancy_identity"]["reconstructed_mean"], "-2")
        self.assertEqual(result["undefined_statistic_reasons"]["conditional_average_win"], "no_positive_observations")
        self.assertNotIn("profit_factor_total_positive_to_total_absolute_loss", result["undefined_statistic_reasons"])

    def test_all_zero_has_zero_mean_but_no_undefined_ratio(self):
        result = compute_distribution(rows([0, 0]))
        self.assertEqual(result["mean"], "0")
        self.assertEqual(result["zero_rate_all_n"], "1")
        self.assertEqual(result["win_rate_all_n"], "0")
        self.assertIsNone(result["win_rate_among_nonzero"])
        self.assertIsNone(result["break_even_win_rate_among_nonzero"])
        self.assertIsNone(result["profit_factor_total_positive_to_total_absolute_loss"])
        self.assertTrue(result["expectancy_identity"]["exact_rational_identity_holds"])

    def test_empty_outcomes_not_zero(self):
        result = compute_distribution([])
        self.assertEqual(result["n"], 0)
        for key in ("mean", "median", "win_rate_all_n", "conditional_average_win",
                    "conditional_average_absolute_loss", "break_even_win_rate_among_nonzero"):
            self.assertIsNone(result[key])
        self.assertIsNone(result["expectancy_identity"]["exact_rational_identity_holds"])
        self.assertEqual(result["undefined_statistic_reasons"]["mean"], "no_observations")

    def test_exact_identity_for_repeating_ratios(self):
        result = compute_distribution(rows([Decimal("0.07"), Decimal("0.11"), Decimal("-0.03")]))
        self.assertEqual(result["mean"], "0.05")
        self.assertEqual(result["expectancy_identity"]["reconstructed_mean"], "0.05")
        self.assertTrue(result["expectancy_identity"]["exact_rational_identity_holds"])

    def test_profit_factor_uses_sums_not_conditional_ratio(self):
        result = compute_distribution(rows([2, -1, -1, -1]))
        self.assertEqual(result["payoff_ratio_average_win_to_average_absolute_loss"], "2")
        self.assertEqual(result["profit_factor_total_positive_to_total_absolute_loss"], render(Fraction(2, 3)))

    def test_decimal_precision_not_binary_float_arithmetic(self):
        result = compute_distribution(rows(["0.1", "0.2", "-0.3"]))
        self.assertEqual(result["mean"], "0")
        self.assertTrue(result["expectancy_identity"]["exact_rational_identity_holds"])

    def test_empirical_quantiles_not_drawdown(self):
        result = compute_distribution(rows([-1, 0, 1]))
        self.assertEqual(result["quantiles"]["p05"], "-0.9")
        self.assertEqual(result["quantiles"]["p95"], "0.9")
        self.assertEqual(result["median"], "0")

    def test_even_median(self):
        result = compute_distribution(rows([-2, -1, 3, 4]))
        self.assertEqual(result["median"], "1")


class TailTests(unittest.TestCase):
    def test_top_count_based_on_all_n_not_positive_n(self):
        result = compute_distribution(rows([10, 5] + [-1] * 99))
        one = result["top_positive_tails"]["top_1_percent_of_all_n"]
        five = result["top_positive_tails"]["top_5_percent_of_all_n"]
        self.assertEqual(one["requested_count_ceiling_fraction_times_all_n"], 2)
        self.assertEqual(one["actual_removed_count"], 2)
        self.assertEqual(five["requested_count_ceiling_fraction_times_all_n"], 6)
        self.assertEqual(five["actual_removed_count"], 2)
        self.assertEqual(five["remaining_count"], 99)
        self.assertEqual(five["mean_without_selected_observations"], "-1")
        self.assertEqual(five["original_n"], 101)

    def test_positive_outlier_contribution_and_main_mean_preserved(self):
        result = compute_distribution(rows([100] + [-1] * 99))
        tail = result["top_positive_tails"]["top_1_percent_of_all_n"]
        self.assertEqual(result["mean"], "0.01")
        self.assertEqual(tail["contribution_to_original_mean"], "1")
        self.assertEqual(tail["share_of_total_positive_return"], "1")
        self.assertEqual(tail["mean_without_selected_observations"], "-1")
        self.assertEqual(result["n"], 100)

    def test_tie_uses_case_id_and_is_order_invariant(self):
        records = [{"case_id": "z", "net_return": "2"}, {"case_id": "a", "net_return": "2"},
                   {"case_id": "m", "net_return": "-1"}]
        a = compute_distribution(records)
        b = compute_distribution(reversed(records))
        self.assertEqual(a, b)
        self.assertEqual(a["top_positive_tails"]["top_1_percent_of_all_n"]["selected_case_ids"], ["a"])

    def test_positive_tail_does_not_remove_zeros_or_losses(self):
        result = compute_distribution(rows([0, -1, -3]))
        tail = result["top_positive_tails"]["top_5_percent_of_all_n"]
        self.assertEqual(tail["actual_removed_count"], 0)
        self.assertEqual(tail["contribution_to_original_mean"], "0")
        self.assertIsNone(tail["share_of_total_positive_return"])
        self.assertEqual(tail["mean_without_selected_observations"], result["mean"])

    def test_worst_loss_tail_retained_and_described(self):
        result = compute_distribution(rows([-100] + [1] * 99))
        tail = result["worst_negative_tails"]["worst_1_percent_of_all_n"]
        self.assertEqual(result["mean"], "-0.01")
        self.assertEqual(tail["selected_return_sum"], "-100")
        self.assertEqual(tail["contribution_to_original_mean"], "-1")
        self.assertEqual(tail["share_of_total_absolute_loss"], "1")
        self.assertEqual(tail["mean_without_selected_observations"], "1")

    def test_removing_only_positive_observation_leaves_null_mean(self):
        result = compute_distribution(rows([5]))
        tail = result["top_positive_tails"]["top_1_percent_of_all_n"]
        self.assertEqual(tail["remaining_count"], 0)
        self.assertIsNone(tail["mean_without_selected_observations"])


class InputTests(unittest.TestCase):
    def test_invalid_values_rejected(self):
        for value in (None, True, .1, "NaN", "Infinity", "-Infinity", "bad"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                finite_decimal(value)

    def test_missing_case_id_rejected(self):
        for case_id in (None, "", 7):
            with self.subTest(case_id=case_id), self.assertRaises(ValueError):
                compute_distribution([{"case_id": case_id, "net_return": "1"}])

    def test_duplicate_case_id_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            compute_distribution([{"case_id": "same", "net_return": "1"},
                                  {"case_id": "same", "net_return": "-1"}])

    def test_missing_return_not_zero(self):
        with self.assertRaises(ValueError):
            compute_distribution([{"case_id": "x", "net_return": None}])

    def test_json_has_no_nonfinite_numbers(self):
        for values in ([], [0], [1], [-1], [1, 0, -1]):
            result = compute_distribution(rows(values))
            json.dumps(result, allow_nan=False)


class HypotheticalExamplesTests(unittest.TestCase):
    def test_registered_probabilities(self):
        data = build_examples()
        self.assertEqual([row["win_probability"] for row in data["break_even_examples"]], ["0.1", "0.2", "0.3", "0.4"])

    def test_each_break_even_exact_fraction_identity(self):
        for row in build_examples()["break_even_examples"]:
            probability = Fraction(Decimal(row["win_probability"]))
            profit = Fraction(row["exact_break_even_profit_multiple_fraction"])
            self.assertEqual(probability * profit - (1 - probability), 0)
            self.assertEqual(row["expected_profit_R_before_costs"], "0")

    def test_profit_multiple_is_distinct_from_payout(self):
        example = build_examples()["positive_expectancy_example"]
        self.assertEqual(example["win_profit_R"], "3")
        self.assertEqual(example["total_payout_R_on_win_if_initial_capital_equals_one_R"], "4")
        self.assertEqual(example["expected_profit_R_before_costs"], "0.2")

    def test_40_percent_not_a_profit_guarantee(self):
        self.assertEqual(build_examples()["higher_win_rate_negative_example"]["expected_profit_R_before_costs"], "-0.2")


if __name__ == "__main__":
    unittest.main()
