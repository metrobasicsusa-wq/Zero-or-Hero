"""Research: scheduled company events (investor / analyst days, product keynotes) -- do short-dated
calls bought the day before pay? Prompted by Marvell's investor day (2026-10-06, up ~10% intraday).

Events come from Alpaca's news (Benzinga headlines, 2024-02 onward, the option history window)
for the stocks of the current dynamic pool. A headline naming the company (at most 3 tickers on
it) that matches an event phrase marks an event; headlines within 30 days of each other are one
event, dated by the first headline that reads as the event itself happening (not an announcement
of a future date). Its reaction day is that day, or the next trading day for a headline after
16:00 ET. "Pre-announced" events also had an announcement headline 3-60 days earlier, so someone could
have known the date in advance; the rest may only be visible afterwards (optimistic).

Trade: at the close of the day before the reaction day, buy a call (5% or 10% above the price) or a
put (5% or 10% below it) on the first expiry on or after it (within 10 days). Exits as in the earnings lottery study: the
reaction day's open, a resting 3x / 5x / 10x limit (else the close), the close, the day's high
(hindsight; an upper bound), or held to expiry. Costs: pay 10% over the daily close, get 10% under
(at least a cent). Control: the same stocks, same construction, every 42nd trading day.
Also the stock's own reaction-day move on event days vs all days. Record only.
Run: python -m hero.research_events
"""

from __future__ import annotations

import json
import re
import statistics
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from hero.research_lottery import entry_cost, exit_value

ET = ZoneInfo("America/New_York")
START = "2024-02-01"
POOL = 100
OTM = (0.05, 0.10)
SIDES = ("call", "put")
TAKE = (3, 5, 10)
CLUSTER_DAYS = 30
LEAD_DAYS = 60
PHRASES = {
    "investor": r"investor day|analyst day|capital markets day|investor meeting|strategy day|financial analyst",
    "product": r"keynote|product (?:launch|event)|launch event|developer conference|\bwwdc\b|\bgtc\b|re:invent|unveils?",
}
# "to host / will hold / announces ... on <date>" reads as a date being announced, not the event.
ANNOUNCE = re.compile(r"\bto (?:host|hold|present|webcast)\b|\bwill (?:host|hold)\b|\bannounces?\b.*\b(?:date|to host)\b|"
                      r"\bschedule[sd]?\b|\bupcoming\b|\bahead of\b|\bpreview\b|\bwhat to expect\b", re.I)


def kind_of(headline: str) -> str | None:
    h = headline.lower()
    for k, pat in PHRASES.items():
        if re.search(pat, h):
            return k
    return None


def reaction_day(created_at: str, days: list[str]) -> str | None:
    """Trading day the headline can first move the stock: same day before 16:00 ET, else the next."""
    t = datetime.fromisoformat(created_at.replace("Z", "+00:00")).astimezone(ET)
    d = t.date().isoformat()
    after = [x for x in days if (x > d if t.hour >= 16 else x >= d)]
    return after[0] if after else None


def find_events(items: list[dict], sym: str, days: list[str]) -> list[dict]:
    """Group a symbol's matching headlines into events (see the module docstring)."""
    hits = []
    for n in items:
        if sym not in (n.get("symbols") or []) or len(n.get("symbols") or []) > 3:
            continue
        k = kind_of(n.get("headline", ""))
        if k:
            hits.append({"t": n["created_at"], "kind": k, "headline": n["headline"],
                         "announce": bool(ANNOUNCE.search(n["headline"]))})
    hits.sort(key=lambda h: h["t"])
    announced = [date.fromisoformat(h["t"][:10]) for h in hits if h["announce"]]
    events, last = [], None
    for h in hits:
        if h["announce"]:
            continue
        d = date.fromisoformat(h["t"][:10])
        if last is not None and (d - last).days <= CLUSTER_DAYS:
            continue  # coverage of the same event
        last = d
        r = reaction_day(h["t"], days)
        if r:
            events.append({"symbol": sym, "kind": h["kind"], "reaction_day": r, "headline": h["headline"],
                           "pre_announced": any(3 <= (d - a).days <= LEAD_DAYS for a in announced)})
    return events


