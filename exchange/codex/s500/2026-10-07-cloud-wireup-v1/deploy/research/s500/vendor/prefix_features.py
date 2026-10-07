"""Causal first-30-minute descriptors, with no outcome-selected thresholds.

Every computed market feature is observable when minute 29 closes (10:00 ET).
No entry/exit, later return, option, account, or network operations are used.
"""

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation, localcontext

from vendor.flush_engine import evaluate_prefix, prepare_bars

VERSION = "first30-prefix-features-v1"


def _prefix_only(bars):
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
            # Unlocatable records cannot be proved to be outside observation.
            prefix.append(bar)
    return prefix


def _previous_close(value):
    if value is None:
        return None, "previous_close_missing"
    if isinstance(value, bool):
        return None, "previous_close_boolean_not_numeric"
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None, "previous_close_not_numeric"
    if not number.is_finite():
        return None, "previous_close_not_finite"
    if number <= 0:
        return None, "previous_close_not_positive"
    return number, None


def _text(number):
    return format(number, "f") if number is not None else None


def first30_features(bars, previous_close, source_complete=True):
    """Return Decimal-string descriptors from valid unique minutes 0..29.

    Previous-close validity is independent of prefix validity; when unavailable,
    only the opening-gap feature is unknown. No relative-volume proxy is used.
    """
    prepared = prepare_bars(_prefix_only(bars), source_complete=source_complete)
    prefix = evaluate_prefix(prepared, "0.02")
    # The vendored engine's flush/no-flush flag is not a feature filter.
    complete = prefix["coverage"]["complete"]
    prior, prior_reason = _previous_close(previous_close)
    result = {
        "version": VERSION,
        "status": "complete" if complete else "unknown_prefix",
        "prefix_valid": complete,
        "reason": None if complete else "prefix_incomplete_or_invalid",
        "observation_start_minute": 0,
        "observation_end_minute": 29,
        "available_at_minute": 30,
        "open0": None,
        "close29": None,
        "min_close": None,
        "t_min": None,
        "max_close": None,
        "close29_return": None,
        "min_close_return": None,
        "max_close_return": None,
        "first30_volume": None,
        "first30_high": None,
        "first30_low": None,
        "first30_high_low_range": None,
        "previous_close": _text(prior),
        "previous_close_valid": prior is not None,
        "previous_close_reason": prior_reason,
        "gap_to_previous_close": None,
        "gap_reason": "prefix_incomplete_or_invalid" if not complete else prior_reason,
        "coverage": prefix["coverage"],
    }
    if not complete:
        return result
    with localcontext() as context:
        context.prec = 42
        opening = prepared.valid[0]["o"]
        ending = prepared.valid[29]["c"]
        minimum = min(prepared.valid[t]["c"] for t in range(30))
        maximum = max(prepared.valid[t]["c"] for t in range(30))
        t_min = next(t for t in range(30) if prepared.valid[t]["c"] == minimum)
        high = max(prepared.valid[t]["h"] for t in range(30))
        low = min(prepared.valid[t]["l"] for t in range(30))
        volume = sum((prepared.valid[t]["v"] for t in range(30)), Decimal(0))
        result.update(
            open0=_text(opening), close29=_text(ending),
            min_close=_text(minimum), t_min=t_min, max_close=_text(maximum),
            close29_return=_text(ending / opening - 1),
            min_close_return=_text(minimum / opening - 1),
            max_close_return=_text(maximum / opening - 1),
            first30_volume=_text(volume),
            first30_high=_text(high), first30_low=_text(low),
            first30_high_low_range=_text((high - low) / opening),
        )
        if prior is not None:
            result["gap_to_previous_close"] = _text(opening / prior - 1)
    return result
