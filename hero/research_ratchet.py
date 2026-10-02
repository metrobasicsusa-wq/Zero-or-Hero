"""Research: "small lottery stake, catch one wave, then change rhythm".

A $500 account splits into a core and a lottery budget. The core goes into the momentum strategy
(126-day momentum, top 2, monthly dynamic top-100 pool) from day one. The budget buys earnings
lottery calls with a fixed stake per bet. As soon as one bet pays 10x or more, or the lottery pot
doubles, the lottery stops and its cash joins the momentum strategy. If the budget runs out
first, what is left joins it too. Compared with putting all $500 into momentum.

Inputs (latest of each, produced by earlier studies):
  research/*-earnings-lottery.json   per-event call trades with several exits
  research/*-s500-aggressive.json    daily returns of the momentum variants
Windows: a fresh start on the first trading day of every month with 12 months ahead.
Record only. Run: python -m hero.research_ratchet
"""

from __future__ import annotations

import json
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

ACCOUNT = 500.0
STAKES = (10, 20, 25)
BUDGETS = (100, 200, 300)
STRIKES = ("价外 5%", "价外 10%", "预期波动 1 倍处", "预期波动 1.5 倍处")
EXITS = {"ret_open": "开盘卖", "ret_tp3": "3 倍止盈", "ret_tp5": "5 倍止盈", "ret_tp10": "10 倍止盈"}
SELECTION = {"every": "每次财报都买", "weekly": "每周只买 1 次（财报前动量最强的）"}
HIT = 9.0  # a 10x bet
CORE = "动态前 100|126|2"
HORIZON_MONTHS = 12


def latest(root: Path, pattern: str) -> dict:
    files = sorted((root / "research").glob(pattern))
    if not files:
        raise SystemExit(f"missing research/{pattern}; run the earlier study first")
    return json.loads(files[-1].read_text())


def growth_index(dates: list[str], rets: list[float]) -> dict[str, float]:
    """Value of $1 at the close of each date (returns earned on that date)."""
    idx, v = {}, 1.0
    for d, r in zip(dates, rets):
        v *= 1 + r
        idx[d] = v
    return idx


def value_at(idx: dict[str, float], days: list[str], d: str) -> float:
    """Index value at the last close on or before d (1.0 before the series starts)."""
    import bisect
    i = bisect.bisect_right(days, d) - 1
    return idx[days[i]] if i >= 0 else 1.0


def pick(events: list[dict], mode: str) -> list[dict]:
    if mode == "every":
        return events
    best: dict[str, dict] = {}
    for e in events:  # one bet per ISO week: the strongest prior momentum
        y, w, _ = date.fromisoformat(e["reaction_day"]).isocalendar()
        k = f"{y}-{w}"
        if k not in best or (e.get("mom126") or -9) > (best[k].get("mom126") or -9):
            best[k] = e
    return sorted(best.values(), key=lambda e: e["reaction_day"])


def window(events, idx, days, start, end, stake, budget) -> dict:
    core = (ACCOUNT - budget) * value_at(idx, days, end) / value_at(idx, days, start)
    pot, switched, hit = float(budget), None, False
    for e in events:
        if not start < e["reaction_day"] <= end:
            continue
        if pot < stake:
            switched = e["reaction_day"]
            break
        r = e["ret"]
        pot += stake * r
        if r >= HIT or pot >= 2 * budget:
            hit, switched = r >= HIT or pot >= 2 * budget, e["reaction_day"]
            break
    if switched:
        pot *= value_at(idx, days, end) / value_at(idx, days, switched)
    return {"final": core + pot, "hit": hit}


