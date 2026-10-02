"""Research: does a short side help? Backtests on daily closes, record only (no trading).

For each experiment's universe it compares the current long-only momentum book with:
  short_leg   short the weakest names (below trend, negative momentum), alone
  long_short  the long book plus that short leg
  bear_short  in a bear regime, also short the regime index (stands in for index puts)
  bear_inverse in a bear regime, also hold the leveraged inverse ETF (SQQQ / SOXS), real prices
Costs: 5 bp per unit of turnover; borrow on every short, by universe.
Symbols that listed later join once they have enough history (no survivorship trimming of
the start date). Run: python -m hero.research_short  (needs Alpaca data keys)
"""

from __future__ import annotations

import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path

from hero.indicators import max_drawdown, momentum, sharpe, sma
from hero.strategies import momentum as mom

COST = 0.0005
YEARS = 6
SHORT_N = 3
SHORT_GROSS = 0.3
HEDGE = 0.3
SETUPS = {
    "Claude": {"config": "config/strategy.json", "inverse": "SQQQ", "borrow": 0.005},
    "Claude-500": {"config": "config/s500.json", "inverse": "SOXS", "borrow": 0.03},
}


def align(bars: dict[str, list[dict]], calendar: str) -> tuple[list[str], dict[str, list[float | None]]]:
    by = {s: {b["t"][:10]: float(b["c"]) for b in bs} for s, bs in bars.items()}
    dates = sorted(by[calendar])
    out = {}
    for s, m in by.items():
        xs, last = [], None
        for d in dates:
            last = m.get(d, last)
            xs.append(last)
        out[s] = xs
    return dates, out


def history(xs: list[float | None], t: int) -> list[float]:
    """Prices up to and including day t, from the symbol's first quote."""
    first = next((i for i, x in enumerate(xs) if x is not None), None)
    return [] if first is None or first > t else xs[first: t + 1]


def short_candidates(hist: dict[str, list[float]], p: dict) -> list[str]:
    scored = []
    for s, xs in hist.items():
        m, trend = momentum(xs, p["momentum_lookback"]), sma(xs, p["trend_sma"])
        if m is not None and trend is not None and xs[-1] < trend and m < 0:
            scored.append((m, s))
    return [s for _, s in sorted(scored)[:SHORT_N]]


def simulate(closes: dict, universe: list[str], regime: str, inverse: str | None, p: dict, max_pos: float,
             borrow: float, start: int, end: int) -> dict[str, list[float]]:
    variants = ("long_only", "short_leg", "long_short", "bear_short", "bear_inverse")
    rets = {v: [] for v in variants}
    prev = {v: {} for v in variants}
    for t in range(start, end - 1):
        hist = {s: h for s in universe if (h := history(closes[s], t)) and len(h) > p["trend_sma"] + 1}
        longs = mom.target_weights(hist, p, regime, max_pos, held=frozenset(prev["long_only"]))
        shorts = {s: -SHORT_GROSS / SHORT_N for s in short_candidates(hist, p)}
        bear = regime in hist and not mom.is_bullish(hist[regime])
        books = {
            "long_only": longs,
            "short_leg": shorts,
            "long_short": {**longs, **{s: longs.get(s, 0) + w for s, w in shorts.items()}},
            "bear_short": {**longs, regime: longs.get(regime, 0) - HEDGE} if bear else longs,
            "bear_inverse": {**longs, inverse: HEDGE} if bear and inverse and history(closes[inverse], t) else longs,
        }
        for v, w in books.items():
            w = {s: x for s, x in w.items() if x}
            r = 0.0
            for s, x in w.items():
                a, b = closes[s][t], closes[s][t + 1]
                if a and b:
                    r += x * (b / a - 1)
            turnover = sum(abs(w.get(s, 0) - prev[v].get(s, 0)) for s in set(w) | set(prev[v]))
            short_gross = sum(-x for x in w.values() if x < 0)
            rets[v].append(r - COST * turnover - borrow / 252 * short_gross)
            prev[v] = w
    return rets


def stats(rets: list[float]) -> dict:
    eq = [1.0]
    for r in rets:
        eq.append(eq[-1] * (1 + r))
    years = len(rets) / 252
    return {"total_return": round(eq[-1] - 1, 4),
            "cagr": round(eq[-1] ** (1 / years) - 1, 4) if years > 0 and eq[-1] > 0 else None,
            "sharpe": round(sharpe(rets), 2), "max_drawdown": round(max_drawdown(eq), 4), "days": len(rets)}


