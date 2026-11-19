"""每日档案: every past SPY session as one card, and today read against the days that looked the same (owner, 11-19:
learn from each past day first, then look at today).

The 11-16 "what kind of day is it" was stopped because each morning's label was made by eye, with nothing fixed
beforehand. Here everything is fixed before a day is read:

  card      what was known in the morning -- the gap (open against the previous close), how many closes in a row were
            up or down, the previous close against its 20-day average, the 20-day realized volatility, and at 10:00 the
            move from the open -- and what the day then did;
  alike     the earlier days with the same labels. With fewer than MIN_DAYS of them the last condition is dropped and
            the search repeated (kept longest: at the open the gap, then volatility, streak, 20-day average; at 10:00
            the 10:00 move, then volatility, gap, streak, 20-day average), so the read always rests on at least a
            month of days. The first run on 11-19 had the gap ahead of volatility at 10:00 and so dropped volatility
            first; on "a move of 1% or more" that read beat the base rate by 10.8% against 19.3% at the open, where
            volatility is kept. Volatility was moved up once, for that reason, and the order is not to be tuned again;
  the read  for each question, the share of those days on which it happened, pulled toward the share on all earlier
            days as if PRIOR more days at that share had been seen;
  the score every day since the first WARM sessions is read with only the days before it and compared with the plain
            share of all earlier days (Brier score). The daybook "knows something" about a question when it beats
            that share in both halves of the scored days and by 1% or more overall. Otherwise it is a description of
            the past, not a forecast, and the report says so.

Prices: Yahoo daily bars; the 10:00 and 15:30 prices from research/2026-11-05-zdte-synth.json (SIP minutes since 2016)
and, after that file ends, Yahoo one-minute bars (the close of the 10:00 and 15:30 minutes, as in that file).
Record only: nothing here places or changes an order.
Run: python -m hero.daybook build        (refresh the cards, score the history, write the report with the last day)
     python -m hero.daybook read GAP [M10]   (read a day in progress, e.g. read 0.0099 -0.0013)
"""

from __future__ import annotations

import json
import math
import statistics
import sys
import urllib.request
from bisect import bisect_left
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "data" / "daybook" / "spy.json"
SYNTH = ROOT / "research" / "2026-11-05-zdte-synth.json"
ET = ZoneInfo("America/New_York")
MIN_DAYS, PRIOR, WARM = 30, 10, 750
BIG, FAR, FLAT = 0.01, 0.005, 0.001
INF = float("inf")
GAPS = ((-0.01, "低开 1% 以上"), (-0.003, "低开"), (0.003, "平开"), (0.01, "高开"), (INF, "高开 1% 以上"))
STREAKS = ((-2.5, "连跌 3 天以上"), (0, "跌 1–2 天"), (2.5, "涨 1–2 天"), (INF, "连涨 3 天以上"))
VOLS = ((0.10, "波动 10% 以下"), (0.14, "波动 10–14%"), (0.18, "波动 14–18%"), (0.22, "波动 18–22%"), (INF, "波动 22% 以上"))
M10S = ((-0.005, "10:00 跌 0.5% 以上"), (-FLAT, "10:00 小跌"), (FLAT, "10:00 持平"), (0.005, "10:00 小涨"), (INF, "10:00 涨 0.5% 以上"))
KEYS = {"open": ("gap", "vol", "streak", "trend"), "1000": ("m10", "vol", "gap", "streak", "trend")}
NAMES = {"gap": "缺口", "vol": "波动", "streak": "连涨连跌", "trend": "20 日均线", "m10": "10:00 的位置"}


def bucket(x: float, edges) -> str:
    return next(name for top, name in edges if x < top)


def labels(c: dict) -> dict:
    out = {"gap": bucket(c["gap"], GAPS), "vol": bucket(c["rv20"], VOLS), "streak": bucket(c["streak"], STREAKS),
           "trend": "20 日均线上方" if c["above20"] else "20 日均线下方"}
    if c.get("m10") is not None:
        out["m10"] = bucket(c["m10"], M10S)
    return out


