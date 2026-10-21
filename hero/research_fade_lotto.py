"""Research: the lottery fade (owner, 10-21: SPCX opened +8.6%, was still above its open at noon, and the 160 put due
that day went from about $0.25 to $3.15 by 14:10 -- "can we watch for those?", then「先大胆点 取样本」).

The same lottery the live scan (hero.sleeve_rules.FadeLotto) records, on Alpaca's history since 2024-02:
  events:  every day a name in data/universe.json (plus SPCX) opened 3%+ from yesterday's close (a split between the
           two days is skipped);
  when:    the first 10-minute scan from 10:00 to 15:00 at which the price is still on the gap's side of the open
           and the option below costs at most the cap ($0.50 or $1.00, on its minute close);
  which:   the nearest expiry within a week, the strike just past the open toward the fade (a put at or under the
           open after a gap up, a call at or over it after a gap down);
  exits:   on minute closes: half on a 3x take and half on a trail (2x, then a 40% giveback) -- the live rule --,
           held to 15:30 (expiring that day) or 15:50; also held to the end, a 3x take alone, a 10x take alone;
  costs:   pay 10% over (at least a cent) to get in, get 10% under to get out (hero.research_ideas).
Groups: every sample; the alert's condition (5%+, $0.50); gap up / gap down; expiring that day or not; and the
$500 ledger's game (the first alert-grade find between 11:00 and 14:00, one a day). Every group goes through
hero.robust (without the best 3, median, years, last 3 months, extra cost). Record only.
Run: python -m hero.research_fade_lotto
"""

from __future__ import annotations

import json
import statistics
import time
from datetime import date, timedelta

from hero.research_0dte import at_or_before, et_minutes
from hero.research_ideas import ROOT, cost_of, intraday, report, save

START = "2024-02-05"
GAP = 0.03
ALERT_GAP, ALERT_CAP = 0.05, 0.5
CAPS = (0.5, 1.0)
SCANS = [f"{h:02d}:{m:02d}" for h in range(10, 15) for m in range(0, 60, 10)] + ["15:00"]
FEED = "sip"
EXITS = {"half": "一半 3 倍止盈、一半回撤（实际规则）", "hold": "拿到收盘", "take3": "3 倍止盈", "take10": "10 倍止盈"}


