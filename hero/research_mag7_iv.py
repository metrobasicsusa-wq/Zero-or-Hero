"""Is low implied volatility on a Magnificent 7 stock an opportunity to buy its options? (owner, 10-29: 「但如果是
mag7 iv 低呢」, after research/2026-10-29-low-iv.md found that low VIX is not.)

Single-stock volatility indexes are gone from Yahoo, so this prices the options themselves (Alpaca option bars, from
2024-02). Every `STEP`th session for AAPL, MSFT, GOOGL, AMZN, META, NVDA and TSLA: at that close buy the at-the-money
call and put (the strike nearest the close, the monthly expiry 25 to 50 days out, the nearest one if there is no monthly),
then sell at the close `HOLD` sessions later (or the last bar before expiry). Buying pays 10% more and selling gets
10% less (research_ideas). The straddle's implied move (call + put) / spot, annualized, is the stock's implied
volatility; it is ranked against the same stock's samples over the previous `RANK_N` samples (about a year), and the
whole-period terciles per stock are reported too. Windows with an earnings reaction inside are kept apart, since a
report lifts implied volatility beforehand and moves the stock. Record only. Run: python -m hero.research_mag7_iv
"""

from __future__ import annotations

import json
import math
import statistics
import sys
import time
from datetime import date, timedelta

from hero import robust
from hero.research_ideas import ROOT, cost_of, out_of

NAMES = ("AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA")
START = "2024-02-01"
STEP = 5
HOLD = 21
RANK_N = 50
EVENTS = ROOT / "research" / "2026-10-02-earnings-reactions.json"


def third_friday(d: date) -> bool:
    return d.weekday() == 4 and 15 <= d.day <= 21


def pick_pair(contracts: list[dict], spot: float) -> tuple[str, str, str, float] | None:
    """(call, put, expiry, strike): the monthly expiry when listed, else the nearest; the strike nearest spot that
    has both a call and a put."""
    exps = sorted({c["expiration_date"] for c in contracts})
    if not exps:
        return None
    exp = next((x for x in exps if third_friday(date.fromisoformat(x))), exps[0])
    calls = {float(c["strike_price"]): c["symbol"] for c in contracts if c["expiration_date"] == exp and c.get("type") == "call"}
    puts = {float(c["strike_price"]): c["symbol"] for c in contracts if c["expiration_date"] == exp and c.get("type") == "put"}
    both = sorted(set(calls) & set(puts), key=lambda k: abs(k - spot))
    if not both:
        return None
    k = both[0]
    return calls[k], puts[k], exp, k


def trade(c_bars: list[dict], p_bars: list[dict], day: str, hold: int = HOLD) -> dict | None:
    """Both legs bought at `day`'s close, sold at the close `hold` bars later (or the last common bar)."""
    cb = {b["t"][:10]: float(b["c"]) for b in c_bars}
    pb = {b["t"][:10]: float(b["c"]) for b in p_bars}
    if day not in cb or day not in pb:
        return None
    later = sorted(d for d in cb if d > day and d in pb)
    if not later:
        return None
    out = later[min(hold, len(later)) - 1]
    c0, p0, c1, p1 = cost_of(cb[day]), cost_of(pb[day]), out_of(cb[out]), out_of(pb[out])
    return {"out": out, "straddle": (c1 + p1) / (c0 + p0) - 1, "call": c1 / c0 - 1, "put": p1 / p0 - 1,
            "mid": cb[day] + pb[day]}


