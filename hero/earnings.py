"""Earnings calendar (Alpha Vantage EARNINGS_CALENDAR), fetched at most once per ET day.

Only the experiments' own symbols are kept. Each fetch is also archived by date, so a later
backtest can use what was known then rather than revised dates. A symbol with no row means
"nothing within the horizon" (or an ETF), never "no earnings": callers treat a missing or stale
calendar as unknown.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

URL = "https://www.alphavantage.co/query"
HORIZON = "3month"
HORIZON_DAYS = 90
MAX_AGE_DAYS = 3  # older than this and the calendar counts as unavailable


def fetch(key: str) -> list[dict]:
    r = requests.get(URL, params={"function": "EARNINGS_CALENDAR", "horizon": HORIZON, "apikey": key}, timeout=60)
    r.raise_for_status()
    if not r.text.startswith("symbol,"):
        raise RuntimeError(f"unexpected earnings calendar response: {r.text[:200].replace(key, '***')}")
    return list(csv.DictReader(io.StringIO(r.text)))


def build(rows: list[dict], symbols: set[str], fetched_at: datetime) -> dict:
    out: dict[str, list[dict]] = {}
    for r in rows:
        if r["symbol"] in symbols:
            out.setdefault(r["symbol"], []).append(
                {"date": r["reportDate"], "time": r.get("timeOfTheDay") or "unknown",
                 "estimate": r.get("estimate") or None, "fiscal_end": r.get("fiscalDateEnding")})
    for v in out.values():
        v.sort(key=lambda x: x["date"])
    return {"source": f"alphavantage EARNINGS_CALENDAR horizon={HORIZON}",
            "fetched_at": fetched_at.isoformat(timespec="seconds"),
            "fetched_on": fetched_at.date().isoformat(),
            "horizon_days": HORIZON_DAYS, "symbols": sorted(symbols), "reports": out}


def refresh(path: Path, symbols: set[str], key: str, now: datetime) -> str:
    """Fetch unless today's (ET) calendar is already on disk with every symbol. Returns what happened."""
    current = load(path)
    if current and current["fetched_on"] == now.date().isoformat() and symbols <= set(current["symbols"]):
        return "fresh"
    data = build(fetch(key), symbols, now)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=1, ensure_ascii=False) + "\n"
    path.write_text(text)
    archive = path.parent / "earnings" / f"{data['fetched_on']}.json"
    archive.parent.mkdir(exist_ok=True)
    archive.write_text(text)
    return f"fetched {sum(len(v) for v in data['reports'].values())} reports for {len(symbols)} symbols"


def load(path: Path) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


def usable(data: dict | None, today: date) -> bool:
    if not data:
        return False
    return (today - date.fromisoformat(data["fetched_on"])).days <= MAX_AGE_DAYS


def next_report(data: dict | None, symbol: str, today: date) -> dict | None:
    """The next report on or after today, or None when nothing is listed within the horizon."""
    for r in (data or {}).get("reports", {}).get(symbol, []):
        if r["date"] >= today.isoformat():
            return r
    return None


def last_safe_expiry(report: dict) -> date:
    """Latest option expiration that settles before the report moves the stock. A post-market
    report on day E comes after that day's close, so E itself is safe; otherwise E-1."""
    d = date.fromisoformat(report["date"])
    return d if report["time"] == "post-market" else d - timedelta(days=1)


def describe(report: dict) -> str:
    when = {"pre-market": "盘前", "post-market": "盘后"}.get(report["time"], "时间未知")
    return f"{report['date']}（{when}）"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
