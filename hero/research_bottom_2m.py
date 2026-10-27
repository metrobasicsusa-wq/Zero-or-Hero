"""Buying the bottom and holding two months (owner, 10-27: 「回测下低点 买期权拿两个月」, after bottom-deep showed the
true low usually comes about a week after the third down day, 2-3% lower, and that 30-60 day calls held 30 sessions did
better than those held 10).

Two layers, like bottom-deep. (A) Prices, 2017 on: every tagged session of SPY / QQQ / SMH / IWM (down streaks of 3 / 4
/ 5 / 6+, the third down day already 8%+ under the 20-session high, the first green close after three down closes,
the first close 8% under the 20-session high, 15% under the 52-week high, every fifth session as the control) with
the ETF held 20 / 42 / 63 sessions (one / two / three months), how much further it fell within the next two months
and how long that took, and the 3x fund (SOXL / TQQQ / SPXL / TNA) held 42 / 63 sessions. (B) Options, 2024-02 on:
the call 75-130 days out (the nearest monthly expiry, so a two-month hold still leaves a month of time value), at
the money, 5% and 10% out, sold at 21 / 42 / 63 sessions, or on a 40% giveback after 2x, or at 3x, the last two
falling back to the 42nd session. Contract lookups go by monthly expiry (the third Friday, or the Thursday before a
holiday) and are cached per expiry; option bars are cached per contract; every miss is counted by reason. Hero games
for $2,000 to 5x and $500 to 20x. A time budget keeps a partial run committable. Research only."""
from __future__ import annotations

import os
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from hero.research_bottom_deep import BUCKETS, ETFS, LEV, PX_START, events as deep_events
from hero.research_bottom_deep import LABEL as DEEP_LABEL
from hero.research_event_turn import Pace
from hero.research_hero_game import game, interval
from hero.research_ideas import report, save

ROOT = Path(__file__).resolve().parent.parent
OPT_START = "2024-02-01"
DTE = (75, 130)
HOLD = 63
OFFS = {"atm": 0.0, "otm5": 0.05, "otm10": 0.10}
LEGS = {"atm": "平值", "otm5": "价外 5%", "otm10": "价外 10%"}
EXITS = {"d21": "第 21 天卖（1 个月）", "d42": "第 42 天（2 个月）", "d63": "第 63 天（3 个月，先到期就到期）",
         "trail42": "翻倍后回撤 40% 卖，否则第 42 天", "take3_42": "3 倍卖，否则第 42 天"}
OPT_TAGS = ("day3", "day3deep", "day5", "green1", "dd8", "every10")
LABEL = {**DEEP_LABEL, "every10": "对照：每第 10 天随便买"}
BUDGET_MIN = float(os.environ.get("RESEARCH_BUDGET_MIN", "100"))


def events(days: list[str], closes: list[float], lev: list[float | None]) -> list[dict]:
    """bottom-deep's tagged sessions, plus the two- and three-month paths: the ETF and the 3x fund at 42 / 63
    sessions, the further fall to the low of the next 42 sessions and how many sessions that took, every tenth
    session as the option layer's control."""
    out = deep_events(days, closes, lev)
    for e in out:
        i = e["i"]
        for k in (42, 63):
            if i + k < len(closes):
                e[f"f{k}"] = round(closes[i + k] / closes[i] - 1, 4)
            if i + k < len(lev) and lev[i] and lev[i + k]:
                e[f"l{k}"] = round(lev[i + k] / lev[i] - 1, 4)
        nxt = closes[i + 1:i + 43]
        low = min(nxt) if nxt else closes[i]
        e["mae42"] = round(low / closes[i] - 1, 4)
        e["days_to_low42"] = (nxt.index(low) + 1) if nxt else 0
        if i % 10 == 0:
            e["tags"].append("every10")
    return out