def side(x: float) -> int:
    return 1 if x > 0 else -1


def flat10(c: dict) -> bool:
    """No 10:00 price, or 10:00 within FLAT of the open (the "10:00 持平" label): no direction to ask about."""
    return c.get("m10") is None or -FLAT <= c["m10"] < FLAT


# What a day did. None = the question does not apply to that day (flat open, flat 10:00, no intraday price).
OUTCOMES = {
    "up": ("收盘在开盘价上方", lambda c: c["c"] > c["o"]),
    "big": ("收盘对昨收超过 1%", lambda c: abs(c["c"] / c["pc"] - 1) >= BIG),
    "against": ("收盘在开盘价的另一边（反着缺口走）",
                lambda c: None if abs(c["gap"]) <= FLAT else side(c["c"] - c["o"]) != side(c["gap"])),
    "cont": ("10:00 以后接着原来的方向走到收盘",
             lambda c: None if flat10(c) else side(c["m10"]) * (c["c"] / c["p1000"] - 1) > 0),
    "far": ("10:00 以后顺着方向再走 0.5% 以上",
            lambda c: None if flat10(c) else side(c["m10"]) * (c["c"] / c["p1000"] - 1) >= FAR),
    "move10": ("10:00 到收盘不分方向动 0.5% 以上", lambda c: None if c.get("p1000") is None else abs(c["c"] / c["p1000"] - 1) >= FAR),
}
ASKED = {"open": ("up", "big", "against"), "1000": ("cont", "far", "move10", "big")}


def cards(daily: list[tuple], intraday: dict[str, dict]) -> list[dict]:
    """One card per session from (day, open, high, low, close) rows in date order; the first 21 sessions only feed
    the averages. `intraday`: day -> {"m10", "p1000", "p1530"}."""
    out = []
    for i in range(21, len(daily)):
        day, o, h, l, c = daily[i]
        closes = [r[4] for r in daily[i - 21:i]]                     # the 21 closes before today
        rets = [math.log(b / a) for a, b in zip(closes, closes[1:])]
        streak = 0
        for a, b in zip(reversed(closes[:-1]), reversed(closes[1:])):   # newest first
            s = 1 if b > a else -1
            if streak and s != side(streak):
                break
            streak += s
        pc = closes[-1]
        card = {"day": day, "o": round(o, 2), "h": round(h, 2), "l": round(l, 2), "c": round(c, 2), "pc": round(pc, 2),
                "gap": round(o / pc - 1, 5), "streak": streak, "above20": pc >= statistics.mean(closes[1:]),
                "rv20": round(statistics.pstdev(rets) * math.sqrt(252), 4)}
        x = intraday.get(day)
        if x:
            card.update(m10=round(x["m10"], 5), p1000=round(x["p1000"], 2), p1530=round(x["p1530"], 2))
        out.append(card)
    return out


class Book:
    """The cards with an index per checkpoint, so a day can be read against the days before it only."""

    def __init__(self, cs: list[dict]):
        self.cards = cs
        self.labels = [labels(c) for c in cs]
        self.index: dict[tuple, list[int]] = {}
        for i, lab in enumerate(self.labels):
            for at, keys in KEYS.items():
                if all(k in lab for k in keys):
                    for n in range(1, len(keys) + 1):
                        self.index.setdefault((at, n, *(lab[k] for k in keys[:n])), []).append(i)

    def alike(self, lab: dict, at: str, before: int) -> tuple[list[int], tuple]:
        """Indexes of the earlier days with the same labels, and the conditions that were kept."""
        keys = KEYS[at]
        for n in range(len(keys), 0, -1):
            idx = self.index.get((at, n, *(lab[k] for k in keys[:n])), [])
            idx = idx[:bisect_left(idx, before)]
            if len(idx) >= MIN_DAYS or n == 1:
                return idx, keys[:n]
        return [], ()

    def share(self, idx, q: str) -> tuple[int, int]:
        ys = [y for y in (OUTCOMES[q][1](self.cards[i]) for i in idx) if y is not None]
        return sum(ys), len(ys)

    def read(self, lab: dict, at: str, before: int, base: dict[str, tuple[int, int]] | None = None) -> dict:
        """The daybook's answer to each question of this checkpoint for a day with these labels."""
        idx, kept = self.alike(lab, at, before)
        base = base or {q: self.share(range(before), q) for q in ASKED[at]}
        qs = {}
        for q in ASKED[at]:
            k, n = self.share(idx, q)
            bk, bn = base[q]
            b = bk / bn if bn else 0.5
            qs[q] = {"k": k, "n": n, "base": round(b, 4), "p": round((k + PRIOR * b) / (n + PRIOR), 4)}
        return {"at": at, "kept": list(kept), "days": len(idx), "idx": idx, "q": qs}


