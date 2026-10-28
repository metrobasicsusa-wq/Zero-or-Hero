"""The bottom studies' signals as plain functions, shared by the follow card (hero.follow_card), the opportunity log
(hero.opportunities) and the ladder ledger's rule (hero.sleeve_rules.Ladder).

`cs` is the completed daily closes before the day, oldest first; `spot` the price now (15:50 for the 15:50 rules).

- down3: the third straight down close (hero.sleeve_rules.Streak): the last two completed closes each fell, the one
  before them did not, and the price now is under yesterday's close (research/*-bottom-long.json `down3`).
- green1: the first up close after three or more down closes (Streak with `green`; research `green1`).
- the first close `level` under the 20-session high: 21 closes with today's price (the backtests'
  closes[i - 20:i + 1]); yesterday was not under the line (research `dd8` for the ETFs, `dd20` for single names).
- the sector bounce at 10:00 (hero.sleeve_rules.Bounce): the sector fell `flush` or more over its prior one to three
  sessions and is `bounce` or more above its own open at 10:00 (research/*-bounce.json `bounce1`).
"""
from __future__ import annotations

from datetime import date, timedelta

LEVEL = 0.08
WINDOW = 21            # closes in the high's window, today's price included (the backtests: closes[i - 20:i + 1])


def down_run(cs: list[float]) -> int:
    """How many completed closes in a row fell under the one before (ending yesterday)."""
    k = 0
    while k + 1 < len(cs) and cs[-1 - k] < cs[-2 - k]:
        k += 1
    return k


def fast_signal(cs: list[float], spot: float) -> bool:
    """Streak's third down day: the last two completed closes each fell, the one before them did not, now under yesterday."""
    return len(cs) >= 4 and spot < cs[-1] and cs[-1] < cs[-2] < cs[-3] and cs[-3] >= cs[-4]


def green_signal(cs: list[float], spot: float) -> bool:
    """The first up close after three or more down closes (Streak with `green`)."""
    return len(cs) >= 4 and spot > cs[-1] and cs[-1] < cs[-2] < cs[-3] < cs[-4]


def depth(cs: list[float], spot: float, level: float = LEVEL) -> dict:
    """Today's and yesterday's distance under the 20-session high (21 closes), whether today is the first break, and
    the price at which today would close at the line."""
    hi = max(cs[-(WINDOW - 1):] + [spot])
    hi_prev = max(cs[-WINDOW:])
    dd, dd_prev = spot / hi - 1, cs[-1] / hi_prev - 1
    return {"hi": hi, "dd": dd, "dd_prev": dd_prev, "first": dd <= -level and dd_prev > -level, "armed": dd_prev > -level,
            "trigger": round(hi * (1 - level), 2)}


def bounce(sector_cs: list[float], sector_open: float, sector_ten: float, flush: float = 0.05, up: float = 0.01) -> dict:
    """The sector's drop over its prior one to three closes (to yesterday) and its 10:00 move off today's open."""
    drop = min(sector_cs[-1] / sector_cs[-1 - k] - 1 for k in (1, 2, 3)) if len(sector_cs) >= 4 else 0.0
    m10 = sector_ten / sector_open - 1 if sector_open else 0.0
    return {"drop": drop, "m10": m10, "on": drop <= -flush and m10 >= up}


def is_monthly(exp: str, listed: set[str]) -> bool:
    """The depth line's expiry: the third Friday, or the Thursday before it when that Friday is not listed (a holiday)."""
    d = date.fromisoformat(exp)
    if d.weekday() == 4 and 15 <= d.day <= 21:
        return True
    fri = d + timedelta(days=1)
    return d.weekday() == 3 and 15 <= fri.day <= 21 and fri.isoformat() not in listed
