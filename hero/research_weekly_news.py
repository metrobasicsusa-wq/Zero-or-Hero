"""Research: pick each week's names from their own news, then buy that week's options (the $500 account).

Not a fixed list: each week, from the month's point-in-time pool (the 100 listed companies with the most
dollar volume over the prior 63 days, as in research_universe), read every headline from Friday 09:30 to
Monday 09:25 ET (known before Monday's open) and score each name:
  heat:      articles in that window vs the name's own median over the prior 8 weeks (unusual attention);
  sentiment: the mean word-list score of its headlines (hero.market.score_text), its sign = the direction;
  wind:      the name's 20-day trend before Monday (up or down), and SPY's;
then pick 3 names a week, in four ways:
  hot:           the 3 with the most unusual attention (heat >= 2, at least 3 articles), direction = sentiment;
  sentiment:     the 3 with the strongest sentiment (at least 3 articles), direction = its sign;
  hot + wind:    "hot", keeping only names whose sentiment agrees with their own 20-day trend and SPY's;
  control:       3 random pool names, direction = their own 20-day trend (no news at all);
and buy, Monday 10:00, the week's last expiry, the nearest listed strike at least 1% beyond the price,
pay 10% over; a 2x / 3x resting take, else sold at Thursday's (the day before expiry) 15:50 close less 10%.
The $500 game: half the account each week, split evenly over that week's picks; a round ends below $50,
at $1,000 or at $10,000. Record only. Run: python -m hero.research_weekly_news
"""

from __future__ import annotations

import json
import random
import statistics
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from hero import research_hero as rh
from hero.market import score_text
from hero.research_0dte import ET, at_or_before, et_minutes
from hero.research_weekly import buy_trade, week_starts

START = "2024-02-01"
POOL = 100
PICKS = 3
OTM = 0.01
TAKES = (2, 3)
FRACTION = 0.5
END_LOSS = 0.9
TARGETS = (2, 20)
FEED = "sip"
WAYS = ("hot", "sentiment", "hot_wind", "control")


def friday_before(monday: date) -> date:
    return monday - timedelta(days=(monday.weekday() - 4) % 7 or 7)


