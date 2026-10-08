"""Research: after SPY / QQQ have closed down several days running, buy that day's same-day options?

research_streak (2026-10-08) found that after down streaks the next five days bounce on average but also
dip further more often: more movement both ways. Here that is put to same-day options, on SIP minutes
since 2024-02, SPY and QQQ, every day with a same-day expiry, grouped by the streak at yesterday's close:
  at 10:00 the option expiring that day, the first $1 strike at least 0.6% / 0.3% beyond the price;
  calls (a bounce), puts (more downside), the live rule (the side of the open), or both (half in each);
  a resting 3x / 5x take, else out at 15:30; pay 10% over, get 10% under;
against all days, so a streak only counts if it beats the base rate;
then the $500 game (half the account each trade, a round ends below $50, at $1,000 / $10,000).
Record only. Run: python -m hero.research_zdte_streak
"""

from __future__ import annotations

import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from hero.research_0dte import at_or_before, et_minutes, occ, strike_for, trade
from hero.research_0dte_names import minutes_by_day, stats
from hero.research_streak import streaks

START = "2024-02-01"
NAMES = ("SPY", "QQQ")
ENTRY = "10:00"
OFFSETS = (0.006, 0.003)
TAKES = (3, 5)
RULES = ("calls", "puts", "trend", "both")
RULE_ZH = {"calls": "买看涨（赌反弹）", "puts": "买看跌（赌继续跌）", "trend": "现行顺势", "both": "涨跌各一半"}
BIG_DROP = -0.01


def groups(streak: int, prev_ret: float | None) -> list[str]:
    """Every group a day belongs to, by yesterday's closing streak and yesterday's change."""
    g = ["全部日子"]
    if streak >= 3:
        g.append("连涨 3 天以上")
    elif streak in (1, 2):
        g.append(f"连涨 {streak} 天")
    elif streak in (-1, -2):
        g.append(f"连跌 {-streak} 天")
    elif streak <= -3:
        g.append("连跌 3 天以上")
    if streak <= -2:
        g.append("连跌 2 天以上")
    if prev_ret is not None and prev_ret <= BIG_DROP:
        g.append("昨天跌 1% 以上")
        if streak <= -2:
            g.append("连跌 2 天以上且昨天跌 1% 以上")
    return g


ORDER = ("全部日子", "连涨 3 天以上", "连涨 2 天", "连涨 1 天", "连跌 1 天", "连跌 2 天", "连跌 3 天以上",
         "连跌 2 天以上", "昨天跌 1% 以上", "连跌 2 天以上且昨天跌 1% 以上")


def results(res: dict, side: str, off: float, take: int) -> dict[str, float]:
    """Per $1 for each rule, from the call / put results of the day."""
    c, p = res.get(f"C|{off}|{take}"), res.get(f"P|{off}|{take}")
    out = {}
    if c is not None:
        out["calls"] = c
    if p is not None:
        out["puts"] = p
    t = c if side == "C" else p
    if t is not None:
        out["trend"] = t
    if c is not None and p is not None:
        out["both"] = (c + p) / 2
    return out


def run(client) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    rows = []
    for und in NAMES:
        days = minutes_by_day(client, und, "2024-01-02", end)
        closes, ordered = [], sorted(days)
        for day in ordered:
            last = at_or_before(days[day], "15:59")
            closes.append(float(last["c"]) if last else (closes[-1] if closes else 0.0))
        st = streaks(closes)
        for i, day in enumerate(ordered):
            if day < START or i < 2:
                continue
            m = days[day]
            spot_bar = at_or_before(m, ENTRY)
            if "09:30" not in m or not spot_bar or not at_or_before(m, "15:30"):
                continue
            spot = float(spot_bar["c"])
            side = "C" if spot >= float(m["09:30"]["o"]) else "P"
            plan = {(k, off): occ(und, day, k, strike_for(spot, k, off)) for k in ("C", "P") for off in OFFSETS}
            try:
                bars = client.option_bars(sorted(set(plan.values())), f"{day}T13:00:00Z", f"{day}T21:00:00Z", timeframe="1Min")
            except Exception:
                continue
            time.sleep(0.15)
            if not bars:
                continue  # no same-day expiry that day
            mins = {s: et_minutes(b) for s, b in bars.items()}
            res = {}
            for (k, off), sym in plan.items():
                for take in TAKES:
                    r = trade(mins[sym], ENTRY, take) if sym in mins else None
                    if r is not None:
                        res[f"{k}|{off}|{take}"] = round(r, 4)
            rows.append({"day": day, "symbol": und, "streak": st[i - 1], "prev_ret": round(closes[i - 1] / closes[i - 2] - 1, 5),
                         "side": side, "res": res})
    return {"generated": date.today().isoformat(), "start": START, "end": end, "rows": rows}


