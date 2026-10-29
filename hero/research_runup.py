"""Run into earnings: does a stock that rallied before its report sell the news? (owner, 10-29: 「会利好出尽吗」「谁在财报前
有利好并拉上去了」「研究下」 -- tonight's run-ups were mostly sectors: oil, drug makers, utilities, staples.)

Two samples.
  1. research/2026-10-02-earnings-reactions.json: 98 large caps, every report 2021-10..2026-10 (1,986). Yahoo daily
     bars (split-adjusted) for the stock, its sector ETF (chips SMH, software IGV, megacap internet QQQ, banks XLF,
     health XLV, industrials XLI, energy XLE, ...) and SPY. Before the report = the last session before the reaction
     day. The run = that close against 20 sessions earlier; the sector's run over the same sessions; near the high =
     within 2% of the highest close of the last 60 sessions.
  2. Our live forecast files since 10-19 (every US report with options, scored): the run against SPY's (no sector
     map), the open against the last trade before the report (gap_pre). Out of sample for anything found in 1.
Outcomes: did it open up, the open and the reaction-day close against the pre-report close, the move against the
options' implied move where priced, and the 20 sessions after the reaction day minus SPY.
"Sector run" groups: the stock ran 10%+ and its sector 5%+ (pushed by its sector) against the stock ran 10%+ while
its sector ran under 3% (its own run). The trade behind "sell the news" is shorting at the pre-report close and
covering at the open: per $1 = -gap, robust with a 0.1% stock cost. Record only. Run: python -m hero.research_runup
"""

from __future__ import annotations

import glob
import json
import statistics
import sys
from datetime import date
from pathlib import Path

from hero import robust

ROOT = Path(__file__).resolve().parent.parent
EVENTS = ROOT / "research" / "2026-10-02-earnings-reactions.json"
STOCK_COST = 0.001
RUN, NEAR, LOOK = 20, 0.02, 60
SECTOR = {}
for etf, names in {
    "SMH": "ADI ALAB AMAT AMD AVGO COHR CRDO INTC KLAC LITE LRCX MRVL MU NVDA QCOM SNDK STX TXN WDC ANET APH CSCO DELL "
           "HPE GLW VRT SMCI CRWV IREN",
    "IGV": "ADBE APP CRM CRWD DDOG INTU NET NOW ORCL PANW PLTR SHOP SNOW IBM ACN",
    "QQQ": "MSFT GOOGL META AMZN NFLX UBER BKNG WBD AAPL",
    "XLP": "COST KO PG WMT", "XLY": "HD NKE TSLA",
    "XLF": "BAC C GS JPM MA MS V WFC COIN HOOD SOFI MSTR",
    "XLV": "ABBV ABT BSX DHR ISRG JNJ LLY MDT MRNA PFE TMO UNH",
    "XLI": "BA CAT GE GEV BE RKLB IONQ", "XLB": "FCX", "XLE": "CVX XOM VLO", "XLU": "NEE", "XLC": "T VZ",
}.items():
    for s in names.split():
        SECTOR[s] = etf


def pre_index(days: list[str], react: str) -> int | None:
    """Index of the last session before the reaction day."""
    lo = [i for i, d in enumerate(days) if d < react]
    return lo[-1] if lo else None


def run_of(closes: dict[str, float], days: list[str], i: int, n: int = RUN) -> float | None:
    return closes[days[i]] / closes[days[i - n]] - 1 if i >= n else None


def features(e: dict, bars: dict[str, dict], sec: dict[str, dict] | None, spy: dict[str, dict]) -> dict | None:
    """One report's run in, its sector's and SPY's over the same sessions, and what followed."""
    days = sorted(bars)
    i = pre_index(days, e["reaction_day"])
    if i is None or i < LOOK or e["reaction_day"] not in bars:
        return None
    c = {d: bars[d]["c"] for d in days}
    pre, d0 = c[days[i]], days[i]
    j = days.index(e["reaction_day"])
    row = {"symbol": e["symbol"], "day": e["reaction_day"], "run": run_of(c, days, i),
           "near_high": pre >= (1 - NEAR) * max(c[d] for d in days[i - LOOK + 1:i + 1]),
           "gap": bars[e["reaction_day"]]["o"] / pre - 1, "move": c[e["reaction_day"]] / pre - 1,
           "implied": e.get("implied_move")}
    for name, src in (("sector", sec), ("spy", spy)):
        row[name] = None
        if src:
            sd = sorted(src)
            if d0 in src:
                k = sd.index(d0)
                row[name] = src[sd[k]]["c"] / src[sd[k - RUN]]["c"] - 1 if k >= RUN else None
    row["after"] = None
    if j + 20 < len(days) and spy and e["reaction_day"] in spy:
        sd = sorted(spy)
        k = sd.index(e["reaction_day"])
        if k + 20 < len(sd):
            row["after"] = (c[days[j + 20]] / c[days[j]] - 1) - (spy[sd[k + 20]]["c"] / spy[sd[k]]["c"] - 1)
    return row


