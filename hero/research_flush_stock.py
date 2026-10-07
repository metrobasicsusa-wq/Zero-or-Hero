"""Research: after a sharp drop from the open ("急跌"), does the stock itself come back? (shares, not options)

research_flush found calls bought after the drop lose about as much as on quiet days. That can mean the
stock does not rebound, or that the options already price it in (plus ~18% round-trip cost). Here the same
events (by 10:00 the stock has traded 2% / 3% below its open; buy at 10:00, or the minute after a close 1%
above the running low, by 11:00), on SIP minutes since 2024-02, measured on the shares:
  close:    entry -> 15:59 close, less 0.05% round trip;
  vs SPY:   the same minus SPY over the same minutes (was it the stock, or the whole market?);
  stop:     sell if it trades back down to the low seen so far, else at the close;
  stop+2%:  that stop, or take +2% (when both could happen in one minute, the stop is assumed);
  next day: held to the next day's close (overnight risk included);
plus the best / worst point after entry. Groups as in research_flush, with every chip / Mag 7 day kept
(no options to price, so no sampling), other stocks from the monthly point-in-time pools (sampled), and
controls: the same names on quiet mornings (less than 1% off the open by 10:00) and on all days, bought at
10:00. Record only. Run: python -m hero.research_flush_stock
"""

from __future__ import annotations

import json
import math
import random
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

from hero import research_0dte as z
from hero.research_flush import BY, DROPS, FEED, POOL, START, first_drop, flush
from hero.research_gap import FOCUS, features

COST = 0.0005  # round trip on liquid shares
TARGET = 0.02
OTHER_DAYS = 5000
METRICS = ("close", "excess", "stop", "stop_tp", "next_day")


def full_day(xs: list[dict]) -> dict[str, dict]:
    sm = z.et_minutes(xs)
    return {k: {"o": float(v["o"]), "h": float(v["h"]), "l": float(v["l"]), "c": float(v["c"])}
            for k, v in sm.items() if "09:30" <= k <= "15:59"}


def after(sm: dict[str, dict], t: str, spy: dict[str, float] | None, next_close: float | None) -> dict | None:
    """The shares bought at the close of minute t (or the first minute after it within 5)."""
    got = z.at_or_after(sm, t)
    if not got:
        return None
    k0, bar = got
    entry = bar["c"]
    keys = sorted(sm)
    low_so_far = min(sm[k]["l"] for k in keys if k <= k0)
    later = [k for k in keys if k > k0]
    if not later:
        return None
    close = sm[later[-1]]["c"]
    out = {"entry_at": k0, "close": round(close / entry - 1 - COST, 4),
           "best": round(max(sm[k]["h"] for k in later) / entry - 1, 4),
           "worst": round(min(sm[k]["l"] for k in later) / entry - 1, 4)}
    stop, stop_tp = None, None
    for k in later:
        b = sm[k]
        if b["l"] <= low_so_far:
            stop = stop if stop is not None else low_so_far / entry - 1
            stop_tp = stop_tp if stop_tp is not None else low_so_far / entry - 1
            break
        if stop_tp is None and b["h"] >= entry * (1 + TARGET):
            stop_tp = TARGET
    out["stop"] = round((stop if stop is not None else close / entry - 1) - COST, 4)
    out["stop_tp"] = round((stop_tp if stop_tp is not None else close / entry - 1) - COST, 4)
    if spy:
        a = z.at_or_before(spy, k0)
        b = z.at_or_before(spy, later[-1])
        if a and b:
            out["excess"] = round(out["close"] - (b / a - 1), 4)
    if next_close:
        out["next_day"] = round(next_close / entry - 1 - COST, 4)
    return out


def summary(xs: list[float]) -> dict:
    n = len(xs)
    m = statistics.mean(xs)
    sd = statistics.pstdev(xs) if n > 1 else 0.0
    return {"n": n, "mean": round(m, 4), "median": round(statistics.median(xs), 4),
            "win": round(sum(x > 0 for x in xs) / n, 3), "t": round(m / (sd / math.sqrt(n)), 2) if sd else 0.0}


