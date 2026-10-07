"""Daily market context: Treasury yields and oil from Alpha Vantage, news sentiment from
Alpaca's news feed (Benzinga headlines) scored with a small finance word list.

Fetched at most once per ET day (3 Alpha Vantage calls plus a few Alpaca news pages) and archived
by date. Record only: nothing here changes a trade until it has been measured against what
actually happened.
"""

from __future__ import annotations

import json
import re
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


# Headline word list. Deliberately small and plain: a score is (positive - negative) hits over all
# hits in the headline and summary, so a headline with no listed word counts as neutral (0).
POSITIVE = ("beat", "beats", "tops", "surge", "surges", "soar", "soars", "jump", "jumps", "rally", "rallies",
            "record", "upgrade", "upgrades", "upgraded", "raise", "raises", "raised", "outperform", "buy",
            "bullish", "strong", "growth", "gain", "gains", "rise", "rises", "rebound", "rebounds", "boost",
            "boosts", "approval", "approved", "wins", "win", "expands", "profit", "optimistic", "top pick")
NEGATIVE = ("miss", "misses", "missed", "plunge", "plunges", "sink", "sinks", "tumble", "tumbles", "drop",
            "drops", "fall", "falls", "slump", "slumps", "downgrade", "downgrades", "downgraded", "cut", "cuts",
            "lower", "lowers", "underperform", "sell", "bearish", "weak", "loss", "losses", "lawsuit", "probe",
            "investigation", "recall", "warns", "warning", "halt", "halted", "layoffs", "decline", "declines",
            "concern", "concerns", "fraud", "delay", "delays", "selloff", "sell-off", "pessimistic")
_POS = re.compile(r"\b(" + "|".join(map(re.escape, POSITIVE)) + r")\b", re.I)
_NEG = re.compile(r"\b(" + "|".join(map(re.escape, NEGATIVE)) + r")\b", re.I)
NEWS_CHUNK = 50      # symbols per request
NEWS_MAX = 1000      # articles per day, to bound the number of pages


def score_text(text: str) -> float:
    pos, neg = len(_POS.findall(text or "")), len(_NEG.findall(text or ""))
    return (pos - neg) / (pos + neg) if pos + neg else 0.0


def summarize_news(items: list[dict], symbols: set[str], top: int = 3) -> dict:
    """Mean word-list score per symbol over Alpaca news items (one item can name several symbols)."""
    acc: dict[str, dict] = {}
    for item in items:
        score = score_text(f"{item.get('headline', '')}. {item.get('summary', '')}")
        for sym in item.get("symbols") or []:
            if sym not in symbols:
                continue
            a = acc.setdefault(sym, {"n": 0, "s": 0.0, "items": []})
            a["n"] += 1
            a["s"] += score
            a["items"].append({"score": round(score, 3), "title": item.get("headline"),
                               "source": item.get("source"), "url": item.get("url"),
                               "published": item.get("created_at")})
    out = {}
    for sym, a in acc.items():
        avg = a["s"] / a["n"]
        out[sym] = {"articles": a["n"], "sentiment": round(avg, 3), "label": label(avg),
                    "top": sorted(a["items"], key=lambda x: -abs(x["score"]))[:top]}
    return out


def fetch_news(client, symbols: set[str], start: datetime, end: datetime) -> list[dict]:
    """Every Alpaca news item for the symbols in the window, deduplicated, capped at NEWS_MAX."""
    seen, items = set(), []
    syms = sorted(symbols)
    for i in range(0, len(syms), NEWS_CHUNK):
        for item in client.news_range(syms[i:i + NEWS_CHUNK], start.isoformat(), end.isoformat()):
            if item.get("id") in seen:
                continue
            seen.add(item.get("id"))
            items.append(item)
            if len(items) >= NEWS_MAX:
                return items
    return items


def label(score: float) -> str:
    # The bands Alpha Vantage used for its scores; kept so labels read the same as before.
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
          previous: dict | None = None, news_client=None) -> dict:
    """Yields and oil: one Alpha Vantage call per series, each preceded by a pause (the earnings step
    before this one also calls Alpha Vantage, and the free tier rejects bursts). News: Alpaca, if a
    client is given. Items already fetched today (in previous) are kept instead of requested again."""
    out = {"source": "alphavantage+alpaca-news", "fetched_at": now.isoformat(timespec="seconds"),
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
    if news_client is None:
        out["errors"]["news"] = "no Alpaca client (ALPACA_API_KEY not set)"
        return out
    since = now - timedelta(hours=24)
    try:
        items = fetch_news(news_client, symbols, since, now)
        out["news"] = summarize_news(items, symbols)
        out["news_window"] = (f"since {since.isoformat(timespec='minutes')} (Alpaca/Benzinga, {len(items)} articles, "
                              f"word-list score)")
    except Exception as e:
        out["errors"]["news"] = str(e)[:200]
    return out


def refresh(path: Path, symbols: set[str], key: str, now: datetime, news_client=None) -> str:
    current = load(path)
    today = now.date().isoformat()
    tries = current.get("attempts", 1) if current and current["fetched_on"] == today else 0
    if tries and (not current.get("errors") or tries >= MAX_ATTEMPTS):
        return "fresh"  # at most MAX_ATTEMPTS x 3 Alpha Vantage calls per day, even if something keeps failing
    data = build(key, symbols, now, previous=current, news_client=news_client)  # a retry only re-requests what failed
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
