"""Research: the signal rules in shares instead of out-of-the-money options (owner, 10-23: 「那你现在做？」).

The option versions of our signal rules have medians of -46% to -78% a trade: whatever their arithmetic mean,
betting half a ledger on them has negative log growth, so the ledgers can only ever win by luck
(research/2026-10-23-verdict.md and the chat after it). Here the same signals buy the stock itself, on a monthly
liquidity-ranked pool of the 300 most traded operating companies (hero.research_universe: what was knowable at the
time; delisted names are still missing from Alpaca's list, so the pool leans to survivors), from 2022 on,
split-adjusted SIP daily bars.

Signals (long only, shares, 0.1% a round trip):
- dip: open 2%+ (5%+) under the prior close -> buy that day's close, sell the close 1 / 3 / 5 / 10 sessions on
  (the dipweek sleeve's shape).
- breakgap: open 2%+ above the prior close, 3x the stock's 20-session average move, above the 20-session high ->
  buy at the open (the live rule waits for 09:35), sell at that close, the next close, the fifth close.
- bigup: open 10%+ above the prior close and the day closes at or above its open (the earnings-drift shape, without
  earnings labels) -> buy the next open, sell the first / third / fifth close after it.
- spydip: SPY (QQQ) closes 1%+ down -> buy the next open, sell that close; the fund itself and its 2x (SSO / QLD).
- baseline: every pool name every day, bought at the close and sold five closes on: what "buy anything" paid.

Samples are counted by day (a day's signals equal-weighted): twenty names gapping down on one crash day are one
bet. hero.robust on the day series, plus log growth a day at 100% / 50% / 25% of the ledger compounded in sequence
(overlapping holds are ignored, so the multiple is an upper bound). Research only: it changes no rule."""
from __future__ import annotations

import json
import math
import statistics
import sys
from datetime import date
from pathlib import Path

from hero import robust
from hero.research_universe import LOOKBACK_DV, candidates, monthly_pools

ROOT = Path(__file__).resolve().parent.parent
START = "2022-01-01"
POOL = 300
COST = 0.001          # a round trip in shares
FUNDS = ["SPY", "QQQ", "SSO", "QLD"]
EXITS = {"dip2": ["hold1", "hold3", "hold5", "hold10"], "dip5": ["hold1", "hold3", "hold5", "hold10"],
         "breakgap": ["same_close", "next_close", "close5"], "bigup": ["close1", "close3", "close5"],
         "spydip_SPY": ["SPY", "SSO"], "spydip_QQQ": ["QQQ", "QLD"], "baseline": ["hold5"]}
TITLES = {"dip2": "低开 2%+ 当天收盘买正股（抄底给一周的形状）", "dip5": "低开 5%+ 当天收盘买正股",
          "breakgap": "突破大跳空（高开 2%+、3 倍平时波动、过 20 日高）开盘买正股", "bigup": "高开 10%+ 且收在开盘上，第二天开盘买正股（财报后漂移的形状）",
          "spydip_SPY": "SPY 收跌 1%+，第二天开盘买、收盘卖", "spydip_QQQ": "QQQ 收跌 1%+，第二天开盘买、收盘卖",
          "baseline": "对照：池子里随便买，拿 5 天"}
LABELS = {"hold1": "第 1 天收盘", "hold3": "第 3 天", "hold5": "第 5 天", "hold10": "第 10 天", "same_close": "当天收盘", "next_close": "第二天收盘",
          "close5": "第 5 天收盘", "close1": "第 1 天收盘", "close3": "第 3 天收盘", "SPY": "SPY 正股", "SSO": "SSO（2 倍）", "QQQ": "QQQ 正股",
          "QLD": "QLD（2 倍）"}
OPTION_VERSIONS = [("抄底给一周（期权，价外 3%，第 5 天）", "research/2026-10-16-dip-week.json", "按天等权 +9%，中位数 -46%，规则选的「低开最多」+5%"),
                   ("财报后漂移（期权，高开 10%+）", "research/2026-10-13-pead.json", "34 笔 +71%，去掉最好 3 笔 +14%，中位数 -14%"),
                   ("突破大跳空（期权，拿到第二天）", "research/2026-10-15-sniper-factors3.md", "57 次 +53%，去掉最好 3 笔 +23%，中位数 -10%"),
                   ("SPY 昨跌后买涨（当天到期看涨 0.3%）", "research/2026-10-08-zdte-streak.json", "67 天 +12%，去掉最好 3 笔 +3%，中位数 -78%")]


def align(bars: dict[str, list[dict]], calendar: str = "SPY"):
    """Per symbol: open, high, close, dollar volume on the calendar's dates (None where the day is missing)."""
    dates = sorted({b["t"][:10] for b in bars[calendar]})
    idx = {d: i for i, d in enumerate(dates)}
    o, h, c, dv = {}, {}, {}, {}
    for s, bs in bars.items():
        oo, hh, cc, vv = [None] * len(dates), [None] * len(dates), [None] * len(dates), [0.0] * len(dates)
        for b in bs:
            i = idx.get(b["t"][:10])
            if i is None:
                continue
            oo[i], hh[i], cc[i] = float(b["o"]), float(b["h"]), float(b["c"])
            vv[i] = float(b["c"]) * float(b.get("v") or 0)
        o[s], h[s], c[s], dv[s] = oo, hh, cc, vv
    return dates, o, h, c, dv


