"""Pure-python indicators over lists of floats (oldest first)."""

from __future__ import annotations

import math


def sma(xs: list[float], n: int) -> float | None:
    return sum(xs[-n:]) / n if len(xs) >= n else None


def momentum(xs: list[float], n: int) -> float | None:
    return xs[-1] / xs[-n - 1] - 1 if len(xs) > n else None


def rsi(xs: list[float], n: int = 14) -> float | None:
    if len(xs) <= n:
        return None
    gains = losses = 0.0
    for a, b in zip(xs[-n - 1:-1], xs[-n:]):
        d = b - a
        gains += max(d, 0)
        losses += max(-d, 0)
    if losses == 0:
        return 100.0
    return 100 - 100 / (1 + gains / losses)


def realized_vol(xs: list[float], n: int = 20) -> float | None:
    if len(xs) <= n:
        return None
    rets = [math.log(b / a) for a, b in zip(xs[-n - 1:-1], xs[-n:])]
    mu = sum(rets) / n
    return math.sqrt(sum((r - mu) ** 2 for r in rets) / (n - 1) * 252)


def sharpe(daily_returns: list[float]) -> float:
    n = len(daily_returns)
    if n < 2:
        return 0.0
    mu = sum(daily_returns) / n
    sd = math.sqrt(sum((r - mu) ** 2 for r in daily_returns) / (n - 1))
    return 0.0 if sd == 0 else mu / sd * math.sqrt(252)


def max_drawdown(equity: list[float]) -> float:
    peak, mdd = -math.inf, 0.0
    for e in equity:
        peak = max(peak, e)
        mdd = min(mdd, e / peak - 1)
    return mdd
