"""Do the two day-signals hold outside SPY, on real option prices? (owner, 11-16: several lines at once -- waiting for
one plan twin to collect 20 days is too slow.)

research/2026-11-16-zdte-detect.md found two signals for "SPY keeps going after 10:00": the price already 0.5% from
the open at 10:00, and yesterday's close under the 20-day average. On model option prices the second looked worth a
rule; on SPY's real prices since 2024-02 it did not (options cost twice as much on those days). Here the same replay
runs on QQQ and IWM: every weekday since 2024-02, 10:00, the nearest expiry (same day when listed), a call if the
price is above the open and a put if below, the first strike 0.3% beyond the price, half sold at 3x and the rest
trailed 40% off its best after a double, else 15:30 -- on real one-minute option closes, at two costs.

Fixed before looking (the same line as the 21 lines of 11-16): a signal PASSES on an underlying when
  (1) mean per $1 on its days minus the mean on the other days is at least +0.15,
  (2) the difference is above zero in both halves of the days (by date),
  (3) it is still above zero after dropping the three best signal days,
  (4) there are at least 60 signal days.
Also shown, not part of the line: all in ($1,000 to $10,000, a zero under $100), the cost of a hero on every day, on
the signal's days and on the others. The 20-day average here is of each day's last regular minute (15:59), not the
official close. Record only. Run: python -m hero.research_zdte_lines
"""

from __future__ import annotations

import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

from hero import robust
from hero.research_0dte import at_or_before, et_minutes
from hero.research_0dte_names import minutes_by_day, pick
from hero.research_event_turn import Pace
from hero.research_ideas import save
from hero.research_zdte_cost import key, play_cost

ROOT = Path(__file__).resolve().parent.parent
UNDS = ("QQQ", "IWM")
START, LEAD = "2024-02-01", "2023-12-01"   # LEAD: twenty closes before the first day
ENTRY, OUT, OFF = "10:00", "15:30", 0.003
COSTS = [(0.05, 0.05, "买卖各 5% 成本"), (0.0, 0.0, "不计成本")]
MIN_MOVE, AVG_DAYS = 0.005, 20
NEED_DIFF, NEED_DAYS = 0.15, 60
SIGNALS = {"lit": f"昨收低于 {AVG_DAYS} 日均线", "moved": f"10:00 已离开盘 {MIN_MOVE:.1%} 以上"}


def lights(closes: dict[str, float], n: int = AVG_DAYS) -> dict[str, bool]:
    """day -> the previous close is below the mean of the last n completed closes (the previous one included)."""
    days = sorted(closes)
    return {days[i]: closes[days[i - 1]] < statistics.mean(closes[d] for d in days[i - n:i]) for i in range(n, len(days))}


def rounds(seq: list[float], start: float = 1000.0, target: float = 10000.0, zero: float = 0.10) -> tuple[int, int]:
    """All in every time: (heroes, zeros). A round ends at the target or under a tenth of the start."""
    h = z = 0
    cash = start
    for x in seq:
        cash *= max(0.0, 1 + x)
        if cash >= target:
            h, cash = h + 1, start
        elif cash < start * zero:
            z, cash = z + 1, start
    return h, z


def cost_per_hero(seq: list[float], start: float = 1000.0) -> float | None:
    h, z = rounds(seq, start)
    return (h + z) * start / h if h else None


