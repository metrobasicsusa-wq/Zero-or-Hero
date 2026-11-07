"""Sniper overnight tickets: when to sell. (owner, 11-04 「改吧」, then 「动态卖呢」 -- one study, done once.)

The sniper holds a ticket bought at 09:35 on the catalyst day to the next session's 15:50. On the same tickets
(research/sniper/factor-rows.json), every same-day dynamic exit (3x, 5x, the trail, half at 3x) lost 4-10% per $1
while holding to the next close made +53%, so the question is not "sell faster on day 1" but two others:
  (i)  stop_back: on day 1 after 10:00 the stock closes a minute back through its open (a call: at or below it, a
       put: at or above) -> sell the ticket at that minute; otherwise hold to the next session's 15:50;
  (ii) dynamic exits on day 2 only, the high tracked from the entry across both sessions: d2_trail2 (after 2x, out on
       a 40% giveback), d2_take3_trail (half at 3x, the rest on the trail), d2_half2 (half at 2x, the rest at 15:50);
  (iii) both: (i) on day 1, then (ii) on day 2.
All against next1550, the ledger's rule. Sample: the factor rows whose open was beyond the 20-session high / low
(breakout), picked with the gap, with an expiry after the day (240), and the 57 of them whose gap was 3+ times the
stock's average swing. The contract is rebuilt the way research_open_catalyst picked it (the 09:35 price, the nearest
expiry within a week, the first strike 0.6% beyond); 10% paid over to get in and given up to get out, on minute
closes. Record only. On Actions: research.yml study=sniper_stop -> research/<date>-sniper-stop.{json,md}.
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from hero import research_ideas as ri
from hero.research_0dte import at_or_before
from hero.research_0dte_names import minutes_by_day

ROOT = Path(__file__).resolve().parent.parent
ENTRY, STOP_FROM, OUT = "09:35", "10:00", "15:50"
OFF = 0.006
START = "2024-02-01"
EXITS = {"next1550": "拿到第二天 15:50（现在的规则）",
         "stop_back": "第一天 10:00 后跌回开盘价就卖",
         "d2_trail2": "第二天翻倍后回撤 40% 卖",
         "d2_take3_trail": "第二天一半 3 倍、其余回撤卖",
         "d2_half2": "第二天到 2 倍卖一半、其余拿到 15:50",
         "both_trail2": "第一天止损 + 第二天回撤卖",
         "both_take3_trail": "第一天止损 + 第二天一半 3 倍其余回撤卖"}
BIG, BASE = "突破 20 日高低 + 跳空 ≥3 倍、顺跳空", "突破 20 日高低、顺跳空"


def sample(rows: list[dict]) -> dict[str, list[dict]]:
    base = [r for r in rows if r.get("breakout") and r.get("with_gap") and str(r.get("exp", "")) > r["day"]]
    return {BIG: [r for r in base if (r.get("gap_z") or 0) >= 3], BASE: base}


def back_through(kind: str, open_px: float, u: float) -> bool:
    return u <= open_px if kind == "C" else u >= open_px


def play(mins1: dict[str, dict], mins2: dict[str, dict], und1: dict[str, dict], kind: str, open_px: float,
         entry: str = ENTRY) -> dict[str, float] | None:
    """Per $1 for every exit. mins1 / mins2: the option's minute bars on day 1 / day 2 (HH:MM -> bar); und1: the
    stock's minute bars on day 1; open_px: its 09:30 open. None when the entry or day 2 is missing."""
    got = ri.entry_cost(mins1, entry)
    if not got or not mins2:
        return None
    t0, cost = got
    p1 = [(k, float(v["c"])) for k, v in sorted(mins1.items()) if k > t0]
    p2 = [(k, float(v["c"])) for k, v in sorted(mins2.items()) if k <= OUT]
    if not p2:
        return None
    end = ri.out_of(p2[-1][1]) / cost - 1
    stop = None
    for k, c in p1:
        if k < STOP_FROM:
            continue
        ub = at_or_before(und1, k)
        if ub and back_through(kind, open_px, float(ub["c"])):
            stop = ri.out_of(c) / cost - 1
            break
    best0 = max([c for _, c in p1] + [cost])
    armed0 = best0 >= 2 * cost

    def day2(take3: bool = False, half2: bool = False) -> float:
        best, armed, half, out = best0, armed0, None, end
        for _, c in p2:
            best = max(best, c)
            armed = armed or c >= 2 * cost
            if half is None and half2 and c >= 2 * cost:
                half = 1.0
            if half is None and take3 and c >= 3 * cost:
                half = 2.0
            if not half2 and armed and c <= 0.6 * best:
                out = ri.out_of(c) / cost - 1
                break
        return (half + out) / 2 if half is not None else out

    res = {"next1550": end, "stop_back": end if stop is None else stop,
           "d2_trail2": day2(), "d2_take3_trail": day2(take3=True), "d2_half2": day2(half2=True)}
    res["both_trail2"] = res["d2_trail2"] if stop is None else stop
    res["both_take3_trail"] = res["d2_take3_trail"] if stop is None else stop
    return {k: round(v, 4) for k, v in res.items()}


