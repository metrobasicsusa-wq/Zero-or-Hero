"""The sector-bounce day: the leader's same-day call (owner, 10-26: 「AVGO 今天就有很好的机会」, 「330 CALL 早上好便宜」,
「今天的」 -- AVGO's 330 call expiring that Monday went from $0.18 to $2.36 by noon, x13, on the first bounce after
SMH's 6.7% Thursday).

Every rule we have enters at 15:50 or at the open on the name's own news; nothing looks at 10:00 for "the sector was
flushed and is bouncing". This asks whether that day is worth the leader's call expiring today (or this week, before
the daily expiries): SMH's prior one-to-three-session drop of 5%+ (`flush`), its 10:00 price 1%+ or 2%+ above its own
open (`m10`), AVGO / NVDA / AMD / MU bought at 10:00 with the call 0.6% and 1% out on the nearest expiry on or after
the day; sold at 15:50, or at 2x / 3x first. Costs 5% a side (and 10%). Controls: the same bounce without a flush,
and every tenth session. Then the robustness checks, a per-name split, same-day versus weekly expiries, and the hero
game. A time budget keeps a partial run committable. Research only."""
from __future__ import annotations

import os
import statistics
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from hero.research_event_turn import Pace
from hero.research_hero_game import game, interval
from hero.research_ideas import report, save

ROOT = Path(__file__).resolve().parent.parent
NAMES = ("AVGO", "NVDA", "AMD", "MU")
SECTOR = "SMH"
START = "2024-02-01"
FLUSH = 0.05
OFFS = {"otm06": 0.006, "otm1": 0.01}
EXITS = {"d0": "15:50 卖", "take2": "2 倍止盈，否则 15:50", "take3": "3 倍止盈，否则 15:50"}
LABEL = {"bounce1": "板块被砸后的反弹日（10:00 离开盘 ≥1%）", "bounce2": "板块被砸后的反弹日（≥2%）",
         "plain1": "对照：没被砸的反弹日（≥1%）", "every10": "对照：每第 10 天随便买"}
BUDGET_MIN = float(os.environ.get("RESEARCH_BUDGET_MIN", "115"))
ET = ZoneInfo("America/New_York")


def et(t: str) -> str:
    """Bar time (UTC ISO) -> ET HH:MM."""
    return datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(ET).strftime("%H:%M")


def at_ten(bars: list[dict]) -> dict[str, float]:
    """day -> the price at 10:00 ET (the close of the last bar before 10:00)."""
    out: dict[str, float] = {}
    for b in bars:
        hhmm = et(b["t"])
        if "09:30" <= hhmm < "10:00":
            out[b["t"][:10]] = float(b["c"])
    return out


def signals(days: list[str], opens: list[float], closes: list[float], ten: dict[str, float]) -> list[dict]:
    """Every session with its flush / bounce reading, each tagged with the groups it belongs to (a day can be in none)."""
    out = []
    for i in range(4, len(days)):
        p10 = ten.get(days[i])
        if not p10:
            continue
        drop = min(closes[i - 1] / closes[i - 1 - k] - 1 for k in (1, 2, 3))
        m10 = p10 / opens[i] - 1
        gap = opens[i] / closes[i - 1] - 1
        groups = []
        if drop <= -FLUSH and m10 >= 0.01:
            groups.append("bounce1")
        if drop <= -FLUSH and m10 >= 0.02:
            groups.append("bounce2")
        if drop > -FLUSH and m10 >= 0.01:
            groups.append("plain1")
        if i % 10 == 0:
            groups.append("every10")
        if groups:
            out.append({"day": days[i], "i": i, "drop": round(drop, 4), "m10": round(m10, 4), "gap": round(gap, 4),
                        "s_close": round(closes[i] / opens[i] - 1, 4), "groups": groups})
    return out


def exits(entry: float, bars: list[dict], cost: float) -> dict[str, float]:
    """Per $1 from the 10:00 bar: sold at the 15:50 bar's close, or the first bar whose high reaches 2x / 3x the paid
    price (then at that multiple), all less `cost` a side."""
    paid = entry * (1 + cost)
    later = [b for b in bars if et(b["t"]) >= "10:00"]
    if not later:
        return {}
    last = next((b for b in reversed(later) if et(b["t"]) <= "15:50"), later[-1])
    d0 = float(last["c"]) * (1 - cost) / paid - 1
    out = {"d0": round(d0, 4)}
    for k, mult in (("take2", 2.0), ("take3", 3.0)):
        hit = next((b for b in later if float(b["h"]) >= paid * mult), None)
        out[k] = round(mult * (1 - cost) - 1, 4) if hit else out["d0"]
    return out


