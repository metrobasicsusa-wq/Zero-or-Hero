"""CPI week and the monthly options expiry (owner, 10-16: "这居然是有历史依据的吗").

Since 2024, when a CPI release fell in the same Monday-Friday week as the monthly expiry (the third Friday; the
Thursday when that Friday is a holiday), SPY closed down on the expiry day 8 times in 13 and was below its open at
10:00 9 times in 12, against about half on other days. Thirteen events is small and the pattern was found by
looking, so here it is checked on the years before it was noticed: CPI release dates from the BLS archive of CPI
news releases (2010-01 .. 2024-01; hero.research_eventday after that), SPY and QQQ daily bars from Yahoo
(split-adjusted, not dividend-adjusted; dividends land on expiry Fridays four times a year, the same for both
groups, and the ex-dividend drop is taken out of the close-to-close move below). Daily bars only: the 10:00 side
needs minutes, which go back two years. Record only. Run: python -m hero.research_cpi_opex
"""

from __future__ import annotations

import json
import math
import statistics
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone

from hero.research_eventday import CPI
from hero.research_ideas import save

# BLS archive of CPI news releases (www.bls.gov/bls/news-release/cpi.htm), release dates read off the file names.
CPI_BLS = ["2010-01-15", "2010-02-19", "2010-03-18", "2010-04-14", "2010-05-19", "2010-06-17", "2010-07-16",
           "2010-08-13", "2010-09-17", "2010-10-15", "2010-11-17", "2010-12-15", "2011-01-14", "2011-02-17",
           "2011-03-17", "2011-04-15", "2011-05-13", "2011-06-15", "2011-07-15", "2011-08-18", "2011-09-15",
           "2011-10-19", "2011-11-16", "2011-12-16", "2012-01-19", "2012-02-17", "2012-03-16", "2012-04-13",
           "2012-05-15", "2012-06-14", "2012-07-17", "2012-08-15", "2012-09-14", "2012-10-16", "2012-11-15",
           "2012-12-14", "2013-01-16", "2013-02-21", "2013-03-15", "2013-04-16", "2013-05-16", "2013-06-18",
           "2013-07-16", "2013-08-15", "2013-09-17", "2013-10-30", "2013-11-20", "2013-12-17", "2014-01-16",
           "2014-02-20", "2014-03-18", "2014-04-15", "2014-05-15", "2014-06-17", "2014-07-22", "2014-08-19",
           "2014-09-17", "2014-10-22", "2014-11-20", "2014-12-17", "2015-01-16", "2015-02-26", "2015-03-24",
           "2015-04-17", "2015-05-22", "2015-06-18", "2015-07-17", "2015-08-19", "2015-09-16", "2015-10-15",
           "2015-11-17", "2015-12-15", "2016-01-20", "2016-02-19", "2016-03-16", "2016-04-14", "2016-05-17",
           "2016-06-16", "2016-07-15", "2016-08-16", "2016-09-16", "2016-10-18", "2016-11-17", "2016-12-15",
           "2017-01-18", "2017-02-15", "2017-03-15", "2017-04-14", "2017-05-12", "2017-06-14", "2017-07-14",
           "2017-08-11", "2017-09-14", "2017-10-13", "2017-11-15", "2017-12-13", "2018-01-12", "2018-02-14",
           "2018-03-13", "2018-04-11", "2018-05-10", "2018-06-12", "2018-07-12", "2018-08-10", "2018-09-13",
           "2018-10-11", "2018-11-14", "2018-12-12", "2019-01-11", "2019-02-13", "2019-03-12", "2019-04-10",
           "2019-05-10", "2019-06-12", "2019-07-11", "2019-08-13", "2019-09-12", "2019-10-10", "2019-11-13",
           "2019-12-11", "2020-01-14", "2020-02-13", "2020-03-11", "2020-04-10", "2020-05-12", "2020-06-10",
           "2020-07-14", "2020-08-12", "2020-09-11", "2020-10-13", "2020-11-12", "2020-12-10", "2021-01-13",
           "2021-02-10", "2021-03-10", "2021-04-13", "2021-05-12", "2021-06-10", "2021-07-13", "2021-08-11",
           "2021-09-14", "2021-10-13", "2021-11-10", "2021-12-10", "2022-01-12", "2022-02-10", "2022-03-10",
           "2022-04-12", "2022-05-11", "2022-06-10", "2022-07-13", "2022-08-10", "2022-09-13", "2022-10-13",
           "2022-11-10", "2022-12-13", "2023-01-12", "2023-02-14", "2023-03-14", "2023-04-12", "2023-05-10",
           "2023-06-13", "2023-07-12", "2023-08-10", "2023-09-13", "2023-10-12", "2023-11-14", "2023-12-12",
           "2024-01-11"]
ALL_CPI = sorted(set(CPI_BLS) | set(CPI))
START, FOUND = "2010-01-01", "2024-01-01"   # FOUND: the two years the pattern was seen in
NAMES = ("SPY", "QQQ")


