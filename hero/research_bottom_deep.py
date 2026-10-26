"""Bottom timing with more history and more patience (owner, 10-26: 「你回测吧 这需要更多的回测 因为不知道真正的底 所以时间要久一点」,
after SMH's nine straight down closes took the third-down-day call to zero).

Two layers. (A) Prices only, as far back as the daily bars go (asked from 2012; Alpaca serves 2016 on): every session of
SPY / QQQ / SMH / IWM is tagged with its down streak, its drawdown from the 20-session and 252-session highs, and
the first up close after three or more down closes; for each event the ETF's forward return at 5 / 10 / 20 / 40
sessions, how much further it fell before its low of the next 20 sessions and how many sessions that took (the "true
bottom" question), the 3x fund (SOXL / TQQQ / SPXL / TNA) held 10 / 20 / 40 sessions, and a ladder on the 3x fund
(a third at the signal, a third more if a later close is 3% lower, the last third at 6% lower, all out 20 sessions
after the first entry) against buying all at once and against every fifth session. Splits by streak length (3, 4, 5,
6+) and by how deep the slide already was. (B) Options, 2024-02 on: the third down day (all, and only when already
8%+ under the 20-session high), the first green close and the control, with the call 30-60 days out at the money and
5% out, held 10 / 20 / 30 sessions or on a 40% giveback after 2x, and an option ladder (a second call bought at the
fifth down day when the slide went on, both sold 20 sessions after the first). Contract lookups go one week of
expiries at a time (the 20-45 day window returned nothing for SPY / QQQ / IWM in bottom-long) and every miss is
counted by reason. Hero games for $2,000 to 5x and $500 to 20x. A time budget keeps a partial run committable.
Research only."""
from __future__ import annotations

import os
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from hero.research_event_turn import Pace
from hero.research_hero_game import game, interval
from hero.research_ideas import report, save

ROOT = Path(__file__).resolve().parent.parent
ETFS = ("SPY", "QQQ", "SMH", "IWM")
LEV = {"SMH": "SOXL", "QQQ": "TQQQ", "SPY": "SPXL", "IWM": "TNA"}
PX_START, OPT_START = "2012-01-01", "2024-02-01"
HOLD = 30
DTE = (30, 60)
OFFS = {"atm": 0.0, "otm5": 0.05}
EXITS = {"d10": "第 10 天收盘卖", "d20": "第 20 天", "d30": "第 30 天（先到期就到期）", "trail2": "翻倍后回撤 40% 卖，否则第 30 天"}
LADDER = (0.03, 0.06)
BUDGET_MIN = float(os.environ.get("RESEARCH_BUDGET_MIN", "115"))
LABEL = {"day3": "连跌第 3 天", "day3deep": "连跌第 3 天且已离 20 日高点 ≥8%", "day4": "连跌第 4 天", "day5": "连跌第 5 天",
         "day6p": "连跌第 6 天以上", "green1": "连跌 3 天后第一根阳线", "dd8": "首次跌到 20 日高点下 8%",
         "dd15y": "首次跌到 52 周高点下 15%", "every5": "对照：每第 5 天随便买"}
BUCKETS = (("跌得浅（离 20 日高点 >-4%）", -0.04, 1.0), ("-4% 到 -8%", -0.08, -0.04), ("-8% 到 -12%", -0.12, -0.08), ("跌得深（<-12%）", -9.0, -0.12))


def streak_at(closes: list[float], i: int) -> int:
    k = 0
    while i - k >= 1 and closes[i - k] < closes[i - k - 1]:
        k += 1
    return k


