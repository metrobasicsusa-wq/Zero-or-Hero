"""Event turns: buy a ~1-month option when a CPI or Fed day turns a stock that was already moving (owner, 10-16).

The chip run of 2026-09: SMH fell about 5% into the 09-16 Fed hike, closed up the next day and ran +17% in three
weeks; then, already below its 20-day average, it closed down on the 10-14 CPI and kept falling. Two rules from
that, on every CPI and Fed day since 2024-02 (hero.research_eventday dates, plus the 2026-07..10 CPI days):
  rebound:   the stock fell 3%+ (and 5%+) over the 5 sessions before the event; the first close up on the event
             day or the next -> buy a call at that close;
  breakdown: the stock closed below its 20-day average the session before the event; the first close down on the
             event day or the next -> buy a put at that close.
The option: expiry nearest 30 days out (25-45), strike nearest the price. Exits on its daily closes: the 5th, 10th
or 20th session, a trail (after 2x, out on a 40% giveback, else the 20th), half at 3x plus the trail. Costs 5% each
way (liquid at-the-money options a month out; the same-day studies use 10%).
Against: the same signal on every event without the filter (options too), and the filtered signal on ordinary days
(stock moves only -- options for every ordinary day would be tens of thousands of fetches).
Underlyings: SMH, QQQ and the 25 active names of research/*-open-catalyst.json. Record only.
Run: python -m hero.research_event_turn
"""

from __future__ import annotations

import statistics
import sys
import time
from datetime import date, timedelta

from hero.research_eventday import CPI, FED
from hero.research_ideas import report, save
from hero.research_sniper_factors import latest

START = "2024-02-01"
CPI_LATE = ["2026-07-14", "2026-08-12", "2026-09-11", "2026-10-14"]  # as reported from the BLS schedule; 07-14 unchecked
EVENTS = {**{d: "CPI" for d in CPI + CPI_LATE}, **{d: "Fed" for d in FED}}
ETFS = ("SMH", "QQQ")
SLIP_M = 0.05
HOLD = 20
RECENT = 92  # "the last three months", in calendar days
EXITS = {"d5": "第 5 天收盘卖", "d10": "第 10 天收盘卖", "d20": "第 20 天收盘卖",
         "trail2": "翻倍后回撤 40% 卖（否则第 20 天）", "take3_trail": "一半 3 倍、其余回撤卖"}


class Pace:
    """Research runs on the main account's key, so while the market is open a study shares that key's request quota
    with the trading runs: wait 2s between signals then (about 60 requests a minute), 0.1s otherwise. The clock is
    asked again every five minutes."""

    def __init__(self, client):
        self.c, self.until, self.gap = client, 0.0, 0.1

    def wait(self) -> None:
        now = time.time()
        if now >= self.until:
            try:
                self.gap = 2.0 if self.c.clock().get("is_open") else 0.1
            except Exception:
                self.gap = 0.1
            self.until = now + 300
        time.sleep(self.gap)


def signals(days: list[str], closes: list[float], events: dict[str, str]) -> list[dict]:
    """One call and one put signal per event at most: the first close up (call) / down (put) on the event day or the
    session after, with what was known before the event: the 5-session move and whether it closed below its 20-day
    average."""
    idx = {d: i for i, d in enumerate(days)}
    out = []
    for e, ev in sorted(events.items()):
        i = idx.get(e)
        if i is None or i < 21:
            continue
        pre = closes[i - 1] / closes[i - 6] - 1
        below = closes[i - 1] < statistics.mean(closes[i - 20:i])
        for kind, up in (("C", True), ("P", False)):
            j = next((j for j in (i, i + 1) if j < len(days) and (closes[j] > closes[j - 1]) == up), None)
            if j is not None:
                out.append({"day": days[j], "i": j, "event": e, "ev": ev, "kind": kind, "pre": round(pre, 4), "below": below})
    return out


def ordinary(days: list[str], closes: list[float], events: dict[str, str]) -> list[dict]:
    """The filtered signals on days that are neither an event day nor the session after one."""
    near = set()
    for i, d in enumerate(days):
        if d in events:
            near.update((i, i + 1))
    out = []
    for i in range(21, len(days)):
        if i in near:
            continue
        pre = closes[i - 1] / closes[i - 6] - 1
        below = closes[i - 1] < statistics.mean(closes[i - 20:i])
        if pre <= -0.03 and closes[i] > closes[i - 1]:
            out.append({"day": days[i], "i": i, "kind": "C", "pre": round(pre, 4), "below": below})
        if below and closes[i] < closes[i - 1]:
            out.append({"day": days[i], "i": i, "kind": "P", "pre": round(pre, 4), "below": below})
    return out


def stock_moves(closes: list[float], i: int, kind: str) -> dict[str, float]:
    sign = 1 if kind == "C" else -1
    return {f"s{k}": round(sign * (closes[i + k] / closes[i] - 1), 4) for k in (5, 10, 20) if i + k < len(closes)}


