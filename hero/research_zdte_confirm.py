"""SPY 10:00 same-day calls / puts: do other markets agreeing at 10:00 make the move more likely to carry on?
(owner, 10-20: "can you see days like today coming?")

10-20 the pre-market looked risk-off, then oil fell from the open, chips and small caps turned up, and SPY's 10:00
call paid 3x-plus. Nothing before the open said so; the question is whether reacting at 10:00 can be sharper. For each
day of the cost replay (research/*-zdte-cost.json: SPY 10:00, the 0.3% strike, half at 3x and the rest trailed), take
how far IWM (small caps), SMH (chips), USO (oil) and TLT (long bonds) had moved from their own opens at 10:00, signed
by SPY's side (+ = the same direction as SPY), and split the trades by it.

Guard against finding a pattern by trying many: 2024-02..2025-12 is where a pattern may be found, 2026 is the check.
A group counts only if it beats "every day" by 10 points per $1 in both, and still makes money without its best three
trades. Costs: our own fills (research/2026-10-20-fills.md) sit near the minute close, so that is the main column;
"sell 10% under" is the cautious one. Record only. Run: python -m hero.research_zdte_confirm
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

from hero import robust
from hero.research_0dte import at_or_before
from hero.research_0dte_names import minutes_by_day
from hero.research_ideas import save

ROOT = Path(__file__).resolve().parent.parent
GAUGES = {"IWM": "小盘 IWM", "SMH": "芯片 SMH", "USO": "油 USO", "TLT": "长债 TLT"}
OFF = "0.003"
MAIN, CAUTIOUS = "0/0", "0/0.1"
EX = "take3_trail"
FLAT = 0.001            # within 0.1% of the open counts as flat
SPLIT = "2026-01-01"    # find before, check from here
EDGE = 0.10             # must beat "every day" by this much per $1, in both halves


def latest_replay(root: Path = ROOT) -> dict:
    files = sorted((root / "research").glob("20??-??-??-zdte-cost.json"))
    if not files:
        raise SystemExit("no research/*-zdte-cost.json: run the zdte_cost study first")
    return json.loads(files[-1].read_text())


def move_at(m: dict[str, dict], at: str = "10:00") -> float | None:
    b = at_or_before(m, at)
    if "09:30" not in m or not b or float(m["09:30"]["o"]) <= 0:
        return None
    return float(b["c"]) / float(m["09:30"]["o"]) - 1


def attach(rows: list[dict], gauges: dict[str, dict[str, dict]]) -> list[dict]:
    """Each replay day with its SPY-signed gauge moves and the two returns; days without the trade are dropped."""
    out = []
    for r in rows:
        res = r.get("res", {}).get(OFF, {})
        if MAIN not in res or CAUTIOUS not in res:
            continue
        side = 1 if r["kind"] == "C" else -1
        g = {}
        for sym, days in gauges.items():
            mv = move_at(days.get(r["day"], {}))
            g[sym] = None if mv is None else round(side * mv, 5)
        out.append({"day": r["day"], "spy": abs(r["move"]), "g": g, "main": res[MAIN][EX], "cautious": res[CAUTIOUS][EX]})
    return out


def bucket(x: float | None) -> str | None:
    if x is None:
        return None
    return "同向" if x >= FLAT else "反向" if x <= -FLAT else "持平"


def groups(days: list[dict]) -> dict[str, list[dict]]:
    g: dict[str, list[dict]] = {"每天都做": days}
    for sym, name in GAUGES.items():
        for b in ("同向", "持平", "反向"):
            g[f"{name} {b}"] = [d for d in days if bucket(d["g"].get(sym)) == b]
    agree = lambda d: sum(bucket(d["g"].get(s)) == "同向" for s in ("IWM", "SMH"))
    against = lambda d: sum(bucket(d["g"].get(s)) == "反向" for s in ("IWM", "SMH"))
    g["小盘和芯片都同向"] = [d for d in days if agree(d) == 2]
    g["小盘和芯片一个同向"] = [d for d in days if agree(d) == 1]
    g["小盘和芯片都不同向"] = [d for d in days if agree(d) == 0]
    g["小盘和芯片都反向"] = [d for d in days if against(d) == 2]
    g["像 10-20：小盘、芯片同向，油反向"] = [d for d in days if agree(d) == 2 and bucket(d["g"].get("USO")) == "反向"]
    g["小盘、芯片同向，且 SPY 已走 0.2% 以上"] = [d for d in days if agree(d) == 2 and d["spy"] >= 0.002]
    return g


def mean_n(xs: list[float]) -> tuple[float, int] | None:
    return (statistics.mean(xs), len(xs)) if xs else None


def verdict(s_in, s_out, base_in, base_out, trim) -> str:
    if not s_in or not s_out or s_in[1] < 20 or s_out[1] < 5:
        return "笔数太少，不下结论"
    better_in = s_in[0] >= base_in[0] + EDGE
    better_out = s_out[0] >= base_out[0] + EDGE
    if better_in and better_out and trim is not None and trim > 0:
        return "样本内外都更好，去掉最好 3 笔也赚：值得实盘小试"
    if better_in and better_out:
        return "样本内外都更好，但靠少数几笔"
    if better_in:
        return "只在 2024–25 更好，2026 没重现"
    if better_out:
        return "只在 2026 更好"
    return "不比每天都做好"


def report(days: list[dict], end: str) -> tuple[dict, str]:
    g = groups(days)
    base = g["每天都做"]
    half = lambda ds, f: mean_n([d["main"] for d in ds if f(d["day"])])
    base_in, base_out = half(base, lambda x: x < SPLIT), half(base, lambda x: x >= SPLIT)
    cell = lambda v: f"{v[0]:+.0%}（{v[1]}）" if v else "—"
    pct = lambda v: "—" if v is None else f"{v:+.0%}"
    table, out = {}, ["| 分组 | 笔数 | 平均每 $1 | 去掉最好 3 笔 | 中位数 | 2024–25（找规律） | 2026（检验） | 卖少拿 10% 时平均 | 结论 | 提醒 |",
                      "|---|---|---|---|---|---|---|---|---|---|"]
    for name, ds in g.items():
        s = robust.summary([(d["day"], d["main"]) for d in ds], end)
        if not s:
            continue
        s_in, s_out = half(ds, lambda x: x < SPLIT), half(ds, lambda x: x >= SPLIT)
        caut = statistics.mean(d["cautious"] for d in ds)
        v = "—" if name == "每天都做" else verdict(s_in, s_out, base_in, base_out, s["trim"])
        table[name] = {**s, "in": s_in, "out": s_out, "cautious": caut, "verdict": v}
        out.append(f"| {name} | {s['n']} | {pct(s['mean'])} | {pct(s['trim'])} | {pct(s['median'])} | {cell(s_in)} | {cell(s_out)} | "
                   f"{pct(caut)} | {v} | {'；'.join(s['flags']) or '—'} |")
    return table, "\n".join(out)


def run(client) -> dict:
    rep = latest_replay()
    gauges = {sym: minutes_by_day(client, sym, rep["start"], rep["end"]) for sym in GAUGES}
    days = attach(rep["rows"], gauges)
    return {"start": rep["start"], "end": rep["end"], "days": days}


def main() -> None:
    from hero.alpaca import Alpaca
    data = run(Alpaca())
    table, body = report(data["days"], data["end"])
    head = [f"# SPY 末日单：10:00 别的市场同不同向？ {data['end']}", "",
            f"{data['start']} 至 {data['end']}，{len(data['days'])} 个交易日。SPY 10:00 当天到期 0.3% 版（比开盘高买看涨、低买看跌，"
            "一半 3 倍、其余翻倍后回撤 40% 卖、否则 15:30），每笔结果来自 research/2026-10-20-zdte-cost（按分钟收盘价，最接近我们的实测成交）。"
            "按 10:00 时 IWM（小盘）、SMH（芯片）、USO（油）、TLT（长债）离各自开盘走了多远分组，方向按 SPY 算："
            "「同向」= 和 SPY 同一个方向走了 0.1% 以上，「反向」= 反方向 0.1% 以上，其余「持平」。", "",
            "为了不被「试得多总有一组好看」骗：2024–25 用来找规律，2026 用来检验；一组要在两段都比「每天都做」每 $1 多赚 10 个百分点，"
            "而且去掉最好 3 笔还赚，才算值得实盘小试。「像 10-20」那一行是照今天挑的，只看不算。只研究，不改交易。", "", body, ""]
    save("zdte-confirm", {**data, "table": table}, "\n".join(head))


if __name__ == "__main__":
    sys.exit(main())