def score(book: Book) -> dict:
    """Every day after the first WARM read with only the days before it, against the plain share of all earlier days."""
    run = {q: [0, 0] for q in OUTCOMES}                        # happened / asked, over the days already passed
    rows = {(at, q): [] for at, qs in ASKED.items() for q in qs}
    for i, c in enumerate(book.cards):
        ys = {q: f(c) for q, (_, f) in OUTCOMES.items()}
        if i >= WARM:
            for at, qs in ASKED.items():
                if all(k in book.labels[i] for k in KEYS[at]):
                    r = book.read(book.labels[i], at, i, {q: tuple(run[q]) for q in qs})
                    for q in qs:
                        if ys[q] is not None:
                            rows[(at, q)].append((c["day"], r["q"][q]["p"], r["q"][q]["base"], int(ys[q])))
        for q, y in ys.items():
            if y is not None:
                run[q][0] += y
                run[q][1] += 1
    out = {}
    for (at, q), rs in rows.items():
        if not rs:
            continue
        brier = lambda xs, j: statistics.mean((x[j] - x[3]) ** 2 for x in xs)
        skill = lambda xs: 1 - brier(xs, 1) / brier(xs, 2) if brier(xs, 2) else 0.0
        half = len(rs) // 2
        halves = (skill(rs[:half]), skill(rs[half:]))
        out[f"{at}|{q}"] = {"n": len(rs), "from": rs[0][0], "rate": round(statistics.mean(x[3] for x in rs), 4),
                            "brier": round(brier(rs, 1), 5), "brier_base": round(brier(rs, 2), 5), "skill": round(skill(rs), 4),
                            "halves": [round(x, 4) for x in halves], "pass": bool(min(halves) > 0 and skill(rs) >= 0.01)}
    return out


def pct(x: float, digits: int = 0) -> str:
    return f"{x:.{digits}%}"


def describe(book: Book, r: dict, lab: dict, sc: dict | None = None) -> list[str]:
    """A read as lines of text."""
    at = r["at"]
    dropped = [NAMES[k] for k in KEYS[at] if k not in r["kept"]]
    cs = [book.cards[i] for i in r["idx"]]
    out = [f"**{'开盘' if at == 'open' else '10:00'}**：{'、'.join(lab[k] for k in KEYS[at])}。",
           f"过去同样的日子 {r['days']} 天（{cs[0]['day'][:4]}–{cs[-1]['day'][:4]}；按「{'、'.join(lab[k] for k in r['kept'])}」找的"
           + (f"，「{'、'.join(dropped)}」放宽了，因为全对上的不到 {MIN_DAYS} 天" if dropped else "") + "）。" if cs else "过去没有同样的日子。", "",
           "| 问题 | 这些日子里发生的 | 所有日子的底数 | 档案的读数 | 这一问历史上档案比底数准吗 |", "|---|---|---|---|---|"]
    for q, v in r["q"].items():
        if not v["n"]:
            continue                                   # the question does not apply to days like this (a flat 10:00)
        s = (sc or {}).get(f"{at}|{q}")
        verdict = "—" if not s else ("准" if s["pass"] else "不准") + f"（{s['skill']:+.1%}）"
        out.append(f"| {OUTCOMES[q][0]} | {v['k']} / {v['n']}" + (f"（{pct(v['k'] / v['n'])}）" if v["n"] else "")
                   + f" | {pct(v['base'])} | {pct(v['p'])} | {verdict} |")
    if cs:
        moves = [abs(c["c"] / c["o"] - 1) for c in cs]
        rng = [(c["h"] - c["l"]) / c["pc"] for c in cs]
        out += ["", f"这些日子开盘到收盘一般动 {pct(statistics.median(moves), 2)}（不分涨跌的中位数），全天高低差一般 {pct(statistics.median(rng), 2)}。",
                "最近的几天：" + "；".join(f"{c['day']} 开盘到收盘 {c['c'] / c['o'] - 1:+.2%}" for c in cs[-5:][::-1]) + "。"]
    return out


