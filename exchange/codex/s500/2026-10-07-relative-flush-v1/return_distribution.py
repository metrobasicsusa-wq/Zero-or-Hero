#!/usr/bin/env python3
"""Pure realized-sample distribution diagnostics, with no strategy win-rate gate.

Input rows contain stable ``case_id`` and finite Decimal/string ``net_return``.
All ratios and the expectancy identity are computed as exact rational numbers.
JSON output renders noninteger statistics as decimal strings with 50 significant
digits. Empty/undefined ratios are null, never NaN or infinity. No network I/O.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction
import math
from typing import Iterable

OUTPUT_PRECISION = 50


def finite_decimal(value):
    if isinstance(value, bool) or not isinstance(value, (Decimal, str, int)):
        raise ValueError("Return must be a Decimal, decimal string, or integer; no floats")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Malformed decimal return") from exc
    if not result.is_finite():
        raise ValueError("Return must be finite")
    return result


def render(value):
    """Render an exact fraction without emitting floats or nonfinite values."""
    if value is None:
        return None
    value = Fraction(value)
    with localcontext() as context:
        context.prec = OUTPUT_PRECISION
        decimal_value = Decimal(value.numerator) / Decimal(value.denominator)
    result = format(decimal_value, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return "0" if result in ("", "-0") else result


def average(values):
    return sum(values, Fraction()) / len(values) if values else None


def quantile(sorted_values, probability):
    """Linear interpolation at (n-1)*p over all observed returns."""
    if not sorted_values:
        return None
    position = (len(sorted_values) - 1) * probability
    lower = position.numerator // position.denominator
    upper = math.ceil(position)
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (position - lower)


def tail_summary(ordered, n, total_return, side_total, percentage, side):
    requested_count = math.ceil(n * percentage)
    selected = ordered[:requested_count]
    selected_sum = sum((value for case_id, value in selected), Fraction())
    actual_count = len(selected)
    remaining_count = n - actual_count
    return {
        "tail": side,
        "fraction_of_all_n_for_requested_count": render(percentage),
        "requested_count_ceiling_fraction_times_all_n": requested_count,
        "original_n": n,
        "actual_removed_count": actual_count,
        "selected_case_ids": [case_id for case_id, value in selected],
        "selected_return_sum": render(selected_sum),
        "contribution_to_original_mean": render(selected_sum / n) if n else None,
        "share_of_total_positive_return" if side == "positive" else "share_of_total_absolute_loss":
            render(abs(selected_sum) / side_total) if side_total else None,
        "remaining_count": remaining_count,
        "mean_without_selected_observations": render((total_return - selected_sum) / remaining_count)
            if remaining_count else None,
        "interpretation": "retrospective_observed_sample_concentration_not_an_actionable_filter_or_target",
    }


def compute_distribution(records: Iterable[dict]):
    """Summarize exactly the supplied nonmissing observations, without filtering.

    Missing outcomes belong in an external coverage ledger. Passing None here is
    an error so that callers cannot silently turn missing outcomes into zeros.
    Duplicate case IDs are errors; rank ties use the stable case ID ascending.
    """
    parsed, seen = [], set()
    for row in records:
        case_id = row.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("A nonempty stable string case_id is required")
        if case_id in seen:
            raise ValueError("Duplicate case_id in distribution")
        seen.add(case_id)
        parsed.append((case_id, Fraction(finite_decimal(row["net_return"]))))
    n = len(parsed)
    positives = [(case_id, value) for case_id, value in parsed if value > 0]
    negatives = [(case_id, value) for case_id, value in parsed if value < 0]
    n_positive, n_negative = len(positives), len(negatives)
    n_zero = n - n_positive - n_negative
    positive_sum = sum((value for case_id, value in positives), Fraction())
    absolute_loss_sum = sum((-value for case_id, value in negatives), Fraction())
    total_return = positive_sum - absolute_loss_sum
    values = sorted(value for case_id, value in parsed)
    mean = total_return / n if n else None
    average_win = positive_sum / n_positive if n_positive else None
    average_loss = absolute_loss_sum / n_negative if n_negative else None
    p_win = Fraction(n_positive, n) if n else None
    p_loss = Fraction(n_negative, n) if n else None
    positive_term = positive_sum / n if n else None
    negative_term = absolute_loss_sum / n if n else None
    identity_result = positive_term - negative_term if n else None
    identity_equal = identity_result == mean if n else None
    if n:
        explicit_identity = (p_win * average_win if average_win is not None else Fraction()) - (
            p_loss * average_loss if average_loss is not None else Fraction())
        assert explicit_identity == mean
    positive_order = sorted(positives, key=lambda item: (-item[1], item[0]))
    negative_order = sorted(negatives, key=lambda item: (item[1], item[0]))
    undefined_reasons = {}
    if not n:
        for name in ("win_rate_all_n", "loss_rate_all_n", "zero_rate_all_n", "mean", "median", "minimum", "maximum"):
            undefined_reasons[name] = "no_observations"
    if not n_positive + n_negative:
        undefined_reasons["win_rate_among_nonzero"] = "no_nonzero_observations"
    if not n_positive:
        undefined_reasons["conditional_average_win"] = "no_positive_observations"
    if not n_negative:
        undefined_reasons["conditional_average_absolute_loss"] = "no_negative_observations"
        undefined_reasons["profit_factor_total_positive_to_total_absolute_loss"] = "zero_total_absolute_loss_denominator"
    if not n_positive or not n_negative:
        reason = "both_positive_and_negative_observations_required_to_estimate_conditional_magnitudes"
        undefined_reasons["payoff_ratio_average_win_to_average_absolute_loss"] = reason
        undefined_reasons["break_even_win_rate_among_nonzero"] = reason
    return {
        "method_version": "return-distribution-v1",
        "return_unit": "same_as_input; stock_return_fraction_when_used_with_stock_event_ledger",
        "n": n,
        "positive_count": n_positive,
        "negative_count": n_negative,
        "zero_count": n_zero,
        "win_rate_all_n": render(p_win),
        "loss_rate_all_n": render(p_loss),
        "zero_rate_all_n": render(Fraction(n_zero, n)) if n else None,
        "nonzero_count": n_positive + n_negative,
        "win_rate_among_nonzero": render(Fraction(n_positive, n_positive + n_negative))
            if n_positive + n_negative else None,
        "mean": render(mean),
        "median": render(quantile(values, Fraction(1, 2))),
        "minimum": render(values[0]) if n else None,
        "maximum": render(values[-1]) if n else None,
        "conditional_average_win": render(average_win),
        "conditional_average_absolute_loss": render(average_loss),
        "payoff_ratio_average_win_to_average_absolute_loss": render(average_win / average_loss)
            if average_win is not None and average_loss is not None else None,
        "profit_factor_total_positive_to_total_absolute_loss": render(positive_sum / absolute_loss_sum)
            if absolute_loss_sum else None,
        "total_positive_return": render(positive_sum),
        "total_absolute_loss": render(absolute_loss_sum),
        "expectancy_identity": {
            "formula": "p_win_all_n * conditional_average_win - p_loss_all_n * conditional_average_absolute_loss = mean",
            "empty_side_convention": "an_empty_side_contributes_zero_while_its_conditional_average_remains_null",
            "positive_term": render(positive_term),
            "negative_term": render(negative_term),
            "reconstructed_mean": render(identity_result),
            "mean": render(mean),
            "exact_rational_identity_holds": identity_equal,
            "verification_arithmetic": "exact_Fraction_from_finite_Decimal_inputs_before_50_digit_display_rounding",
        },
        "break_even_win_rate_among_nonzero": render(average_loss / (average_win + average_loss))
            if average_win is not None and average_loss is not None else None,
        "break_even_scope": "conditional_nonzero; observed_win_and_loss_magnitudes_held_fixed; zeros_excluded",
        "undefined_statistic_reasons": undefined_reasons,
        "quantiles": {
            "method": "linear_interpolation_at_(n_minus_1)_times_probability",
            "p05": render(quantile(values, Fraction(5, 100))),
            "p95": render(quantile(values, Fraction(95, 100))),
            "interpretation": "empirical_single_event_return_quantiles_not_account_drawdown",
        },
        "top_positive_tails": {
            "top_1_percent_of_all_n": tail_summary(positive_order, n, total_return, positive_sum, Fraction(1, 100), "positive"),
            "top_5_percent_of_all_n": tail_summary(positive_order, n, total_return, positive_sum, Fraction(5, 100), "positive"),
        },
        "worst_negative_tails": {
            "worst_1_percent_of_all_n": tail_summary(negative_order, n, total_return, absolute_loss_sum, Fraction(1, 100), "negative"),
            "worst_5_percent_of_all_n": tail_summary(negative_order, n, total_return, absolute_loss_sum, Fraction(5, 100), "negative"),
        },
        "interpretation": {
            "win_rate_gate": "none; no_arbitrary_40_percent_rule",
            "observations": "all_supplied_observed_returns_retained; no_tail_removal_from_main_mean",
            "tail_sensitivity": "retrospective_description_only; cannot_identify_winners_or_losers_in_advance",
            "undefined_ratios": "null; no_infinity_or_NaN; all_positive_sample_does_not_prove_zero_loss_risk",
            "capital": "no_equity_curve_compounding_funding_options_or_500_to_10000_inference",
        },
    }
