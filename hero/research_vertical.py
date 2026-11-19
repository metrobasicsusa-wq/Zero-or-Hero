"""Three-month vertical spreads on real option prices (owner, 11-19: "我都是垂直拿三个月" -- both bullish kinds, and
four questions: which days to open on, hold to expiry or leave early, which strikes, index or single stocks).

Fixed before any of it was run:
  underlyings  SPY, QQQ; the Magnificent 7.
  entry        every session since 2024-02 whose expiry has passed, at that day's option closes: the first monthly
               expiry (third Friday) at least 80 days out, strikes the nearest $5 to the stated share of the close.
  structures   bull call (debit): buy at-the-money / sell 5% up; buy ATM / sell 10% up; buy 5% up / sell 10% up;
               buy 5% down / sell ATM.   bull put (credit): sell ATM / buy 5% down; sell 5% down / buy 10% down;
               sell 10% down / buy 15% down.   Bearish too (owner, while this was being written: "看跌也做，有时候
               顺应方向"): bear put (debit): buy ATM / sell 5% down; buy 5% down / sell 10% down.   bear call
               (credit): sell ATM / buy 5% up; sell 5% up / buy 10% up.
  with the trend   on each entry day the bullish structure when the close is at or above its 50-day average and the
               bearish one when below, against always bullish, always bearish and the reverse choice -- once for the
               debit pair (ATM call spread / ATM put spread) and once for the credit pair (5%-out put / 5%-out call).
  exits        hold to expiry (settled on the underlying's close that day); leave at half the most it can make, else
               expiry; leave at half the most it can make or at half the most it can lose, else expiry -- looked at on
               each day both legs traded, at their closes.
  result       per $1 at risk: for a debit spread what was paid; for a credit spread the width less what was received.
               Costs of 2% and 5% of the spread's price each time it is traded (nothing at expiry).
  day types    on the underlying's own closes: a big down day (-1.5% for an index, -3% for a stock); a third down close
               in a row; above / below its 50-day average; 10% or more under its one-year high; and the days the SPY
               daybook reads "a big day" at 40% or more at the open.
  comparison   the shares over the same three months.
  environment  (owner, while the first run was still fetching: "重点就是你可以判断现在的环境怎么做") each entry day is in
               one of four: at or above its 50-day average and within 10% of its one-year high ("上升"); above the
               average but 10% or more under the high ("反弹中"); below the average and within 10% ("回调"); below and
               10% or more under ("下跌"). Every structure is shown by environment, and the report names the
               environment each underlying is in now (Yahoo closes). Fixed before the first results were read.
What this cannot say: entries run 2024-02 to mid-2026, mostly a rising market, and a three-month trade opened every
day overlaps the sixty before it -- about ten separate quarters, not six hundred trades. Shares of entry MONTHS that
ended above zero are shown beside every average for that reason. Splits: a trade with a split inside it is left out.
Record only. Run: python -m hero.research_vertical (needs the Alpaca keys: the research workflow, study "vertical").
"""

from __future__ import annotations

import statistics
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from hero import daybook as db
from hero import robust
from hero.research_daybook_long import STOCKS, expiry, occ
from hero.research_daybook_replay import FLAG_OPEN, walk
from hero.research_event_turn import Pace
from hero.research_flow_daily import ERRORS, fetch
from hero.research_ideas import save