def run(client) -> dict:
    from hero import research_universe as ru
    end = (date.today() - timedelta(days=1)).isoformat()
    stocks = ru.candidates(client)
    syms = sorted((set(stocks) | FOCUS | {"SPY"}) - {"GOOG"})
    bars: dict[str, list[dict]] = {}
    for i in range(0, len(syms), 200):
        bars.update(client.daily_bars(syms[i:i + 200], "2023-01-01", feed=FEED))
    dates, closes, dvol = ru.align(bars, "SPY")
    have = {s: k for s, k in stocks.items() if s in bars}
    firsts = {s: next((i for i, x in enumerate(closes[s]) if x is not None), None) for s in have}
    start = next(i for i, d in enumerate(dates) if d >= START)
    stop = max(i for i, d in enumerate(dates) if d <= end)
    pools = ru.monthly_pools(dates, closes, dvol, firsts, have, POOL, start)
    keys = sorted(pools)
    opens = {s: {b["t"][:10]: float(b["o"]) for b in bs} for s, bs in bars.items()}
    idx = {d: i for i, d in enumerate(dates)}
    months = sorted({d[:7] for d in dates[start: stop + 1]})

    def month_days(s: str, mo: str) -> dict[str, list[dict]]:
        a = f"{mo}-01"
        b = (date.fromisoformat(a) + timedelta(days=32)).replace(day=1).isoformat()
        try:
            raw = client.stock_bars([s], f"{a}T13:00:00Z", f"{b}T00:00:00Z", feed=FEED).get(s, [])
        except Exception:
            return {}
        out: dict[str, list[dict]] = {}
        for x in raw:
            out.setdefault(x["t"][:10], []).append(x)
        return out

    spy = {}
    for mo in months:
        for d, xs in month_days("SPY", mo).items():
            spy[d] = {k: v["c"] for k, v in full_day(xs).items()}

    rows, base = [], {"quiet": [], "all": []}

    def events(kind: str, s: str, d: str, sm: dict[str, dict]) -> None:
        t = idx.get(d)
        if t is None or not (START <= d <= end) or not closes.get(s) or not closes[s][t - 1]:
            return
        f = first_drop(sm)
        if f is None or not opens.get(s, {}).get(d):
            return
        nxt = closes[s][t + 1] if t + 1 < len(dates) else None
        if kind == "focus":
            r = after(sm, BY, spy.get(d), nxt)
            if r:
                base["all"].append(r)
                if f < 0.01:
                    base["quiet"].append(r)
        if f < min(DROPS):
            return
        info = {"gap": round(opens[s][d] / closes[s][t - 1] - 1, 4), **features(closes[s], opens[s], dates, t)}
        for thr in DROPS:
            fl = flush(sm, thr)
            if not fl:
                continue
            for rule, at in fl["entries"].items():
                r = after(sm, at, spy.get(d), nxt) if at else None
                if r:
                    rows.append({"kind": kind, "day": d, "symbol": s, "thr": thr, "rule": rule, "drop": fl["drop"],
                                 "low_at": fl["low_at"], **info, **r})

    for s in sorted(FOCUS & set(bars) - {"GOOG"}):
        for mo in months:
            for d, xs in month_days(s, mo).items():
                events("focus", s, d, full_day(xs))
    rng = random.Random(13)
    pairs = []
    for t in range(start, stop + 1):
        pool = pools[max(k for k in keys if k <= t)]
        pairs += [(dates[t], s) for s in pool if s not in FOCUS]
    pairs = rng.sample(pairs, min(len(pairs), OTHER_DAYS))
    for d, s in pairs:
        try:
            xs = client.stock_bars([s], f"{d}T13:00:00Z", f"{d}T21:00:00Z", feed=FEED).get(s, [])
        except Exception:
            continue
        events("other", s, d, full_day(xs))
    baselines = {k: {m: summary([r[m] for r in v if r.get(m) is not None]) for m in METRICS
                     if any(r.get(m) is not None for r in v)} for k, v in base.items() if v}
    return {"generated": date.today().isoformat(), "start": START, "end": end, "feed": FEED, "cost": COST,
            "other_days": len(pairs), "baselines": baselines, "rows": rows}


def cut(rows: list[dict], key: str, q: float) -> float:
    xs = sorted(r[key] for r in rows if r["kind"] == "focus" and r.get(key) is not None)
    return xs[int(q * (len(xs) - 1))] if xs else float("inf")


