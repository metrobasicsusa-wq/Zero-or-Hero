"""Research: should the 10:00 same-day rule sit out when the open-to-10:00 move is too small to call?

On 2026-10-08 SPY opened at 774.90 and stood at 775.36 at 10:00 (+0.06%): the live rule read that as
"up", bought the 781 call, and SPY drifted lower. Here, on SIP minutes since 2024-02, SPY and QQQ on
every day with a same-day expiry, the live rule (call above the open at 10:00, else put; strike the
first $1 strike at least 0.6% / 0.3% beyond; a resting 3x take; else out at 15:30; pay 10% over, get
10% under) against:
  buckets:  the live rule's trades split by the size of the open-to-10:00 move;
  skip:     sit out the day when |move| < 0.1% / 0.2% / 0.3%;
  wait:     decide at the first of 10:00 / 10:30 / 11:00 where |move from the open| >= 0.2% / 0.3%,
            buying then in that direction; no such check -> no trade;
  prior:    the direction from yesterday's close instead of today's open;
  agree:    trade only when the open and yesterday's close give the same direction;
then the $500 game (half the account each trade, a round ends below $50, at $1,000 or $10,000).
Record only. Run: python -m hero.research_zdte_filter
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from hero.research_0dte import at_or_before, et_minutes, occ, strike_for, trade
from hero.research_0dte_names import minutes_by_day, stats

START = "2024-02-01"
NAMES = ("SPY", "QQQ")
CHECKS = ("10:00", "10:30", "11:00")
OFFSETS = (0.006, 0.003)
TAKE = 3
BUCKETS = ((0.0, 0.001), (0.001, 0.002), (0.002, 0.003), (0.003, 0.005), (0.005, 1.0))
SKIPS = (0.001, 0.002, 0.003)
WAITS = (0.002, 0.003)


def moves(m: dict[str, dict], prev: float | None) -> dict | None:
    """Price at each check time vs today's open (and vs yesterday's close at 10:00)."""
    if "09:30" not in m:
        return None
    day_open = float(m["09:30"]["o"])
    out = {"open": day_open, "spot": {}, "move": {}}
    for t in CHECKS:
        bar = at_or_before(m, t)
        if not bar:
            return None
        out["spot"][t] = float(bar["c"])
        out["move"][t] = float(bar["c"]) / day_open - 1
    out["prior"] = out["spot"]["10:00"] / prev - 1 if prev else None
    return out


def side(x: float) -> str:
    return "C" if x >= 0 else "P"


def decisions(mv: dict) -> dict[str, tuple[str, str] | None]:
    """Rule name -> (entry time, call/put), or None to sit the day out."""
    m10 = mv["move"]["10:00"]
    out: dict[str, tuple[str, str] | None] = {"live": ("10:00", side(m10))}
    for lo, hi in BUCKETS:
        out[f"bucket|{lo}|{hi}"] = ("10:00", side(m10)) if lo <= abs(m10) < hi else None
    for t in SKIPS:
        out[f"skip|{t}"] = ("10:00", side(m10)) if abs(m10) >= t else None
    for t in WAITS:
        hit = next((c for c in CHECKS if abs(mv["move"][c]) >= t), None)
        out[f"wait|{t}"] = (hit, side(mv["move"][hit])) if hit else None
    pr = mv["prior"]
    out["prior"] = ("10:00", side(pr)) if pr is not None else None
    out["agree"] = ("10:00", side(m10)) if pr is not None and side(pr) == side(m10) else None
    return out


def run(client) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    rows = []
    for und in NAMES:
        days = minutes_by_day(client, und, START, end)
        prev = None
        for day in sorted(days):
            m = days[day]
            last = at_or_before(m, "15:59")
            mv = moves(m, prev)
            prev = float(last["c"]) if last else prev
            if not mv or not at_or_before(m, "15:30"):
                continue
            plan = {(t, k, off): occ(und, day, k, strike_for(mv["spot"][t], k, off))
                    for t in CHECKS for k in ("C", "P") for off in OFFSETS}
            try:
                bars = client.option_bars(sorted(set(plan.values())), f"{day}T13:00:00Z", f"{day}T21:00:00Z", timeframe="1Min")
            except Exception:
                continue
            time.sleep(0.15)
            if not bars:
                continue  # no same-day expiry that day
            mins = {s: et_minutes(b) for s, b in bars.items()}
            res = {}
            for (t, k, off), sym in plan.items():
                r = trade(mins[sym], t, TAKE) if sym in mins else None
                if r is not None:
                    res[f"{t}|{k}|{off}"] = round(r, 4)
            rows.append({"day": day, "symbol": und, "move": {k: round(v, 5) for k, v in mv["move"].items()},
                         "prior": round(mv["prior"], 5) if mv["prior"] is not None else None, "res": res})
    return {"generated": date.today().isoformat(), "start": START, "end": end, "rows": rows}


def trades_for(rows: list[dict], rule: str, off: float) -> list[tuple[str, float]]:
    out = []
    for r in rows:
        mv = {"move": r["move"], "prior": r["prior"], "spot": {}}
        got = decisions(mv).get(rule)
        if got:
            x = r["res"].get(f"{got[0]}|{got[1]}|{off}")
            if x is not None:
                out.append((r["day"], x))
    return sorted(out)


def table(rep: dict) -> dict:
    rules = list(decisions({"move": {c: 0.0 for c in CHECKS}, "prior": 0.0}))
    out = {}
    for und in NAMES:
        for since in (rep["start"], "2026-01-01"):
            rows = [r for r in rep["rows"] if r["symbol"] == und and r["day"] >= since]
            days = len(rows)
            for off in OFFSETS:
                for rule in rules:
                    ts = trades_for(rows, rule, off)
                    if ts:
                        out[f"{und}|{since}|{off}|{rule}"] = {**stats(ts, TAKE), "days": days}
    return out


NAMES_ZH = {"live": "现行：10:00 顺势", "prior": "按昨收定方向", "agree": "开盘和昨收方向一致才做"}


def label(rule: str) -> str:
    if rule in NAMES_ZH:
        return NAMES_ZH[rule]
    kind, *v = rule.split("|")
    if kind == "bucket":
        lo, hi = float(v[0]), float(v[1])
        return f"　开盘到 10:00 涨跌 {lo:.1%}–{hi:.1%}" if hi < 1 else f"　开盘到 10:00 涨跌 ≥ {lo:.1%}"
    if kind == "skip":
        return f"不到 {float(v[0]):.1%} 就不做"
    return f"等到 ≥ {float(v[0]):.1%}（10:00 / 10:30 / 11:00 第一次）再做"


def markdown(rep: dict, t: dict) -> str:
    out = [f"# 末日单：开盘方向不明显就不做？ {rep['generated']}", "",
           f"{rep['start']} 至 {rep['end']}，SIP 分钟线，SPY / QQQ 每个有当天到期合约的日子。现行规则：10:00 比开盘高就买看涨、低就买看跌，"
           "行权价离现价至少 0.6%（另测 0.3%），3 倍止盈，没到 15:30 卖；买入多付 10%、卖出少拿 10%。"
           "对照：按开盘到 10:00 的涨跌幅分组；不到 0.1% / 0.2% / 0.3% 就不做；等到涨跌够大（10:00 / 10:30 / 11:00 第一次）再按那时方向买；"
           "按昨收定方向；开盘和昨收方向一致才做。玩法：$500 每笔押一半，低于 $50 归零，到 $1,000 / $10,000 成功。只研究，不改交易。", ""]
    for und in NAMES:
        for since in (rep["start"], "2026-01-01"):
            for off in OFFSETS:
                rows = [(k.split("|", 3)[3], r) for k, r in t.items() if k.startswith(f"{und}|{since}|{off}|")]
                if not rows:
                    continue
                out += [f"## {und}，{'全部' if since == rep['start'] else '2026 年以来'}，离现价 {off:.1%}（共 {rows[0][1]['days']} 天）", "",
                        "| 规则 | 笔数 | 平均每 $1 | 中位数 | 赚钱比例 | 碰到 3 倍 | 到 $1,000：成功 / 归零 | 到 $10,000：成功 / 归零 |",
                        "|---|---|---|---|---|---|---|---|"]
                for rule, r in rows:
                    out.append(f"| {label(rule)} | {r['n']} | {r['mean']:+.0%} | {r['median']:+.0%} | {r['win']:.0%} | {r['hit']:.0%} | "
                               f"{r['t2']['heroes']} / {r['t2']['zeros']} | {r['t20']['heroes']} / {r['t20']['zeros']} |")
                out.append("")
    return "\n".join(out)


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca())
    t = table(rep)
    rep["table"] = t
    (root / "research" / f"{rep['generated']}-zdte-filter.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    (root / "research" / f"{rep['generated']}-zdte-filter.md").write_text(markdown(rep, t))
    print(markdown(rep, t))


if __name__ == "__main__":
    sys.exit(main())
