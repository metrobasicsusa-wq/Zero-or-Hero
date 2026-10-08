"""Research: after the chip stocks drop hard, what does the next day (and week) usually do?

Daily closes (SIP, adjusted) since 2016 for SMH, SOXX and the large chip names, up to yesterday. For each:
every day; a day down 2.5% or more; the second straight down day with the last one down 2.5% or more
(2026-10-08's shape for SMH: -1.6% then -3.0%); a day down 5% or more. What followed: the next day's
chance of another down close and its average, the next 5 days' average, and how often the next day
fell another 2%. Pooled across the names as well. Record only. Run: python -m hero.research_chipnext
"""

from __future__ import annotations

import json
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

from hero.research_streak import daily

NAMES = ("SMH", "SOXX", "NVDA", "AMD", "AVGO", "MU", "TSM")
GROUPS = ("全部日子", "当天跌 2.5% 以上", "连跌两天、第二天跌 2.5% 以上", "当天跌 5% 以上")


def events(c: list[float], days: list[str]) -> list[dict]:
    out = []
    for i in range(2, len(c) - 5):
        r0, r1 = c[i] / c[i - 1] - 1, c[i - 1] / c[i - 2] - 1
        out.append({"day": days[i], "r": r0, "prev": r1, "next": c[i + 1] / c[i] - 1, "five": c[i + 5] / c[i] - 1})
    return out


def pick(rows: list[dict], g: str) -> list[dict]:
    if g == "当天跌 2.5% 以上":
        return [x for x in rows if x["r"] <= -0.025]
    if g == "连跌两天、第二天跌 2.5% 以上":
        return [x for x in rows if x["r"] <= -0.025 and x["prev"] < 0]
    if g == "当天跌 5% 以上":
        return [x for x in rows if x["r"] <= -0.05]
    return rows


def summary(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0}
    nx = [x["next"] for x in rows]
    return {"n": n, "down": round(sum(v < 0 for v in nx) / n, 3), "next": round(statistics.mean(nx), 4),
            "next_med": round(statistics.median(nx), 4), "drop2": round(sum(v <= -0.02 for v in nx) / n, 3),
            "five": round(statistics.mean(x["five"] for x in rows), 4),
            "five_up": round(sum(x["five"] > 0 for x in rows) / n, 3)}


def run(client) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    per, pooled = {}, {g: [] for g in GROUPS}
    for s in NAMES:
        bs = [b for b in daily(client, s, f"{end}T23:00:00Z") if b["t"][:10] <= end]
        rows = events([float(b["c"]) for b in bs], [b["t"][:10] for b in bs])
        per[s] = {}
        for g in GROUPS:
            sub = pick(rows, g)
            per[s][g] = summary(sub)
            pooled[g] += sub
        per[s]["since2024"] = summary([x for x in pick(rows, "当天跌 2.5% 以上") if x["day"] >= "2024-01-01"])
    return {"generated": date.today().isoformat(), "end": end, "per": per, "pooled": {g: summary(v) for g, v in pooled.items()}}


def markdown(rep: dict) -> str:
    pct = lambda x: f"{x:+.2%}"
    row = lambda name, s: (f"| {name} | {s['n']} | {s['down']:.0%} | {pct(s['next'])}（{pct(s['next_med'])}） | {s['drop2']:.0%} | "
                           f"{pct(s['five'])} | {s['five_up']:.0%} |") if s.get("n") else f"| {name} | 0 | — | — | — | — | — |"
    head = ["| 情况 | 次数 | 第二天收跌 | 第二天平均（中位数） | 第二天再跌 2% 以上 | 5 天平均 | 5 天后收涨 |", "|---|---|---|---|---|---|---|"]
    out = [f"# 芯片股大跌后，第二天会继续跌吗 {rep['generated']}", "",
           f"2016 至 {rep['end']}，日线收盘（SIP，已复权）：{'、'.join(NAMES)}。只研究，不改交易。", "",
           "## 合在一起", ""] + head + [row(g, s) for g, s in rep["pooled"].items()]
    for s, x in rep["per"].items():
        out += ["", f"## {s}", ""] + head + [row(g, x[g]) for g in GROUPS] + [row("当天跌 2.5% 以上（2024 年以来）", x["since2024"])]
    return "\n".join(out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca())
    (root / "research" / f"{rep['generated']}-chipnext.md").write_text(markdown(rep))
    print(markdown(rep))


if __name__ == "__main__":
    sys.exit(main())