def table(rows: list[dict]) -> dict:
    hi_mom, hi_vol = cut(rows, "mom63", 0.75), cut(rows, "vol63", 0.75)
    amd = lambda r: (r.get("mom63") or -9) >= hi_mom and (r.get("vol63") or 0) >= hi_vol
    fo = lambda r: r["kind"] == "focus"
    groups = {
        "半导体 / Mag 7 急跌": fo,
        "　高开 1%+ 后急跌（AMD 10-06 这种）": lambda r: fo(r) and r["gap"] >= 0.01,
        "　平开后急跌": lambda r: fo(r) and abs(r["gap"]) < 0.01,
        "　低开 1%+ 后再急跌": lambda r: fo(r) and r["gap"] <= -0.01,
        "　像 AMD（强势 + 高波动）": lambda r: fo(r) and amd(r),
        "　像 AMD + 高开后急跌": lambda r: fo(r) and amd(r) and r["gap"] >= 0.01,
        "　2024": lambda r: fo(r) and r["day"] < "2025-01-01",
        "　2025": lambda r: fo(r) and "2025-01-01" <= r["day"] < "2026-01-01",
        "　2026 年以来": lambda r: fo(r) and r["day"] >= "2026-01-01",
        "其他股票急跌": lambda r: r["kind"] == "other",
    }
    out = {}
    for g, f in groups.items():
        for thr in DROPS:
            for rule in ("10:00", "confirm"):
                sub = [r for r in rows if f(r) and r["thr"] == thr and r["rule"] == rule]
                if not sub:
                    continue
                row = {"symbols": len({r["symbol"] for r in sub}),
                       "best": round(statistics.median(r["best"] for r in sub), 4),
                       "worst": round(statistics.median(r["worst"] for r in sub), 4)}
                for m in METRICS:
                    xs = [r[m] for r in sub if r.get(m) is not None]
                    if xs:
                        row[m] = summary(xs)
                out[f"{g}|{thr}|{rule}"] = row
    out["_cuts"] = {"mom63_top25": hi_mom, "vol63_top25": hi_vol}
    return out


def markdown(rep: dict, t: dict) -> str:
    rules = {"10:00": "10:00 买", "confirm": "反弹 1% 后买"}
    names = {"close": "拿到收盘", "excess": "减去 SPY", "stop": "跌回低点止损", "stop_tp": "止损 + 涨 2% 止盈", "next_day": "拿到第二天收盘"}

    def cell(r: dict, m: str) -> str:
        x = r.get(m)
        return f"{x['mean']:+.2%} / {x['median']:+.2%} / {x['win']:.0%} / t={x['t']:+.1f}" if x else "—"
    c = t.get("_cuts", {})
    out = [f"# 开盘后急跌，正股之后怎么走 {rep['generated']}", "",
           f"{rep['start']} 至 {rep['end']}，SIP 分钟线。急跌 = 10:00 前从开盘价跌 2%（或 3%）。买点：10:00，或某分钟收盘比当时最低点高 1% 后的下一分钟（11:00 前）。"
           f"买正股，来回成本 {rep['cost']:.2%}。每格：平均 / 中位数 / 胜率 / t 值（|t| ≥ 2 才算比较可信）。"
           f"「减去 SPY」= 同一段时间减掉大盘涨跌。其他股票：从每月股票池随机抽 {rep['other_days']} 个股票日。只研究，不改交易。", "",
           "| 组别 | 急跌 | 买点 | 笔数 | 股票数 | " + " | ".join(names[m] for m in METRICS) + " | 买入后最高 / 最低（中位数） |",
           "|---|---|---|---|---|" + "---|" * len(METRICS) + "---|"]
    for key, r in t.items():
        if key.startswith("_"):
            continue
        g, thr, rule = key.split("|")
        out.append(f"| {g} | {float(thr):.0%} | {rules[rule]} | {r['close']['n']} | {r['symbols']} | "
                   + " | ".join(cell(r, m) for m in METRICS) + f" | {r['best']:+.2%} / {r['worst']:+.2%} |")
    out += ["", "## 对照：半导体 / Mag 7，10:00 买", "", "| 日子 | 笔数 | " + " | ".join(names[m] for m in METRICS) + " |",
            "|---|---|" + "---|" * len(METRICS) + "|"]
    for k, label in (("quiet", "早盘平静（10:00 前离开盘价不到 1%）"), ("all", "全部日子")):
        b = rep["baselines"].get(k)
        if b:
            out.append(f"| {label} | {b['close']['n']} | " + " | ".join(cell(b, m) for m in METRICS) + " |")
    out += ["", f"「像 AMD」= 开盘前 63 天涨幅和年化波动都在半导体 / Mag 7 急跌事件的前 25%（涨幅 ≥ {c.get('mom63_top25', 0):+.0%}、"
            f"波动 ≥ {c.get('vol63_top25', 0):.0%}）。「半导体 / Mag 7」= " + "、".join(sorted(FOCUS - {"GOOG"})) + "。"]
    return "\n".join(out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca())
    t = table(rep["rows"])
    rep["table"] = t
    (root / "research" / f"{rep['generated']}-flush-stock.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    (root / "research" / f"{rep['generated']}-flush-stock.md").write_text(markdown(rep, t))
    print(markdown(rep, t))


if __name__ == "__main__":
    sys.exit(main())
