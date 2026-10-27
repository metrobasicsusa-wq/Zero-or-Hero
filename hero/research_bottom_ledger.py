"""A $1,000 ledger replayed through the bottom studies' trades (owner, 10-27: 「回测下1000 开局 最终会是多少钱」).

The three option studies of the day (bottom-2m on the ETFs, bottom-semi on fourteen semiconductor names, bottom-spread
with $5-wide call spreads) saved every trade with its signal day, expiry, the price paid and the return of each
exit. This replays them as one ledger: $1,000 at the start of 2024-02, every signal in date order, whole contracts
only (a signal the ledger cannot afford is skipped and counted), one bet per underlying at a time (a signal on a
name already held is skipped), the money locked until the exit (63 sessions taken as 91 calendar days, 42 as 61, or
the expiry when sooner) and then returned with the trade's return; "押满" puts all free cash in, "押一半" half. The
price layer does the same from 2017 with the 3x fund held 63 sessions after SMH's first close 8% under its 20-session
high. Reports the final equity, the peak, the deepest drawdown, the year-end path and the biggest single trade.
Research only; no data calls."""
from __future__ import annotations

import json
import os
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

from hero.research_ideas import save

ROOT = Path(__file__).resolve().parent.parent
START = float(os.environ.get("RESEARCH_LEDGER_START", "1000"))   # owner, 10-27: 「那500 呢 2.5 价差」
STARTS = (500.0, 1000.0, 2000.0, 5000.0)
DAYS = {"d42": 61, "d63": 91, "take80": 91}
ETFS = ("SMH", "QQQ", "IWM", "SPY")
STOCKS = ("NVDA", "AVGO", "AMD", "MU", "TSM", "ASML", "AMAT", "LRCX", "KLAC", "QCOM", "INTC", "MRVL", "TXN", "ARM")


def latest(name: str) -> dict:
    files = sorted((ROOT / "research").glob(f"*-{name}.json"))
    if not files:
        raise FileNotFoundError(name)
    return json.loads(files[-1].read_text())


def trades(rows: list[dict], legs: tuple[str, ...], tag: str, exit_: str, unds: tuple[str, ...] | None = None) -> list[dict]:
    """One replayable trade per row: the day, what one contract (or spread) cost with 5% paid, the exit's return and
    the day the money comes back."""
    out = []
    for r in rows:
        if r.get("leg") not in legs or tag not in r["tags"] or (unds and r["und"] not in unds):
            continue
        ret = (r.get("res") or {}).get(exit_)
        if ret is None:
            continue
        d0 = date.fromisoformat(r["day"])
        back = min(d0 + timedelta(days=DAYS[exit_]), date.fromisoformat(r["expiry"]))
        out.append({"day": r["day"], "und": r["und"], "cost": round(r["entry"] * 1.05 * 100, 2), "ret": ret, "back": back.isoformat()})
    order = {u: i for i, u in enumerate(ETFS + STOCKS)}
    return sorted(out, key=lambda t: (t["day"], order.get(t["und"], 99)))