def run(root: Path) -> dict:
    lot, mom = latest(root, "*-earnings-lottery.json"), latest(root, "*-s500-aggressive.json")
    days, idx = mom["series_dates"], growth_index(mom["series_dates"], mom["series"][CORE])
    starts, d = [], date.fromisoformat(max(days[0], min(t["reaction_day"] for t in lot["trades"])))
    last = date.fromisoformat(days[-1])
    while d + timedelta(days=365 * HORIZON_MONTHS // 12) <= last:
        starts.append(d.isoformat())
        d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    windows = [(s, (date.fromisoformat(s) + timedelta(days=365)).isoformat()) for s in starts]
    pure = [ACCOUNT * value_at(idx, days, e) / value_at(idx, days, s) for s, e in windows]
    results = []
    for strike in STRIKES:
        for exit_key, exit_label in EXITS.items():
            base = sorted(({"reaction_day": t["reaction_day"], "mom126": t.get("mom126"), "ret": t[exit_key]}
                           for t in lot["trades"] if t["strategy"] == strike and t.get(exit_key) is not None),
                          key=lambda e: e["reaction_day"])
            for mode in SELECTION:
                evs = pick(base, mode)
                for stake in STAKES:
                    for budget in BUDGETS:
                        if stake > budget:
                            continue
                        runs = [window(evs, idx, days, s, e, stake, budget) for s, e in windows]
                        finals = [r["final"] for r in runs]
                        results.append({
                            "strike": strike, "exit": exit_label, "selection": SELECTION[mode], "stake": stake,
                            "budget": budget, "median": round(statistics.median(finals)),
                            "mean": round(statistics.mean(finals)), "p10": round(sorted(finals)[len(finals) // 10]),
                            "best": round(max(finals)), "worst": round(min(finals)),
                            "hit_rate": round(sum(r["hit"] for r in runs) / len(runs), 2),
                            "beats_pure": round(sum(f > p for f, p in zip(finals, pure)) / len(runs), 2)})
    return {"generated": date.today().isoformat(), "windows": len(windows), "core": CORE,
            "pure": {"median": round(statistics.median(pure)), "mean": round(statistics.mean(pure)),
                     "p10": round(sorted(pure)[len(pure) // 10]), "best": round(max(pure)), "worst": round(min(pure))},
            "results": results}


def markdown(rep: dict) -> str:
    p = rep["pure"]
    out = [f"# 小仓博彩票、中了就换节奏 {rep['generated']}", "",
           f"$500 账户：彩票预算以外的钱从第一天起做动量策略（{rep['core']}）；彩票预算按固定金额买财报看涨期权，"
           f"一旦某次赚 10 倍以上或彩票资金翻倍，就停止买彩票，钱全部转去做动量；预算用完也转过去。"
           f"从 2024 年起每个月作为一个起点，各跑 12 个月，共 {rep['windows']} 个窗口。只研究，不改交易。", "",
           f"**对照：全部 $500 只做动量** —— 12 个月后中位数 ${p['median']:,}，平均 ${p['mean']:,}，"
           f"最差 10% 低于 ${p['p10']:,}，最好 ${p['best']:,}，最差 ${p['worst']:,}", "",
           "## 平均结果最好的 15 种组合", "",
           "| 行权价 | 卖法 | 选择 | 每次 | 预算 | 中奖窗口比例 | 中位数 | 平均 | 最差 10% | 最好 | 赢过纯动量的窗口 |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(rep["results"], key=lambda r: -r["mean"])[:15]:
        out.append(f"| {r['strike']} | {r['exit']} | {r['selection']} | ${r['stake']} | ${r['budget']} | {r['hit_rate']:.0%} | "
                   f"${r['median']:,} | ${r['mean']:,} | ${r['p10']:,} | ${r['best']:,} | {r['beats_pure']:.0%} |")
    out += ["", "## 中位数最好的 5 种（更看重「一般情况」）", ""]
    for r in sorted(rep["results"], key=lambda r: -r["median"])[:5]:
        out.append(f"- {r['strike']}、{r['exit']}、{r['selection']}、每次 ${r['stake']}、预算 ${r['budget']}：中位数 ${r['median']:,}，"
                   f"平均 ${r['mean']:,}，中奖窗口 {r['hit_rate']:.0%}，赢过纯动量 {r['beats_pure']:.0%}")
    return "\n".join(out) + "\n"


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    rep = run(root)
    (root / "research" / f"{rep['generated']}-ratchet.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False) + "\n")
    (root / "research" / f"{rep['generated']}-ratchet.md").write_text(markdown(rep))
    print(markdown(rep))


if __name__ == "__main__":
    sys.exit(main())
