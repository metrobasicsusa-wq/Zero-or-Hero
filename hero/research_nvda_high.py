"""Research: when NVDA and SPY close at new highs together, does the market turn bad afterwards?

The owner's read of 2026: each time NVDA made a new high with SPY at a new high too, things went bad
after. Here, on daily closes since 2016 (SIP, split- and dividend-adjusted, up to yesterday):
  event:    NVDA closes at a new all-time high (of the data since 2016) and SPY closes at a new high
            within the same 3 sessions; the first such day after 10 sessions without one;
  after it: SPY / QQQ / NVDA change over 5 / 10 / 20 sessions, SPY's deepest close below the event
            close within 20 sessions, and how often that reached 3% / 5%;
against every day, and against SPY new highs without an NVDA one; every 2026 event is listed with
what followed; and where NVDA and SPY stand now against their highs. Record only.
Run: python -m hero.research_nvda_high
"""

from __future__ import annotations

import json
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

from hero.research_streak import daily

NAMES = ("SPY", "QQQ", "NVDA")
WARMUP = 250       # a "new high" needs a year of history behind it
TOGETHER = 3       # SPY's high within this many sessions of NVDA's
GAP = 10           # a new event needs this many sessions without one
HORIZONS = (5, 10, 20)
DRAWS = (0.03, 0.05)


def new_highs(closes: list[float]) -> list[bool]:
    out, best = [], float("-inf")
    for i, c in enumerate(closes):
        out.append(i >= WARMUP and c > best)
        best = max(best, c)
    return out


def events(nv: list[bool], sp: list[bool]) -> list[int]:
    """Days where NVDA is at a new high and SPY made one within TOGETHER sessions, de-clustered."""
    out: list[int] = []
    for i in range(len(nv)):
        both = nv[i] and any(sp[j] for j in range(max(0, i - TOGETHER + 1), i + 1))
        if both and (not out or i - out[-1] > GAP):
            out.append(i)
    return out


def after(c: dict[str, list[float]], i: int) -> dict | None:
    if i + max(HORIZONS) >= len(c["SPY"]):
        return None
    out = {f"{s}_{h}": c[s][i + h] / c[s][i] - 1 for s in NAMES for h in HORIZONS}
    low = min(c["SPY"][i + 1:i + 21])
    out["spy_dd"] = low / c["SPY"][i] - 1
    return out


def summary(xs: list[dict]) -> dict:
    n = len(xs)
    if not n:
        return {"n": 0}
    out = {"n": n}
    for k in (k for k in xs[0] if k != "day"):
        vals = [x[k] for x in xs]
        out[k] = round(statistics.mean(vals), 4)
        if k.endswith("_20") or k == "spy_dd":
            out[k + "_down"] = round(sum(v < 0 for v in vals) / n, 3)
    for d in DRAWS:
        out[f"dd{d}"] = round(sum(x["spy_dd"] <= -d for x in xs) / n, 3)
    return out


def run(client) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    bars = {s: {b["t"][:10]: float(b["c"]) for b in daily(client, s, f"{end}T23:00:00Z") if b["t"][:10] <= end} for s in NAMES}
    days = sorted(set.intersection(*(set(v) for v in bars.values())))
    c = {s: [bars[s][d] for d in days] for s in NAMES}
    nv, sp = new_highs(c["NVDA"]), new_highs(c["SPY"])
    ev = events(nv, sp)
    rows = [{"day": days[i], **a} for i in ev if (a := after(c, i))]
    base = [a for i in range(WARMUP, len(days)) if (a := after(c, i))]
    sp_only = [a for i in range(WARMUP, len(days)) if sp[i] and not any(nv[max(0, i - TOGETHER + 1):i + 1]) and (a := after(c, i))]
    recent = [{"day": days[i], "pending": after(c, i) is None, **{s: c[s][i] for s in NAMES},
               **{f"{s}_now": c[s][-1] / c[s][i] - 1 for s in NAMES}} for i in ev if days[i] >= "2026-01-01"]
    now = {s: {"close": c[s][-1], "high": max(c[s]), "off_high": round(c[s][-1] / max(c[s]) - 1, 4),
               "high_day": days[c[s].index(max(c[s]))]} for s in NAMES}
    return {"generated": date.today().isoformat(), "start": days[0], "end": days[-1], "events": rows,
            "summary": {"事件（NVDA 和 SPY 一起新高）": summary(rows),
                        "事件，2024 年以来": summary([r for r in rows if r["day"] >= "2024-01-01"]),
                        "所有日子": summary(base), "SPY 新高但 NVDA 没有": summary(sp_only)},
            "events_2026": recent, "now": now}