def run(client, rows: list[dict] | None = None, pace: float = 0.05, end: str | None = None) -> dict:
    rows = json.loads((ROOT / "research" / "sniper" / "factor-rows.json").read_text()) if rows is None else rows
    groups = sample(rows)
    wanted = {(r["und"], r["day"]): r for rs in groups.values() for r in rs}
    end = end or (date.today() - timedelta(days=1)).isoformat()
    out, skipped = [], {"no_day": 0, "no_contract": 0, "no_bars": 0}
    for und in sorted({u for u, _ in wanted}):
        days = minutes_by_day(client, und, START, end)
        order = sorted(days)
        for (u, day), r in sorted(wanted.items()):
            if u != und:
                continue
            if day not in days or order.index(day) + 1 >= len(order) or "09:30" not in days[day]:
                skipped["no_day"] += 1
                continue
            day2 = order[order.index(day) + 1]
            m1 = days[day]
            b = at_or_before(m1, ENTRY)
            if not b:
                skipped["no_day"] += 1
                continue
            got = ri.contract(client, und, r["kind"], day, float(b["c"]), OFF, 0, 7)
            if not got:
                skipped["no_contract"] += 1
                continue
            sym, exp = got
            res = play(ri.minutes(client, sym, day), ri.minutes(client, sym, day2), m1, r["kind"], float(m1["09:30"]["o"]))
            time.sleep(pace)
            if not res:
                skipped["no_bars"] += 1
                continue
            out.append({"und": und, "day": day, "day2": day2, "kind": r["kind"], "gap": r.get("gap"), "gap_z": r.get("gap_z"),
                        "exp": exp, "exp_old": r.get("exp"), "sym": sym, "headline": (r.get("headline") or "")[:80],
                        "next_old": (r.get("res") or {}).get("next"), "res": res})
    return {"generated": date.today().isoformat(), "start": START, "end": end, "skipped": skipped, "rows": out}


def groups_of(rep: dict) -> dict[str, list[dict]]:
    rows = rep["rows"]
    return {BIG: [r for r in rows if (r.get("gap_z") or 0) >= 3], BASE: rows}


def markdown(rep: dict) -> tuple[dict, str]:
    groups = groups_of(rep)
    intro = (f"狙击拿过夜的单：{rep['start']} 至 {rep['end']}，research/sniper/factor-rows.json 里「开盘突破 20 日高 / 低、顺着跳空买」的单"
             f"（{len(groups[BASE])} 笔重放成功，跳过：没有分钟线 {rep['skipped']['no_day']}、没有合约 {rep['skipped']['no_contract']}、"
             f"期权没有第二天的分钟线 {rep['skipped']['no_bars']}），和其中跳空 ≥3 倍平时波动的 {len(groups[BIG])} 笔。"
             "合约按原来的挑法（09:35 的价、一周内最近到期、价外 0.6% 的第一档），买入多付 10%、卖出少拿 10%，按分钟收盘价成交。"
             "卖法：现在的规则是拿到第二天 15:50；(i) 第一天 10:00 后股价收回开盘价（看涨跌回、看跌涨回）就卖；(ii) 只在第二天动态卖，"
             "最高价从买入起连着两天算；(iii) 两者合起来。已知同一组单当天之内的动态卖都是亏的（notes/lessons.md 11-04），所以这里不再测当天卖。只研究，不改规则。")
    table, md = ri.report(f"狙击过夜单的卖法 {rep['generated']}", intro, groups, EXITS, rep["end"])
    pairs = [(r["res"]["next1550"], r["next_old"]) for r in rep["rows"] if r.get("next_old") is not None]
    if pairs:
        a, b = [p[0] for p in pairs], [p[1] for p in pairs]
        corr = statistics.correlation(a, b) if len(pairs) > 2 and statistics.pstdev(a) and statistics.pstdev(b) else None
        md += (f"\n## 校验\n\n重放的「拿到第二天 15:50」和原表的「拿到第二天收盘」：{len(pairs)} 笔，平均 {statistics.mean(a):+.0%} 对 "
               f"{statistics.mean(b):+.0%}，逐笔相关 {corr:.2f}。" if corr is not None else
               f"\n## 校验\n\n重放 {len(pairs)} 笔，平均 {statistics.mean(a):+.0%} 对原表 {statistics.mean(b):+.0%}。")
        same = sum(1 for r in rep["rows"] if r.get("exp_old") == r.get("exp"))
        md += f" 合约到期日和原表一样的 {same}/{len(rep['rows'])} 笔。\n"
    return table, md


def main() -> None:
    from hero.alpaca import Alpaca
    rep = run(Alpaca())
    table, md = markdown(rep)
    rep["table"] = table
    (ROOT / "research" / f"{rep['generated']}-sniper-stop.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    (ROOT / "research" / f"{rep['generated']}-sniper-stop.md").write_text(md)
    print(md)


if __name__ == "__main__":
    sys.exit(main())
