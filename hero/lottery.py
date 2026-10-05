"""Lottery sleeve: small, capped bets on earnings calls; stop after one big win.

Rules (config "lottery", candidate from research/2026-10-02-ratchet.md):
- one bet per ISO week: among pool stocks reporting this week, the one with the strongest
  126-day momentum;
- buy one call about `otm` above the price, first expiry on/after the reaction day, near the
  close of the last session before the report; if one contract costs more than `stake_max`,
  walk further out of the money (up to `max_otm`), otherwise skip the week;
- sell at the first cycle of the reaction day (the open after the report);
- the sleeve stops for good once a bet returns 10x or more, or its pot doubles, or the pot can no
  longer pay for a bet. While it runs, its pot is kept out of the momentum book's sizing.
The ledger lives in lottery.json next to the journal (saved in rehearsal too: it only tracks
this sleeve), so the sleeve can be audited and replayed.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

ENTRY_AFTER = (15, 30)  # ET: buy in the last half hour, close to where the backtest bought
DEFAULTS = {"stake_max": 50.0, "budget": 200.0, "otm": 0.10, "max_otm": 0.25, "hit": 9.0}


def settings(cfg: dict) -> dict:
    return {**DEFAULTS, **cfg.get("lottery", {})}


def prev_weekday(d: date) -> date:
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def next_weekday(d: date) -> date:
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def schedule(report: dict) -> tuple[date, date]:
    """(entry day, exit day) for a report on day E.
    pre-market:  buy the session before E, sell at E's open.
    post-market: buy on E, sell at the next session's open.
    unknown:     buy the session before E and sell the session after, which covers either timing."""
    e = date.fromisoformat(report["date"])
    if report["time"] == "pre-market":
        return prev_weekday(e), e
    if report["time"] == "post-market":
        return e, next_weekday(e)
    return prev_weekday(e), next_weekday(e)


def pick_for_week(calendar: dict | None, momentum: dict[str, float], today: date) -> dict | None:
    """The stock reporting this ISO week (entry day today or later) with the strongest momentum."""
    week = today.isocalendar()[:2]
    best = None
    for sym, mom in momentum.items():
        for r in (calendar or {}).get("reports", {}).get(sym, []):
            entry, exit_ = schedule(r)
            if entry.isocalendar()[:2] != week or entry < today:
                continue
            if best is None or mom > best["momentum"]:
                best = {"symbol": sym, "momentum": round(mom, 4), "report": r,
                        "entry": entry.isoformat(), "exit": exit_.isoformat()}
    return best


def choose_contract(contracts: list[dict], snaps: dict, spot: float, p: dict) -> tuple[dict, float] | None:
    """Nearest strike at or beyond spot*(1+otm) whose ask fits stake_max, walking out to max_otm."""
    for c in sorted(contracts, key=lambda c: float(c["strike_price"])):
        k = float(c["strike_price"])
        if k < spot * (1 + p["otm"]) or k > spot * (1 + p["max_otm"]):
            continue
        ask = ((snaps.get(c["symbol"]) or {}).get("latestQuote") or {}).get("ap")
        if ask and ask * 100 <= p["stake_max"]:
            return c, float(ask)
    return None


class Ledger:
    def __init__(self, path: Path, p: dict):
        self.path, self.p = path, p
        self.d = json.loads(path.read_text()) if path.exists() else {
            "budget": p["budget"], "pot": p["budget"], "done": None, "pick": None, "open": None, "bets": []}

    def save(self) -> None:
        self.path.write_text(json.dumps(self.d, indent=1, ensure_ascii=False) + "\n")

    @property
    def active(self) -> bool:
        return not self.d["done"]

    def reserve(self) -> float:
        """Cash the momentum book must leave alone while the sleeve runs."""
        return 0.0 if self.d["done"] else max(self.d["pot"], 0.0)

    def opened(self, bet: dict) -> None:
        self.d["pot"] -= bet["cost"]
        self.d["open"] = bet

    def closed(self, proceeds: float, when: str) -> dict:
        bet = {**self.d["open"], "proceeds": round(proceeds, 2), "closed": when}
        bet["ret"] = round(proceeds / bet["cost"] - 1, 3) if bet["cost"] else -1.0
        self.d["pot"] += proceeds
        self.d["open"] = None
        self.d["bets"].append(bet)
        if bet["ret"] >= self.p["hit"]:
            self.d["done"] = f"中奖：{bet['symbol']} 回报 {bet['ret'] + 1:.1f} 倍，彩票仓停止，资金转入动量"
        elif self.d["pot"] >= 2 * self.d["budget"]:
            self.d["done"] = f"彩票资金翻倍（${self.d['pot']:,.0f}），彩票仓停止，资金转入动量"
        elif self.d["pot"] < 5:
            self.d["done"] = f"彩票预算用完（剩 ${self.d['pot']:,.2f}），彩票仓停止"
        return bet


def entry_time(now: datetime) -> bool:
    return (now.hour, now.minute) >= ENTRY_AFTER
