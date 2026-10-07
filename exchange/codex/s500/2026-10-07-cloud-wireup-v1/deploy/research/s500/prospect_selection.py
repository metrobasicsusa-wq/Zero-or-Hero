"""Pure, deterministic prospective universe selection; no network, clock or prices after date.

``selection(date, calendar_prior_dates, universe, daily, source_complete=True)``
accepts a pre-sealed universe and exactly 20 distinct, ascending prior session
dates. The caller must establish the calendar's provenance, receipt cutoff and
that these really are the immediately preceding sessions. This function cannot
establish those facts merely from ISO date strings.

Universe rows have ``symbol`` and ``etf_classification`` (Y/N/unknown). Daily
rows have ``session_date``, ``c`` and ``v``. No same-day or future daily rows,
duplicate symbol dates, malformed dates or ambiguous universe rows are allowed:
any such structural fault blocks the WHOLE selection. Valid older daily rows
are ignored. Missing/invalid prior20 prices or volumes exclude that stock.
An incomplete source also blocks the whole pool; never rank a partial pool.
Numeric parsing admits at most 80 coefficient digits and an absolute Decimal
tuple exponent of 100. These generous encoding limits prevent arithmetic
overflow/underflow or excessive precision allocation; they are input safety
limits, not new economic eligibility thresholds. Out-of-bound values exclude
the affected stock as invalid_prior20_close_or_volume, without partial bars.

Ready results include every universe symbol plus the two unique forced ETF
controls in ``rows``. ``eligible_symbols``, ``excluded_by_reason``, ``metrics``,
``selected_symbols`` and the three selection components provide complete
denominators. Eligible but unselected stocks remain in rows with an empty roles
list. Unknown ETF classifications remain eligible, explicitly marked unknown.
SPY/QQQ are ALWAYS benchmark-only regardless of supplied ETF classification.

This module does not infer that a daily bar is finalized, the source is truly
complete, or the supplied universe was available at the historical cutoff.
Those gates belong to the caller's source/receipt protocol. There are no
intraday data, strategy outcomes, orders or option returns in this selection.
"""

from collections.abc import Mapping
from datetime import date as Date
from decimal import Decimal, InvalidOperation, localcontext
from hashlib import sha256
import re


STORAGE = ("MU", "STX", "WDC", "SNDK", "NTAP", "RMBS", "SIMO", "P")
CONTROLS = ("SPY", "QQQ")
SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
NUMERIC_MAX_DIGITS = 80
NUMERIC_MAX_ABS_EXPONENT = 100


def _iso_date(value):
    if not isinstance(value, str):
        return False
    try:
        return Date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    parsed = number.as_tuple()
    if (len(parsed.digits) > NUMERIC_MAX_DIGITS
            or abs(parsed.exponent) > NUMERIC_MAX_ABS_EXPONENT):
        return None
    return number


def _exact_product(a, b):
    with localcontext() as ctx:
        ctx.Emax, ctx.Emin = 999999, -999999
        ctx.prec = max(28, len(a.as_tuple().digits) + len(b.as_tuple().digits) + 2)
        return a * b


def _median(values):
    values = sorted(values)
    a, b = values[9:11]
    # Addition at unlike exponents needs enough precision for all aligned digits.
    with localcontext() as ctx:
        ctx.Emax, ctx.Emin = 999999, -999999
        ctx.prec = max(28, max(a.adjusted(), b.adjusted())
                       - min(a.as_tuple().exponent, b.as_tuple().exponent) + 4)
        return (a + b) / 2


