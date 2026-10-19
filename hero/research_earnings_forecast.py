"""Earnings forecasts: before each report, which way and how far -- then scored once the reaction day has closed.

Owner, 10-19: forecast every US company's earnings (direction and size), record and score them first; nothing trades
on them. Run each trading day around 15:10 ET (python -m hero.research_earnings_forecast; research.yml earnings_forecast):
  1. score every earlier forecast whose reaction day has closed: the gap (open / prior close) and the move (close /
     prior close), whether the move beat the options' implied move, and whether the direction call was right;
  2. forecast tonight's after-close and tomorrow's before-open reports from the Alpha Vantage calendar (every US
     listing in it), keeping the ones with a price of $5+ and listed options: the implied move is the at-the-money
     straddle of the first expiry on or after the reaction day (mid of the latest quotes) over the price.
The baseline every name gets comes from research/2026-10-02-earnings-reactions.json (1,022 reports with option
prices, 2024-02..2026-09): the close moved more than the implied move 40% of the time, 51% when the stock's own
average reaction had run 25%+ above today's implied move; the direction was a coin toss (51% up; no momentum or
timing split moved it more than a few points). So the baseline says "up" / "down" only as a placeholder at 50%;
direction calls come from the judgment file (YYYY-MM-DD.judgment.json, written by Claude for the names worth a
closer look) and are scored against the same results. Record only.
"""

from __future__ import annotations

import json
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from hero import earnings as earn
from hero.research_ideas import ROOT

OUT = ROOT / "research" / "earnings-forecast"
HIST = ROOT / "research" / "2026-10-02-earnings-reactions.json"
MIN_PRICE = 5.0
P_BIG, P_BIG_HOT, HOT = 0.40, 0.51, 1.25   # P(|move| > implied): base; when own average reaction / implied >= HOT
WINDOW = 45          # days past the reaction day to look for an expiry
WEEKLY = 7           # an expiry this close to the reaction day prices mostly the report itself


def business_after(d: date) -> date:
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def due(rows: list[dict], today: date) -> list[dict]:
    """Calendar rows reporting after today's close or before tomorrow's open, with their reaction day."""
    nxt = business_after(today)
    out = []
    for r in rows:
        when = (r.get("timeOfTheDay") or "").strip()
        if r.get("reportDate") == today.isoformat() and when == "post-market":
            out.append({"symbol": r["symbol"], "report_date": r["reportDate"], "time": when, "reaction_day": nxt.isoformat(),
                        "estimate": r.get("estimate") or None})
        elif r.get("reportDate") == nxt.isoformat() and when == "pre-market":
            out.append({"symbol": r["symbol"], "report_date": r["reportDate"], "time": when, "reaction_day": nxt.isoformat(),
                        "estimate": r.get("estimate") or None})
    return out


def history() -> dict[str, list[float]]:
    """symbol -> its past absolute reaction-day moves."""
    try:
        ev = json.loads(HIST.read_text())["events"]
    except Exception:
        return {}
    out: dict[str, list[float]] = {}
    for e in ev:
        out.setdefault(e["symbol"], []).append(abs(e["move"]))
    return out


def baseline(implied: float | None, past: list[float]) -> dict:
    prior = statistics.mean(past[-8:]) if past else None
    hot = bool(prior and implied and prior / implied >= HOT)
    return {"prior_avg_abs": round(prior, 4) if prior else None, "prior_n": len(past),
            "p_big": P_BIG_HOT if hot else P_BIG, "p_up": 0.5}


def mid(snap: dict) -> float | None:
    q = (snap or {}).get("latestQuote") or {}
    bid, ask = float(q.get("bp") or 0), float(q.get("ap") or 0)
    return round((bid + ask) / 2, 4) if bid > 0 and ask > 0 else None


def legs(client, und: str, spot: float, react: str) -> dict | None:
    """The at-the-money call and put of the first expiry on or after the reaction day (up to WINDOW days out).

    Names with only monthly options get that month's straddle: it also prices the ordinary days before expiry,
    so `days` says how far past the reaction day it runs and the scorecard keeps those apart."""
    try:
        cs = client.option_contracts(und, status="active", expiration_date_gte=react,
                                     expiration_date_lte=(date.fromisoformat(react) + timedelta(days=WINDOW)).isoformat(),
                                     strike_price_gte=f"{spot * 0.9:.2f}", strike_price_lte=f"{spot * 1.1:.2f}")
    except Exception:
        return {"error": True}
    if not cs:
        return None
    exp = min(c["expiration_date"] for c in cs)
    cs = [c for c in cs if c["expiration_date"] == exp]
    k = min({float(c["strike_price"]) for c in cs}, key=lambda x: abs(x - spot))
    got = {c["type"]: c["symbol"] for c in cs if float(c["strike_price"]) == k}
    days = (date.fromisoformat(exp) - date.fromisoformat(react)).days
    return {"expiry": exp, "strike": k, "days": days, **got} if set(got) >= {"call", "put"} else None


