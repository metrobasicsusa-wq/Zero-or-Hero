"""Does the option volume of the first half hour say which way the day goes? (owner, 11-18: is watching option volume
any use -- check it now, and add the Magnificent 7.)

Every trading day since 2024-02, for SPY and the seven: the nearest expiry (the same day for SPY), every listed strike
within BAND of the open, one-minute option bars 09:30-09:59. Two measures of that half hour: contracts (volume) and
dollars (volume x the minute's average price x 100). The put share is puts / (puts + calls). A day's flow is "more puts
than usual" when its put share is above the median of the same underlying's previous 20 days, and "more calls than
usual" when below: single stocks always trade more calls than puts, so the raw share says nothing.

Fixed before looking, per underlying and measure:
  (a) direction -- go with the flow in the shares from 10:00 to 15:30 (long after more calls, short after more puts).
      PASSES when it is right on 55% of the days or more, its mean per $1 is above zero in both halves of the days and
      without its three best days, on 60 days or more. Beside it: always long, and going with the price (long above
      the open at 10:00, short below).
  (b) the 10:00 option trade -- a call above the open and a put below, the first strike OFF beyond the price, half sold
      at 3x and the rest trailed 40% off its best after a double, else 15:30, on real minute closes at 5% a side and at
      no cost: the days the flow AGREES with the price against the days it does not, on the line of
      research_zdte_lines.verdict (+0.15 per $1, above zero in both halves and without the three best days, 60 days).
Record only. Run: python -m hero.research_flow_open (SPY) / python -m hero.research_flow_open_mag7
"""

from __future__ import annotations

import statistics
import sys
from datetime import date, datetime, time as dtime, timedelta, timezone

from hero import robust
from hero.research_0dte import ET, at_or_before, et_minutes
from hero.research_0dte_names import minutes_by_day, pick
from hero.research_event_turn import Pace
from hero.research_ideas import save
from hero.research_zdte_cost import key, play_cost
from hero.research_zdte_lines import COSTS, verdict

START, ENTRY, OUT = "2024-02-01", "10:00", "15:30"
MAG7 = ("AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA")
BAND = {"SPY": 0.01}          # strikes counted, as a share of the open; the stocks use BAND_STOCK
BAND_STOCK = 0.05
OFF = {"SPY": 0.003}          # how far beyond the 10:00 price the traded strike is; the stocks use OFF_STOCK
OFF_STOCK = 0.01
WIDE = {"SPY": 0.06}          # strikes listed for a week, as a share of its first open; the stocks use WIDE_STOCK
WIDE_STOCK = 0.20
LOOK = 20                     # days of the underlying's own put share the day is compared with
MEASURES = {"n": "张数", "usd": "金额"}
NEED_HIT, NEED_DAYS = 0.55, 60


