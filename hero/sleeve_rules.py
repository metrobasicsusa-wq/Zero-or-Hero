"""What each sleeve buys, and when (docs/sleeves-plan.md). Each rule answers, on every cycle:
entries(run, spec, ledger) -> None (not its time) or (slot, picks): a slot (the day, the ISO week) is tried
once, picks are what to buy now; and optionally should_exit(run, sleeve_id, ticket) -> a reason or None.
A pick: {"sym", "asset", "ask", "why", "und", "take", "expiry", "exit_day", "exit_at", "trail"}.
Quotes, contracts and daily closes are fetched once per cycle and shared (run.cache), so the $500 and
$1,000 versions of a rule pick the same contract. Rules never send orders; hero.sleeves does.
"""

from __future__ import annotations

import json
import math
import random
import statistics
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parent.parent
FOCUS = ("NVDA", "AMD", "AVGO", "MU", "INTC", "QCOM", "TSM", "ARM", "MRVL", "AMAT", "LRCX", "KLAC", "ADI", "TXN", "ON",
         "NXPI", "MCHP", "ASML", "MPWR", "TER", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA")


# ---------- shared, cached per cycle ----------
def cached(run, key, fn):
    cache = run.__dict__.setdefault("cache", {})
    if key not in cache:
        try:
            cache[key] = fn()
        except Exception as e:
            run.j.event("alert_error", source=f"sleeve_rules:{key[0] if isinstance(key, tuple) else key}", error=str(e)[:200])
            cache[key] = None
    return cache[key]


def snaps(run, syms: list[str]) -> dict:
    out, need = {}, []
    for s in syms:
        v = run.__dict__.setdefault("cache", {}).get(("snap", s))
        if v is None:
            need.append(s)
        else:
            out[s] = v
    for i in range(0, len(need), 100):
        got = cached(run, ("snaps", tuple(need[i:i + 100])), lambda: run.c.stock_snapshots(need[i:i + 100])) or {}
        for s, v in got.items():
            run.cache[("snap", s)] = v
            out[s] = v
    return out


def spot_open(snap: dict) -> tuple[float, float, float, float]:
    """(price now, today's open, today's low so far, yesterday's close) from a stock snapshot."""
    bar, prev = snap.get("dailyBar") or {}, snap.get("prevDailyBar") or {}
    return (float((snap.get("latestTrade") or {}).get("p") or 0), float(bar.get("o") or 0), float(bar.get("l") or 0),
            float(prev.get("c") or 0))


def contracts(run, und: str, gte: str, lte: str, lo: float, hi: float, kind: str | None = None) -> list[dict]:
    kw = {"status": "active", "expiration_date_gte": gte, "expiration_date_lte": lte,
          "strike_price_gte": f"{lo:.2f}", "strike_price_lte": f"{hi:.2f}"}
    if kind:
        kw["type"] = kind
    return cached(run, ("contracts", und, gte, lte, round(lo, 2), round(hi, 2), kind),
                  lambda: run.c.option_contracts(und, **kw)) or []


def ask_of(run, sym: str) -> float:
    q = cached(run, ("quote", sym), lambda: run.c.option_snapshots([sym]).get(sym) or {}) or {}
    return float((q.get("latestQuote") or {}).get("ap") or 0)


def pick(cs: list[dict], kind: str, spot: float, off: float, expiry: str | None = None) -> dict | None:
    """The nearest listed strike at least `off` beyond the price (calls above, puts below)."""
    want = "call" if kind == "C" else "put"
    cs = [c for c in cs if c.get("type", want) == want and (expiry is None or c["expiration_date"] == expiry)]
    ks = sorted(cs, key=lambda c: float(c["strike_price"]))
    if kind == "C":
        return next((c for c in ks if float(c["strike_price"]) >= spot * (1 + off)), None)
    below = [c for c in ks if float(c["strike_price"]) <= spot * (1 - off)]
    return below[-1] if below else None


def closes(run, syms: list[str], days: int = 400) -> dict[str, list[float]]:
    """Completed daily closes (raw), oldest first."""
    key = ("closes", tuple(sorted(syms)), days)

    def fetch():
        start = (run.today - timedelta(days=days)).isoformat()
        out = {}
        for i in range(0, len(syms), 100):
            for s, bs in run.c.daily_bars(syms[i:i + 100], start, adjustment="raw").items():
                out[s] = [float(b["c"]) for b in bs if b["t"][:10] < run.today.isoformat()]
        return out
    return cached(run, key, fetch) or {}


def pool(run) -> list[str]:
    def load():
        d = json.loads((ROOT / "data" / "universe.json").read_text())
        return list(d.get("stocks") or [])
    return cached(run, "pool", load) or []


def week_key(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def business_day_before(d: date) -> date:
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def next_business_day(d: date) -> date:
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def option_pick(run, und: str, kind: str, spot: float, off: float, gte: str, lte: str, nearest: bool = True) -> tuple[dict, float] | None:
    """A listed contract (nearest expiry in the window, or the last one if not `nearest`) and its ask."""
    lo, hi = (spot * (1 + off) * 0.995, spot * (1 + off + 0.06)) if kind == "C" else (spot * (1 - off - 0.06), spot * (1 - off) * 1.005)
    cs = contracts(run, und, gte, lte, lo, hi, "call" if kind == "C" else "put")
    if not cs:
        return None
    exps = sorted({c["expiration_date"] for c in cs})
    c = pick(cs, kind, spot, off, exps[0] if nearest else exps[-1])
    if not c:
        return None
    ask = ask_of(run, c["symbol"])
    return (c, ask) if ask > 0 else None


def budget(run, spec, s, n: int) -> float:
    """What one pick may cost in this sleeve (its fraction of the sleeve's cash, split over n picks)."""
    frac = spec.get("fraction", getattr(run, "p", {}).get("fraction", 0.5))
    return frac * s["cash"] / max(n, 1)


def trend20(xs: list[float]) -> int:
    if len(xs) < 21:
        return 0
    return 1 if xs[-1] > xs[-21] else -1


# ---------- the rules ----------
class ZeroDTE:
    """10:00, the option expiring today, with the trend since the open, `offset` beyond the price, a `take` x limit.
    Optional: `side` "call" / "put" fixes the side; `after_drop` (e.g. -0.01) trades only the day after a close
    at least that far below the one before it (research/2026-10-08-zdte-streak.md)."""

    def entries(self, run, spec, s):
        day = run.today.isoformat()
        if day in s["slots"] or not (spec.get("entry", "10:00") <= run.hhmm < "15:00"):
            return None
        und = spec["und"]
        why_day = ""
        if spec.get("after_drop") is not None:
            xs = closes(run, [und], days=10).get(und) or []
            if len(xs) < 2:
                return None
            prev = xs[-1] / xs[-2] - 1
            if prev > spec["after_drop"]:
                return (day, [])  # yesterday was not a big enough drop: sit out
            why_day = f"昨天 {und} 收跌 {prev:.1%}，"
        snap = snaps(run, [und]).get(und) or {}
        spot, o, _, _ = spot_open(snap)
        if not spot or not o:
            return None
        kind = {"call": "C", "put": "P"}.get(spec.get("side"), "C" if spot >= o else "P")
        got = option_pick(run, und, kind, spot, spec["offset"], day, day)
        if not got:
            return (day, [])  # no expiry today for this name
        c, ask = got
        how = "买" if spec.get("side") else "顺势买"
        return (day, [{"sym": c["symbol"], "asset": "option", "und": und, "ask": ask, "take": spec["take"], "expiry": day,
                       "exit_day": day, "exit_at": "15:30",
                       "why": f"{why_day}{und} 开盘 ${o:,.2f}、现在 ${spot:,.2f}，{how}当天到期{'看涨' if kind == 'C' else '看跌'} "
                              f"{float(c['strike_price']):g}（离现价 {abs(float(c['strike_price']) / spot - 1):.1%}），{spec['take']:g} 倍止盈，15:30 平仓"}])


class Weekly:
    """First trading day of the week, 10:00: this week's last expiry, 1% beyond, a 2x take, out the day before expiry."""

    def entries(self, run, spec, s):
        wk = week_key(run.today)
        if wk in s["slots"] or run.today.weekday() > 1 or not ("10:00" <= run.hhmm < "15:00"):
            return None
        names = self.names(run, spec)
        friday = run.today + timedelta(days=4 - run.today.weekday())
        picks = []
        for und, kind, why in names:
            spot = spot_open(snaps(run, [und]).get(und) or {})[0]
            if not spot:
                continue
            got = option_pick(run, und, kind, spot, spec.get("otm", 0.01), (run.today + timedelta(days=1)).isoformat(),
                              friday.isoformat(), nearest=False)
            if not got:
                continue
            c, ask = got
            exp = date.fromisoformat(c["expiration_date"])
            picks.append({"sym": c["symbol"], "asset": "option", "und": und, "ask": ask, "take": spec.get("take", 2),
                          "expiry": exp.isoformat(), "exit_day": business_day_before(exp).isoformat(), "exit_at": "15:50",
                          "why": f"{why}；买 {exp:%m-%d} 到期{'看涨' if kind == 'C' else '看跌'} {float(c['strike_price']):g}，"
                                 f"{spec.get('take', 2):g} 倍止盈，到期前一天 15:50 卖"})
        return (wk, picks)

    def names(self, run, spec) -> list[tuple[str, str, str]]:
        p = [x for x in pool(run)]
        mode, n = spec["mode"], spec.get("n", 3)
        if mode == "trend":
            rng = random.Random(week_key(run.today))
            chosen = rng.sample(p, min(n, len(p)))
            cl = closes(run, chosen, 60)
            out = []
            for x in chosen:
                t = trend20(cl.get(x, []))
                if t:
                    out.append((x, "C" if t > 0 else "P", f"随机挑中 {x}，20 日{'涨' if t > 0 else '跌'}"))
            return out
        if mode == "momentum":
            cl = closes(run, p, 120)
            ranked = sorted(((xs[-1] / xs[-64] - 1, x) for x, xs in cl.items() if len(xs) >= 64 and xs[-64]), reverse=True)
            return [(x, "C", f"{x} 63 天涨 {r:+.0%}，股票池第一") for r, x in ranked[:spec.get("n", 1)]]
        if mode == "news":
            from hero.market import score_text
            fri = run.today - timedelta(days=(run.today.weekday() - 4) % 7 or 7)
            start = datetime(fri.year, fri.month, fri.day, 9, 30, tzinfo=ET)
            end = datetime(run.today.year, run.today.month, run.today.day, 9, 25, tzinfo=ET)
            scores: dict[str, list[float]] = {}

            def fetch():
                for i in range(0, len(p), 50):
                    for item in run.c.news_range(p[i:i + 50], start.isoformat(), end.isoformat()):
                        sc = score_text(f"{item.get('headline', '')}. {item.get('summary', '')}")
                        for x in item.get("symbols") or []:
                            if x in p:
                                scores.setdefault(x, []).append(sc)
                return scores
            got = cached(run, ("news", start.isoformat()), fetch) or {}
            ranked = sorted(((abs(statistics.mean(v)), x, statistics.mean(v), len(v)) for x, v in got.items()
                             if len(v) >= 3 and statistics.mean(v) != 0), reverse=True)
            return [(x, "C" if m > 0 else "P", f"{x} 周末 {k} 篇新闻，情绪 {m:+.2f}") for _, x, m, k in ranked[:n]]
        return []


class Net:
    """In a wild week (research_net.signals on SPY): ~4-week calls 20% beyond on the most volatile names."""

    def entries(self, run, spec, s):
        from hero import research_net as rn
        wk = week_key(run.today)
        if wk in s["slots"] or run.today.weekday() > 1 or not ("10:00" <= run.hhmm < "15:00"):
            return None

        def signal():
            bars = run.c.daily_bars(["SPY"], (run.today - timedelta(days=560)).isoformat(), adjustment="raw").get("SPY", [])
            spy = {b["t"][:10]: float(b["c"]) for b in bars if b["t"][:10] < run.today.isoformat()}
            last = max(spy) if spy else None
            sig = rn.signals(spy, [last]) if last else {}
            return [k for k in ("rebound", "wild") if sig.get(k)]
        fired = cached(run, ("net_signal", wk), signal) or []
        if not fired:
            return (wk, [])
        p = pool(run)
        cl = closes(run, p, 120)
        ranked = sorted(((rn.vol(xs[-64:]), x) for x, xs in cl.items() if len(xs) >= 64), reverse=True)[:15]
        picks = []
        for _, und in ranked[:spec.get("n", 3)]:
            spot = spot_open(snaps(run, [und]).get(und) or {})[0]
            if not spot:
                continue
            got = option_pick(run, und, "C", spot, 0.20, (run.today + timedelta(days=21)).isoformat(),
                              (run.today + timedelta(days=35)).isoformat())
            if not got:
                continue
            c, ask = got
            picks.append({"sym": c["symbol"], "asset": "option", "und": und, "ask": ask, "take": 20, "expiry": c["expiration_date"],
                          "exit_day": c["expiration_date"], "exit_at": "15:30",
                          "why": f"撒网：{'、'.join('暴跌后反弹' if f == 'rebound' else '高波动' for f in fired)}；{und} "
                                 f"{c['expiration_date']} 到期、价外 20% 看涨 {float(c['strike_price']):g}，20 倍止盈"})
        return (wk, picks)


class Gap:
    """claude-b's rule, our tested form: a 2%+ gap down; a call 1% above, nearest expiry; trail after 2x; out 15:50."""

    def entries(self, run, spec, s):
        day = run.today.isoformat()
        if day in s["slots"] or not ("09:45" <= run.hhmm < "10:30"):
            return None
        p = pool(run)
        sn = snaps(run, p)
        gaps = []
        for x, v in sn.items():
            spot, o, _, prev = spot_open(v)
            if spot and o and prev and o / prev - 1 <= -0.02:
                gaps.append((o / prev - 1, x, spot))
        picks, n = [], spec.get("n", 2)
        each = budget(run, spec, s, n)
        for g, und, spot in sorted(gaps)[:12]:  # biggest gaps first; skip a contract this sleeve cannot afford
            if len(picks) >= n:
                break
            got = option_pick(run, und, "C", spot, 0.01, day, (run.today + timedelta(days=4)).isoformat())
            if got and got[1] * 100 <= each:
                c, ask = got
                picks.append({"sym": c["symbol"], "asset": "option", "und": und, "ask": ask, "trail": True, "expiry": c["expiration_date"],
                              "exit_day": day, "exit_at": "15:50",
                              "why": f"{und} 低开 {g:.1%}，买看涨 {float(c['strike_price']):g}（{c['expiration_date']} 到期），翻倍后回撤 40% 卖，最晚 15:50"})
        return (day, picks)


class Flush:
    """By 10:00 a chip / Mag 7 name is 2% under its open: a call 1% above, nearest expiry, 2x take, out 15:50."""

    def entries(self, run, spec, s):
        day = run.today.isoformat()
        if day in s["slots"] or not ("10:00" <= run.hhmm < "10:30"):
            return None
        sn = snaps(run, list(FOCUS))
        drops = []
        for x, v in sn.items():
            spot, o, low, _ = spot_open(v)
            if spot and o and low and 1 - low / o >= 0.02:
                drops.append((1 - low / o, x, spot))
        picks, n = [], spec.get("n", 2)
        each = budget(run, spec, s, n)
        for d, und, spot in sorted(drops, reverse=True)[:12]:
            if len(picks) >= n:
                break
            got = option_pick(run, und, "C", spot, 0.01, day, (run.today + timedelta(days=4)).isoformat())
            if got and got[1] * 100 <= each:
                c, ask = got
                picks.append({"sym": c["symbol"], "asset": "option", "und": und, "ask": ask, "take": 2, "expiry": c["expiration_date"],
                              "exit_day": day, "exit_at": "15:50",
                              "why": f"{und} 开盘后跌了 {d:.1%}，买看涨 {float(c['strike_price']):g}（{c['expiration_date']} 到期），2 倍止盈，最晚 15:50"})
        return (day, picks)


class EarningsLotto:
    """The close before a report's reaction: a call about 2x the implied move above; sold at the next open."""

    def entries(self, run, spec, s):
        day = run.today.isoformat()
        if day in s["slots"] or not ("15:40" <= run.hhmm < "15:56"):
            return None
        try:
            reports = json.loads((ROOT / "data" / "earnings.json").read_text()).get("reports") or {}
        except Exception:
            reports = {}
        nxt = next_business_day(run.today).isoformat()
        names = []
        for x, rs in reports.items():
            for r in rs:
                if (r["date"] == day and "post" in r.get("time", "")) or (r["date"] == nxt and "pre" in r.get("time", "")):
                    names.append(x)
        names = [x for x in names if x in set(pool(run))]
        picks = []
        for und in names:
            spot = spot_open(snaps(run, [und]).get(und) or {})[0]
            if not spot:
                continue
            lim = (run.today + timedelta(days=10)).isoformat()
            cs = contracts(run, und, nxt, lim, spot * 0.85, spot * 1.25)
            if not cs:
                continue
            exp = min(c["expiration_date"] for c in cs)
            atm_c, atm_p = pick(cs, "C", spot, 0.0, exp), pick(cs, "P", spot, 0.0, exp)
            if not atm_c or not atm_p:
                continue
            move = (ask_of(run, atm_c["symbol"]) + ask_of(run, atm_p["symbol"])) / spot
            if move <= 0:
                continue
            c = pick(cs, "C", spot, 2 * move, exp)
            ask = ask_of(run, c["symbol"]) if c else 0
            if c and ask > 0:
                picks.append((move, {"sym": c["symbol"], "asset": "option", "und": und, "ask": ask, "expiry": exp,
                                     "exit_day": nxt, "exit_at": "09:35",
                                     "why": f"{und} 财报（预期波动 {move:.1%}），买 2 倍预期波动处看涨 {float(c['strike_price']):g}，第二天开盘卖"}))
        return (day, [pk for _, pk in sorted(picks, key=lambda x: -x[0])[:spec.get("n", 3)]])


class LETF:
    """At 15:50: hold the fund (shares) while it closes above its moving average; sell when it does not.
    `signal` (optional) is the symbol whose average decides, e.g. SOXL held while SMH is above its 200-day."""

    def above(self, run, spec) -> tuple[bool, float, float] | None:
        sig, n = spec.get("signal", spec["sym"]), spec.get("ma", 20)
        xs = closes(run, [sig], max(90, int(n * 1.6))).get(sig, [])
        px = spot_open(snaps(run, [sig]).get(sig) or {})[0]
        if len(xs) < n or not px:
            return None
        ma = (sum(xs[-(n - 1):]) + px) / n
        return px > ma, px, ma

    def entries(self, run, spec, s):
        day = run.today.isoformat()
        if day in s["slots"] or not ("15:45" <= run.hhmm < "15:58") or s["open"]:
            return None
        got = self.above(run, spec)
        if not got:
            return None
        up, px, ma = got
        if not up:
            return (day, [])
        sym, sig = spec["sym"], spec.get("signal", spec["sym"])
        ask = px if sig == sym else spot_open(snaps(run, [sym]).get(sym) or {})[0]
        if not ask:
            return None
        lead = f"{sig} ${px:,.2f} 在 {spec.get('ma', 20)} 日均线 ${ma:,.2f} 上方"
        return (day, [{"sym": sym, "asset": "stock", "ask": ask,
                       "why": f"{lead}，买入 {sym} 拿着" if sig != sym else f"{lead}，买入拿着"}])

    def exit_check(self, run, spec) -> str | None:
        if not ("15:45" <= run.hhmm < "15:58"):
            return None
        got = self.above(run, spec)
        if got and not got[0]:
            return f"{spec.get('signal', spec['sym'])} 跌破 {spec.get('ma', 20)} 日均线（${got[2]:,.2f}）"
        return None

    def should_exit(self, run, sid, t):
        spec = next((x for x in run.specs if x["id"] == sid), None)
        return self.exit_check(run, spec) if spec else None


class Relay:
    """Zero-or-hero relay (research/2026-10-05-hero.md, "混合"): all of the sleeve on one earnings call a week --
    the name reporting this week with the strongest 126-session momentum, about 10% out of the money, bought at
    the close before the reaction and sold at the next open -- until the sleeve is at `switch_at` x its start;
    then all of it in SOXL while SMH closes above its 200-day average. A new round starts with the lottery."""

    TREND = {"sym": "SOXL", "signal": "SMH", "ma": 200}

    def __init__(self):
        self.letf = LETF()

    def phase(self, spec, s) -> str:
        if s.get("relay_round") != s["round"]:
            s["relay_round"], s["relay_phase"] = s["round"], "lotto"
        if s["relay_phase"] == "lotto" and not s["open"] and s["cash"] >= spec.get("switch_at", 3) * s["start"]:
            s["relay_phase"] = "trend"
        return s["relay_phase"]

    def entries(self, run, spec, s):
        if self.phase(spec, s) == "trend":
            return self.letf.entries(run, {**spec, **self.TREND}, s)
        return self.lotto(run, spec, s)

    def lotto(self, run, spec, s):
        wk = "relay-" + week_key(run.today)
        if wk in s["slots"] or s["open"] or not ("15:40" <= run.hhmm < "15:56"):
            return None
        try:
            reports = json.loads((ROOT / "data" / "earnings.json").read_text()).get("reports") or {}
        except Exception:
            reports = {}
        names = set(pool(run))
        cands = []  # (name, the session before its reaction) for entries left this week
        for x, rs in reports.items():
            if x not in names:
                continue
            for r in rs:
                rd, when = date.fromisoformat(r["date"]), r.get("time", "")
                entry = rd if "post" in when else (business_day_before(rd) if "pre" in when else None)
                if entry and week_key(entry) == week_key(run.today) and entry >= run.today:
                    cands.append((x, entry))
        if not cands:
            return None
        cl = closes(run, sorted({x for x, _ in cands}), 260)
        mom = {x: cl[x][-1] / cl[x][-127] - 1 for x, _ in cands if len(cl.get(x, [])) >= 127}
        if not mom:
            return None
        und, entry = max(((x, e) for x, e in cands if x in mom), key=lambda c: mom[c[0]])
        if entry != run.today:
            return None  # the week's pick reports later: wait for its day
        spot = spot_open(snaps(run, [und]).get(und) or {})[0]
        if not spot:
            return None
        nxt = next_business_day(run.today).isoformat()
        cs = contracts(run, und, nxt, (run.today + timedelta(days=10)).isoformat(), spot, spot * 1.3, "call")
        exp = min((c["expiration_date"] for c in cs), default=None)
        c = pick(cs, "C", spot, 0.10, exp) if exp else None
        ask = ask_of(run, c["symbol"]) if c else 0
        if not c or ask <= 0:
            return (wk, [])
        return (wk, [{"sym": c["symbol"], "asset": "option", "und": und, "ask": ask, "expiry": exp, "exit_day": nxt, "exit_at": "09:35",
                      "why": f"接力第一段：本周财报里 126 日动量最强的 {und}（{mom[und]:+.0%}），全仓买价外约 10% 看涨 "
                             f"{float(c['strike_price']):g}，第二天开盘卖；账本到起点 {spec.get('switch_at', 3):g} 倍就转 SOXL"}])

    def should_exit(self, run, sid, t):
        spec = next((x for x in run.specs if x["id"] == sid), None)
        if t.get("asset") == "stock" and spec:
            return self.letf.exit_check(run, {**spec, **self.TREND})
        return None


RULES = {"zdte": ZeroDTE(), "weekly": Weekly(), "net": Net(), "gap": Gap(), "flush": Flush(), "earnings": EarningsLotto(),
         "letf": LETF(), "relay": Relay()}