def forecast(client, rows: list[dict], today: date, wait=lambda: None) -> dict:
    reports = due(rows, today)
    days = {today.isoformat(), business_after(today).isoformat()}
    no_time = sorted({r["symbol"] for r in rows if r.get("reportDate") in days
                      and (r.get("timeOfTheDay") or "").strip() not in ("pre-market", "post-market")})
    syms = sorted({r["symbol"] for r in reports})
    snaps: dict[str, dict] = {}
    for i in range(0, len(syms), 100):
        try:
            snaps.update(client.stock_snapshots(syms[i:i + 100]))
        except Exception:
            pass
    past = history()
    out, skipped = [], {"no_price": 0, "under_5": 0, "no_options": 0, "error": 0}
    skipped_symbols: dict[str, list[str]] = {k: [] for k in skipped}
    pending = []
    for r in reports:
        s = snaps.get(r["symbol"]) or {}
        spot = float(((s.get("latestTrade") or {}).get("p")) or ((s.get("dailyBar") or {}).get("c")) or 0)
        if not spot:
            skipped["no_price"] += 1
            skipped_symbols["no_price"].append(r["symbol"])
            continue
        if spot < MIN_PRICE:
            skipped["under_5"] += 1
            skipped_symbols["under_5"].append(r["symbol"])
            continue
        wait()
        lg = legs(client, r["symbol"], spot, r["reaction_day"])
        if not lg or lg.get("error"):
            why = "error" if lg else "no_options"
            skipped[why] += 1
            skipped_symbols[why].append(r["symbol"])
            continue
        prev = s.get("prevDailyBar") or {}
        pending.append(({**r, "dollar_volume": round(float(prev.get("v") or 0) * float(prev.get("c") or 0))}, spot, lg))
    quotes = {}
    want = [lg[k] for _, _, lg in pending for k in ("call", "put")]
    try:
        quotes = client.option_snapshots(want) if want else {}
    except Exception:
        quotes = {}
    for r, spot, lg in pending:
        c, p = mid(quotes.get(lg["call"])), mid(quotes.get(lg["put"]))
        implied = round((c + p) / spot, 4) if c and p else None
        out.append({**r, "spot": round(spot, 2), "expiry": lg["expiry"], "strike": lg["strike"],
                    "expiry_days": lg["days"], "weekly": lg["days"] <= WEEKLY, "call_mid": c, "put_mid": p,
                    "implied_move": implied, **baseline(implied, past.get(r["symbol"], []))})
    out.sort(key=lambda x: -x["dollar_volume"])   # the most traded first: the judgment file covers the top of the list
    return {"date": today.isoformat(), "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "calendar_rows_due": len(reports), "calendar_no_time": no_time, "skipped": skipped,
            "skipped_symbols": {k: v for k, v in skipped_symbols.items() if v}, "reports": out}


def score_row(row: dict, bars: list[dict]) -> dict | None:
    """The reaction day against the session before it."""
    days = {b["t"][:10]: b for b in bars}
    order = sorted(days)
    if row["reaction_day"] not in days:
        return None
    i = order.index(row["reaction_day"])
    if i == 0:
        return None
    prev, b = float(days[order[i - 1]]["c"]), days[row["reaction_day"]]
    gap, move = float(b["o"]) / prev - 1, float(b["c"]) / prev - 1
    imp = row.get("implied_move")
    return {"gap": round(gap, 4), "move": round(move, 4), "big": (abs(move) > imp) if imp else None, "up": move > 0}