def exits(cost: float, closes: list[float]) -> dict[str, float]:
    """Exits on the option's daily closes after the entry; the ones whose day has not come yet are left out."""
    out_px = lambda c: max(c * (1 - SLIP_M), 0.0) / cost - 1
    path = closes[:HOLD]
    res = {f"d{k}": out_px(path[k - 1]) for k in (5, 10, 20) if len(path) >= k}
    best, armed, gone = 0.0, False, None  # gone: the session the trail sells on
    for k, c in enumerate(path):
        best = max(best, c)
        armed = armed or c >= 2 * cost
        if armed and c <= 0.6 * best:
            gone = k
            break
    if gone is None and len(path) < HOLD:
        return {k: round(v, 4) for k, v in res.items()}  # still open: only the fixed days already passed
    gone = len(path) - 1 if gone is None else gone
    res["trail2"] = out_px(path[gone])
    k3 = next((k for k, c in enumerate(path[:gone + 1]) if c >= 3 * cost), None)
    res["take3_trail"] = res["trail2"] if k3 is None else 0.5 * out_px(path[k3]) + 0.5 * res["trail2"]
    return {k: round(v, 4) for k, v in res.items()}


def option_leg(client, und: str, kind: str, day: str, spot: float, until: str) -> dict | None:
    """The ~1-month contract nearest the price at `day`'s close: its symbol, cost and the exits."""
    d0 = date.fromisoformat(day)
    cs = []
    for status in ("inactive", "active"):
        try:
            cs += client.option_contracts(und, status=status, type="call" if kind == "C" else "put",
                                          expiration_date_gte=(d0 + timedelta(days=25)).isoformat(),
                                          expiration_date_lte=(d0 + timedelta(days=45)).isoformat(),
                                          strike_price_gte=f"{spot * 0.95:.2f}", strike_price_lte=f"{spot * 1.05:.2f}")
        except Exception:
            pass
    if not cs:
        return None
    exp = min({c["expiration_date"] for c in cs}, key=lambda e: abs((date.fromisoformat(e) - d0).days - 30))
    sym = min((c for c in cs if c["expiration_date"] == exp), key=lambda c: abs(float(c["strike_price"]) - spot))["symbol"]
    try:
        bars = client.option_bars([sym], day, f"{until}T23:00:00Z", timeframe="1Day").get(sym, [])
    except Exception:
        return None
    first = next((b for b in bars if b["t"][:10] == day), None)
    if not first or float(first["c"]) <= 0:
        return None
    cost = float(first["c"]) * (1 + SLIP_M)
    after = [float(b["c"]) for b in bars if b["t"][:10] > day]
    return {"option": sym, "expiry": exp, "cost": round(cost, 4), "res": exits(cost, after)}


def run(client) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    names = list(ETFS) + sorted({r["und"] for r in latest("open-catalyst")["rows"]})
    split = client.daily_bars(names, "2023-11-01", adjustment="split", feed="sip")  # signals: no split jumps
    raw = client.daily_bars(names, "2023-11-01", adjustment="raw", feed="sip")      # strikes: the price that day
    events = {d: k for d, k in EVENTS.items() if START <= d <= end}
    rows, plain, missing = [], [], 0
    pace = Pace(client)
    for und in names:
        bs = [b for b in split.get(und, []) if b["t"][:10] <= end]
        if len(bs) < 30:
            continue
        days = [b["t"][:10] for b in bs]
        closes = [float(b["c"]) for b in bs]
        spot = {b["t"][:10]: float(b["c"]) for b in raw.get(und, [])}
        for s in signals(days, closes, events):
            leg = option_leg(client, und, s["kind"], s["day"], spot.get(s["day"], closes[s["i"]]), end)
            if not leg:
                missing += 1
            rows.append({**s, "und": und, "etf": und in ETFS, "stock": stock_moves(closes, s["i"], s["kind"]),
                         **({"option": leg["option"], "expiry": leg["expiry"], "cost": leg["cost"], "res": leg["res"]} if leg else {"res": {}})})
            pace.wait()
        plain += [{**s, "und": und, "etf": und in ETFS, "stock": stock_moves(closes, s["i"], s["kind"])}
                  for s in ordinary(days, closes, events) if s["day"] >= START]
    return {"start": START, "end": end, "names": names, "events": len(events), "missing_options": missing,
            "rows": rows, "plain": plain}


def rules(rows: list[dict]) -> dict[str, list[dict]]:
    """The two rules, their unfiltered controls, and each split into SMH / QQQ and the single names."""
    base = [("反弹：事件前 5 天跌 3%+，之后第一个收涨买看涨", "反弹 3%+", lambda r: r["kind"] == "C" and r["pre"] <= -0.03),
            ("反弹：事件前 5 天跌 5%+", "反弹 5%+", lambda r: r["kind"] == "C" and r["pre"] <= -0.05),
            ("对照：所有事件后第一个收涨买看涨", "看涨对照", lambda r: r["kind"] == "C"),
            ("破位：事件前收在 20 日均线下，之后第一个收跌买看跌", "破位", lambda r: r["kind"] == "P" and r["below"]),
            ("对照：所有事件后第一个收跌买看跌", "看跌对照", lambda r: r["kind"] == "P")]
    out = {}
    for name, short, f in base:
        rs = [r for r in rows if f(r)]
        out[name] = rs
        out[f"　{short}：SMH / QQQ"] = [r for r in rs if r["etf"]]
        out[f"　{short}：个股"] = [r for r in rs if not r["etf"]]
    return out


