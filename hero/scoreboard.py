"""记分卡总账: every hand-kept scorecard recomputed from its raw records, beside the baseline it has to beat (owner, 11-22:
develop more features in parallel). The read-only review of 11-22 found a Brier summary over 10 days while its table had
11, a Disney open taken from the first one-minute bar instead of the daily open, and a follow-card total where both
judgment calls went the author's way. The Saturday check redid these by hand; this module redoes them every time.

No score is copied from a summary line. Each one is recomputed from the rows, the outcome comes from prices (the
daybook's SPY cards, data/daybook/spy.json; Yahoo daily bars for single stocks), and a recorded summary number is read
only to be compared with the recomputed one. Every difference goes to the top of the page.

  briefing   macro/briefings/scores.csv: `against_gap` re-verified from the day card (open against close; a flat open
             within FLAT is not judged), against "always no" and the daybook's base rate; the SPY direction calls
             (close against the prior close) against "follow the gap"; the other SPY rows that prices can settle.
  pm_dir     exchange/claude/pm-dir-scores.md: the noon-to-close call from the table's own prices, against "always up".
  tomorrow   exchange/claude/tomorrow-scores.md: Brier for "big move" (|close / prior close - 1| >= 1%) and for the
             direction, evening and morning versions, against flat 25%, flat at the realised rate of the same days
             (hindsight), flat 55% and the gap rule (0.68 after a gap up, 0.30 after a gap down).
  follow     posts/follow/scorecard.md: the per-$1 totals; a row whose own price limit was broken or whose line was
             barely crossed is shown as booked, strict (each call against the card) and mechanical (the line only).
  earnings   research/earnings-forecast/*.judgment.json from EARN_FROM: the direction call against the reaction-day
             open (the daily bar's open over the prior close), against always down, always up and the day's SPY gap.
  sniper     journal-s500/sniper/ from SNIPER_FROM: did open-to-close (daily bars) go the pick's way; first vs second.
  weekbook   research/weekbook/ledger.json: the weekly reads written down beforehand against what the week did.

Fewer than MIN_N rows is "不够". Only the earnings and sniper cards need the network; a failed fetch says "没取到".
Record only: nothing here places an order or changes a rule.
Run: python -m hero.scoreboard [YYYY-MM-DD]   (writes research/scoreboard/<day>.md and .json)
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from hero import daybook as db
from hero import robust

ROOT = Path(__file__).resolve().parent.parent
OUT = Path("research") / "scoreboard"
MIN_N = 20
FLAT = db.FLAT                     # a gap within ±0.1% is not judged (the daybook's "against" question)
BIG = db.BIG                       # "big move": 1% or more, close against the prior close
GAP_RULE = (0.68, 0.30)            # the tomorrow card's gap rule: p_up after a gap up, after a gap down
EARN_FROM = "2026-11-13"           # from this day only the names we care about get a judgment (CLAUDE.md, 11-13)
SNIPER_FROM = "2026-11-03"         # wrong_if is required from this day (journal-s500/sniper/README.md)
PRICE_TOL = 0.06                   # dollars: a recorded SPY price this far from the daily bar is a difference
MOVE_TOL = 0.0006                  # a recorded move (rounded to 0.01%) this far from the recomputed one
TEXT_TOL = 0.0016                  # a move written in prose (rounded to 0.1%)
BRIER_TOL = 0.0006                 # a Brier score rounded to three places
MONEY_TOL = 0.006                  # a per-$1 total rounded to two places
OPEN_TOL = 0.001                   # a single stock's recorded open this far (relative) from the daily bar's open
NUM = r"[+\-−]?\d+(?:\.\d+)?"


# ---------- small helpers ----------
def num(s: str) -> float:
    return float(s.replace("−", "-").replace("+", ""))


def prose_tol(s: str) -> float:
    """How far a move written in prose may sit from the recomputed one: half its last digit, plus 0.1 point."""
    d = len(s.split(".")[1]) if "." in s else 0
    return 0.5 * 10 ** -d / 100 + 0.001


def side(x: float) -> int:
    return 1 if x > 0 else -1 if x < 0 else 0


def brier(pairs: list[tuple[float, bool]]) -> float | None:
    return sum((p - float(y)) ** 2 for p, y in pairs) / len(pairs) if pairs else None


def pct(x: float | None, digits: int = 2, sign: bool = True) -> str:
    return "—" if x is None else (f"{x:+.{digits}%}" if sign else f"{x:.{digits}%}")


def rate(k: int, n: int) -> str:
    return f"{k}/{n}" + (f"（{k / n:.0%}）" if n else "")


def iso(md: str, today: date) -> str:
    """'11-16' -> '2026-11-16': the latest such day not after `today`."""
    m, d = (int(x) for x in md.split("-"))
    y = today.year if (m, d) <= (today.month, today.day) else today.year - 1
    return f"{y}-{m:02d}-{d:02d}"


def tables(text: str) -> list[list[list[str]]]:
    """Every markdown table in `text` as rows of cells (the header first, the --- line dropped)."""
    out, cur = [], []
    for line in text.splitlines() + [""]:
        s = line.strip()
        if s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                cur.append(cells)
        elif cur:
            out.append(cur)
            cur = []
    return out


def col(head: list[str], test) -> int | None:
    return next((i for i, h in enumerate(head) if test(h)), None)


def enough(n: int) -> str:
    return "够" if n >= MIN_N else f"不够（{n} 次，不到 {MIN_N}）"


def span(days: list[str]) -> list[str] | None:
    return [min(days), max(days)] if days else None


def headline(label: str, n: int, ours: str, best: str, beat: bool | str | None, days: list[str]) -> dict:
    """One line of the overview. `beat`: True / False, "平" for a tie, None when there is nothing to compare."""
    return {"label": label, "n": n, "ours": ours, "best": best, "beat": beat, "enough": enough(n), "range": span(days)}


def vs(ours: float | None, best: float | None, lower: bool = False) -> bool | str | None:
    """Did we beat the best baseline: higher hits, or a lower score when `lower`; "平" when level."""
    if ours is None or best is None:
        return None
    if abs(ours - best) < 1e-12:
        return "平"
    return ours < best if lower else ours > best


def verdict(beat) -> str:
    return "—" if beat is None else "打平" if beat == "平" else "赢" if beat else "没赢"


def load_cards(root: Path) -> dict[str, dict]:
    p = root / "data" / "daybook" / "spy.json"
    return {c["day"]: c for c in json.loads(p.read_text())["cards"]} if p.exists() else {}


# ---------- prices for single stocks ----------
class Fetcher:
    """Daily (open, close) by day for one symbol, from Yahoo through hero.daybook.yahoo, cached. After three failures in
    a row it stops trying (the network is down) and every later symbol reads as not fetched."""

    def __init__(self, get=None):
        self.get = get or self.yahoo
        self.cache: dict[str, dict | None] = {}
        self.fails = 0

    @staticmethod
    def yahoo(sym: str, start: date, end: date) -> dict[str, tuple[float, float]]:
        bars = db.yahoo(sym, "1d", datetime(start.year, start.month, start.day, tzinfo=db.ET),
                        datetime(end.year, end.month, end.day, tzinfo=db.ET) + timedelta(days=1))
        return {t.strftime("%Y-%m-%d"): (o, c) for t, o, _, _, c in bars}

    def bars(self, sym: str, start: date, end: date) -> dict[str, tuple[float, float]] | None:
        key = f"{sym}|{start}|{end}"
        if key in self.cache:
            return self.cache[key]
        got = None
        if self.fails < 3:
            try:
                got = self.get(sym, start, end) or None
                self.fails = 0 if got else self.fails + 1
            except Exception:                                       # network, a bad symbol, a changed format
                self.fails += 1
        self.cache[key] = got
        return got


def day_bar(fetch: Fetcher, sym: str, day: str) -> tuple[dict | None, str | None]:
    """The day's open and close and the close before it, or why not."""
    d = date.fromisoformat(day)
    got = fetch.bars(sym, d - timedelta(days=12), d + timedelta(days=1))
    if not got:
        return None, "没取到"
    days = sorted(got)
    if day not in got:
        return None, "那天没有日线"
    i = days.index(day)
    o, c = got[day]
    return {"o": o, "c": c, "pc": got[days[i - 1]][1] if i else None}, None


