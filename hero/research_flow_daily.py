"""After a day of unusually heavy put (or call) volume in a stock, what does the stock do next? (owner, 11-18: is
watching option volume any use.)

research/2026-10-02-options-flow.md tried one reading (a call surge on a breakout day, on a list picked with
hindsight) and found nothing; research/2026-11-18-flow-open*.md tried the first half hour and found nothing. This is
the daily reading people usually mean by "unusual option activity": the day's volume of near-the-money, near-dated
puts or calls against the stock's own last 20 days.

Stocks: data/universe.json (this month's 100 most traded operating companies; the list leans to recent winners, so
only signal days against the other days of the SAME stocks count). Since 2024-02. Per stock and day: volume of every
contract with a strike within BAND of the previous day's close and an expiry in the next FAR days. A SURGE is SURGE x
the average of the stock's previous LOOKBACK days. The move measured is the stock's close-to-close return
minus SPY's over the next 1 and 5 trading days.

Fixed before looking -- four signals, each with the direction its believers expect:
  put_surge          puts surge                                   -> the stock lags SPY
  put_surge_flat     puts surge on a day the stock moved under 2% -> the stock lags SPY ("someone knows")
  call_surge_quiet   calls surge, not a 20-day closing high       -> the stock beats SPY
  call_surge_break   calls surge on a 20-day closing high         -> the stock beats SPY (the 10-02 signal, this pool)
A signal PASSES at a horizon when its days beat the other days of the same stocks, in the expected direction, by
NEED (0.2% over 1 day, 0.5% over 5), the gap has the same sign in both halves of the period, and there are 100 signal
days or more. Earnings days are not removed (no history of dates here); put_surge_flat is the line least touched by
them. The pool runs in three interleaved parts (a, b, c: every third stock by dollar volume), one workflow run each, so
no run has to beat the 150-minute limit; `python -m hero.research_flow_daily combine` then reads the three files and
writes the one table. Record only. Run: python -m hero.research_flow_daily [a|combine] / research_flow_daily_b / _c
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from hero import robust
from hero.research_event_turn import Pace
from hero.research_flow import months
from hero.research_ideas import save

ROOT = Path(__file__).resolve().parent.parent
START, LEAD = "2024-02-01", "2023-12-15"   # LEAD: twenty days of prices before the first day
BAND, FAR, WIDE = 0.10, 21, 0.20   # WIDE: strikes listed for a month around the previous month's last close
SURGE, LOOKBACK, FLAT = 3.0, 20, 0.02
HORIZONS = (1, 5)
NEED = {1: 0.002, 5: 0.005}
NEED_DAYS = 100
BUDGET = 120 * 60          # seconds of fetching before the run stops taking new stocks (the workflow allows 150 minutes)
SIGNALS = {"put_surge": ("看跌量暴增", -1), "put_surge_flat": (f"看跌量暴增、当天股价涨跌不到 {FLAT:.0%}", -1),
           "call_surge_quiet": ("看涨量暴增、没创 20 日新高", 1), "call_surge_break": ("看涨量暴增、创 20 日新高", 1)}


def pool() -> list[str]:
    return list(json.loads((ROOT / "data" / "universe.json").read_text())["stocks"])


def fetch(pace, call, *args, **kw):
    """One request, three tries. None when it keeps failing: the caller drops the stock rather than count a hole as a
    quiet day."""
    for attempt in range(3):
        pace.wait()
        try:
            return call(*args, **kw)
        except Exception:
            time.sleep(3 * (attempt + 1))
    return None


def volumes(client, pace, sym: str, raw: dict[str, float], end: str) -> dict[str, dict[str, float]] | None:
    """day -> {"call": volume, "put": volume} of contracts within BAND of the previous close that expire within FAR
    days of that day."""
    days, out = sorted(raw), {}
    ref = {d: raw[days[i - 1]] for i, d in enumerate(days) if i}
    for m0, m1 in months(START, end):
        mine = [d for d in days if m0 <= d <= m1]
        before = [d for d in days if d < m0]
        if not mine:
            continue
        spot = raw[before[-1]] if before else raw[mine[0]]
        cs: dict[str, dict] = {}
        for status in ("inactive", "active"):
            got = fetch(pace, client.option_contracts, sym, status=status, expiration_date_gte=m0,
                        expiration_date_lte=(date.fromisoformat(m1) + timedelta(days=FAR)).isoformat(),
                        strike_price_gte=f"{spot * (1 - WIDE):.2f}", strike_price_lte=f"{spot * (1 + WIDE):.2f}")
            if got is None:
                return None
            cs.update({c["symbol"]: c for c in got})
        syms = sorted(cs)
        stop = (date.fromisoformat(m1) + timedelta(days=1)).isoformat()   # the end date is exclusive of that day's bar
        for i in range(0, len(syms), 100):
            bars = fetch(pace, client.option_bars, syms[i:i + 100], m0, stop)
            if bars is None:
                return None
            for s, bs in bars.items():
                exp, kind, strike = cs[s]["expiration_date"], cs[s]["type"], float(cs[s]["strike_price"])
                for b in bs:
                    d = b["t"][:10]
                    if (m0 <= d <= m1 and d in ref and abs(strike / ref[d] - 1) <= BAND
                            and d <= exp <= (date.fromisoformat(d) + timedelta(days=FAR)).isoformat()):
                        out.setdefault(d, {"call": 0.0, "put": 0.0})[kind] += float(b.get("v") or 0)
    return out


def closes(client, symbols: list[str], adjustment: str) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for i in range(0, len(symbols), 50):
        for s, bs in client.daily_bars(symbols[i:i + 50], LEAD, adjustment=adjustment).items():
            out[s] = {b["t"][:10]: float(b["c"]) for b in bs}
    return out


def events(days: list[str], adj: list[float], vol: dict[str, dict[str, float]]) -> dict[int, set[str]]:
    """index of the day -> the signals it carries. One stock; days from LOOKBACK on are judged."""
    out = {}
    for i in range(LOOKBACK, len(days)):
        if days[i - LOOKBACK] < START:      # the whole window needs volume: there is none before START
            continue
        got = set()
        for kind in ("put", "call"):
            hist = [vol.get(d, {}).get(kind, 0.0) for d in days[i - LOOKBACK:i]]
            avg = sum(hist) / LOOKBACK
            if avg > 0 and vol.get(days[i], {}).get(kind, 0.0) >= SURGE * avg:
                if kind == "put":
                    got.add("put_surge")
                    if abs(adj[i] / adj[i - 1] - 1) < FLAT:
                        got.add("put_surge_flat")
                else:
                    got.add("call_surge_break" if adj[i] >= max(adj[i - LOOKBACK:i + 1]) else "call_surge_quiet")
        out[i] = got
    return out


def part_of(part: str) -> list[str]:
    """Every third stock of the pool: each part has its share of the heavy names and is a fair sample by itself."""
    return pool()["abc".index(part)::3]


def run(client, symbols: list[str], clock=time.monotonic) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    pace, began = Pace(client), clock()
    raw, adj = closes(client, symbols, "raw"), closes(client, symbols + ["SPY"], "all")
    spy = adj.get("SPY", {})
    rows, done, dropped, left = [], [], [], []
    for sym in symbols:
        if clock() - began > BUDGET:
            left.append(sym)
            continue
        if sym not in raw or sym not in adj:
            dropped.append(sym)
            continue
        vol = volumes(client, pace, sym, raw[sym], end)
        if vol is None:
            dropped.append(sym)
            continue
        days = [d for d in sorted(adj[sym]) if d in spy and d <= end]
        px, bench = [adj[sym][d] for d in days], [spy[d] for d in days]
        for i, got in events(days, px, vol).items():
            fwd = {str(h): round((px[i + h] / px[i] - 1) - (bench[i + h] / bench[i] - 1), 5) for h in HORIZONS if i + h < len(days)}
            if fwd:
                rows.append({"sym": sym, "day": days[i], "sig": sorted(got), "fwd": fwd})
        done.append(sym)
    return {"generated": date.today().isoformat(), "start": START, "end": end, "stocks": done, "dropped": dropped,
            "not_reached": left, "rows": rows}


def line(rows: list[dict], signal: str, h: int) -> dict | None:
    """The fixed line: signal days against the other days of the same stocks, in the direction the signal expects."""
    sign, k = SIGNALS[signal][1], str(h)
    on = sorted((r["day"], sign * r["fwd"][k]) for r in rows if signal in r["sig"] and k in r["fwd"])
    off = sorted((r["day"], sign * r["fwd"][k]) for r in rows if signal not in r["sig"] and k in r["fwd"])
    if not on or not off:
        return None
    mean = lambda v: statistics.mean(x for _, x in v)
    mid = on[len(on) // 2][0]
    halves = []
    for keep in (lambda d: d < mid, lambda d: d >= mid):
        a, b = [x for x in on if keep(x[0])], [x for x in off if keep(x[0])]
        halves.append(mean(a) - mean(b) if a and b else None)
    diff = mean(on) - mean(off)
    ok = diff >= NEED[h] and all(x is not None and x > 0 for x in halves) and len(on) >= NEED_DAYS
    return {"n": len(on), "stocks": len({r["sym"] for r in rows if signal in r["sig"]}), "on": mean(on), "off": mean(off),
            "off_n": len(off), "diff": diff, "halves": halves, "hit_on": sum(x > 0 for _, x in on) / len(on),
            "hit_off": sum(x > 0 for _, x in off) / len(off), "pass": ok, "flags": robust.flags(robust.summary(on))}


def report(rep: dict) -> tuple[dict, str]:
    pct = lambda v: "—" if v is None else f"{v:+.2%}"
    table, out, total, passed = {}, [], 0, 0
    out += ["| 信号（相信它的人期待的方向） | 之后 | 信号日：次数（股票数） | 信号日的平均 | 其他日子的平均 | 差（前半 / 后半） | "
            "方向对的比例：信号日 / 其他 | 过线 | 提醒 |", "|---|---|---|---|---|---|---|---|---|"]
    for signal, (name, sign) in SIGNALS.items():
        for h in HORIZONS:
            v = table[f"{signal}|{h}"] = line(rep["rows"], signal, h)
            if not v:
                continue
            total, passed = total + 1, passed + bool(v["pass"])
            want = "跑输 SPY" if sign < 0 else "跑赢 SPY"
            out.append(f"| {name}（{want}） | {h} 天 | {v['n']}（{v['stocks']}） | {pct(v['on'])} | {pct(v['off'])} | "
                       f"{pct(v['diff'])}（{pct(v['halves'][0])} / {pct(v['halves'][1])}） | {v['hit_on']:.0%} / {v['hit_off']:.0%} | "
                       f"{'过' if v['pass'] else '不过'} | {'；'.join(v['flags']) or '—'} |")
    gaps = []
    if rep.get("not_reached"):
        gaps.append(f"时间不够没跑到的 {len(rep['not_reached'])} 只（按成交额排在后面的）：{'、'.join(rep['not_reached'])}")
    if rep.get("dropped"):
        gaps.append(f"数据取不全、整只去掉的 {len(rep['dropped'])} 只：{'、'.join(rep['dropped'])}")
    head = [f"# 个股期权量暴增之后，股价怎么走（{rep['generated']}）", "",
            f"{rep['start']} 至 {rep['end']}，股票池是 data/universe.json 的 100 只（这个月成交额最大的公司，偏向最近的赢家，所以只比同一批股票的"
            f"信号日和其他日子），实际跑了 {len(rep['stocks'])} 只。每只每天：行权价在前一天收盘价上下 {BAND:.0%} 以内、{FAR} 天内到期的全部合约的成交量，"
            f"看涨和看跌分开；当天的量到前 {LOOKBACK} 天平均的 {SURGE:g} 倍算「暴增」。之后 1 天、5 天看这只股票的涨跌减去 SPY 同期的涨跌。", "",
            "表里的数都已经按「相信这个信号的人期待的方向」取正负：看跌量暴增期待跑输 SPY，所以它那几行是「SPY 的涨跌减股票的涨跌」，正数 = 如他们所愿。"
            f"及格线在看结果之前写死：信号日比其他日子多 {NEED[1]:.1%}（1 天）/ {NEED[5]:.1%}（5 天）以上、前后两半同号、信号日至少 {NEED_DAYS} 次。"
            "财报日没有剔除（这里没有历史财报日期）；「当天股价涨跌不到 2%」那一行受财报影响最小。只研究，不改交易。", "",
            f"**{total} 条线过了 {passed} 条。**", ""]
    return table, "\n".join(head + out + ([""] + [f"- {g}" for g in gaps] if gaps else [])) + "\n"


def pack(rows: list[dict]) -> list[list]:
    """[sym, day, signal bits, 1-day, 5-day] with the moves in 1/100,000ths: a third of the size."""
    names = list(SIGNALS)
    return [[r["sym"], r["day"], sum(1 << i for i, s in enumerate(names) if s in r["sig"]),
             *(None if str(h) not in r["fwd"] else round(r["fwd"][str(h)] * 1e5) for h in HORIZONS)] for r in rows]


def unpack(rows: list[list]) -> list[dict]:
    names = list(SIGNALS)
    return [{"sym": r[0], "day": r[1], "sig": [s for i, s in enumerate(names) if r[2] >> i & 1],
             "fwd": {str(h): v / 1e5 for h, v in zip(HORIZONS, r[3:]) if v is not None}} for r in rows]


def combine(files: list[Path]) -> dict:
    """The parts' files as one report."""
    parts = [json.loads(f.read_text()) for f in files]
    out = {"generated": max(p["generated"] for p in parts), "start": parts[0]["start"], "end": max(p["end"] for p in parts),
           "stocks": [], "dropped": [], "not_reached": [], "rows": []}
    for p in parts:
        for k in ("stocks", "dropped", "not_reached"):
            out[k] += p[k]
        out["rows"] += unpack(p["rows"])
    return out


def main(part: str | None = None) -> None:
    part = part or (sys.argv[1] if len(sys.argv) > 1 else "a")
    if part == "combine":
        files = sorted((ROOT / "research").glob("*-flow-daily-[abc].json"))[-3:]
        rep = combine(files)
        table, md = report(rep)
        save("flow-daily", {**{k: v for k, v in rep.items() if k != "rows"}, "parts": [f.name for f in files], "table": table}, md)
        return
    from hero.alpaca import Alpaca
    rep = run(Alpaca(), part_of(part))
    table, md = report(rep)
    save(f"flow-daily-{part}", {**rep, "rows": pack(rep["rows"]), "table": table}, md)


if __name__ == "__main__":
    sys.exit(main())
