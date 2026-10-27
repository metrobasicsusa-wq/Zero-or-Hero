"""The bottom signals traded with a $5-wide bull call spread instead of a naked call (owner, 10-27: 「再开另一个回测
垂直价差 5 的距离」), on the four ETFs and the fourteen semiconductor names of the two-month studies.

At each signal (the first close 8% / 15% under the 20-session high, the third down day already 8% under it, every
twentieth session as the control) buy the nearest monthly call 75-130 days out at the money and sell the call one
strike $5 higher (the "atm5" spread), and the same pair starting 5% out of the money ("otm5"); only expiries whose
strike grid has the exact $5 step count. The spread is marked at the long close minus the short close, at expiry at
its intrinsic value capped at $5, sold at 21 / 42 / 63 sessions, or when it reaches 80% of the $5 width, with 5% paid
on the debit each way (10% in the second column set). Each row carries the debit and the most the spread can pay
(width over debit), since a $5 spread caps the hero game. Prices split-adjusted; contract and bar lookups cached;
misses counted by reason. Research only."""
from __future__ import annotations

import os
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from hero.research_bottom_2m import DTE, OPT_START, contracts, monthlies
from hero.research_bottom_deep import ETFS
from hero.research_bottom_semi import LABEL, NAMES, events
from hero.research_event_turn import Pace
from hero.research_hero_game import game, interval
from hero.research_ideas import report, save

ROOT = Path(__file__).resolve().parent.parent
UNDS = tuple(ETFS) + tuple(NAMES)
PX_START = "2012-01-01"
WIDTH = float(os.environ.get("RESEARCH_SPREAD_WIDTH", "5"))   # owner, 10-27: 「那500 呢 2.5 价差」 -- the width is a knob
NAME = "bottom-spread" if WIDTH == 5.0 else f"bottom-spread{WIDTH:g}".replace(".", "")
HOLD = 63
LEGS = {"atm5": f"平值买、高 ${WIDTH:g} 卖", "otm5": f"价外 5% 买、再高 ${WIDTH:g} 卖"}
EXITS = {"d21": "第 21 天卖（1 个月）", "d42": "第 42 天（2 个月）", "d63": "第 63 天（3 个月，先到期就到期）", "take80": f"价差到 ${0.8 * WIDTH:g}（宽度八成）就卖，否则第 63 天"}
OPT_TAGS = ("dd8", "dd15", "day3deep", "every20")
BUDGET_MIN = float(os.environ.get("RESEARCH_BUDGET_MIN", "110"))


def spread_path(entry: float, long: dict[str, float], short: dict[str, float], sessions: list[str], expiry: str,
                k1: float, stock: dict[str, float]) -> tuple[list[float], bool]:
    """The spread's close each session (long minus short; a leg without a bar keeps its last close), at expiry its
    intrinsic value capped at the width."""
    out, last, l_last, s_last = [], entry, None, None
    for d in sessions[:HOLD]:
        if d > expiry:
            return out, True
        if d == expiry and d in stock:
            px = min(max(stock[d] - k1, 0.0), WIDTH)
        else:
            l_last = long.get(d, l_last)
            s_last = short.get(d, s_last)
            px = (l_last - s_last) if (l_last is not None and s_last is not None) else None
        last = last if px is None else max(min(px, WIDTH), 0.0)
        out.append(last)
        if d == expiry:
            return out, True
    return out, len(out) >= HOLD


def exits(cost: float, path: list[float], done: bool, slip: float) -> dict[str, float]:
    out_px = lambda c: max(c * (1 - slip), 0.0) / cost - 1
    res = {f"d{k}": out_px(path[k - 1]) for k in (21, 42) if len(path) >= k}
    if not done or not path:
        return {k: round(v, 4) for k, v in res.items()}
    for k in (21, 42):
        res.setdefault(f"d{k}", out_px(path[-1]))
    res["d63"] = out_px(path[-1])
    res["take80"] = out_px(0.8 * WIDTH) if any(c >= 0.8 * WIDTH for c in path) else res["d63"]
    return {k: round(v, 4) for k, v in res.items()}


