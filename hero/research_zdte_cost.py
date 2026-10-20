"""SPY 10:00 same-day options: does the verdict change with the costs we actually pay? (owner, 10-20)

research/2026-10-16-zdte-minmove.md prices each trade at the minute close plus 10% to get in and minus 10% to get out
(research_0dte.SLIP). research/2026-10-19-fills.md found our same-day buys filled a median 3.9% *below* the last
minute close, and same-day sells at a median 0% (mean 7% worse, pulled by $0.01 exits). Here the two live SPY sleeves
(10:00, the first strike 0.6% / 0.3% beyond the price, half at 3x and the rest sold 40% off its best after a double,
else 15:30) are replayed every weekday since 2024-02 under several (buy, sell) costs, grouped by how far SPY had moved
from its open at 10:00. The last section puts the sleeves' own trades next to the replay of the same days.
Record only. Run: python -m hero.research_zdte_cost
"""

from __future__ import annotations

import json
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

from hero import robust
from hero.research_0dte import at_or_after, at_or_before, et_minutes
from hero.research_0dte_names import minutes_by_day, pick
from hero.research_event_turn import Pace
from hero.research_ideas import save

ROOT = Path(__file__).resolve().parent.parent
START = "2024-02-01"
ENTRY, OUT = "10:00", "15:30"
OFFS = {0.006: ("SPY 末日单（spy0dte）", "spy0dte-1000"), 0.003: ("SPY 末日单 0.3%（spy0dte-near）", "spy0dte-near-1000")}
# (buy, sell): paid over the minute close to get in, given up under it to get out.
COSTS = [(0.10, 0.10, "现在的假设：买多付 10%、卖少拿 10%"), (0.05, 0.05, "各 5%"),
         (0.0, 0.10, "买按收盘价、卖少拿 10%"), (0.0, 0.0, "都按分钟收盘价")]
BANDS = [(0.0, 0.001), (0.001, 0.002), (0.002, 0.003), (0.003, 0.005), (0.005, 1.0)]
FLOORS = (0.002, 0.005)


def key(buy: float, sell: float) -> str:
    return f"{buy:g}/{sell:g}"


def side(px: float, pct: float, sign: int) -> float:
    """Price after cost: at least a cent when there is a cost at all."""
    if pct <= 0:
        return px
    return max(px + sign * max(px * pct, 0.01), 0.0)


def play_cost(opt: dict[str, dict], entry: str, buy: float, sell: float) -> dict[str, float] | None:
    """Per $1 for the live exit (half at 3x, the rest trailed) and two others, on minute closes after the entry."""
    got = at_or_after(opt, entry)
    if not got:
        return None
    t0, bar = got
    c0 = float(bar["c"])
    if c0 <= 0:
        return None
    cost = side(c0, buy, +1)
    path = [float(v["c"]) for k, v in sorted(opt.items()) if t0 < k <= OUT]
    out = lambda px: side(px, sell, -1) / cost - 1
    hold = out(path[-1]) if path else -1.0
    take3 = 2.0 if any(c >= 3 * cost for c in path) else hold
    trail, best, armed = hold, 0.0, False
    for c in path:
        best = max(best, c)
        armed = armed or c >= 2 * cost
        if armed and c <= 0.6 * best:
            trail = out(c)
            break
    live = (take3 + trail) / 2 if take3 == 2.0 else trail
    return {"take3_trail": round(live, 4), "take3": round(take3, 4), "hold": round(hold, 4)}


def run(client) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    pace, rows = Pace(client), []
    days = minutes_by_day(client, "SPY", START, end)
    for day in sorted(days):
        m = days[day]
        b = at_or_before(m, ENTRY)
        if "09:30" not in m or not b or not at_or_before(m, OUT):
            continue
        o, spot = float(m["09:30"]["o"]), float(b["c"])
        kind = "C" if spot >= o else "P"
        cs = []
        for status in ("inactive", "active"):
            try:
                cs = client.option_contracts("SPY", status=status, expiration_date_gte=day,
                                             expiration_date_lte=(date.fromisoformat(day) + timedelta(days=7)).isoformat(),
                                             strike_price_gte=f"{spot * 0.97:.2f}", strike_price_lte=f"{spot * 1.03:.2f}")
            except Exception:
                cs = []
            if cs:
                break
        if not cs:
            continue
        exp = min(c["expiration_date"] for c in cs)
        near = [c for c in cs if c["expiration_date"] == exp]
        syms = {off: pick(near, kind, spot, off) for off in OFFS}
        want = sorted({s for s in syms.values() if s})
        if not want:
            continue
        pace.wait()
        try:
            bars = client.option_bars(want, f"{day}T13:00:00Z", f"{day}T21:00:00Z", timeframe="1Min")
        except Exception:
            continue
        mins = {s: et_minutes(v) for s, v in bars.items()}
        res = {}
        for off, s in syms.items():
            if not s or s not in mins:
                continue
            for buy, sell, _ in COSTS:
                got = play_cost(mins[s], ENTRY, buy, sell)
                if got:
                    res.setdefault(str(off), {})[key(buy, sell)] = got
        if res:
            rows.append({"day": day, "move": round(spot / o - 1, 5), "kind": kind, "syms": syms, "res": res})
    return {"generated": date.today().isoformat(), "start": START, "end": end, "rows": rows}


def trades(rows: list[dict], off: float, k: str, keep=lambda mv: True, ex: str = "take3_trail") -> list[tuple[str, float]]:
    return [(r["day"], r["res"][str(off)][k][ex]) for r in rows
            if str(off) in r["res"] and k in r["res"][str(off)] and keep(abs(r["move"]))]


