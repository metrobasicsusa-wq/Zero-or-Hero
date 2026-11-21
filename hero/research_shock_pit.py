"""The one line of the shock study that passed (hero/research_shock.py, 11-19), run again on the stocks of the time.

"Buy a stock after it fell 5% in half an hour on five times its usual volume while SPY stood still, hold five sessions"
passed its line on data/universe.json: this month's 100 most traded companies, a list picked with hindsight (a stock that
fell and stayed down is less likely to be on it today). This reruns that single line on each month's own pool.

Fixed on 11-21 before any of it was run:
  pool     hero.research_universe.monthly_pools: at each month's first session, the 100 companies with the highest
           average dollar volume over the 63 sessions before it (price 10 or more, a year of history). Candidates: the
           broker's active list (research_universe.candidates) and, so that a stock which has since left the tape can
           be in an old pool, its inactive NYSE / NASDAQ names (no funds), as far as the data API still serves them;
  event    unchanged (research_shock.shock at 5%): the first minute between 10:05 and 15:00 with the stock 5% away from
           30 minutes before, on 5 times the usual volume of that window (its previous 20 full sessions), SPY less than
           1% away. Only when that first shock is down, and only on days the stock is in that month's pool;
  trade    shares at the open of the next minute, out at the close five sessions on, 0.05% round trip;
  the line more than the stock's own usual five-session change since 2024-02 (the average excess above zero), right 55%
           of the time, the average above zero in both halves in time order and without the best 3, 60 trades or more.
Shown beside it, not part of the line: each year, and the stocks not on today's list / no longer active on their own rows.
If it fails the line ends here: the pool made it.

Minute bars are fetched for this month's pool and the next two months' (a stock needs 20 full sessions of its own volume
before its first day in a pool). A name whose best 63-session average never reached FLOOR is dropped while the daily
bars load, to keep memory small; the report checks FLOOR against every pool's cut-off. Three parts by date, one workflow
run each (research_shock_pit, _b, _c); `python -m hero.research_shock_pit combine` joins them. Record only.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from collections import defaultdict, deque
from datetime import date, timedelta
from pathlib import Path

from hero import research_shock as sh
from hero import research_universe as ru
from hero import robust
from hero.research_event_turn import Pace
from hero.research_flow_daily import ERRORS, fetch, pool
from hero.research_flow_open import utc
from hero.research_ideas import save

ROOT = Path(__file__).resolve().parent.parent
START, DAILY_FROM = sh.START, "2022-09-01"   # DAILY_FROM: a year of history plus 63 sessions before the first pool
SIZE, THR, AHEAD_MONTHS, HOLD = 100, 0.05, 2, 5
FLOOR = 100e6
BUDGET = 100 * 60                            # seconds of minute fetching before a part stops (the workflow allows 150 minutes in all)
SPLITS = 2000                                # requests spent finding the names the API refuses, before giving up on a piece
NEED_HIT, NEED_N = sh.NEED_HIT, sh.NEED_N
PARTS = {"a": (START, "2024-12-31"), "b": ("2025-01-01", "2025-12-31"), "c": ("2026-01-01", "9999-12-31")}
OLD = "2026-11-19-shock.json"                # the first run, on today's list: shown beside the new one


def listing(client) -> tuple[dict[str, str], set[str], str | None]:
    """{symbol: company key} for every name that could have been in a pool, the ones no longer active, and why the
    inactive list could not be read (None when it could)."""
    active, gone, err = ru.candidates(client), {}, None
    try:
        for a in client._t("GET", "/v2/assets", params={"status": "inactive", "asset_class": "us_equity"}) or []:
            s, name = a.get("symbol", ""), a.get("name") or ""
            if a.get("exchange") in ("NYSE", "NASDAQ") and s.isalpha() and s.isupper() and s not in active and not ru.FUND_NAME.search(name):
                gone[s] = ru.company(name, s)
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:200]}"
    return {**gone, **active}, set(gone), err


def series(bars: list[dict], at: dict[str, int], n: int) -> tuple[list, list[float], int | None]:
    """One name on the calendar: closes carried forward after the first quote, dollar volume, the last day it traded."""
    c, v, last = [None] * n, [0.0] * n, None
    for b in bars:
        i = at.get(b["t"][:10])
        if i is not None:
            c[i], v[i] = float(b["c"]), float(b["c"]) * float(b.get("v") or 0)
            last = i if last is None else max(last, i)
    prev = None
    for i in range(n):
        if c[i] is None and prev is not None:
            c[i] = prev
        prev = c[i] if c[i] is not None else prev
    return c, v, last


def best_volume(v: list[float], k: int = ru.LOOKBACK_DV) -> float:
    """The highest k-session average dollar volume the name ever had."""
    if len(v) < k:
        return 0.0
    total = best = sum(v[:k])
    for i in range(k, len(v)):
        total += v[i] - v[i - k]
        best = max(best, total)
    return best / k


def load_daily(client, pace, stocks: dict[str, str], gone: frozenset | set = frozenset()) -> dict | None:
    """Adjusted daily closes and dollar volume on SPY's calendar, for the names that could ever make a pool. A request
    for names still trading must work (a hole would quietly change every pool); one for names that left the tape is
    cut in half until the piece the API refuses is small, and that piece is counted and left out."""
    spy = fetch(pace, client.daily_bars, ["SPY"], DAILY_FROM, feed=sh.FEED)
    if not spy or not spy.get("SPY"):
        return None
    dates = [b["t"][:10] for b in spy["SPY"]]
    at = {d: i for i, d in enumerate(dates)}
    out = {"dates": dates, "closes": {}, "dvol": {}, "last": {}, "dropped": 0, "gone_served": 0, "gone_refused": 0}

    def keep(got: dict) -> None:
        for s, bs in got.items():
            c, v, last = series(bs, at, len(dates))
            out["gone_served"] += s in gone and last is not None
            if last is not None and best_volume(v) >= FLOOR:
                out["closes"][s], out["dvol"][s], out["last"][s] = c, v, last
            else:
                out["dropped"] += 1

    live = sorted(s for s in stocks if s not in gone)
    for k in range(0, len(live), 200):
        got = fetch(pace, client.daily_bars, live[k:k + 200], DAILY_FROM, feed=sh.FEED)
        if got is None:
            return None
        keep(got)
    dead = sorted(s for s in stocks if s in gone)
    stack, tries = [dead[k:k + 200] for k in range(0, len(dead), 200)], 0
    while stack:
        chunk = stack.pop()
        pace.wait()
        tries += 1
        try:
            keep(client.daily_bars(chunk, DAILY_FROM, feed=sh.FEED))
        except Exception:
            if len(chunk) > 1 and tries < SPLITS:
                stack += [chunk[:len(chunk) // 2], chunk[len(chunk) // 2:]]
            else:
                out["gone_refused"] += len(chunk)
    return out


def pools_by_month(d: dict, stocks: dict[str, str]) -> tuple[dict[str, list[str]], dict[str, float]]:
    """{month: that month's pool} from 2024-02 on, and each pool's cut-off (its last name's average dollar volume)."""
    dates, closes, dvol = d["dates"], d["closes"], d["dvol"]
    have = {s: stocks.get(s, s) for s in dvol}
    firsts = {s: next((i for i, x in enumerate(closes[s]) if x is not None), None) for s in have}
    start = next((i for i, x in enumerate(dates) if x >= START), len(dates))
    raw = ru.monthly_pools(dates, closes, dvol, firsts, have, SIZE, start)
    cut = {dates[t][:7]: sum(dvol[p[-1]][t - ru.LOOKBACK_DV:t]) / ru.LOOKBACK_DV for t, p in raw.items() if p}
    return {dates[t][:7]: p for t, p in raw.items()}, cut


def months_from(month: str, n: int) -> list[str]:
    y, m = int(month[:4]), int(month[5:7])
    return [f"{y + (m - 1 + k) // 12}-{(m - 1 + k) % 12 + 1:02d}" for k in range(n + 1)]


def wanted(pools: dict[str, list[str]], month: str) -> list[str]:
    """The names to fetch minutes for in `month`: its pool and the next AHEAD_MONTHS months' (for their usual volume)."""
    return sorted({s for m in months_from(month, AHEAD_MONTHS) for s in pools.get(m, [])})


def usual(closes: list, dates: list[str], last: int, k: int = HOLD) -> float | None:
    """The name's average k-session change over every day since START it traded."""
    xs = [closes[t + k] / closes[t] - 1 for t in range(min(last, len(dates) - 1) - k + 1) if dates[t] >= START and closes[t]]
    return statistics.mean(xs) if xs else None


def run(client, first: str, last: str, clock=time.monotonic, pace=None, listed=None) -> dict:
    pace = pace or Pace(client)
    end = min(last, (date.today() - timedelta(days=1)).isoformat())
    rep = {"generated": date.today().isoformat(), "start": first, "end": end, "feed": sh.FEED, "days": 0, "short_days": [],
           "skipped": [], "stopped_at": None, "rows": [], "no_5d": []}
    stocks, gone, err = listing(client)
    rep.update({"candidates": len(stocks), "inactive_candidates": len(gone), "inactive_error": err})
    d = load_daily(client, pace, stocks, gone)
    if d is None:
        return {**rep, "errors": list(ERRORS)}
    pools, cut = pools_by_month(d, stocks)
    listed = set(pool() if listed is None else listed)
    names = sorted({s for p in pools.values() for s in p})
    rep["pools"] = {"months": len(pools), "names": len(names), "gone": sorted(s for s in names if s in gone),
                    "not_today": sorted(s for s in names if s not in listed), "cut_min": min(cut.values()) if cut else None,
                    "floor_ok": bool(cut) and min(cut.values()) >= FLOOR, "dropped": d["dropped"],
                    "gone_served": d["gone_served"], "gone_refused": d["gone_refused"],
                    "by_month": {m: len(p) for m, p in pools.items()}}
    dates, pos = d["dates"], {x: k for k, x in enumerate(d["dates"])}
    todo = [x for x in dates if first <= x <= end]
    if not todo:
        return {**rep, "errors": list(ERRORS)}
    lead = dates[max(0, pos[todo[0]] - sh.LOOKBACK - 3): pos[todo[0]]]      # a few spare for half days
    base = defaultdict(lambda: deque(maxlen=sh.LOOKBACK))
    avg, misses, t0 = {}, 0, clock()
    for day in lead + todo:
        if clock() - t0 > BUDGET:
            rep["stopped_at"] = day
            break
        syms = wanted(pools, day[:7])
        got = fetch(pace, client.stock_bars, ["SPY"] + syms, utc(day, "09:30"), utc(day, "16:00"), feed=sh.FEED)
        if got is None:
            rep["skipped"].append(day)
            misses += 1
            if misses >= 3 and not rep["days"]:
                break
            continue
        spy = sh.day_arrays(got.get("SPY", []))
        if not spy or spy["last"] < sh.idx("15:55"):                        # a half day
            rep["short_days"].append(day)
            continue
        rep["days"] += day >= first
        members = set(pools.get(day[:7], []))
        for s in syms:
            arr = sh.day_arrays(got.get(s, []))
            if not arr:
                continue
            if day >= first and s in members and len(base[s]) == sh.LOOKBACK:
                seen = [x for x in arr["fill"] if x]
                ev = sh.shock(arr, spy, list(base[s]), THR) if max(seen) / min(seen) - 1 >= THR else None
                if ev and ev["move"] < 0:
                    t, c = pos[day], d["closes"][s]
                    held = t + HOLD < len(dates) and t + HOLD <= d["last"][s] and c[t]
                    r = sh.after(arr, ev["i"], 1, {"5d": c[t + HOLD] / c[t]} if held else {})    # sign +1: the buyer's side
                    if r and "5d" in r["g"]:
                        if s not in avg:
                            avg[s] = usual(c, dates, d["last"][s])
                        rep["rows"].append({"sym": s, "day": day, "at": sh.hhmm(ev["i"]), "move": round(ev["move"], 4),
                                            "volx": round(ev["volx"], 1), "entry_at": r["entry_at"], "ret": round(r["g"]["5d"] - sh.COST, 5),
                                            "usual": round(avg[s] or 0.0, 5), "active": s not in gone, "listed": s in listed})
                    elif r:
                        rep["no_5d"].append([s, day])
            base[s].append(arr["cum"])
    return {**rep, "errors": list(ERRORS)}


def verdict(rows: list[dict]) -> dict | None:
    """The trades against the fixed line."""
    xs = sorted((r["day"], r["ret"]) for r in rows)
    if not xs:
        return None
    v = [r for _, r in xs]
    half = len(v) // 2
    halves = (statistics.mean(v[:half]) if half else None, statistics.mean(v[half:]))
    s = robust.summary(xs, extra_cost=0.001)        # shares: another 0.1% each way, not the 5% used for options
    base = statistics.mean(r["usual"] for r in rows)
    checks = {"excess": s["mean"] - base > 0, "hit": s["win"] >= NEED_HIT, "halves": halves[0] is not None and min(halves) > 0,
              "trim": s["trim"] is not None and s["trim"] > 0, "n": len(v) >= NEED_N}
    return {"n": len(v), "stocks": len({r["sym"] for r in rows}), "mean": round(s["mean"], 5), "median": round(s["median"], 5),
            "hit": round(s["win"], 3), "halves": [None if x is None else round(x, 5) for x in halves],
            "trim": None if s["trim"] is None else round(s["trim"], 5), "usual": round(base, 5), "excess": round(s["mean"] - base, 5),
            "years": {y: [round(m, 5), n] for y, (m, n) in s["years"].items()}, "checks": checks, "pass": all(checks.values()),
            "flags": [f.replace("各多 0% 成本", "各多 0.1% 成本") for f in s["flags"]]}


GROUPS = {"全部": lambda r: True, "今天名单里的": lambda r: r["listed"], "不在今天名单里的": lambda r: not r["listed"],
          "已经不在交易的": lambda r: not r["active"]}
CHECKS = {"excess": "比它自己平时 5 天的涨幅多", "hit": f"对的比例 {NEED_HIT:.0%} 以上", "halves": "前后两半平均都大于零",
          "trim": "去掉最好 3 笔仍大于零", "n": f"至少 {NEED_N} 次"}


def pct(x) -> str:
    return "—" if x is None else f"{x:+.2%}".replace("-", "−")


def old_rows(root: Path = ROOT) -> list[dict]:
    """The first run's trades on the same line (today's list): [{sym, day, ret}]."""
    f = root / "research" / OLD
    if not f.exists():
        return []
    return [{"sym": r["sym"], "day": r["day"], "ret": -r["g"]["5d"] - sh.COST} for r in json.loads(f.read_text()).get("rows", [])
            if r.get("thr") == THR and r.get("move", 0) < 0 and "5d" in r.get("g", {})]


def report(rep: dict, old: list[dict] | None = None) -> tuple[dict, str]:
    rows, p = rep.get("rows") or [], rep.get("pools") or {}
    table = {g: verdict([r for r in rows if keep(r)]) for g, keep in GROUPS.items()}
    v = table["全部"]
    out = [f"# 急跌之后拿 5 天：换成当时的股票池重跑（{rep['generated']}）", "",
           "模拟盘研究，只记录，不是投资建议。", "",
           "11-19 的放量急动研究（`research/2026-11-19-shock.md`）里只有一条过了线：一只股票半小时跌 5% 以上、成交量是平时的 5 倍、同时大盘没动，"
           "下一分钟买进正股、拿到 5 个交易日后收盘。那次用的是今天成交额最大的 100 只——事后挑的名单，跌下去没回来的股票不太会在里面。"
           f"这里只重跑这一条，股票池换成每个月按当时成交额选的 {SIZE} 家公司（`hero.research_universe.monthly_pools`），候选里加上了已经不在交易的股票。"
           "事件定义、成本和及格线都没动，线是跑之前写死的。", "",
           f"{rep['start']} 至 {rep['end']}，{rep['days']} 个交易日，{p.get('months', 0)} 个月的池子，前后一共 {p.get('names', 0)} 只股票；"
           f"其中 {len(p.get('not_today') or [])} 只不在今天的名单里，{len(p.get('gone') or [])} 只已经不在交易"
           + (f"（{'、'.join(p['gone'][:12])}）" if p.get("gone") else "") + "。", ""]
    if not v:
        out += ["**没有事件。**", ""]
    else:
        out += [f"## 结论：{'过了线' if v['pass'] else '没过线'}", "",
                "| 及格线 | 结果 | 过没过 |", "|---|---|---|",
                f"| {CHECKS['excess']} | 平均 {pct(v['mean'])}，它们自己平时 5 天 {pct(v['usual'])}，多 {pct(v['excess'])} | {'过' if v['checks']['excess'] else '不过'} |",
                f"| {CHECKS['hit']} | {v['hit']:.0%} | {'过' if v['checks']['hit'] else '不过'} |",
                f"| {CHECKS['halves']} | 前半 {pct(v['halves'][0])}，后半 {pct(v['halves'][1])} | {'过' if v['checks']['halves'] else '不过'} |",
                f"| {CHECKS['trim']} | {pct(v['trim'])} | {'过' if v['checks']['trim'] else '不过'} |",
                f"| {CHECKS['n']} | {v['n']} 次，{v['stocks']} 只股票 | {'过' if v['checks']['n'] else '不过'} |", ""]
        failed = [CHECKS[k] for k, ok in v["checks"].items() if not ok]
        out += [("五条都过。只记录，不改交易；进周日晚的摘要。" if v["pass"] else
                 f"没过的是：{'；'.join(failed)}。11-19 那次过线是池子造成的，这条线到此结束。"), ""]
    out += ["## 拆开看（不算在及格线里）", "", "| 哪些股票 | 次数 | 平均 / 中位数 | 对的比例 | 去掉最好 3 笔 | 各年份 | 提醒 |", "|---|---|---|---|---|---|---|"]
    for g, t in table.items():
        if not t:
            out.append(f"| {g} | 0 | — | — | — | — | 没有事件 |")
            continue
        yrs = "、".join(f"{y} 年 {pct(m)}（{n}）" for y, (m, n) in t["years"].items())
        out.append(f"| {g} | {t['n']} | {pct(t['mean'])} / {pct(t['median'])} | {t['hit']:.0%} | {pct(t['trim'])} | {yrs} | {'；'.join(t['flags']) or '—'} |")
    old = old_rows() if old is None else old
    if old and v:
        both = {(r["sym"], r["day"]) for r in rows} & {(r["sym"], r["day"]) for r in old}
        only_old = [r for r in old if (r["sym"], r["day"]) not in both and rep["start"] <= r["day"] <= rep["end"]]
        only_new = [r for r in rows if (r["sym"], r["day"]) not in both]
        mean = lambda xs: pct(statistics.mean(x["ret"] for x in xs)) if xs else "—"    # noqa: E731
        out += ["", "## 和 11-19 那次比", "",
                f"- 11-19（今天的名单）：{len(old)} 次，平均 {mean(old)}。",
                f"- 两次都有的：{len(both)} 次，平均 {mean([r for r in rows if (r['sym'], r['day']) in both])}。",
                f"- 只在今天的名单里有、当时不在池子里的：{len(only_old)} 次，平均 {mean(only_old)}。这些就是事后挑名单带进来的。",
                f"- 只在当时的池子里有的：{len(only_new)} 次，平均 {mean(only_new)}。"]
    notes = [f"候选 {rep.get('candidates', 0)} 只，其中已经不在交易的 {rep.get('inactive_candidates', 0)} 只"
             + (f"；读不到券商的停牌名单（`{rep['inactive_error']}`），这一次的候选只有还在交易的，退市的进不来" if rep.get("inactive_error") else "")
             + (f"；已经不在交易的里面，数据接口还给得出日线的 {p.get('gone_served', 0)} 只，接口拒绝的 {p.get('gone_refused', 0)} 只" if p else "") + "。",
             "进池子要有一年的日线、股价 10 美元以上（`monthly_pools` 原来的条件），所以上市不到一年的股票不在里面。"]
    if p:
        notes.append(f"为了省内存，63 日平均成交额从没到过 ${FLOOR / 1e6:.0f}M 的股票在读日线时就丢掉了（{p.get('dropped', 0)} 只）；"
                     f"各月池子最后一名的成交额最低是 ${(p.get('cut_min') or 0) / 1e6:.0f}M，" + ("比这条线高，丢掉的进不了任何一个月的池子。" if p.get("floor_ok") else "**没有高过这条线，池子可能缺了股票，结果不能用。**"))
    if rep.get("no_5d"):
        notes.append(f"事件之后不到 5 个交易日就没有收盘价的（退市，或者太靠近数据末尾）{len(rep['no_5d'])} 次，没有算进去："
                     + "、".join(f"{s} {d}" for s, d in rep["no_5d"][:8]) + "。")
    if rep.get("short_days"):
        notes.append(f"提前收盘或没有数据、没有算的日子 {len(rep['short_days'])} 个。")
    if rep.get("skipped"):
        notes.append(f"取数失败跳过的日子 {len(rep['skipped'])} 个：{'、'.join(rep['skipped'][:8])}")
    if rep.get("stopped_at"):
        notes.append(f"**时间不够，只跑到 {rep['stopped_at']} 之前。**")
    for e in (rep.get("errors") or [])[-3:]:
        notes.append(f"请求失败的原因（最后几条）：`{e}`")
    return table, "\n".join(out + ["", "## 说明", ""] + [f"- {n}" for n in notes]) + "\n"


def combine(files: list[Path]) -> dict:
    """The parts' files as one report."""
    parts = sorted((json.loads(f.read_text()) for f in files), key=lambda p: p["start"])
    out = {"generated": max(p["generated"] for p in parts), "start": parts[0]["start"], "end": max(p["end"] for p in parts),
           "feed": sh.FEED, "days": sum(p["days"] for p in parts), "pools": parts[-1].get("pools"),
           "candidates": parts[-1].get("candidates", 0), "inactive_candidates": parts[-1].get("inactive_candidates", 0),
           "inactive_error": next((p["inactive_error"] for p in parts if p.get("inactive_error")), None),
           "stopped_at": next((p["stopped_at"] for p in parts if p.get("stopped_at")), None)}
    for k in ("short_days", "skipped", "errors", "rows", "no_5d"):
        out[k] = [x for p in parts for x in (p.get(k) or [])]
    return out


def main(part: str | None = None) -> None:
    part = part or (sys.argv[1] if len(sys.argv) > 1 else "a")
    if part == "combine":
        files = [fs[-1] for p in PARTS if (fs := sorted((ROOT / "research").glob(f"*-shock-pit-{p}.json")))]   # the newest of each
        rep = combine(files)
        table, md = report(rep)
        save("shock-pit", {**rep, "parts": [f.name for f in files], "table": table}, md)
        return
    from hero.alpaca import Alpaca
    rep = run(Alpaca(), *PARTS[part])
    table, md = report(rep)
    save(f"shock-pit-{part}", {**rep, "table": table}, md)


if __name__ == "__main__":
    sys.exit(main())
