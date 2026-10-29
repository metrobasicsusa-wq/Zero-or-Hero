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
SKEW or at most 1 - SKEW of them).

10-29 (owner: 「说明要进化下」): the lines are written after the move, for the names worth looking up, so the sample leans
(38% of its 74 opened up) and a hand-filled "before" field can carry hindsight -- here "peers fell" opened up 0 of 13
and "fell 5%+ into the report" 22% of 18, while over five years of large caps (research/2026-10-20-earnings-rules.json,
mechanical) the same two signs opened up 57% of 279 and 56% of 479. So a sign the five-year backtest can check only
counts when the backtest leans the same way; otherwise it is listed as not supported. And because what decides the
open is the guide against expectations (below: up 13%, above: 86%; an EPS beat: 49%), each researched call also says
where it expects the guide (`guide_call`: above / inline / below), scored here against `guide_vs_est`: whether we can
read the company, apart from whether we can read the market. Record only. Run: python -m hero.earnings_reasons
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
BACKTEST = ROOT / "research" / "2026-10-20-earnings-rules.json"
BT_MIN_N = 30
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
            item = {"implied": r.get("implied_move"), "call": c.get("direction"), "confidence": c.get("confidence"),
                    "evidence": c.get("evidence"), "guide_call": c.get("guide_call")}
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


def backtest(path: Path = BACKTEST) -> dict[str, dict[str, tuple[int, float]]]:
    """key -> value word -> (n, share up at the open) over the five-year large-cap backtest, for the before-the-report
    signs it can compute mechanically (run20, peers), plus "_base" -> (n, share up) of every report."""
    try:
        feats = json.loads(path.read_text())["features"]
    except Exception:
        return {}
    feats = [f for f in feats if f.get("gap") is not None]
    by: dict[str, dict[str, list[bool]]] = {"run20": {}, "peers": {}}
    for f in feats:
        for key in by:
            v = value(f, key)
            if v is not None:
                by[key].setdefault(v, []).append(f["gap"] > 0)
    out = {key: {v: (len(xs), sum(xs) / len(xs)) for v, xs in d.items()} for key, d in by.items()}
    out["_base"] = {"all": (len(feats), sum(f["gap"] > 0 for f in feats) / len(feats))} if feats else {}
    return out


def usable(rows: list[dict], bt: dict | None = None) -> list[str]:
    """Before-the-report signs with enough cases and a clear lean. A sign the five-year backtest can check is listed
    only if the backtest leans the same way (more up than its base for a lean to up, fewer for a lean to down)."""
    bt = backtest() if bt is None else bt
    base = (bt.get("_base") or {}).get("all", (0, 0.5))[1]
    out = []
    for key, name in BEFORE.items():
        for v, n, up, gap, _ in tally(rows, key):
            if n >= MIN_N and (up >= SKEW or up <= 1 - SKEW):
                lean_up = up >= SKEW
                line = f"{name}「{v}」：{n} 次里开盘涨 {up:.0%}（平均 {gap:+.1%}）→ 倾向判{'涨' if lean_up else '跌'}"
                ref = (bt.get(key) or {}).get(v)
                if ref and ref[0] >= BT_MIN_N:
                    if (ref[1] > base) != lean_up:
                        continue                      # the five-year backtest leans the other way: see not_supported()
                    line += f"（五年回测同向：{ref[0]} 次开盘涨 {ref[1]:.0%}）"
                out.append(line)
    return out


def not_supported(rows: list[dict], bt: dict | None = None) -> list[str]:
    """Signs that clear the bar in our lines but lean the other way over five years: hindsight or this season only."""
    bt = backtest() if bt is None else bt
    base = (bt.get("_base") or {}).get("all", (0, 0.5))[1]
    out = []
    for key, name in BEFORE.items():
        for v, n, up, gap, _ in tally(rows, key):
            ref = (bt.get(key) or {}).get(v)
            if n >= MIN_N and (up >= SKEW or up <= 1 - SKEW) and ref and ref[0] >= BT_MIN_N and (ref[1] > base) != (up >= SKEW):
                out.append(f"{name}「{v}」：这里 {n} 次开盘涨 {up:.0%}，但五年回测 {ref[0]} 次开盘涨 {ref[1]:.0%}"
                           f"（全部 {base:.0%}）→ 不用")
    return out


def guide_calls(rows: list[dict]) -> tuple[int, int, list[str]]:
    """Researched calls that said where the guide would land, against where it landed: (n, right, table lines)."""
    done = [r for r in rows if r.get("guide_call") in ("above", "inline", "below") and r.get("guide_vs_est") in ("above", "inline", "below")]
    lines = []
    for r in sorted(done, key=lambda r: (r["date"], r["symbol"]), reverse=True):
        ok = r["guide_call"] == r["guide_vs_est"]
        gap = "待记分" if r.get("gap") is None else f"{r['gap']:+.1%}"
        lines.append(f"| {r['date']} | {r['symbol']} | {WORDS[r['guide_call']]} | {WORDS[r['guide_vs_est']]} | {'✓' if ok else '✗'} | {gap} |")
    return len(done), sum(r["guide_call"] == r["guide_vs_est"] for r in done), lines


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
    bt = backtest()
    md += [f"- {x}" for x in usable(scored, bt)] or ["还没有：样本不够，判断照旧。"]
    no = not_supported(scored, bt)
    if no:
        md += ["", "## 看起来有、但五年回测不支持（不用）", "",
               f"这些行是看完涨跌才写的，名单也偏向值得查的几只（这里 {len(scored)} 次开盘涨 "
               f"{sum(r['up'] for r in scored) / len(scored):.0%}），五年大公司机械回测（research/2026-10-20-earnings-rules.md）方向相反的就不算。", ""]
        md += [f"- {x}" for x in no]
    n_g, right_g, lines_g = guide_calls(rows)
    md += ["", "## 指引判断（10-30 起：细判写 guide_call，看我们能不能读懂公司）", ""]
    if n_g:
        md += [f"{n_g} 次里猜对 {right_g} 次（{right_g / n_g:.0%}）。指引对预期决定开盘：见下面「财报后才知道的 · 指引对预期」。", "",
               "| 日期 | 股票 | 我们猜指引 | 实际指引对预期 | 对不对 | 开盘 |", "|---|---|---|---|---|---|"] + lines_g
    else:
        md.append("还没有：细判从 10-30 起写 guide_call（above / inline / below），复盘时填 guide_vs_est 后在这里记分。")
    for title, keys in (("财报前就知道的", BEFORE), ("财报后才知道的", AFTER)):
        before = keys is BEFORE
        md += ["", f"## {title}", "", "| 迹象 | 情况 | 次数 | 开盘涨的比例 | 平均开盘 | 我们判对 |" + (" 五年回测开盘涨 |" if before else ""),
               "|---|---|---|---|---|---|" + ("---|" if before else "")]
        n0 = len(md)
        for key, name in keys.items():
            for v, n, up, gap, right in tally(scored, key):
                ref = (bt.get(key) or {}).get(v) if before else None
                extra = (f" {ref[1]:.0%}（{ref[0]}） |" if ref else " — |") if before else ""
                md.append(f"| {name} | {v} | {n} | {up:.0%} | {gap:+.1%} | {pct(right)} |" + extra)
        if len(md) == n0:
            md.append("| — | 还没有已记分的复盘 | | | | |" + (" |" if before else ""))
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