def replay(trs: list[dict], start: float = START, f: float = 1.0) -> dict:
    """The ledger: every signal in order, whole units only, one bet per underlying at a time, cash locked until the
    trade's exit day, then back with its return."""
    cash, open_, taken, skipped, path, big = start, [], [], 0, [], []
    for t in trs:
        open_.sort(key=lambda o: o["back"])
        while open_ and open_[0]["back"] <= t["day"]:
            o = open_.pop(0)
            cash += o["proceeds"]
            path.append((o["back"], cash + sum(x["paid"] for x in open_)))
        if any(o["und"] == t["und"] for o in open_):
            continue
        n = int(f * cash // t["cost"])
        if n < 1:
            skipped += 1
            continue
        paid = n * t["cost"]
        cash -= paid
        proceeds = round(paid * (1 + t["ret"]), 2)
        open_.append({"und": t["und"], "back": t["back"], "paid": paid, "proceeds": proceeds})
        taken.append({**t, "n": n, "paid": round(paid, 2), "pnl": round(proceeds - paid, 2)})
        big.append((proceeds - paid, t["und"], t["day"]))
    for o in sorted(open_, key=lambda o: o["back"]):
        cash += o["proceeds"]
        path.append((o["back"], cash))
    peak, dd, hi = start, 0.0, start
    for _, eq in path:
        hi = max(hi, eq)
        peak = max(peak, eq)
        dd = min(dd, eq / hi - 1)
    years = {}
    for d, eq in path:
        years[d[:4]] = eq
    best = max(big) if big else None
    worst = min(big) if big else None
    return {"final": round(cash, 2), "peak": round(peak, 2), "max_dd": round(dd, 4), "taken": len(taken), "skipped": skipped,
            "years": {y: round(v, 2) for y, v in sorted(years.items())}, "best": best and (round(best[0], 2), best[1], best[2]),
            "worst": worst and (round(worst[0], 2), worst[1], worst[2]), "trades": taken}


def price_replay(events: list[dict], und: str, tag: str, key: str, start: float = START, hold_days: int = 91) -> dict:
    """The price layer: all in at each signal close, held `hold_days` calendar days, no overlap (a signal while held is
    skipped), fractional shares."""
    eq, busy_until, path, n = start, "", [], 0
    for e in sorted((e for e in events if e["und"] == und and tag in e["tags"] and key in e), key=lambda e: e["day"]):
        if e["day"] <= busy_until:
            continue
        eq *= 1 + e[key]
        n += 1
        busy_until = (date.fromisoformat(e["day"]) + timedelta(days=hold_days)).isoformat()
        path.append((busy_until, eq))
    hi, dd = start, 0.0
    for _, v in path:
        hi = max(hi, v)
        dd = min(dd, v / hi - 1)
    years = {}
    for d, v in path:
        years[d[:4]] = v
    return {"final": round(eq, 2), "max_dd": round(dd, 4), "taken": n, "years": {y: round(v, 2) for y, v in sorted(years.items())}}


def slots(trs: list[dict]) -> int:
    """How many trades the strategy could have held one after another (greedy, no overlap, money no object)."""
    busy, n = "", 0
    for t in trs:
        if t["day"] > busy:
            n += 1
            busy = t["back"]
    return n


def resample(trs: list[dict], start: float = START, f: float = 1.0, rounds: int | None = None, sims: int = 2000, seed: int = 7) -> dict:
    """The same ledger with the order removed: as many slots as the strategy really had one after another, each slot
    a different trade drawn from the strategy's own pool (without replacement), whole units only, a slot the ledger
    cannot afford is a slot waited out. The spread of finals says how much of the dated replay is the order."""
    import random
    rng = random.Random(seed)
    rounds = rounds or slots(trs)
    finals = []
    for _ in range(sims):
        cash = start
        for t in rng.sample(trs, min(rounds, len(trs))):
            n = int(f * cash // t["cost"])
            if n < 1:
                continue
            cash += n * t["cost"] * t["ret"]
        finals.append(cash)
    finals.sort()
    q = lambda p: finals[min(len(finals) - 1, int(p * len(finals)))]
    return {"median": round(q(0.5), 2), "p10": round(q(0.1), 2), "p90": round(q(0.9), 2), "mean": round(statistics.mean(finals), 2),
            "p_dead": round(sum(x < 100 for x in finals) / len(finals), 3), "p_5k": round(sum(x >= 5000 for x in finals) / len(finals), 3),
            "p_10k": round(sum(x >= 10000 for x in finals) / len(finals), 3), "rounds": rounds, "sims": sims, "pool": len(trs)}


def run() -> dict:
    b2, bs, sp = latest("bottom-2m"), latest("bottom-semi"), latest("bottom-spread")
    try:
        sp25 = latest("bottom-spread25")
    except FileNotFoundError:
        sp25 = {"rows": []}
    specs = [
        ("SMH 跌 8% · 裸买平值拿 63 天（ETF 版）", b2["rows"], ("atm",), "dd8", "d63", ("SMH",)),
        ("SMH 跌 8% · 裸买价外 5% 拿 63 天", b2["rows"], ("otm5",), "dd8", "d63", ("SMH",)),
        ("SMH 跌 8% · 裸买价外 10% 拿 63 天", b2["rows"], ("otm10",), "dd8", "d63", ("SMH",)),
        ("SMH 跌 8% · 裸买平值拿 42 天", b2["rows"], ("atm",), "dd8", "d42", ("SMH",)),
        ("四只 ETF 跌 8% · 裸买平值拿 63 天", b2["rows"], ("atm",), "dd8", "d63", ETFS),
        ("四只 ETF 连跌第 3 天 · 裸买平值拿 63 天", b2["rows"], ("atm",), "day3", "d63", ETFS),
        ("对照：ETF 每第 10 天随便买 · 平值拿 63 天", b2["rows"], ("atm",), "every10", "d63", ETFS),
        ("14 只半导体股 跌 8% · 裸买平值拿 63 天", bs["rows"], ("atm",), "dd8", "d63", STOCKS),
        ("14 只半导体股 跌 15% · 裸买平值拿 63 天", bs["rows"], ("atm",), "dd15", "d63", STOCKS),
        ("14 只半导体股 跌 8% · 裸买价外 10% 拿 63 天", bs["rows"], ("otm10",), "dd8", "d63", STOCKS),
        ("对照：半导体股每第 20 天随便买 · 平值拿 63 天", bs["rows"], ("atm",), "every20", "d63", STOCKS),
        ("18 只 跌 8% · $5 价差平值 · 到 $4 就卖", sp["rows"], ("atm5",), "dd8", "take80", None),
        ("18 只 跌 8% · $5 价差平值 · 拿到期", sp["rows"], ("atm5",), "dd8", "d63", None),
        ("四只 ETF 跌 8% · $5 价差平值 · 到 $4 就卖", sp["rows"], ("atm5",), "dd8", "take80", ETFS),
        ("14 只半导体股 跌 8% · $5 价差平值 · 到 $4 就卖", sp["rows"], ("atm5",), "dd8", "take80", STOCKS),
        ("18 只 跌 15% · $5 价差价外 5% · 到 $4 就卖", sp["rows"], ("otm5",), "dd15", "take80", None),
        ("对照：18 只每第 20 天随便买 · $5 价差平值 · 到 $4 就卖", sp["rows"], ("atm5",), "every20", "take80", None),
        ("18 只 跌 8% · $2.5 价差平值 · 到 $2 就卖", sp25["rows"], ("atm5",), "dd8", "take80", None),
        ("18 只 跌 8% · $2.5 价差平值 · 拿到期", sp25["rows"], ("atm5",), "dd8", "d63", None),
        ("四只 ETF 跌 8% · $2.5 价差平值 · 到 $2 就卖", sp25["rows"], ("atm5",), "dd8", "take80", ETFS),
        ("14 只半导体股 跌 8% · $2.5 价差平值 · 到 $2 就卖", sp25["rows"], ("atm5",), "dd8", "take80", STOCKS),
        ("18 只 跌 8% · $2.5 价差价外 5% · 到 $2 就卖", sp25["rows"], ("otm5",), "dd8", "take80", None),
        ("18 只 跌 15% · $2.5 价差价外 5% · 到 $2 就卖", sp25["rows"], ("otm5",), "dd15", "take80", None),
        ("对照：18 只每第 20 天随便买 · $2.5 价差平值 · 到 $2 就卖", sp25["rows"], ("atm5",), "every20", "take80", None),
    ]
    out = []
    for name, rows, legs, tag, exit_, unds in specs:
        trs = trades(rows, legs, tag, exit_, unds)
        if not trs:
            continue
        full, half = replay(trs, START, 1.0), replay(trs, START, 0.5)
        out.append({"name": name, "n_signals": len(trs), "one_costs": round(statistics.median(t["cost"] for t in trs), 0),
                    "full": {k: v for k, v in full.items() if k != "trades"}, "half": {k: v for k, v in half.items() if k != "trades"},
                    "starts": {str(int(s0)): {"full": {k: v for k, v in replay(trs, s0, 1.0).items() if k != "trades"},
                                              "half": {k: v for k, v in replay(trs, s0, 0.5).items() if k != "trades"},
                                              "random_full": resample(trs, s0, 1.0), "random_half": resample(trs, s0, 0.5)} for s0 in STARTS},
                    "random_full": resample(trs, START, 1.0), "random_half": resample(trs, START, 0.5),
                    "full_trades": full["trades"]})
    px = {"SMH 跌 8% → SOXL 正股拿 63 天（2017 起）": price_replay(b2["events"], "SMH", "dd8", "l63", 1000.0),
          "SMH 跌 8% → SMH 正股拿 63 天（2017 起）": price_replay(b2["events"], "SMH", "dd8", "f63", 1000.0),
          "SMH 连跌第 3 天 → SOXL 正股拿 63 天（2017 起）": price_replay(b2["events"], "SMH", "day3", "l63", 1000.0),
          "对照：SMH 每第 5 天 → SOXL 拿 63 天（2017 起）": price_replay(b2["events"], "SMH", "every5", "l63", 1000.0),
          "QQQ 跌 8% → TQQQ 正股拿 63 天（2017 起）": price_replay(b2["events"], "QQQ", "dd8", "l63", 1000.0),
          "SPY 跌 8% → SPXL 正股拿 63 天（2017 起）": price_replay(b2["events"], "SPY", "dd8", "l63", 1000.0)}
    return {"start": START, "from": b2["opt_start"], "end": b2["end"], "options": out, "prices": px,
            "sources": {k: v for k, v in (("bottom-2m", b2.get("end")), ("bottom-semi", bs.get("end")), ("bottom-spread", sp.get("end")))}}


def markdown(rep: dict) -> str:
    m = lambda x: f"${x:,.0f}"
    lines = [f"# $500 / $1,000 开局，抄底拿两个月的账本会变成多少钱 {date.today().isoformat()}", "",
             f"把今天的回测（ETF 裸买、半导体股裸买、$5 和 $2.5 价差）里的每一笔按日期重放成一个账本：{rep['from']} 起，只买整张，买不起就跳过（计数），"
             "同一只拿着时不再买，钱锁到出场（63 天按 91 个日历日、42 天按 61 天、先到期按到期）再按那笔的收益拿回来；「押满」每次把现金全押上，「押一半」押一半。"
             "期权回测只有 2024-02 起的数据，所以这是两年零八个月的结果。只研究，不改交易。", "",
             "## 按日期重放（2024-02 至 " + rep["end"] + "）", "",
             "| 策略 | 信号数 | 一张 / 一组 | $500 押满 | 做了 / 买不起 | 最大回撤 | $500 押一半 | 做了 | $1,000 押满 | 做了 / 买不起 | 最大回撤 | $1,000 押一半 | $2,000 押满 | $5,000 押满 | 最大一笔赚 / 亏（$1,000 押满） |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for o in rep["options"]:
        a, b = o["starts"]["500"], o["starts"]["1000"]
        f1 = b["full"]
        best = f"{m(f1['best'][0])} {f1['best'][1]} / {m(f1['worst'][0])} {f1['worst'][1]}" if f1["best"] else "—"
        lines.append(f"| {o['name']} | {o['n_signals']} | {m(o['one_costs'])} | **{m(a['full']['final'])}** | {a['full']['taken']} / {a['full']['skipped']} | {a['full']['max_dd']:+.0%} | "
                     f"{m(a['half']['final'])} | {a['half']['taken']} | **{m(f1['final'])}** | {f1['taken']} / {f1['skipped']} | {f1['max_dd']:+.0%} | {m(b['half']['final'])} | "
                     f"{m(o['starts']['2000']['full']['final'])} | {m(o['starts']['5000']['full']['final'])} | {best} |")
    for s0 in ("500", "1000"):
        lines += ["", f"## ${int(s0):,} 开局，把顺序打乱：这条策略真能一笔接一笔做几次就抽几格，每格从它自己的成交里抽一笔不重复的，抽 2,000 次", "",
                  "按日期重放只有一条路，最终数字一半靠前两三笔的运气；这里给的是分布：中位数、最差一成、最好一成、归零（低于 $100）和到 $5,000 / $10,000 的概率。"
                  "它假设这条策略的成交池就是真相（池子只有两年八个月），所以是「如果这段历史重来一遍」，不是预测。", "",
                  "| 策略 | 池子 / 格子 | 押满：中位数 | 最差一成 | 最好一成 | 归零 | 到 $5,000 | 到 $10,000 | 押一半：中位数 | 最差一成 | 最好一成 | 归零 | 到 $5,000 | 到 $10,000 |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for o in rep["options"]:
            a, b = o["starts"][s0]["random_full"], o["starts"][s0]["random_half"]
            lines.append(f"| {o['name']} | {a['pool']} / {a['rounds']} | **{m(a['median'])}** | {m(a['p10'])} | {m(a['p90'])} | {a['p_dead']:.0%} | {a['p_5k']:.0%} | {a['p_10k']:.0%} | "
                         f"**{m(b['median'])}** | {m(b['p10'])} | {m(b['p90'])} | {b['p_dead']:.0%} | {b['p_5k']:.0%} | {b['p_10k']:.0%} |")
    lines += ["", "## 价格层（2017 起，正股，$1,000 全押、拿 63 个交易日、拿着时不再买，不算成本；$500 开局就是一半）", "",
              "| 策略 | 做了几次 | 最终 | 最大回撤 | 年末 2018 / 2020 / 2022 / 2024 / 2026 |", "|---|---|---|---|---|"]
    for name, p in rep["prices"].items():
        yrs = " / ".join(m(p["years"][y]) if y in p["years"] else "—" for y in ("2018", "2020", "2022", "2024", "2026"))
        lines.append(f"| {name} | {p['taken']} | **{m(p['final'])}** | {p['max_dd']:+.0%} | {yrs} |")
    lines += ["", "## 读法", "",
              "- 「买不起」那一栏是关键：$500 / $1,000 买不起 SMH / AVGO 的 3 个月平值看涨，账本只能做便宜的腿或价差；跳过的信号不算亏也不算赚。",
              "- 押满的账本一次归零就结束（之后的信号都买不起）；押一半能活得久，但翻得慢。",
              "- 两年零八个月里 2024-06 到 2025-06 是一年白等，2025-07 起是大涨：最终数字一半是信号、一半是这段行情。",
              "- 价格层的 3 倍基金正股不会归零，但 2022 那种年份回撤大；它是「稳」线的参考。", "- 只研究，不改交易。"]
    return "\n".join(lines) + "\n"


def main() -> None:
    rep = run()
    md = markdown(rep)
    for o in rep["options"]:
        o["full_trades"] = o["full_trades"][:400]
    save("bottom-ledger", rep, md)


if __name__ == "__main__":
    sys.exit(main())
