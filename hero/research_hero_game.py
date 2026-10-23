"""The hero game on the backtests: which rule, exit and bet size reach $10,000 from $500 most often (owner, 10-23).

The ledgers' own objective (docs/sleeves-plan.md): a round starts at $500, ends at $10,000 (20x, a hero) or under 10%
(a zero), and restarts. Means and win rates do not answer that; this does. Every per-trade series the research
folder holds (research_verdict maps the same files) is replayed as rounds: the ledger bets `f` of itself a trade
(half, as the rules do today, or all of it), sequentially, costs as each study assumed. Per config: rounds, heroes,
the hero rate, trades a round, the peak multiple a round reaches (median / 90th percentile / best), the share of
rounds that touch 2x, and the price of a hero (rounds x the start / heroes). The one-ticket lottery study
(research/2026-10-07-lotto0dte.json) is read as single-trade rounds: a ticket either reaches 20x or not.
Dip-week and pead replay the rule's own pick (the biggest gap-down of the day). No API. Research only."""
from __future__ import annotations

import json
import statistics
import sys
from datetime import date
from pathlib import Path

from hero.research_verdict import load, minmove, pead, streak

ROOT = Path(__file__).resolve().parent.parent
ZERO, TARGET, START = 0.1, 20.0, 500.0
FRACTIONS = (0.5, 1.0)
EXITS = ("take3_trail", "take5_trail", "take3", "take5", "trail2", "hold")


def game(rets: list[float], f: float, zero: float = ZERO, target: float = TARGET) -> dict:
    """Replay rounds: the ledger bets f of itself each trade; a round ends at `target` (a hero) or under `zero`."""
    led, peak, n, heroes, zeros, peaks, lens = 1.0, 1.0, 0, 0, 0, [], []
    for r in rets:
        led *= 1 + f * r
        peak, n = max(peak, led), n + 1
        if led >= target or led < zero:
            heroes += led >= target
            zeros += led < zero
            peaks.append(peak)
            lens.append(n)
            led, peak, n = 1.0, 1.0, 0
    rounds = heroes + zeros
    sp = sorted(peaks)
    return {"n": len(rets), "f": f, "rounds": rounds, "heroes": heroes, "zeros": zeros,
            "p_hero": heroes / rounds if rounds else None,
            "trades_per_round": statistics.mean(lens) if lens else None,
            "x2_rounds": sum(p >= 2 for p in peaks) / rounds if rounds else None,
            "peak_median": statistics.median(sp) if sp else None, "peak_p90": sp[int(0.9 * (len(sp) - 1))] if sp else None,
            "peak_max": sp[-1] if sp else None, "cost_per_hero": rounds * START / heroes if heroes else None,
            "open_peak": peak}


def dipweek_pick(doc: dict, leg: str, exit_: str) -> list[float]:
    """The rule's own pick: the pool name that opened the most below its prior close, one a day."""
    by: dict[str, dict] = {}
    for r in doc["rows"]:
        if r.get("leg") == leg and r.get("res", {}).get(exit_) is not None:
            if r["day"] not in by or r["gap"] < by[r["day"]]["gap"]:
                by[r["day"]] = r
    return [by[d]["res"][exit_] for d in sorted(by)]


def series(docs: dict) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    mm = docs.get("minmove")
    if mm:
        for und in ("SPY", "QQQ", "TSLA", "NVDA"):
            for off in (0.003, 0.006):
                for ex in EXITS:
                    out[f"末日单 {und} 10:00 {off:.1%} {ex}"] = [r for _, r in minmove(mm, und, "10:00", off, ex)]
        for ex in ("take3_trail", "take5", "hold"):
            out[f"末日单 SPY 0.3% 只做 10:00 离开盘 ≥0.5% {ex}"] = [r for _, r in minmove(mm, "SPY", "10:00", 0.003, ex, 0.005)]
    st = docs.get("streak")
    if st:
        for key in ("C|0.003|3", "C|0.006|3", "C|0.003|5"):
            out[f"SPY 昨跌 1% 后买涨 {key}"] = [r for _, r in streak(st, "SPY", key, -0.01)]
    dw = docs.get("dipweek")
    if dw:
        for leg, name in (("otm3", "价外 3%"), ("atm", "平值")):
            for ex in ("d5", "take2", "trail2"):
                out[f"抄底给一周 {name} {ex}"] = dipweek_pick(dw, leg, ex)
    pd_ = docs.get("pead")
    if pd_:
        for ex in ("close_d3", "trail2"):
            out[f"财报后漂移 高开 ≥10% {ex}"] = [r for _, r in sorted(pead(pd_, 0.10, ex))]
    lo = docs.get("lotto")
    if lo:
        for name, keep in (("全部", lambda r: True), ("SPY / QQQ", lambda r: r["symbol"] in ("SPY", "QQQ")), ("个股", lambda r: r["symbol"] not in ("SPY", "QQQ"))):
            rows = [r for r in lo["rows"] if keep(r) and r.get("best_x") is not None]
            # one ticket a round: 20x reached (sold there) or whatever it was at the close
            out[f"一分钱末日彩票 {name}（单张 20 倍止盈）"] = [19.0 if r["best_x"] >= 20 else float(r.get("hold") or -1.0) for r in rows]
    return out


