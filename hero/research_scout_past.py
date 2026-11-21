"""The scout's list on the big drop days of the past, on the stocks of the time (2026-11-21).

hero/scout.py screens a list on every big drop day from 11-20 on. Of its five lines three can be read from prices and
are taken back here; the two that need our own earnings records (the last guidance above estimates, a report within a
week) have no history and are not tested.

Fixed before any of it was run:
  days     the scout's own drop days (scout.big_drop) from FIRST on;
  pool     each month's 100 most traded companies of the time (research_shock_pit.pools_by_month: the list of the 11-21
           flush study, here from 2017), names that have since left the tape included;
  lines    25% or more below the one-year high (scout.DEEP); a 20-session average dollar volume of scout.TRADED or
           more; not scout.WORSE (10 points) behind its group over ten sessions; then at most scout.PER_GROUP names a
           group, the ones that moved most with it over 120 sessions (scout.moves_with, scout.family);
  measured each name from that close to the close 63 and 252 sessions on, less SPY over the same days, the day's names
           averaged. A name that left the tape before then is held to its last close (the report counts them);
  beside   the same day's whole pool, and the pool's names 25% or more down before the other lines: what each step adds.
There is no line to pass: this is the look-back the page was missing. Drop days come in runs, so the report shows every
drop day and, for the robustness table, the first drop day of each month; 2022 apart. Exploration: a lead, not a rule.
Runs in Actions (the broker's daily bars): python -m hero.research_scout_past
"""

from __future__ import annotations

import statistics
import sys
from datetime import date
from pathlib import Path

from hero import research_shock as sh
from hero import research_shock_pit as pit
from hero import robust, scout
from hero.research_event_turn import Pace
from hero.research_flow_daily import ERRORS, fetch
from hero.research_ideas import save
from hero.research_scout_tools import drop_days

ROOT = Path(__file__).resolve().parent.parent
SINCE, FIRST = "2016-01-04", "2017-04-03"       # daily bars from SINCE; a pool needs a year of them plus 63 sessions
HORIZONS = {"3m": ("3 个月后", 63), "1y": ("1 年后", 252)}
STEPS = {"pool": "当天整个池子", "deep": "池子里离高点 25% 以上的", "lines": "再过「成交够大」「没有比同组多跌 10%」", "list": "清单（同一组只留两只）"}
GONE_AFTER = 5                                   # no bar in the last five sessions of the data: the name has left the tape


def picks(t: int, pool: list[str], d: dict, funds: dict[str, list]) -> dict[str, list[str]]:
    """The day's pool put through the lines that can be read from prices: the names left after each step."""
    def tail(c: list) -> list[float]:
        return [c[i] / c[i - 1] - 1 for i in range(t - scout.MOVES_DAYS + 1, t + 1)]
    spy_r = tail(funds["SPY"])
    fund_r = {f: tail(c) for f, c in funds.items() if f in scout.SECTORS and c[t - scout.MOVES_DAYS] is not None}
    out, kept = {"pool": [], "deep": [], "lines": [], "list": []}, []
    for s in pool:
        c = d["closes"][s]
        if d["last"][s] < t or c[t - 251] is None:
            continue
        out["pool"].append(s)
        if scout.state(c, t)["dd"] > scout.DEEP:
            continue
        out["deep"].append(s)
        group, w = scout.moves_with(tail(c), fund_r, spy_r)
        ten = c[t] / c[t - 10] - 1
        group_ten = funds[group][t] / funds[group][t - 10] - 1 if group else 0.0
        if statistics.mean(d["dvol"][s][t - 19:t + 1]) < scout.TRADED or (group and ten - group_ten <= scout.WORSE):
            continue
        out["lines"].append(s)
        kept.append({"symbol": s, "group": group, "with": w})
    groups, final = {}, []
    for k in sorted(kept, key=lambda k: -k["with"]):                    # as scout.screen: the two most like their group
        if k["group"]:
            groups.setdefault(scout.family(k["group"]), []).append(k)
        else:
            final.append(k)
    for cs in groups.values():
        final += cs[:scout.PER_GROUP]
    out["list"] = sorted(k["symbol"] for k in final)
    return out


def ahead(c: list, last: int, t: int, n: int) -> tuple[float, bool] | None:
    """(the change from t to t + n, held all the way). To its last close when the name left the tape first; None when
    t + n is simply past the end of the data."""
    if t + n < len(c) and last >= t + n:
        return c[t + n] / c[t] - 1, True
    if last < t + n and last < len(c) - GONE_AFTER:
        return c[last] / c[t] - 1, False
    return None


