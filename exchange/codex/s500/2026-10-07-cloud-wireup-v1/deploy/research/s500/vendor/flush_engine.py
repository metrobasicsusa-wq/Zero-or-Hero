"""Pure, causal opening-flush event study; minute-open prices are proxies.

No network, account, order, portfolio, or options operations are performed.
Minute zero is the regular-session opening minute. Every return uses exact
minute offsets and two-sided hypothetical costs, never a verified fill.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from typing import Mapping

VERSION = "flush-engine-v1"
THRESHOLDS = ("0.02", "0.03")
FAMILIES = ("FIXED", "REBOUND")
HORIZONS = ("30m", "60m", "close_minus_10m")
COSTS_BPS = (5, 10, 25)


def _number(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("not a number")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("not a finite number") from None
    if not number.is_finite():
        raise ValueError("not a finite number")
    return number


def _text(value):
    return format(value, "f") if value is not None else None


@dataclass(frozen=True)
class PreparedBars:
    """Internal normalized inputs; callers must not mutate either mapping."""

    valid: dict
    invalid: dict
    global_errors: tuple
    source_complete: bool


def prepare_bars(bars, *, source_complete=True):
    """Validate bars once for reuse. Duplicate minutes invalidate that minute.

    Input: mapping of integer minute -> bar, or iterable of bar mappings. Each
    bar needs integer t, positive finite o/h/l/c, nonnegative finite v, and
    coherent OHLC. A malformed/unlocatable t makes the entire input unknown.
    """
    if isinstance(bars, PreparedBars):
        if source_complete is not True:
            return PreparedBars(bars.valid, bars.invalid, bars.global_errors, False)
        return bars
    valid, invalid, global_errors, seen = {}, {}, [], set()
    if isinstance(bars, Mapping):
        items = bars.items()
    else:
        try:
            items = ((None, bar) for bar in bars)
        except TypeError:
            return PreparedBars({}, {}, ("bars_not_iterable",), source_complete is True)
    for key, bar in items:
        if not isinstance(bar, Mapping):
            global_errors.append("bar_not_mapping")
            continue
        minute = bar.get("t")
        if type(minute) is not int or (key is not None and (type(key) is not int or key != minute)):
            global_errors.append("invalid_or_mismatched_minute")
            continue
        if minute in seen:
            valid.pop(minute, None)
            invalid[minute] = "duplicate_minute"
            continue
        seen.add(minute)
        try:
            values = {field: _number(bar.get(field)) for field in ("o", "h", "l", "c", "v")}
        except ValueError:
            invalid[minute] = "missing_or_nonfinite_numeric_field"
            continue
        if any(values[field] <= 0 for field in ("o", "h", "l", "c")):
            invalid[minute] = "nonpositive_price"
        elif values["v"] < 0:
            invalid[minute] = "negative_volume"
        elif not (values["l"] <= min(values["o"], values["c"]) <= max(values["o"], values["c"]) <= values["h"]):
            invalid[minute] = "incoherent_ohlc"
        else:
            valid[minute] = values
    return PreparedBars(valid, invalid, tuple(global_errors), source_complete is True)


def _coverage(prepared, start, end):
    missing = [minute for minute in range(start, end + 1) if minute not in prepared.valid and minute not in prepared.invalid]
    invalid = [{"minute": minute, "reason": prepared.invalid[minute]} for minute in range(start, end + 1) if minute in prepared.invalid]
    return {
        "complete": prepared.source_complete and not prepared.global_errors and not missing and not invalid,
        "source_complete": prepared.source_complete,
        "global_errors": list(prepared.global_errors),
        "missing_minutes": missing,
        "invalid_minutes": invalid,
    }


def _threshold(value):
    threshold = _number(value)
    if not Decimal(0) < threshold < Decimal(1):
        raise ValueError("threshold must lie strictly between zero and one")
    return threshold


def _session(value):
    if type(value) is not int or value <= 0:
        raise ValueError("session_minutes must be a positive integer")
    return value


def evaluate_prefix(bars, threshold, *, source_complete=True):
    """Classify complete first 30 minutes, using opening o and minimum close."""
    prepared = prepare_bars(bars, source_complete=source_complete)
    threshold = _threshold(threshold)
    coverage = _coverage(prepared, 0, 29)
    result = {
        "threshold": _text(threshold), "prefix_start_minute": 0,
        "prefix_end_minute": 29, "coverage": coverage,
        "opening_open": None, "first30_min_close": None,
        "opening_drop_fraction": None, "is_flush": None,
    }
    if not coverage["complete"]:
        result["status"] = "unknown_source" if not prepared.source_complete or prepared.global_errors else "unknown_prefix"
        return result
    opening = prepared.valid[0]["o"]
    minimum = min(prepared.valid[minute]["c"] for minute in range(30))
    with localcontext() as context:
        context.prec = 42
        flush = minimum <= opening * (1 - threshold)
        result.update(status="flush" if flush else "no_flush", is_flush=flush,
                      opening_open=_text(opening), first30_min_close=_text(minimum),
                      opening_drop_fraction=_text(1 - minimum / opening))
    return result


def family_decision(bars, threshold, family, *, session_minutes=390, source_complete=True):
    """Return first causal signal and delayed entry, retaining all non-signals.

    FIXED: signal close at t=29, decision t=30, entry open t=31.
    REBOUND: first t in 30..89 with c[t] >= 1.01 * min(c[0:t]);
    decision t+1, entry open t+2. Missing earlier observation means unknown.
    """
    if family not in FAMILIES:
        raise ValueError("unknown family")
    _session(session_minutes)
    prepared = prepare_bars(bars, source_complete=source_complete)
    prefix = evaluate_prefix(prepared, threshold)
    result = {
        "family": family, "threshold": prefix["threshold"], "prefix": prefix,
        "signal_minute": None, "decision_minute": None, "entry_minute": None,
        "signal_close": None, "prior_running_min_close": None,
        "observation_end_minute": 29, "status": prefix["status"],
        "unknown_minute": None, "reason": None,
    }
    if session_minutes <= 31 or (family == "REBOUND" and session_minutes <= 91):
        result.update(status="invalid_session", reason="session_too_short_for_registered_observation_and_entry")
        return result
    if prefix["is_flush"] is not True:
        return result
    if family == "FIXED":
        result.update(status="signal", signal_minute=29, decision_minute=30,
                      entry_minute=31, signal_close=_text(prepared.valid[29]["c"]))
        return result
    running_minimum = min(prepared.valid[minute]["c"] for minute in range(30))
    with localcontext() as context:
        context.prec = 42
        for minute in range(30, 90):
            result["observation_end_minute"] = minute
            if minute not in prepared.valid:
                result.update(status="unknown_rebound_prefix", unknown_minute=minute,
                              reason=prepared.invalid.get(minute, "missing_minute"))
                return result
            close = prepared.valid[minute]["c"]
            if close >= running_minimum * Decimal("1.01"):
                result.update(status="signal", signal_minute=minute,
                              decision_minute=minute + 1, entry_minute=minute + 2,
                              signal_close=_text(close), prior_running_min_close=_text(running_minimum))
                return result
            running_minimum = min(running_minimum, close)
    result.update(status="valid_no_rebound")
    return result


def evaluate_window(bars, entry_minute, exit_minute, cost_bps, *, session_minutes=390, source_complete=True):
    """Exact open/open return with inclusive coverage and two-sided costs.

    net_return = exit_open*(1-c)/(entry_open*(1+c)) - 1;
    c = cost_bps/10000. Values are decimal strings. No capital compounding.
    """
    _session(session_minutes)
    cost = _number(cost_bps)
    if not Decimal(0) <= cost < Decimal(10000):
        raise ValueError("cost_bps must be in [0, 10000)")
    prepared = prepare_bars(bars, source_complete=source_complete)
    result = {
        "entry_minute": entry_minute, "exit_minute": exit_minute,
        "per_side_cost_bps": _text(cost), "price_kind": "minute_open_proxy",
        "entry_open": None, "exit_open": None, "gross_return": None,
        "net_return": None, "coverage": None,
        "verified_fill": False, "cost_kind": "hypothetical_per_side_scenario",
    }
    if type(entry_minute) is not int or type(exit_minute) is not int or not 0 <= entry_minute < exit_minute < session_minutes:
        result.update(status="invalid_window")
        return result
    coverage = _coverage(prepared, entry_minute, exit_minute)
    result["coverage"] = coverage
    if not coverage["complete"]:
        result["status"] = "unknown_source" if not prepared.source_complete or prepared.global_errors else "unknown_window"
        return result
    entry, exit_ = prepared.valid[entry_minute]["o"], prepared.valid[exit_minute]["o"]
    with localcontext() as context:
        context.prec = 42
        rate = cost / Decimal(10000)
        result.update(status="ok", entry_open=_text(entry), exit_open=_text(exit_),
                      gross_return=_text(exit_ / entry - 1),
                      net_return=_text(exit_ * (1 - rate) / (entry * (1 + rate)) - 1))
    return result


def evaluate_day(bars, *, session_minutes=390, source_complete=True):
    """All 36 predeclared rows, including non-signals and missing returns."""
    _session(session_minutes)
    prepared = prepare_bars(bars, source_complete=source_complete)
    rows = []
    for threshold in THRESHOLDS:
        for family in FAMILIES:
            decision = family_decision(prepared, threshold, family, session_minutes=session_minutes)
            for horizon in HORIZONS:
                entry = decision["entry_minute"]
                exit_ = (entry + (30 if horizon == "30m" else 60) if horizon != "close_minus_10m" else session_minutes - 10) if entry is not None else None
                for cost in COSTS_BPS:
                    row = {"threshold": threshold, "family": family, "horizon": horizon,
                           "per_side_cost_bps": str(cost), "decision": decision,
                           "entry_minute": entry, "exit_minute": exit_,
                           "status": decision["status"], "window": None,
                           "gross_return": None, "net_return": None}
                    if decision["status"] == "signal":
                        window = evaluate_window(prepared, entry, exit_, cost, session_minutes=session_minutes)
                        row.update(window=window, status=window["status"],
                                   gross_return=window["gross_return"], net_return=window["net_return"])
                    rows.append(row)
    return rows
