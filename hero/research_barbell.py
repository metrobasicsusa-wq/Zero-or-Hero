"""Research: a barbell for the $500 / $1,000 accounts -- most of the money in a trend core, a slice on option tickets.

The single-rule studies say option buying loses on average but now and then pays 20-50x, while a trend core
(TQQQ held above its 20-day average) grows but slowly. Here the two together, on the option trades already
replayed on real prices (so since 2024-02, the start of Alpaca's option history):
  core:     TQQQ above its 20-day average (else cash), or SPY held; or no core (all cash between tickets);
  tickets:  each trade risks a fixed share of the whole account at that moment, taken out of the core for the
            day and paid back with its result:
              SPY 0DTE, the live rule (10:00 with the trend, 0.6% out, 3x take), research/*-0dte.json;
              SPY calls the day after a 1%+ drop (0.6% out, 3x take), research/*-zdte-streak.json;
              META $0.01-0.05 same-day tickets with a 50x take, research/*-lotto0dte.json (a day's tickets
              split equally);
  shares:   0.5% / 1% / 2% / 5% / 10% of the account per ticket (and 0%: the core alone);
then, starting a fresh $500 / $1,000 at each month's start: how often it doubled / reached $10,000 within
12 months, how often it fell below 10% of its start, and the median value after 12 months; plus one path
over the whole period. Costs: the ticket results already pay 10% each way; the core 0.05% per switch.
Record only. Run: python -m hero.research_barbell
"""

from __future__ import annotations

import glob
import json
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

from hero.research_letf import curve
from hero.research_streak import daily

ROOT = Path(__file__).resolve().parent.parent
STARTS = (500.0, 1000.0)
TARGET = 10_000.0
HORIZON = 252          # sessions in a year
SHARES = (0.0, 0.005, 0.01, 0.02, 0.05, 0.10)
CORES = ("TQQQ>MA20", "SPY", "cash")
LOTS = ("spy0dte", "spydip", "meta50")
LOT_ZH = {"spy0dte": "SPY 末日单（现行）", "spydip": "SPY 昨跌 1% 后买看涨", "meta50": "META 彩票 50 倍止盈"}


def latest(pattern: str) -> dict:
    files = sorted(glob.glob(str(ROOT / "research" / pattern)))
    return json.loads(Path(files[-1]).read_text()) if files else {}


def tickets() -> dict[str, dict[str, float]]:
    """Each stream: {day: return per $1}."""
    out: dict[str, dict[str, float]] = {k: {} for k in LOTS}
    for d, r in latest("*-0dte.json").get("trades", {}).get("SPY|10:00|0.006|3|trend", []):
        out["spy0dte"][d] = r
    for r in latest("*-zdte-streak.json").get("rows", []):
        x = r["res"].get("C|0.006|3")
        if r["symbol"] == "SPY" and r["prev_ret"] <= -0.01 and x is not None:
            out["spydip"][r["day"]] = x
    by_day: dict[str, list[float]] = {}
    for r in latest("*-lotto0dte.json").get("rows", []):
        if r["symbol"] == "META":
            by_day.setdefault(r["day"], []).append(r["take50"])
    out["meta50"] = {d: statistics.mean(xs) for d, xs in by_day.items()}
    return out


def core_growth(days: list[str], closes: dict[str, dict[str, float]], core: str) -> list[float]:
    """Daily growth factor of the core for each day (1.0 on day 0)."""
    if core == "cash":
        return [1.0] * len(days)
    sym = "TQQQ" if core.startswith("TQQQ") else "SPY"
    c = [closes[sym][d] for d in days]
    eq = curve(c, 20 if core.startswith("TQQQ") else None)
    return [1.0] + [eq[i] / eq[i - 1] for i in range(1, len(eq))]


def path(days: list[str], growth: list[float], lot: dict[str, float], share: float, start: float,
         i0: int, i1: int) -> list[float]:
    """Account value at each day's close from day i0 to i1, a ticket risking `share` on its days."""
    v, out = start, []
    for i in range(i0, i1):
        if i > i0:
            r = lot.get(days[i]) if share else None
            bet = share * v if r is not None else 0.0
            v = (v - bet) * growth[i] + bet * (1 + r if r is not None else 1)
        out.append(v)
    return out


def windows(days: list[str], growth: list[float], lot: dict[str, float], share: float, start: float) -> dict:
    """A fresh account from each month's first session that has a year after it."""
    firsts = [i for i in range(1, len(days)) if days[i][:7] != days[i - 1][:7]]
    res = []
    for i0 in firsts:
        if i0 + HORIZON >= len(days):
            break
        p = path(days, growth, lot, share, start, i0, i0 + HORIZON + 1)
        hero = next((k for k, x in enumerate(p) if x >= TARGET), None)
        zero = next((k for k, x in enumerate(p) if x < 0.1 * start), None)
        res.append({"x2": any(x >= 2 * start for x in p), "hero": hero is not None and (zero is None or hero < zero),
                    "zero": zero is not None and (hero is None or zero < hero), "end": p[-1] / start})
    n = len(res)
    if not n:
        return {"n": 0}
    return {"n": n, "x2": round(sum(r["x2"] for r in res) / n, 2), "hero": round(sum(r["hero"] for r in res) / n, 2),
            "zero": round(sum(r["zero"] for r in res) / n, 2), "median_end": round(statistics.median(r["end"] for r in res), 2),
            "worst_end": round(min(r["end"] for r in res), 2), "best_end": round(max(r["end"] for r in res), 2)}


