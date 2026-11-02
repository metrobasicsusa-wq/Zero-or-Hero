"""Trend-filtered cross-sectional momentum. Shared by live trading and backtests."""

from __future__ import annotations

from hero import macro
from hero.indicators import momentum, rsi, sma

REGIME_SMA = 200
BEAR_EXPOSURE_SCALE = 0.3

# Optional exit / selection rules. All are off by default, so a config without them trades exactly as
# before; research/2026-11-02-momentum-fixes.md backtests them before any goes into config/strategy.json.
#   exit_buffer   a held name stays eligible while its close is within this fraction below the trend SMA
#                 (0.03: dropped only once 3% below the line, not on the first tick under it)
#   exit_confirm  a held name is dropped only after this many consecutive closes below the line (1 = today)
#   rank_buffer   a held name keeps its slot while ranked within top_n + rank_buffer (hysteresis)
#   group_cap     at most this many picks from one GROUPS bucket; 0 = no cap
GROUPS = {
    "大型科技 / AI": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "AVGO", "XLK", "QQQ"],
    "指数": ["SPY", "IWM", "DIA"],
    "金融": ["XLF", "JPM"],
    "能源": ["XLE"],
    "医疗": ["XLV"],
    "消费": ["XLY"],
    "工业": ["XLI"],
}
GROUP_OF = {s: g for g, ss in GROUPS.items() for s in ss}


def _below_line(xs: list[float], n: int, buffer: float, days: int) -> bool:
    """True when each of the last `days` closes is below its own SMA(n) by more than `buffer`."""
    for k in range(days):
        end = len(xs) - k
        m = sma(xs[:end], n)
        if m is None or xs[end - 1] > m * (1 - buffer):
            return False
    return True


def check(sym: str, xs: list[float], p: dict, held: frozenset) -> dict | None:
    """Indicators and the reasons a symbol fails the filters (empty list = eligible); None when the
    history is too short. One place, so rank() and scores() can never disagree."""
    mom = momentum(xs, p["momentum_lookback"])
    trend = sma(xs, p["trend_sma"])
    r = rsi(xs)
    if mom is None or trend is None or r is None:
        return None
    above = xs[-1] / trend - 1
    is_held = sym in held
    fail = []
    if is_held and (p.get("exit_buffer", 0) or p.get("exit_confirm", 1) > 1):
        days = max(1, int(p.get("exit_confirm", 1)))
        if _below_line(xs, p["trend_sma"], p.get("exit_buffer", 0), days):
            fail.append(f"连续 {days} 天跌破 {p['trend_sma']} 日均线下方 {p.get('exit_buffer', 0):.0%} 的缓冲（低 {-above:.1%}）"
                        if days > 1 else f"跌破 {p['trend_sma']} 日均线下方 {p.get('exit_buffer', 0):.0%} 的缓冲（低 {-above:.1%}）")
    elif above <= 0:
        fail.append(f"跌破 {p['trend_sma']} 日均线（低 {-above:.1%}）")
    if mom <= 0:
        fail.append(f"{p['momentum_lookback']} 日动量为负（{mom:+.1%}）")
    if r >= p["rsi_max"] and (not is_held or p.get("rsi_applies_to_holdings", True)):
        fail.append(f"RSI {r:.0f} 过热（上限 {p['rsi_max']}）")
    return {"momentum": round(mom, 4), "above_trend": round(above, 4), "rsi": round(r, 1),
            "eligible": not fail, "fail": fail}


def is_bullish(closes: list[float]) -> bool:
    m = sma(closes, REGIME_SMA)
    return m is None or closes[-1] > m


def rank(closes: dict[str, list[float]], p: dict, held: frozenset = frozenset()) -> list[str]:
    """Symbols passing the trend + overbought filters, strongest momentum first.

    With rsi_applies_to_holdings off, the overbought (RSI) filter only blocks new entries:
    a symbol already held is not dropped just for running hot. exit_buffer / exit_confirm
    likewise only soften the trend exit for names already held."""
    scored = []
    for sym, xs in closes.items():
        c = check(sym, xs, p, held)
        if c and c["eligible"]:
            scored.append((c["momentum"], sym))
    return [s for _, s in sorted(scored, reverse=True)]


def select(ranked: list[str], p: dict, held: frozenset = frozenset()) -> list[str]:
    """The top_n picks from a ranked list, with the optional hysteresis (rank_buffer) and group cap.
    With both off this is ranked[:top_n]."""
    top_n, cap = p["top_n"], int(p.get("group_cap", 0) or 0)
    keep = {s for i, s in enumerate(ranked) if s in held and i < top_n + int(p.get("rank_buffer", 0) or 0)}
    picks, per_group = [], {}
    for pass_held in (True, False):
        for s in ranked:
            if len(picks) >= top_n:
                break
            if (s in keep) != pass_held or s in picks:
                continue
            g = GROUP_OF.get(s)
            if cap and g and per_group.get(g, 0) >= cap:
                continue
            picks.append(s)
            if g:
                per_group[g] = per_group.get(g, 0) + 1
    return sorted(picks, key=ranked.index)


def target_weights(closes: dict[str, list[float]], p: dict, regime_symbol: str,
                   max_position_pct: float, held: frozenset = frozenset(),
                   macro_closes: dict[str, list[float]] | None = None) -> dict[str, float]:
    picks = select(rank(closes, p, frozenset(held)), p, frozenset(held))
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
    out = {}
    for sym, xs in closes.items():
        out[sym] = check(sym, xs, p, held) or {"eligible": False, "fail": ["数据不足"]}
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
    if p.get("group_cap") and s.get("rank", 0) <= p["top_n"] + int(p.get("rank_buffer", 0) or 0):
        return f"动量排名第 {s['rank']}，但{GROUP_OF.get(sym, '同组')}已有 {p['group_cap']} 只"
    return f"动量排名第 {s['rank']}，只持有前 {p['top_n']} 名"
