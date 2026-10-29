"""From here to year-end (owner, 10-29: 「这到年底 要研究下」).

What the S&P 500 did from the last close on or before Oct 29 to the last close of the year, every year since
1950, and the same split by the conditions that hold this year: a midterm-election year, short rates up over
the past six months (^IRX, the 13-week bill, as the Fed-funds proxy), the year-to-date gain at the start, and
the distance from the one-year high. For each window also the deepest dip from the start and the biggest
peak-to-trough fall inside it, so the pullback triggers can be judged. Then three narrower questions:

  rotation - since 2000, Nasdaq 100 (QQQ) against small caps (IWM), the equal-weight S&P (RSP, since 2003),
             semiconductors (SMH) and SPY over the same window;
  losers   - since 2010, do the year's losers (20% or more below their one-year high on Oct 29) get sold in
             November and bounce in late December? The 98 large caps of research/2026-10-02-earnings-
             reactions.json (today's large caps: survivorship favours the bounce);
  our rule - the main account's momentum rule replayed (hero.research_stock_guard.simulate, hold mode) and
             read off for Oct 29 to Dec 31 of 2021-2025, against SPY.

Prices: Yahoo daily closes adjusted for splits and dividends (^GSPC and ^IRX are not adjusted; they need not
be). Every group's yearly returns go through hero.robust as (year-end day, return per $1), so the
"without the best 3" and median columns are shown next to the mean. Research only; nothing here trades.
Run: python -m hero.research_yearend
"""

from __future__ import annotations

import json
import statistics
from datetime import date
from pathlib import Path

from hero import backtest, robust
from hero.research_stock_guard import simulate, yahoo
from hero.strategies import momentum  # noqa: F401  (simulate needs the package import side effects)

ROOT = Path(__file__).resolve().parent.parent
EVENTS = ROOT / "research" / "2026-10-02-earnings-reactions.json"
CONFIG = ROOT / "config" / "strategy.json"
START_MD, END_MD, MID_MD = "10-29", "12-31", "11-30"
FIRST_YEAR = 1950
RATES_UP = 0.25           # percentage points over six months
LOSER = -0.20             # 20% or more below the one-year high
WINNER = -0.10            # within 10% of it
EXTRA_COST = 0.001


def on_or_before(days: list[str], d: str) -> int | None:
    """Index of the last day <= d, or None."""
    i = None
    for j, x in enumerate(days):
        if x <= d:
            i = j
        else:
            break
    return i


def window(px: dict[str, float], year: int, start_md: str = START_MD, end_md: str = END_MD) -> dict | None:
    """Return, deepest dip from the start and biggest fall inside, from the last close on or before
    `year`-`start_md` to the last close on or before `year`-`end_md`."""
    days = sorted(px)
    i0, i1 = on_or_before(days, f"{year}-{start_md}"), on_or_before(days, f"{year}-{end_md}")
    if i0 is None or i1 is None or i1 <= i0 or days[i0][:4] != str(year):
        return None
    path = [px[d] for d in days[i0:i1 + 1]]
    p0 = path[0]
    peak, dd = p0, 0.0
    for x in path:
        peak = max(peak, x)
        dd = min(dd, x / peak - 1)
    return {"year": year, "start": days[i0], "end": days[i1], "r": path[-1] / p0 - 1,
            "dip": min(path) / p0 - 1, "dd": dd, "best": max(path) / p0 - 1, "sessions": len(path) - 1}


def conditions(spx: dict[str, float], irx: dict[str, float] | None, year: int, start_md: str = START_MD) -> dict:
    """What was true at the window's start: midterm year, rates up over six months, YTD gain, distance from
    the one-year high, above the 200-day."""
    days = sorted(spx)
    i0 = on_or_before(days, f"{year}-{start_md}")
    p0 = spx[days[i0]]
    prev_year = on_or_before(days, f"{year - 1}-12-31")
    ytd = p0 / spx[days[prev_year]] - 1 if prev_year is not None else None
    hi = max(spx[d] for d in days[max(0, i0 - 251):i0 + 1])
    ma = statistics.mean(spx[d] for d in days[max(0, i0 - 199):i0 + 1])
    rates = None
    if irx:
        rd = sorted(irx)
        a, b = on_or_before(rd, f"{year}-{start_md}"), on_or_before(rd, f"{year}-04-30")
        if a is not None and b is not None and rd[b][:4] == str(year):
            rates = irx[rd[a]] - irx[rd[b]]
    return {"midterm": year % 4 == 2, "rates": rates, "rates_up": None if rates is None else rates >= RATES_UP,
            "ytd": ytd, "off_high": p0 / hi - 1, "above200": p0 > ma}


