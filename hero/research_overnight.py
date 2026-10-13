"""Idea 2 (2026-10-13): overnight momentum -- buy at the close what closed strong, sell the next day.

Lou, Polk & Skouras (JFE 2019): momentum profits are earned overnight. Here the 100 stocks of data/universe.json,
every session since 2024-02: up 3%+ on the day and closed in the top 15% of its range (calls), or down 3%+ and closed
in the bottom 15% (puts); at most the two biggest moves each way a day. At 15:50 buy next week's option (5 to 12
days out), 3% beyond the close; out on daily option bars: the next open, the next close, the third close, a 2x take
on a daily close (else the third close), or a trail on daily closes. Also only the names above their 50-day average
(calls) / below it (puts). Record only. Run: python -m hero.research_overnight
"""

from __future__ import annotations

import json
import sys
import time
from datetime import date, timedelta

from hero.research_chase import bars_for
from hero.research_ideas import ROOT, contract, dailies, entry_cost, minutes, multiday, report, save

START = "2024-02-01"
MOVE = 0.03
EDGE = 0.15
PER_DAY = 2
OFF = 0.03
EXITS = {"next_open": "第二天开盘卖", "next_close": "第二天收盘卖", "close_d3": "第三天收盘卖", "take2": "收盘翻倍就卖，否则第三天",
         "trail2": "翻倍后回撤 40% 卖"}


def signals(bars: dict[str, list[dict]]) -> list[dict]:
    out = []
    for s, bs in bars.items():
        bs = [b for b in bs if b["t"][:10] >= "2023-10-01"]
        for i in range(50, len(bs)):
            b, prev = bs[i], bs[i - 1]
            hi, lo, c = float(b["h"]), float(b["l"]), float(b["c"])
            if hi <= lo or b["t"][:10] < START:
                continue
            r, pos = c / float(prev["c"]) - 1, (c - lo) / (hi - lo)
            ma50 = sum(float(x["c"]) for x in bs[i - 50:i]) / 50
            if r >= MOVE and pos >= 1 - EDGE:
                out.append({"und": s, "day": b["t"][:10], "kind": "C", "ret": round(r, 4), "close": c, "trend": c > ma50})
            elif r <= -MOVE and pos <= EDGE:
                out.append({"und": s, "day": b["t"][:10], "kind": "P", "ret": round(r, 4), "close": c, "trend": c < ma50})
    keep, by = [], {}
    for x in out:
        by.setdefault((x["day"], x["kind"]), []).append(x)
    for xs in by.values():
        keep += sorted(xs, key=lambda x: -abs(x["ret"]))[:PER_DAY]
    return sorted(keep, key=lambda x: x["day"])


def run(client) -> dict:
    end = (date.today() - timedelta(days=3)).isoformat()
    syms = json.loads((ROOT / "data" / "universe.json").read_text()).get("stocks") or []
    rows = []
    for e in signals(bars_for(client, syms, f"{end}T23:00:00Z", "raw")):
        got = contract(client, e["und"], e["kind"], e["day"], e["close"], OFF, 5, 12)
        if not got:
            continue
        sym, exp = got
        ec = entry_cost(minutes(client, sym, e["day"]), "15:50")
        if not ec:
            continue
        res = multiday(ec[1], dailies(client, sym, e["day"], exp))
        time.sleep(0.03)
        if res:
            rows.append({**e, "sym": sym, "res": res})
    return {"generated": date.today().isoformat(), "start": START, "end": end, "rows": rows}


def main() -> None:
    from hero.alpaca import Alpaca
    rep = run(Alpaca())
    r = rep["rows"]
    groups = {"大涨收在高位，买看涨": [x for x in r if x["kind"] == "C"],
              "　且在 50 日均线上方": [x for x in r if x["kind"] == "C" and x["trend"]],
              "　当天涨 6% 以上": [x for x in r if x["kind"] == "C" and x["ret"] >= 0.06],
              "大跌收在低位，买看跌": [x for x in r if x["kind"] == "P"],
              "　且在 50 日均线下方": [x for x in r if x["kind"] == "P" and x["trend"]],
              "　当天跌 6% 以上": [x for x in r if x["kind"] == "P" and x["ret"] <= -0.06]}
    table, md = report(f"隔夜动量：收盘买强势股，第二天卖 {rep['generated']}",
                       f"{rep['start']} 至 {rep['end']}，data/universe.json 的 100 只股票：当天涨 3% 以上且收在当天区间最高 15%（买看涨），"
                       "或跌 3% 以上且收在最低 15%（买看跌），每天每个方向最多 2 只；15:50 买下周到期（5–12 天）、离收盘价 3% 的期权，"
                       "按期权日线卖出；买入多付 10%、卖出少拿 10%。",
                       groups, EXITS)
    rep["table"] = table
    save("overnight", rep, md)


if __name__ == "__main__":
    sys.exit(main())