def events(days: list[str], closes: list[float], lev: list[float | None]) -> list[dict]:
    """Every tagged session with the ETF's forward path, its further fall to the next-20-session low, the 3x fund held
    10 / 20 / 40 sessions and the ladder (all per $1)."""
    out = []
    for i in range(253, len(days)):
        s = streak_at(closes, i)
        dd20 = closes[i] / max(closes[i - 20:i + 1]) - 1
        dd20p = closes[i - 1] / max(closes[i - 21:i]) - 1
        ddy = closes[i] / max(closes[i - 252:i + 1]) - 1
        ddyp = closes[i - 1] / max(closes[i - 253:i]) - 1
        tags = []
        if s == 3:
            tags.append("day3")
            if dd20 <= -0.08:
                tags.append("day3deep")
        if s == 4:
            tags.append("day4")
        if s == 5:
            tags.append("day5")
        if s >= 6:
            tags.append("day6p")
        if closes[i] > closes[i - 1] and streak_at(closes, i - 1) >= 3:
            tags.append("green1")
        if dd20 <= -0.08 < dd20p:
            tags.append("dd8")
        if ddy <= -0.15 < ddyp:
            tags.append("dd15y")
        if i % 5 == 0:
            tags.append("every5")
        if not tags:
            continue
        fwd = {f"f{k}": round(closes[i + k] / closes[i] - 1, 4) for k in (5, 10, 20, 40) if i + k < len(closes)}
        nxt = closes[i + 1:i + 21]
        low = min(nxt) if nxt else closes[i]
        e = {"day": days[i], "i": i, "streak": s, "dd20": round(dd20, 4), "ddy": round(ddy, 4), "tags": tags, **fwd,
             "mae20": round(low / closes[i] - 1, 4), "days_to_low": (nxt.index(low) + 1) if nxt else 0}
        l0 = lev[i] if i < len(lev) else None
        if l0 and i + 20 < len(lev) and all(lev[j] for j in range(i, min(i + 41, len(lev)))):
            for k in (10, 20, 40):
                if i + k < len(lev):
                    e[f"l{k}"] = round(lev[i + k] / l0 - 1, 4)
            # the ladder: thirds at the signal close, at a close 3% lower, at one 6% lower (within 10 sessions), out at +20
            spent, units = 1 / 3, (1 / 3) / l0
            for lvl in LADDER:
                hit = next((lev[j] for j in range(i + 1, min(i + 11, len(lev))) if lev[j] <= l0 * (1 - lvl)), None)
                if hit:
                    spent += 1 / 3
                    units += (1 / 3) / hit
            e["ladder20"] = round((units * lev[i + 20] + (1 - spent)) - 1, 4)
            e["ladder_spent"] = round(spent, 2)
        out.append(e)
    return out


