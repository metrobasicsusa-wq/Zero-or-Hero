"""Research: buy calls after a sharp drop in the first minutes ("急跌再拉"), as AMD did on 2026-10-06.

On 2026-10-06 AMD opened +2.6%, fell 3.2% from the open by 9:36 (below the prior close), then rallied to
+4.2% by 11:11. A gap rule never sees that day. Here, on the all-exchange SIP tape since 2024-02:
  flush:  by 10:00 the stock has traded 2% (or 3%) below its opening price;
  entry:  "10:00" = buy at 10:00 whenever that happened; "confirm" = wait until a minute closes 1% above
          the running low (by 11:00), buy the next minute -- the "再拉" part, decided only from the past;
  option: nearest expiry within 4 days, calls 1% / 2% above the price at entry, bought at the minute
          close + 10%; exits as in research_gap (trail after 2x, a 3x take, or sold at 15:50);
  groups: the user's focus (research_gap.FOCUS: chips and the Magnificent 7, every flush kept up to a cap),
          split by how they opened (gap up 1%+ like AMD that day / flat / gap down 1%+) and by the
          "AMD-like" pre-open features (63-day trend and volatility in the top quarter); other stocks
          from the monthly point-in-time pools (sampled); control: focus names on days without a flush,
          bought at 10:00.
Then the $500 game (half the account each trade). Record only. Run: python -m hero.research_flush
"""

from __future__ import annotations

import json
import random
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from hero import research_0dte as z
from hero.research_gap import EXITS, FOCUS, features, game, simulate

START = "2024-02-01"
POOL = 100
FEED = "sip"
DROPS = (0.02, 0.03)
BOUNCE = 0.01
OTM = (0.01, 0.02)
BY, LAST = "10:00", "11:00"
CAPS = {"focus": 2500, "other_days": 2500, "control": 400}  # keep a run inside the workflow's time limit
BUDGET = 120 * 60  # seconds for the option part; anything left is reported as not tested


def regular(sm: dict[str, dict]) -> dict[str, dict]:
    """The morning only (everything the study reads is by 11:00), slimmed, to keep thousands of days in memory."""
    return {k: {"o": v["o"], "l": v["l"], "c": v["c"]} for k, v in sm.items() if "09:30" <= k <= "11:05"}


def next_minute(hhmm: str) -> str:
    h, m = map(int, hhmm.split(":"))
    m += 1
    return f"{h + m // 60:02d}:{m % 60:02d}"


def flush(sm: dict[str, dict], drop: float) -> dict | None:
    """Did the stock trade `drop` below its open by 10:00? Entries known at the time: 10:00, and the minute
    after the first close 1% above the running low (by 11:00)."""
    keys = sorted(sm)
    if not keys or keys[0] > "09:35":
        return None
    o = float(sm[keys[0]]["o"])
    low, low_at, hit, confirm = float("inf"), None, None, None
    for k in keys:
        if k > LAST:
            break
        lo, c = float(sm[k]["l"]), float(sm[k]["c"])
        if lo < low:
            low, low_at = lo, k
        if hit is None and k <= BY and low <= o * (1 - drop):
            hit = k
        if hit and k > hit and c >= low * (1 + BOUNCE):
            confirm = next_minute(k)
            break
    if hit is None:
        return None
    return {"drop": round(1 - low / o, 4), "low_at": low_at, "hit": hit, "entries": {"10:00": BY, "confirm": confirm}}


def first_drop(sm: dict[str, dict]) -> float | None:
    keys = sorted(k for k in sm if k <= BY)
    if not keys:
        return None
    o = float(sm[keys[0]]["o"])
    return 1 - min(float(sm[k]["l"]) for k in keys) / o


