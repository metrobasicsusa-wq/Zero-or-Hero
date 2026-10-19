"""Earnings calendar (Alpha Vantage EARNINGS_CALENDAR), fetched at most once per ET day.

Owner, 10-19: Alpaca has no earnings dates, and Alpha Vantage's are sometimes stale (it listed Mattel for 10-19/20;
the company had announced 11-05) or lack the time of day. Two free cross-checks are applied on top:
  - Nasdaq's daily calendar for the next three weeks (same underlying data, but fresher): its date and
    pre/after-market time win; a report Alpha Vantage places inside that window but Nasdaq lists on none of
    its days is kept and marked "disputed";
  - Yahoo's calendarEvents for our symbols reporting in the next 30 days: "confirmed" is True when Yahoo no
    longer marks the date as an estimate (the company has announced it); a confirmed Yahoo date that differs
    from ours is kept as "yahoo_date" for a person to check.
Both are best effort: when either cannot be reached the calendar is Alpha Vantage's alone, as before.

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

import time as _time

import requests

URL = "https://www.alphavantage.co/query"
HORIZON = "3month"
HORIZON_DAYS = 90
MAX_AGE_DAYS = 3  # older than this and the calendar counts as unavailable
NASDAQ = "https://api.nasdaq.com/api/calendar/earnings"
BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
           "Accept": "application/json"}
NASDAQ_TIME = {"time-pre-market": "pre-market", "time-after-hours": "post-market"}
CHECK_DAYS = 21     # calendar days of Nasdaq cross-check
CONFIRM_DAYS = 30   # calendar days of Yahoo confirmation


def fetch(key: str) -> list[dict]:
    r = requests.get(URL, params={"function": "EARNINGS_CALENDAR", "horizon": HORIZON, "apikey": key}, timeout=60)
    r.raise_for_status()
    if not r.text.startswith("symbol,"):
        raise RuntimeError(f"unexpected earnings calendar response: {r.text[:200].replace(key, '***')}")
    return list(csv.DictReader(io.StringIO(r.text)))


def fetch_nasdaq(day: str, get=requests.get) -> list[dict] | None:
    """Nasdaq's calendar for one date in Alpha Vantage's row shape; None when it cannot be read."""
    try:
        r = get(NASDAQ, params={"date": day}, headers=BROWSER, timeout=30)
        r.raise_for_status()
        rows = ((r.json() or {}).get("data") or {}).get("rows") or []
    except Exception:
        return None
    return [{"symbol": x["symbol"], "reportDate": day, "timeOfTheDay": NASDAQ_TIME.get(x.get("time") or "", ""),
             "estimate": (x.get("epsForecast") or "").replace("$", "").strip() or None, "fiscalDateEnding": None}
            for x in rows if x.get("symbol")]


def overlay(av: list[dict], nas: dict[str, list[dict] | None]) -> tuple[list[dict], dict]:
    """Alpha Vantage rows corrected by Nasdaq for the days Nasdaq answered (with at least one row)."""
    covered = {d for d, rs in nas.items() if rs}
    first: dict[str, dict] = {}
    for d in sorted(covered):
        for r in nas[d]:
            first.setdefault(r["symbol"], r)
    out, moved, timed, disputed, done = [], [], [], [], set()
    for r in av:
        sym, n = r["symbol"], first.get(r["symbol"])
        near = n and abs((date.fromisoformat(r["reportDate"]) - date.fromisoformat(n["reportDate"])).days) <= 45
        if n and near and sym not in done:
            done.add(sym)
            new = {**r, "checked": "nasdaq"}
            if n["reportDate"] != r["reportDate"]:
                moved.append([sym, r["reportDate"], n["reportDate"]])
                new.update(reportDate=n["reportDate"], timeOfTheDay=n["timeOfTheDay"])
            elif n["timeOfTheDay"] and n["timeOfTheDay"] != (r.get("timeOfTheDay") or "").strip():
                timed.append([sym, (r.get("timeOfTheDay") or "").strip() or "unknown", n["timeOfTheDay"]])
                new["timeOfTheDay"] = n["timeOfTheDay"]
            out.append(new)
        elif n and near:
            continue                                   # a second Alpha Vantage row for the same report
        elif r["reportDate"] in covered:
            out.append({**r, "disputed": True})
            disputed.append(sym)
        else:
            out.append(r)
    have = {r["symbol"] for r in av}
    added = [{**n, "checked": "nasdaq"} for sym, n in first.items() if sym not in have]
    return out + added, {"days": sorted(covered), "moved": moved, "time_filled": timed, "disputed": sorted(set(disputed)),
                         "added": len(added)}


def cross_check(av: list[dict], today: date, days: int = CHECK_DAYS, get=requests.get, pause: float = 0.3) -> tuple[list[dict], dict]:
    """Alpha Vantage's rows with Nasdaq's next `days` calendar days applied; never raises."""
    try:
        nas = {}
        d = today
        while d <= today + timedelta(days=days):
            if d.weekday() < 5:
                nas[d.isoformat()] = fetch_nasdaq(d.isoformat(), get)
                _time.sleep(pause)
            d += timedelta(days=1)
        return overlay(av, nas)
    except Exception as e:
        return av, {"error": str(e)[:200]}


def yahoo_dates(symbols: list[str], session=None, pause: float = 0.2) -> dict[str, dict]:
    """symbol -> {"date", "confirmed"} from Yahoo's calendarEvents; {} when Yahoo cannot be read."""
    try:
        s = session or requests.Session()
        s.headers.update({"User-Agent": BROWSER["User-Agent"]})
        try:
            s.get("https://fc.yahoo.com", timeout=20)
        except Exception:
            pass
        crumb = s.get("https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=20).text.strip()
        if not crumb or "<" in crumb or " " in crumb:
            return {}
    except Exception:
        return {}
    out = {}
    for sym in symbols:
        try:
            r = s.get(f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{sym}",
                      params={"modules": "calendarEvents", "crumb": crumb}, timeout=20)
            res = ((r.json().get("quoteSummary") or {}).get("result") or [{}])[0]
            e = (res.get("calendarEvents") or {}).get("earnings") or {}
            ds = [x.get("fmt") for x in e.get("earningsDate") or [] if x.get("fmt")]
            if ds:
                out[sym] = {"date": ds[0], "confirmed": e.get("isEarningsDateEstimate") is False}
        except Exception:
            pass
        _time.sleep(pause)
    return out


def confirm(data: dict, today: date, days: int = CONFIRM_DAYS, lookup=None) -> dict:
    """Mark our reports in the next `days` as company-confirmed (or not) from Yahoo; returns a summary."""
    lookup = lookup or yahoo_dates
    end = (today + timedelta(days=days)).isoformat()
    soon = sorted(s for s, rs in data["reports"].items() if any(today.isoformat() <= r["date"] <= end for r in rs))
    got = lookup(soon) if soon else {}
    conflicts = []
    for sym in soon:
        y = got.get(sym)
        if not y:
            continue
        for r in data["reports"][sym]:
            if not (today.isoformat() <= r["date"] <= end):
                continue
            if y["date"] == r["date"]:
                r["confirmed"] = y["confirmed"]
            else:
                r["confirmed"] = False
                if y["confirmed"]:
                    r["yahoo_date"] = y["date"]
                    conflicts.append([sym, r["date"], y["date"]])
    return {"asked": len(soon), "answered": len(got), "confirmed": sum(1 for v in got.values() if v["confirmed"]),
            "conflicts": conflicts}


def build(rows: list[dict], symbols: set[str], fetched_at: datetime) -> dict:
    out: dict[str, list[dict]] = {}
    for r in rows:
        if r["symbol"] in symbols:
            e = {"date": r["reportDate"], "time": (r.get("timeOfTheDay") or "").strip() or "unknown",
                 "estimate": r.get("estimate") or None, "fiscal_end": r.get("fiscalDateEnding")}
            for k in ("checked", "disputed"):
                if r.get(k):
                    e[k] = r[k]
            out.setdefault(r["symbol"], []).append(e)
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
    rows, check = cross_check(fetch(key), now.date())
    data = build(rows, symbols, now)
    try:
        conf = confirm(data, now.date())
    except Exception as e:
        conf = {"error": str(e)[:200]}
    mine = lambda xs: [x for x in xs if (x[0] if isinstance(x, list) else x) in symbols]
    data["cross_check"] = {"nasdaq_days": len(check.get("days", [])), "moved": mine(check.get("moved", [])),
                           "time_filled": mine(check.get("time_filled", [])), "disputed": mine(check.get("disputed", [])),
                           "yahoo": conf, **({"error": check["error"]} if "error" in check else {})}
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
