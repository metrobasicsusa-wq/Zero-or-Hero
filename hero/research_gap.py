"""Research: buy calls after a gap down (claude-b's "rebound_scanner"), checked on our own data.

claude-b (exchange/claude-b/2026-10-06.intro.json) reports its one rule that holds across stocks:
a stock opens 2%+ below the prior close; 15 minutes after the open, buy a cheap same-day call;
after it doubles, sell on a 40% pullback from its best. Here, with point-in-time pools (each
month's top 100 listed companies by the prior 63 days' dollar volume, as in research_universe,
so no "today's winners" tilt), since 2024-02:
  event:  open <= prior close x 0.98 ("gap down"); controls: gap up 2%+ (same calls) and ordinary
          days (|gap| < 0.5%); each group randomly capped (see SAMPLES) to fit the run time;
  option: the nearest expiry within 4 days (flagged when it expires that day), calls 1% / 2% / 3%
          above the 9:45 price, bought at the 9:45-9:50 minute close + 10% (at least a cent);
  exits:  claude-b's trail (once worth 2x, sell when 40% below the best minute close), a 3x
          limit, or sold at 15:50; values are minute closes less 10% (at least a cent);
  "market day": SPY itself gapped 0.7%+ that morning (a macro day), reported apart;
  "chips / Mag 7" (the user's focus): AMD's peers, every one of their gap-downs kept (not sampled);
  "AMD-like" (decided before the open): the 63-day trend and volatility both in
          the top quarter of all gap-down events, and/or the stock's own last 3-6 gap-downs within a
          year rose from the open to the close on average.
Then the $500 game: half of the account on each event in date order, a round ends below $50 or at
$10,000. Record only. Run: python -m hero.research_gap
"""

from __future__ import annotations

import json
import math
import random
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from hero import research_0dte as z
from hero.research_lottery import entry_cost, exit_value