def ytd_band(ytd: float | None) -> str | None:
    if ytd is None:
        return None
    return "年初至今涨 15% 以上" if ytd >= 0.15 else ("年初至今涨 0–15%" if ytd >= 0 else "年初至今是跌的")


def groups(rows: list[dict], now: dict) -> dict[str, list[dict]]:
    """The groups to show: everything, then the ones this year belongs to."""
    g = {"全部年份": rows, "2000 年起": [r for r in rows if r["year"] >= 2000]}
    if now["midterm"]:
        g["中期选举年"] = [r for r in rows if r["midterm"]]
    if now["rates_up"]:
        g[f"半年内短期利率升 {RATES_UP:.2f} 点以上"] = [r for r in rows if r["rates_up"]]
    if now["midterm"] and now["rates_up"]:
        g["中期选举年且利率在升"] = [r for r in rows if r["midterm"] and r["rates_up"]]
    band = ytd_band(now["ytd"])
    if band:
        g[band] = [r for r in rows if ytd_band(r["ytd"]) == band]
        if now["rates_up"]:
            g[band + "，且利率在升"] = [r for r in rows if ytd_band(r["ytd"]) == band and r["rates_up"]]
    if now["off_high"] >= -0.03:
        g["离一年高点 3% 以内"] = [r for r in rows if r["off_high"] >= -0.03]
    elif now["off_high"] >= -0.10:
        g["离一年高点 3%–10%"] = [r for r in rows if -0.10 <= r["off_high"] < -0.03]
    return g


def stats(rows: list[dict]) -> dict | None:
    if not rows:
        return None
    v = sorted(r["r"] for r in rows)
    return {"n": len(v), "years": [r["year"] for r in rows], "up": sum(x > 0 for x in v) / len(v), "mean": statistics.mean(v), "median": statistics.median(v),
            "trim": statistics.mean(v[:-3]) if len(v) > 3 else None, "worst": v[0], "best": v[-1],
            "dip": statistics.mean(r["dip"] for r in rows), "dd": statistics.mean(r["dd"] for r in rows),
            "dip5": sum(r["dip"] <= -0.05 for r in rows) / len(rows), "dip10": sum(r["dip"] <= -0.10 for r in rows) / len(rows),
            "dd5": sum(r["dd"] <= -0.05 for r in rows) / len(rows),
            "flags": (robust.summary([(r["end"], r["r"]) for r in rows], extra_cost=EXTRA_COST) or {}).get("flags", [])}


def pct(x, n: int = 1) -> str:
    return "—" if x is None else f"{x * 100:+.{n}f}%"


