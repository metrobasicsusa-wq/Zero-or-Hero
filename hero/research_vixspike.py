"""Idea 4 (2026-10-13): after a fear spike, buy the rebound.

research_zdte_streak found SPY calls the day after a 1%+ drop the one cell that paid (+10%, in sample). The more
extreme, rarer version: VIXY (the VIX futures ETF) up 8%+ and SPY down 1.5%+ on the same day. The next session at
10:00: SPY / QQQ same-day calls 0.6% out (out on minute closes at 2x / 3x / 5x, a trail, else 15:30), and next
week's calls 2% out held up to three sessions (daily option bars). Against every other day after a 1.5% SPY drop.
Record only. Run: python -m hero.research_vixspike
"""

from __future__ import annotations

import sys
import time
from datetime import date, timedelta

from hero.research_chase import bars_for
from hero.research_ideas import contract, dailies, entry_cost, intraday, minutes, multiday, report, save

START = "2024-02-01"
VIX_UP = 0.08
SPY_DOWN = -0.015
NAMES = ("SPY", "QQQ")
EXITS = {"take2": "当天 2 倍卖", "take3": "当天 3 倍卖", "take5": "当天 5 倍卖", "trail2": "当天翻倍后回撤卖", "hold": "当天 15:30 卖",
         "w_next_close": "周期权：第二天收盘卖", "w_close_d3": "周期权：三天后收盘卖", "w_trail2": "周期权：翻倍后回撤卖"}


def run(client) -> dict:
    end = (date.today() - timedelta(days=3)).isoformat()
    bars = bars_for(client, ["SPY", "QQQ", "VIXY"], f"{end}T23:00:00Z", "raw")
    spy, vix = bars["SPY"], {b["t"][:10]: float(b["c"]) for b in bars.get("VIXY", [])}
    days = [b["t"][:10] for b in spy]
    opens = {s: {b["t"][:10]: float(b["o"]) for b in bars[s]} for s in NAMES}
    rows = []
    for i in range(1, len(spy) - 1):
        d, prev, nxt = days[i], days[i - 1], days[i + 1]
        if d < START:
            continue
        r = float(spy[i]["c"]) / float(spy[i - 1]["c"]) - 1
        if r > SPY_DOWN or d not in vix or prev not in vix:
            continue
        spike = vix[d] / vix[prev] - 1 >= VIX_UP
        for und in NAMES:
            spot = opens[und].get(nxt)
            if not spot:
                continue
            res = {}
            got = contract(client, und, "C", nxt, spot, 0.006, 0, 0)
            if got:
                res.update(intraday(minutes(client, got[0], nxt), "10:00", "15:30") or {})
            got = contract(client, und, "C", nxt, spot, 0.02, 5, 12)
            if got:
                ec = entry_cost(minutes(client, got[0], nxt), "10:00")
                w = multiday(ec[1], dailies(client, got[0], nxt, got[1])) if ec else None
                res.update({f"w_{k}": v for k, v in (w or {}).items()})
            time.sleep(0.03)
            if res:
                rows.append({"und": und, "day": nxt, "spy_drop": round(r, 4), "spike": spike, "res": res})
    return {"generated": date.today().isoformat(), "start": START, "end": end, "rows": rows}


def main() -> None:
    from hero.alpaca import Alpaca
    rep = run(Alpaca())
    r = rep["rows"]
    groups = {}
    for und in NAMES:
        groups[f"{und}：恐慌日（VIXY 涨 8% 以上且 SPY 跌 1.5% 以上）第二天"] = [x for x in r if x["und"] == und and x["spike"]]
        groups[f"{und}：对照，SPY 跌 1.5% 以上但 VIXY 没大涨"] = [x for x in r if x["und"] == und and not x["spike"]]
    table, md = report(f"恐慌反弹：VIXY 大涨、SPY 大跌后第二天买看涨 {rep['generated']}",
                       f"{rep['start']} 至 {rep['end']}：SPY 当天跌 1.5% 以上的日子，分成 VIXY 同时涨 8% 以上（恐慌）和没有；第二天 10:00 "
                       "买当天到期、离现价 0.6% 的看涨（按分钟收盘价卖，最晚 15:30），以及下周到期、离现价 2% 的看涨拿最多三天；"
                       "买入多付 10%、卖出少拿 10%。",
                       groups, EXITS)
    rep["table"] = table
    save("vixspike", rep, md)


if __name__ == "__main__":
    sys.exit(main())
