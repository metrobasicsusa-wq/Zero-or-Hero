"""Rule-based health checks on the latest snapshot. Returns human-readable problems."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

STALE_MINUTES = 40
DAY_LOSS_ALERT = -0.025
POSITION_LOSS_ALERT = -0.35
ORDER_STUCK_HOURS = 2


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def check(snapshot: dict | None, market_open: bool, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    if not snapshot:
        return ["没有快照数据，机器人可能从未成功运行"]
    problems = []
    age = (now - _ts(snapshot["ts"])).total_seconds() / 60
    if market_open and age > STALE_MINUTES:
        problems.append(f"快照已 {age:.0f} 分钟未更新，定时交易可能没在跑")

    acct = snapshot.get("account") or {}
    equity, last = float(acct.get("equity") or 0), float(acct.get("last_equity") or 0)
    if last and equity / last - 1 <= DAY_LOSS_ALERT:
        problems.append(f"当日亏损 {equity / last - 1:.2%}（净值 ${equity:,.0f}）")

    for p in snapshot.get("positions") or []:
        pl = float(p.get("unrealized_plpc") or 0)
        if pl <= POSITION_LOSS_ALERT:
            problems.append(f"{p['symbol']} 亏损 {pl:.0%}，接近或超过止损线")

    orders = snapshot.get("open_orders") or []
    protected = {o["symbol"] for o in orders if o.get("protective_stop")}
    for p in snapshot.get("positions") or []:
        if p.get("asset_class") == "us_equity" and p["symbol"] not in protected:
            problems.append(f"{p['symbol']} 没有券商端保护性止损单")

    for o in orders:
        if o.get("protective_stop"):
            continue  # resting GTC stops are meant to stay open
        hours = (now - _ts(o["submitted_at"])).total_seconds() / 3600
        if o.get("status") in ("new", "accepted", "partially_filled") and hours >= ORDER_STUCK_HOURS:
            problems.append(f"{o['symbol']} {o['side']} 挂单 {hours:.1f} 小时未完全成交（{o['status']}）")
    return problems


def load(journal: Path) -> dict | None:
    path = journal / "snapshot.json"
    return json.loads(path.read_text()) if path.exists() else None