def markdown(rep: dict) -> str:
    pct = lambda x: f"{x:+.1%}"
    out = [f"# 英伟达和 SPY 一起创新高之后 {rep['generated']}", "",
           f"{rep['start']} 至 {rep['end']}，日线收盘（SIP，已按拆股和分红调整）。事件：NVDA 收盘创新高（2016 年以来的数据里），"
           f"同时 SPY 在 {TOGETHER} 个交易日内也收盘创新高；之后 {GAP} 个交易日内的重复不算。"
           "看之后 5 / 10 / 20 个交易日的涨跌，以及 20 天内 SPY 收盘最深跌了多少。和所有日子、和「SPY 新高但 NVDA 没有」比。只研究，不改交易。", "",
           "## 汇总", "",
           "| 情况 | 次数 | SPY 5 天 | SPY 10 天 | SPY 20 天 | 20 天后 SPY 收跌 | QQQ 20 天 | NVDA 20 天 | 20 天内 SPY 最深 | 跌过 3% | 跌过 5% |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for g, s in rep["summary"].items():
        if not s["n"]:
            continue
        out.append(f"| {g} | {s['n']} | {pct(s['SPY_5'])} | {pct(s['SPY_10'])} | {pct(s['SPY_20'])} | {s['SPY_20_down']:.0%} | "
                   f"{pct(s['QQQ_20'])} | {pct(s['NVDA_20'])} | {pct(s['spy_dd'])} | {s['dd0.03']:.0%} | {s['dd0.05']:.0%} |")
    out += ["", "## 2026 年的每一次", "", "| 日期 | NVDA | SPY | 之后 SPY 5 / 10 / 20 天 | 20 天内 SPY 最深 | NVDA 20 天 | 到现在 SPY / NVDA |",
            "|---|---|---|---|---|---|---|"]
    by_day = {r["day"]: r for r in rep["events"]}
    for e in rep["events_2026"]:
        r = by_day.get(e["day"])
        later = f"{pct(r['SPY_5'])} / {pct(r['SPY_10'])} / {pct(r['SPY_20'])}" if r else "（不满 20 天）"
        dd = pct(r["spy_dd"]) if r else "—"
        nv20 = pct(r["NVDA_20"]) if r else "—"
        out.append(f"| {e['day']} | ${e['NVDA']:.2f} | ${e['SPY']:.2f} | {later} | {dd} | {nv20} | {pct(e['SPY_now'])} / {pct(e['NVDA_now'])} |")
    if not rep["events_2026"]:
        out.append("| （2026 年没有） |  |  |  |  |  |  |")
    out += ["", "## 现在离高点多远（截至 " + rep["end"] + "）", "", "| 标的 | 收盘 | 最高收盘 | 哪天 | 离高点 |", "|---|---|---|---|---|"]
    for s, x in rep["now"].items():
        out.append(f"| {s} | ${x['close']:.2f} | ${x['high']:.2f} | {x['high_day']} | {pct(x['off_high'])} |")
    return "\n".join(out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca())
    (root / "research" / f"{rep['generated']}-nvda-high.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    (root / "research" / f"{rep['generated']}-nvda-high.md").write_text(markdown(rep))
    print(markdown(rep))


if __name__ == "__main__":
    sys.exit(main())