def trade(client, sym: str, entry_day: str, react: str, spot: float, react_close: float, closes: dict) -> list[dict]:
    """Calls and puts, one per OTM level each, bought at entry_day's close."""
    return [{"side": side, **t} for side in SIDES for t in one_side(client, sym, entry_day, react, spot, react_close, closes, side)]


def one_side(client, sym: str, entry_day: str, react: str, spot: float, react_close: float, closes: dict,
             side: str) -> list[dict]:
    """One option per OTM level (above the price for calls, below for puts); per-exit returns."""
    call = side == "call"
    payoff = (lambda px, k: max(px - k, 0.0)) if call else (lambda px, k: max(k - px, 0.0))
    lo, hi = (spot * 1.02, spot * 1.16) if call else (spot * 0.84, spot * 0.98)
    lim = (date.fromisoformat(react) + timedelta(days=10)).isoformat()
    cs = []
    for status in ("inactive", "active"):
        try:
            cs += client.option_contracts(sym, status=status, type=side, expiration_date_gte=react,
                                          expiration_date_lte=lim, strike_price_gte=f"{lo:.2f}",
                                          strike_price_lte=f"{hi:.2f}")
        except Exception:
            pass
    if not cs:
        return []
    exp = min(c["expiration_date"] for c in cs)
    by_strike = {float(c["strike_price"]): c["symbol"] for c in cs if c["expiration_date"] == exp}
    picks = {}
    for m in OTM:
        ok = [k for k in sorted(by_strike) if (k >= spot * (1 + m) if call else k <= spot * (1 - m))]
        if ok:
            picks[m] = ok[0] if call else ok[-1]
    if not picks:
        return []
    try:
        bars = client.option_bars(sorted({by_strike[k] for k in picks.values()}), entry_day, exp)
    except Exception:
        return []
    time.sleep(0.1)
    exp_close = closes.get(exp) or closes[max(d for d in closes if d <= exp)] if any(d <= exp for d in closes) else None
    out = []
    for m, k in picks.items():
        ohlc = {b["t"][:10]: b for b in bars.get(by_strike[k], [])}
        if entry_day not in ohlc:
            continue  # no trade at entry: no price to assume
        cost = entry_cost(float(ohlc[entry_day]["c"]))
        b = ohlc.get(react)
        close_val = exit_value(float(b["c"])) if b else payoff(react_close, k)
        r = {"otm": m, "strike": k, "expiry": exp, "cost": round(cost, 4),
             "ret_close": round(close_val / cost - 1, 4),
             "ret_expiry": round(payoff(exp_close, k) / cost - 1, 4) if exp_close else None}
        if b:
            o, h = float(b["o"]), float(b["h"])
            r["ret_open"] = round(exit_value(o) / cost - 1, 4)
            r["ret_high"] = round(exit_value(h) / cost - 1, 4)
            for t in TAKE:
                r[f"ret_tp{t}"] = round((t - 1) if h >= t * cost else close_val / cost - 1, 4)
        else:
            for key in ["ret_open", "ret_high"] + [f"ret_tp{t}" for t in TAKE]:
                r[key] = r["ret_close"]
        out.append(r)
    return out


EXITS = [("ret_open", "反应日开盘卖"), ("ret_tp3", "3 倍止盈（否则收盘卖）"), ("ret_tp5", "5 倍止盈（否则收盘卖）"),
         ("ret_tp10", "10 倍止盈（否则收盘卖）"), ("ret_close", "反应日收盘卖"), ("ret_expiry", "持有到期"),
         ("ret_high", "卖在当天最高价（事后才知道，理论上限）")]


def stats(xs: list[float]) -> dict:
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "mean": round(statistics.mean(xs), 3), "median": round(statistics.median(xs), 3),
            "win": round(sum(x > 0 for x in xs) / len(xs), 3), "x3": round(sum(x >= 2 for x in xs) / len(xs), 3),
            "x10": round(sum(x >= 9 for x in xs) / len(xs), 3)}


