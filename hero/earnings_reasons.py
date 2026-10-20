"""Why did it go up, why did it go down? (owner, 10-20: earnings season, learn every day.)

After each batch of reports is scored, the 15:05 routine looks up the main ones and appends one line per report to
research/earnings-forecast/reasons.jsonl:

  {"date": the forecast date, "symbol",
   before the report (usable for the next calls):
     "last_guide": what the company did to its guidance the quarter before ("raise" / "keep" / "cut" / null),
     "run20": the 20-session return into the report, "peers": how same-industry reports that week opened ("up" / "down" / "mixed" / null),
   after the report (explains the move, not known at 16:00):
     "eps" / "rev": "beat" / "inline" / "miss" / null, "guide": "raise" / "keep" / "cut" / "none" / null,
     "guide_vs_est": "above" / "inline" / "below" / null, "tags": ["成本", "估值高", ...],
   "reason": one line with its source}

The outcome is not copied: it is read from research/earnings-forecast/<date>.json (the open against the last trade
before the report, the close) and the judgment file, so a re-score never leaves two versions. This module tallies the
lines into research/earnings-forecast/lessons.md: how often each kind of report opened up, how often our calls were
right in each, and which before-the-report signs have enough cases to use (n >= MIN_N and up at the open in at least
SKEW or at most 1 - SKEW of them). Record only. Run: python -m hero.earnings_reasons
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FOLDER = ROOT / "research" / "earnings-forecast"
MIN_N = 15
SKEW = 0.65
BEFORE = {"last_guide": "上次指引", "run20": "进财报前 20 天", "peers": "同行这周的反应"}
AFTER = {"eps": "EPS 对预期", "rev": "收入对预期", "guide": "这次指引", "guide_vs_est": "指引对预期", "combo": "业绩 + 指引"}
WORDS = {"beat": "超预期", "inline": "符合", "miss": "不及", "raise": "上调", "keep": "维持", "cut": "下调", "none": "没给",
         "above": "高于预期", "below": "低于预期", "up": "涨", "down": "跌", "mixed": "有涨有跌"}


def load_reasons(folder: Path = FOLDER) -> list[dict]:
    p = folder / "reasons.jsonl"
    if not p.exists():
        return []
    out = {}
    for line in p.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            out[(r["date"], r["symbol"])] = r      # a later line for the same report replaces the earlier one
    return list(out.values())


def outcomes(folder: Path = FOLDER) -> dict[tuple[str, str], dict]:
    """(date, symbol) -> the scored result and our call, from the forecast and judgment files."""
    out = {}
    for f in sorted(folder.glob("20??-??-??.json")):
        try:
            rep = json.loads(f.read_text())
        except Exception:
            continue
        j = folder / f"{rep.get('date', f.stem)}.judgment.json"
        calls = {c["symbol"]: c for c in json.loads(j.read_text()).get("calls", [])} if j.exists() else {}
        for r in rep.get("reports", []):
            res, c = r.get("result"), calls.get(r["symbol"], {})
            item = {"implied": r.get("implied_move"), "call": c.get("direction"), "confidence": c.get("confidence")}
            if res:
                gap = res.get("gap_pre", res.get("gap"))
                item.update({"gap": gap, "move": res.get("move"), "up": gap is not None and gap > 0})
                if c.get("direction") in ("up", "down") and gap is not None:
                    item["right"] = (c["direction"] == "up") == (gap > 0)
            out[(rep.get("date", f.stem), r["symbol"])] = item
    return out


def run_bucket(x) -> str | None:
    if x is None:
        return None
    return "跌了 5% 以上" if x <= -0.05 else "涨了 5% 以上" if x >= 0.05 else "±5% 以内"


def value(r: dict, key: str) -> str | None:
    if key == "run20":
        return run_bucket(r.get("run20"))
    if key == "combo":
        e, g = r.get("eps"), r.get("guide")
        if not e or not g:
            return None
        return f"{WORDS.get(e, e)} + 指引{WORDS.get(g, g)}"
    v = r.get(key)
    return None if v is None else WORDS.get(v, v)


def tally(rows: list[dict], key: str) -> list[tuple[str, int, float, float | None, float | None]]:
    """(value, n, share up at the open, mean open gap, share of our calls right) for each value of `key`."""
    by: dict[str, list[dict]] = {}
    for r in rows:
        v = value(r, key)
        if v is not None and r.get("gap") is not None:
            by.setdefault(v, []).append(r)
    out = []
    for v, rs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        rights = [r["right"] for r in rs if "right" in r]
        out.append((v, len(rs), sum(r["up"] for r in rs) / len(rs), statistics.mean(r["gap"] for r in rs),
                    sum(rights) / len(rights) if rights else None))
    return out


def usable(rows: list[dict]) -> list[str]:
    """Before-the-report signs with enough cases and a clear lean."""
    out = []
    for key, name in BEFORE.items():
        for v, n, up, gap, _ in tally(rows, key):
            if n >= MIN_N and (up >= SKEW or up <= 1 - SKEW):
                out.append(f"{name}「{v}」：{n} 次里开盘涨 {up:.0%}（平均 {gap:+.1%}）→ 倾向判{'涨' if up >= SKEW else '跌'}")
    return out


def render(folder: Path = FOLDER) -> str:
    reasons, res = load_reasons(folder), outcomes(folder)
    rows = [{**r, **res.get((r["date"], r["symbol"]), {})} for r in reasons]
    scored = [r for r in rows if r.get("gap") is not None]
    pct = lambda v: "—" if v is None else f"{v:.0%}"
    md = ["# 财报复盘：为什么涨、为什么跌", "",
          f"每批财报记分后，查主要几只的业绩、指引和原因，记在 reasons.jsonl（{len(reasons)} 条，已记分 {len(scored)} 条）。"
          "「开盘涨」按反应日开盘对财报前最后一笔。「财报前就知道的」可以用来下次判断；「财报后才知道的」只解释涨跌。"
          f"一个迹象要有 {MIN_N} 次以上、开盘涨（或跌）的比例到 {SKEW:.0%} 才算可用。只记录，不交易。", "",
          "## 可用规则", ""]
    md += [f"- {x}" for x in usable(scored)] or ["还没有：样本不够，判断照旧。"]
    for title, keys in (("财报前就知道的", BEFORE), ("财报后才知道的", AFTER)):
        md += ["", f"## {title}", "", "| 迹象 | 情况 | 次数 | 开盘涨的比例 | 平均开盘 | 我们判对 |", "|---|---|---|---|---|---|"]
        n0 = len(md)
        for key, name in keys.items():
            for v, n, up, gap, right in tally(scored, key):
                md.append(f"| {name} | {v} | {n} | {up:.0%} | {gap:+.1%} | {pct(right)} |")
        if len(md) == n0:
            md.append("| — | 还没有已记分的复盘 | | | | |")
    tags: dict[str, list[dict]] = {}
    for r in scored:
        for t in r.get("tags") or []:
            tags.setdefault(t, []).append(r)
    if tags:
        md += ["", "## 常见原因（标签）", "", "| 原因 | 次数 | 开盘涨的比例 |", "|---|---|---|"]
        md += [f"| {t} | {len(rs)} | {sum(r['up'] for r in rs) / len(rs):.0%} |" for t, rs in sorted(tags.items(), key=lambda kv: -len(kv[1]))]
    md += ["", "## 每一条（新的在前）", "", "| 日期 | 股票 | 我们判 | 开盘 | 收盘 | 业绩 / 指引 | 原因 |", "|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: (r["date"], r["symbol"]), reverse=True):
        call = {"up": "涨", "down": "跌"}.get(r.get("call"), "—")
        if "right" in r:
            call += " ✓" if r["right"] else " ✗"
        gap = "待记分" if r.get("gap") is None else f"{r['gap']:+.1%}"
        move = "—" if r.get("move") is None else f"{r['move']:+.1%}"
        what = "、".join(x for x in (value(r, "combo") or (value(r, "eps") and f"EPS {value(r, 'eps')}"),
                                     value(r, "guide_vs_est") and f"指引{value(r, 'guide_vs_est')}") if x) or "—"
        md.append(f"| {r['date']} | {r['symbol']} | {call} | {gap} | {move} | {what} | {r.get('reason', '')} |")
    return "\n".join(md) + "\n"


def main() -> None:
    out = FOLDER / "lessons.md"
    out.write_text(render())
    print(f"wrote {out}")


if __name__ == "__main__":
    sys.exit(main())
