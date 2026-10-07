"""Daily market context: Treasury yields and oil from Alpha Vantage, news sentiment from
Alpaca's news feed (Benzinga headlines) scored with a small finance word list, and two free
market-wide sentiment gauges: CBOE's daily put/call ratios and the weekly AAII investor survey.

Fetched at most once per ET day (3 Alpha Vantage calls plus a few Alpaca news pages) and archived
by date. Record only: nothing here changes a trade until it has been measured against what
actually happened.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timedelta
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


CBOE_URL = "https://cdn.cboe.com/data/us/options/market_statistics/daily/{day}_daily_options"
AAII_URL = "https://www.aaii.com/sentimentsurvey/sent_results"
UA = {"User-Agent": "Mozilla/5.0 (zero-or-hero research; daily, one request)"}
PUTCALL = {"TOTAL PUT/CALL RATIO": "total", "EQUITY PUT/CALL RATIO": "equity",
           "INDEX PUT/CALL RATIO": "index", "SPX + SPXW PUT/CALL RATIO": "spx"}
AAII_AVG = {"bullish": 37.5, "neutral": 31.5, "bearish": 31.0}  # AAII's long-run averages since 1987
GAUGES = ("putcall", "aaii")


def fetch_putcall(today: date, get=requests.get, back: int = 6) -> dict:
    """The latest CBOE daily put/call ratios on or before today (a day not yet published is 403)."""
    for i in range(back + 1):
        day = today - timedelta(days=i)
        r = get(CBOE_URL.format(day=day.isoformat()), headers=UA, timeout=30)
        if r.status_code in (403, 404):
            continue
        r.raise_for_status()
        ratios = {x["name"]: float(x["value"]) for x in r.json().get("ratios", [])}
        out = {"date": day.isoformat(), **{k: ratios[n] for n, k in PUTCALL.items() if n in ratios}}
        if "total" in out:
            return out
    raise RuntimeError(f"no CBOE put/call data in the {back + 1} days to {today}")


def parse_aaii(html: str, today: date) -> dict:
    """Latest row of AAII's results table: 'Sep 30 34.6% 18.9% 46.5%' (no year on the page)."""
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    m = re.search(r"Reported Date Bullish Neutral Bearish ([A-Z][a-z]{2}) (\d{1,2}) ([\d.]+)% ([\d.]+)% ([\d.]+)%", text)
    if not m:
        raise RuntimeError("AAII results table not found")
    month, day, bull, neut, bear = m.groups()
    d = datetime.strptime(f"{month} {day} {today.year}", "%b %d %Y").date()
    if d > today:  # a December row read in January
        d = d.replace(year=today.year - 1)
    bull, neut, bear = float(bull), float(neut), float(bear)
    return {"date": d.isoformat(), "bullish": bull, "neutral": neut, "bearish": bear,
            "spread": round(bull - bear, 1)}


def fetch_aaii(today: date, get=requests.get) -> dict:
    r = get(AAII_URL, headers=UA, timeout=30)
    r.raise_for_status()
    return parse_aaii(r.text, today)


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
          previous: dict | None = None, news_client=None, series: bool = True, news: bool = True,
          gauges: bool = True) -> dict:
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
        if not series:  # today's Alpha Vantage retries are used up: keep the failure, ask nothing
            if name in done.get("errors", {}):
                out["errors"][name] = done["errors"][name]
            continue
        time.sleep(pause)
        try:
            out["series"][name] = summarize_series(_get({"function": fn, **params}, key).get("data", []), unit)
        except Exception as e:  # one failed series must not lose the others
            out["errors"][name] = str(e)[:200]
    _gauges(out, done, now, gauges)
    if "news" in done and "news" not in done.get("errors", {}):
        out["news"], out["news_window"] = done["news"], done.get("news_window")
        return out
    if not news:  # today's news retries are used up
        if "news" in done.get("errors", {}):
            out["errors"]["news"] = done["errors"]["news"]
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


def _gauges(out: dict, done: dict, now: datetime, fetch: bool) -> None:
    """Put/call and AAII into out["gauges"]: kept from today's earlier fetch, else fetched (if allowed)."""
    for name, fn in (("putcall", fetch_putcall), ("aaii", fetch_aaii)):
        if name in done.get("gauges", {}) and name not in done.get("errors", {}):
            out.setdefault("gauges", {})[name] = done["gauges"][name]
        elif not fetch:
            if name in done.get("errors", {}):
                out["errors"][name] = done["errors"][name]
        else:
            try:
                out.setdefault("gauges", {})[name] = fn(now.date())
            except Exception as e:  # free sources: a failure here must not lose the rest
                out["errors"][name] = str(e)[:200]


def refresh(path: Path, symbols: set[str], key: str, now: datetime, news_client=None) -> str:
    """Once per ET day, with up to MAX_ATTEMPTS tries counted separately for the Alpha Vantage series
    and for the news, so a news failure never spends Alpha Vantage calls and vice versa."""
    current = load(path)
    today = now.date().isoformat()
    same_day = bool(current) and current["fetched_on"] == today
    errors = current.get("errors", {}) if same_day else {}
    tries = current.get("attempts", 1) if same_day else 0
    news_tries = current.get("news_attempts", 0) if same_day else 0  # files before 10/07 had no news counter
    want_series = not same_day or (any(k in errors for k in SERIES) and tries < MAX_ATTEMPTS)
    want_news = not same_day or ("news" in errors and news_tries < MAX_ATTEMPTS and news_client is not None)
    gauge_tries = current.get("gauge_attempts", 0) if same_day else 0
    want_gauges = not same_day or ((any(k in errors for k in GAUGES) or not current.get("gauges"))
                                   and gauge_tries < MAX_ATTEMPTS)
    if not want_series and not want_news and not want_gauges:
        return "fresh"  # at most MAX_ATTEMPTS x 3 Alpha Vantage calls per day, even if something keeps failing
    data = build(key, symbols, now, previous=current, news_client=news_client, series=want_series, news=want_news,
                 gauges=want_gauges)
    data["attempts"] = tries + (1 if want_series else 0)
    data["news_attempts"] = news_tries + (1 if want_news else 0)
    data["gauge_attempts"] = gauge_tries + (1 if want_gauges else 0)
    text = json.dumps(data, indent=1, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    archive = path.parent / "market" / f"{data['fetched_on']}.json"
    archive.parent.mkdir(exist_ok=True)
    archive.write_text(text)
    return (f"series {sorted(data['series'])}, news for {len(data.get('news', {}))} symbols, "
            f"gauges {sorted(data.get('gauges', {}))}, errors {sorted(data['errors'])}")


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
    g = data.get("gauges", {})
    if g.get("putcall"):
        p = g["putcall"]
        parts = [f"{n} {p[k]:.2f}" for k, n in (("total", "全部"), ("equity", "个股"), ("index", "指数"),
                                                  ("spx", "SPX")) if k in p]
        out.append(f"CBOE 认沽/认购比（{p['date']}）：" + "，".join(parts))
    if g.get("aaii"):
        a = g["aaii"]
        out.append(f"AAII 散户调查（{a['date']}）：看多 {a['bullish']:.1f}%、中性 {a['neutral']:.1f}%、看空 "
                   f"{a['bearish']:.1f}%，多空差 {a['spread']:+.1f} 点（长期均值看多 {AAII_AVG['bullish']}%、"
                   f"看空 {AAII_AVG['bearish']}%）")
    return out
