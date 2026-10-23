"""A strong stock's small pullback, bought with next week's call (owner, 10-23: 「改，推完排上」).

The one setup in the signal group's record with real excess (research/2026-10-23-peer-signals.md: 3-10% under the
20-day high, +5.4% in ten sessions, +3.5% over SMH, 38 signals) tried as our kind of rule: the stock is up 10%+ over
the last 20 sessions and above its 50-day average, closes 3-10% under its 20-session high; buy at that close the
nearest 5-12 day call (at the money / 3% out), the dip-week legs, exits and costs (5% a side, and 10%). One event per
name per five sessions. Pool: data/universe.json's 100 plus the dip-week 28, 2024-02 on, with the same time budget as
research_dip_week_broad. Hero game on the rule's pick (the deepest pullback of the day). Research only."""
from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from hero.research_dip_week import EXITS, START, legs
from hero.research_dip_week_broad import BUDGET_MIN, names
from hero.research_event_turn import Pace
from hero.research_hero_game import game, interval
from hero.research_ideas import report, save

ROOT = Path(__file__).resolve().parent.parent
MOM, LOW, HIGH, GAP_SESSIONS = 0.10, -0.10, -0.03, 5


def events(days: list[str], highs: list[float], closes: list[float]) -> list[dict]:
    """Closes 3-10% under the 20-session high after a 10%+ run, above the 50-day average; one per five sessions."""
    out, last = [], -99
    for i in range(51, len(days)):
        mom = closes[i - 1] / closes[i - 21] - 1
        hi20 = max(highs[i - 20:i])
        from_high = closes[i] / hi20 - 1
        ma50 = statistics.mean(closes[i - 50:i])
        if mom >= MOM and LOW <= from_high <= HIGH and closes[i] > ma50 and i - last >= GAP_SESSIONS:
            last = i
            out.append({"day": days[i], "i": i, "mom": round(mom, 4), "from_high": round(from_high, 4),
                        "up_day": closes[i] >= closes[i - 1],
                        "stock": {f"s{k}": round(closes[i + k] / closes[i] - 1, 4) for k in (1, 3, 5, 10) if i + k < len(closes)}})
    return out


def run(client, syms: list[str], budget_min: float = BUDGET_MIN) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    split = client.daily_bars(syms, "2023-10-01", adjustment="split", feed="sip")
    raw = client.daily_bars(syms, "2023-10-01", adjustment="raw", feed="sip")
    rows, missing, done, t0 = [], 0, [], time.monotonic()
    pace = Pace(client)
    for und in syms:
        if (time.monotonic() - t0) / 60 > budget_min:
            break
        bs = [b for b in split.get(und, []) if b["t"][:10] <= end]
        if len(bs) < 60:
            done.append(und)
            continue
        days = [b["t"][:10] for b in bs]
        stock = {b["t"][:10]: float(b["c"]) for b in raw.get(und, [])}
        for e in events(days, [float(b["h"]) for b in bs], [float(b["c"]) for b in bs]):
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


def groups(rows: list[dict]) -> dict[str, list[dict]]:
    atm = [r for r in rows if r.get("leg") == "atm"]
    otm = [r for r in rows if r.get("leg") == "otm3"]
    return {"强势股回调 3–10% · 平值": atm, "强势股回调 3–10% · 价外 3%": otm,
            "　回调 3–6% · 平值": [r for r in atm if r["from_high"] >= -0.06], "　回调 6–10% · 平值": [r for r in atm if r["from_high"] < -0.06],
            "　前 20 天涨 20% 以上 · 平值": [r for r in atm if r.get("mom", 0) >= 0.20],
            "　当天收阳 · 平值": [r for r in atm if r.get("up_day")], "　当天收阴 · 平值": [r for r in atm if not r.get("up_day")]}


def pick_series(rows: list[dict], leg: str, exit_: str, key: str = "res") -> list[float]:
    """The rule's pick a day: the deepest pullback among that day's events."""
    by: dict[str, dict] = {}
    for r in rows:
        if r.get("leg") != leg or (r.get(key) or {}).get(exit_) is None:
            continue
        if r["day"] not in by or r["from_high"] < by[r["day"]]["from_high"]:
            by[r["day"]] = r
    return [by[d][key][exit_] for d in sorted(by)]


def hero_table(rows: list[dict]) -> tuple[dict, str]:
    out = {}
    lines = ["", "## hero 游戏：规则自己的选法（当天回调最深的那只），每轮到 20 倍", "",
             "| 腿 · 出场 · 成本 | 笔数 | 平均 | 押一半：到 20 倍 / 轮（区间） | 每个 hero 的成本 | 押满：到 20 倍 / 轮（区间） | 每个 hero 的成本 |", "|---|---|---|---|---|---|---|"]
    for leg, lname in (("otm3", "价外 3%"), ("atm", "平值")):
        for ex in ("d5", "trail2", "take2"):
            for key, cost in (("res", "5%"), ("res10", "10%")):
                xs = pick_series(rows, leg, ex, key)
                if len(xs) < 20:
                    continue
                g5, g10 = game(xs, 0.5), game(xs, 1.0)
                c5, c10 = interval(g5["heroes"], g5["rounds"]) or (0, 0), interval(g10["heroes"], g10["rounds"]) or (0, 0)
                out[f"{leg}|{ex}|{cost}"] = {"n": len(xs), "mean": statistics.mean(xs), "half": g5, "full": g10}
                lines.append(f"| {lname} · {ex} · {cost} | {len(xs)} | {statistics.mean(xs):+.0%} | {g5['heroes']}/{g5['rounds']}（{c5[0]:.0%}–{c5[1]:.0%}） | "
                             f"{('$%s' % format(g5['cost_per_hero'], ',.0f')) if g5['cost_per_hero'] else '—'} | {g10['heroes']}/{g10['rounds']}（{c10[0]:.0%}–{c10[1]:.0%}） | "
                             f"{('$%s' % format(g10['cost_per_hero'], ',.0f')) if g10['cost_per_hero'] else '—'} |")
    return out, "\n".join(lines) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    syms = names()
    rep = run(Alpaca(), syms)
    gs = groups(rep["rows"])
    table, md = report(f"强势股回调 3–10%，买下周的看涨 {date.today().isoformat()}",
                       f"{rep['start']} 至 {rep['end']}，{len(syms)} 只（实盘池 100 只加抄底研究的 28 只；时间预算 {BUDGET_MIN:.0f} 分钟，跑完 {len(rep['done'])} 只"
                       f"{'，没跑完的排在后面' if rep['partial'] else ''}）：前 20 天涨 10% 以上、在 50 日均线上、收盘离 20 日最高 3–10% 的日子（每只股票 5 天内只记一次），"
                       "当天收盘买最近的 5–12 天到期看涨（平值 / 价外 3%），按期权每日收盘价卖，买卖各算 5% 成本（res10：各 10%）。"
                       f"没有期权数据的事件 {rep['missing']} 个。起因：research/2026-10-23-peer-signals.md 里唯一有超额的形态。", gs, EXITS, rep["end"])
    hero, hero_md = hero_table(rep["rows"])
    md += hero_md
    rep["hero"] = hero
    save("pullback", rep, md)


if __name__ == "__main__":
    sys.exit(main())
