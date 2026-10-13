"""Idea 6 (2026-10-13): bitcoin moves over the weekend; MSTR / COIN / MARA only trade again on Monday.

Bitcoin trades all weekend, the stocks tied to it do not. From Friday 16:00 ET to Monday 09:00 ET (Alpaca's BTC/USD
hourly bars), every weekend since 2024-02 (and long weekends: the first session after a market holiday). On the
first session after: MSTR, COIN, MARA options the way bitcoin went, the nearest expiry (within a week), 2% out,
bought at 09:35 (also 10:00); out on minute closes at 2x / 3x / 5x, a trail, else 15:30; or held to the next close.
Grouped by the size of the weekend move (all / 3%+ / 5%+) and whether the stock's opening gap already took the
same direction. Record only. Run: python -m hero.research_btcweekend
"""

from __future__ import annotations

import sys
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from hero.research_0dte import at_or_before
from hero.research_0dte_names import minutes_by_day
from hero.research_ideas import contract, dailies, entry_cost, intraday, minutes, multiday, report, save

ET = ZoneInfo("America/New_York")
START = "2024-02-01"
NAMES = ("MSTR", "COIN", "MARA")
ENTRIES = ("09:35", "10:00")
OFF = 0.02
EXITS = {"take2": "2 倍卖", "take3": "3 倍卖", "take5": "5 倍卖", "trail2": "翻倍后回撤 40% 卖", "hold": "拿到 15:30",
         "next_close": "拿到下一个交易日收盘"}


def btc_hourly(client, start: str, end: str) -> dict[datetime, float]:
    """{UTC hour: close} for BTC/USD."""
    out, params = {}, {"symbols": "BTC/USD", "timeframe": "1Hour", "start": start, "end": end, "limit": 10000}
    while True:
        page = client._d("/v1beta3/crypto/us/bars", params) or {}
        for b in (page.get("bars") or {}).get("BTC/USD", []):
            out[datetime.fromisoformat(b["t"].replace("Z", "+00:00"))] = float(b["c"])
        if not page.get("next_page_token"):
            return out
        params["page_token"] = page["next_page_token"]


def at_or_before_t(px: dict[datetime, float], t: datetime) -> float | None:
    for k in range(0, 6):
        v = px.get(t - timedelta(hours=k))
        if v:
            return v
    return None


def run(client) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    btc = btc_hourly(client, f"{START}T00:00:00Z", f"{end}T23:59:59Z")
    days = {u: minutes_by_day(client, u, START, end) for u in NAMES}
    sessions = sorted(days["MSTR"])
    rows = []
    for i in range(1, len(sessions)):
        prev, day = sessions[i - 1], sessions[i]
        if (date.fromisoformat(day) - date.fromisoformat(prev)).days < 3:
            continue  # only the first session after a weekend or holiday break
        t0 = datetime.fromisoformat(prev).replace(hour=16, tzinfo=ET).astimezone(timezone.utc)
        t1 = datetime.fromisoformat(day).replace(hour=9, tzinfo=ET).astimezone(timezone.utc)
        a, b = at_or_before_t(btc, t0), at_or_before_t(btc, t1)
        if not a or not b:
            continue
        wk = b / a - 1
        kind = "C" if wk >= 0 else "P"
        for und in NAMES:
            m, pm = days[und].get(day), days[und].get(prev)
            last = at_or_before(pm or {}, "15:59")
            if not m or "09:30" not in m or not last:
                continue
            gap = float(m["09:30"]["o"]) / float(last["c"]) - 1
            for at in ENTRIES:
                s = at_or_before(m, at)
                if not s:
                    continue
                got = contract(client, und, kind, day, float(s["c"]), OFF, 0, 7)
                if not got:
                    continue
                mins = minutes(client, got[0], day)
                res = intraday(mins, at, "15:30") or {}
                ec = entry_cost(mins, at)
                if ec and got[1] > day:
                    nx = multiday(ec[1], dailies(client, got[0], day, got[1]), 1)
                    if nx:
                        res["next_close"] = nx["next_close"]
                time.sleep(0.03)
                if res:
                    rows.append({"und": und, "day": day, "at": at, "btc": round(wk, 4), "gap": round(gap, 4),
                                 "gap_same": (gap >= 0) == (wk >= 0), "res": res})
    return {"generated": date.today().isoformat(), "start": START, "end": end, "rows": rows}


def main() -> None:
    from hero.alpaca import Alpaca
    rep = run(Alpaca())
    r = rep["rows"]
    groups = {}
    for at in ENTRIES:
        base = [x for x in r if x["at"] == at]
        groups[f"{at} 买：所有周末"] = base
        groups[f"　{at}：比特币周末动 3% 以上"] = [x for x in base if abs(x["btc"]) >= 0.03]
        groups[f"　{at}：比特币周末动 5% 以上"] = [x for x in base if abs(x["btc"]) >= 0.05]
        groups[f"　{at}：动 3% 以上，但股票开盘跳空方向相反或没跳"] = [x for x in base if abs(x["btc"]) >= 0.03 and not x["gap_same"]]
        for und in NAMES:
            groups[f"　{at}：{und}，比特币动 3% 以上"] = [x for x in base if x["und"] == und and abs(x["btc"]) >= 0.03]
    table, md = report(f"比特币周末跳动，周一买 MSTR / COIN / MARA {rep['generated']}",
                       f"{rep['start']} 至 {rep['end']}：比特币周五 16:00 到周一 09:00（美东）的涨跌，周末或长假后第一个交易日顺着比特币方向买 "
                       "MSTR、COIN、MARA 最近到期（一周内）、离现价 2% 的期权，09:35（另测 10:00）买，按分钟收盘价卖出，最晚 15:30，"
                       "或拿到下一个交易日收盘；买入多付 10%、卖出少拿 10%。",
                       groups, EXITS)
    rep["table"] = table
    save("btcweekend", rep, md)


if __name__ == "__main__":
    sys.exit(main())
