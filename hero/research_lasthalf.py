"""Idea 1 (2026-10-13): the last half hour on the cheapest same-day options.

Gao, Han, Li & Zhou (JFE 2018): the S&P 500's first half-hour return predicts its last half-hour return, more so on
volatile, high-volume and news days. Our 0DTE sleeves sell at 15:30 and miss that half hour. Here, SPY and QQQ, every
day with a same-day expiry since 2024-02: the direction from the prior close to 10:00 (the paper's) or from the open
to 10:00 (our live signal); at 15:25 (also 15:00) buy that day's option the same way, 0.3% / 0.6% beyond the price;
out on minute closes at 2x / 3x / 5x, a trail (2x then a 40% giveback), else at 15:58. Also the paper's strong days
(the first half hour 0.5%+). Record only. Run: python -m hero.research_lasthalf
"""

from __future__ import annotations

import statistics
import sys
import time
from datetime import date, timedelta

from hero.research_0dte import at_or_before, et_minutes
from hero.research_0dte_names import minutes_by_day, pick
from hero.research_ideas import intraday, report, save

START = "2024-02-01"
NAMES = ("SPY", "QQQ")
ENTRIES = ("15:25", "15:00")
OFFS = (0.003, 0.006)
EXITS = {"take2": "2 倍卖", "take3": "3 倍卖", "take5": "5 倍卖", "trail2": "翻倍后回撤 40% 卖", "hold": "拿到 15:58"}


def run(client) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    rows = []
    for und in NAMES:
        days = minutes_by_day(client, und, START, end)
        order = sorted(days)
        for i, day in enumerate(order[1:], 1):
            m = days[day]
            prev = at_or_before(days[order[i - 1]], "15:59")
            ten = at_or_before(m, "10:00")
            if "09:30" not in m or not prev or not ten or not at_or_before(m, "15:58"):
                continue
            o, pc, t10 = float(m["09:30"]["o"]), float(prev["c"]), float(ten["c"])
            first = t10 / pc - 1
            spots = {at: float(at_or_before(m, at)["c"]) for at in ENTRIES if at_or_before(m, at)}
            if not spots:
                continue
            mid = statistics.mean(spots.values())
            try:
                cs = client.option_contracts(und, status="inactive", expiration_date=day, strike_price_gte=f"{mid * 0.97:.2f}",
                                             strike_price_lte=f"{mid * 1.03:.2f}") or \
                     client.option_contracts(und, status="active", expiration_date=day, strike_price_gte=f"{mid * 0.97:.2f}",
                                             strike_price_lte=f"{mid * 1.03:.2f}")
            except Exception:
                cs = []
            if not cs:
                continue  # no same-day expiry
            plan = []
            for at, spot in spots.items():
                for sig, up in (("prev", first >= 0), ("open", t10 >= o)):
                    for off in OFFS:
                        sym = pick(cs, "C" if up else "P", spot, off)
                        if sym:
                            plan.append((at, sig, off, sym))
            try:
                bars = client.option_bars(sorted({p[3] for p in plan}), f"{day}T13:00:00Z", f"{day}T21:00:00Z", timeframe="1Min")
            except Exception:
                continue
            time.sleep(0.05)
            mins = {k: et_minutes(v) for k, v in bars.items()}
            for at, sig, off, sym in plan:
                res = intraday(mins.get(sym, {}), at, "15:58") if sym in mins else None
                if res:
                    rows.append({"und": und, "day": day, "at": at, "sig": sig, "off": off, "first": round(first, 4), "res": res})
    return {"generated": date.today().isoformat(), "start": START, "end": end, "rows": rows}


def groups_of(r: list[dict]) -> dict[str, list[dict]]:
    out = {}
    for und in NAMES:
        for at in ENTRIES:
            for off in OFFS:
                base = [x for x in r if x["und"] == und and x["at"] == at and x["off"] == off]
                out[f"{und} {at} 买 {off:.1%}，方向=昨收到 10:00"] = [x for x in base if x["sig"] == "prev"]
                out[f"　{und} {at} {off:.1%}：前半小时动 0.5% 以上"] = [x for x in base if x["sig"] == "prev" and abs(x["first"]) >= 0.005]
                out[f"{und} {at} 买 {off:.1%}，方向=开盘到 10:00"] = [x for x in base if x["sig"] == "open"]
    return out


def main() -> None:
    from hero.alpaca import Alpaca
    rep = run(Alpaca())
    table, md = report(f"尾盘半小时买末日票 {rep['generated']}",
                       f"{rep['start']} 至 {rep['end']}，SPY / QQQ 有当天到期合约的日子。按前半小时方向（昨收到 10:00，或开盘到 10:00），"
                       "15:25（另测 15:00）买当天到期、离现价 0.3% / 0.6% 的期权，按分钟收盘价卖出，最晚 15:58；买入多付 10%、卖出少拿 10%。",
                       groups_of(rep["rows"]), EXITS)
    rep["table"] = table
    save("lasthalf", rep, md)


if __name__ == "__main__":
    sys.exit(main())
