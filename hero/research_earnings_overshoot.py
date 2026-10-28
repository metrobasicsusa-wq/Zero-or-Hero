"""After an earnings report: when does owning both sides pay, and what does a stock do after it moves more than its
options priced? (owner, 10-28, after GOOGL -9%, NOW -18% and FTNT -24% in one night: 「所以同时买还是会赚的」「明天
开盘会继续跌吗」; then 「自己选个大研究去做」.)

Events: research/2026-10-02-earnings-reactions.json (98 large caps, every report 2021-10..2026-10; 1022 of them with
the at-the-money straddle priced on the close before the reaction).

Part 1, the straddle. Bought at that close, valued on the reaction day at its intrinsic value: per $1, |gap| / implied - 1
(sold at the open) or |move| / implied - 1 (sold at the close). When the expiry is after the reaction day (784 of the
1022) some time value is left, so these are floors; the bias is the same for every group, so groups compare fairly.
Grouped by how cheap the straddle was against the stock's own past reactions (cheapness = implied / prior average
move), the implied move itself, 126-day momentum, timing, sector group and year.

Part 2, after the move. From the reaction day's close, the stock's return 1, 5, 10, 20 and 60 sessions later minus
SPY's over the same sessions (Yahoo, split-adjusted). Grouped by direction, by size against the implied move and in
absolute terms, and by whether the reaction day closed beyond its open (held) or gave some back. hero.robust on the
20-session excess return of the bigger groups, traded with the move and against it.

Survivorship: these are today's large caps. Record only. Run: python -m hero.research_earnings_overshoot
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import date
from pathlib import Path

from hero import robust
from hero.research_earnings_rules import GROUP

ROOT = Path(__file__).resolve().parent.parent
EVENTS = ROOT / "research" / "2026-10-02-earnings-reactions.json"
HORIZONS = (1, 5, 10, 20, 60)
STOCK_COST = 0.001   # robust's extra cost per side for a stock trade (its default 5% is for options)
TECH = ("芯片", "AI 硬件 / 算力", "软件")
RATIO_EDGES = ((0.0, 1.0, "没超过定价"), (1.0, 1.5, "定价的 1–1.5 倍"), (1.5, 2.0, "定价的 1.5–2 倍"), (2.0, 99.0, "定价的 2 倍以上"))


def straddle_rows(events: list[dict]) -> list[dict]:
    """One row per report with an implied move: the straddle's floor return at the open and at the close."""
    out = []
    for e in events:
        imp = e.get("implied_move")
        if not imp:
            continue
        out.append({"day": e["reaction_day"], "symbol": e["symbol"], "implied": imp, "cheapness": e.get("cheapness"),
                    "mom126": e.get("mom126"), "timing": e.get("timing"), "group": GROUP.get(e["symbol"], "其他"),
                    "same_day": e.get("expiry") == e["reaction_day"],
                    "open": abs(e["gap"]) / imp - 1, "close": abs(e["move"]) / imp - 1})
    return out


def forward(events: list[dict], bars: dict[str, dict[str, dict]], horizons: tuple[int, ...] = HORIZONS) -> list[dict]:
    """Each report's reaction day and the stock's return from that close h sessions on, raw and minus SPY."""
    spy = bars.get("SPY") or {}
    spy_days = sorted(spy)
    spy_idx = {d: i for i, d in enumerate(spy_days)}
    out = []
    for e in events:
        b = bars.get(e["symbol"]) or {}
        days = sorted(b)
        idx = {d: i for i, d in enumerate(days)}
        i, j = idx.get(e["reaction_day"]), spy_idx.get(e["reaction_day"])
        if i is None or j is None:
            continue
        c0, o0, s0 = b[days[i]]["c"], b[days[i]]["o"], spy[spy_days[j]]["c"]
        sign = 1 if e["move"] > 0 else -1
        imp = e.get("implied_move")
        row = {"day": e["reaction_day"], "symbol": e["symbol"], "move": e["move"], "gap": e["gap"], "implied": imp,
               "ratio": abs(e["move"]) / imp if imp else None, "sign": sign, "group": GROUP.get(e["symbol"], "其他"),
               "held": (c0 / o0 - 1) * sign >= 0, "fwd": {}, "exc": {}}
        for h in horizons:
            if i + h < len(days) and j + h < len(spy_days):
                r = b[days[i + h]]["c"] / c0 - 1
                row["fwd"][h] = r
                row["exc"][h] = r - (spy[spy_days[j + h]]["c"] / s0 - 1)
        out.append(row)
    return out


def ratio_label(r: float | None) -> str | None:
    if r is None:
        return None
    for lo, hi, name in RATIO_EDGES:
        if lo <= r < hi:
            return name
    return None


def stats(xs: list[float]) -> dict | None:
    if not xs:
        return None
    return {"n": len(xs), "mean": statistics.mean(xs), "median": statistics.median(xs), "win": sum(x > 0 for x in xs) / len(xs)}


