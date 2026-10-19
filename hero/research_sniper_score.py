"""The sniper's scorecard: do higher confidence numbers pick better? (owner, 2026-10-15: rank by them.)

Every journal-s500/sniper/YYYY-MM-DD.json lists the day's candidates (rank, symbol, direction, confidence; files
before 10-15 only have the one pick). For each candidate whose day has closed: the stock's day from the open in the
picked direction -- the close (open -> close), the best point (high for a call, low for a put) and whether it moved
3% or more the right way at some point (roughly where a 0.10-delta option can double) -- and, for the one traded,
the sniper ledgers' booked result. Grouped by confidence and by rank. Stock moves stand in for option prices, which
for the untraded candidates are not known. From 10-20 the files also list `skipped`: every scanned name with news that
was looked at and not made a candidate, in the direction of its gap, with a reason tag (owner, 10-19, after AMZN ran
+3.9% from a 2% gap we set aside as "analyst news"): they are scored the same way and set against the candidates, so
the names we pass over are measured too. Written to research/sniper/scorecard.md. Run: python -m hero.research_sniper_score
"""

from __future__ import annotations

import json
import statistics
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUCKETS = ((0.0, 0.2, "< 0.2"), (0.2, 0.3, "0.2–0.3"), (0.3, 0.4, "0.3–0.4"), (0.4, 1.01, ">= 0.4"))
HIT = 0.03


def picks(root: Path = ROOT) -> list[dict]:
    out = []
    for f in sorted((root / "journal-s500" / "sniper").glob("20??-??-??.json")):
        d = json.loads(f.read_text())
        day = d.get("date") or f.stem
        cands = d.get("candidates") or ([{**d["sniper"], "rank": 1}] if d.get("sniper") else [])
        traded = (d.get("sniper") or {}).get("symbol")
        for c in cands:
            if c.get("symbol") and c.get("direction") in ("call", "put"):
                out.append({"day": day, "symbol": c["symbol"], "direction": c["direction"], "rank": int(c.get("rank") or 0),
                            "confidence": float(c.get("confidence") or 0), "traded": c["symbol"] == traded, "skipped": False})
        for c in d.get("skipped") or []:
            if c.get("symbol") and c.get("direction") in ("call", "put"):
                out.append({"day": day, "symbol": c["symbol"], "direction": c["direction"], "rank": 0, "confidence": 0.0,
                            "traded": False, "skipped": True, "reason": c.get("reason") or "其他"})
    return out


def score(p: dict, bar: dict) -> dict:
    o, h, l, c = (float(bar[k]) for k in ("o", "h", "l", "c"))
    sign = 1 if p["direction"] == "call" else -1
    best = (h / o - 1) if sign > 0 else (1 - l / o)
    return {**p, "close_move": round(sign * (c / o - 1), 4), "best_move": round(best, 4), "hit": best >= HIT}


def booked(root: Path = ROOT) -> dict[tuple[str, str], float]:
    """(day, symbol) -> the sniper ledgers' return on what they paid, from journal-s500/sleeves.json."""
    try:
        book = json.loads((root / "journal-s500" / "sleeves.json").read_text()).get("sleeves") or {}
    except Exception:
        return {}
    paid, back = {}, {}
    for sid, s in book.items():
        if not sid.startswith("sniper-"):
            continue
        for t in s.get("closed") or []:
            key = (str(t.get("opened", ""))[:10], t.get("und") or "")
            paid[key] = paid.get(key, 0.0) + float(t.get("paid") or 0)
            back[key] = back.get(key, 0.0) + float(t.get("proceeds") or 0)
    return {k: round(back[k] / paid[k] - 1, 3) for k in paid if paid[k]}


def table(rows: list[dict], label: str, groups: list[tuple[str, list[dict]]]) -> list[str]:
    out = [f"| {label} | 次数 | 收盘方向对 | 当天曾顺向 3%+ | 开盘到收盘平均 | 最好点平均 |", "|---|---|---|---|---|---|"]
    for name, rs in groups:
        if not rs:
            continue
        out.append(f"| {name} | {len(rs)} | {sum(r['close_move'] > 0 for r in rs) / len(rs):.0%} | {sum(r['hit'] for r in rs) / len(rs):.0%} | "
                   f"{statistics.mean(r['close_move'] for r in rs):+.1%} | {statistics.mean(r['best_move'] for r in rs):+.1%} |")
    return out


def report(rows: list[dict], real: dict) -> str:
    md = [f"# 开盘狙击记分卡 {date.today().isoformat()}", "",
          "每天盘前给几只候选打的信心分（0–1，主观判断），按当天股价从开盘起朝所选方向走了多少来记分；"
          "「当天曾顺向 3%+」大约是 0.1 delta 的期权能翻倍的位置。只有排第一的那只真下单。股价代替期权价，只是粗看。", ""]
    if not rows:
        return "\n".join(md + ["还没有可记分的候选。", ""])
    cands, skipped = [r for r in rows if not r.get("skipped")], [r for r in rows if r.get("skipped")]
    md += table(cands, "信心分", [(n, [r for r in cands if lo <= r["confidence"] < hi]) for lo, hi, n in BUCKETS]) + [""]
    md += table(cands, "排名", [(f"第 {k} 名", [r for r in cands if r["rank"] == k]) for k in sorted({r["rank"] for r in cands})]) + [""]
    if skipped:
        days = {r["day"] for r in skipped}   # set against the candidates of the same days only
        md += ["看过但没进候选的（按跳空方向记分，10-20 起）：", ""]
        md += table(rows, "分组", [("进了候选（同一批日子）", [r for r in cands if r["day"] in days]), ("看过没选（全部）", skipped)]
                    + [(f"　没选：{k}", [r for r in skipped if r["reason"] == k]) for k in sorted({r["reason"] for r in skipped})]) + [""]
    md += ["| 日期 | 排名 | 股票 | 方向 | 信心 | 开盘到收盘 | 最好点 | 真下单结果 |", "|---|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: (r["day"], r.get("skipped", False), r["rank"])):
        res = real.get((r["day"], r["symbol"]))
        rank = f"没选（{r['reason']}）" if r.get("skipped") else r["rank"]
        md.append(f"| {r['day']} | {rank} | {r['symbol']} | {'看涨' if r['direction'] == 'call' else '看跌'} | {r['confidence']:.2f} | "
                  f"{r['close_move']:+.1%} | {r['best_move']:+.1%} | {'' if not r['traded'] else (f'{res:+.0%}' if res is not None else '没买或还拿着')} |")
    return "\n".join(md) + "\n"


def run(client, root: Path = ROOT) -> tuple[list[dict], str]:
    ps = [p for p in picks(root) if p["day"] < date.today().isoformat()]  # only days that have closed
    rows = []
    for day in sorted({p["day"] for p in ps}):
        todays = [p for p in ps if p["day"] == day]
        bars = client.daily_bars(sorted({p["symbol"] for p in todays}), day, adjustment="raw")
        for p in todays:
            bar = next((b for b in bars.get(p["symbol"], []) if b["t"][:10] == day), None)
            if bar:
                rows.append(score(p, bar))
    return rows, report(rows, booked(root))


def main() -> None:
    from hero.alpaca import Alpaca
    rows, md = run(Alpaca())
    out = ROOT / "research" / "sniper"
    out.mkdir(parents=True, exist_ok=True)
    (out / "scorecard.md").write_text(md)
    (out / "scorecard.json").write_text(json.dumps(rows, ensure_ascii=False) + "\n")
    print(md)


if __name__ == "__main__":
    sys.exit(main())