def yahoo_daily(sym: str, start: str) -> dict[str, dict]:
    """day -> {o, c, div}: Yahoo's daily bars (split-adjusted) with the dividend paid that day, if any."""
    p1 = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp())
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1={p1}&period2={int(datetime.now().timestamp())}"
           "&interval=1d&events=div")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    res = json.load(urllib.request.urlopen(req, timeout=30))["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    day = lambda t: (datetime.fromtimestamp(t, timezone.utc) - timedelta(hours=5)).date().isoformat()
    divs = {day(v["date"]): float(v["amount"]) for v in ((res.get("events") or {}).get("dividends") or {}).values()}
    return {day(t): {"o": o, "c": c, "div": divs.get(day(t), 0.0)}
            for t, o, c in zip(res["timestamp"], q["open"], q["close"]) if o and c}


def third_friday(y: int, m: int) -> date:
    d = date(y, m, 15)
    return d + timedelta(days=(4 - d.weekday()) % 7)


def expiries(days: list[str]) -> list[str]:
    """The monthly expiry in each month the bars cover: the third Friday, or the trading day before it in that week
    when the Friday is a holiday (Good Friday, Juneteenth)."""
    have, out = set(days), []
    first, last = date.fromisoformat(days[0]), date.fromisoformat(days[-1])
    y, m = first.year, first.month
    while (y, m) <= (last.year, last.month):
        f = d = third_friday(y, m)
        while f <= last and d.isoformat() not in have and (f - d).days < 4:   # a Friday past the data has not come
            d -= timedelta(days=1)
        if f <= last and d.isoformat() in have:
            out.append(d.isoformat())
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def cpi_in_week(day: str, cpi: list[str]) -> str | None:
    """The CPI release in the Monday-Friday week of `day`, if any."""
    d = date.fromisoformat(day)
    mon, fri = d - timedelta(days=d.weekday()), d + timedelta(days=4 - d.weekday())
    return next((c for c in cpi if mon.isoformat() <= c <= fri.isoformat()), None)


def rows_for(bars: dict[str, dict], cpi: list[str], today: str) -> list[dict]:
    days = sorted(d for d in bars if d < today)
    ex = set(expiries(days))
    out = []
    for i in range(6, len(days)):
        d, b, prev = days[i], bars[days[i]], bars[days[i - 1]]
        wk = date.fromisoformat(d).weekday()
        c = cpi_in_week(d, cpi)
        start = next(days[k] for k in range(i - 1, -1, -1) if date.fromisoformat(days[k]) < date.fromisoformat(d) -
                     timedelta(days=wk))   # the last close before this week
        out.append({"day": d, "opex": d in ex, "friday": wk == 4, "cpi": c, "cpi_wd": date.fromisoformat(c).weekday() if c else None,
                    "cc": (b["c"] + b["div"]) / prev["c"] - 1, "oc": b["c"] / b["o"] - 1,
                    "week": (b["c"] + b["div"]) / bars[start]["c"] - 1})
    return out


def binom_tail(k: int, n: int, p: float) -> float:
    """P(X >= k) for X ~ Binomial(n, p)."""
    if p <= 0 or p >= 1:
        return float(k <= 0) if p <= 0 else 1.0
    term = lambda j: math.exp(math.lgamma(n + 1) - math.lgamma(j + 1) - math.lgamma(n - j + 1) + j * math.log(p) +
                              (n - j) * math.log(1 - p))
    return min(1.0, sum(term(j) for j in range(k, n + 1)))


def summary(rs: list[dict], base: float | None = None) -> dict:
    if not rs:
        return {"n": 0}
    down = sum(r["cc"] < 0 for r in rs)
    s = {"n": len(rs), "down": down, "down_share": down / len(rs), "mean": statistics.mean(r["cc"] for r in rs),
         "median": statistics.median(r["cc"] for r in rs), "oc_down": sum(r["oc"] < 0 for r in rs) / len(rs),
         "oc_mean": statistics.mean(r["oc"] for r in rs), "week": statistics.mean(r["week"] for r in rs)}
    if base is not None:
        s["p"] = binom_tail(down, len(rs), base)
    return s


def groups(rows: list[dict]) -> dict[str, list[dict]]:
    ox = [r for r in rows if r["opex"]]
    g = {"CPI 周的到期日：全部": [r for r in ox if r["cpi"]],
         "　2010–2023（发现之前，检验用）": [r for r in ox if r["cpi"] and r["day"] < FOUND],
         "　2024–2026（发现规律的那段）": [r for r in ox if r["cpi"] and r["day"] >= FOUND],
         "　2010–2015": [r for r in ox if r["cpi"] and r["day"] < "2016"],
         "　2016–2021": [r for r in ox if r["cpi"] and "2016" <= r["day"] < "2022"],
         "　2022–2023（加息）": [r for r in ox if r["cpi"] and "2022" <= r["day"] < FOUND],
         "　2022–2026（通胀是焦点以来）": [r for r in ox if r["cpi"] and r["day"] >= "2022"],
         "　CPI 在周一到周三": [r for r in ox if r["cpi"] and r["cpi_wd"] <= 2],
         "　CPI 在周四或到期当天": [r for r in ox if r["cpi"] and r["cpi_wd"] >= 3],
         "对照：其他到期日": [r for r in ox if not r["cpi"]],
         "　其他到期日 2010–2023": [r for r in ox if not r["cpi"] and r["day"] < FOUND],
         "对照：不是到期日的周五": [r for r in rows if r["friday"] and not r["opex"]],
         "　其中 CPI 那周的周五": [r for r in rows if r["friday"] and not r["opex"] and r["cpi"]],
         "对照：所有交易日": rows}
    return g


def table(name: str, gs: dict[str, list[dict]]) -> list[str]:
    base = summary(gs["对照：其他到期日"])["down_share"]
    tested = list(gs)[:list(gs).index("对照：其他到期日")]
    out = [f"### {name}", "", "| 情况 | 次数 | 收跌（对昨收） | 比其他到期日更常跌的概率 p | 平均 | 中位数 | 开盘到收盘跌 | 开盘到收盘平均 | 整周平均 |",
           "|---|---|---|---|---|---|---|---|---|"]
    for g, rs in gs.items():
        s = summary(rs, base if g in tested else None)
        if not s["n"]:
            continue
        p = f"{s['p']:.2f}" if "p" in s else "—"
        out.append(f"| {g} | {s['n']} | {s['down']}（{s['down_share']:.0%}） | {p} | {s['mean']:+.2%} | {s['median']:+.2%} | "
                   f"{s['oc_down']:.0%} | {s['oc_mean']:+.2%} | {s['week']:+.2%} |")
    return out + [""]


def main() -> None:
    today = date.today().isoformat()
    rep, md = {"generated": today, "cpi_dates": len(ALL_CPI), "names": {}}, []
    for und in NAMES:
        rows = rows_for(yahoo_daily(und, START), ALL_CPI, today)
        gs = groups(rows)
        base, tested = summary(gs["对照：其他到期日"])["down_share"], list(gs)[:list(gs).index("对照：其他到期日")]
        rep["names"][und] = {g: summary(rs, base if g in tested else None) for g, rs in gs.items()}
        rep["names"][und]["events"] = [{k: r[k] for k in ("day", "cpi", "cc", "oc", "week")} for r in gs["CPI 周的到期日：全部"]]
        md += table(und, gs)
    spy = rep["names"]["SPY"]
    old, new = spy["　2010–2023（发现之前，检验用）"], spy["　2024–2026（发现规律的那段）"]
    infl = spy["　2022–2026（通胀是焦点以来）"]
    head = [f"# CPI 那一周的月度期权到期日 {today}", "",
            f"{START[:4]} 至 {today}：CPI 公布日 {len(ALL_CPI)} 个（2024-01 以前取自 BLS 的 CPI 新闻稿存档，之后用 hero/research_eventday 的日期表），"
            "SPY / QQQ 日线（Yahoo，拆股调整；分红日把分红加回收盘）。到期日 = 每月第三个周五，那天休市就用同一周的前一个交易日。"
            "「CPI 周」= CPI 在到期日那一周的周一到周五（含到期当天）。p 值：假设跌的概率和其他到期日一样，至少跌这么多次的概率（单边）。"
            "只有日线，所以测不了 10:00 那一条。只研究，不改交易。", "",
            f"**结论（SPY）**：2024–2026 这段 {new['n']} 次里收跌 {new['down']} 次（{new['down_share']:.0%}），平均 {new['mean']:+.2%}；"
            f"用发现之前的 2010–2023 检验：{old['n']} 次收跌 {old['down']} 次（{old['down_share']:.0%}），平均 {old['mean']:+.2%}，"
            f"其他到期日跌的比例 {spy['对照：其他到期日']['down_share']:.0%}（p = {old['p']:.2f}）——长历史里不成立。"
            f"只在 2022 年以来成立：{infl['n']} 次收跌 {infl['down']} 次（{infl['down_share']:.0%}，p = {infl['p']:.2f}），"
            "但这一段是看了结果才挑出来的，2016–2021 和 2010–2015 都是反的。", ""]
    ev = rep["names"]["SPY"]["events"]
    lines = ["CPI 周到期日明细（SPY，对昨收 / 开盘到收盘 / 整周）：", ""] + [
        f"- {e['day']}（CPI {e['cpi'][5:]}）{e['cc']:+.2%} / {e['oc']:+.2%} / {e['week']:+.2%}" for e in ev] + [""]
    save("cpi-opex", rep, "\n".join(head + md + lines))


if __name__ == "__main__":
    sys.exit(main())
