"""Why a reversal day cannot be told at 10:00, and whether a change of course mid-session would help (2026-11-20).

The user, after a day when SPY was 0.5% above its open at 10:00 (three ledgers bought calls) and closed 3% lower:
"研究下为什么发现不了 而且我们是不是需要中间的变奏". Everything here is read from records already in the repository:
research/2026-11-17-zdte-times.json (SPY same-day options at real minute prices, 2024-02 on, an entry at 09:45, 10:00,
10:30, 11:00 and 12:00 in the direction of the move from the open at that time, 0.3% away, the ledgers' own exits)
and data/daybook/spy.json (each day's gap, close and 10:00 price). No keys, no orders.

The lines were written before the results were looked at (they are in the chat of 11-20 and in the report):

- A feature seen at 10:00 "counts" if the 10:00 trade's result per $1 differs by FEATURE_LINE or more between the days
  it is on and the days it is off, with the same sign in both halves of the sample and still there without the best 3
  days of the better side. Four features: the gap and the 10:00 move point opposite ways; the 10:00 move is just over
  the 0.5% line; the day before closed 1.5% or more lower; monthly option expiry.
- A change of course passes if its added trades, with 5% paid on each side, average a profit, in both halves, without
  their best 3 days, and there are at least NEED of them. Three changes: "flip" (the first of 10:30, 11:00, 12:00 at
  which SPY is on the other side of its open than at 10:00: buy that side there); "add" (at 12:00 the side has held and
  the move is larger than at 10:00: buy the same side again); "wait" (no trade at 10:00, one at 11:00 instead).

What these records cannot say: what selling the 10:00 position at the moment of the flip would have brought. That needs
the first contract's price at the later time, which the saved rows do not hold.
"""

from __future__ import annotations

import json
import statistics
from datetime import date
from pathlib import Path

from hero import robust

ROOT = Path(__file__).resolve().parent.parent
SOURCE = "research/2026-11-17-zdte-times.json"
LATER = ("10:30", "11:00", "12:00")
COST, FREE = "0.05/0.05", "0/0"
LIT = 0.005                 # the spy0dte ledgers trade only when SPY is this far from its open at 10:00
JUST_OVER = 0.007           # "just over the line": between LIT and this
GAP = 0.002                 # a gap worth the name
DROP = -0.015               # "the day before fell hard"
FEATURE_LINE, NEED, BEST = 0.15, 30, 3
FEATURES = {"gap_against": "缺口和 10:00 的方向相反（低开后翻红、高开后翻绿）", "just_over": "10:00 刚过线（离开盘 0.5%–0.7%）",
            "after_drop": "前一天收跌 1.5% 以上", "opex": "月度期权到期日"}


def load(root: Path = ROOT) -> tuple[dict[str, dict[str, dict]], dict[str, dict]]:
    """day -> time -> the saved entry; day -> the day archive's card with the previous day's close-to-close move added."""
    days: dict[str, dict[str, dict]] = {}
    for r in json.loads((root / SOURCE).read_text())["rows"]:
        days.setdefault(r["day"], {})[r["t"]] = r
    cards, prev = {}, None
    for c in json.loads((root / "data" / "daybook" / "spy.json").read_text())["cards"]:
        cards[c["day"]] = {**c, "prev_move": prev["c"] / prev["pc"] - 1 if prev and prev.get("pc") else None}
        prev = c
    return days, cards


def side(move: float | None) -> int:
    return 0 if not move else (1 if move > 0 else -1)


def monthly(day: str) -> bool:
    d = date.fromisoformat(day)
    return d.weekday() == 4 and 15 <= d.day <= 21


def flip(times: dict[str, dict]) -> dict | None:
    """The first later entry made on the other side of the open than the 10:00 entry."""
    first = side(times.get("10:00", {}).get("move"))
    return next((times[t] for t in LATER if t in times and first and side(times[t]["move"]) == -first), None) if first else None


