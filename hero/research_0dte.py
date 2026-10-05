"""Research: same-day-expiry ("0DTE") options on SPY and QQQ, judged by the zero-or-hero goal.

Every trading day from 2024-02 (Alpaca's option history), with one-minute bars:
  entry at 10:00, 12:00 or 14:00 ET, one contract expiring that day;
  direction: "trend" (call if the price is above the day's open at entry, else put) or calls only;
  strike: 0.3% / 0.6% / 1.0% beyond the price (rounded outward to the $1 grid);
  exit: a resting limit at 3x / 5x / 10x the cost (filled if a later minute's high reaches it),
        otherwise sold at 15:30 (the account must be flat well before the close).
Costs: pay 10% over the minute close to get in (at least a cent), get 10% under it on a
market exit; the limit fills at its price. A contract with no trade within 5 minutes of the
entry time is skipped that day.
Then each rule is replayed as the experiment would run it: an attempt ends when the account
falls below $50 of its $500 (cheap 0DTE contracts still trade at a few cents, so a 40% line
would end attempts that can still recover), and a fresh $500 attempt begins:
all of the account on every trade, or 20% of it; and the chances of 5x / 10x / 100x.
Record only. Run: python -m hero.research_0dte
"""

from __future__ import annotations

import json
import math
import statistics
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from hero import research_hero as rh

ET = ZoneInfo("America/New_York")
START = "2024-02-01"
UNDERLYINGS = ("SPY", "QQQ")
ENTRIES = ("10:00", "12:00", "14:00")
OFFSETS = (0.003, 0.006, 0.010)
TAKE = (3, 5, 10)
EXIT_AT = "15:30"
SLIP = 0.10
FRACTIONS = (1.0, 0.2)
END_LOSS = 0.9  # the attempt is over below $50 of $500


def occ(und: str, day: str, kind: str, strike: float) -> str:
    d = date.fromisoformat(day)
    return f"{und}{d:%y%m%d}{kind}{int(round(strike * 1000)):08d}"


def et_minutes(bars: list[dict]) -> dict[str, dict]:
    """{'HH:MM': bar} in New York time."""
    out = {}
    for b in bars:
        t = datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ET)
        out[t.strftime("%H:%M")] = b
    return out


def at_or_after(minutes: dict[str, dict], hhmm: str, within: int = 5) -> tuple[str, dict] | None:
    h, m = map(int, hhmm.split(":"))
    for k in range(within + 1):
        mm = h * 60 + m + k
        key = f"{mm // 60:02d}:{mm % 60:02d}"
        if key in minutes:
            return key, minutes[key]
    return None


def at_or_before(minutes: dict[str, dict], hhmm: str) -> dict | None:
    keys = [k for k in minutes if k <= hhmm]
    return minutes[max(keys)] if keys else None


def strike_for(spot: float, kind: str, off: float) -> float:
    return float(math.ceil(spot * (1 + off))) if kind == "C" else float(math.floor(spot * (1 - off)))


def trade(opt: dict[str, dict], entry: str, take: float) -> float | None:
    """Return per $1 for one contract bought at `entry`, limit at take x cost, else out at 15:30."""
    got = at_or_after(opt, entry)
    if not got:
        return None
    t0, bar = got
    cost = float(bar["c"]) + max(float(bar["c"]) * SLIP, 0.01)
    target = take * cost
    for k in sorted(opt):
        if t0 < k <= EXIT_AT and float(opt[k]["h"]) >= target:
            return take - 1
    last = at_or_before({k: v for k, v in opt.items() if k > t0}, EXIT_AT)
    px = float(last["c"]) if last else 0.0  # no later trade: treat as worthless (it usually is)
    return max(px - max(px * SLIP, 0.01), 0.0) / cost - 1


def trading_days(client, und: str, start: str, end: str) -> dict[str, dict[str, dict]]:
    """{day: {'HH:MM': bar}} of the underlying, month by month."""
    out: dict[str, dict[str, dict]] = {}
    d = date.fromisoformat(start).replace(day=1)
    while d <= date.fromisoformat(end):
        nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        bars = client.stock_bars([und], d.isoformat(), (nxt - timedelta(days=1)).isoformat()).get(und, [])
        for b in bars:
            t = datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ET)
            out.setdefault(t.date().isoformat(), {})[t.strftime("%H:%M")] = b
        d = nxt
    return out