ROOT = Path(__file__).resolve().parent.parent
START, LEAD = "2024-02-01", "2023-01-03"
INDEXES = ("SPY", "QQQ")
MIN_DAYS, STEP = 80, 5
COSTS = (0.02, 0.05)
BUDGET = 125 * 60
# name, calls or puts, the lower and the higher strike as shares of the close, bought (debit) or sold (credit), bullish
STRUCTS = {
    "call_atm_5": ("买入看涨价差：平值买、高 5% 卖", "C", 1.00, 1.05, True, True),
    "call_atm_10": ("买入看涨价差：平值买、高 10% 卖", "C", 1.00, 1.10, True, True),
    "call_otm_5": ("买入看涨价差：高 5% 买、高 10% 卖", "C", 1.05, 1.10, True, True),
    "call_itm_5": ("买入看涨价差：低 5% 买、平值卖", "C", 0.95, 1.00, True, True),
    "put_atm_5": ("卖出看跌价差：平值卖、低 5% 买", "P", 0.95, 1.00, False, True),
    "put_otm5_5": ("卖出看跌价差：低 5% 卖、低 10% 买", "P", 0.90, 0.95, False, True),
    "put_otm10_5": ("卖出看跌价差：低 10% 卖、低 15% 买", "P", 0.85, 0.90, False, True),
    "bear_put_atm_5": ("买入看跌价差：平值买、低 5% 卖", "P", 0.95, 1.00, True, False),
    "bear_put_otm_5": ("买入看跌价差：低 5% 买、低 10% 卖", "P", 0.90, 0.95, True, False),
    "bear_call_atm_5": ("卖出看涨价差：平值卖、高 5% 买", "C", 1.00, 1.05, False, False),
    "bear_call_otm5_5": ("卖出看涨价差：高 5% 卖、高 10% 买", "C", 1.05, 1.10, False, False),
}
PAIRS = {"付钱的一对（平值看涨价差 / 平值看跌价差）": ("call_atm_5", "bear_put_atm_5"),
         "收钱的一对（卖低 5% 的看跌价差 / 卖高 5% 的看涨价差）": ("put_otm5_5", "bear_call_otm5_5")}
EXITS = {"expiry": "拿到到期", "tp": "赚到一半就走", "tp_sl": "赚一半或亏一半就走"}
DAYS = {"big_drop": "大跌日", "streak3": "连跌 3 天", "below50": "50 日均线下方", "above50": "50 日均线上方",
        "dd10": "离一年高点回撤 10% 以上", "daybook": "SPY 档案说「会大」"}
REFS = ("call_atm_5", "put_otm5_5", "bear_put_atm_5")
ENVS = {"up": "上升（均线上方、离高点 10% 以内）", "rebound": "反弹中（均线上方、离高点 10% 以上）",
        "pullback": "回调（均线下方、离高点 10% 以内）", "down": "下跌（均线下方、离高点 10% 以上）"}
SHARES = sorted({m for x in STRUCTS.values() for m in x[2:4]})


def strike(spot: float, share: float) -> float:
    return float(round(spot * share / STEP) * STEP)


def spread(kind: str, k_lo: float, k_hi: float, series: list[tuple[str, float]], settle: float, cost: float, debit: bool = True) -> dict | None:
    """What $1 at risk made under each exit. `series`: (day, value of the spread) on the days both legs traded, the
    entry day first. The value is the dearer leg less the cheaper one (the lower call less the higher call; the higher
    put less the lower put). A debit spread pays it and is long it; a credit spread receives it and owes it."""
    width, v0 = k_hi - k_lo, series[0][1]
    if width <= 0 or not 0 < v0 < width:
        return None
    # A spread is worth between nothing and its width. The two legs' last trades are not from the same moment, so a
    # day's difference can fall outside that; the first run (11-19) had 37 results worse than losing everything.
    series = [(t, min(max(v, 0.0), width)) for t, v in series]
    worth = max(0.0, min(settle - k_lo, width)) if kind == "C" else max(0.0, min(k_hi - settle, width))   # at expiry
    up, down = v0 + 0.5 * (width - v0), 0.5 * v0                               # the value half way to its most / to nothing
    if debit:
        paid = v0 * (1 + cost)
        at = lambda v: v * (1 - cost) / paid - 1                              # sold before expiry
        end = worth / paid - 1
        won, lost = (lambda v: v >= up), (lambda v: v <= down)
    else:
        got = v0 * (1 - cost)
        risk = width - got
        at = lambda v: (got - v * (1 + cost)) / risk                          # bought back before expiry
        end = (got - worth) / risk
        won, lost = (lambda v: v <= down), (lambda v: v >= up)
    out = {"expiry": end, "tp": end, "tp_sl": end}
    for name, leave in (("tp", won), ("tp_sl", lambda v: won(v) or lost(v))):
        hit = next((v for _, v in series[1:] if leave(v)), None)
        if hit is not None:
            out[name] = at(hit)
    return {k: round(v, 4) for k, v in out.items()}


def day_types(closes: list[float], i: int, index: bool) -> dict[str, bool] | None:
    """The entry day's labels from split-adjusted closes up to and including day i."""
    if i < 252:
        return None
    r = closes[i] / closes[i - 1] - 1
    avg50 = statistics.mean(closes[i - 49:i + 1])
    return {"big_drop": r <= (-0.015 if index else -0.03),
            "streak3": all(closes[i - k] < closes[i - k - 1] for k in range(3)),
            "below50": closes[i] < avg50, "above50": closes[i] >= avg50,
            "dd10": closes[i] <= 0.90 * max(closes[i - 251:i + 1])}


