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


def clock(now) -> str:
    """The time of day the rules compare with: `now` (New York time) as "HH:MM", three hours on from 12:00 on an
    early-close day."""
    hhmm = now.strftime("%H:%M")
    ec = early_close(now.date())
    if not ec or hhmm < SHIFT_FROM:
        return hhmm
    m = min(_minutes(hhmm) + _minutes(CLOSE) - _minutes(ec), 23 * 60 + 59)
    return f"{m // 60:02d}:{m % 60:02d}"
