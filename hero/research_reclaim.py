"""Research: a name that opened below yesterday's close takes it back -- buy that day's calls then (AMZN / MU, 2026-10-07).

On 2026-10-07 AMZN and MU both opened down; their same-day calls sat near zero until each stock climbed
back above the prior close (MU 10:18, AMZN 11:13), then went 30-100x. Here, on SIP minutes since 2024-02,
for SPY / QQQ and the big single stocks on days they list an expiry that day:
  signal:  opened below the prior close, and between 10:00 and 13:00 a minute closes above it (the first
           such minute); buy the next minute;
  option:  the call expiring that day, the nearest listed strike at least 1% / 2% above the price then;
           pay the minute close + max($0.01, 10%), get max($0.01, 10%) under;
  exits:   a 3x / 5x resting take, a trail (once worth 2x, out 40% below the best), or held to 15:50;
  control: the same names on other expiry days, the same call bought at 11:00;
then a $500 / $1,000 account putting half of itself on each signal day (split over that day's signals,
whole contracts), a round ends below 10% of its start or at $10,000. Record only.
Run: python -m hero.research_reclaim
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from hero.research_0dte import at_or_after, et_minutes
from hero.research_0dte_names import candidate_day, minutes_by_day
from hero.research_lotto0dte import cost_in, value_out

START = "2024-02-01"
NAMES = ("SPY", "QQQ", "AAPL", "AMZN", "MSFT", "META", "NVDA", "TSLA", "GOOGL", "AVGO", "AMD", "MU")
WINDOW = ("10:00", "13:00")
OTM = (0.01, 0.02)
LAST = "15:50"
FRACTION = 0.5
STARTS = (500.0, 1000.0)
TARGET = 10_000.0
FEED = "sip"
EXITS = ("take3", "take5", "trail", "hold")


def next_minute(hhmm: str) -> str:
    h, m = map(int, hhmm.split(":"))
    m += 1
    return f"{h + m // 60:02d}:{m % 60:02d}"


def signal(m: dict[str, dict], prev: float) -> str | None:
    """The minute after the first close above the prior close in the window, if the day opened below it."""
    keys = sorted(m)
    if not keys or keys[0] > "09:35" or float(m[keys[0]]["o"]) >= prev:
        return None
    for k in keys:
        if k < WINDOW[0]:
            continue
        if k > WINDOW[1]:
            return None
        if float(m[k]["c"]) > prev:
            return next_minute(k)
    return None


def trade(opt: dict[str, dict], entry: str) -> dict | None:
    got = at_or_after(opt, entry)
    if not got:
        return None
    k0, bar = got
    px = float(bar["c"])
    if px <= 0:
        return None
    cost = cost_in(px)
    later = [k for k in sorted(opt) if k0 < k <= LAST]
    highs = [(k, float(opt[k].get("h", opt[k]["c"])), float(opt[k]["c"])) for k in later]
    last = value_out(highs[-1][2]) if highs else 0.0
    out = {"px": px, "cost": round(cost, 3), "best_x": round(max((h for _, h, _ in highs), default=0) / cost, 1),
           "hold": round(last / cost - 1, 3)}
    for tk in (3, 5):
        out[f"take{tk}"] = tk - 1 if any(h >= tk * cost for _, h, _ in highs) else out["hold"]
    peak, armed, trail = 0.0, False, None
    for _, _, c in highs:
        v = value_out(c)
        peak = max(peak, v)
        armed = armed or v >= 2 * cost
        if armed and v <= 0.6 * peak:
            trail = v / cost - 1
            break
    out["trail"] = round(trail if trail is not None else out["hold"], 3)
    return out


def run(client) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    rows = []
    for und in NAMES:
        closes = {b["t"][:10]: float(b["c"]) for b in client.daily_bars([und], "2024-01-01", adjustment="raw", feed=FEED).get(und, [])}
        days = minutes_by_day(client, und, START, end)
        ds = sorted(closes)
        for day in sorted(days):
            if not candidate_day(und, day) or day not in closes:
                continue
            i = ds.index(day)
            if i == 0:
                continue
            prev, m = closes[ds[i - 1]], days[day]
            sig = signal(m, prev)
            entry, kind = (sig, "signal") if sig else ("11:00", "control")
            got = at_or_after(m, entry)
            if not got:
                continue
            spot = float(got[1]["c"])
            cs = []
            for status in ("inactive", "active"):
                try:
                    cs = client.option_contracts(und, status=status, type="call", expiration_date=day,
                                                 strike_price_gte=f"{spot * 1.005:.2f}", strike_price_lte=f"{spot * 1.06:.2f}")
                except Exception:
                    cs = []
                if cs:
                    break
            if not cs:
                continue
            ks = sorted((float(c["strike_price"]), c["symbol"]) for c in cs)
            picks = {off: next((s for k, s in ks if k >= spot * (1 + off)), None) for off in OTM}
            want = sorted({s for s in picks.values() if s})
            if not want:
                continue
            try:
                ob = client.option_bars(want, f"{day}T13:00:00Z", f"{day}T21:00:00Z", timeframe="1Min")
            except Exception:
                continue
            time.sleep(0.1)
            for off, s in picks.items():
                if not s:
                    continue
                t = trade(et_minutes(ob.get(s, [])), entry)
                if t:
                    rows.append({"day": day, "symbol": und, "kind": kind, "otm": off, "entry": entry,
                                 "gap": round(float(m[sorted(m)[0]]["o"]) / prev - 1, 4), **t})
    return {"generated": date.today().isoformat(), "start": START, "end": end, "rows": rows}


def game(sub: list[dict], start: float, key: str) -> dict:
    by_day: dict[str, list[dict]] = {}
    for r in sub:
        by_day.setdefault(r["day"], []).append(r)
    bank, heroes, zeros = start, 0, 0
    for d in sorted(by_day):
        budget = FRACTION * bank / len(by_day[d])
        for r in by_day[d]:
            n = int(budget // (r["cost"] * 100))
            if n >= 1:
                bank += n * r["cost"] * 100 * r[key]
        if bank < start * 0.1:
            zeros, bank = zeros + 1, start
        elif bank >= TARGET:
            heroes, bank = heroes + 1, start
    return {"heroes": heroes, "zeros": zeros, "end": round(bank, 2)}


def table(rep: dict) -> dict:
    rows = rep["rows"]
    out = {}
    groups = {"信号：低开后回到昨收": lambda r: r["kind"] == "signal",
              "　只看个股": lambda r: r["kind"] == "signal" and r["symbol"] not in ("SPY", "QQQ"),
              "　只看 SPY / QQQ": lambda r: r["kind"] == "signal" and r["symbol"] in ("SPY", "QQQ"),
              "　低开 2%+ 后回来": lambda r: r["kind"] == "signal" and r["gap"] <= -0.02,
              "　11:00 前回来": lambda r: r["kind"] == "signal" and r["entry"] <= "11:00",
              "　11:00 后回来": lambda r: r["kind"] == "signal" and r["entry"] > "11:00",
              "　2026 年以来": lambda r: r["kind"] == "signal" and r["day"] >= "2026-01-01",
              "对照：其他日子 11:00 买": lambda r: r["kind"] == "control"}
    for s in NAMES:
        groups[f"信号：{s}"] = (lambda s: lambda r: r["kind"] == "signal" and r["symbol"] == s)(s)
    for g, f in groups.items():
        for off in OTM:
            sub = [r for r in rows if f(r) and r["otm"] == off]
            if not sub:
                continue
            row = {"n": len(sub), "days": len({r["day"] for r in sub}), "px": round(statistics.median(r["px"] for r in sub), 2),
                   "p3": round(sum(r["best_x"] >= 3 for r in sub) / len(sub), 3),
                   "p10": round(sum(r["best_x"] >= 10 for r in sub) / len(sub), 3)}
            for k in EXITS:
                xs = [r[k] for r in sub]
                row[k] = {"mean": round(statistics.mean(xs), 3), "median": round(statistics.median(xs), 3),
                          "win": round(sum(x > 0 for x in xs) / len(xs), 3)}
            for st in STARTS:
                row[f"g{int(st)}"] = game(sub, st, "take5")
            out[f"{g}|{off}"] = row
    return out


def markdown(rep: dict, t: dict) -> str:
    names = {"take3": "3 倍止盈", "take5": "5 倍止盈", "trail": "翻倍后回撤 40% 卖", "hold": "拿到 15:50"}
    out = [f"# 低开后回到昨收：买当天到期看涨 {rep['generated']}", "",
           f"{rep['start']} 至 {rep['end']}，SIP 分钟线，只看当天有到期合约的日子。信号：开盘低于昨收，10:00–13:00 之间第一次有分钟收盘回到昨收上方，下一分钟买当天到期、"
           "比当时价格高 1% / 2% 的看涨；买入多付 max($0.01, 10%)、卖出少拿 max($0.01, 10%)。对照：同一批股票其他到期日 11:00 买。"
           "玩法：每个有信号的日子押账户一半（平分、整张），5 倍止盈；低于起始 10% 归零、到 $10,000 成功。只研究，不改交易。", "",
           "| 组别 | 价外 | 笔数 | 天数 | 价格中位数 | 到过 3 倍 | 到过 10 倍 | " + " | ".join(f"{names[k]}：平均 / 中位数 / 胜率" for k in EXITS)
           + " | $500：成功 / 归零 / 期末 | $1,000：成功 / 归零 / 期末 |",
           "|---|---|---|---|---|---|---|" + "---|" * len(EXITS) + "---|---|"]
    for key, r in t.items():
        g, off = key.split("|")
        cells = " | ".join(f"{r[k]['mean']:+.0%} / {r[k]['median']:+.0%} / {r[k]['win']:.0%}" for k in EXITS)
        a, b = r["g500"], r["g1000"]
        out.append(f"| {g} | {float(off):.0%} | {r['n']} | {r['days']} | ${r['px']:.2f} | {r['p3']:.0%} | {r['p10']:.1%} | {cells} | "
                   f"{a['heroes']} / {a['zeros']} / ${a['end']:,.0f} | {b['heroes']} / {b['zeros']} / ${b['end']:,.0f} |")
    best = sorted([r for r in rep["rows"] if r["kind"] == "signal"], key=lambda r: -r["best_x"])[:12]
    out += ["", "## 信号里涨得最多的 12 张", "", "| 日期 | 标的 | 价外 | 买入时间 | 买价 | 最高倍数 | 拿到 15:50 |", "|---|---|---|---|---|---|---|"]
    for r in best:
        out.append(f"| {r['day']} | {r['symbol']} | {r['otm']:.0%} | {r['entry']} | ${r['px']:.2f} | {r['best_x']:.0f} 倍 | {r['hold']:+.0%} |")
    return "\n".join(out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca())
    t = table(rep)
    rep["table"] = t
    (root / "research" / f"{rep['generated']}-reclaim.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    (root / "research" / f"{rep['generated']}-reclaim.md").write_text(markdown(rep, t))
    print(markdown(rep, t))


if __name__ == "__main__":
    sys.exit(main())