def _p(x: float | None, digits: int = 1) -> str:
    return "—" if x is None else f"{x * 100:+.{digits}f}%"


def straddle_table(rows: list[dict]) -> tuple[dict, list[str]]:
    groups: dict[str, list[dict]] = {"全部": rows}
    cheap = sorted(r["cheapness"] for r in rows if r["cheapness"] is not None)
    if cheap:
        t1, t2 = cheap[len(cheap) // 3], cheap[2 * len(cheap) // 3]
        groups[f"便宜（定价 < 自己过去平均的 {t1:.2f} 倍）"] = [r for r in rows if r["cheapness"] is not None and r["cheapness"] < t1]
        groups[f"中间（{t1:.2f}–{t2:.2f} 倍）"] = [r for r in rows if r["cheapness"] is not None and t1 <= r["cheapness"] < t2]
        groups[f"贵（≥ {t2:.2f} 倍）"] = [r for r in rows if r["cheapness"] is not None and r["cheapness"] >= t2]
    groups["定价不到 5%"] = [r for r in rows if r["implied"] < 0.05]
    groups["定价 5%–10%"] = [r for r in rows if 0.05 <= r["implied"] < 0.10]
    groups["定价 10% 以上"] = [r for r in rows if r["implied"] >= 0.10]
    groups["126 日涨 30% 以上进财报"] = [r for r in rows if (r["mom126"] or 0) >= 0.30]
    groups["126 日跌进财报"] = [r for r in rows if r["mom126"] is not None and r["mom126"] < 0]
    groups["盘前财报"] = [r for r in rows if r["timing"] and "before" in r["timing"]]
    groups["盘后财报"] = [r for r in rows if r["timing"] and "after" in r["timing"]]
    groups["当天到期（估值最准）"] = [r for r in rows if r["same_day"]]
    groups["科技三组（芯片、AI 硬件、软件）"] = [r for r in rows if r["group"] in TECH]
    groups["科技三组 + 便宜（< 1.05 倍）"] = [r for r in rows if r["group"] in TECH and r["cheapness"] is not None and r["cheapness"] < 1.05]
    groups["科技三组 + 当天到期"] = [r for r in rows if r["group"] in TECH and r["same_day"]]
    groups["非科技 + 贵（≥ 1.34 倍）"] = [r for r in rows if r["group"] not in TECH and r["cheapness"] is not None and r["cheapness"] >= 1.34]
    for g in sorted({r["group"] for r in rows}):
        groups[f"板块：{g}"] = [r for r in rows if r["group"] == g]
    for y in sorted({r["day"][:4] for r in rows}):
        groups[f"{y} 年"] = [r for r in rows if r["day"][:4] == y]
    table, md = {}, ["| 情况 | 次数 | 开盘卖：平均 | 中位数 | 赚的比例 | 收盘卖：平均 | 中位数 | 赚的比例 |", "|---|---|---|---|---|---|---|---|"]
    for name, rs in groups.items():
        if len(rs) < 10:
            continue
        o, c = stats([r["open"] for r in rs]), stats([r["close"] for r in rs])
        table[name] = {"open": o, "close": c}
        md.append(f"| {name} | {o['n']} | {_p(o['mean'], 0)} | {_p(o['median'], 0)} | {o['win']:.0%} | {_p(c['mean'], 0)} | "
                  f"{_p(c['median'], 0)} | {c['win']:.0%} |")
    return table, md


def drift_groups(fw: list[dict]) -> dict[str, list[dict]]:
    g: dict[str, list[dict]] = {}
    for d, name in ((1, "涨"), (-1, "跌")):
        side = [r for r in fw if r["sign"] == d]
        g[f"财报后{name}（全部）"] = side
        for lo, hi, lab in RATIO_EDGES:
            g[f"{name}，{lab}"] = [r for r in side if r["ratio"] is not None and lo <= r["ratio"] < hi]
        g[f"{name} 10% 以上"] = [r for r in side if abs(r["move"]) >= 0.10]
        g[f"{name} 10% 以上，当天收在开盘{'之上' if d > 0 else '之下'}（守住）"] = [r for r in side if abs(r["move"]) >= 0.10 and r["held"]]
        g[f"{name} 10% 以上，当天收回一部分"] = [r for r in side if abs(r["move"]) >= 0.10 and not r["held"]]
        g[f"{name} 超过定价 1.5 倍，软件"] = [r for r in side if (r["ratio"] or 0) >= 1.5 and r["group"] == "软件"]
        g[f"{name} 超过定价 1.5 倍，芯片 / AI 硬件"] = [r for r in side if (r["ratio"] or 0) >= 1.5 and r["group"] in ("芯片", "AI 硬件 / 算力")]
    return g


def drift_table(fw: list[dict], end: str) -> tuple[dict, list[str]]:
    groups = drift_groups(fw)
    table: dict = {}
    md = ["| 情况 | 次数 | 第 1 天 | 第 5 天 | 第 10 天 | 第 20 天 | 第 60 天 | 第 20 天顺着原方向的比例 |", "|---|---|---|---|---|---|---|---|"]
    for name, rs in groups.items():
        if len(rs) < 8:
            continue
        cells, row = [], {}
        for h in HORIZONS:
            xs = [r["exc"][h] for r in rs if h in r["exc"]]
            s = stats(xs)
            row[h] = s
            cells.append("—" if not s else f"{_p(s['mean'])}（中位 {_p(s['median'])}）")
        same = [r["exc"][20] * r["sign"] > 0 for r in rs if 20 in r["exc"]]
        table[name] = row
        md.append(f"| {name} | {len(rs)} | " + " | ".join(cells) + f" | {sum(same) / len(same):.0%} |" if same else "")
    rob = {}
    for name, h in (("跌 10% 以上", 20), ("跌，定价的 1.5–2 倍", 20), ("跌，定价的 2 倍以上", 20), ("跌 超过定价 1.5 倍，软件", 20),
                    ("跌 超过定价 1.5 倍，芯片 / AI 硬件", 20), ("涨 10% 以上", 20), ("涨，定价的 2 倍以上", 20),
                    ("涨 超过定价 1.5 倍，芯片 / AI 硬件", 20), ("涨 超过定价 1.5 倍，芯片 / AI 硬件", 60)):
        rs = [r for r in groups.get(name, []) if h in r["exc"]]
        if len(rs) >= 8:
            rob[f"{name} · 顺着方向拿 {h} 天"] = robust.summary([(r["day"], r["exc"][h] * r["sign"]) for r in rs], end,
                                                         extra_cost=STOCK_COST)
            rob[f"{name} · 反着方向拿 {h} 天"] = robust.summary([(r["day"], -r["exc"][h] * r["sign"]) for r in rs], end,
                                                         extra_cost=STOCK_COST)
    return {"groups": table, "robust": rob}, md


def report(events: list[dict], bars: dict[str, dict[str, dict]]) -> tuple[dict, str]:
    rows = straddle_rows(events)
    fw = forward(events, bars)
    end = max(e["reaction_day"] for e in events)
    st, st_md = straddle_table(rows)
    dr, dr_md = drift_table(fw, end)
    tech_cheap = [r for r in rows if r["group"] in TECH and r["cheapness"] is not None and r["cheapness"] < 1.05]
    rob_st = {"两边买 · 开盘卖（全部）": robust.summary([(r["day"], r["open"]) for r in rows], end),
              "两边买 · 收盘卖（全部）": robust.summary([(r["day"], r["close"]) for r in rows], end),
              "两边买 · 收盘卖（科技三组 + 便宜）": robust.summary([(r["day"], r["close"]) for r in tech_cheap], end),
              "两边买 · 收盘卖（软件）": robust.summary([(r["day"], r["close"]) for r in rows if r["group"] == "软件"], end)}
    md = [f"# 财报之后：什么时候两边买能赚，跌超定价之后怎么走 {date.today().isoformat()}", "",
          f"{EVENTS.name} 的 {len({e['symbol'] for e in events})} 只大公司、{len(events)} 次财报（{min(e['reaction_day'] for e in events)} 到 {end}），"
          f"其中 {len(rows)} 次有财报前一天收盘的平值跨式定价。之后的走势用 Yahoo 日线（拆股调整），从反应日收盘算，减去 SPY 同期。"
          "幸存者偏差：都是今天的大公司。只研究，不改交易。", "",
          "## 1. 两边同时买（平值跨式，财报前一天收盘买）", "",
          "按内在价值估：开盘卖 = |开盘跳空| ÷ 定价 − 1，收盘卖 = |当天涨跌| ÷ 定价 − 1。到期日在反应日之后的（大多数）还剩时间价值，"
          "所以这是下限；各组偏差一样，可以互相比。", ""] + st_md + ["", robust.table(rob_st, "稳健性检查：两边买"), "",
          "## 2. 财报大动之后的 1–60 天（减去 SPY 同期，每格是平均和中位数）", ""] + dr_md + ["",
          robust.table(dr["robust"], "稳健性检查：财报大动后拿 20 / 60 天（减去 SPY；正股成本按买卖各多付 0.1%）")]
    return {"straddle": st, "straddle_robust": rob_st, "drift": dr, "n_events": len(events), "n_straddle": len(rows),
            "end": end}, "\n".join(md) + "\n"


def main() -> None:
    from hero.research_earnings_control import yahoo
    from hero.research_ideas import save
    events = json.loads(EVENTS.read_text())["events"]
    bars = {}
    for s in sorted({e["symbol"] for e in events}) + ["SPY"]:
        try:
            bars[s] = yahoo(s, "2021-06-01")
        except Exception as ex:
            print("skip", s, ex)
        time.sleep(0.2)
    rep, md = report(events, bars)
    save("earnings-overshoot", rep, md)


if __name__ == "__main__":
    sys.exit(main())