def options(client, s: str, d: str, sm: dict[str, dict], entries: dict[str, str | None]) -> list[dict]:
    """claude-b-style calls for each entry time; one contract list and one bar request per stock-day."""
    spots = {}
    for name, t in entries.items():
        b = z.at_or_after(sm, t) if t else None
        if b:
            spots[name] = (b[0], float(b[1]["c"]))
    if not spots:
        return []
    lo, hi = min(p for _, p in spots.values()), max(p for _, p in spots.values())
    lim = (date.fromisoformat(d) + timedelta(days=4)).isoformat()
    cs = []
    for status in ("inactive", "active"):
        try:
            cs = client.option_contracts(s, status=status, type="call", expiration_date_gte=d, expiration_date_lte=lim,
                                         strike_price_gte=f"{lo * 1.003:.2f}", strike_price_lte=f"{hi * 1.05:.2f}")
        except Exception:
            cs = []
        if cs:
            break
    if not cs:
        return []
    exp = min(c["expiration_date"] for c in cs)
    by_k = {float(c["strike_price"]): c["symbol"] for c in cs if c["expiration_date"] == exp}
    picks = {}
    for name, (t, spot) in spots.items():
        for m in OTM:
            k = next((k for k in sorted(by_k) if k >= spot * (1 + m)), None)
            if k:
                picks[(name, m)] = (t, by_k[k])
    if not picks:
        return []
    try:
        ob = client.option_bars(sorted({v[1] for v in picks.values()}), f"{d}T13:00:00Z", f"{d}T21:00:00Z", timeframe="1Min")
    except Exception:
        return []
    out = []
    for (name, m), (t, sym) in picks.items():
        r = simulate(z.et_minutes(ob.get(sym, [])), entry=t)
        if r:
            out.append({"rule": name, "otm": m, "entry": t, "same_day": exp == d, **r})
    return out


def run(client) -> dict:
    from hero import research_universe as ru
    t0 = time.time()
    end = (date.today() - timedelta(days=1)).isoformat()
    stocks = ru.candidates(client)
    syms = sorted((set(stocks) | FOCUS | {"SPY"}) - {"GOOG"})
    bars: dict[str, list[dict]] = {}
    for i in range(0, len(syms), 200):
        bars.update(client.daily_bars(syms[i:i + 200], "2023-01-01", feed=FEED))
    dates, closes, dvol = ru.align(bars, "SPY")
    have = {s: k for s, k in stocks.items() if s in bars}
    for s in set(have) | (FOCUS & set(bars)):
        closes.setdefault(s, [None] * len(dates))
        dvol.setdefault(s, [0.0] * len(dates))
    firsts = {s: next((i for i, x in enumerate(closes[s]) if x is not None), None) for s in have}
    start = next(i for i, d in enumerate(dates) if d >= START)
    stop = max(i for i, d in enumerate(dates) if d <= end)
    pools = ru.monthly_pools(dates, closes, dvol, firsts, have, POOL, start)
    keys = sorted(pools)
    opens = {s: {b["t"][:10]: float(b["o"]) for b in bs} for s, bs in bars.items()}
    idx = {d: i for i, d in enumerate(dates)}

    def info(s: str, d: str) -> dict | None:
        t = idx[d]
        prev, o = closes[s][t - 1], opens.get(s, {}).get(d)
        if not prev or not o:
            return None
        return {"gap": round(o / prev - 1, 4), **features(closes[s], opens.get(s, {}), dates, t)}

    # 1) chips / Mag 7: every day, from month-long minute requests (keep only what the study needs)
    focus = sorted(FOCUS & set(bars) - {"GOOG"})
    found, quiet = [], []
    months = sorted({d[:7] for d in dates[start: stop + 1]})
    for s in focus:
        for mo in months:
            a = f"{mo}-01"
            b = (date.fromisoformat(a) + timedelta(days=32)).replace(day=1).isoformat()
            try:
                raw = client.stock_bars([s], f"{a}T13:00:00Z", f"{b}T00:00:00Z", feed=FEED).get(s, [])
            except Exception:
                continue
            by_day: dict[str, list[dict]] = {}
            for x in raw:
                by_day.setdefault(x["t"][:10], []).append(x)
            for d, xs in by_day.items():
                if d not in idx or not (START <= d <= end):
                    continue
                sm = regular(z.et_minutes(xs))
                f = first_drop(sm)
                if f is None:
                    continue
                if f >= min(DROPS):
                    found.append((d, s, sm))
                elif f < 0.01:
                    quiet.append((d, s, sm))
    rng = random.Random(11)
    if len(found) > CAPS["focus"]:
        found = rng.sample(found, CAPS["focus"])
    quiet = rng.sample(quiet, min(len(quiet), CAPS["control"]))
    # 2) other stocks: random pool stock-days, one day of minutes each, keep the flushes
    pairs = []
    for t in range(start, stop + 1):
        pool = pools[max(k for k in keys if k <= t)]
        pairs += [(dates[t], s) for s in pool if s not in FOCUS]
    pairs = rng.sample(pairs, min(len(pairs), CAPS["other_days"]))
    others = []
    for d, s in pairs:
        try:
            sm = regular(z.et_minutes(client.stock_bars([s], f"{d}T13:00:00Z", f"{d}T21:00:00Z", feed=FEED).get(s, [])))
        except Exception:
            continue
        f = first_drop(sm)
        if f is not None and f >= min(DROPS):
            others.append((d, s, sm))
    flush_rate = round(len(others) / len(pairs), 3) if pairs else None

    rows, untested = [], 0
    jobs = [("focus", x) for x in found] + [("other", x) for x in others] + [("control", x) for x in quiet]
    rng.shuffle(jobs)  # if time runs out, what is left out is random, not the late dates
    for kind, (d, s, sm) in jobs:
        if time.time() - t0 > BUDGET:
            untested += 1
            continue
        base = info(s, d)
        if base is None:
            continue
        if kind == "control":
            for r in options(client, s, d, sm, {"10:00": BY}):
                rows.append({"kind": kind, "day": d, "symbol": s, "thr": 0.0, "drop": round(first_drop(sm) or 0, 4), **base, **r})
            continue
        for thr in DROPS:
            fl = flush(sm, thr)
            if not fl:
                continue
            for r in options(client, s, d, sm, fl["entries"]):
                rows.append({"kind": kind, "day": d, "symbol": s, "thr": thr, "drop": fl["drop"], "low_at": fl["low_at"],
                             **base, **r})
    return {"generated": date.today().isoformat(), "start": START, "end": end, "feed": FEED, "pool": POOL,
            "events": {"focus": len(found), "other": len(others), "control": len(quiet)},
            "other_flush_rate": flush_rate, "untested": untested, "rows": rows}