def run(client, pace=None, first: str = FIRST) -> dict:
    pace = pace or Pace(client)
    rep = {"generated": date.today().isoformat(), "since": SINCE, "start": first, "rows": [], "lists": {}, "left_early": 0}
    stocks, gone, err = pit.listing(client)
    rep.update({"candidates": len(stocks), "inactive_candidates": len(gone), "inactive_error": err})
    d = pit.load_daily(client, pace, stocks, gone, since=SINCE)
    got = fetch(pace, client.daily_bars, list(scout.FUNDS), SINCE, feed=sh.FEED) if d else None
    if not d or got is None:
        return {**rep, "errors": list(ERRORS)}
    dates = d["dates"]
    at = {x: i for i, x in enumerate(dates)}
    funds = {f: pit.series(bs, at, len(dates))[0] for f, bs in got.items()}
    pools, cut = pit.pools_by_month(d, stocks, start=first)
    names = sorted({s for p in pools.values() for s in p})
    rep["pools"] = {"months": len(pools), "names": len(names), "gone": sorted(s for s in names if s in gone), "cut_min": min(cut.values()) if cut else None,
                    "floor_ok": bool(cut) and min(cut.values()) >= pit.FLOOR, "dropped": d["dropped"], "gone_served": d["gone_served"], "gone_refused": d["gone_refused"]}
    moves = {f: {dates[i]: c[i] / c[i - 1] - 1 for i in range(1, len(c)) if c[i - 1]} for f, c in funds.items() if f in scout.DROP or f in scout.SECTORS}
    drops = {x: why for x, why in drop_days(moves).items() if x >= first}
    rep.update({"end": dates[-1], "drop_days": len(drops), "stretches": scout.stretches(list(drops))})
    spy = funds["SPY"]
    for day in sorted(drops):
        t = at[day]
        got = picks(t, pools.get(day[:7], []), d, funds)
        rep["lists"][day] = got["list"]
        for h, (_, n) in HORIZONS.items():
            if t + n >= len(dates):
                continue
            base = spy[t + n] / spy[t] - 1
            for step, syms in got.items():
                rets = [ahead(d["closes"][s], d["last"][s], t, n) for s in syms]
                rets = [r for r in rets if r]
                if step == "list":
                    rep["left_early"] += sum(not full for _, full in rets)
                if rets:
                    mean = statistics.mean(r for r, _ in rets)
                    rep["rows"].append({"day": day, "h": h, "step": step, "n": len(rets), "ret": round(mean, 5), "spy": round(base, 5), "ex": round(mean - base, 5)})
    return {**rep, "errors": list(ERRORS)}


def first_of_month(rows: list[dict]) -> list[dict]:
    seen, out = set(), []
    for r in sorted(rows, key=lambda r: r["day"]):
        if r["day"][:7] not in seen:
            seen.add(r["day"][:7])
            out.append(r)
    return out


def stat(rows: list[dict]) -> dict | None:
    if not rows:
        return None
    return {"days": len(rows), "names": statistics.mean(r["n"] for r in rows), "ret": statistics.mean(r["ret"] for r in rows),
            "spy": statistics.mean(r["spy"] for r in rows), "ex": statistics.mean(r["ex"] for r in rows), "beat": sum(r["ex"] > 0 for r in rows) / len(rows)}


def tables(rows: list[dict]) -> dict:
    out = {}
    for h in HORIZONS:
        for step in STEPS:
            mine = [r for r in rows if r["h"] == h and r["step"] == step]
            s = robust.summary([(r["day"], r["ex"]) for r in first_of_month(mine)], extra_cost=0.001)
            out[f"{h}|{step}"] = {"all": stat(mine), "y2022": stat([r for r in mine if r["day"][:4] == "2022"]), "other": stat([r for r in mine if r["day"][:4] != "2022"]),
                                  "monthly": s and {"n": s["n"], "mean": s["mean"], "trim": s["trim"], "median": s["median"], "win": s["win"],
                                                    "years": {y: list(v) for y, v in s["years"].items()},
                                                    "flags": [f.replace("各多 0% 成本", "各多 0.1% 成本") for f in s["flags"]]}}
    return out


def pct(x: float | None) -> str:
    return "—" if x is None else f"{x:+.1%}".replace("-", "−")