def by_year(dates: list[str], rets: list[float], start: int) -> dict[str, float]:
    out: dict[str, float] = {}
    for i, r in enumerate(rets):
        y = dates[start + i + 1][:4]
        out[y] = out.get(y, 1.0) * (1 + r)
    return {y: round(v - 1, 4) for y, v in out.items()}


def rebound_days(closes: dict, regime: str, start: int, end: int) -> list[int]:
    """Days in sharp rebounds (index up >10% in 20 days after being >10% below its 60-day high),
    when momentum short books historically get squeezed."""
    xs, out = closes[regime], []
    for t in range(max(start, 60), end - 1):
        win = [x for x in xs[t - 60: t + 1] if x]
        if xs[t] and xs[t - 20] and max(win) and xs[t - 20] < 0.9 * max(win) and xs[t] / xs[t - 20] - 1 > 0.10:
            out.append(t - start)
    return out


def run(client, root: Path) -> dict:
    report = {"generated": date.today().isoformat(), "method": __doc__.strip().splitlines()[0],
              "params": {"years": YEARS, "short_n": SHORT_N, "short_gross": SHORT_GROSS, "hedge": HEDGE, "cost": COST},
              "results": {}}
    for name, s in SETUPS.items():
        cfg = json.loads((root / s["config"]).read_text())
        universe, regime = cfg["universe"], cfg["regime_symbol"]
        start_day = (date.today() - timedelta(days=365 * YEARS)).isoformat()
        bars = client.daily_bars(sorted(set(universe) | {regime, s["inverse"]}), start_day)
        dates, closes = align(bars, regime)
        warm = max(cfg["stocks"]["trend_sma"], cfg["stocks"]["momentum_lookback"], 200) + 5
        rets = simulate(closes, universe, regime, s["inverse"], cfg["stocks"], cfg["risk"]["max_position_pct"],
                        s["borrow"], warm, len(dates))
        rb = rebound_days(closes, regime, warm, len(dates))
        report["results"][name] = {
            "period": f"{dates[warm]} → {dates[-1]}",
            "universe_coverage": {u: next((dates[i] for i, x in enumerate(closes.get(u, [])) if x), None)
                                  for u in universe},
            "borrow_rate": s["borrow"], "inverse": s["inverse"],
            "variants": {v: {**stats(r), "by_year": by_year(dates, r, warm),
                             "rebound_days_return": round(math.prod(1 + r[i] for i in rb) - 1, 4) if rb else None}
                         for v, r in rets.items()},
            "rebound_days": len(rb),
        }
    return report


def markdown(rep: dict) -> str:
    names = {"long_only": "现策略（只做多）", "short_leg": "只做空最弱的 3 只（30%）", "long_short": "做多 + 做空最弱 3 只",
             "bear_short": "熊市时加空指数 30%（≈买看跌）", "bear_inverse": "熊市时加 30% 反向 ETF"}
    out = [f"# 做空研究回测 {rep['generated']}", "", "只研究，不改交易。日收盘价回测，含 5 个基点交易成本和借券费。", ""]
    for exp, r in rep["results"].items():
        out += [f"## {exp}（{r['period']}，借券费 {r['borrow_rate']:.1%}/年，反向 ETF {r['inverse']}）", "",
                "| 方案 | 总收益 | 年化 | 夏普 | 最大回撤 | 急反弹期（%d 天） |" % r["rebound_days"],
                "|---|---|---|---|---|---|"]
        for v, s in r["variants"].items():
            rb = f"{s['rebound_days_return']:+.1%}" if s["rebound_days_return"] is not None else "—"
            cagr = f"{s['cagr']:+.1%}" if s["cagr"] is not None else "—"
            out.append(f"| {names[v]} | {s['total_return']:+.1%} | {cagr} | {s['sharpe']:.2f} | {s['max_drawdown']:.1%} | {rb} |")
        years = sorted(next(iter(r["variants"].values()))["by_year"])
        out += ["", "| 方案 | " + " | ".join(years) + " |", "|---|" + "---|" * len(years)]
        for v, s in r["variants"].items():
            out.append(f"| {names[v]} | " + " | ".join(f"{s['by_year'].get(y, 0):+.1%}" for y in years) + " |")
        late = [u for u, d in r["universe_coverage"].items() if d and d > r["period"][:10]]
        if late:
            out += ["", f"上市较晚、中途加入回测的标的：{', '.join(late)}"]
        out.append("")
    return "\n".join(out)


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca(), root)
    out = root / "research"
    out.mkdir(exist_ok=True)
    (out / f"{rep['generated']}-short-backtest.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False) + "\n")
    (out / f"{rep['generated']}-short-backtest.md").write_text(markdown(rep))
    print(markdown(rep))


if __name__ == "__main__":
    sys.exit(main())
