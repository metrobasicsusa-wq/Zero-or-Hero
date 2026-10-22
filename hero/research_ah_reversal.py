"""Research: the after-hours reversal (owner, 10-22: META was +5% after hours and -6% at the open --
「明明盘后是涨的，到中间跌回去了」「今晚把那个盘后反转的回测做了」).

After-close earnings reports since 2024-02 (research/2026-10-02-earnings-reactions.json: 98 large companies with the
8-K acceptance time of each report). On the SIP minute tape (04:00-20:00 ET) every report gets these prices against
the regular close before it (C0): an hour after the 8-K (a1h), the extended-hours close at 20:00 (ah20), the next
open (o), 09:35 (m935), 10:00 (m10) and the next close (c1). Questions:
  1. how often the first reaction (a1h, ah20) points the other way from the open, by its size;
  2. how much of the first reaction is given back by the open (SPY's gap that morning taken out);
  3. what the fade and the follow make in shares: at 20:00 buy the after-hours losers (ah20 at or under -2% / -3% /
     -5%) or the winners, out at 09:35 / 10:00 / the close; the same from an hour after the 8-K;
  4. for our earnings ledgers (bought at the close before the report): out an hour after / at 20:00 / 09:35 (the
     live exit) / the next close.
Costs: 0.2% each way in extended hours, 0.05% in regular hours (shares; no borrow for the short lines, which are
only there to read). Every group goes through hero.robust (without the best 3, median, years, last 3 months, extra
cost). Record only. Run: python -m hero.research_ah_reversal
"""

from __future__ import annotations

import json
import statistics
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

from hero import robust
from hero.research_ideas import ROOT, save

ET = ZoneInfo("America/New_York")
START = "2024-02-05"
EVENTS = ROOT / "research" / "2026-10-02-earnings-reactions.json"
COST_AH, COST_RTH = 0.002, 0.0005   # one way, shares
THRESHOLDS = (0.02, 0.03, 0.05)
FEED = "sip"


def _t(b: dict) -> datetime:
    return datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ET)


def at(d: date, hh: int, mm: int) -> datetime:
    return datetime.combine(d, dtime(hh, mm), ET)


def points(bars: list[dict], report_day: date, react_day: date, accepted: datetime | None) -> dict | None:
    """The prices that frame one after-close report, as returns against the regular close before it."""
    bs = sorted(bars, key=lambda b: b["t"])
    reg = [b for b in bs if at(report_day, 9, 30) <= _t(b) <= at(report_day, 16, 0)]
    if not reg:
        return None
    c0 = float(reg[-1]["c"])
    ah = [b for b in bs if at(report_day, 16, 0) < _t(b) <= at(report_day, 20, 0)]
    a1h = None
    if accepted is not None and accepted + timedelta(minutes=60) <= at(report_day, 19, 59):
        xs = [b for b in ah if _t(b) >= accepted + timedelta(minutes=60)]
        a1h = float(xs[0]["c"]) if xs else None
    ah20 = float(ah[-1]["c"]) if ah else None
    nxt = [b for b in bs if at(react_day, 9, 30) <= _t(b) <= at(react_day, 16, 0)]
    if not nxt:
        return None
    o = float(nxt[0]["o"])
    m935 = next((float(b["c"]) for b in nxt if _t(b) >= at(react_day, 9, 35)), None)
    m10 = next((float(b["c"]) for b in nxt if _t(b) >= at(react_day, 10, 0)), None)
    c1 = float(nxt[-1]["c"])
    r = lambda p: round(p / c0 - 1, 4) if p else None
    return {"c0": c0, "a1h": r(a1h), "ah20": r(ah20), "o": r(o), "m935": r(m935), "m10": r(m10), "c1": r(c1),
            "accepted": accepted.strftime("%H:%M") if accepted else None, "ah_bars": len(ah)}


def ret(p_in: float | None, p_out: float | None, cost_in: float, cost_out: float) -> float | None:
    """Per $1 long from one return point to another (both against C0), after costs."""
    if p_in is None or p_out is None:
        return None
    return (1 + p_out) * (1 - cost_out) / ((1 + p_in) * (1 + cost_in)) - 1


