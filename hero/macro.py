"""Price-based macro gauges: long Treasuries, oil, volatility and the dollar, read from ETFs.

Each gauge raises a flag on a sharp move. With enough flags up the stock book can be scaled
down (stocks.macro_scale < 1); at 1.0 the gauges are only recorded. Evolution tests both.
"""

from __future__ import annotations

from hero.indicators import momentum, sma

SYMBOLS = {"rates": "TLT", "oil": "USO", "fear": "VIXY", "dollar": "UUP"}
LOOKBACK = 20
MIN_FLAGS = 2  # one gauge alone is noise; two together is a regime


def gauges(closes: dict[str, list[float]]) -> dict[str, dict]:
    """Per gauge: the 20-day move, whether it is flagged, and a plain-language reading."""
    out = {}
    for name, sym in SYMBOLS.items():
        xs = closes.get(sym)
        if not xs:
            continue
        if name == "fear":
            m = sma(xs, LOOKBACK)
            if m is None:
                continue
            v = xs[-1] / m - 1
            flag = v >= 0.15
            text = f"恐慌指数（{sym}）比 20 日均值{'高' if v >= 0 else '低'} {abs(v):.0%}"
        else:
            v = momentum(xs, LOOKBACK)
            if v is None:
                continue
            if name == "rates":
                flag = v <= -0.04  # long bonds falling fast = long yields rising fast
                text = f"长期美债（{sym}）20 日 {v:+.1%}" + ("，利率快速上升" if flag else "")
            elif name == "oil":
                flag = v >= 0.15
                text = f"油价（{sym}）20 日 {v:+.1%}" + ("，急涨" if flag else "")
            else:
                flag = v >= 0.03
                text = f"美元（{sym}）20 日 {v:+.1%}" + ("，走强" if flag else "")
        out[name] = {"symbol": sym, "value": round(v, 4), "flag": flag, "text": text}
    return out


def risk_off(g: dict[str, dict]) -> bool:
    return sum(x["flag"] for x in g.values()) >= MIN_FLAGS


def summary(g: dict[str, dict]) -> str:
    if not g:
        return "宏观数据不足"
    up = [x["text"] for x in g.values() if x["flag"]]
    head = f"宏观警报 {len(up)}/{len(g)}"
    return head + ("：" + "；".join(up) if up else "：无")
