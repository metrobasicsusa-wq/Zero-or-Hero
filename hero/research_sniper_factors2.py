"""The sniper's judgement, part 2: the factors gap traders weigh that research_sniper_factors left out (owner, 10-15).

Same events as research_sniper_factors (research/*-open-catalyst.json: 25 active names since 2024-02, the open 2%+
from the prior close, the option bought at 09:35 the way the first five minutes went, nearest expiry, 0.6% out,
10% costs each way). Tagged with what a trader could see by 09:35:
  gap size against the stock's own swings: |gap| / its average absolute daily move over 20 sessions;
  pre-market volume: shares traded 04:00-09:29 ET (SIP minutes) / the 20-session average daily volume;
  the first five minutes: how far 09:30 -> 09:34 went (in daily swings) and its volume / average daily volume;
  dear options (proxy): 20-session swing / 120-session swing -- options price recent volatility;
  where the price is: with or against the 50-day average; the open beyond the 20-session high (call) / low (put);
  the kind of news: headlines between the prior close and 09:30 sorted by keywords (earnings, deal, FDA, rating,
            contract or product, legal, market lists only, none);
  the market: SPY's 20-session realized volatility, in thirds of the sample.
Record only. Run: python -m hero.research_sniper_factors2
"""

from __future__ import annotations

import re
import statistics
import sys
import time
from datetime import date

from hero.research_0dte import et_minutes
from hero.research_ideas import report, save
from hero.research_open_catalyst import utc
from hero.research_sniper_factors import EXITS, latest

KINDS = [("财报", r"earnings|results|quarter|\bq[1-4]\b|eps|revenue|guidance|outlook|beats|misses"),
         ("并购 / 交易", r"acqui|merger|buyout|takeover|to buy |deal to|stake"),
         ("FDA / 审批", r"\bfda\b|approval|approves|clearance|trial"),
         ("分析师评级", r"upgrade|downgrade|price target|initiates|reiterate|\bpt\b|overweight|underweight|outperform|neutral"),
         ("合同 / 产品 / 合作", r"contract|partnership|partners|order|launch|unveil|agreement|award|wins|selects"),
         ("诉讼 / 监管", r"lawsuit|probe|investigat|\bsec\b|antitrust|regulator|fine|recall|ban"),
         ("只有市场清单", r"stocks moving|movers|trending|why .* (is|are) |what's going on|top \d+|pre-market session|stock market today")]


def kind_of(heads: list[str]) -> str:
    if not heads:
        return "没有新闻"
    text = " ".join(h.lower() for h in heads)
    for name, pat in KINDS[:-1]:
        if re.search(pat, text):
            return name
    return "只有市场清单" if re.search(KINDS[-1][1], text) else "其他新闻"


def daily_stats(bars: list[dict]) -> dict[str, dict]:
    """day -> what was known before that day's open: swing20, swing120, adv20, ma50, hi20, lo20, prev close."""
    out = {}
    for i in range(121, len(bars)):
        prev = bars[i - 121:i]
        rets = [abs(float(prev[k]["c"]) / float(prev[k - 1]["c"]) - 1) for k in range(1, len(prev))]
        out[bars[i]["t"][:10]] = {"swing20": statistics.mean(rets[-20:]), "swing120": statistics.mean(rets),
                                  "adv20": statistics.mean(float(b["v"]) for b in prev[-20:]),
                                  "ma50": statistics.mean(float(b["c"]) for b in prev[-50:]),
                                  "hi20": max(float(b["h"]) for b in prev[-20:]), "lo20": min(float(b["l"]) for b in prev[-20:]),
                                  "prev_close": float(prev[-1]["c"]), "prev_day": prev[-1]["t"][:10]}
    return out


def tag(r: dict, st: dict, mins: dict, heads: list[str], spy_vol: float | None) -> dict | None:
    if not st:
        return None
    sign = 1 if r["kind"] == "C" else -1
    pre = sum(float(b["v"]) for k, b in mins.items() if "04:00" <= k < "09:30")
    first = [mins[k] for k in sorted(mins) if "09:30" <= k <= "09:34"]
    o = float(first[0]["o"]) if first else st["prev_close"] * (1 + r["gap"])
    f5 = sign * (float(first[-1]["c"]) / o - 1) / st["swing20"] if first else None
    v5 = sum(float(b["v"]) for b in first) / st["adv20"] if first else None
    res = dict(r["res"])
    if r.get("next") is not None:
        res["next"] = r["next"]
    # The minute bars are raw and the daily stats split-adjusted: compare the open on the daily scale (the gap is a
    # raw / raw ratio). Before 2026-10-16 this used the raw open, and every call on a stock before its split read
    # as a breakout (129 of 3395 rows wrong).
    o_adj = st["prev_close"] * (1 + r["gap"])
    return {**r, "res": res, "gap_z": abs(r["gap"]) / st["swing20"], "rvol_pre": pre / st["adv20"] if mins else None,
            "f5": f5, "v5": v5, "dear": st["swing20"] / st["swing120"],
            "trend": (st["prev_close"] > st["ma50"]) == (sign > 0),
            "breakout": (o_adj > st["hi20"]) if sign > 0 else (o_adj < st["lo20"]),
            "news_kind": kind_of(heads), "spy_vol": spy_vol}


