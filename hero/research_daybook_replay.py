"""The last three months walked through day by day with the daybook (owner, 11-19: walk the past three months).

For every session: what the daybook (hero/daybook.py) would have read at the open and at 10:00 from the days before
it, what the day then did, and what the 10:00 follow trade made on real option prices (the card's "一定要跟" trade: a
same-day option 0.3% beyond the 10:00 price in the direction of the move since the open, half sold at 3x, the rest
trailed 40% off its best after a double, else 15:30, 5% costs each way; research/2026-11-17-zdte-times.json).

Two flags, fixed before any of this was looked at: the daybook "says a big day" when its open read of "closes more
than 1% from yesterday's close" is FLAG_OPEN or more (the plain share is 24%), and "says more to come" when its 10:00
read of "moves 0.5% or more either way from 10:00 to the close" is FLAG_1000 or more (plain share 35%).

Three months is a walk, not a verdict: the follow trade by flag is also shown for every day the real prices cover
(since 2024-02), against the line used on 11-17 for every other day-signal (research_zdte_lines.verdict: +0.15 per $1
over the other days, above zero in both halves, still above zero without the three best flagged days, 60 flagged days).
Record only. Run: python -m hero.research_daybook_replay
"""

from __future__ import annotations

import json
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

from hero import daybook as db
from hero import robust
from hero.research_ideas import save
from hero.research_zdte_lines import verdict

ROOT = Path(__file__).resolve().parent.parent
TIMES = ROOT / "research" / "2026-11-17-zdte-times.json"
FLAG_OPEN, FLAG_1000 = 0.40, 0.50
COST = "0.05/0.05"
MONTHS = 92          # calendar days


def follow() -> dict[str, dict]:
    """day -> the 10:00 follow trade on real option prices: which side and what $1 made."""
    rows = json.loads(TIMES.read_text())["rows"]
    return {r["day"]: {"kind": r["kind"], "ret": r["res"][COST]} for r in rows if r["t"] == "10:00" and COST in r.get("res", {})}


def walk(book: db.Book, first: str) -> list[dict]:
    """Each day from `first`: the reads made from the days before it, and what the day did."""
    run = {q: [0, 0] for q in db.OUTCOMES}
    out = []
    for i, c in enumerate(book.cards):
        ys = {q: f(c) for q, (_, f) in db.OUTCOMES.items()}
        if c["day"] >= first:
            lab = book.labels[i]
            row = {"day": c["day"], "lab": lab, "gap": c["gap"], "m10": c.get("m10"), "ret": c["c"] / c["pc"] - 1, "oc": c["c"] / c["o"] - 1,
                   "range": (c["h"] - c["l"]) / c["pc"], "after10": None if c.get("p1000") is None else c["c"] / c["p1000"] - 1, "y": ys}
            for at in db.KEYS:
                if all(k in lab for k in db.KEYS[at]):
                    r = book.read(lab, at, i, {q: tuple(run[q]) for q in db.ASKED[at]})
                    row[at] = {q: (v["p"], v["base"]) for q, v in r["q"].items()}
                    row[at + "_days"] = r["days"]
            out.append(row)
        for q, y in ys.items():
            if y is not None:
                run[q][0] += y
                run[q][1] += 1
    return out


def pct(x, d: int = 1) -> str:
    return "—" if x is None else f"{x:+.{d}%}"


def tally(rows: list[dict], at: str, q: str, flag: float) -> dict:
    """How often the question came true on the flagged days and on the others, and the Brier score against the base."""
    rs = [r for r in rows if at in r and r["y"][q] is not None]
    on, off = [r for r in rs if r[at][q][0] >= flag], [r for r in rs if r[at][q][0] < flag]
    share = lambda xs: (sum(r["y"][q] for r in xs), len(xs))
    brier = lambda j: statistics.mean((r[at][q][j] - r["y"][q]) ** 2 for r in rs) if rs else None
    return {"n": len(rs), "on": share(on), "off": share(off), "brier": brier(0), "brier_base": brier(1)}


def trade(rows: list[dict], fl: dict[str, dict], keep) -> list[tuple[str, float]]:
    return [(r["day"], fl[r["day"]]["ret"]) for r in rows if r["day"] in fl and keep(r)]


