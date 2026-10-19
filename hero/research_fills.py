"""Real option fills against what the backtests assume (owner 2026-10-19).

The studies price an option at its minute (or daily) bar close and pay a fixed cost on top: 10% each way for
same-day trading (research_0dte.SLIP), 5% for liquid month-out contracts (research_event_turn.SLIP_M). This
study checks those numbers against the fills both paper accounts actually got (journal*/fills.jsonl):

- every option order's average fill price, against the close of the last minute bar before the fill (what a
  backtest entering at that minute would have used; the fill's own minute is left out because our own trade
  is in it). Bars older than STALE minutes are not used: a thin contract's last trade can be far from the market.
- cost per side: a buy that paid 1.10 against a 1.00 bar cost 10%; a sell that got 0.90 against 1.00 cost 10%.
- split by same-day / short / longer expiry and by price, since a one-cent tick is 10% of a $0.10 contract.
- the main account also logs the quote (bid / ask / mid) with each option order: fills against that mid.

Record only; nothing here changes a rule. Run: python -m hero.research_fills (needs the Alpaca keys: the
research workflow, study "fills").
"""

from __future__ import annotations

import json
import statistics
import sys
from datetime import date, datetime
from pathlib import Path

from hero.research_0dte import ET, SLIP, et_minutes
from hero.research_event_turn import SLIP_M, Pace
from hero.research_ideas import save

ROOT = Path(__file__).resolve().parent.parent
ACCOUNTS = {"主账户": "journal", "多账本": "journal-s500"}
STALE = 10    # minutes: an older last bar is not a price
BATCH = 50    # option symbols per bars request
BUCKETS = ((0.10, "$0.10 以下"), (0.50, "$0.10–0.50"), (2.00, "$0.50–2"), (float("inf"), "$2 以上"))


def is_option(sym: str) -> bool:
    return len(sym) > 15 and sym[-9] in "CP"


def expiry(sym: str) -> str:
    """OCC symbol -> expiry date, e.g. SPY261019P00762000 -> 2026-10-19."""
    d = sym[-15:-9]
    return f"20{d[:2]}-{d[2:4]}-{d[4:]}"


def load_orders(root: Path = ROOT) -> list[dict]:
    """Option fills grouped into orders: one row per (account, order) with the qty-weighted average price.
    Fills from before order keys were logged are their own order."""
    out: dict[tuple, dict] = {}
    for acct, folder in ACCOUNTS.items():
        path = root / folder / "fills.jsonl"
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            f = json.loads(line)
            if not is_option(f.get("symbol", "")):
                continue
            key = (acct, f.get("order_key") or f["execution_key"])
            qty, px = float(f["qty"]), float(f["price"])
            o = out.setdefault(key, {"account": acct, "order_key": key[1], "symbol": f["symbol"], "side": f["side"],
                                     "qty": 0.0, "value": 0.0, "time": f["transaction_time"]})
            o["qty"] += qty
            o["value"] += qty * px
            o["time"] = min(o["time"], f["transaction_time"])
    rows = []
    for o in out.values():
        t = datetime.fromisoformat(o["time"].replace("Z", "+00:00")).astimezone(ET)
        rows.append({**{k: v for k, v in o.items() if k != "value"}, "price": round(o["value"] / o["qty"], 4),
                     "day": t.date().isoformat(), "hhmm": t.strftime("%H:%M"), "expiry": expiry(o["symbol"])})
    return sorted(rows, key=lambda r: r["time"])


def logged_mids(root: Path = ROOT) -> dict[str, dict]:
    """Main account: order_key -> the quote logged with the order (bid / ask / mid)."""
    path = root / "journal" / "trades.jsonl"
    if not path.exists():
        return {}
    ev = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    ack = {e["intent_key"]: e["order_key"] for e in ev if e.get("kind") == "order_ack" and e.get("order_key")}
    out = {}
    for e in ev:
        q = e.get("evidence") or {}
        if e.get("kind") == "order" and q.get("mid") and e.get("intent_key") in ack:
            out[ack[e["intent_key"]]] = {"bid": q.get("bid"), "ask": q.get("ask"), "mid": q["mid"]}
    return out


def reference(mins: dict[str, dict], hhmm: str) -> tuple[float, int] | None:
    """Close of the last minute bar before `hhmm` and its age in minutes, if no older than STALE."""
    keys = [k for k in mins if k < hhmm]
    if not keys:
        return None
    k = max(keys)
    age = (int(hhmm[:2]) * 60 + int(hhmm[3:])) - (int(k[:2]) * 60 + int(k[3:]))
    if age > STALE:
        return None
    return float(mins[k]["c"]), age


def side_cost(side: str, fill: float, ref: float) -> float:
    """What the fill cost against the reference, as a share of it (positive = worse than the reference)."""
    return fill / ref - 1 if side == "buy" else 1 - fill / ref


def horizon(r: dict) -> str:
    days = (date.fromisoformat(r["expiry"]) - date.fromisoformat(r["day"])).days
    return "当天到期" if days <= 0 else "1–7 天到期" if days <= 7 else "8 天以上到期"


def bucket(ref: float) -> str:
    return next(name for top, name in BUCKETS if ref < top)


