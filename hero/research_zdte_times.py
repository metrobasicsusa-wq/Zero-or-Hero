"""SPY same-day options: does "already 0.5% from the open" work at other times of day? (owner, 11-17: what else can we add.)

The one day-signal with something behind it on real prices is "SPY is 0.5% or more from its open at 10:00"
(research/2026-11-16-zdte-detect.md: 50 days since 2024-02, +0.23 per $1 against -0.10 on the other days). It looks
once, at 10:00. On 11-17 SPY was +0.14% at 10:00, so nothing was done, and then fell 1.6%. Here the same trade is
replayed at 09:45, 10:00, 10:30, 11:00 and 12:00: at each time, a call if SPY is above its open and a put if below,
the first same-day strike 0.3% beyond the price, half sold at 3x and the rest trailed 40% off its best after a
double, else 15:30 -- on real one-minute option closes, at two costs.

Fixed before looking:
  (a) each time on its own, against the same line as before (research_zdte_lines.verdict: a gap of +0.15 per $1,
      above zero in both halves, still above zero without the three best signal days, at least 60 signal days);
  (b) the question that would add trades: on the days 10:00 does NOT show 0.5%, take the first later time (10:30,
      11:00, 12:00) that does. That set passes when its mean per $1 is above zero at 5% costs, still above zero
      without its three best days, above zero in both halves, and has at least 40 days.
Record only. Run: python -m hero.research_zdte_times
"""

from __future__ import annotations

import statistics
import sys
from datetime import date, timedelta

from hero import robust
from hero.research_0dte import at_or_before, et_minutes
from hero.research_0dte_names import minutes_by_day, pick
from hero.research_event_turn import Pace
from hero.research_ideas import save
from hero.research_zdte_cost import key, play_cost
from hero.research_zdte_lines import COSTS, MIN_MOVE, OFF, cost_per_hero, verdict

UND, START, OUT = "SPY", "2024-02-01", "15:30"
TIMES = ("09:45", "10:00", "10:30", "11:00", "12:00")
LATER = ("10:30", "11:00", "12:00")
NEED_LATER_DAYS = 40


def run(client) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    pace, rows = Pace(client), []
    days = minutes_by_day(client, UND, START, end)
    for day in sorted(days):
        m = days[day]
        if "09:30" not in m or not at_or_before(m, OUT):
            continue
        o = float(m["09:30"]["o"])
        spots = {t: float(b["c"]) for t in TIMES if (b := at_or_before(m, t))}
        if not spots:
            continue
        cs = []
        for status in ("inactive", "active"):
            try:
                cs = client.option_contracts(UND, status=status, expiration_date_gte=day, expiration_date_lte=day,
                                             strike_price_gte=f"{min(spots.values()) * 0.97:.2f}",
                                             strike_price_lte=f"{max(spots.values()) * 1.03:.2f}")
            except Exception:
                cs = []
            if cs:
                break
        if not cs:
            continue
        want = {}
        for t, spot in spots.items():
            kind = "C" if spot >= o else "P"
            sym = pick(cs, kind, spot, OFF)
            if sym:
                want[t] = (kind, sym, spot)
        if not want:
            continue
        pace.wait()
        try:
            bars = client.option_bars(sorted({s for _, s, _ in want.values()}), f"{day}T13:00:00Z", f"{day}T21:00:00Z",
                                      timeframe="1Min")
        except Exception:
            continue
        mins = {s: et_minutes(v) for s, v in bars.items()}
        for t, (kind, sym, spot) in want.items():
            if sym not in mins:
                continue
            res = {}
            for buy, sell, _ in COSTS:
                got = play_cost(mins[sym], t, buy, sell)
                if got:
                    res[key(buy, sell)] = got["take3_trail"]
            if res:
                rows.append({"day": day, "t": t, "move": round(spot / o - 1, 5), "kind": kind, "sym": sym, "res": res})
    return {"generated": date.today().isoformat(), "start": START, "end": end, "rows": rows}


def later_first(rows: list[dict], k: str) -> list[tuple[str, float]]:
    """(day, per $1) of the first later time that shows the move, on days 10:00 does not."""
    by: dict[str, dict[str, dict]] = {}
    for r in rows:
        by.setdefault(r["day"], {})[r["t"]] = r
    out = []
    for day in sorted(by):
        ten = by[day].get("10:00")
        if not ten or abs(ten["move"]) >= MIN_MOVE:
            continue
        for t in LATER:
            r = by[day].get(t)
            if r and abs(r["move"]) >= MIN_MOVE and k in r["res"]:
                out.append((day, r["res"][k]))
                break
    return out


