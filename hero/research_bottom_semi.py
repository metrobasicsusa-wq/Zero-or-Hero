"""The two-month bottom study on the well-known semiconductor names (owner, 10-27: 「你可以拿半导体的知名股票做下回测」),
after bottom-2m found that on the ETFs the depth of the slide (the first close 8% under the 20-session high), not
the length of the down streak, is what a two-month hold pays for.

Same two layers as hero.research_bottom_2m, per stock. Single names move two to three times as much as SMH, so the
depth tags are nested: the first close 8% / 15% / 20% under the 20-session high, the third down day already 8% or
15% under it, the first green close after three or more down closes, and every fifth (prices) / twentieth (options)
session as the control. (A) Prices, 2017 on (split-adjusted): the stock held 20 / 42 / 63 sessions, the further fall
within the next two months and how long it took. (B) Options, 2024-02 on: the nearest monthly call 75-130 days out,
at the money, 5% and 10% out, sold at 21 / 42 / 63 sessions, on a 40% giveback after 2x or at 3x, with what one
contract cost (a $2,000 ledger cannot buy an at-the-money three-month call on a $900 stock). Contract lookups by
monthly expiry and bars per contract are cached; misses are counted by reason. Hero games for $2,000 to 5x and $500 to
20x. A time budget keeps a partial run committable. Research only."""
from __future__ import annotations

import os
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from hero.research_bottom_2m import DTE, EXITS, LEGS, OPT_START, legs
from hero.research_bottom_deep import streak_at
from hero.research_event_turn import Pace
from hero.research_hero_game import game, interval
from hero.research_ideas import report, save

ROOT = Path(__file__).resolve().parent.parent
NAMES = ("NVDA", "AVGO", "AMD", "MU", "TSM", "ASML", "AMAT", "LRCX", "KLAC", "QCOM", "INTC", "MRVL", "TXN", "ARM")
PX_START = "2012-01-01"
DEPTHS = ((0.08, "dd8"), (0.15, "dd15"), (0.20, "dd20"))
LABEL = {"dd8": "首次跌到 20 日高点下 8%", "dd15": "首次跌到 20 日高点下 15%", "dd20": "首次跌到 20 日高点下 20%",
         "day3": "连跌第 3 天", "day3deep": "连跌第 3 天且已离 20 日高点 ≥8%", "day3deep15": "连跌第 3 天且已离 20 日高点 ≥15%",
         "green1": "连跌 3 天后第一根阳线", "every5": "对照：每第 5 天随便买", "every20": "对照：每第 20 天随便买"}
OPT_TAGS = ("dd8", "dd15", "day3deep", "every20")
BUDGET_MIN = float(os.environ.get("RESEARCH_BUDGET_MIN", "110"))


def events(days: list[str], closes: list[float]) -> list[dict]:
    """Every tagged session with the stock's forward path at 20 / 42 / 63 sessions and its further fall within the
    next 42 sessions. The depth tags fire on the first close through each level (8% / 15% / 20% under the 20-session
    high), so one slide gives at most one of each."""
    out = []
    for i in range(253, len(days)):
        s = streak_at(closes, i)
        dd20 = closes[i] / max(closes[i - 20:i + 1]) - 1
        dd20p = closes[i - 1] / max(closes[i - 21:i]) - 1
        ddy = closes[i] / max(closes[i - 252:i + 1]) - 1
        tags = []
        for lvl, tag in DEPTHS:
            if dd20 <= -lvl < dd20p:
                tags.append(tag)
        if s == 3:
            tags.append("day3")
            if dd20 <= -0.08:
                tags.append("day3deep")
            if dd20 <= -0.15:
                tags.append("day3deep15")
        if closes[i] > closes[i - 1] and streak_at(closes, i - 1) >= 3:
            tags.append("green1")
        if i % 5 == 0:
            tags.append("every5")
        if i % 20 == 0:
            tags.append("every20")
        if not tags:
            continue
        fwd = {f"f{k}": round(closes[i + k] / closes[i] - 1, 4) for k in (20, 42, 63) if i + k < len(closes)}
        nxt = closes[i + 1:i + 43]
        low = min(nxt) if nxt else closes[i]
        out.append({"day": days[i], "i": i, "streak": s, "dd20": round(dd20, 4), "ddy": round(ddy, 4), "tags": tags, **fwd,
                    "mae42": round(low / closes[i] - 1, 4), "days_to_low42": (nxt.index(low) + 1) if nxt else 0})
    return out