def traded(days: dict, lit: bool) -> list[str]:
    """The days the 10:00 entry is made: every day (the near ledgers) or only 0.5% or more from the open (the spy0dte ledgers)."""
    return sorted(d for d, ts in days.items() if "10:00" in ts and side(ts["10:00"]["move"]) and (not lit or abs(ts["10:00"]["move"]) >= LIT))


def flip_rates(days: dict, which: list[str]) -> dict:
    """Of the days traded at 10:00: on how many SPY was on the other side of its open by each later time (cumulative)."""
    out, seen = {"n": len(which)}, set()
    for t in LATER:
        seen |= {d for d in which if t in days[d] and side(days[d][t]["move"]) == -side(days[d]["10:00"]["move"])}
        out[t] = len(seen)
    return out


def features(day: str, times: dict, card: dict | None) -> dict[str, bool]:
    m = times["10:00"]["move"]
    gap, prev = (card or {}).get("gap"), (card or {}).get("prev_move")
    return {"gap_against": gap is not None and abs(gap) >= GAP and side(gap) == -side(m), "just_over": LIT <= abs(m) < JUST_OVER,
            "after_drop": prev is not None and prev <= DROP, "opex": monthly(day)}


def halves(xs: list[tuple[str, float]]) -> tuple[float | None, float | None]:
    xs = sorted(xs)
    a, b = xs[:len(xs) // 2], xs[len(xs) // 2:]
    return (statistics.mean(r for _, r in a) if a else None, statistics.mean(r for _, r in b) if b else None)


def feature_row(days: dict, cards: dict, which: list[str], name: str, cost: str = COST) -> dict:
    """The 10:00 trade on the days a feature is on against the days it is off, and whether the difference clears the line."""
    on = [(d, days[d]["10:00"]["res"][cost]) for d in which if features(d, days[d], cards.get(d))[name]]
    off = [(d, days[d]["10:00"]["res"][cost]) for d in which if not features(d, days[d], cards.get(d))[name]]
    out = {"on_n": len(on), "off_n": len(off), "on": statistics.mean(r for _, r in on) if on else None,
           "off": statistics.mean(r for _, r in off) if off else None,
           "on_flip": sum(bool(flip(days[d])) for d, _ in on) / len(on) if on else None,
           "off_flip": sum(bool(flip(days[d])) for d, _ in off) / len(off) if off else None}
    if len(on) < NEED or not off:
        return {**out, "diff": None, "verdict": "样本不够"}
    diff = out["on"] - out["off"]
    cut = sorted(d for d, _ in on + off)[(len(on) + len(off)) // 2]
    parts = []
    for keep in (lambda d: d < cut, lambda d: d >= cut):
        a, b = [r for d, r in on if keep(d)], [r for d, r in off if keep(d)]
        parts.append(statistics.mean(a) - statistics.mean(b) if a and b else None)
    better, worse = (on, off) if diff > 0 else (off, on)
    trimmed = sorted(r for _, r in better)[:-BEST]
    rest = (statistics.mean(trimmed) - statistics.mean(r for _, r in worse)) * (1 if diff > 0 else -1) if trimmed else None
    ok = abs(diff) >= FEATURE_LINE and all(p is not None and p * diff > 0 for p in parts) and rest is not None and rest * diff > 0 and abs(rest) >= FEATURE_LINE / 2
    return {**out, "diff": diff, "halves": parts, "rest": rest, "verdict": "算数" if ok else "不算数"}


def change(days: dict, which: list[str], kind: str, cost: str = COST) -> list[tuple[str, float]]:
    """The trades a change of course adds (flip, add) or makes instead (wait), as (day, result per $1)."""
    out = []
    for d in which:
        ts = days[d]
        if kind == "flip":
            f = flip(ts)
            if f:
                out.append((d, f["res"][cost]))
        elif kind == "add":
            a, b = ts["10:00"]["move"], ts.get("12:00", {}).get("move")
            if b is not None and side(a) == side(b) and abs(b) > abs(a):
                out.append((d, ts["12:00"]["res"][cost]))
        elif kind == "wait" and "11:00" in ts and side(ts["11:00"]["move"]):
            out.append((d, ts["11:00"]["res"][cost]))
    return out


def verdict(xs: list[tuple[str, float]]) -> dict:
    """The pass line for a change of course: a profit on average, in both halves, without the best 3, on NEED trades or more."""
    if len(xs) < NEED:
        return {"n": len(xs), "mean": statistics.mean(r for _, r in xs) if xs else None, "pass": False, "why": f"只有 {len(xs)} 笔，不到 {NEED} 笔"}
    v = sorted(r for _, r in xs)
    a, b = halves(xs)
    mean, rest = statistics.mean(v), statistics.mean(v[:-BEST])
    fails = [w for w, bad in (("平均是亏的", mean <= 0), ("前半段是亏的", a <= 0), ("后半段是亏的", b <= 0), (f"去掉最好 {BEST} 天是亏的", rest <= 0)) if bad]
    return {"n": len(xs), "mean": mean, "halves": (a, b), "rest": rest, "pass": not fails, "why": "；".join(fails) or "四条都过"}


def disagree(days: dict, cost: str = COST) -> dict:
    """Not one of the lines written beforehand, a lead only: the days the 09:45 side and the 10:00 side differ (today the
    09:40 ledger bought puts and the 10:00 ledgers calls), and how each of the two entries did."""
    both = [d for d, ts in days.items() if "09:45" in ts and "10:00" in ts and side(ts["09:45"]["move"]) * side(ts["10:00"]["move"]) == -1]
    return {"n": len(both), "share": len(both) / len(days), "early": statistics.mean(days[d]["09:45"]["res"][cost] for d in both) if both else None,
            "late": statistics.mean(days[d]["10:00"]["res"][cost] for d in both) if both else None,
            "early_won": sum(days[d]["09:45"]["res"][cost] > days[d]["10:00"]["res"][cost] for d in both) / len(both) if both else None}


TODAY = ("开盘 713.56（低开 0.32%）。09:40 在开盘价下方一点（713.28），那个账本买了 5 张看跌 710，最后赚了约 4.9 倍。"
         "10:00 是 717.19（比开盘高 0.51%，刚过 0.5% 的线），**当天的最高点 718.29 就出在 10:00 这一分钟**，三个账本在这里买了 44 张看涨 721，全部归零。"
         "10:16 跌回开盘价下方；10:30 是 −0.43%，11:00 −0.82%，12:00 −1.31%，15:30 −2.93%。"
         "按下面「反手」的写法，今天会在 10:30 买看跌（标普 710.47），拿到 15:30（692.68）：今天这一笔会大赚，下面看的是 700 天里这样做平均怎样。")


def run(root: Path = ROOT) -> dict:
    days, cards = load(root)
    out = {"source": SOURCE, "days": len(days), "start": min(days), "end": max(days), "disagree": disagree(days), "bases": {}}
    for key, lit in (("every", False), ("lit", True)):
        which = traded(days, lit)
        first = [(d, days[d]["10:00"]["res"][COST]) for d in which]
        flipped = {d for d in which if flip(days[d])}
        base = {"n": len(which), "first": statistics.mean(r for _, r in first), "first_free": statistics.mean(days[d]["10:00"]["res"][FREE] for d in which),
                "flips": flip_rates(days, which),
                "first_on_flip": statistics.mean(r for d, r in first if d in flipped) if flipped else None,
                "first_no_flip": statistics.mean(r for d, r in first if d not in flipped) if len(flipped) < len(which) else None,
                "features": {name: feature_row(days, cards, which, name) for name in FEATURES}, "changes": {}, "trades": {}}
        for kind in ("flip", "add", "wait"):
            xs = change(days, which, kind)
            base["changes"][kind] = {**verdict(xs), "free": statistics.mean(r for _, r in change(days, which, kind, FREE)) if xs else None}
            base["trades"][kind] = xs
        base["trades"]["first"] = first
        out["bases"][key] = base
    return out


def pct(x: float | None) -> str:
    return "—" if x is None else f"{x:+.0%}".replace("-", "−")


def per(x: float | None) -> str:
    return "—" if x is None else f"{x:+.2f}".replace("-", "−")


BASES = {"every": "每天 10:00 都做（09:40 / 10:00 那两个「离现价 0.3%」账本的做法）", "lit": "只在 10:00 离开盘 0.5% 以上才做（今天亏的那三个账本的做法）"}
CHANGES = {"flip": "反手：10:30、11:00、12:00 里第一次到了开盘价另一边，就买那一边", "add": "加一枪：12:00 方向没变、走得比 10:00 更远，顺势再买",
           "wait": "等一等：10:00 不买，11:00 再按那时的方向买"}


def report(rep: dict, today: dict | None = None) -> str:
    out = ["# 为什么 10:00 看不出今天会反过来，要不要中途变（2026-11-20）", "",
           "模拟盘研究，只记录，不是投资建议。没有改任何规则。", "",
           "用户 11-20：标普 10:00 比开盘高 0.5%，三个账本顺势买了看涨，之后跌了 3%，44 张归零；「今天所有买末日 put 都赚爆了」「研究下为什么发现不了 而且我们是不是需要中间的变奏」。", "",
           f"数据：`{rep['source']}`，标普当天到期期权的真实分钟价，{rep['start']} 到 {rep['end']}，{rep['days']} 个交易日；每天 09:45、10:00、10:30、11:00、12:00 各有一笔"
           "「顺着那一刻相对开盘价的方向买、离现价 0.3%、账本自己的卖法」的记录。下面的每 $1 都带买卖各 5% 的成本，括号里是不计成本的。线在看结果之前写死（见 `hero/research_zdte_flip.py` 开头）。", ""]
    if today:
        out += ["## 今天发生了什么", "", today["text"], ""]
    out += ["## 一、为什么发现不了", "",
            "先说已经查过的（[11-16 的研究](2026-11-16-zdte-detect.md)，十年、十条信号）：10:00 之后接着走还是反过来，每一档波动里都是五五开，十条信号没有一条能认出方向，能认出的只有「今天容易是大日子」。", "",
            "这次多看一步：10:00 在开盘价哪一边，之后翻到另一边的有多少。", "",
            "| 做法 | 天数 | 到 10:30 翻了 | 到 11:00 | 到 12:00 | 10:00 那笔：翻了的日子 | 没翻的日子 |", "|---|---|---|---|---|---|---|"]
    for key, b in rep["bases"].items():
        f = b["flips"]
        out.append(f"| {BASES[key]} | {b['n']} | {f['10:30']}（{f['10:30'] / b['n']:.0%}） | {f['11:00']}（{f['11:00'] / b['n']:.0%}） | {f['12:00']}（{f['12:00'] / b['n']:.0%}） | "
                   f"{per(b['first_on_flip'])} | {per(b['first_no_flip'])} |")
    out += ["", "四个事先定的特征，能不能在 10:00 把要翻的日子分出来（看 10:00 那一笔每 $1 的结果差不差 0.15 以上）：", ""]
    for key, b in rep["bases"].items():
        out += [f"**{BASES[key]}**", "", "| 特征 | 亮：天数 / 每 $1 / 之后翻边的比例 | 不亮：天数 / 每 $1 / 翻边的比例 | 差（前半 / 后半；去掉最好 3 天） | 结论 |", "|---|---|---|---|---|"]
        for name, r in b["features"].items():
            hv = "—" if r.get("halves") is None else f"{per(r['halves'][0])} / {per(r['halves'][1])}；{per(r.get('rest'))}"
            out.append(f"| {FEATURES[name]} | {r['on_n']} / {per(r['on'])} / {'—' if r['on_flip'] is None else format(r['on_flip'], '.0%')} | "
                       f"{r['off_n']} / {per(r['off'])} / {'—' if r['off_flip'] is None else format(r['off_flip'], '.0%')} | {per(r['diff'])}（{hv}） | {r['verdict']} |")
        out.append("")
    out += ["## 二、要不要中间的变奏", "",
            "及格线：新增的这些单子平均是赚的、前后两半都赚、去掉最好 3 天还赚、至少 30 笔。", ""]
    for key, b in rep["bases"].items():
        out += [f"**{BASES[key]}**：10:00 那一笔本身每 $1 是 {per(b['first'])}（不计成本 {per(b['first_free'])}），{b['n']} 天。", "",
                "| 变奏 | 笔数 | 每 $1（不计成本） | 前半 / 后半 | 去掉最好 3 天 | 过线吗 |", "|---|---|---|---|---|---|"]
        for kind, c in b["changes"].items():
            hv = "—" if not c.get("halves") else f"{per(c['halves'][0])} / {per(c['halves'][1])}"
            out.append(f"| {CHANGES[kind]} | {c['n']} | {per(c['mean'])}（{per(c.get('free'))}） | {hv} | {per(c.get('rest'))} | {'**过**' if c['pass'] else '不过'}：{c['why']} |")
        out.append("")
        out.append(robust.table({"10:00 那一笔": robust.summary(b["trades"]["first"], end=rep["end"]),
                                 **{CHANGES[k].split("：")[0]: robust.summary(b["trades"][k], end=rep["end"]) for k in ("flip", "add", "wait")}},
                                title="稳健性检查（每 $1，带成本）"))
        out.append("")
    out += ["**把 10:00 的单子砍掉呢？** 这个 11-02 查过（[同日期权要不要止损](2026-11-02-zdte-stop.md)，6,835 张，真实分钟价）："
            "「标的跌回开盘价就卖」在标普 0.3% 上是每 $1 −15%，现行（不止损）是 −18%；归零少两成，到 20 倍的少一成。平均好一点点，不改结论，当时没改规则。", "",
            "## 顺手看的（不在事先写的线里，只当线索）", ""]
    g = rep["disagree"]
    out += [f"今天 09:40 和 10:00 两个时点方向相反，早的那个对了。700 天里 09:45 和 10:00 方向相反的有 {g['n']} 天（{g['share']:.0%}）："
            f"09:45 那一笔每 $1 {per(g['early'])}，10:00 那一笔 {per(g['late'])}，早的那笔比晚的好的占 {g['early_won']:.0%}。" if g["n"] else "没有方向相反的日子。", "",
            "## 这些记录答不了的", "",
            "- 「翻边的那一刻把 10:00 的单子卖掉」能省多少：要那张合约在 10:30 / 11:00 / 12:00 的价，存下来的记录里没有。要查得在工作流里用分钟期权价另跑。",
            "- 只在标普上查了。QQQ、IWM 存下来的只有 10:00 那一笔，没有后面几个时点。一条变奏要变成规则，先换标的再验（CLAUDE.md ⑥）。",
            "- 检查点只有 10:30、11:00、12:00 三个。今天真正掉头的时间在它们之间，「盯着每一分钟」的做法这里测不了。"]
    return "\n".join(out) + "\n"


def main() -> None:
    rep = run()
    text = report(rep, {"text": TODAY})
    out = ROOT / "research"
    (out / "2026-11-20-zdte-flip.md").write_text(text)
    slim = {**rep, "bases": {k: {x: v for x, v in b.items() if x != "trades"} for k, b in rep["bases"].items()}}
    (out / "2026-11-20-zdte-flip.json").write_text(json.dumps(slim, ensure_ascii=False, indent=1) + "\n")
    print(text)


if __name__ == "__main__":
    main()