def selection(date, calendar_prior_dates, universe, daily, source_complete=True):
    """Select the inherited top64 + remaining SHA32 + storage + ETF controls.

    The original hash is SHA256(``s500_flush_v1|{date}|{symbol}``). Liquidity
    rank ties and hash ties use the symbol. Thresholds are inclusive: previous
    close >=5; median of 20 daily close*volume values >=20,000,000. Close must
    be positive and volume nonnegative on every required prior session.

    Returns ``ready``, ``blocked_source_incomplete`` or
    ``blocked_invalid_inputs``. All blocked responses have no selection,
    eligible pool, metrics or ranked rows. Exceptions are not used to signal
    ordinary malformed input. Inputs are not mutated.
    """
    result = {
        "status": None, "date": date if isinstance(date, str) else None,
        "prior20_dates": [], "prior_session_date": None,
        "selected_symbols": [], "eligible_symbols": [], "excluded_by_reason": {},
        "selected_top64": [], "selected_hash32": [], "selected_storage": [],
        "forced_controls": [], "metrics": {}, "rows": [], "errors": [],
        "counts": {"universe": 0, "eligible_stocks": 0, "selected": 0,
                   "selected_stocks": 0, "selected_controls": 0},
        "diagnostics": {"ignored_older_daily_rows": 0,
                        "daily_symbols_outside_universe": []},
        "rule_version": "s500_flush_v1_prospective_selection_v1",
        "same_day_or_future_prices_used": False,
        "outcomes_used": False,
    }
    if source_complete is not True:
        result["status"] = "blocked_source_incomplete"
        result["errors"] = [{"code": "source_complete_must_be_true", "path": "source_complete"}]
        return result

    errors = result["errors"]
    def error(code, path):
        errors.append({"code": code, "path": path})

    if not _iso_date(date):
        error("invalid_session_date", "date")
    priors_valid = (isinstance(calendar_prior_dates, (list, tuple))
                    and len(calendar_prior_dates) == 20
                    and all(_iso_date(d) for d in calendar_prior_dates))
    if priors_valid:
        priors_valid = (len(set(calendar_prior_dates)) == 20
                        and list(calendar_prior_dates) == sorted(calendar_prior_dates)
                        and _iso_date(date) and all(d < date for d in calendar_prior_dates))
    if not priors_valid:
        error("require_20_distinct_ascending_prior_session_dates", "calendar_prior_dates")
    else:
        result["prior20_dates"] = list(calendar_prior_dates)
        result["prior_session_date"] = calendar_prior_dates[-1]

    classification = {}
    if not isinstance(universe, (list, tuple)):
        error("universe_must_be_sequence", "universe")
    else:
        for i, item in enumerate(universe):
            path = f"universe[{i}]"
            if not isinstance(item, Mapping):
                error("universe_row_must_be_mapping", path)
                continue
            symbol = item.get("symbol")
            if not isinstance(symbol, str) or not SYMBOL_PATTERN.fullmatch(symbol):
                error("invalid_universe_symbol", path + ".symbol")
                continue
            if symbol in classification:
                error("duplicate_universe_symbol", path + ".symbol")
                continue
            flag = item.get("etf_classification")
            if flag not in ("Y", "N", "unknown"):
                error("invalid_etf_classification", path + ".etf_classification")
                continue
            classification[symbol] = flag
    result["counts"]["universe"] = len(classification)

    daymaps = {}
    if not isinstance(daily, Mapping):
        error("daily_must_be_mapping", "daily")
    else:
        for symbol, bars in daily.items():
            if not isinstance(symbol, str) or not SYMBOL_PATTERN.fullmatch(symbol):
                error("invalid_daily_symbol", "daily")
                continue
            path = "daily." + symbol
            if not isinstance(bars, (list, tuple)):
                error("daily_bars_must_be_sequence", path)
                continue
            daymap, seen = {}, set()
            for i, bar in enumerate(bars):
                bar_path = f"{path}[{i}]"
                if not isinstance(bar, Mapping):
                    error("daily_bar_must_be_mapping", bar_path)
                    continue
                day = bar.get("session_date")
                if not _iso_date(day):
                    error("invalid_daily_session_date", bar_path + ".session_date")
                    continue
                if day in seen:
                    error("duplicate_daily_session_date", bar_path + ".session_date")
                seen.add(day)
                if _iso_date(date) and day >= date:
                    error("same_day_or_future_daily_bar", bar_path + ".session_date")
                if priors_valid and day in calendar_prior_dates:
                    daymap[day] = bar
                elif priors_valid and day < calendar_prior_dates[0]:
                    result["diagnostics"]["ignored_older_daily_rows"] += 1
                elif priors_valid and day < date:
                    # An off-calendar row inside the window cannot fill a required session.
                    # Reject it rather than quietly using an inconsistent daily calendar.
                    error("daily_date_inside_window_not_in_calendar", bar_path + ".session_date")
            daymaps[symbol] = daymap
        result["diagnostics"]["daily_symbols_outside_universe"] = sorted(
            s for s in daymaps if s not in classification and s not in CONTROLS)

    if errors:
        result["status"] = "blocked_invalid_inputs"
        return result

    eligible, metrics, exclusions = [], {}, {}
    for symbol in sorted(classification):
        if symbol in CONTROLS:
            reason = "forced_ETF_control_not_stock"
        elif classification[symbol] == "Y":
            reason = "explicit_ETF_Y"
        else:
            reason = None
        bars = daymaps.get(symbol, {})
        if reason is None and any(day not in bars for day in calendar_prior_dates):
            reason = "missing_one_or_more_prior20_session_daily_bars"
        if reason is None:
            prices = [(_number(bars[day].get("c")), _number(bars[day].get("v")))
                      for day in calendar_prior_dates]
            if any(c is None or v is None or c <= 0 or v < 0 for c, v in prices):
                reason = "invalid_prior20_close_or_volume"
        if reason is None:
            prior_close = prices[-1][0]
            median = _median([_exact_product(c, v) for c, v in prices])
            calculated = {"prior_close": str(prior_close),
                          "median_prior20_daily_close_times_volume": str(median)}
            metrics[symbol] = calculated
            if prior_close < 5:
                reason = "prior_close_below_5"
            elif median < 20_000_000:
                reason = "prior20_median_dollar_volume_below_20m"
        if reason:
            exclusions.setdefault(reason, []).append(symbol)
        else:
            eligible.append(symbol)

    ranked = sorted(eligible, key=lambda s: (
        Decimal(metrics[s]["median_prior20_daily_close_times_volume"]).copy_negate(), s))
    top = ranked[:64]
    top_set = set(top)
    sampled = sorted((s for s in eligible if s not in top_set), key=lambda s: (
        sha256(f"s500_flush_v1|{date}|{s}".encode()).hexdigest(), s))[:32]
    overlay = [s for s in STORAGE if s in eligible]
    selected = sorted(set(top + sampled + overlay + list(CONTROLS)))
    selected_set, eligible_set = set(selected), set(eligible)
    reasons = {symbol: reason for reason, symbols in exclusions.items() for symbol in symbols}
    rows = []
    for symbol in sorted(set(classification) | set(CONTROLS)):
        is_control = symbol in CONTROLS
        roles = []
        if symbol in top_set:
            roles.append("prior20_dollar_volume_top64")
        if symbol in sampled:
            roles.append("remaining_eligible_sha256_first32")
        if symbol in overlay:
            roles.append("eligible_storage_overlay")
        if is_control:
            roles.append("forced_ETF_control")
        rows.append({
            "case_id": date + "__" + symbol, "date": date, "symbol": symbol,
            "in_supplied_universe": symbol in classification,
            "etf_classification": classification.get(symbol, "unknown"),
            "classification_unknown": classification.get(symbol, "unknown") == "unknown",
            "known_forced_ETF_identity": is_control,
            "target_role": "benchmark" if is_control else "stock_candidate",
            "stock_eligible": symbol in eligible_set,
            "selected": symbol in selected_set, "roles": roles,
            "selection_metrics": metrics.get(symbol),
            "exclusion_reason": "forced_ETF_control_not_stock" if is_control else reasons.get(symbol),
            "prior_session_date": calendar_prior_dates[-1],
        })
    result.update({
        "status": "ready", "rows": rows, "metrics": metrics,
        "selected_symbols": selected, "eligible_symbols": sorted(eligible),
        "excluded_by_reason": dict(sorted(exclusions.items())),
        "selected_top64": top, "selected_hash32": sampled,
        "selected_storage": overlay, "forced_controls": list(CONTROLS),
    })
    result["counts"].update({"eligible_stocks": len(eligible), "selected": len(selected),
                             "selected_stocks": len(selected) - 2, "selected_controls": 2})
    return result
