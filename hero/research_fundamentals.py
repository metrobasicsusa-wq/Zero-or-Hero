"""After the report: if the business is still fine, is a post-earnings drop a buy? (owner, 10-29: 「那也要做个财报后续要
怎么看 基本面 没坏都可以」.)

Events: research/2026-10-02-earnings-reactions.json (98 large caps, 2021-10..2026-10). The fundamentals come from the
reports themselves, through SEC XBRL (companyconcept: Revenues / RevenueFromContractWithCustomerExcludingAssessedTax /
SalesRevenueNet / RevenuesNetOfInterestExpense, quarterly; a missing fourth quarter is the fiscal year less the first
three), so nothing here needs analyst estimates:
  the quarter reported = the latest quarter ending 5-110 days before the release;
  revenue growth = that quarter against the same quarter a year earlier; the change in growth = that growth less the
  previous quarter's growth.
  intact   = revenue still growing and growth slowed by less than 5 points ("没坏");
  slowing  = still growing but growth slowed by 5 points or more;
  shrinking = revenue down on a year ago.
Guidance is not in XBRL (NOW 10-28 fell on its next-quarter guide with this quarter fine), so "intact" here means the
quarter just reported; the reaction itself carries the guide.
Outcomes from the reaction day's close: 5 / 20 / 60 sessions later minus SPY (Yahoo, split-adjusted), grouped by the
reaction (fell 5%+, fell, rose, rose 5%+) and the fundamentals. Robust on buying the 5%+ drops at the reaction close,
held 20 and 60 sessions (0.1% stock cost). Survivorship: today's large caps. Record only.
Run: python -m hero.research_fundamentals
"""

from __future__ import annotations

import json
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from hero import robust

ROOT = Path(__file__).resolve().parent.parent
EVENTS = ROOT / "research" / "2026-10-02-earnings-reactions.json"
CONCEPTS = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "RevenuesNetOfInterestExpense")
SLOW = 0.05
STOCK_COST = 0.001
HORIZONS = (5, 20, 60)


def _d(s: str) -> date:
    return date.fromisoformat(s[:10])


def quarterly(facts: list[dict]) -> dict[str, float]:
    """period end -> quarterly value: ~3-month durations as filed (latest filing wins), and a missing fiscal fourth
    quarter as the year less its three quarters."""
    q, years = {}, {}
    for f in sorted(facts, key=lambda f: f.get("filed", "")):
        if "start" not in f:
            continue
        days = (_d(f["end"]) - _d(f["start"])).days
        if 80 <= days <= 100:
            q[f["end"]] = float(f["val"])
        elif 350 <= days <= 380:
            years[f["end"]] = (f["start"], float(f["val"]))
    for end, (start, val) in years.items():
        if end in q:
            continue
        inside = [v for e, v in q.items() if start < e < end and (_d(end) - _d(e)).days < 300]
        if len(inside) == 3:
            q[end] = val - sum(inside)
    return q


def _near(q: dict[str, float], target: date, tol: int) -> tuple[str, float] | None:
    best = min(q, key=lambda e: abs((_d(e) - target).days), default=None)
    return (best, q[best]) if best and abs((_d(best) - target).days) <= tol else None


def growth(q: dict[str, float], release: date) -> dict | None:
    """Revenue growth of the quarter a release reports, the previous quarter's, and the change."""
    ends = [e for e in q if 5 <= (release - _d(e)).days <= 110]
    if not ends:
        return None
    end = max(ends)
    a = _near(q, _d(end) - timedelta(days=364), 12)
    p = _near(q, _d(end) - timedelta(days=91), 15)
    if not a or a[1] <= 0:
        return None
    g = q[end] / a[1] - 1
    out = {"end": end, "growth": g, "prev": None, "change": None}
    if p:
        pa = _near(q, _d(p[0]) - timedelta(days=364), 12)
        if pa and pa[1] > 0:
            out["prev"] = p[1] / pa[1] - 1
            out["change"] = g - out["prev"]
    return out


