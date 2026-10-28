"""The signal follow card (hero.follow_card) replayed over every trading day since 2024-02, against the old card.

docs/questions/2026-10-28-follow-card.md, task 2. No data calls: the studies' saved trades are the card's trades --
research/*-bottom-long.json (the fast line: SMH's third down day, the call nearest the money 20-45 days out, 2x take
else the 20th session), research/*-bottom-2m.json (the depth line: SPY / QQQ / SMH / IWM first closing 8% under their
20-session high; the monthly at-the-money call held 63 sessions, the 5%-out call held 42) and
research/*-bottom-spread.json (the same signal, the $5 call spread sold at $4). Each trading day the card either
names the day's signals or says there is none; following it puts $1 on each signal. Two ways: every signal (the
studies' own sample, so the means must match theirs) and one at a time (a signal on a line and name still held is
passed over; the hold is the line's longest: 20 / 63 / 42 / 63 sessions). The old card is the SPY 10:00 same-day
call (0.3% out, half at 3x, the rest on a giveback; research/2026-10-16-zdte-minmove.json), every day. Per month:
cards and signals; the longest run of days without a signal; hero.robust's checks for each line. Research only."""
from __future__ import annotations

import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

from hero import robust
from hero.follow_card import ETFS, is_session, latest
from hero.research_ideas import save

ROOT = Path(__file__).resolve().parent.parent
LINES = {"fast": "快线 · SMH 连跌第 3 天 · 平值 2 倍止盈否则第 20 天", "atm63": "深度 · 平值拿 63 天",
         "otm5_42": "深度 · 价外 5% 拿 42 天", "spread": "深度 · $5 价差到 $4 卖"}
HOLD = {"fast": 20, "atm63": 63, "otm5_42": 42, "spread": 63}
CARD = ("fast", "atm63")   # the card's headline trade per signal: the fast line's call, the depth line's at-the-money call


def calendar(start: str, end: str, extra: set[str] = frozenset()) -> list[str]:
    """The NYSE sessions from `start` to `end` (hero.follow_card.is_session), plus any signal day in `extra` (a study's
    day is a session by construction). The old card's own rows skip a few days (2024-03-15), so they are not used."""
    days, d = set(extra), date.fromisoformat(start)
    while d.isoformat() <= end:
        if is_session(d):
            days.add(d.isoformat())
        d += timedelta(days=1)
    return sorted(x for x in days if start <= x <= end)


def signals(long_: dict, b2: dict, sp: dict) -> dict[str, list[dict]]:
    """Day -> the card's trades that day: {"line", "und", "ret"} per line (fast, atm63, otm5_42, spread)."""
    out: dict[str, list[dict]] = {}

    def add(day, line, und, ret):
        if ret is not None:
            out.setdefault(day, []).append({"line": line, "und": und, "ret": float(ret)})
    for r in long_.get("rows", []):
        if r["und"] == "SMH" and r.get("sig") == "down3" and r.get("leg") == "atm":
            add(r["day"], "fast", "SMH", (r.get("res") or {}).get("take2"))
    for r in b2.get("rows", []):
        if r["und"] in ETFS and "dd8" in r.get("tags", []):
            if r.get("leg") == "atm":
                add(r["day"], "atm63", r["und"], (r.get("res") or {}).get("d63"))
            elif r.get("leg") == "otm5":
                add(r["day"], "otm5_42", r["und"], (r.get("res") or {}).get("d42"))
    for r in sp.get("rows", []):
        if r["und"] in ETFS and "dd8" in r.get("tags", []) and r.get("leg") == "atm5":
            add(r["day"], "spread", r["und"], (r.get("res") or {}).get("take80"))
    return out


def replay(days: list[str], sigs: dict[str, list[dict]], one_at_a_time: bool = False) -> dict:
    """Walk the calendar: which days carry a card, the trades taken, per month, the longest run without a signal."""
    index = {d: i for i, d in enumerate(days)}
    busy: dict[tuple[str, str], int] = {}   # (line, name) -> last session index it is held
    taken, cards, months = [], [], {}
    gap, longest, gap_end = 0, 0, None
    for i, d in enumerate(days):
        today = []
        for t in sigs.get(d, []):
            key = (t["line"], t["und"])
            if one_at_a_time and busy.get(key, -1) >= i:
                continue
            today.append({**t, "day": d})
            if one_at_a_time:
                busy[key] = i + HOLD[t["line"]]
        headline = [t for t in today if t["line"] in CARD]
        m = months.setdefault(d[:7], {"cards": 0, "signals": 0, "card_sum": 0.0})
        if headline:
            cards.append(d)
            m["cards"] += 1
            m["signals"] += len(headline)
            m["card_sum"] += sum(t["ret"] for t in headline)
            gap = 0
        else:
            gap += 1
            if gap > longest:
                longest, gap_end = gap, d
        taken += today
    by = {line: [(t["day"], t["ret"]) for t in taken if t["line"] == line] for line in LINES}
    card = [(t["day"], t["ret"]) for t in taken if t["line"] in CARD]
    start_gap = days[index[gap_end] - longest + 1] if gap_end else None
    return {"days": len(days), "cards": len(cards), "card_days": cards, "trades": taken, "by_line": by, "card": card,
            "months": months, "longest_gap": longest, "gap_from": start_gap, "gap_to": gap_end,
            "card_sum": round(sum(r for _, r in card), 4), "line_sum": {k: round(sum(r for _, r in v), 4) for k, v in by.items()}}


