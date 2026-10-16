"""A dip-buy given a week instead of a day (owner, 10-16: does it rise or fall after a failed bounce? -- backtest it).

10-16 the same-day dip calls (gap / flush sleeves) went to nothing. Stock history 2021-26, 30 active names: after a
gap down of 2%+ that closed below its open, the stock was +2.2% five sessions later on average (any day +0.8%) --
the bounce tends to come, just not the same day. Does an option with a week to run catch it?
Events since 2024-02 (Alpaca's option history), the 25 active names of research/*-open-catalyst.json plus TSM,
NBIS, CRWV: the open 2%+ below the prior close. At that day's close, a call on the nearest expiry 5-12 days out, at
the money (strike nearest the price) and 3% out. Split by the day: the bounce failed (closed below the open) or
held. Exits on the option's daily closes (a session without a trade keeps the last close; at expiry, the stock's
close gives the value): the next close, the 3rd, the 5th (or expiry if sooner), a 2x take, a trail (2x then a 40%
giveback). Costs 5% each way, and 10% for comparison. Record only. Run: python -m hero.research_dip_week
"""

from __future__ import annotations

import statistics
import sys
import time
from datetime import date, timedelta

from hero.research_event_turn import spread
from hero.research_ideas import report, save
from hero.research_sniper_factors import latest

START = "2024-02-01"
EXTRA = ("TSM", "NBIS", "CRWV")
OFFS = {"atm": 0.0, "otm3": 0.03}
HOLD = 5
EXITS = {"d1": "第二天收盘卖", "d3": "第 3 天收盘卖", "d5": "第 5 天收盘卖（先到期就到期）",
         "take2": "2 倍止盈（否则第 5 天）", "trail2": "翻倍后回撤 40% 卖（否则第 5 天）"}


def events(days: list[str], opens: list[float], closes: list[float]) -> list[dict]:
    """Every open 2%+ below the prior close, with the stock's own moves after it (split-adjusted prices)."""
    out = []
    for i in range(21, len(days)):
        g = opens[i] / closes[i - 1] - 1
        if g > -0.02:
            continue
        out.append({"day": days[i], "i": i, "gap": round(g, 4), "failed": closes[i] < opens[i],
                    "below": closes[i - 1] < statistics.mean(closes[i - 20:i]),
                    "stock": {f"s{k}": round(closes[i + k] / closes[i] - 1, 4) for k in (1, 3, 5, 10) if i + k < len(closes)}})
    return out


def option_path(entry: float, marks: dict[str, float], sessions: list[str], expiry: str, strike: float,
                stock: dict[str, float]) -> tuple[list[float], bool]:
    """The option's close on each session after the entry, up to HOLD sessions or its expiry: a session without a
    trade keeps the last close, the expiry session without one is worth what the stock's close leaves it. Done when
    HOLD sessions passed or the expiry did."""
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
    res = {f"d{k}": out_px(path[k - 1]) for k in (1, 3) if len(path) >= k}
    if not done or not path:
        return {k: round(v, 4) for k, v in res.items()}
    res["d1"] = res.get("d1", out_px(path[-1]))
    res["d3"] = res.get("d3", out_px(path[-1]))
    res["d5"] = out_px(path[-1])
    res["take2"] = next((out_px(c) for c in path if c >= 2 * cost), res["d5"])
    best, armed, res["trail2"] = 0.0, False, res["d5"]
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
                                          expiration_date_gte=(d0 + timedelta(days=5)).isoformat(),
                                          expiration_date_lte=(d0 + timedelta(days=12)).isoformat(),
                                          strike_price_gte=f"{spot * 0.95:.2f}", strike_price_lte=f"{spot * 1.08:.2f}")
        except Exception:
            pass
    if not cs:
        return {}
    exp = min(c["expiration_date"] for c in cs)
    ks = sorted((float(c["strike_price"]), c["symbol"]) for c in cs if c["expiration_date"] == exp)
    want = {"atm": min(ks, key=lambda k: abs(k[0] - spot)), "otm3": next((k for k in ks if k[0] >= spot * 1.03), None)}
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
        cost = marks[day] * 1.05
        out[name] = {"option": k[1], "expiry": exp, "cost": round(cost, 4), "res": exits(cost, path, done, 0.05),
                     "res10": exits(marks[day] * 1.10, path, done, 0.10)}
    return out


