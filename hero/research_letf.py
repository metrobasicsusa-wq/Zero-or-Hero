"""Research: leveraged ETFs held only above their moving average (sleeve 14 of the $500 / $1,000 plan).

TQQQ and SOXL (3x Nasdaq-100 / 3x semiconductors) and, for reference, QQQ: at each day's close, hold the
fund for the next day if it closed above its 20-day (or 50-day) average, else stay in cash; trades at the
close, 0.05% each way. Compared with simply holding. From each month's start, a fresh $500 / $1,000:
how often it reached $10,000 within 1 / 2 / 3 years, how often it fell below 10% of its start first,
and how long the winners took; plus the whole-period growth, yearly returns and worst drawdown.
Daily bars (split-adjusted) since 2015. Record only. Run: python -m hero.research_letf
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

FUNDS = ("TQQQ", "SOXL", "QQQ")
AVERAGES = (20, 50)
COST = 0.0005
TARGET = 10_000.0
START = "2015-01-01"
FEED = "sip"


def curve(closes: list[float], avg: int | None) -> list[float]:
    """Daily growth of $1: in the fund on days after a close above its average (always, if avg is None)."""
    out, eq, held = [1.0], 1.0, False
    for i in range(1, len(closes)):
        if held:
            eq *= closes[i] / closes[i - 1]
        want = True if avg is None else (i + 1 >= avg and closes[i] > sum(closes[i + 1 - avg: i + 1]) / avg)
        if want != held:
            eq *= 1 - COST
            held = want
        out.append(eq)
    return out


def max_dd(xs: list[float]) -> float:
    peak, dd = xs[0], 0.0
    for x in xs:
        peak = max(peak, x)
        dd = min(dd, x / peak - 1)
    return dd


def attempts(dates: list[str], eq: list[float], start_cash: float) -> dict:
    """A fresh account at each month's first day: did it reach $10,000, or fall below 10% of its start, first?"""
    firsts = [i for i in range(len(dates)) if i == 0 or dates[i][:7] != dates[i - 1][:7]]
    res = {"n": 0, "hero1": 0, "hero2": 0, "hero3": 0, "zero": 0, "days": []}
    for i in firsts:
        horizon = (date.fromisoformat(dates[i]) + timedelta(days=3 * 365)).isoformat()
        if horizon > dates[-1]:
            break
        res["n"] += 1
        for j in range(i, len(dates)):
            if dates[j] > horizon:
                break
            v = start_cash * eq[j] / eq[i]
            if v < start_cash * 0.1:
                res["zero"] += 1
                break
            if v >= TARGET:
                yrs = (date.fromisoformat(dates[j]) - date.fromisoformat(dates[i])).days / 365
                for y in (1, 2, 3):
                    if yrs <= y:
                        res[f"hero{y}"] += 1
                res["days"].append((date.fromisoformat(dates[j]) - date.fromisoformat(dates[i])).days)
                break
    days = sorted(res.pop("days"))
    res["median_days"] = days[len(days) // 2] if days else None
    return res


def run(client) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    bars = client.daily_bars(list(FUNDS), START, feed=FEED)
    out = {"generated": date.today().isoformat(), "end": end, "funds": {}}
    for f in FUNDS:
        bs = [b for b in bars.get(f, []) if b["t"][:10] <= end]
        dates, closes = [b["t"][:10] for b in bs], [float(b["c"]) for b in bs]
        if len(closes) < 60:
            continue
        rows = {}
        for avg in (None,) + AVERAGES:
            eq = curve(closes, avg)
            years = {}
            for i, d in enumerate(dates):
                years.setdefault(d[:4], [eq[i], eq[i]])[1] = eq[i]
            name = "hold" if avg is None else f"ma{avg}"
            rows[name] = {"growth": round(eq[-1], 2), "max_dd": round(max_dd(eq), 3),
                          "years": {y: round(b / a - 1, 3) for y, (a, b) in years.items()},
                          "a500": attempts(dates, eq, 500.0), "a1000": attempts(dates, eq, 1000.0)}
        out["funds"][f] = {"first": dates[0], "rows": rows}
    return out


def markdown(rep: dict) -> str:
    names = {"hold": "一直拿着", "ma20": "20 日均线上方才拿", "ma50": "50 日均线上方才拿"}
    out = [f"# 杠杆 ETF 趋势持有 {rep['generated']}", "",
           "每天收盘时，收在均线上方就拿到下一天，否则空仓；进出各 0.05%。从每个月第一天开一个新的 $500 / $1,000 账户，"
           "看 3 年内先到 $10,000 还是先跌到起始的 10% 以下。只研究，不改交易。", "",
           "| 基金 | 规则 | 期间增长倍数 | 最大回撤 | $500：1 / 2 / 3 年内到 $10,000（次 / 共） | $500：跌破 $50 | $1,000：3 年内到 $10,000 | 成功用时中位数（天） |",
           "|---|---|---|---|---|---|---|---|"]
    for f, v in rep["funds"].items():
        for k, r in v["rows"].items():
            a, b = r["a500"], r["a1000"]
            out.append(f"| {f}（{v['first'][:4]} 起） | {names[k]} | {r['growth']:,.1f} 倍 | {r['max_dd']:.0%} | "
                       f"{a['hero1']} / {a['hero2']} / {a['hero3']}（共 {a['n']}） | {a['zero']} | {b['hero3']}（共 {b['n']}） | "
                       f"{a['median_days'] if a['median_days'] is not None else '—'} |")
    out += ["", "## 每年回报", ""]
    for f, v in rep["funds"].items():
        years = sorted(next(iter(v["rows"].values()))["years"])
        out += [f"### {f}", "", "| 规则 | " + " | ".join(years) + " |", "|---" * (len(years) + 1) + "|"]
        for k, r in v["rows"].items():
            out.append(f"| {names[k]} | " + " | ".join(f"{r['years'].get(y, 0):+.0%}" for y in years) + " |")
        out.append("")
    return "\n".join(out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca())
    (root / "research" / f"{rep['generated']}-letf.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    (root / "research" / f"{rep['generated']}-letf.md").write_text(markdown(rep))
    print(markdown(rep))


if __name__ == "__main__":
    sys.exit(main())
