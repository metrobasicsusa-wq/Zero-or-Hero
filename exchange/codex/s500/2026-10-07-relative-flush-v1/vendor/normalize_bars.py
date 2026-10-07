"""Strict provider timestamp normalization; duplicates and bad records survive.

No market requests or account operations. Nonzero subsecond timestamps must be
rejected before Python datetime could truncate nanoseconds to microseconds.
"""

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
import re
from zoneinfo import ZoneInfo

VERSION = "provider-minute-normalization-v1"
_RFC3339 = re.compile(
    r"(?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2})[Tt]"
    r"(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})"
    r"(?:\.(?P<fraction>[0-9]{1,9}))?"
    r"(?P<offset>[Zz]|[+-][0-9]{2}:[0-9]{2})"
)
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_CLOCK = re.compile(r"[0-9]{2}:[0-9]{2}")


def _opening(date, session_open_et):
    if not isinstance(date, str) or not _DATE.fullmatch(date):
        raise ValueError("date must use YYYY-MM-DD")
    if not isinstance(session_open_et, str) or not _CLOCK.fullmatch(session_open_et):
        raise ValueError("session_open_et must use HH:MM")
    try:
        naive = datetime.strptime(date + " " + session_open_et, "%Y-%m-%d %H:%M")
    except ValueError:
        raise ValueError("invalid session date or opening time") from None
    eastern = ZoneInfo("America/New_York")
    first = naive.replace(tzinfo=eastern, fold=0)
    second = naive.replace(tzinfo=eastern, fold=1)
    if first.utcoffset() != second.utcoffset():
        raise ValueError("ambiguous or nonexistent session opening time")
    if first.astimezone(timezone.utc).astimezone(eastern).replace(tzinfo=None) != naive:
        raise ValueError("nonexistent session opening time")
    return first.astimezone(timezone.utc)


def _timestamp_minute(value, opening):
    if not isinstance(value, str):
        return None, "timestamp_not_string"
    match = _RFC3339.fullmatch(value)
    if not match:
        return None, "invalid_rfc3339_timestamp"
    hour, minute, second = (int(match[name]) for name in ("hour", "minute", "second"))
    if hour > 23 or minute > 59 or second > 59:
        return None, "invalid_or_unsupported_clock_component"
    offset = match["offset"]
    if offset.lower() == "z":
        tz = timezone.utc
    else:
        offset_hour, offset_minute = int(offset[1:3]), int(offset[4:6])
        if offset_hour > 23 or offset_minute > 59:
            return None, "invalid_utc_offset"
        if offset == "-00:00":
            return None, "unknown_local_offset"
        sign = 1 if offset[0] == "+" else -1
        tz = timezone(sign * timedelta(hours=offset_hour, minutes=offset_minute))
    try:
        year, month, day = (int(part) for part in match["date"].split("-"))
        whole_second = datetime(year, month, day, hour, minute, second, tzinfo=tz)
    except ValueError:
        return None, "invalid_calendar_date"
    fraction = match["fraction"] or ""
    # Do not pass fractions to datetime: it discards precision past 6 digits.
    if second != 0 or any(digit != "0" for digit in fraction):
        return None, "non_minute_timestamp"
    try:
        delta = whole_second.astimezone(timezone.utc) - opening
    except (ValueError, OverflowError):
        return None, "utc_timestamp_out_of_range"
    seconds = delta.days * 86400 + delta.seconds
    if delta.microseconds or seconds % 60:
        return None, "non_minute_timestamp"
    return seconds // 60, None


def normalize_provider_bars(provider_bars, date, session_open_et="09:30"):
    """Return {bars: list, issues: list}, preserving order, fields, duplicates.

    Valid t becomes an integer relative to the New York session open. Invalid
    t remains its original string (non-string t is stringified), so flush_engine
    cannot silently treat it as a valid integer offset. Each invalid timestamp
    contributes an issue. Duplicate valid offsets are retained and diagnosed;
    the engine determines which prefix/window they invalidate. No sorting,
    deduplication, time clipping, forward filling, or OHLC changes occur.
    """
    opening = _opening(date, session_open_et)
    if isinstance(provider_bars, (str, bytes, Mapping)):
        raise ValueError("provider_bars must be an iterable of bar mappings")
    try:
        source = iter(provider_bars)
    except TypeError:
        raise ValueError("provider_bars must be an iterable of bar mappings") from None
    normalized, issues, first_index = [], [], {}
    for index, bar in enumerate(source):
        if not isinstance(bar, Mapping):
            normalized.append({"t": None})
            issues.append({"index": index, "kind": "bar_not_mapping"})
            continue
        output = dict(bar)
        original = bar.get("t")
        minute, error = _timestamp_minute(original, opening)
        if error:
            output["t"] = original if isinstance(original, str) else str(original)
            issues.append({"index": index, "kind": error, "timestamp": output["t"]})
        else:
            output["t"] = minute
            if minute in first_index:
                issues.append({"index": index, "kind": "duplicate_minute", "minute": minute,
                               "first_index": first_index[minute]})
            else:
                first_index[minute] = index
        normalized.append(output)
    return {"bars": normalized, "issues": issues}
