"""The scout's second half (hero.scout has the first): real option quotes for three structures side by side, and a plan
ledger that scores the scout's picks against the same structures bought on SPY the same day.

Owner, 11-20: a plan ledger, not real orders; several structures side by side.  For every name on a big drop day's list:

  spread3m   a bull call spread on the first monthly expiry 80 days or more out: buy the strike nearest the price, sell
             the one nearest 10% higher (5% for an index fund)
  spread1y   the same on the first monthly expiry 330 days or more out, selling 20% higher (10% for a fund)
  csp3m      a cash-secured put on the three-month expiry, 10% under the price (5% for a fund)

A bought leg is priced at the ask and a sold one at the bid; a quote not made today is not used and the structure is
left out with the reason.  Nothing is sent to the broker.  Each entry is settled on the underlying's close at expiry
(strikes follow a split in between), as profit per $1 that could have been lost, and SPY's entries of the same day are
the yardstick: did picking beat just buying the index the same way.
"""

from __future__ import annotations

import json
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from hero.scout import FUNDS, OUT, group_name

ET = ZoneInfo("America/New_York")
BASELINE = "SPY"
KINDS = {"spread3m": "三个月看涨价差", "spread1y": "一年看涨价差", "csp3m": "卖看跌、留足现金（三个月）"}
# kind: days out, how far the search for an expiry goes, the second strike as a share of the price for a stock / for a fund
PLAN = {"spread3m": (80, 50, 1.10, 1.05), "spread1y": (330, 120, 1.20, 1.10), "csp3m": (80, 50, 0.90, 0.95)}
WIDE = 0.20      # trading now costs this much more than the middle of the quotes: the page says so (the entry is still recorded)


def monthly(d: date) -> bool:
    return d.weekday() == 4 and 15 <= d.day <= 21


def pick_expiry(contracts: list[dict]) -> str | None:
    """The first monthly expiry among the listed contracts; the first expiry of any kind when no monthly is listed."""
    days = sorted({c["expiration_date"] for c in contracts})
    return next((d for d in days if monthly(date.fromisoformat(d))), days[0] if days else None)


def nearest(contracts: list[dict], kind: str, expiry: str, target: float) -> dict | None:
    side = [c for c in contracts if c["expiration_date"] == expiry and c["type"] == kind]
    return min(side, key=lambda c: abs(float(c["strike_price"]) - target)) if side else None


def legs(kind: str, spot: float, fund: bool, contracts: list[dict]) -> list[dict] | str:
    """The legs of one structure from the listed contracts, or the reason there are none."""
    expiry = pick_expiry(contracts)
    if not expiry:
        return "这段时间没有挂牌的到期日"
    far = spot * PLAN[kind][3 if fund else 2]
    if kind == "csp3m":
        p = nearest(contracts, "put", expiry, far)
        return [leg(p, "sell")] if p else "没有合适的看跌行权价"
    lo, hi = nearest(contracts, "call", expiry, spot), nearest(contracts, "call", expiry, far)
    if not lo or not hi or float(hi["strike_price"]) <= float(lo["strike_price"]):
        return "没有两个不同的看涨行权价"
    return [leg(lo, "buy"), leg(hi, "sell")]


def leg(c: dict, side: str) -> dict:
    return {"occ": c["symbol"], "side": side, "type": "C" if c["type"] == "call" else "P",
            "strike": float(c["strike_price"]), "expiry": c["expiration_date"]}


def priced(ls: list[dict], snaps: dict, today: str) -> list[dict] | str:
    """Each leg with the price it would trade at now (ask to buy, bid to sell), or why a leg cannot be priced."""
    out = []
    for x in ls:
        q = (snaps.get(x["occ"]) or {}).get("latestQuote") or {}
        bid, ask = float(q.get("bp") or 0), float(q.get("ap") or 0)
        px = ask if x["side"] == "buy" else bid
        if not px:
            return f"{x['occ'][-9:]} 没有{'卖' if x['side'] == 'buy' else '买'}价"
        if str(q.get("t", ""))[:10] != today:
            return f"报价不是今天的（{str(q.get('t', ''))[:10] or '没有时刻'}）"
        out.append({**x, "price": px, "bid": bid, "ask": ask})
    return out


def middle(ls: list[dict]) -> float | None:
    """What the structure costs (a spread) or pays (a put sold) at the middle of each leg's quote; None when a leg shows one side only."""
    if any(not x.get("bid") or not x.get("ask") for x in ls):
        return None
    ms = [(x["bid"] + x["ask"]) / 2 for x in ls]
    return round(ms[0] - ms[1] if len(ms) == 2 else ms[0], 4)