def table_a(evs: list[dict]) -> str:
    def row(name, g):
        if not g:
            return None
        m = lambda k: statistics.mean(e[k] for e in g if k in e) if any(k in e for e in g) else None
        f = lambda v: "—" if v is None else f"{v:+.1%}"
        win = sum(e.get("f20", 0) > 0 for e in g if "f20" in e) / max(1, sum("f20" in e for e in g))
        return (f"| {name} | {len(g)} | {f(m('f5'))} | {f(m('f10'))} | {f(m('f20'))} | {f(m('f40'))} | {win:.0%} | "
                f"{f(m('mae20'))} / {statistics.median(e['mae20'] for e in g):+.1%} | {statistics.median(e['days_to_low'] for e in g):.0f} | "
                f"{f(m('l20'))} | {f(m('ladder20'))} | {f(m('l40'))} |")
    lines = ["## A. 价格层（有多少年算多少年）：信号之后 ETF 本身、3 倍杠杆基金、分批买", "",
             "ETF 的 5 / 10 / 20 / 40 天收益；「再跌多少」= 之后 20 天最低收盘对信号日（平均 / 中位数），「到最低几天」= 中位数；3 倍基金拿 20 / 40 天；"
             "分批 = 信号日买 1/3，之后 10 天内再跌 3% 补 1/3、跌 6% 再补 1/3，20 天后全卖（没补的钱算 0 收益）。都不算成本。", "",
             "| 情况 | 笔数 | 5 天 | 10 天 | 20 天 | 40 天 | 20 天赚钱 | 再跌多少 | 到最低几天 | 3 倍拿 20 天 | 3 倍分批 20 天 | 3 倍拿 40 天 |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for tag, label in LABEL.items():
        lines.append(row(label, [e for e in evs if tag in e["tags"]]))
    lines.append(row("　连跌第 3 天，按深度：", []) or "")
    for name, lo, hi in BUCKETS:
        lines.append(row(f"　连跌第 3 天 · {name}", [e for e in evs if "day3" in e["tags"] and lo < e["dd20"] <= hi]))
    for name, lo, hi in (("离 52 周高点 <10%", -0.10, 1.0), ("10% 到 20%", -0.20, -0.10), (">20%", -9.0, -0.20)):
        lines.append(row(f"　连跌第 3 天 · {name}", [e for e in evs if "day3" in e["tags"] and lo < e["ddy"] <= hi]))
    for und in ETFS:
        lines.append(row(f"　连跌第 3 天 · {und}", [e for e in evs if "day3" in e["tags"] and e["und"] == und]))
    lines = [x for x in lines if x is not None]
    years = sorted({e["day"][:4] for e in evs})
    lines += ["", "按年份（连跌第 3 天，ETF 20 天收益 / 3 倍分批 20 天 / 笔数）：", "",
              "| 年份 | " + " | ".join(years) + " |", "|---|" + "---|" * len(years)]
    cells = []
    for y in years:
        g = [e for e in evs if "day3" in e["tags"] and e["day"].startswith(y) and "f20" in e]
        cells.append(f"{statistics.mean(e['f20'] for e in g):+.1%} / {statistics.mean(e['ladder20'] for e in g if 'ladder20' in e):+.0%} / {len(g)}"
                     if g and any("ladder20" in e for e in g) else (f"{statistics.mean(e['f20'] for e in g):+.1%} / — / {len(g)}" if g else "—"))
    lines.append("| 连跌第 3 天 | " + " | ".join(cells) + " |")
    ctl = []
    for y in years:
        g = [e for e in evs if "every5" in e["tags"] and e["day"].startswith(y) and "f20" in e]
        ctl.append(f"{statistics.mean(e['f20'] for e in g):+.1%} / {statistics.mean(e['ladder20'] for e in g if 'ladder20' in e):+.0%} / {len(g)}"
                   if g and any("ladder20" in e for e in g) else "—")
    lines.append("| 对照：每第 5 天 | " + " | ".join(ctl) + " |")
    return "\n".join(lines) + "\n"


def option_path(entry: float, marks: dict[str, float], sessions: list[str], expiry: str, strike: float,
                stock: dict[str, float]) -> tuple[list[float], bool]:
    out, last = [], entry
    for d in sessions[:HOLD]:
        if d > expiry:
            return out, True
        px = marks.get(d)
        if px is None and d == expiry and d in stock:
            px = max(stock[d] - strike, 0.0)
        last = last if px is None else px
        out.append(last)
        if d == expiry:
            return out, True
    return out, len(out) >= HOLD


def exits(cost: float, path: list[float], done: bool, slip: float) -> dict[str, float]:
    out_px = lambda c: max(c * (1 - slip), 0.0) / cost - 1
    res = {f"d{k}": out_px(path[k - 1]) for k in (10, 20) if len(path) >= k}
    if not done or not path:
        return {k: round(v, 4) for k, v in res.items()}
    for k in (10, 20):
        res.setdefault(f"d{k}", out_px(path[-1]))
    res["d30"] = out_px(path[-1])
    best, armed, res["trail2"] = 0.0, False, res["d30"]
    for c in path:
        best = max(best, c)
        armed = armed or c >= 2 * cost
        if armed and c <= 0.6 * best:
            res["trail2"] = out_px(c)
            break
    return {k: round(v, 4) for k, v in res.items()}


def contracts(client, und: str, day: str, spot: float, pace: Pace, misses: dict) -> list[dict]:
    """The nearest expiries 30-60 days out, asked one week at a time so SPY's daily expiries do not swamp the page."""
    d0 = date.fromisoformat(day)
    lo = d0 + timedelta(days=DTE[0])
    while lo <= d0 + timedelta(days=DTE[1]):
        hi = min(lo + timedelta(days=6), d0 + timedelta(days=DTE[1]))
        cs = []
        for status in ("inactive", "active"):
            try:
                cs += client.option_contracts(und, status=status, type="call", expiration_date_gte=lo.isoformat(),
                                              expiration_date_lte=hi.isoformat(),
                                              strike_price_gte=f"{spot * 0.98:.2f}", strike_price_lte=f"{spot * 1.08:.2f}")
            except Exception as e:
                misses[f"contracts:{type(e).__name__}"] = misses.get(f"contracts:{type(e).__name__}", 0) + 1
            pace.wait()
        if cs:
            return cs
        lo = hi + timedelta(days=1)
    misses["no_contracts"] = misses.get("no_contracts", 0) + 1
    return []


def legs(client, und: str, day: str, spot: float, until: str, sessions: list[str], stock: dict[str, float],
         pace: Pace, misses: dict) -> dict[str, dict]:
    cs = contracts(client, und, day, spot, pace, misses)
    if not cs:
        return {}
    exp = min(c["expiration_date"] for c in cs)
    ks = sorted((float(c["strike_price"]), c["symbol"]) for c in cs if c["expiration_date"] == exp)
    want = {"atm": min(ks, key=lambda k: abs(k[0] - spot)), "otm5": next((k for k in ks if k[0] >= spot * 1.05), None)}
    syms = sorted({k[1] for k in want.values() if k})
    try:
        bars = client.option_bars(syms, day, f"{until}T23:00:00Z", timeframe="1Day")
    except Exception as e:
        misses[f"bars:{type(e).__name__}"] = misses.get(f"bars:{type(e).__name__}", 0) + 1
        return {}
    pace.wait()
    out = {}
    for name, k in want.items():
        if not k:
            continue
        marks = {b["t"][:10]: float(b["c"]) for b in bars.get(k[1], [])}
        if not marks.get(day):
            misses["no_entry_bar"] = misses.get("no_entry_bar", 0) + 1
            continue
        path, done = option_path(marks[day], marks, sessions, exp, k[0], stock)
        out[name] = {"option": k[1], "expiry": exp, "dte": (date.fromisoformat(exp) - d0_days(day, exp)).days if False else (date.fromisoformat(exp) - date.fromisoformat(day)).days,
                     "entry": marks[day], "path": [round(p, 4) for p in path], "done": done,
                     "res": exits(marks[day] * 1.05, path, done, 0.05), "res10": exits(marks[day] * 1.10, path, done, 0.10)}
    return out


def d0_days(a, b):
    return date.fromisoformat(a)


def ladder_option(first: dict, second: dict | None, sessions_between: int) -> float | None:
    """Two calls, the first at the third down day and the second at the fifth when the slide went on; both sold 20
    sessions after the first. Per $1 of the two tranches (a missing second tranche is cash)."""
    p1 = first.get("path") or []
    if len(p1) < 20:
        return None
    c1 = first["entry"] * 1.05
    r1 = p1[19] * 0.95 / c1 - 1
    if not second:
        return round(r1 / 2, 4)
    p2 = second.get("path") or []
    k = 20 - sessions_between
    if len(p2) < k:
        return None
    c2 = second["entry"] * 1.05
    r2 = p2[k - 1] * 0.95 / c2 - 1
    return round((r1 + r2) / 2, 4)


def run(client, budget_min: float = BUDGET_MIN) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    t0 = time.monotonic()
    pace = Pace(client)
    syms = list(ETFS) + list(LEV.values())
    split = client.daily_bars(syms, PX_START, adjustment="split", feed="sip")
    raw = client.daily_bars(list(ETFS), OPT_START, adjustment="raw", feed="sip")
    evs, rows, misses, done = [], [], {}, []
    by_und = {}
    for und in ETFS:
        bs = [b for b in split.get(und, []) if b["t"][:10] <= end]
        days = [b["t"][:10] for b in bs]
        closes = [float(b["c"]) for b in bs]
        lv = {b["t"][:10]: float(b["c"]) for b in split.get(LEV[und], [])}
        lev = [lv.get(d) for d in days]
        es = events(days, closes, lev)
        for e in es:
            e["und"] = und
        evs += es
        by_und[und] = (days, closes, es)
    stop = False
    for und in ETFS:
        days, closes, es = by_und[und]
        stock = {b["t"][:10]: float(b["c"]) for b in raw.get(und, [])}
        opt_days = {}
        for e in es:
            if e["day"] < OPT_START or not any(t in e["tags"] for t in ("day3", "green1", "every5", "day5")):
                continue
            if (time.monotonic() - t0) / 60 > budget_min:
                stop = True
                break
            i = e["i"]
            got = legs(client, und, e["day"], stock.get(e["day"]) or closes[i], end, days[i + 1:], stock, pace, misses)
            opt_days[e["day"]] = got
            for name, leg in got.items():
                rows.append({**{k: v for k, v in e.items() if k != "i"}, "leg": name, **{k: v for k, v in leg.items() if k != "path"}, "path": leg["path"]})
            if not got:
                rows.append({**{k: v for k, v in e.items() if k != "i"}, "leg": None, "res": {}})
        if stop:
            break
        # the option ladder: a day3 whose slide went on to day5 two sessions later
        for e in es:
            if e["day"] < OPT_START or "day3" not in e["tags"]:
                continue
            first = (opt_days.get(e["day"]) or {}).get("atm")
            if not first:
                continue
            i = e["i"]
            d5 = days[i + 2] if i + 2 < len(days) and streak_at(closes, i + 2) == 5 else None
            second = (opt_days.get(d5) or {}).get("atm") if d5 else None
            lad = ladder_option(first, second, 2)
            if lad is not None:
                rows.append({**{k: v for k, v in e.items() if k != "i"}, "leg": "ladder", "second": bool(second),
                             "res": {"d20": lad}, "res10": {"d20": lad}})
        done.append(und)
    return {"px_start": PX_START, "opt_start": OPT_START, "end": end, "etfs": list(ETFS), "done": done, "partial": len(done) < len(ETFS),
            "events": evs, "rows": rows, "misses": misses, "minutes": round((time.monotonic() - t0) / 60, 1),
            "px_from": min((e["day"] for e in evs), default=None)}


def groups(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for tag in ("day3", "day3deep", "green1", "every5"):
        for leg, lname in (("atm", "平值"), ("otm5", "价外 5%")):
            out[f"{LABEL[tag]} · {lname}"] = [r for r in rows if r.get("leg") == leg and tag in r["tags"]]
    out["连跌第 3 天 · 分批（第 3 天买、第 5 天还跌再买）"] = [r for r in rows if r.get("leg") == "ladder"]
    for und in ETFS:
        out[f"　连跌第 3 天 · {und} · 平值"] = [r for r in rows if r.get("leg") == "atm" and "day3" in r["tags"] and r["und"] == und]
    return {k: v for k, v in out.items() if v}


def series(rows: list[dict], tag: str, leg: str, exit_: str, key: str = "res") -> list[float]:
    by: dict[str, float] = {}
    for r in sorted(rows, key=lambda r: ETFS.index(r["und"])):
        if r.get("leg") == leg and tag in r["tags"] and (r.get(key) or {}).get(exit_) is not None:
            by.setdefault(r["day"], r[key][exit_])
    return [by[d] for d in sorted(by)]


def hero_table(rows: list[dict]) -> str:
    lines = ["", "## hero 游戏：每天一笔，按日期重放成轮；$2,000 到 $10,000（5 倍）和 $500 到 $10,000（20 倍）各算一遍（押满）", "",
             "| 情况 · 腿 · 出场 | 笔数 | 平均 | $2,000 到 5 倍：hero / 轮（区间） | 每个 hero | $500 到 20 倍：hero / 轮（区间） | 每个 hero |",
             "|---|---|---|---|---|---|---|"]
    for tag in ("day3", "day3deep", "green1", "every5"):
        for leg, lname in (("atm", "平值"), ("otm5", "价外 5%")):
            for ex in ("d10", "d20", "trail2"):
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
    xs = series(rows, "day3", "ladder", "d20")
    if len(xs) >= 12:
        g5 = game(xs, 1.0, 0.1, 5.0); c5 = interval(g5["heroes"], g5["rounds"]) or (0, 0)
        lines.append(f"| 连跌第 3 天 · 分批两张 · d20 | {len(xs)} | {statistics.mean(xs):+.0%} | {g5['heroes']}/{g5['rounds']}（{c5[0]:.0%}–{c5[1]:.0%}） | "
                     f"{('$%s' % format(g5['rounds'] * 2000 / g5['heroes'], ',.0f')) if g5['heroes'] else '—'} | — | — |")
    return "\n".join(lines) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    rep = run(Alpaca())
    gs = groups(rep["rows"])
    years = sorted({e["day"][:4] for e in rep["events"]})
    table, md = report(f"抄底回测加长：{years[0]} 年起的价格层，2024 年起的期权层 {date.today().isoformat()}",
                       f"价格层 {rep['px_from']} 至 {rep['end']}（SPY / QQQ / SMH / IWM 和 3 倍基金）；期权层 {rep['opt_start']} 起，买 {DTE[0]}–{DTE[1]} 天到期的看涨，"
                       f"拿 10 / 20 / 30 天或翻倍后回撤 40% 卖，买卖各多付 5%；跑完 {len(rep['done'])} 只{'，预算到了没跑完' if rep['partial'] else ''}。"
                       f"没拿到期权数据的原因：{rep['misses'] or '无'}。", gs, EXITS, rep["end"])
    rep["table"] = table
    md = md.replace("## 稳健性检查", table_a(rep["events"]) + "\n## B. 期权层稳健性检查", 1) if "## 稳健性检查" in md else md + table_a(rep["events"])
    md += hero_table(rep["rows"])
    md += ("\n## 读法\n\n- 价格层回答「真正的底在哪」：信号之后平均还要再跌多少、几天到最低，以及拿 20 / 40 天是不是比拿 5 天好。\n"
           "- 期权层只有 2024 年起的数据，笔数少；分批是第 3 天一张、第 5 天还跌再一张。\n- 只研究，不改交易。\n")
    rep["events"] = [{k: v for k, v in e.items() if k != "i"} for e in rep["events"]]
    for r in rep["rows"]:
        r.pop("path", None)
    save("bottom-deep", rep, md)


if __name__ == "__main__":
    sys.exit(main())