def legs(client, und: str, day: str, spot: float, pace: Pace) -> dict[str, dict]:
    d0 = date.fromisoformat(day)
    cs = []
    for status in ("inactive", "active"):
        try:
            cs += client.option_contracts(und, status=status, type="call", expiration_date_gte=day,
                                          expiration_date_lte=(d0 + timedelta(days=7)).isoformat(),
                                          strike_price_gte=f"{spot * 0.99:.2f}", strike_price_lte=f"{spot * 1.06:.2f}")
        except Exception:
            pass
        pace.wait()
    if not cs:
        return {}
    exp = min(c["expiration_date"] for c in cs)
    ks = sorted((float(c["strike_price"]), c["symbol"]) for c in cs if c["expiration_date"] == exp)
    want = {name: next((k for k in ks if k[0] >= spot * (1 + off)), None) for name, off in OFFS.items()}
    syms = sorted({k[1] for k in want.values() if k})
    if not syms:
        return {}
    try:
        bars = client.option_bars(syms, f"{day}T13:00:00Z", f"{day}T21:00:00Z", timeframe="1Min")
    except Exception:
        return {}
    pace.wait()
    out = {}
    for name, k in want.items():
        if not k:
            continue
        bs = sorted(bars.get(k[1], []), key=lambda b: b["t"])
        ten = [b for b in bs if "09:45" <= et(b["t"]) <= "10:05"]
        if not ten:
            continue
        entry = float(ten[-1]["c"])
        if entry <= 0:
            continue
        out[name] = {"option": k[1], "expiry": exp, "dte": (date.fromisoformat(exp) - d0).days, "entry": entry,
                     "res": exits(entry, bs, 0.05), "res10": exits(entry, bs, 0.10)}
    return out


def run(client, budget_min: float = BUDGET_MIN) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    t0 = time.monotonic()
    pace = Pace(client)
    split = client.daily_bars([SECTOR] + list(NAMES), "2023-12-01", adjustment="split", feed="sip")
    raw = client.daily_bars([SECTOR] + list(NAMES), "2023-12-01", adjustment="raw", feed="sip")
    sbars = [b for b in split.get(SECTOR, []) if b["t"][:10] <= end]
    days = [b["t"][:10] for b in sbars]
    ten: dict[str, float] = {}
    for y in range(2024, date.today().year + 1):
        for m0 in (1, 4, 7, 10):
            s, e = f"{y}-{m0:02d}-01", (date(y + (m0 == 10), (m0 + 3) % 12 or 12, 1) - timedelta(days=1)).isoformat()
            if s > end:
                break
            try:
                ten.update(at_ten(client.stock_bars([SECTOR], f"{s}T13:00:00Z", f"{min(e, end)}T15:00:00Z", timeframe="5Min", feed="sip").get(SECTOR, [])))
            except Exception:
                pass
            pace.wait()
    ev = [e for e in signals(days, [float(b["o"]) for b in sbars], [float(b["c"]) for b in sbars], ten) if e["day"] >= START]
    rows, missing, done = [], 0, 0
    for e in ev:
        if (time.monotonic() - t0) / 60 > budget_min:
            break
        day = e["day"]
        try:
            mins = client.stock_bars(list(NAMES), f"{day}T13:00:00Z", f"{day}T21:00:00Z", timeframe="5Min", feed="sip")
        except Exception:
            mins = {}
        pace.wait()
        for und in NAMES:
            bs = sorted(mins.get(und, []), key=lambda b: b["t"])
            t10 = [b for b in bs if "09:30" <= et(b["t"]) < "10:00"]
            opn = next((float(b["o"]) for b in bs if et(b["t"]) >= "09:30"), None)
            if not t10 or not opn:
                missing += 1
                continue
            spot = float(t10[-1]["c"])
            last = next((float(b["c"]) for b in reversed(bs) if et(b["t"]) <= "15:50"), spot)
            got = legs(client, und, day, spot, pace)
            if not got:
                missing += 1
                rows.append({**e, "und": und, "u10": round(spot / opn - 1, 4), "u_close": round(last / opn - 1, 4), "leg": None, "res": {}})
            for name, leg in got.items():
                rows.append({**e, "und": und, "u10": round(spot / opn - 1, 4), "u_close": round(last / opn - 1, 4), "leg": name, **leg})
        done += 1
    return {"start": START, "end": end, "names": list(NAMES), "sector": SECTOR, "events": len(ev), "done": done,
            "partial": done < len(ev), "rows": rows, "missing": missing, "minutes": round((time.monotonic() - t0) / 60, 1)}