def run(client) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    trades: dict[str, list[tuple[str, float]]] = {}
    days_used = {}
    for und in UNDERLYINGS:
        days = trading_days(client, und, START, end)
        used = 0
        for day in sorted(days):
            m = days[day]
            if "09:30" not in m or "15:30" not in m:
                continue
            day_open = float(m["09:30"]["o"])
            plan = {}
            for entry in ENTRIES:
                spot_bar = at_or_before(m, entry)
                if not spot_bar:
                    continue
                spot = float(spot_bar["c"])
                trend = "C" if spot >= day_open else "P"
                for off in OFFSETS:
                    for kind in ("C", "P"):
                        plan[(entry, off, kind)] = (occ(und, day, kind, strike_for(spot, kind, off)), trend)
            syms = sorted({v[0] for v in plan.values()})
            try:
                bars = client.option_bars(syms, f"{day}T13:00:00Z", f"{day}T21:00:00Z", timeframe="1Min")
            except Exception:
                continue
            time.sleep(0.15)
            if not bars:
                continue  # no same-day expiry that day
            used += 1
            mins = {s: et_minutes(b) for s, b in bars.items()}
            for (entry, off, kind), (sym, trend) in plan.items():
                if sym not in mins:
                    continue
                for take in TAKE:
                    r = trade(mins[sym], entry, take)
                    if r is None:
                        continue
                    base = f"{und}|{entry}|{off}|{take}"
                    if kind == "C":
                        trades.setdefault(base + "|calls", []).append((day, r))
                    if kind == trend:
                        trades.setdefault(base + "|trend", []).append((day, r))
        days_used[und] = used
    rows = []
    last_day = max((d for v in trades.values() for d, _ in v), default=end)
    for key, ts in trades.items():
        und, entry, off, take, mode = key.split("|")
        rets = [r for _, r in ts]
        row = {"underlying": und, "entry": entry, "offset": float(off), "take": int(take), "direction": mode,
               "n": len(rets), "win": round(sum(r > 0 for r in rets) / len(rets), 3),
               "mean": round(statistics.mean(rets), 3), "hit_take": round(sum(r >= int(take) - 1 for r in rets) / len(rets), 3)}
        for f in FRACTIONS:
            stream = [(d, max(f * r, -1.0)) for d, r in ts]
            first = (date.fromisoformat(ts[0][0]) - timedelta(days=1)).isoformat()
            for target in rh.TARGETS:
                seq = rh.sequential(stream, first, target, END_LOSS)
                win = rh.windows(stream, first, last_day, target, END_LOSS)
                row[f"f{f}_t{target}"] = {"heroes": seq["heroes"], "zeros": seq["zeros"],
                                          "days": seq["median_days_to_hero"], "p12": win["p_hero"]}
        rows.append(row)
    return {"generated": date.today().isoformat(), "start": START, "days_used": days_used, "rows": rows,
            # every trade per rule, so the replay can be redone with other end lines or targets
            "trades": {k: [[d, round(r, 4)] for d, r in v] for k, v in trades.items()}}


def markdown(rep: dict) -> str:
    out = [f"# 末日期权（0DTE）研究 {rep['generated']}", "",
           f"自 {rep['start']}，用到的交易日：{rep['days_used']}。每天在 10:00 / 12:00 / 14:00 买 1 张当天到期的 SPY 或 QQQ 期权，"
           "行权价离现价 0.3% / 0.6% / 1.0%，方向为「顺势」（开盘以来涨就买看涨、跌就买看跌）或「只买看涨」，"
           "挂 3 / 5 / 10 倍止盈单，没到就 15:30 卖出。买入多付 10%、卖出少拿 10%（至少 1 美分），止盈按挂单价成交。"
           "资金：每次全仓（100%）或每次 20%；账户低于 $50 算归零、用新的 $500 重来。只研究，不改交易。", ""]
    rows = rep["rows"]
    out += ["## 每笔交易的平均结果（最好的 10 种和最差的 5 种）", "",
            "| 标的 | 买入 | 距离 | 方向 | 止盈 | 次数 | 赚钱比例 | 碰到止盈 | 平均每 $1 |", "|---|---|---|---|---|---|---|---|---|"]
    ranked = sorted(rows, key=lambda r: -r["mean"])
    for r in ranked[:10] + ranked[-5:]:
        out.append(f"| {r['underlying']} | {r['entry']} | {r['offset']:.1%} | {'顺势' if r['direction'] == 'trend' else '只买看涨'} | "
                   f"{r['take']} 倍 | {r['n']} | {r['win']:.0%} | {r['hit_take']:.0%} | {r['mean']:+.0%} |")
    out.append(f"\n共 {len(rows)} 种规则，平均每 $1 为正的有 {sum(r['mean'] > 0 for r in rows)} 种。\n")
    for f in FRACTIONS:
        for t in rh.TARGETS:
            key = f"f{f}_t{t}"
            best = sorted(rows, key=lambda r: (-(r[key]["p12"] or 0), -r[key]["heroes"]))[:8]
            out += [f"## 每次押 {f:.0%}、目标 {t} 倍：最有机会的 8 种", "",
                    "| 标的 | 买入 | 距离 | 方向 | 止盈 | 连续尝试 成功/归零 | 每次成功平均要尝试 | 成功用时（中位天数） | 12 个月内成功比例 |",
                    "|---|---|---|---|---|---|---|---|---|"]
            for r in best:
                x = r[key]
                per = f"{(x['heroes'] + x['zeros']) / x['heroes']:.1f}" if x["heroes"] else "—"
                days = f"{x['days']:.0f}" if x["days"] is not None else "—"
                p12 = f"{x['p12']:.0%}" if x["p12"] is not None else "—"
                out.append(f"| {r['underlying']} | {r['entry']} | {r['offset']:.1%} | {'顺势' if r['direction'] == 'trend' else '只买看涨'} | "
                           f"{r['take']} 倍 | {x['heroes']} / {x['zeros']} | {per} | {days} | {p12} |")
            out.append("")
    return "\n".join(out)


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca())
    (root / "research" / f"{rep['generated']}-0dte.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False) + "\n")
    (root / "research" / f"{rep['generated']}-0dte.md").write_text(markdown(rep))
    print(markdown(rep))


if __name__ == "__main__":
    sys.exit(main())