def thirds(rows: list[dict], key: str) -> tuple[float, float]:
    xs = sorted(r[key] for r in rows if r.get(key) is not None)
    return xs[len(xs) // 3], xs[2 * len(xs) // 3]


def groups(rows: list[dict]) -> dict[str, list[dict]]:
    g = {"全部": rows}
    g.update({"跳空 < 1.5 倍平时波动": [r for r in rows if r["gap_z"] < 1.5],
              "跳空 1.5–3 倍平时波动": [r for r in rows if 1.5 <= r["gap_z"] < 3],
              "跳空 3 倍以上平时波动": [r for r in rows if r["gap_z"] >= 3]})
    for key, label, fmt in (("rvol_pre", "盘前成交量 / 日均", "{:.0%}"), ("f5", "前 5 分钟走了几倍平时波动", "{:.2f}"),
                            ("v5", "前 5 分钟成交量 / 日均", "{:.0%}"), ("spy_vol", "SPY 20 日波动", "{:.2%}")):
        lo, hi = thirds(rows, key)
        has = [r for r in rows if r.get(key) is not None]
        g[f"{label}：低（< {fmt.format(lo)}）"] = [r for r in has if r[key] < lo]
        g[f"{label}：中"] = [r for r in has if lo <= r[key] < hi]
        g[f"{label}：高（>= {fmt.format(hi)}）"] = [r for r in has if r[key] >= hi]
    g.update({"期权可能便宜（近期波动 < 0.9 倍长期）": [r for r in rows if r["dear"] < 0.9],
              "期权一般（0.9–1.2 倍）": [r for r in rows if 0.9 <= r["dear"] < 1.2],
              "期权可能偏贵（近期波动 >= 1.2 倍长期）": [r for r in rows if r["dear"] >= 1.2],
              "顺 50 日均线趋势": [r for r in rows if r["trend"]],
              "逆 50 日均线趋势": [r for r in rows if not r["trend"]],
              "开盘突破 20 日高 / 低点": [r for r in rows if r["breakout"]],
              "开盘没突破": [r for r in rows if not r["breakout"]]})
    for k in sorted({r["news_kind"] for r in rows}):
        g[f"新闻：{k}"] = [r for r in rows if r["news_kind"] == k]
    return g


def main() -> None:
    from hero.alpaca import Alpaca
    c = Alpaca()
    oc = latest("open-catalyst")
    rows = oc["rows"]
    syms = sorted({r["und"] for r in rows})
    daily = c.daily_bars(syms + ["SPY"], "2023-06-01", adjustment="all", feed="sip")
    stats = {s: daily_stats(daily.get(s, [])) for s in syms}
    spy = daily.get("SPY", [])
    spy_vol = {}
    for i in range(21, len(spy)):
        rets = [float(spy[k]["c"]) / float(spy[k - 1]["c"]) - 1 for k in range(i - 20, i)]
        spy_vol[spy[i]["t"][:10]] = statistics.pstdev(rets)
    by_day: dict[str, list[dict]] = {}
    for r in rows:
        by_day.setdefault(r["day"], []).append(r)
    out = []
    for day in sorted(by_day):
        evs = by_day[day]
        names = sorted({r["und"] for r in evs})
        try:
            mins = {s: et_minutes(b) for s, b in c.stock_bars(names, f"{day}T08:00:00Z", f"{day}T15:00:00Z", feed="sip").items()}
        except Exception:
            mins = {}
        heads: dict[str, list[str]] = {}
        prev = next((stats[n][day]["prev_day"] for n in names if day in stats.get(n, {})), None)
        if prev:
            try:
                for item in c.news_range(names, utc(prev, "16:00"), utc(day, "09:30")):
                    for s in item.get("symbols") or []:
                        heads.setdefault(s, []).append(item.get("headline", ""))
            except Exception:
                pass
        for r in evs:
            t = tag(r, stats.get(r["und"], {}).get(day), mins.get(r["und"], {}), heads.get(r["und"], []), spy_vol.get(day))
            if t:
                out.append(t)
        time.sleep(0.05)
    table, md = report(f"开盘狙击的判断因素回测（二）{date.today().isoformat()}",
                       f"{oc['start']} 至 {oc['end']}，和上一份同样的 {len(out)} 次开盘跳空 2% 以上（25 只活跃股；09:35 顺着前 5 分钟买最近到期、"
                       "离现价 0.6% 的期权，按分钟收盘价卖，买入多付 10%、卖出少拿 10%）。这次按交易员常看的因素分组：跳空相对平时波动、"
                       "盘前成交量、前 5 分钟的力度和成交量、期权贵不贵（用近期 / 长期波动代替）、股价位置、新闻类型（标题关键词）、大盘波动。"
                       "「低 / 中 / 高」是按样本三等分。",
                       groups(out), EXITS)
    save("sniper-factors2", {"generated": date.today().isoformat(), "rows": len(out), "table": table}, md)


if __name__ == "__main__":
    sys.exit(main())
