"""每周档案: every past SPY week as one card, and the coming week read against the weeks that looked the same (owner,
11-23: "我觉得要做的是预测接下来一周会发生什么的功能").

Built the way hero/daybook.py is, with everything fixed on 11-23 before any week was read:

  card      what was known at the close before the week -- where the last close stood against its 20- and 50-day
            averages and its one-year high, the 20-day realized volatility, the VIX close, the last week's move, and
            whether the coming week is short (a holiday) or holds the monthly options expiry -- and what the week then
            did (Monday's open to Friday's close, the high and the low, the VIX on Friday);
  alike     the earlier weeks with the same labels. With fewer than MIN_WEEKS of them the last label in KEYS is dropped
            and the search repeated. KEYS puts volatility first (the daybook found that what can be learnt is the size
            of a move, and size follows volatility), then the last week's move, the VIX, the 20-day average, the
            calendar, the one-year high, the 50-day average. The order is not to be tuned after looking at results;
  the read  the share of those weeks on which each question happened, pulled toward the share on all earlier weeks as
            if PRIOR more weeks at that share had been seen;
  the score every week after the first WARM labelled weeks is read with only the weeks before it and compared with the
            plain share of all earlier weeks (Brier score). A question passes when the book beats that share in both
            halves of the scored weeks and by 1% or more overall (the daybook's line). A question that does not pass
            is a description of the past, not a forecast, and the page says so.

Each run also writes the read of the week down before (or, when run late, during) the week in research/weekbook/
ledger.json, so the forward record can be scored against what the history walk said. Record only: nothing here
places or changes an order. Prices: the daybook's cards (data/daybook/spy.json, refreshed at every close) and the VIX
from Yahoo.
Run: python -m hero.weekbook [YYYY-MM-DD]   (the week holding that day; on a weekend, the next week)
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from bisect import bisect_left
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from hero import daybook as db

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "research" / "weekbook"
MIN_WEEKS, PRIOR, WARM = 30, 10, 104
INF = float("inf")
VOLS = ((0.12, "波动 12% 以下"), (0.18, "波动 12–18%"), (0.25, "波动 18–25%"), (INF, "波动 25% 以上"))
LAST = ((-0.03, "上周跌 3% 以上"), (-0.01, "上周跌 1–3%"), (0.01, "上周平"), (0.03, "上周涨 1–3%"), (INF, "上周涨 3% 以上"))
VIXES = ((15, "VIX 15 以下"), (20, "VIX 15–20"), (30, "VIX 20–30"), (INF, "VIX 30 以上"))
HIGHS = ((-0.20, "离一年高点 20% 以上"), (-0.10, "离一年高点 10–20%"), (-0.05, "离一年高点 5–10%"), (INF, "离一年高点 5% 以内"))
KEYS = ("vol", "last", "vix", "trend", "short", "opex", "high", "trend50")
NAMES = {"vol": "波动", "last": "上一周", "vix": "VIX", "trend": "20 日均线", "short": "短周", "opex": "期权到期周",
         "high": "离一年高点", "trend50": "50 日均线"}
BIG, RANGE, TOUCH = 0.02, 0.03, 0.02


def bucket(x: float, edges) -> str:
    return next(name for top, name in edges if x < top)


# ---------- the calendar ----------
def easter(y: int) -> date:
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month = (h + l_ - 7 * m + 114) // 31
    return date(y, month, (h + l_ - 7 * m + 114) % 31 + 1)


def nth(y: int, m: int, weekday: int, n: int) -> date:
    """The n-th `weekday` (Monday 0) of a month; n = -1 for the last one."""
    if n > 0:
        d = date(y, m, 1)
        return d + timedelta(days=(weekday - d.weekday()) % 7 + 7 * (n - 1))
    d = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def observed(d: date) -> date:
    return d - timedelta(days=1) if d.weekday() == 5 else d + timedelta(days=1) if d.weekday() == 6 else d


def holidays(y: int) -> set[date]:
    """New York Stock Exchange full closures (the usual rules; one-off closures are not in it)."""
    out = {observed(date(y, 1, 1)), nth(y, 1, 0, 3), nth(y, 2, 0, 3), easter(y) - timedelta(days=2), nth(y, 5, 0, -1),
           observed(date(y, 7, 4)), nth(y, 9, 0, 1), nth(y, 11, 3, 4), observed(date(y, 12, 25))}
    if y >= 2022:
        out.add(observed(date(y, 6, 19)))
    return {d for d in out if d.year == y}


def sessions(monday: date) -> list[date]:
    days = [monday + timedelta(days=k) for k in range(5)]
    hs = holidays(monday.year) | holidays((monday + timedelta(days=4)).year)
    return [d for d in days if d not in hs]


def opex(monday: date) -> bool:
    """The week holds the monthly options expiry (the third Friday, or the Thursday when that Friday is a holiday)."""
    friday = monday + timedelta(days=4)
    third = nth(friday.year, friday.month, 4, 3)
    return monday <= third <= friday


# ---------- the cards ----------
def monday_of(d: str) -> date:
    x = date.fromisoformat(d)
    return x - timedelta(days=x.weekday())


def weeks(cards: list[dict], vix: dict[str, float]) -> list[dict]:
    """One card per week from the daybook's day cards (date order): the labels' inputs as of the last close before the
    week, and what the week did. The first year only feeds the one-year high."""
    closes = [c["c"] for c in cards]
    groups: dict[date, list[int]] = {}
    for i, c in enumerate(cards):
        groups.setdefault(monday_of(c["day"]), []).append(i)
    order = sorted(groups)
    out = []
    for k, mon in enumerate(order):
        idx = groups[mon]
        pre = idx[0] - 1                                           # the last session before the week
        if k == 0 or pre < 251:
            continue
        prev_pre = groups[order[k - 1]][0] - 1
        w = {"week": mon.isoformat(), "days": len(idx), **inputs(closes, pre, prev_pre, vix.get(cards[pre]["day"]), mon)}
        first, last = cards[idx[0]], cards[idx[-1]]
        w.update(o=first["o"], c=last["c"], h=max(cards[i]["h"] for i in idx), l=min(cards[i]["l"] for i in idx),
                 pc=closes[pre], vix_end=vix.get(last["day"]), complete=(len(idx) == len(sessions(mon))))
        out.append(w)
    return out


def inputs(closes: list[float], pre: int, prev_pre: int | None, vix: float | None, mon: date) -> dict:
    """What was known at the close `pre` about the week that starts on `mon`."""
    rets = [math.log(b / a) for a, b in zip(closes[pre - 20:pre], closes[pre - 19:pre + 1])]
    return {"rv20": round(statistics.pstdev(rets) * math.sqrt(252), 4),
            "above20": closes[pre] >= statistics.mean(closes[pre - 19:pre + 1]),
            "above50": closes[pre] >= statistics.mean(closes[pre - 49:pre + 1]),
            "dd": round(closes[pre] / max(closes[pre - 251:pre + 1]) - 1, 4),
            "last": round(closes[pre] / closes[prev_pre] - 1, 4) if prev_pre is not None and prev_pre >= 0 else None,
            "vix_pre": vix, "short": len(sessions(mon)) < 5, "opex": opex(mon)}


def labels(w: dict) -> dict:
    out = {"vol": bucket(w["rv20"], VOLS), "trend": "20 日均线上方" if w["above20"] else "20 日均线下方",
           "trend50": "50 日均线上方" if w["above50"] else "50 日均线下方", "high": bucket(w["dd"], HIGHS),
           "short": "短周" if w["short"] else "整周", "opex": "期权到期周" if w["opex"] else "不是期权到期周"}
    if w.get("last") is not None:
        out["last"] = bucket(w["last"], LAST)
    if w.get("vix_pre"):
        out["vix"] = bucket(w["vix_pre"], VIXES)
    return out


# What a week did. None = cannot be told (no VIX that day).
OUTCOMES = {
    "big": ("这一周收盘对上周五超过 ±2%", lambda w: abs(w["c"] / w["pc"] - 1) >= BIG),
    "range": ("这一周最高到最低超过 3%", lambda w: (w["h"] - w["l"]) / w["pc"] >= RANGE),
    "up": ("这一周收涨（周五收盘高于上周五）", lambda w: w["c"] > w["pc"]),
    "fade": ("周五收在周一开盘价下方", lambda w: w["c"] < w["o"]),
    "touch": ("这一周盘中碰到上周五收盘 −2%", lambda w: w["l"] <= w["pc"] * (1 - TOUCH)),
    "vix_down": ("周五的 VIX 比上周五低", lambda w: None if not (w.get("vix_end") and w.get("vix_pre")) else w["vix_end"] < w["vix_pre"]),
}


class Book:
    def __init__(self, ws: list[dict]):
        self.weeks = ws
        self.labels = [labels(w) for w in ws]
        self.index: dict[tuple, list[int]] = {}
        for i, lab in enumerate(self.labels):
            for n in range(1, len(KEYS) + 1):
                if all(k in lab for k in KEYS[:n]):
                    self.index.setdefault((n, *(lab[k] for k in KEYS[:n])), []).append(i)

    def alike(self, lab: dict, before: int) -> tuple[list[int], tuple]:
        for n in range(len(KEYS), 0, -1):
            if not all(k in lab for k in KEYS[:n]):
                continue
            idx = self.index.get((n, *(lab[k] for k in KEYS[:n])), [])
            idx = idx[:bisect_left(idx, before)]
            if len(idx) >= MIN_WEEKS or n == 1:
                return idx, KEYS[:n]
        return [], ()

    def share(self, idx, q: str) -> tuple[int, int]:
        ys = [y for y in (OUTCOMES[q][1](self.weeks[i]) for i in idx if self.weeks[i]["complete"]) if y is not None]
        return sum(ys), len(ys)

    def read(self, lab: dict, before: int, base: dict[str, tuple[int, int]] | None = None) -> dict:
        idx, kept = self.alike(lab, before)
        base = base or {q: self.share(range(before), q) for q in OUTCOMES}
        qs = {}
        for q in OUTCOMES:
            k, n = self.share(idx, q)
            bk, bn = base[q]
            b = bk / bn if bn else 0.5
            qs[q] = {"k": k, "n": n, "base": round(b, 4), "p": round((k + PRIOR * b) / (n + PRIOR), 4)}
        return {"kept": list(kept), "weeks": len(idx), "idx": idx, "q": qs}


def score(book: Book) -> dict:
    """Every week after the first WARM read with only the weeks before it, against the plain share of all earlier weeks."""
    run = {q: [0, 0] for q in OUTCOMES}
    rows = {q: [] for q in OUTCOMES}
    for i, w in enumerate(book.weeks):
        if not w["complete"]:
            continue                                               # a week still running teaches nothing yet
        ys = {q: f(w) for q, (_, f) in OUTCOMES.items()}
        if i >= WARM:
            r = book.read(book.labels[i], i, {q: tuple(run[q]) for q in OUTCOMES})
            for q, y in ys.items():
                if y is not None:
                    rows[q].append((w["week"], r["q"][q]["p"], r["q"][q]["base"], int(y)))
        for q, y in ys.items():
            if y is not None:
                run[q][0] += y
                run[q][1] += 1
    out = {}
    for q, rs in rows.items():
        if not rs:
            continue
        brier = lambda xs, j: statistics.mean((x[j] - x[3]) ** 2 for x in xs)     # noqa: E731
        skill = lambda xs: 1 - brier(xs, 1) / brier(xs, 2) if brier(xs, 2) else 0.0  # noqa: E731
        half = len(rs) // 2
        halves = (skill(rs[:half]), skill(rs[half:]))
        out[q] = {"n": len(rs), "from": rs[0][0], "rate": round(statistics.mean(x[3] for x in rs), 4), "brier": round(brier(rs, 1), 5),
                  "brier_base": round(brier(rs, 2), 5), "skill": round(skill(rs), 4), "halves": [round(x, 4) for x in halves],
                  "pass": bool(min(halves) > 0 and skill(rs) >= 0.01)}
    return out


# ---------- the week to read ----------
def target(today: date) -> date:
    """The week to read: the one holding `today`, or the next one on a weekend."""
    mon = today - timedelta(days=today.weekday())
    return mon + timedelta(days=7) if today.weekday() >= 5 else mon


def probe(cards: list[dict], ws: list[dict], vix: dict[str, float], mon: date) -> dict:
    """The labels' inputs of the week starting `mon`, from the closes before it."""
    closes = [c["c"] for c in cards]
    pre = bisect_left([c["day"] for c in cards], mon.isoformat()) - 1
    prev = [w for w in ws if w["week"] < mon.isoformat()]
    prev_pre = None
    if prev:
        last_week = date.fromisoformat(prev[-1]["week"])
        before = [i for i, c in enumerate(cards) if monday_of(c["day"]) < last_week]
        prev_pre = before[-1] if before else None
    return {"week": mon.isoformat(), **inputs(closes, pre, prev_pre, vix.get(cards[pre]["day"]), mon), "pc": closes[pre], "pre_day": cards[pre]["day"]}