def old_card(old: dict) -> list[tuple[str, float]]:
    return [(r["day"], r["res"]["10:00|0.003"]["take3_trail"]) for r in old.get("rows", [])
            if r.get("und") == "SPY" and (r.get("res") or {}).get("10:00|0.003", {}).get("take3_trail") is not None]


def run(root: Path = ROOT) -> dict:
    long_, b2, sp, old = latest("bottom-long", root), latest("bottom-2m", root), latest("bottom-spread", root), latest("zdte-minmove", root)
    end = min(x.get("end") or "9999" for x in (long_, b2, sp))
    sigs = signals(long_, b2, sp)
    days = calendar("2024-02-01", end, set(sigs))
    every, single = replay(days, sigs), replay(days, sigs, one_at_a_time=True)
    oc = old_card(old)
    summ = lambda xs: robust.summary(xs, end=end)
    return {"from": days[0], "to": days[-1], "end": end, "every": every, "one": single, "old": oc,
            "checks": {"every": {k: summ(v) for k, v in every["by_line"].items()} | {"card": summ(every["card"])},
                       "one": {k: summ(v) for k, v in single["by_line"].items()} | {"card": summ(single["card"])},
                       "old": summ(oc)}}


def markdown(rep: dict) -> str:
    e, o, oc = rep["every"], rep["one"], rep["old"]
    p = lambda x: f"{x:+.2f}"
    old_mean = statistics.mean(r for _, r in oc) if oc else 0.0
    L = [f"# 新跟单卡逐日重放：有信号才出卡 {date.today().isoformat()}", "",
         f"{rep['from']} 至 {rep['to']} 的 {e['days']} 个交易日，每天按 hero/follow_card.py 的规则判断：SMH 连跌第 3 天（快线）、"
         "SPY / QQQ / SMH / IWM 第一次收在 20 日高点下 8%（深度）。照卡片做就是每个信号放 $1，结果用三个研究文件里已有的逐笔收益"
         "（买卖各多付 5%），不重新下载期权数据。卡片的主单：快线买 20–45 天平值、翻倍卖否则第 20 天；深度买月度平值拿 63 天。"
         "价外 5% 和 $5 价差另列。只研究，不改交易。", "",
         "## 新卡对旧卡", "",
         "| | 出卡天数 | 每 $1 合计 | 每笔 / 每天平均 | 最长连续没信号 |", "|---|---|---|---|---|",
         f"| 新卡 · 全部信号（和研究文件同一批成交） | {e['cards']} 天 / {e['days']} 天（{len(e['card'])} 笔） | {p(e['card_sum'])} | "
         f"{(e['card_sum'] / len(e['card'])) if e['card'] else 0:+.0%} | {e['longest_gap']} 个交易日（{e['gap_from']} 至 {e['gap_to']}） |",
         f"| 新卡 · 同一只拿着不再买 | {o['cards']} 天（{len(o['card'])} 笔） | {p(o['card_sum'])} | {(o['card_sum'] / len(o['card'])) if o['card'] else 0:+.0%} | "
         f"{o['longest_gap']} 个交易日（{o['gap_from']} 至 {o['gap_to']}） |",
         f"| 旧卡 · SPY 10:00 当天到期 0.3%（每天都出） | {len(oc)} 天 | {p(sum(r for _, r in oc))} | {old_mean:+.0%} | 0 |", "",
         "分线（全部信号 / 同一只拿着不再买）：", ""]
    for k, name in LINES.items():
        L.append(f"- {name}：{len(e['by_line'][k])} 笔、每 $1 合计 {p(e['line_sum'][k])} / {len(o['by_line'][k])} 笔、合计 {p(o['line_sum'][k])}")
    L += ["", robust.table({f"新卡主单（全部信号）": rep["checks"]["every"]["card"], f"新卡主单（同一只拿着不再买）": rep["checks"]["one"]["card"],
                            **{f"{LINES[k]}（全部）": rep["checks"]["every"][k] for k in LINES},
                            **{f"{LINES[k]}（拿着不再买）": rep["checks"]["one"][k] for k in LINES},
                            "旧卡：SPY 10:00 当天到期 0.3%（每天）": rep["checks"]["old"]}, "稳健性检查（hero/robust.py）"),
          "## 每个月出几张卡（全部信号口径）", "", "| 月份 | 出卡天数 | 主单笔数 | 主单每 $1 合计 |", "|---|---|---|---|"]
    for mth, m in sorted(e["months"].items()):
        L.append(f"| {mth} | {m['cards']} | {m['signals']} | {m['card_sum']:+.2f} |")
    per_month = statistics.mean(m["cards"] for m in e["months"].values()) if e["months"] else 0
    deep = [len(e["by_line"][k]) for k in ("atm63", "otm5_42", "spread")]
    L += ["", "## 读法", "",
          f"- 平均每月 {per_month:.1f} 天有卡，其余日子卡片写「今天没有信号」；最长 {e['longest_gap']} 个交易日一张卡都没有。",
          "- 每 $1 的平均和三个研究文件一样（同一批成交）；「同一只拿着不再买」少掉的是阴跌里接连触发的那几笔。",
          "- 旧卡每天都推荐一个平均每 $1 −14%、中位数 −86% 的单子；新卡把这个换成「没有信号就不做」。",
          f"- 样本小（快线 {len(e['by_line']['fast'])} 笔、深度每条 {min(deep)}–{max(deep)} 笔），2024–26 多数时间在涨；看提醒列。"]
    return "\n".join(L) + "\n"


def main() -> None:
    rep = run()
    md = markdown(rep)
    for k in ("every", "one"):
        rep[k]["trades"] = rep[k]["trades"][:500]
    save("follow-card", rep, md)


if __name__ == "__main__":
    sys.exit(main())
