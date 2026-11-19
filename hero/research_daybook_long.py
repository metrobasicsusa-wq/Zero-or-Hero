"""The daybook's days with long-dated options instead of same-day ones (owner, 11-19: "这里期权都选择长期的").

research/2026-11-19-daybook-replay.md: the daybook calls a big day well, but a same-day option bought on its word
loses like on any other day -- the option is already dear and has to be right by 15:30. Here the same days, the same
10:00 entry, but options with a month or a quarter to run, on real prices since 2024-02.

Fixed before any of it was run:
  days      every day; today's condition (SPY 0.5% or more from its open at 10:00); the daybook's open flag ("closes
            more than 1% from yesterday's close" read at 40% or more); its 10:00 flag ("moves 0.5% or more either way
            from 10:00 to the close" read at 50% or more) -- the flags of research_daybook_replay;
  contract  SPY, the first monthly expiry (third Friday, or the session before when that is a holiday) at least 25 days
            out ("一个月") and at least 80 days out ("三个月"), the strike the nearest multiple of $5 to the 10:00 price;
  position  "顺着" one option with the move since the open (a call above the open, a put below); "两边" the call and the
            put together;
  entry     the close of the first minute bar of each leg from 10:00 (within half an hour, else the day is left out);
  exit      the option's daily close that day, the next session and five sessions on;
  costs     1% each way (what a penny-wide SPY monthly costs) and 5% each way (the house number for month-out options);
  the line  a flag PASSES for a tenor, position and holding period when, at 5% costs, its days average above zero, still
            do without their three best days, beat the other days in both halves of the period, and number 60 or more.
36 lines are looked at; a few will pass by chance, so a pass that does not show at the neighbouring tenor and holding
period counts for little, and a passing flag is split into the days today's condition already has and the rest.
Record only. Run: python -m hero.research_daybook_long (needs the Alpaca keys: the research workflow, study "daybook_long").
"""

from __future__ import annotations

import statistics
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from hero import daybook as db
from hero import robust
from hero.research_daybook_replay import FLAG_1000, FLAG_OPEN, walk
from hero.research_event_turn import Pace
from hero.research_flow_daily import ERRORS, fetch
from hero.research_flow_open import utc
from hero.research_ideas import save

ROOT = Path(__file__).resolve().parent.parent
START = "2024-02-01"
TENORS = {"1m": ("一个月", 25), "3m": ("三个月", 80)}
HOLDS = {"close": ("当天收盘", 0), "1d": ("第二天收盘", 1), "5d": ("5 天后收盘", 5)}
POSITIONS = {"with": "顺着方向买一张", "both": "看涨看跌两边都买"}
COSTS = (0.01, 0.05)
STEP, NEED_DAYS = 5, 60
BUDGET = 110 * 60
FLAGS = {
    "cond": ("现在的条件（10:00 离开盘 0.5% 以上）", lambda r: r["m10"] is not None and abs(r["m10"]) >= 0.005),
    "open": (f"档案开盘时说「会大」（{FLAG_OPEN:.0%} 以上）", lambda r: "open" in r and r["open"]["big"][0] >= FLAG_OPEN),
    "1000": (f"档案 10:00 说「还会动」（{FLAG_1000:.0%} 以上）", lambda r: "1000" in r and r["1000"]["move10"][0] >= FLAG_1000),
}


def third_friday(year: int, month: int) -> date:
    d = date(year, month, 1)
    return d + timedelta(days=(4 - d.weekday()) % 7 + 14)


def expiry(day: str, min_days: int, sessions: set[str]) -> str:
    """The first monthly expiry at least `min_days` after `day`; the session before when that Friday is a holiday
    (known only for dates the cards cover)."""
    d = date.fromisoformat(day)
    y, m = d.year, d.month
    while True:
        e = third_friday(y, m)
        if (e - d).days >= min_days:
            near = [e - timedelta(days=k) for k in range(4)]            # the Friday, or the session just before a holiday
            return next((x.isoformat() for x in near if x.isoformat() in sessions), e.isoformat())
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def occ(expiry_day: str, kind: str, strike: float, und: str = "SPY") -> str:
    return f"{und}{date.fromisoformat(expiry_day):%y%m%d}{kind}{int(round(strike * 1000)):08d}"


