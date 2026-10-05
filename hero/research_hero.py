"""Research: "zero or hero" — which bold play gives the best chance of turning $500 into 5x / 10x /
100x, accepting that most attempts go to zero (restarts are allowed)?

The objective is the probability of reaching the target, not the average return: in an
unfavourable game, bold play (concentrate, stay all-in) maximises the chance of a large target.

Strategies, all fully invested, built from earlier studies' data:
  - momentum 126-day / top 2 on the monthly dynamic pool (research_s500 daily returns),
    alone and at 3x / 5x daily leverage (a rough stand-in for using calls instead of shares:
    it ignores option time decay and volatility, so it flatters the levered versions);
  - TQQQ / SOXL while QQQ / SMH are above their 200-day average;
  - rolling lottery: all of the money on one earnings call a week (strongest momentum name,
    ~10% out of the money or at 1x the implied move, sold at the open), winnings rolled on;
  - mixed: the rolling lottery until the account triples, then SOXL trend with everything.
Each attempt starts at $500 and ends at the target (a hero) or at the experiment's 40% loss
line (zero; a fresh $500 attempt begins the next day). Lottery data covers 2024-02 onward and
comes from today's stock list, so it is optimistic. Record only. Run: python -m hero.research_hero
"""

from __future__ import annotations

import json
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

TARGETS = (5, 10, 100)
END_LOSS = 0.4
HORIZON_DAYS = 365


def latest(root: Path, pattern: str) -> dict:
    return json.loads(sorted((root / "research").glob(pattern))[-1].read_text())


def daily(mom: dict, key: str, lev: float = 1.0) -> list[tuple[str, float]]:
    return [(d, max(r * lev, -1.0)) for d, r in zip(mom["series_dates"], mom["series"][key])]


def lottery_stream(lot: dict, strike: str, exit_key: str = "ret_open") -> list[tuple[str, float]]:
    """One bet a week (strongest prior momentum), the whole account each time."""
    best: dict[str, dict] = {}
    for t in lot["trades"]:
        if t["strategy"] != strike or t.get(exit_key) is None:
            continue
        y, w, _ = date.fromisoformat(t["reaction_day"]).isocalendar()
        k = f"{y}-{w}"
        if k not in best or (t.get("mom126") or -9) > (best[k].get("mom126") or -9):
            best[k] = t
    return sorted(((t["reaction_day"], t[exit_key]) for t in best.values()))


def mixed(first: list[tuple[str, float]], then: list[tuple[str, float]], switch_at: float) -> dict:
    """A stream factory: play `first` until the attempt is up switch_at x, then `then`."""
    return {"first": first, "then": then, "switch_at": switch_at}


def play(stream, start: str, target: float, end_day: str | None = None, end_loss: float = END_LOSS):
    """Run one attempt from `start`. Returns (outcome, end date, multiple): outcome is
    'hero' (target reached), 'zero' (40% loss line) or 'open' (data/horizon ran out)."""
    bank = 1.0
    if isinstance(stream, dict):
        switched = False
        evs = [e for e in stream["first"] if e[0] > start]
        while evs:
            d, r = evs.pop(0)
            if end_day and d > end_day:
                break
            bank *= 1 + r
            if bank >= target:
                return "hero", d, bank
            if bank <= 1 - end_loss:
                return "zero", d, bank
            if not switched and bank >= stream["switch_at"]:
                switched = True
                evs = [e for e in stream["then"] if e[0] > d]
        return "open", None, bank
    for d, r in stream:
        if d <= start:
            continue
        if end_day and d > end_day:
            break
        bank *= 1 + r
        if bank >= target:
            return "hero", d, bank
        if bank <= 1 - end_loss:
            return "zero", d, bank
    return "open", None, bank


def sequential(stream, first_day: str, target: float, end_loss: float = END_LOSS) -> dict:
    """Restart after every hero or zero, back to back through the whole period."""
    start, heroes, zeros, days_to_hero = first_day, 0, 0, []
    while True:
        outcome, end, _ = play(stream, start, target, end_loss=end_loss)
        if outcome == "open":
            break
        if outcome == "hero":
            heroes += 1
            days_to_hero.append((date.fromisoformat(end) - date.fromisoformat(start)).days)
        else:
            zeros += 1
        start = end
    return {"heroes": heroes, "zeros": zeros, "attempts": heroes + zeros + 1,
            "median_days_to_hero": statistics.median(days_to_hero) if days_to_hero else None}


