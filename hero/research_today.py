"""Observation: how the chip stocks and the Magnificent 7 traded on one day (default: the latest).

For each name in research_gap.FOCUS (plus SPY, QQQ): the gap from the prior close, the path through
the day (vs the prior close, on all-exchange SIP 1-minute bars, IEX if refused), the low and high with their times, open-to-close,
whether it got back above the prior close, and what was known before the open (63-day trend and
volatility, how its own last gap-downs did from open to close). For every 2%+ gap-down, claude-b's
call trade (research_gap.simulate: nearest expiry within 4 days, 1/2/3% above the 9:45 price).
Record only. Run: python -m hero.research_today [YYYY-MM-DD]
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

from hero import research_0dte as z
from hero.research_gap import EXITS, FOCUS, GAP, MAG7, OTM, SEMIS, features, simulate

MARKS = ("09:31", "09:45", "10:00", "10:30", "11:00", "12:00", "13:00", "14:00", "15:00", "15:59")


def path(sm: dict[str, dict], prev: float) -> dict | None:
    keys = sorted(k for k in sm if "09:30" <= k <= "15:59")
    if not keys:
        return None
    o, c = float(sm[keys[0]]["o"]), float(sm[keys[-1]]["c"])
    lo_k = min(keys, key=lambda k: float(sm[k]["l"]))
    hi_k = max(keys, key=lambda k: float(sm[k]["h"]))
    lo, hi = float(sm[lo_k]["l"]), float(sm[hi_k]["h"])
    back = next((k for k in keys if float(sm[k]["h"]) >= prev), None) if o < prev else None
    marks = {}
    for m in MARKS:
        b = z.at_or_before({k: sm[k] for k in keys}, m)
        if b:
            marks[m] = round(float(b["c"]) / prev - 1, 4)
    return {"gap": round(o / prev - 1, 4), "open_to_close": round(c / o - 1, 4), "day": round(c / prev - 1, 4),
            "low": round(lo / prev - 1, 4), "low_at": lo_k, "high": round(hi / prev - 1, 4), "high_at": hi_k,
            "low_to_close": round(c / lo - 1, 4), "back_above_prev": back, "marks": marks}


def calls(client, s: str, d: str, sm: dict[str, dict]) -> list[dict]:
    spot_bar = z.at_or_before(sm, "09:45")
    if not spot_bar:
        return []
    spot = float(spot_bar["c"])
    lim = (date.fromisoformat(d) + timedelta(days=4)).isoformat()
    cs = []
    for status in ("active", "inactive"):
        cs = client.option_contracts(s, status=status, type="call", expiration_date_gte=d, expiration_date_lte=lim,
                                     strike_price_gte=f"{spot * 1.005:.2f}", strike_price_lte=f"{spot * 1.06:.2f}")
        if cs:
            break
    if not cs:
        return []
    exp = min(c["expiration_date"] for c in cs)
    by_k = {float(c["strike_price"]): c["symbol"] for c in cs if c["expiration_date"] == exp}
    picks = {m: next((k for k in sorted(by_k) if k >= spot * (1 + m)), None) for m in OTM}
    picks = {m: k for m, k in picks.items() if k}
    if not picks:
        return []
    ob = client.option_bars(sorted({by_k[k] for k in picks.values()}), f"{d}T13:00:00Z", f"{d}T21:00:00Z", timeframe="1Min")
    out = []
    for m, k in picks.items():
        r = simulate(z.et_minutes(ob.get(by_k[k], [])))
        if r:
            out.append({"otm": m, "strike": k, "expiry": exp, **r})
    return out


def run(client, day: str | None = None) -> dict:
    syms = sorted((FOCUS - {"GOOG"}) | {"SPY", "QQQ"})
    first = (date.today() - timedelta(days=420)).isoformat()
    try:  # official all-exchange closes, so the gap is measured against the real prior close
        bars = client.daily_bars(syms, first, adjustment="raw", feed="sip")
    except Exception:
        bars = client.daily_bars(syms, first, adjustment="raw")
    spy_days = sorted(b["t"][:10] for b in bars.get("SPY", []))
    if day is None:
        day = spy_days[-1] if spy_days else date.today().isoformat()
    rows = []
    for s in syms:
        hist = sorted((b["t"][:10], float(b["o"]), float(b["c"])) for b in bars.get(s, []) if b["t"][:10] < day)
        if len(hist) < 2:
            continue
        dates = [h[0] for h in hist] + [day]
        xs = [h[2] for h in hist] + [None]
        opens = {h[0]: h[1] for h in hist}
        prev = hist[-1][2]
        try:  # the full tape (all exchanges); IEX alone is thin in the first minutes
            sm, feed = z.et_minutes(client.stock_bars([s], f"{day}T13:00:00Z", f"{day}T21:00:00Z", feed="sip").get(s, [])), "sip"
        except Exception:
            sm, feed = z.et_minutes(client.stock_bars([s], f"{day}T13:00:00Z", f"{day}T21:00:00Z").get(s, [])), "iex"
        p = path(sm, prev)
        if not p:
            continue
        row = {"symbol": s, "group": "Mag 7" if s in MAG7 else "半导体" if s in SEMIS else "大盘",
               "prev_close": prev, "feed": feed, **p, **features(xs, opens, dates, len(dates) - 1)}
        if p["gap"] <= -GAP:
            try:
                row["calls"] = calls(client, s, day, sm)
            except Exception as e:
                row["calls_error"] = str(e)[:120]
        rows.append(row)
    return {"generated": date.today().isoformat(), "day": day, "rows": rows}


def pct(x) -> str:
    return "—" if x is None else f"{x:+.1%}"


def markdown(rep: dict) -> str:
    rows = sorted(rep["rows"], key=lambda r: r["gap"])
    out = [f"# {rep['day']} 半导体 / Mag 7 盘中走势", "",
           f"相对昨收的涨跌（1 分钟线，行情源：{'、'.join(sorted({r.get('feed', 'iex') for r in rep['rows']})) or '—'}；sip = 全部交易所，iex = 单一交易所）。"
           "「收复」= 盘中回到昨收的时间。盘前已知：63 日涨幅 / 63 日波动 / 过去一年自己最近几次低开 2%+ 当天开盘到收盘的平均。", "",
           "| 股票 | 组 | 开盘缺口 | 最低（时间） | 最高（时间） | 收盘 | 开→收 | 低→收 | 收复 | 63日涨幅 | 63日波动 | 以往低开当天（次数） |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| {r['symbol']} | {r['group']} | {pct(r['gap'])} | {pct(r['low'])}（{r['low_at']}） | {pct(r['high'])}（{r['high_at']}） | "
                   f"{pct(r['day'])} | {pct(r['open_to_close'])} | {pct(r['low_to_close'])} | {r['back_above_prev'] or '—'} | "
                   f"{pct(r['mom63'])} | {pct(r['vol63'])} | {pct(r['prior_rebound'])}（{r['prior_n']}） |")
    out += ["", "## 盘中路径（相对昨收）", "", "| 股票 | " + " | ".join(MARKS) + " |", "|---" * (len(MARKS) + 1) + "|"]
    for r in rows:
        out.append(f"| {r['symbol']} | " + " | ".join(pct(r["marks"].get(m)) for m in MARKS) + " |")
    gd = [r for r in rows if r.get("calls")]
    if gd:
        out += ["", "## 低开 2%+ 的：9:45 买看涨（claude-b 规则）", "",
                "| 股票 | 价外 | 行权价 | 到期 | 成本 | 最好 | 回撤卖 | 3 倍止盈 | 收盘卖 |", "|---|---|---|---|---|---|---|---|---|"]
        for r in gd:
            for c in r["calls"]:
                out.append(f"| {r['symbol']} | {c['otm']:.0%} | {c['strike']:g} | {c['expiry']} | ${c['cost']:.2f} | {c['best']:.1f} 倍 | "
                           + " | ".join(f"{c[k]:+.0%}" for k in EXITS) + " |")
    return "\n".join(out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca(), sys.argv[1] if len(sys.argv) > 1 else None)
    (root / "research" / f"{rep['day']}-today.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    (root / "research" / f"{rep['day']}-today.md").write_text(markdown(rep))
    print(markdown(rep))


if __name__ == "__main__":
    sys.exit(main())