def legs(client, und: str, day: str, spot: float, until: str, sessions: list[str], stock: dict[str, float],
         pace: Pace, misses: dict, cache: dict) -> dict[str, dict]:
    """The two $5-wide spreads for one signal day on the nearest monthly 75-130 days out whose grid has the $5 step
    and whose legs both traded that day (the next monthly otherwise)."""
    out: dict[str, dict] = {}
    for exp in monthlies(day):
        cs = contracts(client, und, exp, pace, misses, cache)
        ks = sorted((float(c["strike_price"]), c["symbol"], c["expiration_date"]) for c in cs)
        if not ks:
            continue
        by_k = {k[0]: k for k in ks}
        want = {}
        for name, k1 in (("atm5", min(ks, key=lambda k: abs(k[0] - spot))),
                         ("otm5", next((k for k in ks if k[0] >= round(spot * 1.05, 4)), None))):
            if name in out or not k1:
                continue
            k2 = by_k.get(round(k1[0] + WIDTH, 2))
            if not k2:
                misses["no_width"] = misses.get("no_width", 0) + 1
                continue
            want[name] = (k1, k2)
        bars = cache.setdefault("bars", {})
        need = sorted({k[1] for pair in want.values() for k in pair if k[1] not in bars})
        if need:
            got = {}
            try:
                got = client.option_bars(need, day, f"{until}T23:00:00Z", timeframe="1Day")
            except Exception as e:
                misses[f"bars:{type(e).__name__}"] = misses.get(f"bars:{type(e).__name__}", 0) + 1
            pace.wait()
            for s in need:
                bars[s] = {b["t"][:10]: float(b["c"]) for b in got.get(s, [])}
        for name, (k1, k2) in want.items():
            ml, ms = bars.get(k1[1], {}), bars.get(k2[1], {})
            if not ml.get(day) or ms.get(day) is None:
                continue
            debit = ml[day] - ms[day]
            if debit <= 0.05:
                misses["debit_too_small"] = misses.get("debit_too_small", 0) + 1
                continue
            path, done = spread_path(debit, ml, ms, sessions, k1[2], k1[0], stock)
            out[name] = {"long": k1[1], "short": k2[1], "k1": k1[0], "k2": k2[0], "expiry": k1[2],
                         "dte": (date.fromisoformat(k1[2]) - date.fromisoformat(day)).days, "entry": round(debit, 4),
                         "cost": round(debit * 100, 0), "max_x": round(WIDTH / debit, 2), "path": [round(p, 4) for p in path], "done": done,
                         "res": exits(debit * 1.05, path, done, 0.05), "res10": exits(debit * 1.10, path, done, 0.10)}
        if len(out) == len(LEGS):
            break
    for name in LEGS:
        if name not in out:
            misses["no_spread"] = misses.get("no_spread", 0) + 1
    return out


def run(client, budget_min: float = BUDGET_MIN) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    t0 = time.monotonic()
    pace = Pace(client)
    split = client.daily_bars(list(UNDS), PX_START, adjustment="split", feed="sip")
    raw = client.daily_bars(list(UNDS), OPT_START, adjustment="raw", feed="sip")
    evs, rows, misses, done, cache = [], [], {}, [], {}
    by_und = {}
    for und in UNDS:
        bs = [b for b in split.get(und, []) if b["t"][:10] <= end]
        days = [b["t"][:10] for b in bs]
        closes = [float(b["c"]) for b in bs]
        es = events(days, closes)
        for e in es:
            e["und"] = und
        evs += es
        by_und[und] = (days, closes, es)
    stop = False
    for und in UNDS:
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
                rows.append({**base, "leg": name, "spot": round(spot, 2), **{k: v for k, v in leg.items() if k != "path"}, "path": leg["path"]})
            if not got:
                rows.append({**base, "leg": None, "res": {}})
        if stop:
            break
        done.append(und)
    return {"opt_start": OPT_START, "end": end, "unds": list(UNDS), "done": done, "partial": len(done) < len(UNDS), "width": WIDTH,
            "dte": list(DTE), "rows": rows, "misses": misses, "minutes": round((time.monotonic() - t0) / 60, 1),
            "calls": {"contracts": len([k for k in cache if k != "bars"]), "options": len(cache.get("bars", {}))}}


