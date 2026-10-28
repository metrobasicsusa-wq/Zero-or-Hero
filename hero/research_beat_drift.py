"""Calls after a chip / AI-hardware report that beat its options' price (owner, 10-28: 「自己选个大研究去做」; the stock
version is research/2026-10-28-earnings-overshoot.md: chips and AI hardware that closed the reaction day up more than
1.5x the straddle went on to beat SPY by +9% over 20 sessions, median +3%, 70% of 30, every year positive).

Can a small account ride that with options? Events from research/2026-10-02-earnings-reactions.json since 2024-02:
the reaction day closed up at least `RATIO` times the implied move. On the next session at 10:00 buy a call 25 to 50
days out, at the money or 5% out; out on daily option bars: the 10th or 20th close, a 2x take on a close (else the
20th), a trail (2x then a 40% giveback, else the 20th). Comparisons: the same for every group, for 1-1.5x beats, and
puts after chip / AI-hardware reports that fell more than 1.5x (the stock version found no edge there). Buying pays
10% more and selling gets 10% less (research_ideas). Record only. Run: python -m hero.research_beat_drift
"""

from __future__ import annotations

import json
import sys
import time
from datetime import date, timedelta

from hero.research_earnings_overshoot import EVENTS
from hero.research_earnings_rules import GROUP
from hero.research_0dte import at_or_after
from hero.research_ideas import cost_of, dailies, minutes, multiday, pick, report, save

START = "2024-02-01"
RATIO = 1.5
HARD = ("芯片", "AI 硬件 / 算力")
LEGS = {"atm": 0.0, "otm5": 0.05}
ENTRY_WITHIN = 60    # minutes after 10:00 to find the first trade: these 1-2 month contracts trade less than weeklies
EXITS = {"close_d10": "第 10 天收盘卖", "close_d20": "第 20 天收盘卖", "take2": "收盘翻倍就卖，否则第 20 天", "trail2": "翻倍后回撤 40% 卖，否则第 20 天"}


def events(rep: dict, start: str = START) -> list[dict]:
    """Reports with a straddle price since `start` that moved at least 1x their implied move, tagged."""
    out = []
    for e in rep["events"]:
        imp = e.get("implied_move")
        if not imp or e["reaction_day"] < start:
            continue
        ratio = abs(e["move"]) / imp
        if ratio < 1.0:
            continue
        out.append({"und": e["symbol"], "react": e["reaction_day"], "kind": "C" if e["move"] > 0 else "P",
                    "move": e["move"], "ratio": round(ratio, 2), "group": GROUP.get(e["symbol"], "其他")})
    return out


def third_friday(d: date) -> bool:
    return d.weekday() == 4 and 15 <= d.day <= 21


def monthly_contract(client, und: str, kind: str, day: str, spot: float, off: float, lo: int = 25, hi: int = 50) -> tuple[str, str] | None:
    """(symbol, expiry) from `lo` to `hi` days out, the monthly (third Friday) expiry when there is one -- the liquid
    one -- else the nearest; first strike `off` beyond spot."""
    d0 = date.fromisoformat(day)
    cs = []
    for status in ("inactive", "active"):
        try:
            cs = client.option_contracts(und, status=status, type="call" if kind == "C" else "put",
                                         expiration_date_gte=(d0 + timedelta(days=lo)).isoformat(),
                                         expiration_date_lte=(d0 + timedelta(days=hi)).isoformat(),
                                         strike_price_gte=f"{spot * (1 - off - 0.08):.2f}", strike_price_lte=f"{spot * (1 + off + 0.08):.2f}")
        except Exception:
            cs = []
        if cs:
            break
    if not cs:
        return None
    exps = sorted({c["expiration_date"] for c in cs})
    exp = next((x for x in exps if third_friday(date.fromisoformat(x))), exps[0])
    sym = pick([c for c in cs if c["expiration_date"] == exp], kind, spot, off)
    return (sym, exp) if sym else None


