"""Is low implied volatility an opportunity to buy options? (owner, 10-29: 「做个测试 iv 低是机会吗」)

VIX is the 30-day implied volatility of the S&P 500. For every session since 1991: the VIX close, its rank in the
past year (share of the last 252 closes below it), and what the index then did over the next 21 sessions. An at-the-
money option priced at that VIX costs about 0.8 * VIX * sqrt(30/365) of the index for the straddle and half that for a
call or a put, so held to the end its return per $1 is |move| / straddle - 1 (the straddle), max(move, 0) / call - 1
(the call) and the same for the put. Realized volatility = the next 21 sessions' daily log returns, annualized.

Caveat: VIX includes the skew premium and runs a point or two above at-the-money implied volatility, so the cost is
slightly overstated for every group alike; the realized / implied ratio is reported so the size of that doubt is
visible. hero.robust runs on one sample a month (every 21st session) so overlapping windows are not counted twice.
Record only. Run: python -m hero.research_low_iv
"""

from __future__ import annotations

import math
import statistics
import sys
from datetime import date

from hero import robust

H = 21
T = 30 / 365
RANK_DAYS = 252
LEVELS = ((0, 12), (12, 15), (15, 20), (20, 30), (30, 999))
RANKS = ((0.0, 0.2, "一年里最低的 20%"), (0.2, 0.5, "20%–50%"), (0.5, 0.8, "50%–80%"), (0.8, 1.01, "一年里最高的 20%"))


def rows(spx: dict[str, float], vix: dict[str, float], start: str = "1991-01-01") -> list[dict]:
    days = sorted(d for d in vix if d in spx)
    out = []
    for i in range(RANK_DAYS, len(days) - H):
        d = days[i]
        if d < start:
            continue
        v, s0, s1 = vix[d] / 100, spx[d], spx[days[i + H]]
        r = s1 / s0 - 1
        logs = [math.log(spx[days[k]] / spx[days[k - 1]]) for k in range(i + 1, i + H + 1)]
        rv = statistics.pstdev(logs) * math.sqrt(252)
        past = [vix[days[k]] for k in range(i - RANK_DAYS, i)]
        straddle = 0.8 * v * math.sqrt(T)
        out.append({"day": d, "i": i, "vix": vix[d], "rank": sum(p < vix[d] for p in past) / len(past), "move": r,
                    "rv_over_iv": rv / v, "straddle": abs(r) / straddle - 1,
                    "call": max(r, 0.0) / (straddle / 2) - 1, "put": max(-r, 0.0) / (straddle / 2) - 1})
    return out


def table(rs: list[dict]) -> tuple[dict, list[str]]:
    groups = {"全部": rs}
    for lo, hi in LEVELS:
        groups[f"VIX {lo}–{hi if hi < 999 else '以上'}"] = [r for r in rs if lo <= r["vix"] < hi]
    for lo, hi, name in RANKS:
        groups[f"VIX 在{name}"] = [r for r in rs if lo <= r["rank"] < hi]
    out, md = {}, ["| 情况 | 天数 | 真实波动 ÷ 隐含（中位数） | 真实超过隐含的比例 | 跨式：平均 | 中位数 | 看涨：平均 | 中位数 | 赚的比例 | 看跌：平均 | 标普之后 21 天 |",
                   "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, g in groups.items():
        if not g:
            continue
        m = lambda k: statistics.mean(r[k] for r in g)
        md_ = lambda k: statistics.median(r[k] for r in g)
        row = {"n": len(g), "ratio": md_("rv_over_iv"), "over": sum(r["rv_over_iv"] > 1 for r in g) / len(g),
               "straddle": m("straddle"), "straddle_med": md_("straddle"), "call": m("call"), "call_med": md_("call"),
               "call_win": sum(r["call"] > 0 for r in g) / len(g), "put": m("put"), "move": m("move")}
        out[name] = row
        md.append(f"| {name} | {row['n']} | {row['ratio']:.2f} | {row['over']:.0%} | {row['straddle']:+.0%} | {row['straddle_med']:+.0%} | "
                  f"{row['call']:+.0%} | {row['call_med']:+.0%} | {row['call_win']:.0%} | {row['put']:+.0%} | {row['move']:+.1%} |")
    return out, md


def monthly(rs: list[dict]) -> list[dict]:
    """One sample every 21 sessions, so the robustness check does not count overlapping windows."""
    out, last = [], -H
    for r in rs:
        if r["i"] - last >= H:
            out.append(r)
            last = r["i"]
    return out


def report(spx: dict[str, float], vix: dict[str, float]) -> tuple[dict, str]:
    rs = rows(spx, vix)
    tab, md = table(rs)
    ms = monthly(rs)
    end = rs[-1]["day"]
    rob = {}
    for key, name in (("straddle", "两边买（跨式）"), ("call", "买看涨")):
        rob[f"{name} · VIX 在一年里最低的 20%"] = robust.summary([(r["day"], r[key]) for r in ms if r["rank"] < 0.2], end)
        rob[f"{name} · VIX 在一年里最高的 20%"] = robust.summary([(r["day"], r[key]) for r in ms if r["rank"] >= 0.8], end)
        rob[f"{name} · 全部"] = robust.summary([(r["day"], r[key]) for r in ms], end)
    head = [f"# IV 低是买期权的机会吗？ {date.today().isoformat()}", "",
            f"标普 500 和 VIX 日线（Yahoo），{rs[0]['day']} 到 {end} 的每个交易日：按当天 VIX 的水平和它在过去一年里的位置分组，"
            "假设按 VIX 定价买平值期权，拿 21 个交易日（约 30 天）到期。跨式成本 ≈ 0.8 × VIX × √(30/365) × 指数，看涨、看跌各一半。"
            "VIX 含偏度溢价，一般比平值隐含波动率高一两个点，所以期权成本略被高估，各组一样；"
            "「真实波动 ÷ 隐含」那一列可以看这个偏差有多大。稳健性检查每月只取一个样本，避免重叠的窗口重复计数。只研究，不改交易。", ""]
    md_all = head + md + ["", robust.table(rob, "稳健性检查（每月一个样本）")]
    return {"groups": tab, "robust": rob, "end": end, "n": len(rs)}, "\n".join(md_all) + "\n"


def main() -> None:
    from hero.research_earnings_control import yahoo
    from hero.research_ideas import save
    spx = {d: b["c"] for d, b in yahoo("%5EGSPC", "1989-01-01").items()}
    vix = {d: b["c"] for d, b in yahoo("%5EVIX", "1989-01-01").items()}
    rep, md = report(spx, vix)
    save("low-iv", rep, md)


if __name__ == "__main__":
    sys.exit(main())
