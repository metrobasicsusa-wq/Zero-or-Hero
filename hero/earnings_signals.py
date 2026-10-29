"""After-the-report signals with evidence behind them, from a scored forecast file (owner, 10-29: 「那怎么改进」).

Calling the direction before a report has been a coin flip (research/2026-10-20-earnings-rules.md: no simple rule
beat 52% over 1,880 reports; our own calls 49% over 314), and both sides of the options lose (buying the straddle
-20% per $1 in docs/earnings-after-2026-10-28.md, selling an iron fly -11% to -29% in research/2026-10-06-ironfly.md).
What has had an edge uses the market's reaction, which only exists after the report:
  - beat: a chip / AI-hardware stock that closed the reaction day up at least 1.5x its implied move (20 sessions
    after: +9% vs SPY on average, median +3%, 70% of 30; research/2026-10-28-earnings-overshoot.md);
  - held: opened up 10% or more and closed at or above the open (the PEAD rule our s500 ledger already trades:
    research/2026-10-13-pead.md).
Record only. Run: python -m hero.earnings_signals [YYYY-MM-DD]  (default: the newest scored forecast file)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from hero.research_earnings_rules import GROUP

ROOT = Path(__file__).resolve().parent.parent / "research" / "earnings-forecast"
HARD = ("芯片", "AI 硬件 / 算力")
BEAT = 1.5
HELD_GAP = 0.10


def signals(rep: dict) -> list[dict]:
    """One row per scored report that meets a signal: symbol, group, gap, move, implied, ratio and which signals."""
    out = []
    for r in rep.get("reports", []):
        res = r.get("result")
        if not res or res.get("move") is None:
            continue
        gap, move, imp = res.get("gap"), res["move"], r.get("implied_move")
        group = GROUP.get(r["symbol"], "其他")
        ratio = move / imp if imp else None
        tags = []
        if group in HARD and ratio is not None and ratio >= BEAT:
            tags.append("beat")
        if gap is not None and gap >= HELD_GAP and (1 + move) >= (1 + gap):
            tags.append("held")
        if tags:
            out.append({"symbol": r["symbol"], "reaction_day": r["reaction_day"], "group": group, "gap": gap,
                        "move": move, "implied": imp, "weekly": r.get("weekly"),
                        "ratio": round(ratio, 2) if ratio is not None else None, "signals": tags})
    return sorted(out, key=lambda x: -x["move"])


def markdown(rows: list[dict]) -> str:
    if not rows:
        return "没有符合条件的（芯片 / AI 硬件涨超定价 1.5 倍、高开 10% 以上且守住）。\n"
    name = {"beat": "芯片 / AI 硬件涨超定价 1.5 倍", "held": "高开 10% 以上且守住"}
    md = ["| 股票 | 反应日 | 开盘 | 收盘 | 定价 | 倍数 | 信号 |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        imp = f"{r['implied']:.1%}" + ("" if r["weekly"] else "（月）") if r["implied"] else "—"
        ratio = f"{r['ratio']:.1f}×" if r["ratio"] is not None else "—"
        gap = f"{r['gap']:+.1%}" if r["gap"] is not None else "—"
        md.append(f"| {r['symbol']} | {r['reaction_day']} | {gap} | {r['move']:+.1%} | {imp} | {ratio} | "
                  f"{'、'.join(name[t] for t in r['signals'])} |")
    return "\n".join(md) + "\n"


def latest_scored(root: Path = ROOT) -> Path | None:
    for f in sorted(root.glob("20??-??-??.json"), reverse=True):
        if any(r.get("result") for r in json.loads(f.read_text()).get("reports", [])):
            return f
    return None


def main(argv: list[str]) -> int:
    f = ROOT / f"{argv[0]}.json" if argv else latest_scored()
    if not f or not f.exists():
        print("没有已记分的财报预测文件")
        return 1
    print(f"# 财报后的信号（{f.stem} 那批，只记录）\n")
    print(markdown(signals(json.loads(f.read_text()))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
