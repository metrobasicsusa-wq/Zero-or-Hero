"""Trend-filtered cross-sectional momentum. Shared by live trading and backtests."""

from __future__ import annotations

from hero import macro
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
                   max_position_pct: float, held: frozenset = frozenset(),
                   macro_closes: dict[str, list[float]] | None = None) -> dict[str, float]:
    picks = rank(closes, p, frozenset(held))[: p["top_n"]]
    if not picks:
        return {}
    gross = p["gross_exposure"]
    if regime_symbol in closes and not is_bullish(closes[regime_symbol]):
        gross *= BEAR_EXPOSURE_SCALE
    if macro_closes and p.get("macro_scale", 1.0) < 1.0 and macro.risk_off(macro.gauges(macro_closes)):
        gross *= p["macro_scale"]
    w = min(gross / len(picks), max_position_pct)
    return {s: w for s in picks}


def scores(closes: dict[str, list[float]], p: dict, held: frozenset = frozenset()) -> dict[str, dict]:
    """Per-symbol indicators, eligibility and momentum rank: the evidence behind rank()."""
    rsi_on_held = p.get("rsi_applies_to_holdings", True)
    out = {}
    for sym, xs in closes.items():
        mom = momentum(xs, p["momentum_lookback"])
        trend = sma(xs, p["trend_sma"])
        r = rsi(xs)
        if mom is None or trend is None or r is None:
            out[sym] = {"eligible": False, "fail": ["数据不足"]}
            continue
        above = xs[-1] / trend - 1
        fail = []
        if above <= 0:
            fail.append(f"跌破 {p['trend_sma']} 日均线（低 {-above:.1%}）")
        if mom <= 0:
            fail.append(f"{p['momentum_lookback']} 日动量为负（{mom:+.1%}）")
        if r >= p["rsi_max"] and (sym not in held or rsi_on_held):
            fail.append(f"RSI {r:.0f} 过热（上限 {p['rsi_max']}）")
        out[sym] = {"momentum": round(mom, 4), "above_trend": round(above, 4), "rsi": round(r, 1),
                    "eligible": not fail, "fail": fail}
    ranked = rank(closes, p, held)
    for i, sym in enumerate(ranked, 1):
        out[sym]["rank"] = i
    return out


def why_selected(sym: str, s: dict, p: dict) -> str:
    return (f"动量排名第 {s['rank']}/{p['top_n']}：{p['momentum_lookback']} 日涨 {s['momentum']:+.1%}，"
            f"高于 {p['trend_sma']} 日均线 {s['above_trend']:.1%}，RSI {s['rsi']:.0f}")


def why_dropped(sym: str, s: dict | None, p: dict) -> str:
    if not s:
        return "不在股票池或没有行情数据"
    if s.get("fail"):
        return "；".join(s["fail"])
    return f"动量排名第 {s['rank']}，只持有前 {p['top_n']} 名"
