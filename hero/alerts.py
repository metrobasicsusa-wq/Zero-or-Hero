"""Intraday defence: a price circuit breaker and keyword news alerts.

Both can only make the bot more careful: no new buys or options, tighter stops. Neither sells
on its own; exits stay with the stops and the normal rules. Every alert is journaled with its
evidence so the rules can be measured later.
"""

from __future__ import annotations

import re

DEFAULTS = {
    "index": "SPY",          # market gauge
    "index_drop": -0.02,     # index down this much on the day -> market breaker
    "fear": "VIXY",          # volatility ETF
    "fear_jump": 0.15,       # up this much on the day -> market breaker
    "stock_drop": -0.08,     # a held stock down this much on the day -> tighten its stop
    "tight_stop_pct": 0.03,  # tightened stop: this far below the current price (never lowered)
}

# Headline patterns that mark a stock-specific shock. Kept narrow on purpose: a false alert
# only blocks adding for a day, but a noisy list would block everything.
NEWS_PATTERNS = {
    "停牌": r"\btrading (halt|halted|suspended)\b|\bhalts? trading\b",
    "监管调查": r"\b(sec|doj|ftc|justice department)\b.{0,40}\b(probe|investigat\w*|charges?|subpoena\w*|sues?)\b"
                r"|\bsubpoena\w*\b",
    "欺诈": r"\b(fraud|accounting irregularit\w+)\b",
    "破产": r"\b(bankruptcy|chapter 11|insolven\w+)\b",
    "下调指引": r"\b(cuts?|lowers?|slashes?|withdraws?)\b.{0,30}\b(guidance|outlook|forecast)\b"
                r"|\b(guidance|outlook|forecast) (cut|lowered|slashed|withdrawn)\b",
    "增发": r"\b(secondary|stock|share|equity) offering\b|\bdilut\w+\b",
    "降级": r"\bdowngrade[sd]?\b",
    "高管离职": r"\b(ceo|cfo|chief executive|chief financial officer)\b.{0,30}\b(resign\w*|steps? down|ousted|departs?|fired)\b",
    "做空报告": r"\bshort[- ]seller\b|\bshort report\b",
    "退市": r"\bdelist\w*\b",
    "召回": r"\bproduct recall\b|\brecall(s|ed|ing)?\b.{0,30}\b(vehicles?|cars|units|products?|devices?)\b",
}
_COMPILED = {k: re.compile(v, re.I) for k, v in NEWS_PATTERNS.items()}


def settings(cfg: dict) -> dict:
    return {**DEFAULTS, **cfg.get("risk", {}).get("circuit", {})}


def day_move(snap: dict | None) -> float | None:
    """Today's move from the previous close to the latest trade."""
    if not snap:
        return None
    last = (snap.get("latestTrade") or {}).get("p")
    prev = (snap.get("prevDailyBar") or {}).get("c")
    return last / prev - 1 if last and prev else None


def market_breaker(snaps: dict, c: dict) -> str | None:
    """Why the market breaker is on, or None."""
    idx, fear = day_move(snaps.get(c["index"])), day_move(snaps.get(c["fear"]))
    reasons = []
    if idx is not None and idx <= c["index_drop"]:
        reasons.append(f"{c['index']} 盘中 {idx:+.1%}（线 {c['index_drop']:+.0%}）")
    if fear is not None and fear >= c["fear_jump"]:
        reasons.append(f"{c['fear']} 盘中 {fear:+.1%}（线 {c['fear_jump']:+.0%}）")
    return "；".join(reasons) or None


def stock_drops(snaps: dict, held: set[str], c: dict) -> dict[str, float]:
    out = {}
    for sym in held:
        m = day_move(snaps.get(sym))
        if m is not None and m <= c["stock_drop"]:
            out[sym] = m
    return out


def match(headline: str) -> list[str]:
    return [k for k, rx in _COMPILED.items() if rx.search(headline or "")]


def news_alerts(items: list[dict], symbols: set[str]) -> dict[str, list[dict]]:
    """Headlines that hit a pattern, per symbol of ours they mention."""
    out: dict[str, list[dict]] = {}
    for n in items:
        hits = match(n.get("headline", ""))
        if not hits:
            continue
        for sym in set(n.get("symbols") or []) & symbols:
            out.setdefault(sym, []).append({"id": n.get("id"), "headline": n.get("headline"), "matched": hits,
                                            "source": n.get("source"), "url": n.get("url"),
                                            "created_at": n.get("created_at")})
    return out


def tight_stop(current: float, c: dict) -> float:
    return round(current * (1 - c["tight_stop_pct"]), 2)
