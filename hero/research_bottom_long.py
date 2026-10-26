"""Bottom timing held longer, on a $2,000 ledger (owner, 10-26: 「抄底是可以选择呆久一点的 可以模拟用 2000 抄底 回测」).

research/2026-10-26-bottom.md bought next week's call after each kind of selling on SPY / QQQ / SMH / IWM and sold it
within five sessions. This keeps the same signals (hero.research_bottom.signals: the drawdown crossings, the down
streaks, the big drops, the first green close, the reclaim, the VIXY spikes, and every fifth session as the control)
but buys the call 20 to 45 days out, at the money and 5% out, and holds it 5 / 10 / 15 / 20 sessions, or to 2x /
3x, or on a 40% giveback after 2x. Costs 5% a side (and 10%). The hero game is replayed for a $2,000 ledger whose
hero is $10,000 (5x, not the $500 ledger's 20x) and whose zero line is 10%. Beside the options, the 3x fund (SOXL,
TQQQ, SPXL, TNA) bought at the signal close and held 10 / 20 sessions, per $1, from the same daily bars. A time budget
keeps a partial run committable. Research only."""
from __future__ import annotations

import os
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from hero.research_bottom import ETFS, LABEL, signals
from hero.research_dip_week import START
from hero.research_event_turn import Pace
from hero.research_hero_game import game, interval
from hero.research_ideas import report, save

ROOT = Path(__file__).resolve().parent.parent
HOLD = 20
DTE = (20, 45)
OFFS = {"atm": 0.0, "otm5": 0.05}
EXITS = {"d5": "第 5 天收盘卖", "d10": "第 10 天", "d20": "第 20 天（先到期就到期）", "take2": "2 倍止盈，否则第 20 天",
         "trail2": "翻倍后回撤 40% 卖，否则第 20 天"}
LETF = {"SMH": "SOXL", "QQQ": "TQQQ", "SPY": "SPXL", "IWM": "TNA"}
LEDGER, HERO, ZERO = 2000.0, 5.0, 0.1
BUDGET_MIN = float(os.environ.get("RESEARCH_BUDGET_MIN", "115"))
MAIN = ("down3", "green1", "dd5", "dd8", "bigdown", "reclaim", "every5")


def option_path(entry: float, marks: dict[str, float], sessions: list[str], expiry: str, strike: float,
                stock: dict[str, float]) -> tuple[list[float], bool]:
    """The option's close on each of the next HOLD sessions (a session without a trade keeps the last close; the
    expiry session is worth what the stock leaves it); done when HOLD sessions passed or the expiry did."""
    out, last = [], entry
    for d in sessions[:HOLD]:
        if d > expiry:
            return out, True
        px = marks.get(d)
        if px is None and d == expiry and d in stock:
            px = max(stock[d] - strike, 0.0)
        last = last if px is None else px
        out.append(last)
        if d == expiry:
            return out, True
    return out, len(out) >= HOLD


def exits(cost: float, path: list[float], done: bool, slip: float) -> dict[str, float]:
    out_px = lambda c: max(c * (1 - slip), 0.0) / cost - 1
    res = {f"d{k}": out_px(path[k - 1]) for k in (5, 10, 15) if len(path) >= k}
    if not done or not path:
        return {k: round(v, 4) for k, v in res.items()}
    for k in (5, 10, 15):
        res.setdefault(f"d{k}", out_px(path[-1]))
    res["d20"] = out_px(path[-1])
    res["take2"] = next((out_px(c) for c in path if c >= 2 * cost), res["d20"])
    res["take3"] = next((out_px(c) for c in path if c >= 3 * cost), res["d20"])
    best, armed, res["trail2"] = 0.0, False, res["d20"]
    for c in path:
        best = max(best, c)
        armed = armed or c >= 2 * cost
        if armed and c <= 0.6 * best:
            res["trail2"] = out_px(c)
            break
    return {k: round(v, 4) for k, v in res.items()}


