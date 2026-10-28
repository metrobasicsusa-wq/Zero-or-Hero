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
# Audit 10-26 #10: the rules the studies enter at 10:00 (ZeroDTE, VixSpike, PEAD, weekly, net) stop looking at 10:30;
# they used to look until 15:00, so a late cycle could buy hours after the moment the study measured.
CUTOFF = "10:30"
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


def history(run, syms: list[str], days: int = 120) -> dict[str, list[tuple[float, float]]]:
    """Completed daily (close, high) pairs, split-adjusted, oldest first (a bar without a high uses its close)."""
    key = ("history", tuple(sorted(syms)), days)

    def fetch():
        start = (run.today - timedelta(days=days)).isoformat()
        out = {}
        for i in range(0, len(syms), 100):
            for s, bs in run.c.daily_bars(syms[i:i + 100], start, adjustment="split").items():
                out[s] = [(float(b["c"]), float(b.get("h") or b["c"])) for b in bs if b["t"][:10] < run.today.isoformat()]
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


# Owner, 10-19: earnings only in shares or 2x funds (option IV collapses after the report). The most traded 2x long
# single-stock fund per name; names, fund names and 3-month average volumes checked on Yahoo 2026-10-19, funds under
# ~200k shares a day left out (BOEU, CRWL, QCMU, ASMG, SNOU, MRVU, SHPU, ANEL). Alpaca's asset is checked before
# each buy; anything not tradable there falls back to the stock itself.
LEVERED_2X = {"NVDA": "NVDL", "TSLA": "TSLL", "AAPL": "AAPU", "AMZN": "AMZU", "MSFT": "MSFU", "META": "METU",
              "GOOGL": "GGLL", "AMD": "AMDL", "COIN": "CONL", "MSTR": "MSTU", "PLTR": "PTIR", "MU": "MUU",
              "AVGO": "AVL", "NFLX": "NFXL", "TSM": "TSMX", "SMCI": "SMCX", "ARM": "ARMG", "HOOD": "ROBN",
              "UBER": "UBRL", "LLY": "LLYX", "IONQ": "IONX", "RKLB": "RKLX", "SOFI": "SOFX", "APP": "APPX",
              "ORCL": "ORCX", "CRWV": "CWVX", "NBIS": "NBIL", "INTC": "INTW", "BABA": "BABX", "NOW": "NOWL"}


def levered(run, und: str) -> tuple[str, bool] | None:
    """The 2x long fund for `und` when Alpaca trades it, else the stock; and whether only whole shares can be bought.
    None when neither is tradable."""
    for sym in ([LEVERED_2X[und]] if und in LEVERED_2X else []) + [und]:
        a = cached(run, ("asset", sym), lambda sym=sym: run.c.asset(sym)) or {}
        if a.get("tradable"):
            return sym, not a.get("fractionable", False)
    return None


def verified(r: dict) -> bool:
    """A report date both calendars agree on (hero/earnings.py marks the others)."""
    return not r.get("disputed") and not r.get("yahoo_date")


def share_pick(run, und: str, spot: float, nxt: str, why: str) -> dict | None:
    """Shares or the 2x fund, bought now and sold at the next session's 09:35."""
    got = levered(run, und)
    if not got:
        return None
    sym, whole = got
    ask = spot if sym == und else spot_open(snaps(run, [sym]).get(sym) or {})[0]
    if not ask:
        return None
    what = f"两倍 ETF {sym}" if sym != und else "正股"
    return {"sym": sym, "asset": "stock", "und": und, "ask": ask, "whole": whole, "exit_day": nxt, "exit_at": "09:35",
            "why": f"{why}，买{what}，第二天 09:35 卖（10-19 起财报只做正股 / 两倍）"}


def unverified_skip(run, spec, und: str, r: dict) -> None:
    run.j.event("sleeve_skip", dry_run=bool(spec.get("plan_only")), sleeve=spec["id"], symbol=und,
                why=f"{spec['id']}：{und} {r['date']} 的财报日期没核实（"
                    + ("Alpha Vantage 和 Nasdaq 对不上" if r.get("disputed") else f"Yahoo 的公司确认日期是 {r.get('yahoo_date')}")
                    + "），不买")


def budget(run, spec, s, n: int) -> float:
    """What one pick may cost in this sleeve: one contract the ledger can pay for. The runner sizes it with the
    sleeve's fraction and, when that cannot buy one, goes all in for one (hero.sleeves.units; owner, 10-22: before,
    a contract above half the ledger was passed over here and small ledgers stopped trading)."""
    return s["cash"]


def trend20(xs: list[float]) -> int:
    if len(xs) < 21:
        return 0
    return 1 if xs[-1] > xs[-21] else -1


# ---------- the rules ----------
class ZeroDTE:
    """10:00, the option expiring today (with `fallback`, when the name has none today, its nearest expiry within a week;
    without it, the day is sat out -- audit 10-26 #4), with the
    trend since the open, `offset` beyond the price, a `take` x limit, out at 15:30 the same day either way. With
    `half_take`, half the contracts carry the take and the rest run on a trail (research/2026-10-13-zdte-timing.md).
    Optional: `side` "call" / "put" fixes the side; `after_drop` (e.g. -0.01) trades only the day after a close
    at least that far below the one before it (research/2026-10-08-zdte-streak.md); `min_move` (e.g. 0.005) trades
    only when the price is at least that far from the open at entry time, else the day is sat out
    (research/2026-10-20-zdte-cost.md: the only group of days that paid under every cost assumption)."""

    def entries(self, run, spec, s):
        day = run.today.isoformat()
        if day in s["slots"] or not (spec.get("entry", "10:00") <= run.hhmm < spec.get("cutoff", CUTOFF)):
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
        if spec.get("min_move") and abs(spot / o - 1) < spec["min_move"]:
            run.j.event("sleeve_skip", dry_run=False, sleeve=spec["id"], symbol=und,
                        why=f"{spec['id']}：{und} 开盘 ${o:,.2f}、现在 ${spot:,.2f}，离开盘 {spot / o - 1:+.2%}，"
                            f"不到 {spec['min_move']:.1%}，今天不做")
            return (day, [])
        kind = {"call": "C", "put": "P"}.get(spec.get("side"), "C" if spot >= o else "P")
        # audit 10-26 #4: no same-day expiry -> no trade, unless the spec sets `fallback` (the nearest within a week).
        # The 12 fallback buys lost 40%, against the same-day study's verdict (research/2026-10-13-zdte-nearest.md).
        fallback = bool(spec.get("fallback"))
        got = option_pick(run, und, kind, spot, spec["offset"], day,
                          (run.today + timedelta(days=7)).isoformat() if fallback else day)
        if not got:
            if not fallback:
                sid = spec.get("id", spec.get("kind", "zdte"))
                run.j.event("sleeve_skip", dry_run=bool(spec.get("plan_only")), sleeve=sid, symbol=und,
                            why=f"{sid}：{und} 今天没有当天到期的期权（或没有报价），不买最近到期的（审计 10-26 #4），今天不做")
            return (day, [])  # nothing listed (within a week, with `fallback`)
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