def wide(e: dict) -> bool:
    """Trading now is at least WIDE worse than the middle: a spread costs that much more, a put sold pays that much less."""
    now, mid = e.get("debit", e.get("credit")), e.get("mid")
    if not mid or mid <= 0:
        return False
    return now >= mid * (1 + WIDE) if "debit" in e else now <= mid * (1 - WIDE)


def entry(day: str, sym: str, kind: str, spot: float, ls: list[dict]) -> dict | str:
    """The numbers of one structure per share: what is paid or received, the most that can be lost and made, breakeven."""
    base = {"id": f"{day}|{sym}|{kind}", "day": day, "symbol": sym, "kind": kind, "baseline": sym == BASELINE, "spot": spot,
            "expiry": ls[0]["expiry"], "legs": ls, "status": "open", "mid": middle(ls)}
    if kind == "csp3m":
        k, got = ls[0]["strike"], ls[0]["price"]
        return {**base, "credit": got, "risk": round(k - got, 4), "gain": got, "breakeven": round(k - got, 4), "cash": round((k - got) * 100, 2)}
    paid, width = round(ls[0]["price"] - ls[1]["price"], 4), ls[1]["strike"] - ls[0]["strike"]
    if not 0 < paid < width:
        return f"两条腿的报价对不上（买 {ls[0]['price']:.2f}、卖 {ls[1]['price']:.2f}，宽 {width:g}）"
    return {**base, "debit": paid, "risk": paid, "gain": round(width - paid, 4), "breakeven": round(ls[0]["strike"] + paid, 4), "cash": round(paid * 100, 2)}


def settle(e: dict, close: float, factor: float = 1.0) -> dict:
    """An entry closed at expiry on the underlying's close; `factor` divides the strikes when shares split after entry."""
    ks = [x["strike"] / factor for x in e["legs"]]
    if e["kind"] == "csp3m":
        got, risk = e["credit"] / factor, e["risk"] / factor
        result = (got - max(ks[0] - close, 0.0)) / risk
    else:
        paid = e["debit"] / factor
        result = (max(0.0, min(close - ks[0], ks[1] - ks[0])) - paid) / paid
    return {**e, "status": "closed", "close": close, "result": round(result, 4)}


def mark(e: dict, snaps: dict, today: str) -> dict | None:
    """What an open entry is worth per share today: closed now (the bought leg sold at the bid, the sold leg bought back
    at the ask) and at the middle of the quotes. None when a leg has no quote made today: the last mark is then kept."""
    now, mid = 0.0, 0.0
    for x in e["legs"]:
        q = (snaps.get(x["occ"]) or {}).get("latestQuote") or {}
        bid, ask = float(q.get("bp") or 0), float(q.get("ap") or 0)
        if not ask or str(q.get("t", ""))[:10] != today:
            return None
        sign = 1 if x["side"] == "buy" else -1
        now += sign * (bid if x["side"] == "buy" else ask)
        mid += sign * (bid + ask) / 2
    floor = 0.0 if e["kind"] != "csp3m" else None           # a spread paid for is never worth less than nothing
    return {"day": today, "value": round(max(now, floor) if floor is not None else now, 4),
            "mid": round(max(mid, floor) if floor is not None else mid, 4)}


def marked(e: dict, at: str = "value") -> float | None:
    """An open entry's result per $1 at risk if it were closed at its last mark (`at`: "value" to trade now, "mid")."""
    m = e.get("mark")
    if not m:
        return None
    if e["kind"] == "csp3m":
        return (e["credit"] + m[at]) / e["risk"]             # the put sold is a negative value: what buying it back costs
    return (m[at] - e["debit"]) / e["debit"]


def marks(ledger: list[dict]) -> dict:
    """Open entries with a mark, by structure: the picks' average result now beside SPY's from the same entry days."""
    out = {}
    for kind in KINDS:
        live = [e for e in ledger if e["kind"] == kind and e["status"] == "open" and e.get("mark")]
        picks = [e for e in live if not e["baseline"]]
        days = {e["day"] for e in picks}
        base = [e for e in live if e["baseline"] and e["day"] in days]
        mean = lambda es, at: statistics.mean(marked(e, at) for e in es) if es else None   # noqa: E731
        out[kind] = {"n": len(picks), "as_of": max((e["mark"]["day"] for e in picks), default=None),
                     "picks": mean(picks, "value"), "picks_mid": mean(picks, "mid"), "spy": mean(base, "value"), "spy_mid": mean(base, "mid")}
    return out


