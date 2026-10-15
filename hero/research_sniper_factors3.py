"""The sniper's judgement, part 3: do the few factors that held overnight in part 2 stack, and do they hold out of
sample? (owner, 10-15: keep going.)

Part 2 (research/2026-10-15-sniper-factors2.md): held to the next close, an open beyond the 20-session high / low
(+9% per $1, 447 events) and a gap of 3+ times the stock's average daily swing (+2%, 247) were the only groups not
losing; heavy pre-market volume helped. Here, on the same events and tags (hero.research_sniper_factors2.tag):
  the combinations (breakout and big gap, with heavy pre-market volume, either one, ...), and
  each split in two: 2024 (where a rule would have been found) and 2025-26 (whether it still held).
"Held to the next close" exists only when the contract did not expire that day, so its count is given apart.
The tagged rows are saved to research/sniper/factor-rows.json for later checks offline.
Record only. Run: python -m hero.research_sniper_factors3
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import date

from hero.research_0dte import et_minutes
from hero.research_ideas import ROOT, report, save
from hero.research_open_catalyst import utc
from hero.research_sniper_factors import EXITS, latest
from hero.research_sniper_factors2 import daily_stats, tag

SPLIT = "2025-01-01"


def tagged_rows(c) -> tuple[dict, list[dict]]:
    oc = latest("open-catalyst")
    rows = oc["rows"]
    syms = sorted({r["und"] for r in rows})
    daily = c.daily_bars(syms + ["SPY"], "2023-06-01", adjustment="all", feed="sip")
    stats = {s: daily_stats(daily.get(s, [])) for s in syms}
    spy = daily.get("SPY", [])
    spy_vol = {spy[i]["t"][:10]: statistics.pstdev([float(spy[k]["c"]) / float(spy[k - 1]["c"]) - 1 for k in range(i - 20, i)])
               for i in range(21, len(spy))}
    by_day: dict[str, list[dict]] = {}
    for r in rows:
        by_day.setdefault(r["day"], []).append(r)
    out = []
    for day in sorted(by_day):
        evs = by_day[day]
        names = sorted({r["und"] for r in evs})
        try:
            mins = {s: et_minutes(b) for s, b in c.stock_bars(names, f"{day}T08:00:00Z", f"{day}T15:00:00Z", feed="sip").items()}
        except Exception:
            mins = {}
        heads: dict[str, list[str]] = {}
        prev = next((stats[n][day]["prev_day"] for n in names if day in stats.get(n, {})), None)
        if prev:
            try:
                for item in c.news_range(names, utc(prev, "16:00"), utc(day, "09:30")):
                    for s in item.get("symbols") or []:
                        heads.setdefault(s, []).append(item.get("headline", ""))
            except Exception:
                pass
        for r in evs:
            t = tag(r, stats.get(r["und"], {}).get(day), mins.get(r["und"], {}), heads.get(r["und"], []), spy_vol.get(day))
            if t:
                out.append(t)
        time.sleep(0.05)
    return oc, out


def groups(rows: list[dict]) -> dict[str, list[dict]]:
    hi_pre = sorted(r["rvol_pre"] for r in rows if r.get("rvol_pre") is not None)[2 * len(rows) // 3]
    big = lambda r: r["gap_z"] >= 3
    brk = lambda r: r["breakout"]
    pre = lambda r: (r.get("rvol_pre") or 0) >= hi_pre
    calm = lambda r: r.get("spy_vol") is not None and r["spy_vol"] < 0.0072
    sets = {"全部": lambda r: True,
            "A 开盘突破 20 日高 / 低点": brk,
            "B 跳空 >= 3 倍平时波动": big,
            "C 盘前量大（前三分之一）": pre,
            "A 且 B": lambda r: brk(r) and big(r),
            "A 或 B": lambda r: brk(r) or big(r),
            "A 且 C": lambda r: brk(r) and pre(r),
            "（A 或 B）且 C": lambda r: (brk(r) or big(r)) and pre(r),
            "A 且 B 且 C": lambda r: brk(r) and big(r) and pre(r),
            "A 且大盘平静": lambda r: brk(r) and calm(r),
            "A 且顺跳空": lambda r: brk(r) and r["with_gap"],
            "A 且有新闻": lambda r: brk(r) and r["news"] > 0}
    g = {}
    for name, f in sets.items():
        g[name] = [r for r in rows if f(r)]
        g[f"　{name}：2024"] = [r for r in rows if f(r) and r["day"] < SPLIT]
        g[f"　{name}：2025–26"] = [r for r in rows if f(r) and r["day"] >= SPLIT]
    return g


def main() -> None:
    from hero.alpaca import Alpaca
    oc, rows = tagged_rows(Alpaca())
    out = ROOT / "research" / "sniper"
    out.mkdir(parents=True, exist_ok=True)
    (out / "factor-rows.json").write_text(json.dumps(rows, ensure_ascii=False) + "\n")
    gs = groups(rows)
    table, md = report(f"开盘狙击的判断因素回测（三）：叠加和分段 {date.today().isoformat()}",
                       f"{oc['start']} 至 {oc['end']}，同样的 {len(rows)} 次开盘跳空 2% 以上（25 只活跃股；09:35 顺着前 5 分钟买最近到期、"
                       "离现价 0.6% 的期权，买入多付 10%、卖出少拿 10%）。看第二份里唯一不亏的几组能不能叠加，以及分成 2024 和 2025–26 两段是否都成立。"
                       "「拿到下一个交易日收盘」只有合约不在当天到期时才有，笔数另列在下面。",
                       gs, EXITS)
    md += "\n「拿到下一个交易日收盘」的笔数：\n\n| 情况 | 笔数 |\n|---|---|\n" + "\n".join(
        f"| {k} | {sum('next' in r['res'] for r in v)} |" for k, v in gs.items()) + "\n"
    save("sniper-factors3", {"generated": date.today().isoformat(), "rows": len(rows), "table": table}, md)


if __name__ == "__main__":
    sys.exit(main())
