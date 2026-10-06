"""Zero-or-hero phase 1: same-day-expiry (0DTE) index options until the account doubles.

Once a day, at the configured entry time, buy calls (or puts, following the day's direction)
expiring today, a set distance beyond the price, with a set fraction of the account. A resting
limit sells at `take` times the cost; anything still open is sold at `exit_at` (Alpaca
liquidates expiring positions it cannot exercise in the last hour, so we are flat before).
When the account reaches `switch_at` times its start, phase 1 ends for good and the experiment
runs its normal (momentum) strategy. The attempt itself ends below the configured loss line.

In rehearsal nothing is sent: fills are simulated from live quotes (buy at the ask, the limit
fills when the bid reaches it, the time exit at the bid) on a simulated cash balance, so the
rehearsal shows what the rule would have done. Book: zdte.json next to the journal.
"""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

DEFAULTS = {"underlying": "SPY", "entry": "12:00", "offset": 0.003, "direction": "trend", "take": 5,
            "fraction": 1.0, "exit_at": "15:30", "switch_at": 2.0}


def settings(cfg: dict) -> dict:
    return {**DEFAULTS, **cfg.get("zero_dte", {})}


def occ(und: str, day: date, kind: str, strike: float) -> str:
    return f"{und}{day:%y%m%d}{kind}{int(round(strike * 1000)):08d}"


def strike_for(spot: float, kind: str, off: float) -> float:
    return float(math.ceil(spot * (1 + off))) if kind == "C" else float(math.floor(spot * (1 - off)))


def direction(p: dict, day_open: float, spot: float) -> str:
    if p["direction"] == "calls":
        return "C"
    return "C" if spot >= day_open else "P"


def hhmm(now) -> str:
    return now.strftime("%H:%M")


class Book:
    def __init__(self, path: Path, start_capital: float):
        self.path = path
        self.d = json.loads(path.read_text()) if path.exists() else {
            "phase": "zero_dte", "switched": None, "sim_cash": start_capital, "start": start_capital,
            "today": None, "history": []}

    def go_live(self, day: str) -> None:
        """Rehearsal over: start the real book on the account, keeping the rehearsal for the record."""
        rehearsal = {k: v for k, v in self.d.items() if k != "rehearsal"}
        self.d = {"phase": "zero_dte", "switched": None, "start": self.d["start"], "today": None, "history": [],
                  "mode": "live", "live_from": day, "rehearsal": rehearsal}

    def save(self) -> None:
        self.path.write_text(json.dumps(self.d, indent=1, ensure_ascii=False) + "\n")

    @property
    def active(self) -> bool:
        return self.d["phase"] == "zero_dte"

    def trade_today(self, day: str) -> dict | None:
        t = self.d["today"]
        return t if t and t["date"] == day else None

    def close(self, proceeds: float, how: str, when: str) -> dict:
        t = self.d["today"]
        t.update({"status": "closed", "proceeds": round(proceeds, 2), "exit": how, "closed_at": when,
                  "ret": round(proceeds / t["paid"] - 1, 3) if t["paid"] else -1.0})
        if t.get("simulated") and "sim_cash" in self.d:
            self.d["sim_cash"] = round(self.d["sim_cash"] + proceeds, 2)
        self.d["history"].append(dict(t))
        return t

    def maybe_switch(self, equity: float, p: dict, day: str) -> str | None:
        if self.active and equity >= p["switch_at"] * self.d["start"]:
            self.d["phase"], self.d["switched"] = "momentum", day
            return (f"账户 ${equity:,.2f} 达到起始资金的 {p['switch_at']:g} 倍：末日阶段结束，"
                    f"从现在起永久改用动量策略")
        return None