# ---------- 1. the briefing: against_gap and the SPY direction calls ----------
def against_gap(rows: list[dict], cards: dict[str, dict]) -> dict:
    out, disc, unparsed = [], [], []
    for r in rows:
        if r["kind"] != "against_gap":
            continue
        day, text = r["date"], r["prediction"].strip()
        call = "no" if text.startswith("不会") else "yes" if text.startswith("会") else None
        c = cards.get(day)
        if call is None or c is None:
            unparsed.append(f"{day} against_gap「{text}」：" + ("读不出会不会" if call is None else "没有这天的 SPY 日线卡"))
            continue
        rec = r["result"].strip()
        m = re.search(r"开\s*(\d+(?:\.\d+)?)\s*收\s*(\d+(?:\.\d+)?)", r.get("source") or "")
        if m and (abs(float(m.group(1)) - c["o"]) > PRICE_TOL or abs(float(m.group(2)) - c["c"]) > PRICE_TOL):
            disc.append(f"{day}：记的开 {m.group(1)} 收 {m.group(2)}，日线是开 {c['o']} 收 {c['c']}")
        if abs(c["gap"]) <= FLAT:
            if rec in ("hit", "miss"):
                disc.append(f"{day}：缺口 {pct(c['gap'])} 在 ±0.1% 以内，本该不判，记的是 {rec}")
            continue
        against = side(c["c"] - c["o"]) != side(c["gap"])
        hit = (call == "yes") == against
        if rec in ("hit", "miss") and (rec == "hit") != hit:
            disc.append(f"{day}：填「{'会' if call == 'yes' else '不会'}」，缺口 {pct(c['gap'])}、开到收 {pct(c['c'] / c['o'] - 1)}"
                        f"，重算是{'对' if hit else '错'}，记的是 {rec}")
        out.append({"day": day, "call": call, "gap": c["gap"], "oc": round(c["c"] / c["o"] - 1, 5), "against": against,
                    "hit": hit, "recorded": rec})
    n = len(out)
    k = sum(x["hit"] for x in out)
    k_no = sum(not x["against"] for x in out)
    first = min((x["day"] for x in out), default=None)
    past = [c for d, c in cards.items() if first and d < first and abs(c["gap"]) > FLAT]
    base = sum(side(c["c"] - c["o"]) != side(c["gap"]) for c in past) / len(past) if past else None
    left = MIN_N - n
    lines = [f"简报 09:07 写的「今天会不会反着缺口走」（会 = SPY 收盘在开盘价的另一边；缺口 ±0.1% 以内不判），按 SPY 日线卡重算。"
             f"停的线（CLAUDE.md）：{MIN_N} 次里对不到 13 次就提出停。", "",
             "| 日期 | 填的 | 缺口 | 开到收 | 反着走了吗 | 重算 | 记的 |", "|---|---|---|---|---|---|---|"]
    lines += [f"| {x['day']} | {'会' if x['call'] == 'yes' else '不会'} | {pct(x['gap'])} | {pct(x['oc'])} | {'是' if x['against'] else '否'} | "
              f"{'对' if x['hit'] else '错'} | {x['recorded']} |" for x in out]
    lines += ["", f"- 我们：{rate(k, n)}。", f"- 对照「一律填不会」：{rate(k_no, n)}。"]
    if base is not None:
        lines.append(f"- 每日档案的底数：{first} 之前缺口 0.1% 以上的 {len(past)} 天里反着走 {base:.1%}，"
                     f"所以「一律不会」长期大约对 {1 - base:.1%}；这 {n} 次按这个比例该对 {n * (1 - base):.1f} 次。")
    if n and left > 0:
        need = 13 - k
        lines.append(f"- 离 {MIN_N} 次还差 {left} 次；要过 13 次的线，剩下的要对 {max(need, 0)} 次"
                     + ("（已经到不了）。" if need > left else "。"))
    best = max([("一律不会", k_no / n if n else 0)] + ([("底数（一律不会的长期命中）", 1 - base)] if base is not None else []),
               key=lambda t: t[1])
    return {"key": "against_gap", "title": "简报：今天会不会反着缺口走（against_gap）", "n": n, "range": span([x["day"] for x in out]),
            "ours": {"hits": k, "n": n},
            "baselines": [{"name": "一律不会", "hits": k_no, "n": n, "beat": k > k_no}] +
                         ([{"name": "每日档案底数", "rate": round(1 - base, 4), "beat": n > 0 and k / n > 1 - base}] if base is not None else []),
            "headlines": [headline("反着缺口走（against_gap）", n, rate(k, n), f"{best[0]} {best[1]:.0%}",
                                   vs(k / n, best[1]) if n else None, [x["day"] for x in out])],
            "rows": out, "discrepancies": disc, "unparsed": unparsed, "lines": lines}


SPY_DIR = (re.compile(r"^SPY\s*(?:今天)?\s*收盘(上涨|下跌)$"), re.compile(r"^SPY\s*(?:今天)?\s*收盘(高于|低于)(?:昨收|上周五)"))
SPY_RANGE = ((re.compile(r"^SPY\s*(?:今天)?\s*收盘涨跌幅在\s*±(\d+(?:\.\d+)?)%\s*以内"), lambda r, x: abs(r) <= x),
             (re.compile(r"^SPY\s*(?:今天)?\s*收盘涨幅在\s*0%\s*到\s*\+(\d+(?:\.\d+)?)%"), lambda r, x: 0 <= r <= x),
             (re.compile(r"^SPY\s*(?:今天)?\s*收盘对昨收的涨跌幅超过\s*±(\d+(?:\.\d+)?)%"), lambda r, x: abs(r) > x))


def spy_direction(rows: list[dict], cards: dict[str, dict]) -> dict:
    out, checks, disc, unparsed, other = [], [], [], [], 0
    for r in rows:
        if r["kind"] != "prediction":
            continue
        day, text, rec = r["date"], r["prediction"].strip(), r["result"].strip()
        up = None
        for rx in SPY_DIR:
            m = rx.match(text)
            if m:
                up = m.group(1) in ("上涨", "高于")
        rng = next(((rx, f, float(m.group(1)) / 100) for rx, f in SPY_RANGE if (m := rx.match(text))), None)
        if up is None and rng is None:
            if text.startswith("SPY"):
                unparsed.append(f"{day}「{text}」：是 SPY 的，但读不出是方向还是幅度")
            else:
                other += 1
            continue
        c = cards.get(day)
        if c is None:
            unparsed.append(f"{day}「{text}」：没有这天的 SPY 日线卡")
            continue
        move = c["c"] / c["pc"] - 1
        if up is not None:
            hit = (c["c"] > c["pc"]) == up
            gap_hit = (c["c"] > c["pc"]) == (c["gap"] > 0)
            out.append({"day": day, "up": up, "move": round(move, 5), "gap": c["gap"], "hit": hit, "gap_hit": gap_hit,
                        "always_up": c["c"] > c["pc"], "recorded": rec})
        else:
            hit = rng[1](move, rng[2])
            checks.append({"day": day, "text": text, "move": round(move, 5), "hit": hit, "recorded": rec})
        if rec in ("hit", "miss") and (rec == "hit") != hit:
            disc.append(f"{day}「{text}」：日线 {c['pc']} → {c['c']}（{pct(move)}），重算是{'对' if hit else '错'}，记的是 {rec}")
    n = len(out)
    k, kg, ku = (sum(x[f] for x in out) for f in ("hit", "gap_hit", "always_up"))
    lines = ["简报里「SPY 收盘对昨收涨还是跌」的那几行（prediction 一类），按 SPY 日线卡重算，和「跟着缺口走」（高开猜涨、低开猜跌）比。",
             "",
             f"- 我们：{rate(k, n)}。", f"- 跟着缺口走：{rate(kg, n)}。", f"- 一律猜涨：{rate(ku, n)}。",
             f"- 猜的方向和缺口同号：{rate(sum(x['up'] == (x['gap'] > 0) for x in out), n)}。", "",
             "| 日期 | 猜的 | 缺口 | 收盘对昨收 | 重算 | 缺口规则 | 记的 |", "|---|---|---|---|---|---|---|"]
    lines += [f"| {x['day']} | {'涨' if x['up'] else '跌'} | {pct(x['gap'])} | {pct(x['move'])} | {'对' if x['hit'] else '错'} | "
              f"{'对' if x['gap_hit'] else '错'} | {x['recorded']} |" for x in out]
    if checks:
        kc = sum(x["hit"] for x in checks)
        lines += ["", f"顺手核对的 SPY 幅度预测 {len(checks)} 行（重算 {kc} 对；只核记的对错，不算成绩）。"]
    if other:
        lines += ["", f"另外 {other} 行是油价、利率、纳指对标普、个股，手里没有价格，不核。"]
    best = max([("跟着缺口走", kg), ("一律猜涨", ku)], key=lambda t: t[1])
    return {"key": "spy_direction", "title": "简报：SPY 收盘对昨收的方向", "n": n, "range": span([x["day"] for x in out]),
            "ours": {"hits": k, "n": n},
            "baselines": [{"name": "跟着缺口走", "hits": kg, "n": n, "beat": k > kg}, {"name": "一律猜涨", "hits": ku, "n": n, "beat": k > ku}],
            "headlines": [headline("简报的 SPY 方向", n, rate(k, n), f"{best[0]} {rate(best[1], n)}", vs(k, best[1]) if n else None,
                                   [x["day"] for x in out])],
            "rows": out, "checks": checks, "discrepancies": disc, "unparsed": unparsed, "lines": lines}