def report(book: Book, sc: dict) -> str:
    cs = book.cards
    last, i = cs[-1], len(cs) - 1
    out = [f"# 每日档案：先学过去的每一天，再看今天（{date.today().isoformat()}）", "",
           f"SPY {cs[0]['day']} 到 {last['day']}，{len(cs)} 个交易日，每天一张档案。早上就知道的：缺口（开盘对昨收）、之前连涨还是连跌几天、"
           "昨收在不在 20 日均线上方、近 20 天的波动、10:00 离开盘多少。之后发生的：开盘到收盘、10:00 到收盘、全天高低、收盘对昨收。", "",
           f"读一天的办法（全部事先写死）：找出之前标签完全一样的日子；不到 {MIN_DAYS} 天就按固定顺序放宽最后一个条件再找。"
           f"每个问题给的读数 = 这些日子里发生的比例，往「所有日子的底数」拉一点（相当于多看了 {PRIOR} 天底数）。只记录，不据此下单。", "",
           "## 这样学，比底数准吗", "",
           f"把历史走一遍：从第 {WARM + 1} 天（{cs[WARM]['day']}）起，每一天只用它之前的日子来读，和「不分日子、只报底数」比谁更准（Brier 分，越低越准）。"
           "过线 = 前后两半都比底数准，全期至少好 1%。", "",
           "| 什么时候 | 问题 | 天数 | 发生的比例 | 档案 | 只报底数 | 好多少（前半 / 后半） | 过线 |", "|---|---|---|---|---|---|---|---|"]
    for key, s in sc.items():
        at, q = key.split("|")
        out.append(f"| {'开盘' if at == 'open' else '10:00'} | {OUTCOMES[q][0]} | {s['n']} | {pct(s['rate'])} | {s['brier']:.4f} | {s['brier_base']:.4f} | "
                   f"{s['skill']:+.1%}（{s['halves'][0]:+.1%} / {s['halves'][1]:+.1%}） | {'过' if s['pass'] else '不过'} |")
    passed = [OUTCOMES[k.split('|')[1]][0] for k, s in sc.items() if s["pass"]]
    out += ["", f"**{len(sc)} 个问题过了 {len(passed)} 个" + (f"：{'；'.join(dict.fromkeys(passed))}" if passed else "") + "。**"
            "没过线的问题，下面的读数只是对过去的描述，不能当预测用。", "",
            f"## 最近一天：{last['day']}", ""]
    lab = book.labels[i]
    for at in KEYS:
        if all(k in lab for k in KEYS[at]):
            out += describe(book, book.read(lab, at, i), lab, sc) + [""]
    did = [f"开盘 {last['o']}（缺口 {last['gap']:+.2%}）"]
    if last.get("m10") is not None:
        did.append(f"10:00 对开盘 {last['m10']:+.2%}")
    did += [f"收盘 {last['c']}（开盘到收盘 {last['c'] / last['o'] - 1:+.2%}，对昨收 {last['c'] / last['pc'] - 1:+.2%}）",
            f"全天高低差 {(last['h'] - last['l']) / last['pc']:.2%}"]
    out += ["这一天实际：" + "，".join(did) + "。"]
    return "\n".join(out) + "\n"