def pct(x: float, digits: int = 0) -> str:
    return f"{x:.{digits}%}"


def page(book: Book, sc: dict, w: dict, lab: dict, r: dict, ledger: list[dict]) -> str:
    mon = date.fromisoformat(w["week"])
    days = sessions(mon)
    dropped = [NAMES[k] for k in KEYS if k in lab and k not in r["kept"]]
    ws = [book.weeks[i] for i in r["idx"]]
    out = [f"# 每周档案：{w['week']} 这一周（{len(days)} 个交易日）", "",
           "模拟盘研究，只记录，不是投资建议。先学过去的每一周，再看这一周：标签全是上一周收盘就知道的，找出标签一样的过去的周，看那些周里发生了什么。"
           "每一问再和「不分周、只报底数」比过：历史上不比底数准的问题，读数只是描述，不能当预测。", "",
           f"## 这一周的标签（截至 {w['pre_day']} 收盘）", "",
           "、".join(lab[k] for k in KEYS if k in lab) + "。",
           f"（SPY {w['pc']:.2f}；20 日波动 {pct(w['rv20'], 1)}；上一周 {w['last']:+.2%}；VIX {w['vix_pre']}；离一年高点 {w['dd']:+.1%}。）", "",
           f"过去同样的周 {r['weeks']} 周" + (f"（{ws[0]['week'][:4]}–{ws[-1]['week'][:4]}）" if ws else "") + f"，按「{'、'.join(lab[k] for k in r['kept'])}」找的"
           + (f"；「{'、'.join(dropped)}」放宽了，因为全对上的不到 {MIN_WEEKS} 周" if dropped else "") + "。", "",
           "| 问题 | 这些周里发生的 | 所有周的底数 | 档案的读数 | 这一问历史上档案比底数准吗 |", "|---|---|---|---|---|"]
    for q, v in r["q"].items():
        if not v["n"]:
            continue
        s = sc.get(q)
        verdict = "—" if not s else ("准" if s["pass"] else "不准") + f"（{s['skill']:+.1%}）"
        out.append(f"| {OUTCOMES[q][0]} | {v['k']} / {v['n']}（{pct(v['k'] / v['n'])}） | {pct(v['base'])} | **{pct(v['p'])}** | {verdict} |")
    full = [x for x in ws if x["complete"]]
    if full:
        moves = [abs(x["c"] / x["pc"] - 1) for x in full]
        rng = [(x["h"] - x["l"]) / x["pc"] for x in full]
        out += ["", f"这些周收盘对上周五一般动 {pct(statistics.median(moves), 1)}（不分涨跌的中位数），一周最高到最低一般 {pct(statistics.median(rng), 1)}；"
                f"按上周五 {w['pc']:.2f} 算，大约是 ±{w['pc'] * statistics.median(moves):.0f} 美元的收盘、{w['pc'] * statistics.median(rng):.0f} 美元的高低差。",
                "最近的几周：" + "；".join(f"{x['week']} {x['c'] / x['pc'] - 1:+.1%}" for x in full[-5:][::-1]) + "。"]
    out += ["", "## 这样学，比底数准吗", "",
            f"把历史走一遍：从第 {WARM + 1} 个有标签的周（{sc[next(iter(sc))]['from'] if sc else '—'}）起，每一周只用它之前的周来读，和只报底数比（Brier 分，越低越准）。"
            "过线 = 前后两半都比底数准，全期至少好 1%。线和标签是 11-23 先写死的。", "",
            "| 问题 | 周数 | 发生的比例 | 档案 | 只报底数 | 好多少（前半 / 后半） | 过线 |", "|---|---|---|---|---|---|---|"]
    for q, s in sc.items():
        out.append(f"| {OUTCOMES[q][0]} | {s['n']} | {pct(s['rate'])} | {s['brier']:.4f} | {s['brier_base']:.4f} | "
                   f"{s['skill']:+.1%}（{s['halves'][0]:+.1%} / {s['halves'][1]:+.1%}） | {'过' if s['pass'] else '不过'} |")
    passed = [OUTCOMES[q][0] for q, s in sc.items() if s["pass"]]
    out += ["", f"**{len(sc)} 个问题过了 {len(passed)} 个" + (f"：{'；'.join(passed)}" if passed else "") + "。**"]
    done = [x for x in ledger if x["week"] < w["week"]]
    if done:
        out += ["", "## 事先记下的读数和实际", "", "| 周 | 记的时候 | 问题 | 读数 | 实际 |", "|---|---|---|---|---|"]
        by = {x["week"]: x for x in book.weeks}
        for x in done[-8:]:
            real = by.get(x["week"])
            for q, p in x["p"].items():
                y = OUTCOMES[q][1](real) if real and real["complete"] else None
                out.append(f"| {x['week']} | {x['written'][:16]} | {OUTCOMES[q][0]} | {pct(p)} | {'—' if y is None else ('发生了' if y else '没有')} |")
    return "\n".join(out) + "\n"