def run(client) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    names = sorted({r["und"] for r in latest("open-catalyst")["rows"]} | set(EXTRA))
    split = client.daily_bars(names, "2023-12-01", adjustment="split", feed="sip")
    raw = client.daily_bars(names, "2023-12-01", adjustment="raw", feed="sip")
    rows, missing = [], 0
    for und in names:
        bs = [b for b in split.get(und, []) if b["t"][:10] <= end]
        if len(bs) < 30:
            continue
        days = [b["t"][:10] for b in bs]
        stock = {b["t"][:10]: float(b["c"]) for b in raw.get(und, [])}
        for e in events(days, [float(b["o"]) for b in bs], [float(b["c"]) for b in bs]):
            if e["day"] < START:
                continue
            got = legs(client, und, e["day"], stock.get(e["day"], 0.0) or float(bs[e["i"]]["c"]), end, days[e["i"] + 1:], stock)
            if not got:
                missing += 1
            for name, leg in got.items():
                rows.append({**e, "und": und, "leg": name, **leg})
            if not got:
                rows.append({**e, "und": und, "leg": None, "res": {}})
            time.sleep(0.1)
    return {"start": START, "end": end, "names": names, "rows": rows, "missing": missing}


def groups(rows: list[dict]) -> dict[str, list[dict]]:
    atm = [r for r in rows if r["leg"] == "atm"]
    otm = [r for r in rows if r["leg"] == "otm3"]
    return {"抄底失败（低开 2%+、收盘低于开盘）· 平值": [r for r in atm if r["failed"]],
            "抄底失败 · 价外 3%": [r for r in otm if r["failed"]],
            "　失败且低开 5%+ · 平值": [r for r in atm if r["failed"] and r["gap"] <= -0.05],
            "　失败且前一天已在 20 日均线下 · 平值": [r for r in atm if r["failed"] and r["below"]],
            "　失败且前一天还在 20 日均线上 · 平值": [r for r in atm if r["failed"] and not r["below"]],
            "对照：低开后当天收回（收盘不低于开盘）· 平值": [r for r in atm if not r["failed"]],
            "对照：全部低开 2%+ · 平值": atm,
            "对照：全部低开 2%+ · 价外 3%": otm}


def main() -> None:
    from hero.alpaca import Alpaca
    rep = run(Alpaca())
    gs = groups(rep["rows"])
    table, md = report(f"抄底给一周：低开日收盘买 5–12 天到期的看涨 {date.today().isoformat()}",
                       f"{rep['start']} 至 {rep['end']}，{len(rep['names'])} 只（25 只活跃股加 TSM、NBIS、CRWV）开盘比昨收低 2% 以上的日子，"
                       "当天收盘买最近的 5–12 天到期看涨（平值 / 价外 3%），按期权每日收盘价卖（没成交的日子沿用上一个收盘价，到期日用股价算价值），"
                       f"买卖各算 5% 成本。没有期权数据的事件 {rep['missing']} 个。最近还没走满的只计已过去的天数。",
                       gs, EXITS)
    md += ("\n## 每 $1 的分布\n\n每格「平均 / 去掉最好 3 笔 / 中位数 / 2024（笔数） / 2025–26 / 最近 3 个月」。\n\n"
           "| 情况 | " + " | ".join(EXITS.values()) + " |\n|---|" + "---|" * len(EXITS) + "\n" +
           "\n".join(f"| {g} | " + " | ".join(spread(rs, x, rep["end"]) for x in EXITS) + " |" for g, rs in gs.items()) + "\n")
    md += "\n## 成本改成买卖各 10%\n\n| 情况 | " + " | ".join(EXITS.values()) + " |\n|---|" + "---|" * len(EXITS) + "\n"
    for g, rs in gs.items():
        cells = []
        for x in EXITS:
            v = [r["res10"][x] for r in rs if x in r.get("res10", {})]
            cells.append(f"{statistics.mean(v):+.0%}" if v else "—")
        md += f"| {g} | " + " | ".join(cells) + " |\n"
    md += "\n## 只看股价（同一批事件，从当天收盘算）\n\n| 情况 | 次数 | 第二天 | 3 天后 | 5 天后 | 10 天后 |\n|---|---|---|---|---|---|\n"
    ev = {(r["und"], r["day"]): r for r in rep["rows"]}.values()
    for name, f in (("抄底失败", lambda r: r["failed"]), ("低开后当天收回", lambda r: not r["failed"]), ("全部低开 2%+", lambda r: True)):
        xs = [r for r in ev if f(r)]
        cells = []
        for k in ("s1", "s3", "s5", "s10"):
            v = [r["stock"][k] for r in xs if k in r["stock"]]
            cells.append(f"{statistics.mean(v):+.1%} / 涨 {sum(x > 0 for x in v) / len(v):.0%}" if v else "—")
        md += f"| {name} | {len(xs)} | " + " | ".join(cells) + " |\n"
    save("dip-week", {"generated": date.today().isoformat(), "table": table, "rows": rep["rows"], "missing": rep["missing"]}, md)


if __name__ == "__main__":
    sys.exit(main())
