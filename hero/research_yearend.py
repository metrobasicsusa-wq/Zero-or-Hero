"""What the last two months of the year have done (owner, 11-03: 「今年快要结束了 回测下最后两月会发什么」).

Price layer only, Yahoo daily closes back to 1993 (SPY), 1999 (QQQ), 2000 (SMH / IWM), 1990 (VIX). Every window runs
from the close of the last session on or before Nov 2 (where 2026 stands tonight) to the close of Dec 31, with the
sub-windows the calendar folklore talks about: November, the first half of December (tax-loss selling), the second
half (Santa Claus), Thanksgiving week, and January after. Then the same windows sliced by what this year looks like
(midterm year, year-to-date, the chip drawdown at Nov 2, October), the losers and winners of the 100-stock universe
(tax-loss selling and the January effect; today's universe, so survivors), the momentum top-8 at year end, the VIX,
and the sectors. Record only: nothing here changes a rule.
"""
from __future__ import annotations

import gzip
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

from hero import robust
from hero.research_ideas import save
from hero.research_support import ohlcv

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "research" / "evolve-cache" / "yearend-bars.json.gz"
START = "1990-01-01"
INDEXES = ("SPY", "QQQ", "IWM", "DIA", "SMH")
SECTORS = ("XLK", "XLF", "XLE", "XLV", "XLY", "XLI", "XLP", "XLU", "XLB", "XLRE", "XLC", "GLD", "TLT")
VIX = "^VIX"
MOMENTUM = ("SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "XLV", "XLY", "XLI",
            "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "AVGO", "JPM")
ANCHOR = (11, 2)          # the entry close: the last session on or before Nov 2
LOSER_DD, LOSER_YTD, WINNER_YTD = -0.25, 0.0, 0.30
STOCK_COST = 0.001
THIS_YEAR = 2026


# ---- bars ------------------------------------------------------------------------------------------------------

def universe() -> list[str]:
    u = json.loads((ROOT / "data" / "universe.json").read_text())
    return list(dict.fromkeys(list(INDEXES) + list(SECTORS) + [VIX] + list(MOMENTUM) + u["stocks"] + u["etfs"]))


def load(syms: list[str], log=lambda *a: None) -> dict[str, list[dict]]:
    """Yahoo daily bars for every symbol, cached for the day (the cache dir is gitignored)."""
    today = date.today().isoformat()
    if CACHE.exists():
        c = json.loads(gzip.decompress(CACHE.read_bytes()))
        if c.get("day") == today and all(s in c["bars"] for s in syms):
            return c["bars"]
    bars: dict[str, list[dict]] = {}

    def one(s):
        for k in range(3):
            try:
                return s, [{"d": b["d"], "c": b["c"]} for b in ohlcv(s, START)]
            except Exception as e:   # noqa: BLE001
                time.sleep(1 + k)
                err = e
        log(f"  {s}: {err}")
        return s, []

    with ThreadPoolExecutor(6) as ex:
        for s, bs in ex.map(one, syms):
            if bs:
                bars[s] = bs
    log(f"  {len(bars)} symbols")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_bytes(gzip.compress(json.dumps({"day": today, "bars": bars}).encode()))
    return bars


# ---- calendar --------------------------------------------------------------------------------------------------

def close_on_or_before(bars: list[dict], d: str) -> tuple[int, float] | None:
    """(index, close) of the last session on or before day d; None when the bars start after d."""
    lo, hi = 0, len(bars) - 1
    if not bars or bars[0]["d"] > d:
        return None
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if bars[mid]["d"] <= d:
            lo = mid
        else:
            hi = mid - 1
    return lo, bars[lo]["c"]


def ret(bars: list[dict], d0: str, d1: str) -> float | None:
    """Close-to-close return from the last session on or before d0 to the last on or before d1; None when the
    data ends more than a week before d1 (a window that has not happened yet)."""
    a, b = close_on_or_before(bars, d0), close_on_or_before(bars, d1)
    if not a or not b or b[0] <= a[0]:
        return None
    if bars[-1]["d"] < d1 and (date.fromisoformat(d1) - date.fromisoformat(bars[-1]["d"])).days > 7:
        return None
    return b[1] / a[1] - 1


def thanksgiving(year: int) -> date:
    d = date(year, 11, 1)
    first_thu = d + timedelta(days=(3 - d.weekday()) % 7)
    return first_thu + timedelta(weeks=3)


def windows(year: int) -> dict[str, tuple[str, str]]:
    """Each named window as (entry close day, exit close day)."""
    tg = thanksgiving(year)
    return {
        "nov_dec": (f"{year}-11-02", f"{year}-12-31"),
        "nov": (f"{year}-11-02", f"{year}-11-30"),
        "dec_1h": (f"{year}-11-30", f"{year}-12-15"),
        "dec_2h": (f"{year}-12-15", f"{year}-12-31"),
        "tg_week": ((tg - timedelta(days=7)).isoformat(), (tg + timedelta(days=1)).isoformat()),
        "jan": (f"{year}-12-31", f"{year + 1}-01-31"),
    }


def year_row(bars: list[dict], year: int) -> dict | None:
    """The windows of one year for one symbol, plus the state at the Nov 2 close and the dip inside the window."""
    a = close_on_or_before(bars, f"{year}-11-02")
    if not a or bars[a[0]]["d"] < f"{year}-10-01" or bars[-1]["d"] < f"{year}-12-31" and year != THIS_YEAR:
        return None
    i, c0 = a
    row = {"year": year}
    for k, (d0, d1) in windows(year).items():
        row[k] = ret(bars, d0, d1)
    if year == THIS_YEAR:
        row = {"year": year, **{k: None for k in windows(year)}}
    end = close_on_or_before(bars, f"{year}-12-31")
    seg = [b["c"] for b in bars[i + 1:(end[0] + 1) if end else len(bars)]]
    row["dip"] = min(seg) / c0 - 1 if seg else None
    row["peak"] = max(seg) / c0 - 1 if seg else None
    prev = close_on_or_before(bars, f"{year - 1}-12-31")
    row["ytd"] = c0 / prev[1] - 1 if prev and bars[prev[0]]["d"] > f"{year - 1}-11-30" else None
    row["oct"] = ret(bars, f"{year}-09-30", f"{year}-10-31") if bars[0]["d"] <= f"{year}-09-30" else None
    row["dd20"] = c0 / max(b["c"] for b in bars[max(0, i - 20):i + 1]) - 1 if i >= 20 else None
    row["dd52"] = c0 / max(b["c"] for b in bars[max(0, i - 252):i + 1]) - 1 if i >= 252 else None
    row["r5"] = c0 / bars[i - 5]["c"] - 1 if i >= 5 else None
    return row


def is_midterm(year: int) -> bool:
    return year % 4 == 2


# ---- tables ----------------------------------------------------------------------------------------------------

def stats(xs: list[float | None]) -> dict | None:
    v = [x for x in xs if x is not None]
    if not v:
        return None
    return {"n": len(v), "mean": statistics.mean(v), "median": statistics.median(v), "up": sum(x > 0 for x in v) / len(v),
            "worst": min(v), "best": max(v)}


def seasonal(rows: list[dict], keys=("nov_dec", "nov", "dec_1h", "dec_2h", "tg_week", "jan", "dip")) -> dict:
    past = [r for r in rows if r["year"] != THIS_YEAR]
    return {k: stats([r.get(k) for r in past]) for k in keys}


def sliced(rows: list[dict], name: str, keep) -> dict:
    sel = [r for r in rows if r["year"] != THIS_YEAR and keep(r)]
    return {"name": name, "years": [r["year"] for r in sel], **seasonal(sel)}


def conditions(rows: list[dict], this: dict | None) -> list[dict]:
    """The Nov-Dec windows sliced by what the year looked like at the Nov 2 close; the slice 2026 falls in is marked."""
    out = [
        sliced(rows, "全部年份", lambda r: True),
        sliced(rows, "中期选举年", lambda r: is_midterm(r["year"])),
        sliced(rows, "大选年", lambda r: r["year"] % 4 == 0),
        sliced(rows, "单数年（选前 / 选后）", lambda r: r["year"] % 2 == 1),
        sliced(rows, "到 11-02 今年涨 ≥10%", lambda r: (r.get("ytd") or 0) >= 0.10),
        sliced(rows, "到 11-02 今年涨 ≥20%", lambda r: (r.get("ytd") or 0) >= 0.20),
        sliced(rows, "到 11-02 今年是跌的", lambda r: r.get("ytd") is not None and r["ytd"] < 0),
        sliced(rows, "10 月是跌的", lambda r: r.get("oct") is not None and r["oct"] < 0),
        sliced(rows, "10 月涨的", lambda r: r.get("oct") is not None and r["oct"] >= 0),
        sliced(rows, "11-02 离 20 日高点 ≥5%", lambda r: (r.get("dd20") or 0) <= -0.05),
        sliced(rows, "11-02 离 20 日高点 ≥8%", lambda r: (r.get("dd20") or 0) <= -0.08),
        sliced(rows, "11-02 离 20 日高点不到 2%", lambda r: r.get("dd20") is not None and r["dd20"] > -0.02),
        sliced(rows, "前 5 天跌 ≥3%", lambda r: (r.get("r5") or 0) <= -0.03),
    ]
    if this:
        for c in out:
            c["this_year"] = {
                "全部年份": True, "中期选举年": is_midterm(THIS_YEAR), "大选年": THIS_YEAR % 4 == 0, "单数年（选前 / 选后）": THIS_YEAR % 2 == 1,
                "到 11-02 今年涨 ≥10%": (this.get("ytd") or 0) >= 0.10, "到 11-02 今年涨 ≥20%": (this.get("ytd") or 0) >= 0.20,
                "到 11-02 今年是跌的": (this.get("ytd") or 0) < 0, "10 月是跌的": (this.get("oct") or 0) < 0,
                "10 月涨的": (this.get("oct") or 0) >= 0, "11-02 离 20 日高点 ≥5%": (this.get("dd20") or 0) <= -0.05,
                "11-02 离 20 日高点 ≥8%": (this.get("dd20") or 0) <= -0.08,
                "11-02 离 20 日高点不到 2%": (this.get("dd20") or 0) > -0.02, "前 5 天跌 ≥3%": (this.get("r5") or 0) <= -0.03,
            }[c["name"]]
    return out


def losers_winners(bars: dict[str, list[dict]], stocks: list[str], years: range) -> dict:
    """Tax-loss selling and the January effect: the universe's losers (≥25% under the 52-week high and down on the
    year at Nov 2) against its winners (up ≥30% on the year), each window relative to SPY, year by year."""
    out = {"years": [], "this_year": {"losers": [], "winners": []}}
    spy = bars["SPY"]
    for y in list(years) + [THIS_YEAR]:
        groups = {"losers": [], "winners": [], "others": []}
        for s in stocks:
            if s == "SPY" or s not in bars:
                continue
            r = year_row(bars[s], y)
            if not r or r.get("dd52") is None or r.get("ytd") is None:
                continue
            g = "losers" if r["dd52"] <= LOSER_DD and r["ytd"] < LOSER_YTD else "winners" if r["ytd"] >= WINNER_YTD else "others"
            groups[g].append((s, r))
        if y == THIS_YEAR:
            out["this_year"] = {g: sorted(((s, r["dd52"], r["ytd"]) for s, r in rs), key=lambda t: t[1])[:25] for g, rs in groups.items() if g != "others"}
            continue
        sp = year_row(spy, y) or {}
        row = {"year": y, "n": {g: len(rs) for g, rs in groups.items()}}
        for k in ("nov_dec", "nov", "dec_1h", "dec_2h", "jan"):
            base = sp.get(k)
            for g, rs in groups.items():
                v = [r[k] for _, r in rs if r.get(k) is not None]
                row[f"{g}|{k}"] = (statistics.mean(v) - (base or 0)) if v and base is not None else None
        out["years"].append(row)
    out["summary"] = {}
    for g in ("losers", "winners", "others"):
        for k in ("nov_dec", "nov", "dec_1h", "dec_2h", "jan"):
            out["summary"][f"{g}|{k}"] = stats([r.get(f"{g}|{k}") for r in out["years"]])
    return out


def momentum_yearend(bars: dict[str, list[dict]], years: range, top_n: int = 8, look: int = 63, sma: int = 50) -> dict:
    """The strategy's shape at Nov 2 (top-8 by 63-day return among the 20 names above their 50-day average),
    held to Dec 31, against the equal-weight 20 and SPY."""
    rows = []
    for y in years:
        ranked = []
        for s in MOMENTUM:
            b = bars.get(s)
            a = close_on_or_before(b, f"{y}-11-02") if b else None
            if not a or a[0] < max(look, sma) or b[a[0]]["d"] < f"{y}-10-25":
                continue
            i, c0 = a
            above = c0 > statistics.mean(x["c"] for x in b[i - sma + 1:i + 1])
            r = ret(b, f"{y}-11-02", f"{y}-12-31")
            if r is None:
                continue
            ranked.append((c0 / b[i - look]["c"] - 1, s, r, above))
        if len(ranked) < 10:
            continue
        ranked.sort(reverse=True)
        picks = [t for t in ranked if t[3]][:top_n]
        bottom = ranked[-top_n:]
        rows.append({"year": y, "n": len(ranked), "picks": [t[1] for t in picks],
                     "top": statistics.mean(t[2] for t in picks) if picks else 0.0,
                     "bottom": statistics.mean(t[2] for t in bottom), "ew": statistics.mean(t[2] for t in ranked),
                     "spy": ret(bars["SPY"], f"{y}-11-02", f"{y}-12-31"), "held": len(picks)})
    return {"years": rows, "summary": {k: stats([r[k] for r in rows]) for k in ("top", "bottom", "ew", "spy")},
            "top_beats_ew": sum(r["top"] > r["ew"] for r in rows) / len(rows) if rows else None}


def vix_yearend(bars: list[dict], years: range) -> dict:
    rows = []
    for y in years:
        a, b = close_on_or_before(bars, f"{y}-11-02"), close_on_or_before(bars, f"{y}-12-31")
        if not a or not b or bars[a[0]]["d"] < f"{y}-10-25":
            continue
        rows.append({"year": y, "start": a[1], "end": b[1], "chg": b[1] / a[1] - 1,
                     "low": min(x["c"] for x in bars[a[0]:b[0] + 1]), "high": max(x["c"] for x in bars[a[0]:b[0] + 1])})
    now = close_on_or_before(bars, f"{THIS_YEAR}-11-02")
    by = {}
    for name, keep in (("全部", lambda r: True), ("11-02 VIX < 15", lambda r: r["start"] < 15),
                       ("15–20", lambda r: 15 <= r["start"] < 20), ("≥ 20", lambda r: r["start"] >= 20)):
        sel = [r for r in rows if keep(r)]
        by[name] = {"n": len(sel), "years": [r["year"] for r in sel], "chg": stats([r["chg"] for r in sel]),
                    "fell": sum(r["chg"] < 0 for r in sel) / len(sel) if sel else None}
    return {"years": rows, "by_start": by, "this_year": now[1] if now else None}


def sectors(bars: dict[str, list[dict]], years: range) -> list[dict]:
    out = []
    for s in SECTORS + ("SPY",):
        if s not in bars:
            continue
        rs = [(y, ret(bars[s], f"{y}-11-02", f"{y}-12-31")) for y in years]
        rs = [(y, r) for y, r in rs if r is not None]
        st = stats([r for _, r in rs])
        if st:
            out.append({"symbol": s, **st, "since": rs[0][0], "last5": [round(r, 4) for _, r in rs[-5:]]})
    return sorted(out, key=lambda r: -r["mean"])


def robust_rows(rows_by: dict[str, list[dict]]) -> dict[str, dict | None]:
    return {s: robust.summary([(f"{r['year']}-11-02", r["nov_dec"]) for r in rows if r.get("nov_dec") is not None],
                              extra_cost=STOCK_COST) for s, rows in rows_by.items()}


# ---- run -------------------------------------------------------------------------------------------------------

def run(bars: dict[str, list[dict]], stocks: list[str]) -> dict:
    years = range(1993, THIS_YEAR)
    rows_by = {s: [r for r in (year_row(bars[s], y) for y in list(years) + [THIS_YEAR]) if r] for s in INDEXES if s in bars}
    this = {s: next((r for r in rs if r["year"] == THIS_YEAR), None) for s, rs in rows_by.items()}
    rep = {
        "as_of": date.today().isoformat(),
        "this_year": this,
        "seasonal": {s: seasonal(rs) for s, rs in rows_by.items()},
        "years": {s: [r for r in rs if r["year"] >= 2008] for s, rs in rows_by.items()},
        "conditions": {s: conditions(rs, this.get(s)) for s, rs in rows_by.items() if s in ("SPY", "QQQ", "SMH")},
        "losers_winners": losers_winners(bars, stocks, range(2010, THIS_YEAR)),
        "momentum": momentum_yearend(bars, range(2010, THIS_YEAR)),
        "vix": vix_yearend(bars[VIX], range(1990, THIS_YEAR)) if VIX in bars else None,
        "sectors": sectors(bars, range(1999, THIS_YEAR)),
        "robust": robust_rows({s: rows_by[s] for s in ("SPY", "QQQ", "SMH", "IWM") if s in rows_by}),
    }
    return rep


def _p(x, nd=1) -> str:
    return "—" if x is None else f"{x * 100:+.{nd}f}%"


def _st(s: dict | None) -> str:
    return "—" if not s else f"{_p(s['mean'])} / {_p(s['median'])} / {s['up']:.0%}（{s['n']}）"


def markdown(rep: dict) -> str:
    o = [f"# 一年的最后两个月：11-02 收盘到 12-31，历史上发生了什么 {rep['as_of']}", "",
         "价格层，Yahoo 日线：SPY 1993 起、QQQ 1999、SMH / IWM 2000、VIX 1990。每个窗口从 11-02（或之前最后一个交易日）收盘到 12-31 收盘；"
         "子窗口：11 月、12 月上半（到 12-15）、12 月下半、感恩节那周、之后的 1 月。「最深」= 窗口内最低收盘对 11-02 收盘。"
         "每格「平均 / 中位数 / 涨的年份比例（年数）」。只研究，不改交易。", ""]
    t = rep["this_year"]
    o += ["## 今年 11-02 收盘的状态", "", "| | 今年到 11-02 | 10 月 | 离 20 日高点 | 离 52 周高点 | 前 5 天 |", "|---|---|---|---|---|---|"]
    for s, r in t.items():
        if r:
            o.append(f"| {s} | {_p(r.get('ytd'))} | {_p(r.get('oct'))} | {_p(r.get('dd20'))} | {_p(r.get('dd52'))} | {_p(r.get('r5'))} |")
    o += ["", "## 每年都这样吗：11-02 到 12-31", "", "| | 两个月 | 11 月 | 12 月上半 | 12 月下半 | 感恩节周 | 之后的 1 月 | 窗口内最深 |",
          "|---|---|---|---|---|---|---|---|"]
    for s, se in rep["seasonal"].items():
        o.append(f"| {s} | " + " | ".join(_st(se[k]) for k in ("nov_dec", "nov", "dec_1h", "dec_2h", "tg_week", "jan")) +
                 f" | {_p(se['dip']['mean']) if se.get('dip') else '—'}（中位 {_p(se['dip']['median']) if se.get('dip') else '—'}） |")
    o += ["", "### 近年逐年（SPY / QQQ / SMH 的两个月，和 SMH 窗口内最深）", "", "| 年 | SPY | QQQ | SMH | IWM | SMH 最深 | SMH 11-02 离 20 日高 | 中期选举 |", "|---|---|---|---|---|---|---|---|"]
    ys = rep["years"]
    for i, r in enumerate(ys.get("SPY", [])):
        y = r["year"]
        if y == THIS_YEAR:
            continue
        g = lambda s: next((x for x in ys.get(s, []) if x["year"] == y), {})
        o.append(f"| {y} | {_p(r['nov_dec'])} | {_p(g('QQQ').get('nov_dec'))} | {_p(g('SMH').get('nov_dec'))} | {_p(g('IWM').get('nov_dec'))} | "
                 f"{_p(g('SMH').get('dip'))} | {_p(g('SMH').get('dd20'))} | {'是' if is_midterm(y) else ''} |")
    o += ["", "## 按今年的样子分组（两个月的结果）", ""]
    for s, cs in rep["conditions"].items():
        o += [f"### {s}", "", "| 分组 | 今年在这组 | 两个月 | 12 月上半 | 12 月下半 | 之后的 1 月 | 最深 | 年份 |", "|---|---|---|---|---|---|---|---|"]
        for c in cs:
            yrs = c["years"]
            o.append(f"| {c['name']} | {'**是**' if c.get('this_year') else ''} | {_st(c['nov_dec'])} | {_st(c['dec_1h'])} | {_st(c['dec_2h'])} | "
                     f"{_st(c['jan'])} | {_p(c['dip']['mean']) if c.get('dip') else '—'} | {', '.join(str(y) for y in yrs[-8:])}{'…' if len(yrs) > 8 else ''} |")
        o.append("")
    lw = rep["losers_winners"]
    o += ["## 输家和赢家：卖税损和 1 月效应（100 只池子，2010 起；今天的池子，所以是幸存者）", "",
          "11-02 时离 52 周高点 ≥25% 且今年是跌的 = 输家；今年涨 ≥30% = 赢家。每格是该组平均减 SPY（超额），按年平均。", "",
          "| 组 | 两个月 | 11 月 | 12 月上半 | 12 月下半 | 之后的 1 月 |", "|---|---|---|---|---|---|"]
    for g, name in (("losers", "输家"), ("winners", "赢家"), ("others", "其他")):
        o.append(f"| {name} | " + " | ".join(_st(lw["summary"].get(f"{g}|{k}")) for k in ("nov_dec", "nov", "dec_1h", "dec_2h", "jan")) + " |")
    o += ["", "| 年 | 输家数 | 输家两个月 | 输家 12 上半 | 输家 12 下半 | 输家 1 月 | 赢家数 | 赢家两个月 | 赢家 1 月 |", "|---|---|---|---|---|---|---|---|---|"]
    for r in lw["years"]:
        o.append(f"| {r['year']} | {r['n']['losers']} | {_p(r.get('losers|nov_dec'))} | {_p(r.get('losers|dec_1h'))} | {_p(r.get('losers|dec_2h'))} | "
                 f"{_p(r.get('losers|jan'))} | {r['n']['winners']} | {_p(r.get('winners|nov_dec'))} | {_p(r.get('winners|jan'))} |")
    ty = lw["this_year"]
    o += ["", "今年 11-02 的输家（离 52 周高、今年）：" + "、".join(f"{s} {_p(dd, 0)} / {_p(ytd, 0)}" for s, dd, ytd in ty.get("losers", [])[:20]),
          "", "今年的赢家：" + "、".join(f"{s} {_p(ytd, 0)}" for s, _, ytd in sorted(ty.get("winners", []), key=lambda t: -t[2])[:20]), ""]
    m = rep["momentum"]
    o += ["## 动量策略的形状在年底：11-02 按 63 日涨幅选 20 只里 50 日线上的前 8，拿到 12-31", "",
          f"| | 前 8 | 等权 20 | 后 8 | SPY |", "|---|---|---|---|---|",
          "| 平均 / 中位 / 涨的年份 | " + " | ".join(_st(m["summary"][k]) for k in ("top", "bottom", "ew", "spy")) + " |",
          "", f"前 8 跑赢等权的年份：{m['top_beats_ew']:.0%}。" if m.get("top_beats_ew") is not None else "", "",
          "| 年 | 前 8 | 等权 | 后 8 | SPY | 前 8 是谁 |", "|---|---|---|---|---|---|"]
    for r in m["years"]:
        o.append(f"| {r['year']} | {_p(r['top'])} | {_p(r['ew'])} | {_p(r['bottom'])} | {_p(r['spy'])} | {' '.join(r['picks'])} |")
    v = rep.get("vix")
    if v:
        o += ["", f"## VIX：11-02 到 12-31（今年 11-02 收 {v['this_year']:.1f}）" if v.get("this_year") else "## VIX：11-02 到 12-31", "", "| 11-02 的 VIX | 年数 | 变化（平均 / 中位） | 下跌的年份 | 年份 |", "|---|---|---|---|---|"]
        for name, b in v["by_start"].items():
            o.append(f"| {name} | {b['n']} | {_p(b['chg']['mean']) if b['chg'] else '—'} / {_p(b['chg']['median']) if b['chg'] else '—'} | "
                     f"{b['fell']:.0%} | {', '.join(str(y) for y in b['years'][-10:])} |" if b["n"] else f"| {name} | 0 | — | — | |")
        last = v["years"][-6:]
        o.append("")
        o.append("近年：" + "；".join(f"{r['year']} {r['start']:.0f} → {r['end']:.0f}（最高 {r['high']:.0f}）" for r in last))
    o += ["", "## 板块：11-02 到 12-31 的平均（1999 起，有多少年算多少年）", "", "| | 平均 | 中位 | 涨的年份 | 起 | 最近 5 年 |", "|---|---|---|---|---|---|"]
    for r in rep["sectors"]:
        o.append(f"| {r['symbol']} | {_p(r['mean'])} | {_p(r['median'])} | {r['up']:.0%} | {r['since']} | {' '.join(_p(x, 0) for x in r['last5'])} |")
    o += ["", robust.table(rep["robust"], "稳健性检查（每年的两个月当一笔）"), ""]
    return "\n".join(o)


def main() -> None:
    log = lambda *a: print(*a, file=sys.stderr, flush=True)
    syms = universe()
    log(f"fetching {len(syms)} symbols")
    bars = load(syms, log)
    u = json.loads((ROOT / "data" / "universe.json").read_text())
    rep = run(bars, u["stocks"] + u["etfs"])
    save("yearend", rep, markdown(rep))


if __name__ == "__main__":
    main()
