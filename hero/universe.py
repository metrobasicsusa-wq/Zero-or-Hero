"""Monthly dynamic universe: the most traded stocks by average dollar volume over the last 63
trading days (price above $10, at least a year of history), plus broad and sector ETFs.

Rebuilt once per ET month from data known at the time (same rule as hero.research_universe),
saved to data/universe.json and archived per month so live picks can be audited later.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from hero.research_universe import ETFS, LOOKBACK_DV, MIN_PRICE, candidates, top_by_company

HISTORY_DAYS = 400  # calendar days: enough for a year of trading history
VERSION = 2  # bump when the selection rule changes, so this month's pool is rebuilt


def build(client, size: int, now: datetime) -> dict:
    stocks = candidates(client)  # {symbol: company}: operating companies only, no funds
    start = (now - timedelta(days=HISTORY_DAYS)).date().isoformat()
    ranked, seen = [], 0
    syms = list(stocks)
    for i in range(0, len(syms), 200):
        for sym, bars in client.daily_bars(syms[i:i + 200], start).items():
            seen += 1
            if len(bars) < 252 or float(bars[-1]["c"]) < MIN_PRICE:
                continue
            recent = bars[-LOOKBACK_DV:]
            ranked.append((sum(float(b["c"]) * float(b.get("v") or 0) for b in recent) / len(recent), sym))
    pool = top_by_company(ranked, stocks, size)
    return {"version": VERSION, "month": now.strftime("%Y-%m"), "built_at": now.isoformat(timespec="seconds"), "size": size,
            "method": f"top {size} operating companies (no ETFs/ETNs/funds, one share class each) by "
                      f"{LOOKBACK_DV}-day average dollar volume (IEX), price > ${MIN_PRICE}, >= 252 trading days "
                      f"of history; plus {len(ETFS)} broad and sector ETFs",
            "candidates": len(stocks), "with_data": seen, "stocks": pool, "etfs": ETFS}


def refresh(path: Path, client, size: int, now: datetime) -> str:
    current = load(path)
    if current and current["month"] == now.strftime("%Y-%m") and current["size"] == size \
            and current.get("version") == VERSION:
        return "fresh"
    data = build(client, size, now)
    if len(data["stocks"]) < size // 2:
        raise RuntimeError(f"only {len(data['stocks'])} stocks qualified; keeping the previous universe")
    text = json.dumps(data, indent=1) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    archive = path.parent / "universe" / f"{data['month']}.json"
    archive.parent.mkdir(exist_ok=True)
    archive.write_text(text)
    return f"built {data['month']}: {len(data['stocks'])} stocks + {len(ETFS)} ETFs from {data['candidates']} candidates"


def load(path: Path) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


def symbols(data: dict | None) -> list[str]:
    return sorted(set(data["stocks"]) | set(data["etfs"])) if data else []