def env(types: dict) -> str:
    return ("rebound" if types["dd10"] else "up") if types["above50"] else ("down" if types["dd10"] else "pullback")


def now_types(unds) -> dict[str, dict]:
    """Each underlying's labels as of its latest close (Yahoo daily bars, split-adjusted)."""
    out = {}
    start = datetime.now(timezone.utc) - timedelta(days=420)
    for und in unds:
        try:
            closes = [c for *_, c in db.yahoo(und, "1d", start, datetime.now(timezone.utc) + timedelta(days=1))]
        except Exception:
            continue
        t = day_types(closes, len(closes) - 1, und in INDEXES) if len(closes) > 252 else None
        if t:
            out[und] = {**t, "close": round(closes[-1], 2), "vs50": round(closes[-1] / statistics.mean(closes[-50:]) - 1, 4),
                        "vs_high": round(closes[-1] / max(closes[-252:]) - 1, 4)}
    return out


def split_inside(raw: list[float], adj: list[float], a: int, b: int) -> bool:
    """A split between sessions a and b: the raw-to-adjusted ratio moved by more than a dividend would move it."""
    return abs((raw[b] / adj[b]) / (raw[a] / adj[a]) - 1) > 0.2


def run(client, unds=INDEXES + STOCKS, clock=time.monotonic, pace=None, now: datetime | None = None, big_days: set[str] | None = None) -> dict:
    pace, now = pace or Pace(client), now or datetime.now(timezone.utc)
    today = now.date().isoformat()
    cap = (now - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    raw = fetch(pace, client.daily_bars, list(unds), LEAD, adjustment="raw", feed="sip")
    adj = fetch(pace, client.daily_bars, list(unds), LEAD, adjustment="all", feed="sip")
    rep = {"generated": date.today().isoformat(), "start": START, "unds": list(unds), "rows": [], "stopped_at": None, "not_reached": []}
    if not raw or not adj:
        return {**rep, "errors": list(ERRORS)}
    big_days = big_days or set()
    t0, misses = clock(), 0
    for und in unds:
        days = [b["t"][:10] for b in raw.get(und, []) if b["t"][:10] < today]
        if len(days) < 300 or len(adj.get(und, [])) < len(days):
            rep["not_reached"].append(und)
            continue
        rc = [float(b["c"]) for b in raw[und]][:len(days)]
        ac = [float(b["c"]) for b in adj[und]][:len(days)]
        pos, sessions = {d: i for i, d in enumerate(days)}, set(days)
        for i, d in enumerate(days):
            if d < START:
                continue
            exp = expiry(d, MIN_DAYS, sessions)
            if exp not in pos:                                    # not expired yet (or a holiday the cards do not know)
                continue
            if clock() - t0 > BUDGET:
                rep["stopped_at"] = f"{und} {d}"
                break
            labels = day_types(ac, i, und in INDEXES)
            if labels is None or split_inside(rc, ac, i, pos[exp]):
                continue
            ks = {m: strike(rc[i], m) for m in SHARES}
            syms = {(x[1], m): occ(exp, x[1], ks[m], und) for x in STRUCTS.values() for m in x[2:4]}
            bars = fetch(pace, client.option_bars, sorted(set(syms.values())), f"{d}T00:00:00Z", min(f"{exp}T23:59:59Z", cap), timeframe="1Day")
            if bars is None:
                misses += 1
                if misses >= 3 and not rep["rows"]:
                    return {**rep, "errors": list(ERRORS)}
                continue
            px = {s: {b["t"][:10]: float(b["c"]) for b in bs} for s, bs in bars.items()}
            res = {}
            for key, (_, kind, lo, hi, debit, _bull) in STRUCTS.items():
                a, b = px.get(syms[(kind, lo)], {}), px.get(syms[(kind, hi)], {})
                if d not in a or d not in b or ks[lo] == ks[hi]:
                    continue
                value = (lambda t: a[t] - b[t]) if kind == "C" else (lambda t: b[t] - a[t])   # the dearer leg less the cheaper
                series = [(t, value(t)) for t in sorted(set(a) & set(b)) if d <= t < exp]
                got = {f"{c:.2f}": spread(kind, ks[lo], ks[hi], series, rc[pos[exp]], c, debit) for c in COSTS}
                if all(got.values()):
                    res[key] = got
            if res:
                # the single legs' entry closes too (owner, 11-19: "垂直也只是个工具"): with the strikes and the settlement
                # they price the other tools held to expiry -- a call or a put alone, a sold put, shares with a sold call
                legs = {f"{kind}{m:.2f}": px.get(sym, {}).get(d) for (kind, m), sym in syms.items()}
                rep["rows"].append({"und": und, "day": d, "exp": exp, "spot": round(rc[i], 2), "settle": round(rc[pos[exp]], 2),
                                    "shares": round(ac[pos[exp]] / ac[i] - 1, 4), "types": {**labels, "daybook": d in big_days}, "res": res,
                                    "strikes": {f"{m:.2f}": ks[m] for m in SHARES}, "legs": legs})
        else:
            continue
        rep["not_reached"] += [u for u in unds[list(unds).index(und) + 1:]]
        break
    return {**rep, "errors": list(ERRORS)}


def pct(x) -> str:
    return "—" if x is None else f"{x:+.0%}"


def stats(xs: list[tuple[str, float]]) -> dict | None:
    """Averages for (entry day, per $1 at risk), and the share of entry months that ended above zero."""
    if not xs:
        return None
    v = [r for _, r in xs]
    months: dict[str, list[float]] = {}
    for d, r in xs:
        months.setdefault(d[:7], []).append(r)
    up = sum(statistics.mean(m) > 0 for m in months.values())
    return {"n": len(v), "mean": statistics.mean(v), "median": statistics.median(v), "win": sum(r > 0 for r in v) / len(v),
            "wiped": sum(r <= -0.95 for r in v) / len(v), "months": len(months), "months_up": up}


def pick(rows: list[dict], key: str, exit_: str, cost: float, keep=lambda r: True) -> list[tuple[str, float]]:
    c = f"{cost:.2f}"
    return [(r["day"], r["res"][key][c][exit_]) for r in rows if key in r["res"] and keep(r)]


def halves(on: list[tuple[str, float]], off: list[tuple[str, float]]) -> list[float | None]:
    both = sorted(on + off)
    if not both:
        return [None, None]
    mid = both[len(both) // 2][0]
    out = []
    for side in (lambda d: d < mid, lambda d: d >= mid):
        a, b = [r for d, r in on if side(d)], [r for d, r in off if side(d)]
        out.append(statistics.mean(a) - statistics.mean(b) if a and b else None)
    return out


def report(rep: dict, now: dict[str, dict] | None = None) -> tuple[dict, str]:
    lo, hi = COSTS
    now = now or {}
    groups = {"指数（SPY、QQQ）": [r for r in rep["rows"] if r["und"] in INDEXES], "个股（七巨头）": [r for r in rep["rows"] if r["und"] in STOCKS]}
    table, out = {}, []
    for g, rows in groups.items():
        if not rows:
            continue
        sh = [r["shares"] for r in rows]
        out += ["", f"## {g}：全貌", "",
                f"{len(rows)} 笔进场（{rows[0]['day']} 到 {max(r['day'] for r in rows)}）。同样三个月直接拿正股：涨的占 {sum(x > 0 for x in sh) / len(sh):.0%}，平均 {statistics.mean(sh):+.1%}，中位数 {statistics.median(sh):+.1%}。", "",
                f"每 $1 风险的收益，成本按每次买卖 {hi:.0%}（括号里是 {lo:.0%}）。「亏光」= 亏掉 95% 以上。「月份」= 按进场月份分，平均为正的月份 / 全部月份。", "",
                "| 做法 | 怎么走 | 笔数 | 平均 | 中位数 | 赚的比例 | 亏光的比例 | 月份 |", "|---|---|---|---|---|---|---|---|"]
        for key, (name, *_ ) in STRUCTS.items():
            for e, ename in EXITS.items():
                s = table[f"{g}|{key}|{e}"] = stats(pick(rows, key, e, hi))
                if not s:
                    continue
                cheap = stats(pick(rows, key, e, lo))
                out.append(f"| {name} | {ename} | {s['n']} | {pct(s['mean'])}（{pct(cheap['mean'])}） | {pct(s['median'])} | {s['win']:.0%} | {s['wiped']:.0%} | {s['months_up']} / {s['months']} |")
        out += ["", f"### {g}：哪种日子开仓好", "",
                f"两种代表做法、拿到到期、成本 {hi:.0%}。「差」= 这种日子开仓的平均减去其余日子的平均；按进场日期分前后两半各算一次。", "",
                "| 做法 | 哪种日子 | 笔数 | 这种日子 | 其余日子 | 差（前半 / 后半） | 这种日子赚的比例 | 月份 |", "|---|---|---|---|---|---|---|---|"]
        for key in REFS:
            for t, tname in DAYS.items():
                on, off = pick(rows, key, "expiry", hi, lambda r: r["types"][t]), pick(rows, key, "expiry", hi, lambda r: not r["types"][t])
                a, b = stats(on), stats(off)
                table[f"{g}|{key}|day|{t}"] = {"on": a, "off": b, "halves": halves(on, off)}
                if not a or not b:
                    continue
                h = halves(on, off)
                out.append(f"| {STRUCTS[key][0]} | {tname} | {a['n']} | {pct(a['mean'])} | {pct(b['mean'])} | {pct(a['mean'] - b['mean'])}（{pct(h[0])} / {pct(h[1])}） | {a['win']:.0%} | {a['months_up']} / {a['months']} |")
        out += ["", f"### {g}：按进场时的环境分", "",
                f"拿到到期，成本 {hi:.0%}。每格：每 $1 风险的平均，赚的比例，（笔数；为正的月份 / 月份）。环境只有几段行情撑着的，月份那个数会很小。", "",
                "| 做法 | " + " | ".join(ENVS.values()) + " |", "|---|---|---|---|---|"]
        for key, (name, *_ ) in STRUCTS.items():
            cells = []
            for e in ENVS:
                st = table[f"{g}|env|{e}|{key}"] = stats(pick(rows, key, "expiry", hi, lambda r, e=e: env(r["types"]) == e))
                cells.append(f"{pct(st['mean'])}，赚 {st['win']:.0%}（{st['n']}；{st['months_up']} / {st['months']}）" if st else "—")
            out.append(f"| {name} | " + " | ".join(cells) + " |")
        sh_cells = []
        for e in ENVS:
            xs = [r["shares"] for r in rows if env(r["types"]) == e]
            sh_cells.append(f"平均 {statistics.mean(xs):+.1%}，涨的占 {sum(x > 0 for x in xs) / len(xs):.0%}（{len(xs)}）" if xs else "—")
        out.append("| 同样三个月拿正股 | " + " | ".join(sh_cells) + " |")
        out += ["", f"### {g}：顺着方向选", "",
                f"进场那天收盘在 50 日均线上方就做看涨的那一种，在下方就做看跌的；和一直做看涨、一直做看跌、反着选比。拿到到期，成本 {hi:.0%}。", "",
                "| 哪一对 | 怎么选 | 笔数 | 平均 | 中位数 | 赚的比例 | 亏光的比例 | 月份 |", "|---|---|---|---|---|---|---|---|"]
        for pname, (bull, bear) in PAIRS.items():
            have = [r for r in rows if bull in r["res"] and bear in r["res"]]
            c = f"{hi:.2f}"
            choose = {"一直做看涨的": lambda r: bull, "一直做看跌的": lambda r: bear,
                      "顺着选（均线上方看涨、下方看跌）": lambda r: bull if r["types"]["above50"] else bear,
                      "反着选": lambda r: bear if r["types"]["above50"] else bull}
            for cname, f in choose.items():
                s = table[f"{g}|trend|{pname}|{cname}"] = stats([(r["day"], r["res"][f(r)][c]["expiry"]) for r in have])
                if s:
                    out.append(f"| {pname} | {cname} | {s['n']} | {pct(s['mean'])} | {pct(s['median'])} | {s['win']:.0%} | {s['wiped']:.0%} | {s['months_up']} / {s['months']} |")
        out += ["", f"### {g}：按进场年份", "", "| 做法（拿到到期） | " + " | ".join(f"{y} 年" for y in ("2024", "2025", "2026")) + " |", "|---|---|---|---|"]
        for key, (name, *_ ) in STRUCTS.items():
            cells = []
            for y in ("2024", "2025", "2026"):
                s = stats(pick(rows, key, "expiry", hi, lambda r, y=y: r["day"][:4] == y))
                cells.append(f"{pct(s['mean'])}，赚 {s['win']:.0%}（{s['n']}）" if s else "—")
            out.append(f"| {name} | " + " | ".join(cells) + " |")
    checks = {f"{g.split('（')[0]}·{STRUCTS[key][0]}·{label}": robust.summary(pick(rows, key, ex, hi))
              for g, rows in groups.items() if rows for key in REFS for ex, label in EXITS.items()}
    out += ["", robust.table(checks, f"稳健性检查（代表做法，成本 {hi:.0%}）").rstrip()]
    out += ["", "## 每个标的（两种代表做法，拿到到期）", "", "| 标的 | 笔数 | " + " | ".join(STRUCTS[k][0] for k in REFS) + " | 同期正股 |", "|---|---|---|---|---|"]
    for und in rep["unds"]:
        rows = [r for r in rep["rows"] if r["und"] == und]
        if not rows:
            continue
        cells = []
        for key in REFS:
            s = stats(pick(rows, key, "expiry", hi))
            cells.append(f"{pct(s['mean'])}，赚 {s['win']:.0%}，亏光 {s['wiped']:.0%}" if s else "—")
        sh = [r["shares"] for r in rows]
        out.append(f"| {und} | {len(rows)} | " + " | ".join(cells) + f" | 平均 {statistics.mean(sh):+.1%}，涨的占 {sum(x > 0 for x in sh) / len(sh):.0%} |")
    notes = ["进场的日子从 2024-02 到 2026 年中，大部分时间市场在涨，看涨的做法在这段时间里天然好看；每天开一笔、每笔拿三个月，前后六十笔是重叠的，独立的样本大约十个季度。所以每个平均旁边都列了「月份」。",
             "进场和中途走用的是期权当天的收盘价（最后一笔成交），两条腿的最后一笔不一定是同一时刻，价差的数会有噪音；到期按正股当天收盘价结算。",
             "行权价取最接近的 $5 整数倍；那天两条腿只要有一条没有成交，这一笔就不算。"]
    if rep.get("stopped_at"):
        notes.append(f"时间不够，只跑到 {rep['stopped_at']}；没跑到的标的：{'、'.join(rep['not_reached']) or '无'}。")
    notes += [f"请求失败的原因（最后几条）：`{e}`" for e in (rep.get("errors") or [])[-3:]]
    where = []
    if now:
        where = ["", "## 现在各个标的在哪种环境", "", "| 标的 | 最新收盘 | 对 50 日均线 | 对一年高点 | 环境 | 今天是不是大跌日 / 连跌 3 天 |", "|---|---|---|---|---|---|"]
        where += [f"| {u} | {t['close']} | {t['vs50']:+.1%} | {t['vs_high']:+.1%} | {ENVS[env(t)].split('（')[0]} | {'是' if t['big_drop'] else '否'} / {'是' if t['streak3'] else '否'} |"
                  for u, t in now.items()]
        where += ["", "对照下面每一组「按进场时的环境分」那张表里同名的一列，就是过去在这种环境里开仓、三个月后的结果。"]
    out = where + out
    head = [f"# 三个月的垂直价差：哪种日子开、怎么走、行权价怎么选、指数还是个股（{rep['generated']}）", "",
            "用户 11-19：平时做的是垂直价差，拿三个月，买入看涨价差和卖出看跌价差两种都做，看跌的也做，有时候顺着方向。这里用真实期权价把这几件事各查一遍：2024-02 起每个交易日收盘开一笔，"
            "买第一个 80 天以后到期的月度期权，只算已经到期的。只研究，不改交易，不是投资建议。"]
    return table, "\n".join(head + out + ["", "## 说明", ""] + [f"- {n}" for n in notes]) + "\n"


def render() -> None:
    """The report again from the newest saved run, with where each underlying stands now."""
    import json
    path = sorted((ROOT / "research").glob("*-vertical.json"))[-1]
    rep = json.loads(path.read_text())
    table, md = report(rep, now_types(rep["unds"]))
    path.with_suffix(".md").write_text(md)
    path.write_text(json.dumps({**rep, "table": table}, ensure_ascii=False) + "\n")
    print(md)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "render":
        return render()
    from hero.alpaca import Alpaca
    reads = walk(db.Book(db.load()), START)
    big = {r["day"] for r in reads if "open" in r and r["open"]["big"][0] >= FLAG_OPEN}
    rep = run(Alpaca(), big_days=big)
    table, md = report(rep, now_types(rep["unds"]))
    save("vertical", {**rep, "table": table}, md)


if __name__ == "__main__":
    sys.exit(main())
