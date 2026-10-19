"""Robustness checks every study runs on its trades (notes/lessons.md, 2026-10-15 and 10-16).

A study hands over its trades as (day, return per $1) pairs, e.g. ("2025-03-07", 0.42) for +42%.
summary() gives the full-period mean next to the checks that caught us before:

- without the best 3 trades, and the median (is it a few big wins?);
- each calendar year, and the last three months (does it still work?);
- an extra cost on both sides (does the edge survive worse fills?).

flags() turns those into plain warnings, and table() prints them as one markdown table, so a new idea
cannot be reported on its mean alone. price_basis() catches raw minute prices compared with split-adjusted
daily prices (10-16: 129 NVDA/AVGO/... "breakouts" were split artefacts).
"""

from __future__ import annotations

import statistics
from datetime import date, timedelta

RECENT = 92          # "the last three months", in calendar days
DROP_BEST = 3
EXTRA_COST = 0.05    # extra slippage on each side, on top of what the study already paid
FEW = 30             # fewer trades than this: say so
GAP = 0.05           # a period that disagrees by less than this (per $1) is noise, not a warning


def with_cost(r: float, side: float) -> float:
    """A return per $1 after paying `side` more to get in and getting `side` less to get out."""
    return max((1 + r) * (1 - side) / (1 + side) - 1, -1.0)


def summary(xs: list[tuple[str, float]], end: str | None = None, recent_days: int = RECENT,
            extra_cost: float = EXTRA_COST) -> dict | None:
    """Mean, the checks, and flags for (day, return per $1) trades. `end` is the last day of the
    sample (default: the last trade), so "the last three months" means the same thing across groups."""
    xs = [(d, float(r)) for d, r in xs if r is not None]
    if not xs:
        return None
    v = sorted(r for _, r in xs)
    end = end or max(d for d, _ in xs)
    cut = (date.fromisoformat(end[:10]) - timedelta(days=recent_days)).isoformat()
    years: dict[str, list[float]] = {}
    for d, r in xs:
        years.setdefault(d[:4], []).append(r)
    recent = [r for d, r in xs if d >= cut]
    costly = [with_cost(r, extra_cost) for r in v]
    s = {
        "n": len(v),
        "mean": statistics.mean(v),
        "trim": statistics.mean(v[:-DROP_BEST]) if len(v) > DROP_BEST else None,
        "median": statistics.median(v),
        "win": sum(r > 0 for r in v) / len(v),
        "x2": sum(r >= 1 for r in v) / len(v),
        "years": {y: (statistics.mean(rs), len(rs)) for y, rs in sorted(years.items())},
        "recent": (statistics.mean(recent), len(recent)) if recent else None,
        "extra_cost": extra_cost,
        "costly": statistics.mean(costly),
    }
    s["flags"] = flags(s)
    return s


def flags(s: dict) -> list[str]:
    """Plain warnings for a summary; empty when nothing stands out."""
    out = []
    mean = s["mean"]
    if s["n"] < FEW:
        out.append(f"只有 {s['n']} 笔，样本少")
    if mean > 0 and s["trim"] is not None and s["trim"] <= 0:
        out.append(f"去掉最好 {DROP_BEST} 笔就不赚（{s['trim']:+.0%}），靠少数几笔")
    if mean > 0 and s["median"] < 0:
        out.append(f"中位数 {s['median']:+.0%}，多数笔亏")
    rec = s["recent"]
    if rec and rec[1] >= 5 and (rec[0] > 0) != (mean > 0) and abs(rec[0] - mean) >= GAP:
        out.append(f"最近 3 个月 {rec[0]:+.0%}（{rec[1]} 笔），和全期方向相反")
    big = {y: m for y, (m, n) in s["years"].items() if n >= 5}
    if len(big) >= 2 and len({m > 0 for m in big.values()}) > 1 and max(big.values()) - min(big.values()) >= 2 * GAP:
        out.append("各年份不一致：" + "、".join(f"{y} {m:+.0%}" for y, m in big.items()))
    if mean > 0 and s["costly"] <= 0:
        out.append(f"买卖再各多 {s['extra_cost']:.0%} 成本就不赚（{s['costly']:+.0%}）")
    return out


def fragile(s: dict | None) -> bool:
    """True when a positive mean leans on a few trades, an old period or cheap fills."""
    return bool(s) and s["mean"] > 0 and any(
        f.startswith(("去掉最好", "最近", "各年份", "买卖再")) for f in s["flags"])


def _pct(x) -> str:
    return "—" if x is None else f"{x:+.0%}"


def _part(p) -> str:
    return "—" if not p else f"{p[0]:+.0%}（{p[1]}）"


def table(rows: dict[str, dict | None], title: str = "稳健性检查") -> str:
    """One markdown table: a row per group, every check in its own column, warnings last."""
    out = [f"## {title}", "",
           f"每 $1 的平均收益；去掉最好 {DROP_BEST} 笔、中位数、各年份、最近 3 个月、买卖再各多 {EXTRA_COST:.0%} 成本，"
           "看平均数是不是靠几笔大赚、过去的年份或便宜的成交撑起来的。", "",
           f"| 情况 | 笔数 | 平均 | 去掉最好 {DROP_BEST} 笔 | 中位数 | 赚钱比例 | 各年份 | 最近 3 个月 | 多付成本后 | 提醒 |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for name, s in rows.items():
        if not s:
            out.append(f"| {name} | 0 | — | — | — | — | — | — | — | 没有成交 |")
            continue
        yrs = "、".join(f"{y} {_part(p)}" for y, p in s["years"].items())
        out.append(f"| {name} | {s['n']} | {_pct(s['mean'])} | {_pct(s['trim'])} | {_pct(s['median'])} | "
                   f"{s['win']:.0%} | {yrs} | {_part(s['recent'])} | {_pct(s['costly'])} | "
                   f"{'；'.join(s['flags']) or '—'} |")
    return "\n".join(out) + "\n"


def price_basis(pairs: list[tuple[str, str, float, float]], tol: float = 0.03) -> list[dict]:
    """(day, symbol, minute-bar price, daily-bar price) for the same moment, e.g. the 09:30 open.
    Returns the pairs that differ by more than `tol`: one side is split-adjusted and the other raw,
    so the two must not be compared until both are on one basis."""
    bad = []
    for day, sym, raw, adj in pairs:
        if raw and adj and abs(raw / adj - 1) > tol:
            bad.append({"day": day, "symbol": sym, "minute": raw, "daily": adj, "ratio": round(raw / adj, 4)})
    return bad