def half_bank(trades: list[dict], key: str, target: float = 10_000.0) -> dict:
    """$500, half of it on each event in date order (the user's way); a round ends below $50
    (zero) or at $10,000 (hero), and a fresh $500 round starts."""
    bank, zeros, heroes, best = 500.0, 0, 0, 500.0
    for t in sorted(trades, key=lambda t: t["reaction_day"]):
        bank += bank * 0.5 * max(t[key], -1.0)
        best = max(best, min(bank, target))
        if bank < 50:
            zeros, bank = zeros + 1, 500.0
        elif bank >= target:
            heroes, bank = heroes + 1, 500.0
    return {"final": round(bank), "best": round(best), "zeros": zeros, "heroes": heroes}


def run(client, root: Path) -> dict:
    from hero import universe
    pool = universe.load(root / "data" / "universe.json")["stocks"][:POOL]
    today = date.today()
    end = (today - timedelta(days=1)).isoformat()
    raw: dict[str, dict[str, float]] = {}
    opens: dict[str, dict[str, float]] = {}
    for i in range(0, len(pool), 50):
        for s, bs in client.daily_bars(pool[i:i + 50], START, adjustment="raw").items():
            raw[s] = {b["t"][:10]: float(b["c"]) for b in bs}
            opens[s] = {b["t"][:10]: float(b["o"]) for b in bs}
    events, controls, moves_ev, moves_all, scanned = [], [], [], [], 0
    for sym in pool:
        closes = raw.get(sym)
        if not closes:
            continue
        days = sorted(closes)
        try:
            items = list(client.news_range([sym], f"{START}T00:00:00Z", f"{end}T23:59:59Z"))
        except Exception:
            continue
        scanned += len(items)
        for e in find_events(items, sym, days):
            i = days.index(e["reaction_day"])
            if i == 0:
                continue
            entry, r = days[i - 1], e["reaction_day"]
            e["move"] = round(closes[r] / closes[entry] - 1, 4)
            e["gap"] = round(opens[sym][r] / closes[entry] - 1, 4)
            moves_ev.append(abs(e["move"]))
            e["trades"] = trade(client, sym, entry, r, closes[entry], closes[r], closes)
            events.append(e)
        moves_all += [abs(closes[days[j]] / closes[days[j - 1]] - 1) for j in range(1, len(days))]
        for j in range(42, len(days) - 1, 42):  # control: ordinary days, same stock, same construction
            entry, r = days[j - 1], days[j]
            for t in trade(client, sym, entry, r, closes[entry], closes[r], closes):
                controls.append({"symbol": sym, "reaction_day": r, **t})
    flat = [{"symbol": e["symbol"], "reaction_day": e["reaction_day"], "kind": e["kind"],
             "pre_announced": e["pre_announced"], **t} for e in events for t in e["trades"]]
    groups = {"全部事件": flat,
              "投资者 / 分析师日": [t for t in flat if t["kind"] == "investor"],
              "产品发布 / 主题演讲": [t for t in flat if t["kind"] == "product"],
              "事先公布日期的事件": [t for t in flat if t["pre_announced"]],
              "对照：普通交易日": controls}
    table = {}
    for side in SIDES:
        for g, ts in groups.items():
            for m in OTM:
                sub = [t for t in ts if t["otm"] == m and t["side"] == side]
                table[f"{g}|{side}|{m}"] = {k: stats([t.get(k) for t in sub]) for k, _ in EXITS}
                table[f"{g}|{side}|{m}"]["bank_tp5"] = half_bank(sub, "ret_tp5") if sub else None
    return {"generated": today.isoformat(), "start": START, "pool": len(pool), "headlines": scanned,
            "events": len(events), "with_trades": sum(bool(e["trades"]) for e in events),
            "abs_move": {"events": stats(moves_ev), "all_days": stats(moves_all)},
            "table": table, "event_list": [{k: v for k, v in e.items() if k != "trades"} for e in events],
            "trades": flat}