def groups(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for tag in OPT_TAGS:
        for leg, lname in LEGS.items():
            out[f"{LABEL[tag]} · {lname}"] = [r for r in rows if r.get("leg") == leg and tag in r["tags"]]
            out[f"　{LABEL[tag]} · {lname} · 四只 ETF"] = [r for r in rows if r.get("leg") == leg and tag in r["tags"] and r["und"] in ETFS]
            out[f"　{LABEL[tag]} · {lname} · 半导体股"] = [r for r in rows if r.get("leg") == leg and tag in r["tags"] and r["und"] in NAMES]
    for und in UNDS:
        out[f"　首次跌到 20 日高点下 8% · {und} · 平值价差"] = [r for r in rows if r.get("leg") == "atm5" and "dd8" in r["tags"] and r["und"] == und]
    return {k: v for k, v in out.items() if v}


def cost_table(rows: list[dict]) -> str:
    lines = ["", f"## 一组价差多少钱、最多赚几倍（信号日的净支出 × 100，中位数；宽度 ${WIDTH:g} ÷ 净支出）", "",
             f"| 标的 | 笔数 | 平值价差一组 | 最多几倍 | 价外价差一组 | 最多几倍 | 平值价差到期拿满 ${WIDTH:g} 的比例 |", "|---|---|---|---|---|---|---|"]
    for und in UNDS:
        g = {leg: [r for r in rows if r.get("leg") == leg and r["und"] == und and r.get("cost")] for leg in LEGS}
        if not g["atm5"]:
            continue
        med = lambda leg, k: (f"${statistics.median(r[k] for r in g[leg]):,.0f}" if k == "cost" else f"{statistics.median(r[k] for r in g[leg]):.1f}x") if g[leg] else "—"
        full = [r for r in g["atm5"] if r.get("done")]
        hit = sum(r["res"].get("d63", -1) >= (WIDTH * 0.95 / (r["entry"] * 1.05) - 1) - 1e-6 for r in full) / len(full) if full else 0.0
        lines.append(f"| {und} | {len(g['atm5'])} | {med('atm5', 'cost')} | {med('atm5', 'max_x')} | {med('otm5', 'cost')} | {med('otm5', 'max_x')} | {hit:.0%} |")
    return "\n".join(lines) + "\n"


def series(rows: list[dict], tag: str, leg: str, exit_: str) -> list[float]:
    by: dict[str, float] = {}
    for r in sorted(rows, key=lambda r: UNDS.index(r["und"])):
        if r.get("leg") == leg and tag in r["tags"] and (r.get("res") or {}).get(exit_) is not None:
            by.setdefault(r["day"], r["res"][exit_])
    return [by[d] for d in sorted(by)]


def hero_table(rows: list[dict]) -> str:
    lines = ["", "## hero 游戏：按日期重放成轮（同一天几只触发只算一只）；$2,000 到 5 倍和 $500 到 20 倍（押满）", "",
             f"${WIDTH:g} 的价差一组最多只能赚「宽度 ÷ 净支出」倍，到 5 倍 / 20 倍要连赢几轮；看的是成功率。", "",
             "| 情况 · 腿 · 出场 | 笔数 | 平均 | $2,000 到 5 倍：hero / 轮（区间） | 每个 hero | $500 到 20 倍：hero / 轮（区间） | 每个 hero |",
             "|---|---|---|---|---|---|---|"]
    for tag in OPT_TAGS:
        for leg, lname in LEGS.items():
            for ex in ("d42", "d63", "take80"):
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
    table, md = report(f"抄底用 ${WIDTH:g} 宽的牛市看涨价差拿两个月：2024 年起，4 只 ETF 和 14 只半导体股 {date.today().isoformat()}",
                       f"{len(UNDS)} 只：{'、'.join(UNDS)}。{rep['opt_start']} 至 {rep['end']}，信号日买最近的月度到期（{DTE[0]}–{DTE[1]} 天）平值看涨、卖高 ${WIDTH:g} 的看涨（另一组从价外 5% 起），"
                       f"拿 21 / 42 / 63 个交易日或价差到 ${0.8 * WIDTH:g} 先卖，净支出买卖各多付 5%；到期按内在价值、最多 ${WIDTH:g} 结算。"
                       f"跑完 {len(rep['done'])} 只{'，预算到了没跑完' if rep['partial'] else ''}，{rep['minutes']} 分钟。没拿到数据的原因：{rep['misses'] or '无'}。",
                       gs, EXITS, rep["end"])
    rep["table"] = table
    md += cost_table(rep["rows"]) + hero_table(rep["rows"])
    md += ("\n## 读法\n\n- 价差把成本降到几百块、把最大收益封在 $5：$900 的 MU 一组平值价差 $5 宽只是 0.5% 的距离，几乎是个 50/50 的硬币；"
           "$200 的 NVDA 是 2.3%，$40 的 INTC 是 12%。「最多几倍」那一栏决定它能不能当 hero 工具。\n"
           "- 和裸买看涨（research/2026-10-27-bottom-2m.md、bottom-semi.md）对照同一信号、同一出场。\n- 只研究，不改交易。\n")
    for r in rep["rows"]:
        r.pop("path", None)
    save(NAME, rep, md)


if __name__ == "__main__":
    sys.exit(main())