def groups(rows: list[dict]) -> dict[str, list[dict]]:
    ok = [r for r in rows if r["run"] is not None]
    g = {"全部": ok,
         "财报前 20 天涨 10% 以上": [r for r in ok if r["run"] >= 0.10],
         "　涨 10% 以上，而且在 60 日高点附近（2% 内）": [r for r in ok if r["run"] >= 0.10 and r["near_high"]],
         "　板块推上去的（板块同期也涨 5% 以上）": [r for r in ok if r["run"] >= 0.10 and r.get("sector") is not None and r["sector"] >= 0.05],
         "　个股自己涨的（板块同期不到 3%）": [r for r in ok if r["run"] >= 0.10 and r.get("sector") is not None and r["sector"] < 0.03],
         "　比 SPY 多涨 10% 以上": [r for r in ok if r.get("spy") is not None and r["run"] - r["spy"] >= 0.10],
         "财报前 20 天涨 5%–10%": [r for r in ok if 0.05 <= r["run"] < 0.10],
         "财报前 20 天 ±5% 以内": [r for r in ok if -0.05 < r["run"] < 0.05],
         "财报前 20 天跌 10% 以上": [r for r in ok if r["run"] <= -0.10],
         "　比 SPY 少涨 10% 以上": [r for r in ok if r.get("spy") is not None and r["run"] - r["spy"] <= -0.10]}
    return g


def _m(xs):
    xs = [x for x in xs if x is not None]
    return statistics.mean(xs) if xs else None


def table(rows: list[dict], end: str, title: str) -> tuple[dict, str]:
    g = groups(rows)
    f = lambda v, d=1: "—" if v is None else f"{v * 100:+.{d}f}%"
    md = [f"### {title}", "", "| 情况 | 次数 | 开盘涨的比例 | 开盘：平均 | 中位数 | 反应日收盘：平均 | 涨跌 ÷ 期权定价 | 之后 20 天减 SPY |",
          "|---|---|---|---|---|---|---|---|"]
    tab, rob = {}, {}
    for name, rs in g.items():
        if len(rs) < 5:
            md.append(f"| {name} | {len(rs)} | — | — | — | — | — | — |")
            continue
        ratio = [abs(r["move"]) / r["implied"] for r in rs if r.get("implied")]
        row = {"n": len(rs), "up": sum(r["gap"] > 0 for r in rs) / len(rs), "gap": _m(r["gap"] for r in rs),
               "gap_med": statistics.median(r["gap"] for r in rs), "move": _m(r["move"] for r in rs),
               "ratio": statistics.median(ratio) if ratio else None, "n_ratio": len(ratio), "after": _m(r["after"] for r in rs)}
        tab[name] = row
        md.append(f"| {name} | {row['n']} | {row['up']:.0%} | {f(row['gap'])} | {f(row['gap_med'])} | {f(row['move'])} | "
                  + (f"{row['ratio']:.2f}（{row['n_ratio']}）" if row["ratio"] is not None else "—") + f" | {f(row['after'])} |")
    for name in ("财报前 20 天涨 10% 以上", "　板块推上去的（板块同期也涨 5% 以上）", "　个股自己涨的（板块同期不到 3%）",
                 "　涨 10% 以上，而且在 60 日高点附近（2% 内）"):
        rs = g.get(name) or []
        if len(rs) >= 5:
            rob[f"{name.strip()} · 财报前做空、开盘回补"] = robust.summary([(r["day"], -r["gap"]) for r in rs], end, extra_cost=STOCK_COST)
    rs = g.get("财报前 20 天跌 10% 以上") or []
    if len(rs) >= 5:
        rob["财报前 20 天跌 10% 以上 · 财报前买、开盘卖"] = robust.summary([(r["day"], r["gap"]) for r in rs], end, extra_cost=STOCK_COST)
    return {"groups": tab, "robust": rob}, "\n".join(md) + "\n\n" + robust.table(rob, f"稳健性检查（{title}）") + "\n"


