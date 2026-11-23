"""多标的档案: the weekbook (hero/weekbook.py) for eleven symbols, and a second book for the next three months (owner,
11-23: more forecasting features; the owner trades three-month vertical spreads on SPY and the megacaps and watches
Google and Apple).

Everything below was fixed on 2026-11-23, before any result was read, and is not to be tuned after looking:

  symbols   SPY, QQQ, SMH, IWM, AAPL, GOOGL, MSFT, NVDA, AMZN, META, TSLA. Yahoo daily bars from 2010-01-01 where they
            exist, split-adjusted as Yahoo gives them (not dividend-adjusted). The VIX from Yahoo the same way. The
            first 272 sessions of a symbol only feed the one-year high and the volatility's own normal level.
  volatility the 20-day realized volatility over the symbol's own trailing one-year median of it (bands < 0.8x,
            0.8-1.2x, 1.2-1.6x, > 1.6x): a single stock at 25% is calm while SPY at 25% is not. The VIX band is SPY's
            weekbook band for every symbol.
  week      Monday's open to Friday's close, read at the close before the week. The weekbook's labels with the
            volatility band above, and its six questions unchanged. Relaxation order (the weekbook's): volatility, the
            last week's move, the VIX, the 20-day average, the short week, the expiry week, the one-year high, the
            50-day average.
  quarter   63 sessions from an entry close. Samples enter only on the first session of each month, so two neighbours
            share two thirds of their days (the page says so). Labels known at the entry close: the volatility band,
            the last 63 sessions' move (<= -15%, -15..-5%, -5..+5%, +5..+15%, >= +15%), the distance from the one-year
            closing high (within 5%, 5-10%, 10-20%, 20-30%, > 30%), above or below the 200-day average, the VIX band.
            Questions (end = the close 63 sessions later; touch = any close in those 63 sessions): ends up; |end move|
            > 10%; end move > +10%; end move < -10%; touched -10%; touched -20%; ended within +-5%. They are how a
            three-month bull call spread (bought at the money, sold 10% higher) or a put sold 10% lower would end.
            Relaxation order: volatility, the VIX, the distance from the one-year high, the last 63 sessions' move,
            the 200-day average -- size follows volatility (the daybook's and the weekbook's finding: the symbol's own
            first, then the market's), then how deep the fall from the high is (where a bear market shows), then the
            last three months' move and the 200-day average, which mostly repeat the one before.
  alike     the earlier samples with the same labels; with fewer than MIN_N (30) of them the last label in the order is
            dropped and the search repeated. Only samples finished by the read close count: a week once its Friday has
            closed, a quarter once its 63rd session has closed (on or before the close being read).
  the read  the share of those samples on which each question happened, pulled toward the share on all finished
            earlier samples as if PRIOR (10) more samples at that share had been seen.
  the score walk forward exactly like the weekbook: every finished sample after the first WARM (104 weeks; 36 monthly
            samples) is read with only the samples finished by its read close and compared with the plain share of
            those samples (Brier score). A question passes when it beats that share in both halves of the scored
            samples and by 1% or more overall. A question that does not pass is a description, not a forecast.

The quarter read on the page is taken at the latest finished close (not only on the first of a month); the labels are
the same, the history walk is on the monthly samples. Each run writes the reads down once per symbol, horizon and
period (the week's Monday; the month of the close) in research/horizonbook/ledger.json, for scoring them forward.
Record only: nothing here places or changes an order, and no rule follows from it.
Run: python -m hero.horizonbook [YYYY-MM-DD]
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from bisect import bisect_left, bisect_right
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from hero import daybook as db
from hero import weekbook as wb

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "research" / "horizonbook"
SYMBOLS = ("SPY", "QQQ", "SMH", "IWM", "AAPL", "GOOGL", "MSFT", "NVDA", "AMZN", "META", "TSLA")
NAMES_ZH = {"SPY": "标普 500", "QQQ": "纳指 100", "SMH": "半导体", "IWM": "小盘股", "AAPL": "苹果", "GOOGL": "谷歌",
            "MSFT": "微软", "NVDA": "英伟达", "AMZN": "亚马逊", "META": "Meta", "TSLA": "特斯拉"}
START = datetime(2010, 1, 1, tzinfo=timezone.utc)
MIN_N, PRIOR = 30, 10
WARM = {"week": 104, "quarter": 36}
H, YEAR = 63, 252
FIRST = YEAR + 19                       # the first close with a year of 20-day volatilities behind it (index 271)
CLOSED = time(16, 15)                   # a day's bar counts once this New York time has passed
INF = float("inf")
RVOLS = ((0.8, "波动偏低（不到自己常态的 0.8 倍）"), (1.2, "波动平常（常态的 0.8–1.2 倍）"),
         (1.6, "波动偏高（常态的 1.2–1.6 倍）"), (INF, "波动很高（常态的 1.6 倍以上）"))
Q_LAST = ((-0.15, "前三个月跌 15% 以上"), (-0.05, "前三个月跌 5–15%"), (0.05, "前三个月在 ±5% 以内"),
          (0.15, "前三个月涨 5–15%"), (INF, "前三个月涨 15% 以上"))
Q_HIGHS = ((-0.30, "离一年高点 30% 以上"), (-0.20, "离一年高点 20–30%"), (-0.10, "离一年高点 10–20%"),
           (-0.05, "离一年高点 5–10%"), (INF, "离一年高点 5% 以内"))
KEYS = {"week": wb.KEYS, "quarter": ("vol", "vix", "high", "last", "trend200")}
NAMES = {"week": wb.NAMES, "quarter": {"vol": "波动", "vix": "VIX", "high": "离一年高点", "last": "前三个月",
                                       "trend200": "200 日均线"}}
QUARTER = {
    "up": ("三个月后收涨", lambda s: s["end"] > s["pc"]),
    "big": ("三个月后涨跌超过 ±10%", lambda s: abs(s["end"] / s["pc"] - 1) > 0.10),
    "up10": ("三个月后涨超 10%", lambda s: s["end"] / s["pc"] - 1 > 0.10),
    "down10": ("三个月后跌超 10%", lambda s: s["end"] / s["pc"] - 1 < -0.10),
    "touch10": ("三个月里有一天收盘跌到 −10%", lambda s: s["low"] <= s["pc"] * 0.90),
    "touch20": ("三个月里有一天收盘跌到 −20%", lambda s: s["low"] <= s["pc"] * 0.80),
    "flat5": ("三个月后在 ±5% 以内", lambda s: abs(s["end"] / s["pc"] - 1) <= 0.05),
}
OUTCOMES = {"week": wb.OUTCOMES, "quarter": QUARTER}
SHORT = {"week": {"big": "动超 ±2%", "range": "高低差超 3%", "up": "收涨", "fade": "收在周一开盘下方", "touch": "碰到 −2%",
                  "vix_down": "VIX 回落"},
         "quarter": {"up": "收涨", "big": "动超 ±10%", "up10": "涨超 10%", "down10": "跌超 10%", "touch10": "碰到 −10%",
                     "touch20": "碰到 −20%", "flat5": "±5% 以内"}}
HZ = {"week": "这一周", "quarter": "这三个月"}


def bucket(x: float, edges) -> str:
    return next(name for top, name in edges if x < top)


# ---------- the inputs ----------
def rel_vol(closes: list[float]) -> list[float | None]:
    """At each close, the 20-day realized volatility over its own trailing one-year (252-close) median; None before
    there is a year of it."""
    logs = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    rv: list[float | None] = [None] * len(closes)
    for i in range(20, len(closes)):
        rv[i] = statistics.pstdev(logs[i - 20:i]) * math.sqrt(252)      # the 20 returns ending at close i
    rel: list[float | None] = [None] * len(closes)
    for i in range(FIRST, len(closes)):
        med = statistics.median(rv[i - YEAR + 1:i + 1])
        rel[i] = round(rv[i] / med, 4) if med else None
    return rel


def finished(bars: list[tuple], today: date, now: datetime) -> list[tuple]:
    """The bars of sessions that had closed by `today` (today's own bar only once the close has passed)."""
    t = today.isoformat()
    done = now.date() > today or now.time() >= CLOSED
    return [b for b in bars if b[0] < t or (b[0] == t and done)]


def add_sessions(day: str, n: int) -> str:
    """The n-th session after `day` on the usual exchange calendar (one-off closures not in it)."""
    d = date.fromisoformat(day)
    while n:
        d += timedelta(days=1)
        if d.weekday() < 5 and d not in wb.holidays(d.year):
            n -= 1
    return d.isoformat()


# ---------- the week book ----------
def cards_of(bars: list[tuple]) -> list[dict]:
    return [{"day": d, "o": o, "h": h, "l": l, "c": c} for d, o, h, l, c in bars]


def week_samples(bars: list[tuple], vix: dict[str, float], before: str, rel: list | None = None) -> list[dict]:
    """The weekbook's week cards for one symbol, weeks before `before` only, with the relative volatility at the close
    before each week, the read close ("day") and the close the week finished at ("done"; None if it is short of a
    session)."""
    days = [b[0] for b in bars]
    rel = rel if rel is not None else rel_vol([b[4] for b in bars])
    out = []
    for w in wb.weeks(cards_of(bars), vix):
        if w["week"] >= before:
            continue
        pre = bisect_left(days, w["week"]) - 1
        if rel[pre] is None:
            continue
        last = bisect_left(days, (date.fromisoformat(w["week"]) + timedelta(days=7)).isoformat()) - 1
        out.append({**w, "key": w["week"], "day": days[pre], "rvrel": rel[pre], "done": days[last] if w["complete"] else None})
    return out


def week_labels(w: dict) -> dict:
    return {**wb.labels(w), "vol": bucket(w["rvrel"], RVOLS)}


def week_probe(bars: list[tuple], ws: list[dict], vix: dict[str, float], mon: date, rel: list) -> dict:
    p = wb.probe(cards_of(bars), ws, vix, mon)
    pre = bisect_left([b[0] for b in bars], mon.isoformat()) - 1
    return {**p, "day": p["pre_day"], "rvrel": rel[pre]}


# ---------- the quarter book ----------
def quarter_inputs(closes: list[float], rel: list, e: int, vix: float | None) -> dict:
    """What was known at the close `e`."""
    return {"pc": closes[e], "rvrel": rel[e], "last": round(closes[e] / closes[e - H] - 1, 4),
            "dd": round(closes[e] / max(closes[e - YEAR + 1:e + 1]) - 1, 4),
            "above200": closes[e] >= statistics.mean(closes[e - 199:e + 1]), "vix_pre": vix}


def quarter_samples(bars: list[tuple], vix: dict[str, float], rel: list | None = None) -> list[dict]:
    """One sample per month, entered at the close of the month's first session; finished at the close 63 sessions
    later (end), with the lowest and highest close of those 63 sessions."""
    days, closes = [b[0] for b in bars], [b[4] for b in bars]
    rel = rel if rel is not None else rel_vol(closes)
    out = []
    for e in range(max(FIRST, 1), len(bars)):
        if days[e][:7] == days[e - 1][:7] or rel[e] is None:
            continue
        s = {"key": days[e], "day": days[e], **quarter_inputs(closes, rel, e, vix.get(days[e]))}
        if e + H < len(bars):
            path = closes[e + 1:e + H + 1]
            s.update(end=closes[e + H], low=min(path), high=max(path), done=days[e + H])
        else:
            s["done"] = None
        out.append(s)
    return out


def quarter_labels(s: dict) -> dict:
    out = {"vol": bucket(s["rvrel"], RVOLS), "high": bucket(s["dd"], Q_HIGHS), "last": bucket(s["last"], Q_LAST),
           "trend200": "200 日均线上方" if s["above200"] else "200 日均线下方"}
    if s.get("vix_pre"):
        out["vix"] = bucket(s["vix_pre"], wb.VIXES)
    return out


# ---------- the book ----------
class Book:
    """Samples in date order, each with labels known at its read close ("day") and, once finished, the close it
    finished at ("done"). A read at a close uses only the samples finished by that close."""

    def __init__(self, samples: list[dict], labs: list[dict], keys: tuple, outcomes: dict, min_n: int = MIN_N):
        self.samples, self.labels, self.keys, self.outcomes, self.min_n = samples, labs, keys, outcomes, min_n
        self.ys = [{q: (f(s) if s.get("done") else None) for q, (_, f) in outcomes.items()} for s in samples]
        self.index: dict[tuple, dict] = {}
        for i, (s, lab) in enumerate(zip(samples, labs)):
            if not s.get("done"):
                continue
            for n in range(len(keys) + 1):
                if not all(k in lab for k in keys[:n]):
                    break
                e = self.index.setdefault((n, *(lab[k] for k in keys[:n])),
                                          {"idx": [], "done": [], "cum": {q: [(0, 0)] for q in outcomes}})
                if e["done"] and s["done"] < e["done"][-1]:
                    raise ValueError("samples must finish in date order")
                e["idx"].append(i)
                e["done"].append(s["done"])
                for q in outcomes:
                    y, (k0, n0) = self.ys[i][q], e["cum"][q][-1]
                    e["cum"][q].append((k0, n0) if y is None else (k0 + int(bool(y)), n0 + 1))

    def usable(self, key: tuple, at: str) -> tuple[list[int], dict]:
        """The samples under `key` finished by the close `at`, and per question (happened, asked) over them."""
        e = self.index.get(key)
        if not e:
            return [], {q: (0, 0) for q in self.outcomes}
        m = bisect_right(e["done"], at)
        return e["idx"][:m], {q: e["cum"][q][m] for q in self.outcomes}

    def alike(self, lab: dict, at: str) -> tuple[list[int], dict, tuple]:
        for n in range(len(self.keys), 0, -1):
            if not all(k in lab for k in self.keys[:n]):
                continue
            idx, cnt = self.usable((n, *(lab[k] for k in self.keys[:n])), at)
            if len(idx) >= self.min_n or n == 1:
                return idx, cnt, self.keys[:n]
        return [], {q: (0, 0) for q in self.outcomes}, ()

    def read(self, lab: dict, at: str) -> dict:
        idx, cnt, kept = self.alike(lab, at)
        _, base = self.usable((0,), at)
        qs = {}
        for q in self.outcomes:
            (k, n), (bk, bn) = cnt[q], base[q]
            b = bk / bn if bn else 0.5
            qs[q] = {"k": k, "n": n, "base": round(b, 4), "p": round((k + PRIOR * b) / (n + PRIOR), 4)}
        return {"kept": list(kept), "n": len(idx), "idx": idx, "q": qs}


def score(book: Book, warm: int) -> dict:
    """Every finished sample after the first `warm` read with only the samples finished by its read close, against the
    plain share of those samples; the weekbook's pass line."""
    rows: dict[str, list] = {q: [] for q in book.outcomes}
    for i, s in enumerate(book.samples):
        if i < warm or not s.get("done"):
            continue
        r = book.read(book.labels[i], s["day"])
        for q, y in book.ys[i].items():
            if y is not None:
                rows[q].append((s["key"], r["q"][q]["p"], r["q"][q]["base"], int(y)))
    out = {}
    for q, rs in rows.items():
        if not rs:
            continue
        brier = lambda xs, j: statistics.mean((x[j] - x[3]) ** 2 for x in xs)        # noqa: E731
        skill = lambda xs: 1 - brier(xs, 1) / brier(xs, 2) if brier(xs, 2) else 0.0   # noqa: E731
        half = len(rs) // 2
        halves = (skill(rs[:half]), skill(rs[half:]))
        out[q] = {"n": len(rs), "from": rs[0][0], "rate": round(statistics.mean(x[3] for x in rs), 4),
                  "brier": round(brier(rs, 1), 5), "brier_base": round(brier(rs, 2), 5), "skill": round(skill(rs), 4),
                  "halves": [round(x, 4) for x in halves], "pass": bool(min(halves) > 0 and skill(rs) >= 0.01)}
    return out


# ---------- one symbol ----------
def study(bars: list[tuple], vix: dict[str, float], mon: date) -> dict:
    """Both books of one symbol, scored, and the reads of the week starting `mon` and of the three months from the
    last bar's close. `bars`: finished (day, open, high, low, close) in date order."""
    days, closes = [b[0] for b in bars], [b[4] for b in bars]
    rel = rel_vol(closes)
    ws = week_samples(bars, vix, mon.isoformat(), rel)
    wbook = Book(ws, [week_labels(w) for w in ws], KEYS["week"], OUTCOMES["week"])
    wp = week_probe(bars, ws, vix, mon, rel)
    wlab = week_labels(wp)
    qs = quarter_samples(bars, vix, rel)
    qbook = Book(qs, [quarter_labels(s) for s in qs], KEYS["quarter"], OUTCOMES["quarter"])
    last = len(bars) - 1
    qp = {"day": days[last], **quarter_inputs(closes, rel, last, vix.get(days[last]))}
    qp["until"] = add_sessions(qp["day"], H)
    qlab = quarter_labels(qp)
    return {"week": {"book": wbook, "score": score(wbook, WARM["week"]), "probe": wp, "labels": wlab,
                     "read": wbook.read(wlab, wp["day"]), "period": mon.isoformat()},
            "quarter": {"book": qbook, "score": score(qbook, WARM["quarter"]), "probe": qp, "labels": qlab,
                        "read": qbook.read(qlab, qp["day"]), "period": qp["day"][:7]},
            "first": days[0], "last": days[-1]}


# ---------- the page ----------
def pct(x: float, digits: int = 0) -> str:
    return f"{x:.{digits}%}"


def cell(v: dict, s: dict | None) -> str:
    """A read: bold when the question beats the base rate historically, else marked as a description."""
    if not v["n"]:
        return "—"
    return f"**{pct(v['p'])}**" if s and s["pass"] else f"{pct(v['p'])}（描述）"


def verdict(s: dict | None) -> str:
    if not s:
        return "—"
    return f"准（{s['skill']:+.1%}）" if s["pass"] else f"不准（{s['skill']:+.1%}）：只是描述"


def section(sym: str, st: dict) -> list[str]:
    w, q = st["week"], st["quarter"]
    wp, qp = w["probe"], q["probe"]
    out = [f"## {sym}（{NAMES_ZH.get(sym, sym)}，截至 {qp['day']} 收盘 {qp['pc']:.2f}）", ""]
    for hz, x in (("week", w), ("quarter", q)):
        p, lab, r, sc, book = x["probe"], x["labels"], x["read"], x["score"], x["book"]
        keys = KEYS[hz]
        dropped = [NAMES[hz][k] for k in keys if k in lab and k not in r["kept"]]
        used = [book.samples[i] for i in r["idx"]]
        span = f"（{used[0]['key'][:4]}–{used[-1]['key'][:4]}）" if used else ""
        if hz == "week":
            n_days = len(wb.sessions(date.fromisoformat(x["period"])))
            last = f"；上一周 {p['last']:+.1%}" if p.get("last") is not None else ""
            head = (f"**这一周**（{x['period']} 起 {n_days} 个交易日，读数用 {p['day']} 的收盘）："
                    + "、".join(lab[k] for k in keys if k in lab) + "。"
                    + f"（20 日波动是自己常态的 {p['rvrel']:.2f} 倍{last}；离一年高点 {p['dd']:+.1%}。）")
            unit = "周"
        else:
            head = (f"**这三个月**（从 {p['day']} 收盘起 63 个交易日，到 {p['until']} 前后）："
                    + "、".join(lab[k] for k in keys if k in lab) + "。"
                    + f"（波动是自己常态的 {p['rvrel']:.2f} 倍；前三个月 {p['last']:+.1%}；离一年高点 {p['dd']:+.1%}。）")
            unit = "月初样本"
        out += [head, "",
                f"过去同样的{unit} {r['n']} 个{span}，按「{'、'.join(lab[k] for k in r['kept'])}」找的"
                + (f"；「{'、'.join(dropped)}」放宽了，因为全对上的不到 {MIN_N} 个" if dropped else "")
                + (f"；只剩这一个标签也不到 {MIN_N} 个，就用这 {r['n']} 个（读数更靠近底数）" if r["n"] < MIN_N else "") + "。", "",
                f"| {HZ[hz]} | 读数 | 这些样本里发生的 | 底数 | 历史上比底数准吗 |", "|---|---|---|---|---|"]
        for qk, v in r["q"].items():
            if not v["n"]:
                continue
            out.append(f"| {OUTCOMES[hz][qk][0]} | {cell(v, sc.get(qk))} | {v['k']} / {v['n']}（{pct(v['k'] / v['n'])}） | "
                       f"{pct(v['base'])} | {verdict(sc.get(qk))} |")
        done = [s for s in used if s.get("done")]
        if done and hz == "week":
            moves = [abs(s["c"] / s["pc"] - 1) for s in done]
            rng = [(s["h"] - s["l"]) / s["pc"] for s in done]
            out += ["", f"这些周收盘对上周五一般动 {pct(statistics.median(moves), 1)}（不分涨跌的中位数），一周最高到最低一般 "
                        f"{pct(statistics.median(rng), 1)}。"]
        elif len(done) >= 10:
            ends = sorted(s["end"] / s["pc"] - 1 for s in done)
            deci = statistics.quantiles(ends, n=10)
            lows = [s["low"] / s["pc"] - 1 for s in done]
            out += ["", f"这些样本三个月后不分涨跌一般动 {pct(statistics.median(abs(e) for e in ends), 1)}；十个里最差的一个在 "
                        f"{deci[0]:+.0%} 以下，最好的一个在 {deci[-1]:+.0%} 以上；三个月里最低的收盘一般在 {statistics.median(lows):+.1%}。"]
        out.append("")
    return out


def matrix(rep: dict, hz: str) -> list[str]:
    syms = list(rep)
    out = [f"| {HZ[hz]} | " + " | ".join(syms) + " | 过线的标的数 |", "|---" * (len(syms) + 2) + "|"]
    for q in OUTCOMES[hz]:
        cells, n = [], 0
        for sym in syms:
            s = rep[sym][hz]["score"].get(q)
            if not s:
                cells.append("—")
                continue
            n += s["pass"]
            cells.append(f"**{s['skill']:+.0%} 过**" if s["pass"] else f"{s['skill']:+.0%}")
        out.append(f"| {OUTCOMES[hz][q][0]} | " + " | ".join(cells) + f" | {n} / {len(syms)} |")
    return out


def forward(ledger: list[dict], rep: dict, data: dict) -> list[str]:
    """The reads written down before, once their week or three months have finished, against what happened."""
    rows: dict[tuple, list] = {}
    for x in ledger:
        st = rep.get(x["symbol"])
        if not st:
            continue
        if x["horizon"] == "week":
            real = next((w for w in st["week"]["book"].samples if w["week"] == x["period"] and w.get("done")), None)
        else:
            bars = data[x["symbol"]]
            days = [b[0] for b in bars]
            i = bisect_left(days, x["from"])
            real = None
            if i < len(days) and days[i] == x["from"] and i + H < len(bars):
                path = [b[4] for b in bars[i + 1:i + H + 1]]
                real = {"pc": bars[i][4], "end": path[-1], "low": min(path), "done": days[i + H]}
        if not real:
            continue
        for q, p in x["p"].items():
            y = OUTCOMES[x["horizon"]][q][1](real)
            if y is not None:
                rows.setdefault((x["horizon"], q), []).append((p, x["base"].get(q, 0.5), int(y)))
    if not rows:
        first = min((x["written"][:10] for x in ledger), default="—")
        return [f"还没有走完的读数（第一次记在 {first}）。走完一周或 63 个交易日以后，这里按同样的 Brier 分和底数比。"]
    out = ["| 期限 | 问题 | 走完的读数 | 发生的比例 | 读数的 Brier | 底数的 Brier |", "|---|---|---|---|---|---|"]
    for (hz, q), rs in rows.items():
        out.append(f"| {HZ[hz]} | {OUTCOMES[hz][q][0]} | {len(rs)} | {pct(statistics.mean(r[2] for r in rs))} | "
                   f"{statistics.mean((r[0] - r[2]) ** 2 for r in rs):.4f} | {statistics.mean((r[1] - r[2]) ** 2 for r in rs):.4f} |")
    return out


def page(today: date, rep: dict, notes: list[str], ledger: list[dict], data: dict) -> str:
    syms = list(rep)
    out = [f"# 多标的档案：这一周和这三个月（{today.isoformat()}）", "",
           "模拟盘研究，只记录，不是投资建议，也不据此下单、不改规则。", "",
           f"{len(syms)} 个标的（" + "、".join(f"{s} {NAMES_ZH.get(s, s)}" for s in syms) + "），每一个都把过去的每一周、每个月初建一张档案："
           "档案上的标签全是当时收盘就知道的（波动高不高、之前涨跌了多少、离一年高点多远、VIX 多高……），之后发生了什么也记下来。"
           f"读现在：找出标签一样的过去的样本（不到 {MIN_N} 个就按事先定的顺序放宽一个标签再找），看每个问题在那些样本里发生的比例，"
           f"再往「所有样本的底数」拉一点（相当于多看了 {PRIOR} 个底数样本）。", "",
           "两本账：**这一周** = 周一开盘到周五收盘，读数用上周最后一个收盘；**这三个月** = 从一个收盘起往后 63 个交易日。"
           "三个月这本的样本每个月第一个交易日进场一个，相邻两个样本有三分之二的日子是重叠的："
           "样本数看起来有一百多，独立的三个月其实只有三分之一左右，过线的结果也要打个折扣。", "",
           "每一问都把历史走一遍记过分：每个样本只用当时已经走完的更早的样本来读（三个月的样本要等 63 个交易日走完才能用），"
           "和「只报底数」比谁更准（Brier 分）。过线 = 前后两半都比底数准、全期至少好 1%，和每周档案同一条线。"
           "**没过线的问题，读数只是对过去的描述，不是预测；表里标了「描述」。**过线的读数加粗。", "",
           "波动的标签按每个标的自己的常态分：近 20 天的波动 ÷ 它自己过去一年的中位数（不到 0.8 倍偏低，0.8–1.2 倍平常，1.2–1.6 倍偏高，1.6 倍以上很高）"
           "——个股 25% 的波动算平静，标普 25% 就不平静了。VIX 的档对所有标的都一样。", "",
           "### 三个月的问题和价差的关系", "",
           "- 三个月后收涨：一张「平值买、高 10% 卖」的三个月看涨价差，到期至少有一部分价值；",
           "- 三个月后涨超 10%：这张价差到期拿满；",
           "- 三个月后跌超 10%：「低 10% 卖一张看跌」到期要赔；",
           "- 三个月里有一天收盘跌到 −10% / −20%：卖出的看跌中途被打到，浮亏最大的时候；",
           "- 三个月后在 ±5% 以内：横着走，价差拿不到多少，卖出的看跌最安稳；",
           "- 三个月后涨跌超过 ±10%：不管方向，只问动得大不大。", "",
           "## 一眼看完", "",
           "| 标的 | 截至 | 波动档 | 这一周：动超 ±2% | 这一周：收涨 | 三个月：收涨 | 三个月：涨超 10% | 三个月：跌超 10% | 三个月：碰到 −10% |",
           "|---|---|---|---|---|---|---|---|---|"]
    for sym in syms:
        w, q = rep[sym]["week"], rep[sym]["quarter"]
        c = lambda x, k: cell(x["read"]["q"][k], x["score"].get(k))      # noqa: E731
        out.append(f"| {sym} | {q['probe']['day'][5:]} | {q['labels']['vol'].split('（')[0]}（{q['probe']['rvrel']:.2f} 倍） | "
                   f"{c(w, 'big')} | {c(w, 'up')} | {c(q, 'up')} | {c(q, 'up10')} | {c(q, 'down10')} | {c(q, 'touch10')} |")
    out += ["", "## 哪些问题过线", ""]
    for hz in OUTCOMES:
        out += [f"| {HZ[hz]}的问题 | 过线的标的数 | 过线的标的 |", "|---|---|---|"]
        for q in OUTCOMES[hz]:
            ok = [s for s in syms if rep[s][hz]["score"].get(q, {}).get("pass")]
            out.append(f"| {OUTCOMES[hz][q][0]} | {len(ok)} / {len(syms)} | {'、'.join(ok) or '—'} |")
        out.append("")
    for sym in syms:
        out += section(sym, rep[sym])
    out += ["## 历史记分：每个标的、每一问比底数好多少", "",
            f"Brier 分比底数好多少（正 = 比底数准）。这一周从第 {WARM['week'] + 1} 个有标签的周起记分，三个月从第 {WARM['quarter'] + 1} 个月初的样本起；"
            "「过」= 前后两半都比底数准、全期至少好 1%。", ""]
    for hz in OUTCOMES:
        out += matrix(rep, hz) + [""]
    first = {s: (rep[s]["week"]["score"].get("big", {}).get("from", "—"), rep[s]["quarter"]["score"].get("up", {}).get("from", "—"),
                 rep[s]["quarter"]["score"].get("up", {}).get("n", 0)) for s in syms}
    out += ["记分的起点：" + "；".join(f"{s} 周 {a}、三个月 {b}（{n} 个样本）" for s, (a, b, n) in first.items()) + "。", "",
            "## 事先记下的读数和实际", ""] + forward(ledger, rep, data) + ["",
            "## 说明和不足", "",
            "- 数据：Yahoo 日线，2010 年起（META 2012-05、TSLA 2010-06 上市以后），拆股调整过、不含分红，所以三个月的涨跌少算了分红（标普一季度大约 0.3–0.4%）。"
            "每个标的前 272 个交易日只用来算一年高点和波动的常态。",
            "- 三个月的样本互相重叠（见上），一百多个样本大约只相当于四五十个独立的三个月。",
            "- 三个月的读数用的是最近一个收盘，不一定是月初；历史记分只在月初的样本上做，标签是同一套。",
            "- 「涨超 10%」「跌超 10%」里有一半是「动得大」：在方向纯靠掷硬币的假数据上（tests/test_horizonbook.py），这两问也常常过线。"
            "所以它们过线不等于看准了方向；方向只看「收涨」那一问。",
            "- 一周的问题照每周档案用标普的尺子（±2%、高低差 3%、碰到 −2%）；对英伟达、特斯拉这样的个股差不多每周都发生，底数很高，读数也就说不出多少。",
            "- 个股的财报周没有单独的标签：一周的读数里，碰上财报的周和平常的周混在一起。",
            "- 标签、放宽顺序、问题和过线都是 2026-11-23 先写死的（见 hero/horizonbook.py 开头），不许看了结果再调；想改就新开一版并排记。"
            "放宽顺序（先放宽最后一个）：这一周 = " + "、".join(NAMES["week"][k] for k in KEYS["week"])
            + "；这三个月 = " + "、".join(NAMES["quarter"][k] for k in KEYS["quarter"]) + "。"]
    out += [f"- {n}" for n in notes]
    return "\n".join(out) + "\n"


# ---------- data and the run ----------
def load(symbols=SYMBOLS, now: datetime | None = None, fetch=None) -> tuple[dict[str, list[tuple]], list[str]]:
    """(day, open, high, low, close) per symbol from Yahoo; a symbol Yahoo gives no quote block for is skipped with a
    note."""
    fetch = fetch or db.yahoo
    now = now or datetime.now(db.ET)
    data, notes = {}, []
    for sym in symbols:
        try:
            bars = fetch(sym, "1d", START, now + timedelta(days=1))
        except KeyError as e:
            notes.append(f"{sym}：Yahoo 没有给报价（缺 {e}），这次跳过。")
            continue
        except Exception as e:                                       # noqa: BLE001 -- a network failure skips the symbol
            notes.append(f"{sym}：取数失败（{type(e).__name__}），这次跳过。")
            continue
        by = {}
        for t, o, h, l, c in bars:
            by[t.strftime("%Y-%m-%d")] = (round(o, 4), round(h or max(o, c), 4), round(l or min(o, c), 4), round(c, 4))
        data[sym] = [(d, *v) for d, v in sorted(by.items())]
    return data, notes


def load_vix(now: datetime | None = None, fetch=None) -> dict[str, float]:
    fetch = fetch or db.yahoo
    now = now or datetime.now(db.ET)
    return {t.strftime("%Y-%m-%d"): round(c, 2) for t, _, _, _, c in fetch("%5EVIX", "1d", START, now + timedelta(days=1)) if c}


def strip(r: dict) -> dict:
    return {k: v for k, v in r.items() if k != "idx"}


def run(today: date | None = None, data: dict | None = None, vix: dict | None = None, notes: list[str] | None = None,
        write: bool = True, out_dir: Path = OUT, now: datetime | None = None) -> dict:
    now = now or datetime.now(db.ET)
    today = today or now.date()
    notes = list(notes or [])
    if data is None:
        data, more = load(SYMBOLS, now)
        notes += more
    vix = vix if vix is not None else load_vix(now)
    vix = {d: v for d, v in vix.items() if d < today.isoformat() or (d == today.isoformat() and (now.date() > today or now.time() >= CLOSED))}
    mon = wb.target(today)
    rep, used = {}, {}
    for sym, bars in data.items():
        bars = finished(bars, today, now)
        if len(bars) < FIRST + 2 * H:
            notes.append(f"{sym}：只有 {len(bars)} 个交易日，不够建档，这次跳过。")
            continue
        rep[sym], used[sym] = study(bars, vix, mon), bars
    lpath = out_dir / "ledger.json"
    ledger = json.loads(lpath.read_text())["reads"] if lpath.exists() else []
    have = {(x["symbol"], x["horizon"], x["period"]) for x in ledger}
    stamp = now.isoformat(timespec="minutes")
    for sym, st in rep.items():
        for hz in OUTCOMES:
            x = st[hz]
            if (sym, hz, x["period"]) in have:
                continue
            ledger.append({"symbol": sym, "horizon": hz, "period": x["period"], "from": x["probe"]["day"],
                           "close": x["probe"]["pc"], "written": stamp, "labels": x["labels"], "kept": x["read"]["kept"],
                           "n": x["read"]["n"], "p": {q: v["p"] for q, v in x["read"]["q"].items() if v["n"]},
                           "base": {q: v["base"] for q, v in x["read"]["q"].items() if v["n"]},
                           "passes": [q for q, s in x["score"].items() if s["pass"]]})
    text = page(today, rep, notes, ledger, used)
    out = {"generated": stamp, "date": today.isoformat(), "week": mon.isoformat(), "notes": notes,
           "fixed": {"min_n": MIN_N, "prior": PRIOR, "warm": WARM, "keys": {k: list(v) for k, v in KEYS.items()}},
           "symbols": {sym: {"first": st["first"], "last": st["last"],
                             **{hz: {"period": st[hz]["period"], "labels": st[hz]["labels"],
                                     "inputs": {k: v for k, v in st[hz]["probe"].items() if k not in ("week",)},
                                     "read": strip(st[hz]["read"]), "score": st[hz]["score"]} for hz in OUTCOMES}}
                       for sym, st in rep.items()}}
    if write:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{today.isoformat()}.md").write_text(text)
        (out_dir / f"{today.isoformat()}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
        lpath.write_text(json.dumps({"reads": ledger}, ensure_ascii=False, indent=1) + "\n")
    return {**out, "page": text, "rep": rep}


def main() -> None:
    day = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else None
    print(run(day)["page"])


if __name__ == "__main__":
    sys.exit(main())
