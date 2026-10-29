"""Do support levels hold on big stocks? (owner, 10-29: 「那要研究下」, after the support levels listed for MSFT,
GOOGL, META, AMZN, ORCL and NVDA came with the caveat that we had never tested buying at them.)

Daily bars from Yahoo (split-adjusted) for the ~100 large caps of research/2026-10-02-earnings-reactions.json, since
2010. Every session, using only the bars before it, the nearest level of each kind below the prior close:
  pivot   the highest swing low of the last year (the lowest low of the 5 sessions either side) below the price;
  sma50 / sma100 / sma200   the moving averages of the close;
  volume  the nearest dense price band of the last half year (a band of 30 with at least 1.5x the average volume);
  gap     the top of the nearest up-gap not yet filled.
A touch: the prior close at least 1% above the level (within 15%), and the day's low at or below it. Buy with a resting
limit at the level (at the open when it opened below), then sell at the close 5, 10 or 20 sessions later, or with a
stop 5% below the entry (checked on the day's low, gaps fill at the open; the entry day counts) else the 20th close.
Each kind re-arms 10 sessions after a touch.

The control is the same buy on the same stock at the same depth below the prior close (to the nearest 0.5%) on days
with no level of any kind within 1.5% of the price: does the line add anything to "bought a dip of that size"?
Robustness (hero.robust, a 0.1% stock cost) runs on one trade per day (that day's touches averaged), since a selloff
makes many stocks touch at once. Survivorship: these are today's big companies. Record only.
Run: python -m hero.research_support
"""

from __future__ import annotations

import bisect
import json
import statistics
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from hero import robust

ROOT = Path(__file__).resolve().parent.parent
EVENTS = ROOT / "research" / "2026-10-02-earnings-reactions.json"
START = "2010-01-01"
PIV = 5
LOOK = 250
VP_N, VP_BINS, VP_DENSE = 126, 30, 1.5
APPROACH = 0.01
MAXD = 0.15
NEAR = 0.015
COOL = 10
HOLDS = (5, 10, 20)
LONG = (60, 120, 250)   # owner, 10-29: 「但就是拿久一点」 -- 3 months, 6 months, a year (when the bars reach that far)
BASE_STEP = 5           # the "any day" baseline buys every 5th close
STOP = 0.05
STEP = 0.005
STOCK_COST = 0.001
WARM = 260
MAG7 = {"AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA"}
TYPES = {"pivot": "前低（一年内的波段低点）", "sma50": "50 日均线", "sma100": "100 日均线", "sma200": "200 日均线",
         "volume": "成交密集区（半年）", "gap": "没补的跳空缺口"}


