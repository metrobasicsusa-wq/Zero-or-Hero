#!/usr/bin/env python3
"""Streaming descriptive statistics for the registered flush/rebound event study.

This module has no network or broker calls. Returns are fractions, never dollars.
Missing outcomes remain missing. Dates with no complete signal are not zero-return
observations. Bootstrap inference resamples date means and is descriptive only.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
from typing import Iterable

BOOTSTRAP_REPLICATES = 2000
BOOTSTRAP_NAMESPACE = "s500-flush-rebound-20261007-date-mean-excess-v1"
RETURN_UNIT = "fraction"
DROPS = ("0.02", "0.03")
FAMILIES = ("FIXED", "REBOUND")
HORIZONS = ("30m", "60m", "close_minus_10m")
COSTS = (5, 10, 25)
VARIANTS = tuple((drop, family, horizon, cost) for drop in DROPS
                 for family in FAMILIES for horizon in HORIZONS for cost in COSTS)
VARIANT_INDEX = {variant: index for index, variant in enumerate(VARIANTS)}
PRIMARY = ("0.02", "REBOUND", "60m", 10)
GROUPS = ("stock", "SPY", "QQQ")


def finite_number(value):
    """None means absent; malformed or nonfinite supplied outcomes are errors."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValueError(f"Expected numeric fraction, got {type(value).__name__}")
    try:
        numeric = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Malformed numeric return supplied") from exc
    if not numeric.is_finite() or not math.isfinite(float(numeric)):
        raise ValueError("Nonfinite return supplied")
    return float(numeric)


def describe(values):
    values = [finite_number(value) for value in values]
    if any(value is None for value in values):
        raise ValueError("describe accepts observed values only")
    n = len(values)
    return {
        "n": n,
        "mean": statistics.fmean(values) if n else None,
        "median": statistics.median(values) if n else None,
        "positive_fraction": sum(value > 0 for value in values) / n if n else None,
        "zero_count": sum(value == 0 for value in values),
        "negative_count": sum(value < 0 for value in values),
        "minimum": min(values) if n else None,
        "maximum": max(values) if n else None,
    }


def percentile(sorted_values, probability):
    if not sorted_values:
        return None
    position = (len(sorted_values) - 1) * probability
    lo = math.floor(position)
    hi = math.ceil(position)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (position - lo)


def date_cluster_bootstrap(date_means, variant_id, replicates=BOOTSTRAP_REPLICATES):
    """Mean across nonmissing date means, with whole-date replacement draws.

    Each date contributes one equally weighted observed mean; no IID trade draws,
    no zero padding on non-event dates, and no mixing variants or holding periods.
    """
    if not isinstance(replicates, int) or isinstance(replicates, bool) or replicates < 1:
        raise ValueError("replicates must be a positive integer")
    dates = sorted(date_means)
    values = [finite_number(date_means[date]) for date in dates]
    if any(value is None for value in values):
        raise ValueError("Bootstrap cannot include null date means")
    seed_label = BOOTSTRAP_NAMESPACE + "|" + variant_id
    seed_hex = hashlib.sha256(seed_label.encode()).hexdigest()
    seed = int(seed_hex[:16], 16)
    result = {
        "estimator": "equally_weighted_mean_of_complete_matched_excess_date_means",
        "resampling_unit": "date",
        "return_unit": RETURN_UNIT,
        "n_dates": len(dates),
        "point_estimate": statistics.fmean(values) if values else None,
        "replicates": replicates,
        "seed_namespace": BOOTSTRAP_NAMESPACE,
        "seed_label_sha256": seed_hex,
        "seed_integer": seed,
        "percentile_interpolation": "linear_at_(n_minus_1)_times_probability",
        "confidence_level": 0.95,
        "lower": None,
        "upper": None,
        "status": "insufficient_dates" if len(dates) < 2 else "computed",
        "interpretation": "descriptive_date_cluster_interval_not_proven_alpha_or_strict_out_of_sample",
    }
    if len(dates) >= 2:
        rng = random.Random(seed)
        n = len(values)
        samples = sorted(statistics.fmean(values[rng.randrange(n)] for _ in range(n))
                         for _ in range(replicates))
        result["lower"] = percentile(samples, 0.025)
        result["upper"] = percentile(samples, 0.975)
    return result