# ---------- 2. pm_dir ----------
def pm_dir(text: str, cards: dict[str, dict], today: date) -> dict:
    out, disc, unparsed = [], [], []
    tab = next((t for t in tables(text) if any("填的" in h for h in t[0])), None)
    if not tab:
        return empty("pm_dir", "午盘：12:00 到收盘的方向（pm_dir）", "没找到记分表")
    h = tab[0]
    ix = {"day": col(h, lambda s: s == "日期"), "p12": col(h, lambda s: s.startswith("12:00 的")), "call": col(h, lambda s: s == "填的"),
          "close": col(h, lambda s: s == "收盘"), "m12": col(h, lambda s: s.startswith("12:00 到")), "h12": col(h, lambda s: s == "对错"),
          "pw": col(h, lambda s: s.startswith("写的时候的")), "mw": col(h, lambda s: s.startswith("写的时候到")),
          "hw": col(h, lambda s: s.startswith("按写的"))}
    for row in tab[1:]:
        try:
            day = iso(re.search(r"\d{1,2}-\d{1,2}", row[ix["day"]]).group(0), today)
            call = {"涨": 1, "跌": -1}[row[ix["call"]].strip()]
            p12, close = num(re.search(NUM, row[ix["p12"]]).group(0)), num(re.search(NUM, row[ix["close"]]).group(0))
        except (AttributeError, KeyError, TypeError, IndexError, ValueError):
            unparsed.append(f"pm_dir 一行读不出：{' | '.join(row)}")
            continue
        x = {"day": day, "call": call, "p12": p12, "close": close, "m12": round(close / p12 - 1, 5)}
        x["h12"] = side(x["m12"]) == call
        c = cards.get(day)
        if c and abs(c["c"] - close) > PRICE_TOL:
            disc.append(f"{day}：表里收盘 {close}，日线 {c['c']}")
        rec = row[ix["h12"]].strip() if ix["h12"] is not None else ""
        if rec in ("对", "错") and (rec == "对") != x["h12"]:
            disc.append(f"{day}：12:00 {p12} → 收 {close}（{pct(x['m12'])}），重算是{'对' if x['h12'] else '错'}，记的是{rec}")
        m = re.search(NUM + "%", row[ix["m12"]]) if ix["m12"] is not None else None
        if m and abs(num(m.group(0)[:-1]) / 100 - x["m12"]) > MOVE_TOL:
            disc.append(f"{day}：表里 12:00 到收盘写 {m.group(0)}，按表里的价是 {pct(x['m12'])}")
        mw = re.search(r"\d+(?:\.\d+)?", row[ix["pw"]]) if ix["pw"] is not None else None
        if mw:
            pw = float(mw.group(0))
            x.update(pw=pw, mw=round(close / pw - 1, 5))
            x["hw"] = side(x["mw"]) == call
            recw = row[ix["hw"]].strip() if ix["hw"] is not None else ""
            if recw in ("对", "错") and (recw == "对") != x["hw"]:
                disc.append(f"{day}：写的时候 {pw} → 收 {close}（{pct(x['mw'])}），重算是{'对' if x['hw'] else '错'}，记的是{recw}")
        out.append(x)
    n = len(out)
    k12 = sum(x["h12"] for x in out)
    u12 = sum(x["m12"] > 0 for x in out)
    w = [x for x in out if "hw" in x]
    kw, uw = sum(x["hw"] for x in w), sum(x["mw"] > 0 for x in w)
    for rx, val, what in ((r"按 12:00 的价\s*(\d+)\s*对\s*(\d+)\s*错", (k12, n - k12), "按 12:00"),
                          (r"一律填「涨」是\s*(\d+)\s*对\s*(\d+)\s*错", (u12, n - u12), "一律涨"),
                          (r"按写的那一刻的价是\s*(\d+)\s*对\s*(\d+)\s*错", (kw, len(w) - kw), "按写的时候")):
        m = re.search(rx, text)
        if m and (int(m.group(1)), int(m.group(2))) != val:
            disc.append(f"累计那句：{what} 记的是 {m.group(1)} 对 {m.group(2)} 错，重算是 {val[0]} 对 {val[1]} 错")
    m = re.search(r"累计：\s*(\d+)\s*次", text)
    if m and int(m.group(1)) != n:
        disc.append(f"累计那句写 {m.group(1)} 次，表里有 {n} 行")
    lines = ["12:22 午盘写「12:00 到收盘 SPY 涨还是跌」，用表里自己的价重算；11-23 起正式口径是「写的那一刻的价」，12:00 那一列留着对照。"
             f"停的线：{MIN_N} 次里对不到 13 次就提出停。", "",
             f"- 按写的时候：我们 {rate(kw, len(w))}，一律填涨 {rate(uw, len(w))}。",
             f"- 按 12:00：我们 {rate(k12, n)}，一律填涨 {rate(u12, n)}。"]
    return {"key": "pm_dir", "title": "午盘：12:00 到收盘的方向（pm_dir）", "n": n, "range": span([x["day"] for x in out]),
            "ours": {"at_write": [kw, len(w)], "at_noon": [k12, n]},
            "baselines": [{"name": "一律涨（按写的时候）", "hits": uw, "n": len(w), "beat": kw > uw},
                          {"name": "一律涨（按 12:00）", "hits": u12, "n": n, "beat": k12 > u12}],
            "headlines": [headline("午盘方向（按写的时候）", len(w), rate(kw, len(w)), f"一律涨 {rate(uw, len(w))}", vs(kw, uw) if w else None,
                                   [x["day"] for x in w]),
                          headline("午盘方向（按 12:00）", n, rate(k12, n), f"一律涨 {rate(u12, n)}", vs(k12, u12) if n else None,
                                   [x["day"] for x in out])],
            "rows": out, "discrepancies": disc, "unparsed": unparsed, "lines": lines}


def empty(key: str, title: str, why: str) -> dict:
    return {"key": key, "title": title, "n": 0, "range": None, "ours": None, "baselines": [], "headlines": [],
            "rows": [], "discrepancies": [], "unparsed": [why], "lines": [why]}


# ---------- 3. tomorrow ----------
PAIR = r"(?:晚上版\s*(\d+)%、早上版\s*(\d+)%|晚上版和早上版都是\s*(\d+)%)"


def _pair(m) -> tuple[float, float] | None:
    if not m:
        return None
    if m.group(3):
        return int(m.group(3)) / 100, int(m.group(3)) / 100
    return int(m.group(1)) / 100, int(m.group(2)) / 100


