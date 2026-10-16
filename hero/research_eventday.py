"""Idea 5 (2026-10-13): data days -- buy both sides of SPY / QQQ on CPI, jobs and Fed days.

Every day, both sides lose (research_zdte_streak: -31% / 1% at 3x). Data days were never split out. Dates below are
the release days as scheduled (CPI and payrolls at 08:30, before the open; the Fed at 14:00); 2025's government
shutdown moved some releases, so a few may be off by a day -- treat the result as rough. 2026-10-16: checked against
claude-c's release calendar (exchange/claude-c/2026-10-16.macro-surprises.json): January 2026's CPI came out 02-13, not
02-11, and its payrolls 02-11, not 02-06; the late-2025 payrolls (11-20, 12-16) and 2026-07..10 releases were added.
Results written before then used the old dates. On those days: at 10:00
(and 13:55 on Fed days) a call and a put on the day's expiry, each 0.6% out, half the stake each; each leg out on
minute closes at 3x / 5x / a trail, else 15:30. Against every other day with the same rule. Record only.
Run: python -m hero.research_eventday
"""

from __future__ import annotations

import sys
import time
from datetime import date, timedelta

from hero.research_0dte import at_or_before, et_minutes
from hero.research_0dte_names import minutes_by_day, pick
from hero.research_ideas import intraday, report, save

START = "2024-02-01"
NAMES = ("SPY", "QQQ")
CPI = ["2024-02-13", "2024-03-12", "2024-04-10", "2024-05-15", "2024-06-12", "2024-07-11", "2024-08-14", "2024-09-11",
       "2024-10-10", "2024-11-13", "2024-12-11", "2025-01-15", "2025-02-12", "2025-03-12", "2025-04-10", "2025-05-13",
       "2025-06-11", "2025-07-15", "2025-08-12", "2025-09-11", "2025-10-24", "2025-12-18", "2026-01-13", "2026-02-13",
       "2026-03-11", "2026-04-10", "2026-05-12", "2026-06-10", "2026-07-14", "2026-08-12", "2026-09-11", "2026-10-14"]
JOBS = ["2024-02-02", "2024-03-08", "2024-04-05", "2024-05-03", "2024-06-07", "2024-07-05", "2024-08-02", "2024-09-06",
        "2024-10-04", "2024-11-01", "2024-12-06", "2025-01-10", "2025-02-07", "2025-03-07", "2025-04-04", "2025-05-02",
        "2025-06-06", "2025-07-03", "2025-08-01", "2025-09-05", "2025-11-20", "2025-12-16", "2026-01-09", "2026-02-11",
        "2026-03-06", "2026-04-03", "2026-05-08", "2026-06-05", "2026-07-02", "2026-08-07", "2026-09-04", "2026-10-02"]
FED = ["2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18", "2025-01-29",
       "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10", "2026-01-28",
       "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16"]
EXITS = {"take3": "各腿 3 倍卖", "take5": "各腿 5 倍卖", "trail2": "各腿翻倍后回撤卖", "hold": "拿到 15:30"}


def kind_of(day: str) -> str:
    return "Fed" if day in FED else "CPI" if day in CPI else "非农" if day in JOBS else ""


def run(client) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    rows = []
    for und in NAMES:
        days = minutes_by_day(client, und, START, end)
        for day in sorted(days):
            m = days[day]
            if "09:30" not in m or not at_or_before(m, "15:30"):
                continue
            ev = kind_of(day)
            times = ["10:00"] + (["13:55"] if ev == "Fed" else [])
            spots = {t: float(at_or_before(m, t)["c"]) for t in times if at_or_before(m, t)}
            if not spots:
                continue
            mid = sum(spots.values()) / len(spots)
            try:
                cs = client.option_contracts(und, status="inactive", expiration_date=day, strike_price_gte=f"{mid * 0.97:.2f}",
                                             strike_price_lte=f"{mid * 1.03:.2f}") or \
                     client.option_contracts(und, status="active", expiration_date=day, strike_price_gte=f"{mid * 0.97:.2f}",
                                             strike_price_lte=f"{mid * 1.03:.2f}")
            except Exception:
                cs = []
            if not cs:
                continue
            legs = {t: (pick(cs, "C", s, 0.006), pick(cs, "P", s, 0.006)) for t, s in spots.items()}
            want = sorted({x for pair in legs.values() for x in pair if x})
            try:
                bars = client.option_bars(want, f"{day}T13:00:00Z", f"{day}T21:00:00Z", timeframe="1Min")
            except Exception:
                continue
            time.sleep(0.05)
            mins = {k: et_minutes(v) for k, v in bars.items()}
            for t, (c, p) in legs.items():
                rc = intraday(mins.get(c, {}), t, "15:30") if c in mins else None
                rp = intraday(mins.get(p, {}), t, "15:30") if p in mins else None
                if rc and rp:
                    rows.append({"und": und, "day": day, "event": ev, "at": t,
                                 "res": {k: round((rc[k] + rp[k]) / 2, 4) for k in EXITS}})
    return {"generated": date.today().isoformat(), "start": START, "end": end, "rows": rows}


def main() -> None:
    from hero.alpaca import Alpaca
    rep = run(Alpaca())
    r = rep["rows"]
    groups = {}
    for und in NAMES:
        groups[f"{und}：CPI 日 10:00"] = [x for x in r if x["und"] == und and x["event"] == "CPI"]
        groups[f"{und}：非农日 10:00"] = [x for x in r if x["und"] == und and x["event"] == "非农"]
        groups[f"{und}：Fed 日 10:00"] = [x for x in r if x["und"] == und and x["event"] == "Fed" and x["at"] == "10:00"]
        groups[f"{und}：Fed 日 13:55（决议前 5 分钟）"] = [x for x in r if x["und"] == und and x["event"] == "Fed" and x["at"] == "13:55"]
        groups[f"{und}：对照，其他日子 10:00"] = [x for x in r if x["und"] == und and not x["event"]]
    table, md = report(f"数据日双向买末日票 {rep['generated']}",
                       f"{rep['start']} 至 {rep['end']}，SPY / QQQ：CPI、非农、Fed 决议日（日期按公布日程，2025 年政府停摆期间少数可能差一天），"
                       "10:00（Fed 日另测 13:55）同时买当天到期、离现价 0.6% 的看涨和看跌各一半钱，每条腿按分钟收盘价卖出，最晚 15:30；"
                       "买入多付 10%、卖出少拿 10%。",
                       groups, EXITS)
    rep["table"] = table
    save("eventday", rep, md)


if __name__ == "__main__":
    sys.exit(main())
