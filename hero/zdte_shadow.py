"""Shadow log: the $500 same-day rule on single stocks, with real quotes and no orders.

research/2026-10-07-0dte-names.md found TSLA and NVDA losing far less than SPY under the live rule,
but it charged every name the same 10% cost, and single-stock same-day options usually trade wider.
So on days a name lists an expiry that day, at the entry time this picks the contract the rule would
(a call above the open, else a put, the nearest listed strike at least `offset` beyond the price),
records its real bid and ask, "buys" at the ask, then on each cycle checks the bid: the 3x take when
the bid reaches it, else out at the bid at the exit time. SPY is logged the same way, so the spreads
compare like for like. Simulated only: it never sends an order. Book: zdte_shadow.json in the journal.
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULTS = {"enabled": False, "symbols": ["TSLA", "NVDA", "SPY"], "entry": "10:00", "offset": 0.006, "take": 3,
            "exit_at": "15:30"}


def settings(cfg: dict) -> dict:
    return {**DEFAULTS, **cfg.get("zdte_shadow", {})}


def pick(contracts: list[dict], kind: str, spot: float, off: float) -> dict | None:
    """The nearest listed strike at least `off` beyond the price."""
    ks = sorted(contracts, key=lambda c: float(c["strike_price"]))
    if kind == "C":
        return next((c for c in ks if float(c["strike_price"]) >= spot * (1 + off)), None)
    below = [c for c in ks if float(c["strike_price"]) <= spot * (1 - off)]
    return below[-1] if below else None


def spread(bid: float, ask: float) -> float | None:
    mid = (bid + ask) / 2
    return round((ask - bid) / mid, 4) if bid > 0 and ask > 0 else None


class Book:
    def __init__(self, path: Path):
        self.path = path
        self.d = json.loads(path.read_text()) if path.exists() else {"days": {}}

    def save(self) -> None:
        days = dict(sorted(self.d["days"].items())[-120:])  # about six months is plenty
        self.path.write_text(json.dumps({**self.d, "days": days}, indent=1, ensure_ascii=False) + "\n")

    def today(self, day: str) -> dict:
        return self.d["days"].setdefault(day, {})

    def summary(self) -> dict:
        """Per symbol: trades, average return, how often the take was hit, median entry spread."""
        out = {}
        for tickets in self.d["days"].values():
            for sym, t in tickets.items():
                if t.get("status") != "closed":
                    continue
                s = out.setdefault(sym, {"n": 0, "sum": 0.0, "takes": 0, "spreads": []})
                s["n"] += 1
                s["sum"] += t["ret"]
                s["takes"] += t["exit"] == "take"
                if t.get("spread") is not None:
                    s["spreads"].append(t["spread"])
        for s in out.values():
            sp = sorted(s.pop("spreads"))
            s["mean"] = round(s.pop("sum") / s["n"], 3)
            s["median_spread"] = sp[len(sp) // 2] if sp else None
        return out