def tomorrow(text: str, cards: dict[str, dict], today: date) -> dict:
    fc: dict[str, dict] = {}
    rec: dict[str, dict] = {}
    disc, unparsed, blank = [], [], []

    def put(day, key, val, where):
        old = fc.setdefault(day, {}).get(key)
        if old is not None and abs(old[0] - val) > 1e-9:
            disc.append(f"{day} {NAMES_T[key]}：{old[1]}写 {old[0]:.0%}，{where}写 {val:.0%}（用{old[1]}的）")
        elif old is None:
            fc[day][key] = (val, where)

    tab = next((t for t in tables(text) if any("晚上版" in h for h in t[0]) and any("实际" in h for h in t[0])), None)
    if tab:
        h = tab[0]
        ie, im = col(h, lambda s: s.startswith("晚上版")), col(h, lambda s: s.startswith("早上版"))
        imv, idr, ibg = col(h, lambda s: "涨跌幅" in s), col(h, lambda s: "方向" in s), col(h, lambda s: "发生了吗" in s)
        for row in tab[1:]:
            m = re.search(r"预测\s*(\d{1,2}-\d{1,2})", row[0])
            if not m:
                unparsed.append(f"明天预测的表里一行读不出预测的是哪天：{row[0]}")
                continue
            day = iso(m.group(1), today)
            for i, ver in ((ie, "eve"), (im, "mor")):
                pm = re.match(r"\s*(\d+)%\s*/\s*(\d+)%", row[i])
                if pm:
                    put(day, ver + "_big", int(pm.group(1)) / 100, "表里")
                    put(day, ver + "_up", int(pm.group(2)) / 100, "表里")
                else:
                    blank.append((day, ver, row[i]))
            mv = re.search(NUM + "%", row[imv])
            if mv:
                rec.setdefault(day, {}).update(move=num(mv.group(0)[:-1]) / 100,
                                               up={"涨": True, "跌": False}.get(row[idr].strip()),
                                               big={"发生了": True, "没有": False}.get(row[ibg].strip()))
    for para in re.split(r"\n\s*\n", text):
        m = re.match(r"\s*第.{1,3}天（(\d{1,2}-\d{1,2})", para)
        if not m:
            continue
        day = iso(m.group(1), today)
        big = _pair(re.search(r"大波动[^。]*?" + PAIR, para))
        dirn = _pair(re.search(r"方向\s*" + PAIR, para))
        for pair, q in ((big, "big"), (dirn, "up")):
            if pair:
                put(day, "eve_" + q, pair[0], "段落里")
                put(day, "mor_" + q, pair[1], "段落里")
        oc = re.search(r"开到收：我填\s*(\d+)%", para)
        if oc:
            put(day, "oc", int(oc.group(1)) / 100, "段落里")
        a = re.search(r"实际\s*(" + NUM + r")%、(收涨|收跌)(?:、开到收\s*(" + NUM + r")%)?", para)
        if a:
            r = rec.setdefault(day, {})
            r.setdefault("move", num(a.group(1)) / 100)
            r.setdefault("up", a.group(2) == "收涨")
            if a.group(3):
                r["oc"] = num(a.group(3)) / 100
        if re.search(r"大波动没发生", para):
            rec.setdefault(day, {}).setdefault("big", False)
        elif re.search(r"大波动(?:算)?发生了", para):
            rec.setdefault(day, {}).setdefault("big", True)
    filled = []
    for day, ver, cell in blank:
        name = "晚上版" if ver == "eve" else "早上版"
        if fc.get(day, {}).get(ver + "_big") is None:
            unparsed.append(f"{day} 的{name}：表里写的是「{cell}」，段落里也没有数字")
        else:
            filled.append(f"{day} 的{name}（表里写「{cell}」）")
    # the morning p_up is the open-to-close forecast where no separate number was written (as the 11-15 check did)
    for day, f in fc.items():
        if "oc" not in f and "mor_up" in f:
            f["oc"] = (f["mor_up"][0], "早上版的 p_up")
    rows = []
    for day in sorted(fc):
        c = cards.get(day)
        if c is None:
            unparsed.append(f"{day}：没有这天的 SPY 日线卡，打不了分")
            continue
        y = {"big": abs(c["c"] / c["pc"] - 1) >= BIG, "up": c["c"] > c["pc"], "oc": c["c"] > c["o"]}
        rows.append({"day": day, **{k: v[0] for k, v in fc[day].items()}, "y_big": y["big"], "y_up": y["up"], "y_oc": y["oc"],
                     "gap": c["gap"], "move": round(c["c"] / c["pc"] - 1, 5)})
        r = rec.get(day, {})
        mv = c["c"] / c["pc"] - 1
        if r.get("move") is not None and abs(r["move"] - mv) > MOVE_TOL:
            disc.append(f"{day}：记的实际涨跌 {pct(r['move'])}，日线 {pct(mv)}")
        if r.get("up") is not None and r["up"] != y["up"]:
            disc.append(f"{day}：记的方向是{'涨' if r['up'] else '跌'}，日线是{'涨' if y['up'] else '跌'}")
        if r.get("big") is not None and r["big"] != y["big"]:
            disc.append(f"{day}：记的大波动{'发生了' if r['big'] else '没有'}，日线 {pct(mv)}")
        if r.get("oc") is not None and abs(r["oc"] - (c["c"] / c["o"] - 1)) > MOVE_TOL:
            disc.append(f"{day}：记的开到收 {pct(r['oc'])}，日线 {pct(c['c'] / c['o'] - 1)}")

    def score(key: str, yk: str) -> tuple[list[dict], float | None]:
        rs = [x for x in rows if x.get(key) is not None]
        return rs, brier([(x[key], x[yk]) for x in rs])

    def flat(rs, yk, p):
        return brier([(p, x[yk]) for x in rs])

    def gap_rule(rs, yk):
        return brier([(GAP_RULE[0] if x["gap"] > 0 else GAP_RULE[1] if x["gap"] < 0 else 0.5, x[yk]) for x in rs])

    def baselines(rs, q):
        yk = "y_" + q
        r_ = sum(x[yk] for x in rs) / len(rs) if rs else 0
        out = [("事后的水平（这几天的比例 " + f"{r_:.0%}）", flat(rs, yk, r_))]
        if q == "big":
            out.insert(0, ("一律 25%", flat(rs, yk, 0.25)))
        else:
            out.insert(0, ("一律 55%", flat(rs, yk, 0.55)))
        if q == "up":
            out.append(("缺口死规则（高开 68%、低开 30%）", gap_rule(rs, yk)))
        return out

    scores, heads = [], []
    for key, q, label in (("eve_big", "big", "大波动（晚上版）"), ("mor_big", "big", "大波动（早上版）"), ("eve_up", "up", "方向（晚上版）"),
                          ("mor_up", "up", "方向（早上版）"), ("oc", "oc", "开到收（早上）")):
        rs, b = score(key, "y_" + q)
        if not rs:
            continue
        bl = baselines(rs, q)
        best = min(bl, key=lambda t: t[1])
        scores.append({"label": label, "n": len(rs), "brier": round(b, 4), "range": span([x["day"] for x in rs]),
                       "baselines": [{"name": nm, "brier": round(v, 4), "beat": b < v} for nm, v in bl]})
        heads.append(headline("明天预测：" + label, len(rs), f"Brier {b:.3f}", f"{best[0]} {best[1]:.3f}", vs(b, best[1], lower=True),
                              [x["day"] for x in rs]))
    # the recorded summary table, compared cell by cell
    m = re.search(r"Brier 分数（到目前为止，(\d+) 天）", text)
    summ = next((t for t in tables(text) if t[0] and t[0][0] == "版本"), None)
    if summ:
        h = summ[0]
        qs = {i: ("big" if "大波动" in s else "oc" if "开到收" in s else "up" if "方向" in s else None) for i, s in enumerate(h)}
        union = {q: [x for x in rows if any(x.get(k) is not None for k in KEYS_T[q])] for q in ("big", "up", "oc")}
        if m:
            for q, rs in union.items():
                if rs and int(m.group(1)) != len(rs):
                    disc.append(f"Brier 汇总表写「{m.group(1)} 天」，{NAMES_Q[q]}能打分的有 {len(rs)} 天")
        for row in summ[1:]:
            lab = row[0]
            for i, cell in enumerate(row[1:], 1):
                q = qs.get(i)
                mv = re.search(r"\d\.\d+", cell)
                if not q or not mv:
                    continue
                recv = float(mv.group(0))
                if lab.startswith("晚上版") or lab.startswith("早上版"):
                    key = ("eve_" if lab.startswith("晚上版") else "mor_") + q if q != "oc" else "oc"
                    if q == "oc" and lab.startswith("晚上版"):
                        continue
                    ours = score(key, "y_" + q)[1]
                elif (fm := re.search(r"一律\s*(\d+)%", lab)):
                    ours = flat(union[q], "y_" + q, int(fm.group(1)) / 100) if union[q] else None
                    real = sum(x["y_" + q] for x in union[q]) / len(union[q]) if union[q] else None
                    if real is not None and ("事后" in lab or "水平" in lab) and abs(int(fm.group(1)) / 100 - real) > 0.005:
                        disc.append(f"Brier 汇总表「{lab}」说是这几天的水平，这 {len(union[q])} 天{NAMES_Q[q]}实际的比例是 {real:.0%}"
                                    f"（按 {real:.0%} 一律填是 {flat(union[q], 'y_' + q, real):.3f}）")
                elif "缺口死规则" in lab and q == "up":
                    ours = gap_rule(union[q], "y_" + q)
                else:
                    unparsed.append(f"Brier 汇总表的「{lab}」读不出是哪一种")
                    continue
                if ours is not None and abs(ours - recv) > BRIER_TOL:
                    disc.append(f"Brier 汇总表「{lab}」的{NAMES_Q[q]}：记的 {recv:.3f}，重算 {ours:.3f}（{len(union[q])} 天）")
    lines = ["每天收盘写明天 SPY「收盘对今天超过 1%」的概率和收涨的概率（晚上版），09:15 再写一次（早上版）。Brier 分数越低越准。"
             "结果全从 SPY 日线卡算；前几天的「开到收」用早上版的 p_up（11-15 补算时的口径），之后用每天写的那个数。"
             + (f"按天的表里没填、从「第几天」那段取的：{'、'.join(filled)}。" if filled else ""), "",
             "| 哪一问 | 天数 | 我们 | " + " | ".join(["对照 1", "对照 2", "对照 3"]) + " |", "|---|---|---|---|---|---|"]
    for s in scores:
        cells = [f"{b['name']} {b['brier']:.3f}{'（我们好）' if b['beat'] else ''}" for b in s["baselines"]]
        lines.append(f"| {s['label']} | {s['n']} | {s['brier']:.3f} | " + " | ".join(cells + [""] * (3 - len(cells))) + " |")
    lines += ["", "| 预测的那天 | 晚上版 大波动 / 收涨 | 早上版 大波动 / 收涨 | 开到收 | 实际 | 大波动 | 开到收涨了吗 |", "|---|---|---|---|---|---|---|"]
    for x in rows:
        f = lambda k: "—" if x.get(k) is None else f"{x[k]:.0%}"
        lines.append(f"| {x['day']} | {f('eve_big')} / {f('eve_up')} | {f('mor_big')} / {f('mor_up')} | {f('oc')} | {pct(x['move'])} | "
                     f"{'是' if x['y_big'] else '否'} | {'是' if x['y_oc'] else '否'} |")
    n = max((s["n"] for s in scores), default=0)
    return {"key": "tomorrow", "title": "收盘：明天会怎样（概率，Brier 分数）", "n": n, "range": span([x["day"] for x in rows]),
            "ours": scores, "baselines": [], "headlines": heads, "rows": rows, "discrepancies": disc, "unparsed": unparsed, "lines": lines}