def scoreboard(ledger: list[dict]) -> dict:
    """Closed entries by structure: the picks' average per $1 at risk beside SPY's on the same entry days."""
    out = {}
    for kind in KINDS:
        done = [e for e in ledger if e["kind"] == kind and e["status"] == "closed"]
        picks = [e for e in done if not e["baseline"]]
        days = {e["day"] for e in picks}
        base = [e for e in done if e["baseline"] and e["day"] in days]
        out[kind] = {"open": sum(e["kind"] == kind and e["status"] == "open" and not e["baseline"] for e in ledger), "closed": len(picks),
                     "picks": statistics.mean(e["result"] for e in picks) if picks else None,
                     "won": sum(e["result"] > 0 for e in picks) / len(picks) if picks else None,
                     "spy": statistics.mean(e["result"] for e in base) if base else None}
    return out


def money(x: float) -> str:
    return f"${x:,.0f}" if abs(x) >= 100 else f"${x:,.2f}"


def section(day: str, added: list[dict], skipped: list[tuple[str, str, str]], ledger: list[dict]) -> str:
    out = ["", "## 四、三种做法的真实报价（一张 = 100 股）", ""]
    if added:
        out += ["买的那条腿按卖价、卖的那条腿按买价算，是现在就成交大概要付的价。最后一行的标普是对照。", "",
                "| 标的 | 做法 | 到期 | 行权价 | 一张要放的钱 | 最多亏 | 最多赚 | 到期时股价在哪保本 |", "|---|---|---|---|---|---|---|---|"]
        for e in added:
            ks = " / ".join(f"{x['strike']:g}" for x in e["legs"])
            be = e["breakeven"] / e["spot"] - 1
            out.append(f"| {e['symbol']} | {KINDS[e['kind']]} | {e['expiry']} | {ks} | {money(e['cash'])} | {money(e['risk'] * 100)} | {money(e['gain'] * 100)} | "
                       f"${e['breakeven']:,.2f}（{be:+.1%}） |")
        out += ["", "卖看跌那一种「要放的钱」是留着接股票的现金，「最多亏」是股票跌到零的情况。"]
        loose = [e for e in added if wide(e)]
        if loose:
            out += ["", f"报价很宽的（现在就成交比两边报价的中间差两成以上，真要做会挂限价单；账本照现在就成交的价记，中间价也存着）："
                    + "；".join(f"{e['symbol']} 的{KINDS[e['kind']]}（现在 {money(e.get('debit', e.get('credit')) * 100)}，中间价 {money(e['mid'] * 100)}）" for e in loose) + "。"]
    else:
        out.append("今天没有新记的。")
    if skipped:
        out += ["", "没记上的：" + "；".join(f"{s} 的{KINDS[k]}（{why}）" for s, k, why in skipped) + "。"]
    board = scoreboard(ledger)
    out += ["", "## 五、演算账本到现在", "", "不下真单，只按上面的价记「如果照做」。到期按正股收盘价结算，每 $1 最多能亏的钱赚了多少；对照是同一天同样做法买标普。", "",
            "| 做法 | 还没到期 | 已到期 | 清单平均 | 赚的比例 | 同一天的标普 |", "|---|---|---|---|---|---|"]
    for kind, b in board.items():
        out.append(f"| {KINDS[kind]} | {b['open']} | {b['closed']} | {'—' if b['picks'] is None else format(b['picks'], '+.0%')} | "
                   f"{'—' if b['won'] is None else format(b['won'], '.0%')} | {'—' if b['spy'] is None else format(b['spy'], '+.0%')} |")
    live = marks(ledger)
    if any(v["n"] for v in live.values()):
        show = lambda x: "—" if x is None else format(x, "+.0%")   # noqa: E731
        out += ["", "没到期的仓现在值多少（每天按报价估一次；每 $1 最多能亏的钱，现在是赚是亏）：", "",
                "| 做法 | 估了现值的 | 清单：现在平掉 | 清单：按中间价 | 同一天的标普：现在平掉 | 标普：按中间价 |", "|---|---|---|---|---|---|"]
        out += [f"| {KINDS[kind]} | {v['n']} | {show(v['picks'])} | {show(v['picks_mid'])} | {show(v['spy'])} | {show(v['spy_mid'])} |"
                for kind, v in live.items() if v["n"]]
        out += ["", "「现在平掉」= 买的那条腿按买价卖、卖的那条腿按卖价买回，刚记进去的仓会先亏掉一个买卖价差；「按中间价」不算这个差。"
                "大跌日连着来的时候，同一只股票每天会各记一笔：这是每个进场日一个样本，笔数不等于仓位。"]
    return "\n".join(out) + "\n"