def monthlies(day: str, lo: int = DTE[0], hi: int = DTE[1]) -> list[str]:
    """The third Fridays between `lo` and `hi` calendar days after `day`, nearest first."""
    d0 = date.fromisoformat(day)
    a, b = d0 + timedelta(days=lo), d0 + timedelta(days=hi)
    out, y, m = [], a.year, a.month
    for _ in range(4):
        first = date(y, m, 1)
        third = first + timedelta(days=(4 - first.weekday()) % 7 + 14)
        if a <= third <= b:
            out.append(third.isoformat())
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


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
    """Per $1 paid: sold at the 21st / 42nd / 63rd session close (the last close when the path ends sooner), on a
    40% giveback after 2x within the first 42 sessions, or at 3x the cost within those 42 (a limit fill)."""
    out_px = lambda c: max(c * (1 - slip), 0.0) / cost - 1
    res = {f"d{k}": out_px(path[k - 1]) for k in (21, 42) if len(path) >= k}
    if not done or not path:
        return {k: round(v, 4) for k, v in res.items()}
    for k in (21, 42):
        res.setdefault(f"d{k}", out_px(path[-1]))
    res["d63"] = out_px(path[-1])
    seg = path[:42]
    best, armed, res["trail42"] = 0.0, False, res["d42"]
    for c in seg:
        best = max(best, c)
        armed = armed or c >= 2 * cost
        if armed and c <= 0.6 * best:
            res["trail42"] = out_px(c)
            break
    res["take3_42"] = (3 * (1 - slip) - 1) if any(c >= 3 * cost for c in seg) else res["d42"]
    return {k: round(v, 4) for k, v in res.items()}


def contracts(client, und: str, exp: str, pace: Pace, misses: dict, cache: dict) -> list[dict]:
    """Every call on the monthly expiry `exp` (or the Thursday before it), cached per underlying and expiry."""
    key = (und, exp)
    if key in cache:
        return cache[key]
    status = "inactive" if exp < date.today().isoformat() else "active"
    cs: list[dict] = []
    try:
        cs = client.option_contracts(und, status=status, type="call",
                                     expiration_date_gte=(date.fromisoformat(exp) - timedelta(days=1)).isoformat(),
                                     expiration_date_lte=exp)
    except Exception as e:
        misses[f"contracts:{type(e).__name__}"] = misses.get(f"contracts:{type(e).__name__}", 0) + 1
    pace.wait()
    if not cs:
        misses["no_contracts"] = misses.get("no_contracts", 0) + 1
    cache[key] = cs
    return cs


def legs(client, und: str, day: str, spot: float, until: str, sessions: list[str], stock: dict[str, float],
         pace: Pace, misses: dict, cache: dict) -> dict[str, dict]:
    """The three calls for one signal day: at the money, 5% out, 10% out on the nearest monthly expiry 75-130 days
    out that has a bar on the signal day (the next monthly otherwise)."""
    out: dict[str, dict] = {}
    for exp in monthlies(day):
        cs = contracts(client, und, exp, pace, misses, cache)
        ks = sorted((float(c["strike_price"]), c["symbol"], c["expiration_date"]) for c in cs)
        if not ks:
            continue
        want = {"atm": min(ks, key=lambda k: abs(k[0] - spot))}
        for name, off in OFFS.items():
            if off:
                want[name] = next((k for k in ks if k[0] >= round(spot * (1 + off), 4)), None)
        bars = cache.setdefault("bars", {})
        need = sorted({k[1] for n, k in want.items() if k and n not in out and k[1] not in bars})
        if need:
            got = {}
            try:
                got = client.option_bars(need, day, f"{until}T23:00:00Z", timeframe="1Day")
            except Exception as e:
                misses[f"bars:{type(e).__name__}"] = misses.get(f"bars:{type(e).__name__}", 0) + 1
            pace.wait()
            for s in need:
                bars[s] = {b["t"][:10]: float(b["c"]) for b in got.get(s, [])}
        for name, k in want.items():
            if not k or name in out:
                continue
            marks = bars.get(k[1], {})
            if not marks.get(day):
                continue
            path, done = option_path(marks[day], marks, sessions, k[2], k[0], stock)
            out[name] = {"option": k[1], "expiry": k[2], "dte": (date.fromisoformat(k[2]) - date.fromisoformat(day)).days,
                         "entry": marks[day], "path": [round(p, 4) for p in path], "done": done,
                         "res": exits(marks[day] * 1.05, path, done, 0.05), "res10": exits(marks[day] * 1.10, path, done, 0.10)}
        if len(out) == len(OFFS):
            break
    for name in OFFS:
        if name not in out:
            misses["no_entry_bar"] = misses.get("no_entry_bar", 0) + 1
    return out