def markdown(rep: dict) -> str:
    out = [f"# 公司事件日（投资者日、产品发布会）买短期看涨 / 看跌期权 {rep['generated']}", "",
           f"起因：Marvell 2026-10-06 投资者日盘中涨约 10%。股票：本月动态池前 {rep['pool']} 只（今天的名单，偏向今天的赢家）。"
           f"新闻：Alpaca（Benzinga）{rep['start']} 以来 {rep['headlines']:,} 条标题，找到 {rep['events']} 个事件，"
           f"其中 {rep['with_trades']} 个有期权数据。事件前一天收盘买最近到期（10 天内）、价外 5% 或 10% 的看涨期权（行权价高于现价）或看跌期权（低于现价）；"
           "买入多付 10%、卖出少拿 10%。对照组：同一批股票每 42 个交易日一次、同样的买法。只研究，不改交易。", "",
           "## 事件日股价是不是动得更多", "",
           f"- 事件反应日涨跌幅（绝对值）中位数 {rep['abs_move']['events'].get('median', 0):.1%}，"
           f"平均 {rep['abs_move']['events'].get('mean', 0):.1%}（{rep['abs_move']['events'].get('n', 0)} 次）",
           f"- 所有交易日中位数 {rep['abs_move']['all_days'].get('median', 0):.1%}，平均 {rep['abs_move']['all_days'].get('mean', 0):.1%}", ""]
    for key, label in EXITS:
        out += [f"## {label}", "", "| 方向 | 组别 | 价外 | 次数 | 平均每 $1 | 中位数 | 赚钱比例 | ≥3 倍 | ≥10 倍 |", "|---|---|---|---|---|---|---|---|---|"]
        for gk, row in rep["table"].items():
            g, side, m = gk.split("|")
            g = f"{'看涨' if side == 'call' else '看跌'} | {g}"
            s = row[key]
            if not s.get("n"):
                out.append(f"| {g} | {float(m):.0%} | 0 | — | — | — | — | — |")
                continue
            out.append(f"| {g} | {float(m):.0%} | {s['n']} | {s['mean']:+.0%} | {s['median']:+.0%} | {s['win']:.0%} | {s['x3']:.0%} | {s['x10']:.0%} |")
        out.append("")
    out += ["## $500 每次押一半、5 倍止盈，按时间顺序玩所有事件（低于 $50 归零、到 $10,000 成功，都重来）", "",
            "| 方向 | 组别 | 价外 | 最后余额 | 最高到过 | 成功次数 | 归零次数 |", "|---|---|---|---|---|---|---|"]
    for gk, row in rep["table"].items():
        g, side, m = gk.split("|")
        g = f"{'看涨' if side == 'call' else '看跌'} | {g}"
        b = row.get("bank_tp5")
        if b:
            out.append(f"| {g} | {float(m):.0%} | ${b['final']:,} | ${b['best']:,} | {b['heroes']} | {b['zeros']} |")
    big = sorted([e for e in rep["event_list"] if e.get("move") is not None], key=lambda e: -abs(e["move"]))[:15]
    out += ["", "## 股价反应最大的 15 个事件", "", "| 股票 | 反应日 | 类型 | 事先公布 | 当天涨跌 | 标题 |", "|---|---|---|---|---|---|"]
    for e in big:
        out.append(f"| {e['symbol']} | {e['reaction_day']} | {'投资者日' if e['kind'] == 'investor' else '产品'} | "
                   f"{'是' if e['pre_announced'] else '否'} | {e['move']:+.1%} | {e['headline'][:80].replace('|', '/')} |")
    return "\n".join(out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca(), root)
    (root / "research" / f"{rep['generated']}-events.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False) + "\n")
    (root / "research" / f"{rep['generated']}-events.md").write_text(markdown(rep))
    print(markdown(rep))


if __name__ == "__main__":
    sys.exit(main())