def other_indexes(full: list[dict], flag, path: Path | None = None) -> dict[str, dict]:
    """The same flag days on QQQ and IWM: the 10:00 follow trade there (same-day expiries only), against the line."""
    path = path or ROOT / "research" / "2026-11-17-zdte-lines.json"
    if not path.exists():
        return {}
    by = {r["day"]: r for r in full}
    out = {}
    for und in ("QQQ", "IWM"):
        rs = [x for x in json.loads(path.read_text())["rows"] if x["und"] == und and x.get("dte") == 0 and COST in x["res"] and x["day"] in by]
        v = verdict([(x["day"], x["res"][COST]) for x in rs if flag(by[x["day"]])], [(x["day"], x["res"][COST]) for x in rs if not flag(by[x["day"]])])
        if v:
            out[und] = v
    return out


GROUPS = {
    "每天都做": lambda r: True,
    "10:00 离开盘 0.5% 以上（现在的条件）": lambda r: r["m10"] is not None and abs(r["m10"]) >= 0.005,
    f"档案开盘时说「会大」（{FLAG_OPEN:.0%} 以上）": lambda r: "open" in r and r["open"]["big"][0] >= FLAG_OPEN,
    f"档案 10:00 说「还会动」（{FLAG_1000:.0%} 以上）": lambda r: "1000" in r and r["1000"]["move10"][0] >= FLAG_1000,
}


def frac(kn: tuple[int, int]) -> str:
    return f"{kn[0]} / {kn[1]}" + (f"（{kn[0] / kn[1]:.0%}）" if kn[1] else "")