NAMES_T = {"eve_big": "晚上版大波动", "eve_up": "晚上版收涨", "mor_big": "早上版大波动", "mor_up": "早上版收涨", "oc": "开到收"}
NAMES_Q = {"big": "大波动", "up": "方向", "oc": "开到收"}
KEYS_T = {"big": ("eve_big", "mor_big"), "up": ("eve_up", "mor_up"), "oc": ("oc",)}


# ---------- 4. the follow card ----------
def strip_paren(s: str) -> str:
    return re.sub(r"（[^）]*）", "", s)


def same(a, b) -> bool:
    """A recorded value against a recomputed one: numbers within MONEY_TOL, tuples item by item."""
    if isinstance(a, tuple):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    if isinstance(a, float) or isinstance(b, float):
        return abs(a - b) <= MONEY_TOL
    return a == b


def follow(text: str, today: date) -> dict:
    tab = next((t for t in tables(text) if any("每 $1" in h for h in t[0]) and any("结论" in h for h in t[0])), None)
    if not tab:
        return empty("follow", "跟单卡", "没找到跟单卡的表")
    h = tab[0]
    ix = {"day": col(h, lambda s: s == "日期"), "concl": col(h, lambda s: s == "结论"), "move": col(h, lambda s: s.startswith("10:00")),
          "order": col(h, lambda s: "一定要跟" in s), "per": col(h, lambda s: s == "每 $1"), "exc": col(h, lambda s: "例外" in s)}
    rows, disc, unparsed = [], [], []
    for row in tab[1:]:
        try:
            day = iso(re.search(r"\d{1,2}-\d{1,2}", row[ix["day"]]).group(0), today)
        except (AttributeError, IndexError, TypeError):
            unparsed.append(f"跟单卡一行读不出日期：{' | '.join(row)}")
            continue
        concl, order, exc = row[ix["concl"]], row[ix["order"]], row[ix["exc"]].replace("*", "")
        whole = " ".join(row)
        x = {"day": day, "counted": "不计" not in concl}
        cv = re.findall(r"\$?(" + NUM + ")", strip_paren(concl))
        x["concl"] = num(cv[-1]) if x["counted"] and cv else None
        if x["counted"] and x["concl"] is None:
            unparsed.append(f"{day} 的结论读不出数：{concl}")
        pv = re.search(NUM, row[ix["per"]].replace("*", ""))
        x["per"] = num(pv.group(0)) if pv and x["counted"] else None
        mv = re.search("(" + NUM + ")%", row[ix["move"]])
        x["move"] = num(mv.group(1)) / 100 if mv else None
        x["triggered"] = "触发" in exc and not exc.startswith("没")
        x["graze"] = "擦线" in concl or "擦线" in exc
        alt = re.search(r"照线做(?:的话)?是\s*(" + NUM + ")", whole)
        x["line_alt"] = num(alt.group(1)) if alt else None
        lim = re.search(r"卡片写最多\s*\$(\d+(?:\.\d+)?)", order)
        paid = re.search(r"\$(\d+(?:\.\d+)?)", order)
        x["limit_broken"] = bool(lim and paid and float(paid.group(1)) > float(lim.group(1)))
        late = re.search(r"事后看[^）]*?约\s*(" + NUM + ")", order)
        x["missed"] = num(late.group(1)) if late and not x["counted"] else None
        if x["move"] is not None and x["counted"] and not x["graze"] and (abs(x["move"]) >= 0.005) != x["triggered"]:
            disc.append(f"{day}：10:00 离开盘 {pct(x['move'])}，按 0.5% 的线{'该' if abs(x['move']) >= 0.005 else '不该'}触发，记的是"
                        f"{'触发' if x['triggered'] else '没触发'}")
        if x["triggered"] and x["concl"] is not None and x["per"] is not None and abs(x["concl"] - x["per"]) > 1e-9:
            disc.append(f"{day}：触发的那天结论记 {x['concl']:+.2f}，同一单「一定要跟」记 {x['per']:+.2f}")
        rows.append(x)
    cnt = [x for x in rows if x["counted"]]

    def reading(x, field, how):
        v = x[field]
        if v is None:
            return 0.0
        opts = [v]
        if x["limit_broken"] and (field == "per" or x["triggered"]):
            opts.append(0.0)                                         # the card's own limit says it is not bought
        if x["graze"] and field == "concl" and x["line_alt"] is not None:
            opts.append(x["line_alt"])
        if how == "strict":
            return min(opts)
        if how == "mech":
            return x["line_alt"] if (x["graze"] and field == "concl" and x["line_alt"] is not None) else v
        return v

    tot = {f: {h_: round(sum(reading(x, f, h_) for x in cnt), 2) for h_ in ("booked", "strict", "mech")} for f in ("concl", "per")}
    missed = [x for x in rows if x["missed"] is not None]
    tot["per"]["with_missed"] = round(tot["per"]["booked"] + sum(x["missed"] for x in missed), 2)
    pers = [x["per"] for x in cnt if x["per"] is not None]
    wins, losses = sum(v > 0 for v in pers), sum(v <= 0 for v in pers)
    trig = [x for x in cnt if x["triggered"]]
    flagged = [x for x in cnt if x["limit_broken"] or x["graze"]]
    rb = robust.summary([(x["day"], x["per"]) for x in cnt if x["per"] is not None])
    # the recorded summary lines
    checks = [(r"合计（(\d+) 天）", lambda m: (int(m.group(1)), len(cnt)), "天数"),
              (r"「一定要跟」：(\d+) 赚 (\d+) 亏", lambda m: ((int(m.group(1)), int(m.group(2))), (wins, losses)), "「一定要跟」几赚几亏"),
              (r"每 \$1 合计 \**(" + NUM + ")", lambda m: (num(m.group(1)), tot["per"]["booked"]), "「一定要跟」合计"),
              (r"触发 (\d+) 次：[^；]*?合计 (" + NUM + ")", lambda m: ((int(m.group(1)), num(m.group(2))), (len(trig), tot["concl"]["booked"])),
               "结论（触发几次、合计）"),
              (r"从严[^。；]*?是 (" + NUM + ")", lambda m: (num(m.group(1)), tot["concl"]["strict"]), "结论从严"),
              (r"机械地记是 (" + NUM + ")", lambda m: (num(m.group(1)), tot["concl"]["mech"]), "结论按线机械地记"),
              (r"算进去是 \d+ 天合计 (" + NUM + ")", lambda m: (num(m.group(1)), tot["per"]["with_missed"]), "把没发那天算进去")]
    for rx, f, what in checks:
        m = re.search(rx, text)
        if not m:
            continue
        rec, ours = f(m)
        if not same(rec, ours):
            disc.append(f"合计那段「{what}」：记的是 {rec}，按表重算是 {ours}")
    lines = ["跟单卡每天三条：「结论」（有条件地跟：10:00 SPY 离开盘 0.5% 以上才做）、「一定要跟」（每天都做的 0.3% 版）。按表里每一行的每 $1 重算；"
             "对照是不跟（$0）。卡片自己的限价被打破、或者擦线的行，三种读法并排：记的（账上怎么记）、从严（每一处裁量都算对卡片不利）、"
             "机械（只按 0.5% 的线和程序的成交）。", ""]
    if flagged:
        lines += ["| 日期 | 情况 | 结论：记的 / 从严 / 机械 | 「一定要跟」：记的 / 从严 / 机械 |", "|---|---|---|---|"]
        for x in flagged:
            why = "、".join(w for w, on in (("超过卡片写的限价", x["limit_broken"]), ("擦线", x["graze"])) if on)
            lines.append(f"| {x['day']} | {why} | {reading(x, 'concl', 'booked'):+.2f} / {reading(x, 'concl', 'strict'):+.2f} / "
                         f"{reading(x, 'concl', 'mech'):+.2f} | {reading(x, 'per', 'booked'):+.2f} / {reading(x, 'per', 'strict'):+.2f} / "
                         f"{reading(x, 'per', 'mech'):+.2f} |")
        lines.append("")
    lines += [f"- 结论（{len(cnt)} 天，触发 {len(trig)} 次）：记的 {tot['concl']['booked']:+.2f}，从严 {tot['concl']['strict']:+.2f}，"
              f"机械 {tot['concl']['mech']:+.2f}；不跟是 $0。",
              f"- 「一定要跟」（{len(pers)} 天，{wins} 赚 {losses} 亏）：记的 {tot['per']['booked']:+.2f}，从严 {tot['per']['strict']:+.2f}，"
              f"机械 {tot['per']['mech']:+.2f}" + (f"；把没发的那天（事后看）算进去 {tot['per']['with_missed']:+.2f}" if missed else "") + "；不做是 $0。"]
    if rb:
        lines.append(f"- 「一定要跟」每天平均 {rb['mean']:+.2f}，去掉最好 3 笔 {rb['trim']:+.2f}，中位数 {rb['median']:+.2f}"
                     + (f"；提醒：{'；'.join(rb['flags'])}" if rb.get("flags") else "") + "。")
    days = [x["day"] for x in cnt]
    return {"key": "follow", "title": "跟单卡（每 $1）", "n": len(cnt), "range": span(days),
            "ours": tot, "baselines": [{"name": "不跟 / 不做（$0）", "value": 0.0}],
            "headlines": [headline("跟单卡「结论」（触发的几次）", len(trig),
                                   f"记的 {tot['concl']['booked']:+.2f} / 从严 {tot['concl']['strict']:+.2f} / 机械 {tot['concl']['mech']:+.2f}",
                                   "不跟 $0（按从严比）", vs(tot["concl"]["strict"], 0.0), [x["day"] for x in trig]),
                          headline("跟单卡「一定要跟」", len(pers), f"记的 {tot['per']['booked']:+.2f} / 从严 {tot['per']['strict']:+.2f}",
                                   "不做 $0（按从严比）", vs(tot["per"]["strict"], 0.0), days)],
            "rows": rows, "robust": rb and {k: rb[k] for k in ("n", "mean", "trim", "median", "flags")},
            "discrepancies": disc, "unparsed": unparsed, "lines": lines}


