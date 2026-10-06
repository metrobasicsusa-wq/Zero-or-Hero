"""Research: how rare is a 20x option, and where do they come from?

Online "hero" stories (GameStop, weekly Apple / Tesla calls) share three things: a single stock
jumping far more than expected, cheap out-of-the-money calls, and holding instead of taking a
small profit. This counts, for the current dynamic pool (today's list, so it leans towards
today's winners), every week since 2024-02:
  entry at the first trading day's close of the week; one call per stock for
    "weekly":  the first expiry 2-8 days out, and
    "monthly": the expiry nearest 4 weeks out (21-35 days);
  strikes 10% / 20% / 30% / 50% above the price.
For each: the cost (10% over the close, at least a cent), the best value it ever reached before
expiry (the daily high, 10% under it: did it ever reach 5x / 10x / 20x / 50x?), its value at
expiry, and the return of "a resting 20x limit, else held to expiry". Then what the 20x winners
had in common: which stocks, whether an earnings report fell inside the holding window, and the
stock's momentum going in. Record only. Run: python -m hero.research_tails
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from hero.research_lottery import entry_cost, exit_value

START = "2024-02-01"
POOL = 100
OTM = (0.10, 0.20, 0.30, 0.50)
MULTS = (5, 10, 20, 50)
KINDS = {"weekly": (2, 8), "monthly": (21, 35)}


def week_entries(days: list[str]) -> list[str]:
    """First trading day of each ISO week."""
    seen, out = set(), []
    for d in days:
        wk = date.fromisoformat(d).isocalendar()[:2]
        if wk not in seen:
            seen.add(wk)
            out.append(d)
    return out


def pick_expiry(expiries: list[str], entry: str, kind: str) -> str | None:
    lo, hi = KINDS[kind]
    e = date.fromisoformat(entry)
    ok = [x for x in expiries if lo <= (date.fromisoformat(x) - e).days <= hi]
    if not ok:
        return None
    return ok[0] if kind == "weekly" else min(ok, key=lambda x: abs((date.fromisoformat(x) - e).days - 28))


def outcome(bars: dict[str, dict], entry: str, expiry: str, strike: float, settle: float | None) -> dict | None:
    """Cost, best multiple reached after the entry day, value at expiry; None without an entry trade."""
    if entry not in bars:
        return None
    cost = entry_cost(float(bars[entry]["c"]))
    later = [b for d, b in bars.items() if entry < d <= expiry]
    peak = max((exit_value(float(b["h"])) for b in later), default=0.0)
    exp_val = max(settle - strike, 0.0) if settle is not None else 0.0
    best = peak / cost
    return {"cost": round(cost, 3), "best": round(best, 2), "ret_expiry": round(exp_val / cost - 1, 3),
            "ret_take20": 19.0 if best >= 20 else round(exp_val / cost - 1, 3)}


def months(start: str, end: str) -> list[tuple[str, str]]:
    out, d = [], date.fromisoformat(start).replace(day=1)
    while d <= date.fromisoformat(end):
        nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        out.append((d.isoformat(), (nxt - timedelta(days=1)).isoformat()))
        d = nxt
    return out


def run(client, root: Path) -> dict:
    from hero import universe
    pool = universe.load(root / "data" / "universe.json")["stocks"][:POOL]
    end = (date.today() - timedelta(days=1)).isoformat()
    first = (date.fromisoformat(START) - timedelta(days=100)).isoformat()
    raw: dict[str, dict[str, float]] = {}
    for i in range(0, len(pool), 50):
        for s, bs in client.daily_bars(pool[i:i + 50], first, adjustment="raw").items():
            raw[s] = {b["t"][:10]: float(b["c"]) for b in bs}
    earn: set[tuple[str, str]] = set()
    ef = sorted((root / "research").glob("*-earnings-reactions.json"))
    if ef:
        earn = {(e["symbol"], e["reaction_day"]) for e in json.loads(ef[-1].read_text())["events"]}
    rows = []
    for sym in pool:
        closes = raw.get(sym)
        if not closes:
            continue
        days = sorted(closes)
        entries = [d for d in week_entries(days) if d >= START and d <= end]
        for m0, m1 in months(START, end):
            month_entries = [d for d in entries if m0 <= d <= m1]
            if not month_entries:
                continue
            spots = [closes[d] for d in month_entries]
            cs = []
            for status in ("inactive", "active"):
                try:
                    cs += client.option_contracts(sym, status=status, type="call", expiration_date_gte=m0,
                                                  expiration_date_lte=(date.fromisoformat(m1) + timedelta(days=40)).isoformat(),
                                                  strike_price_gte=f"{min(spots) * 1.09:.2f}",
                                                  strike_price_lte=f"{max(spots) * 1.6:.2f}")
                except Exception:
                    pass
            if not cs:
                continue
            by_exp: dict[str, dict[float, str]] = {}
            for c in cs:
                by_exp.setdefault(c["expiration_date"], {})[float(c["strike_price"])] = c["symbol"]
            expiries = sorted(by_exp)
            plan = []
            for d in month_entries:
                spot = closes[d]
                i = days.index(d)
                mom = round(spot / closes[days[i - 63]] - 1, 3) if i >= 63 else None
                for kind in KINDS:
                    exp = pick_expiry(expiries, d, kind)
                    if not exp:
                        continue
                    for m in OTM:
                        ks = [k for k in sorted(by_exp[exp]) if k >= spot * (1 + m)]
                        if ks:
                            plan.append((d, kind, exp, m, ks[0], by_exp[exp][ks[0]], spot, mom))
            if not plan:
                continue
            syms = sorted({p[5] for p in plan})
            last_exp = max(p[2] for p in plan)
            bars: dict[str, list] = {}
            for j in range(0, len(syms), 100):
                try:
                    bars.update(client.option_bars(syms[j:j + 100], m0, last_exp))
                except Exception:
                    pass
            time.sleep(0.1)
            ohlc = {s: {b["t"][:10]: b for b in bs} for s, bs in bars.items()}
            for d, kind, exp, m, k, osym, spot, mom in plan:
                settle = closes.get(exp)
                if settle is None:
                    past = [x for x in days if x <= exp]
                    settle = closes[past[-1]] if past and exp <= end else None
                if settle is None:
                    continue  # not expired yet
                o = outcome(ohlc.get(osym, {}), d, exp, k, settle)
                if not o:
                    continue
                window = [x for x in days if d < x <= exp]
                peak_stock = max((closes[x] for x in window), default=spot) / spot - 1
                rows.append({"symbol": sym, "entry": d, "kind": kind, "expiry": exp, "otm": m, "strike": k,
                             "spot": spot, "mom63": mom, "stock_peak": round(peak_stock, 3),
                             "earnings": any((sym, x) in earn for x in window), **o})
    return {"generated": date.today().isoformat(), "start": START, "pool": len(pool), "rows": rows}


def summarize(rows: list[dict]) -> dict:
    groups = {}
    for kind in KINDS:
        for m in OTM:
            sub = [r for r in rows if r["kind"] == kind and r["otm"] == m]
            if not sub:
                continue
            n = len(sub)
            hits = [r for r in sub if r["best"] >= 20]
            groups[f"{kind}|{m}"] = {
                "n": n, "cost_median": round(statistics.median(r["cost"] for r in sub), 3),
                **{f"x{x}": round(sum(r["best"] >= x for r in sub) / n, 4) for x in MULTS},
                "mean_expiry": round(statistics.mean(r["ret_expiry"] for r in sub), 3),
                "mean_take20": round(statistics.mean(r["ret_take20"] for r in sub), 3),
                "hits20": len(hits),
                "earn_share_hits": round(sum(r["earnings"] for r in hits) / len(hits), 3) if hits else None,
                "earn_share_all": round(sum(r["earnings"] for r in sub) / n, 3)}
    hits = [r for r in rows if r["best"] >= 20]
    by_sym: dict[str, int] = {}
    for r in hits:
        by_sym[r["symbol"]] = by_sym.get(r["symbol"], 0) + 1
    moms = [r["mom63"] for r in rows if r["mom63"] is not None]
    q = statistics.quantiles(moms, n=4) if len(moms) > 4 else [0, 0, 0]

    def quart(x):
        return 1 + sum(x > c for c in q)
    mq_all = {i: 0 for i in range(1, 5)}
    mq_hit = {i: 0 for i in range(1, 5)}
    for r in rows:
        if r["mom63"] is not None:
            mq_all[quart(r["mom63"])] += 1
            if r["best"] >= 20:
                mq_hit[quart(r["mom63"])] += 1
    by_month: dict[str, int] = {}
    for r in hits:
        by_month[r["entry"][:7]] = by_month.get(r["entry"][:7], 0) + 1
    return {"groups": groups, "hits": len(hits), "by_symbol": sorted(by_sym.items(), key=lambda x: -x[1])[:15],
            "momentum_quartiles": {str(i): {"trades": mq_all[i], "hits20": mq_hit[i],
                                            "rate": round(mq_hit[i] / mq_all[i], 4) if mq_all[i] else None} for i in mq_all},
            "momentum_cuts": [round(c, 3) for c in q],
            "by_month": sorted(by_month.items()),
            "top": sorted(hits, key=lambda r: -r["best"])[:25]}


def markdown(rep: dict, s: dict) -> str:
    kind_name = {"weekly": "本周到期", "monthly": "约 4 周后到期"}
    out = [f"# 20 倍期权有多稀有、从哪来 {rep['generated']}", "",
           f"本月动态池前 {rep['pool']} 只股票（今天的名单，偏向今天的赢家），自 {rep['start']} 每周第一个交易日收盘，"
           "每只股票买一张价外 10% / 20% / 30% / 50% 的看涨期权（本周到期，或约 4 周后到期）；买入多付 10%。"
           "「最高到过」= 到期前任何一天的最高价（少拿 10%）相对成本的倍数；「20 倍挂单」= 到 20 倍就卖，否则拿到期。只研究，不改交易。", "",
           "## 多少张能到 5 / 10 / 20 / 50 倍", "",
           "| 到期 | 价外 | 张数 | 成本中位数 | ≥5 倍 | ≥10 倍 | ≥20 倍 | ≥50 倍 | 拿到期平均每 $1 | 20 倍挂单平均每 $1 | 20 倍单里有财报的比例 | 全部里有财报的比例 |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for key, g in s["groups"].items():
        kind, m = key.split("|")
        eh = f"{g['earn_share_hits']:.0%}" if g["earn_share_hits"] is not None else "—"
        out.append(f"| {kind_name[kind]} | {float(m):.0%} | {g['n']:,} | ${g['cost_median']:.2f} | {g['x5']:.1%} | {g['x10']:.1%} | "
                   f"{g['x20']:.2%} | {g['x50']:.2%} | {g['mean_expiry']:+.0%} | {g['mean_take20']:+.0%} | {eh} | {g['earn_share_all']:.0%} |")
    out += ["", f"## 20 倍以上一共 {s['hits']} 张：集中在哪些股票", "",
            "| 股票 | 20 倍以上的张数 |", "|---|---|"] + [f"| {k} | {v} |" for k, v in s["by_symbol"]]
    c = s["momentum_cuts"]
    out += ["", f"## 买入前 63 天涨幅分四档（分界 {c[0]:+.0%} / {c[1]:+.0%} / {c[2]:+.0%}）", "",
            "| 档 | 张数 | 20 倍以上 | 比例 |", "|---|---|---|---|"]
    for i, v in s["momentum_quartiles"].items():
        rate = f"{v['rate']:.2%}" if v["rate"] is not None else "—"
        out.append(f"| {'最弱' if i == '1' else '最强' if i == '4' else '第 ' + i} | {v['trades']:,} | {v['hits20']} | {rate} |")
    out += ["", "## 按买入月份", "", "| 月份 | 20 倍以上的张数 |", "|---|---|"] + [f"| {k} | {v} |" for k, v in s["by_month"]]
    out += ["", "## 最大的 25 张", "", "| 股票 | 买入 | 到期 | 价外 | 成本 | 最高倍数 | 期间股价最多涨 | 期间有财报 |", "|---|---|---|---|---|---|---|---|"]
    for r in s["top"]:
        out.append(f"| {r['symbol']} | {r['entry']} | {r['expiry']} | {r['otm']:.0%} | ${r['cost']:.2f} | {r['best']:.0f} 倍 | "
                   f"{r['stock_peak']:+.0%} | {'是' if r['earnings'] else '否'} |")
    return "\n".join(out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca(), root)
    s = summarize(rep["rows"])
    rep["summary"] = s
    rep["trades"] = len(rep["rows"])
    rep["rows"] = [r for r in rep["rows"] if r["best"] >= 10]  # the full set is too big to keep in the repo
    (root / "research" / f"{rep['generated']}-tails.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    (root / "research" / f"{rep['generated']}-tails.md").write_text(markdown(rep, s))
    print(markdown(rep, s))


if __name__ == "__main__":
    sys.exit(main())
