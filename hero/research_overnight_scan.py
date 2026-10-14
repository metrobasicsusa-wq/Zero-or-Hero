"""What the overnight sleeve would pick right now (owner, 2026-10-14: scan today before its 15:50 run).

Runs hero.sleeve_rules.Overnight on live snapshots as if it were 15:50, and lists every pool name up 2%+ on the
day with its range position and 50-day average, so the near misses show too. Nothing is ordered.
Written to research/overnight-scan/YYYY-MM-DD-HHMM.md. Run: python -m hero.research_overnight_scan
"""

from __future__ import annotations

import sys
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from hero import sleeve_rules as sr

ET = ZoneInfo("America/New_York")


def main() -> None:
    from hero.alpaca import Alpaca
    now = datetime.now(ET)
    errors = []
    run = SimpleNamespace(c=Alpaca(), today=now.date(), hhmm="15:50", specs=[], cache={},
                          j=SimpleNamespace(event=lambda *a, **k: errors.append(k)))
    p = sr.pool(run)
    cl = sr.closes(run, p, 120)
    rows = []
    for x, v in sr.snaps(run, p).items():
        spot, _, low, prev = sr.spot_open(v)
        hi = float((v.get("dailyBar") or {}).get("h") or 0)
        xs = cl.get(x) or []
        if not spot or not prev or hi <= low or len(xs) < 50:
            continue
        r, pos, ma = spot / prev - 1, (spot - low) / (hi - low), sum(xs[-50:]) / 50
        if r >= 0.02:
            ok = r >= 0.03 and pos >= 0.85 and spot > ma
            rows.append((r, x, spot, pos, ma, ok))
    got = sr.Overnight().entries(run, {}, {"slots": {}, "open": [], "cash": 500.0})
    picks = (got or (None, []))[1]
    lines = [f"# 隔夜动量预扫 {now:%Y-%m-%d %H:%M} 美东", "",
             "按 15:50 的规则用此刻行情跑一遍（不下单）：当天涨 3% 以上、收在当天区间最高 15%、在 50 日均线上方，选涨最多的一只。"
             "收盘前价格还会变，15:50 的正式选股可能不同。", ""]
    if picks:
        pk = picks[0]
        lines += [f"**此刻会选：{pk['und']}**，{pk['sym']}，卖价 ${pk['ask']:.2f}（一张 ${pk['ask'] * 100:,.0f}）", "", pk["why"], ""]
    else:
        lines += ["**此刻没有符合条件的（或合约买不起）。**", ""]
    lines += ["股票池里今天涨 2% 以上的：", "", "| 股票 | 涨幅 | 现价 | 在当天区间位置 | 50 日均线 | 符合 |", "|---|---|---|---|---|---|"]
    for r, x, spot, pos, ma, ok in sorted(rows, reverse=True):
        lines.append(f"| {x} | {r:+.1%} | ${spot:,.2f} | {pos:.0%} | ${ma:,.2f} | {'✔' if ok else '—'} |")
    if not rows:
        lines.append("| （没有） | | | | | |")
    if errors:
        lines += ["", f"出错：{errors[:3]}"]
    out = sr.ROOT / "research" / "overnight-scan"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{now:%Y-%m-%d-%H%M}.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