def matched_excess(target_return, controls):
    """Require the target and exactly three distinct, complete controls."""
    target_return = finite_number(target_return)
    if len(controls) != 3:
        return None, "requires_exactly_three_controls"
    symbols = [control.get("symbol") for control in controls]
    if any(not isinstance(symbol, str) or not symbol for symbol in symbols):
        return None, "missing_control_symbol"
    if len(set(symbols)) != 3:
        return None, "duplicate_control_symbol"
    if target_return is None:
        return None, "target_outcome_missing"
    values = [finite_number(control.get("net_return")) for control in controls]
    if any(control.get("status") != "complete" or value is None
           for control, value in zip(controls, values)):
        return None, "one_or_more_control_outcomes_incomplete"
    return target_return - statistics.fmean(values), "complete"


def iter_json_rows(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Line {line_number} is not an object")
            yield value


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def variant_id(variant):
    drop, family, horizon, cost = variant
    return f"drop{drop}_{family}_{horizon}_cost{cost}bps_per_side"


def variant_object(variant):
    return dict(zip(("drop_threshold", "family", "exit_horizon", "cost_bps_per_side"), variant))


def new_day():
    return {
        "counts": collections.Counter({name: 0 for name in (
            "selected_cases", "eligible_risk_set", "ineligible_or_unknown_risk_set",
            "flush_true", "flush_false", "flush_unknown", "complete_returns",
            "signal_return_missing", "complete_matched_excess")}),
        "signal_status_counts": collections.Counter({name: 0 for name in ("signal", "no_flush", "no_rebound", "unknown")}),
        "missing_stage_counts": collections.Counter(),
        "missing_reason_counts": collections.Counter(),
        "paired_control_status_counts": collections.Counter(),
        "returns": [],
        "matched_excesses": [],
        "SPY_returns": [], "SPY_excesses": [],
        "QQQ_returns": [], "QQQ_excesses": [],
    }


def assert_same_number(expected, actual, label):
    actual = finite_number(actual)
    if (expected is None) != (actual is None):
        raise ValueError(f"{label} missingness mismatch")
    if expected is not None and not math.isclose(expected, actual, abs_tol=1e-12, rel_tol=1e-10):
        raise ValueError(f"{label} arithmetic mismatch")


def consume_row(row, days, masks, identities, calendar_dates):
    variant = (row["drop_threshold"], row["family"], row["exit_horizon"], row["cost_bps_per_side"])
    if variant not in VARIANT_INDEX:
        raise ValueError("Unregistered variant")
    if isinstance(variant[3], bool):
        raise ValueError("Cost cannot be a boolean")
    date = row["date"]
    if date not in calendar_dates:
        raise ValueError("Outcome date absent from registered calendar")
    symbol, case_id = row["symbol"], row["case_id"]
    role = row["target_role"]
    if role == "stock" and symbol not in ("SPY", "QQQ"):
        group = "stock"
    elif role == "benchmark" and symbol in ("SPY", "QQQ"):
        group = symbol
    else:
        raise ValueError("Invalid stock/benchmark role or SPY/QQQ included in primary targets")
    identity = (date, symbol, role)
    if case_id in identities and identities[case_id] != identity:
        raise ValueError("Case identity changed between variants")
    identities[case_id] = identity
    bit = 1 << VARIANT_INDEX[variant]
    if masks.get(case_id, 0) & bit:
        raise ValueError("Duplicate case/variant")
    masks[case_id] = masks.get(case_id, 0) | bit
    key = (group, variant, date)
    day = days.setdefault(key, new_day())
    day["counts"]["selected_cases"] += 1
    risk = row["risk_set_eligible"]
    flush = row["flush_detected"]
    if type(risk) is not bool or (flush is not None and type(flush) is not bool):
        raise ValueError("Risk/flush flags must be bool or permitted null")
    day["counts"]["eligible_risk_set"] += int(risk)
    day["counts"]["ineligible_or_unknown_risk_set"] += int(not risk)
    day["counts"]["flush_true" if flush is True else "flush_false" if flush is False else "flush_unknown"] += 1
    signal = row["signal_status"]
    if signal not in ("signal", "no_flush", "no_rebound", "unknown"):
        raise ValueError("Unexpected signal status")
    day["signal_status_counts"][signal] += 1
    missing_stage, missing_reason = row.get("missing_stage"), row.get("missing_reason")
    if missing_stage:
        day["missing_stage_counts"][str(missing_stage)] += 1
    if missing_reason:
        day["missing_reason_counts"][str(missing_reason)] += 1
    value = finite_number(row.get("net_return"))
    if value is not None:
        if signal != "signal" or row.get("entry_minute") is None or row.get("exit_minute") is None:
            raise ValueError("Complete return supplied without a signal and defined entry/exit")
        if missing_stage:
            raise ValueError("Complete return supplied with missing stage")
        day["returns"].append(value)
        day["counts"]["complete_returns"] += 1
    elif signal == "signal":
        day["counts"]["signal_return_missing"] += 1
    control_status = row["paired_control_status"]
    if control_status not in ("complete_three", "insufficient_candidates", "missing_return", "not_applicable"):
        raise ValueError("Unexpected paired-control status")
    day["paired_control_status_counts"][control_status] += 1
    controls = row.get("paired_controls", [])
    computed_excess, computed_status = matched_excess(value, controls)
    if symbol in {control.get("symbol") for control in controls}:
        raise ValueError("Target cannot be its own matched control")
    if any(control.get("symbol") in ("SPY", "QQQ") for control in controls):
        raise ValueError("ETF benchmark cannot be one of the three stock controls")
    if control_status == "complete_three":
        if group != "stock" or computed_status != "complete":
            raise ValueError("Invalid complete matched-control claim")
        assert_same_number(computed_excess, row.get("matched_excess"), "matched excess")
        day["matched_excesses"].append(computed_excess)
        day["counts"]["complete_matched_excess"] += 1
    elif row.get("matched_excess") is not None:
        raise ValueError("Incomplete pair must not supply matched excess")
    for benchmark in ("SPY", "QQQ"):
        item = row.get("benchmarks", {}).get(benchmark, {})
        benchmark_return = finite_number(item.get("net_return"))
        excess = finite_number(item.get("excess_return"))
        if benchmark_return is not None:
            day[benchmark + "_returns"].append(benchmark_return)
        if excess is not None:
            if value is None or benchmark_return is None:
                raise ValueError("Benchmark excess requires both complete returns")
            assert_same_number(value - benchmark_return, excess, benchmark + " excess")
            day[benchmark + "_excesses"].append(excess)


COUNTERS = ("counts", "signal_status_counts", "missing_stage_counts", "missing_reason_counts", "paired_control_status_counts")
VECTORS = ("returns", "matched_excesses", "SPY_returns", "SPY_excesses", "QQQ_returns", "QQQ_excesses")


def describe_slice(day_items):
    totals = {name: collections.Counter() for name in COUNTERS}
    vectors = {name: [] for name in VECTORS}
    date_means = {name: [] for name in VECTORS}
    for date, day in day_items:
        for name in COUNTERS:
            totals[name].update(day[name])
        for name in VECTORS:
            vectors[name].extend(day[name])
            if day[name]:
                date_means[name].append(statistics.fmean(day[name]))
    return {
        "calendar_dates_in_slice": len(day_items),
        **{name: dict(sorted(counts.items())) for name, counts in totals.items()},
        "event_weighted": {name: describe(values) for name, values in vectors.items()},
        "date_weighted": {name: describe(values) for name, values in date_means.items()},
    }


def build_summary(rows: Iterable[dict], calendar_dates, expected_case_ids=None):
    calendar_dates = sorted(calendar_dates)
    if len(calendar_dates) != len(set(calendar_dates)):
        raise ValueError("Duplicate calendar dates")
    for date in calendar_dates:
        if dt.date.fromisoformat(date).year != 2026:
            raise ValueError("Study date outside 2026")
    days, masks, identities = {}, {}, {}
    calendar_set = set(calendar_dates)
    n_rows = 0
    for row in rows:
        consume_row(row, days, masks, identities, calendar_set)
        n_rows += 1
    full_mask = (1 << len(VARIANTS)) - 1
    if any(mask != full_mask for mask in masks.values()):
        raise ValueError("One or more cases missing registered variants")
    if expected_case_ids is not None and set(masks) != set(expected_case_ids):
        raise ValueError("Observed cases differ from frozen registry")
    if n_rows != len(masks) * len(VARIANTS):
        raise ValueError("Row count does not equal cases times variants")
    months = sorted({date[:7] for date in calendar_dates})
    results, daily = [], []
    for group in GROUPS:
        for variant in VARIANTS:
            vid = variant_id(variant)
            day_items = [(date, days.get((group, variant, date), new_day())) for date in calendar_dates]
            all_stats = describe_slice(day_items)
            item = {
                "group": group,
                "variant_id": vid,
                "variant": variant_object(variant),
                "is_registered_primary": group == "stock" and variant == PRIMARY,
                "all": all_stats,
                "months": {month: describe_slice([(date, day) for date, day in day_items if date.startswith(month)])
                           for month in months},
                "periods": {
                    "H1": describe_slice([(date, day) for date, day in day_items if date < "2026-07-01"]),
                    "H2_to_cutoff": describe_slice([(date, day) for date, day in day_items if date >= "2026-07-01"]),
                },
                "matched_excess_date_cluster_bootstrap": date_cluster_bootstrap(
                    {date: statistics.fmean(day["matched_excesses"]) for date, day in day_items if day["matched_excesses"]},
                    group + "|" + vid),
            }
            results.append(item)
            for date, day in day_items:
                daily.append({"date": date, "group": group, "variant_id": vid,
                              **{name: dict(sorted(day[name].items())) for name in COUNTERS},
                              **{name: describe(day[name]) for name in VECTORS}})
    primary = next(item for item in results if item["is_registered_primary"])
    return {
        "method_version": "summarize-flush-v1",
        "return_unit": RETURN_UNIT,
        "calendar_dates": calendar_dates,
        "selected_case_count": len(masks),
        "ledger_row_count": n_rows,
        "registered_variant_count": len(VARIANTS),
        "case_variant_coverage": "complete_exactly_once",
        "case_counts_by_group": dict(collections.Counter("stock" if role == "stock" else symbol
                                                        for date, symbol, role in identities.values())),
        "primary_variant_id": variant_id(PRIMARY),
        "interpretation": {
            "returns": "stock_minute_open_print_price_proxy_returns_after_fixed_cost_scenarios_not_verified_fills",
            "no_signal_dates": "excluded_from_return_means_not_filled_with_zero",
            "risk_set": "reported_separately_from_conditional_signal_return_sample",
            "matched_control_requirement": "exactly_three_distinct_complete_stocks_equal_weighted_same_date_entry_exit",
            "inference": "date_mean_cluster_bootstrap_descriptive_not_proven_alpha",
            "periods": "monthly_H1_H2_descriptive_H2_not_strict_out_of_sample_after_peer_inspiration",
            "multiplicity": "all_36_variants_reported_without_return_ranking_or_winner_selection",
            "wealth": "no_500_dollar_portfolio_or_options_return_inference",
        },
        "variants": results,
    }, daily, primary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", nargs="+", required=True, type=Path)
    parser.add_argument("--calendar", required=True, type=Path)
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    calendar = json.loads(args.calendar.read_text())
    registry = json.loads(args.registry.read_text())
    expected = [item["case_id"] for item in registry["cases"]]
    def all_rows():
        for path in args.input:
            yield from iter_json_rows(path)
    summary, daily, primary = build_summary(all_rows(), [item["date"] for item in calendar], expected)
    summary["input_files"] = [{"file": path.name, "sha256": file_sha256(path), "bytes": path.stat().st_size}
                              for path in args.input]
    summary["calendar_sha256"] = file_sha256(args.calendar)
    summary["registry_sha256"] = file_sha256(args.registry)
    summary["summarizer_sha256"] = file_sha256(__file__)
    summary["created_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, result in (("summary.json", summary), ("primary-inference.json", primary)):
        (args.output_dir / name).write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    with (args.output_dir / "daily-statistics.jsonl").open("w") as stream:
        for row in daily:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")
    print(json.dumps({"rows": summary["ledger_row_count"], "cases": summary["selected_case_count"],
                      "variants": len(VARIANTS), "groups": GROUPS,
                      "primary_complete_returns": primary["all"]["event_weighted"]["returns"]["n"],
                      "primary_complete_matched_excess": primary["all"]["event_weighted"]["matched_excesses"]["n"]}))


if __name__ == "__main__":
    main()
