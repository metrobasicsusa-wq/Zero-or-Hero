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


def dipweek_pick(doc: dict, leg: str, exit_: str, key: str = "res") -> list[float]:
    """The rule's own pick: the pool name that opened the most below its prior close, one a day. `key` "res10": 10% costs."""
    by: dict[str, dict] = {}
    for r in doc["rows"]:
        if r.get("leg") == leg and (r.get(key) or {}).get(exit_) is not None:
            if r["day"] not in by or r["gap"] < by[r["day"]]["gap"]:
                by[r["day"]] = r
    return [by[d][key][exit_] for d in sorted(by)]


def interval(h: int, n: int) -> tuple[float, float] | None:
    """A 95% Wilson interval for h heroes in n rounds: the rounds are few, the point estimate alone misleads."""
    if not n:
        return None
    z, p = 1.96, h / n
    mid = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / (1 + z * z / n)
    return max(0.0, mid - half), min(1.0, mid + half)


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
                out[f"抄底给一周 {name} {ex}（成本各 10%）"] = dipweek_pick(dw, leg, ex, "res10")
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


def live_rounds(book: dict, label: str) -> list[dict]:
    """The ledgers' own rounds (hero/sleeves.py Book.end_round): how many ended, how, their peaks, the price of a hero."""
    out = []
    for sid, s in (book.get("sleeves") or {}).items():
        if not isinstance(s, dict):
            continue
        rounds = s.get("rounds") or []
        start = float(s.get("start") or START)
        by: dict[str, dict] = {}   # one entry = its take half and its trail half together
        for t in s.get("closed", []):
            if t.get("paid") and t.get("proceeds") is not None:
                e = by.setdefault(str(t.get("tid", "")).rstrip("r"), {"paid": 0.0, "got": 0.0})
                e["paid"] += float(t["paid"])
                e["got"] += float(t["proceeds"])
        tails = [e["got"] / e["paid"] - 1 for e in by.values() if e["paid"] > 0]
        heroes = sum(r["end"] == "hero" for r in rounds)
        stopped = sum(r["end"] == "stopped" for r in rounds)   # audit 10-26 #2: a stopped rule's last round is not a zero
        changed = sum(r["end"] == "rule" for r in rounds)      # audit 10-26 #3: nor one cut short by a rule change
        zeros = len(rounds) - heroes - stopped - changed
        peaks = [r.get("peak", 0) / start for r in rounds]
        x = round(float(s.get("target") or TARGET * start) / start, 2)   # audit 10-26 #3: the ledger's goal, 20x unless set
        out.append({"id": sid, "book": label, "x": x, "rounds": len(rounds), "heroes": heroes, "zeros": zeros, "stopped": stopped,
                    "rule": changed,
                    "x2_rounds": sum(p >= 2 for p in peaks) / len(rounds) if rounds else None,
                    "trades_per_round": statistics.mean(r.get("trades", 0) for r in rounds) if rounds else None,
                    "cost_per_hero": len(rounds) * start / heroes if heroes else None,
                    "trades": len(tails), "x2": sum(r >= 1 for r in tails) / len(tails) if tails else None,
                    "x5": sum(r >= 4 for r in tails) / len(tails) if tails else None,
                    "now": {"round": s.get("round", 1), "cash": round(float(s.get("cash", start)) / start, 2),
                            "peak": round(float(s.get("best", start)) / start, 2), "trades": len(s.get("closed", [])) - s.get("round_from", 0)}})
    return out


