"""Three months, six months or a year? The same bullish structures at three expiries (2026-11-20).

The owner trades three-month vertical spreads; the question on a day the market fell hard: does a longer expiry hold up better?
The tools study (hero/research_tools.py) showed what sank a three-month bull call spread entered in a decline: 2022,
when the decline outlasted the option. This puts three expiries side by side on the same entry days: the first
monthly expiry 80, 170 and 350 days out; shares, a call at the money, a bull call spread (at the money / 10% higher)
and a cash-secured put 5% lower; SPY, QQQ and seven megacaps; 2016 on.

Option prices are estimated, not real: Black-Scholes with the volatility fitted in research_tools (60-day realized
and VIX3M, fitted on the real three-month legs). For six months the level is scaled by VIX6M / VIX3M; there is no
history of a one-year index, so one year uses that ratio to the power 1.5, a guess. The skew is flattened with the
square root of time. One year is the roughest column and the report says so.

Entries overlap heavily (a new one every day, each held for months), so the report shows the daily averages and,
for the robustness table, one entry a month for each underlying. Exploration: a lead, not a rule.
"""

from __future__ import annotations

import json
import math
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from hero import daybook as db
from hero import research_tools as rt
from hero import robust
from hero.research_daybook_long import expiry
from hero.research_vertical import INDEXES, day_types, env

ROOT = Path(__file__).resolve().parent.parent
START = "2016-01-04"
COST = 0.05
HORIZONS = {"3m": ("3 个月", 80, None), "6m": ("6 个月", 170, 1.0), "1y": ("1 年", 350, 1.5)}   # label, days to the expiry, the power of VIX6M / VIX3M
FITTED_AT = 0.28                         # the years to expiry the skew was fitted at (three months)
UP, DOWN = 1.10, 0.95                    # the spread's short strike and the put's strike, as shares of the price
TOOLS = {"shares": "正股", "call": "单买平值看涨", "spread": "看涨价差（平值 / 高 10%）", "csp": "卖看跌、留足现金（低 5%）"}
ENVS = {"down": "下跌环境", "pullback": "回调环境"}


def term(v3: float | None, v6: float | None, power: float | None) -> float | None:
    """How much higher or lower volatility is taken to be at a longer expiry than at three months."""
    if power is None:
        return 1.0
    return (v6 / v3) ** power if v3 and v6 else None


def structures(spot: float, settle: float, t: float, rate: float, q: float, vol) -> dict[str, tuple[float, float]]:
    """Per share, held to expiry: (profit, money at risk) for each tool. `vol(m)` is the volatility at a strike m x spot."""
    c100 = rt.bs(spot, spot, t, rate, q, vol(1.00), "C")
    c_up = rt.bs(spot, spot * UP, t, rate, q, vol(UP), "C")
    p_dn = rt.bs(spot, spot * DOWN, t, rate, q, vol(DOWN), "P")
    out = {"shares": (settle - spot, spot)}
    paid = c100 * (1 + COST)
    out["call"] = (max(settle - spot, 0.0) - paid, paid)
    width, debit = (UP - 1) * spot, c100 - c_up
    if 0 < debit < width:
        paid = debit * (1 + COST)
        out["spread"] = (max(0.0, min(settle - spot, width)) - paid, paid)
    got = p_dn * (1 - COST)
    out["csp"] = (got - max(DOWN * spot - settle, 0.0), DOWN * spot - got)
    return out