def run(client, budget_min: float = BUDGET_MIN) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    t0 = time.monotonic()
    pace = Pace(client)
    syms = list(ETFS) + list(LEV.values())
    split = client.daily_bars(syms, PX_START, adjustment="split", feed="sip")
    raw = client.daily_bars(list(ETFS), OPT_START, adjustment="raw", feed="sip")
    evs, rows, misses, done, cache = [], [], {}, [], {}
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
        for e in es:
            if e["day"] < OPT_START or not any(t in e["tags"] for t in OPT_TAGS):
                continue
            if (time.monotonic() - t0) / 60 > budget_min:
                stop = True
                break
            i = e["i"]
            got = legs(client, und, e["day"], stock.get(e["day"]) or closes[i], end, days[i + 1:], stock, pace, misses, cache)
            base = {k: v for k, v in e.items() if k != "i"}
            for name, leg in got.items():
                rows.append({**base, "leg": name, **{k: v for k, v in leg.items() if k != "path"}, "path": leg["path"]})
            if not got:
                rows.append({**base, "leg": None, "res": {}})
        if stop:
            break
        done.append(und)
    return {"px_start": PX_START, "opt_start": OPT_START, "end": end, "etfs": list(ETFS), "done": done, "partial": len(done) < len(ETFS),
            "dte": list(DTE), "hold": HOLD, "events": evs, "rows": rows, "misses": misses,
            "minutes": round((time.monotonic() - t0) / 60, 1), "px_from": min((e["day"] for e in evs), default=None),
            "calls": {"contracts": len([k for k in cache if k != "bars"]), "options": len(cache.get("bars", {}))}}


