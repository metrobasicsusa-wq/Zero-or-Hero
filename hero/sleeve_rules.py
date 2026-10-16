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
    """10:00, the option expiring today (or, when the name has none today, its nearest expiry within a week), with the
    trend since the open, `offset` beyond the price, a `take` x limit, out at 15:30 the same day either way. With
    `half_take`, half the contracts carry the take and the rest run on a trail (research/2026-10-13-zdte-timing.md).
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
        got = option_pick(run, und, kind, spot, spec["offset"], day, (run.today + timedelta(days=7)).isoformat())
        if not got:
            return (day, [])  # nothing listed within a week
        c, ask = got
        exp = c["expiration_date"]
        how = "买" if spec.get("side") else "顺势买"
        when = "当天到期" if exp == day else f"最近到期（{exp[5:]}，今天没有当天到期的）"
        return (day, [{"sym": c["symbol"], "asset": "option", "und": und, "ask": ask, "take": spec["take"], "expiry": exp,
                       "half": bool(spec.get("half_take")),
                       "exit_day": day, "exit_at": "15:30",
                       "why": f"{why_day}{und} 开盘 ${o:,.2f}、现在 ${spot:,.2f}，{how}{when}{'看涨' if kind == 'C' else '看跌'} "
                              f"{float(c['strike_price']):g}（离现价 {abs(float(c['strike_price']) / spot - 1):.1%}），{spec['take']:g} 倍止盈，15:30 平仓"}])


class OneShot(ZeroDTE):
    """One shot at the target (owner, 10-13: "with luck one ticket gets there"): the 0DTE pick, but all of the sleeve
    (fraction 1.0 in the spec), further out (`offset`, 1% by default), no take and no trail, held to 15:55."""

    def entries(self, run, spec, s):
        got = super().entries(run, {**spec, "take": 0, "offset": spec.get("offset", 0.01)}, s)
        if not got or not got[1]:
            return got
        day, picks = got
        for pk in picks:
            pk.pop("take", None)
            pk["half"] = False
            pk.update({"exit_at": "15:55", "expiry_out": "15:55",
                       "why": pk["why"].split("，0 倍止盈")[0] + "，全仓、不止盈，拿到 15:55 赌一把到目标"})
        return (day, picks)


def business_days_after(d: date, n: int) -> date:
    for _ in range(n):
        d = next_business_day(d)
    return d


def held_out(run, exp: str, days: int = 3) -> tuple[str, str]:
    """(exit_day, exit_at) for a ticket held `days` sessions: 15:50 that day, or 15:30 on its expiry if sooner."""
    out = business_days_after(run.today, days).isoformat()
    return (out, "15:50") if out < exp else (exp, "15:30")


class Overnight:
    """research/2026-10-13-overnight.md (Lou, Polk & Skouras: momentum is earned overnight). At 15:50: the pool
    names up `move` (3%) or more on the day, in the top 15% of the day's range and above their 50-day average; the
    biggest mover gets next week's call (5 to 12 days out) 3% above the price; out after 2x on a 40% giveback, else
    15:50 on the third session after."""

    def entries(self, run, spec, s):
        day = run.today.isoformat()
        if day in s["slots"] or not (spec.get("entry", "15:45") <= run.hhmm < "15:58"):
            return None
        p = pool(run)
        cl = closes(run, p, 120)
        held = {t.get("und") for t in s["open"]}
        found = []
        for x, v in snaps(run, p).items():
            spot, _, low, prev = spot_open(v)
            hi = float((v.get("dailyBar") or {}).get("h") or 0)
            xs = cl.get(x) or []
            if not spot or not prev or hi <= low or len(xs) < 50 or x in held:
                continue
            r, pos, ma = spot / prev - 1, (spot - low) / (hi - low), sum(xs[-50:]) / 50
            if r >= spec.get("move", 0.03) and pos >= 1 - spec.get("edge", 0.15) and spot > ma:
                found.append((r, x, spot, pos))
        each = budget(run, spec, s, 1)
        for r, und, spot, pos in sorted(found, reverse=True)[:8]:
            got = option_pick(run, und, "C", spot, spec.get("offset", 0.03), (run.today + timedelta(days=5)).isoformat(),
                              (run.today + timedelta(days=12)).isoformat())
            if not got or got[1] * 100 > each:
                continue
            c, ask = got
            exp = c["expiration_date"]
            out, at = held_out(run, exp)
            return (day, [{"sym": c["symbol"], "asset": "option", "und": und, "ask": ask, "trail": True, "expiry": exp,
                           "exit_day": out, "exit_at": at,
                           "why": f"隔夜动量：{und} 今天涨 {r:+.1%}、收在当天区间 {pos:.0%} 处、在 50 日均线上方，买看涨 "
                                  f"{float(c['strike_price']):g}（{exp} 到期）；翻倍后回撤 40% 卖，否则 {out[5:]} {at} 卖"}])
        return (day, [])


class Pead:
    """research/2026-10-13-pead.md: post-earnings drift. data/earnings.json only lists reports still to come, so the
    rule remembers each report's reaction day (s["pead_watch"]: the report day if before the open, else the next
    session). The session after a reaction day that gapped up `gap` (5%) or more and closed at or above its open: at
    10:00, next week's call (5 to 12 days out) 3% above the price; out after 2x on a 40% giveback, else 15:50 on the
    third session after."""

    def remember(self, run, spec, s) -> None:
        try:
            reports = json.loads(Path(spec.get("earnings") or ROOT / "data" / "earnings.json").read_text()).get("reports") or {}
        except Exception:
            reports = {}
        w = s.setdefault("pead_watch", {})
        for x, rs in reports.items():
            for r in rs:
                rd, when = date.fromisoformat(r["date"]), r.get("time", "")
                react = rd if "pre" in when else next_business_day(rd) if "post" in when else None
                if react and run.today <= react <= run.today + timedelta(days=7):
                    w.setdefault(react.isoformat(), [])
                    if x not in w[react.isoformat()]:
                        w[react.isoformat()].append(x)
        cut = (run.today - timedelta(days=10)).isoformat()
        for k in [k for k in w if k < cut]:
            del w[k]

    def entries(self, run, spec, s):
        self.remember(run, spec, s)
        day = run.today.isoformat()
        if day in s["slots"] or not (spec.get("entry", "10:00") <= run.hhmm < "15:00"):
            return None
        react = business_day_before(run.today).isoformat()
        names = sorted(s["pead_watch"].get(react) or [])
        if not names:
            return (day, [])

        def fetch():
            start = (run.today - timedelta(days=10)).isoformat()
            return run.c.daily_bars(names, start, adjustment="raw")
        bars = cached(run, ("pead_bars", tuple(names)), fetch) or {}
        found = []
        for x in names:
            bs = [b for b in bars.get(x, []) if b["t"][:10] < day]
            if len(bs) < 2 or bs[-1]["t"][:10] != react:
                continue
            o, c, prev = float(bs[-1]["o"]), float(bs[-1]["c"]), float(bs[-2]["c"])
            if prev and o / prev - 1 >= spec.get("gap", 0.05) and c >= o:
                found.append((o / prev - 1, x, c / o - 1))
        each = budget(run, spec, s, 1)
        for g, und, held in sorted(found, reverse=True):
            spot = spot_open(snaps(run, [und]).get(und) or {})[0]
            if not spot:
                continue
            got = option_pick(run, und, "C", spot, spec.get("offset", 0.03), (run.today + timedelta(days=5)).isoformat(),
                              (run.today + timedelta(days=12)).isoformat())
            if not got or got[1] * 100 > each:
                continue
            c, ask = got
            exp = c["expiration_date"]
            out, at = held_out(run, exp)
            return (day, [{"sym": c["symbol"], "asset": "option", "und": und, "ask": ask, "trail": True, "expiry": exp,
                           "exit_day": out, "exit_at": at,
                           "why": f"财报后漂移：{und} 昨天财报高开 {g:+.1%}、收盘比开盘 {held:+.1%} 守住，买看涨 "
                                  f"{float(c['strike_price']):g}（{exp} 到期）；翻倍后回撤 40% 卖，否则 {out[5:]} {at} 卖"}])
        return (day, [])


class VixSpike(ZeroDTE):
    """research/2026-10-13-vixspike.md: the session after a fear day (VIXY up `vix_up` 8%+ and SPY down 1.5%+ at the
    close), at 10:00 the 0DTE call 0.6% above the price (`und`, SPY by default), a 3x take, out 15:30."""

    def entries(self, run, spec, s):
        day = run.today.isoformat()
        if day in s["slots"] or not (spec.get("entry", "10:00") <= run.hhmm < "15:00"):
            return None
        cl = closes(run, ["SPY", "VIXY"], 10)
        spy, vix = cl.get("SPY") or [], cl.get("VIXY") or []
        if len(spy) < 2 or len(vix) < 2:
            return None
        r, v = spy[-1] / spy[-2] - 1, vix[-1] / vix[-2] - 1
        if r > spec.get("spy_down", -0.015) or v < spec.get("vix_up", 0.08):
            return (day, [])  # yesterday was no fear day: sit out
        got = super().entries(run, {"und": "SPY", "offset": 0.006, "take": 3, **spec, "side": "call", "after_drop": None}, s)
        if not got or not got[1]:
            return got
        for pk in got[1]:
            pk["why"] = f"恐慌反弹：昨天 SPY {r:+.1%}、VIXY {v:+.1%}；" + pk["why"]
        return got


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


class BreakGap:
    """research/2026-10-15-sniper-factors3.md: an unusual gap that breaks out -- the open 2%+ from the prior close and
    `swings` (3) times the stock's average daily move over 20 sessions, beyond its 20-session high (gap up) or low (gap
    down), and the first minutes still going that way. From 09:35, the largest such gap of the pool: the nearest expiry
    after today, 0.6% out, held to 15:50 the next session (15:30 if that is its expiry). 57 events since 2024-02 held to
    the next close: +53% per $1 (2024 +34%, 2025-26 +67%; 10-16 split fix, was +60%), most of them earnings reactions."""

    def entries(self, run, spec, s):
        day = run.today.isoformat()
        if day in s["slots"] or not (spec.get("entry", "09:35") <= run.hhmm < spec.get("cutoff", "10:00")):
            return None
        p = pool(run)

        def fetch():
            start = (run.today - timedelta(days=45)).isoformat()
            out = {}
            for i in range(0, len(p), 100):
                for x, bs in run.c.daily_bars(p[i:i + 100], start, adjustment="raw").items():
                    out[x] = [b for b in bs if b["t"][:10] < day]
            return out
        bars = cached(run, ("breakgap_bars", day), fetch) or {}
        found = []
        for x, v in snaps(run, p).items():
            spot, o, _, prev = spot_open(v)
            bs = bars.get(x) or []
            if not spot or not o or not prev or len(bs) < 21:
                continue
            closes_ = [float(b["c"]) for b in bs[-21:]]
            swing = statistics.mean(abs(closes_[k] / closes_[k - 1] - 1) for k in range(1, 21))
            hi, lo = max(float(b["h"]) for b in bs[-20:]), min(float(b["l"]) for b in bs[-20:])
            g = o / prev - 1
            if not swing or abs(g) < spec.get("gap", 0.02) or abs(g) / swing < spec.get("swings", 3):
                continue
            if (g > 0 and o > hi and spot >= o) or (g < 0 and o < lo and spot <= o):
                found.append((abs(g) / swing, x, spot, g, hi if g > 0 else lo))
        each = budget(run, spec, s, 1)
        for z, und, spot, g, level in sorted(found, reverse=True)[:8]:
            kind = "C" if g > 0 else "P"
            got = option_pick(run, und, kind, spot, spec.get("offset", 0.006), (run.today + timedelta(days=1)).isoformat(),
                              (run.today + timedelta(days=8)).isoformat())
            if not got or got[1] * 100 > each:
                continue
            c, ask = got
            exp = c["expiration_date"]
            out = min(next_business_day(run.today).isoformat(), exp)
            at = "15:50" if out < exp else "15:30"
            return (day, [{"sym": c["symbol"], "asset": "option", "und": und, "ask": ask, "expiry": exp, "exit_day": out, "exit_at": at,
                           "why": f"突破大跳空：{und} {'高' if g > 0 else '低'}开 {g:+.1%}（平时一天波动的 {z:.1f} 倍），"
                                  f"{'高于 20 日最高' if g > 0 else '低于 20 日最低'} {level:,.2f}，开盘后继续{'涨' if g > 0 else '跌'}；"
                                  f"买{'看涨' if kind == 'C' else '看跌'} {float(c['strike_price']):g}（{exp} 到期），拿到 {out[5:]} {at} 卖"}])
        return (day, [])


class HikeRally:
    """A fast run while the Fed is raising rates (owner, 10-16: "rising this fast, some news will knock it down").
    SMH since 2011: after 15 sessions up 10%+, 20 sessions later it was lower 12 of 44 times outside hiking cycles but 5
    of 10 inside them (2018, 2022-23), and 8%+ lower at some point 4 of 10. Only while `hiking` is set in the spec (the
    Fed's first hike in this cycle was 2026-09-16; unset it when the Fed cuts). At 15:50, for each of `unds` (symbol ->
    the 15-session rise, e.g. SMH 10%, QQQ 7%) that is up that much against its close 15 sessions ago: the put nearest
    the price, 25-45 days out; out after 2x on a 40% giveback, else 15:50 on the 20th session (15:30 if it expires
    first). Once a name fires it sits out 28 days (one ticket per run-up)."""

    def entries(self, run, spec, s):
        day = run.today.isoformat()
        if day in s["slots"] or not (spec.get("entry", "15:45") <= run.hhmm < "15:58"):
            return None
        if not spec.get("hiking"):
            return (day, [])
        unds = spec.get("unds") or {"SMH": 0.10, "QQQ": 0.07}
        last = s.setdefault("hike_last", {})
        cl = closes(run, sorted(unds), 60)
        held = {t.get("und") for t in s["open"]}
        found = []
        for und, rise in sorted(unds.items()):
            xs = cl.get(und) or []
            spot = spot_open(snaps(run, [und]).get(und) or {})[0]
            if len(xs) < 15 or not spot or und in held:
                continue
            if last.get(und) and (run.today - date.fromisoformat(last[und])).days < spec.get("rest_days", 28):
                continue
            r = spot / xs[-15] - 1
            if r >= rise:
                found.append((und, spot, r))
        picks = []
        for und, spot, r in found:
            got = option_pick(run, und, "P", spot, 0.0, (run.today + timedelta(days=25)).isoformat(),
                              (run.today + timedelta(days=45)).isoformat())
            if not got:
                continue
            c, ask = got
            exp = c["expiration_date"]
            out, at = held_out(run, exp, spec.get("hold", 20))
            last[und] = day
            picks.append({"sym": c["symbol"], "asset": "option", "und": und, "ask": ask, "trail": True, "expiry": exp,
                          "exit_day": out, "exit_at": at,
                          "why": f"加息期快涨：{und} 15 个交易日涨 {r:+.1%}（门槛 {unds[und]:.0%}），赌一个消息砸下来；买看跌 "
                                 f"{float(c['strike_price']):g}（{exp} 到期），翻倍后回撤 40% 卖，否则 {out[5:]} {at} 卖"})
        return (day, picks)


class DipWeek:
    """research/2026-10-16-dip-week.md: a dip given a week instead of a day. At 15:50, the pool name that opened the
    most below its prior close (2%+, `gap`) gets the call `offset` (3%) out on the nearest expiry 5 to 12 days away;
    out after 2x on a 40% giveback, else 15:50 on the fifth session (15:30 if it expires first). 1,772 gap-downs since
    2024-02, 3% out, sold on the fifth close: +19% per $1 at 5% costs each way (2024 +8%, 2025-26 +23%, without the
    best three +17%; median -70%), +8% at 10% costs -- so the bid and ask go into the why: the cost is the question."""

    def entries(self, run, spec, s):
        day = run.today.isoformat()
        if day in s["slots"] or not (spec.get("entry", "15:45") <= run.hhmm < "15:58"):
            return None
        held = {t.get("und") for t in s["open"]}
        gaps = []
        for x, v in snaps(run, pool(run)).items():
            spot, o, _, prev = spot_open(v)
            if spot and o and prev and o / prev - 1 <= -spec.get("gap", 0.02) and x not in held:
                gaps.append((o / prev - 1, x, spot, spot < o))
        each = budget(run, spec, s, 1)
        for g, und, spot, failed in sorted(gaps)[:8]:
            got = option_pick(run, und, "C", spot, spec.get("offset", 0.03), (run.today + timedelta(days=5)).isoformat(),
                              (run.today + timedelta(days=12)).isoformat())
            if not got or got[1] * 100 > each:
                continue
            c, ask = got
            q = (run.cache.get(("quote", c["symbol"])) or {}).get("latestQuote") or {}
            bid = float(q.get("bp") or 0)
            spread = f"，买价 ${bid:.2f} / 卖价 ${ask:.2f}（价差占中间价 {(ask - bid) / ((ask + bid) / 2):.0%}）" if bid > 0 else ""
            exp = c["expiration_date"]
            out, at = held_out(run, exp, spec.get("hold", 5))
            k = float(c["strike_price"])
            return (day, [{"sym": c["symbol"], "asset": "option", "und": und, "ask": ask, "trail": True, "expiry": exp,
                           "exit_day": out, "exit_at": at,
                           "why": f"抄底给一周：{und} 低开 {g:+.1%}，{'收盘前仍低于开盘' if failed else '已回到开盘之上'}；买看涨 {k:g}"
                                  f"（{exp} 到期，离现价 {k / spot - 1:.1%}）{spread}；翻倍后回撤 40% 卖，否则 {out[5:]} {at} 卖"}])
        return (day, [])


class OpenGap:
    """research/2026-10-13-open-catalyst.md, the one group worth watching: a 2%+ gap that the first minutes keep going
    the same way; the largest such gap, at the first cycle from 09:35, an option 0.6% beyond the price in the gap's
    direction, the day's expiry else the nearest within a week. Same-day: half on a 3x take, half on a trail, out 15:30.
    A later expiry is held overnight: out after 2x on a 40% giveback, else at 15:50 the next session."""

    def entries(self, run, spec, s):
        day = run.today.isoformat()
        if day in s["slots"] or not (spec.get("entry", "09:35") <= run.hhmm < "10:00"):
            return None
        gaps = []
        for x, v in snaps(run, pool(run)).items():
            spot, o, _, prev = spot_open(v)
            if spot and o and prev and abs(o / prev - 1) >= spec.get("gap", 0.02) and (spot >= o) == (o > prev):
                gaps.append((abs(o / prev - 1), x, spot, o / prev - 1))
        each = budget(run, spec, s, 1)
        for _, und, spot, g in sorted(gaps, reverse=True)[:8]:
            kind = "C" if g > 0 else "P"
            got = option_pick(run, und, kind, spot, spec.get("offset", 0.006), day, (run.today + timedelta(days=7)).isoformat())
            if not got or got[1] * 100 > each:
                continue
            c, ask = got
            exp, side = c["expiration_date"], "看涨" if kind == "C" else "看跌"
            head = f"{und} {'高' if g > 0 else '低'}开 {g:+.1%}、开盘后继续{'涨' if g > 0 else '跌'}，买{side} {float(c['strike_price']):g}（{exp} 到期）"
            if exp == day:
                return (day, [{"sym": c["symbol"], "asset": "option", "und": und, "ask": ask, "take": 3, "half": True, "expiry": exp,
                               "exit_day": day, "exit_at": "15:30", "why": f"{head}，一半 3 倍止盈、一半翻倍后回撤 40% 卖，最晚 15:30"}])
            out = min(next_business_day(run.today).isoformat(), exp)
            return (day, [{"sym": c["symbol"], "asset": "option", "und": und, "ask": ask, "trail": True, "expiry": exp,
                           "exit_day": out, "exit_at": "15:50" if out < exp else "15:30",
                           "why": f"{head}，留一晚：翻倍后回撤 40% 卖，否则 {out[5:]} {'15:50' if out < exp else '15:30'} 卖"}])
        return (day, [])


class Sniper:
    """claude-b's catalyst_open, our form: a pre-market Claude session writes at most one pick a day to
    journal-s500/sniper/YYYY-MM-DD.json ({"sniper": {"symbol", "direction", "catalyst", "confidence"} or null}).
    From the first cycle at 09:30 until 10:00, once the stock is not going against the pick, buy the nearest expiry
    (the day's if listed), the first strike `swing_frac` (half) of the stock's average daily move (20 sessions) out of
    the money -- never further out than `min_delta` (0.30): owner, 10-15, a fixed 0.10 delta is a lottery ticket on a
    slow mega-cap (AAPL 355C at 345.5) -- and ride it on the trail (after 2x, out on a 40% giveback), else out at 15:30
    on its expiry day."""

    def entries(self, run, spec, s):
        day = run.today.isoformat()
        if day in s["slots"] or not (spec.get("entry", "09:30") <= run.hhmm < spec.get("cutoff", "10:00")):
            return None
        path = ROOT / spec.get("dir", "journal-s500/sniper") / f"{day}.json"
        try:
            pk = (json.loads(path.read_text()) or {}).get("sniper")
        except Exception:
            pk = None
        if not pk or pk.get("direction") not in ("call", "put") or not pk.get("symbol"):
            return (day, [])  # no pick today
        und, kind = pk["symbol"], "C" if pk["direction"] == "call" else "P"
        spot, o, _, _ = spot_open(snaps(run, [und]).get(und) or {})
        if not spot or not o or (spot < o if kind == "C" else spot > o):
            return None  # going against the pick: wait for a later cycle, until the cutoff
        lo, hi = (spot, spot * 1.15) if kind == "C" else (spot * 0.85, spot)
        cs = contracts(run, und, day, (run.today + timedelta(days=7)).isoformat(), lo, hi, "call" if kind == "C" else "put")
        if not cs:
            return (day, [])
        exp = min(c["expiration_date"] for c in cs)
        near = [c for c in cs if c["expiration_date"] == exp]
        quotes = run.c.option_snapshots([c["symbol"] for c in near]) or {}
        xs = (closes(run, [und], 60).get(und) or [])[-21:]
        swing = statistics.mean(abs(xs[k] / xs[k - 1] - 1) for k in range(1, len(xs))) if len(xs) >= 6 else 0.02
        dist = max(spec.get("swing_frac", 0.5) * swing, 0.003)
        sign = 1 if kind == "C" else -1
        priced = []  # (how far out, contract, ask, |delta| or None), nearest the money first
        for c in near:
            q = quotes.get(c["symbol"]) or {}
            ask = float((q.get("latestQuote") or {}).get("ap") or 0)
            d = (q.get("greeks") or {}).get("delta")
            out = sign * (float(c["strike_price"]) / spot - 1)
            if ask > 0 and out >= 0:
                priced.append((out, c, ask, abs(float(d)) if d is not None else None))
        priced.sort(key=lambda x: x[0])
        if not priced:
            return (day, [])
        # half the stock's usual daily move out of the money (AAPL ~0.5%, CRWV ~3%), never past min_delta
        pick = next((x for x in priced if x[0] >= dist), priced[-1])
        floor = spec.get("min_delta", 0.30)
        if pick[3] is not None and pick[3] < floor:
            closer = [x for x in priced if x[0] <= pick[0] and x[3] is not None and x[3] >= floor]
            pick = closer[-1] if closer else priced[0]
        _, c, ask, d = pick
        how = f"离现价 {pick[0]:.1%}（平时一天波动 {swing:.1%} 的一半）" + (f"，delta {d:.2f}" if d is not None else "")
        exp = c["expiration_date"]
        return (day, [{"sym": c["symbol"], "asset": "option", "und": und, "ask": ask, "trail": True, "expiry": exp,
                       "exit_day": exp, "exit_at": "15:30",
                       "why": f"开盘狙击 {und}{'看涨' if kind == 'C' else '看跌'} {float(c['strike_price']):g}（{exp} 到期，{how}）："
                              f"{str(pk.get('catalyst', ''))[:120]}；翻倍后回撤 40% 卖，否则到期日 15:30 卖"}])


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
         "letf": LETF(), "relay": Relay(), "opengap": OpenGap(), "sniper": Sniper(), "oneshot": OneShot(),
         "overnight": Overnight(), "pead": Pead(), "vixspike": VixSpike(), "breakgap": BreakGap(),
         "hikerally": HikeRally(), "dipweek": DipWeek()}