def report(rep: dict) -> str:
    rows, fl = rep["rows"], rep["follow"]
    recent = [r for r in rows if r["day"] >= rep["since"]]
    out = [f"# 把过去三个月逐日走一遍（{rep['generated']}）", "",
           f"{recent[0]['day']} 到 {recent[-1]['day']}，{len(recent)} 个交易日。每一天：档案只用它之前的日子，在开盘和 10:00 各读一次；再看当天实际怎么走，"
           "以及「10:00 顺着开盘以来的方向买」那张单按真实期权价的结果（当天到期、离现价 0.3%、一半 3 倍卖、另一半翻倍后回撤四成卖、最晚 15:30，买卖各 5% 成本）。"
           f"两条「点名」的线是看结果之前写死的：开盘时读数 {FLAG_OPEN:.0%} 以上算「会大」，10:00 读数 {FLAG_1000:.0%} 以上算「还会动」。只记录，不据此下单。", "",
           "## 一、三个月合计", ""]
    a, b = tally(recent, "open", "big", FLAG_OPEN), tally(recent, "1000", "move10", FLAG_1000)
    up = [r for r in recent if "open" in r]
    hit = sum((r["open"]["up"][0] > 0.5) == r["y"]["up"] for r in up)
    out += ["| 档案的话 | 点名的日子里说中的 | 没点名的日子里发生的 | 档案 Brier / 只报底数 |", "|---|---|---|---|",
            f"| 开盘时：今天收盘对昨收会超过 1% | {frac(a['on'])} | {frac(a['off'])} | {a['brier']:.3f} / {a['brier_base']:.3f} |",
            f"| 10:00：到收盘不分方向还会动 0.5% 以上 | {frac(b['on'])} | {frac(b['off'])} | {b['brier']:.3f} / {b['brier_base']:.3f} |", "",
            f"方向（开盘时猜收盘在开盘价上方还是下方）：{len(up)} 天对 {hit} 天（{hit / len(up):.0%}）；这一问走十年历史也不比底数准，列在这里只为对照。", "",
            "「10:00 顺着方向买」那张单，按哪些日子做（三个月，真实期权价）：", ""]
    out.append(robust.table({name: robust.summary(trade(recent, fl, keep)) for name, keep in GROUPS.items()}, "三个月里的每 $1 收益").split("\n", 2)[2])
    full = [r for r in rows if r["day"] in fl]
    out += ["", f"## 二、三个月太短，同一件事放到真实期权价覆盖的全部日子（{full[0]['day']} 到 {full[-1]['day']}，{len(full)} 天）", "",
            "及格线和 11-17 查别的信号用的是同一条：点名的日子比其余日子每 $1 多 0.15 以上、前后两半都为正、去掉最好 3 天仍为正、点名至少 60 天。", "",
            "| 按什么点名 | 点名的天数 | 点名的日子每 $1 | 其余日子每 $1 | 差（前半 / 后半） | 去掉最好 3 天后的差 | 过线 |", "|---|---|---|---|---|---|---|"]
    verdicts = {}
    for name, keep in list(GROUPS.items())[1:]:
        on, off = trade(full, fl, keep), trade(full, fl, lambda r, keep=keep: not keep(r))
        v = verdicts[name] = verdict(on, off)
        if not v:
            continue
        num = lambda x: "—" if x is None else f"{x:+.2f}"
        out.append(f"| {name} | {v['on_n']} | {num(v['on_mean'])} | {num(v['off_mean'])} | {num(v['diff'])}（{num(v['halves'][0])} / {num(v['halves'][1])}） | "
                   f"{num(v['minus3'])} | {'过' if v['pass'] else '不过'} |")
    names = list(GROUPS)
    cond, flag = GROUPS[names[1]], GROUPS[names[3]]
    split = {**GROUPS, "　档案 10:00 点名、也满足现在的条件": lambda r: flag(r) and cond(r),
             "　只有档案 10:00 点名（离开盘不到 0.5%）": lambda r: flag(r) and not cond(r)}
    out += ["", robust.table({name: robust.summary(trade(full, fl, keep)) for name, keep in split.items()}, "全部日子的稳健性检查").split("\n", 2)[2], "",
            "最后两行把「档案 10:00 点名」的日子拆开：一部分本来就满足现在的条件，另一部分是档案多点出来的。多点出来的那些如果不赚，过线就只是现在的条件换了个名字。", ""]
    others = other_indexes(full, flag)
    if others:
        out += ["同一个点名放到别的指数（SPY 的档案点名，那天在 QQQ、IWM 上做同样的单；research/2026-11-17-zdte-lines.json）：", "",
                "| 指数 | 点名的天数 | 点名的日子每 $1 | 其余日子每 $1 | 差（前半 / 后半） | 去掉最好 3 天后的差 | 过线 |", "|---|---|---|---|---|---|---|"]
        num = lambda x: "—" if x is None else f"{x:+.2f}"
        out += [f"| {u} | {v['on_n']} | {num(v['on_mean'])} | {num(v['off_mean'])} | {num(v['diff'])}（{num(v['halves'][0])} / {num(v['halves'][1])}） | "
                f"{num(v['minus3'])} | {'过' if v['pass'] else '不过'} |" for u, v in others.items()]
    out += ["",
            "## 三、逐日（新的在前）", "",
            "「会大」「还会动」两列是档案的读数，加粗 = 点名；后面一列是当天实际。最后两列是 10:00 那张单：买的是看涨还是看跌、每 $1 的结果。", "",
            "| 日期 | 开盘时的样子 | 会大 | 实际对昨收 | 高低差 | 10:00 对开盘 | 还会动 | 10:00 到收盘 | 10:00 的单 | 每 $1 |", "|---|---|---|---|---|---|---|---|---|---|"]
    for r in reversed(recent):
        lab = r["lab"]
        p1 = r.get("open", {}).get("big", (None,))[0]
        p2 = r.get("1000", {}).get("move10", (None,))[0]
        bold = lambda p, f: "—" if p is None else (f"**{p:.0%}**" if p >= f else f"{p:.0%}")
        t = fl.get(r["day"])
        out.append(f"| {r['day'][5:]} | {lab['gap']}、{lab['vol']} | {bold(p1, FLAG_OPEN)} | {pct(r['ret'])}{' ✓' if r['y']['big'] else ''} | {r['range']:.1%} | "
                   f"{pct(r['m10'], 2)} | {bold(p2, FLAG_1000)} | {pct(r['after10'])}{' ✓' if r['y']['move10'] else ''} | "
                   f"{'—' if not t else '看涨' if t['kind'] == 'C' else '看跌'} | {'—' if not t else format(t['ret'], '+.2f')} |")
    last = max(fl)
    out += ["", f"- ✓ = 那一问当天发生了（收盘对昨收超过 1%；10:00 到收盘动了 0.5% 以上）。",
            f"- 10:00 那张单的真实期权价只算到 {last}，之后的日子留空。"]
    return "\n".join(out) + "\n", verdicts


def main() -> None:
    book = db.Book(db.load())
    fl = follow()
    since = (date.fromisoformat(book.cards[-1]["day"]) - timedelta(days=MONTHS)).isoformat()
    rows = walk(book, min(min(fl), since))
    rep = {"generated": date.today().isoformat(), "since": since, "rows": rows, "follow": fl}
    md, verdicts = report(rep)
    slim = [{k: v for k, v in r.items() if k != "y"} | {"y": {q: (None if y is None else bool(y)) for q, y in r["y"].items()}} for r in rows if r["day"] >= since]
    save("daybook-replay", {"generated": rep["generated"], "since": since, "flags": [FLAG_OPEN, FLAG_1000], "verdicts": verdicts, "rows": slim}, md)


if __name__ == "__main__":
    sys.exit(main())
