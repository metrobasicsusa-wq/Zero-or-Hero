"""Shadow book: cast a net of cheap calls only in wild markets (simulated, never sends orders).

From the point-in-time net study (research/2026-10-06-net-pit.md): on the first trading day of a
week, if SPY's own past says the market is wild ("rebound" after an 8% fall, or 20-day volatility
in the top 20% of its past year), buy ~4-week calls 20% above the price on the 15 most volatile
stocks of this month's dynamic pool, half of the book spread evenly, and let them run: a 20x take,
else held to expiry. Fills are simulated from live quotes (buy at the ask, the take when the bid
reaches it, expiry at intrinsic value) on a separate $500 book, with fractional contracts so the
$500 can spread over 15 names (a real $500 account could only afford a few of them).
A round ends below $50 or at $10,000 and a fresh $500 round begins. Book: net.json in the journal.
"""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

DEFAULTS = {"enabled": False, "net": 15, "otm": 0.20, "take": 20, "fraction": 0.5, "start_capital": 500.0,
            "entry": "10:00", "min_days": 21, "max_days": 35, "target": 10_000.0, "end": 50.0}


def settings(cfg: dict) -> dict:
    return {**DEFAULTS, **cfg.get("net_shadow", {})}


def vol(closes: list[float]) -> float:
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a and b]
    if len(rets) < 3:
        return 0.0
    m = sum(rets) / len(rets)
    return math.sqrt(sum((r - m) ** 2 for r in rets) / len(rets) * 252)


def week_key(day: date) -> str:
    y, w, _ = day.isocalendar()
    return f"{y}-W{w:02d}"


class Book:
    def __init__(self, path: Path, start: float):
        self.path = path
        self.d = json.loads(path.read_text()) if path.exists() else {
            "cash": start, "start": start, "open": [], "history": [], "checked": None, "last_signal": None,
            "heroes": 0, "zeros": 0, "round": 1}

    def save(self) -> None:
        self.path.write_text(json.dumps(self.d, indent=1, ensure_ascii=False) + "\n")

    def close(self, t: dict, value_each: float, how: str, day: str) -> dict:
        proceeds = round(value_each * 100 * t["qty"], 2)
        self.d["cash"] = round(self.d["cash"] + proceeds, 2)
        done = {**t, "closed": day, "exit": how, "proceeds": proceeds,
                "ret": round(proceeds / t["paid"] - 1, 3) if t["paid"] else -1.0}
        self.d["open"] = [x for x in self.d["open"] if x["symbol"] != t["symbol"]]
        self.d["history"].append(done)
        self.d["history"] = self.d["history"][-200:]
        return done

    def round_check(self, p: dict) -> str | None:
        """Only when flat: a round ends below the end line or at the target."""
        if self.d["open"]:
            return None
        cash = self.d["cash"]
        if cash < p["end"] or cash >= p["target"]:
            hero = cash >= p["target"]
            self.d["heroes" if hero else "zeros"] += 1
            self.d["round"] += 1
            self.d["cash"] = self.d["start"]
            return (f"撒网演练第 {self.d['round'] - 1} 轮" + ("到 $10,000，成功" if hero else "低于 $50，归零")
                    + f"（${cash:,.2f}），重开 ${self.d['start']:,.0f}")
        return None