def ohlcv(sym: str, start: str = START) -> list[dict]:
    p1 = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp())
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1={p1}"
           f"&period2={int(datetime.now().timestamp())}&interval=1d")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    res = json.load(urllib.request.urlopen(req, timeout=30))["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    day = lambda t: (datetime.fromtimestamp(t, timezone.utc) - timedelta(hours=5)).date().isoformat()
    out = []
    for t, o, h, l, c, v in zip(res["timestamp"], q["open"], q["high"], q["low"], q["close"], q["volume"]):
        if o and h and l and c:
            out.append({"d": day(t), "o": o, "h": h, "l": l, "c": c, "v": v or 0})
    return out


def pivots(bars: list[dict], k: int = PIV) -> list[int]:
    """Indexes of swing lows: the lowest low of the k sessions either side (usable from index + k + 1 on)."""
    out = []
    for j in range(k, len(bars) - k):
        lo = bars[j]["l"]
        if all(lo <= bars[x]["l"] for x in range(j - k, j + k + 1)):
            out.append(j)
    return out


def dense_bands(bars: list[dict], i: int) -> list[float]:
    """Centers of the bands of the last VP_N sessions before i holding at least VP_DENSE x the average volume."""
    win = bars[max(0, i - VP_N):i]
    lo, hi = min(b["l"] for b in win), max(b["h"] for b in win)
    if hi <= lo:
        return []
    w = (hi - lo) / VP_BINS
    vol = [0.0] * VP_BINS
    for b in win:
        tp = (b["h"] + b["l"] + b["c"]) / 3
        vol[min(int((tp - lo) / w), VP_BINS - 1)] += b["v"]
    avg = sum(vol) / VP_BINS
    return [lo + (k + 0.5) * w for k in range(VP_BINS) if avg > 0 and vol[k] >= VP_DENSE * avg]


def outcome(bars: list[dict], i: int, level: float) -> dict | None:
    """A resting buy at `level` on session i (filled at the open when it opened below)."""
    b = bars[i]
    e = min(b["o"], level)
    if e <= 0 or i + max(HOLDS) >= len(bars):
        return None
    res = {"held": b["c"] >= level}
    for h in HOLDS + LONG:
        res[f"r{h}"] = bars[i + h]["c"] / e - 1 if i + h < len(bars) else None
    stop, out = e * (1 - STOP), None
    if b["l"] <= stop:
        out = stop
    else:
        for x in bars[i + 1:i + max(HOLDS) + 1]:
            if x["o"] <= stop:
                out = x["o"]
                break
            if x["l"] <= stop:
                out = stop
                break
    res["stop20"] = (out if out is not None else bars[i + max(HOLDS)]["c"]) / e - 1
    return res


def off_bucket(off: float) -> int:
    """Distance below the 20-day high: 0 = within 5%, 1 = 5-10%, 2 = 10-15%, 3 = 15% or more."""
    return 0 if off > -0.05 else 1 if off > -0.10 else 2 if off > -0.15 else 3


def cell_of(e: dict) -> tuple:
    return (max(2, round(e["depth"] / STEP)), e["up"], off_bucket(e["off_high"]), e["after_drop"])


def scan(sym: str, bars: list[dict]) -> tuple[list[dict], dict[tuple, list[dict]]]:
    """(touches, control trades by depth bucket) for one stock."""
    n = len(bars)
    pre = [0.0]
    for b in bars:
        pre.append(pre[-1] + b["c"])
    sma = lambda i, m: (pre[i] - pre[i - m]) / m if i >= m else None
    pivs = pivots(bars)
    gaps: list[tuple[float, float]] = []   # (bottom, top) of up-gaps not yet filled
    bands: list[float] = []
    last = {t: -COOL for t in TYPES}
    events, control = [], {}
    for i in range(1, n):
        b, pc = bars[i], bars[i - 1]["c"]
        if i >= WARM and i + max(HOLDS) < n:
            if i % 5 == 0 or not bands:
                bands = dense_bands(bars, i)
            ps = [bars[j]["l"] for j in pivs[bisect.bisect_left(pivs, i - LOOK):bisect.bisect_right(pivs, i - PIV - 1)]]
            smas = {f"sma{m}": sma(i, m) for m in (50, 100, 200)}
            every = ps + [v for v in smas.values() if v] + bands + [t for _, t in gaps] + [bt for bt, _ in gaps]
            below = lambda xs: max((x for x in xs if x and pc >= x * (1 + APPROACH) and x >= pc * (1 - MAXD)), default=None)
            lv = {"pivot": below(ps), "sma50": below([smas["sma50"]]), "sma100": below([smas["sma100"]]),
                  "sma200": below([smas["sma200"]]), "volume": below(bands), "gap": below([t for _, t in gaps])}
            hi20 = max(x["h"] for x in bars[i - 20:i])
            drop10 = any(bars[k]["c"] / bars[k - 1]["c"] - 1 <= -0.08 for k in range(i - 10, i))
            for t, level in lv.items():
                if level is None or b["l"] > level or i - last[t] < COOL:
                    continue
                res = outcome(bars, i, level)
                if not res:
                    continue
                last[t] = i
                others = [x for x in every if abs(x / level - 1) <= NEAR and abs(x - level) > 1e-9]
                events.append({"sym": sym, "day": b["d"], "type": t, "level": level, "depth": 1 - level / pc,
                               "stacked": bool(others), "up": smas["sma200"] is not None and pc > smas["sma200"],
                               "crash_day": b["l"] / pc - 1 <= -0.08, "after_drop": drop10,
                               "off_high": pc / hi20 - 1, **res})
            # control: the same depth, trend, distance from the 20-day high and recent crash, no level within NEAR
            cell = (smas["sma200"] is not None and pc > smas["sma200"], off_bucket(pc / hi20 - 1), drop10)
            k = 2
            while k * STEP <= MAXD:
                lvl = pc * (1 - k * STEP)
                if b["l"] > lvl:
                    break
                if not any(abs(x / lvl - 1) <= NEAR for x in every if x):
                    res = outcome(bars, i, lvl)
                    if res:
                        control.setdefault((k, *cell), []).append({"day": b["d"], **res})
                k += 1
        # bookkeeping for the next session: fill gaps, open new ones
        gaps = [(bt, tp) for bt, tp in gaps if b["l"] > bt]
        if i >= 1 and b["l"] > bars[i - 1]["h"]:
            gaps.append((bars[i - 1]["h"], b["l"]))
    return events, control


def attach_control(events: list[dict], control: dict[str, dict[tuple, list[dict]]]) -> None:
    """Each touch gets the mean outcome of control trades in its cell (same depth to 0.5%, same side of the 200-day
    average, same distance band from the 20-day high, same crash-in-the-last-10-days flag): its own stock's when it
    has at least 5 there, else all stocks'."""
    keys = ["held", "r5", "r10", "r20", "stop20"] + [f"r{h}" for h in LONG]

    def mean(rows):
        out = {}
        for x in keys:
            v = [float(r[x]) for r in rows if r.get(x) is not None]
            out[x] = statistics.mean(v) if len(v) >= 3 else None
        return out
    pooled_rows: dict[tuple, list[dict]] = {}
    for c in control.values():
        for cell, rows in c.items():
            pooled_rows.setdefault(cell, []).extend(rows)
    pooled = {cell: mean(rows) for cell, rows in pooled_rows.items() if len(rows) >= 5}
    cache: dict[tuple, dict | None] = {}
    for e in events:
        cell = cell_of(e)
        key = (e["sym"], *cell)
        if key not in cache:
            rows = control.get(e["sym"], {}).get(cell, [])
            cache[key] = mean(rows) if len(rows) >= 5 else pooled.get(cell)
        c = cache[key]
        e["ctrl"] = c
        if c:
            for x in keys:
                if e.get(x) is not None and c.get(x) is not None:
                    e[f"x_{x}"] = float(e[x]) - c[x]


def first_touch(events: list[dict]) -> list[dict]:
    """One per stock and day: the highest level touched (the one a buyer would reach first)."""
    best: dict[tuple[str, str], dict] = {}
    for e in events:
        key = (e["sym"], e["day"])
        if key not in best or e["level"] > best[key]["level"]:
            best[key] = e
    return list(best.values())


def groups(events: list[dict]) -> dict[str, list[dict]]:
    ft = first_touch(events)
    g = {"全部（同一天同一只只算最先碰到的那条线）": ft}
    for t, name in TYPES.items():
        g[f"　{name}"] = [e for e in events if e["type"] == t]
    g["几条线重合（1.5% 内还有别的线）"] = [e for e in ft if e["stacked"]]
    g["只有一条线"] = [e for e in ft if not e["stacked"]]
    g["股价在 200 日均线上方（上升趋势）"] = [e for e in ft if e["up"]]
    g["股价在 200 日均线下方（下降趋势）"] = [e for e in ft if not e["up"]]
    g["当天就跌了 8% 以上（直接砸穿）"] = [e for e in ft if e["crash_day"]]
    g["前 10 天里有一天跌了 8% 以上（像谷歌今天）"] = [e for e in ft if e["after_drop"]]
    g["已经离 20 日高点 15% 以上"] = [e for e in ft if e["off_high"] <= -0.15]
    g["七巨头"] = [e for e in ft if e["sym"] in MAG7]
    g["2022 年起"] = [e for e in ft if e["day"] >= "2022-01-01"]
    return g


def row(es: list[dict]) -> dict | None:
    xs = [e for e in es if "x_r20" in e]
    if len(xs) < 20:
        return None
    m = lambda k: statistics.mean(float(e[k]) for e in xs)
    return {"n": len(xs), "days": len({e["day"] for e in xs}), "held": m("held"), "ctrl_held": m("held") - m("x_held"),
            "r5": m("r5"), "r10": m("r10"), "r20": m("r20"), "r20_med": statistics.median(e["r20"] for e in xs),
            "win20": sum(e["r20"] > 0 for e in xs) / len(xs), "ctrl_r20": m("r20") - m("x_r20"), "x_r20": m("x_r20"),
            "x_r10": m("x_r10"), "stop20": m("stop20"), "ctrl_stop20": m("stop20") - m("x_stop20")}


def per_day(es: list[dict], key: str) -> list[tuple[str, float]]:
    by: dict[str, list[float]] = {}
    for e in es:
        if e.get(key) is not None:
            by.setdefault(e["day"], []).append(float(e[key]))
    return [(d, statistics.mean(v)) for d, v in sorted(by.items())]


def report(events: list[dict], control: dict[str, dict[tuple, list[dict]]], end: str) -> tuple[dict, str]:
    attach_control(events, control)
    g = groups(events)
    tab = {}
    md = ["| 情况 | 次数 | 当天收在线上 | 对照 | 5 天 | 10 天 | 20 天 | 20 天中位数 | 20 天赚的比例 | 对照 20 天 | **比对照多** | 5% 止损版 | 对照止损版 |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, es in g.items():
        r = row(es)
        if not r:
            continue
        tab[name] = r
        md.append(f"| {name} | {r['n']} | {r['held']:.0%} | {r['ctrl_held']:.0%} | {r['r5']:+.1%} | {r['r10']:+.1%} | {r['r20']:+.1%} | "
                  f"{r['r20_med']:+.1%} | {r['win20']:.0%} | {r['ctrl_r20']:+.1%} | **{r['x_r20']:+.1%}** | {r['stop20']:+.1%} | {r['ctrl_stop20']:+.1%} |")
    rob = {}
    for name in ("全部（同一天同一只只算最先碰到的那条线）", "　前低（一年内的波段低点）", "　200 日均线", "几条线重合（1.5% 内还有别的线）",
                 "前 10 天里有一天跌了 8% 以上（像谷歌今天）"):
        es = g.get(name) or []
        rob[f"{name.strip()} · 拿 20 天"] = robust.summary(per_day(es, "r20"), end, extra_cost=STOCK_COST)
        rob[f"{name.strip()} · 比对照多（20 天）"] = robust.summary(per_day(es, "x_r20"), end, extra_cost=STOCK_COST)
    return {"groups": tab, "robust": rob}, "\n".join(md) + "\n\n" + robust.table(rob, "稳健性检查（每天一笔：当天所有碰线的平均）") + "\n"


def baseline(data: dict[str, list[dict]]) -> dict[str, float]:
    """Buy at the close every BASE_STEP-th session (after the same warm-up), no condition: the drift of these names."""
    out = {}
    for h in (20,) + LONG:
        v = [bars[i + h]["c"] / bars[i]["c"] - 1 for bars in data.values() for i in range(WARM, len(bars) - h, BASE_STEP)]
        out[f"r{h}"] = statistics.mean(v) if v else None
    return out


def long_table(events: list[dict], base: dict[str, float], end: str) -> tuple[dict, str]:
    g = groups(events)
    names = ["全部（同一天同一只只算最先碰到的那条线）", "　前低（一年内的波段低点）", "　200 日均线", "几条线重合（1.5% 内还有别的线）",
             "股价在 200 日均线下方（下降趋势）", "前 10 天里有一天跌了 8% 以上（像谷歌今天）", "七巨头"]
    md = ["| 情况 | " + " | ".join(f"{h} 天：碰线 / 对照 / 差（次数）" for h in (20,) + LONG) + " |",
          "|---|" + "---|" * (1 + len(LONG))]
    tab = {}
    for name in names:
        es = g.get(name) or []
        cells, row_ = [], {}
        for h in (20,) + LONG:
            xs = [e for e in es if e.get(f"x_r{h}") is not None]
            if len(xs) < 20:
                cells.append("—")
                continue
            m = statistics.mean(e[f"r{h}"] for e in xs)
            x = statistics.mean(e[f"x_r{h}"] for e in xs)
            row_[h] = {"n": len(xs), "mean": m, "ctrl": m - x, "x": x, "median": statistics.median(e[f"r{h}"] for e in xs)}
            cells.append(f"{m:+.1%} / {m - x:+.1%} / **{x:+.1%}**（{len(xs)}）")
        tab[name] = row_
        md.append(f"| {name.strip()} | " + " | ".join(cells) + " |")
    md.append("| 对照二：随便哪天收盘买（同一批股票，每 5 天一次） | " + " | ".join(
        f"{base[f'r{h}']:+.1%}" if base.get(f"r{h}") is not None else "—" for h in (20,) + LONG) + " |")
    rob = {}
    for name in names[:1] + names[5:6]:
        es = g.get(name) or []
        for h in LONG:
            rob[f"{name.strip()} · 拿 {h} 天"] = robust.summary(per_day(es, f"r{h}"), end, extra_cost=STOCK_COST)
            rob[f"{name.strip()} · 拿 {h} 天比对照多"] = robust.summary(per_day(es, f"x_r{h}"), end, extra_cost=STOCK_COST)
    return {"long": tab, "base": base, "long_robust": rob}, "\n".join(md) + "\n\n" + robust.table(rob, "稳健性检查（拿得久的版本，每天一笔）") + "\n"


def per_name(events: list[dict], names: list[str]) -> str:
    md = ["| 股票 | 碰线次数 | 当天收在线上 | 对照 | 20 天 | 对照 20 天 | 比对照多 |", "|---|---|---|---|---|---|---|"]
    for s in names:
        r = row([e for e in first_touch(events) if e["sym"] == s])
        if r:
            md.append(f"| {s} | {r['n']} | {r['held']:.0%} | {r['ctrl_held']:.0%} | {r['r20']:+.1%} | {r['ctrl_r20']:+.1%} | {r['x_r20']:+.1%} |")
    return "\n".join(md) + "\n"


def main() -> None:
    from hero.research_ideas import save
    syms = sorted({e["symbol"] for e in json.loads(EVENTS.read_text())["events"]})

    def load(s):
        for k in range(3):
            try:
                return s, ohlcv(s)
            except Exception:
                time.sleep(2 * (k + 1))
        return s, []
    with ThreadPoolExecutor(8) as ex:
        data = dict(ex.map(load, syms))
    events, control = [], {}
    for s, bars in data.items():
        if len(bars) > WARM + 30:
            ev, ctl = scan(s, bars)
            events += ev
            control[s] = ctl
    end = max(e["day"] for e in events)
    tab, body = report(events, control, end)
    ltab, lbody = long_table(events, baseline(data), end)
    names = ["MSFT", "GOOGL", "META", "AMZN", "ORCL", "NVDA"]
    rep = {"generated": date.today().isoformat(), "start": START, "end": end, "symbols": len([s for s in data if data[s]]),
           "events": len(events), "control": sum(len(v) for c in control.values() for v in c.values()), **tab, **ltab}
    head = [f"# 碰到支撑位买，有用吗？ {rep['generated']}", "",
            f"{rep['symbols']} 只大公司（research/2026-10-02-earnings-reactions.json 的名单），Yahoo 日线 {START[:4]} 年起。"
            "每天只用之前的数据找出各类支撑线里离现价最近的一条：一年内的波段低点、50 / 100 / 200 日均线、半年成交密集区、没补的跳空缺口。"
            "「碰线」= 前一天收盘至少高出这条线 1%（15% 以内），当天最低价碰到或跌破。在线上挂买单（开盘就在线下则按开盘价），"
            "拿 5 / 10 / 20 个交易日收盘卖；止损版是买价下方 5% 止损（当天也算，跳空按开盘价），否则第 20 天卖。同一类线碰过后 10 天内不重复算。",
            "",
            "**对照**：同样跌幅（离前收的距离，按 0.5% 分档）、同样趋势（在 200 日均线上方还是下方）、离 20 日高点同样远（5% / 10% / 15% 分档）、前 10 天同样有没有单日大跌 8%，"
            "但 1.5% 以内没有任何一类支撑线的日子，同样挂单买、同样卖；先用同一只股票的，不够 5 次用全部股票的。"
            "「比对照多」才是这条线本身的作用。「当天收在线上」= 当天收盘守住了这条线。"
            "稳健性每天只算一笔（当天所有碰线的平均），因为大跌的时候很多股票同时碰线。正股成本按买卖各 0.1%。"
            "幸存者偏差：都是今天的大公司，过去跌下去没回来的不在名单里。只研究，不改交易。", ""]
    md = ("\n".join(head) + "\n" + body + "\n## 拿久一点：3 个月、半年、一年（用户 10-29「但就是拿久一点」）\n\n"
          "同样的碰线和对照，拿 60 / 120 / 250 个交易日收盘卖（数据够长的才算）。「对照二」是同一批股票随便哪天收盘买，看这批股票本身涨了多少。"
          "拿得越久，每笔重叠越多，各年份那一栏比平均数更可信。\n\n" + lbody
          + "\n## 这六只自己的历史（所有类型合在一起）\n\n" + per_name(events, names))
    save("support", rep, md)


if __name__ == "__main__":
    sys.exit(main())