def score_due(client, today: date, root: Path = OUT) -> list[str]:
    """Fill in results for every forecast file whose reaction days have closed; returns the files changed."""
    changed = []
    for f in sorted(root.glob("20??-??-??.json")):
        rep = json.loads(f.read_text())
        open_rows = [r for r in rep.get("reports", []) if "result" not in r and r["reaction_day"] < today.isoformat()]
        if not open_rows:
            continue
        start = (date.fromisoformat(min(r["reaction_day"] for r in open_rows)) - timedelta(days=7)).isoformat()
        syms = sorted({r["symbol"] for r in open_rows})
        bars: dict[str, list[dict]] = {}
        for i in range(0, len(syms), 100):
            try:
                bars.update(client.daily_bars(syms[i:i + 100], start, adjustment="raw", feed="sip"))
            except Exception:
                pass
        for r in open_rows:
            res = score_row(r, bars.get(r["symbol"], []))
            if res:
                r["result"] = res
        f.write_text(json.dumps(rep, ensure_ascii=False) + "\n")
        changed.append(f.name)
    return changed


def judged(root: Path = OUT) -> dict[tuple[str, str], dict]:
    """(forecast date, symbol) -> Claude's judgment for it, from YYYY-MM-DD.judgment.json."""
    out = {}
    for f in sorted(root.glob("20??-??-??.judgment.json")):
        d = json.loads(f.read_text())
        for j in d.get("calls") or []:
            out[(d.get("date") or f.name[:10], j["symbol"])] = j
    return out


