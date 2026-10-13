"""Idea 3 (2026-10-13): post-earnings drift -- buy the day after a report that gapped and held.

Post-earnings announcement drift is one of the steadiest anomalies: big surprises keep moving for days. Unlike the
relay (a call bought before the report), this buys after the market has confirmed it. Events from
research/*-earnings-reactions.json since 2024-02: a reaction-day gap of 5%+ that held into the close (closed at or
above its open) -> calls; a 5%+ gap down that held (closed at or below its open) -> puts. On the next session at
10:00 buy next week's option (5 to 12 days out), 3% beyond that session's open; out on daily option bars: the next
open / close, the third close, a 2x take on a close, or a trail. Record only. Run: python -m hero.research_pead
"""

from __future__ import annotations

import glob
import json
import sys
import time
from datetime import date, timedelta

from hero.research_chase import bars_for
from hero.research_ideas import ROOT, contract, dailies, entry_cost, minutes, multiday, report, save

START = "2024-02-01"
GAP = 0.05
OFF = 0.03
EXITS = {"next_open": "第三天开盘卖", "next_close": "第三天收盘卖", "close_d3": "持有三个交易日后收盘卖",
         "take2": "收盘翻倍就卖，否则三天后", "trail2": "翻倍后回撤 40% 卖"}


def events() -> list[dict]:
    rep = json.loads(open(sorted(glob.glob(str(ROOT / "research" / "*-earnings-reactions.json")))[-1]).read())
    out = []
    for e in rep["events"]:
        g, m = e.get("gap"), e.get("move")
        if g is None or m is None or e["reaction_day"] < START:
            continue
        held = (1 + m) / (1 + g) - 1  # reaction day close vs open
        if g >= GAP and held >= 0:
            out.append({"und": e["symbol"], "react": e["reaction_day"], "kind": "C", "gap": g, "held": round(held, 4)})
        elif g <= -GAP and held <= 0:
            out.append({"und": e["symbol"], "react": e["reaction_day"], "kind": "P", "gap": g, "held": round(held, 4)})
    return out


def run(client) -> dict:
    end = (date.today() - timedelta(days=3)).isoformat()
    evs = events()
    bars = bars_for(client, sorted({e["und"] for e in evs}), f"{end}T23:00:00Z", "raw")
    rows = []
    for e in evs:
        bs = bars.get(e["und"], [])
        nxt = next((b for b in bs if b["t"][:10] > e["react"]), None)
        if not nxt or nxt["t"][:10] > end:
            continue
        day, spot = nxt["t"][:10], float(nxt["o"])
        got = contract(client, e["und"], e["kind"], day, spot, OFF, 5, 12)
        if not got:
            continue
        sym, exp = got
        ec = entry_cost(minutes(client, sym, day), "10:00")
        if not ec:
            continue
        res = multiday(ec[1], dailies(client, sym, day, exp))
        time.sleep(0.03)
        if res:
            rows.append({**e, "day": day, "sym": sym, "res": res})
    return {"generated": date.today().isoformat(), "start": START, "end": end, "rows": rows}


def main() -> None:
    from hero.alpaca import Alpaca
    rep = run(Alpaca())
    r = rep["rows"]
    groups = {"财报高开 5% 以上且守住，第二天买看涨": [x for x in r if x["kind"] == "C"],
              "　高开 10% 以上": [x for x in r if x["kind"] == "C" and x["gap"] >= 0.10],
              "　当天收盘比开盘再涨 3% 以上": [x for x in r if x["kind"] == "C" and x["held"] >= 0.03],
              "财报低开 5% 以上且没反弹，第二天买看跌": [x for x in r if x["kind"] == "P"],
              "　低开 10% 以上": [x for x in r if x["kind"] == "P" and x["gap"] <= -0.10],
              "　当天收盘比开盘再跌 3% 以上": [x for x in r if x["kind"] == "P" and x["held"] <= -0.03]}
    table, md = report(f"财报后漂移：确认后第二天追 {rep['generated']}",
                       f"{rep['start']} 至 {rep['end']}，research/*-earnings-reactions.json 的财报事件：反应日跳空 5% 以上且收盘守住（高开收在开盘之上 / "
                       "低开收在开盘之下），下一个交易日 10:00 买下周到期（5–12 天）、离开盘价 3% 的期权，按期权日线卖出"
                       "（「第三天」= 反应日之后第二个交易日）；买入多付 10%、卖出少拿 10%。",
                       groups, EXITS)
    rep["table"] = table
    save("pead", rep, md)


if __name__ == "__main__":
    sys.exit(main())