# ---------- 5. earnings ----------
def earnings(root: Path, cards: dict[str, dict], fetch: Fetcher, today: date) -> dict:
    base = root / "research" / "earnings-forecast"
    reasons: dict[tuple[str, str], dict] = {}
    rp = base / "reasons.jsonl"
    if rp.exists():
        for line in rp.read_text().splitlines():
            if line.strip():
                try:
                    r = json.loads(line)
                    reasons[(r["date"], r["symbol"])] = r
                except (ValueError, KeyError):
                    pass
    forecasts: dict[str, dict] = {}
    for p in sorted(base.glob("*.json")):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", p.name):
            try:
                forecasts[p.name[:10]] = {r["symbol"]: r for r in json.loads(p.read_text()).get("reports", [])}
            except (ValueError, KeyError, AttributeError):
                pass
    rows, disc, unparsed, pending = [], [], [], []
    for p in sorted(base.glob("*.judgment.json")):
        day = p.name[:10]
        if day < EARN_FROM:
            continue
        for c in json.loads(p.read_text()).get("calls", []):
            sym, dirn = c.get("symbol"), c.get("direction")
            rep = next((forecasts[d][sym] for d in sorted(forecasts) if d >= day and sym in forecasts[d]), None)
            if not rep or not rep.get("reaction_day") or dirn not in ("up", "down"):
                unparsed.append(f"{day} {sym}：" + ("找不到它的财报日" if not rep else "方向读不出"))
                continue
            rday = rep["reaction_day"]
            if rday > today.isoformat():
                pending.append(f"{sym}（{rday}）")
                continue
            bar, why = day_bar(fetch, sym, rday)
            x = {"day": rday, "written": day, "symbol": sym, "call": dirn, "evidence": c.get("evidence"),
                 "recorded_gap": (rep.get("result") or {}).get("gap")}
            spy = cards.get(rday)
            if spy is None:
                sb, _ = day_bar(fetch, "SPY", rday)
                spy = {"gap": sb["o"] / sb["pc"] - 1} if sb and sb["pc"] else None
            x["spy_gap"] = spy and round(spy["gap"], 5)
            rr = reasons.get((day, sym))
            mt = re.search(r"开盘\s*(" + NUM + ")%", rr["reason"]) if rr else None
            x["text_gap"] = num(mt.group(1)) / 100 if mt else None
            text_tol = prose_tol(mt.group(1)) if mt else TEXT_TOL
            if not bar or not bar["pc"]:
                x["fetched"] = False
                unparsed.append(f"{rday} {sym}：日线{why or '缺前一天'}，这一笔没算")
                rows.append(x)
                continue
            g = bar["o"] / bar["pc"] - 1
            x.update(fetched=True, gap=round(g, 5), hit=(g > 0) == (dirn == "up") and g != 0)
            for what, v, tol in (("预测文件 result.gap", x["recorded_gap"], 0.002), ("reasons.jsonl 写的开盘", x["text_gap"], text_tol)):
                if v is not None and (side(v) != side(g) or abs(v - g) > tol):
                    disc.append(f"{rday} {sym}：{what} {pct(v, 1)}，日线开盘对前收 {pct(g, 1)}（开 {bar['o']:.2f}，前收 {bar['pc']:.2f}）")
            rows.append(x)
    got = [x for x in rows if x.get("fetched")]
    n = len(got)
    k = sum(x["hit"] for x in got)
    kd = sum(x["gap"] < 0 for x in got)
    ku = sum(x["gap"] > 0 for x in got)
    sp = [x for x in got if x["spy_gap"] is not None]
    ks = sum(side(x["gap"]) == side(x["spy_gap"]) for x in sp)
    prof = [x for x in got if x["evidence"] == "profile"]
    lines = [f"{EARN_FROM} 起只给在意的名字写判断（*.judgment.json）。按反应日的日线开盘对前一天收盘，看方向猜对没有；"
             "对照：一律跌、一律涨、跟着当天 SPY 的缺口。", "",
             f"- 我们：{rate(k, n)}" + (f"（其中有档案的 {rate(sum(x['hit'] for x in prof), len(prof))}）" if prof and len(prof) != n else "") + "。",
             f"- 一律跌 {rate(kd, n)}；一律涨 {rate(ku, n)}；跟着 SPY 缺口 {rate(ks, len(sp))}。"]
    if pending:
        lines.append(f"- 还没到反应日：{'、'.join(pending)}。")
    lines += ["", "| 反应日 | 股票 | 猜的 | 依据 | 日线开盘对前收 | 对吗 | SPY 缺口 | 记的（预测文件 / reasons） |", "|---|---|---|---|---|---|---|---|"]
    for x in rows:
        lines.append(f"| {x['day']} | {x['symbol']} | {'涨' if x['call'] == 'up' else '跌'} | {x['evidence'] or '—'} | "
                     f"{pct(x.get('gap'), 1) if x.get('fetched') else '没取到'} | {('对' if x['hit'] else '错') if x.get('fetched') else '—'} | "
                     f"{pct(x['spy_gap'])} | {pct(x['recorded_gap'], 1)} / {pct(x['text_gap'], 1)} |")
    best = max([("一律跌", kd, n), ("一律涨", ku, n), ("跟着 SPY 缺口", ks, len(sp))], key=lambda t: t[1] / t[2] if t[2] else 0)
    return {"key": "earnings", "title": "财报：方向对反应日的开盘", "n": n, "range": span([x["day"] for x in got]),
            "ours": {"hits": k, "n": n},
            "baselines": [{"name": "一律跌", "hits": kd, "n": n, "beat": k > kd}, {"name": "一律涨", "hits": ku, "n": n, "beat": k > ku},
                          {"name": "跟着 SPY 缺口", "hits": ks, "n": len(sp), "beat": bool(sp) and k / n > ks / len(sp)}],
            "headlines": [headline("财报方向（反应日开盘）", n, rate(k, n), f"{best[0]} {rate(best[1], best[2])}",
                                   vs(k / n, best[1] / best[2]) if n and best[2] else None, [x["day"] for x in got])],
            "rows": rows, "pending": pending, "discrepancies": disc, "unparsed": unparsed, "lines": lines}