def label(gr: dict | None) -> str | None:
    if not gr:
        return None
    if gr["growth"] <= 0:
        return "shrinking"
    if gr["change"] is not None and gr["change"] <= -SLOW:
        return "slowing"
    return "intact"


NAMES = {"intact": "基本面没坏（营收还在增长、增速放缓不到 5 个点）", "slowing": "增速明显放缓（掉 5 个点以上）", "shrinking": "营收同比下降"}


def forward(e: dict, bars: dict[str, dict], spy: dict[str, dict]) -> dict:
    days, sd = sorted(bars), sorted(spy)
    out = {}
    if e["reaction_day"] not in bars or e["reaction_day"] not in spy:
        return out
    i, j = days.index(e["reaction_day"]), sd.index(e["reaction_day"])
    for h in HORIZONS:
        if i + h < len(days) and j + h < len(sd):
            out[h] = (bars[days[i + h]]["c"] / bars[days[i]]["c"] - 1) - (spy[sd[j + h]]["c"] / spy[sd[j]]["c"] - 1)
    return out


def groups(rows: list[dict]) -> dict[str, list[dict]]:
    react = {"财报后跌 5% 以上": lambda r: r["move"] <= -0.05, "财报后跌 0–5%": lambda r: -0.05 < r["move"] <= 0,
             "财报后涨 0–5%": lambda r: 0 < r["move"] < 0.05, "财报后涨 5% 以上": lambda r: r["move"] >= 0.05}
    g = {"全部（有营收数据的）": [r for r in rows if r["label"]]}
    for rname, keep in react.items():
        rs = [r for r in rows if r["label"] and keep(r)]
        g[rname] = rs
        for lab, name in NAMES.items():
            g[f"　{rname} · {name}"] = [r for r in rs if r["label"] == lab]
        g[f"　{rname} · 基本面没坏而且增速加快（+2 个点以上）"] = [r for r in rs if r["label"] == "intact" and (r["change"] or 0) >= 0.02]
    g["跌 10% 以上 · 基本面没坏"] = [r for r in rows if r["label"] == "intact" and r["move"] <= -0.10]
    g["跌 10% 以上 · 增速放缓或下降"] = [r for r in rows if r["label"] in ("slowing", "shrinking") and r["move"] <= -0.10]
    return g


def table(rows: list[dict], end: str) -> tuple[dict, str]:
    g = groups(rows)
    pct = lambda v: "—" if v is None else f"{v:+.1%}"
    md = ["| 情况 | 次数 | 反应日平均 | 之后 5 天减 SPY | 20 天：平均 | 中位数 | 跑赢比例 | 60 天：平均 | 中位数 |",
          "|---|---|---|---|---|---|---|---|---|"]
    tab, rob = {}, {}
    for name, rs in g.items():
        x20 = [r["fwd"][20] for r in rs if 20 in r["fwd"]]
        if len(x20) < 5:
            md.append(f"| {name} | {len(rs)} | — | — | — | — | — | — | — |")
            continue
        x5 = [r["fwd"][5] for r in rs if 5 in r["fwd"]]
        x60 = [r["fwd"][60] for r in rs if 60 in r["fwd"]]
        row = {"n": len(rs), "move": statistics.mean(r["move"] for r in rs), "d5": statistics.mean(x5) if x5 else None,
               "d20": statistics.mean(x20), "d20_med": statistics.median(x20), "win20": sum(x > 0 for x in x20) / len(x20),
               "d60": statistics.mean(x60) if x60 else None, "d60_med": statistics.median(x60) if x60 else None}
        tab[name] = row
        md.append(f"| {name} | {row['n']} | {pct(row['move'])} | {pct(row['d5'])} | {pct(row['d20'])} | {pct(row['d20_med'])} | "
                  f"{row['win20']:.0%} | {pct(row['d60'])} | {pct(row['d60_med'])} |")
    for name in ("　财报后跌 5% 以上 · " + NAMES["intact"], "　财报后跌 5% 以上 · " + NAMES["slowing"],
                 "　财报后跌 5% 以上 · " + NAMES["shrinking"], "财报后跌 5% 以上"):
        rs = g.get(name) or []
        for h in (20, 60):
            xs = [(r["day"], r["fwd"][h]) for r in rs if h in r["fwd"]]
            if len(xs) >= 5:
                rob[f"{name.strip()} · 收盘买、拿 {h} 天（减 SPY）"] = robust.summary(xs, end, extra_cost=STOCK_COST)
    return {"groups": tab, "robust": rob}, "\n".join(md) + "\n\n" + robust.table(rob, "稳健性检查（财报后跌 5% 以上，反应日收盘买）") + "\n"