def legs(day: str, spot: float, sessions: set[str], und: str = "SPY") -> dict[str, dict]:
    strike = round(spot / STEP) * STEP
    out = {}
    for t, (_, min_days) in TENORS.items():
        e = expiry(day, min_days, sessions)
        out[t] = {"exp": e, "strike": strike, "C": occ(e, "C", strike, und), "P": occ(e, "P", strike, und)}
    return out


def first_close(bars: list[dict]) -> float | None:
    return float(min(bars, key=lambda b: b["t"])["c"]) if bars else None


def run(client, cards: list[dict], clock=time.monotonic, pace=None, now: datetime | None = None) -> dict:
    pace, now = pace or Pace(client), now or datetime.now(timezone.utc)
    days = [c["day"] for c in cards]
    sessions, pos = set(days), {d: i for i, d in enumerate(days)}
    cap = (now - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")       # the newest minutes are not served
    rows, skipped, stopped, misses, t0 = [], [], None, 0, clock()
    for c in cards:
        if c["day"] < START or c.get("p1000") is None:
            continue
        if clock() - t0 > BUDGET:
            stopped = c["day"]
            break
        lg = legs(c["day"], c["p1000"], sessions)
        syms = [lg[t][k] for t in lg for k in "CP"]
        entry = fetch(pace, client.option_bars, syms, utc(c["day"], "10:00"), min(utc(c["day"], "10:30"), cap), timeframe="1Min")
        i = pos[c["day"]]
        last = days[min(i + 5, len(days) - 1)]
        daily = fetch(pace, client.option_bars, syms, f"{c['day']}T00:00:00Z", min(f"{last}T23:59:59Z", cap), timeframe="1Day") if entry is not None else None
        if entry is None or daily is None:
            skipped.append(c["day"])
            misses += 1
            if misses >= 3 and not rows:
                break                                                       # nothing works: stop instead of burning the run
            continue
        row = {"day": c["day"], "kind": "C" if c["m10"] >= 0 else "P", "legs": {}}
        for t, x in lg.items():
            closes = {k: {b["t"][:10]: float(b["c"]) for b in daily.get(x[k], [])} for k in "CP"}
            row["legs"][t] = {"exp": x["exp"], "strike": x["strike"],
                              "in": {k: first_close(entry.get(x[k], [])) for k in "CP"},
                              "out": {h: {k: closes[k].get(days[i + n]) if i + n < len(days) else None for k in "CP"} for h, (_, n) in HOLDS.items()}}
        rows.append(row)
    return {"generated": date.today().isoformat(), "start": START, "end": rows[-1]["day"] if rows else None, "rows": rows,
            "skipped": skipped, "stopped_at": stopped, "errors": list(ERRORS)}


def result(leg: dict, kind: str, position: str, hold: str, cost: float) -> float | None:
    """What $1 made: bought at the entry plus `cost`, sold at the exit less `cost`."""
    ks = (kind,) if position == "with" else ("C", "P")
    paid = [leg["in"][k] for k in ks]
    got = [leg["out"][hold][k] for k in ks]
    if any(x is None or x <= 0 for x in paid) or any(x is None for x in got):
        return None
    return sum(got) * (1 - cost) / (sum(paid) * (1 + cost)) - 1


def trades(rows: list[dict], reads: dict[str, dict], tenor: str, position: str, hold: str, cost: float, keep) -> list[tuple[str, float]]:
    out = []
    for r in rows:
        if r["day"] in reads and keep(reads[r["day"]]):
            x = result(r["legs"][tenor], r["kind"], position, hold, cost)
            if x is not None:
                out.append((r["day"], x))
    return out


def judge(on: list[tuple[str, float]], off: list[tuple[str, float]]) -> dict | None:
    """The fixed line: the flagged days at the higher cost against zero and against the other days."""
    if not on or not off:
        return None
    mean = lambda xs: statistics.mean(r for _, r in xs) if xs else None
    both = sorted(on + off)
    mid = both[len(both) // 2][0]
    halves = []
    for side in (lambda d: d < mid, lambda d: d >= mid):
        a, b = [x for x in on if side(x[0])], [x for x in off if side(x[0])]
        halves.append(mean(a) - mean(b) if a and b else None)
    rest = sorted(on, key=lambda x: -x[1])[3:]
    trim = mean(rest)
    ok = (mean(on) > 0 and trim is not None and trim > 0 and all(h is not None and h > 0 for h in halves) and len(on) >= NEED_DAYS)
    return {"n": len(on), "mean": mean(on), "other": mean(off), "trim": trim, "halves": halves, "win": sum(r > 0 for _, r in on) / len(on),
            "median": statistics.median(r for _, r in on), "pass": bool(ok)}


def num(x) -> str:
    return "—" if x is None else f"{x:+.1%}"


def report(rep: dict, reads: dict[str, dict]) -> tuple[dict, str]:
    rows, lo, hi = rep["rows"], COSTS[0], COSTS[1]
    table, out, total, passed = {}, [], 0, []
    for t, (tname, _) in TENORS.items():
        for p, pname in POSITIONS.items():
            out += ["", f"## {tname}到期，{pname}", "",
                    f"| 哪些日子 | 拿到 | 天数 | 每 $1 平均（成本 {hi:.0%}） | 成本 {lo:.0%} 时 | 中位数 | 赚的比例 | 去掉最好 3 天 | 其余日子 | 比其余日子好（前半 / 后半） | 过线 |",
                    "|---|---|---|---|---|---|---|---|---|---|---|"]
            for h, (hname, _) in HOLDS.items():
                every = trades(rows, reads, t, p, h, hi, lambda r: True)
                if every:
                    v = [r for _, r in every]
                    cheap = [r for _, r in trades(rows, reads, t, p, h, lo, lambda r: True)]
                    out.append(f"| 每天都做 | {hname} | {len(v)} | {num(statistics.mean(v))} | {num(statistics.mean(cheap))} | {num(statistics.median(v))} | "
                               f"{sum(x > 0 for x in v) / len(v):.0%} | {num(statistics.mean(sorted(v)[:-3]) if len(v) > 3 else None)} | — | — | — |")
                for f, (fname, keep) in FLAGS.items():
                    on = trades(rows, reads, t, p, h, hi, keep)
                    v = table[f"{t}|{p}|{h}|{f}"] = judge(on, trades(rows, reads, t, p, h, hi, lambda r, keep=keep: not keep(r)))
                    if not v:
                        continue
                    total += 1
                    if v["pass"]:
                        passed.append(f"{tname}、{pname}、{hname}、{fname}")
                    cheap = [r for _, r in trades(rows, reads, t, p, h, lo, keep)]
                    out.append(f"| {fname} | {hname} | {v['n']} | {num(v['mean'])} | {num(statistics.mean(cheap))} | {num(v['median'])} | {v['win']:.0%} | "
                               f"{num(v['trim'])} | {num(v['other'])} | {num(v['halves'][0])} / {num(v['halves'][1])} | {'过' if v['pass'] else '不过'} |")
    cond, flag = FLAGS["cond"][1], FLAGS["1000"][1]
    split = {"档案 10:00 点名、也满足现在的条件": lambda r: flag(r) and cond(r), "只有档案 10:00 点名（离开盘不到 0.5%）": lambda r: flag(r) and not cond(r)}
    extra = ["", "## 把「档案 10:00 点名」拆开（一个月到期、成本 5%）", "",
             "点名的日子里有一部分本来就满足现在的条件。多点出来的那部分不赚，过线就不算档案的功劳。", "",
             robust.table({f"{POSITIONS[p]}、{HOLDS[h][0]}：{name}": robust.summary(trades(rows, reads, "1m", p, h, hi, keep), extra_cost=0.0)
                           for p in POSITIONS for h in HOLDS for name, keep in split.items()}, "拆开").split("\n", 4)[4]]
    notes = [f"成本 {lo:.0%} 是 SPY 月度期权实际的买卖价差水平，{hi:.0%} 是我们给一个月左右的期权一直用的保守数；及格线按 {hi:.0%} 判。",
             "进场价是 10:00 起第一根分钟线的收盘价，出场价是期权当天的日线收盘价（最后一笔成交）。行权价取最接近 10:00 价格的 $5 整数倍。"]
    if rep.get("skipped"):
        notes.append(f"取数失败跳过的日子 {len(rep['skipped'])} 个：{'、'.join(rep['skipped'][:8])}")
    if rep.get("stopped_at"):
        notes.append(f"时间不够，只跑到 {rep['stopped_at']} 之前。")
    notes += [f"请求失败的原因（最后几条）：`{e}`" for e in (rep.get("errors") or [])[-3:]]
    head = [f"# 档案点名的日子，换成到期远的期权（{rep['generated']}）", "",
            f"{rep['start']} 至 {rep['end']}，{len(rows)} 个交易日，SPY，真实期权价。用户 11-19：这条线里的期权都选到期远的。"
            "同样的日子、同样在 10:00 买，但买一个月或三个月后到期的；顺着开盘以来的方向买一张，或者看涨看跌两边都买；拿到当天收盘、第二天收盘或 5 天后收盘。", "",
            f"及格线（跑之前写死，按成本 {hi:.0%} 判）：点名的日子平均为正、去掉最好 3 天仍为正、前后两半都比其余日子好、至少 {NEED_DAYS} 天。只研究，不改交易。", "",
            f"**{total} 条线过了 {len(passed)} 条" + ("：" + "；".join(passed) if passed else "") + "。**"
            "线多了总会有碰巧过的：相邻的到期和持有期不跟着过的，不算数。"]
    return table, "\n".join(head + out + extra + ["", "## 说明", ""] + [f"- {n}" for n in notes]) + "\n"


# ---- The one line written down after the first run (11-19), on other underlyings (owner: "不一定是大盘吧") ----
# research/2026-11-19-daybook-long.md, last section: the days the SPY daybook's 10:00 read says "more to come" while SPY
# is still under 0.5% from its open; a month-out call and put at the nearest $5 strike, bought at 10:00, sold at the
# close five sessions on. Passes on an underlying when, at 1% AND at 2% each way: the mean, the median, the mean without
# the three best days and the mean of each half are all above zero, there are 40 days or more, and with the days
# merged into stretches (under five sessions apart = one stretch) the stretches average above zero and most of them
# are positive. Indexes: QQQ and IWM must both pass. Stocks (the Magnificent 7, fixed before running): five of the
# seven must pass and so must the seven together (each day's average over the stocks). A control, not part of the
# line: every tenth session, the same trade.
INDEXES = ("QQQ", "IWM")
STOCKS = ("AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA")
LINE_COSTS, LINE_DAYS, STRETCH, CONTROL_EVERY, STOCKS_NEED = (0.01, 0.02), 40, 5, 10, 5


def only_flag(r: dict) -> bool:
    return FLAGS["1000"][1](r) and not FLAGS["cond"][1](r)


def run_others(client, cards: list[dict], reads: dict[str, dict], unds=INDEXES + STOCKS, clock=time.monotonic, pace=None,
               now: datetime | None = None) -> dict:
    pace, now = pace or Pace(client), now or datetime.now(timezone.utc)
    days = [c["day"] for c in cards]
    sessions, pos = set(days), {d: i for i, d in enumerate(days)}
    cap = (now - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    period = [d for d in days if d >= START]
    flagged = [d for d in period if d in reads and only_flag(reads[d])]
    control = [d for k, d in enumerate(period) if k % CONTROL_EVERY == 0 and d not in flagged]
    rows, skipped, stopped, t0 = [], [], None, clock()
    for d in sorted(flagged + control):
        if clock() - t0 > BUDGET:
            stopped = d
            break
        spots = fetch(pace, client.stock_bars, list(unds), utc(d, "10:00"), min(utc(d, "10:05"), cap), feed="sip")
        if spots is None:
            skipped.append(d)
            if len(skipped) >= 3 and not rows:
                break
            continue
        last = days[min(pos[d] + 5, len(days) - 1)]
        for und in unds:
            spot = first_close(spots.get(und, []))
            if not spot:
                continue
            lg = legs(d, spot, sessions, und)["1m"]
            syms = [lg["C"], lg["P"]]
            entry = fetch(pace, client.option_bars, syms, utc(d, "10:00"), min(utc(d, "10:30"), cap), timeframe="1Min")
            daily = fetch(pace, client.option_bars, syms, f"{d}T00:00:00Z", min(f"{last}T23:59:59Z", cap), timeframe="1Day") if entry is not None else None
            if entry is None or daily is None:
                continue
            i = pos[d]
            out_day = days[i + 5] if i + 5 < len(days) else None
            closes = {k: {b["t"][:10]: float(b["c"]) for b in daily.get(lg[k], [])} for k in "CP"}
            rows.append({"und": und, "day": d, "group": "flag" if d in flagged else "control", "spot": round(spot, 2), "strike": lg["strike"], "exp": lg["exp"],
                         "in": {k: first_close(entry.get(lg[k], [])) for k in "CP"}, "out": {k: closes[k].get(out_day) if out_day else None for k in "CP"}})
    return {"generated": date.today().isoformat(), "start": START, "flag_days": flagged, "control_days": len(control), "unds": list(unds),
            "rows": rows, "skipped": skipped, "stopped_at": stopped, "errors": list(ERRORS)}


def both(row: dict, cost: float) -> float | None:
    paid, got = [row["in"][k] for k in "CP"], [row["out"][k] for k in "CP"]
    if any(x is None or x <= 0 for x in paid) or any(x is None for x in got):
        return None
    return sum(got) * (1 - cost) / (sum(paid) * (1 + cost)) - 1


def stretches(xs: list[tuple[str, float]], pos: dict[str, int]) -> list[float]:
    """Days under STRETCH sessions apart are one stretch: its mean."""
    out: list[list[tuple[str, float]]] = []
    for d, r in sorted(xs):
        if out and pos[d] - pos[out[-1][-1][0]] < STRETCH:
            out[-1].append((d, r))
        else:
            out.append([(d, r)])
    return [statistics.mean(r for _, r in e) for e in out]


def line(by_cost: dict[float, list[tuple[str, float]]], pos: dict[str, int]) -> dict | None:
    """The fixed line for one underlying (or one day-averaged group): every check at every cost."""
    if not by_cost or not all(by_cost.values()):
        return None
    out, ok = {}, True
    for cost, xs in by_cost.items():
        xs = sorted(xs)
        v = sorted(r for _, r in xs)
        half = len(xs) // 2
        halves = [statistics.mean(r for _, r in xs[:half]) if half else None, statistics.mean(r for _, r in xs[half:])]
        st_ = stretches(xs, pos)
        c = {"n": len(v), "mean": statistics.mean(v), "median": statistics.median(v), "win": sum(r > 0 for r in v) / len(v),
             "trim": statistics.mean(v[:-3]) if len(v) > 3 else None, "halves": halves, "stretches": len(st_),
             "stretch_mean": statistics.mean(st_), "stretch_up": sum(m > 0 for m in st_)}
        c["ok"] = bool(c["mean"] > 0 and c["median"] > 0 and c["trim"] is not None and c["trim"] > 0 and all(h is not None and h > 0 for h in halves)
                       and c["n"] >= LINE_DAYS and c["stretch_mean"] > 0 and c["stretch_up"] * 2 > c["stretches"])
        ok = ok and c["ok"]
        out[f"{cost:.2f}"] = c
    return {"costs": out, "pass": ok}


def others_table(rep: dict, pos: dict[str, int]) -> tuple[dict, str]:
    rows = rep["rows"]
    trades_of = lambda keep, cost: [(r["day"], x) for r in rows if keep(r) and (x := both(r, cost)) is not None]
    table, out = {}, []
    lo, hi = LINE_COSTS

    def add(name: str, keep, key: str, by_cost=None) -> None:
        by_cost = by_cost or {c: trades_of(lambda r: r["group"] == "flag" and keep(r), c) for c in LINE_COSTS}
        v = table[key] = line(by_cost, pos)
        ctl = [x for _, x in trades_of(lambda r: r["group"] == "control" and keep(r), lo)]
        if not v:
            out.append(f"| {name} | 0 | — | — | — | — | — | — | — | {num(statistics.mean(ctl)) if ctl else '—'} | 没有数据 |")
            return
        a, b = v["costs"][f"{lo:.2f}"], v["costs"][f"{hi:.2f}"]
        out.append(f"| {name} | {a['n']} | {num(a['mean'])} / {num(a['median'])} | {a['win']:.0%} | {num(a['trim'])} | {num(a['halves'][0])} / {num(a['halves'][1])} | "
                   f"{a['stretch_up']} / {a['stretches']}，{num(a['stretch_mean'])} | {num(b['mean'])} / {num(b['median'])} | {num(b['trim'])} | "
                   f"{num(statistics.mean(ctl)) if ctl else '—'}（{len(ctl)}） | {'过' if v['pass'] else '不过'} |")

    def averaged(unds, cost):                                   # each day's average over the underlyings that have a price
        by: dict[str, list[float]] = {}
        for r in rows:
            if r["group"] == "flag" and r["und"] in unds and (x := both(r, cost)) is not None:
                by.setdefault(r["day"], []).append(x)
        return [(d, statistics.mean(v)) for d, v in sorted(by.items())]

    head = [f"| 标的 | 天数 | 平均 / 中位数（每边 {lo:.0%}） | 赚的比例 | 去掉最好 3 天 | 前半 / 后半 | 赚的段数 / 段数，段的平均 | 平均 / 中位数（每边 {hi:.0%}） | 去掉最好 3 天（{hi:.0%}） | 平时每 10 天做一次（{lo:.0%}） | 过线 |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    out += ["## 指数", "", *head]
    for u in INDEXES:
        if u in rep["unds"]:
            add(u, lambda r, u=u: r["und"] == u, u)
    out += ["", "## 个股（七巨头）", "", *head]
    stocks = [u for u in STOCKS if u in rep["unds"]]
    for u in stocks:
        add(u, lambda r, u=u: r["und"] == u, u)
    add("七只合在一起（每天取平均）", lambda r: r["und"] in stocks, "stocks_all", {c: averaged(stocks, c) for c in LINE_COSTS})
    got = lambda u: bool(table.get(u) and table[u]["pass"])
    verdict_ = {"indexes": all(got(u) for u in INDEXES if u in rep["unds"]) and any(u in rep["unds"] for u in INDEXES),
                "stocks": sum(got(u) for u in stocks) >= STOCKS_NEED and got("stocks_all"), "stocks_passed": sum(got(u) for u in stocks)}
    return {"lines": table, "verdict": verdict_}, "\n".join(out)


def report_others(rep: dict, pos: dict[str, int]) -> tuple[dict, str]:
    table, body = others_table(rep, pos)
    v = table["verdict"]
    notes = [f"点名的日子 {len(rep['flag_days'])} 天（SPY 档案 10:00 读数 {FLAG_1000:.0%} 以上、SPY 10:00 离开盘不到 0.5%），对照 {rep['control_days']} 天（每 {CONTROL_EVERY} 个交易日取 1 天）。",
             "个股的一个月期权会跨到财报，财报前后期权的贵贱变化很大，这里没有剔除。英伟达 2024-06 拆过股，拆股前后几天的合约对不上的会缺。",
             "行权价取最接近 10:00 价格的 $5 整数倍，股价低的（英伟达 $150 上下）离平值会差到 1% 多。"]
    if rep.get("skipped"):
        notes.append(f"取数失败跳过的日子 {len(rep['skipped'])} 个：{'、'.join(rep['skipped'][:8])}")
    if rep.get("stopped_at"):
        notes.append(f"时间不够，只跑到 {rep['stopped_at']} 之前。")
    notes += [f"请求失败的原因（最后几条）：`{e}`" for e in (rep.get("errors") or [])[-3:]]
    head = [f"# 到期远的期权那一条线，放到别的指数和个股上（{rep['generated']}）", "",
            "用户 11-19：「现在就跑吧，不一定是大盘吧」。线是 research/2026-11-19-daybook-long.md 末尾写死的那一条，没有改：SPY 档案 10:00 点名「今天还会动」、"
            "但 SPY 10:00 离开盘还不到 0.5% 的日子，一个月后到期、最接近现价的看涨看跌各一张，10:00 买，5 个交易日后收盘卖。真实期权价。", "",
            f"过线 = 每边 {LINE_COSTS[0]:.0%} 和 {LINE_COSTS[1]:.0%} 两档成本下都是：平均为正、中位数为正、去掉最好 3 天为正、前后两半为正、至少 {LINE_DAYS} 天，"
            f"并且把相隔不到 {STRETCH} 个交易日的日子合成一段后，段的平均为正、赚的段过半。指数要 QQQ 和 IWM 都过；个股（七巨头，跑之前定的）要 7 只里至少 {STOCKS_NEED} 只过，合在一起也过。只研究，不改交易。", "",
            f"**指数：{'过' if v['indexes'] else '不过'}。个股：{'过' if v['stocks'] else '不过'}（7 只里过了 {v['stocks_passed']} 只）。**", ""]
    return table, "\n".join(head) + body + "\n\n## 说明\n\n" + "\n".join(f"- {n}" for n in notes) + "\n"


def main_others() -> None:
    from hero.alpaca import Alpaca
    cards = db.load()
    reads = {r["day"]: r for r in walk(db.Book(cards), START)}
    rep = run_others(Alpaca(), cards, reads)
    table, md = report_others(rep, {c["day"]: i for i, c in enumerate(cards)})
    save("daybook-long-others", {**rep, "table": table}, md)


def main() -> None:
    from hero.alpaca import Alpaca
    cards = db.load()
    rep = run(Alpaca(), cards)
    reads = {r["day"]: r for r in walk(db.Book(cards), START)}
    table, md = report(rep, reads)
    save("daybook-long", {**rep, "table": table}, md)


if __name__ == "__main__":
    sys.exit(main())