def run(client) -> dict:
    from hero import research_universe as ru
    end = (date.today() - timedelta(days=1)).isoformat()
    stocks = ru.candidates(client)
    syms = sorted(set(stocks) | {"SPY"})
    bars: dict[str, list[dict]] = {}
    for i in range(0, len(syms), 200):
        bars.update(client.daily_bars(syms[i:i + 200], "2023-01-01", adjustment="raw", feed=FEED))
    dates, closes, dvol = ru.align(bars, "SPY")
    have = {s: k for s, k in stocks.items() if s in bars}
    firsts = {s: next((i for i, x in enumerate(closes[s]) if x is not None), None) for s in have}
    start = next(i for i, d in enumerate(dates) if d >= START)
    pools = ru.monthly_pools(dates, closes, dvol, firsts, have, POOL, start)
    keys = sorted(pools)
    idx = {d: i for i, d in enumerate(dates)}
    starts = [d for d in week_starts([d for d in dates if d >= START]) if d <= end]
    counts: dict[str, list[int]] = {}  # each name's article counts, week by week (for "unusual")
    rng = random.Random(17)
    rows, picks_log = [], []
    for d0 in starts:
        t = idx[d0]
        pool = pools[max(k for k in keys if k <= t)]
        monday = date.fromisoformat(d0)
        fri = friday_before(monday)
        win0 = datetime(fri.year, fri.month, fri.day, 9, 30, tzinfo=ET)
        win1 = datetime(monday.year, monday.month, monday.day, 9, 25, tzinfo=ET)
        news: dict[str, list[float]] = {s: [] for s in pool}
        for i in range(0, len(pool), 50):
            try:
                for item in client.news_range(pool[i:i + 50], win0.isoformat(), win1.isoformat()):
                    sc = score_text(f"{item.get('headline', '')}. {item.get('summary', '')}")
                    for s in item.get("symbols") or []:
                        if s in news:
                            news[s].append(sc)
            except Exception:
                continue
        trend = {}
        for s in pool + ["SPY"]:
            xs = closes.get(s) or []
            a, b = (xs[t - 21] if t >= 21 else None), (xs[t - 1] if t >= 1 else None)
            trend[s] = (1 if b > a else -1) if a and b else 0
        score = {}
        for s in pool:
            n = len(news[s])
            hist = counts.get(s, [])[-8:]
            base = statistics.median(hist) if len(hist) >= 4 else None
            sent = statistics.mean(news[s]) if n else 0.0
            score[s] = {"n": n, "heat": (n / max(base, 1)) if base is not None else None, "sent": round(sent, 3)}
            counts.setdefault(s, []).append(n)
        direction = lambda s: 1 if score[s]["sent"] > 0 else -1 if score[s]["sent"] < 0 else 0
        hot = sorted([s for s in pool if (score[s]["heat"] or 0) >= 2 and score[s]["n"] >= 3 and direction(s)],
                     key=lambda s: -score[s]["heat"])
        plan = {
            "hot": [(s, direction(s)) for s in hot[:PICKS]],
            "sentiment": [(s, direction(s)) for s in sorted([s for s in pool if score[s]["n"] >= 3 and direction(s)],
                                                            key=lambda s: -abs(score[s]["sent"]))[:PICKS]],
            "hot_wind": [(s, direction(s)) for s in hot if direction(s) == trend[s] == trend["SPY"]][:PICKS],
            "control": [(s, trend[s]) for s in rng.sample(pool, min(PICKS, len(pool))) if trend[s]],
        }
        picks_log.append({"week": d0, **{w: [(s, dd, score[s]) for s, dd in v] for w, v in plan.items()}})
        friday = (monday + timedelta(days=4)).isoformat()
        week_days = [d for d in dates if d0 <= d <= friday]
        for s in sorted({s for v in plan.values() for s, _ in v}):
            try:
                sm = et_minutes(client.stock_bars([s], f"{d0}T13:00:00Z", f"{d0}T21:00:00Z", feed=FEED).get(s, []))
            except Exception:
                continue
            bar = at_or_before(sm, "10:00")
            if not bar:
                continue
            spot = float(bar["c"])
            cs = []
            for status in ("inactive", "active"):
                try:
                    cs = client.option_contracts(s, status=status, expiration_date_gte=(monday + timedelta(days=1)).isoformat(),
                                                 expiration_date_lte=friday, strike_price_gte=f"{spot * 0.9:.2f}",
                                                 strike_price_lte=f"{spot * 1.1:.2f}")
                except Exception:
                    cs = []
                if cs:
                    break
            if not cs:
                continue
            exp = max(c["expiration_date"] for c in cs)
            before = [d for d in week_days if d < exp]
            last_key = f"{before[-1]} 15:50" if before else f"{exp} 12:00"
            calls = sorted((float(c["strike_price"]), c["symbol"]) for c in cs if c["expiration_date"] == exp and c["type"] == "call")
            puts = sorted((float(c["strike_price"]), c["symbol"]) for c in cs if c["expiration_date"] == exp and c["type"] == "put")
            call = next((x for k, x in calls if k >= spot * (1 + OTM)), None)
            below = [x for k, x in puts if k <= spot * (1 - OTM)]
            put = below[-1] if below else None
            want = [x for x in (call, put) if x]
            if not want:
                continue
            try:
                ob = client.option_bars(want, f"{d0}T13:00:00Z", f"{exp}T21:00:00Z", timeframe="1Min")
            except Exception:
                continue
            time.sleep(0.1)
            res = {}
            for sym, side in ((call, 1), (put, -1)):
                if not sym:
                    continue
                m = {}
                for b in ob.get(sym, []):
                    tt = datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ET)
                    k = tt.strftime("%Y-%m-%d %H:%M")
                    if k >= f"{d0} 10:00":
                        m[k] = b
                first = sorted(m)[:1]
                if first and first[0] <= f"{d0} 10:05":
                    res[side] = {tk: buy_trade(m, tk, last_key) for tk in TAKES}
            for way, chosen in plan.items():
                for name, dd in chosen:
                    if name == s and dd in res:
                        rows.append({"week": d0, "way": way, "symbol": s, "side": dd, "n": score[s]["n"],
                                     "sent": score[s]["sent"], **{f"r{tk}": res[dd][tk] for tk in TAKES}})
    return {"generated": date.today().isoformat(), "start": START, "end": end, "weeks": len(starts), "rows": rows,
            "picks": picks_log}