def too_wide(run, spec, c: dict, ask: float) -> tuple[float, bool]:
    """(bid, skip): owner, 10-23 (docs/hero-plan.md): the hero rates were measured at 5% costs a side, so a quote wider
    than `max_spread` of the mid (or with no bid) is passed over and logged. No cap in the spec: never skip."""
    q = (run.cache.get(("quote", c["symbol"])) or {}).get("latestQuote") or {}
    bid = float(q.get("bp") or 0)
    cap = spec.get("max_spread")
    if cap is None:
        return bid, False
    if bid <= 0 or (ask - bid) / ((ask + bid) / 2) > cap:
        run.j.event("sleeve_skip", dry_run=True, sleeve=spec.get("id", spec.get("kind", "")), symbol=c["symbol"],
                    why=f"{spec.get('id', spec.get('kind', ''))}：{c['symbol']} 买价 ${bid:.2f} / 卖价 ${ask:.2f}，价差超过中间价的 {cap:.0%}，换下一只")
        return bid, True
    return bid, False


def realized_vol(run, sym: str = "SPY", n: int = 20) -> float | None:
    """Annualised standard deviation of the last n daily close-to-close moves, None without enough closes."""
    xs = (closes(run, [sym], 60).get(sym) or [])[-(n + 1):]
    if len(xs) < n + 1:
        return None
    rs = [xs[k] / xs[k - 1] - 1 for k in range(1, len(xs))]
    return statistics.pstdev(rs) * 252 ** 0.5


def calm(run, spec, s) -> bool:
    """Owner, 10-23: dip-buying only pays when the market is moving. With `min_vol` set, a 20-day SPY realised vol under
    it (the backtest's bottom third, under 10.3%: -19% a trade, 11% ever 2x) sits the day out. No data: trade."""
    lo = spec.get("min_vol")
    if lo is None:
        return False
    v = realized_vol(run)
    if v is None or v >= lo:
        return False
    run.j.event("sleeve_skip", dry_run=True, sleeve=spec.get("id", spec.get("kind", "")), symbol="SPY",
                why=f"{spec.get('id', spec.get('kind', ''))}：SPY 20 日实现波动 {v:.1%} 低于 {lo:.1%}（回测里最低的三分之一，这段平均每 $1 -19%），今天不做")
    return True


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
            bid, skip = too_wide(run, spec, c, ask)
            if skip:
                continue
            exp = c["expiration_date"]
            out, at = held_out(run, exp)
            spread = f"，买价 ${bid:.2f} / 卖价 ${ask:.2f}" if bid > 0 else ""
            return (day, [{"sym": c["symbol"], "asset": "option", "und": und, "ask": ask, "trail": True, "expiry": exp,
                           "exit_day": out, "exit_at": at,
                           "why": f"隔夜动量：{und} 今天涨 {r:+.1%}、收在当天区间 {pos:.0%} 处、在 50 日均线上方，买看涨 "
                                  f"{float(c['strike_price']):g}（{exp} 到期）{spread}；翻倍后回撤 40% 卖，否则 {out[5:]} {at} 卖"}])
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
        maybe = s.setdefault("pead_unknown", {})

        def add(book: dict, react: date, x: str) -> None:
            if run.today <= react <= run.today + timedelta(days=7):
                book.setdefault(react.isoformat(), [])
                if x not in book[react.isoformat()]:
                    book[react.isoformat()].append(x)
        for x, rs in reports.items():
            for r in rs:
                rd, when = date.fromisoformat(r["date"]), r.get("time", "")
                if "pre" in when or "post" in when:
                    add(w, rd if "pre" in when else next_business_day(rd), x)
                else:
                    # audit 10-26 #7: time unknown (a third of the calendar) used to be skipped. Both days are candidates:
                    # the report day as if before the open, the next session only if the report day did not gap (entries)
                    add(w, rd, x)
                    add(maybe, next_business_day(rd), x)
        cut = (run.today - timedelta(days=10)).isoformat()
        for book in (w, maybe):
            for k in [k for k in book if k < cut]:
                del book[k]

    def entries(self, run, spec, s):
        self.remember(run, spec, s)
        day = run.today.isoformat()
        if day in s["slots"] or not (spec.get("entry", "10:00") <= run.hhmm < spec.get("cutoff", CUTOFF)):
            return None
        react = business_day_before(run.today).isoformat()
        names = sorted(s["pead_watch"].get(react) or [])
        maybe = sorted(set((s.get("pead_unknown") or {}).get(react) or []) - set(names))   # audit 10-26 #7
        if not names and not maybe:
            return (day, [])
        every = names + maybe

        def fetch():
            start = (run.today - timedelta(days=10)).isoformat()
            return run.c.daily_bars(every, start, adjustment="raw")
        bars = cached(run, ("pead_bars", tuple(every)), fetch) or {}
        found = []
        for x in every:
            bs = [b for b in bars.get(x, []) if b["t"][:10] < day]
            if len(bs) < 2 or bs[-1]["t"][:10] != react:
                continue
            if x in maybe:   # the session after an unknown-time report: only if the report day itself did not gap
                if len(bs) < 3 or not float(bs[-3]["c"]):
                    continue
                # review 10-28: the report day's own gap decides (3%, as EarningsLotto), not the entry gap -- pead-500
                # enters on 10%, and a 6% gap on the report day is still the reaction
                if abs(float(bs[-2]["o"]) / float(bs[-3]["c"]) - 1) >= spec.get("unknown_gap", 0.03):
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
            unknown = "（财报时间未知，财报当天没跳空，按跳空认定昨天是反应日）" if und in maybe else ""
            return (day, [{"sym": c["symbol"], "asset": "option", "und": und, "ask": ask, "trail": True, "expiry": exp,
                           "exit_day": out, "exit_at": at,
                           "why": f"财报后漂移：{und} 昨天财报高开 {g:+.1%}{unknown}、收盘比开盘 {held:+.1%} 守住，买看涨 "
                                  f"{float(c['strike_price']):g}（{exp} 到期）；翻倍后回撤 40% 卖，否则 {out[5:]} {at} 卖"}])
        return (day, [])