def groups(rows: list[dict], off: float, k: str) -> dict[str, list[tuple[str, float]]]:
    g = {"每天都做": trades(rows, off, k)}
    for lo, hi in BANDS:
        name = f"10:00 离开盘 {lo:.1%}–{hi:.1%}" if hi < 1 else f"10:00 离开盘 {lo:.1%} 以上"
        g[name] = trades(rows, off, k, lambda mv, lo=lo, hi=hi: lo <= mv < hi)
    for f in FLOORS:
        g[f"不到 {f:.1%} 就不做"] = trades(rows, off, k, lambda mv, f=f: mv >= f)
    return g


def live_trades(root: Path, ledger: str) -> dict[str, float]:
    """The sleeve's own same-day trades: day -> per $1 (its halves weighted by what each cost)."""
    try:
        sl = json.loads((root / "journal-s500" / "sleeves.json").read_text())["sleeves"][ledger]
    except Exception:
        return {}
    by: dict[str, list[tuple[float, float]]] = {}
    for c in sl.get("closed", []):
        if c.get("kind") == "zdte" and c.get("ret") is not None and c.get("paid"):
            by.setdefault(c["opened"][:10], []).append((float(c["paid"]), float(c["ret"])))
    return {d: sum(p * r for p, r in xs) / sum(p for p, _ in xs) for d, xs in by.items()}


def pct(v) -> str:
    return "—" if v is None else f"{v:+.0%}"


def report(rep: dict, root: Path = ROOT) -> tuple[dict, str]:
    end, table, out = rep["end"], {}, []
    for off, (title, ledger) in OFFS.items():
        out += [f"## {title}：离现价 {off:.1%}", "", "每天都做，现行出场（一半 3 倍、其余回撤）：", "",
                "| 成本假设 | 笔数 | 平均每 $1 | 去掉最好 3 笔 | 中位数 | 收在 2 倍以上 | 2024 | 2025–26 | 最近 3 个月 | 提醒 |",
                "|---|---|---|---|---|---|---|---|---|---|"]
        for buy, sell, name in COSTS:
            s = robust.summary(trades(rep["rows"], off, key(buy, sell)), end)
            table[f"{off}|{key(buy, sell)}|每天都做"] = s
            if not s:
                continue
            ys = s["years"]
            y24 = ys.get("2024")
            later = [(m, n) for y, (m, n) in ys.items() if y >= "2025"]
            y2526 = (sum(m * n for m, n in later) / sum(n for _, n in later), sum(n for _, n in later)) if later else None
            cell = lambda v: f"{v[0]:+.0%}（{v[1]}）" if v else "—"
            out.append(f"| {name} | {s['n']} | {pct(s['mean'])} | {pct(s['trim'])} | {pct(s['median'])} | {s['x2']:.0%} | "
                       f"{cell(y24)} | {cell(y2526)} | {cell(s['recent'])} | {'；'.join(s['flags']) or '—'} |")
        out += ["", "按 10:00 时 SPY 离开盘走了多远（平均每 $1 / 去掉最好 3 笔，笔数）：", "",
                "| 情况 | 笔数 | " + " | ".join(n for _, _, n in COSTS) + " |", "|---|---|" + "---|" * len(COSTS)]
        gs = {key(b, s): groups(rep["rows"], off, key(b, s)) for b, s, _ in COSTS}
        for gname in gs[key(*COSTS[0][:2])]:
            cells, n = [], 0
            for b, s, _ in COSTS:
                st = robust.summary(gs[key(b, s)][gname], end)
                table[f"{off}|{key(b, s)}|{gname}"] = st
                n = st["n"] if st else n
                cells.append(f"{pct(st['mean'])} / {pct(st['trim'])}" if st else "—")
            out.append(f"| {gname} | {n} | " + " | ".join(cells) + " |")
        live = live_trades(root, ledger)
        days = {r["day"]: r for r in rep["rows"]}
        both = [d for d in sorted(live) if d in days and str(off) in days[d]["res"]]
        if both:
            out += ["", f"账本 {ledger} 自己的成交 vs 同一天的回测（每 $1）：", "",
                    "| 日期 | 10:00 离开盘 | 回测合约 | 账本实际 | " + " | ".join(n for _, _, n in COSTS) + " |",
                    "|---|---|---|---|" + "---|" * len(COSTS)]
            for d in both:
                r = days[d]
                cells = [pct(r["res"][str(off)].get(key(b, s), {}).get("take3_trail")) for b, s, _ in COSTS]
                out.append(f"| {d} | {r['move']:+.2%} | {r['syms'].get(str(off)) or r['syms'].get(off) or '—'} | "
                           f"{pct(live[d])} | " + " | ".join(cells) + " |")
        out.append("")
    head = [f"# SPY 末日单：按真实成本重算 {rep['generated']}", "",
            f"{rep['start']} 至 {rep['end']}，SPY 每个交易日 10:00，按现行挑法（当天到期；比开盘高买看涨、低买看跌；"
            "行权价离现价至少 0.6% 或 0.3%），一半 3 倍卖、其余翻倍后从最高回撤 40% 卖、否则 15:30 卖，按分钟收盘价。"
            "只换成本假设：research/2026-10-19-fills.md 里我们自己的当天到期成交，买入中位数比成交前一分钟收盘价便宜 3.9%，"
            "卖出中位数 0%（平均差 7%，被 $0.01 清仓单拉低）。「都按分钟收盘价」最接近实测、偏乐观；「现在的假设」偏悲观。"
            "最后一节拿两个 $1000 账本自己的成交和同一天的回测对照（账本每 10 分钟检查一次回撤，回测每分钟）。只研究，不改交易。", ""]
    return table, "\n".join(head + out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    rep = run(Alpaca())
    table, md = report(rep)
    save("zdte-cost", {**rep, "table": table}, md)


if __name__ == "__main__":
    sys.exit(main())
