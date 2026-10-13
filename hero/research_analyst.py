"""Research: a pre-market analyst upgrade / downgrade -- buy the option the rating's way, and when to sell?

Owner, 2026-10-13: test the analyst-rating catalyst (claude-b's zoh/analyst.py found upgrades carried into the
session more often than an ordinary day; its real-price follow-up, REALOPT2.md, was roughly flat for upgrades).
Our own check, on real option minutes since 2024-02, on claude-b's 25 large caps:
  events:   Benzinga headlines "<firm> Upgrades|Downgrades <stock> to <rating>" (strict form), on at most two
            symbols, published between the prior close (16:00) and 09:30; one per stock-day;
  entries:  09:35 the rating's way (upgrade -> call, downgrade -> put); and "confirm": the first minute between
            09:45 and 12:00 with the stock 1%+ from its open the rating's way (none -> no trade);
  contract: the day's expiry, else the nearest within a week, the first strike 0.6% beyond the entry price;
  exits:    on minute closes -- 3x / 5x, trailing after 2x (40% giveback) or 1.5x (30%), half at 3x plus a
            trailing runner, hold to 15:30; and for a later expiry, held to the next close;
  costs:    10% over to get in, 10% under to get out.
Then one sleeve's game (one event a day, the first; half the ledger a ticket, a round ends below 10%).
Record only. Run: python -m hero.research_analyst
"""

from __future__ import annotations

import json
import re
import statistics
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from hero.research_0dte import ET, SLIP, at_or_after, at_or_before, et_minutes
from hero.research_0dte_names import minutes_by_day, pick
from hero.research_open_catalyst import utc
from hero.research_zdte_timing import EXITS, EXIT_ZH, game, out_px, play

START = "2024-02-01"
NAMES = ("NVDA", "AMD", "AVGO", "MU", "META", "MSFT", "GOOGL", "AMZN", "AAPL", "TSLA", "INTC", "ARM", "QCOM", "NFLX",
         "ORCL", "PLTR", "CRM", "ADBE", "ANET", "COIN", "WDC", "STX", "LITE", "SMCI", "UBER")
RATING = re.compile(r"^(.+?) (Upgrades|Downgrades) (.+?) to (.+?)(?:,|$)")
OFF = 0.006


def events(client, und: str, start: str, end: str) -> dict[str, dict]:
    """{day: {"up": bool, "headline": str}} for strict pre-market rating changes (the first one of the day)."""
    out: dict[str, dict] = {}
    for n in client.news_range([und], f"{start}T00:00:00Z", f"{end}T23:59:59Z"):
        m = RATING.match(n.get("headline") or "")
        if not m or len(n.get("symbols") or []) > 2:
            continue
        t = datetime.fromisoformat(n["created_at"].replace("Z", "+00:00")).astimezone(ET)
        hhmm = t.strftime("%H:%M")
        if hhmm < "09:30":
            day = t.date()
        elif hhmm >= "16:00":
            day = t.date() + timedelta(days=1)
            while day.weekday() > 4:
                day += timedelta(days=1)
        else:
            continue  # published in the session: not known at the open
        out.setdefault(day.isoformat(), {"up": m.group(2) == "Upgrades", "headline": n["headline"][:140]})
    return out


def confirm_minute(m: dict[str, dict], up: bool) -> str | None:
    o = float(m["09:30"]["o"])
    for k in sorted(m):
        if "09:45" <= k <= "12:00":
            r = float(m[k]["c"]) / o - 1
            if (r >= 0.01) if up else (r <= -0.01):
                return k
    return None


def run(client) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    rows = []
    for und in NAMES:
        try:
            evs = events(client, und, START, end)
        except Exception:
            evs = {}
        if not evs:
            continue
        days = minutes_by_day(client, und, START, end)
        order = sorted(days)
        for day, ev in sorted(evs.items()):
            m = days.get(day)
            if not m or "09:30" not in m or not at_or_before(m, "15:30") or day not in order or order.index(day) == 0:
                continue
            prev = at_or_before(days[order[order.index(day) - 1]], "15:59")
            o = float(m["09:30"]["o"])
            gap = o / float(prev["c"]) - 1 if prev else 0.0
            kind = "C" if ev["up"] else "P"
            entries = {"open": "09:35", "confirm": confirm_minute(m, ev["up"])}
            spots = {k: float(at_or_before(m, t)["c"]) for k, t in entries.items() if t and at_or_before(m, t)}
            if not spots:
                continue
            cs = []
            for status in ("inactive", "active"):
                try:
                    cs = client.option_contracts(und, status=status, type="call" if kind == "C" else "put",
                                                 expiration_date_gte=day,
                                                 expiration_date_lte=(date.fromisoformat(day) + timedelta(days=7)).isoformat(),
                                                 strike_price_gte=f"{min(spots.values()) * 0.97:.2f}",
                                                 strike_price_lte=f"{max(spots.values()) * 1.03:.2f}")
                except Exception:
                    cs = []
                if cs:
                    break
            if not cs:
                continue
            exp = min(c["expiration_date"] for c in cs)
            near = [c for c in cs if c["expiration_date"] == exp]
            syms = {k: pick(near, kind, s, OFF) for k, s in spots.items()}
            want = sorted({s for s in syms.values() if s})
            if not want:
                continue
            try:
                bars = client.option_bars(want, f"{day}T13:00:00Z", f"{day}T21:00:00Z", timeframe="1Min")
                daily = client.option_bars(want, (date.fromisoformat(day) + timedelta(days=1)).isoformat(), f"{exp}T23:00:00Z",
                                           timeframe="1Day") if exp > day else {}
            except Exception:
                continue
            time.sleep(0.05)
            res, nxt = {}, {}
            for k, sym in syms.items():
                mins = et_minutes(bars.get(sym, [])) if sym else {}
                got = play(mins, entries[k]) if mins else None
                if not got:
                    continue
                res[k] = got
                first = at_or_after(mins, entries[k])
                d = (daily or {}).get(sym) or []
                if first and d:
                    cost = float(first[1]["c"]) + max(float(first[1]["c"]) * SLIP, 0.01)
                    nxt[k] = round(out_px(float(d[0]["c"])) / cost - 1, 4)
            if res:
                rows.append({"und": und, "day": day, "up": ev["up"], "gap": round(gap, 4), "headline": ev["headline"],
                             "exp": exp, "confirm_at": entries["confirm"], "res": res, "next": nxt})
    return {"generated": date.today().isoformat(), "start": START, "end": end, "rows": rows}