def table(rep: dict) -> dict:
    out = {}
    for way in WAYS:
        for tk in TAKES:
            sub = [r for r in rep["rows"] if r["way"] == way and r.get(f"r{tk}") is not None]
            if not sub:
                continue
            xs = [r[f"r{tk}"] for r in sub]
            by_week: dict[str, list[float]] = {}
            for r in sub:
                by_week.setdefault(r["week"], []).append(r[f"r{tk}"])
            stream = [(w, max(FRACTION * statistics.mean(v), -1.0)) for w, v in sorted(by_week.items())]
            first = (date.fromisoformat(stream[0][0]) - timedelta(days=1)).isoformat()
            row = {"n": len(xs), "weeks": len(by_week), "names": len({r["symbol"] for r in sub}),
                   "mean": round(statistics.mean(xs), 3), "median": round(statistics.median(xs), 3),
                   "win": round(sum(x > 0 for x in xs) / len(xs), 3), "hit": round(sum(x >= tk - 1 for x in xs) / len(xs), 3),
                   "calls": round(sum(r["side"] > 0 for r in sub) / len(sub), 2)}
            for tg in TARGETS:
                seq = rh.sequential(stream, first, tg, END_LOSS)
                row[f"t{tg}"] = {"heroes": seq["heroes"], "zeros": seq["zeros"]}
            out[f"{way}|{tk}"] = row
    return out


def markdown(rep: dict, t: dict) -> str:
    names = {"hot": "新闻最热（比平常多 2 倍以上）", "sentiment": "新闻情绪最强", "hot_wind": "最热 + 风向一致（个股 20 日和 SPY 同向）",
             "control": "对照：随机挑，按 20 日趋势"}
    out = [f"# 周内期权：按个股新闻挑股票 {rep['generated']}", "",
           f"{rep['start']} 至 {rep['end']}，{rep['weeks']} 周。每周从当月股票池（成交额前 100）里，用周五 9:30 到周一 9:25 的新闻挑 3 只："
           "「最热」= 新闻数是自己过去 8 周中位数的 2 倍以上；方向 = 标题情绪（简单词表）的正负。"
           "周一 10:00 买当周最后到期、离现价至少 1% 的期权，买入多付 10%；止盈 2 / 3 倍，没到就到期前一天 15:50 卖（少拿 10%）。"
           "玩法：$500，每周押一半、平分给当周挑中的股票，低于 $50 归零，到 $1,000 / $10,000 成功。只研究，不改交易。", "",
           "| 挑法 | 止盈 | 笔数 | 周数 | 股票数 | 看涨占比 | 平均每 $1 | 中位数 | 赚钱比例 | 碰到止盈 | 到 $1,000：成功 / 归零 | 到 $10,000：成功 / 归零 |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for key, r in t.items():
        way, tk = key.split("|")
        out.append(f"| {names[way]} | {tk} 倍 | {r['n']} | {r['weeks']} | {r['names']} | {r['calls']:.0%} | {r['mean']:+.0%} | {r['median']:+.0%} | "
                   f"{r['win']:.0%} | {r['hit']:.0%} | {r['t2']['heroes']} / {r['t2']['zeros']} | {r['t20']['heroes']} / {r['t20']['zeros']} |")
    return "\n".join(out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca())
    t = table(rep)
    rep["table"] = t
    (root / "research" / f"{rep['generated']}-weekly-news.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    (root / "research" / f"{rep['generated']}-weekly-news.md").write_text(markdown(rep, t))
    print(markdown(rep, t))


if __name__ == "__main__":
    sys.exit(main())