def ranks(rows: list[dict], n: int = RANK_N) -> None:
    """Each sample's implied volatility against the same stock's previous `n` samples (None until there are 20)."""
    by: dict[str, list[dict]] = {}
    for r in sorted(rows, key=lambda r: r["day"]):
        by.setdefault(r["und"], []).append(r)
    for rs in by.values():
        for i, r in enumerate(rs):
            past = [x["iv"] for x in rs[max(0, i - n):i]]
            r["rank"] = sum(p < r["iv"] for p in past) / len(past) if len(past) >= 20 else None
        ivs = sorted(x["iv"] for x in rs)
        t1, t2 = ivs[len(ivs) // 3], ivs[2 * len(ivs) // 3]
        for r in rs:
            r["tercile"] = "低" if r["iv"] < t1 else "中" if r["iv"] < t2 else "高"


def run(client) -> dict:
    end = (date.today() - timedelta(days=35)).isoformat()
    reacts: dict[str, list[str]] = {}
    for e in json.loads(EVENTS.read_text())["events"]:
        reacts.setdefault(e["symbol"], []).append(e["reaction_day"])
    bars = client.daily_bars(list(NAMES), START, adjustment="raw")
    rows = []
    for und in NAMES:
        bs = [b for b in bars.get(und, []) if b["t"][:10] <= end]
        closes = {b["t"][:10]: float(b["c"]) for b in bars.get(und, [])}
        days = sorted(closes)
        for i in range(0, len(bs), STEP):
            day, spot = bs[i]["t"][:10], float(bs[i]["c"])
            d0 = date.fromisoformat(day)
            try:
                cs = client.option_contracts(und, status="inactive", expiration_date_gte=(d0 + timedelta(days=25)).isoformat(),
                                             expiration_date_lte=(d0 + timedelta(days=50)).isoformat(),
                                             strike_price_gte=f"{spot * 0.95:.2f}", strike_price_lte=f"{spot * 1.05:.2f}")
                if not cs:
                    cs = client.option_contracts(und, status="active", expiration_date_gte=(d0 + timedelta(days=25)).isoformat(),
                                                 expiration_date_lte=(d0 + timedelta(days=50)).isoformat(),
                                                 strike_price_gte=f"{spot * 0.95:.2f}", strike_price_lte=f"{spot * 1.05:.2f}")
            except Exception:
                cs = []
            got = pick_pair(cs, spot)
            if not got:
                continue
            call, put, exp, k = got
            try:
                ob = client.option_bars([call, put], day, f"{exp}T23:00:00Z", timeframe="1Day")
            except Exception:
                continue
            t = trade(ob.get(call, []), ob.get(put, []), day)
            time.sleep(0.03)
            if not t:
                continue
            years = (date.fromisoformat(exp) - d0).days / 365
            iv = t["mid"] / spot / (0.8 * math.sqrt(years)) if years > 0 else None
            j = days.index(day)
            later = days[j + 1:j + 1 + HOLD]
            rv = (statistics.pstdev([math.log(closes[later[m]] / closes[later[m - 1] if m else day]) for m in range(len(later))])
                  * math.sqrt(252)) if len(later) >= 5 else None
            rows.append({"und": und, "day": day, "spot": spot, "strike": k, "expiry": exp, "iv": iv, "rv": rv, **t,
                         "earnings": any(day < x <= t["out"] for x in reacts.get(und, []))})
    ranks(rows)
    return {"generated": date.today().isoformat(), "start": START, "end": end, "rows": rows}


def table(rows: list[dict], end: str) -> tuple[dict, str]:
    groups = {"全部": rows, "窗口里没有财报": [r for r in rows if not r["earnings"]], "窗口里有财报": [r for r in rows if r["earnings"]]}
    for scope, base in (("没有财报", [r for r in rows if not r["earnings"]]), ("全部", rows)):
        for lab in ("低", "中", "高"):
            groups[f"{scope} · IV 在自己两年半里的{lab}三分之一"] = [r for r in base if r["tercile"] == lab]
        groups[f"{scope} · IV 在自己过去一年最低的 20%"] = [r for r in base if r["rank"] is not None and r["rank"] < 0.2]
        groups[f"{scope} · IV 在自己过去一年最高的 20%"] = [r for r in base if r["rank"] is not None and r["rank"] >= 0.8]
    for und in NAMES:
        groups[f"{und} · 没有财报 · IV 低三分之一"] = [r for r in rows if r["und"] == und and not r["earnings"] and r["tercile"] == "低"]
    md = ["| 情况 | 笔数 | 真实波动 ÷ 隐含（中位数） | 两边买：平均 | 中位数 | 赚的比例 | 买看涨：平均 | 中位数 | 买看跌：平均 |",
          "|---|---|---|---|---|---|---|---|---|"]
    out = {}
    for name, g in groups.items():
        if len(g) < 5:
            continue
        ratio = [r["rv"] / r["iv"] for r in g if r["rv"] and r["iv"]]
        row = {"n": len(g), "ratio": statistics.median(ratio) if ratio else None,
               "straddle": statistics.mean(r["straddle"] for r in g), "straddle_med": statistics.median(r["straddle"] for r in g),
               "win": sum(r["straddle"] > 0 for r in g) / len(g), "call": statistics.mean(r["call"] for r in g),
               "call_med": statistics.median(r["call"] for r in g), "put": statistics.mean(r["put"] for r in g)}
        out[name] = row
        md.append(f"| {name} | {row['n']} | {row['ratio']:.2f} | {row['straddle']:+.0%} | {row['straddle_med']:+.0%} | {row['win']:.0%} | "
                  f"{row['call']:+.0%} | {row['call_med']:+.0%} | {row['put']:+.0%} |" if row["ratio"] is not None else
                  f"| {name} | {row['n']} | — | {row['straddle']:+.0%} | {row['straddle_med']:+.0%} | {row['win']:.0%} | "
                  f"{row['call']:+.0%} | {row['call_med']:+.0%} | {row['put']:+.0%} |")
    rob = {}
    base = [r for r in rows if not r["earnings"]]
    for key, nm in (("straddle", "两边买"), ("call", "买看涨")):
        for lab in ("低", "高"):
            rob[f"{nm} · 没有财报 · IV {lab}三分之一"] = robust.summary([(r["day"], r[key]) for r in base if r["tercile"] == lab], end)
    return {"groups": out, "robust": rob}, "\n".join(md) + "\n\n" + robust.table(rob, "稳健性检查（没有财报的窗口）") + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    from hero.research_ideas import save
    rep = run(Alpaca())
    tab, body = table(rep["rows"], rep["end"])
    rep["table"] = tab
    head = [f"# 七巨头 IV 低的时候买期权划算吗？ {rep['generated']}", "",
            f"{rep['start']} 到 {rep['end']}，AAPL、MSFT、GOOGL、AMZN、META、NVDA、TSLA，每 {STEP} 个交易日取一个样本：收盘买平值看涨和看跌"
            f"（25–50 天到期，优先月度到期），{HOLD} 个交易日后收盘卖；买入多付 10%、卖出少拿 10%。IV = 两边价格之和 ÷ 股价，按年化折算；"
            "「低 / 中 / 高三分之一」是在同一只股票这两年半的样本里比，「过去一年」是和它前 50 个样本比。窗口里有财报的单独算。只研究，不改交易。", ""]
    save("mag7-iv", rep, "\n".join(head) + "\n" + body)


if __name__ == "__main__":
    sys.exit(main())