def table(rep: dict) -> dict:
    out = {}
    for und in NAMES:
        rows = [r for r in rep["rows"] if r["symbol"] == und]
        for off in OFFSETS:
            for take in TAKES:
                bucket: dict[tuple[str, str], list[tuple[str, float]]] = {}
                for r in rows:
                    got = results(r["res"], r["side"], off, take)
                    for g in groups(r["streak"], r["prev_ret"]):
                        for rule, x in got.items():
                            bucket.setdefault((g, rule), []).append((r["day"], x))
                for (g, rule), ts in bucket.items():
                    out[f"{und}|{off}|{take}|{g}|{rule}"] = stats(sorted(ts), take)
    return out


def markdown(rep: dict, t: dict) -> str:
    out = [f"# 连跌后买末日期权 {rep['generated']}", "",
           f"{rep['start']} 至 {rep['end']}，SIP 分钟线，SPY / QQQ 每个有当天到期合约的日子，按昨天收盘时的连涨 / 连跌天数分组。"
           "10:00 买当天到期、离现价至少 0.6%（另测 0.3%）的期权：只买看涨（赌反弹）、只买看跌（赌继续跌）、现行顺势（10:00 比开盘高买涨、低买跌）、"
           "涨跌各买一半；3 倍 / 5 倍止盈，没到 15:30 卖；买入多付 10%、卖出少拿 10%。每格是「平均每 $1 / 碰到止盈的比例」。"
           "和「全部日子」比，才知道连跌是不是真的更好。只研究，不改交易。", ""]
    for und in NAMES:
        for off in OFFSETS:
            for take in TAKES:
                out += [f"## {und}，离现价 {off:.1%}，{take} 倍止盈", "",
                        "| 昨天收盘时 | 天数 | " + " | ".join(RULE_ZH[r] for r in RULES) + " |",
                        "|---|---|" + "---|" * len(RULES)]
                for g in ORDER:
                    cells = [t.get(f"{und}|{off}|{take}|{g}|{r}") for r in RULES]
                    if not any(cells):
                        continue
                    n = max(c["n"] for c in cells if c)
                    txt = [f"{c['mean']:+.0%} / {c['hit']:.0%}" if c else "—" for c in cells]
                    out.append(f"| {g} | {n} | " + " | ".join(txt) + " |")
                out.append("")
    out += ["## $500 玩法（3 倍止盈）：到 $1,000 成功 / 归零，到 $10,000 成功 / 归零", "",
            "| 标的 | 离现价 | 昨天收盘时 | " + " | ".join(RULE_ZH[r] for r in RULES) + " |", "|---|---|---|" + "---|" * len(RULES)]
    for und in NAMES:
        for off in OFFSETS:
            for g in ("全部日子", "连跌 2 天以上", "连跌 3 天以上", "昨天跌 1% 以上"):
                cells = [t.get(f"{und}|{off}|3|{g}|{r}") for r in RULES]
                if not any(cells):
                    continue
                txt = [f"{c['t2']['heroes']}/{c['t2']['zeros']}，{c['t20']['heroes']}/{c['t20']['zeros']}" if c else "—" for c in cells]
                out.append(f"| {und} | {off:.1%} | {g} | " + " | ".join(txt) + " |")
    return "\n".join(out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca())
    t = table(rep)
    rep["table"] = t
    (root / "research" / f"{rep['generated']}-zdte-streak.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    (root / "research" / f"{rep['generated']}-zdte-streak.md").write_text(markdown(rep, t))
    print(markdown(rep, t))


if __name__ == "__main__":
    sys.exit(main())