# ---------- 6. the sniper ----------
OC_RX = re.compile(r"开(?:盘)?到收(?:盘)?\s*(" + NUM + ")%")
ALL_DAY = re.compile(r"当天收盘|全天的线|过夜的线|收盘收")


OPEN_RX = re.compile(r"开(?:盘价?)?\s*\$?(\d+(?:\.\d+)?)")


def recorded_oc(s: dict) -> tuple[float | None, float]:
    """The open-to-close move the close exchange wrote for the pick, and how far it may sit from the daily bar."""
    texts = [s.get(k) for k in ("open_to_close", "all_day_line", "wrong_if_all_day")] + list((s.get("checks") or {}).values()) + \
            [s.get(k) for k in ("path", "direction", "wrong_if")]
    for t in texts:
        if isinstance(t, str) and (m := OC_RX.search(t)):
            return num(m.group(1)) / 100, prose_tol(m.group(1))
    t = s.get("open_to_close")
    m = re.search(r"开\s*\$?(\d+(?:\.\d+)?)[^收]*?收\s*\$?(\d+(?:\.\d+)?)", t) if isinstance(t, str) else None
    return (float(m.group(2)) / float(m.group(1)) - 1, MOVE_TOL) if m else (None, 0.0)


def recorded_open(s: dict) -> float | None:
    """The pick's open price as the close exchange wrote it (the first one it names)."""
    for t in [s.get(k) for k in ("open_to_close", "all_day_line", "path", "wrong_if_10am", "wrong_if")] + list((s.get("checks") or {}).values()):
        if isinstance(t, str) and (m := OPEN_RX.search(t)):
            return float(m.group(1))
    return None


def recorded_all_day(s: dict) -> bool | None:
    for t in [s.get("all_day_line"), s.get("wrong_if_all_day"), (s.get("checks") or {}).get("close_back_below_open")]:
        if isinstance(t, str):
            head = t[:40]
            if re.search(r"没发生|没触发", head):
                return False
            if re.search(r"发生|触发", head):
                return True
    m = re.search(r"(?:全天|过夜)的线[^：]*：\s*(没发生|发生了)", s.get("wrong_if") or "")
    return (m.group(1) == "发生了") if m else None


def sniper(root: Path, fetch: Fetcher, today: date) -> dict:
    rows, disc, unparsed, none = [], [], [], []
    for p in sorted((root / "journal-s500" / "sniper").glob("*.json")):
        day = p.name[:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) or day < SNIPER_FROM or day > today.isoformat():
            continue
        s = json.loads(p.read_text()).get("sniper")
        if not s:
            none.append(day)
            continue
        sym, dirn = s.get("symbol"), s.get("direction")
        if dirn not in ("call", "put"):
            unparsed.append(f"{day} {sym}：方向读不出（{dirn}）")
            continue
        wrong = s.get("wrong_if") or ""
        x = {"day": day, "symbol": sym, "direction": dirn, "order": s.get("order"), "wrong_if": wrong, "all_day_line": bool(ALL_DAY.search(wrong))}
        cp = root / "exchange" / "claude" / f"{day}.close.json"
        rec = (json.loads(cp.read_text()).get("sniper") or {}) if cp.exists() else {}
        (x["recorded_oc"], oc_tol), x["recorded_all_day"] = recorded_oc(rec), recorded_all_day(rec)
        x["recorded_open"] = recorded_open(rec)
        if day == today.isoformat() and datetime.now(db.ET).hour < 16:
            x["fetched"] = False
            rows.append(x)
            continue
        bar, why = day_bar(fetch, sym, day)
        if not bar:
            x["fetched"] = False
            unparsed.append(f"{day} {sym}：日线{why}，这一笔没算")
            rows.append(x)
            continue
        oc = bar["c"] / bar["o"] - 1
        x.update(fetched=True, oc=round(oc, 5), match=side(oc) == (1 if dirn == "call" else -1))
        if x["recorded_oc"] is not None and (side(x["recorded_oc"]) != side(oc) or abs(x["recorded_oc"] - oc) > oc_tol):
            disc.append(f"{day} {sym}：收盘交流记的开到收 {pct(x['recorded_oc'])}，日线 {pct(oc)}（开 {bar['o']:.2f} 收 {bar['c']:.2f}）")
        if x["recorded_open"] is not None and abs(x["recorded_open"] / bar["o"] - 1) > OPEN_TOL:
            disc.append(f"{day} {sym}：收盘交流记的开盘 {x['recorded_open']:.2f}，日线开盘 {bar['o']:.2f}"
                        f"（差 {x['recorded_open'] / bar['o'] - 1:+.2%}；可能取的是分钟线第一根，11-22 定的口径是日线开盘）")
        if x["recorded_all_day"] is not None and x["recorded_all_day"] != (not x["match"]):
            disc.append(f"{day} {sym}：全天的线（收盘收回开盘价）记的是{'发生了' if x['recorded_all_day'] else '没发生'}，"
                        f"按日线开到收 {pct(oc, 1)} 是{'没发生' if x['match'] else '发生了'}")
        rows.append(x)
    got = [x for x in rows if x.get("fetched")]
    n = len(got)
    k = sum(x["match"] for x in got)
    kc = sum(x["oc"] > 0 for x in got)
    kp = sum(x["oc"] < 0 for x in got)
    no_wrong = [x["day"] for x in rows if not x["wrong_if"]]
    by = {}
    for x in got:
        o = x["order"] or "没记"
        by.setdefault(o, [0, 0])
        by[o][0] += x["match"]
        by[o][1] += 1
    lines = [f"{SNIPER_FROM} 起每天的狙击第 1 名，按日线看开盘到收盘有没有朝选的方向走（一分钟线太重，「10:00 那一刻」的 wrong_if 这里核不了，"
             "只核全天那句「当天收盘收回开盘价」）。对照：同一批股票一律看涨、一律看跌。", "",
             f"- 我们：{rate(k, n)}；一律看涨 {rate(kc, n)}；一律看跌 {rate(kp, n)}。",
             "- order：" + ("；".join(f"{'主角（first）' if o == 'first' else '二线（second）' if o == 'second' else o} {rate(v[0], v[1])}"
                                     for o, v in sorted(by.items())) or "—") + "（攒到 15 笔再比）。"]
    if no_wrong:
        lines.append(f"- 没写 wrong_if：{'、'.join(no_wrong)}。")
    if none:
        lines.append(f"- 没选（sniper 是空的）：{'、'.join(none)}。")
    lines += ["", "| 日期 | 股票 | 方向 | order | 开到收 | 方向对吗 | 有全天的线 | 收盘交流记的开到收 |", "|---|---|---|---|---|---|---|---|"]
    for x in rows:
        lines.append(f"| {x['day']} | {x['symbol']} | {'看涨' if x['direction'] == 'call' else '看跌'} | {x['order'] or '—'} | "
                     f"{pct(x.get('oc'), 1) if x.get('fetched') else '没取到'} | {('对' if x['match'] else '错') if x.get('fetched') else '—'} | "
                     f"{'有' if x['all_day_line'] else '没有'} | {pct(x['recorded_oc'], 1)} |")
    best = max([("一律看涨", kc), ("一律看跌", kp)], key=lambda t: t[1])
    return {"key": "sniper", "title": "狙击：开盘到收盘的方向", "n": n, "range": span([x["day"] for x in got]),
            "ours": {"hits": k, "n": n, "by_order": by},
            "baselines": [{"name": "一律看涨", "hits": kc, "n": n, "beat": k > kc}, {"name": "一律看跌", "hits": kp, "n": n, "beat": k > kp}],
            "headlines": [headline("狙击方向（开到收）", n, rate(k, n), f"{best[0]} {rate(best[1], n)}", vs(k, best[1]) if n else None,
                                   [x["day"] for x in got])],
            "rows": rows, "discrepancies": disc, "unparsed": unparsed, "lines": lines}


