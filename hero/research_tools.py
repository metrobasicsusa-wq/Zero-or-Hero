"""Which tool in which environment (owner, 11-19: 「垂直也只是个工具」「重点就是你可以判断现在的环境怎么做」).

Exploration, not a rule: everything here is a lead for notes/leads.md. Three months to expiry, held to expiry, SPY, QQQ
and the seven big stocks, the same four environments as hero.research_vertical (above or below the 50-day average, within
10% of the one-year high or not). Side by side: shares, a bought call, a bought put, a cash-secured put, a covered call,
the four verticals, a straddle.

Two samples:
  real   -- the vertical study's rows (2024-02 on): the entry-day closes of eight real option legs and the settle price.
  model  -- every session since 2016 with estimated option prices: Black-Scholes on a volatility fitted to the real legs
            (the at-the-money one as a mix of the stock's own 60-day movement and VIX3M, the other strikes as a fixed
            share of it per underlying). It exists to see the declines the real sample lacks (late 2018, 2020, 2022);
            the report first shows how far it is from the real prices over the months both cover.

Each tool is given two ways: per $1 at risk (what was paid, or what could be lost), and per $10,000 of stock it stands for
(the same number of shares in every tool), because a call and a covered call tie up very different money.
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from hero import daybook as db
from hero import robust
from hero.research_daybook_long import STOCKS, expiry
from hero.research_ideas import save
from hero.research_vertical import ENVS, INDEXES, day_types, env, now_types, pct, stats

ROOT = Path(__file__).resolve().parent.parent
COST, SHARE_COST = 0.05, 0.0005          # each option trade, each share trade
MODEL_START, MIN_DAYS = "2016-01-04", 80
YIELD = {"SPY": 0.012, "QQQ": 0.006, "AAPL": 0.005, "MSFT": 0.007}
LEGS = ("P0.85", "P0.90", "P0.95", "P1.00", "C0.95", "C1.00", "C1.05", "C1.10")
TOOLS = {
    "shares": "正股",
    "call_atm": "单买看涨（平值）",
    "call_otm": "单买看涨（高 5%）",
    "bull_call": "买入看涨价差（平值买、高 5% 卖）",
    "bull_put": "卖出看跌价差（低 5% 卖、低 10% 买）",
    "csp_atm": "卖看跌、留足现金（平值）",
    "csp_otm": "卖看跌、留足现金（低 5%）",
    "covered": "正股加卖看涨（高 5%）",
    "straddle": "看涨看跌两边都买（平值）",
    "put_atm": "单买看跌（平值）",
    "bear_put": "买入看跌价差（平值买、低 5% 卖）",
    "bear_call": "卖出看涨价差（高 5% 卖、高 10% 买）",
}
CREDIT = ("bull_put", "csp_otm", "csp_atm", "covered", "bear_call")
VOLS = ((0, 16, "16 以下"), (16, 20, "16–20"), (20, 25, "20–25"), (25, 32, "25–32"), (32, 999, "32 以上"))
STRETCHES = {"2018 年底": ("2018-10-01", "2018-12-31"), "2020 年 2–3 月": ("2020-02-20", "2020-03-31"),
             "2022 年": ("2022-01-03", "2022-12-30"), "2025 年 3–4 月": ("2025-03-03", "2025-04-30")}


# ---------- option arithmetic ----------
def cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs(spot: float, k: float, t: float, r: float, q: float, vol: float, kind: str) -> float:
    """Black-Scholes value of a European call ("C") or put ("P"); t in years."""
    if t <= 0 or vol <= 0:
        return max(0.0, spot - k if kind == "C" else k - spot)
    d1 = (math.log(spot / k) + (r - q + vol * vol / 2) * t) / (vol * math.sqrt(t))
    d2 = d1 - vol * math.sqrt(t)
    if kind == "C":
        return spot * math.exp(-q * t) * cdf(d1) - k * math.exp(-r * t) * cdf(d2)
    return k * math.exp(-r * t) * cdf(-d2) - spot * math.exp(-q * t) * cdf(-d1)


def implied(price: float, spot: float, k: float, t: float, r: float, q: float, kind: str) -> float | None:
    """The volatility that gives `price`; None when no volatility between 1% and 300% does."""
    lo, hi = 0.01, 3.0
    if not price or price <= bs(spot, k, t, r, q, lo, kind) or price >= bs(spot, k, t, r, q, hi, kind):
        return None
    for _ in range(50):
        mid = (lo + hi) / 2
        if bs(spot, k, t, r, q, mid, kind) < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def tools(spot: float, settle: float, k: dict[str, float], leg: dict[str, float], cost: float = COST) -> dict[str, tuple[float, float]]:
    """(profit per share, money at risk per share) of each tool held to expiry. `k` maps "0.95" and so on to strikes,
    `leg` maps "C1.00" and so on to entry prices; a tool whose legs are missing or whose prices make no sense is left out.
    Buying pays `cost` more, selling gets `cost` less (a spread on its net price, as hero.research_vertical does)."""
    def have(*names):
        return all(leg.get(n) and leg[n] > 0 for n in names)

    def call(m):
        return max(settle - k[m], 0.0)

    def put(m):
        return max(k[m] - settle, 0.0)
    out = {"shares": (settle * (1 - SHARE_COST) - spot * (1 + SHARE_COST), spot * (1 + SHARE_COST))}
    if have("C1.00"):
        paid = leg["C1.00"] * (1 + cost)
        out["call_atm"] = (call("1.00") - paid, paid)
    if have("C1.05"):
        paid = leg["C1.05"] * (1 + cost)
        out["call_otm"] = (call("1.05") - paid, paid)
        got = leg["C1.05"] * (1 - cost)
        if got < spot:
            out["covered"] = (min(settle, k["1.05"]) - spot + got, spot - got)
    if have("P1.00"):
        paid = leg["P1.00"] * (1 + cost)
        out["put_atm"] = (put("1.00") - paid, paid)
        got = leg["P1.00"] * (1 - cost)
        out["csp_atm"] = (got - put("1.00"), k["1.00"] - got)
    if have("P0.95"):
        got = leg["P0.95"] * (1 - cost)
        out["csp_otm"] = (got - put("0.95"), k["0.95"] - got)
    if have("C1.00", "P1.00"):
        paid = (leg["C1.00"] + leg["P1.00"]) * (1 + cost)
        out["straddle"] = (abs(settle - k["1.00"]) - paid, paid)
    for name, kind, lo, hi, debit in (("bull_call", "C", "1.00", "1.05", True), ("bull_put", "P", "0.90", "0.95", False),
                                      ("bear_put", "P", "0.95", "1.00", True), ("bear_call", "C", "1.05", "1.10", False)):
        dear, cheap = (kind + lo, kind + hi) if kind == "C" else (kind + hi, kind + lo)
        if not have(dear, cheap):
            continue
        width, v0 = k[hi] - k[lo], leg[dear] - leg[cheap]
        if width <= 0 or not 0 < v0 < width:
            continue
        worth = max(0.0, min(settle - k[lo], width)) if kind == "C" else max(0.0, min(k[hi] - settle, width))
        if debit:
            paid = v0 * (1 + cost)
            out[name] = (worth - paid, paid)
        else:
            got = v0 * (1 - cost)
            out[name] = (got - worth, width - got)
    return out


def row(und: str, day: str, exp: str, spot: float, settle: float, types: dict, k: dict, leg: dict, extra: dict | None = None) -> dict:
    """One entry: per $1 at risk ("risk") and per $1 of stock ("stock") for every tool that could be priced."""
    got = tools(spot, settle, k, leg)
    return {"und": und, "day": day, "exp": exp, "env": env(types), "above50": types["above50"], "move": settle / spot - 1,
            "below_short_put": settle < k["0.95"], "above_short_call": settle > k["1.05"],
            "risk": {t: round(p / r, 4) for t, (p, r) in got.items() if r > 0},
            "stock": {t: round(p / spot, 5) for t, (p, r) in got.items() if r > 0}, **(extra or {})}


# ---------- the real sample ----------
def newest(pattern: str) -> Path:
    return sorted((ROOT / "research").glob(pattern))[-1]


def real_rows(rep: dict) -> list[dict]:
    return [row(r["und"], r["day"], r["exp"], r["spot"], r["settle"], r["types"], r["strikes"], r["legs"])
            for r in rep["rows"] if r.get("legs")]


# ---------- the volatility model ----------
def realized(closes: list[float], i: int, n: int = 60) -> float:
    rets = [math.log(closes[j] / closes[j - 1]) for j in range(i - n + 1, i + 1)]
    return statistics.pstdev(rets) * math.sqrt(252)


def fit2(a: list[float], b: list[float], y: list[float]) -> tuple[float, float]:
    """Least squares for y = p * a + q * b (no constant)."""
    saa, sbb, sab = sum(x * x for x in a), sum(x * x for x in b), sum(x * z for x, z in zip(a, b))
    say, sby = sum(x * z for x, z in zip(a, y)), sum(x * z for x, z in zip(b, y))
    det = saa * sbb - sab * sab
    return ((say * sbb - sby * sab) / det, (sby * saa - say * sab) / det)


def observed(rep: dict, hist: dict) -> dict[str, list[dict]]:
    """Per underlying: the at-the-money implied volatility of each real entry, the other legs' volatility as a share of
    it, and what the model would have known that day (60-day realized, VIX3M)."""
    out: dict[str, list[dict]] = {}
    for r in rep["rows"]:
        und, day = r["und"], r["day"]
        at = hist["index"].get(und, {}).get(day)
        v3 = hist["vix3m"].get(day)
        if at is None or at < 60 or not v3 or not r.get("legs"):
            continue
        t = (date.fromisoformat(r["exp"]) - date.fromisoformat(day)).days / 365
        rate, q = hist["rate"].get(day, 0.04), YIELD.get(und, 0.0)
        ivs = {n: implied(p, r["spot"], r["strikes"][n[1:]], t, rate, q, n[0]) for n, p in r["legs"].items() if p}
        if not ivs.get("C1.00") or not ivs.get("P1.00"):
            continue
        atm = (ivs["C1.00"] + ivs["P1.00"]) / 2
        out.setdefault(und, []).append({"day": day, "atm": atm, "rv": realized(hist["closes"][und], at), "v3": v3 / 100,
                                        "skew": {n: v / atm for n, v in ivs.items() if v}})
    return out


def fit(obs: dict[str, list[dict]]) -> dict[str, dict]:
    """Per underlying: at-the-money volatility = p x its 60-day realized + q x VIX3M; each other strike a fixed share of
    it (puts below the price, calls above). "miss" is the typical error out of sample, each half fitted on the other."""
    out = {}
    for und, xs in obs.items():
        xs = sorted(xs, key=lambda x: x["day"])
        p, q = fit2([x["rv"] for x in xs], [x["v3"] for x in xs], [x["atm"] for x in xs])
        h, miss = len(xs) // 2, []
        for tr, te in ((xs[:h], xs[h:]), (xs[h:], xs[:h])):
            if len(tr) > 10 and te:
                a, b = fit2([x["rv"] for x in tr], [x["v3"] for x in tr], [x["atm"] for x in tr])
                miss.append(statistics.mean(abs((a * x["rv"] + b * x["v3"]) / x["atm"] - 1) for x in te))
        skew = {"1.00": 1.0}
        for m, name in (("0.85", "P0.85"), ("0.90", "P0.90"), ("0.95", "P0.95"), ("1.05", "C1.05"), ("1.10", "C1.10")):
            vals = [x["skew"][name] for x in xs if name in x["skew"]]
            skew[m] = statistics.median(vals) if vals else 1.0
        out[und] = {"p": p, "q": q, "skew": skew, "n": len(xs), "miss": statistics.mean(miss) if miss else None}
    return out


def model_legs(spot: float, t: float, rate: float, q: float, atm: float, skew: dict[str, float]) -> tuple[dict, dict]:
    """Strikes (exact shares of the price) and estimated entry prices of the eight legs."""
    k = {m: spot * float(m) for m in skew}
    return k, {n: bs(spot, k[n[1:]], t, rate, q, atm * skew[n[1:]], n[0]) for n in LEGS}


def model_rows(hist: dict, fits: dict[str, dict], start: str = MODEL_START) -> list[dict]:
    days = hist["days"]
    sessions = set(days)
    out = []
    for und, f in fits.items():
        closes, at = hist["closes"][und], hist["index"][und]
        for day in days:
            i = at.get(day)
            v3 = hist["vix3m"].get(day)
            if day < start or i is None or i < 252 or not v3:
                continue
            exp = expiry(day, MIN_DAYS, sessions)
            j = at.get(exp)
            if j is None:
                continue                                                # not expired yet, or no bar that day
            types = day_types(closes, i, und in INDEXES)
            atm = f["p"] * realized(closes, i) + f["q"] * v3 / 100
            t = (date.fromisoformat(exp) - date.fromisoformat(day)).days / 365
            k, leg = model_legs(closes[i], t, hist["rate"].get(day, 0.02), YIELD.get(und, 0.0), atm, f["skew"])
            year = closes[i - 251:i + 1]
            top = max(year)
            out.append(row(und, day, exp, closes[i], closes[j], types, k, leg,
                           {"v3": v3, "since": crossed(closes, i), "dd": closes[i] / top - 1,
                            "high_ago": 251 - max(n for n, c in enumerate(year) if c == top)}))
    return out


def crossed(closes: list[float], i: int) -> int:
    """Sessions since the close last changed sides of its 50-day average (1 = it crossed today)."""
    def above(j):
        return closes[j] >= statistics.mean(closes[j - 49:j + 1])
    side, n = above(i), 1
    while n < 60 and i - n >= 49 and above(i - n) == side:
        n += 1
    return n


def load_history(unds=INDEXES + STOCKS) -> dict:
    """Split-adjusted daily closes since 2014 (Yahoo), VIX3M and the three-month Treasury bill."""
    now = datetime.now(timezone.utc)
    start = datetime(2014, 1, 2, tzinfo=timezone.utc)

    def closes(sym):
        return [(t.strftime("%Y-%m-%d"), c) for t, _, _, _, c in db.yahoo(sym, "1d", start, now + timedelta(days=1))]
    spy = closes("SPY")
    hist = {"days": [d for d, _ in spy], "closes": {}, "index": {},
            "vix3m": dict(closes("%5EVIX3M")), "rate": {d: c / 100 for d, c in closes("%5EIRX")}}
    for und in unds:
        rows = spy if und == "SPY" else closes(und)
        hist["closes"][und] = [c for _, c in rows]
        hist["index"][und] = {d: i for i, (d, _) in enumerate(rows)}
    return hist


# ---------- the report ----------
def cell(rows: list[dict], tool: str, basis: str = "risk") -> list[tuple[str, float]]:
    return [(r["day"], r[basis][tool]) for r in rows if tool in r[basis]]


def line(rows: list[dict], tool: str) -> str:
    s = stats(cell(rows, tool))
    if not s:
        return "—"
    per = statistics.mean(x for _, x in cell(rows, tool, "stock")) * 10_000
    return f"{pct(s['mean'])}，赚 {s['win']:.0%}，${per:+,.0f}"


def by_env(rows: list[dict], title: str, note: str) -> list[str]:
    out = ["", f"### {title}", "", note, "", "| 工具 | " + " | ".join(v.split("（")[0] for v in ENVS.values()) + " | 全部 |",
           "|---|" + "---|" * (len(ENVS) + 1)]
    for tool, name in TOOLS.items():
        cells = [line([r for r in rows if r["env"] == e], tool) for e in ENVS] + [line(rows, tool)]
        out.append(f"| {name} | " + " | ".join(cells) + " |")
    counts = []
    for e in ENVS:
        sub = [r for r in rows if r["env"] == e]
        counts.append(f"{len(sub)} 笔 / {len({r['day'][:7] for r in sub})} 个月")
    out.append("| 笔数 / 进场月份 | " + " | ".join(counts) + f" | {len(rows)} 笔 |")
    return out


def groups(rows: list[dict]) -> dict[str, list[dict]]:
    return {"指数（SPY、QQQ）": [r for r in rows if r["und"] in INDEXES], "个股（七巨头）": [r for r in rows if r["und"] in STOCKS]}


def worst_year(rows: list[dict], tool: str) -> str:
    years: dict[str, list[float]] = {}
    for d, x in cell(rows, tool):
        years.setdefault(d[:4], []).append(x)
    big = {y: statistics.mean(v) for y, v in years.items() if len(v) >= 20}
    if not big:
        return "—"
    y = min(big, key=big.get)
    return f"{y}：{pct(big[y])}"


def today(rows: list[dict], e: str) -> list[str]:
    """Every tool in one environment: the average, how often it paid, dollars per $10,000 of stock on average and in the
    worst twentieth of entries, and its worst entry year."""
    sub = [r for r in rows if r["env"] == e]
    out = ["| 工具 | 每 $1 风险 | 赚的比例 | 每 $1 万正股：平均 | 每 $1 万正股：最差的 5% | 最差的一年（每 $1 风险） |", "|---|---|---|---|---|---|"]
    for tool, name in TOOLS.items():
        s, per = stats(cell(sub, tool)), sorted(x * 10_000 for _, x in cell(sub, tool, "stock"))
        if not s or len(per) < 30:
            out.append(f"| {name} | — | — | — | — | — |")
            continue
        out.append(f"| {name} | {pct(s['mean'])} | {s['win']:.0%} | ${statistics.mean(per):+,.0f} | ${per[len(per) // 20]:+,.0f} | {worst_year(sub, tool)} |")
    out.append(f"\n{len(sub)} 笔进场，{len({r['day'][:7] for r in sub})} 个进场月份。")
    return out


def report(real: list[dict], model: list[dict], fits: dict, now: dict) -> tuple[dict, str]:
    table: dict = {}
    out = ["# 哪种环境用哪种工具：三个月到期，拿到到期（2026-11-20，探索）", "",
           "用户 11-19：「垂直也只是个工具」「重点就是你可以判断现在的环境怎么做」。这一页把正股、单买期权、卖看跌、备兑、四种垂直价差、两边都买并排放，"
           "按进场那天的环境分开看。**探索档：这里的每一条都是线索，不是规则**；多切了几刀，没有事先写及格线，想拿去用的要另外验证。只记录，不交易。", "",
           "每格三个数：每 $1 风险的平均收益，赚的比例，和「每 $1 万正股对应的股数」平均赚多少美元（同样的股数，各工具占的钱不同，这个数可以直接比）。"
           f"期权每次买卖成本 {COST:.0%}，正股 {SHARE_COST:.2%}；全部拿到到期。环境：在不在 50 日均线上方 × 离一年高点是否 10% 以上。"]
    if now:
        out += ["", "## 现在各标的在哪种环境", "", "| 标的 | 环境 | 对 50 日均线 | 对一年高点 |", "|---|---|---|---|"]
        out += [f"| {und} | {ENVS[env(t)].split('（')[0]} | {t['vs50']:+.1%} | {t['vs_high']:+.1%} |" for und, t in now.items()]
        out += ["", "下面是这几种环境里，2016 年以来每种工具的结果（期权价是估的，见第二节；是那一组的平均，不是某一只自己的）。"
                "「最差的 5%」= 二十次里最坏的那一次大概亏多少；「最差的一年」= 按进场年份平均最低的那一年。"]
        for g, rows in groups(model).items():
            for e in ENVS:
                names = [u for u, t in now.items() if env(t) == e and (u in INDEXES) == g.startswith("指数")]
                if names:
                    out += ["", f"### {'、'.join(names)}：{ENVS[e]}", ""] + today(rows, e)
    out += ["", "## 一、真实期权价（2024-02 到 2026-07 进场）"]
    for g, rows in groups(real).items():
        out += by_env(rows, g, "进场日和到期日的价格都是真实成交价。这段时间大部分在涨，「下跌」环境只有几段。")
        table[f"real|{g}"] = {e: {t: stats(cell([r for r in rows if r["env"] == e], t)) for t in TOOLS} for e in ENVS}

    out += ["", "## 二、估算的期权价（2016 年起每个交易日）", "",
            "为了看到真实样本里没有的行情（2018 年底、2020 年、2022 年），用模型估进场时的期权价：平值的波动率 = 这只股票自己 60 天的实际波动 × p + VIX3M × q，"
            "其他行权价按固定比例（都从 2024-02 以来的真实期权价里量出来）；到期按正股真实收盘价结算。**期权价是估的，正股的涨跌是真的。**", "",
            "| 标的 | p（自己的波动） | q（VIX3M） | 样本外的典型误差 | 低 10% 看跌 / 高 10% 看涨的波动率是平值的 |", "|---|---|---|---|---|"]
    for und, f in fits.items():
        out.append(f"| {und} | {f['p']:.2f} | {f['q']:.2f} | {f['miss']:.0%} | {f['skew']['0.90']:.2f} 倍 / {f['skew']['1.10']:.2f} 倍 |")
    lo, hi = min(r["day"] for r in real), max(r["day"] for r in real)
    same = [r for r in model if lo <= r["day"] <= hi]
    out += ["", "**先对账：同一段时间（真实样本覆盖的那些月份），估算价和真实价各算出多少。**", "",
            "| 工具 | 指数：真实 | 指数：估算 | 个股：真实 | 个股：估算 |", "|---|---|---|---|---|"]
    check = {}
    for tool, name in TOOLS.items():
        cells = []
        for g in groups(real):
            a, b = stats(cell(groups(real)[g], tool)), stats(cell(groups(same)[g], tool))
            cells += [f"{pct(a['mean'])}，赚 {a['win']:.0%}" if a else "—", f"{pct(b['mean'])}，赚 {b['win']:.0%}" if b else "—"]
            check[f"{g}|{tool}"] = (a["mean"] if a else None, b["mean"] if b else None)
        out.append(f"| {name} | " + " | ".join(cells) + " |")
    table["check"] = check
    gaps = [abs(a - b) for a, b in check.values() if a is not None and b is not None]
    out += ["", f"两边平均差 {statistics.mean(gaps):.0%}，最大差 {max(gaps):.0%}（每 $1 风险）。真实样本每 5 个交易日取一天、行权价取 $5 整数倍，估算样本每天都取、行权价取准确的比例，"
            "所以不会完全一样；差得多的那几格，下面估算价的结果就要打折看。"]
    for g, rows in groups(model).items():
        out += by_env(rows, g + "：2016 年起", "期权价是估的。")
        table[f"model|{g}"] = {e: {t: stats(cell([r for r in rows if r["env"] == e], t)) for t in TOOLS} for e in ENVS}

    out += ["", "### 「跌深了开看涨」在每一年", "", "下跌环境（均线下方、离高点 10% 以上）里开仓，按进场年份。每格：买入看涨价差每 $1 风险的平均（笔数）｜正股三个月的平均涨跌。", "",
            "| 年份 | 指数 | 个股 |", "|---|---|---|"]
    years = sorted({r["day"][:4] for r in model})
    for y in years:
        cells = []
        for g, rows in groups(model).items():
            sub = [r for r in rows if r["env"] == "down" and r["day"][:4] == y]
            s = stats(cell(sub, "bull_call"))
            cells.append(f"{pct(s['mean'])}（{s['n']}）｜{statistics.mean(r['move'] for r in sub):+.1%}" if s else "—")
        out.append(f"| {y} | " + " | ".join(cells) + " |")
    out += ["", "### 几段真跌的行情里，各种工具", "", "那段时间里每天进场、三个月后到期，不分环境。每格：每 $1 风险的平均，赚的比例。", ""]
    for g, rows in groups(model).items():
        out += [f"**{g}**", "", "| 工具 | " + " | ".join(STRETCHES) + " |", "|---|" + "---|" * len(STRETCHES)]
        for tool, name in TOOLS.items():
            cells = []
            for a, b in STRETCHES.values():
                s = stats(cell([r for r in rows if a <= r["day"] <= b], tool))
                cells.append(f"{pct(s['mean'])}，赚 {s['win']:.0%}" if s else "—")
            out.append(f"| {name} | " + " | ".join(cells) + " |")
        out.append("")

    out += ["## 三、刚换边的头 10 天（估算价，2016 年起）", "",
            "「刚站回」= 收盘回到 50 日均线上方的头 10 个交易日；「刚跌破」= 跌到下方的头 10 个交易日；其余是已经在那一边 10 天以上。每格：每 $1 风险的平均，赚的比例，每 $1 万正股。", ""]
    for g, rows in groups(model).items():
        parts = {"刚站回均线": [r for r in rows if r["above50"] and r["since"] <= 10], "在上方 10 天以上": [r for r in rows if r["above50"] and r["since"] > 10],
                 "刚跌破均线": [r for r in rows if not r["above50"] and r["since"] <= 10], "在下方 10 天以上": [r for r in rows if not r["above50"] and r["since"] > 10]}
        out += [f"**{g}**", "", "| 工具 | " + " | ".join(f"{k}（{len(v)}）" for k, v in parts.items()) + " |", "|---|" + "---|" * len(parts)]
        for tool in ("shares", "call_atm", "bull_call", "bull_put", "csp_otm", "covered", "straddle", "bear_put"):
            out.append(f"| {TOOLS[tool]} | " + " | ".join(line(v, tool) for v in parts.values()) + " |")
        out.append("")
        table[f"cross|{g}"] = {k: {t: stats(cell(v, t)) for t in TOOLS} for k, v in parts.items()}

    out += ["## 四、波动高的时候，收钱的工具值不值（估算价，2016 年起）", "",
            "按进场那天的 VIX3M 分档。每格：每 $1 风险的平均，赚的比例；括号里是到期时正股跌破「低 5%」那个行权价的比例（卖出的看跌被打穿）。", ""]
    for g, rows in groups(model).items():
        out += [f"**{g}**", "", "| 工具 | " + " | ".join(f"VIX3M {n}" for *_, n in VOLS) + " |", "|---|" + "---|" * len(VOLS)]
        for tool in CREDIT + ("bull_call", "shares"):
            cells = []
            for a, b, _ in VOLS:
                sub = [r for r in rows if a <= r["v3"] < b]
                s = stats(cell(sub, tool))
                cells.append(f"{pct(s['mean'])}，赚 {s['win']:.0%}" if s else "—")
            out.append(f"| {TOOLS[tool]} | " + " | ".join(cells) + " |")
        hit = []
        for a, b, _ in VOLS:
            sub = [r for r in rows if a <= r["v3"] < b]
            hit.append(f"{sum(r['below_short_put'] for r in sub) / len(sub):.0%}（{len(sub)} 笔）" if sub else "—")
        out += ["| 到期时跌破低 5% 的比例 | " + " | ".join(hit) + " |", ""]

    out += ["## 五、下跌环境里，有没有哪把尺能提前分开 2022（看了结果才切的，只当线索）", "",
            "下跌环境里开买入看涨价差，十年里只有 2022 年进场的是亏的。下面按三把尺再切一刀，每格：全部（笔数）｜2022 年进场的｜其余年份。每 $1 风险的平均。", ""]
    cuts = {"离一年高点过了多少个交易日": ("high_ago", ((0, 30, "30 以内"), (30, 60, "30–60"), (60, 120, "60–120"), (120, 999, "120 以上"))),
            "离一年高点多深": ("dd", ((-0.13, 0, "10%–13%"), (-0.18, -0.13, "13%–18%"), (-0.25, -0.18, "18%–25%"), (-9, -0.25, "25% 以上"))),
            "进场日的 VIX3M": ("v3", ((0, 22, "22 以下"), (22, 27, "22–27"), (27, 32, "27–32"), (32, 999, "32 以上")))}
    for g, rows in groups(model).items():
        down = [r for r in rows if r["env"] == "down"]
        out += [f"**{g}**", ""]
        for title, (key, bins) in cuts.items():
            out += [f"| {title} | " + " | ".join(n for *_, n in bins) + " |", "|---|" + "---|" * len(bins)]
            cells = []
            for a, b, _ in bins:
                sub = [r for r in down if a <= r[key] < b]
                parts = [stats(cell(x, "bull_call")) for x in (sub, [r for r in sub if r["day"][:4] == "2022"], [r for r in sub if r["day"][:4] != "2022"])]
                cells.append("｜".join(f"{pct(x['mean'])}（{x['n']}）" if x else "—" for x in parts))
            out += ["| 买入看涨价差 | " + " | ".join(cells) + " |", ""]

    checks = {}
    for g, rows in groups(model).items():
        for tool in ("bull_call", "bull_put", "csp_otm", "covered", "call_atm", "straddle"):
            for e in ("down", "up"):
                checks[f"{g.split('（')[0]}·{ENVS[e].split('（')[0]}·{TOOLS[tool].split('（')[0]}"] = robust.summary(
                    cell([r for r in rows if r["env"] == e], tool))
    out += [robust.table(checks, "稳健性检查（估算价，2016 年起；每 $1 风险）").rstrip(), "",
            "「再多付 5% 成本」那一列是按占用的全部本金算的：对买入的期权和价差有意义（在已经扣的 5% 上再加）；对「卖看跌、留足现金」和「正股加卖看涨」没有意义（本金是整只股票的钱，不是期权的钱），那两行的这条提醒不算数。",
            "", "## 说明", "",
            "- 每天进一笔、每笔拿三个月，前后六十多笔是重叠的；十年大约四十个不重叠的季度，「下跌」环境集中在 2018 年底、2020 年春、2022 年、2025 年春这几段。笔数多不等于证据多。",
            "- 估算价没有财报前后波动率的涨落、没有盘口价差，行权价也不是真实挂牌的那几个；个股的误差比指数大（见上面的表）。",
            "- 七巨头是今天回头看的赢家，2016 年没人知道是这七只；个股那一半天然偏好看涨的工具。",
            "- 「每 $1 万正股」把所有工具放在同样的股数上比：一张看涨和 100 股正股对应同样的股数，但占的钱差十倍以上。",
            "- 全部是「拿到到期」。中途走的结果见 research/2026-11-19-vertical.md（只有真实价、只有垂直价差）。"]
    return table, "\n".join(out) + "\n"


def main() -> None:
    rep = json.loads(newest("*-vertical.json").read_text())
    hist = load_history()
    fits = fit(observed(rep, hist))
    real, model = real_rows(rep), model_rows(hist, fits)
    table, md = report(real, model, fits, now_types(INDEXES + STOCKS))
    save("tools", {"generated": date.today().isoformat(), "fits": fits, "real_rows": len(real), "model_rows": len(model), "table": table}, md)


if __name__ == "__main__":
    main()
