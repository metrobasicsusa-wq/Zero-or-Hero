"""Dip-week on the live pool (owner, 10-23: 「改，推完排上」): the hero plan's main engine was measured on 28 of today's
winners (research/2026-10-16-dip-week.md); the live rule picks from data/universe.json's 100 most traded names. Same
events (an open 2%+ under the prior close), same legs (next week's call, at the money and 3% out), same exits and costs
(5% a side, and 10%), on the 100 plus the 28, 2024-02 on. A time budget (RESEARCH_BUDGET_MIN, 115) stops the fetch
loop so a partial result still commits; the report says how many names were covered. Adds the hero game on the rule's
own pick (the biggest gap-down of the day) with intervals. Research only."""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from hero.research_dip_week import EXITS, EXTRA, START, events, groups, legs
from hero.research_event_turn import Pace
from hero.research_hero_game import game, interval
from hero.research_ideas import report, save
from hero.research_sniper_factors import latest

ROOT = Path(__file__).resolve().parent.parent
BUDGET_MIN = float(os.environ.get("RESEARCH_BUDGET_MIN", "115"))


def names() -> list[str]:
    """The live pool first (what the rule can buy), then the 28 of the first study for the overlap."""
    try:
        pool = list(json.loads((ROOT / "data" / "universe.json").read_text()).get("stocks") or [])
    except FileNotFoundError:
        pool = []
    old = sorted({r["und"] for r in latest("open-catalyst")["rows"]} | set(EXTRA))
    return pool + [s for s in old if s not in pool]


def run(client, syms: list[str], budget_min: float = BUDGET_MIN) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    split = client.daily_bars(syms, "2023-12-01", adjustment="split", feed="sip")
    raw = client.daily_bars(syms, "2023-12-01", adjustment="raw", feed="sip")
    rows, missing, done, t0 = [], 0, [], time.monotonic()
    pace = Pace(client)
    for und in syms:
        if (time.monotonic() - t0) / 60 > budget_min:
            break
        bs = [b for b in split.get(und, []) if b["t"][:10] <= end]
        if len(bs) < 30:
            done.append(und)
            continue
        days = [b["t"][:10] for b in bs]
        stock = {b["t"][:10]: float(b["c"]) for b in raw.get(und, [])}
        for e in events(days, [float(b["o"]) for b in bs], [float(b["c"]) for b in bs]):
            if e["day"] < START:
                continue
            got = legs(client, und, e["day"], stock.get(e["day"], 0.0) or float(bs[e["i"]]["c"]), end, days[e["i"] + 1:], stock)
            if not got:
                missing += 1
                rows.append({**e, "und": und, "leg": None, "res": {}})
            for name, leg in got.items():
                rows.append({**e, "und": und, "leg": name, **leg})
            pace.wait()
        done.append(und)
    return {"start": START, "end": end, "names": syms, "done": done, "partial": len(done) < len(syms), "rows": rows, "missing": missing,
            "minutes": round((time.monotonic() - t0) / 60, 1)}


def pick_series(rows: list[dict], leg: str, exit_: str, key: str = "res", only: set[str] | None = None) -> list[float]:
    """The rule's own pick a day: the biggest gap-down among the names (optionally a subset)."""
    by: dict[str, dict] = {}
    for r in rows:
        if r.get("leg") != leg or (r.get(key) or {}).get(exit_) is None or (only and r["und"] not in only):
            continue
        if r["day"] not in by or r["gap"] < by[r["day"]]["gap"]:
            by[r["day"]] = r
    return [by[d][key][exit_] for d in sorted(by)]


def hero_table(rows: list[dict], old: set[str]) -> tuple[dict, str]:
    out = {}
    lines = ["", "## hero 游戏：规则自己的选法（当天低开最多的那只），每轮到 20 倍", "",
             "| 池子 | 腿 · 出场 · 成本 | 笔数 | 平均 | 押一半：到 20 倍 / 轮（区间） | 每个 hero 的成本 | 押满：到 20 倍 / 轮（区间） | 每个 hero 的成本 |",
             "|---|---|---|---|---|---|---|---|"]
    for pool_name, only in (("100 只实盘池 + 28", None), ("原来的 28 只", old)):
        for leg, lname in (("otm3", "价外 3%"), ("atm", "平值")):
            for ex in ("d5", "trail2", "take2"):
                for key, cost in (("res", "5%"), ("res10", "10%")):
                    xs = pick_series(rows, leg, ex, key, only)
                    if len(xs) < 20:
                        continue
                    g5, g10 = game(xs, 0.5), game(xs, 1.0)
                    c5, c10 = interval(g5["heroes"], g5["rounds"]) or (0, 0), interval(g10["heroes"], g10["rounds"]) or (0, 0)
                    out[f"{pool_name}|{leg}|{ex}|{cost}"] = {"n": len(xs), "mean": statistics.mean(xs), "half": g5, "full": g10}
                    lines.append(f"| {pool_name} | {lname} · {ex} · {cost} | {len(xs)} | {statistics.mean(xs):+.0%} | "
                                 f"{g5['heroes']}/{g5['rounds']}（{c5[0]:.0%}–{c5[1]:.0%}） | {('$%s' % format(g5['cost_per_hero'], ',.0f')) if g5['cost_per_hero'] else '—'} | "
                                 f"{g10['heroes']}/{g10['rounds']}（{c10[0]:.0%}–{c10[1]:.0%}） | {('$%s' % format(g10['cost_per_hero'], ',.0f')) if g10['cost_per_hero'] else '—'} |")
    return out, "\n".join(lines) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    syms = names()
    rep = run(Alpaca(), syms)
    old = sorted({r["und"] for r in latest("open-catalyst")["rows"]} | set(EXTRA))
    gs = groups(rep["rows"])
    table, md = report(f"抄底给一周：换成 100 只实盘池 {date.today().isoformat()}",
                       f"{rep['start']} 至 {rep['end']}，data/universe.json 的 {len(syms)} 只（实盘池 100 只加原来的 28 只；时间预算 {BUDGET_MIN:.0f} 分钟，"
                       f"跑完 {len(rep['done'])} 只{'，没跑完的名字按字母序排在后面' if rep['partial'] else ''}）开盘比昨收低 2% 以上的日子，"
                       "当天收盘买最近的 5–12 天到期看涨（平值 / 价外 3%），按期权每日收盘价卖，买卖各算 5% 成本（res10：各 10%）。"
                       f"没有期权数据的事件 {rep['missing']} 个。和 research/2026-10-16-dip-week.md 同一口径，池子换成实盘的。", gs, EXITS, rep["end"])
    hero, hero_md = hero_table(rep["rows"], set(old))
    md += hero_md
    rep["hero"] = hero
    save("dip-week-broad", rep, md)


if __name__ == "__main__":
    sys.exit(main())
