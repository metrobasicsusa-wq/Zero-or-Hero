"""One page per company before its report (owner, 10-20: "every company is read differently").

Netflix 10-20: we called it up on "expectations are already low"; its last three reports had all opened 5-12% down,
on the same thing (revenue only matching its own forecast), and the fourth did too. A company's own record is what to
read first. Two parts per company:
  - from the data: its last reactions (research/2026-10-02-earnings-reactions.json for 98 large caps since 2021, plus
    every report this repo has scored since): open vs the last price before the report, the close, the options'
    implied move, and how often it opened up;
  - written by hand in research/earnings-forecast/companies.json after each review: what the market watches for it,
    its guidance habit, and why it moved last time; plus its lines in reasons.jsonl.
Note: across those 98 companies the direction of past reactions barely predicts the next (51.6% open up overall;
45.5% after two down in a row, 50-52% after one), so the pages are for the why, not a streak rule.
Run: python -m hero.earnings_profiles [--date YYYY-MM-DD] [SYMBOL ...]  (no symbols: the day's top TOP by turnover)
"""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FOLDER = ROOT / "research" / "earnings-forecast"
HIST = ROOT / "research" / "2026-10-02-earnings-reactions.json"
LAST = 8
TOP = 12


def history(root: Path = ROOT) -> dict[str, list[dict]]:
    """symbol -> its past reactions, oldest first: {day, timing, gap, move, implied}."""
    out: dict[str, dict[str, dict]] = {}
    try:
        for e in json.loads((root / "research" / HIST.name).read_text())["events"]:
            if e.get("gap") is not None:
                out.setdefault(e["symbol"], {})[e["reaction_day"]] = {
                    "day": e["reaction_day"], "timing": e.get("timing"), "gap": e["gap"], "move": e.get("move"),
                    "implied": e.get("implied_move")}
    except Exception:
        pass
    for f in sorted((root / "research" / "earnings-forecast").glob("20??-??-??.json")):
        try:
            rep = json.loads(f.read_text())
        except Exception:
            continue
        for r in rep.get("reports", []):
            res = r.get("result")
            if not res:
                continue
            gap = res.get("gap_pre", res.get("gap"))
            if gap is None:
                continue
            out.setdefault(r["symbol"], {})[r["reaction_day"]] = {
                "day": r["reaction_day"], "timing": "after close" if r.get("time") == "post-market" else "before open",
                "gap": gap, "move": res.get("move"), "implied": r.get("implied_move")}
    return {s: [v[d] for d in sorted(v)] for s, v in out.items()}


def notes(root: Path = ROOT) -> dict[str, dict]:
    p = root / "research" / "earnings-forecast" / "companies.json"
    return json.loads(p.read_text()) if p.exists() else {}


def reasons(root: Path = ROOT) -> dict[str, list[dict]]:
    p = root / "research" / "earnings-forecast" / "reasons.jsonl"
    out: dict[str, dict[str, dict]] = {}
    if p.exists():
        for line in p.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                out.setdefault(r["symbol"], {})[r["date"]] = r
    return {s: [v[d] for d in sorted(v)] for s, v in out.items()}


def page(sym: str, hist: list[dict], note: dict | None, why: list[dict]) -> str:
    md = [f"## {sym}", ""]
    if note:
        for key, name in (("watch", "市场盯什么"), ("habit", "指引习惯"), ("last", "上次为什么涨跌")):
            if note.get(key):
                md.append(f"- {name}：{note[key]}")
        for n in note.get("notes", [])[-3:]:
            md.append(f"- {n.get('date', '')}：{n.get('text', '')}")
    else:
        md.append("- 还没有手写的档案（市场盯什么、指引习惯）：复盘时补。")
    last = hist[-LAST:]
    if last:
        ups = sum(h["gap"] > 0 for h in last)
        streak, sign = 0, None
        for h in reversed(last):
            s = h["gap"] > 0
            if sign is None or s == sign:
                sign, streak = s, streak + 1
            else:
                break
        imp = [abs(h["gap"]) / h["implied"] for h in last if h.get("implied")]
        md += [f"- 最近 {len(last)} 次：开盘涨 {ups} 次、跌 {len(last) - ups} 次；最近连续 {streak} 次{'涨' if sign else '跌'}"
               + (f"；开盘幅度是期权定价的 {statistics.median(imp):.1f} 倍（中位数）" if imp else ""), "",
               "| 反应日 | 时间 | 开盘 | 收盘 | 期权定价 |", "|---|---|---|---|---|"]
        for h in reversed(last):
            md.append(f"| {h['day']} | {'盘后' if h.get('timing') == 'after close' else '盘前'} | {h['gap']:+.1%} | "
                      + ("—" if h.get("move") is None else f"{h['move']:+.1%}") + " | "
                      + ("—" if not h.get("implied") else f"{h['implied']:.1%}") + " |")
    else:
        md.append("- 没有过去的反应数据（不在 98 只大公司里，也还没被我们记过分）。")
    for r in why[-2:]:
        md.append(f"- 复盘 {r['date']}：{r.get('reason', '')}")
    return "\n".join(md)


def top_symbols(day: str, root: Path = ROOT, n: int = TOP) -> list[str]:
    p = root / "research" / "earnings-forecast" / f"{day}.json"
    if not p.exists():
        return []
    return [r["symbol"] for r in json.loads(p.read_text()).get("reports", [])[:n]]


def render(symbols: list[str], day: str, root: Path = ROOT) -> str:
    hist, nts, why = history(root), notes(root), reasons(root)
    head = [f"# 公司档案 {day}", "",
            "判断前先读：每家市场盯的东西、指引习惯、上次为什么涨跌，以及它自己最近几次财报的开盘反应（开盘对财报前最后一笔）。"
            "98 只大公司的整体数据里，过去涨跌的方向几乎预测不了下一次（整体开盘涨 51.6%，连跌两次后 45.5%），"
            "所以看这些是为了理解「这家公司要什么才会涨」，不是按连涨连跌下注。", ""]
    return "\n".join(head + [page(s, hist.get(s, []), nts.get(s), why.get(s, [])) + "\n" for s in symbols])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("symbols", nargs="*")
    a = ap.parse_args()
    syms = [s.upper() for s in a.symbols] or top_symbols(a.date)
    out = FOLDER / "profiles" / f"{a.date}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(syms, a.date))
    print(f"wrote {out} ({len(syms)} companies)")


if __name__ == "__main__":
    main()
