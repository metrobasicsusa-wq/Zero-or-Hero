"""Broker-side protective stops for stock positions.

Each long stock position carries one GTC sell-stop order resting at the broker, so a crash
between trading cycles (or while the bot is not running at all) is still cut off. These are
catastrophe stops sized to each stock's own volatility; normal exits stay with the daily
rebalance. Our stops are recognised by their client_order_id prefix.
"""

from __future__ import annotations

import math

from hero.indicators import realized_vol

PREFIX = "hero-stop-"
VOL_WINDOW = 20


def is_protective_stop(order: dict) -> bool:
    return (order.get("client_order_id") or "").startswith(PREFIX)


def stop_pct(closes: list[float] | None, risk: dict) -> tuple[float, float | None]:
    """Stop distance as a fraction of price, and the daily volatility it was based on."""
    vol = realized_vol(closes, VOL_WINDOW) if closes else None
    if vol is None:
        return risk["stop_max_pct"], None
    daily = vol / math.sqrt(252)
    return min(max(risk["stop_vol_mult"] * daily, risk["stop_min_pct"]), risk["stop_max_pct"]), daily


def stop_price(avg_entry: float, current: float, pct: float) -> float:
    """Below both the entry and the current price, so a fresh stop never triggers on placement."""
    return round(min(avg_entry, current) * (1 - pct), 2)