def verdict(on: list[tuple[str, float]], off: list[tuple[str, float]]) -> dict | None:
    """The fixed line for one signal on one underlying: (day, per $1) on its days against the other days."""
    if not on or not off:
        return None
    mean = lambda xs: statistics.mean(r for _, r in xs)
    both = sorted(on + off)
    mid = both[len(both) // 2][0]
    halves = []
    for keep in (lambda d: d < mid, lambda d: d >= mid):
        a, b = [x for x in on if keep(x[0])], [x for x in off if keep(x[0])]
        halves.append(mean(a) - mean(b) if a and b else None)
    rest = sorted(on, key=lambda x: -x[1])[3:]
    minus3 = mean(rest) - mean(off) if rest else None
    diff = mean(on) - mean(off)
    ok = (diff >= NEED_DIFF and all(h is not None and h > 0 for h in halves) and minus3 is not None and minus3 > 0
          and len(on) >= NEED_DAYS)
    return {"on_n": len(on), "on_mean": mean(on), "on_median": statistics.median(r for _, r in on),
            "off_n": len(off), "off_mean": mean(off), "off_median": statistics.median(r for _, r in off),
            "diff": diff, "halves": halves, "minus3": minus3, "pass": ok,
            "hero_all": cost_per_hero([r for _, r in both]), "hero_on": cost_per_hero([r for _, r in sorted(on)]),
            "hero_off": cost_per_hero([r for _, r in sorted(off)]), "flags": robust.flags(robust.summary(sorted(on)))}


def run(client, unds: tuple[str, ...] = UNDS) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    pace, rows = Pace(client), []
    for und in unds:
        days = minutes_by_day(client, und, LEAD, end)
        lit = lights({d: float(m[max(m)]["c"]) for d, m in days.items() if m})
        for day in sorted(d for d in days if d >= START):
            m = days[day]
            b = at_or_before(m, ENTRY)
            if "09:30" not in m or not b or not at_or_before(m, OUT) or day not in lit:
                continue
            o, spot = float(m["09:30"]["o"]), float(b["c"])
            kind = "C" if spot >= o else "P"
            cs = []
            for status in ("inactive", "active"):
                try:
                    cs = client.option_contracts(und, status=status, expiration_date_gte=day,
                                                 expiration_date_lte=(date.fromisoformat(day) + timedelta(days=7)).isoformat(),
                                                 strike_price_gte=f"{spot * 0.97:.2f}", strike_price_lte=f"{spot * 1.03:.2f}")
                except Exception:
                    cs = []
                if cs:
                    break
            if not cs:
                continue
            exp = min(c["expiration_date"] for c in cs)
            sym = pick([c for c in cs if c["expiration_date"] == exp], kind, spot, OFF)
            if not sym:
                continue
            pace.wait()
            try:
                bars = client.option_bars([sym], f"{day}T13:00:00Z", f"{day}T21:00:00Z", timeframe="1Min")
            except Exception:
                continue
            if sym not in bars:
                continue
            mins, res = et_minutes(bars[sym]), {}
            for buy, sell, _ in COSTS:
                got = play_cost(mins, ENTRY, buy, sell)
                if got:
                    res[key(buy, sell)] = got["take3_trail"]
            if res:
                rows.append({"und": und, "day": day, "move": round(spot / o - 1, 5), "kind": kind, "sym": sym,
                             "dte": (date.fromisoformat(exp) - date.fromisoformat(day)).days, "lit": lit[day], "res": res})
    return {"generated": date.today().isoformat(), "start": START, "end": end, "rows": rows}


def split(rows: list[dict], und: str, signal: str, k: str, same_day: bool = True):
    """(on, off) lists of (day, per $1) for one underlying, one signal and one cost."""
    on, off = [], []
    for r in rows:
        if r["und"] != und or k not in r["res"] or (same_day and r["dte"] != 0):
            continue
        hit = r["lit"] if signal == "lit" else abs(r["move"]) >= MIN_MOVE
        (on if hit else off).append((r["day"], r["res"][k]))
    return on, off


def report(rep: dict) -> tuple[dict, str]:
    usd = lambda v: "没有 hero" if v is None else f"${v:,.0f}"
    num = lambda v: "—" if v is None else f"{v:+.2f}"
    table, out, passed, lines = {}, [], 0, 0
    for und in sorted({r["und"] for r in rep["rows"]}):
        mine = [r for r in rep["rows"] if r["und"] == und]
        same = [r for r in mine if r["dte"] == 0]
        out += [f"## {und}", "", f"{len(mine)} 个交易日有成交记录，其中当天到期的 {len(same)} 天（下面只用这些）；"
                f"昨收低于均线的占 {sum(r['lit'] for r in same) / max(len(same), 1):.0%}，"
                f"10:00 离开盘 {MIN_MOVE:.1%} 以上的占 {sum(abs(r['move']) >= MIN_MOVE for r in same) / max(len(same), 1):.0%}。", "",
                "| 信号 | 成本 | 亮：天数 / 每 $1（中位数） | 不亮：天数 / 每 $1（中位数） | 差（前半 / 后半；去掉最好 3 天） | 过线 | "
                "每个 hero 的成本：每天 / 只做亮 / 只做不亮 | 亮的日子的提醒 |", "|---|---|---|---|---|---|---|---|"]
        for signal, name in SIGNALS.items():
            for buy, sell, cname in COSTS:
                v = verdict(*split(rep["rows"], und, signal, key(buy, sell)))
                table[f"{und}|{signal}|{key(buy, sell)}"] = v
                if not v:
                    continue
                lines, passed = lines + 1, passed + bool(v["pass"])
                out.append(f"| {name} | {cname} | {v['on_n']} / {v['on_mean']:+.2f}（{v['on_median']:+.2f}） | "
                           f"{v['off_n']} / {v['off_mean']:+.2f}（{v['off_median']:+.2f}） | "
                           f"{v['diff']:+.2f}（{num(v['halves'][0])} / {num(v['halves'][1])}；{num(v['minus3'])}） | "
                           f"{'过' if v['pass'] else '不过'} | {usd(v['hero_all'])} / {usd(v['hero_on'])} / {usd(v['hero_off'])} | "
                           f"{'；'.join(v['flags']) or '—'} |")
        out.append("")
    head = [f"# 两条信号在 QQQ 和 IWM 上成不成立（真实期权价）{rep['generated']}", "",
            f"{rep['start']} 至 {rep['end']}，每个交易日 10:00，当天到期，比开盘高买看涨、低买看跌，行权价离现价至少 {OFF:.1%}，"
            "一半 3 倍卖、其余翻倍后从最高回撤 40% 卖、否则 15:30 卖，按期权的分钟收盘价。"
            f"事先写死的线：信号亮的日子和其余日子的平均每 $1 相差 {NEED_DIFF:+.2f} 以上、前后两半都为正、去掉亮的日子里最好的 3 天后仍为正、"
            f"亮的日子至少 {NEED_DAYS} 天。均线用每天最后一分钟（15:59）的价，不是官方收盘价。只研究，不改交易。", "",
            f"**{lines} 条线过了 {passed} 条。**", ""]
    return table, "\n".join(head + out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    rep = run(Alpaca())
    table, md = report(rep)
    save("zdte-lines", {**rep, "table": table}, md)


if __name__ == "__main__":
    sys.exit(main())
