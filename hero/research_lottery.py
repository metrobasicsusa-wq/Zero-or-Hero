"""Research step 3: what would buying out-of-the-money calls before earnings actually have paid?

Uses the events from research/*-earnings-reactions.json (SEC 8-K item 2.02 timing). For each
event with option history (from 2024-02) it buys one call at the close of the day before the
reaction, for the first expiry on or after the reaction day, at several strikes:
  moneyness +0% / +5% / +10% / +15% / +20% above the stock price, and
  1x / 1.5x / 2x the options-implied move above it.
Exits: sell at the reaction day's close, or hold to expiry (intrinsic value at that close).
Option prices are daily closes (last trades), which for cheap contracts sit inside a wide
spread, so a cost of paying 10% above and selling 10% below the close is applied on top
(at least one cent). A contract with no trade on the entry day is skipped.
Also replays a $500 account betting 10% of its balance on each event in date order.
Record only. Run: python -m hero.research_lottery
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

SLIP = 0.10
MONEYNESS = (0.0, 0.05, 0.10, 0.15, 0.20)
IMPLIED_MULT = (1.0, 1.5, 2.0)
BET = 0.10


def entry_cost(px: float) -> float:
    return px + max(px * SLIP, 0.01)


def exit_value(px: float) -> float:
    return max(px - max(px * SLIP, 0.01), 0.0)


def pick_strike(strikes: list[float], target: float) -> float | None:
    above = [k for k in strikes if k >= target]
    return min(above) if above else None


def events_file(root: Path) -> Path:
    files = sorted((root / "research").glob("*-earnings-reactions.json"))
    if not files:
        raise SystemExit("run python -m hero.research_earnings first")
    return files[-1]


def strategies(ev: dict) -> dict[str, float]:
    """Target strike per strategy (as a price), from the spot at entry."""
    spot = ev["spot"]
    out = {f"价外 {int(m * 100)}%": spot * (1 + m) for m in MONEYNESS}
    for k in IMPLIED_MULT:
        out[f"预期波动 {k:g} 倍处"] = spot * (1 + k * ev["implied_move"])
    return out


def run(client, root: Path) -> dict:
    src = json.loads(events_file(root).read_text())
    events = [e for e in src["events"] if e.get("implied_move") and e.get("expiry")]
    syms = sorted({e["symbol"] for e in events})
    raw: dict[str, dict[str, float]] = {}
    start = min(e["reaction_day"] for e in events)
    start = (date.fromisoformat(start) - timedelta(days=10)).isoformat()
    for i in range(0, len(syms), 100):
        for s, bars in client.daily_bars(syms[i:i + 100], start, adjustment="raw").items():
            raw[s] = {b["t"][:10]: float(b["c"]) for b in bars}
    trades = []
    for e in events:
        closes = raw.get(e["symbol"], {})
        days = sorted(closes)
        r = e["reaction_day"]
        if r not in closes:
            continue
        i = days.index(r)
        if i == 0:
            continue
        entry_day, spot = days[i - 1], closes[days[i - 1]]
        exp = e["expiry"]
        exp_close = closes.get(exp) or (closes[max(d for d in days if d <= exp)] if any(d <= exp for d in days) else None)
        ev = {**e, "spot": spot}
        targets = strategies(ev)
        try:
            cs = []
            for status in ("inactive", "active"):
                cs = client.option_contracts(e["symbol"], status=status, type="call", expiration_date=exp,
                                             strike_price_gte=f"{spot:.2f}",
                                             strike_price_lte=f"{spot * (1 + max(max(MONEYNESS), 2 * e['implied_move'])) * 1.05:.2f}")
                if cs:
                    break
        except Exception:
            continue
        by_strike = {float(c["strike_price"]): c["symbol"] for c in cs}
        chosen = {name: pick_strike(sorted(by_strike), t) for name, t in targets.items()}
        wanted = sorted({by_strike[k] for k in chosen.values() if k is not None})
        if not wanted:
            continue
        try:
            bars = client.option_bars(wanted, entry_day, r)
        except Exception:
            continue
        time.sleep(0.2)
        px = {sym: {b["t"][:10]: float(b["c"]) for b in bs} for sym, bs in bars.items()}
        for name, k in chosen.items():
            if k is None:
                continue
            sym = by_strike[k]
            buy = px.get(sym, {}).get(entry_day)
            if not buy:
                continue  # no trade at entry: cannot assume a price
            cost = entry_cost(buy)
            sell_r = px.get(sym, {}).get(r)
            # No trade on the reaction day: fall back to intrinsic value (a floor; the real bid could be higher).
            val_r = exit_value(sell_r) if sell_r is not None else max(closes[r] - k, 0.0)
            val_exp = max((exp_close or 0) - k, 0.0) if exp_close is not None else None
            trades.append({"symbol": e["symbol"], "reaction_day": r, "strategy": name, "strike": k, "spot": spot,
                           "entry_close": buy, "cost": round(cost, 4), "move": e["move"],
                           "ret_reaction": round(val_r / cost - 1, 4),
                           "ret_expiry": round(val_exp / cost - 1, 4) if val_exp is not None else None,
                           "implied_move": e["implied_move"], "cheapness": e.get("cheapness"), "mom126": e.get("mom126")})
    return {"generated": date.today().isoformat(), "source": events_file(root).name, "events": len(events),
            "slippage": SLIP, "bet": BET, "trades": trades}


def summarize(trades: list[dict]) -> dict:
    out = {}
    for name in dict.fromkeys(t["strategy"] for t in trades):
        rows = sorted([t for t in trades if t["strategy"] == name], key=lambda t: t["reaction_day"])
        res = {}
        for exit_key in ("ret_reaction", "ret_expiry"):
            rets = [t[exit_key] for t in rows if t[exit_key] is not None]
            if not rets:
                continue
            bank = 500.0
            for x in rets:  # bet 10% of the balance on each event, in date order
                bank += bank * BET * x
            res[exit_key] = {"n": len(rets), "hit_rate": round(sum(x > 0 for x in rets) / len(rets), 3),
                             "avg": round(statistics.mean(rets), 3), "median": round(statistics.median(rets), 3),
                             "best": round(max(rets), 1), "share_10x": round(sum(x >= 9 for x in rets) / len(rets), 3),
                             "bank_500": round(bank, 0)}
        out[name] = res
    return out


def markdown(rep: dict, s: dict) -> str:
    out = [f"# 财报彩票期权真实盈亏（第 3 步）{rep['generated']}", "",
           f"事件来自 {rep['source']}（有期权数据的 {rep['events']} 次）。反应日前一天收盘买入一张看涨期权（到期日 = 反应日当天或之后最近的一个），"
           f"两种退出：反应日收盘卖出 / 持有到期。价格用日线收盘价，另加买卖各 {rep['slippage']:.0%} 的价差成本（至少 1 美分）。"
           f"「$500 账户」= 按时间顺序每次押当前余额的 {rep['bet']:.0%}。只研究，不改交易。", ""]
    for exit_key, label in (("ret_reaction", "反应日收盘卖出"), ("ret_expiry", "持有到期")):
        out += [f"## {label}", "", "| 行权价 | 次数 | 赚钱比例 | 平均每 $1 回报 | 中位数 | 最大倍数 | ≥10 倍的比例 | $500 账户最后 |",
                "|---|---|---|---|---|---|---|---|"]
        for name, res in s.items():
            r = res.get(exit_key)
            if not r:
                continue
            out.append(f"| {name} | {r['n']} | {r['hit_rate']:.0%} | {r['avg']:+.0%} | {r['median']:+.0%} | "
                       f"{r['best'] + 1:.0f}× | {r['share_10x']:.1%} | ${r['bank_500']:,.0f} |")
        out.append("")
    return "\n".join(out)


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca(), root)
    s = summarize(rep["trades"])
    out = root / "research"
    (out / f"{rep['generated']}-earnings-lottery.json").write_text(
        json.dumps({**rep, "summary": s}, indent=1, ensure_ascii=False) + "\n")
    (out / f"{rep['generated']}-earnings-lottery.md").write_text(markdown(rep, s))
    print(markdown(rep, s))


if __name__ == "__main__":
    sys.exit(main())
