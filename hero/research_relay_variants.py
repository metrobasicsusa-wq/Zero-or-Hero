"""Research: how fragile is the relay rule (live from 2026-10-12) to its own choices?

research_hero (2026-10-05) gave the relay (all-in weekly earnings call ~10% out of the money on the
strongest 126-day momentum name, sold at the next open; at 3x the start, all-in SOXL while SMH is above
its 200-day average) about a 29% chance of 10x within 12 months. Here the same data, one choice at a
time: the strike, how the week's name is picked, the exit, the switch point, the trend leg; then two
checks on luck: 12-month windows starting in 2024 against 2025, and the result with the best weeks
removed (the three biggest lottery wins set to a total loss). Data and caveats as research_hero
(lottery since 2024-02, today's stock list: optimistic). Record only. Run: python -m hero.research_relay_variants
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

from hero.research_hero import HORIZON_DAYS, daily, latest, mixed, play, sequential

TARGET = 10
LIVE = {"strike": "价外 10%", "pick": "mom", "exit": "ret_open", "switch": 3.0, "trend": "SOXL"}
PICKS = {"mom": ("126 日动量最强（现行）", lambda t: t.get("mom126") or -9),
         "cheap": ("期权相对最便宜", lambda t: -(t.get("cheapness") or 9)),
         "implied": ("预期波动最大", lambda t: t.get("implied_move") or -9),
         "weak": ("126 日动量最弱（对照）", lambda t: -(t.get("mom126") or 9))}
EXITS = {"ret_open": "第二天开盘卖（现行）", "ret_reaction": "反应日收盘卖", "ret_tp3": "3 倍止盈否则到期", "ret_expiry": "拿到到期"}


def stream(lot: dict, strike: str, pick: str = "mom", exit_key: str = "ret_open", drop_best: int = 0) -> list[tuple[str, float]]:
    """One bet a week: the name the pick ranks first; drop_best turns the biggest wins into total losses."""
    key = PICKS[pick][1]
    best: dict[str, dict] = {}
    for t in lot["trades"]:
        if t["strategy"] != strike or t.get(exit_key) is None:
            continue
        y, w, _ = date.fromisoformat(t["reaction_day"]).isocalendar()
        k = f"{y}-{w}"
        if k not in best or key(t) > key(best[k]):
            best[k] = t
    out = sorted((t["reaction_day"], t[exit_key]) for t in best.values())
    for d, _ in sorted(out, key=lambda e: -e[1])[:drop_best]:
        out = [(x, -1.0 if x == d else r) for x, r in out]
    return out


def windows_by_year(s, first_day: str, last_day: str, target: float) -> dict[str, dict]:
    """12-month windows from each month start, grouped by the year the window starts in."""
    d = date.fromisoformat(first_day).replace(day=1) + timedelta(days=32)
    d = d.replace(day=1)
    out: dict[str, list[str]] = {}
    while d + timedelta(days=HORIZON_DAYS) <= date.fromisoformat(last_day):
        outcome, _, _ = play(s, (d - timedelta(days=1)).isoformat(), target, (d + timedelta(days=HORIZON_DAYS)).isoformat())
        for g in ("全部", str(d.year)):
            out.setdefault(g, []).append(outcome)
        d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return {g: {"n": len(v), "hero": round(v.count("hero") / len(v), 2), "zero": round(v.count("zero") / len(v), 2)}
            for g, v in out.items()}


def variants() -> list[tuple[str, dict]]:
    out = [("现行接力", {})]
    out += [(f"行权价：{s}", {"strike": s}) for s in ("价外 0%", "价外 5%", "价外 15%", "预期波动 1 倍处", "预期波动 1.5 倍处")]
    out += [(f"选股：{PICKS[p][0]}", {"pick": p}) for p in PICKS if p != "mom"]
    out += [(f"卖出：{EXITS[e]}", {"exit": e}) for e in EXITS if e != "ret_open"]
    out += [(f"转趋势：到 {x:g} 倍", {"switch": x}) for x in (2.0, 5.0)] + [("不转趋势（一直买彩票）", {"switch": None})]
    out += [("趋势腿换成 TQQQ", {"trend": "TQQQ"}), ("去掉最好的 3 周（改成全亏）", {"drop_best": 3}),
            ("去掉最好的 6 周（改成全亏）", {"drop_best": 6})]
    return out


def run(root: Path) -> dict:
    mom, lot = latest(root, "*-s500-aggressive.json"), latest(root, "*-earnings-lottery.json")
    last = mom["series_dates"][-1]
    rows = []
    for name, ch in variants():
        v = {**LIVE, "drop_best": 0, **ch}
        lotto = stream(lot, v["strike"], v["pick"], v["exit"], v["drop_best"])
        if not lotto:
            continue
        s = lotto if v["switch"] is None else mixed(lotto, daily(mom, v["trend"]), v["switch"])
        first = (date.fromisoformat(lotto[0][0]) - timedelta(days=1)).isoformat()
        seq = sequential(s, first, TARGET)
        wins = [r for _, r in lotto]
        rows.append({"variant": name, "weeks": len(lotto), "lotto_win": round(sum(r > 0 for r in wins) / len(wins), 2),
                     "lotto_mean": round(sum(wins) / len(wins), 3), "heroes": seq["heroes"], "zeros": seq["zeros"],
                     "windows": windows_by_year(s, first, last, TARGET)})
    return {"generated": date.today().isoformat(), "last_day": last, "target": TARGET, "rows": rows}


def markdown(rep: dict) -> str:
    years = sorted({g for r in rep["rows"] for g in r["windows"] if g != "全部"})
    out = [f"# 接力规则的变体：到 {rep['target']} 倍的机会有多稳 {rep['generated']}", "",
           f"数据同 2026-10-05 的 hero 研究（彩票 2024-02 起、用今天的股票名单，偏乐观；趋势腿数据到 {rep['last_day']}）。"
           "每次只改一项，其它照现行接力：价外 10% 看涨、126 日动量最强的那只、第二天开盘卖、到 3 倍转全仓 SOXL（SMH 在 200 日线上才拿）、亏 40% 算归零。"
           "「12 个月窗口」= 每月一个起点，12 个月内到 10 倍的比例 / 归零的比例；按起点年份分开看是不是只靠某一段行情。只研究，不改交易。", "",
           "| 变体 | 彩票周数 | 彩票赚钱周比例 | 彩票平均每周 | 连续尝试：成功 / 归零 | 12 个月窗口（全部） | "
           + " | ".join(f"起点在 {y} 年" for y in years) + " |",
           "|---|---|---|---|---|---|" + "---|" * len(years)]
    for r in rep["rows"]:
        w = r["windows"]
        cell = lambda g: f"{w[g]['hero']:.0%} / {w[g]['zero']:.0%}（{w[g]['n']}）" if g in w else "—"
        out.append(f"| {r['variant']} | {r['weeks']} | {r['lotto_win']:.0%} | {r['lotto_mean']:+.0%} | {r['heroes']} / {r['zeros']} | "
                   f"{cell('全部')} | " + " | ".join(cell(y) for y in years) + " |")
    return "\n".join(out) + "\n"


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    rep = run(root)
    (root / "research" / f"{rep['generated']}-relay-variants.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False) + "\n")
    (root / "research" / f"{rep['generated']}-relay-variants.md").write_text(markdown(rep))
    print(markdown(rep))


if __name__ == "__main__":
    sys.exit(main())
