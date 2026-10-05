"""Daily market context from Alpha Vantage: Treasury yields, oil, and news sentiment.

Fetched at most once per ET day (4 API calls) and archived by date. Record only: nothing here
changes a trade until it has been measured against what actually happened.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

URL = "https://www.alphavantage.co/query"
SERIES = {  # name -> (function, params, unit)
    "us2y": ("TREASURY_YIELD", {"interval": "daily", "maturity": "2year"}, "%"),
    "us10y": ("TREASURY_YIELD", {"interval": "daily", "maturity": "10year"}, "%"),
    "wti": ("WTI", {"interval": "daily"}, "$"),
}
PAUSE_S = 13  # free tier: 5 calls a minute and no bursts
MAX_ATTEMPTS = 2


def _get(params: dict, key: str) -> dict:
    r = requests.get(URL, params={**params, "apikey": key}, timeout=60)
    r.raise_for_status()
    data = r.json()
    for k in ("Information", "Note", "Error Message"):
        if k in data:
            raise RuntimeError(f"alpha vantage {params.get('function')}: {str(data[k])[:200].replace(key, '***')}")
    return data


def summarize_series(points: list[dict], unit: str) -> dict:
    """Latest value with 1-day and ~20-trading-day changes; '.' marks a missing day."""
    xs = [(p["date"], float(p["value"])) for p in points if p.get("value") not in (None, "", ".")]
    xs.sort()
    if not xs:
        return {"unit": unit, "latest": None}
    d, v = xs[-1]
    prev = xs[-2][1] if len(xs) > 1 else None
    back = xs[-21][1] if len(xs) > 20 else None
    return {"unit": unit, "date": d, "latest": v,
            "chg_1d": round(v - prev, 3) if prev is not None else None,
            "chg_20d": round(v - back, 3) if back is not None else None}


def summarize_news(feed: list[dict], symbols: set[str], top: int = 3) -> dict:
    """Relevance-weighted sentiment per symbol from the market-wide news feed (one call covers all)."""
    acc: dict[str, dict] = {}
    for item in feed:
        for t in item.get("ticker_sentiment", []):
            sym = t.get("ticker")
            if sym not in symbols:
                continue
            rel, score = float(t.get("relevance_score") or 0), float(t.get("ticker_sentiment_score") or 0)
            a = acc.setdefault(sym, {"n": 0, "w": 0.0, "ws": 0.0, "items": []})
            a["n"] += 1
            a["w"] += rel
            a["ws"] += rel * score
            a["items"].append({"relevance": round(rel, 3), "score": round(score, 3), "title": item.get("title"),
                               "source": item.get("source"), "url": item.get("url"),
                               "published": item.get("time_published")})
    out = {}
    for sym, a in acc.items():
        avg = a["ws"] / a["w"] if a["w"] else 0.0
        out[sym] = {"articles": a["n"], "sentiment": round(avg, 3), "label": label(avg),
                    "top": sorted(a["items"], key=lambda x: -x["relevance"])[:top]}
    return out


def label(score: float) -> str:
    # Alpha Vantage's own bands.
    if score <= -0.35:
        return "偏空"
    if score <= -0.15:
        return "略偏空"
    if score < 0.15:
        return "中性"
    if score < 0.35:
        return "略偏多"
    return "偏多"


def build(key: str, symbols: set[str], now: datetime, pause: float = PAUSE_S,
          previous: dict | None = None) -> dict:
    """One call per item, each preceded by a pause: the step before this one (earnings) also calls
    Alpha Vantage, and the free tier rejects bursts. Items already fetched today (in previous) are
    kept instead of being requested again."""
    out = {"source": "alphavantage", "fetched_at": now.isoformat(timespec="seconds"),
           "fetched_on": now.date().isoformat(), "symbols": sorted(symbols), "series": {}, "errors": {}}
    done = previous if previous and previous.get("fetched_on") == out["fetched_on"] else {}
    for name, (fn, params, unit) in SERIES.items():
        if name in done.get("series", {}) and name not in done.get("errors", {}):
            out["series"][name] = done["series"][name]
            continue
        time.sleep(pause)
        try:
            out["series"][name] = summarize_series(_get({"function": fn, **params}, key).get("data", []), unit)
        except Exception as e:  # one failed series must not lose the others
            out["errors"][name] = str(e)[:200]
    if "news" in done and "news" not in done.get("errors", {}):
        out["news"], out["news_window"] = done["news"], done.get("news_window")
        return out
    since = (now - timedelta(hours=24)).strftime("%Y%m%dT%H%M")
    time.sleep(pause)
    try:
        feed = _get({"function": "NEWS_SENTIMENT", "sort": "LATEST", "limit": "1000", "time_from": since}, key)
        out["news"] = summarize_news(feed.get("feed", []), symbols)
        out["news_window"] = f"since {since} (market-wide feed, up to 1000 articles)"
    except Exception as e:
        out["errors"]["news"] = str(e)[:200]
    return out


def refresh(path: Path, symbols: set[str], key: str, now: datetime) -> str:
    current = load(path)
    today = now.date().isoformat()
    tries = current.get("attempts", 1) if current and current["fetched_on"] == today else 0
    if tries and (not current.get("errors") or tries >= MAX_ATTEMPTS):
        return "fresh"  # at most MAX_ATTEMPTS x 4 calls per day, even if something keeps failing
    data = build(key, symbols, now, previous=current)  # a retry only re-requests what failed
    data["attempts"] = tries + 1
    text = json.dumps(data, indent=1, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    archive = path.parent / "market" / f"{data['fetched_on']}.json"
    archive.parent.mkdir(exist_ok=True)
    archive.write_text(text)
    return f"series {sorted(data['series'])}, news for {len(data.get('news', {}))} symbols, errors {sorted(data['errors'])}"


def load(path: Path) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


def lines(data: dict | None) -> list[str]:
    """Plain-language readings for reviews and the dashboard."""
    if not data:
        return []
    s, out = data.get("series", {}), []
    names = {"us2y": "2 年期美债", "us10y": "10 年期美债", "wti": "WTI 原油"}
    for k, name in names.items():
        x = s.get(k) or {}
        if x.get("latest") is None:
            continue
        if x["unit"] == "%":
            chg = f"，日变动 {x['chg_1d'] * 100:+.0f} 基点" if x.get("chg_1d") is not None else ""
            chg += f"，20 日 {x['chg_20d'] * 100:+.0f} 基点" if x.get("chg_20d") is not None else ""
            out.append(f"{name} {x['latest']:.2f}%{chg}（{x['date']}）")
        else:
            chg = f"，20 日 {x['chg_20d']:+.2f}" if x.get("chg_20d") is not None else ""
            out.append(f"{name} ${x['latest']:.2f}{chg}（{x['date']}）")
    if s.get("us2y", {}).get("latest") is not None and s.get("us10y", {}).get("latest") is not None:
        out.append(f"10 年减 2 年利差 {(s['us10y']['latest'] - s['us2y']['latest']) * 100:+.0f} 基点")
    return out