def yahoo(sym: str, interval: str, start: datetime, end: datetime) -> list[tuple]:
    """(New York datetime, open, high, low, close) bars."""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval={interval}"
           f"&period1={int(start.timestamp())}&period2={int(end.timestamp())}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    r = json.load(urllib.request.urlopen(req, timeout=40))["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    return [(datetime.fromtimestamp(t, ET), o, h, l, c) for t, o, h, l, c in
            zip(r.get("timestamp") or [], q["open"], q["high"], q["low"], q["close"]) if c and o]


def fetch(today: date | None = None) -> tuple[list[tuple], dict[str, dict]]:
    """Daily bars since late 2015, and the 10:00 / 15:30 prices: the stored file first, Yahoo minutes after it ends."""
    today = today or datetime.now(ET).date()
    now = datetime.now(ET)
    daily = [(t.strftime("%Y-%m-%d"), o, h, l, c) for t, o, h, l, c in
             yahoo("SPY", "1d", datetime(2015, 11, 1, tzinfo=ET), now + timedelta(days=1))]
    if daily and daily[-1][0] == today.isoformat() and now.hour < 16:
        daily = daily[:-1]                                           # today's bar is not finished
    intraday = {r["day"]: {"m10": r["move"], "p1000": r["spot"], "p1530": r["p1530"]}
                for r in json.loads(SYNTH.read_text())["rows"] if r.get("p1530")}
    first = date.fromisoformat(max(intraday)) + timedelta(days=1)
    first = max(first, today - timedelta(days=28))                    # one-minute bars go back about thirty days
    while first <= today:
        bars = yahoo("SPY", "1m", datetime(first.year, first.month, first.day, tzinfo=ET),
                     datetime(first.year, first.month, first.day, tzinfo=ET) + timedelta(days=7))
        by: dict[str, dict] = {}
        for t, o, h, l, c in bars:
            by.setdefault(t.strftime("%Y-%m-%d"), {})[t.strftime("%H:%M")] = (o, c)
        for d, m in by.items():
            if "09:30" in m and "10:00" in m and "15:30" in m:
                intraday[d] = {"m10": m["10:00"][1] / m["09:30"][0] - 1, "p1000": m["10:00"][1], "p1530": m["15:30"][1]}
        first += timedelta(days=7)
    return daily, intraday


def load() -> list[dict]:
    return json.loads(STORE.read_text())["cards"]


def build() -> None:
    from hero.research_ideas import save
    daily, intraday = fetch()
    cs = cards(daily, intraday)
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps({"built": date.today().isoformat(), "cards": cs}, ensure_ascii=False) + "\n")
    book = Book(cs)
    sc = score(book)
    save("daybook", {"generated": date.today().isoformat(), "first": cs[0]["day"], "last": cs[-1]["day"], "days": len(cs), "score": sc},
         report(book, sc))


def read_now(gap: float, m10: float | None = None) -> str:
    """A day in progress: the gap (and the 10:00 move) typed in, the rest from the stored cards."""
    cs = load()
    daily = [(c["day"], c["o"], c["h"], c["l"], c["c"]) for c in cs]
    pc = cs[-1]["c"]
    probe = cards(daily[-21:] + [("9999-12-31", pc * (1 + gap), pc, pc, pc)], {})[0]   # streak, trend and volatility as of today
    probe["m10"] = m10
    book, lab = Book(cs), labels(probe)
    sc = score(book)
    out = []
    for at in KEYS:
        if all(k in lab for k in KEYS[at]):
            out += describe(book, book.read(lab, at, len(cs)), lab, sc) + [""]
    return "\n".join(out)


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "read":
        print(read_now(float(sys.argv[2]), float(sys.argv[3]) if len(sys.argv) > 3 else None))
    else:
        build()


if __name__ == "__main__":
    sys.exit(main())
