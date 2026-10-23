"""The volatility regime behind every hero engine (owner, 10-23: 「改，推完排上」).

The quick cut on 10-23 (chat) found dip-week's results living in the market's wilder third: SPY's 20-session realised
vol in its bottom third, -19% a trade; top third, +38%. This makes it a study: SPY's realised vol from its own daily
bars (knowable each evening), terciles over 2024-02 on and fixed thresholds (10% / 12% / 15%), applied to every
per-trade series the research folder holds -- dip-week (and the broad-pool and pullback runs when they exist),
overnight momentum, pead, the 0DTE families, SPY after a down day -- with mean, trimmed mean, median, the 2x and 5x
tails and the hero game (half and all-in, with intervals) per regime, and today's regime. Research only."""
from __future__ import annotations

import json
import math
import statistics
import sys
from datetime import date
from pathlib import Path

from hero.research_hero_game import dipweek_pick, game, interval
from hero.research_verdict import minmove, pead, streak

ROOT = Path(__file__).resolve().parent.parent
THRESHOLDS = (0.103, 0.12, 0.15)
N = 20


def realised(bars: list[dict], n: int = N) -> dict[str, float]:
    """Annualised standard deviation of the last n close-to-close moves, keyed by the day it is known for (that evening)."""
    cs = [(b["t"][:10], float(b["c"])) for b in bars]
    out = {}
    for i in range(n, len(cs)):
        rs = [cs[k][1] / cs[k - 1][1] - 1 for k in range(i - n + 1, i + 1)]
        out[cs[i][0]] = statistics.pstdev(rs) * math.sqrt(252)
    return out


def regime_of(vol: dict[str, float], lo: float, hi: float):
    days = sorted(vol)

    def f(day: str) -> str | None:
        """The regime known the evening before `day`."""
        ks = [k for k in days if k < day]
        if not ks:
            return None
        v = vol[ks[-1]]
        return "high" if v > hi else ("low" if v < lo else "mid")
    return f


def latest_file(prefix: str) -> dict | None:
    fs = sorted((ROOT / "research").glob(f"*-{prefix}.json"))
    return json.loads(fs[-1].read_text()) if fs else None


def pick_by_key(rows: list[dict], leg: str, exit_: str, key: str, better) -> list[tuple[str, float]]:
    by: dict[str, dict] = {}
    for r in rows:
        if r.get("leg") != leg or (r.get("res") or {}).get(exit_) is None:
            continue
        if r["day"] not in by or better(r, by[r["day"]]):
            by[r["day"]] = r
    return [(d, by[d]["res"][exit_]) for d in sorted(by)]


def series(docs: dict) -> dict[str, list[tuple[str, float]]]:
    out: dict[str, list[tuple[str, float]]] = {}
    dw = docs.get("dipweek")
    if dw:
        for leg, name in (("otm3", "价外 3%"), ("atm", "平值")):
            for ex in ("d5", "trail2"):
                by: dict[str, dict] = {}
                for r in dw["rows"]:
                    if r.get("leg") == leg and (r.get("res") or {}).get(ex) is not None and (r["day"] not in by or r["gap"] < by[r["day"]]["gap"]):
                        by[r["day"]] = r
                out[f"抄底给一周 {name} {ex}"] = [(d, by[d]["res"][ex]) for d in sorted(by)]
    for key, label, better in (("dipweek_broad", "抄底给一周·100 只池", lambda r, o: r["gap"] < o["gap"]),
                               ("pullback", "强势股回调", lambda r, o: r["from_high"] < o["from_high"])):
        doc = docs.get(key)
        if doc:
            for leg, name in (("otm3", "价外 3%"), ("atm", "平值")):
                out[f"{label} {name} d5"] = pick_by_key(doc["rows"], leg, "d5", "res", better)
    ov = docs.get("overnight")
    if ov:
        by = {}
        for r in ov["rows"]:
            if r["kind"] == "C" and r.get("trend") and r["res"].get("close_d3") is not None and (r["day"] not in by or abs(r["ret"]) > abs(by[r["day"]]["ret"])):
                by[r["day"]] = r
        out["隔夜动量 50 日线上 拿 3 天"] = [(d, by[d]["res"]["close_d3"]) for d in sorted(by)]
    pd_ = docs.get("pead")
    if pd_:
        out["财报后漂移 高开 ≥10%"] = sorted(pead(pd_, 0.10, "trail2"))
    mm = docs.get("minmove")
    if mm:
        out["末日单 SPY 0.3% 现行出场"] = minmove(mm, "SPY", "10:00", 0.003, "take3_trail")
        out["末日单 SPY 0.3% 只做 ≥0.5%"] = minmove(mm, "SPY", "10:00", 0.003, "take3_trail", 0.005)
        out["末日单 TSLA 0.6%"] = minmove(mm, "TSLA", "10:00", 0.006, "take3_trail")
        out["末日单 QQQ 0.6%"] = minmove(mm, "QQQ", "10:00", 0.006, "take3_trail")
    st = docs.get("streak")
    if st:
        out["SPY 昨跌 1% 后买涨 0.3%"] = streak(st, "SPY", "C|0.003|3", -0.01)
    return {k: v for k, v in out.items() if v}


