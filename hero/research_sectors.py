"""Do sectors have to be kept apart in a backtest? (owner, 2026-11-20: 「你的回测中 板块要分开吗」)

The night before, a study of entries made "in a decline" (under the 50-day average and 10% or more under the
one-year high) had used SPY, QQQ and seven megacaps: one sector, ten years, one long bear market. This one reads
shares only (no option prices to estimate), split- and dividend-adjusted closes from Yahoo, since 1999 where the
fund is that old: three indexes and fourteen sector funds (the same list the scout uses). It asks three things:

1. Entering a fund while it is in a decline: three months and a year later, against any day. And the same split by
   whether the market (SPY) was in a decline that day too ("it falls with the market") or not ("it falls alone").
2. Entering a fund 25% or more under its high: inside each of the three bear markets and outside them, and which
   funds were each bear market's epicentre (the deepest under their highs during it).
3. Technology falling alone (XLK in a decline, SPY not): 2000-02 against the years since.

Every day is a sample and the samples overlap, so each line also says how many separate stretches it comes from,
and the robustness table is fed one entry a stretch (its first day), not one a day. Exploration: a lead, not a rule.
"""

from __future__ import annotations

import json
import statistics
import time
from datetime import date
from pathlib import Path

from hero import robust, scout

ROOT = Path(__file__).resolve().parent.parent
DEEP = scout.DEEP
ERAS = {"2000-02": ("2000-09", "2002-10"), "2008": ("2007-11", "2009-02"), "2022": ("2022-01", "2022-12")}
GAP = 45                      # calendar days without a sample before a new stretch begins (the scout's)
FEW = 3                       # stretches: fewer than this and a line is not summarised


def load(sleep=time.sleep) -> dict[str, list[tuple[str, float]]]:
    out = {}
    for f in scout.FUNDS:
        out[f] = [(d, a) for d, _, a, _ in scout.fetch(f)]
        sleep(0.15)
    return out


def era(day: str) -> str | None:
    return next((name for name, (a, b) in ERAS.items() if a <= day[:7] <= b), None)


def firsts(rows: list[dict], gap: int = GAP) -> list[dict]:
    """The first day of each separate stretch of samples."""
    out, prev = [], None
    for r in sorted(rows, key=lambda r: r["day"]):
        if prev is None or (date.fromisoformat(r["day"]) - date.fromisoformat(prev)).days > gap:
            out.append(r)
        prev = r["day"]
    return out


def brief(rows: list[dict], key: str) -> dict:
    """A group of days: how many, in how many stretches, the mean and the share up over `key` (m3 or y1), and the
    same over one entry a stretch."""
    xs = [r[key] for r in rows if r[key] is not None]
    one = [r[key] for r in firsts(rows) if r[key] is not None]
    return {"days": len(xs), "stretches": len(firsts(rows)), "mean": statistics.mean(xs) if xs else None,
            "up": sum(x > 0 for x in xs) / len(xs) if xs else None,
            "first_mean": statistics.mean(one) if len(one) >= FEW else None, "first_up": sum(x > 0 for x in one) / len(one) if len(one) >= FEW else None,
            "first_n": len(one)}


def declines(rows: dict[str, list[dict]]) -> dict[str, dict]:
    """Per fund: entering in a decline against any day, and the decline days split by the market's own state."""
    out = {}
    for f, rs in rows.items():
        down = [r for r in rs if r["env"] == "down"]
        out[f] = {"any": {k: brief(rs, k) for k in ("m3", "y1")}, "down": {k: brief(down, k) for k in ("m3", "y1")},
                  "with": {k: brief([r for r in down if r["market_down"]], k) for k in ("m3", "y1")},
                  "alone": {k: brief([r for r in down if not r["market_down"]], k) for k in ("m3", "y1")}}
    return out


def depths(rows: dict[str, list[dict]]) -> dict[str, dict[str, float]]:
    """How far under its one-year high each fund got inside each bear market (the lowest reading)."""
    out: dict[str, dict[str, float]] = {name: {} for name in ERAS}
    for f, rs in rows.items():
        for name in ERAS:
            inside = [r["dd"] for r in rs if era(r["day"]) == name]
            if inside:
                out[name][f] = min(inside)
    return out


def epicentres(depth: dict[str, dict[str, float]], n: int = 3) -> dict[str, list[str]]:
    """Each bear market's epicentre: the n sector funds that got deepest under their highs during it."""
    return {name: [f for f in sorted((f for f in d if f in scout.SECTORS), key=lambda f: d[f])[:n]] for name, d in depth.items()}