class VixSpike(ZeroDTE):
    """research/2026-10-13-vixspike.md: the session after a fear day (VIXY up `vix_up` 8%+ and SPY down 1.5%+ at the
    close), at 10:00 the 0DTE call 0.6% above the price (`und`, SPY by default), a 3x take, out 15:30."""

    def entries(self, run, spec, s):
        day = run.today.isoformat()
        if day in s["slots"] or not (spec.get("entry", "10:00") <= run.hhmm < spec.get("cutoff", CUTOFF)):
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
        if wk in s["slots"] or run.today.weekday() > 1 or not ("10:00" <= run.hhmm < spec.get("cutoff", CUTOFF)):
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
        if wk in s["slots"] or run.today.weekday() > 1 or not ("10:00" <= run.hhmm < spec.get("cutoff", CUTOFF)):
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
    out after 2x on a 40% giveback, else 15:50 on the fifth session (15:30 if it expires first); `exit` "hold" keeps it to
    that close, "take2" rests a sell at 2x (owner, 10-23, docs/hero-plan.md: the three exits the hero game rated), and
    `max_spread` skips a quote wider than that share of the mid (the hero rate was measured at 5% costs), `min_vol` sits out
    a calm market (SPY 20-day realised vol under it: the backtest's bottom third lost 19% a trade). 1,772 gap-downs since
    2024-02, 3% out, sold on the fifth close: +19% per $1 at 5% costs each way (2024 +8%, 2025-26 +23%, without the
    best three +17%; median -70%), +8% at 10% costs -- so the bid and ask go into the why: the cost is the question."""

    def entries(self, run, spec, s):
        day = run.today.isoformat()
        if day in s["slots"] or not (spec.get("entry", "15:45") <= run.hhmm < "15:58"):
            return None
        if calm(run, spec, s):
            return (day, [])
        held = {t.get("und") for t in s["open"]}
        gaps = []
        for x, v in snaps(run, pool(run)).items():
            spot, o, _, prev = spot_open(v)
            if spot and o and prev and o / prev - 1 <= -spec.get("gap", 0.02) and x not in held:
                gaps.append((o / prev - 1, x, spot, spot < o))
        each = budget(run, spec, s, 1)
        exit_ = spec.get("exit", "trail")   # trail: after 2x, out on a 40% giveback; hold: the fifth close; take2: all out at 2x
        for g, und, spot, failed in sorted(gaps)[:8]:
            got = option_pick(run, und, "C", spot, spec.get("offset", 0.03), (run.today + timedelta(days=5)).isoformat(),
                              (run.today + timedelta(days=12)).isoformat())
            if not got or got[1] * 100 > each:
                continue
            c, ask = got
            bid, skip = too_wide(run, spec, c, ask)
            if skip:
                continue
            spread = f"，买价 ${bid:.2f} / 卖价 ${ask:.2f}（价差占中间价 {(ask - bid) / ((ask + bid) / 2):.0%}）" if bid > 0 else ""
            exp = c["expiration_date"]
            out, at = held_out(run, exp, spec.get("hold", 5))
            k = float(c["strike_price"])
            pk = {"sym": c["symbol"], "asset": "option", "und": und, "ask": ask, "trail": exit_ == "trail", "expiry": exp,
                  "exit_day": out, "exit_at": at}
            if exit_ == "take2":
                pk["take"] = 2
            sell = {"hold": "拿到", "take2": "2 倍止盈，否则"}.get(exit_, "翻倍后回撤 40% 卖，否则")
            pk["why"] = (f"抄底给一周：{und} 低开 {g:+.1%}，{'收盘前仍低于开盘' if failed else '已回到开盘之上'}；买看涨 {k:g}"
                         f"（{exp} 到期，离现价 {k / spot - 1:.1%}）{spread}；{sell} {out[5:]} {at} 卖")
            return (day, [pk])
        return (day, [])


class Pullback:
    """research/2026-10-23-pullback.md and the owner's 10-23 ask (「能从中找出那个最强的吗」, then 「我人不在 你自己决定」):
    a strong stock's small pullback, bought with next week's call. At 15:50, among the pool names up `mom` (20%) or
    more over the 20 sessions before today, above their 50-day average, closing 3-10% under their 20-session high and
    (`down_day`) under yesterday's close, the deepest pullback gets the call `offset` (at the money) out on the nearest
    expiry 8 to 12 days away; exits, `max_spread` and `min_vol` as DipWeek. The cut was chosen from ~30 pre-specified
    splits of 1,953 events (2024-02 on, 101 names): 485 events, +23% per $1 at 5% costs (+11% at 10%), every year
    positive, first and second half alike (+23% / +22%), the stock itself +2.8% in five sessions; as one pick a day
    +21%, 7/61 rounds to 20x all in (6-22%), about $4,400 a hero. Caveats in the same report: the last three months
    -36% (a calm market, like dip-week), five names carry 42% of the profit, and the best of thirty cuts flatters."""

    def entries(self, run, spec, s):
        day = run.today.isoformat()
        if day in s["slots"] or not (spec.get("entry", "15:45") <= run.hhmm < "15:58"):
            return None
        if calm(run, spec, s):
            return (day, [])
        held = {t.get("und") for t in s["open"]}
        names = [x for x in pool(run) if x not in held]
        hist = history(run, names)
        found = []
        for x, v in snaps(run, names).items():
            bars = hist.get(x) or []
            spot, _, _, prev = spot_open(v)
            if not spot or len(bars) < 51:
                continue
            cs = [c for c, _ in bars]
            mom = cs[-1] / cs[-21] - 1
            hi20 = max(h for _, h in bars[-20:])
            ma50 = statistics.mean(cs[-50:])
            dip = spot / hi20 - 1
            if mom >= spec.get("mom", 0.20) and spot > ma50 and spec.get("low", -0.10) <= dip <= spec.get("high", -0.03) \
                    and (not spec.get("down_day", True) or spot < (prev or cs[-1])):
                found.append((dip, x, spot, mom))
        each = budget(run, spec, s, 1)
        exit_ = spec.get("exit", "trail")
        for dip, und, spot, mom in sorted(found)[:8]:
            got = option_pick(run, und, "C", spot, spec.get("offset", 0.0), (run.today + timedelta(days=spec.get("min_days", 8))).isoformat(),
                              (run.today + timedelta(days=spec.get("max_days", 12))).isoformat())
            if not got or got[1] * 100 > each:
                continue
            c, ask = got
            bid, skip = too_wide(run, spec, c, ask)
            if skip:
                continue
            spread = f"，买价 ${bid:.2f} / 卖价 ${ask:.2f}（价差占中间价 {(ask - bid) / ((ask + bid) / 2):.0%}）" if bid > 0 else ""
            exp = c["expiration_date"]
            out, at = held_out(run, exp, spec.get("hold", 5))
            k = float(c["strike_price"])
            pk = {"sym": c["symbol"], "asset": "option", "und": und, "ask": ask, "trail": exit_ == "trail", "expiry": exp,
                  "exit_day": out, "exit_at": at}
            if exit_ == "take2":
                pk["take"] = 2
            sell = {"hold": "拿到", "take2": "2 倍止盈，否则"}.get(exit_, "翻倍后回撤 40% 卖，否则")
            pk["why"] = (f"强势股回调：{und} 前 20 天 {mom:+.0%}、在 50 日线上，今天收在 20 日最高下方 {-dip:.1%}"
                         f"{'、收阴' if spec.get('down_day', True) else ''}；买看涨 {k:g}（{exp} 到期，离现价 {k / spot - 1:+.1%}）{spread}；"
                         f"{sell} {out[5:]} {at} 卖")
            return (day, [pk])
        return (day, [])


class Streak:
    """research/2026-10-26-bottom.md and the owner's 10-26 ask (「抄底 我需要个抄底时机 这也是一个 hero 方式」): the third
    straight down close on an index ETF, the one bottom signal that beat buying on any day. At 15:50, the first of `unds`
    (SMH, QQQ, SPY, IWM in that order) not already held whose last two completed closes each fell under the one before,
    whose close before those was not itself a down day (the fourth down day lost in the study) and whose price now sits
    under yesterday's close gets the call `offset` (at the money) out on the nearest expiry 5 to 12 days away (the
    study's leg); exits, `max_spread` and `min_vol` as DipWeek. 127 signals on the four ETFs since 2024-02, sold on the
    fifth close at 5% costs: +28% per $1 (+15% at 10%), every year positive (+39% / +28% / +14%), without the best
    three +20%, median +6%, 53% winners, against -2% for buying every fifth day; by ETF SMH +55% (33, 70% winners),
    QQQ +43%, SPY +10%, IWM +2%. Hero game, one pick a day all in: 7/41 rounds to 20x (9-31%), about $2,900 a hero.
    Caveats from the same report: the last three months -38% (14 signals), the low-vol third only +7% (high-vol +56%),
    the 3% out call loses (-4%), and buying the big down day itself or the fourth down day loses. `dte` (lo, hi) moves the
    expiry window and `green` takes the first up close after three or more down closes instead (owner, 10-26
    「抄底是可以选择呆久一点的 可以模拟用 2000 抄底 回测」, research/2026-10-26-bottom-long.md: SMH, the 20-45 day call held ten
    sessions: third down day at the money +37% per $1 (every year positive, without the best three +16%), first green close
    5% out +97% (+51%); the $2,000 ledger reaches $10,000 in 2 of 3 half-stake rounds)."""

    UNDS = ("SMH", "QQQ", "SPY", "IWM")

    def entries(self, run, spec, s):
        day = run.today.isoformat()
        if day in s["slots"] or not (spec.get("entry", "15:45") <= run.hhmm < "15:58"):
            return None
        if calm(run, spec, s):
            return (day, [])
        held = {t.get("und") for t in s["open"]}
        unds = [x for x in spec.get("unds", self.UNDS) if x not in held]
        hist = history(run, unds, days=40)
        snap = snaps(run, unds)
        each = budget(run, spec, s, 1)
        exit_ = spec.get("exit", "trail")
        for und in unds:
            cs = [c for c, _ in hist.get(und) or []]
            spot, _, _, prev = spot_open(snap.get(und) or {})
            if not spot or len(cs) < 4:
                continue
            prev = prev or cs[-1]
            if spec.get("green"):
                # the first up close after three or more down closes (research/2026-10-26-bottom-long.md: 5% out, ten sessions)
                if not (spot > prev and cs[-1] < cs[-2] < cs[-3] < cs[-4]):
                    continue
            elif not (spot < prev and cs[-1] < cs[-2] < cs[-3] and cs[-3] >= cs[-4]):
                continue
            lo, hi = spec.get("dte", (5, 12))
            got = option_pick(run, und, "C", spot, spec.get("offset", 0.0), (run.today + timedelta(days=lo)).isoformat(),
                              (run.today + timedelta(days=hi)).isoformat())
            if not got or got[1] * 100 > each:
                continue
            c, ask = got
            bid, skip = too_wide(run, spec, c, ask)
            if skip:
                continue
            spread = f"，买价 ${bid:.2f} / 卖价 ${ask:.2f}（价差占中间价 {(ask - bid) / ((ask + bid) / 2):.0%}）" if bid > 0 else ""
            exp = c["expiration_date"]
            out, at = held_out(run, exp, spec.get("hold", 5))
            k = float(c["strike_price"])
            pk = {"sym": c["symbol"], "asset": "option", "und": und, "ask": ask, "trail": exit_ == "trail", "expiry": exp,
                  "exit_day": out, "exit_at": at}
            if exit_ == "take2":
                pk["take"] = 2
            sell = {"hold": "拿到", "take2": "2 倍止盈，否则"}.get(exit_, "翻倍后回撤 40% 卖，否则")
            head = (f"抄底第一根阳线：{und} 连跌 3 天后今天收阳（{cs[-3]:.2f} → {cs[-2]:.2f} → {cs[-1]:.2f} → 现价 {spot:.2f}）" if spec.get("green")
                    else f"抄底三连跌：{und} 连跌第 3 天（{cs[-3]:.2f} → {cs[-2]:.2f} → {cs[-1]:.2f} → 现价 {spot:.2f}，三天 {spot / cs[-3] - 1:+.1%}）")
            pk["why"] = f"{head}；买看涨 {k:g}（{exp} 到期，离现价 {k / spot - 1:+.1%}）{spread}；{sell} {out[5:]} {at} 卖"
            return (day, [pk])
        return (day, [])


class Bounce:
    """research/2026-10-26-bounce.md and the owner's 10-26 asks (「AVGO 今天就有很好的机会」, 「330 CALL 早上好便宜」, 「今天的」:
    AVGO's same-day 330 call went $0.18 to $2.36 by noon on the first bounce after SMH's 6.7% Thursday). At 10:00, when
    `sector` (SMH) fell `flush` (5%) or more over its prior one to three sessions and now sits `bounce` (1%) or more
    above its own open, the one of `unds` (AVGO, NVDA, AMD, MU) furthest above its own open, at least `own` (1%), gets
    the call `offset` (0.6%) out on the nearest expiry on or after today (the daily expiry when the name has one),
    held with no take (the study's 2x take flattened the result: the profit is in the tail) to 15:30 when it expires
    that day, like every other rule (audit 10-26 #9; it was 15:50), else 15:50. Ten such days
    since 2024-02, 40 calls across the four names: +49% per $1 at 5% costs (+35% at 10%), against -15% on bounce days
    without a flush and -34% on any day; without the best three -4%, median -8%, 38% winners, 2024 -22% / 2025 -37% /
    2026 +125%; the name that is itself up 1%+ at 10:00: 33 calls, +65%, without the best three +2%. A lottery with a
    fat right tail (6 of 40 doubled, one 10x, four to zero), a few fires a year; `max_spread` as DipWeek."""

    UNDS = ("AVGO", "NVDA", "AMD", "MU")

    def entries(self, run, spec, s):
        day = run.today.isoformat()
        if day in s["slots"] or not (spec.get("entry", "10:00") <= run.hhmm < spec.get("until", "10:15")):
            return None
        sector = spec.get("sector", "SMH")
        unds = [x for x in spec.get("unds", self.UNDS) if x not in {t.get("und") for t in s["open"]}]
        cs = [c for c, _ in (history(run, [sector], days=15).get(sector) or [])]
        if len(cs) < 4:
            return None
        drop = min(cs[-1] / cs[-1 - k] - 1 for k in (1, 2, 3))
        snap = snaps(run, [sector] + unds)
        sspot, sopen, _, _ = spot_open(snap.get(sector) or {})
        if not sspot or not sopen:
            return None
        m10 = sspot / sopen - 1
        if drop > -spec.get("flush", 0.05) or m10 < spec.get("bounce", 0.01):
            return (day, [])   # an ordinary day
        found = []
        for und in unds:
            spot, o, _, _ = spot_open(snap.get(und) or {})
            if spot and o and spot / o - 1 >= spec.get("own", 0.01):
                found.append((spot / o - 1, und, spot))
        each = budget(run, spec, s, 1)
        for u10, und, spot in sorted(found, reverse=True):
            got = option_pick(run, und, "C", spot, spec.get("offset", 0.006), day, (run.today + timedelta(days=7)).isoformat())
            if not got or got[1] * 100 > each:
                continue
            c, ask = got
            bid, skip = too_wide(run, spec, c, ask)
            if skip:
                continue
            spread = f"，买价 ${bid:.2f} / 卖价 ${ask:.2f}（价差占中间价 {(ask - bid) / ((ask + bid) / 2):.0%}）" if bid > 0 else ""
            exp = c["expiration_date"]
            k = float(c["strike_price"])
            at = "15:30" if exp == day else "15:50"   # audit 10-26 #9: a contract on its expiry leaves at 15:30, as every other rule's
            pk = {"sym": c["symbol"], "asset": "option", "und": und, "ask": ask, "expiry": exp, "trail": False,
                  "exit_day": day, "exit_at": at}
            if spec.get("take"):
                pk["take"], pk["half"] = spec["take"], False
            pk["why"] = (f"板块反弹日：{sector} 前 1–3 天跌 {drop:.1%}，10:00 离开盘 {m10:+.1%}；{und} 离开盘 {u10:+.1%}，"
                         f"买{'当天到期' if exp == day else '最近到期（' + exp[5:] + '）'}看涨 {k:g}（离现价 {k / spot - 1:+.1%}）{spread}；"
                         f"{('%g 倍止盈，否则 ' % spec['take']) if spec.get('take') else ''}{at} 卖")
            return (day, [pk])
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
    on its expiry day. The pick may say `"overnight": true` (the nearest expiry after today, held to the next session's
    15:50: research/2026-10-15-sniper-factors3.md, a 20-day break with a 3x gap pays into the next close) or name an
    `"expiry"` (the first listed on or after it); 10-23 the T pick said 10-30 in prose and the rule bought the day's
    $0.01 contract."""

    def entries(self, run, spec, s):
        day = run.today.isoformat()
        if day in s["slots"] or not (spec.get("entry", "09:30") <= run.hhmm < spec.get("cutoff", "10:00")):
            return None
        path = ROOT / spec.get("dir", "journal-s500/sniper") / f"{day}.json"
        try:
            doc = json.loads(path.read_text()) or {}
            if spec.get("rank"):   # owner, 10-22 (samples too slow): plan twins follow candidates 2 and 3 as well
                pk = next((c for c in doc.get("candidates") or [] if int(c.get("rank") or 0) == int(spec["rank"])), None)
                pk = {**pk, "catalyst": f"候选第 {spec['rank']} 名：{pk.get('why', '')}"} if pk else None
            else:
                pk = doc.get("sniper")
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
        exps = sorted({c["expiration_date"] for c in cs})
        if pk.get("expiry"):
            exps = [e for e in exps if e >= str(pk["expiry"])]
        elif pk.get("overnight"):
            exps = [e for e in exps if e > day]
        if not exps:
            return (day, [])
        exp = exps[0]
        near = [c for c in cs if c["expiration_date"] == exp]
        quotes = run.c.option_snapshots([c["symbol"] for c in near]) or {}
        xs = (closes(run, [und], 60).get(und) or [])[-21:]
        swing = statistics.mean(abs(xs[k] / xs[k - 1] - 1) for k in range(1, len(xs))) if len(xs) >= 6 else 0.02
        frac = spec.get("swing_frac", 0.5)
        dist = max(frac * swing, 0.003)
        sign = 1 if kind == "C" else -1
        min_ask = spec.get("min_ask", 0.05)   # audit 10-26 #6: never a penny contract
        priced = []  # (how far out, contract, ask, |delta| or None), nearest the money first
        for c in near:
            q = quotes.get(c["symbol"]) or {}
            ask = float((q.get("latestQuote") or {}).get("ap") or 0)
            d = (q.get("greeks") or {}).get("delta")
            out = sign * (float(c["strike_price"]) / spot - 1)
            if ask >= min_ask and out >= 0:
                priced.append((out, c, ask, abs(float(d)) if d is not None else None))
        priced.sort(key=lambda x: x[0])
        if not priced:
            return (day, [])
        # `swing_frac` (half) of the stock's usual daily move out of the money (AAPL ~0.5%, CRWV ~3%), never past min_delta.
        # Audit 10-26 #6: a contract without a delta counts as under the floor (it used to skip the check and buy $0.01).
        pick = next((x for x in priced if x[0] >= dist), priced[-1])
        floor = spec.get("min_delta", 0.30)
        if pick[3] is None or pick[3] < floor:
            closer = [x for x in priced if x[0] <= pick[0] and x[3] is not None and x[3] >= floor]
            pick = closer[-1] if closer else priced[0]
        _, c, ask, d = pick
        part = {0.5: "的一半", 1.0: "，一整天"}.get(frac, f"的 {frac:g} 倍")
        how = (f"离现价 {pick[0]:.1%}（目标是平时一天波动 {swing:.1%}{part}）"
               + (f"，delta {d:.2f}" if d is not None else "，没有 delta，按最接近现价的买"))
        exp = c["expiration_date"]
        out, at = exp, "15:30"
        if pk.get("overnight") or pk.get("expiry"):   # held to the next session's close, the expiry's 15:30 if sooner
            out = min(next_business_day(run.today).isoformat(), exp)
            at = "15:50" if out < exp else "15:30"
        sell = f"拿到 {out[5:]} {at} 卖" if out != exp or at != "15:30" else "到期日 15:30 卖"
        return (day, [{"sym": c["symbol"], "asset": "option", "und": und, "ask": ask, "trail": True, "expiry": exp,
                       "exit_day": out, "exit_at": at,
                       "why": f"开盘狙击 {und}{'看涨' if kind == 'C' else '看跌'} {float(c['strike_price']):g}（{exp} 到期，{how}）："
                              f"{str(pk.get('catalyst', ''))[:120]}；翻倍后回撤 40% 卖，否则{sell}"}])


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
    """The close before a report's reaction: the stock's 2x long fund (or the stock), sold at the next open.
    Until 10-19 it bought a call about 2x the implied move above; option IV collapsing after the report took
    earn-500 from $500 to $211 in a week, so (owner, 10-19) shares or 2x funds only. The names are still the
    pool's reports with the largest implied moves (at-the-money straddle), now only to choose. `instrument`
    "option" brings back the old call (about 2x the implied move above) for a plan-only twin."""

    def entries(self, run, spec, s):
        day = run.today.isoformat()
        if day in s["slots"] or not ("15:40" <= run.hhmm < "15:56"):
            return None
        try:
            reports = json.loads((ROOT / "data" / "earnings.json").read_text()).get("reports") or {}
        except Exception:
            reports = {}
        nxt = next_business_day(run.today).isoformat()
        inpool = set(pool(run))
        names, unknown = [], set()
        for x, rs in reports.items():
            for r in rs:
                t = r.get("time", "")
                blind = "pre" not in t and "post" not in t   # audit 10-26 #7: a third of the calendar; decided by today's gap below
                if (r["date"] == day and ("post" in t or blind)) or (r["date"] == nxt and "pre" in t):
                    if x not in inpool:
                        continue
                    if not verified(r):
                        unverified_skip(run, spec, x, r)
                        continue
                    names.append(x)
                    if blind:
                        unknown.add(x)
        picks = []
        for und in names:
            spot, o, _, prev = spot_open(snaps(run, [und]).get(und) or {})
            if not spot:
                continue
            if und in unknown:
                # the report is dated today, time unknown: a gap at today's open means it came before the open and the
                # reaction is over; a quiet open means it comes after the close, so tonight is the reaction
                g = o / prev - 1 if o and prev else 0.0
                if abs(g) >= spec.get("unknown_gap", 0.03):
                    run.j.event("sleeve_skip", dry_run=bool(spec.get("plan_only")), sleeve=spec.get("id", "earnings"), symbol=und,
                                why=f"{spec.get('id', 'earnings')}：{und} 今天财报、时间未知，今天开盘跳空 {g:+.1%}，"
                                    "说明盘前已经发了、反应已过，不买")
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
            if spec.get("instrument") == "put":
                # 10-26 (the owner's 「全面优化」 after three sub-agents rebutted the earnings review): this season's reports
                # open down more often than up (45%) and shares have no convexity, so the plan twins hold the at-the-money
                # put into the report instead; `weekly_only` skips names whose nearest expiry is more than a week out
                # (a monthly straddle prices days beyond the report). Plan only: the lower bound was still negative.
                if spec.get("weekly_only") and (date.fromisoformat(exp) - date.fromisoformat(nxt)).days > 7:
                    continue
                ask = ask_of(run, atm_p["symbol"])
                if ask > 0:
                    picks.append((move, {"sym": atm_p["symbol"], "asset": "option", "und": und, "ask": ask, "expiry": exp,
                                         "exit_day": nxt, "exit_at": "09:35",
                                         "why": f"{und} 财报（期权定价的波动 {move:.1%}），买平值认沽 "
                                                f"{float(atm_p['strike_price']):g}（{exp} 到期），第二天 09:35 卖（只演算：这季财报开盘跌的多，正股没有凸性）"}))
                continue
            if spec.get("instrument") == "option":
                # the rule before 10-19, kept only for its plan twin (owner, 10-21: what would the stopped rule have made?)
                c = pick(cs, "C", spot, 2 * move, exp)
                ask = ask_of(run, c["symbol"]) if c else 0
                if c and ask > 0:
                    picks.append((move, {"sym": c["symbol"], "asset": "option", "und": und, "ask": ask, "expiry": exp,
                                         "exit_day": nxt, "exit_at": "09:35",
                                         "why": f"{und} 财报（期权定价的波动 {move:.1%}），买 2 倍波动处看涨 "
                                                f"{float(c['strike_price']):g}（{exp} 到期），第二天 09:35 卖（10-19 前的财报彩票）"}))
                continue
            pk = share_pick(run, und, spot, nxt, f"{und} 财报（期权定价的波动 {move:.1%}）")
            if pk:
                picks.append((move, pk))
        out = [pk for _, pk in sorted(picks, key=lambda x: -x[0])[:spec.get("n", 3)]]
        for pk in out:
            if pk.get("und") in unknown:
                pk["why"] += "；财报时间未知，今天开盘没跳空，按盘后发算"
        return (day, out)


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
    """Zero-or-hero relay (research/2026-10-05-hero.md, "混合"): all of the sleeve on one earnings play a week --
    the name reporting this week with the strongest 126-session momentum, bought at the close before the reaction
    and sold at the next open; since 10-19 (owner) its 2x long fund or the stock, not a call about 10% out of
    the money -- until the sleeve is at `switch_at` x its start;
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
                if not verified(r):
                    continue
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
        pk = share_pick(run, und, spot, nxt, f"接力第一段：本周财报里 126 日动量最强的 {und}（{mom[und]:+.0%}），全仓")
        if not pk:
            return (wk, [])
        pk["why"] += f"；账本到起点 {spec.get('switch_at', 3):g} 倍就转 SOXL"
        return (wk, [pk])

    def should_exit(self, run, sid, t):
        spec = next((x for x in run.specs if x["id"] == sid), None)
        if t.get("asset") == "stock" and t.get("sym") == self.TREND["sym"] and spec:   # the lotto's shares leave at 09:35
            return self.letf.exit_check(run, {**spec, **self.TREND})
        return None


class FadeLotto:
    """Owner, 10-21 (SPCX: up 8.6% at the open, still above it at noon, the 160 put due that day at $0.25 reached $3.15
    by 14:10): a cheap lottery on a big gap fading back. From `scan_from` (11:00) to `scan_until` (14:00), every cycle: names
    that opened `gap` (5%) or more from yesterday's close and are still on the gap's side of the open -- the pool, the
    day's most traded, today's earnings reactions and `watch` -- and for each the nearest expiry within a week, the
    strike just past the open toward the fade (a put at or under the open after a gap up, a call at or over it after a
    gap down), asking at most `max_ask`. Each new find is an alert in the trade feed (once a name a day, `alert` on one
    ledger only); the ledger books the first find of the day: half on a 3x take, half on a trail, out 15:30 if it
    expires that day else 15:50. Only about 55% of 5%+ gap ups trade back under the open that day: most go to zero."""

    def universe(self, run, spec) -> list[str]:
        names = list(pool(run)) + list(spec.get("watch") or [])
        names += cached(run, "most_actives", lambda: run.c.most_actives(100)) or []

        def reactions():
            files = sorted((ROOT / "research" / "earnings-forecast").glob("20??-??-??.json"))
            out = []
            for f in files[-3:]:
                try:
                    out += [r["symbol"] for r in json.loads(f.read_text()).get("reports", [])
                            if r.get("reaction_day") == run.today.isoformat()]
                except Exception:
                    continue
            return out
        names += cached(run, "earnings_reactions", reactions) or []
        return list(dict.fromkeys(x for x in names if x and x.isalpha()))

    def finds(self, run, spec) -> list[dict]:
        def scan():
            day, out = run.today.isoformat(), []
            found = []
            for x, v in snaps(run, self.universe(run, spec)).items():
                spot, o, _, prev = spot_open(v)
                if not spot or not o or not prev:
                    continue
                g = o / prev - 1
                if (g >= spec.get("gap", 0.05) and spot > o) or (g <= -spec.get("gap", 0.05) and spot < o):
                    found.append((abs(g), x, spot, o, g))
            for _, und, spot, o, g in sorted(found, reverse=True)[:spec.get("top", 10)]:
                kind = "P" if g > 0 else "C"
                lo, hi = (o * 0.9, o * 1.0001) if kind == "P" else (o * 0.9999, o * 1.1)
                cs = contracts(run, und, day, (run.today + timedelta(days=7)).isoformat(), lo, hi, "put" if kind == "P" else "call")
                if not cs:
                    continue
                side_ok = (lambda k: k <= o) if kind == "P" else (lambda k: k >= o)
                cs = [c for c in cs if side_ok(float(c["strike_price"]))]
                if not cs:
                    continue
                exp = min(c["expiration_date"] for c in cs)
                ks = sorted((c for c in cs if c["expiration_date"] == exp), key=lambda c: float(c["strike_price"]))
                c = ks[-1] if kind == "P" else ks[0]       # just past the open toward the fade
                ask = ask_of(run, c["symbol"])
                if not 0 < ask <= spec.get("max_ask", 0.5):
                    continue
                k = float(c["strike_price"])
                side = "看跌" if kind == "P" else "看涨"
                when = "今天到期" if exp == day else f"{exp[5:]} 到期"
                out.append({"sym": c["symbol"], "asset": "option", "und": und, "ask": ask, "take": 3, "half": True, "gap": round(g, 4),
                            "expiry": exp, "exit_day": day, "exit_at": "15:30" if exp == day else "15:50",
                            "why": f"彩票：{und} {'高' if g > 0 else '低'}开 {g:+.1%}（开盘 ${o:,.2f}），现在 ${spot:,.2f}、"
                                   f"还在开盘{'上' if g > 0 else '下'}方 {abs(spot / o - 1):.1%}；{side} {k:g}（{when}）卖价 ${ask:.2f}，"
                                   f"{'跌回' if g > 0 else '涨回'} ${k:g} {'以下' if g > 0 else '以上'}才开始值钱；"
                                   f"一半 3 倍止盈、一半翻倍后回撤 40% 卖，最晚 {'15:30' if exp == day else '15:50'}"})
            return out
        return cached(run, ("fadelotto", run.today.isoformat(), run.hhmm, spec.get("gap", 0.05), spec.get("max_ask", 0.5)), scan) or []

    def entries(self, run, spec, s):
        """`alert`: post each name once a day when it is at least `alert_gap` and asks at most `alert_max_ask`.
        `every` (owner, 10-21「先大胆点 取样本」): book every name the first time it qualifies each day (a sample, with a
        fixed `qty` set in the spec); else the first find of the day only, like a ledger that plays it."""
        day = run.today.isoformat()
        if not (spec.get("scan_from", "11:00") <= run.hhmm < spec.get("scan_until", "14:00")):
            return None
        picks = self.finds(run, spec)
        if spec.get("alert"):
            seen = s.setdefault("alerted", {})
            for k in [k for k in seen if k != day]:
                seen.pop(k)
            told = seen.setdefault(day, [])
            for pk in picks:
                if (pk["und"] not in told and abs(pk["gap"]) >= spec.get("alert_gap", spec.get("gap", 0.05))
                        and pk["ask"] <= spec.get("alert_max_ask", spec.get("max_ask", 0.5))):
                    told.append(pk["und"])
                    run.j.event("lotto_alert", dry_run=False, symbol=pk["sym"],
                                why=pk["why"].split("；一半")[0].removeprefix("彩票：") + "。只记录、不下单（模拟盘，不是投资建议）")
        if spec.get("every"):
            taken = s.setdefault("taken", {})
            for k in [k for k in taken if k != day]:
                taken.pop(k)
            have = taken.setdefault(day, [])
            new = [pk for pk in picks if pk["und"] not in have]
            if not new:
                return None
            have += [pk["und"] for pk in new]
            s["keep"] = spec.get("keep", 1000)
            return (f"{day} {run.hhmm}", new)
        if day in s["slots"] or not picks:
            return None              # nothing yet: look again next cycle
        return (day, picks[:1])


# Inverse funds for a peer's "down" leg: the -1x fund where one exists, else a leveraged one at a fraction of the notional.
INVERSE = {"SPY": "SH", "QQQ": "PSQ", "DIA": "DOG", "IWM": "RWM", "SMH": ["SOXS", 3], "SOXX": ["SOXS", 3], "XLE": "ERY"}


class Peer:
    """Owner, 10-22 (「C 的大盘方向好像都很准」, 「按你的来」): what claude-c's direction call is worth in dollars.
    Its morning file (exchange/<producer>/YYYY-MM-DD.morning.json, archived by the 09:15 exchange and pushed before the
    open) names a few ETFs, each with a side and a falsifier (the prior close). The first cycle after the open buys
    shares of each leg in its direction -- the ETF itself for up, its inverse fund for down (`inverse`; a 3x fund at a
    third of the notional) -- and sells them at 15:50, the last cycle. The fraction is split over the legs. Plan only:
    it records the call and never sends an order. The peer's file is reference material, not instructions."""

    def legs(self, run, spec) -> list[tuple[str, str, str]] | None:
        """(symbol, "up" / "down", falsifier) per leg; None when the day's file is not in yet."""
        root = Path(spec["exchange_dir"]) if spec.get("exchange_dir") else ROOT / "exchange" / spec.get("producer", "claude-c")
        try:
            d = json.loads((root / f"{run.today.isoformat()}.morning.json").read_text())
        except Exception:
            return None
        out = []
        for l in (d.get("direction_call") or {}).get("legs") or []:
            side = str(l.get("side") or "")
            up, down = ("涨" in side or side.upper().startswith("BULL")), ("跌" in side or side.upper().startswith("BEAR"))
            if up != down and l.get("symbol"):
                out.append((str(l["symbol"]).upper(), "up" if up else "down", str(l.get("falsifier") or "")))
        return out

    def entries(self, run, spec, s):
        day = run.today.isoformat()
        if day in s["slots"] or not (spec.get("entry", "09:31") <= run.hhmm < spec.get("cutoff", "10:30")):
            return None
        legs = self.legs(run, spec)
        if legs is None:
            return None   # not archived yet: look again next cycle, until the cutoff
        who = spec.get("producer", "claude-c")
        inverse = {**INVERSE, **(spec.get("inverse") or {})}
        picks = []
        for und, side, falsifier in legs:
            if side == "up":
                sym, lev = und, 1
            else:
                inv = inverse.get(und)
                if not inv:
                    run.j.event("sleeve_skip", dry_run=True, sleeve=spec["id"], symbol=und,
                                why=f"{spec['id']}：{who} 看跌 {und}，没有反向 ETF，这条腿只记录不买")
                    continue
                sym, lev = (inv, 1) if isinstance(inv, str) else (inv[0], int(inv[1]))
            spot, o, _, prev = spot_open(snaps(run, [sym]).get(sym) or {})
            if not spot:
                continue
            what = und if side == "up" else f"反向 ETF {sym}" + (f"（{lev} 倍，只用 1/{lev} 的钱）" if lev > 1 else "")
            picks.append({"sym": sym, "asset": "stock", "und": und, "ask": spot, "whole": False, "scale": 1 / lev,
                          "exit_day": day, "exit_at": spec.get("exit_at", "15:50"),   # 10-23: without exit_day the 15:50 sale never fired
                          "why": f"跟 {who} 的方向：{und} 看{'涨' if side == 'up' else '跌'}（它的证伪线：{falsifier}），"
                                 f"买{what} ${spot:,.2f}（开盘 ${o:,.2f}、昨收 ${prev:,.2f}），{spec.get('exit_at', '15:50')} 卖"})
        return (day, picks)


RULES = {"zdte": ZeroDTE(), "weekly": Weekly(), "net": Net(), "gap": Gap(), "flush": Flush(), "earnings": EarningsLotto(),
         "letf": LETF(), "relay": Relay(), "opengap": OpenGap(), "sniper": Sniper(), "oneshot": OneShot(),
         "overnight": Overnight(), "pead": Pead(), "vixspike": VixSpike(), "breakgap": BreakGap(),
         "hikerally": HikeRally(), "dipweek": DipWeek(), "fadelotto": FadeLotto(), "peer": Peer(), "pullback": Pullback(),
         "bottom": Streak(), "bounce": Bounce()}