def groups(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for g, label in LABEL.items():
        for leg, lname in (("otm06", "价外 0.6%"), ("otm1", "价外 1%")):
            out[f"{label} · {lname}"] = [r for r in rows if r.get("leg") == leg and g in r["groups"]]
    for und in NAMES:
        out[f"　被砸后反弹（≥1%）· {und} · 价外 0.6%"] = [r for r in rows if r.get("leg") == "otm06" and "bounce1" in r["groups"] and r["und"] == und]
    out["　被砸后反弹（≥1%）· 当天到期 · 价外 0.6%"] = [r for r in rows if r.get("leg") == "otm06" and "bounce1" in r["groups"] and r.get("dte") == 0]
    out["　被砸后反弹（≥1%）· 本周到期 · 价外 0.6%"] = [r for r in rows if r.get("leg") == "otm06" and "bounce1" in r["groups"] and (r.get("dte") or 0) > 0]
    out["　被砸后反弹（≥1%）· 那只自己 10:00 也涨 ≥1% · 价外 0.6%"] = [r for r in rows if r.get("leg") == "otm06" and "bounce1" in r["groups"] and (r.get("u10") or 0) >= 0.01]
    return {k: v for k, v in out.items() if v}


def series(rows: list[dict], g: str, leg: str, exit_: str, key: str = "res") -> list[tuple[str, float]]:
    """One pick a day: the name whose own 10:00 move is the largest (the leader of the bounce)."""
    by: dict[str, dict] = {}
    for r in rows:
        if r.get("leg") == leg and g in r["groups"] and (r.get(key) or {}).get(exit_) is not None:
            if r["day"] not in by or (r.get("u10") or 0) > (by[r["day"]].get("u10") or 0):
                by[r["day"]] = r
    return [(d, by[d][key][exit_]) for d in sorted(by)]


def hero_table(rows: list[dict]) -> tuple[dict, str]:
    out = {}
    lines = ["", "## hero 游戏：每天一笔（反弹里 10:00 涨得最多的那只），按日期重放成轮，每轮到 20 倍", "",
             "| 情况 · 腿 · 出场 · 成本 | 笔数 | 平均 | 押一半：到 20 倍 / 轮（区间） | 每个 hero 的成本 | 押满：到 20 倍 / 轮（区间） | 每个 hero 的成本 |",
             "|---|---|---|---|---|---|---|"]
    for g, label in LABEL.items():
        for leg, lname in (("otm06", "价外 0.6%"), ("otm1", "价外 1%")):
            for ex in ("d0", "take3"):
                for key, cost in (("res", "5%"), ("res10", "10%")):
                    xs = [x for _, x in series(rows, g, leg, ex, key)]
                    if len(xs) < 8:
                        continue
                    g5, g10 = game(xs, 0.5), game(xs, 1.0)
                    c5, c10 = interval(g5["heroes"], g5["rounds"]) or (0, 0), interval(g10["heroes"], g10["rounds"]) or (0, 0)
                    out[f"{g}|{leg}|{ex}|{cost}"] = {"n": len(xs), "mean": statistics.mean(xs), "half": g5, "full": g10}
                    lines.append(f"| {label} · {lname} · {ex} · {cost} | {len(xs)} | {statistics.mean(xs):+.0%} | "
                                 f"{g5['heroes']}/{g5['rounds']}（{c5[0]:.0%}–{c5[1]:.0%}） | {('$%s' % format(g5['cost_per_hero'], ',.0f')) if g5['cost_per_hero'] else '—'} | "
                                 f"{g10['heroes']}/{g10['rounds']}（{c10[0]:.0%}–{c10[1]:.0%}） | {('$%s' % format(g10['cost_per_hero'], ',.0f')) if g10['cost_per_hero'] else '—'} |")
    return out, "\n".join(lines) + "\n"


def multiples(rows: list[dict]) -> str:
    lines = ["", "## 倍数分布（价外 0.6%、15:50 卖、5% 成本）：多少笔翻了 2 倍、5 倍、10 倍", "",
             "| 情况 | 笔数 | 中位数 | ≥2 倍 | ≥5 倍 | ≥10 倍 | 归零（-90% 以下） |", "|---|---|---|---|---|---|---|"]
    for g, label in LABEL.items():
        xs = [r["res"]["d0"] for r in rows if r.get("leg") == "otm06" and g in r["groups"] and "d0" in (r.get("res") or {})]
        if not xs:
            continue
        lines.append(f"| {label} | {len(xs)} | {statistics.median(xs):+.0%} | {sum(x >= 1 for x in xs)} | {sum(x >= 4 for x in xs)} | "
                     f"{sum(x >= 9 for x in xs)} | {sum(x <= -0.9 for x in xs)} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    rep = run(Alpaca())
    gs = groups(rep["rows"])
    table, md = report(f"板块反弹日的龙头末日单 {date.today().isoformat()}",
                       f"{rep['start']} 至 {rep['end']}，SMH 前 1–3 个交易日跌 {FLUSH:.0%} 以上、10:00 离开盘涨 1% / 2% 以上的日子（共 {rep['events']} 个候选日，"
                       f"跑完 {rep['done']} 个{'，预算到了没跑完' if rep['partial'] else ''}），10:00 买 AVGO / NVDA / AMD / MU 当天或本周到期、"
                       f"离现价 0.6% / 1% 的看涨；对照是没被砸的反弹日和每第 10 天。没有期权分钟线的 {rep['missing']} 笔跳过。",
                       gs, EXITS, rep["end"])
    rep["table"] = table
    rep["hero"], hero_md = hero_table(rep["rows"])
    md += multiples(rep["rows"]) + hero_md
    md += ("\n## 读法\n\n- 先看对照组：被砸后的反弹日要比「没被砸的反弹日」和「随便买」都好才算时机。\n"
           "- 当天到期的只有日权上市以后的日子，笔数少；本周到期的是历史代理，倍数会小。\n"
           "- 轮数少，区间比点估计重要。只研究，不改交易。\n")
    save("bounce", rep, md)


if __name__ == "__main__":
    sys.exit(main())
