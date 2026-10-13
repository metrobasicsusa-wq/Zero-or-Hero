"""Research: for the 0DTE sleeves, when to get in and, above all, when to get out?

Owner, 2026-10-13: what matters most is knowing when to sell, and a 10:00 entry misses moves like META's.
Every weekday since 2024-02 on SPY, QQQ, TSLA, NVDA, META, with the live pick (the expiry of the day, else the
nearest within a week; a call if the price is above the open, else a put; the first strike 0.6% beyond the
price), for each entry time 09:35 / 09:45 / 10:00 / 10:30, played through each exit on one-minute *closes* (a lone
print at a minute's high is not counted as a fill):
  take3 / take5:      sell once a minute closes at 3x / 5x the cost, else at 15:30;
  trail2:             once a close reaches 2x, sell when a close falls 40% below the best close since;
  trail15:            once a close reaches 1.5x, sell when a close falls 30% below the best close;
  take3_trail:        sell half at 3x, the rest on trail2 (the most a ticket can make while keeping a runner);
  hold:               sell at 15:30;
paying 10% over to get in and getting 10% under to get out. Reported per $1 (mean, median), the share of tickets
ending at 2x or more, and the sleeve game (half the ledger a ticket, a round ends below 10%): rounds that doubled
against rounds that went to zero, and rounds that reached 20x ($10,000). Also the intraday path of the names on
the last session (stock minutes, the SIP tape 20 minutes back). Record only. Run: python -m hero.research_zdte_timing
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from hero import research_hero as rh
from hero.research_0dte import ET, SLIP, at_or_after, at_or_before, et_minutes
from hero.research_0dte_names import minutes_by_day, pick

START = "2024-02-01"
NAMES = ("SPY", "QQQ", "TSLA", "NVDA", "META")
ENTRIES = ("09:35", "09:45", "10:00", "10:30")
EXITS = ("take3", "take5", "trail2", "trail15", "take3_trail", "hold")
EXIT_ZH = {"take3": "3 倍卖", "take5": "5 倍卖", "trail2": "翻倍后回撤 40% 卖", "trail15": "1.5 倍后回撤 30% 卖",
           "take3_trail": "3 倍卖一半，其余回撤卖", "hold": "拿到 15:30"}
OFF = 0.006
OUT = "15:30"


def out_px(px: float) -> float:
    return max(px - max(px * SLIP, 0.01), 0.0)


def play(opt: dict[str, dict], entry: str) -> dict[str, float] | None:
    """Per $1 for each exit, on minute closes after the entry minute."""
    got = at_or_after(opt, entry)
    if not got:
        return None
    t0, bar = got
    if float(bar["c"]) <= 0:
        return None
    cost = float(bar["c"]) + max(float(bar["c"]) * SLIP, 0.01)
    path = [(k, float(v["c"])) for k, v in sorted(opt.items()) if t0 < k <= OUT]
    last = path[-1][1] if path else 0.0
    res = {"hold": out_px(last) / cost - 1}
    for name, x in (("take3", 3), ("take5", 5)):
        hit = next((c for _, c in path if c >= x * cost), None)
        res[name] = x - 1 if hit is not None else res["hold"]

    def trail(arm: float, give: float) -> float:
        best, armed = 0.0, False
        for _, c in path:
            best = max(best, c)
            armed = armed or c >= arm * cost
            if armed and c <= (1 - give) * best:
                return out_px(c) / cost - 1
        return res["hold"]
    res["trail2"], res["trail15"] = trail(2, 0.4), trail(1.5, 0.3)
    res["take3_trail"] = (res["take3"] + res["trail2"]) / 2 if res["take3"] == 2 else res["trail2"]
    return {k: round(v, 4) for k, v in res.items()}


def path_today(client, names: tuple[str, ...]) -> list[dict]:
    """The last session's intraday path: open, 10:00, high and low with their times, last."""
    end = datetime.now(timezone.utc) - timedelta(minutes=20)
    start = (end - timedelta(days=5)).strftime("%Y-%m-%dT00:00:00Z")
    out = []
    try:
        bars = client.stock_bars(list(names), start, end.strftime("%Y-%m-%dT%H:%M:%SZ"), feed="sip")
    except Exception:
        return out
    for s, bs in bars.items():
        if not bs:
            continue
        day = max(datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ET).date() for b in bs)
        m = {k: v for k, v in et_minutes([b for b in bs if datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ET).date() == day]).items()
             if "09:30" <= k <= "15:59"}
        if "09:30" not in m:
            continue
        o = float(m["09:30"]["o"])
        hi = max(m.items(), key=lambda kv: float(kv[1]["h"]))
        lo = min(m.items(), key=lambda kv: float(kv[1]["l"]))
        ten = at_or_before(m, "10:00")
        lk = max(m)
        out.append({"symbol": s, "day": day.isoformat(), "open": o, "ten": float(ten["c"]) if ten else None,
                    "high": float(hi[1]["h"]), "high_at": hi[0], "low": float(lo[1]["l"]), "low_at": lo[0],
                    "last": float(m[lk]["c"]), "last_at": lk})
    return out