def spread(rows: list[dict], key: str, end: str) -> str:
    """mean / without the best 3 / median / 2024 / 2025-26 / last three months, for one exit."""
    xs = sorted(((r["day"], r["res"][key]) for r in rows if key in r.get("res", {})), key=lambda x: x[1])
    if not xs:
        return "—"
    v = [x for _, x in xs]
    cut = (date.fromisoformat(end) - timedelta(days=RECENT)).isoformat()
    part = lambda f: (lambda p: f"{statistics.mean(p):+.0%}（{len(p)}）" if p else "—")([x for d, x in xs if f(d)])
    trim = f"{statistics.mean(v[:-3]):+.0%}" if len(v) > 3 else "—"
    return (f"{statistics.mean(v):+.0%} / {trim} / {statistics.median(v):+.0%} / "
            f"{part(lambda d: d < '2025-01-01')} / {part(lambda d: d >= '2025-01-01')} / {part(lambda d: d >= cut)}")


def stock_table(groups: dict[str, list[dict]]) -> list[str]:
    out = ["| 情况 | 次数 | 5 天后（朝所选方向）平均 / 对的比例 | 10 天后 | 20 天后 |", "|---|---|---|---|---|"]
    for name, rs in groups.items():
        cells = []
        for k in ("s5", "s10", "s20"):
            v = [r["stock"][k] for r in rs if k in r["stock"]]
            cells.append(f"{statistics.mean(v):+.1%} / {sum(x > 0 for x in v) / len(v):.0%}" if v else "—")
        out.append(f"| {name} | {len(rs)} | " + " | ".join(cells) + " |")
    return out


def main() -> None:
    from hero.alpaca import Alpaca
    rep = run(Alpaca())
    gs = rules(rep["rows"])
    table, md = report(f"事件后转向：CPI / Fed 日买一个月期权 {date.today().isoformat()}",
                       f"{rep['start']} 至 {rep['end']}，{rep['events']} 个 CPI / Fed 日，SMH、QQQ 和 25 只活跃股。事件当天或第二天的第一个收涨（收跌）"
                       "按收盘价买约一个月到期（25–45 天里最接近 30 天）、行权价最接近现价的看涨（看跌），按期权每日收盘价卖，买卖各算 5% 成本。"
                       f"没有期权数据的信号 {rep['missing_options']} 个（只算股价）。最近的信号还没走满 20 天，只计已经过去的天数。",
                       gs, EXITS)
    md += ("\n## 每 $1 的分布\n\n每格「平均 / 去掉最好 3 笔 / 中位数 / 2024（笔数） / 2025–26 / 最近 3 个月」。\n\n"
           "| 情况 | " + " | ".join(EXITS.values()) + " |\n|---|" + "---|" * len(EXITS) + "\n" +
           "\n".join(f"| {g} | " + " | ".join(spread(rs, x, rep["end"]) for x in EXITS) + " |" for g, rs in gs.items()) + "\n")
    plain = {"平日：前 5 天跌 3%+、当天收涨（看涨方向）": [r for r in rep["plain"] if r["kind"] == "C"],
             "平日：前一天在 20 日均线下、当天收跌（看跌方向）": [r for r in rep["plain"] if r["kind"] == "P"]}
    md += ("\n## 只看股价：事件日对平日\n\n期权只算了事件日；这里用股价比较同样的信号在事件日和平日有没有差别"
           "（平日的信号会连着几天出现，彼此不独立）。\n\n" +
           "\n".join(stock_table({**{k: v for k, v in gs.items() if not k.startswith("　")}, **plain})) + "\n")
    cases = [r for r in rep["rows"] if r["und"] == "SMH" and (r["event"], r["kind"]) in (("2026-09-16", "C"), ("2026-10-14", "P"))]
    if cases:
        md += "\n## 对照 9 月这一轮（SMH）\n\n" + "\n".join(
            f"- {r['ev']} {r['event']}，{r['day']} 收盘{'买看涨' if r['kind'] == 'C' else '买看跌'}：事件前 5 天 {r['pre']:+.1%}，"
            f"{'在' if r['below'] else '不在'} 20 日均线下；期权 {r.get('option', '无数据')}；"
            + ("、".join(f"{EXITS[k]} {v:+.0%}" for k, v in r["res"].items()) or "还没有可卖的日子") for r in cases) + "\n"
    save("event-turn", {"generated": date.today().isoformat(), "table": table, "rows": rep["rows"],
                        "missing_options": rep["missing_options"]}, md)


if __name__ == "__main__":
    sys.exit(main())
