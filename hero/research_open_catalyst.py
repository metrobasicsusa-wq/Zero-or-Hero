"""Research: a stock gaps at the open on pre-market news -- buy its option at 09:35 the way the open is going?

Owner, 2026-10-13: META ran +4.5% between 09:30 and 10:00 (its high at 09:59), so a 10:00 entry buys the top;
claude-b caught it by entering at the open on the catalyst. Every weekday since 2024-02 on a liquid list:
  event:     the open at least 2% away from the prior close (SIP minutes), with or without headlines between
             the prior close and 09:30 (Alpaca / Benzinga news; one or more, and three or more);
  direction: the first five minutes (above the open at 09:35 -> a call, below -> a put), also the gap's direction;
  contract:  the day's expiry, else the nearest within a week, the first strike 0.6% beyond the 09:35 price;
  exits:     on minute closes -- 3x / 5x, trailing after 2x (40% giveback) or 1.5x (30%), half at 3x plus a
             trailing runner, hold to 15:30; and for a later expiry, held to the next close;
  costs:     10% over to get in, 10% under to get out.
Then one sleeve's game: the day's largest gap only, half the ledger a ticket, a round ends below 10%: rounds
that doubled against rounds that went to zero, and rounds that reached 20x. Record only.
Run: python -m hero.research_open_catalyst
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from hero.research_0dte import ET, SLIP, at_or_after, at_or_before, et_minutes
from hero.research_0dte_names import minutes_by_day, pick
from hero.research_zdte_timing import EXITS, EXIT_ZH, game, out_px, play

START = "2024-02-01"
NAMES = ("TSLA", "NVDA", "META", "AAPL", "AMZN", "MSFT", "GOOGL", "AMD", "AVGO", "MU", "PLTR", "NFLX", "COIN",
         "MSTR", "SMCI", "ORCL", "CRM", "UBER", "SHOP", "ARM", "HOOD", "APP", "CRWD", "MRVL", "INTC")
GAP = 0.02
ENTRY = "09:35"
OFF = 0.006


def utc(day: str, hhmm: str) -> str:
    """An ET wall time on `day` as a UTC timestamp."""
    h, m = map(int, hhmm.split(":"))
    t = datetime.fromisoformat(day).replace(hour=h, minute=m, tzinfo=ET).astimezone(timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def run(client) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    rows = []
    for und in NAMES:
        days = minutes_by_day(client, und, START, end)
        order = sorted(days)
        for i, day in enumerate(order[1:], 1):
            m, prev = days[day], days[order[i - 1]]
            last = at_or_before(prev, "15:59")
            if "09:30" not in m or not last or not at_or_before(m, "15:30"):
                continue
            o, pc = float(m["09:30"]["o"]), float(last["c"])
            gap = o / pc - 1
            if abs(gap) < GAP:
                continue
            b = at_or_before(m, ENTRY)
            if not b:
                continue
            spot = float(b["c"])
            kind = "C" if spot >= o else "P"
            try:
                heads = [n.get("headline", "") for n in client.news_range([und], utc(order[i - 1], "16:00"), utc(day, "09:30"))]
            except Exception:
                heads = []
            cs = []
            for status in ("inactive", "active"):
                try:
                    cs = client.option_contracts(und, status=status, type="call" if kind == "C" else "put",
                                                 expiration_date_gte=day,
                                                 expiration_date_lte=(date.fromisoformat(day) + timedelta(days=7)).isoformat(),
                                                 strike_price_gte=f"{spot * 0.97:.2f}", strike_price_lte=f"{spot * 1.03:.2f}")
                except Exception:
                    cs = []
                if cs:
                    break
            if not cs:
                continue
            exp = min(c["expiration_date"] for c in cs)
            sym = pick([c for c in cs if c["expiration_date"] == exp], kind, spot, OFF)
            if not sym:
                continue
            try:
                bars = client.option_bars([sym], f"{day}T13:00:00Z", f"{day}T21:00:00Z", timeframe="1Min")
            except Exception:
                continue
            mins = et_minutes(bars.get(sym, []))
            res = play(mins, ENTRY)
            if not res:
                continue
            nxt = None
            if exp > day:
                got = at_or_after(mins, ENTRY)
                try:
                    daily = client.option_bars([sym], (date.fromisoformat(day) + timedelta(days=1)).isoformat(),
                                               f"{exp}T23:00:00Z", timeframe="1Day").get(sym, [])
                except Exception:
                    daily = []
                if got and daily:
                    cost = float(got[1]["c"]) + max(float(got[1]["c"]) * SLIP, 0.01)
                    nxt = round(out_px(float(daily[0]["c"])) / cost - 1, 4)
            time.sleep(0.05)
            rows.append({"und": und, "day": day, "gap": round(gap, 4), "kind": kind, "with_gap": (kind == "C") == (gap > 0),
                         "news": len(heads), "headline": heads[-1][:120] if heads else "", "exp": exp,
                         "res": res, "next": nxt})
    return {"generated": date.today().isoformat(), "start": START, "end": end, "rows": rows}


def groups(rows: list[dict]) -> dict[str, list[dict]]:
    return {"跳空 2% 以上（全部）": rows,
            "　开盘前有新闻": [r for r in rows if r["news"] >= 1],
            "　开盘前新闻 3 条以上": [r for r in rows if r["news"] >= 3],
            "　没有新闻": [r for r in rows if r["news"] == 0],
            "　前 5 分钟顺着跳空方向": [r for r in rows if r["with_gap"]],
            "　前 5 分钟逆着跳空方向": [r for r in rows if not r["with_gap"]],
            "　有新闻且顺着跳空": [r for r in rows if r["news"] >= 1 and r["with_gap"]],
            "　跳空 4% 以上且有新闻": [r for r in rows if r["news"] >= 1 and abs(r["gap"]) >= 0.04]}


def cell(rs: list[dict], key: str) -> dict | None:
    xs = [r["res"][key] for r in rs]
    if not xs:
        return None
    return {"n": len(xs), "mean": round(statistics.mean(xs), 3), "x2": round(sum(x >= 1 for x in xs) / len(xs), 3),
            "x5": round(sum(x >= 4 for x in xs) / len(xs), 3)}


def daily_pick(rs: list[dict]) -> list[dict]:
    """One ticket a day for one sleeve: the largest gap of the day."""
    best: dict[str, dict] = {}
    for r in rs:
        if r["day"] not in best or abs(r["gap"]) > abs(best[r["day"]]["gap"]):
            best[r["day"]] = r
    return [best[d] for d in sorted(best)]


def table(rep: dict) -> dict:
    out = {}
    for g, rs in groups(rep["rows"]).items():
        one = daily_pick(rs)
        for x in EXITS:
            c = cell(rs, x)
            if c and one:
                g2, g20 = game([(r["day"], r["res"][x]) for r in one], 2), game([(r["day"], r["res"][x]) for r in one], 20)
                c |= {"days": len(one), "h2": g2["heroes"], "z2": g2["zeros"], "h20": g20["heroes"]}
            out[f"{g}|{x}"] = c
        nx = [r["next"] for r in rs if r.get("next") is not None]
        out[f"{g}|next"] = {"n": len(nx), "mean": round(statistics.mean(nx), 3),
                            "x2": round(sum(x >= 1 for x in nx) / len(nx), 3)} if nx else None
    return out


def markdown(rep: dict, t: dict) -> str:
    names = list(groups([]))
    out = [f"# 开盘跳空 + 开盘前新闻：09:35 顺着开盘方向买 {rep['generated']}", "",
           f"{rep['start']} 至 {rep['end']}，{len(NAMES)} 只活跃股：开盘离昨收 2% 以上的日子，看昨收到 09:30 之间有没有新闻；"
           "09:35 比开盘高买看涨、低买看跌，当天到期（没有就一周内最近到期），离现价 0.6%；按分钟收盘价判断卖出，买入多付 10%、卖出少拿 10%。"
           "每格「平均每 $1 / 收在 2 倍以上 / 收在 5 倍以上（笔数）」；账本玩法每天只做跳空最大的一只。只研究，不改交易。", ""]
    out += ["| 情况 | " + " | ".join(EXIT_ZH[x] for x in EXITS) + " | 非当天到期拿到第二天收盘 |", "|---|" + "---|" * (len(EXITS) + 1)]
    for g in names:
        cs = [t.get(f"{g}|{x}") for x in EXITS]
        nx = t.get(f"{g}|next")
        out.append(f"| {g} | " + " | ".join(f"{c['mean']:+.0%} / {c['x2']:.0%} / {c['x5']:.0%}（{c['n']}）" if c else "—" for c in cs)
                   + " | " + (f"{nx['mean']:+.0%} / {nx['x2']:.0%}（{nx['n']}）" if nx else "—") + " |")
    out += ["", "## 一个账本每天只做跳空最大的一只：到 2 倍 成功 / 归零，到 20 倍成功", "",
            "| 情况 | 天数 | " + " | ".join(EXIT_ZH[x] for x in EXITS) + " |", "|---|---|" + "---|" * len(EXITS)]
    for g in names:
        cs = [t.get(f"{g}|{x}") for x in EXITS]
        days = next((c["days"] for c in cs if c and "days" in c), 0)
        out.append(f"| {g} | {days} | " + " | ".join(f"{c['h2']} / {c['z2']}，{c['h20']}" if c and "h2" in c else "—" for c in cs) + " |")
    top = sorted(rep["rows"], key=lambda r: -r["res"]["take3_trail"])[:15]
    out += ["", "## 最好的 15 笔（3 倍卖一半、其余回撤卖）", "", "| 日期 | 股票 | 跳空 | 方向 | 新闻数 | 结果 | 最后一条开盘前新闻 |", "|---|---|---|---|---|---|---|"]
    out += [f"| {r['day']} | {r['und']} | {r['gap']:+.1%} | {'看涨' if r['kind'] == 'C' else '看跌'} | {r['news']} | "
            f"{r['res']['take3_trail']:+.0%} | {r['headline'].replace('|', '/')} |" for r in top]
    return "\n".join(out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca())
    t = table(rep)
    rep["table"] = t
    (root / "research" / f"{rep['generated']}-open-catalyst.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    (root / "research" / f"{rep['generated']}-open-catalyst.md").write_text(markdown(rep, t))
    print(markdown(rep, t))


if __name__ == "__main__":
    sys.exit(main())