def daily(client, syms: list[str], start: str, end: str, adjustment: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for i in range(0, len(syms), 50):
        params = {"symbols": ",".join(syms[i:i + 50]), "timeframe": "1Day", "start": start, "end": f"{end}T23:00:00Z",
                  "adjustment": adjustment, "feed": FEED, "limit": 10000}
        while True:
            page = client._d("/v2/stocks/bars", params) or {}
            for s, bs in (page.get("bars") or {}).items():
                out.setdefault(s, []).extend(bs)
            if not page.get("next_page_token"):
                break
            params["page_token"] = page["next_page_token"]
    return out


def gaps(adj: list[dict], raw: list[dict], start: str = START, gap: float = GAP) -> list[dict]:
    """Days that opened `gap` or more from the close before, with the raw open (what the strikes are near)."""
    by_raw = {b["t"][:10]: b for b in raw}
    out = []
    for i in range(1, len(adj)):
        d0, d1 = adj[i - 1]["t"][:10], adj[i]["t"][:10]
        r0, r1 = by_raw.get(d0), by_raw.get(d1)
        if d1 < start or not r0 or not r1:
            continue
        f0, f1 = float(adj[i - 1]["c"]) / float(r0["c"]), float(adj[i]["c"]) / float(r1["c"])
        if abs(f1 / f0 - 1) > 0.01:
            continue  # a split between the two days
        g = float(adj[i]["o"]) / float(adj[i - 1]["c"]) - 1
        if abs(g) >= gap:
            out.append({"day": d1, "gap": round(g, 4), "open": float(r1["o"]), "prev": float(r0["c"])})
    return out


def contract(client, sym: str, day: str, kind: str, o: float) -> tuple[str, str, float] | None:
    """(symbol, expiry, strike): the nearest expiry within a week, the strike just past the open toward the fade."""
    lo, hi = (o * 0.9, o * 1.0001) if kind == "P" else (o * 0.9999, o * 1.1)
    end = (date.fromisoformat(day) + timedelta(days=7)).isoformat()
    cs = []
    for status in ("inactive", "active"):
        try:
            cs = client.option_contracts(sym, status=status, type="put" if kind == "P" else "call", expiration_date_gte=day,
                                         expiration_date_lte=end, strike_price_gte=f"{lo:.2f}", strike_price_lte=f"{hi:.2f}")
        except Exception:
            cs = []
        if cs:
            break
    cs = [c for c in cs if (float(c["strike_price"]) <= o if kind == "P" else float(c["strike_price"]) >= o)]
    if not cs:
        return None
    exp = min(c["expiration_date"] for c in cs)
    ks = sorted((c for c in cs if c["expiration_date"] == exp), key=lambda c: float(c["strike_price"]))
    c = ks[-1] if kind == "P" else ks[0]
    return c["symbol"], exp, float(c["strike_price"])


def still_beyond(stock: dict[str, dict], o: float, g: float) -> list[str]:
    """The scan times at which the price is still on the gap's side of the open."""
    out = []
    for t in SCANS:
        bar = at_or_before(stock, t)
        if bar and ((g > 0 and float(bar["c"]) > o) or (g < 0 and float(bar["c"]) < o)):
            out.append(t)
    return out


def entry(opt: dict[str, dict], times: list[str], cap: float) -> str | None:
    """The first scan time at which the option's last minute (within 10 minutes) closed at or under the cap."""
    mins = lambda hhmm: int(hhmm[:2]) * 60 + int(hhmm[3:])
    for t in times:
        keys = [k for k in opt if k <= t]
        if not keys:
            continue
        k = max(keys)
        if mins(t) - mins(k) <= 10 and 0 < float(opt[k]["c"]) <= cap:
            return t
    return None


def play(opt: dict[str, dict], at: str, out: str) -> tuple[dict, float, float] | None:
    """The exits (per $1) from `at`, the cost paid and the best multiple of the cost reached after it."""
    res = intraday(opt, at, out, takes=(3, 10))
    if not res:
        return None
    first = min(k for k in opt if k >= at)
    cost = cost_of(float(opt[first]["c"]))
    later = [float(v["c"]) for k, v in opt.items() if first < k <= out]
    res["half"] = round((res["take3"] + res["trail2"]) / 2, 4)
    return res, cost, (max(later) / cost if later else 0.0)


def run(client, end: str | None = None) -> dict:
    end = end or date.today().isoformat()
    syms = list(json.loads((ROOT / "data" / "universe.json").read_text()).get("stocks") or []) + ["SPCX"]
    syms = list(dict.fromkeys(syms))
    first = (date.fromisoformat(START) - timedelta(days=10)).isoformat()
    adj, raw = daily(client, syms, first, end, "all"), daily(client, syms, first, end, "raw")
    by_day: dict[str, list[dict]] = {}
    for s in syms:
        for ev in gaps(adj.get(s, []), raw.get(s, [])):
            by_day.setdefault(ev["day"], []).append({**ev, "sym": s})
    rows, counts = [], {"gaps": 0, "beyond": 0, "contract": 0, "cheap": {str(c): 0 for c in CAPS}}
    for day in sorted(by_day):
        evs = by_day[day]
        counts["gaps"] += len(evs)
        try:
            mins = client.stock_bars([e["sym"] for e in evs], f"{day}T13:00:00Z", f"{day}T21:00:00Z", feed=FEED)
        except Exception:
            continue
        todo = []
        for e in evs:
            stock = et_minutes(mins.get(e["sym"], []))
            times = still_beyond(stock, e["open"], e["gap"])
            if not times:
                continue
            counts["beyond"] += 1
            kind = "P" if e["gap"] > 0 else "C"
            got = contract(client, e["sym"], day, kind, e["open"])
            time.sleep(0.02)
            if not got:
                continue
            counts["contract"] += 1
            # did the price get back through the open after the first scan it was still beyond it?
            after = [float(v["c"]) for k, v in stock.items() if times[0] < k <= "15:59"]
            crossed = any((p < e["open"]) if kind == "P" else (p > e["open"]) for p in after)
            todo.append((e, kind, times, got, crossed))
        if not todo:
            continue
        try:
            opts = client.option_bars([g[0] for _, _, _, g, _ in todo], f"{day}T13:00:00Z", f"{day}T21:00:00Z", timeframe="1Min")
        except Exception:
            continue
        for e, kind, times, (osym, exp, strike), crossed in todo:
            opt = et_minutes(opts.get(osym, []))
            out = "15:30" if exp == day else "15:50"
            for cap in CAPS:
                at = entry(opt, times, cap)
                if not at:
                    continue
                counts["cheap"][str(cap)] += 1
                got = play(opt, at, out)
                if not got:
                    continue
                res, cost, peak = got
                rows.append({"day": day, "sym": e["sym"], "kind": kind, "gap": e["gap"], "cap": cap, "at": at, "option": osym,
                             "strike": strike, "expiry": exp, "same_day": exp == day, "cost": round(cost, 3),
                             "peak": round(peak, 2), "crossed": crossed, "res": res})
    return {"generated": date.today().isoformat(), "start": START, "end": end, "names": len(syms), "counts": counts, "rows": rows}


def groups(rows: list[dict]) -> dict[str, list[dict]]:
    by = lambda f: sorted((r for r in rows if f(r)), key=lambda r: (r["day"], r["at"]))
    alert = lambda r: r["cap"] == ALERT_CAP and abs(r["gap"]) >= ALERT_GAP
    return {
        "样本：跳空 3%+、≤$1.00（每只每天第一次）": by(lambda r: r["cap"] == 1.0),
        "　高开 → 看跌": by(lambda r: r["cap"] == 1.0 and r["kind"] == "P"),
        "　低开 → 看涨": by(lambda r: r["cap"] == 1.0 and r["kind"] == "C"),
        "　当天到期": by(lambda r: r["cap"] == 1.0 and r["same_day"]),
        "　不是当天到期": by(lambda r: r["cap"] == 1.0 and not r["same_day"]),
        "提醒条件：跳空 5%+、≤$0.50": by(alert),
        "　高开 5%+ → 看跌（SPCX 那种）": by(lambda r: alert(r) and r["kind"] == "P"),
        "　跳空 8%+": by(lambda r: alert(r) and abs(r["gap"]) >= 0.08),
        "$500 账本玩法：11:00–14:00 每天第一张": [r for r in by(lambda r: alert(r) and "11:00" <= r["at"] < "14:00")],
    }


def extras(rep: dict) -> str:
    rows, c = rep["rows"], rep["counts"]
    out = ["", "## 漏斗", "",
           f"- 跳空 3% 以上：{c['gaps']} 次（{rep['names']} 只，{rep['start']} 至 {rep['end']}）",
           f"- 10:00–15:00 某次扫描时还没回到开盘价：{c['beyond']} 次",
           f"- 找得到一周内到期、开盘价那一档的期权：{c['contract']} 次",
           f"- 扫描时那张期权 ≤$0.50：{c['cheap']['0.5']} 次；≤$1.00：{c['cheap']['1.0']} 次", ""]
    for cap in CAPS:
        rs = [r for r in rows if r["cap"] == cap]
        if not rs:
            continue
        peaks = [r["peak"] for r in rs]
        out.append(f"- ≤${cap:.2f} 的 {len(rs)} 张：之后回到开盘价另一边的 {sum(r['crossed'] for r in rs) / len(rs):.0%}；"
                   f"最高到 3 倍以上 {sum(p >= 3 for p in peaks)} 张、10 倍以上 {sum(p >= 10 for p in peaks)} 张、"
                   f"20 倍以上 {sum(p >= 20 for p in peaks)} 张；最高倍数中位 {statistics.median(peaks):.1f}")
    best = sorted((r for r in rows if r["cap"] == 1.0), key=lambda r: -r["peak"])[:12]
    if best:
        out += ["", "## 最高倍数前 12 张（≤$1.00 样本）", "", "| 日期 | 股票 | 跳空 | 买入 | 合约 | 成本 | 最高倍数 | 实际规则 |", "|---|---|---|---|---|---|---|---|"]
        out += [f"| {r['day']} | {r['sym']} | {r['gap']:+.1%} | {r['at']} | {r['strike']:g}{r['kind']}（{r['expiry'][5:]}） | ${r['cost']:.2f} | "
                f"{r['peak']:.1f} 倍 | {r['res']['half']:+.0%} |" for r in best]
    spcx = [r for r in rows if r["sym"] == "SPCX"]
    if spcx:
        out += ["", "## SPCX", ""] + [f"- {r['day']} 跳空 {r['gap']:+.1%}，{r['at']} 买 {r['strike']:g}{r['kind']}（≤${r['cap']:.2f}，成本 ${r['cost']:.2f}），"
                                       f"最高 {r['peak']:.1f} 倍，实际规则 {r['res']['half']:+.0%}" for r in spcx]
    return "\n".join(out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    rep = run(Alpaca())
    table, md = report(f"高开回落彩票：回测 {rep['generated']}",
                       f"{rep['start']} 至 {rep['end']}，data/universe.json 的 {rep['names'] - 1} 只加 SPCX：开盘比昨收跳空 3% 以上、"
                       "10:00–15:00 每 10 分钟扫一次，第一次还没回到开盘价、而且开盘价那一档（高开买看跌、低开买看涨）、一周内最近到期的期权"
                       "分钟收盘价在上限以内时买；买入多付 10%、卖出少拿 10%；当天到期的 15:30、其他 15:50 卖。和实盘扫描（fadelotto）同一个规则。",
                       groups(rep["rows"]), EXITS, rep["end"])
    rep["table"] = table
    save("fade-lotto", rep, md + extras(rep))


if __name__ == "__main__":
    main()
