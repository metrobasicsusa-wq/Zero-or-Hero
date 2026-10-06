"""Research: iron butterflies sold before earnings (a $10,000-account, phase-2 candidate).

The earnings study found the actual move smaller than the options-implied move 60% of the time.
An iron butterfly sells that implied move: the day before the reaction, sell the at-the-money call
and put, buy a call W above and a put W below (W = 1x / 1.5x / 2x the implied move), on the first
expiry on or after the reaction day. The most it can lose is W less the credit. Exits: buy it back
at the reaction-day close (after the volatility drop), or hold to expiry (intrinsic value).
Every leg pays max($0.02, 5%) against us each way, on daily closes. Control: the same names on
ordinary days, with each name's typical implied move. Then a $10,000 account risking 5% or 10% of
itself (the maximum loss) each reaction day, split evenly over that day's trades, in date order. Record only.
Run: python -m hero.research_ironfly
"""

from __future__ import annotations

import json
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

from hero.research_seller import CAPITAL, closes_of, contracts, curve_stats, last_on_or_before, slip

START = "2024-02-01"
WINGS = (1.0, 1.5, 2.0)
RISK = (0.05, 0.10)


def nearest(ks: list[float], x: float) -> float:
    return min(ks, key=lambda k: abs(k - x))


def ironfly(px: dict[str, dict[str, float]], legs: dict[str, str], k: float, w: float, entry: str, react: str,
            settle_react: float, settle_exp: float | None) -> dict | None:
    """legs: {'sc','sp','lc','lp'} -> option symbols (short call / put at k, long call k+w, long put k-w)."""
    p = {n: px.get(s, {}) for n, s in legs.items()}
    if not all(entry in p[n] for n in legs):
        return None
    e = {n: p[n][entry] for n in legs}
    credit = (e["sc"] - slip(e["sc"], True)) + (e["sp"] - slip(e["sp"], True)) \
        - (e["lc"] + slip(e["lc"], True)) - (e["lp"] + slip(e["lp"], True))
    if credit <= 0.05 or credit >= w:
        return None
    max_loss = w - credit

    def owed(s):  # what the position owes at expiry, capped at the wing width
        return min(abs(s - k), w)
    if all(react in p[n] for n in legs):
        r = {n: p[n][react] for n in legs}
        cost = (r["sc"] + slip(r["sc"], True)) + (r["sp"] + slip(r["sp"], True)) \
            - max(r["lc"] - slip(r["lc"], True), 0) - max(r["lp"] - slip(r["lp"], True), 0)
        cost = min(max(cost, 0.0), w)
    else:
        cost = owed(settle_react)  # no trades that day: assume the intrinsic value (a floor on time value)
    out = {"credit": round(credit, 3), "max_loss": round(max_loss, 3),
           "ret_react": round((credit - cost) / max_loss, 3)}  # per $1 of risk
    if settle_exp is not None:
        out["ret_expiry"] = round((credit - owed(settle_exp)) / max_loss, 3)
    return out


def trade(client, sym: str, entry: str, react: str, spot: float, move: float, closes: dict[str, float]) -> list[dict | None]:
    lim = (date.fromisoformat(react) + timedelta(days=10)).isoformat()
    top = max(WINGS) * move
    found = {}
    for kind in ("call", "put"):
        found[kind] = contracts(client, sym, kind, expiration_date_gte=react, expiration_date_lte=lim,
                                strike_price_gte=f"{spot * (1 - top - 0.03):.2f}", strike_price_lte=f"{spot * (1 + top + 0.03):.2f}")
    if not found["call"] or not found["put"]:
        return [None] * len(WINGS)
    exp = min(c["expiration_date"] for c in found["call"] + found["put"])
    calls = {float(c["strike_price"]): c["symbol"] for c in found["call"] if c["expiration_date"] == exp}
    puts = {float(c["strike_price"]): c["symbol"] for c in found["put"] if c["expiration_date"] == exp}
    both = sorted(set(calls) & set(puts))
    if not both:
        return [None] * len(WINGS)
    k = nearest(both, spot)
    plans = []
    for m in WINGS:
        up, dn = nearest(sorted(calls), k + spot * move * m), nearest(sorted(puts), k - spot * move * m)
        w = min(up - k, k - dn)
        if w <= 0 or (k + w) not in calls or (k - w) not in puts:
            plans.append(None)
            continue
        plans.append(({"sc": calls[k], "sp": puts[k], "lc": calls[k + w], "lp": puts[k - w]}, w))
    wanted = sorted({s for pl in plans if pl for s in pl[0].values()})
    if not wanted:
        return [None] * len(WINGS)
    px = closes_of(client, wanted, entry, exp)
    settle_exp = closes.get(exp) or (last_on_or_before(closes, exp) if exp <= max(closes) else None)
    return [ironfly(px, pl[0], k, pl[1], entry, react, closes[react], settle_exp) if pl else None for pl in plans]