def utc(day: str, hhmm: str) -> str:
    h, m = map(int, hhmm.split(":"))
    t = datetime.combine(date.fromisoformat(day), dtime(h, m), tzinfo=ET).astimezone(timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def week_contracts(client, und: str, monday: date, spot: float) -> list[dict]:
    """Every contract of the week's expiries (and the one after, for a Friday holiday) in a wide band of strikes."""
    wide, out = WIDE.get(und, WIDE_STOCK), []
    for status in ("inactive", "active"):
        try:
            out += client.option_contracts(und, status=status, expiration_date_gte=monday.isoformat(),
                                           expiration_date_lte=(monday + timedelta(days=11)).isoformat(),
                                           strike_price_gte=f"{spot * (1 - wide):.2f}", strike_price_lte=f"{spot * (1 + wide):.2f}")
        except Exception:
            pass
    return list({c["symbol"]: c for c in out}.values())


def half_hour(bars: dict[str, list[dict]], kinds: dict[str, str]) -> dict[str, float]:
    """Calls and puts of 09:30-09:59, in contracts and in dollars."""
    tot = {"calls": 0.0, "puts": 0.0, "call_usd": 0.0, "put_usd": 0.0}
    for sym, bs in bars.items():
        side = "call" if kinds.get(sym) == "call" else "put"
        for k, b in et_minutes(bs).items():
            if "09:30" <= k <= "09:59":
                v = float(b.get("v") or 0)
                tot[side + "s"] += v
                tot[side + "_usd"] += v * float(b.get("vw") or b.get("c") or 0) * 100
    return {k: round(v, 2) for k, v in tot.items()}


def run(client, unds: tuple[str, ...] = ("SPY",)) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    pace, rows = Pace(client), []
    for und in unds:
        days = minutes_by_day(client, und, START, end)
        listed: dict[date, list[dict]] = {}
        for day in sorted(days):
            m = days[day]
            b, last = at_or_before(m, ENTRY), at_or_before(m, OUT)
            if "09:30" not in m or not b or not last:
                continue
            o, spot = float(m["09:30"]["o"]), float(b["c"])
            d = date.fromisoformat(day)
            monday = d - timedelta(days=d.weekday())
            if monday not in listed:
                listed[monday] = week_contracts(client, und, monday, o)
            cs = [c for c in listed[monday] if day <= c["expiration_date"] <= (d + timedelta(days=7)).isoformat()]
            if not cs:
                continue
            exp = min(c["expiration_date"] for c in cs)
            cs = [c for c in cs if c["expiration_date"] == exp]
            band = BAND.get(und, BAND_STOCK)
            near = {c["symbol"]: c["type"] for c in cs if o * (1 - band) <= float(c["strike_price"]) <= o * (1 + band)}
            if not near:
                continue
            pace.wait()
            flow: dict[str, list[dict]] = {}
            syms = sorted(near)
            try:
                for i in range(0, len(syms), 100):
                    flow.update(client.option_bars(syms[i:i + 100], utc(day, "09:30"), utc(day, "10:00"), timeframe="1Min"))
            except Exception:
                continue
            tot = half_hour(flow, near)
            if tot["calls"] + tot["puts"] <= 0:
                continue
            kind = "C" if spot >= o else "P"
            sym, res = pick(cs, kind, spot, OFF.get(und, OFF_STOCK)), {}
            if sym:
                try:
                    mins = et_minutes(client.option_bars([sym], f"{day}T13:00:00Z", f"{day}T21:00:00Z", timeframe="1Min").get(sym, []))
                except Exception:
                    mins = {}
                for buy, sell, _ in COSTS:
                    got = play_cost(mins, ENTRY, buy, sell) if mins else None
                    if got:
                        res[key(buy, sell)] = got["take3_trail"]
            rows.append({"und": und, "day": day, "dte": (date.fromisoformat(exp) - d).days, "move": round(spot / o - 1, 5),
                         "ret": round(float(last["c"]) / spot - 1, 5), "kind": kind, "sym": sym, "res": res, **tot})
    return {"generated": date.today().isoformat(), "start": START, "end": end, "unds": list(unds), "rows": rows}


def flows(rows: list[dict], measure: str) -> dict[str, int]:
    """day -> +1 (more calls than this underlying's last LOOK days) or -1 (more puts). One underlying's rows."""
    c, p = ("calls", "puts") if measure == "n" else ("call_usd", "put_usd")
    rows = sorted((r for r in rows if r[c] + r[p] > 0), key=lambda r: r["day"])
    share = [r[p] / (r[c] + r[p]) for r in rows]
    out = {}
    for i in range(LOOK, len(rows)):
        med = statistics.median(share[i - LOOK:i])
        if share[i] != med:
            out[rows[i]["day"]] = -1 if share[i] > med else 1
    return out


def direction(xs: list[tuple[str, float]]) -> dict | None:
    """The fixed line (a) for (day, per $1) of going one way in the shares from 10:00 to 15:30."""
    xs = sorted(xs)
    if not xs:
        return None
    mean = lambda v: statistics.mean(r for _, r in v) if v else None
    half = len(xs) // 2
    halves = [mean(xs[:half]), mean(xs[half:])]
    rest = sorted(xs, key=lambda x: -x[1])[3:]
    hit = sum(r > 0 for _, r in xs) / len(xs)
    ok = (hit >= NEED_HIT and all(h is not None and h > 0 for h in halves) and bool(rest) and mean(rest) > 0
          and len(xs) >= NEED_DAYS)
    return {"n": len(xs), "hit": hit, "mean": mean(xs), "halves": halves, "minus3": mean(rest), "pass": ok,
            "flags": robust.flags(robust.summary(xs))}


def lines(rows: list[dict], und: str) -> dict:
    """Both fixed lines for one underlying: {measure: {"dir", "long", "price", "trade": {cost: verdict}}}."""
    mine = [r for r in rows if r["und"] == und]
    out = {}
    for measure in MEASURES:
        f = flows(mine, measure)
        on = [r for r in mine if r["day"] in f]
        side = lambda r: 1 if r["move"] >= 0 else -1
        trade = {}
        for buy, sell, _ in COSTS:
            k = key(buy, sell)
            trade[k] = verdict([(r["day"], r["res"][k]) for r in on if k in r["res"] and f[r["day"]] == side(r)],
                               [(r["day"], r["res"][k]) for r in on if k in r["res"] and f[r["day"]] != side(r)])
        out[measure] = {"dir": direction([(r["day"], f[r["day"]] * r["ret"]) for r in on]),
                        "long": direction([(r["day"], r["ret"]) for r in on]),
                        "price": direction([(r["day"], side(r) * r["ret"]) for r in on]),
                        "agree": sum(f[r["day"]] == side(r) for r in on) / len(on) if on else None,
                        "trade": trade}
    return out


def report(rep: dict) -> tuple[dict, str]:
    pct = lambda v: "—" if v is None else f"{v:+.2%}"
    num = lambda v: "—" if v is None else f"{v:+.2f}"
    usd = lambda v: "没有 hero" if v is None else f"${v:,.0f}"
    table, out, total, passed = {}, [], 0, 0
    for und in rep["unds"]:
        mine = [r for r in rep["rows"] if r["und"] == und]
        if not mine:
            continue
        got = table[und] = lines(rep["rows"], und)
        puts = statistics.median(r["puts"] / (r["calls"] + r["puts"]) for r in mine)
        out += [f"## {und}", "", f"{len(mine)} 个交易日；头半小时看跌占成交张数的中位数 {puts:.0%}；"
                f"其中当天到期的 {sum(r['dte'] == 0 for r in mine)} 天。", "",
                "**(a) 跟着期权量的方向买卖正股，10:00 到 15:30**", "",
                "| 量按 | 天数 | 方向对的比例 | 平均每 $1（前半 / 后半；去掉最好 3 天） | 过线 | 对照：一直做多 | 对照：跟价格（高于开盘做多） | "
                "量和价格同向的比例 | 提醒 |", "|---|---|---|---|---|---|---|---|---|"]
        for measure, name in MEASURES.items():
            g = got[measure]
            d = g["dir"]
            if not d:
                continue
            total, passed = total + 1, passed + bool(d["pass"])
            side = lambda v: "—" if not v else f"{v['hit']:.0%} / {pct(v['mean'])}"
            out.append(f"| {name} | {d['n']} | {d['hit']:.0%} | {pct(d['mean'])}（{pct(d['halves'][0])} / {pct(d['halves'][1])}；{pct(d['minus3'])}） | "
                       f"{'过' if d['pass'] else '不过'} | {side(g['long'])} | {side(g['price'])} | "
                       f"{'—' if g['agree'] is None else format(g['agree'], '.0%')} | {'；'.join(d['flags']) or '—'} |")
        out += ["", "**(b) 10:00 的期权买法：量和价格同向的日子，对不同向的日子**", "",
                "| 量按 | 成本 | 同向：天数 / 每 $1（中位数） | 不同向：天数 / 每 $1（中位数） | 差（前半 / 后半；去掉最好 3 天） | 过线 | "
                "每个 hero 的成本：每天 / 只做同向 / 只做不同向 | 同向日子的提醒 |", "|---|---|---|---|---|---|---|---|"]
        for measure, name in MEASURES.items():
            for buy, sell, cname in COSTS:
                v = got[measure]["trade"][key(buy, sell)]
                if not v:
                    continue
                total, passed = total + 1, passed + bool(v["pass"])
                out.append(f"| {name} | {cname} | {v['on_n']} / {v['on_mean']:+.2f}（{v['on_median']:+.2f}） | "
                           f"{v['off_n']} / {v['off_mean']:+.2f}（{v['off_median']:+.2f}） | "
                           f"{v['diff']:+.2f}（{num(v['halves'][0])} / {num(v['halves'][1])}；{num(v['minus3'])}） | "
                           f"{'过' if v['pass'] else '不过'} | {usd(v['hero_all'])} / {usd(v['hero_on'])} / {usd(v['hero_off'])} | "
                           f"{'；'.join(v['flags']) or '—'} |")
        out.append("")
    head = [f"# 开盘头半小时的期权量能不能看出当天方向（真实期权价）{rep['generated']}", "",
            f"{rep['start']} 至 {rep['end']}，{'、'.join(rep['unds'])}。每个交易日取最近到期的期权（SPY 是当天到期），行权价在开盘价上下 "
            f"{BAND['SPY']:.0%}（个股 {BAND_STOCK:.0%}）以内的全部合约，09:30–09:59 的成交：按张数和按金额各算一次看跌占比。"
            f"占比高于这只股票自己前 {LOOK} 天的中位数算「看跌比平时多」，低于算「看涨比平时多」（个股平时就是看涨多，原始占比没有意义）。", "",
            f"及格线在看结果之前写死。(a) 方向：10:00 到 15:30 跟着量的方向做正股，方向对的比例 {NEED_HIT:.0%} 以上、前后两半平均都为正、"
            f"去掉最好 3 天仍为正、至少 {NEED_DAYS} 天。(b) 期权：10:00 高于开盘买看涨、低于买看跌，行权价离现价至少 {OFF['SPY']:.1%}"
            f"（个股 {OFF_STOCK:.0%}），一半 3 倍卖、其余翻倍后从最高回撤 40% 卖、否则 15:30 卖；量和价格同向的日子对不同向的日子，"
            "用 11-16 那条线（差 +0.15 以上、前后两半都为正、去掉最好 3 天仍为正、同向至少 60 天）。只研究，不改交易。", "",
            f"**{total} 条线过了 {passed} 条。**", ""]
    return table, "\n".join(head + out) + "\n"


def main(unds: tuple[str, ...] = ("SPY",), name: str = "flow-open") -> None:
    from hero.alpaca import Alpaca
    rep = run(Alpaca(), unds)
    table, md = report(rep)
    save(name, {**rep, "table": table}, md)


if __name__ == "__main__":
    sys.exit(main())