def run() -> dict:
    from concurrent.futures import ThreadPoolExecutor
    from hero import research_earnings as re_
    from hero.research_earnings_control import yahoo
    events = json.loads(EVENTS.read_text())["events"]
    syms = sorted({e["symbol"] for e in events})
    ciks = re_.cik_map()
    facts: dict[str, dict[str, float]] = {}
    for s in syms:
        if s not in ciks:
            continue
        merged: dict[str, float] = {}
        for c in CONCEPTS:
            try:
                d = re_._sec(f"https://data.sec.gov/api/xbrl/companyconcept/CIK{ciks[s]:010d}/us-gaap/{c}.json")
            except Exception:
                continue
            for end, v in quarterly(d.get("units", {}).get("USD", [])).items():
                merged.setdefault(end, v)   # the first concept that has the quarter wins
        facts[s] = merged

    def load(s):
        try:
            return s, yahoo(s, "2021-01-01")
        except Exception:
            return s, {}
    with ThreadPoolExecutor(12) as ex:
        px = dict(ex.map(load, syms + ["SPY"]))
    rows = []
    for e in events:
        q = facts.get(e["symbol"]) or {}
        rel = datetime.fromisoformat(e["accepted"]).date()
        gr = growth(q, rel) if q else None
        rows.append({"symbol": e["symbol"], "day": e["reaction_day"], "move": e["move"], "label": label(gr),
                     "growth": gr["growth"] if gr else None, "change": gr["change"] if gr else None,
                     "fwd": forward(e, px.get(e["symbol"]) or {}, px["SPY"])})
    return {"generated": date.today().isoformat(), "rows": rows, "with_revenue": sum(1 for r in rows if r["label"]),
            "no_revenue": sorted({r["symbol"] for r in rows if not r["label"]})}


def main() -> None:
    from hero.research_ideas import save
    rep = run()
    end = max(r["day"] for r in rep["rows"])
    tab, md = table(rep["rows"], end)
    rep["table"] = tab
    head = [f"# 财报后跌了，基本面没坏就能买吗？ {rep['generated']}", "",
            f"98 只大公司 {len(rep['rows'])} 次财报（2021-10 到 2026-10），{rep['with_revenue']} 次能从 SEC 财报数据算出营收增速"
            f"（算不出的公司：{'、'.join(rep['no_revenue']) or '无'}，多是银行和营收口径特殊的）。"
            "「基本面没坏」= 这季营收同比还在增长、增速比上一季放缓不到 5 个百分点；「增速明显放缓」= 放缓 5 个点以上；"
            "「营收同比下降」= 比去年同季少。指引不在 SEC 结构化数据里，所以这里的「没坏」只看刚报的这一季，指引好坏已经体现在反应日的涨跌里。"
            "之后的走势从反应日收盘算，减去 SPY 同期；稳健性检查按「反应日收盘买」每 $1 算，正股成本买卖各 0.1%。"
            "幸存者偏差：都是今天的大公司，事后看是赢家。只研究，不改交易。", ""]
    save("fundamentals", rep, "\n".join(head) + "\n" + md)


if __name__ == "__main__":
    sys.exit(main())