def fetch(client, sym: str, report_day: date, react_day: date) -> list[dict]:
    start = at(report_day, 15, 55).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = at(react_day, 16, 5).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for attempt in range(3):
        try:
            return client.stock_bars([sym], start, end, timeframe="1Min", feed=FEED).get(sym, [])
        except Exception as e:
            if attempt == 2:
                print(f"{sym} {report_day}: {str(e)[:120]}")
                return []
            time.sleep(2 * (attempt + 1))
    return []


def spy_gaps(client) -> dict[str, float]:
    """reaction day -> SPY's open against the prior close (to take the market out of the overnight give-back)."""
    bars = client.daily_bars(["SPY"], (date.fromisoformat(START) - timedelta(days=10)).isoformat(), adjustment="raw",
                             feed=FEED).get("SPY", [])
    out, prev = {}, None
    for b in bars:
        if prev:
            out[b["t"][:10]] = float(b["o"]) / prev - 1
        prev = float(b["c"])
    return out


def run(client) -> dict:
    ev = json.loads(EVENTS.read_text())["events"]
    ev = [e for e in ev if e.get("timing") == "after close" and e["reaction_day"] >= START]
    gaps = spy_gaps(client)
    rows = []
    for e in ev:
        acc = datetime.fromisoformat(e["accepted"]).astimezone(ET) if e.get("accepted") else None
        react = date.fromisoformat(e["reaction_day"])
        rd = acc.date() if acc and acc.date() < react else react - timedelta(days=1)
        while rd.weekday() >= 5:
            rd -= timedelta(days=1)
        p = points(fetch(client, e["symbol"], rd, react), rd, react, acc)
        if not p:
            continue
        p.update({"symbol": e["symbol"], "report_day": rd.isoformat(), "day": e["reaction_day"], "spy_gap": gaps.get(e["reaction_day"])})
        rows.append(p)
    return {"generated": datetime.now(ET).isoformat(timespec="minutes"), "start": START, "events": len(ev), "rows": rows}


def flips(rows: list[dict]) -> list[str]:
    out = ["## 1. 第一反应和次日开盘的方向", "",
           "对财报前的常规收盘：「1 小时后」= 8-K 提交 1 小时后的第一根分钟线，「20:00」= 盘后最后一根；"
           "「回吐」= 开盘减去 1 小时后的涨跌，已扣掉当天 SPY 的开盘缺口。", "",
           "| 1 小时后的幅度 | 次数 | 1 小时后 → 开盘反向 | 20:00 → 开盘反向 | 盘后涨的：到开盘回吐 | 盘后跌的：到开盘收回 | 开盘比 1 小时后走得更远 |",
           "|---|---|---|---|---|---|---|"]
    buckets = [("< 1%", 0, 0.01), ("1–3%", 0.01, 0.03), ("3–6%", 0.03, 0.06), ("≥ 6%", 0.06, 9)]
    for name, lo, hi in buckets + [("全部", 0, 9)]:
        xs = [r for r in rows if r["a1h"] is not None and r["o"] is not None and lo <= abs(r["a1h"]) < hi]
        if not xs:
            continue
        flip1 = sum((r["a1h"] > 0) != (r["o"] > 0) for r in xs) / len(xs)
        ys = [r for r in xs if r["ah20"] is not None]
        flip20 = sum((r["ah20"] > 0) != (r["o"] > 0) for r in ys) / len(ys) if ys else float("nan")
        adj = lambda r: r["o"] - r["a1h"] - (r["spy_gap"] or 0)
        up = [adj(r) for r in xs if r["a1h"] > 0]
        dn = [adj(r) for r in xs if r["a1h"] < 0]
        far = sum(abs(r["o"]) > abs(r["a1h"]) and (r["o"] > 0) == (r["a1h"] > 0) for r in xs) / len(xs)
        out.append(f"| {name} | {len(xs)} | {flip1:.0%} | {flip20:.0%} | "
                   f"{(statistics.mean(up) * 100 if up else float('nan')):+.1f} 点（{len(up)}） | "
                   f"{(statistics.mean(dn) * 100 if dn else float('nan')):+.1f} 点（{len(dn)}） | {far:.0%} |")
    return out