def windows(stream, first_day: str, last_day: str, target: float, end_loss: float = END_LOSS) -> dict:
    """Fresh attempt at each month start: chance of the target within 12 months."""
    d = date.fromisoformat(first_day).replace(day=1)
    out = []
    while d + timedelta(days=HORIZON_DAYS) <= date.fromisoformat(last_day):
        s = (d - timedelta(days=1)).isoformat()
        outcome, _, mult = play(stream, s, target, (d + timedelta(days=HORIZON_DAYS)).isoformat(), end_loss)
        out.append((outcome, mult))
        d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    n = len(out)
    return {"n": n, "p_hero": round(sum(o == "hero" for o, _ in out) / n, 2) if n else None,
            "p_zero": round(sum(o == "zero" for o, _ in out) / n, 2) if n else None}


def run(root: Path) -> dict:
    mom, lot = latest(root, "*-s500-aggressive.json"), latest(root, "*-earnings-lottery.json")
    core = "动态前 100|126|2"
    lot10 = lottery_stream(lot, "价外 10%")
    lot1x = lottery_stream(lot, "预期波动 1 倍处")
    soxl = daily(mom, "SOXL")
    strategies = {
        "动量 126 天持 2 只（全仓）": daily(mom, core),
        "动量 ×3（≈用期权放大，偏乐观）": daily(mom, core, 3),
        "动量 ×5（≈用期权放大，偏乐观）": daily(mom, core, 5),
        "TQQQ 趋势（全仓）": daily(mom, "TQQQ"),
        "SOXL 趋势（全仓）": soxl,
        "彩票全仓滚动：价外 10%": lot10,
        "彩票全仓滚动：预期波动 1 倍处": lot1x,
        "混合：彩票全仓到 3 倍，再全仓 SOXL 趋势": mixed(lot10, soxl, 3.0),
    }
    first_lot = lot10[0][0] if lot10 else mom["series_dates"][0]
    last = mom["series_dates"][-1]
    rows = []
    for name, stream in strategies.items():
        is_lot = "彩票" in name
        start = (date.fromisoformat(first_lot) - timedelta(days=1)).isoformat() if is_lot else mom["series_dates"][0]
        for t in TARGETS:
            rows.append({"strategy": name, "target": t, "data_from": start, **sequential(stream, start, t),
                         **{f"w_{k}": v for k, v in windows(stream, start, last, t).items()}})
    return {"generated": date.today().isoformat(), "end_loss": END_LOSS, "last_day": last, "rows": rows}


def markdown(rep: dict) -> str:
    out = [f"# Zero or Hero：哪种打法最有机会翻 5 / 10 / 100 倍 {rep['generated']}", "",
           f"每次尝试从 $500 开始，到达目标 = 英雄，亏损 {rep['end_loss']:.0%} = 归零（第二天用新的 $500 重来）。"
           "「连续尝试」= 从数据开头一直跑到最后，统计成功和失败次数；「12 个月窗口」= 每月一个起点，12 个月内到达目标的比例。"
           "彩票数据只有 2024-02 以后，且来自今天的股票名单，结果偏乐观；杠杆版本按每日收益乘倍数估算，忽略期权时间损耗，也偏乐观。只研究，不改交易。", ""]
    for t in (5, 10, 100):
        out += [f"## 目标 {t} 倍（$500 → ${500 * t:,}）", "",
                "| 打法 | 数据起点 | 连续尝试：成功 / 归零 | 成功一次平均要尝试 | 成功用时（中位天数） | 12 个月内成功的比例 | 12 个月内归零的比例 |",
                "|---|---|---|---|---|---|---|"]
        for r in [r for r in rep["rows"] if r["target"] == t]:
            per = f"{(r['heroes'] + r['zeros']) / r['heroes']:.1f} 次" if r["heroes"] else "—（没成功过）"
            days = f"{r['median_days_to_hero']:.0f}" if r["median_days_to_hero"] is not None else "—"
            pw = f"{r['w_p_hero']:.0%}" if r["w_p_hero"] is not None else "—"
            pz = f"{r['w_p_zero']:.0%}" if r["w_p_zero"] is not None else "—"
            out.append(f"| {r['strategy']} | {r['data_from']} | {r['heroes']} / {r['zeros']} | {per} | {days} | {pw} | {pz} |")
        out.append("")
    return "\n".join(out)


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    rep = run(root)
    (root / "research" / f"{rep['generated']}-hero.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False) + "\n")
    (root / "research" / f"{rep['generated']}-hero.md").write_text(markdown(rep))
    print(markdown(rep))


if __name__ == "__main__":
    sys.exit(main())