def legs(client, und: str, day: str, spot: float, until: str, sessions: list[str], stock: dict[str, float]) -> dict[str, dict]:
    d0 = date.fromisoformat(day)
    cs = []
    for status in ("inactive", "active"):
        try:
            cs += client.option_contracts(und, status=status, type="call",
                                          expiration_date_gte=(d0 + timedelta(days=DTE[0])).isoformat(),
                                          expiration_date_lte=(d0 + timedelta(days=DTE[1])).isoformat(),
                                          strike_price_gte=f"{spot * 0.97:.2f}", strike_price_lte=f"{spot * 1.10:.2f}")
        except Exception:
            pass
    if not cs:
        return {}
    exp = min(c["expiration_date"] for c in cs)
    ks = sorted((float(c["strike_price"]), c["symbol"]) for c in cs if c["expiration_date"] == exp)
    want = {"atm": min(ks, key=lambda k: abs(k[0] - spot)), "otm5": next((k for k in ks if k[0] >= spot * 1.05), None)}
    syms = sorted({k[1] for k in want.values() if k})
    try:
        bars = client.option_bars(syms, day, f"{until}T23:00:00Z", timeframe="1Day")
    except Exception:
        return {}
    out = {}
    for name, k in want.items():
        if not k:
            continue
        marks = {b["t"][:10]: float(b["c"]) for b in bars.get(k[1], [])}
        if not marks.get(day):
            continue
        path, done = option_path(marks[day], marks, sessions, exp, k[0], stock)
        out[name] = {"option": k[1], "expiry": exp, "dte": (date.fromisoformat(exp) - d0).days, "cost": round(marks[day] * 1.05, 4),
                     "res": exits(marks[day] * 1.05, path, done, 0.05), "res10": exits(marks[day] * 1.10, path, done, 0.10)}
    return out


def letf_hold(closes: list[float], i: int) -> dict[str, float]:
    """The 3x fund bought at the signal close, per $1 after 10 and 20 sessions (no costs: shares)."""
    out = {}
    for k in (10, 20):
        if i + k < len(closes):
            out[f"d{k}"] = round(closes[i + k] / closes[i] - 1, 4)
    return out


def run(client, budget_min: float = BUDGET_MIN) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    syms = list(ETFS) + ["VIXY"] + list(LETF.values())
    split = client.daily_bars(syms, "2023-12-01", adjustment="split", feed="sip")
    raw = client.daily_bars(list(ETFS), "2023-12-01", adjustment="raw", feed="sip")
    vix = {b["t"][:10]: float(b["c"]) for b in split.get("VIXY", []) if b["t"][:10] <= end}
    rows, letf_rows, missing, done, t0 = [], [], 0, [], time.monotonic()
    pace = Pace(client)
    for und in ETFS:
        bs = [b for b in split.get(und, []) if b["t"][:10] <= end]
        if len(bs) < 30:
            done.append(und)
            continue
        days = [b["t"][:10] for b in bs]
        stock = {b["t"][:10]: float(b["c"]) for b in raw.get(und, [])}
        lev = {b["t"][:10]: float(b["c"]) for b in split.get(LETF[und], [])}
        lev_closes = [lev.get(d) for d in days]
        stop = False
        for e in signals(days, [float(b["o"]) for b in bs], [float(b["c"]) for b in bs], und, vix):
            if e["day"] < START or e["sig"] not in MAIN:
                continue
            if all(c is not None for c in lev_closes[e["i"]:e["i"] + 21]):
                letf_rows.append({**e, "fund": LETF[und], "res": letf_hold(lev_closes, e["i"])})
            if (time.monotonic() - t0) / 60 > budget_min:
                stop = True
                break
            got = legs(client, und, e["day"], stock.get(e["day"], 0.0) or float(bs[e["i"]]["c"]), end, days[e["i"] + 1:], stock)
            if not got:
                missing += 1
                rows.append({**e, "leg": None, "res": {}})
            for name, leg in got.items():
                rows.append({**e, "leg": name, **leg})
            pace.wait()
        if stop:
            break
        done.append(und)
    return {"start": START, "end": end, "etfs": list(ETFS), "done": done, "partial": len(done) < len(ETFS), "rows": rows,
            "letf": letf_rows, "missing": missing, "minutes": round((time.monotonic() - t0) / 60, 1)}