def strategies(rows: list[dict]) -> dict[str, list[tuple[str, float]]]:
    G: dict[str, list[tuple[str, float]]] = {}
    exits = {"m935": "09:35 卖", "m10": "10:00 卖", "c1": "次日收盘卖"}
    for thr in THRESHOLDS:
        for side, name in (("loser", "盘后跌"), ("winner", "盘后涨")):
            sel = [r for r in rows if r["ah20"] is not None and (r["ah20"] <= -thr if side == "loser" else r["ah20"] >= thr)]
            for k, label in exits.items():
                xs = [(r["day"], ret(r["ah20"], r[k], COST_AH, COST_RTH)) for r in sel if r[k] is not None]
                G[f"20:00 买{name} {thr:.0%} 以上的，{label}"] = xs
            sel1 = [r for r in rows if r["a1h"] is not None and (r["a1h"] <= -thr if side == "loser" else r["a1h"] >= thr)]
            xs = [(r["day"], ret(r["a1h"], r["m935"], COST_AH, COST_RTH)) for r in sel1 if r["m935"] is not None]
            G[f"8-K 后 1 小时买{name} {thr:.0%} 以上的，09:35 卖"] = xs
    for thr in THRESHOLDS:
        sel = [r for r in rows if r["ah20"] is not None and r["ah20"] >= thr]
        G[f"参考：20:00 卖空盘后涨 {thr:.0%} 以上的，09:35 回补（不算借券）"] = \
            [(r["day"], -ret(r["ah20"], r["m935"], COST_AH, COST_RTH)) for r in sel if r["m935"] is not None]
    return G


def ledger_exits(rows: list[dict]) -> dict[str, list[tuple[str, float]]]:
    G = {}
    for k, label, cost in (("a1h", "8-K 后 1 小时卖", COST_AH), ("ah20", "20:00 卖", COST_AH),
                           ("m935", "次日 09:35 卖（实盘）", COST_RTH), ("c1", "次日收盘卖", COST_RTH)):
        G[f"财报前收盘买，{label}"] = [(r["day"], ret(0.0, r[k], COST_RTH, cost)) for r in rows if r[k] is not None]
    G["财报前收盘买，1 小时后是涨的就 20:00 卖、跌的拿到 09:35"] = [
        (r["day"], ret(0.0, r["ah20"], COST_RTH, COST_AH) if r["a1h"] > 0 else ret(0.0, r["m935"], COST_RTH, COST_RTH))
        for r in rows if r["a1h"] is not None and r["ah20"] is not None and r["m935"] is not None]
    return G


def markdown(rep: dict) -> str:
    rows = rep["rows"]
    n_ah = sum(1 for r in rows if r["ah20"] is not None)
    out = [f"# 盘后反转：财报盘后的第一反应和第二天开盘 {rep['generated'][:10]}", "",
           f"{rep['start']} 以来 98 家大公司的盘后财报（research/2026-10-02-earnings-reactions.json），{rep['events']} 次里拿到分钟线的 "
           f"{len(rows)} 次（有盘后成交的 {n_ah} 次）。用户 10-22：META 盘后 +5%、开盘 -6%——盘后是人少时候的一次投票，电话会和隔夜（卖方报告、板块）"
           "之后才是真正的定价。正股：盘后买卖各算 0.2% 成本，常规时段 0.05%。只研究，不改交易。", ""]
    out += flips(rows)
    out += ["", "## 2. 做法：20:00 买盘后跌的（反转）还是买涨的（延续）", "",
            robust.table({k: robust.summary(v) for k, v in strategies(rows).items()}, "每 $1 的收益（正股）")]
    out += ["", "## 3. 我们的财报正股账本：什么时候卖", "",
            robust.table({k: robust.summary(v) for k, v in ledger_exits(rows).items()}, "财报前收盘买（不看判断，全部盘后财报）")]
    big = sorted([r for r in rows if r["a1h"] is not None], key=lambda r: -abs(r["o"] - r["a1h"]))[:15]
    out += ["", "## 4. 第一反应和开盘差得最远的 15 次", "", "| 反应日 | 股票 | 8-K | 1 小时后 | 20:00 | 开盘 | 次日收盘 |", "|---|---|---|---|---|---|---|"]
    for r in big:
        f = lambda x: f"{x:+.1%}" if x is not None else "—"
        out.append(f"| {r['day']} | {r['symbol']} | {r['accepted'] or '—'} | {f(r['a1h'])} | {f(r['ah20'])} | {f(r['o'])} | {f(r['c1'])} |")
    return "\n".join(out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    rep = run(Alpaca())
    save("ah-reversal", rep, markdown(rep))


if __name__ == "__main__":
    main()