GROUPS = (("上调", lambda r: r["up"]), ("下调", lambda r: not r["up"]),
          ("上调，2026 年", lambda r: r["up"] and r["day"] >= "2026-01-01"),
          ("上调，开盘高开 1% 以上", lambda r: r["up"] and r["gap"] >= 0.01),
          ("上调，开盘没怎么动（<1%）", lambda r: r["up"] and r["gap"] < 0.01))


def table(rep: dict) -> dict:
    out = {}
    for label, keep in GROUPS:
        rs = [r for r in rep["rows"] if keep(r)]
        for entry in ("open", "confirm"):
            sub = [r for r in rs if entry in r["res"]]
            one = {}
            for r in sub:
                one.setdefault(r["day"], r)
            for x in EXITS:
                xs = [r["res"][entry][x] for r in sub]
                if not xs:
                    out[f"{label}|{entry}|{x}"] = None
                    continue
                seq = [(d, r["res"][entry][x]) for d, r in sorted(one.items())]
                g2, g20 = game(seq, 2), game(seq, 20)
                out[f"{label}|{entry}|{x}"] = {"n": len(xs), "mean": round(statistics.mean(xs), 3),
                                               "x2": round(sum(v >= 1 for v in xs) / len(xs), 3),
                                               "h2": g2["heroes"], "z2": g2["zeros"], "h20": g20["heroes"]}
            nx = [r["next"][entry] for r in sub if entry in (r.get("next") or {})]
            out[f"{label}|{entry}|next"] = {"n": len(nx), "mean": round(statistics.mean(nx), 3),
                                            "x2": round(sum(v >= 1 for v in nx) / len(nx), 3)} if nx else None
    return out


def markdown(rep: dict, t: dict) -> str:
    rows = rep["rows"]
    out = [f"# 开盘前分析师上调 / 下调：顺着评级买期权 {rep['generated']}", "",
           f"{rep['start']} 至 {rep['end']}，{len(NAMES)} 只大型股（claude-b 的名单），严格标题「某机构 Upgrades / Downgrades 某股 to 某评级」，"
           f"昨收到 09:30 之间发布，共 {len(rows)} 次（上调 {sum(r['up'] for r in rows)}、下调 {sum(not r['up'] for r in rows)}）。"
           "两种进场：09:35 直接买；「确认」= 09:45 到 12:00 之间股价离开盘 1% 以上朝评级方向走时才买。当天到期（没有就一周内最近到期）、离现价 0.6%；"
           "按分钟收盘价判断卖出，买入多付 10%、卖出少拿 10%。每格「平均每 $1 / 收在 2 倍以上 / 账本到 2 倍 成功·归零·到 20 倍」。只研究，不改交易。", ""]
    for entry, zh in (("open", "09:35 直接买"), ("confirm", "等确认再买")):
        out += [f"## {zh}", "", "| 情况 | 笔数 | " + " | ".join(EXIT_ZH[x] for x in EXITS) + " | 非当天到期拿到第二天收盘 |",
                "|---|---|" + "---|" * (len(EXITS) + 1)]
        for label, _ in GROUPS:
            cs = [t.get(f"{label}|{entry}|{x}") for x in EXITS]
            n = next((c["n"] for c in cs if c), 0)
            nx = t.get(f"{label}|{entry}|next")
            out.append(f"| {label} | {n} | " + " | ".join(f"{c['mean']:+.0%} / {c['x2']:.0%} / {c['h2']}·{c['z2']}·{c['h20']}" if c else "—"
                                                       for c in cs)
                       + " | " + (f"{nx['mean']:+.0%} / {nx['x2']:.0%}（{nx['n']}）" if nx else "—") + " |")
        out.append("")
    best = sorted((r for r in rows if "open" in r["res"]), key=lambda r: -r["res"]["open"]["take3_trail"])[:12]
    out += ["## 09:35 买、3 倍卖一半其余回撤卖：最好的 12 笔", "", "| 日期 | 股票 | 评级 | 开盘跳空 | 结果 | 标题 |", "|---|---|---|---|---|---|"]
    out += [f"| {r['day']} | {r['und']} | {'上调' if r['up'] else '下调'} | {r['gap']:+.1%} | {r['res']['open']['take3_trail']:+.0%} | "
            f"{r['headline'].replace('|', '/')} |" for r in best]
    return "\n".join(out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca())
    t = table(rep)
    rep["table"] = t
    (root / "research" / f"{rep['generated']}-analyst.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    (root / "research" / f"{rep['generated']}-analyst.md").write_text(markdown(rep, t))
    print(markdown(rep, t))


if __name__ == "__main__":
    sys.exit(main())