def live_rows(root: Path, bars_for) -> list[dict]:
    """Scored reports in our forecast files: the run before against SPY's, the open against the last pre-report trade."""
    out, spy = [], bars_for("SPY")
    for f in sorted(glob.glob(str(root / "research" / "earnings-forecast" / "20??-??-??.json"))):
        for x in json.loads(Path(f).read_text()).get("reports", []):
            res = x.get("result")
            if not res:
                continue
            b = bars_for(x["symbol"])
            if not b:
                continue
            row = features({"symbol": x["symbol"], "reaction_day": x["reaction_day"], "implied_move": x.get("implied_move")}, b, None, spy)
            if not row:
                continue
            row["gap"] = res.get("gap_pre", res.get("gap"))
            row["move"] = res.get("move", row["move"])
            out.append(row)
    return out


def main() -> None:
    from concurrent.futures import ThreadPoolExecutor
    from hero.research_earnings_control import yahoo
    from hero.research_ideas import save
    events = json.loads(EVENTS.read_text())["events"]
    syms = sorted({e["symbol"] for e in events} | set(SECTOR.values()) | {"SPY"})
    cache: dict[str, dict] = {}

    def load(s, start="2021-01-01"):
        try:
            return s, yahoo(s, start)
        except Exception:
            return s, {}
    with ThreadPoolExecutor(12) as ex:
        cache.update(dict(ex.map(load, syms)))
    rows = [r for e in events if (r := features(e, cache.get(e["symbol"]) or {}, cache.get(SECTOR.get(e["symbol"], "")), cache["SPY"]))]
    end = max(r["day"] for r in rows)
    t1, md1 = table(rows, end, f"98 只大公司，{min(r['day'] for r in rows)} 到 {end}")
    live_syms = sorted({x["symbol"] for f in glob.glob(str(ROOT / "research" / "earnings-forecast" / "20??-??-??.json"))
                        for x in json.loads(Path(f).read_text()).get("reports", []) if x.get("result")} - set(cache))
    with ThreadPoolExecutor(16) as ex:
        cache.update(dict(ex.map(lambda s: load(s, "2026-05-01"), live_syms)))
    live = live_rows(ROOT, lambda s: cache.get(s) or {})
    t2, md2 = table(live, max((r["day"] for r in live), default=end), "我们 10-19 起记分的全部美股财报（新样本，没有板块对照）")
    head = [f"# 财报前涨上去的，会利好出尽吗？ {date.today().isoformat()}", "",
            "用户 10-29：「会利好出尽吗」「谁在财报前有利好并拉上去了」——今晚财报前涨得多的大多是板块：石油、药、公用事业、日用品。"
            "「财报前 20 天」= 财报前最后一个交易日收盘对 20 个交易日前；「板块推上去的」= 同期板块 ETF 也涨 5% 以上（芯片 SMH、软件 IGV、"
            "大型互联网 QQQ、银行 XLF、医疗 XLV、工业 XLI、能源 XLE 等）；「个股自己涨的」= 板块同期不到 3%。开盘和收盘都对财报前收盘"
            "（新样本的开盘对财报前最后一笔）。「涨跌 ÷ 期权定价」是反应日收盘涨跌幅的绝对值除以财报前的平值跨式定价（中位数），"
            "小于 1 说明比市场预期动得小。稳健性按「财报前做空、开盘回补」每 $1 算，成本买卖各 0.1%。"
            "幸存者偏差：98 只都是今天的大公司。只研究，不改交易。", ""]
    rep = {"generated": date.today().isoformat(), "big": t1, "live": t2, "n_big": len(rows), "n_live": len(live)}
    save("runup", rep, "\n".join(head) + "\n" + md1 + "\n" + md2)


if __name__ == "__main__":
    sys.exit(main())