START = "2024-02-01"
POOL = 100
OTM = (0.01, 0.02, 0.03)
GAP = 0.02
# The user's focus: the AMD-style rebound now shows up mainly in chip stocks and the Magnificent 7.
MAG7 = {"AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "NVDA", "TSLA"}
SEMIS = {"NVDA", "AMD", "AVGO", "MU", "INTC", "QCOM", "TSM", "ARM", "MRVL", "AMAT", "LRCX", "KLAC", "ADI", "TXN",
         "ON", "NXPI", "MCHP", "ASML", "MPWR", "TER", "ENTG", "GFS", "SNDK", "SMH", "SOXX"}
FOCUS = MAG7 | SEMIS
SAMPLES = {"gap_down": 3000, "gap_up": 400, "ordinary": 400}  # caps keep a run inside the time limit
EXITS = ("trail", "tp3", "close")
FEED = "sip"  # the all-exchange tape: IEX alone is thin around the open, where this trade lives


def simulate(minutes: dict[str, dict], entry: str = "09:45") -> dict | None:
    """Per-$1 returns for each exit from one option's minute bars."""
    got = z.at_or_after(minutes, entry)
    if not got:
        return None
    t0, bar = got
    cost = entry_cost(float(bar["c"]))
    path = [(k, exit_value(float(minutes[k]["c"]))) for k in sorted(minutes) if t0 < k <= "15:50"]
    last = path[-1][1] if path else 0.0
    out = {"cost": round(cost, 3), "close": round(last / cost - 1, 3), "tp3": round(last / cost - 1, 3), "trail": None,
           "best": round(max((v for _, v in path), default=0.0) / cost, 2)}
    for _, v in path:
        if v >= 3 * cost:
            out["tp3"] = 2.0
            break
    peak, armed = 0.0, False
    for _, v in path:
        peak = max(peak, v)
        armed = armed or v >= 2 * cost
        if armed and v <= 0.6 * peak:
            out["trail"] = round(v / cost - 1, 3)
            break
    if out["trail"] is None:
        out["trail"] = out["close"]
    return out


def features(xs: list, opens: dict[str, float], dates: list[str], t: int) -> dict:
    """What was known before the open: 63-day trend and volatility, and how the stock's own last
    gap-downs (2%+, within a year) did from the open to the close that day."""
    out = {"mom63": None, "vol63": None, "prior_n": 0, "prior_rebound": None}
    if t >= 64 and xs[t - 1] and xs[t - 64]:
        out["mom63"] = round(xs[t - 1] / xs[t - 64] - 1, 3)
        rets = [math.log(b / a) for a, b in zip(xs[t - 64: t - 1], xs[t - 63: t]) if a and b]
        if len(rets) > 20:
            m = sum(rets) / len(rets)
            out["vol63"] = round(math.sqrt(sum((r - m) ** 2 for r in rets) / len(rets) * 252), 3)
    moves = []
    for u in range(max(1, t - 250), t):
        o, prev, c = opens.get(dates[u]), xs[u - 1], xs[u]
        if o and prev and c and o / prev - 1 <= -GAP:
            moves.append(c / o - 1)
    if moves:
        last = moves[-6:]
        out["prior_n"], out["prior_rebound"] = len(last), round(sum(last) / len(last), 4)
    return out


def game(events: list[dict], key: str) -> dict:
    bank, heroes, zeros, best = 500.0, 0, 0, 500.0
    for e in sorted(events, key=lambda e: e["day"]):
        bank += 0.5 * bank * max(e[key], -1.0)
        best = max(best, min(bank, 10_000.0))
        if bank < 50:
            zeros, bank = zeros + 1, 500.0
        elif bank >= 10_000:
            heroes, bank = heroes + 1, 500.0
    return {"heroes": heroes, "zeros": zeros, "best": round(best)}


def run(client) -> dict:
    from hero import research_universe as ru
    first = "2023-01-01"
    end = (date.today() - timedelta(days=1)).isoformat()
    stocks = ru.candidates(client)
    syms = sorted(set(stocks) | {"SPY"})
    bars: dict[str, list[dict]] = {}
    for i in range(0, len(syms), 200):
        bars.update(client.daily_bars(syms[i:i + 200], first, feed=FEED))
    dates, closes, dvol = ru.align(bars, "SPY")
    have = {s: k for s, k in stocks.items() if s in bars}
    for s in have:
        closes.setdefault(s, [None] * len(dates))
        dvol.setdefault(s, [0.0] * len(dates))
    firsts = {s: next((i for i, x in enumerate(closes[s]) if x is not None), None) for s in have}
    start = next(i for i, d in enumerate(dates) if d >= START)
    pools = ru.monthly_pools(dates, closes, dvol, firsts, have, POOL, start)
    keys = sorted(pools)
    opens = {s: {b["t"][:10]: float(b["o"]) for b in bars[s]} for s in list(have) + ["SPY"] if s in bars}
    spy_gap = {}
    for t in range(start, len(dates)):
        d, prev = dates[t], closes["SPY"][t - 1]
        if prev and d in opens["SPY"]:
            spy_gap[d] = opens["SPY"][d] / prev - 1
    cand = {"gap_down": [], "gap_up": [], "ordinary": []}
    for t in range(start, len(dates)):
        d = dates[t]
        if d > end:
            break
        pool = pools[max(k for k in keys if k <= t)]
        for s in pool:
            prev, o = closes[s][t - 1], opens.get(s, {}).get(d)
            if not prev or not o:
                continue
            g = o / prev - 1
            kind = "gap_down" if g <= -GAP else "gap_up" if g >= GAP else "ordinary" if abs(g) < 0.005 else None
            if kind:
                cand[kind].append((d, s, round(g, 4), features(closes[s], opens.get(s, {}), dates, t)))
    rng = random.Random(7)
    for k, n in SAMPLES.items():  # chip / Mag 7 gap-downs are always kept; the rest are sampled
        keep = [c for c in cand[k] if k == "gap_down" and c[1] in FOCUS]
        rest = [c for c in cand[k] if c not in keep]
        if len(rest) > n:
            rest = rng.sample(rest, n)
        cand[k] = sorted(keep + rest)
    rows = []
    for kind, evs in cand.items():
        for d, s, g, feat in evs:
            try:
                sm = z.et_minutes(client.stock_bars([s], f"{d}T13:00:00Z", f"{d}T21:00:00Z", feed=FEED).get(s, []))
            except Exception:
                continue
            spot_bar = z.at_or_before(sm, "09:45")
            if not spot_bar:
                continue
            spot = float(spot_bar["c"])
            lim = (date.fromisoformat(d) + timedelta(days=4)).isoformat()
            cs = []
            for status in ("inactive", "active"):
                try:
                    cs = client.option_contracts(s, status=status, type="call", expiration_date_gte=d, expiration_date_lte=lim,
                                                 strike_price_gte=f"{spot * 1.005:.2f}", strike_price_lte=f"{spot * 1.06:.2f}")
                except Exception:
                    cs = []
                if cs:
                    break
            if not cs:
                continue
            exp = min(c["expiration_date"] for c in cs)
            by_k = {float(c["strike_price"]): c["symbol"] for c in cs if c["expiration_date"] == exp}
            picks = {}
            for m in OTM:
                ks = [k for k in sorted(by_k) if k >= spot * (1 + m)]
                if ks:
                    picks[m] = ks[0]
            if not picks:
                continue
            try:
                ob = client.option_bars(sorted({by_k[k] for k in picks.values()}), f"{d}T13:00:00Z", f"{d}T21:00:00Z",
                                        timeframe="1Min")
            except Exception:
                continue
            time.sleep(0.1)
            for m, k in picks.items():
                r = simulate(z.et_minutes(ob.get(by_k[k], [])))
                if r:
                    rows.append({"kind": kind, "day": d, "symbol": s, "gap": g, "otm": m, "same_day": exp == d,
                                 "market_day": abs(spy_gap.get(d, 0.0)) >= 0.007, **feat, **r})
    return {"generated": date.today().isoformat(), "start": START, "pool": POOL, "feed": FEED,
            "candidates": {k: len(v) for k, v in cand.items()}, "rows": rows}


def cut(rows: list[dict], key: str, q: float) -> float:
    xs = sorted(r[key] for r in rows if r["kind"] == "gap_down" and r.get(key) is not None)
    return xs[int(q * (len(xs) - 1))] if xs else float("inf")


def table(rows: list[dict]) -> dict:
    out = {}
    hi_mom, hi_vol = cut(rows, "mom63", 0.75), cut(rows, "vol63", 0.75)
    strong = lambda r: (r.get("mom63") or -9) >= hi_mom
    wild = lambda r: (r.get("vol63") or 0) >= hi_vol
    bouncer = lambda r: r.get("prior_n", 0) >= 3 and (r.get("prior_rebound") or -1) > 0
    gd = lambda r: r["kind"] == "gap_down"
    groups = {
        "低开 + 半导体 / Mag 7": lambda r: gd(r) and r["symbol"] in FOCUS,
        "低开 + 半导体 / Mag 7 + 像 AMD": lambda r: gd(r) and r["symbol"] in FOCUS and strong(r) and wild(r),
        "低开 + 半导体 / Mag 7 + 常收复": lambda r: gd(r) and r["symbol"] in FOCUS and bouncer(r),
        "低开 + 半导体 / Mag 7，2026 年以来": lambda r: gd(r) and r["symbol"] in FOCUS and r["day"] >= "2026-01-01",
        "低开 + 半导体 / Mag 7，当天到期": lambda r: gd(r) and r["symbol"] in FOCUS and r["same_day"],
        "低开 + 其他股票": lambda r: gd(r) and r["symbol"] not in FOCUS,
        "低开 + 63 天涨幅前 25%": lambda r: gd(r) and strong(r),
        "低开 + 波动前 25%": lambda r: gd(r) and wild(r),
        "低开 + 强势且高波动（像 AMD）": lambda r: gd(r) and strong(r) and wild(r),
        "低开 + 过去低开常当天收复": lambda r: gd(r) and bouncer(r),
        "低开 + 像 AMD 且常收复": lambda r: gd(r) and strong(r) and wild(r) and bouncer(r),
        "低开 + 像 AMD，2026 年以来": lambda r: gd(r) and strong(r) and wild(r) and r["day"] >= "2026-01-01",
        "低开 2%+（全部）": lambda r: r["kind"] == "gap_down",
        "低开 2%+，当天到期": lambda r: r["kind"] == "gap_down" and r["same_day"],
        "低开 2%+，非宏观日": lambda r: r["kind"] == "gap_down" and not r["market_day"],
        "对照：高开 2%+": lambda r: r["kind"] == "gap_up",
        "对照：普通日": lambda r: r["kind"] == "ordinary",
    }
    for g, f in groups.items():
        for m in OTM:
            sub = [r for r in rows if f(r) and r["otm"] == m]
            if not sub:
                continue
            row = {"n": len(sub), "symbols": len({r["symbol"] for r in sub}), "cost": round(statistics.median(r["cost"] for r in sub), 2),
                   "x2": round(sum(r["best"] >= 2 for r in sub) / len(sub), 3)}
            for k in EXITS:
                xs = [r[k] for r in sub]
                row[k] = {"mean": round(statistics.mean(xs), 3), "median": round(statistics.median(xs), 3),
                          "win": round(sum(x > 0 for x in xs) / len(xs), 3)}
            row["game_trail"] = game(sub, "trail")
            if g == "低开 2%+（全部）":
                per = {}
                for r in sub:
                    per.setdefault(r["symbol"], []).append(r["trail"])
                ok = {s: v for s, v in per.items() if len(v) >= 5}
                row["symbols_5plus"] = len(ok)
                row["symbols_positive"] = sum(statistics.mean(v) > 0 for v in ok.values())
            out[f"{g}|{m}"] = row
    out["_cuts"] = {"mom63_top25": hi_mom, "vol63_top25": hi_vol}
    return out


def markdown(rep: dict, t: dict) -> str:
    names = {"trail": "翻倍后回撤 40% 卖", "tp3": "3 倍止盈", "close": "15:50 卖"}
    out = [f"# 低开买反弹（claude-b 的 rebound_scanner）独立复核 {rep['generated']}", "",
           f"自 {rep['start']}，当时的股票池（每月按之前 63 天成交额排前 {rep['pool']} 的上市公司）。候选：{rep['candidates']}（对照组随机抽样）。"
           "9:45 买最近到期（4 天内，标出当天到期的）、比现价高 1% / 2% / 3% 的看涨，买入多付 10%、卖出少拿 10%，用分钟收盘价。"
           f"股票行情：{rep.get('feed', 'iex')}（sip = 全部交易所合并，iex = 单一交易所）。「宏观日」= SPY 自己当天高开或低开 0.7% 以上。玩法：$500，每个事件押一半，低于 $50 归零、到 $10,000 成功。只研究，不改交易。", "",
           "| 组别 | 价外 | 笔数 | 股票数 | 成本中位数 | 到过 2 倍 | " + " | ".join(f"{names[k]}：平均 / 中位数 / 胜率" for k in EXITS)
           + " | $500 半仓（回撤卖）：成功 / 归零 |",
           "|---|---|---|---|---|---|" + "---|" * len(EXITS) + "---|"]
    for key, r in t.items():
        if key.startswith("_"):
            continue
        g, m = key.split("|")
        cells = " | ".join(f"{r[k]['mean']:+.0%} / {r[k]['median']:+.0%} / {r[k]['win']:.0%}" for k in EXITS)
        out.append(f"| {g} | {float(m):.0%} | {r['n']} | {r['symbols']} | ${r['cost']:.2f} | {r['x2']:.0%} | {cells} | "
                   f"{r['game_trail']['heroes']} / {r['game_trail']['zeros']} |")
    c = t.get("_cuts", {})
    out += ["", f"「像 AMD」= 低开前 63 天涨幅和波动率都在所有低开事件的前 25%（涨幅 ≥ {c.get('mom63_top25', 0):+.0%}、"
            f"年化波动 ≥ {c.get('vol63_top25', 0):.0%}）；「常收复」= 过去一年最近 3～6 次低开当天从开盘到收盘平均是涨的。都只用开盘前已知的数据。"
            "「半导体 / Mag 7」= " + "、".join(sorted(FOCUS)) + "（在当月股票池里才算；这些股票的低开全部保留，不抽样）。"]
    gd = [(k, r) for k, r in t.items() if k.startswith("低开 2%+（全部）")]
    out += ["", "## 按股票（低开全部，回撤卖，至少 5 笔的股票）", ""]
    for k, r in gd:
        out.append(f"- 价外 {float(k.split('|')[1]):.0%}：{r.get('symbols_5plus', 0)} 只里 {r.get('symbols_positive', 0)} 只平均为正"
                   "（claude-b 报告：22 只里 15 只）")
    return "\n".join(out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca())
    t = table(rep["rows"])
    rep["table"] = t
    name = f"{rep['generated']}-gap" + ("-sip" if FEED == "sip" else "")
    (root / "research" / f"{name}.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    (root / "research" / f"{name}.md").write_text(markdown(rep, t))
    print(markdown(rep, t))


if __name__ == "__main__":
    sys.exit(main())
