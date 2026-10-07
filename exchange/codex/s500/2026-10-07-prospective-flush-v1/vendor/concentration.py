"""Pure event concentration and exhaustive leave-one-entity-out diagnostics.

API: summarize_concentration(records), where each record has stable case_id,
ISO date, symbol, and net_return/matched_excess as finite Decimal/string/int or
None. Every input row is a signal, including missing outcomes. No market reads,
bootstrap, parameter selection, equity curve, or portfolio simulation occurs.
"""
from collections import defaultdict
from datetime import date as Date
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction

METRICS = ("net_return", "matched_excess")
DISPLAY_PRECISION = 50


def parse_number(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (Decimal, str, int)):
        raise ValueError("Outcome must be finite Decimal/string/int or None; no float")
    try:
        decimal_value = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Malformed decimal outcome") from exc
    if not decimal_value.is_finite():
        raise ValueError("Outcome must be finite")
    return Fraction(decimal_value)


def render(value):
    if value is None:
        return None
    value = Fraction(value)
    with localcontext() as context:
        context.prec = DISPLAY_PRECISION
        decimal_value = Decimal(value.numerator) / Decimal(value.denominator)
    result = format(decimal_value, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return "0" if result in ("", "-0") else result


def parse_records(records):
    parsed = []
    seen = set()
    for row in records:
        case_id, day, symbol = row.get("case_id"), row.get("date"), row.get("symbol")
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ValueError("case_id must be a unique nonempty string")
        if not isinstance(symbol, str) or not symbol:
            raise ValueError("symbol must be a nonempty string")
        if not isinstance(day, str) or Date.fromisoformat(day).isoformat() != day:
            raise ValueError("date must be canonical YYYY-MM-DD")
        seen.add(case_id)
        parsed.append({"case_id": case_id, "date": day, "symbol": symbol,
                       **{metric: parse_number(row[metric]) for metric in METRICS}})
    return sorted(parsed, key=lambda row: (row["date"], row["symbol"], row["case_id"]))


def metric_stats(records, metric):
    observed = [row for row in records if row[metric] is not None]
    dates = defaultdict(list)
    for row in observed:
        dates[row["date"]].append(row[metric])
    values = [row[metric] for row in observed]
    n, d = len(values), len(dates)
    total = sum(values, Fraction())
    means = {day: sum(xs, Fraction()) / len(xs) for day, xs in sorted(dates.items())}
    return {
        "complete_count": n,
        "missing_count": len(records) - n,
        "positive_count": sum(value > 0 for value in values),
        "negative_count": sum(value < 0 for value in values),
        "zero_count": sum(value == 0 for value in values),
        "event_sum": total,
        "event_mean": total / n if n else None,
        "observed_date_count": d,
        "observed_dates": sorted(dates),
        "date_means": means,
        "date_counts": {day: len(xs) for day, xs in sorted(dates.items())},
        "date_equal_mean": sum(means.values(), Fraction()) / d if d else None,
    }


def public_metric(stats):
    return {
        **{key: stats[key] for key in ("complete_count", "missing_count", "positive_count",
            "negative_count", "zero_count", "observed_date_count", "observed_dates")},
        **{key: render(stats[key]) for key in ("event_sum", "event_mean", "date_equal_mean")},
        "date_means": {day: render(value) for day, value in stats["date_means"].items()},
        "date_counts": stats["date_counts"],
    }


def entity_summary(records, key, value, overall):
    group = {"entity_type": key, "entity": list(value) if isinstance(value, tuple) else value,
             "signals": len(records), "case_ids": [row["case_id"] for row in records],
             "signal_dates": sorted({row["date"] for row in records}), "metrics": {}}
    exact = {}
    for metric in METRICS:
        own = metric_stats(records, metric)
        base = overall[metric]
        n, d = base["complete_count"], base["observed_date_count"]
        pooled = own["event_sum"] / n if n else None
        date_equal = sum((row[metric] / base["date_counts"][row["date"]]
                          for row in records if row[metric] is not None), Fraction()) / d if d else None
        group["metrics"][metric] = {
            **public_metric(own),
            "contribution_to_original_pooled_mean": render(pooled),
            "contribution_to_original_date_equal_mean": render(date_equal),
            "original_complete_denominator": n,
            "original_observed_date_denominator": d,
        }
        exact[metric] = {"pooled": pooled, "date_equal": date_equal}
    return group, exact


def leave_one_out(records, axis, value, overall):
    removed = [row for row in records if row[axis] == value]
    remaining = [row for row in records if row[axis] != value]
    original_signal_dates = {row["date"] for row in records}
    remaining_signal_dates = {row["date"] for row in remaining}
    result = {
        "removed_entity_type": axis, "removed_entity": value,
        "removed_signal_count": len(removed), "remaining_signal_count": len(remaining),
        "remaining_signal_dates": sorted(remaining_signal_dates),
        "lost_signal_dates": sorted(original_signal_dates - remaining_signal_dates),
        "metrics": {},
        "interpretation": "hypothetical_leave_one_whole_entity_out_including_wins_losses_and_missing_rows; original_main_sample_unchanged",
    }
    for metric in METRICS:
        remaining_stats = metric_stats(remaining, metric)
        removed_stats = metric_stats(removed, metric)
        result["metrics"][metric] = {
            **public_metric(remaining_stats),
            "removed_complete_count": removed_stats["complete_count"],
            "removed_missing_count": removed_stats["missing_count"],
            "removed_event_sum": render(removed_stats["event_sum"]),
            "lost_observed_dates": sorted(set(overall[metric]["observed_dates"]) - set(remaining_stats["observed_dates"])),
        }
    return result


def ranking(groups, exact_groups):
    out = {"signal_frequency_all_entities": [group["entity"] for group in sorted(groups, key=lambda g: (-g["signals"], g["entity"]))],
           "metrics": {}}
    for metric in METRICS:
        out["metrics"][metric] = {}
        for label in ("pooled", "date_equal"):
            observed = [(g["entity"], exact[metric][label]) for g, exact in zip(groups, exact_groups)
                        if g["metrics"][metric]["complete_count"]]
            out["metrics"][metric][label] = {
                "positive_contributors_descending": [key for key, value in sorted(observed, key=lambda pair: (-pair[1], pair[0])) if value > 0],
                "negative_contributors_ascending": [key for key, value in sorted(observed, key=lambda pair: (pair[1], pair[0])) if value < 0],
                "zero_contributors": [key for key, value in sorted(observed) if value == 0],
                "no_complete_outcome_entities": [g["entity"] for g in groups if not g["metrics"][metric]["complete_count"]],
            }
    out["interpretation"] = "all_entities_ranked_descriptively_not_selected_for_trading_or_parameter_optimization"
    return out


def summarize_concentration(records):
    parsed = parse_records(records)
    overall = {metric: metric_stats(parsed, metric) for metric in METRICS}
    axes = {"date": defaultdict(list), "symbol": defaultdict(list), "date_symbol": defaultdict(list)}
    for row in parsed:
        axes["date"][row["date"]].append(row)
        axes["symbol"][row["symbol"]].append(row)
        axes["date_symbol"][(row["date"], row["symbol"])].append(row)
    groups, identities, all_exact_groups = {}, {}, {}
    for axis, partitions in axes.items():
        groups[axis], exact_rows = [], []
        for value, subset in sorted(partitions.items()):
            public, exact = entity_summary(subset, axis, value, overall)
            groups[axis].append(public)
            exact_rows.append(exact)
        all_exact_groups[axis] = exact_rows
        identities[axis] = {}
        for metric in METRICS:
            identities[axis][metric] = {}
            for weight, original_field in (("pooled", "event_mean"), ("date_equal", "date_equal_mean")):
                target = overall[metric][original_field]
                contributed = sum((row[metric][weight] for row in exact_rows), Fraction()) if target is not None else None
                equal = contributed == target if target is not None else None
                assert equal is not False
                identities[axis][metric][weight] = {
                    "sum_of_entity_contributions": render(contributed), "original_mean": render(target),
                    "exact_fraction_identity_holds": equal,
                    "status": "verified_exact" if equal else "undefined_no_complete_outcomes",
                }
    return {
        "method_version": "concentration-v1",
        "signal_count": len(parsed),
        "signal_date_count": len(axes["date"]),
        "symbol_count": len(axes["symbol"]),
        "overall": {metric: public_metric(stats) for metric, stats in overall.items()},
        "by_date": groups["date"], "by_symbol": groups["symbol"],
        "date_symbol_matrix_observed_cells": groups["date_symbol"],
        "contribution_identity_checks": identities,
        "leave_one_date_out": [leave_one_out(parsed, "date", value, overall) for value in sorted(axes["date"])],
        "leave_one_symbol_out": [leave_one_out(parsed, "symbol", value, overall) for value in sorted(axes["symbol"])],
        "rankings": {axis: ranking(groups[axis], all_exact_groups[axis]) for axis in ("date", "symbol")},
        "method": {
            "return_unit": "fraction; sums_are_sums_of_event_returns_not_account_profits",
            "pooled_entity_contribution": "sum(entity_metric_returns)/original_metric_complete_N",
            "date_equal_entity_contribution": "sum(entity_return/original_metric_complete_count_on_its_date)/original_metric_observed_D",
            "metric_denominators": "net_return_and_matched_excess_use_their_own_N_date_counts_and_D",
            "missing": "all_signal_rows_retained; missing_outcomes_excluded_only_from_return_arithmetic; empty_mean_null",
            "empty_entity_contribution": "zero_if_overall_denominator_exists_means_no_contribution_not_a_zero_return_observation",
            "leave_one_out": "remove_one_entire_entity_only; recompute_remaining_nonempty_date_means_and_counts; never_change_main_sample",
            "matrix": "only_observed_symbol_date_cells; missing_signal_outcomes_retained; absent_cartesian_cells_not_fabricated",
            "identity_arithmetic": "exact_Fraction_before_decimal_display_rounding",
            "display_precision_significant_digits": DISPLAY_PRECISION,
            "inference": "descriptive_concentration_not_new_bootstrap_or_probability_or_causal_advantage",
            "no_win_rate_gate": True,
            "matched_excess": "relative_peer_performance_not_trade_profit_or_winning_probability",
            "capital": "no_equity_curve_drawdown_options_returns_or_funding_survival_model",
        },
    }