def table(docs: dict) -> dict[str, dict[str, dict]]:
    return {name: {f"f{int(f * 100)}": game(rets, f) for f in FRACTIONS} for name, rets in series(docs).items() if rets}


def fmt(x, kind="pct") -> str:
    if x is None:
        return "—"
    if kind == "pct":
        return f"{x:.1%}"
    if kind == "x":
        return f"{x:.1f}x"
    if kind == "usd":
        return f"${x:,.0f}"
    return f"{x:.0f}"


def markdown(t: dict, today: str) -> str:
    out = [f"# hero 游戏：哪条规则、怎么押最常到 20 倍 {today}", "",
           "账本的目标是每轮从 $500 到 $10,000（20 倍），低于 10% 归零重来；平均每 $1 和胜率回答不了这个，这张表回答。把各研究文件的逐笔收益按顺序"
           "重放成轮：每笔押账本的一半（现行）或押满，成本按各研究原来的假设（末日单买多付 10%、卖少拿 10%，抄底给一周各 5%，彩票按价位）。"
           "「每个 hero 的成本」= 轮数 × $500 ÷ hero 数，是这条规则作为 hero 引擎的价钱；低于 $10,000 才是正期望的 hero 游戏。"
           "抄底给一周、财报后漂移按规则自己的选法（当天低开最多的那只）重放。只研究，不改交易。", "",
           "| 规则 | 笔数 | 押法 | 轮数 | 到 20 倍 | hero 率 | 每轮笔数 | 到过 2 倍的轮 | 峰值中位 / 九成 / 最高 | 每个 hero 的成本 |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for name, by_f in t.items():
        for key, g in by_f.items():
            out.append(f"| {name} | {g['n']} | 押{'满' if g['f'] >= 1 else '一半'} | {g['rounds']} | {g['heroes']} | {fmt(g['p_hero'])} | "
                       f"{fmt(g['trades_per_round'], 'n')} | {fmt(g['x2_rounds'])} | {fmt(g['peak_median'], 'x')} / {fmt(g['peak_p90'], 'x')} / {fmt(g['peak_max'], 'x')} | "
                       f"{fmt(g['cost_per_hero'], 'usd')} |")
    out += ["", "## 读法", "",
            "- 末日单押一半几乎出不了 hero（SPY 0.3% 现行出场 0/58），押满才有 1–3%，但一轮只有 2 笔、每个 hero 要烧两三万。",
            "- 抄底给一周是唯一每个 hero 成本低于 $10,000 的（押一半 4–9%，10 笔一轮），但它的回测池只有 28 只流动性最好的、成本按 5%；实盘池 100 只、小票的价差能到 10% 以上，那时平均就转负。",
            "- 一分钱彩票单张到 20 倍 1.7%，和押满的末日单差不多，但没有「连赢两把」的路径，不要。",
            "- 财报后漂移样本太少（34 笔、1–2 轮），峰值 23 倍，留着看。"]
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    today = argv[1] if len(argv) > 1 else date.today().isoformat()
    docs = {}
    for k, f in (("minmove", "2026-10-16-zdte-minmove.json"), ("streak", "2026-10-08-zdte-streak.json"),
                 ("dipweek", "2026-10-16-dip-week.json"), ("pead", "2026-10-13-pead.json"), ("lotto", "2026-10-07-lotto0dte.json")):
        try:
            docs[k] = load(f)
        except FileNotFoundError:
            pass
    t = table(docs)
    md = markdown(t, today)
    out = ROOT / "research" / f"{today}-hero-game"
    out.with_suffix(".md").write_text(md)
    out.with_suffix(".json").write_text(json.dumps({"generated": today, "zero": ZERO, "target": TARGET, "table": t}, ensure_ascii=False, indent=1))
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
