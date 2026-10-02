"""Research for Claude-500: which aggressive rule has an edge that survives an unbiased pool?

Grid: momentum lookback x number of names held x stock pool (the fixed hand-picked 20, and the
monthly liquidity-ranked top 100 / top 300 built only from data known at the time), plus
leveraged-ETF trend following (TQQQ / SOXL while QQQ / SMH is above its 200-day average).
Every variant is all-in (90% invested) with daily rebalancing and 5 bp costs.

Besides return, Sharpe and drawdown it plays the experiment's restart rule: an attempt ends at
a 40% loss of its start capital and a fresh $500 attempt begins. Reported: attempts used and
net result = final equity - all capital allocated, per $500. Record only, no trading.
Run: python -m hero.research_s500
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

from hero.indicators import sma
from hero.research_short import by_year, stats
from hero.research_universe import ETFS, LOOKBACK_DV, align, candidates, monthly_pools, simulate, window

YEARS = 6
COST = 0.0005
GROSS = 0.9
END_LOSS = 0.4
LOOKBACKS = (10, 20, 63, 126)
TOP_NS = (1, 2, 3, 4)
LEVERED = {"TQQQ": "QQQ", "SOXL": "SMH"}


def attempts(rets: list[float]) -> dict:
    """Replay the restart rule: per $500, how many attempts and what is left net of all of them."""
    cap, used, ended = 1.0, 1, []
    for r in rets:
        cap *= 1 + r
        if cap <= 1 - END_LOSS:
            ended.append(round(cap, 3))
            cap, used = 1.0, used + 1  # a fresh $500; what was left of the old attempt is written off
    return {"attempts": used, "final_attempt_multiple": round(cap, 3),
            "net_per_500": round((cap - used) * 500, 0)}


def params(lookback: int, top_n: int) -> dict:
    return {"momentum_lookback": lookback, "trend_sma": 20 if lookback <= 20 else 50, "top_n": top_n,
            "gross_exposure": GROSS, "rsi_max": 85, "rsi_applies_to_holdings": False}


def levered_trend(dates, closes, firsts, etf: str, gauge: str, start: int) -> list[float]:
    rets, prev = [], 0.0
    for t in range(start, len(dates) - 1):
        g = window(closes[gauge], firsts[gauge], t, 201)
        on = len(g) > 200 and g[-1] > sma(g, 200)
        w = GROSS if on and closes[etf][t] else 0.0
        r = w * (closes[etf][t + 1] / closes[etf][t] - 1) if w else 0.0
        rets.append(r - COST * abs(w - prev))
        prev = w
    return rets


def run(client, root: Path) -> dict:
    cfg = json.loads((root / "config" / "s500.json").read_text())
    start_day = (date.today() - timedelta(days=365 * YEARS)).isoformat()
    stocks = candidates(client)
    syms = sorted(set(stocks) | set(ETFS) | set(cfg["universe"]) | set(LEVERED) | set(LEVERED.values()))
    bars: dict[str, list[dict]] = {}
    for i in range(0, len(syms), 200):
        bars.update(client.daily_bars(syms[i:i + 200], start_day))
    dates, closes, dvol = align(bars, "SPY")
    for s in syms:
        closes.setdefault(s, [None] * len(dates))
        dvol.setdefault(s, [0.0] * len(dates))
    firsts = {s: next((i for i, x in enumerate(xs) if x is not None), None) for s, xs in closes.items()}
    have = {s: k for s, k in stocks.items() if s in bars}
    warm = 260 + LOOKBACK_DV

    pools = {"固定 20 只": lambda t: cfg["universe"]}
    for size in (100, 300):
        mp = monthly_pools(dates, closes, dvol, firsts, have, size, warm)
        keys = sorted(mp)
        pools[f"动态前 {size}"] = lambda t, mp=mp, keys=keys: mp[max(k for k in keys if k <= t)] + ETFS

    rows = []
    for pool, pool_at in pools.items():
        for lb in LOOKBACKS:
            for n in TOP_NS:
                rets, _ = simulate(dates, closes, firsts, params(lb, n), cfg["regime_symbol"], 1.0, warm, pool_at)
                rows.append({"pool": pool, "lookback": lb, "top_n": n, **stats(rets), **attempts(rets),
                             "by_year": by_year(dates, rets, warm)})
    for etf, gauge in LEVERED.items():
        rets = levered_trend(dates, closes, firsts, etf, gauge, warm)
        rows.append({"pool": f"{etf}（{gauge} 在 200 日线上方时持有）", "lookback": None, "top_n": None,
                     **stats(rets), **attempts(rets), "by_year": by_year(dates, rets, warm)})

    # Robust = positive CAGR on BOTH dynamic pools for the same (lookback, top_n).
    def key(r):
        return (r["lookback"], r["top_n"])
    dyn = {}
    for r in rows:
        if r["pool"].startswith("动态"):
            dyn.setdefault(key(r), []).append(r)
    robust = [k for k, rs in dyn.items() if len(rs) == 2 and all((x["cagr"] or -1) > 0 for x in rs)]
    return {"generated": date.today().isoformat(), "period": f"{dates[warm]} → {dates[-1]}",
            "candidates": len(have), "gross": GROSS, "end_loss": END_LOSS, "rows": rows,
            "robust": [{"lookback": k[0], "top_n": k[1],
                        "dyn_cagr": [x["cagr"] for x in dyn[k]], "dyn_attempts": [x["attempts"] for x in dyn[k]]}
                       for k in sorted(robust, key=lambda k: -min(x["cagr"] for x in dyn[k]))]}


def markdown(rep: dict) -> str:
    out = [f"# Claude-500 激进方案回测 {rep['generated']}", "",
           f"区间 {rep['period']}；候选股票 {rep['candidates']} 只；所有方案 {rep['gross']:.0%} 仓位、每日调仓、5 个基点成本；"
           f"按实验规则「起始资金亏 {rep['end_loss']:.0%} 就重来」重放：尝试次数、每 $500 的净结果（最终净值 − 全部投入）。"
           "只研究，不改交易。", "",
           "| 股票池 | 动量天数 | 持几只 | 年化 | 夏普 | 最大回撤 | 尝试次数 | 每 $500 净结果 |",
           "|---|---|---|---|---|---|---|---|"]
    for r in sorted(rep["rows"], key=lambda r: (r["pool"], r["lookback"] or 0, r["top_n"] or 0)):
        cagr = f"{r['cagr']:+.1%}" if r["cagr"] is not None else "归零"
        out.append(f"| {r['pool']} | {r['lookback'] or '—'} | {r['top_n'] or '—'} | {cagr} | {r['sharpe']:.2f} | "
                   f"{r['max_drawdown']:.0%} | {r['attempts']} | ${r['net_per_500']:+,.0f} |")
    out += ["", "## 在两个动态股票池上都赚钱的组合（按较差的那个年化排序）", ""]
    if rep["robust"]:
        for k in rep["robust"]:
            out.append(f"- 动量 {k['lookback']} 天、持 {k['top_n']} 只：动态前 100/前 300 年化 "
                       + " / ".join(f"{c:+.1%}" for c in k["dyn_cagr"]) + f"，尝试次数 {k['dyn_attempts']}")
    else:
        out.append("- 没有任何组合在两个动态股票池上都是正收益。")
    return "\n".join(out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca(), root)
    out = root / "research"
    out.mkdir(exist_ok=True)
    (out / f"{rep['generated']}-s500-aggressive.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False) + "\n")
    (out / f"{rep['generated']}-s500-aggressive.md").write_text(markdown(rep))
    print(markdown(rep))


if __name__ == "__main__":
    sys.exit(main())