def scorecard(root: Path = OUT) -> str:
    rows, js = [], judged(root)
    for f in sorted(root.glob("20??-??-??.json")):
        rep = json.loads(f.read_text())
        for r in rep.get("reports", []):
            if "result" in r:
                rows.append({**r, "day": rep["date"], "judgment": js.get((rep["date"], r["symbol"]))})
    md = [f"# 财报预测记分卡 {date.today().isoformat()}", "",
          "每个交易日 15:10 ET 前后，对当晚盘后、次日盘前发财报的美股（$5 以上、有期权）记下期权隐含的涨跌幅（平值跨式 / 股价），"
          "基线给每只「超过隐含幅度」的概率（历史 40%；自身过去平均反应比隐含高 25% 以上的 51%）；方向的判断只在判断文件里。"
          "反应日收盘后按收盘对前收记分。只记录，不交易。", ""]
    if not rows:
        return "\n".join(md + ["还没有可记分的财报。", ""])
    allbig = [r for r in rows if r["result"]["big"] is not None]
    big = [r for r in allbig if r.get("weekly", True)]   # files from before 10-19 noon had only weekly expiries
    md += ["## 基线：涨跌幅有没有超过期权定价", "", "| 组 | 次数 | 实际超过隐含 | 基线预计 |", "|---|---|---|---|"]
    for name, rs in (("一周内到期的全部", big), ("自身平均反应偏大（基线 51%）", [r for r in big if r["p_big"] >= P_BIG_HOT]),
                     ("其他（基线 40%）", [r for r in big if r["p_big"] < P_BIG_HOT]),
                     ("月度到期（隐含偏大，只作参考）", [r for r in allbig if r.get("weekly") is False])):
        if rs:
            md.append(f"| {name} | {len(rs)} | {sum(r['result']['big'] for r in rs) / len(rs):.0%} | "
                      f"{statistics.mean(r['p_big'] for r in rs):.0%} |")
    jr = [r for r in rows if r["judgment"]]
    md += ["", "## 判断（Claude 挑出来细看的）", ""]
    if not jr:
        md.append("还没有带判断、而且已经记分的财报。")
    else:
        dirs = [r for r in jr if r["judgment"].get("direction") in ("up", "down")]
        bigs = [r for r in jr if r["judgment"].get("big") in (True, False) and r["result"]["big"] is not None]
        md += ["| 项目 | 次数 | 对的比例 |", "|---|---|---|"]
        if dirs:
            md.append(f"| 方向（涨 / 跌） | {len(dirs)} | "
                      f"{sum((r['judgment']['direction'] == 'up') == r['result']['up'] for r in dirs) / len(dirs):.0%} |")
        if bigs:
            md.append(f"| 幅度（会不会超过隐含） | {len(bigs)} | "
                      f"{sum(r['judgment']['big'] == r['result']['big'] for r in bigs) / len(bigs):.0%} |")
        hi = [r for r in dirs if float(r["judgment"].get("confidence") or 0) >= 0.6]
        if hi:
            md.append(f"| 方向，信心 0.6 以上 | {len(hi)} | "
                      f"{sum((r['judgment']['direction'] == 'up') == r['result']['up'] for r in hi) / len(hi):.0%} |")
    md += ["", "## 明细（最近 40 条）", "", "| 预测日 | 股票 | 时间 | 隐含 | 跳空 | 收盘 | 超过隐含 | 判断 | 判断对不对 |",
           "|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: (r["day"], r["symbol"]))[-40:]:
        j, res = r["judgment"], r["result"]
        jtxt = f"{'涨' if j.get('direction') == 'up' else '跌' if j.get('direction') == 'down' else '—'} / " \
               f"{'大' if j.get('big') is True else '小' if j.get('big') is False else '—'} / {float(j.get('confidence') or 0):.1f}" if j else ""
        ok = ""
        if j and j.get("direction") in ("up", "down"):
            ok = "对" if (j["direction"] == "up") == res["up"] else "错"
        md.append(f"| {r['day']} | {r['symbol']} | {'盘后' if r['time'] == 'post-market' else '盘前'} | "
                  f"{(r.get('implied_move') or 0):.1%}{'（月）' if r.get('weekly') is False else ''} | {res['gap']:+.1%} | {res['move']:+.1%} | "
                  f"{'是' if res['big'] else '否' if res['big'] is not None else '—'} | {jtxt} | {ok} |")
    return "\n".join(md) + "\n"


def markdown(rep: dict) -> str:
    md = [f"# 财报预测 {rep['date']}", "",
          f"当晚盘后、下一个交易日盘前发财报：日历里 {rep['calendar_rows_due']} 只"
          f"（另有 {len(rep.get('calendar_no_time', []))} 只今明两天发、但日历没写盘前盘后，没算进来），记下 {len(rep['reports'])} 只"
          f"（跳过：没有价格 {rep['skipped']['no_price']}、股价不到 $5 {rep['skipped']['under_5']}、"
          f"{WINDOW} 天内没有期权 {rep['skipped']['no_options']}、查期权出错 {rep['skipped'].get('error', 0)}）。"
          "隐含幅度 = 反应日当天或之后第一个到期的平值跨式 / 股价；到期离反应日超过一周的（多是只有月度期权的）标『月』，"
          "那个跨式还包含财报以外的日子，隐含偏大，记分时分开算。成交额来自 IEX（只占全市场一小部分），只用来排序。"
          "基线概率见记分卡说明；方向判断在同日的 judgment 文件里。只记录，不交易。", "",
          "| 股票 | 时间 | 反应日 | 股价 | 前一天 IEX 成交额 | 隐含幅度 | 过去平均反应 | 超过隐含的基线概率 |", "|---|---|---|---|---|---|---|---|"]
    for r in rep["reports"]:
        prior = f"{r['prior_avg_abs']:.1%}（{r['prior_n']} 次）" if r.get("prior_avg_abs") else "—"
        imp = f"{r['implied_move']:.1%}" if r.get("implied_move") else "—"
        if r.get("implied_move") and r.get("weekly") is False:
            imp += f"（月，{r['expiry']}）"
        md.append(f"| {r['symbol']} | {'盘后' if r['time'] == 'post-market' else '盘前'} | {r['reaction_day']} | "
                  f"${r['spot']:,.2f} | ${r.get('dollar_volume', 0) / 1e6:,.1f}M | {imp} | {prior} | {r['p_big']:.0%} |")
    for why, label in (("no_options", f"{WINDOW} 天内没有期权"), ("error", "查期权出错"), ("under_5", "股价不到 $5"), ("no_price", "没有价格")):
        if rep.get("skipped_symbols", {}).get(why):
            md += ["", f"{label}：{'、'.join(rep['skipped_symbols'][why])}"]
    return "\n".join(md) + "\n"


def main() -> None:
    import os
    from hero.alpaca import Alpaca
    from hero.research_event_turn import Pace
    client = Alpaca()
    today = datetime.now(ZoneInfo("America/New_York")).date()
    OUT.mkdir(parents=True, exist_ok=True)
    changed = score_due(client, today)
    rows = earn.fetch(os.environ["ALPHAVANTAGE_API_KEY"])
    rep = forecast(client, rows, today, wait=Pace(client).wait)  # the main account's key: slow while the market is open
    (OUT / f"{today.isoformat()}.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    (OUT / f"{today.isoformat()}.md").write_text(markdown(rep))
    (OUT / "scorecard.md").write_text(scorecard())
    print(markdown(rep))
    print("scored:", changed)


if __name__ == "__main__":
    sys.exit(main())