def max_dd(xs: list[float]) -> float:
    peak, dd = xs[0], 0.0
    for x in xs:
        peak = max(peak, x)
        dd = min(dd, x / peak - 1)
    return dd


def run(client) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    closes = {s: {b["t"][:10]: float(b["c"]) for b in daily(client, s, f"{end}T23:00:00Z") if b["t"][:10] <= end}
              for s in ("TQQQ", "SPY")}
    lots = tickets()
    first = min(min(v) for v in lots.values() if v)
    last = max(max(v) for v in lots.values() if v)
    all_days = sorted(set(closes["TQQQ"]) & set(closes["SPY"]))
    days = [d for d in all_days if d <= last]
    i_first = next(i for i, d in enumerate(days) if d >= first)
    out = {"generated": date.today().isoformat(), "first": days[i_first], "last": days[-1], "streams": {k: len(v) for k, v in lots.items()},
           "rows": []}
    for core in CORES:
        g_all = core_growth(days, closes, core)  # computed over the warm-up too, so the 20-day average is ready
        sub_days, growth = days[i_first:], g_all[i_first:]
        for lot in LOTS:
            for share in SHARES:
                if share == 0 and lot != LOTS[0]:
                    continue  # the core alone once per core
                if core == "cash" and share == 0:
                    continue
                row = {"core": core, "lot": lot if share else "—", "share": share}
                for st in STARTS:
                    row[f"w{int(st)}"] = windows(sub_days, growth, lots[lot], share, st)
                p = path(sub_days, growth, lots[lot], share, 1000.0, 0, len(sub_days))
                row["whole"] = {"end": round(p[-1] / 1000.0, 2), "max_dd": round(max_dd(p), 3)}
                out["rows"].append(row)
    return out


def markdown(rep: dict) -> str:
    core_zh = {"TQQQ>MA20": "TQQQ 20 日均线上方", "SPY": "一直拿 SPY", "cash": "不放核心（现金）"}
    out = [f"# 杠铃组合：趋势核心 + 一小块期权票 {rep['generated']}", "",
           f"{rep['first']} 至 {rep['last']}（期权历史从 2024-02 开始，所以只有这段）。每笔期权票拿账户当时总值的 0.5% / 1% / 2% / 5% / 10% 去买，"
           "其余放在核心里（TQQQ 收在 20 日均线上方才拿 / 一直拿 SPY / 现金），票的结果当天回到核心。"
           f"票的数据：{ {LOT_ZH[k]: v for k, v in rep['streams'].items()} } 次（天）。"
           "每个月初用新的 $500 / $1,000 开始，看 12 个月内翻倍、到 $10,000、跌破起始 10% 的比例，以及 12 个月后的中位数倍数；"
           "再看从头到尾一条路径的倍数和最大回撤。期权票已按买卖各 10% 成本算。只研究，不改交易。", "",
           "| 核心 | 票 | 每笔占账户 | $500：翻倍 / 到 $1 万 / 归零 / 12 个月后中位数（最差～最好） | $1,000：同左 | 全程倍数 | 全程最大回撤 |",
           "|---|---|---|---|---|---|---|"]
    for r in rep["rows"]:
        cells = []
        for st in STARTS:
            w = r[f"w{int(st)}"]
            cells.append(f"{w['x2']:.0%} / {w['hero']:.0%} / {w['zero']:.0%} / {w['median_end']:.2f} 倍（{w['worst_end']:.2f}～{w['best_end']:.2f}）"
                         if w.get("n") else "—")
        lot = LOT_ZH.get(r["lot"], "—")
        out.append(f"| {core_zh[r['core']]} | {lot} | {r['share']:.0%} | {cells[0]} | {cells[1]} | {r['whole']['end']:.2f} 倍 | {r['whole']['max_dd']:.0%} |")
    n = rep["rows"][0]["w500"].get("n", 0)
    out += ["", f"每格的比例来自 {n} 个起点（每月一个，且后面要有满 12 个月），起点互相重叠，不是独立样本。"]
    return "\n".join(out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    rep = run(Alpaca())
    (ROOT / "research" / f"{rep['generated']}-barbell.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    (ROOT / "research" / f"{rep['generated']}-barbell.md").write_text(markdown(rep))
    print(markdown(rep))


if __name__ == "__main__":
    sys.exit(main())
