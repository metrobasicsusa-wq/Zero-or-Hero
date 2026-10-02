"""Research: does a surge in speculative call volume, together with a price breakout, predict
further gains (the Meta-September-2026 pattern), and would buying calls on it have paid?

Universe: the 50 most traded stocks of this month's dynamic pool (today's list, so it leans
towards today's winners; the comparison between signal days and other days of the same stocks
is what matters). Period: option history from 2024-02.

Daily call volume per stock = sum of volume over calls 0-20% above the month's opening price,
expiring within 35 days (where speculative buying shows up). Signal day: that volume is at least
SURGE x its previous 20-day average AND the stock closes at a 20-day high. Compared with all
days and with breakout days without a surge: the stock's next 5 / 10 / 21-day returns.
Then the real option trade on each signal: buy the call closest to 7.5% out of the money with
25-50 days left at the signal-day close, sell 21 trading days later (or at the last close before
expiry), paying 10% spread each way (at least one cent).
Record only. Run: python -m hero.research_flow
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

START = "2024-02-01"
POOL = 50
SURGE = 3.0
LOOKBACK = 20
HORIZONS = (5, 10, 21)
SLIP = 0.10


def months(start: str, end: str) -> list[tuple[str, str]]:
    out, d = [], date.fromisoformat(start).replace(day=1)
    stop = date.fromisoformat(end)
    while d <= stop:
        nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        out.append((d.isoformat(), min(nxt - timedelta(days=1), stop).isoformat()))
        d = nxt
    return out


def contracts(client, sym: str, **kw) -> list[dict]:
    out = []
    for status in ("inactive", "active"):
        try:
            out += client.option_contracts(sym, status=status, type="call", **kw)
        except Exception:
            pass
    return out


def call_volume(client, sym: str, raw: dict[str, float], end: str) -> dict[str, float]:
    """Daily volume of near-term calls 0-20% out of the money, month by month."""
    vol: dict[str, float] = {}
    days = sorted(raw)
    for m0, m1 in months(START, end):
        first = next((d for d in days if d >= m0), None)
        if first is None or first > m1:
            continue
        spot = raw[first]
        cs = contracts(client, sym, expiration_date_gte=m0,
                       expiration_date_lte=(date.fromisoformat(m1) + timedelta(days=35)).isoformat(),
                       strike_price_gte=f"{spot:.2f}", strike_price_lte=f"{spot * 1.2:.2f}")
        syms = sorted({c["symbol"] for c in cs})
        for i in range(0, len(syms), 100):
            try:
                bars = client.option_bars(syms[i:i + 100], m0, m1)
            except Exception:
                continue
            for bs in bars.values():
                for b in bs:
                    d = b["t"][:10]
                    vol[d] = vol.get(d, 0.0) + float(b.get("v") or 0)
            time.sleep(0.1)
    return vol


def signals(dates: list[str], closes: list[float], vol: dict[str, float]) -> tuple[list[int], list[int]]:
    """(surge+breakout days, breakout-without-surge days) as indices."""
    sig, brk = [], []
    for i in range(LOOKBACK, len(dates) - 1):
        hist = [vol.get(d, 0.0) for d in dates[i - LOOKBACK: i]]
        avg = sum(hist) / LOOKBACK
        breakout = closes[i] >= max(closes[i - LOOKBACK: i + 1])
        if not breakout:
            continue
        if avg > 0 and vol.get(dates[i], 0.0) >= SURGE * avg:
            sig.append(i)
        else:
            brk.append(i)
    return sig, brk


def forward(closes: list[float], idx: list[int], h: int) -> list[float]:
    return [closes[i + h] / closes[i] - 1 for i in idx if i + h < len(closes)]


def option_trade(client, sym: str, day: str, exit_day: str, spot: float) -> dict | None:
    lo, hi = (date.fromisoformat(day) + timedelta(days=25)).isoformat(), (date.fromisoformat(day) + timedelta(days=50)).isoformat()
    cs = contracts(client, sym, expiration_date_gte=lo, expiration_date_lte=hi,
                   strike_price_gte=f"{spot * 1.03:.2f}", strike_price_lte=f"{spot * 1.15:.2f}")
    if not cs:
        return None
    c = min(cs, key=lambda c: (abs(float(c["strike_price"]) / spot - 1.075), c["expiration_date"]))
    end = min(exit_day, c["expiration_date"])
    try:
        bars = client.option_bars([c["symbol"]], day, end).get(c["symbol"], [])
    except Exception:
        return None
    px = {b["t"][:10]: float(b["c"]) for b in bars}
    if day not in px:
        return None
    buy = px[day] + max(px[day] * SLIP, 0.01)
    last = max(d for d in px if d <= end)
    sell = max(px[last] - max(px[last] * SLIP, 0.01), 0.0)
    return {"contract_strike": float(c["strike_price"]), "expiry": c["expiration_date"], "buy": round(buy, 3),
            "sell": round(sell, 3), "exit_day": last, "ret": round(sell / buy - 1, 3)}


def stats(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "mean": round(statistics.mean(xs), 4), "median": round(statistics.median(xs), 4),
            "win": round(sum(x > 0 for x in xs) / len(xs), 3)}


def run(client, root: Path) -> dict:
    from hero import universe
    pool = universe.load(root / "data" / "universe.json")["stocks"][:POOL]
    start = (date.fromisoformat(START) - timedelta(days=60)).isoformat()
    today = date.today().isoformat()
    adj, raw = {}, {}
    for i in range(0, len(pool), 50):
        adj.update(client.daily_bars(pool[i:i + 50], start))
        raw.update(client.daily_bars(pool[i:i + 50], start, adjustment="raw"))
    rows, sig_all, brk_all, base_all, trades = [], {h: [] for h in HORIZONS}, {h: [] for h in HORIZONS}, \
        {h: [] for h in HORIZONS}, []
    for sym in pool:
        if sym not in adj or sym not in raw:
            continue
        dates = [b["t"][:10] for b in adj[sym]]
        closes = [float(b["c"]) for b in adj[sym]]
        rawc = {b["t"][:10]: float(b["c"]) for b in raw[sym]}
        vol = call_volume(client, sym, rawc, today)
        sig, brk = signals(dates, closes, vol)
        everyday = [i for i in range(LOOKBACK, len(dates)) if dates[i] >= START]
        sig = [i for i in sig if dates[i] >= START]
        brk = [i for i in brk if dates[i] >= START]
        for h in HORIZONS:
            sig_all[h] += forward(closes, sig, h)
            brk_all[h] += forward(closes, brk, h)
            base_all[h] += forward(closes, everyday, h)
        last_trade = None
        for i in sig:
            if last_trade is not None and i - last_trade < 21:
                continue  # one trade per signal cluster
            if i + 21 >= len(dates) or dates[i] not in rawc:
                continue
            t = option_trade(client, sym, dates[i], dates[i + 21], rawc[dates[i]])
            if t:
                trades.append({"symbol": sym, "day": dates[i], "stock_21d": round(closes[i + 21] / closes[i] - 1, 4), **t})
                last_trade = i
        rows.append({"symbol": sym, "signals": len(sig), "breakouts": len(brk)})
    rets = [t["ret"] for t in trades]
    bank = 500.0
    for t in sorted(trades, key=lambda t: t["day"]):
        bank += bank * 0.10 * t["ret"]
    return {"generated": today, "pool": len(rows), "surge": SURGE,
            "forward": {str(h): {"signal": stats(sig_all[h]), "breakout_only": stats(brk_all[h]),
                                 "all_days": stats(base_all[h])} for h in HORIZONS},
            "options": {**stats(rets), "best": max(rets) if rets else None,
                        "share_3x": round(sum(r >= 2 for r in rets) / len(rets), 3) if rets else None,
                        "bank_500_bet10pct": round(bank, 0)},
            "per_symbol": rows, "trades": trades}


def markdown(rep: dict) -> str:
    out = [f"# 期权成交量暴增 + 价格突破 {rep['generated']}", "",
           f"股票：本月动态池前 {rep['pool']} 只（今天的名单，偏向今天的赢家，重点看同一批股票里信号日和普通日的差别）。"
           f"信号：价外 0–20%、35 天内到期的看涨期权成交量 ≥ 过去 20 天均值的 {rep['surge']:g} 倍，且收盘创 20 日新高。"
           "自 2024-02。只研究，不改交易。", "",
           "## 之后股价表现", "", "| 持有天数 | 信号日（平均 / 中位 / 上涨比例 / 次数） | 只有突破、没有暴增 | 所有交易日 |", "|---|---|---|---|"]
    for h, v in rep["forward"].items():
        def f(s):
            return f"{s['mean']:+.2%} / {s['median']:+.2%} / {s['win']:.0%} / {s['n']}" if s.get("n") else "—"
        out.append(f"| {h} 天 | {f(v['signal'])} | {f(v['breakout_only'])} | {f(v['all_days'])} |")
    o = rep["options"]
    out += ["", "## 真实期权交易（信号日收盘买入约 7.5% 价外、25–50 天到期的看涨期权，21 个交易日后卖出，含 10% 价差成本）", ""]
    if o.get("n"):
        out += [f"- 交易 {o['n']} 次，赚钱比例 {o['win']:.0%}，平均每 $1 回报 {o['mean']:+.0%}，中位数 {o['median']:+.0%}，"
                f"最好一次 {o['best'] + 1:.1f} 倍，3 倍以上占 {o['share_3x']:.0%}",
                f"- $500 账户每次押余额 10%，按时间顺序：最后 ${o['bank_500_bet10pct']:,.0f}"]
    else:
        out.append("- 没有可成交的交易")
    out += ["", "## 信号最多的股票", ""]
    for r in sorted(rep["per_symbol"], key=lambda r: -r["signals"])[:15]:
        out.append(f"- {r['symbol']}：信号 {r['signals']} 次，单纯突破 {r['breakouts']} 次")
    return "\n".join(out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca(), root)
    out = root / "research"
    (out / f"{rep['generated']}-options-flow.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False) + "\n")
    (out / f"{rep['generated']}-options-flow.md").write_text(markdown(rep))
    print(markdown(rep))


if __name__ == "__main__":
    sys.exit(main())