def deep(rows: dict[str, list[dict]]) -> dict[str, dict]:
    """Per fund: entering 25% or more under the high, inside each bear market and outside them all, a year later."""
    out = {}
    for f, rs in rows.items():
        d = [r for r in rs if r["dd"] <= DEEP]
        if d:
            out[f] = {**{name: brief([r for r in d if era(r["day"]) == name], "y1") for name in ERAS},
                      "other": brief([r for r in d if era(r["day"]) is None], "y1")}
    return out


def tech_alone(rows: dict[str, list[dict]], fund: str = "XLK") -> dict[str, dict]:
    """Technology in a decline while the market is not: the 2000-02 bear market against everything since."""
    alone = [r for r in rows.get(fund, []) if r["env"] == "down" and not r["market_down"]]
    return {"2000-02": {k: brief([r for r in alone if r["day"] < "2003-01-01"], k) for k in ("m3", "y1")},
            "since": {k: brief([r for r in alone if r["day"] >= "2003-01-01"], k) for k in ("m3", "y1")}}


def run(data: dict[str, list[tuple[str, float]]]) -> dict:
    spy = data["SPY"]
    rows = {f: scout.past(series, spy) for f, series in data.items() if len(series) > 300}
    depth = depths(rows)
    sectors = [r for f in scout.SECTORS for r in rows.get(f, [])]
    entries = {
        "指数在下跌环境进场（每段第一天，1 年后）": [(r["day"], r["y1"]) for f in scout.INDEXES for r in firsts([x for x in rows.get(f, []) if x["env"] == "down"]) if r["y1"] is not None],
        "行业基金：跟着大盘跌（每段第一天，1 年后）": [(r["day"], r["y1"]) for f in scout.SECTORS for r in firsts([x for x in rows.get(f, []) if x["env"] == "down" and x["market_down"]]) if r["y1"] is not None],
        "行业基金：自己跌（每段第一天，1 年后）": [(r["day"], r["y1"]) for f in scout.SECTORS for r in firsts([x for x in rows.get(f, []) if x["env"] == "down" and not x["market_down"]]) if r["y1"] is not None],
        "行业基金：跌 25% 以上，熊市外（每段第一天，1 年后）": [(r["day"], r["y1"]) for f in scout.SECTORS for r in firsts([x for x in rows.get(f, []) if x["dd"] <= DEEP and era(x["day"]) is None]) if r["y1"] is not None],
        "行业基金：跌 25% 以上，熊市里（每段第一天，1 年后）": [(r["day"], r["y1"]) for f in scout.SECTORS for r in firsts([x for x in rows.get(f, []) if x["dd"] <= DEEP and era(x["day"])]) if r["y1"] is not None],
    }
    return {"start": {f: rs[0]["day"] for f, rs in rows.items()}, "end": spy[-1][0], "declines": declines(rows), "depth": depth,
            "epicentre": epicentres(depth), "deep": deep(rows), "tech_alone": tech_alone(rows), "entries": entries,
            "sector_days": len(sectors)}


def pct(x: float | None, digits: int = 1) -> str:
    return "—" if x is None else f"{x:+.{digits}%}".replace("-", "−")


def cell(b: dict) -> str:
    if not b["days"]:
        return "—"
    one = f"；每段只算第一天 {pct(b['first_mean'])}、涨 {b['first_up']:.0%}" if b["first_mean"] is not None else ""
    return f"{pct(b['mean'])}，涨 {b['up']:.0%}（{b['days']} 天、{b['stretches']} 段{one}）"