def groups(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for sig in MAIN:
        for leg, lname in (("atm", "平值"), ("otm5", "价外 5%")):
            out[f"{LABEL[sig]} · {lname}"] = [r for r in rows if r.get("leg") == leg and r["sig"] == sig]
    for sig in ("down3", "green1", "dd8"):
        for und in ETFS:
            out[f"　{LABEL[sig]} · {und} · 平值"] = [r for r in rows if r.get("leg") == "atm" and r["sig"] == sig and r["und"] == und]
    return {k: v for k, v in out.items() if v}


def series(rows: list[dict], sig: str, leg: str, exit_: str, key: str = "res") -> list[float]:
    """One ticket a day across the four ETFs (the first in ETFS order when several fire)."""
    by: dict[str, float] = {}
    for r in sorted(rows, key=lambda r: ETFS.index(r["und"])):
        if r.get("leg") == leg and r["sig"] == sig and (r.get(key) or {}).get(exit_) is not None:
            by.setdefault(r["day"], r[key][exit_])
    return [by[d] for d in sorted(by)]


def hero_table(rows: list[dict]) -> tuple[dict, str]:
    out = {}
    lines = ["", f"## hero 游戏：$2,000 起步、到 $10,000（5 倍）算 hero、跌破 $200 归零；每天一笔，按日期重放成轮", "",
             "| 信号 · 腿 · 出场 · 成本 | 笔数 | 平均 | 押一半：hero / 轮（区间） | 每个 hero 的成本 | 押满：hero / 轮（区间） | 每个 hero 的成本 |",
             "|---|---|---|---|---|---|---|"]
    for sig in MAIN:
        for leg, lname in (("atm", "平值"), ("otm5", "价外 5%")):
            for ex in ("d10", "d20", "trail2"):
                for key, cost in (("res", "5%"), ("res10", "10%")):
                    xs = series(rows, sig, leg, ex, key)
                    if len(xs) < 15:
                        continue
                    g5, g10 = game(xs, 0.5, ZERO, HERO), game(xs, 1.0, ZERO, HERO)
                    c5, c10 = interval(g5["heroes"], g5["rounds"]) or (0, 0), interval(g10["heroes"], g10["rounds"]) or (0, 0)
                    cost5 = g5["rounds"] * LEDGER / g5["heroes"] if g5["heroes"] else None
                    cost10 = g10["rounds"] * LEDGER / g10["heroes"] if g10["heroes"] else None
                    out[f"{sig}|{leg}|{ex}|{cost}"] = {"n": len(xs), "mean": statistics.mean(xs), "half": g5, "full": g10,
                                                     "cost_half": cost5, "cost_full": cost10}
                    lines.append(f"| {LABEL[sig]} · {lname} · {ex} · {cost} | {len(xs)} | {statistics.mean(xs):+.0%} | "
                                 f"{g5['heroes']}/{g5['rounds']}（{c5[0]:.0%}–{c5[1]:.0%}） | {('$%s' % format(cost5, ',.0f')) if cost5 else '—'} | "
                                 f"{g10['heroes']}/{g10['rounds']}（{c10[0]:.0%}–{c10[1]:.0%}） | {('$%s' % format(cost10, ',.0f')) if cost10 else '—'} |")
    return out, "\n".join(lines) + "\n"


def letf_table(rows: list[dict]) -> str:
    lines = ["", "## 对比：3 倍杠杆 ETF 正股（SOXL / TQQQ / SPXL / TNA）信号当天收盘买，拿 10 / 20 天，每 $1（不算成本）", "",
             "| 信号 | 笔数 | 拿 10 天 | 拿 20 天 | 20 天赚钱比例 | 20 天中位数 |", "|---|---|---|---|---|---|"]
    for sig in MAIN:
        xs10 = [r["res"]["d10"] for r in rows if r["sig"] == sig and "d10" in r["res"]]
        xs20 = [r["res"]["d20"] for r in rows if r["sig"] == sig and "d20" in r["res"]]
        if not xs20:
            continue
        lines.append(f"| {LABEL[sig]} | {len(xs20)} | {statistics.mean(xs10):+.1%} | {statistics.mean(xs20):+.1%} | "
                     f"{sum(x > 0 for x in xs20) / len(xs20):.0%} | {statistics.median(xs20):+.1%} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    rep = run(Alpaca())
    gs = groups(rep["rows"])
    table, md = report(f"抄底拿久一点：3–6 周到期的看涨，$2,000 账本到 5 倍 {date.today().isoformat()}",
                       f"{rep['start']} 至 {rep['end']}，SPY / QQQ / SMH / IWM（跑完 {len(rep['done'])} 只{'，预算到了没跑完' if rep['partial'] else ''}）。"
                       f"信号同 research/2026-10-26-bottom.md（连跌第 3 天、连跌后第一根阳线、跌到 20 日高点下 5% / 8%、单日大跌、低开收复，对照每第 5 天），"
                       f"买 {DTE[0]}–{DTE[1]} 天到期的平值 / 价外 5% 看涨，拿 5 / 10 / 20 天或 2 倍止盈、翻倍后回撤 40% 卖，买卖各多付 5%。"
                       f"没有期权数据的 {rep['missing']} 个。", gs, EXITS, rep["end"])
    rep["table"] = table
    rep["hero"], hero_md = hero_table(rep["rows"])
    md += letf_table(rep["letf"]) + hero_md
    md += ("\n## 读法\n\n- 先和 research/2026-10-26-bottom.md（下周到期、第 5 天卖）比：拿久、买远是不是更好。\n"
           "- $2,000 账本只要 5 倍，轮数会比 $500 到 20 倍多；看每个 hero 的成本和区间。\n- 只研究，不改交易。\n")
    save("bottom-long", rep, md)


if __name__ == "__main__":
    sys.exit(main())