def rows(hist: dict, fits: dict[str, dict], vix6m: dict[str, float], start: str = START) -> list[dict]:
    """Every entry day and expiry whose expiry has passed: the day's environment and each tool's result per $1 at risk."""
    days, sessions, out = hist["days"], set(hist["days"]), []
    for und, f in fits.items():
        closes, at = hist["closes"][und], hist["index"][und]
        for day in days:
            i, v3 = at.get(day), hist["vix3m"].get(day)
            if day < start or i is None or i < 252 or not v3:
                continue
            types = day_types(closes, i, und in INDEXES)
            base = f["p"] * rt.realized(closes, i) + f["q"] * v3 / 100
            spot, rate, q = closes[i], hist["rate"].get(day, 0.02), rt.YIELD.get(und, 0.0)
            for key, (_, min_days, power) in HORIZONS.items():
                exp = expiry(day, min_days, sessions)
                j, ratio = at.get(exp), term(v3, vix6m.get(day), power)
                if j is None or ratio is None:
                    continue
                t = (date.fromisoformat(exp) - date.fromisoformat(day)).days / 365
                flat = math.sqrt(FITTED_AT / t)

                def vol(m: float, base=base, ratio=ratio, flat=flat, skew=f["skew"]) -> float:
                    return base * ratio * (1 + (skew.get(f"{m:.2f}", 1.0) - 1) * flat)
                res = structures(spot, closes[j], t, rate, q, vol)
                out.append({"und": und, "day": day, "hor": key, "env": env(types), "move": closes[j] / spot - 1,
                            "risk": {k: p / r for k, (p, r) in res.items()}, "atm": rt.bs(spot, spot, t, rate, q, vol(1.00), "C") / spot})
    return out


def monthly(xs: list[dict]) -> list[dict]:
    """One entry a month for each underlying and expiry: the first of the month's entries."""
    seen, out = set(), []
    for r in sorted(xs, key=lambda r: r["day"]):
        k = (r["und"], r["hor"], r["day"][:7])
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def stat(xs: list[float]) -> dict | None:
    return {"n": len(xs), "mean": statistics.mean(xs), "won": sum(x > 0 for x in xs) / len(xs), "lost": sum(x <= -0.95 for x in xs) / len(xs)} if xs else None


def pick(xs: list[dict], tool: str, **want) -> list[float]:
    return [r["risk"][tool] for r in xs if tool in r["risk"] and all(want[k](r) for k in want)]


def tables(xs: list[dict]) -> dict:
    out = {}
    for group, keep in (("指数", lambda r: r["und"] in INDEXES), ("个股", lambda r: r["und"] not in INDEXES)):
        sub = [r for r in xs if keep(r)]
        g = {"y2022": {}, "env": {}, "atm": {}, "monthly": {}}
        for h in HORIZONS:
            at_h = [r for r in sub if r["hor"] == h]
            g["atm"][h] = statistics.median(r["atm"] for r in at_h) if at_h else None
            for tool in TOOLS:
                g["y2022"][f"{tool}|{h}"] = {"daily": stat(pick(at_h, tool, y=lambda r: r["day"][:4] == "2022")),
                                             "monthly": stat(pick(monthly(at_h), tool, y=lambda r: r["day"][:4] == "2022"))}
                for e in ENVS:
                    g["env"][f"{e}|{tool}|{h}"] = {"all": stat(pick(at_h, tool, e=lambda r, e=e: r["env"] == e)),
                                                   "y2022": stat(pick(at_h, tool, e=lambda r, e=e: r["env"] == e and r["day"][:4] == "2022")),
                                                   "other": stat(pick(at_h, tool, e=lambda r, e=e: r["env"] == e and r["day"][:4] != "2022"))}
            g["monthly"][h] = [(r["day"], r["risk"]["spread"]) for r in monthly(at_h) if "spread" in r["risk"]]
        out[group] = g
    return out


def pct(x: float | None) -> str:
    return "—" if x is None else f"{x:+.0%}".replace("-", "−")


def cell(s: dict | None) -> str:
    return "—" if not s else f"{pct(s['mean'])}，赚 {s['won']:.0%}，亏光 {s['lost']:.0%}（{s['n']}）"