def report(rep: dict) -> str:
    name = scout.FUNDS
    out = ["# 回测里板块要不要分开（2026-11-20）", "",
           "模拟盘研究，只记录，不是投资建议。探索档：下面每一条都是线索，不是规则。", "",
           "用户 11-20 问：「你的回测中 板块要分开吗」。前一晚「跌深了开看涨最好」那份研究用的是标普、纳指和七只大股票——其实是一个板块、十年、一次长熊。"
           f"这一份只看正股（不用估期权价），Yahoo 的复权收盘价，能拉多早拉多早（标普从 {rep['start']['SPY'][:4]} 年起，到 {rep['end']}），三只指数基金加十四只行业基金。", "",
           "「下跌环境」= 在 50 日均线下方、离一年高点 10% 以上。每天一个样本、前后重叠，所以每格都写了来自几段行情；「每段只算第一天」是把一段连着的日子当一次。", "",
           "## 一、在下跌环境进场，比平时好吗", "",
           "| | 平时：3 个月后 | 下跌环境进场：3 个月后 | 平时：1 年后 | 下跌环境进场：1 年后 |", "|---|---|---|---|---|"]
    for f in scout.INDEXES:
        d = rep["declines"].get(f)
        if d:
            out.append(f"| {name[f]}（{f}） | {pct(d['any']['m3']['mean'])}，涨 {d['any']['m3']['up']:.0%} | {cell(d['down']['m3'])} | "
                       f"{pct(d['any']['y1']['mean'])}，涨 {d['any']['y1']['up']:.0%} | {cell(d['down']['y1'])} |")
    out += ["", "## 二、「跟着大盘跌」和「自己跌」要分开", "",
            "同样是这只基金在下跌环境：那天标普也在下跌环境，算「跟着大盘跌」；标普不在，算「自己跌」。一年后：", "",
            "| | 跟着大盘跌 | 自己跌 | 平时 |", "|---|---|---|---|"]
    for f in scout.SECTORS:
        d = rep["declines"].get(f)
        if d:
            out.append(f"| {name[f]}（{f}，{rep['start'][f][:4]} 年起） | {cell(d['with']['y1'])} | {cell(d['alone']['y1'])} | {pct(d['any']['y1']['mean'])}，涨 {d['any']['y1']['up']:.0%} |")
    out += ["", "## 三、跌 25% 以上进场：熊市的震中不一样", "",
            "震中 = 那次熊市里离自己一年高点最远的三只行业基金（按数算的，不是挑的）："]
    for era_name, fs in rep["epicentre"].items():
        out.append(f"- {era_name}：" + "、".join(f"{name[f]} {pct(rep['depth'][era_name][f], 0)}" for f in fs))
    out += ["", "离高点 25% 以上那些天进场，一年后：", "", "| | 2000-02 | 2008 | 2022 | 三次熊市以外 |", "|---|---|---|---|---|"]
    for f, d in rep["deep"].items():
        marks = {e: "**" if f in rep["epicentre"][e] else "" for e in ERAS}
        out.append(f"| {name[f]}（{f}） | " + " | ".join(f"{marks[e]}{cell(d[e])}{marks[e]}" for e in ERAS) + f" | {cell(d['other'])} |")
    out += ["", "加粗的是那次熊市的震中。", "",
            "## 四、科技自己跌、标普没事", "",
            "科技基金（XLK）在下跌环境、标普不在：", "",
            "| | 3 个月后 | 1 年后 |", "|---|---|---|",
            f"| 2000-02 那次熊市里 | {cell(rep['tech_alone']['2000-02']['m3'])} | {cell(rep['tech_alone']['2000-02']['y1'])} |",
            f"| 2003 年以后 | {cell(rep['tech_alone']['since']['m3'])} | {cell(rep['tech_alone']['since']['y1'])} |", "",
            robust.table({k: robust.summary(v, end=rep["end"]) for k, v in rep["entries"].items()}, title="稳健性检查（每段行情只算第一天进场，一年后的涨跌）"),
            "「多付成本后」那一栏是给期权用的（买卖各多 5%），对买卖基金太重：正股每边实际不到 0.1%。留着是为了和别的研究同一张表，这里不当结论。", "",
            "## 对回测的改法", "",
            "- 个股按板块分组，同一个板块同一天只算一个样本；侦察的清单已经这样做（同一组只留两只）。",
            "- 「自己跌」和「跟着大盘跌」分开报，不合在一个平均数里。",
            "- 按「段」数样本，不按天数；一段行情里的几百天不是几百次。",
            "- 熊市只有三次，震中各不相同，「跌 25% 以后进场」在熊市里外是两回事，而进场那天并不知道是不是熊市。", "",
            "## 说明", "",
            "- 行业基金是一篮子，个股的尾巴更长。通信和互联网（XLC）2018 年才有，软件、地区银行、生物科技也没有覆盖 2000 年那次。",
            "- 复权价含分红。只看了买进拿着，没有期权。"]
    return "\n".join(out) + "\n"


def main() -> None:
    rep = run(load())
    text = report(rep)
    out = ROOT / "research"
    (out / "2026-11-20-sectors.md").write_text(text)
    slim = {k: v for k, v in rep.items() if k != "entries"} | {"entries": {k: len(v) for k, v in rep["entries"].items()}}
    (out / "2026-11-20-sectors.json").write_text(json.dumps(slim, ensure_ascii=False, indent=1) + "\n")
    print(text)


if __name__ == "__main__":
    main()
