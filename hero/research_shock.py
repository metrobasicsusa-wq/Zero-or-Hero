"""A stock moves 5% in half an hour on five times its usual volume while SPY stands still (AMD, 11-19: a short report,
-11% in thirty minutes). The owner asked whether that amplification is a direction one can watch: once it shows, does
the move keep going, or come back?

Everything below was fixed on 11-19 before any of it was run:
  stocks   data/universe.json (this month's 100 most traded companies), SIP minutes since 2024-02;
  event    the first minute t between 10:05 and 15:00 (so the half hour starts at 09:35 or later) when the close of t
           is 5% (and, as its own tier, 8%) away from the close of t-30, the volume of those 30 minutes is 5 times the
           average of the same clock window over the stock's previous 20 full sessions, and SPY moved less than 1% over
           the same 30 minutes. One event per stock, day and tier;
  entry    the open of the next minute (the first bar within five minutes); shares, 0.05% round trip;
  measured in the direction of the shock: the close 5, 15 and 30 minutes after t, the day's close, the next day's
           close and the close five sessions on. Up and down shocks apart, then together;
  the line the same for "go with it" and "go against it": right 55% of the time, the average above zero in both halves
           of the events (in time order) and without the best 3, and 60 events or more.

Minute bars are raw prices and daily bars split-adjusted, so the overnight legs are the day's raw close over the entry
times the ratio of two daily closes (hero.robust.price_basis counts how many event days the two bases disagree on).
Earnings days are not removed: the opening gap itself never counts (the half hour starts at 09:35), a second leg after
the open does. Half days are left out. The period runs in two parts (a: to 2025-06, b: from 2025-07), one workflow run
each; `python -m hero.research_shock combine` joins them. Record only.
Run: python -m hero.research_shock [a|combine] / python -m hero.research_shock_b
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from collections import deque
from datetime import date, timedelta
from pathlib import Path

from hero import research_0dte as z
from hero import robust
from hero.research_event_turn import Pace
from hero.research_flow_daily import ERRORS, fetch, pool
from hero.research_flow_open import utc
from hero.research_ideas import save

ROOT = Path(__file__).resolve().parent.parent
START, LEAD = "2024-02-01", "2023-12-01"   # LEAD: daily bars from here, for the 20 sessions before the first day
FEED = "sip"
WINDOW, LOOKBACK = 30, 20
MOVES = (0.05, 0.08)
VOL_X, SPY_MAX = 5.0, 0.01
FIRST, LAST = "09:35", "15:00"             # the half hour starts at FIRST or later and ends by LAST
COST = 0.0005
HORIZONS = {"5m": "5 分钟后", "15m": "15 分钟后", "30m": "30 分钟后", "close": "当天收盘", "1d": "第二天收盘", "5d": "5 天后收盘"}
AHEAD = {"1d": 1, "5d": 5}
NEED_HIT, NEED_N = 0.55, 60
BUDGET = 120 * 60                          # seconds of fetching before a run stops (the workflow allows 150 minutes)
PARTS = {"a": (START, "2025-06-30"), "b": ("2025-07-01", "9999-12-31")}
SESSION = 390                              # minutes from 09:30 to 15:59


def idx(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m - 570


def hhmm(i: int) -> str:
    return f"{(i + 570) // 60:02d}:{(i + 570) % 60:02d}"


def day_arrays(bars: list[dict]) -> dict | None:
    """One regular session by minute: opens and closes where it traded, closes carried forward, running volume."""
    o, c, v = [None] * SESSION, [None] * SESSION, [0.0] * SESSION
    for k, b in z.et_minutes(bars).items():
        if "09:30" <= k <= "15:59":
            i = idx(k)
            o[i], c[i], v[i] = float(b["o"]), float(b["c"]), float(b.get("v") or 0)
    if all(x is None for x in c):
        return None
    fill, prev, cum, total = [], None, [], 0.0
    for i in range(SESSION):
        prev = c[i] if c[i] is not None else prev
        total += v[i]
        fill.append(prev)
        cum.append(total)
    return {"o": o, "c": c, "fill": fill, "cum": cum, "last": max(i for i, x in enumerate(c) if x is not None)}


def shock(day: dict, spy: dict, base: list[list[float]], thr: float) -> dict | None:
    """The first minute when the stock is `thr` away from where it was WINDOW minutes before, on VOL_X times the usual
    volume of that clock window (`base`: the previous sessions' running volumes), with SPY less than SPY_MAX away."""
    for i in range(idx(FIRST) + WINDOW, idx(LAST) + 1):
        a, b = day["fill"][i - WINDOW], day["c"][i]
        if not a or not b or abs(b / a - 1) < thr:
            continue
        usual = statistics.mean(cum[i] - cum[i - WINDOW] for cum in base)
        vol = day["cum"][i] - day["cum"][i - WINDOW]
        if usual <= 0 or vol < VOL_X * usual:
            continue
        sa, sb = spy["fill"][i - WINDOW], spy["fill"][i]
        if not sa or not sb or abs(sb / sa - 1) >= SPY_MAX:
            continue
        return {"i": i, "move": b / a - 1, "volx": vol / usual}
    return None


def after(day: dict, i: int, sign: int, ratios: dict[str, float | None]) -> dict | None:
    """In at the open of the minute after i. The moves from there, before costs, counted in the shock's direction;
    `ratios`: a later daily close over this day's daily close (one basis), for the overnight legs."""
    j = next((k for k in range(i + 1, min(i + 6, SESSION)) if day["o"][k] is not None), None)
    if j is None:
        return None
    entry, close = day["o"][j], day["fill"][day["last"]]
    out = {h: sign * (day["fill"][min(i + k, day["last"])] / entry - 1) for h, k in (("5m", 5), ("15m", 15), ("30m", 30))}
    out["close"] = sign * (close / entry - 1)
    for h, r in ratios.items():
        if r:
            out[h] = sign * (close / entry * r - 1)
    return {"entry_at": hhmm(j), "g": {h: round(x, 5) for h, x in out.items()}}


def run(client, symbols: list[str], first: str, last: str, clock=time.monotonic, pace=None) -> dict:
    pace = pace or Pace(client)
    end = min(last, (date.today() - timedelta(days=1)).isoformat())
    symbols = [s for s in symbols if s != "SPY"]
    rep = {"generated": date.today().isoformat(), "start": first, "end": end, "feed": FEED, "stocks": len(symbols),
           "days": 0, "short_days": [], "skipped": [], "stopped_at": None, "rows": []}
    daily: dict[str, list[dict]] = {}
    for k in range(0, len(symbols) + 1, 50):
        got = fetch(pace, client.daily_bars, (["SPY"] + symbols)[k:k + 50], LEAD, feed=FEED)
        if got is None:
            return {**rep, "errors": list(ERRORS)}
        daily.update(got)
    days = [b["t"][:10] for b in daily.get("SPY", []) if b["t"][:10] <= end]
    adj = {s: {b["t"][:10]: float(b["c"]) for b in bs} for s, bs in daily.items()}
    pos = {d: k for k, d in enumerate(days)}
    todo = [d for d in days if d >= first]
    if not todo:
        return {**rep, "errors": list(ERRORS)}
    lead = days[max(0, pos[todo[0]] - LOOKBACK - 3): pos[todo[0]]]      # a few spare for half days
    base = {s: deque(maxlen=LOOKBACK) for s in symbols}
    basis, misses, t0 = [], 0, clock()
    for d in lead + todo:
        if clock() - t0 > BUDGET:
            rep["stopped_at"] = d
            break
        got = fetch(pace, client.stock_bars, ["SPY"] + symbols, utc(d, "09:30"), utc(d, "16:00"), feed=FEED)
        if got is None:
            rep["skipped"].append(d)
            misses += 1
            if misses >= 3 and not rep["days"]:
                break                                   # nothing works: stop instead of burning the whole run
            continue
        spy = day_arrays(got.get("SPY", []))
        if not spy or spy["last"] < idx("15:55"):       # a half day: not measured and in no one's usual volume
            rep["short_days"].append(d)
            continue
        rep["days"] += d >= first
        for s in symbols:
            day = day_arrays(got.get(s, []))
            if not day:
                continue
            if d >= first and len(base[s]) == LOOKBACK:
                seen = [x for x in day["fill"] if x]
                swing = max(seen) / min(seen) - 1
                for thr in MOVES:
                    ev = shock(day, spy, list(base[s]), thr) if swing >= thr else None
                    if not ev:
                        continue
                    a0, t = adj.get(s, {}).get(d), pos[d]
                    ratios = {h: (adj[s].get(days[t + k], 0) / a0 or None) if a0 and t + k < len(days) else None
                              for h, k in AHEAD.items()}
                    r = after(day, ev["i"], 1 if ev["move"] > 0 else -1, ratios)
                    if r:
                        rep["rows"].append({"sym": s, "day": d, "at": hhmm(ev["i"]), "thr": thr, "move": round(ev["move"], 4),
                                            "volx": round(ev["volx"], 1), **r})
                        basis.append((d, s, day["fill"][day["last"]], a0 or 0.0))
            base[s].append(day["cum"])
    off = robust.price_basis(basis)
    rep["basis"] = {"checked": len(basis), "off": len(off), "symbols": sorted({b["symbol"] for b in off})}
    return {**rep, "errors": list(ERRORS)}


def line(rows: list[dict], h: str, side: int) -> dict | None:
    """One line against the fixed bar. side +1: go with the shock; -1: go against it. After COST."""
    xs = sorted((r["day"], side * r["g"][h] - COST) for r in rows if h in r["g"])
    if not xs:
        return None
    v = [r for _, r in xs]
    half = len(v) // 2
    halves = (statistics.mean(v[:half]) if half else None, statistics.mean(v[half:]))
    s = robust.summary(xs, extra_cost=0.001)        # shares: another 0.1% each way, not the 5% used for options
    ok = (len(v) >= NEED_N and s["win"] >= NEED_HIT and halves[0] is not None and min(halves) > 0
          and s["trim"] is not None and s["trim"] > 0)
    return {"n": len(v), "stocks": len({r["sym"] for r in rows if h in r["g"]}), "mean": round(s["mean"], 5),
            "median": round(s["median"], 5), "hit": round(s["win"], 3), "halves": [None if x is None else round(x, 5) for x in halves],
            "trim": None if s["trim"] is None else round(s["trim"], 5), "pass": ok,
            "flags": [f.replace("各多 0% 成本", "各多 0.1% 成本") for f in s["flags"]]}


GROUPS = {"涨跌合起来": lambda r: True, "急涨": lambda r: r["move"] > 0, "急跌": lambda r: r["move"] < 0}


def pct(x) -> str:
    return "—" if x is None else f"{x:+.2%}"


def report(rep: dict) -> tuple[dict, str]:
    table, out, counts = {}, [], {"main": [0, 0], "all": [0, 0]}
    for thr in MOVES:
        rows_t = [r for r in rep["rows"] if r["thr"] == thr]
        out += ["", f"## 半小时内动了 {thr:.0%} 以上（{len(rows_t)} 次，{len({r['sym'] for r in rows_t})} 只股票）", "",
                "| 方向 | 之后 | 次数 | 顺着做：平均 / 中位数 | 对的比例 | 前半 / 后半 | 去掉最好 3 笔 | 顺着做 | 反着做：平均 | 对的比例 | 反着做 | 提醒 |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for g, keep in GROUPS.items():
            rows = [r for r in rows_t if keep(r)]
            for h, name in HORIZONS.items():
                go, back = line(rows, h, 1), line(rows, h, -1)
                table[f"{thr}|{g}|{h}"] = {"with": go, "against": back}
                if not go:
                    continue
                for v in (go, back):
                    for key in ("all", "main") if (thr == MOVES[0] and g == "涨跌合起来") else ("all",):
                        counts[key][0] += 1
                        counts[key][1] += bool(v["pass"])
                better = go if go["mean"] >= back["mean"] else back
                out.append(f"| {g} | {name} | {go['n']} | {pct(go['mean'])} / {pct(go['median'])} | {go['hit']:.0%} | "
                           f"{pct(go['halves'][0])} / {pct(go['halves'][1])} | {pct(go['trim'])} | {'过' if go['pass'] else '不过'} | "
                           f"{pct(back['mean'])} | {back['hit']:.0%} | {'过' if back['pass'] else '不过'} | {'；'.join(better['flags']) or '—'} |")
    years: dict[str, int] = {}
    for r in rep["rows"]:
        if r["thr"] == MOVES[0]:
            years[r["day"][:4]] = years.get(r["day"][:4], 0) + 1
    big = sorted((r for r in rep["rows"] if r["thr"] == MOVES[0]), key=lambda r: -abs(r["move"]))[:12]
    eg = ["", "## 幅度最大的 12 次（核对用）", "", "| 日期 | 股票 | 时刻 | 半小时涨跌 | 成交量是平时的 | 之后到收盘（顺着方向为正） | 第二天收盘 |", "|---|---|---|---|---|---|---|"]
    eg += [f"| {r['day']} | {r['sym']} | {r['at']} | {r['move']:+.1%} | {r['volx']:.0f} 倍 | {pct(r['g'].get('close'))} | {pct(r['g'].get('1d'))} |" for r in big]
    notes = []
    b = rep.get("basis") or {}
    if b.get("checked"):
        notes.append(f"价格口径：{b['checked']} 个事件日里，分钟线收盘和日线收盘差 3% 以上的有 {b['off']} 个"
                     + (f"（{'、'.join(b['symbols'])}，后来拆过股）" if b.get("symbols") else "")
                     + "。隔夜的部分用日线自己的比值接上，不受这个影响。")
    if rep.get("short_days"):
        notes.append(f"提前收盘或没有数据、没有算的日子 {len(rep['short_days'])} 个。")
    if rep.get("skipped"):
        notes.append(f"取数失败跳过的日子 {len(rep['skipped'])} 个：{'、'.join(rep['skipped'][:8])}")
    if rep.get("stopped_at"):
        notes.append(f"时间不够，只跑到 {rep['stopped_at']} 之前。")
    for e in (rep.get("errors") or [])[-3:]:
        notes.append(f"请求失败的原因（最后几条）：`{e}`")
    head = [f"# 单只股票放量急动之后：顺着走还是反着走（{rep['generated']}）", "",
            f"{rep['start']} 至 {rep['end']}，{rep['days']} 个交易日，data/universe.json 的 {rep['stocks']} 只股票（这个月成交额最大的公司），SIP 分钟线。"
            f"事件：10:05 到 15:00 之间第一次出现「这一分钟的收盘价比 {WINDOW} 分钟前差 {MOVES[0]:.0%} 以上（另记 {MOVES[1]:.0%} 一档），这 {WINDOW} 分钟的成交量是"
            f"自己前 {LOOKBACK} 个交易日同一时段平均的 {VOL_X:g} 倍以上，同时 SPY 这 {WINDOW} 分钟变动不到 {SPY_MAX:.0%}」；每只股票每天每档只取第一次。"
            f"下一分钟开盘价进场，买卖正股，来回成本 {COST:.2%}。", "",
            "表里的数是「顺着急动的方向做」扣掉成本后的收益（急跌之后接着跌算正）；「反着做」是同一批事件反过来做、同样扣成本。"
            f"及格线在跑之前写死，两边用同一条：对的比例 {NEED_HIT:.0%} 以上、按时间分的前后两半平均都大于零、去掉最好 3 笔仍大于零、至少 {NEED_N} 次。"
            "财报日没有剔除（开盘跳空本身不算，窗口从 09:35 起）。只研究，不改交易。", "",
            f"**主线（{MOVES[0]:.0%} 一档、涨跌合起来）{counts['main'][0]} 条过了 {counts['main'][1]} 条；全部 {counts['all'][0]} 条过了 {counts['all'][1]} 条。**"
            "线多了总会有几条碰巧过，只有主线过了才算数。", "",
            "各年的事件数（" + f"{MOVES[0]:.0%}" + " 一档）：" + ("、".join(f"{y} 年 {n} 次" for y, n in sorted(years.items())) or "没有") + "。"]
    return table, "\n".join(head + out + (eg if big else []) + ([""] + [f"- {n}" for n in notes] if notes else [])) + "\n"


def combine(files: list[Path]) -> dict:
    """The parts' files as one report."""
    parts = sorted((json.loads(f.read_text()) for f in files), key=lambda p: p["start"])
    out = {"generated": max(p["generated"] for p in parts), "start": parts[0]["start"], "end": max(p["end"] for p in parts),
           "feed": FEED, "stocks": max(p["stocks"] for p in parts), "days": sum(p["days"] for p in parts),
           "stopped_at": next((p["stopped_at"] for p in parts if p.get("stopped_at")), None),
           "short_days": [], "skipped": [], "errors": [], "rows": [], "basis": {"checked": 0, "off": 0, "symbols": []}}
    for p in parts:
        for k in ("short_days", "skipped", "errors", "rows"):
            out[k] += p.get(k) or []
        b = p.get("basis") or {}
        out["basis"] = {"checked": out["basis"]["checked"] + b.get("checked", 0), "off": out["basis"]["off"] + b.get("off", 0),
                        "symbols": sorted(set(out["basis"]["symbols"]) | set(b.get("symbols") or []))}
    return out


def main(part: str | None = None) -> None:
    part = part or (sys.argv[1] if len(sys.argv) > 1 else "a")
    if part == "combine":
        files = [fs[-1] for p in PARTS if (fs := sorted((ROOT / "research").glob(f"*-shock-{p}.json")))]   # the newest of each
        rep = combine(files)
        table, md = report(rep)
        save("shock", {**rep, "parts": [f.name for f in files], "table": table}, md)
        return
    from hero.alpaca import Alpaca
    rep = run(Alpaca(), pool(), *PARTS[part])
    table, md = report(rep)
    save(f"shock-{part}", {**rep, "table": table}, md)


if __name__ == "__main__":
    sys.exit(main())