def ret(c: dict, s: str, t_out: int, entry: float) -> float | None:
    x = c[s][t_out] if t_out < len(c[s]) else None
    return x / entry - 1 - COST if x and entry else None


def signals(dates, o, h, c, pool_at, start: int) -> list[dict]:
    """One row per (signal, day, symbol) with the returns of every exit."""
    rows, n = [], len(dates)
    for t in range(start, n):
        for s in pool_at(t):
            if s not in c or t < 21:
                continue
            cp, op, cl = c[s][t - 1], o[s][t], c[s][t]
            if not cp or not op or not cl:
                continue
            gap = op / cp - 1
            if gap <= -0.02:
                r = {f"hold{k}": ret(c, s, t + k, cl) for k in (1, 3, 5, 10)}
                rows.append({"sig": "dip2", "day": dates[t], "sym": s, "gap": round(gap, 4), "res": r})
                if gap <= -0.05:
                    rows.append({"sig": "dip5", "day": dates[t], "sym": s, "gap": round(gap, 4), "res": dict(r)})
            if gap >= 0.02:
                xs = [c[s][k] for k in range(t - 21, t)]
                highs = [h[s][k] for k in range(t - 20, t)]
                if all(xs) and all(highs):
                    swing = statistics.mean(abs(xs[k] / xs[k - 1] - 1) for k in range(1, 21))
                    if swing and gap / swing >= 3 and op > max(highs):
                        r = {"same_close": ret(c, s, t, op), "next_close": ret(c, s, t + 1, op), "close5": ret(c, s, t + 5, op)}
                        rows.append({"sig": "breakgap", "day": dates[t], "sym": s, "gap": round(gap, 4), "swings": round(gap / swing, 1), "res": r})
            if gap >= 0.10 and cl >= op and t + 1 < n and o[s][t + 1]:
                e = o[s][t + 1]
                r = {"close1": ret(c, s, t + 1, e), "close3": ret(c, s, t + 3, e), "close5": ret(c, s, t + 5, e)}
                rows.append({"sig": "bigup", "day": dates[t + 1], "sym": s, "gap": round(gap, 4), "res": r})
        for und, lev in (("SPY", "SSO"), ("QQQ", "QLD")):
            if und not in c or t + 1 >= n:
                continue
            cp, cl = c[und][t - 1], c[und][t]
            if cp and cl and cl / cp - 1 <= -0.01:
                r = {x: ret(c, x, t + 1, o[x][t + 1]) if x in o and o[x][t + 1] else None for x in (und, lev)}
                rows.append({"sig": f"spydip_{und}", "day": dates[t + 1], "sym": und, "res": r})
    return rows


def baseline(dates, c, pool_at, start: int) -> list[tuple[str, float]]:
    out = []
    for t in range(start, len(dates) - 5):
        xs = [ret(c, s, t + 5, c[s][t]) for s in pool_at(t) if s in c and c[s][t]]
        xs = [x for x in xs if x is not None]
        if xs:
            out.append((dates[t], statistics.mean(xs)))
    return out


def day_series(rows: list[dict], sig: str, exit_: str) -> list[tuple[str, float]]:
    by: dict[str, list[float]] = {}
    for r in rows:
        if r["sig"] == sig and r["res"].get(exit_) is not None:
            by.setdefault(r["day"], []).append(r["res"][exit_])
    return [(d, statistics.mean(v)) for d, v in sorted(by.items())]


def growth(xs: list[tuple[str, float]]) -> dict:
    """Log growth a day at full / half / quarter size, the sequential multiple at full size and its worst drawdown."""
    v = [r for _, r in xs]
    out = {}
    for f in (1.0, 0.5, 0.25):
        g = statistics.mean(math.log(1 + f * r) for r in v) if all(1 + f * r > 0 for r in v) else None
        out[f"g{int(f * 100)}"] = g
    led, peak, dd = 1.0, 1.0, 0.0
    for r in v:
        led *= max(1 + r, 0.0)
        peak = max(peak, led)
        dd = max(dd, 1 - led / peak if peak else 0)
    out.update({"multiple": led, "drawdown": dd, "worst": min(v), "best": max(v)})
    return out


def evaluate(rows: list[dict], base: list[tuple[str, float]], end: str) -> dict:
    table: dict[str, dict] = {}
    for sig, exits in EXITS.items():
        for x in exits:
            xs = base if sig == "baseline" else day_series(rows, sig, x)
            if not xs:
                continue
            s = robust.summary(xs, end=end, extra_cost=COST)
            rows_n = len(base) if sig == "baseline" else sum(1 for r in rows if r["sig"] == sig and r["res"].get(x) is not None)
            table[f"{sig}|{x}"] = {**s, "rows": rows_n, **growth(xs)}
    return table