def run(client) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    rows = []
    for und in NAMES:
        days = minutes_by_day(client, und, START, end)
        for day in sorted(days):
            m = days[day]
            if "09:30" not in m or not at_or_before(m, OUT):
                continue
            o = float(m["09:30"]["o"])
            plan = {}
            for e in ENTRIES:
                b = at_or_before(m, e)
                if b:
                    spot = float(b["c"])
                    plan[e] = ("C" if spot >= o else "P", spot)
            if not plan:
                continue
            lo = min(s for _, s in plan.values()) * 0.97
            hi = max(s for _, s in plan.values()) * 1.03
            cs = []
            for status in ("inactive", "active"):
                try:
                    cs = client.option_contracts(und, status=status, expiration_date_gte=day,
                                                 expiration_date_lte=(date.fromisoformat(day) + timedelta(days=7)).isoformat(),
                                                 strike_price_gte=f"{lo:.2f}", strike_price_lte=f"{hi:.2f}")
                except Exception:
                    cs = []
                if cs:
                    break
            if not cs:
                continue
            exp = min(c["expiration_date"] for c in cs)
            near = [c for c in cs if c["expiration_date"] == exp]
            syms = {e: pick(near, k, spot, OFF) for e, (k, spot) in plan.items()}
            want = sorted({s for s in syms.values() if s})
            if not want:
                continue
            try:
                bars = client.option_bars(want, f"{day}T13:00:00Z", f"{day}T21:00:00Z", timeframe="1Min")
            except Exception:
                continue
            time.sleep(0.05)
            mins = {s: et_minutes(b) for s, b in bars.items()}
            res = {}
            for e, s in syms.items():
                if s and s in mins:
                    got = play(mins[s], e)
                    if got:
                        res[e] = got
            if res:
                rows.append({"und": und, "day": day, "dte": (date.fromisoformat(exp) - date.fromisoformat(day)).days,
                             "side": {e: k for e, (k, _) in plan.items()}, "res": res})
    return {"generated": date.today().isoformat(), "start": START, "end": end, "rows": rows, "today": path_today(client, NAMES)}


def game(xs: list[tuple[str, float]], target: float) -> dict:
    s = sorted((d, max(0.5 * r, -1.0)) for d, r in xs)
    first = (date.fromisoformat(s[0][0]) - timedelta(days=1)).isoformat()
    q = rh.sequential(s, first, target, 0.9)
    return {"heroes": q["heroes"], "zeros": q["zeros"]}


def cell(xs: list[tuple[str, float]]) -> dict | None:
    if not xs:
        return None
    r = [x for _, x in xs]
    g2, g20 = game(xs, 2), game(xs, 20)
    return {"n": len(r), "mean": round(statistics.mean(r), 3), "median": round(statistics.median(r), 3),
            "x2": round(sum(x >= 1 for x in r) / len(r), 3), "h2": g2["heroes"], "z2": g2["zeros"], "h20": g20["heroes"]}


def table(rep: dict) -> dict:
    out = {}
    for und in NAMES + ("个股合计",):
        rs = [r for r in rep["rows"] if r["und"] == und or (und == "个股合计" and r["und"] in ("TSLA", "NVDA", "META"))]
        for e in ENTRIES:
            for x in EXITS:
                out[f"{und}|{e}|{x}"] = cell([(r["day"], r["res"][e][x]) for r in rs if e in r["res"]])
    return out


def markdown(rep: dict, t: dict) -> str:
    out = [f"# 末日单什么时候进、什么时候卖 {rep['generated']}", "",
           f"{rep['start']} 至 {rep['end']}，{'、'.join(NAMES)} 每个交易日：当天到期（没有就最近到期），比开盘高买看涨、低买看跌，离现价 0.6%；"
           "进场 09:35 / 09:45 / 10:00 / 10:30，按分钟收盘价判断卖出（不算分钟最高价的零星成交），买入多付 10%、卖出少拿 10%。"
           "每格「平均每 $1 / 收在 2 倍以上的比例 / 账本玩法到 2 倍：成功 / 归零 / 到 20 倍成功次数」。只研究，不改交易。", ""]
    if rep.get("today"):
        out += ["## 最近一个交易日的走势（股票分钟线）", "", "| 股票 | 日期 | 开盘 | 10:00 | 最高（时间） | 最低（时间） | 最新（时间） |", "|---|---|---|---|---|---|---|"]
        for p in rep["today"]:
            pc = lambda x: f"${x:,.2f}（{x / p['open'] - 1:+.1%}）"
            out.append(f"| {p['symbol']} | {p['day']} | ${p['open']:,.2f} | {pc(p['ten']) if p['ten'] else '—'} | {pc(p['high'])} {p['high_at']} | "
                       f"{pc(p['low'])} {p['low_at']} | {pc(p['last'])} {p['last_at']} |")
        out.append("")
    for und in NAMES + ("个股合计",):
        out += [f"## {und}", "", "| 进场 | " + " | ".join(EXIT_ZH[x] for x in EXITS) + " |", "|---|" + "---|" * len(EXITS)]
        for e in ENTRIES:
            cs = [t.get(f"{und}|{e}|{x}") for x in EXITS]
            out.append(f"| {e} | " + " | ".join(f"{c['mean']:+.0%} / {c['x2']:.0%} / {c['h2']}·{c['z2']}·{c['h20']}" if c else "—"
                                                for c in cs) + " |")
        out.append("")
    return "\n".join(out)


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca())
    t = table(rep)
    rep["table"] = t
    (root / "research" / f"{rep['generated']}-zdte-timing.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    (root / "research" / f"{rep['generated']}-zdte-timing.md").write_text(markdown(rep, t))
    print(markdown(rep, t))


if __name__ == "__main__":
    sys.exit(main())
