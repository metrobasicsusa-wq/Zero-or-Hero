"""Trend-filtered cross-sectional momentum. Shared by live trading and backtests."""

from __future__ import annotations

from hero.indicators import momentum, rsi, sma

REGIME_SMA = 200
BEAR_EXPOSURE_SCALE = 0.3


def is_bullish(closes: list[float]) -> bool:
    m = sma(closes, REGIME_SMA)
    return m is None or closes[-1] > m


def rank(closes: dict[str, list[float]], p: dict, held: frozenset = frozenset()) -> list[str]:
    """Symbols passing the trend + overbought filters, strongest momentum first.

    With rsi_applies_to_holdings off, the overbought (RSI) filter only blocks new entries:
    a symbol already held is not dropped just for running hot."""
    rsi_on_held = p.get("rsi_applies_to_holdings", True)
    scored = []
    for sym, xs in closes.items():
        mom = momentum(xs, p["momentum_lookback"])
        trend = sma(xs, p["trend_sma"])
        r = rsi(xs)
        if mom is None or trend is None or r is None:
            continue
        cool_enough = r < p["rsi_max"] or (sym in held and not rsi_on_held)
        if xs[-1] > trend and mom > 0 and cool_enough:
            scored.append((mom, sym))
    return [s for _, s in sorted(scored, reverse=True)]


def target_weights(closes: dict[str, list[float]], p: dict, regime_symbol: str,
                   max_position_pct: float, held: frozenset = frozenset()) -> dict[str, float]:
    picks = rank(closes, p, frozenset(held))[: p["top_n"]]
    if not picks:
        return {}
    gross = p["gross_exposure"]
    if regime_symbol in closes and not is_bullish(closes[regime_symbol]):
        gross *= BEAR_EXPOSURE_SCALE
    w = min(gross / len(picks), max_position_pct)
    return {s: w for s in picks}