def later_verdict(xs: list[tuple[str, float]]) -> dict | None:
    if not xs:
        return None
    mean = lambda v: statistics.mean(r for _, r in v)
    mid = xs[len(xs) // 2][0]
    halves = [mean(h) if h else None for h in ([x for x in xs if x[0] < mid], [x for x in xs if x[0] >= mid])]
    rest = sorted(xs, key=lambda x: -x[1])[3:]
    minus3 = mean(rest) if rest else None
    ok = (mean(xs) > 0 and minus3 is not None and minus3 > 0 and all(h is not None and h > 0 for h in halves)
          and len(xs) >= NEED_LATER_DAYS)
    return {"n": len(xs), "mean": mean(xs), "median": statistics.median(r for _, r in xs), "minus3": minus3,
            "halves": halves, "pass": ok, "hero": cost_per_hero([r for _, r in xs]),
            "flags": robust.flags(robust.summary(xs))}


def report(rep: dict) -> tuple[dict, str]:
    usd = lambda v: "没有 hero" if v is None else f"${v:,.0f}"
    num = lambda v: "—" if v is None else f"{v:+.2f}"
    table, out = {}, []
    out += ["## 每个时点单独看", "",
            "| 时点 | 成本 | 亮：天数 / 每 $1（中位数） | 不亮：天数 / 每 $1 | 差（前半 / 后半；去掉最好 3 天） | 过线 | "
            "每个 hero 的成本：只做亮 / 只做不亮 | 亮的日子的提醒 |", "|---|---|---|---|---|---|---|---|"]
    for t in TIMES:
        for buy, sell, cname in COSTS:
            k = key(buy, sell)
            mine = [r for r in rep["rows"] if r["t"] == t and k in r["res"]]
            v = verdict([(r["day"], r["res"][k]) for r in mine if abs(r["move"]) >= MIN_MOVE],
                        [(r["day"], r["res"][k]) for r in mine if abs(r["move"]) < MIN_MOVE])
            table[f"{t}|{k}"] = v
            if v:
                out.append(f"| {t} | {cname} | {v['on_n']} / {v['on_mean']:+.2f}（{v['on_median']:+.2f}） | {v['off_n']} / {v['off_mean']:+.2f} | "
                           f"{v['diff']:+.2f}（{num(v['halves'][0])} / {num(v['halves'][1])}；{num(v['minus3'])}） | "
                           f"{'过' if v['pass'] else '不过'} | {usd(v['hero_on'])} / {usd(v['hero_off'])} | {'；'.join(v['flags']) or '—'} |")
    out += ["", f"## 10:00 没亮的日子，之后第一次亮（{'、'.join(LATER)}）", "",
            "| 成本 | 天数 | 每 $1（中位数） | 前半 / 后半 | 去掉最好 3 天 | 过线 | 每个 hero 的成本 | 提醒 |", "|---|---|---|---|---|---|---|---|"]
    for buy, sell, cname in COSTS:
        v = later_verdict(later_first(rep["rows"], key(buy, sell)))
        table[f"later|{key(buy, sell)}"] = v
        if v:
            out.append(f"| {cname} | {v['n']} | {v['mean']:+.2f}（{v['median']:+.2f}） | {num(v['halves'][0])} / {num(v['halves'][1])} | "
                       f"{num(v['minus3'])} | {'过' if v['pass'] else '不过'} | {usd(v['hero'])} | {'；'.join(v['flags']) or '—'} |")
    head = [f"# 「离开盘 {MIN_MOVE:.1%} 才做」换个时点看还成不成立（真实期权价）{rep['generated']}", "",
            f"{rep['start']} 至 {rep['end']}，SPY 每个交易日在 {'、'.join(TIMES)} 各看一眼：比开盘高买看涨、低买看跌，当天到期，"
            f"行权价离当时的价至少 {OFF:.1%}，一半 3 倍卖、其余翻倍后从最高回撤 40% 卖、否则 15:30 卖，按期权的分钟收盘价。"
            "及格线在看结果之前写死：单个时点用 11-16 那条线（亮和不亮差 +0.15 以上、前后两半都为正、去掉最好 3 天仍为正、至少 60 天）；"
            f"「10:00 没亮、之后第一次亮」这一组要求买卖各 5% 成本下平均为正、去掉最好 3 天仍为正、前后两半都为正、至少 {NEED_LATER_DAYS} 天。"
            "只研究，不改交易。", ""]
    return table, "\n".join(head + out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    rep = run(Alpaca())
    table, md = report(rep)
    save("zdte-times", {**rep, "table": table}, md)


if __name__ == "__main__":
    sys.exit(main())