def table(g: dict[str, list[dict]]) -> list[str]:
    out = ["| 情况 | 年数 | 涨的比例 | 平均 | 中位数 | 去掉最好 3 年 | 最差 | 最好 | 窗口里平均最低 | 跌过 5% 的年份 | 跌过 10% | 中途回撤超 5% | robust 提醒 |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, rows in g.items():
        s = stats(rows)
        if not s:
            out.append(f"| {name} | 0 | — | — | — | — | — | — | — | — | — | — | 没有 |")
            continue
        out.append(f"| {name} | {s['n']} | {s['up']:.0%} | {pct(s['mean'])} | {pct(s['median'])} | {pct(s['trim'])} | {pct(s['worst'])} | {pct(s['best'])} | "
                   f"{pct(s['dip'])} | {s['dip5']:.0%} | {s['dip10']:.0%} | {s['dd5']:.0%} | {'；'.join(s['flags']) or '—'} |")
    small = [(name, rows) for name, rows in g.items() if 0 < len(rows) <= 10]
    if small:
        out += ["", "小样本组是哪些年（涨跌）：" + "；".join(
            f"{name}：" + "、".join(f"{r['year']} {pct(r['r'], 0)}" for r in sorted(rows, key=lambda r: r['year'])) for name, rows in small)]
    return out


def halves(px: dict[str, float], years: list[int]) -> dict[str, dict]:
    """The window in two halves and the last five sessions of December."""
    out = {"10-29 到 11-30": [], "11-30 到 12-31": [], "12 月最后 5 个交易日": []}
    days = sorted(px)
    for y in years:
        a, b = window(px, y, START_MD, MID_MD), window(px, y, MID_MD, END_MD)
        if a and b:
            out["10-29 到 11-30"].append(a["r"])
            out["11-30 到 12-31"].append(b["r"])
        i1 = on_or_before(days, f"{y}-{END_MD}")
        if i1 is not None and i1 >= 5:
            out["12 月最后 5 个交易日"].append(px[days[i1]] / px[days[i1 - 5]] - 1)
    return {k: {"n": len(v), "mean": statistics.mean(v), "median": statistics.median(v), "up": sum(x > 0 for x in v) / len(v)}
            for k, v in out.items() if v}


def rotation(px: dict[str, dict[str, float]], years: list[int]) -> dict:
    """Window returns of each ETF per year, and IWM minus QQQ."""
    rows = []
    for y in years:
        w = {s: window(p, y) for s, p in px.items()}
        if w.get("QQQ") and w.get("IWM") and w.get("SPY"):
            rows.append({"year": y, **{s: (x["r"] if x else None) for s, x in w.items()}, "spread": w["IWM"]["r"] - w["QQQ"]["r"]})
    out = {"rows": rows}
    for s in list(px) + ["spread"]:
        v = [r[s] for r in rows if r.get(s) is not None]
        if v:
            sv = sorted(v)
            out[s] = {"n": len(v), "mean": statistics.mean(v), "median": statistics.median(v), "up": sum(x > 0 for x in v) / len(v),
                      "trim": statistics.mean(sv[:-3]) if len(sv) > 3 else None}
    return out


def losers(px: dict[str, dict[str, float]], spy: dict[str, float], years: list[int]) -> dict:
    """Stocks 20%+ below their one-year high on Oct 29 against those within 10% of it: Nov, Dec, the last
    half of Dec, and the whole window, each minus SPY."""
    legs = {"11 月（10-29 到 11-30）": (START_MD, MID_MD), "12 月（11-30 到 12-31）": (MID_MD, END_MD),
            "12 月后半（12-15 到 12-31）": ("12-15", END_MD), "整段（10-29 到 12-31）": (START_MD, END_MD)}
    out: dict[str, dict[str, list[float]]] = {"跌了 20% 以上的": {k: [] for k in legs}, "离高点 10% 以内的": {k: [] for k in legs}}
    count = {"跌了 20% 以上的": 0, "离高点 10% 以内的": 0}
    for s, p in px.items():
        days = sorted(p)
        for y in years:
            i0 = on_or_before(days, f"{y}-{START_MD}")
            if i0 is None or i0 < 252 or days[i0][:4] != str(y):
                continue
            off = p[days[i0]] / max(p[d] for d in days[i0 - 251:i0 + 1]) - 1
            grp = "跌了 20% 以上的" if off <= LOSER else ("离高点 10% 以内的" if off >= WINNER else None)
            if not grp:
                continue
            count[grp] += 1
            for name, (a, b) in legs.items():
                w, m = window(p, y, a, b), window(spy, y, a, b)
                if w and m:
                    out[grp][name].append(w["r"] - m["r"])
    res = {}
    for grp, legs_ in out.items():
        res[grp] = {"n": count[grp]}
        for name, v in legs_.items():
            if v:
                res[grp][name] = {"n": len(v), "mean": statistics.mean(v), "median": statistics.median(v), "up": sum(x > 0 for x in v) / len(v)}
    return res


def our_rule(closes: dict[str, list[float]], dates: list[str], cfg: dict) -> list[dict]:
    """The momentum rule's Oct 29 -> Dec 31 return each year it has history for, next to SPY."""
    p, regime, max_pos = cfg["stocks"], cfg["regime_symbol"], cfg["risk"]["max_position_pct"]
    start = 200
    sim = simulate(closes, dates, p, regime, max_pos, start, {}, "hold")
    rets = dict(sim["rets"])
    out = []
    for y in sorted({d[:4] for d in rets}):
        ds = [d for d in rets if f"{y}-{START_MD}" < d <= f"{y}-{END_MD}"]
        if len(ds) < 30:
            continue
        eq = 1.0
        for d in ds:
            eq *= 1 + rets[d]
        i0, i1 = dates.index(ds[0]) - 1, dates.index(ds[-1])
        out.append({"year": int(y), "rule": eq - 1, "spy": closes["SPY"][i1] / closes["SPY"][i0] - 1})
    return out


def now_block(px: dict[str, dict[str, float]], today: str) -> list[str]:
    out = ["| 标的 | 价格 | 对 50 日均线 | 对 200 日均线 | 离一年高点 | 年初至今 |", "|---|---|---|---|---|---|"]
    for s, p in px.items():
        days = [d for d in sorted(p) if d <= today]
        c = [p[d] for d in days]
        prev = on_or_before(days, f"{int(today[:4]) - 1}-12-31")
        out.append(f"| {s} | {c[-1]:.2f} | {pct(c[-1] / statistics.mean(c[-50:]) - 1)} | {pct(c[-1] / statistics.mean(c[-200:]) - 1)} | "
                   f"{pct(c[-1] / max(c[-252:]) - 1)} | {pct(c[-1] / c[prev] - 1) if prev is not None else '—'} |")
    return out


def upcoming(symbols: set[str], today: str) -> list[str]:
    """Reports after today for our symbols, from the latest calendar archive."""
    files = sorted((ROOT / "data" / "earnings").glob("*.json"))
    if not files:
        return []
    reps = json.loads(files[-1].read_text()).get("reports") or {}
    rows = sorted((r["date"], s, r.get("time") or "") for s, rs in reps.items() if s in symbols for r in rs if r.get("date", "") >= today)
    return [f"{d} {s}（{'盘前' if t == 'pre-market' else '盘后' if t == 'post-market' else '时间未知'}）" for d, s, t in rows]


def markdown(rep: dict) -> str:
    now, g = rep["now"], rep["groups"]
    o = [f"# 从现在到年底（{rep['generated']}）", "",
         f"标普 500 从每年 10-29 前最后一个收盘到 12-31 收盘，{FIRST_YEAR}–{rep['last_year']} 年共 {len(rep['rows'])} 年。"
         f"今年的条件：{'中期选举年' if now['midterm'] else '不是中期选举年'}；13 周国债利率半年变化 {now['rates']:+.2f} 点"
         f"（{'算升' if now['rates_up'] else '不算升'}）；年初至今 {pct(now['ytd'])}；离一年高点 {pct(now['off_high'])}；"
         f"{'在' if now['above200'] else '不在'} 200 日均线上方。只研究，不改交易。", "",
         "## 标普 500 这段的历史", "", *table(g), "",
         "「窗口里平均最低」是从起点算最深跌到哪；「中途回撤」是窗口里任一高点到之后低点的最大跌幅。robust 的年份提醒按期权的 5% 门槛定，股指两个月的平均在 3% 以内，所以各年代要自己看下表。", "",
         "按年代：", "", "| 年代 | 年数 | 涨的比例 | 平均 | 中位数 | 最差 |", "|---|---|---|---|---|---|"]
    for dec, s in rep["decades"].items():
        o.append(f"| {dec} | {s['n']} | {s['up']:.0%} | {pct(s['mean'])} | {pct(s['median'])} | {pct(s['worst'])} |")
    o += ["", "## 窗口分两半", "", f"| 段 | 全部年份：平均 / 中位数 / 涨的比例 | {rep['like_group']}：平均 / 中位数 / 涨的比例 |", "|---|---|---|"]
    for k in rep["halves_all"]:
        a, b = rep["halves_all"][k], rep["halves_like"].get(k)
        o.append(f"| {k} | {pct(a['mean'])} / {pct(a['median'])} / {a['up']:.0%}（{a['n']}） | "
                 + (f"{pct(b['mean'])} / {pct(b['median'])} / {b['up']:.0%}（{b['n']}） |" if b else "— |"))
    rot = rep["rotation"]
    o += ["", f"## 轮动：2000 年起同一段，纳指 100 对小盘（{rot['QQQ']['n']} 年）", "",
          "| 标的 | 平均 | 中位数 | 去掉最好 3 年 | 涨的比例 |", "|---|---|---|---|---|"]
    for s in ("SPY", "QQQ", "IWM", "RSP", "SMH"):
        if s in rot:
            x = rot[s]
            o.append(f"| {s}{'（2003 起）' if s == 'RSP' else ''} | {pct(x['mean'])} | {pct(x['median'])} | {pct(x['trim'])} | {x['up']:.0%} |")
    x = rot["spread"]
    o.append(f"| IWM 减 QQQ | {pct(x['mean'])} | {pct(x['median'])} | {pct(x['trim'])} | 小盘赢 {x['up']:.0%} |")
    o += ["", "逐年 IWM 减 QQQ：" + "、".join(f"{r['year']} {pct(r['spread'], 0)}" for r in rot["rows"]), ""]
    lo = rep["losers"]
    o += [f"## 年底卖亏损股：2010 年起 {rep['loser_symbols']} 只大公司，10-29 时离一年高点的位置（减去 SPY 同期）", "",
          "| 组 | 股票·年 | 11 月 | 12 月 | 12 月后半 | 整段 |", "|---|---|---|---|---|---|"]
    for grp, legs in lo.items():
        cells = [f"{pct(v['mean'])} / 中位 {pct(v['median'])} / {v['up']:.0%} 赢" for k, v in legs.items() if k != "n"]
        o.append(f"| {grp} | {legs['n']} | " + " | ".join(cells) + " |")
    o += ["", "都是今天还在的大公司：跌了还活着的才在名单里，反弹被高估。", ""]
    o += ["## 我们的动量规则在这段的记录（hero.research_stock_guard 的回放，不算止损和期权）", "", "| 年 | 规则 | SPY | 差 |", "|---|---|---|---|"]
    for r in rep["our_rule"]:
        o.append(f"| {r['year']} | {pct(r['rule'])} | {pct(r['spy'])} | {pct(r['rule'] - r['spy'])} |")
    o += ["", f"## 现在的位置（{rep['asof']} 收盘）", "", *now_block_rows(rep), ""]
    if rep["upcoming"]:
        o += ["股票池里还没发的财报：" + "、".join(rep["upcoming"]), ""]
    o += ["日历：11-03 中期选举、11-06 和 12-04 非农、CPI 约 11 月中和 12 月中、12-08/09 FOMC、12-18 四巫日、12-31 收官。", ""]
    return "\n".join(o) + "\n"


def now_block_rows(rep: dict) -> list[str]:
    return rep["now_table"]


def main() -> None:
    today = date.today().isoformat()
    cfg = json.loads(CONFIG.read_text())
    spx = {d: c for d, c in yahoo("^GSPC", "1949-06-01").items() if d < today}
    irx = {d: c for d, c in yahoo("^IRX", "1959-06-01").items() if d < today}
    this_year = int(today[:4])
    years = [y for y in range(FIRST_YEAR, this_year) if window(spx, y)]
    rows = []
    for y in years:
        w = window(spx, y)
        rows.append({**w, **conditions(spx, irx, y)})
    now = conditions(spx, irx, this_year)
    g = groups(rows, now)
    like_name = next((k for k in ("中期选举年且利率在升", "中期选举年", ytd_band(now["ytd"])) if k in g), "全部年份")
    like = g[like_name]
    decades = {}
    for dec in range(1950, this_year, 10):
        rs = [r for r in rows if dec <= r["year"] < dec + 10]
        if rs:
            s = stats(rs)
            decades[f"{dec}s"] = {k: s[k] for k in ("n", "up", "mean", "median", "worst")}
    etfs = {s: {d: c for d, c in yahoo(s, "1998-01-01").items() if d < today} for s in ("SPY", "QQQ", "IWM", "RSP", "SMH")}
    rot = rotation(etfs, [y for y in years if y >= 2000])
    syms = sorted({e["symbol"] for e in json.loads(EVENTS.read_text())["events"]})
    px = {}
    for s in syms:
        try:
            px[s] = {d: c for d, c in yahoo(s, "2009-01-01").items() if d < today}
        except Exception:  # noqa: BLE001 - a name Yahoo no longer serves is dropped
            pass
    lo = losers(px, etfs["SPY"], [y for y in years if y >= 2010])
    uni = {s: {d: c for d, c in yahoo(s, "2020-06-01").items() if d < today} for s in cfg["universe"]}
    bars = {s: [{"t": d, "c": c} for d, c in sorted(m.items())] for s, m in uni.items()}
    dates, closes = backtest.align(bars, cfg["regime_symbol"])
    ours = our_rule(closes, dates, cfg)
    vix = {d: c for d, c in yahoo("^VIX", "2024-01-01").items() if d < today}
    rep = {"generated": today, "asof": max(etfs["SPY"]), "last_year": years[-1], "rows": rows, "now": now, "groups": {k: stats(v) for k, v in g.items()},
           "decades": decades, "halves_all": halves(spx, years), "halves_like": halves(spx, [r["year"] for r in like]),
           "like_group": like_name, "rotation": rot, "losers": lo, "loser_symbols": len(px),
           "our_rule": ours, "upcoming": upcoming(set(cfg["universe"]), today),
           "now_table": now_block({**{s: etfs[s] for s in ("SPY", "QQQ", "IWM", "RSP", "SMH")}, "VIX": vix}, today)}
    md = markdown({**rep, "groups": g})
    (ROOT / "research" / f"{today}-yearend.json").write_text(json.dumps({**rep, "rows": rows}, ensure_ascii=False, default=str) + "\n")
    (ROOT / "research" / f"{today}-yearend.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