def run(client, day: str | None = None, write: bool = True) -> dict:
    day = day or datetime.now(ET).date().isoformat()
    path = OUT / f"{day}.json"
    if not path.exists():
        raise SystemExit(f"no scout page for {day}: run hero.scout first")
    rep = json.loads(path.read_text())
    lpath = OUT / "ledger.json"
    ledger = json.loads(lpath.read_text()) if lpath.exists() else []
    have = {e["id"] for e in ledger}
    names = [c["symbol"] for c in rep["kept"]] + ([BASELINE] if rep["kept"] else [])
    added, skipped = [], []
    today = date.fromisoformat(day)
    spots = {s: float((v.get("latestTrade") or {}).get("p") or 0) for s, v in (client.stock_snapshots(names) if names else {}).items()}
    for sym in names:
        spot = spots.get(sym)
        if not spot:
            skipped += [(sym, k, "没有现价") for k in KINDS]
            continue
        chains = {}
        for kind, (out_days, span, *_) in PLAN.items():
            if f"{day}|{sym}|{kind}" in have:
                continue
            key = (out_days, span)
            if key not in chains:
                try:
                    chains[key] = client.option_contracts(sym, status="active", expiration_date_gte=(today + timedelta(days=out_days)).isoformat(),
                                                          expiration_date_lte=(today + timedelta(days=out_days + span)).isoformat(),
                                                          strike_price_gte=f"{spot * 0.8:.2f}", strike_price_lte=f"{spot * 1.3:.2f}")
                except Exception as e:
                    chains[key] = []
                    skipped.append((sym, kind, f"取不到合约：{str(e)[:60]}"))
                    continue
            ls = legs(kind, spot, sym in FUNDS, chains[key])
            if isinstance(ls, str):
                skipped.append((sym, kind, ls))
                continue
            try:
                got = priced(ls, client.option_snapshots([x["occ"] for x in ls]), day)
            except Exception as e:
                got = f"取不到报价：{str(e)[:60]}"
            e = entry(day, sym, kind, spot, got) if not isinstance(got, str) else got
            if isinstance(e, str):
                skipped.append((sym, kind, e))
                continue
            e["group"] = group_name(next((c.get("group") for c in rep["kept"] if c["symbol"] == sym), None)) if sym != BASELINE else "对照"
            ledger.append(e)
            added.append(e)
    due = sorted({e["symbol"] for e in ledger if e["status"] == "open" and e["expiry"] < day})
    if due:
        first = min(e["day"] for e in ledger if e["status"] == "open" and e["expiry"] < day)
        raw, adj = client.daily_bars(due, first, adjustment="raw"), client.daily_bars(due, first, adjustment="split")
        for i, e in enumerate(ledger):
            if e["status"] != "open" or e["expiry"] >= day:
                continue
            r = {b["t"][:10]: float(b["c"]) for b in raw.get(e["symbol"], [])}
            a = {b["t"][:10]: float(b["c"]) for b in adj.get(e["symbol"], [])}
            last = max((d for d in a if d <= e["expiry"]), default=None)
            if last and e["day"] in r and e["day"] in a and a[e["day"]]:
                ledger[i] = settle(e, a[last], r[e["day"]] / a[e["day"]])
    live = [e for e in ledger if e["status"] == "open"]
    if live:
        try:
            snaps = client.option_snapshots(sorted({x["occ"] for e in live for x in e["legs"]}))
        except Exception:
            snaps = {}
        for e in live:
            e["mark"] = mark(e, snaps, day) or e.get("mark")
    text = section(day, [e for e in ledger if e["day"] == day], skipped, ledger)      # a second run the same day keeps the table
    if write:
        lpath.write_text(json.dumps(ledger, ensure_ascii=False, indent=1) + "\n")
        page = OUT / f"{day}.md"
        page.write_text(page.read_text().split("\n## 四、")[0].rstrip("\n") + "\n" + text)
    return {"added": len(added), "skipped": len(skipped), "open": sum(e["status"] == "open" for e in ledger), "section": text}


def main() -> None:
    now = datetime.now(ET)
    if "--scheduled" in sys.argv and not (now.weekday() < 5 and "15:30" <= now.strftime("%H:%M") <= "16:45"):
        print(f"not the scout's hour in New York ({now:%a %H:%M}); nothing done")
        return
    from hero import scout
    from hero.alpaca import Alpaca
    rep = scout.run()
    got = run(Alpaca(), rep["day"], write="--dry" not in sys.argv)
    print((OUT / f"{rep['day']}.md").read_text().split("\n## 四、")[0] + got.pop("section"))
    print(json.dumps(got, ensure_ascii=False))


if __name__ == "__main__":
    main()