def load_vix(start: datetime) -> dict[str, float]:
    now = datetime.now(timezone.utc)
    return {t.strftime("%Y-%m-%d"): round(c, 2) for t, _, _, _, c in db.yahoo("%5EVIX", "1d", start, now + timedelta(days=1)) if c}


def run(today: date | None = None, cards: list[dict] | None = None, vix: dict[str, float] | None = None, write: bool = True, out_dir: Path = OUT) -> dict:
    today = today or datetime.now(db.ET).date()
    cards = cards if cards is not None else db.load()
    vix = vix if vix is not None else load_vix(datetime(2015, 1, 2, tzinfo=timezone.utc))
    mon = target(today)
    ws = [w for w in weeks(cards, vix) if w["week"] < mon.isoformat()]
    book = Book(ws)
    sc = score(book)
    w = probe(cards, ws, vix, mon)
    lab = labels(w)
    r = book.read(lab, len(ws))
    lpath = out_dir / "ledger.json"
    ledger = json.loads(lpath.read_text())["reads"] if lpath.exists() else []
    if not any(x["week"] == w["week"] for x in ledger):
        ledger.append({"week": w["week"], "written": datetime.now(db.ET).isoformat(timespec="minutes"),
                       "labels": lab, "p": {q: v["p"] for q, v in r["q"].items() if v["n"]}})
    text = page(book, sc, w, lab, r, ledger)
    rep = {"week": w["week"], "labels": lab, "inputs": {k: v for k, v in w.items() if k != "week"}, "kept": r["kept"], "alike": r["weeks"],
           "read": r["q"], "score": sc}
    if write:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{w['week']}.md").write_text(text)
        (out_dir / f"{w['week']}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1) + "\n")
        lpath.write_text(json.dumps({"reads": ledger}, ensure_ascii=False, indent=1) + "\n")
    return {**rep, "page": text}


def main() -> None:
    day = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else None
    print(run(day)["page"])


if __name__ == "__main__":
    sys.exit(main())
