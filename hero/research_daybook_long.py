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


def occ(expiry_day: str, kind: str, strike: float) -> str:
    return f"SPY{date.fromisoformat(expiry_day):%y%m%d}{kind}{int(round(strike * 1000)):08d}"


def legs(day: str, spot: float, sessions: set[str]) -> dict[str, dict]:
    strike = round(spot / STEP) * STEP
    out = {}
    for t, (_, min_days) in TENORS.items():
        e = expiry(day, min_days, sessions)
        out[t] = {"exp": e, "strike": strike, "C": occ(e, "C", strike), "P": occ(e, "P", strike)}
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


def main() -> None:
    from hero.alpaca import Alpaca
    cards = db.load()
    rep = run(Alpaca(), cards)
    reads = {r["day"]: r for r in walk(db.Book(cards), START)}
    table, md = report(rep, reads)
    save("daybook-long", {**rep, "table": table}, md)


if __name__ == "__main__":
    sys.exit(main())