def cut(rows: list[dict], key: str, q: float) -> float:
    xs = sorted(r[key] for r in rows if r["kind"] == "focus" and r.get(key) is not None)
    return xs[int(q * (len(xs) - 1))] if xs else float("inf")


def table(rows: list[dict]) -> dict:
    hi_mom, hi_vol = cut(rows, "mom63", 0.75), cut(rows, "vol63", 0.75)
    amd = lambda r: (r.get("mom63") or -9) >= hi_mom and (r.get("vol63") or 0) >= hi_vol
    fo = lambda r: r["kind"] == "focus"
    groups = {
        "半导体 / Mag 7 急跌": fo,
        "　高开 1%+ 后急跌（AMD 10-06 这种）": lambda r: fo(r) and r["gap"] >= 0.01,
        "　平开后急跌": lambda r: fo(r) and abs(r["gap"]) < 0.01,
        "　低开 1%+ 后再急跌": lambda r: fo(r) and r["gap"] <= -0.01,
        "　像 AMD（强势 + 高波动）": lambda r: fo(r) and amd(r),
        "　像 AMD + 高开后急跌": lambda r: fo(r) and amd(r) and r["gap"] >= 0.01,
        "　2026 年以来": lambda r: fo(r) and r["day"] >= "2026-01-01",
        "　当天到期": lambda r: fo(r) and r["same_day"],
        "其他股票急跌": lambda r: r["kind"] == "other",
        "对照：半导体 / Mag 7 没急跌的日子": lambda r: r["kind"] == "control",
    }
    out = {}
    for g, f in groups.items():
        for thr in (0.0,) + DROPS:
            for rule in ("10:00", "confirm"):
                for m in OTM:
                    sub = [r for r in rows if f(r) and r["thr"] == thr and r["rule"] == rule and r["otm"] == m]
                    if not sub:
                        continue
                    row = {"n": len(sub), "symbols": len({r["symbol"] for r in sub}),
                           "cost": round(statistics.median(r["cost"] for r in sub), 2),
                           "x2": round(sum(r["best"] >= 2 for r in sub) / len(sub), 3)}
                    for k in EXITS:
                        xs = [r[k] for r in sub]
                        row[k] = {"mean": round(statistics.mean(xs), 3), "median": round(statistics.median(xs), 3),
                                  "win": round(sum(x > 0 for x in xs) / len(xs), 3)}
                    row["game_trail"] = game(sub, "trail")
                    out[f"{g}|{thr}|{rule}|{m}"] = row
    out["_cuts"] = {"mom63_top25": hi_mom, "vol63_top25": hi_vol}
    return out


