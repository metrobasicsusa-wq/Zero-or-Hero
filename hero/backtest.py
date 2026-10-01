"""Daily close-to-close backtest of the stock momentum strategy."""

from __future__ import annotations

from hero.indicators import max_drawdown, sharpe
from hero.strategies import momentum

COST = 0.0005  # per unit of turnover (commission-free, so this is slippage)


def align(bars: dict[str, list[dict]], calendar_symbol: str) -> tuple[list[str], dict[str, list[float]]]:
    """Put every symbol on the calendar symbol's dates, forward-filling gaps."""
    by_sym = {s: {b["t"][:10]: float(b["c"]) for b in bs} for s, bs in bars.items()}
    dates = sorted(by_sym[calendar_symbol])
    out = {}
    for s, m in by_sym.items():
        xs, last = [], None
        for d in dates:
            last = m.get(d, last)
            xs.append(last)
        first = next((i for i, x in enumerate(xs) if x is not None), None)
        if first == 0:
            out[s] = xs
    return dates, out


def run(closes: dict[str, list[float]], p: dict, regime: str, max_pos: float,
        start: int, end: int) -> dict:
    rets, equity, w_prev = [], [1.0], {}
    for t in range(start, end - 1):
        w = momentum.target_weights({s: xs[: t + 1] for s, xs in closes.items()}, p, regime, max_pos)
        turnover = sum(abs(w.get(s, 0) - w_prev.get(s, 0)) for s in set(w) | set(w_prev))
        r = sum(wt * (closes[s][t + 1] / closes[s][t] - 1) for s, wt in w.items()) - COST * turnover
        rets.append(r)
        equity.append(equity[-1] * (1 + r))
        w_prev = w
    return {"sharpe": sharpe(rets), "total_return": equity[-1] - 1,
            "max_drawdown": max_drawdown(equity), "days": len(rets)}
