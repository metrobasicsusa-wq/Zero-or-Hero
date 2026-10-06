"""Research: cast a net of cheap calls only when the market is wild.

The 20x study found that 20x calls come from volatile stocks in wild stretches (the April 2025
rebound, September 2025, spring 2026) and almost never in calm ones, while a stock's own momentum
told nothing. So: stay out most of the time; in a signal week, buy ~4-week calls 20% / 30% above
the price on the 15 most volatile stocks of the pool (by their own last 63 days, known at entry),
and let one or two of them pay for the rest.

Signals, from SPY's own past closes only (each week's first trading day):
  "rebound": SPY fell 8%+ below its 63-day high within the last 10 days and is now 3%+ off the low;
  "wild":    SPY's 20-day volatility is in the top 20% of its own past year;
  "control": every 4th week regardless (the same net cast blindly).
Exits on daily closes (a lone high print cannot fake a fill, and the stock must have actually
moved at least halfway to the strike that day): a resting 10x or 20x limit, else held to expiry.
Costs: pay 10% over the close (at least a cent), sell 10% under. The pool is today's list, which
leans to today's winners. Then the user's game: $500, half of it spread over each signal week's
net, a round ends below $50 (zero) or at $10,000 (hero) and a fresh $500 round begins.
Record only. Run: python -m hero.research_net
"""

from __future__ import annotations

import json
import math
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from hero.research_lottery import entry_cost, exit_value
from hero.research_tails import pick_expiry, week_entries

START = "2024-02-01"
POOL = 100
NET = 15
OTM = (0.20, 0.30)
TAKES = (10, 20)
TARGET, END = 10_000.0, 50.0


def vol(closes: list[float]) -> float:
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    return statistics.pstdev(rets) * math.sqrt(252) if len(rets) > 2 else 0.0


def signals(spy: dict[str, float], entries: list[str]) -> dict[str, list[str]]:
    days = sorted(spy)
    idx = {d: i for i, d in enumerate(days)}
    vols = {d: vol([spy[x] for x in days[max(0, i - 20): i + 1]]) for i, d in enumerate(days) if i >= 20}
    out = {"rebound": [], "wild": [], "control": entries[::4]}
    for d in entries:
        i = idx[d]
        if i < 260:
            continue
        last10 = [spy[x] for x in days[i - 9: i + 1]]
        dd = min(spy[days[j]] / max(spy[x] for x in days[j - 62: j + 1]) - 1 for j in range(i - 9, i + 1))
        if dd <= -0.08 and spy[d] >= min(last10) * 1.03:
            out["rebound"].append(d)
        past = sorted(vols[x] for x in days[i - 252: i] if x in vols)
        if past and vols[d] >= past[int(0.8 * len(past))]:
            out["wild"].append(d)
    return out


def ticket(bars: dict[str, dict], stock: dict[str, float], entry: str, expiry: str, strike: float, spot: float,
           otm: float) -> dict | None:
    """Per-$1 returns for the 10x / 20x limits (else expiry) and for holding to expiry."""
    if entry not in bars:
        return None
    cost = entry_cost(float(bars[entry]["c"]))
    settle = stock.get(expiry) or max(((d, v) for d, v in stock.items() if d <= expiry), default=(None, None))[1]
    if settle is None:
        return None
    held = max(settle - strike, 0.0) / cost - 1
    out = {"ret_expiry": round(held, 3), "best": 0.0}
    hit = {t: None for t in TAKES}
    for d in sorted(x for x in bars if entry < x <= expiry):
        v = exit_value(float(bars[d]["c"])) / cost
        moved = stock.get(d, 0.0) >= spot * (1 + otm / 2)
        if moved:
            out["best"] = max(out["best"], round(v, 1))
            for t in TAKES:
                if hit[t] is None and v >= t:
                    hit[t] = d
    for t in TAKES:
        out[f"ret_tp{t}"] = round(t - 1, 3) if hit[t] else out["ret_expiry"]
    return out


def run(client, root: Path) -> dict:
    from hero import universe
    pool = universe.load(root / "data" / "universe.json")["stocks"][:POOL]
    first = "2023-01-01"
    end = (date.today() - timedelta(days=1)).isoformat()
    raw: dict[str, dict[str, float]] = {}
    for i in range(0, len(pool), 50):
        for s, bs in client.daily_bars(pool[i:i + 50], first, adjustment="raw").items():
            raw[s] = {b["t"][:10]: float(b["c"]) for b in bs}
    spy = {b["t"][:10]: float(b["c"]) for b in client.daily_bars(["SPY"], first, adjustment="raw").get("SPY", [])}
    entries = [d for d in week_entries(sorted(spy)) if START <= d <= end]
    sig = signals(spy, entries)
    weeks = sorted({d for v in sig.values() for d in v})
    tickets: dict[str, list[dict]] = {}
    for d in weeks:
        e = date.fromisoformat(d)
        if (date.fromisoformat(end) - e).days < 36:
            continue  # its options have not expired yet
        ranked = []
        for s in pool:
            c = raw.get(s, {})
            ds = [x for x in sorted(c) if x <= d]
            if len(ds) >= 64 and ds[-1] == d:
                ranked.append((vol([c[x] for x in ds[-64:]]), s))
        net = [s for _, s in sorted(ranked, reverse=True)[:NET]]
        rows = []
        for s in net:
            spot = raw[s][d]
            cs = []
            for status in ("inactive", "active"):
                try:
                    cs += client.option_contracts(s, status=status, type="call",
                                                  expiration_date_gte=(e + timedelta(days=21)).isoformat(),
                                                  expiration_date_lte=(e + timedelta(days=35)).isoformat(),
                                                  strike_price_gte=f"{spot * 1.19:.2f}", strike_price_lte=f"{spot * 1.4:.2f}")
                except Exception:
                    pass
            if not cs:
                continue
            exp = pick_expiry(sorted({c["expiration_date"] for c in cs}), d, "monthly")
            if not exp:
                continue
            by_k = {float(c["strike_price"]): c["symbol"] for c in cs if c["expiration_date"] == exp}
            picks = {}
            for m in OTM:
                ks = [k for k in sorted(by_k) if k >= spot * (1 + m)]
                if ks:
                    picks[m] = ks[0]
            if not picks:
                continue
            try:
                bars = client.option_bars(sorted({by_k[k] for k in picks.values()}), d, exp)
            except Exception:
                continue
            time.sleep(0.1)
            for m, k in picks.items():
                ohlc = {b["t"][:10]: b for b in bars.get(by_k[k], [])}
                t = ticket(ohlc, raw[s], d, exp, k, spot, m)
                if t:
                    rows.append({"symbol": s, "otm": m, **t})
        tickets[d] = rows
    return {"generated": date.today().isoformat(), "start": START, "net": NET, "signals": sig, "tickets": tickets}