def run(client, budget_min: float = BUDGET_MIN) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    t0 = time.monotonic()
    pace = Pace(client)
    split = client.daily_bars(list(NAMES), PX_START, adjustment="split", feed="sip")
    raw = client.daily_bars(list(NAMES), OPT_START, adjustment="raw", feed="sip")
    evs, rows, misses, done, cache = [], [], {}, [], {}
    by_und = {}
    for und in NAMES:
        bs = [b for b in split.get(und, []) if b["t"][:10] <= end]
        days = [b["t"][:10] for b in bs]
        closes = [float(b["c"]) for b in bs]
        es = events(days, closes)
        for e in es:
            e["und"] = und
        evs += es
        by_und[und] = (days, closes, es)
    stop = False
    for und in NAMES:
        days, closes, es = by_und[und]
        stock = {b["t"][:10]: float(b["c"]) for b in raw.get(und, [])}
        for e in es:
            if e["day"] < OPT_START or not any(t in e["tags"] for t in OPT_TAGS):
                continue
            if (time.monotonic() - t0) / 60 > budget_min:
                stop = True
                break
            i = e["i"]
            spot = stock.get(e["day"]) or closes[i]
            got = legs(client, und, e["day"], spot, end, days[i + 1:], stock, pace, misses, cache)
            base = {k: v for k, v in e.items() if k != "i"}
            for name, leg in got.items():
                rows.append({**base, "leg": name, **{k: v for k, v in leg.items() if k != "path"}, "path": leg["path"],
                             "cost": round(leg["entry"] * 100, 0), "spot": round(spot, 2)})
            if not got:
                rows.append({**base, "leg": None, "res": {}})
        if stop:
            break
        done.append(und)
    return {"px_start": PX_START, "opt_start": OPT_START, "end": end, "names": list(NAMES), "done": done, "partial": len(done) < len(NAMES),
            "dte": list(DTE), "events": evs, "rows": rows, "misses": misses, "minutes": round((time.monotonic() - t0) / 60, 1),
            "px_from": min((e["day"] for e in evs), default=None),
            "calls": {"contracts": len([k for k in cache if k != "bars"]), "options": len(cache.get("bars", {}))}}


