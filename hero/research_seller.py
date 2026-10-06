"""Research: option-selling strategies for a $10,000 account (Claude-500's phase 2 candidate).

Only defined-risk structures an options-level-3 account may trade (no naked selling):
  1. SPY put credit spreads: sell a put 1.5% / 3% / 5% below the price, buy one $5 or $10 lower;
     weekly (~7 days) or monthly (~30 days) to expiry; held to expiry, or closed at 50% of the
     credit (take) / when the loss reaches 2x the credit (stop), checked on daily closes.
     Sized so each spread's maximum loss is 25% or 50% of the account.
  2. The wheel: sell a ~30-day cash-secured put 5% below the price; if assigned, hold the shares
     and sell ~30-day calls 5% above the price until called away. A $10,000 account can only
     secure stocks under $100, so the name is the strongest 126-day momentum stock of the
     dynamic pool (today's list: leans to today's winners) priced under cash / 100, re-picked at
     each new put; plus SPY with the account scaled up (as if $100,000) for comparison.
  3. Butterflies before events: the day before an earnings reaction (implied move from the
     earnings study) or an investor / product event, buy a call butterfly centred at the price
     with wings 0.5x / 1x the implied move (3% / 6% for events without one); sell at the
     reaction-day close, or hold to expiry. Control: the same names on ordinary days.
Prices are daily option closes; every leg pays max($0.01, 3%) against us on SPY and
max($0.02, 5%) on single stocks, each way. Daily closes miss intraday swings, so stops fill late
(losses may be understated) and there is no early assignment. Since 2024-02 (option history).
Record only. Run: python -m hero.research_seller
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

START = "2024-02-01"
CAPITAL = 10_000.0
SPREAD_OTM = (0.015, 0.03, 0.05)
SPREAD_WIDTH = (5, 10)
SPREAD_DTE = {"weekly": 7, "monthly": 30}
RISK = (0.25, 0.5)
FLY_WINGS = (0.5, 1.0)
EVENT_WINGS = (0.03, 0.06)


def slip(px: float, single: bool) -> float:
    return max(0.02, 0.05 * px) if single else max(0.01, 0.03 * px)


# ---------- data helpers ----------

def contracts(client, und: str, kind: str, **kw) -> list[dict]:
    out = []
    for status in ("inactive", "active"):
        try:
            out += client.option_contracts(und, status=status, type=kind, **kw)
        except Exception:
            pass
    return out


def closes_of(client, syms: list[str], start: str, end: str) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for i in range(0, len(syms), 100):
        try:
            bars = client.option_bars(syms[i:i + 100], start, end)
        except Exception:
            continue
        for s, bs in bars.items():
            out[s] = {b["t"][:10]: float(b["c"]) for b in bs}
    time.sleep(0.1)
    return out


def last_on_or_before(px: dict[str, float], day: str) -> float | None:
    ks = [d for d in px if d <= day]
    return px[max(ks)] if ks else None


# ---------- 1. put credit spreads ----------

def spread_result(short: dict[str, float], long: dict[str, float], days: list[str], entry: str, expiry: str,
                  ks: float, kl: float, settle: float, managed: bool) -> dict | None:
    """Per-spread credit, P&L and max loss (dollars per 1-lot), and the exit day."""
    if entry not in short or entry not in long:
        return None
    credit = (short[entry] - slip(short[entry], False)) - (long[entry] + slip(long[entry], False))
    width = ks - kl
    if credit <= 0.05 or credit >= width:
        return None
    exit_day, close_cost = expiry, None
    if managed:
        for d in [x for x in days if entry < x < expiry]:
            s, l_ = short.get(d), long.get(d)
            if s is None or l_ is None:
                continue
            cost = (s + slip(s, False)) - max(l_ - slip(l_, False), 0.0)
            if cost <= 0.5 * credit or cost >= 3 * credit:
                exit_day, close_cost = d, min(cost, width)
                break
    if close_cost is None:
        close_cost = min(max(ks - settle, 0.0) - max(kl - settle, 0.0), width)
    return {"entry": entry, "exit": exit_day, "credit": round(credit * 100, 2),
            "pnl": round((credit - close_cost) * 100, 2), "max_loss": round((width - credit) * 100, 2)}


def run_spreads(client, spy: dict[str, float]) -> list[dict]:
    days = sorted(spy)
    rows = []
    mondays, seen = [], set()
    for d in days:
        if d < START:
            continue
        wk = date.fromisoformat(d).isocalendar()[:2]
        if wk not in seen:
            seen.add(wk)
            mondays.append(d)
    for kind, dte in SPREAD_DTE.items():
        entries = mondays if kind == "weekly" else mondays[::4]
        for entry in entries:
            spot = spy[entry]
            e = date.fromisoformat(entry)
            cs = contracts(client, "SPY", "put", expiration_date_gte=(e + timedelta(days=dte - 3)).isoformat(),
                           expiration_date_lte=(e + timedelta(days=dte + 4)).isoformat(),
                           strike_price_gte=f"{spot * 0.9:.2f}", strike_price_lte=f"{spot * 0.99:.2f}")
            if not cs:
                continue
            exp = min({c["expiration_date"] for c in cs}, key=lambda x: abs((date.fromisoformat(x) - e).days - dte))
            if exp > days[-1]:
                continue
            strikes = {float(c["strike_price"]): c["symbol"] for c in cs if c["expiration_date"] == exp}
            plan = []
            for otm in SPREAD_OTM:
                ss = [k for k in strikes if k <= spot * (1 - otm)]
                if not ss:
                    continue
                ks = max(ss)
                for w in SPREAD_WIDTH:
                    if ks - w in strikes:
                        plan.append((otm, w, ks, ks - w))
            if not plan:
                continue
            px = closes_of(client, sorted({strikes[k] for p in plan for k in p[2:]}), entry, exp)
            settle = spy.get(exp) or last_on_or_before(spy, exp)
            for otm, w, ks, kl in plan:
                for managed in (False, True):
                    r = spread_result(px.get(strikes[ks], {}), px.get(strikes[kl], {}), days, entry, exp, ks, kl,
                                      settle, managed)
                    if r:
                        rows.append({"kind": kind, "otm": otm, "width": w, "managed": managed, **r})
    return rows


def account(trades: list[dict], risk: float, start_day: str, end_day: str) -> dict:
    """$10,000; one position at a time (the next entry after the last exit); each sized so its
    maximum loss is `risk` of the account (fractional lots, a simplification)."""
    eq, curve, last_exit = CAPITAL, [(start_day, CAPITAL)], ""
    for t in sorted(trades, key=lambda t: t["entry"]):
        if t["entry"] <= last_exit:
            continue
        lots = risk * eq / t["max_loss"]
        eq += lots * t["pnl"]
        curve.append((t["exit"], eq))
        last_exit = t["exit"]
        if eq <= 0:
            break
    return curve_stats(curve, start_day, end_day)


def curve_stats(curve: list[tuple[str, float]], start_day: str, end_day: str) -> dict:
    peak, dd = curve[0][1], 0.0
    for _, v in curve:
        peak = max(peak, v)
        dd = min(dd, v / peak - 1)
    monthly: dict[str, float] = {}
    for d, v in curve:
        monthly[d[:7]] = v
    months = sorted(monthly)
    rets, prev = {}, curve[0][1]
    for m in months:
        rets[m] = monthly[m] / prev - 1
        prev = monthly[m]
    years = max((date.fromisoformat(end_day) - date.fromisoformat(start_day)).days / 365.25, 0.1)
    final = curve[-1][1]
    worst = sorted(rets.items(), key=lambda x: x[1])[:3]
    return {"final": round(final), "cagr": round((max(final, 1) / curve[0][1]) ** (1 / years) - 1, 3),
            "max_dd": round(dd, 3), "worst_months": [(m, round(r, 3)) for m, r in worst],
            "trades": len(curve) - 1, "m2024_08": round(rets.get("2024-08", 0.0), 3),
            "m2025_04": round(rets.get("2025-04", 0.0), 3)}


# ---------- 2. the wheel ----------

def pick_option(client, und: str, kind: str, day: str, spot: float, otm: float, single: bool) -> dict | None:
    e = date.fromisoformat(day)
    lo, hi = (spot * (1 - otm - 0.1), spot * (1 - otm)) if kind == "put" else (spot * (1 + otm), spot * (1 + otm + 0.1))
    cs = contracts(client, und, kind, expiration_date_gte=(e + timedelta(days=25)).isoformat(),
                   expiration_date_lte=(e + timedelta(days=38)).isoformat(),
                   strike_price_gte=f"{lo:.2f}", strike_price_lte=f"{hi:.2f}")
    if not cs:
        return None
    exp = min({c["expiration_date"] for c in cs}, key=lambda x: abs((date.fromisoformat(x) - e).days - 30))
    by_k = {float(c["strike_price"]): c["symbol"] for c in cs if c["expiration_date"] == exp}
    k = max(by_k) if kind == "put" else min(by_k)
    px = closes_of(client, [by_k[k]], day, day).get(by_k[k], {}).get(day)
    if not px:
        return None
    return {"expiry": exp, "strike": k, "premium": max(px - slip(px, single), 0.0)}


def run_wheel(client, closes: dict[str, dict[str, float]], picker, capital: float, single: bool) -> dict:
    """picker(day, cash) -> symbol or None. Returns the account curve stats and the cycle log."""
    spy_days = sorted(closes["SPY"])
    day = next(d for d in spy_days if d >= START)
    end = spy_days[-1]
    cash, sym, shares, basis = capital, None, 0, 0.0
    curve, log = [(day, capital)], []
    while day < end:
        if shares == 0:
            sym = picker(day, cash)
            if not sym:
                day = next((d for d in spy_days if d > (date.fromisoformat(day) + timedelta(days=7)).isoformat()), end)
                continue
            spot = last_on_or_before(closes[sym], day)
            o = pick_option(client, sym, "put", day, spot, 0.05, single)
            n = int(cash // (o["strike"] * 100)) if o else 0
            if not o or n < 1 or o["expiry"] > end:
                day = next((d for d in spy_days if d > (date.fromisoformat(day) + timedelta(days=7)).isoformat()), end)
                continue
            cash += o["premium"] * 100 * n
            settle = last_on_or_before(closes[sym], o["expiry"])
            if settle < o["strike"]:
                shares, basis = 100 * n, o["strike"]
                cash -= o["strike"] * 100 * n
                log.append((day, sym, "put assigned", o["strike"], round(o["premium"], 2)))
            else:
                log.append((day, sym, "put expired", o["strike"], round(o["premium"], 2)))
        else:
            spot = last_on_or_before(closes[sym], day)
            o = pick_option(client, sym, "call", day, spot, 0.05, single)
            if not o or o["expiry"] > end:
                break
            cash += o["premium"] * shares
            settle = last_on_or_before(closes[sym], o["expiry"])
            if settle > o["strike"]:
                cash += o["strike"] * shares
                log.append((day, sym, "called away", o["strike"], round(o["premium"], 2)))
                shares = 0
            else:
                log.append((day, sym, "call expired", o["strike"], round(o["premium"], 2)))
        day = next((d for d in spy_days if d > o["expiry"]), end)
        mark = cash + (shares * last_on_or_before(closes[sym], day) if shares else 0)
        curve.append((day, mark))
    stats = curve_stats(curve, curve[0][0], end)
    return {**stats, "cycles": len(log), "log": log[-40:]}


# ---------- 3. butterflies before events ----------

def fly_result(px: dict[str, dict[str, float]], legs: tuple[str, str, str], ks: tuple[float, float, float], entry: str,
               react: str, settle_react: float, settle_exp: float | None) -> dict | None:
    lo, mid, hi = (px.get(s, {}) for s in legs)
    if not all(entry in x for x in (lo, mid, hi)):
        return None
    cost = (lo[entry] + slip(lo[entry], True)) + (hi[entry] + slip(hi[entry], True)) - 2 * (mid[entry] - slip(mid[entry], True))
    if cost <= 0.02:
        return None
    w = ks[1] - ks[0]

    def intrinsic(s):
        return max(0.0, w - abs(s - ks[1]))
    if all(react in x for x in (lo, mid, hi)):
        val = (lo[react] - slip(lo[react], True)) + (hi[react] - slip(hi[react], True)) - 2 * (mid[react] + slip(mid[react], True))
        val = max(val, 0.0)
    else:
        val = intrinsic(settle_react) * 0.5  # no trades that day: assume half the intrinsic could be had
    out = {"cost": round(cost, 3), "ret_react": round(val / cost - 1, 3), "max_mult": round(w / cost, 2)}
    if settle_exp is not None:
        out["ret_expiry"] = round(intrinsic(settle_exp) / cost - 1, 3)
    return out


def fly_trades(client, sym: str, entry: str, react: str, spot: float, halves: list[float],
               closes: dict[str, float]) -> list[dict | None]:
    """One butterfly per wing size, sharing one contract lookup and one price fetch."""
    e = date.fromisoformat(react)
    top = max(halves)
    cs = contracts(client, sym, "call", expiration_date_gte=react, expiration_date_lte=(e + timedelta(days=10)).isoformat(),
                   strike_price_gte=f"{spot * (1 - 2 * top - 0.02):.2f}", strike_price_lte=f"{spot * (1 + 2 * top + 0.02):.2f}")
    if not cs:
        return [None] * len(halves)
    exp = min(c["expiration_date"] for c in cs)
    by_k = {float(c["strike_price"]): c["symbol"] for c in cs if c["expiration_date"] == exp}
    ks = sorted(by_k)
    mid = min(ks, key=lambda k: abs(k - spot))
    plans = []
    for half in halves:
        w = min((k - mid for k in ks if k > mid), key=lambda d: abs(d - spot * half), default=None)
        ok = w and (mid - w) in by_k and (mid + w) in by_k
        plans.append(((by_k[mid - w], by_k[mid], by_k[mid + w]), (mid - w, mid, mid + w)) if ok else None)
    wanted = sorted({leg for pl in plans if pl for leg in pl[0]})
    if not wanted:
        return [None] * len(halves)
    px = closes_of(client, wanted, entry, exp)
    settle_exp = closes.get(exp) or (last_on_or_before(closes, exp) if exp <= max(closes) else None)
    return [fly_result(px, pl[0], pl[1], entry, react, closes[react], settle_exp) if pl else None for pl in plans]


def run_flies(client, root: Path, raw: dict[str, dict[str, float]]) -> list[dict]:
    rows = []
    earn = sorted((root / "research").glob("*-earnings-reactions.json"))
    evs = sorted((root / "research").glob("*-events.json"))
    plans = []
    imp: dict[str, list[float]] = {}
    if earn:
        for e in json.loads(earn[-1].read_text())["events"]:
            if e.get("implied_move") and e["reaction_day"] >= START:
                plans.append(("财报", e["symbol"], e["reaction_day"], [w * e["implied_move"] for w in FLY_WINGS]))
                imp.setdefault(e["symbol"], []).append(e["implied_move"])
    if evs:
        for e in json.loads(evs[-1].read_text())["event_list"]:
            plans.append(("投资者日 / 发布会", e["symbol"], e["reaction_day"], list(EVENT_WINGS)))
    # control: the earnings names on ordinary days, with their typical implied move
    for s, ims in imp.items():
        closes = raw.get(s)
        if not closes:
            continue
        days = [d for d in sorted(closes) if d >= START]
        busy = {p[2] for p in plans if p[1] == s}
        m = statistics.median(ims)
        for d in days[40:-12:42]:
            if not any(abs((date.fromisoformat(d) - date.fromisoformat(b)).days) < 10 for b in busy):
                plans.append(("对照：普通交易日", s, d, [w * m for w in FLY_WINGS]))
    for group, sym, react, halves in plans:
        closes = raw.get(sym)
        if not closes or react not in closes:
            continue
        days = sorted(closes)
        i = days.index(react)
        if i == 0:
            continue
        entry = days[i - 1]
        for j, r in enumerate(fly_trades(client, sym, entry, react, closes[entry], halves, closes)):
            if r:
                rows.append({"group": group, "symbol": sym, "entry": entry, "react": react, "wing": j, **r})
    return rows


def fly_account(trades: list[dict], key: str, stake: float, start: str, end: str) -> dict:
    """$10,000, `stake` of the account on each butterfly (its whole cost is the risk), in date order."""
    eq, curve = CAPITAL, [(start, CAPITAL)]
    for t in sorted(trades, key=lambda t: t["entry"]):
        if t.get(key) is None:
            continue
        eq += stake * eq * t[key]
        curve.append((t["react"], eq))
    return curve_stats(curve, start, end)


# ---------- run ----------

def run(client, root: Path) -> dict:
    from hero import universe
    pool = universe.load(root / "data" / "universe.json")["stocks"]
    first = (date.fromisoformat(START) - timedelta(days=200)).isoformat()
    syms = sorted(set(pool) | {"SPY"})
    raw: dict[str, dict[str, float]] = {}
    for i in range(0, len(syms), 50):
        for s, bs in client.daily_bars(syms[i:i + 50], first, adjustment="raw").items():
            raw[s] = {b["t"][:10]: float(b["c"]) for b in bs}
    spy = raw["SPY"]
    end = sorted(spy)[-1]
    spreads = run_spreads(client, spy)
    spread_table = {}
    for kind in SPREAD_DTE:
        for otm in SPREAD_OTM:
            for w in SPREAD_WIDTH:
                for managed in (False, True):
                    ts = [t for t in spreads if (t["kind"], t["otm"], t["width"], t["managed"]) == (kind, otm, w, managed)]
                    if not ts:
                        continue
                    key = f"{kind}|{otm}|{w}|{managed}"
                    spread_table[key] = {"n": len(ts), "win": round(sum(t["pnl"] > 0 for t in ts) / len(ts), 3),
                                         "avg_credit": round(statistics.mean(t["credit"] for t in ts), 1),
                                         "avg_pnl_on_risk": round(statistics.mean(t["pnl"] / t["max_loss"] for t in ts), 4),
                                         "worst_on_risk": round(min(t["pnl"] / t["max_loss"] for t in ts), 3),
                                         **{f"risk{r}": account(ts, r, START, end) for r in RISK}}

    def momentum_under_cash(day: str, cash: float) -> str | None:
        best, score = None, -9.0
        for s in pool:
            c = raw.get(s)
            if not c or day not in c:
                continue
            ds = [d for d in sorted(c) if d <= day]
            if len(ds) < 127 or c[day] * 100 > cash:
                continue
            m = c[day] / c[ds[-127]] - 1
            if m > score:
                best, score = s, m
        return best
    wheels = {"动量股（价格低于现金 / 100）": run_wheel(client, raw, momentum_under_cash, CAPITAL, True),
              "SPY（账户放大到 $100,000 才做得了）": run_wheel(client, raw, lambda d, c: "SPY", 100_000.0, False)}
    flies = run_flies(client, root, raw)
    fly_table = {}
    for g in sorted({t["group"] for t in flies}):
        for j in range(2):
            ts = [t for t in flies if t["group"] == g and t["wing"] == j]
            if not ts:
                continue
            row = {"n": len(ts), "cost_to_max": round(statistics.median(1 / t["max_mult"] for t in ts), 2)}
            for key in ("ret_react", "ret_expiry"):
                xs = [t[key] for t in ts if t.get(key) is not None]
                if xs:
                    row[key] = {"mean": round(statistics.mean(xs), 3), "median": round(statistics.median(xs), 3),
                                "win": round(sum(x > 0 for x in xs) / len(xs), 3)}
            row["acct_react_5pct"] = fly_account(ts, "ret_react", 0.05, START, end)
            fly_table[f"{g}|{j}"] = row
    return {"generated": date.today().isoformat(), "start": START, "end": end, "capital": CAPITAL,
            "spreads": spread_table, "wheels": wheels, "flies": fly_table,
            "spy_buy_hold": round(spy[end] / spy[next(d for d in sorted(spy) if d >= START)] - 1, 3)}


def markdown(rep: dict) -> str:
    out = [f"# $10,000 账户的卖方策略 {rep['generated']}", "",
           f"{rep['start']} 至 {rep['end']}，只用有上限的结构（期权等级 3，不裸卖）。价格用期权每日收盘价；SPY 每腿进出各吃 max($0.01, 3%)、个股 max($0.02, 5%) 的差价。"
           "只看收盘价，止损会比实际晚（亏损可能被低估），也没算提前被行权。只研究，不改交易。"
           f"同期 SPY 买入持有 {rep['spy_buy_hold']:+.0%}。", "",
           "## 1. SPY 卖看跌价差", "",
           "「管理」= 赚到一半权利金就平、亏到权利金 2 倍就止损；否则拿到期。仓位：每笔最大亏损占账户 25% 或 50%，一次只持一笔。", "",
           "| 周期 | 卖出离现价 | 宽度 | 管理 | 笔数 | 胜率 | 平均权利金 | 平均每笔（占最大亏损） | 最差一笔 | 25% 仓位：年化 / 最大回撤 / 2024-08 / 2025-04 | 50% 仓位：年化 / 最大回撤 |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for key, r in rep["spreads"].items():
        kind, otm, w, managed = key.split("|")
        a, b = r["risk0.25"], r["risk0.5"]
        out.append(f"| {'每周' if kind == 'weekly' else '每月'} | {float(otm):.1%} | ${w} | {'是' if managed == 'True' else '否'} | {r['n']} | "
                   f"{r['win']:.0%} | ${r['avg_credit']:.0f} | {r['avg_pnl_on_risk']:+.1%} | {r['worst_on_risk']:+.0%} | "
                   f"{a['cagr']:+.0%} / {a['max_dd']:.0%} / {a['m2024_08']:+.0%} / {a['m2025_04']:+.0%} | {b['cagr']:+.0%} / {b['max_dd']:.0%} |")
    out += ["", "## 2. 轮动（wheel）：卖现金担保看跌 → 被行权就拿股票、卖备兑看涨", "",
            "| 标的 | 结束资金 | 年化 | 最大回撤 | 2024-08 | 2025-04 | 最差三个月 | 循环次数 |", "|---|---|---|---|---|---|---|---|"]
    for name, w in rep["wheels"].items():
        worst = "、".join(f"{m} {r:+.0%}" for m, r in w["worst_months"])
        out.append(f"| {name} | ${w['final']:,} | {w['cagr']:+.0%} | {w['max_dd']:.0%} | {w['m2024_08']:+.0%} | {w['m2025_04']:+.0%} | {worst} | {w['cycles']} |")
    out += ["", "## 3. 事件前开蝴蝶（中心 = 现价，翼宽 = 预期涨跌幅 0.5 倍 / 1 倍；没有预期幅度的事件用 3% / 6%）", "",
            "| 组别 | 翼宽 | 笔数 | 成本 / 最大价值 | 反应日收盘卖：平均 / 中位数 / 胜率 | 持有到期：平均 / 胜率 | 每笔押 5%：年化 / 最大回撤 |",
            "|---|---|---|---|---|---|---|"]
    for key, r in rep["flies"].items():
        g, j = key.split("|")
        rr, re_ = r.get("ret_react", {}), r.get("ret_expiry", {})
        a = r["acct_react_5pct"]
        out.append(f"| {g} | {'窄（0.5 倍 / 3%）' if j == '0' else '宽（1 倍 / 6%）'} | {r['n']} | {r['cost_to_max']:.0%} | "
                   f"{rr.get('mean', 0):+.0%} / {rr.get('median', 0):+.0%} / {rr.get('win', 0):.0%} | "
                   f"{re_.get('mean', 0):+.0%} / {re_.get('win', 0):.0%} | {a['cagr']:+.0%} / {a['max_dd']:.0%} |")
    return "\n".join(out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca(), root)
    (root / "research" / f"{rep['generated']}-seller.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False) + "\n")
    (root / "research" / f"{rep['generated']}-seller.md").write_text(markdown(rep))
    print(markdown(rep))


if __name__ == "__main__":
    sys.exit(main())