def entry(mins: dict[str, dict], at: str = "10:00", within: int = ENTRY_WITHIN) -> tuple[str, float] | None:
    got = at_or_after(mins, at, within)
    if not got or float(got[1]["c"]) <= 0:
        return None
    return got[0], cost_of(float(got[1]["c"]))


def exits(cost: float, days: list[dict]) -> dict[str, float] | None:
    """multiday() at 20 sessions, plus the 10th close."""
    res = multiday(cost, days, hold=20)
    if not res:
        return None
    ten = multiday(cost, days, hold=10)
    res["close_d10"] = ten["close_d10"] if ten else res["close_d20"]
    return res


def run(client) -> dict:
    from hero.research_chase import bars_for
    end = (date.today() - timedelta(days=3)).isoformat()
    evs = events(json.loads(EVENTS.read_text()))
    bars = bars_for(client, sorted({e["und"] for e in evs}), f"{end}T23:00:00Z", "raw")
    rows = []
    for e in evs:
        bs = bars.get(e["und"], [])
        nxt = next((b for b in bs if b["t"][:10] > e["react"]), None)
        if not nxt or nxt["t"][:10] > end:
            continue
        day, spot = nxt["t"][:10], float(nxt["o"])
        for leg, off in LEGS.items():
            got = monthly_contract(client, e["und"], e["kind"], day, spot, off)
            if not got:
                continue
            sym, exp = got
            ec = entry(minutes(client, sym, day))
            if not ec:
                continue
            res = exits(ec[1], dailies(client, sym, day, exp))
            time.sleep(0.03)
            if res:
                rows.append({**e, "day": day, "leg": leg, "sym": sym, "cost": ec[1], "res": res})
    return {"generated": date.today().isoformat(), "start": START, "end": end, "rows": rows}


def groups(rows: list[dict]) -> dict[str, list[dict]]:
    c = [r for r in rows if r["kind"] == "C"]
    p = [r for r in rows if r["kind"] == "P"]
    hard = lambda r: r["group"] in HARD
    g = {}
    for leg, name in (("atm", "平值"), ("otm5", "价外 5%")):
        g[f"芯片 / AI 硬件 涨超定价 {RATIO} 倍 · {name}看涨"] = [r for r in c if r["leg"] == leg and hard(r) and r["ratio"] >= RATIO]
        g[f"　其中 2 倍以上 · {name}"] = [r for r in c if r["leg"] == leg and hard(r) and r["ratio"] >= 2.0]
        g[f"　对照：芯片 / AI 硬件 涨 1–{RATIO} 倍 · {name}"] = [r for r in c if r["leg"] == leg and hard(r) and r["ratio"] < RATIO]
        g[f"对照：其他板块 涨超定价 {RATIO} 倍 · {name}看涨"] = [r for r in c if r["leg"] == leg and not hard(r) and r["ratio"] >= RATIO]
        g[f"对照：芯片 / AI 硬件 跌超定价 {RATIO} 倍 · {name}看跌"] = [r for r in p if r["leg"] == leg and hard(r) and r["ratio"] >= RATIO]
    return g


def main() -> None:
    from hero.alpaca import Alpaca
    rep = run(Alpaca())
    table, md = report(f"芯片 / AI 硬件财报大超之后买看涨 {rep['generated']}",
                       f"{rep['start']} 至 {rep['end']}，{EVENTS.name} 里反应日收盘涨跌超过期权定价的财报（定价 = 财报前一天收盘的平值跨式）。"
                       "下一个交易日 10:00 起（这些 1–2 个月的合约成交少，取 11:00 前第一笔）买 25–50 天到期、优先月度到期的期权（平值或价外 5%），按期权日线卖出；买入多付 10%、卖出少拿 10%。"
                       "正股版（research/2026-10-28-earnings-overshoot.md）：芯片 / AI 硬件涨超定价 1.5 倍的 30 次，之后 20 天比 SPY 多涨 +9%、中位数 +3%。"
                       "幸存者偏差：都是今天的大公司。",
                       groups(rep["rows"]), EXITS, rep["end"])
    rep["table"] = table
    save("beat-drift", rep, md)


if __name__ == "__main__":
    sys.exit(main())
