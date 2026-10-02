"""Long single-leg options: calls on the strongest trends, puts on the index in a bear regime.

Risk per trade is capped at the premium paid, so no margin or spread approval is needed.
"""

from __future__ import annotations

from datetime import date, datetime, timezone


def dte(expiration: str, today: date) -> int:
    return (date.fromisoformat(expiration) - today).days


def mid(snapshot: dict, max_spread: float = 0.15) -> float | None:
    q = snapshot.get("latestQuote") or {}
    bid, ask = q.get("bp") or 0, q.get("ap") or 0
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    # Skip illiquid contracts with spreads wider than max_spread of mid.
    m = (bid + ask) / 2
    return m if (ask - bid) / m <= max_spread else None


def parse_ts(t: str) -> datetime:
    """RFC 3339 timestamp, as Alpaca sends it (often with nanoseconds and a Z suffix)."""
    t = t.replace("Z", "+00:00")
    if "." in t:
        head, rest = t.split(".", 1)
        frac = "".join(ch for ch in rest if ch.isdigit())
        tz = rest[len(frac):]
        t = f"{head}.{frac[:6]}{tz}"
    ts = datetime.fromisoformat(t)
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def quote_age_s(snapshot: dict, now: datetime | None = None) -> float | None:
    t = (snapshot.get("latestQuote") or {}).get("t")
    if not t:
        return None
    return ((now or datetime.now(timezone.utc)) - parse_ts(t)).total_seconds()


def pick_contract(contracts: list[dict], snapshots: dict[str, dict], spot: float,
                  p: dict, budget: float | None = None, now: datetime | None = None) -> tuple[dict, float] | None:
    """Contract whose delta is closest to the target (falls back to moneyness).

    Optional filters from p: max_spread, delta_min/delta_max, max_quote_age_s; and with a
    budget, only contracts whose full premium (mid x 100) fits are considered."""
    best, best_score = None, float("inf")
    for c in contracts:
        snap = snapshots.get(c["symbol"])
        if not snap or not c.get("tradable", True):
            continue
        price = mid(snap, p.get("max_spread", 0.15))
        if price is None:
            continue
        if budget is not None and price * 100 > budget:
            continue
        if "max_quote_age_s" in p:
            age = quote_age_s(snap, now)
            if age is None or age > p["max_quote_age_s"]:
                continue
        delta = (snap.get("greeks") or {}).get("delta")
        if delta is not None and not p.get("delta_min", 0) <= abs(delta) <= p.get("delta_max", 1):
            continue
        if delta is None and "delta_min" in p:
            continue  # a delta band was asked for; without greeks we cannot honour it
        if delta is not None:
            score = abs(abs(delta) - p["target_delta"])
        else:
            score = abs(float(c["strike_price"]) / spot - 1)
        if score < best_score:
            best, best_score = (c, price), score
    return best


def reject_reasons(contracts: list[dict], snapshots: dict[str, dict], p: dict, budget: float | None = None,
                   now: datetime | None = None) -> dict[str, int]:
    """Why each contract failed pick_contract's filters (first failing filter only), for the skip log."""
    out: dict[str, int] = {}

    def bump(k):
        out[k] = out.get(k, 0) + 1

    for c in contracts:
        snap = snapshots.get(c["symbol"])
        if not snap or not c.get("tradable", True):
            bump("无报价")
            continue
        raw = mid(snap, 1.0)
        if raw is None:
            bump("无报价")
        elif budget is not None and raw * 100 > budget:
            bump("超预算")
        elif mid(snap, p.get("max_spread", 0.15)) is None:
            bump("价差太宽")
        elif "max_quote_age_s" in p and (quote_age_s(snap, now) is None or quote_age_s(snap, now) > p["max_quote_age_s"]):
            bump("报价过旧")
        else:
            delta = (snap.get("greeks") or {}).get("delta")
            if delta is None and "delta_min" in p:
                bump("无 delta")
            elif delta is not None and not p.get("delta_min", 0) <= abs(delta) <= p.get("delta_max", 1):
                bump("delta 不在范围")
    return out


def should_exit(position: dict, today: date, expiration: str, p: dict) -> str | None:
    pl = float(position.get("unrealized_plpc") or 0)
    if pl >= p["take_profit"]:
        return f"take_profit {pl:.0%}"
    if pl <= p["stop_loss"]:
        return f"stop_loss {pl:.0%}"
    if dte(expiration, today) <= p["exit_dte"]:
        return f"dte<={p['exit_dte']}"
    return None


def why_exit(position: dict, today: date, expiration: str, p: dict) -> str:
    pl = float(position.get("unrealized_plpc") or 0)
    if pl >= p["take_profit"]:
        return f"盈利 {pl:+.0%}，达到止盈线 {p['take_profit']:+.0%}"
    if pl <= p["stop_loss"]:
        return f"亏损 {pl:+.0%}，触及止损线 {p['stop_loss']:+.0%}"
    return f"距到期只剩 {dte(expiration, today)} 天（≤{p['exit_dte']} 天就平仓，避免时间价值加速流失）"


def occ_expiration(symbol: str) -> str:
    """Expiration date from an OCC symbol like AAPL250117C00150000."""
    tail = symbol[-15:]
    return f"20{tail[0:2]}-{tail[2:4]}-{tail[4:6]}"
