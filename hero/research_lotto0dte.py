"""Research: same-day "lottery tickets" -- the $0.01-$0.05 options that sometimes go 100x (AMZN, 2026-10-07).

On every day a name lists an expiry that day (SPY / QQQ daily; the big single stocks on Fridays, and on
Monday / Wednesday too in 2026), at 10:00 take the call and the put nearest the money whose 10:00 minute
close is between $0.01 and $0.05 (the closest-to-money "nearly worthless" ticket each side), on SIP
minutes since 2024-02. Pay the close + max($0.01, 10%) to get in -- on a $0.02 ticket that is +50%, which
is what such a ticket really costs -- and either hold it to the last minute close by 15:55 less
max($0.01, 10%) (a 0DTE ticket must be sold, not exercised, by a small account), or sell at a resting
10x / 20x / 50x take. Reported: how often a ticket reached 10x / 20x / 100x, the mean per $1, by name and
side; and the $500 / $1,000 game: each such day spends 5% of the account split over that day's tickets
(at least one whole contract each, so some days a small account cannot buy them all), a round ends below
$50 / $100 or at $10,000. Record only. Run: python -m hero.research_lotto0dte
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from hero.research_0dte import ET, at_or_before, et_minutes
from hero.research_0dte_names import candidate_day, minutes_by_day

START = "2024-02-01"
NAMES = ("SPY", "QQQ", "AAPL", "AMZN", "MSFT", "META", "NVDA", "TSLA", "GOOGL", "AVGO", "AMD", "MU")
ENTRY = "10:00"
LAST = "15:55"
MAX_PX, MIN_PX = 0.05, 0.01
TAKES = (10, 20, 50)
SPEND = 0.05
STARTS = (500.0, 1000.0)
TARGET = 10_000.0


def cost_in(px: float) -> float:
    return px + max(0.01, 0.10 * px)


def value_out(px: float) -> float:
    return max(px - max(0.01, 0.10 * px), 0.0)


def ticket(m: dict[str, dict]) -> dict | None:
    """One ticket from its minute bars (keys HH:MM): entry at the 10:00 close (within 5 minutes)."""
    keys = sorted(k for k in m if k >= ENTRY)
    if not keys or keys[0] > "10:05":
        return None
    px = float(m[keys[0]]["c"])
    if not MIN_PX <= px <= MAX_PX:
        return None
    cost = cost_in(px)
    later = [k for k in keys[1:] if k <= LAST]
    best = max((float(m[k].get("h", m[k]["c"])) for k in later), default=0.0)
    last = value_out(float(m[later[-1]]["c"])) if later else 0.0
    out = {"px": px, "cost": round(cost, 3), "best_x": round(best / cost, 1), "hold": round(last / cost - 1, 3)}
    for tk in TAKES:
        out[f"take{tk}"] = tk - 1 if best >= tk * cost else out["hold"]
    return out


def run(client) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    rows = []
    for und in NAMES:
        days = minutes_by_day(client, und, START, end)
        for day in sorted(days):
            m = days[day]
            if not candidate_day(und, day):
                continue
            bar = at_or_before(m, ENTRY)
            if not bar:
                continue
            spot = float(bar["c"])
            cs = []
            for status in ("inactive", "active"):
                try:
                    cs = client.option_contracts(und, status=status, expiration_date=day,
                                                 strike_price_gte=f"{spot * 0.92:.2f}", strike_price_lte=f"{spot * 1.08:.2f}")
                except Exception:
                    cs = []
                if cs:
                    break
            if not cs:
                continue
            # out-of-the-money strikes only, nearest first; the first whose 10:00 price is cheap enough wins
            calls = sorted((float(c["strike_price"]), c["symbol"]) for c in cs if c["type"] == "call" and float(c["strike_price"]) > spot)
            puts = sorted(((float(c["strike_price"]), c["symbol"]) for c in cs if c["type"] == "put" and float(c["strike_price"]) < spot), reverse=True)
            want = [s for _, s in calls[:25]] + [s for _, s in puts[:25]]
            if not want:
                continue
            try:
                ob = client.option_bars(want, f"{day}T13:00:00Z", f"{day}T21:00:00Z", timeframe="1Min")
            except Exception:
                continue
            time.sleep(0.1)
            for side, ladder in (("call", calls), ("put", puts)):
                for k, s in ladder[:25]:
                    t = ticket(et_minutes(ob.get(s, [])))
                    if t:
                        rows.append({"day": day, "symbol": und, "side": side, "strike": k, "spot": spot,
                                     "away": round(abs(k / spot - 1), 4), **t})
                        break
    return {"generated": date.today().isoformat(), "start": START, "end": end, "rows": rows}


def summarize(sub: list[dict]) -> dict:
    out = {"n": len(sub), "days": len({r["day"] for r in sub}), "away": round(statistics.median(r["away"] for r in sub), 4),
           "px": round(statistics.median(r["px"] for r in sub), 3)}
    for x in (10, 20, 100):
        out[f"p{x}"] = round(sum(r["best_x"] >= x for r in sub) / len(sub), 4)
    out["hold"] = round(statistics.mean(r["hold"] for r in sub), 3)
    for tk in TAKES:
        out[f"take{tk}"] = round(statistics.mean(r[f"take{tk}"] for r in sub), 3)
    return out


def game(sub: list[dict], start: float, key: str) -> dict:
    """Each day: SPEND of the account over that day's tickets, whole contracts only."""
    by_day: dict[str, list[dict]] = {}
    for r in sub:
        by_day.setdefault(r["day"], []).append(r)
    bank, heroes, zeros, best, rounds_days = start, 0, 0, start, []
    first = None
    for d in sorted(by_day):
        first = first or d
        budget = SPEND * bank / len(by_day[d])
        for r in by_day[d]:
            n = int(budget // (r["cost"] * 100))
            if n >= 1:
                bank += n * r["cost"] * 100 * r[key]
        best = max(best, min(bank, TARGET))
        if bank < start * 0.1:
            zeros, bank = zeros + 1, start
        elif bank >= TARGET:
            heroes, bank = heroes + 1, start
    return {"heroes": heroes, "zeros": zeros, "end": round(bank, 2), "best": round(best, 2)}


def table(rep: dict) -> dict:
    rows = rep["rows"]
    groups = {"全部": rows, "看涨": [r for r in rows if r["side"] == "call"], "看跌": [r for r in rows if r["side"] == "put"],
              "只看 SPY / QQQ": [r for r in rows if r["symbol"] in ("SPY", "QQQ")],
              "只看个股": [r for r in rows if r["symbol"] not in ("SPY", "QQQ")]}
    for s in NAMES:
        groups[s] = [r for r in rows if r["symbol"] == s]
    out = {}
    for g, sub in groups.items():
        if not sub:
            continue
        row = summarize(sub)
        for st in STARTS:
            for key in ("hold", "take20"):
                row[f"g{int(st)}_{key}"] = game(sub, st, key)
        out[g] = row
    return out


def markdown(rep: dict, t: dict) -> str:
    out = [f"# 末日彩票：$0.01–0.05 当天到期期权 {rep['generated']}", "",
           f"{rep['start']} 至 {rep['end']}，SIP 分钟线。每个有当天到期合约的日子，10:00 各取一张最靠近现价、价格在 $0.01–0.05 之间的价外看涨和看跌。"
           "买入付分钟收盘价 + max($0.01, 10%)（$0.02 的票实际要付 $0.03，多 50%），卖出少拿 max($0.01, 10%)；拿到 15:55 卖，或挂 10 / 20 / 50 倍止盈。"
           "玩法：每天花账户 5%，平分给当天的票，只能买整张；$500 低于 $50、$1,000 低于 $100 归零，到 $10,000 成功。只研究，不改交易。", "",
           "| 组 | 张数 | 天数 | 离现价（中位） | 价格（中位） | 到过 10 倍 | 到过 20 倍 | 到过 100 倍 | 拿到 15:55：每 $1 | 10 倍止盈 | 20 倍止盈 | 50 倍止盈 | $500：成功 / 归零 / 期末（拿到 15:55） | $1,000：成功 / 归零 / 期末（20 倍止盈） |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for g, r in t.items():
        a, b = r["g500_hold"], r["g1000_take20"]
        out.append(f"| {g} | {r['n']} | {r['days']} | {r['away']:.1%} | ${r['px']:.2f} | {r['p10']:.1%} | {r['p20']:.1%} | {r['p100']:.2%} | "
                   f"{r['hold']:+.0%} | {r['take10']:+.0%} | {r['take20']:+.0%} | {r['take50']:+.0%} | "
                   f"{a['heroes']} / {a['zeros']} / ${a['end']:,.0f} | {b['heroes']} / {b['zeros']} / ${b['end']:,.0f} |")
    best = sorted(rep["rows"], key=lambda r: -r["best_x"])[:15]
    out += ["", "## 涨得最多的 15 张", "", "| 日期 | 标的 | 方向 | 行权价 | 10:00 价格 | 最高倍数 | 15:55 卖 |", "|---|---|---|---|---|---|---|"]
    for r in best:
        out.append(f"| {r['day']} | {r['symbol']} | {'看涨' if r['side'] == 'call' else '看跌'} | {r['strike']:g} | ${r['px']:.2f} | {r['best_x']:.0f} 倍 | {r['hold']:+.0%} |")
    return "\n".join(out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca())
    t = table(rep)
    rep["table"] = t
    (root / "research" / f"{rep['generated']}-lotto0dte.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    (root / "research" / f"{rep['generated']}-lotto0dte.md").write_text(markdown(rep, t))
    print(markdown(rep, t))


if __name__ == "__main__":
    sys.exit(main())
