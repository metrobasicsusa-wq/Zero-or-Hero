#!/usr/bin/env python3
"""Generate pure binary arithmetic examples; reads no market observations."""
from fractions import Fraction
import json
from pathlib import Path

from return_distribution import render


def build_examples():
    examples = []
    for probability in (Fraction(1, 10), Fraction(2, 10), Fraction(3, 10), Fraction(4, 10)):
        profit = (1 - probability) / probability
        expectancy = probability * profit - (1 - probability)
        assert expectancy == 0
        examples.append({
            "classification": "HYPOTHETICAL_ARITHMETIC_NOT_MARKET_EVIDENCE",
            "win_probability": render(probability),
            "loss_per_losing_event_R": "1",
            "zero_probability": "0",
            "break_even_profit_multiple_of_one_R_loss": render(profit),
            "exact_break_even_profit_multiple_fraction": f"{profit.numerator}/{profit.denominator}",
            "total_payout_multiple_if_initial_capital_equals_one_R": render(profit + 1),
            "expected_profit_R_before_costs": render(expectancy),
        })
    return {
        "version": "hypothetical-binary-payoff-v1",
        "scope": "pure_binary_arithmetic_no_actual_option_prices_no_strategy_probabilities",
        "assumptions": {
            "loss_per_losing_event_R": "1", "zero_probability": "0", "costs": "omitted_not_zero_in_reality",
            "expectancy_formula": "p * profit_multiple - (1-p) * 1",
            "break_even_profit_multiple_formula": "(1-p)/p",
            "payout_convention": "profit excludes returned principal; total_payout=profit+1 only if initial_principal=1R",
        },
        "break_even_examples": examples,
        "positive_expectancy_example": {
            "classification": "HYPOTHETICAL_ARITHMETIC_NOT_MARKET_EVIDENCE",
            "win_probability": "0.3", "win_profit_R": "3", "loss_R": "1",
            "expected_profit_R_before_costs": render(Fraction(3, 10) * 3 - Fraction(7, 10)),
            "total_payout_R_on_win_if_initial_capital_equals_one_R": "4",
        },
        "higher_win_rate_negative_example": {
            "classification": "HYPOTHETICAL_ARITHMETIC_NOT_MARKET_EVIDENCE",
            "win_probability": "0.4", "win_profit_R": "1", "loss_R": "1",
            "expected_profit_R_before_costs": render(Fraction(4, 10) - Fraction(6, 10)),
        },
        "limitations": [
            "No empirical option data or transaction prices enter these examples.",
            "No 40-percent acceptance gate is imposed.",
            "Execution costs and varying tail losses can change the break-even point.",
            "No wealth path, repeated funding, portfolio survival, or 500-to-10000 probability is simulated.",
        ],
    }


if __name__ == "__main__":
    Path(__file__).with_name("HYPOTHETICAL_PAYOFF.json").write_text(
        json.dumps(build_examples(), ensure_ascii=False, indent=2, allow_nan=False) + "\n")
