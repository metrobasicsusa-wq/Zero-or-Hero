"""The rules' clock on an early-close day (2026-11-24, owner: 「全部看你」 on the half-day fix).

Every rule in the trading code reads the time of day as an "HH:MM" string and compares it with fixed times written for
a 16:00 close: same-day options out at 15:30, overnight tickets out at 15:50, buys from 15:40 or 15:45, the last
cycle at 15:50, the lottery after 15:30. On the three NYSE early closes (13:00) the market shuts before any of them,
so expiring options would expire unsold and the afternoon buys would never run.

`clock(now)` is that string with one change: on an early-close day, from 12:00 on, it reads three hours later
(12:30 -> "15:30", 12:50 -> "15:50", 13:00 -> "16:00"). The morning keeps its own times (09:35, 10:00); a window that
ends at 15:00 ends at 12:00; the last hour before the early close plays the last hour of a full day. No rule's
number changes. Equity options stop trading at 13:00 too (index options 13:15), so a 15:30 exit becomes 12:30.

The early closes (NYSE's rules, checked 2026-11-23, the same as hero.calendar_week.early_close, which a test holds
it to): the day after Thanksgiving; Christmas Eve on a Monday to Thursday (on a Friday it is the observed Christmas
closure); July 3 on a Monday to Thursday (on a Friday it is the observed Independence Day). It is self-contained so
the trading code does not import the research modules.
"""

from __future__ import annotations

from datetime import date, timedelta

CLOSE, EARLY, SHIFT_FROM = "16:00", "13:00", "12:00"


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def thanksgiving(y: int) -> date:
    first = date(y, 11, 1)
    return first + timedelta(days=(3 - first.weekday()) % 7 + 21)


def early_close(day: date) -> str | None:
    """"13:00" on an NYSE early-close day, else None."""
    if day == thanksgiving(day.year) + timedelta(days=1):
        return EARLY
    if (day.month, day.day) in ((12, 24), (7, 3)) and day.weekday() < 4:
        return EARLY
    return None


def _easter(y: int) -> date:
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    return date(y, (h + l_ - 7 * m + 114) // 31, (h + l_ - 7 * m + 114) % 31 + 1)


def _nth(y: int, month: int, weekday: int, n: int) -> date:
    """The n-th `weekday` (Monday 0) of a month; n = -1 for the last one."""
    if n > 0:
        first = date(y, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = date(y + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    return d - timedelta(days=1) if d.weekday() == 5 else d + timedelta(days=1) if d.weekday() == 6 else d


def holidays(y: int) -> set[date]:
    """NYSE full-day closures in year `y` (2026-11-27: the rules' "next session" skipped only weekends, so a ticket
    meant to be sold the day before a Friday expiry was dated Thanksgiving and sold the Friday morning instead).
    The usual rules, the same as hero.weekbook.holidays, which a test holds it to: a Saturday holiday is observed on
    the Friday, a Sunday one on the Monday, except New Year's Day on a Saturday (no closure the Friday before)."""
    out = {_observed(date(y, 1, 1)), _nth(y, 1, 0, 3), _nth(y, 2, 0, 3), _easter(y) - timedelta(days=2),
           _nth(y, 5, 0, -1), _observed(date(y, 7, 4)), _nth(y, 9, 0, 1), _nth(y, 11, 3, 4), _observed(date(y, 12, 25))}
    if y >= 2022:
        out.add(_observed(date(y, 6, 19)))
    return {d for d in out if d.year == y}


def is_session(day: date) -> bool:
    """The exchange is open that day (early closes count)."""
    return day.weekday() < 5 and day not in holidays(day.year)


def next_session(day: date) -> date:
    day += timedelta(days=1)
    while not is_session(day):
        day += timedelta(days=1)
    return day


def prev_session(day: date) -> date:
    day -= timedelta(days=1)
    while not is_session(day):
        day -= timedelta(days=1)
    return day


def clock(now) -> str:
    """The time of day the rules compare with: `now` (New York time) as "HH:MM", three hours on from 12:00 on an
    early-close day."""
    hhmm = now.strftime("%H:%M")
    ec = early_close(now.date())
    if not ec or hhmm < SHIFT_FROM:
        return hhmm
    m = min(_minutes(hhmm) + _minutes(CLOSE) - _minutes(ec), 23 * 60 + 59)
    return f"{m // 60:02d}:{m % 60:02d}"
