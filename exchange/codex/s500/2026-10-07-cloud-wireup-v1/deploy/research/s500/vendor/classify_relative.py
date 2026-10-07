"""Exploratory first-30-minute stock/market classification, never fitted on PnL.

Only the target's earliest minimum close in minutes 0..29 identifies the
comparison minute. Benchmark movement uses that exact minute, not its own low.
"""

from collections.abc import Mapping
from decimal import Decimal, localcontext

from vendor.flush_engine import evaluate_prefix, prepare_bars

VERSION = "relative-flush-classifier-v1"
MARKET_DOWN_RETURN_THRESHOLD = Decimal("-0.005")


def _prefix_only(bars):
    """Ignore identifiable out-of-prefix bars, preserve unlocatable failures."""
    if isinstance(bars, (str, bytes, Mapping)):
        return [{"t": None}]
    try:
        source = iter(bars)
    except TypeError:
        return [{"t": None}]
    prefix = []
    for bar in source:
        if isinstance(bar, Mapping) and type(bar.get("t")) is int:
            if 0 <= bar["t"] <= 29:
                prefix.append(bar)
        else:
            # Cannot establish that malformed timestamps belong outside prefix.
            prefix.append(bar)
    return prefix


def _validated_prefix(bars, source_complete):
    prepared = prepare_bars(_prefix_only(bars), source_complete=source_complete)
    # Reuse the independently-tested validity/OHLC/duplicate rules. Its 2% flush
    # result is deliberately unused: classification is not a new signal filter.
    result = evaluate_prefix(prepared, "0.02")
    return prepared, result


def _text(number):
    return format(number, "f") if number is not None else None


def classify_case(target_bars, benchmark_bars, source_complete=True):
    """Return complete-prefix market grouping and simple relative return.

    Both inputs are normalized lists with integer t (0 means regular open).
    Every minute 0..29 must be present, unique, and valid on both sides. Output
    numeric values are Decimal strings. Invalid prefixes are retained as unknown.
    No entry, exit, option, return-after-signal, or account data are consumed.
    """
    target, target_prefix = _validated_prefix(target_bars, source_complete)
    benchmark, benchmark_prefix = _validated_prefix(benchmark_bars, source_complete)
    target_valid = target_prefix["coverage"]["complete"]
    benchmark_valid = benchmark_prefix["coverage"]["complete"]
    result = {
        "version": VERSION,
        "target_valid": target_valid,
        "benchmark_valid": benchmark_valid,
        "t_min": None,
        "target_open": None,
        "target_min_close": None,
        "benchmark_open": None,
        "benchmark_close_at_target_minute": None,
        "target_return": None,
        "benchmark_return": None,
        "relative_return": None,
        "market_down_return_threshold": _text(MARKET_DOWN_RETURN_THRESHOLD),
        "classification": None,
        "status": None,
        "reason": None,
        "coverage": {"target": target_prefix["coverage"], "benchmark": benchmark_prefix["coverage"]},
    }
    with localcontext() as context:
        context.prec = 42
        if target_valid:
            target_open = target.valid[0]["o"]
            minimum = min(target.valid[t]["c"] for t in range(30))
            t_min = next(t for t in range(30) if target.valid[t]["c"] == minimum)
            target_return = minimum / target_open - 1
            result.update(t_min=t_min, target_open=_text(target_open),
                          target_min_close=_text(minimum), target_return=_text(target_return))
        if benchmark_valid:
            result["benchmark_open"] = _text(benchmark.valid[0]["o"])
        if not target_valid or not benchmark_valid:
            if not target_valid and not benchmark_valid:
                result.update(status="unknown_both_prefixes", reason="target_and_benchmark_prefix_incomplete_or_invalid")
            elif not target_valid:
                result.update(status="unknown_target_prefix", reason="target_prefix_incomplete_or_invalid")
            else:
                result.update(status="unknown_benchmark_prefix", reason="benchmark_prefix_incomplete_or_invalid")
            return result
        benchmark_open = benchmark.valid[0]["o"]
        benchmark_close = benchmark.valid[t_min]["c"]
        benchmark_return = benchmark_close / benchmark_open - 1
        result.update(benchmark_close_at_target_minute=_text(benchmark_close),
                      benchmark_return=_text(benchmark_return),
                      relative_return=_text(target_return - benchmark_return))
        # Equivalent cross-product comparison preserves the inclusive boundary
        # without relying on rounded display-return strings. All input price
        # digits plus multiplier digits fit before performing this comparison.
        context.prec = max(42, len(benchmark_open.as_tuple().digits)
                           + len(benchmark_close.as_tuple().digits) + 10)
        market_down = benchmark_close <= benchmark_open * (1 + MARKET_DOWN_RETURN_THRESHOLD)
        result.update(status="classified", reason=None,
                      classification="market_down" if market_down else "market_not_down")
    return result
