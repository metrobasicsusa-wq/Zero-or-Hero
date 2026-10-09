"""Research: is "SPY fell 1%+ yesterday, buy today's same-day call" (spydip, plan-only) real or a lucky cell?

research_zdte_streak (2026-10-08) found +10% a trade and a 3x take hit 33% of the time on the 67 days
after a 1%+ drop, against -35% / 19% on all days: one cell of many, so possibly luck. Its saved per-day
rows (SPY and QQQ, 10:00 entry, 0.6% / 0.3% out, 3x / 5x take, 10% costs both ways) are reused here
with no new data:
  the drop threshold moved around 1% (a real effect should not live at one exact number);
  split by year (2024 / 2025 / 2026) and by half of the sample;
  split by whether the market was already bouncing at 10:00 (above the open) or still falling;
  the same rule on QQQ;
  and a placebo: the same calls after an up day of the same size.
Record only. Run: python -m hero.research_spydip_robust
"""

from __future__ import annotations

import json
import statistics
import sys
from datetime import date
from pathlib import Path

THRESHOLDS = (-0.005, -0.0075, -0.01, -0.0125, -0.015, -0.02)
CELLS = (("0.006", 3), ("0.006", 5), ("0.003", 3))


def cell(xs: list[float], take: int) -> dict | None:
    if not xs:
        return None
    return {"n": len(xs), "mean": round(statistics.mean(xs), 3), "median": round(statistics.median(xs), 3),
            "hit": round(sum(x >= take - 1 for x in xs) / len(xs), 3)}


def calls(rows: list[dict], off: str, take: int) -> list[float]:
    return [r["res"][f"C|{off}|{take}"] for r in rows if f"C|{off}|{take}" in r["res"]]


def groups(rows: list[dict]) -> dict[str, list[dict]]:
    """Each test on one name's rows: thresholds, years, halves, 10:00 direction, placebo."""
    days = sorted(r["day"] for r in rows)
    mid = days[len(days) // 2] if days else ""
    dip = [r for r in rows if r["prev_ret"] <= -0.01]
    out = {"全部日子": rows}
    out |= {f"昨天跌 {-t:.2%} 以上": [r for r in rows if r["prev_ret"] <= t] for t in THRESHOLDS}
    out |= {"昨天跌 0.5%–1%": [r for r in rows if -0.01 < r["prev_ret"] <= -0.005]}
    out |= {f"跌 1% 以上，{y} 年": [r for r in dip if r["day"].startswith(y)] for y in ("2024", "2025", "2026")}
    out |= {"跌 1% 以上，前一半日子": [r for r in dip if r["day"] < mid], "跌 1% 以上，后一半日子": [r for r in dip if r["day"] >= mid]}
    out |= {"跌 1% 以上，10:00 已高于开盘": [r for r in dip if r["side"] == "C"],
            "跌 1% 以上，10:00 还低于开盘": [r for r in dip if r["side"] == "P"]}
    out |= {"对照：昨天涨 1% 以上": [r for r in rows if r["prev_ret"] >= 0.01]}
    return out


def run(root: Path) -> dict:
    src = sorted((root / "research").glob("*-zdte-streak.json"))[-1]
    rep = json.loads(src.read_text())
    out = {}
    for und in ("SPY", "QQQ"):
        rows = [r for r in rep["rows"] if r["symbol"] == und]
        for g, rs in groups(rows).items():
            for off, take in CELLS:
                out[f"{und}|{g}|{off}|{take}"] = cell(calls(rs, off, take), take)
    return {"generated": date.today().isoformat(), "source": src.name, "start": rep["start"], "end": rep["end"], "table": out}


def markdown(rep: dict) -> str:
    t = rep["table"]
    head = "| 情况 | " + " | ".join(f"离现价 {float(o):.1%}，{k} 倍止盈" for o, k in CELLS) + " |"
    out = [f"# SPY 跌 1% 后第二天买末日看涨：是真的还是运气 {rep['generated']}", "",
           f"不取新数据，用 {rep['source']} 里每天的结果（{rep['start']} 至 {rep['end']}，10:00 买当天到期看涨，"
           "止盈或 15:30 卖，买入多付 10%、卖出少拿 10%）。把跌幅门槛前后挪、按年份和前后两半拆开、按 10:00 时是否已经反弹拆开，"
           "再看 QQQ 和「昨天涨 1%」的对照。每格「平均每 $1 / 中位数 / 碰到止盈比例（天数）」。只研究，不改交易。", ""]
    for und in ("SPY", "QQQ"):
        out += [f"## {und}", "", head, "|---|" + "---|" * len(CELLS)]
        names = list(dict.fromkeys(k.split("|")[1] for k in t if k.startswith(und + "|")))
        for g in names:
            cs = [t.get(f"{und}|{g}|{o}|{k}") for o, k in CELLS]
            out.append(f"| {g} | " + " | ".join(f"{c['mean']:+.0%} / {c['median']:+.0%} / {c['hit']:.0%}（{c['n']}）" if c else "—"
                                                 for c in cs) + " |")
        out.append("")
    return "\n".join(out)


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    rep = run(root)
    (root / "research" / f"{rep['generated']}-spydip-robust.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    (root / "research" / f"{rep['generated']}-spydip-robust.md").write_text(markdown(rep))
    print(markdown(rep))


if __name__ == "__main__":
    sys.exit(main())