def report(t: dict, fits: dict, start: str, end: str, checks: list[dict] | None = None) -> str:
    names = [v[0] for v in HORIZONS.values()]
    out = ["# 三个月、六个月还是一年：同样的看涨做法换到期（2026-11-20）", "",
           "模拟盘研究，只记录，不是投资建议。探索档：线索，不是规则。**期权价是估的，不是真实成交价；一年那一栏的波动率是外推的，最粗。**", "",
           f"用户平时做三个月的垂直价差；11-20 大跌那天的问题是：到期远一些的是不是更扛得住。前一晚的研究里，三个月看涨价差在下跌环境栽在 2022 年：跌的时间比期权长。这里把三种到期放在同一批进场日上并排："
           f"80、170、350 天以后的第一个月度到期，{start} 到 {end}，标普、纳指和七只大股票，拿到到期。每 $1 是对「最多能亏的钱」；买入多付 5%、卖出少拿 5%。", "",
           "「亏光」= 亏掉 95% 以上。括号里是笔数：每天一笔、前后重叠，笔数多不等于样本多；最后的稳健性检查按每只每月只算一笔。", ""]
    for group, g in t.items():
        out += [f"## {group}", "", "**2022 年全年进场（不分环境）**", "", "| 做法 | " + " | ".join(names) + " |", "|---|---|---|---|"]
        for tool, label in TOOLS.items():
            out.append(f"| {label} | " + " | ".join(cell(g["y2022"][f"{tool}|{h}"]["daily"]) for h in HORIZONS) + " |")
        for e, elabel in ENVS.items():
            out += ["", f"**在{elabel}进场**（全部年份；2022 年 / 其余年份）", "", "| 做法 | " + " | ".join(names) + " |", "|---|---|---|---|"]
            for tool, label in TOOLS.items():
                cells = []
                for h in HORIZONS:
                    c = g["env"][f"{e}|{tool}|{h}"]
                    cells.append(f"{cell(c['all'])}；{pct(c['y2022']['mean']) if c['y2022'] else '—'} / {pct(c['other']['mean']) if c['other'] else '—'}")
                out.append(f"| {label} | " + " | ".join(cells) + " |")
        out += ["", "平值看涨的价钱占现价（中位数）：" + "，".join(f"{HORIZONS[h][0]} {g['atm'][h]:.1%}" for h in HORIZONS if g["atm"][h] is not None) + "。", "",
                robust.table({f"看涨价差，{HORIZONS[h][0]}": robust.summary(g["monthly"][h], end=end) for h in HORIZONS},
                             title=f"稳健性检查（{group}，看涨价差，每只每月只算第一笔）"), ""]
    out += real_lines(checks or [])
    out += ["## 说明", "",
            "- 波动率的估法：平值 = p × 60 日实现波动 + q × VIX3M（每只各自拟合，重合的两年半里和真实价算出的结果平均差 1%、最大差 4%）；六个月乘 VIX6M / VIX3M；一年没有指数的历史，用这个比值的 1.5 次方，是猜的。",
            "- 偏斜按时间的平方根放平。个股没有自己的期限结构，用的是指数的比值。",
            "- 一年的价差贵得多（平值看涨占现价的比例见各组），同样的钱买到的张数少；每 $1 的结果好，不等于同样本金赚得多。",
            "- 每只的拟合：" + "；".join(f"{u} p={f['p']:.2f} q={f['q']:.2f}" for u, f in fits.items()) + "。"]
    return "\n".join(out) + "\n"


def skew_at(skew: dict[str, float], m: float) -> float:
    """The fitted skew at a strike m x spot: a straight line between the fitted points, flat beyond them."""
    pts = sorted((float(k), v) for k, v in skew.items())
    if m <= pts[0][0]:
        return pts[0][1]
    for (a, va), (b, vb) in zip(pts, pts[1:]):
        if m <= b:
            return va + (vb - va) * (m - a) / (b - a)
    return pts[-1][1]


