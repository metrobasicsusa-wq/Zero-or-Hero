"""Research: the $500 account's same-day-expiry rule on SPY vs QQQ / IWM vs single stocks.

The live rule (config/s500.json zero_dte): at 10:00 buy the option expiring that day, a call if the
underlying is above its open, else a put, 0.6% beyond the price; a resting limit at 3x the cost, else
sold at 15:30. Here the same rule (also 0.3% / 1.0% away, and a 5x take) on SPY, QQQ, IWM and the single
stocks that list same-day expiries (most only on Fridays; some on Monday / Wednesday too in 2026),
on every day that underlying had an expiry that day, since 2024-02:
  strike: the nearest listed strike at least the offset beyond the 10:00 price (single stocks list
          $1 / $2.5 / $5 grids, so "0.6%" is often further away);
  prices: SIP stock minutes, option minute closes, pay 10% over to get in and get 10% under to get out
          (the same for every name -- single-stock spreads are usually wider, so this favours them);
  also SPY on only the days the stocks could trade, so the comparison is on the same days;
  and the $500 game: half the account each trade, a round ends below $50, at $1,000 (2x) or $10,000 (20x).
Record only. Run: python -m hero.research_0dte_names
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from hero import research_hero as rh
from hero.research_0dte import ET, at_or_before, et_minutes, trade

START = "2024-02-01"
INDEXES = ("SPY", "QQQ", "IWM")
STOCKS = ("AAPL", "AMZN", "MSFT", "META", "NVDA", "TSLA", "GOOGL", "AVGO", "AMD", "MU")
ENTRY = "10:00"
OFFSETS = (0.003, 0.006, 0.010)
TAKES = (3, 5)
FRACTION = 0.5
END_LOSS = 0.9
TARGETS = (2, 20)
FEED = "sip"


def minutes_by_day(client, und: str, start: str, end: str) -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    d = date.fromisoformat(start).replace(day=1)
    while d <= date.fromisoformat(end):
        nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        try:
            bars = client.stock_bars([und], f"{d.isoformat()}T13:00:00Z", f"{nxt.isoformat()}T00:00:00Z", feed=FEED).get(und, [])
        except Exception:
            bars = []
        for b in bars:
            t = datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ET)
            k = t.strftime("%H:%M")
            if "09:30" <= k <= "15:59":
                out.setdefault(t.date().isoformat(), {})[k] = b
        d = nxt
    return out


def candidate_day(und: str, day: str) -> bool:
    """Days worth asking about: every weekday for the index funds; for single stocks Fridays, and
    every weekday in 2026 (some added Monday / Wednesday expiries that year)."""
    return und in INDEXES or date.fromisoformat(day).weekday() == 4 or day >= "2026-01-01"


def pick(contracts: list[dict], kind: str, spot: float, off: float) -> str | None:
    want = "call" if kind == "C" else "put"
    ks = sorted((float(c["strike_price"]), c["symbol"]) for c in contracts if c.get("type", want) == want)
    if kind == "C":
        got = [s for k, s in ks if k >= spot * (1 + off)]
        return got[0] if got else None
    got = [s for k, s in ks if k <= spot * (1 - off)]
    return got[-1] if got else None


def run(client) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    trades: dict[str, list[tuple[str, float]]] = {}
    days_used: dict[str, int] = {}
    for und in INDEXES + STOCKS:
        days = minutes_by_day(client, und, START, end)
        used = 0
        for day in sorted(days):
            m = days[day]
            if not candidate_day(und, day) or "09:30" not in m or not at_or_before(m, "15:30"):
                continue
            spot_bar = at_or_before(m, ENTRY)
            if not spot_bar:
                continue
            spot, day_open = float(spot_bar["c"]), float(m["09:30"]["o"])
            kind = "C" if spot >= day_open else "P"
            cs = []
            for status in ("inactive", "active"):
                try:
                    cs = client.option_contracts(und, status=status, expiration_date=day,
                                                 strike_price_gte=f"{spot * 0.97:.2f}", strike_price_lte=f"{spot * 1.03:.2f}")
                except Exception:
                    cs = []
                if cs:
                    break
            if not cs:
                continue  # no expiry that day
            picks = {off: pick(cs, kind, spot, off) for off in OFFSETS}
            syms = sorted({s for s in picks.values() if s})
            if not syms:
                continue
            try:
                bars = client.option_bars(syms, f"{day}T13:00:00Z", f"{day}T21:00:00Z", timeframe="1Min")
            except Exception:
                continue
            time.sleep(0.1)
            used += 1
            mins = {s: et_minutes(b) for s, b in bars.items()}
            for off, sym in picks.items():
                if not sym or sym not in mins:
                    continue
                for take in TAKES:
                    r = trade(mins[sym], ENTRY, take)
                    if r is not None:
                        trades.setdefault(f"{und}|{off}|{take}", []).append((day, r))
        days_used[und] = used
    return {"generated": date.today().isoformat(), "start": START, "end": end, "days_used": days_used,
            "trades": {k: [[d, round(r, 4)] for d, r in v] for k, v in trades.items()}}


def stats(ts: list, take: int) -> dict:
    rets = [r for _, r in ts]
    out = {"n": len(rets), "mean": round(statistics.mean(rets), 3), "median": round(statistics.median(rets), 3),
           "win": round(sum(r > 0 for r in rets) / len(rets), 3), "hit": round(sum(r >= take - 1 for r in rets) / len(rets), 3)}
    stream = [(d, max(FRACTION * r, -1.0)) for d, r in ts]
    first = (date.fromisoformat(ts[0][0]) - timedelta(days=1)).isoformat()
    for t in TARGETS:
        seq = rh.sequential(stream, first, t, END_LOSS)
        out[f"t{t}"] = {"heroes": seq["heroes"], "zeros": seq["zeros"]}
    return out


def table(rep: dict) -> dict:
    tr = {k: [tuple(x) for x in v] for k, v in rep["trades"].items()}
    stock_days = {d for k, v in tr.items() if k.split("|")[0] in STOCKS for d, _ in v}
    out = {}
    for off in OFFSETS:
        for take in TAKES:
            for und in INDEXES + STOCKS:
                ts = tr.get(f"{und}|{off}|{take}")
                if ts:
                    out[f"{und}|{off}|{take}"] = stats(ts, take)
            spy = [x for x in tr.get(f"SPY|{off}|{take}", []) if x[0] in stock_days]
            if spy:
                out[f"SPY（只看个股有到期的日子）|{off}|{take}"] = stats(spy, take)
            pooled = sorted(x for und in STOCKS for x in tr.get(f"{und}|{off}|{take}", []))
            if pooled:
                s = stats(pooled, take)
                for t in TARGETS:
                    s.pop(f"t{t}")  # several trades a day: not one account's sequence
                out[f"个股合计|{off}|{take}"] = s
    return out


def markdown(rep: dict, t: dict) -> str:
    out = [f"# 末日单：SPY vs QQQ / IWM vs 个股 {rep['generated']}", "",
           f"{rep['start']} 至 {rep['end']}。规则同 $500 账户：10:00 顺势买当天到期的期权（涨就看涨、跌就看跌），行权价离现价至少 0.3% / 0.6% / 1.0%"
           "（取最近的挂牌行权价；个股行权价间隔大，实际常更远），挂 3 / 5 倍止盈，没到就 15:30 卖。买入多付 10%、卖出少拿 10%（个股实际价差通常更大，这对个股有利）。"
           f"只算当天有到期合约的日子（个股多数只有周五，2026 年部分加了周一、三）。用到的天数：{rep['days_used']}。"
           "玩法：$500，每笔押一半，低于 $50 归零，到 $1,000（2 倍）或 $10,000（20 倍）成功，然后重来。只研究，不改交易。", ""]
    for off in OFFSETS:
        for take in TAKES:
            out += [f"## 离现价 {off:.1%}，{take} 倍止盈", "",
                    "| 标的 | 笔数 | 平均每 $1 | 中位数 | 赚钱比例 | 碰到止盈 | 到 $1,000：成功 / 归零 | 到 $10,000：成功 / 归零 |",
                    "|---|---|---|---|---|---|---|---|"]
            rows = [(k.split("|")[0], r) for k, r in t.items() if k.endswith(f"|{off}|{take}")]
            for name, r in sorted(rows, key=lambda x: -x[1]["mean"]):
                g = lambda tt: f"{r[tt]['heroes']} / {r[tt]['zeros']}" if tt in r else "—"
                out.append(f"| {name} | {r['n']} | {r['mean']:+.0%} | {r['median']:+.0%} | {r['win']:.0%} | {r['hit']:.0%} | {g('t2')} | {g('t20')} |")
            out.append("")
    return "\n".join(out)


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca())
    t = table(rep)
    rep["table"] = t
    (root / "research" / f"{rep['generated']}-0dte-names.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    (root / "research" / f"{rep['generated']}-0dte-names.md").write_text(markdown(rep, t))
    print(markdown(rep, t))


if __name__ == "__main__":
    sys.exit(main())