def table_a(evs: list[dict]) -> str:
    def row(name, g):
        if not g:
            return None
        m = lambda k: statistics.mean(e[k] for e in g if k in e) if any(k in e for e in g) else None
        f = lambda v: "—" if v is None else f"{v:+.1%}"
        n42 = [e for e in g if "f42" in e]
        win = sum(e["f42"] > 0 for e in n42) / len(n42) if n42 else 0.0
        med = statistics.median(e["f42"] for e in n42) if n42 else None
        return (f"| {name} | {len(g)} | {f(m('f20'))} | {f(m('f42'))} | {f(med)} | {f(m('f63'))} | {win:.0%} | "
                f"{f(m('mae42'))} / {statistics.median(e['mae42'] for e in g):+.1%} | {statistics.median(e['days_to_low42'] for e in g):.0f} |")
    lines = ["## A. 价格层（2017 年起，拆股调整价，不算成本）：信号之后正股拿 1 / 2 / 3 个月", "",
             "20 / 42 / 63 个交易日的收益（平均；42 天另给中位数）；「再跌多少」= 之后 42 天最低收盘对信号日（平均 / 中位数），「到最低几天」= 中位数。", "",
             "| 情况 | 笔数 | 20 天 | 42 天 | 42 天中位数 | 63 天 | 42 天赚钱 | 再跌多少 | 到最低几天 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for tag, label in LABEL.items():
        lines.append(row(label, [e for e in evs if tag in e["tags"]]))
    for tag in ("dd8", "dd15"):
        for und in NAMES:
            lines.append(row(f"　{LABEL[tag]} · {und}", [e for e in evs if tag in e["tags"] and e["und"] == und]))
    lines = [x for x in lines if x is not None]
    years = sorted({e["day"][:4] for e in evs})
    lines += ["", "按年份（42 天平均收益 / 笔数）：", "", "| 情况 | " + " | ".join(years) + " |", "|---|" + "---|" * len(years)]
    for tag in ("dd8", "dd15", "dd20", "day3deep", "every5"):
        cells = []
        for y in years:
            g = [e for e in evs if tag in e["tags"] and e["day"].startswith(y) and "f42" in e]
            cells.append(f"{statistics.mean(e['f42'] for e in g):+.1%} / {len(g)}" if g else "—")
        lines.append(f"| {LABEL[tag]} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def groups(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for tag in OPT_TAGS:
        for leg, lname in LEGS.items():
            out[f"{LABEL[tag]} · {lname}"] = [r for r in rows if r.get("leg") == leg and tag in r["tags"]]
    for tag in ("dd8", "dd15"):
        for und in NAMES:
            out[f"　{LABEL[tag]} · {und} · 平值"] = [r for r in rows if r.get("leg") == "atm" and tag in r["tags"] and r["und"] == und]
    return {k: v for k, v in out.items() if v}


def cost_table(rows: list[dict]) -> str:
    lines = ["", "## 一张多少钱（平值腿，信号日的买价 × 100，中位数）", "", "| 股票 | 笔数 | 一张平值 | 一张价外 5% | 一张价外 10% | $2,000 买得起平值的比例 |", "|---|---|---|---|---|---|"]
    for und in NAMES:
        g = {leg: [r for r in rows if r.get("leg") == leg and r["und"] == und and r.get("cost")] for leg in LEGS}
        if not g["atm"]:
            continue
        med = lambda leg: f"${statistics.median(r['cost'] for r in g[leg]):,.0f}" if g[leg] else "—"
        ok = sum(r["cost"] <= 2000 for r in g["atm"]) / len(g["atm"])
        lines.append(f"| {und} | {len(g['atm'])} | {med('atm')} | {med('otm5')} | {med('otm10')} | {ok:.0%} |")
    return "\n".join(lines) + "\n"


def series(rows: list[dict], tag: str, leg: str, exit_: str) -> list[float]:
    by: dict[str, float] = {}
    for r in sorted(rows, key=lambda r: NAMES.index(r["und"])):
        if r.get("leg") == leg and tag in r["tags"] and (r.get("res") or {}).get(exit_) is not None:
            by.setdefault(r["day"], r["res"][exit_])
    return [by[d] for d in sorted(by)]


def hero_table(rows: list[dict]) -> str:
    lines = ["", "## hero 游戏：按日期重放成轮（同一天几只触发只算一只）；$2,000 到 5 倍和 $500 到 20 倍（押满）", "",
             "| 情况 · 腿 · 出场 | 笔数 | 平均 | $2,000 到 5 倍：hero / 轮（区间） | 每个 hero | $500 到 20 倍：hero / 轮（区间） | 每个 hero |",
             "|---|---|---|---|---|---|---|"]
    for tag in OPT_TAGS:
        for leg, lname in LEGS.items():
            for ex in ("d42", "d63", "trail42"):
                xs = series(rows, tag, leg, ex)
                if len(xs) < 12:
                    continue
                g5, g20 = game(xs, 1.0, 0.1, 5.0), game(xs, 1.0, 0.1, 20.0)
                c5, c20 = interval(g5["heroes"], g5["rounds"]) or (0, 0), interval(g20["heroes"], g20["rounds"]) or (0, 0)
                cost5 = g5["rounds"] * 2000 / g5["heroes"] if g5["heroes"] else None
                cost20 = g20["rounds"] * 500 / g20["heroes"] if g20["heroes"] else None
                lines.append(f"| {LABEL[tag]} · {lname} · {ex} | {len(xs)} | {statistics.mean(xs):+.0%} | "
                             f"{g5['heroes']}/{g5['rounds']}（{c5[0]:.0%}–{c5[1]:.0%}） | {('$%s' % format(cost5, ',.0f')) if cost5 else '—'} | "
                             f"{g20['heroes']}/{g20['rounds']}（{c20[0]:.0%}–{c20[1]:.0%}） | {('$%s' % format(cost20, ',.0f')) if cost20 else '—'} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    rep = run(Alpaca())
    gs = groups(rep["rows"])
    years = sorted({e["day"][:4] for e in rep["events"]})
    table, md = report(f"半导体知名股的抄底拿两个月：{years[0]} 年起的价格层，2024 年起买 {DTE[0]}–{DTE[1]} 天的看涨 {date.today().isoformat()}",
                       f"{len(NAMES)} 只：{'、'.join(NAMES)}。价格层 {rep['px_from']} 至 {rep['end']}（拆股调整价）；期权层 {rep['opt_start']} 起，买最近的月度到期（{DTE[0]}–{DTE[1]} 天）"
                       f"平值 / 价外 5% / 价外 10% 的看涨，拿 21 / 42 / 63 个交易日，或翻倍后回撤 40%、或 3 倍先卖，买卖各多付 5%；"
                       f"跑完 {len(rep['done'])} 只{'，预算到了没跑完' if rep['partial'] else ''}，{rep['minutes']} 分钟。"
                       f"没拿到期权数据的原因：{rep['misses'] or '无'}。", gs, EXITS, rep["end"])
    rep["table"] = table
    md = md.replace("## 稳健性检查", table_a(rep["events"]) + "\n## B. 期权层稳健性检查", 1) if "## 稳健性检查" in md else md + table_a(rep["events"])
    md += cost_table(rep["rows"]) + hero_table(rep["rows"])
    md += ("\n## 读法\n\n- 单只股票一天动 2–4%，离 20 日高点 8% 在涨势里也常见，所以多了 15% / 20% 两档；每档只在第一次跌破时算一次。\n"
           "- 两个月的持有期里一定跨一次财报，财报的跳空算在结果里。\n- 期权层只有 2024 年起的数据，NVDA / AVGO / LRCX 2024 年拆股前的合约可能查不到。\n- 只研究，不改交易。\n")
    rep["events"] = [{k: v for k, v in e.items() if k != "i"} for e in rep["events"]]
    for r in rep["rows"]:
        r.pop("path", None)
    save("bottom-semi", rep, md)


if __name__ == "__main__":
    sys.exit(main())