def run(client, root: Path) -> dict:
    src = json.loads(sorted((root / "research").glob("*-earnings-reactions.json"))[-1].read_text())
    events = [e for e in src["events"] if e.get("implied_move") and e["reaction_day"] >= START]
    syms = sorted({e["symbol"] for e in events})
    raw: dict[str, dict[str, float]] = {}
    first = (date.fromisoformat(START) - timedelta(days=30)).isoformat()
    for i in range(0, len(syms), 50):
        for s, bs in client.daily_bars(syms[i:i + 50], first, adjustment="raw").items():
            raw[s] = {b["t"][:10]: float(b["c"]) for b in bs}
    plans = [("财报", e["symbol"], e["reaction_day"], e["implied_move"], abs(e.get("move") or 0)) for e in events]
    typical = {s: statistics.median(e["implied_move"] for e in events if e["symbol"] == s) for s in syms}
    for s in syms:
        c = raw.get(s)
        if not c:
            continue
        busy = [e["reaction_day"] for e in events if e["symbol"] == s]
        for d in [x for x in sorted(c) if x >= START][30:-12:42]:
            if all(abs((date.fromisoformat(d) - date.fromisoformat(b)).days) >= 10 for b in busy):
                plans.append(("对照：普通交易日", s, d, typical[s], None))
    rows = []
    for group, sym, react, move, actual in plans:
        c = raw.get(sym)
        if not c or react not in c:
            continue
        days = sorted(c)
        i = days.index(react)
        if i == 0:
            continue
        entry = days[i - 1]
        for j, r in enumerate(trade(client, sym, entry, react, c[entry], move, c)):
            if r:
                rows.append({"group": group, "symbol": sym, "entry": entry, "react": react, "wing": WINGS[j],
                             "implied": move, "actual": actual, **r})
    end = max(max(c) for c in raw.values())
    table = {}
    for g in sorted({r["group"] for r in rows}):
        for m in WINGS:
            sub = [r for r in rows if r["group"] == g and r["wing"] == m]
            if not sub:
                continue
            row = {"n": len(sub), "credit_to_risk": round(statistics.median(r["credit"] / r["max_loss"] for r in sub), 2)}
            for key in ("ret_react", "ret_expiry"):
                xs = [r[key] for r in sub if r.get(key) is not None]
                if xs:
                    row[key] = {"mean": round(statistics.mean(xs), 3), "median": round(statistics.median(xs), 3),
                                "win": round(sum(x > 0 for x in xs) / len(xs), 3), "worst": round(min(xs), 2)}
            by_day: dict[str, list[float]] = {}
            for r in sub:
                by_day.setdefault(r["react"], []).append(r["ret_react"])
            for risk in RISK:  # one day's trades share that day's risk budget, so busy days are not overbet
                eq, curve = CAPITAL, [(START, CAPITAL)]
                for d in sorted(by_day):
                    eq += risk * eq * statistics.mean(by_day[d])
                    curve.append((d, eq))
                row[f"acct{risk}"] = curve_stats(curve, START, end)
            table[f"{g}|{m}"] = row
    return {"generated": date.today().isoformat(), "start": START, "end": end, "events": len(events), "table": table,
            "rows": rows}


def markdown(rep: dict) -> str:
    out = [f"# 财报前卖铁蝴蝶 {rep['generated']}", "",
           f"{rep['start']} 至 {rep['end']}，{rep['events']} 次财报（来自 earnings-reactions 研究，带期权预期涨跌幅）。"
           "财报反应日前一天收盘：卖平值看涨和看跌，买上下各 W 的保护（W = 预期涨跌幅的 1 / 1.5 / 2 倍），最近到期。"
           "最多亏 W − 收到的权利金。每腿进出各吃 max($0.02, 5%)。收益按「每 $1 最大亏损」计。"
           "对照：同一批股票的普通交易日，用各自典型的预期涨跌幅。账户：$10,000，每个反应日的最大亏损合计占账户 5% 或 10%（同一天几笔平分）。只研究，不改交易。", "",
           "| 组别 | 翼宽 | 笔数 | 权利金 / 最大亏损 | 反应日收盘平仓：平均 / 中位数 / 胜率 / 最差 | 拿到期：平均 / 胜率 | 5%：年化 / 最大回撤 | 10%：年化 / 最大回撤 |",
           "|---|---|---|---|---|---|---|---|"]
    for key, r in rep["table"].items():
        g, m = key.split("|")
        a, e = r.get("ret_react", {}), r.get("ret_expiry", {})
        out.append(f"| {g} | {float(m):g} 倍 | {r['n']} | {r['credit_to_risk']:.2f} | "
                   f"{a.get('mean', 0):+.0%} / {a.get('median', 0):+.0%} / {a.get('win', 0):.0%} / {a.get('worst', 0):+.0%} | "
                   f"{e.get('mean', 0):+.0%} / {e.get('win', 0):.0%} | "
                   f"{r['acct0.05']['cagr']:+.0%} / {r['acct0.05']['max_dd']:.0%} | {r['acct0.1']['cagr']:+.0%} / {r['acct0.1']['max_dd']:.0%} |")
    return "\n".join(out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca(), root)
    (root / "research" / f"{rep['generated']}-ironfly.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    (root / "research" / f"{rep['generated']}-ironfly.md").write_text(markdown(rep))
    print(markdown(rep))


if __name__ == "__main__":
    sys.exit(main())