def cell(xs: list[float]) -> dict:
    v = sorted(xs)
    g5, g10 = game(xs, 0.5), game(xs, 1.0)
    return {"n": len(v), "mean": statistics.mean(v), "trim": statistics.mean(v[:-3]) if len(v) > 3 else None, "median": statistics.median(v),
            "x2": sum(x >= 1 for x in v) / len(v), "x5": sum(x >= 4 for x in v) / len(v),
            "half": {"heroes": g5["heroes"], "rounds": g5["rounds"], "ci": interval(g5["heroes"], g5["rounds"]), "cost": g5["cost_per_hero"]},
            "full": {"heroes": g10["heroes"], "rounds": g10["rounds"], "ci": interval(g10["heroes"], g10["rounds"]), "cost": g10["cost_per_hero"]}}


def split(xs: list[tuple[str, float]], vol: dict[str, float], lo: float, hi: float) -> dict[str, dict]:
    f = regime_of(vol, lo, hi)
    by = {"low": [], "mid": [], "high": []}
    for d, r in xs:
        g = f(d)
        if g:
            by[g].append(r)
    out = {g: cell(v) for g, v in by.items() if len(v) >= 8}
    for t in THRESHOLDS:
        ft = regime_of(vol, t, 9.0)
        v = [r for d, r in xs if ft(d) == "mid"]
        if len(v) >= 8:
            out[f"vol≥{t:.1%}"] = cell(v)
    return out


def pct(x) -> str:
    return "—" if x is None else f"{x:+.0%}"


def hero_cell(h: dict) -> str:
    ci = h.get("ci") or (0, 0)
    cost = f"${h['cost']:,.0f}" if h.get("cost") else "—"
    return f"{h['heroes']}/{h['rounds']}（{ci[0]:.0%}–{ci[1]:.0%}）{cost}"


def markdown(table: dict, meta: dict) -> str:
    out = [f"# 波动期：每条 hero 引擎在市场动 / 不动时的样子 {meta['generated']}", "",
           f"SPY 20 日实现波动（年化，用前一天收盘算，当晚就知道）{meta['start']} 到 {meta['end']} 的三分位：低 <{meta['lo']:.1%}、高 >{meta['hi']:.1%}；"
           f"另按固定门槛 10.3% / 12% / 15% 只做「波动在门槛之上」的日子。今天（{meta['today']}）的 20 日波动 {meta['vol_now']:.1%}，是{meta['regime_now']}档。"
           "每格：笔数、平均每 $1、去掉最好 3 笔、中位数、到过 2 倍 / 5 倍的比例，押一半和押满时每轮到 20 倍的次数 / 轮数（95% 区间）和每个 hero 的成本。"
           "各规则的成本按它们各自研究的假设。只研究，不改交易。", ""]
    for name, by in table.items():
        out += [f"## {name}", "", "| 档 | 笔数 | 平均 | 去掉最好 3 笔 | 中位数 | ≥2 倍 | ≥5 倍 | 押一半：20 倍 / 轮（区间）成本 | 押满：20 倍 / 轮（区间）成本 |", "|---|---|---|---|---|---|---|---|---|"]
        for g, c in by.items():
            label = {"low": "低波动（最低三分之一）", "mid": "中", "high": "高波动（最高三分之一）"}.get(g, f"只做 {g}")
            out.append(f"| {label} | {c['n']} | {pct(c['mean'])} | {pct(c['trim'])} | {pct(c['median'])} | {c['x2']:.0%} | {c['x5']:.1%} | {hero_cell(c['half'])} | {hero_cell(c['full'])} |")
        out.append("")
    out += ["## 读法", "",
            "- 抄底给一周、财报后漂移是「大动之后买凸性」：低波动期平均为负，高波动期才有 hero。门槛选哪个看各档的轮数和成本，不看单格最好的。",
            "- 末日单、隔夜动量对波动不敏感的话，门槛对它们没用。",
            "- 轮数都少，区间比点估计重要；两个门槛之间差不出区间的，用更宽松的那个（多攒轮）。"]
    return "\n".join(out) + "\n"


def main() -> int:
    from hero.alpaca import Alpaca
    c = Alpaca()
    spy = c.daily_bars(["SPY"], "2023-12-01", adjustment="split", feed="sip").get("SPY") or []
    vol = realised(spy)
    days = [d for d in sorted(vol) if d >= "2024-02-01"]
    vs = sorted(vol[d] for d in days)
    lo, hi = vs[len(vs) // 3], vs[2 * len(vs) // 3]
    docs = {}
    for k, f in (("minmove", "2026-10-16-zdte-minmove.json"), ("streak", "2026-10-08-zdte-streak.json"), ("dipweek", "2026-10-16-dip-week.json"),
                 ("pead", "2026-10-13-pead.json"), ("overnight", "2026-10-13-overnight.json")):
        try:
            docs[k] = json.loads((ROOT / "research" / f).read_text())
        except FileNotFoundError:
            pass
    for k, prefix in (("dipweek_broad", "dip-week-broad"), ("pullback", "pullback")):
        doc = latest_file(prefix)
        if doc:
            docs[k] = doc
    table = {name: split(xs, vol, lo, hi) for name, xs in series(docs).items()}
    today = max(vol)
    meta = {"generated": date.today().isoformat(), "start": days[0], "end": days[-1], "lo": lo, "hi": hi, "today": today, "vol_now": vol[today],
            "regime_now": "高" if vol[today] > hi else ("低" if vol[today] < lo else "中")}
    md = markdown(table, meta)
    out = ROOT / "research" / f"{meta['generated']}-vol-regime"
    out.with_suffix(".md").write_text(md)
    out.with_suffix(".json").write_text(json.dumps({"meta": meta, "table": table, "vol": vol}, ensure_ascii=False, indent=1))
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