def game(weeks: list[list[float]]) -> dict:
    """$500; half of the account spread evenly over each signal week's tickets."""
    bank, heroes, zeros, best = 500.0, 0, 0, 500.0
    for rets in weeks:
        if not rets:
            continue
        bank += 0.5 * bank * statistics.mean(rets)
        best = max(best, min(bank, TARGET))
        if bank < END:
            zeros, bank = zeros + 1, 500.0
        elif bank >= TARGET:
            heroes, bank = heroes + 1, 500.0
    return {"final": round(bank), "best": round(best), "heroes": heroes, "zeros": zeros}


def summarize(rep: dict) -> dict:
    out = {}
    for name, days in rep["signals"].items():
        ws = [d for d in days if d in rep["tickets"] and rep["tickets"][d]]
        for m in OTM:
            for key in ("ret_tp10", "ret_tp20", "ret_expiry"):
                per_week = [[t[key] for t in rep["tickets"][d] if t["otm"] == m] for d in ws]
                flat = [x for w in per_week for x in w]
                if not flat:
                    continue
                wk = [statistics.mean(w) for w in per_week if w]
                out[f"{name}|{m}|{key}"] = {
                    "weeks": len(wk), "tickets": len(flat), "mean": round(statistics.mean(flat), 3),
                    "week_win": round(sum(x > 0 for x in wk) / len(wk), 3),
                    "best_week": round(max(wk), 2), "worst_week": round(min(wk), 2),
                    "x20_share": round(sum(t["best"] >= 20 for d in ws for t in rep["tickets"][d] if t["otm"] == m) / len(flat), 4),
                    "weeks_with_20x": sum(any(t["best"] >= 20 for t in rep["tickets"][d] if t["otm"] == m) for d in ws),
                    **game(per_week)}
    return out


def markdown(rep: dict, s: dict) -> str:
    names = {"rebound": "暴跌后反弹（SPY 10 天内跌破 63 日高点 8%，已从低点反弹 3%）",
             "wild": "高波动（SPY 20 日波动率在过去一年前 20%）", "control": "对照：每 4 周一次，不看行情"}
    keys = {"ret_tp10": "10 倍止盈（否则拿到期）", "ret_tp20": "20 倍止盈（否则拿到期）", "ret_expiry": "拿到期"}
    out = [f"# 高波动时期撒网买彩票 {rep['generated']}", "",
           f"自 {rep['start']}。信号周：在动态池（今天的名单，偏向今天的赢家）里挑过去 63 天波动最大的 {rep['net']} 只，"
           "每只买一张约 4 周后到期、价外 20% 或 30% 的看涨期权，各押同样的钱。用每日收盘价判断止盈（单笔异常的最高价不算），"
           "而且当天股价至少要涨到行权价的一半路程。买入多付 10%、卖出少拿 10%。"
           "玩法：$500，每个信号周把一半资金平分到这一网，低于 $50 归零、到 $10,000 成功，都重来。只研究，不改交易。", "",
           "## 信号出现的周数", ""]
    for k, v in rep["signals"].items():
        out.append(f"- {names[k]}：{len(v)} 周（{', '.join(v[:12])}{' …' if len(v) > 12 else ''}）")
    out += ["", "## 结果", "",
            "| 信号 | 价外 | 卖法 | 周数 | 张数 | 平均每 $1 | 赚钱的周 | 最好一周 | 最差一周 | 到过 20 倍的张数比例 | 有 20 倍的周数 | $500 半仓：成功 / 归零 / 最高到过 |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for key, r in s.items():
        name, m, k = key.split("|")
        out.append(f"| {names[name].split('（')[0]} | {float(m):.0%} | {keys[k]} | {r['weeks']} | {r['tickets']} | {r['mean']:+.0%} | "
                   f"{r['week_win']:.0%} | {r['best_week']:+.0%} | {r['worst_week']:+.0%} | {r['x20_share']:.1%} | {r['weeks_with_20x']} | "
                   f"{r['heroes']} / {r['zeros']} / ${r['best']:,} |")
    return "\n".join(out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca(), root)
    s = summarize(rep)
    rep["summary"] = s
    (root / "research" / f"{rep['generated']}-net.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    (root / "research" / f"{rep['generated']}-net.md").write_text(markdown(rep, s))
    print(markdown(rep, s))


if __name__ == "__main__":
    sys.exit(main())