def run(client, root: Path = ROOT) -> dict:
    orders = load_orders(root)
    mids = logged_mids(root)
    by_day: dict[str, set[str]] = {}
    for o in orders:
        by_day.setdefault(o["day"], set()).add(o["symbol"])
    bars: dict[tuple[str, str], dict[str, dict]] = {}
    pace = Pace(client)
    for day, syms in sorted(by_day.items()):
        syms = sorted(syms)
        for i in range(0, len(syms), BATCH):
            try:
                got = client.option_bars(syms[i:i + BATCH], f"{day}T13:00:00Z", f"{day}T21:00:00Z", timeframe="1Min")
            except Exception:
                got = {}
            for s in syms[i:i + BATCH]:
                bars[(day, s)] = et_minutes(got.get(s, []))
            pace.wait()
    rows = []
    for o in orders:
        ref = reference(bars.get((o["day"], o["symbol"]), {}), o["hhmm"])
        row = {**o, "horizon": horizon(o)}
        if ref:
            row.update(ref=ref[0], age=ref[1], cost=round(side_cost(o["side"], o["price"], ref[0]), 4),
                       cents=round((o["price"] - ref[0]) * (1 if o["side"] == "buy" else -1), 4), bucket=bucket(ref[0]))
        q = mids.get(o["order_key"])
        if q:
            row.update(quote=q, vs_mid=round(side_cost(o["side"], o["price"], q["mid"]), 4))
        rows.append(row)
    return {"generated": date.today().isoformat(), "orders": len(orders),
            "first": orders[0]["day"] if orders else None, "last": orders[-1]["day"] if orders else None, "rows": rows}


def cell(rs: list[dict]) -> str:
    v = [r["cost"] for r in rs]
    if not v:
        return "| — | — | — | — | — |"
    c = statistics.median(r["cents"] for r in rs)
    return (f"| {len(v)} | {statistics.median(v):+.1%} | {statistics.mean(v):+.1%} | "
            f"{sum(x > SLIP for x in v) / len(v):.0%} | {c * 100:+.1f}¢ |")


def report(rep: dict) -> str:
    rows = rep["rows"]
    priced = [r for r in rows if "cost" in r]
    head = ["# 真实成交 vs 回测的成本假设 " + rep["generated"], "",
            f"{rep['first']} 至 {rep['last']}，两个模拟账户的 {rep['orders']} 笔期权订单（同一订单的分笔成交合并）。"
            f"参考价 = 成交前最后一根分钟线的收盘价（{STALE} 分钟内，自己成交的那一分钟不算）；"
            f"有参考价的 {len(priced)} 笔。每边成本：买入比参考价多付、卖出比参考价少拿的比例，正数 = 比回测用的价格差。"
            f"回测现在假设：当天交易每边 {SLIP:.0%}，一个月左右的流动性好的合约每边 {SLIP_M:.0%}。只研究，不改交易。", ""]
    out = head
    for title, key, groups in (("按到期", "horizon", ("当天到期", "1–7 天到期", "8 天以上到期")),
                               ("按参考价", "bucket", tuple(n for _, n in BUCKETS))):
        out += [f"## {title}", "",
                f"| 方向 | {title[1:]} | 笔数 | 中位数 | 平均 | 差过 {SLIP:.0%} 的比例 | 中位数（每张差几美分 ÷ 100 股） |",
                "|---|---|---|---|---|---|---|"]
        for side, name in (("buy", "买入"), ("sell", "卖出")):
            for g in groups:
                rs = [r for r in priced if r["side"] == side and r[key] == g]
                if rs:
                    out.append(f"| {name} | {g} " + cell(rs))
            rs = [r for r in priced if r["side"] == side]
            if rs:
                out.append(f"| {name} | 全部 " + cell(rs))
        out.append("")
    quoted = [r for r in rows if "vs_mid" in r]
    if quoted:
        out += ["## 主账户：成交价对下单时的中间价", "",
                "主账户下期权单时记下了买价、卖价和中间价（限价挂在中间价附近）。", "",
                "| 合约 | 方向 | 买价 / 卖价 | 中间价 | 成交 | 比中间价差 |", "|---|---|---|---|---|---|"]
        for r in quoted:
            q = r["quote"]
            out.append(f"| {r['symbol']} | {'买入' if r['side'] == 'buy' else '卖出'} | {q['bid']} / {q['ask']} | "
                       f"{q['mid']} | {r['price']} | {r['vs_mid']:+.1%} |")
        out.append("")
    missing = len(rows) - len(priced)
    out += ["## 说明", "",
            f"- 没有参考价的 {missing} 笔：成交前 {STALE} 分钟内没有成交（冷门合约），或者取不到分钟线。",
            "- 卖不出去时挂 $0.01 限价卖的单（没有买价）也算在里面，便宜合约那一档会因此偏差。",
            "- 从 2026-10-19 起，多账本的买单记下单时的卖价、卖单记下单时的买价，以后可以直接对报价算。"]
    return "\n".join(out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    rep = run(Alpaca())
    save("fills", rep, report(rep))


if __name__ == "__main__":
    sys.exit(main())