def against_real(ledger: list[dict], hist: dict, fits: dict[str, dict], vix6m: dict[str, float]) -> list[dict]:
    """The call spreads the scout wrote down at real quotes, priced the way this study prices them: is the estimate near?"""
    out = []
    for e in ledger:
        f, at = fits.get(e["symbol"]), hist["index"].get(e["symbol"], {})
        i, v3 = at.get(e["day"]), hist["vix3m"].get(e["day"])
        if not f or i is None or not v3 or e["kind"] not in ("spread3m", "spread1y") or not e.get("mid"):
            continue
        ratio = term(v3, vix6m.get(e["day"]), None if e["kind"] == "spread3m" else HORIZONS["1y"][2])
        if ratio is None:
            continue
        closes, spot = hist["closes"][e["symbol"]], e["spot"]
        t = (date.fromisoformat(e["expiry"]) - date.fromisoformat(e["day"])).days / 365
        base, flat = f["p"] * rt.realized(closes, i) + f["q"] * v3 / 100, math.sqrt(FITTED_AT / t)
        lo, hi = sorted(leg["strike"] for leg in e["legs"])
        legs = [rt.bs(spot, k, t, hist["rate"].get(e["day"], 0.02), rt.YIELD.get(e["symbol"], 0.0),
                      base * ratio * (1 + (skew_at(f["skew"], k / spot) - 1) * flat), "C") for k in (lo, hi)]
        out.append({"day": e["day"], "symbol": e["symbol"], "kind": e["kind"], "expiry": e["expiry"], "strikes": [lo, hi],
                    "model": legs[0] - legs[1], "mid": e["mid"], "now": e["debit"], "width": hi - lo})
    return out


def real_lines(checks: list[dict]) -> list[str]:
    if not checks:
        return []
    out = ["## 和真实报价对一下", "",
           "侦察（`research/scout/ledger.json`）在大跌日按真实报价记下的看涨价差，用这里的估法再算一遍。能对的只有估法里有的标的，到现在只有几笔，是抽查，不是验证。", "",
           "| 日子 | 标的 | 到期 | 行权价 | 估的 | 真实中间价 | 真实现在就成交 | 估的比中间价 | 最多赚（每 $1）：估的 / 中间价 |", "|---|---|---|---|---|---|---|---|---|"]
    for c in checks:
        out.append(f"| {c['day']} | {c['symbol']} | {c['expiry']} | {c['strikes'][0]:g} / {c['strikes'][1]:g} | ${c['model']:.2f} | ${c['mid']:.2f} | ${c['now']:.2f} | "
                   f"{pct(c['model'] / c['mid'] - 1)} | {pct(c['width'] / c['model'] - 1)} / {pct(c['width'] / c['mid'] - 1)} |")
    far = [c for c in checks if c["kind"] == "spread1y"]
    if far:
        gap = statistics.mean(c["model"] / c["mid"] - 1 for c in far)
        out += ["", f"一年的价差，估的比真实中间价{'便宜' if gap < 0 else '贵'} {abs(gap):.0%}（{len(far)} 笔）。估得便宜，上面一年那一栏的每 $1 结果就偏好：同样的涨幅，真实的本钱更高。"]
    return out + [""]


def load_vix6m() -> dict[str, float]:
    now = datetime.now(timezone.utc)
    return {t.strftime("%Y-%m-%d"): c for t, _, _, _, c in db.yahoo("%5EVIX6M", "1d", datetime(2014, 1, 2, tzinfo=timezone.utc), now + timedelta(days=1)) if c}


def main() -> None:
    hist = rt.load_history()
    fits = rt.fit(rt.observed(json.loads(rt.newest("*-vertical.json").read_text()), hist))
    vix6m = load_vix6m()
    xs = rows(hist, fits, vix6m)
    t = tables(xs)
    book = ROOT / "research" / "scout" / "ledger.json"
    checks = against_real(json.loads(book.read_text()) if book.exists() else [], hist, fits, vix6m)
    text = report(t, fits, START, max(r["day"] for r in xs), checks)
    out = ROOT / "research"
    (out / "2026-11-20-expiries.md").write_text(text)
    slim = {g: {k: v for k, v in d.items() if k != "monthly"} | {"monthly": {h: len(v) for h, v in d["monthly"].items()}} for g, d in t.items()}
    (out / "2026-11-20-expiries.json").write_text(json.dumps({"start": START, "rows": len(xs), "tables": slim, "against_real": checks}, ensure_ascii=False, indent=1) + "\n")
    print(text)


if __name__ == "__main__":
    main()