# ---------- 7. the weekbook ----------
def weekbook(root: Path, cards: dict[str, dict]) -> dict:
    lp = root / "research" / "weekbook" / "ledger.json"
    if not lp.exists():
        return empty("weekbook", "每周档案：事先记的读数", "还没有 research/weekbook/ledger.json")
    from hero import weekbook as wb
    reads = json.loads(lp.read_text()).get("reads", [])
    cl = [cards[d] for d in sorted(cards)]
    try:
        ws = wb.weeks(cl, {})
    except Exception:
        ws = []
    by = {w["week"]: w for w in ws}
    need_vix = any(r["week"] in by and by[r["week"]]["complete"] and "vix_down" in r.get("p", {}) for r in reads)
    if need_vix:
        try:
            vix = wb.load_vix(datetime(2015, 1, 2, tzinfo=db.ET))
            ws = wb.weeks(cl, vix)
            by = {w["week"]: w for w in ws}
        except Exception:
            pass
    pairs, base_pairs, rows, pending, unparsed = [], [], [], [], []
    for r in reads:
        w = by.get(r["week"])
        if not w or not w["complete"]:
            pending.append(r["week"])
            continue
        before = [v for v in ws if v["week"] < r["week"] and v["complete"]]
        for q, p in r.get("p", {}).items():
            if q not in wb.OUTCOMES:
                unparsed.append(f"{r['week']} 的问题 {q} 不认识")
                continue
            y = wb.OUTCOMES[q][1](w)
            if y is None:
                unparsed.append(f"{r['week']} {wb.OUTCOMES[q][0]}：判不了（要 VIX，没取到）")
                continue
            ys = [v for v in (wb.OUTCOMES[q][1](b) for b in before) if v is not None]
            b = sum(ys) / len(ys) if ys else 0.5
            pairs.append((p, y))
            base_pairs.append((b, y))
            rows.append({"week": r["week"], "q": q, "p": p, "base": round(b, 4), "y": y})
    ours, bb = brier(pairs), brier(base_pairs)
    weeks_done = sorted({x["week"] for x in rows})
    lines = ["每周档案（hero/weekbook.py）每周一前把这一周的读数记进 ledger.json；这里拿记下的读数对这一周实际发生的，和「只报底数」比（Brier）。", ""]
    lines.append(f"- 走完的周 {len(weeks_done)} 周、{len(pairs)} 问：" + (f"我们 {ours:.3f}，底数 {bb:.3f}。" if pairs else "还没有能打分的。"))
    if pending:
        lines.append(f"- 还没走完：{'、'.join(pending)}。")
    return {"key": "weekbook", "title": "每周档案：事先记的读数", "n": len(weeks_done), "range": span(weeks_done),
            "ours": {"brier": ours and round(ours, 4), "questions": len(pairs)}, "baselines": [{"name": "底数", "brier": bb and round(bb, 4)}],
            "headlines": [headline("每周档案（记下的读数）", len(weeks_done), f"Brier {ours:.3f}" if pairs else "还没有走完的周",
                                   f"底数 {bb:.3f}" if pairs else "—", vs(ours, bb, lower=True) if pairs else None, weeks_done)],
            "rows": rows, "pending": pending, "discrepancies": [], "unparsed": unparsed, "lines": lines}


# ---------- the page ----------
def build(root: Path = ROOT, today: date | None = None, fetch: Fetcher | None = None) -> dict:
    today = today or datetime.now(db.ET).date()
    fetch = fetch or Fetcher()
    cards = load_cards(root)
    sc = root / "macro" / "briefings" / "scores.csv"
    rows = list(csv.DictReader(io.StringIO(sc.read_text()))) if sc.exists() else []

    def text(rel: str) -> str | None:
        p = root / rel
        return p.read_text() if p.exists() else None

    out = [against_gap(rows, cards), spy_direction(rows, cards)]
    for rel, fn, key, title in (("exchange/claude/pm-dir-scores.md", lambda t: pm_dir(t, cards, today), "pm_dir", "午盘：12:00 到收盘的方向（pm_dir）"),
                                ("exchange/claude/tomorrow-scores.md", lambda t: tomorrow(t, cards, today), "tomorrow", "收盘：明天会怎样"),
                                ("posts/follow/scorecard.md", lambda t: follow(t, today), "follow", "跟单卡（每 $1）")):
        t = text(rel)
        out.append(fn(t) if t is not None else empty(key, title, f"没有 {rel}"))
    out += [earnings(root, cards, fetch, today), sniper(root, fetch, today), weekbook(root, cards)]
    return {"date": today.isoformat(), "daybook_last": max(cards) if cards else None, "cards": out}


def page(rep: dict) -> str:
    cs = rep["cards"]
    disc = [(c["title"], d) for c in cs for d in c["discrepancies"]]
    out = [f"# 记分卡总账：{rep['date']}", "",
           "模拟盘研究，只记录，不是投资建议，不据此下单、不改规则。每张记分卡都从原始记录重算：结果按价格（SPY 用每日档案的日线卡，"
           f"到 {rep['daybook_last']}；个股用 Yahoo 日线），汇总那几句只拿来对照，不抄。每一项和它该赢的对照并排；不到 {MIN_N} 次的写「不够」。", "",
           f"## 先看：记录和重算对不上的地方（{len(disc)} 处）", ""]
    out += [f"- **{t}**：{d}" for t, d in disc] or ["- 没有。"]
    out += ["", "## 一张表", "", "| 记分卡 | 次数 | 日期 | 我们 | 最好的对照 | 赢了吗 | 样本 |", "|---|---|---|---|---|---|---|"]
    for c in cs:
        for h in c["headlines"]:
            rng = f"{h['range'][0][5:]} 到 {h['range'][1][5:]}" if h["range"] else "—"
            beat = verdict(h["beat"])
            out.append(f"| {h['label']} | {h['n']} | {rng} | {h['ours']} | {h['best']} | {beat} | {h['enough']} |")
    for c in cs:
        out += ["", f"## {c['title']}", ""]
        for h in c["headlines"]:
            rng = f"{h['range'][0]} 到 {h['range'][1]}" if h["range"] else "没有日期"
            beat = "" if h["beat"] is None else "，" + verdict(h["beat"])
            out.append(f"**{h['label']}**：我们 {h['ours']}；最好的对照 {h['best']}{beat}。{h['n']} 次，{rng}；样本{h['enough']}。")
        if c["headlines"]:
            out.append("")
        out += c["lines"]
        if c["discrepancies"]:
            out += ["", "对不上的：", ""] + [f"- {d}" for d in c["discrepancies"]]
    un = [(c["title"], u) for c in cs for u in c["unparsed"]]
    out += ["", f"## 没读懂、没算的行（{len(un)} 行）", ""] + ([f"- {t}：{u}" for t, u in un] or ["- 没有。"])
    return "\n".join(out) + "\n"


def run(root: Path = ROOT, today: date | None = None, fetch: Fetcher | None = None, write: bool = True) -> dict:
    rep = build(root, today, fetch)
    text = page(rep)
    if write:
        d = root / OUT
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{rep['date']}.md").write_text(text)
        slim = {**rep, "cards": [{k: v for k, v in c.items() if k != "lines"} for c in rep["cards"]]}
        (d / f"{rep['date']}.json").write_text(json.dumps(slim, ensure_ascii=False, indent=1, default=str) + "\n")
    return {**rep, "page": text}


def main() -> None:
    day = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else None
    print(run(today=day)["page"])


if __name__ == "__main__":
    main()