def table_a(evs: list[dict]) -> str:
    def row(name, g):
        if not g:
            return None
        m = lambda k: statistics.mean(e[k] for e in g if k in e) if any(k in e for e in g) else None
        f = lambda v: "—" if v is None else f"{v:+.1%}"
        n42 = [e for e in g if "f42" in e]
        win = sum(e["f42"] > 0 for e in n42) / len(n42) if n42 else 0.0
        return (f"| {name} | {len(g)} | {f(m('f20'))} | {f(m('f42'))} | {f(m('f63'))} | {win:.0%} | "
                f"{f(m('mae42'))} / {statistics.median(e['mae42'] for e in g):+.1%} | {statistics.median(e['days_to_low42'] for e in g):.0f} | "
                f"{f(m('l42'))} | {f(m('l63'))} |")
    lines = ["## A. 价格层（2017 年起）：信号之后拿 1 / 2 / 3 个月，ETF 本身和 3 倍杠杆基金", "",
             "ETF 的 20 / 42 / 63 个交易日收益（约 1 / 2 / 3 个月）；「再跌多少」= 之后 42 天最低收盘对信号日（平均 / 中位数），「到最低几天」= 中位数，"
             "回答「真正的底离信号多远」；3 倍基金拿 42 / 63 天。都不算成本。", "",
             "| 情况 | 笔数 | 20 天 | 42 天 | 63 天 | 42 天赚钱 | 再跌多少 | 到最低几天 | 3 倍拿 42 天 | 3 倍拿 63 天 |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for tag, label in LABEL.items():
        lines.append(row(label, [e for e in evs if tag in e["tags"]]))
    for name, lo, hi in BUCKETS:
        lines.append(row(f"　连跌第 3 天 · {name}", [e for e in evs if "day3" in e["tags"] and lo < e["dd20"] <= hi]))
    for und in ETFS:
        lines.append(row(f"　连跌第 3 天 · {und}", [e for e in evs if "day3" in e["tags"] and e["und"] == und]))
    for und in ETFS:
        lines.append(row(f"　首次跌到 20 日高点下 8% · {und}", [e for e in evs if "dd8" in e["tags"] and e["und"] == und]))
    lines = [x for x in lines if x is not None]
    years = sorted({e["day"][:4] for e in evs})
    lines += ["", "按年份（ETF 42 天收益 / 3 倍基金 42 天 / 笔数）：", "", "| 情况 | " + " | ".join(years) + " |", "|---|" + "---|" * len(years)]
    for tag in ("day3", "day3deep", "dd8", "every5"):
        cells = []
        for y in years:
            g = [e for e in evs if tag in e["tags"] and e["day"].startswith(y) and "f42" in e]
            l = [e["l42"] for e in g if "l42" in e]
            cells.append(f"{statistics.mean(e['f42'] for e in g):+.1%} / {(statistics.mean(l) if l else 0):+.0%} / {len(g)}" if g else "—")
        lines.append(f"| {LABEL[tag]} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def groups(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for tag in OPT_TAGS:
        for leg, lname in LEGS.items():
            out[f"{LABEL[tag]} · {lname}"] = [r for r in rows if r.get("leg") == leg and tag in r["tags"]]
    for tag in ("day3", "dd8"):
        for und in ETFS:
            out[f"　{LABEL[tag]} · {und} · 平值"] = [r for r in rows if r.get("leg") == "atm" and tag in r["tags"] and r["und"] == und]
    return {k: v for k, v in out.items() if v}


def series(rows: list[dict], tag: str, leg: str, exit_: str, key: str = "res") -> list[float]:
    by: dict[str, float] = {}
    for r in sorted(rows, key=lambda r: ETFS.index(r["und"])):
        if r.get("leg") == leg and tag in r["tags"] and (r.get(key) or {}).get(exit_) is not None:
            by.setdefault(r["day"], r[key][exit_])
    return [by[d] for d in sorted(by)]


def hero_table(rows: list[dict]) -> str:
    lines = ["", "## hero 游戏：按日期重放成轮；$2,000 到 $10,000（5 倍）和 $500 到 $10,000（20 倍）各算一遍（押满）", "",
             "拿两个月的单子在时间上会重叠，这里仍按一笔接一笔重放，所以「轮」比真实账本快；看的是成功率，不是用时。", "",
             "| 情况 · 腿 · 出场 | 笔数 | 平均 | $2,000 到 5 倍：hero / 轮（区间） | 每个 hero | $500 到 20 倍：hero / 轮（区间） | 每个 hero |",
             "|---|---|---|---|---|---|---|"]
    for tag in OPT_TAGS:
        for leg, lname in LEGS.items():
            for ex in ("d42", "d63", "trail42", "take3_42"):
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
    table, md = report(f"抄底拿两个月：{years[0]} 年起的价格层，2024 年起买 {DTE[0]}–{DTE[1]} 天的看涨 {date.today().isoformat()}",
                       f"价格层 {rep['px_from']} 至 {rep['end']}（SPY / QQQ / SMH / IWM 和 3 倍基金）；期权层 {rep['opt_start']} 起，买最近的月度到期（{DTE[0]}–{DTE[1]} 天）"
                       f"平值 / 价外 5% / 价外 10% 的看涨，拿 21 / 42 / 63 个交易日，或翻倍后回撤 40%、或 3 倍先卖，买卖各多付 5%；"
                       f"跑完 {len(rep['done'])} 只{'，预算到了没跑完' if rep['partial'] else ''}，{rep['minutes']} 分钟。"
                       f"没拿到期权数据的原因：{rep['misses'] or '无'}。", gs, EXITS, rep["end"])
    rep["table"] = table
    md = md.replace("## 稳健性检查", table_a(rep["events"]) + "\n## B. 期权层稳健性检查", 1) if "## 稳健性检查" in md else md + table_a(rep["events"])
    md += hero_table(rep["rows"])
    md += ("\n## 读法\n\n- 价格层回答「拿两个月够不够等到底」：信号之后 42 天内最低点离信号日多远、几天到；42 / 63 天的收益要和「每第 5 天随便买」比，差额才是信号的价值。\n"
           "- 期权层只有 2024 年起的数据，而且 2024–26 大多是涨的年份，笔数少；价外 10% 是彩票形状，看中位数和去掉最好 3 笔。\n"
           "- 月度到期的合约流动性比周度好，没成交的信号日才会少；misses 里的 no_entry_bar 是按腿数的。\n- 只研究，不改交易。\n")
    rep["events"] = [{k: v for k, v in e.items() if k != "i"} for e in rep["events"]]
    for r in rep["rows"]:
        r.pop("path", None)
    save("bottom-2m", rep, md)


if __name__ == "__main__":
    sys.exit(main())