def report(rep: dict) -> tuple[dict, str]:
    t, p = tables(rep.get("rows") or []), rep.get("pools") or {}
    sizes = [len(v) for v in (rep.get("lists") or {}).values()]
    out = [f"# 侦察的清单，放到过去的大跌日上（{rep['generated']}）", "",
           "模拟盘研究，只记录，不是投资建议。探索档：线索，不是规则；这里没有及格线，是补页面上缺的那一块回头看。", "",
           "侦察（`hero/scout.py`）从 11-20 起在每个大跌日出一张清单。它的五条线里，能从价格读出来的三条——离一年高点 25% 以上、成交够大、十天里没有比同组多跌 10% 以上——"
           "加上「同一组只留最像这一组的两只」，这里放回过去的大跌日上；「上次指引高于预期」和「一周内不发财报」要用我们自己的财报记录，没有历史，**没测**。", "",
           f"大跌日按侦察同一条线判，{rep['start']} 到 {rep.get('end', '')} 一共 {rep.get('drop_days', 0)} 天，连着的算一段是 {rep.get('stretches', 0)} 段。"
           f"股票池是每个月按当时成交额选的 100 家（{p.get('months', 0)} 个月，前后 {p.get('names', 0)} 只），不是今天的名单。"
           "每个大跌日把当天选出来的股票等权平均，拿到 63 个和 252 个交易日后的收盘，减去同一天买标普（SPY）的结果。", ""]
    if sizes:
        out += [f"清单的大小：平均每个大跌日 {statistics.mean(sizes):.1f} 只，{sum(1 for x in sizes if not x)} 天一只都没有，最多 {max(sizes)} 只。", ""]
    for h, (name, _) in HORIZONS.items():
        out += [f"## {name}", "", "| 选法 | 大跌日 | 每天几只 | 这些股票 | 同一天的标普 | 比标普多 | 比标普好的日子 | 2022 年 / 其余（比标普多） |", "|---|---|---|---|---|---|---|---|"]
        for step, label in STEPS.items():
            c = t[f"{h}|{step}"]
            a = c["all"]
            if not a:
                out.append(f"| {label} | 0 | — | — | — | — | — | — |")
                continue
            out.append(f"| {label} | {a['days']} | {a['names']:.1f} | {pct(a['ret'])} | {pct(a['spy'])} | {pct(a['ex'])} | {a['beat']:.0%} | "
                       f"{pct(c['y2022']['ex']) if c['y2022'] else '—'} / {pct(c['other']['ex']) if c['other'] else '—'} |")
        out += ["", f"**每个月只算第一个大跌日**（{name}，比标普多多少）", "", "| 选法 | 月数 | 平均 | 去掉最好 3 笔 | 中位数 | 比标普好的比例 | 各年份 | 提醒 |", "|---|---|---|---|---|---|---|---|"]
        for step, label in STEPS.items():
            m = t[f"{h}|{step}"]["monthly"]
            if not m:
                out.append(f"| {label} | 0 | — | — | — | — | — | 没有 |")
                continue
            yrs = "、".join(f"{y} {pct(v[0])}（{v[1]}）" for y, v in m["years"].items())
            out.append(f"| {label} | {m['n']} | {pct(m['mean'])} | {pct(m['trim'])} | {pct(m['median'])} | {m['win']:.0%} | {yrs} | {'；'.join(m['flags']) or '—'} |")
        out.append("")
    notes = ["「上次指引高于预期」和「一周内不发财报」两条没有测；清单在实盘里比这里更短。",
             f"候选 {rep.get('candidates', 0)} 只，其中已经不在交易的 {rep.get('inactive_candidates', 0)} 只"
             + (f"；读不到券商的停牌名单（`{rep['inactive_error']}`），退市的进不来" if rep.get("inactive_error") else "")
             + (f"；数据接口还给得出日线的 {p.get('gone_served', 0)} 只，进过池子的 {len(p.get('gone') or [])} 只" if p else "") + "。",
             f"清单里的股票在到期前就不再交易的有 {rep.get('left_early', 0)} 只次，按它最后一个收盘价算。",
             "进池子要有一年的日线、股价 10 美元以上，上市不到一年的不在里面。每天一个样本、前后重叠，天数多不等于样本多。"]
    if p:
        notes.append(f"各月池子最后一名的 63 日平均成交额最低是 ${(p.get('cut_min') or 0) / 1e6:.0f}M，读日线时丢掉的是从没到过 ${pit.FLOOR / 1e6:.0f}M 的股票（{p.get('dropped', 0)} 只）："
                     + ("丢掉的进不了任何一个月的池子。" if p.get("floor_ok") else "**池子可能缺了股票，结果不能用。**"))
    for e in (rep.get("errors") or [])[-3:]:
        notes.append(f"请求失败的原因（最后几条）：`{e}`")
    return t, "\n".join(out + ["## 说明", ""] + [f"- {n}" for n in notes]) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    rep = run(Alpaca())
    table, md = report(rep)
    save("scout-past", {**rep, "table": table}, md)


if __name__ == "__main__":
    sys.exit(main())