def live_markdown(rows: list[dict]) -> str:
    out = ["", "## 实盘和演算账本的轮（journal-s500）", "",
           "每条规则自己的轮：到 20 倍 / 归零各几次、到过 2 倍的轮占几成、每轮几笔、每个 hero 到现在花了多少；最后一列是进行中的这一轮。"
           "对上面回测的同一列看：实盘的 hero 率低于回测一半以上，先查成交成本，再谈规则。", "",
           "hero 太稀，先看单笔的尾巴：「笔 ≥2 倍 / ≥5 倍」是每笔（一半止盈 + 一半回撤合起来算）到过 2 倍、5 倍的比例，几十笔就能和回测对上；"
           "押满时 hero 率约等于连赢两三把的概率，由它推算。", "",
           "目标不同的账本分开列（审计 10-26 #3）：hero 是起始的 20 倍（$500 → $10,000、$1,000 → $20,000），$2,000 账本按它的研究是 $10,000（5 倍），不能和 20 倍的放在一起比。"
           "「归零」括号里的停 / 换规则是停开新仓或规则改了结束的轮，不算归零。"]
    for x in sorted({r.get("x", 20.0) for r in rows}, reverse=True):
        out += ["", f"### 目标 {x:g} 倍", "",
                f"| 账本 | 本子 | 笔数 | 笔 ≥2 倍 | 笔 ≥5 倍 | 轮数 | 到 {x:g} 倍 | 归零 | 到过 2 倍的轮 | 每轮笔数 | 每个 hero 的成本 | 进行中：第几轮 · 现在 / 峰值（倍）· 笔数 |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in (r for r in rows if r.get("x", 20.0) == x):
            n = r["now"]
            other = "，".join(f"{k} {r[key]}" for k, key in (("停", "stopped"), ("换规则", "rule")) if r.get(key))
            zeros = f"{r['zeros']}（{other}）" if other else str(r["zeros"])
            out.append(f"| {r['id']} | {r['book']} | {r['trades']} | {fmt(r['x2'])} | {fmt(r['x5'])} | {r['rounds']} | {r['heroes']} | {zeros} | {fmt(r['x2_rounds'])} | "
                       f"{fmt(r['trades_per_round'], 'n')} | {fmt(r['cost_per_hero'], 'usd')} | 第 {n['round']} 轮 · {n['cash']:.2f} / {n['peak']:.2f} · {n['trades']} |")
    return "\n".join(out) + "\n"


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
           "| 规则 | 笔数 | 押法 | 轮数 | 到 20 倍 | hero 率（95% 区间） | 每轮笔数 | 到过 2 倍的轮 | 峰值中位 / 九成 / 最高 | 每个 hero 的成本 |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for name, by_f in t.items():
        for key, g in by_f.items():
            ci = interval(g["heroes"], g["rounds"])
            out.append(f"| {name} | {g['n']} | 押{'满' if g['f'] >= 1 else '一半'} | {g['rounds']} | {g['heroes']} | {fmt(g['p_hero'])}"
                       f"{'（' + fmt(ci[0]) + '–' + fmt(ci[1]) + '）' if ci else ''} | "
                       f"{fmt(g['trades_per_round'], 'n')} | {fmt(g['x2_rounds'])} | {fmt(g['peak_median'], 'x')} / {fmt(g['peak_p90'], 'x')} / {fmt(g['peak_max'], 'x')} | "
                       f"{fmt(g['cost_per_hero'], 'usd')} |")
    out += ["", "## 读法", "",
            "- 轮数都很少，先看区间再看点估计：SPY 0.3% 现行出场押一半 0/58（区间 0–6%）、押满 9/314（1–5%），两者分不出高下。押满的理由是理论，不是这组数：",
            "  负期望的游戏里，大胆下注（押满、或押到刚好够到目标）最大化到达目标的概率（Dubins–Savage），押一半只是把钱更久地暴露在负漂移里。",
            "- 抄底给一周押一半 3/38（0–16%）、每个 hero $6,000–7,000，是唯一低于 $10,000 的；但成本从各 5% 改成各 10%，hero 率掉到 3–6%、每个 hero $8,000–18,000，",
            "  2 倍止盈的两个变体在 10% 成本下一个 hero 都没有。所以它当主力是押它的成本能守在 5%（实盘：价差 ≤10% 才买），不是已经证明。三种出场分不出高下，多开账本是为了多攒轮。",
            "- 抄底给一周的回测池只有 28 只今天的赢家，一天一笔、拿 5 天的重叠也没算（按顺序全额重投是上限）。实盘的轮记分会告诉我们偏了多少。",
            "- 一分钱彩票单张到 20 倍 1.7%，和押满的末日单差不多，但没有「连赢两把」的路径，不要。财报后漂移 34 笔、1–2 轮，只是线索。"]
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
    live = []
    for name, label in (("sleeves.json", "实盘"), ("sleeves_plan.json", "演算")):
        try:
            live += live_rounds(json.loads((ROOT / "journal-s500" / name).read_text()), label)
        except FileNotFoundError:
            pass
    live = [r for r in live if r["rounds"] or r["trades"]]
    if live:
        md += live_markdown(live)
    out = ROOT / "research" / f"{today}-hero-game"
    out.with_suffix(".md").write_text(md)
    out.with_suffix(".json").write_text(json.dumps({"generated": today, "zero": ZERO, "target": TARGET, "table": t, "live": live},
                                                   ensure_ascii=False, indent=1))
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