def pct(x) -> str:
    return "—" if x is None else f"{x:+.1%}"


def markdown(table: dict, meta: dict) -> str:
    out = [f"# 同一个信号换成正股 {meta['generated']}", "",
           f"{meta['start']} 至 {meta['end']}，每月按前 {LOOKBACK_DV} 天成交额重选的 {POOL} 只股票（只用当时已知的数据；候选 {meta['candidates']} 只今天仍上市的公司，"
           "退市的不在，所以偏向幸存者），拆股调整的 SIP 日线，买卖合计 0.1% 成本。**样本按天数**：同一天的信号等权算一笔，一个崩盘日 20 只一起反弹只算一次。"
           "期权版的对照数字来自各自的研究文件。只研究，不改交易。", "",
           "## 读法", "",
           "- 「平均」是按天的算术平均；「押满复利」把每天的信号收益按顺序全仓滚（忽略持仓重叠，是上限），看能不能复利——期权版任何规则押一半都是负的。",
           "- 「去掉最好 3 天」「各年份」「最近 3 个月」是 hero/robust.py 的检查；2022 是下跌年，专门看抄底在熊市里是什么样。", ""]
    for sig, exits in EXITS.items():
        rows = [(x, table.get(f"{sig}|{x}")) for x in exits if table.get(f"{sig}|{x}")]
        if not rows:
            continue
        out += [f"## {TITLES[sig]}", "",
                "| 卖在 | 天数（笔数） | 平均 | 去掉最好 3 天 | 中位数 | 赚钱比例 | 各年份 | 最近 3 个月 | 最差一天 | 押满复利 / 最大回撤 | 对数增长：押满 · 一半 · 四分之一 |",
                "|---|---|---|---|---|---|---|---|---|---|---|"]
        for x, s in rows:
            yrs = "、".join(f"{y} {pct(m)}（{n}）" for y, (m, n) in s["years"].items())
            rec = f"{pct(s['recent'][0])}（{s['recent'][1]}）" if s.get("recent") else "—"
            gs = " · ".join("—" if s.get(k) is None else f"{s[k]:+.4f}" for k in ("g100", "g50", "g25"))
            out.append(f"| {LABELS.get(x, x)} | {s['n']}（{s['rows']}） | {pct(s['mean'])} | {pct(s['trim'])} | {pct(s['median'])} | {s['win']:.0%} | {yrs} | {rec} | "
                       f"{pct(s['worst'])} | {s['multiple']:.2f} 倍 / {s['drawdown']:.0%} | {gs} |")
        out.append("")
    out += ["## 同一信号的期权版（之前的研究）", "", "| 规则 | 文件 | 结果 |", "|---|---|---|"]
    out += [f"| {a} | {b} | {c} |" for a, b, c in OPTION_VERSIONS]
    return "\n".join(out) + "\n"


def run(client) -> tuple[dict, dict, list[dict]]:
    stocks = candidates(client)
    syms = sorted(set(stocks) | set(FUNDS))
    bars: dict[str, list[dict]] = {}
    for i in range(0, len(syms), 200):
        bars.update(client.daily_bars(syms[i:i + 200], START, adjustment="split", feed="sip"))
    dates, o, h, c, dv = align(bars)
    for s in syms:
        for d_ in (o, h, c):
            d_.setdefault(s, [None] * len(dates))
        dv.setdefault(s, [0.0] * len(dates))
    have = {s: k for s, k in stocks.items() if s in bars}
    filled = {}
    for s, xs in c.items():   # the pool ranking forward-fills, as research_universe does
        last, ys = None, []
        for x in xs:
            last = x if x is not None else last
            ys.append(last)
        filled[s] = ys
    firsts = {s: next((i for i, x in enumerate(xs) if x is not None), None) for s, xs in c.items()}
    warm = LOOKBACK_DV + 1
    pools = monthly_pools(dates, filled, dv, firsts, have, POOL, warm)
    keys = sorted(pools)

    def pool_at(t):
        k = max(k for k in keys if k <= t)
        return pools[k]

    rows = signals(dates, o, h, c, pool_at, keys[0])
    base = baseline(dates, c, pool_at, keys[0])
    meta = {"generated": date.today().isoformat(), "start": dates[keys[0]], "end": dates[-1], "candidates": len(have), "pool": POOL}
    return meta, evaluate(rows, base, dates[-1]), rows


def main() -> int:
    from hero.alpaca import Alpaca
    meta, table, rows = run(Alpaca())
    md = markdown(table, meta)
    out = ROOT / "research" / f"{meta['generated']}-shares-signals"
    out.with_suffix(".md").write_text(md)
    keep = [r for r in rows if r["sig"] != "dip2"]   # the 2% dips are tens of thousands of rows: the day series is in the table
    out.with_suffix(".json").write_text(json.dumps({"meta": meta, "table": table, "rows": keep,
                                                    "dip2_days": day_series(rows, "dip2", "hold5")}, ensure_ascii=False))
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