def markdown(rep: dict, t: dict) -> str:
    names = {"trail": "翻倍后回撤 40% 卖", "tp3": "3 倍止盈", "close": "15:50 卖"}
    rules = {"10:00": "10:00 买", "confirm": "从低点反弹 1% 后买"}
    c = t.get("_cuts", {})
    out = [f"# 开盘后急跌再拉 买看涨 {rep['generated']}", "",
           f"{rep['start']} 至 {rep['end']}，行情 {rep['feed']}（全部交易所合并）。急跌 = 10:00 前从开盘价跌了 2%（或 3%）。"
           "两种买点（都只用当时已知的）：10:00 买；或等某分钟收盘比当时的最低点高 1%（11:00 前），下一分钟买。"
           "买最近到期（4 天内）、比买入时价格高 1% / 2% 的看涨，买入多付 10%、卖出少拿 10%，用分钟收盘价。"
           f"事件数：{rep['events']}（其他股票是从每月股票池随机抽的股票日，其中急跌占 {rep['other_flush_rate']}）。"
           f"时间不够没测的：{rep['untested']}。玩法：$500，每笔押一半，低于 $50 归零、到 $10,000 成功。只研究，不改交易。", "",
           "| 组别 | 急跌 | 买点 | 价外 | 笔数 | 股票数 | 成本中位数 | 到过 2 倍 | "
           + " | ".join(f"{names[k]}：平均 / 中位数 / 胜率" for k in EXITS) + " | $500 半仓（回撤卖）：成功 / 归零 |",
           "|---|---|---|---|---|---|---|---|" + "---|" * len(EXITS) + "---|"]
    for key, r in t.items():
        if key.startswith("_"):
            continue
        g, thr, rule, m = key.split("|")
        cells = " | ".join(f"{r[k]['mean']:+.0%} / {r[k]['median']:+.0%} / {r[k]['win']:.0%}" for k in EXITS)
        out.append(f"| {g} | {float(thr):.0%} | {rules[rule]} | {float(m):.0%} | {r['n']} | {r['symbols']} | ${r['cost']:.2f} | "
                   f"{r['x2']:.0%} | {cells} | {r['game_trail']['heroes']} / {r['game_trail']['zeros']} |")
    out += ["", f"「像 AMD」= 当天开盘前 63 天涨幅和年化波动都在半导体 / Mag 7 急跌事件的前 25%（涨幅 ≥ {c.get('mom63_top25', 0):+.0%}、"
            f"波动 ≥ {c.get('vol63_top25', 0):.0%}）。「半导体 / Mag 7」= " + "、".join(sorted(FOCUS - {"GOOG"}))
            + "。对照组「急跌」列为 0%：开盘到 10:00 跌不到 1% 的日子，10:00 买。"]
    return "\n".join(out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca())
    t = table(rep["rows"])
    rep["table"] = t
    (root / "research" / f"{rep['generated']}-flush.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    (root / "research" / f"{rep['generated']}-flush.md").write_text(markdown(rep, t))
    print(markdown(rep, t))


if __name__ == "__main__":
    sys.exit(main())
