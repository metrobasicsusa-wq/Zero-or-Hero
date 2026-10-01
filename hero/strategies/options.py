"""Long single-leg options: calls on the strongest trends, puts on the index in a bear regime.

Risk per trade is capped at the premium paid, so no margin or spread approval is needed.
"""

from __future__ import annotations

from datetime import date


def dte(expiration: str, today: date) -> int:
    return (date.fromisoformat(expiration) - today).days


def mid(snapshot: dict) -> float | None:
    q = snapshot.get("latestQuote") or {}
    bid, ask = q.get("bp") or 0, q.get("ap") or 0
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    # Skip illiquid contracts with spreads wider than 15% of mid.
    m = (bid + ask) / 2
    return m if (ask - bid) / m <= 0.15 else None


def pick_contract(contracts: list[dict], snapshots: dict[str, dict], spot: float,
                  p: dict) -> tuple[dict, float] | None:
    """Contract whose delta is closest to the target (falls back to moneyness)."""
    best, best_score = None, float("inf")
    for c in contracts:
        snap = snapshots.get(c["symbol"])
        if not snap or not c.get("tradable", True):
            continue
        price = mid(snap)
        if price is None:
            continue
        delta = (snap.get("greeks") or {}).get("delta")
        if delta is not None:
            score = abs(abs(delta) - p["target_delta"])
        else:
            score = abs(float(c["strike_price"]) / spot - 1)
        if score < best_score:
            best, best_score = (c, price), score
    return best


def should_exit(position: dict, today: date, expiration: str, p: dict) -> str | None:
    pl = float(position.get("unrealized_plpc") or 0)
    if pl >= p["take_profit"]:
        return f"take_profit {pl:.0%}"
    if pl <= p["stop_loss"]:
        return f"stop_loss {pl:.0%}"
    if dte(expiration, today) <= p["exit_dte"]:
        return f"dte<={p['exit_dte']}"
    return None


def occ_expiration(symbol: str) -> str:
    """Expiration date from an OCC symbol like AAPL250117C00150000."""
    tail = symbol[-15:]
    return f"20{tail[0:2]}-{tail[2:4]}-{tail[4:6]}"
