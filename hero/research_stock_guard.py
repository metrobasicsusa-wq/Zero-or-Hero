"""Should the main account's stocks get an earnings guard too? (owner, 10-28: 「去研究」)

The options already have one (config/strategy.json options.earnings_guard: no contract that lives past a
report). The stocks have none: on 10-28 the main account held MSFT, about a tenth of equity, into its report.
This replays the stock momentum strategy with the live config/strategy.json parameters over its 20-symbol
universe, in hero.backtest's daily close-to-close model (5 bp per unit of turnover; no stops, no options),
four ways:

  hold      - today's rule: hold through reports;
  cash      - sell at the close before the reaction day, buy back at the reaction day's close if still picked
              (that one day sits in cash);
  spy       - the same, but that day's weight sits in SPY (keep the market, skip the company's own news);
  no-entry  - keep what is held, but open nothing within 5 sessions before its report (what the options
              guard does for contracts).

Reports: research/2026-10-02-earnings-reactions.json (SEC 8-K item 2.02; the acceptance time gives the
reaction day) through 10-02, then the daily calendar archives in data/earnings/ (the archive fetched on or
before the report date, so only what was known in advance). A release followed by another within 40 days
(Tesla's delivery numbers, pre-announcements) is not a scheduled report and is not guarded: no calendar would
have shown it. Prices: Yahoo daily closes adjusted for splits and dividends.

Per-report checks go through hero.robust: every report the strategy held into, as (reaction day, that day's
return per $1), and the same minus SPY's day. Research only; nothing here trades.
Run: python -m hero.research_stock_guard
"""

from __future__ import annotations

import json
import statistics
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from hero import backtest, robust
from hero.indicators import max_drawdown, sharpe
from hero.strategies import momentum

ROOT = Path(__file__).resolve().parent.parent
EVENTS = ROOT / "research" / "2026-10-02-earnings-reactions.json"
ARCHIVE = ROOT / "data" / "earnings"
CONFIG = ROOT / "config" / "strategy.json"
PRICES_FROM = "2020-06-01"      # 200 sessions of warm-up before the first report on file
CLUSTER_DAYS = 40               # a release this close before the next one is not the scheduled report
ENTRY_BLOCK = 5                 # sessions, for no-entry
EXTRA_COST = 0.001              # robust's "pay more" check, per side: 10 bp, not robust's 5% for options
RECENT_FROM = "2025-01-01"      # reports have gone the other way since: shown on their own
MODES = {"hold": "照旧拿过财报", "cash": "财报前一天收盘卖，财报那天收盘买回（那天拿现金）",
         "spy": "同上，但那天换成 SPY", "no-entry": "只是财报前 5 天内不新买，已有的照拿"}


def yahoo(sym: str, start: str = PRICES_FROM) -> dict[str, float]:
    """day -> close adjusted for splits and dividends."""
    p1 = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp())
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1={p1}"
           f"&period2={int(time.time())}&interval=1d&events=div,splits")
    err = None
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            res = json.load(urllib.request.urlopen(req, timeout=30))["chart"]["result"][0]
            break
        except Exception as e:  # noqa: BLE001 - research fetch, retried
            err = e
            time.sleep(1)
    else:
        raise err
    adj = res["indicators"]["adjclose"][0]["adjclose"]
    day = lambda t: (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=t - 5 * 3600)).date().isoformat()
    return {day(t): c for t, c in zip(res["timestamp"], adj) if c}


def scheduled(events: list[dict]) -> dict[str, list[str]]:
    """symbol -> reaction days of scheduled reports: drop a release that has another within CLUSTER_DAYS after it."""
    by: dict[str, list[str]] = {}
    for e in events:
        by.setdefault(e["symbol"], []).append(e["reaction_day"])
    out = {}
    for s, days in by.items():
        days = sorted(set(days))
        keep = [d for d, nxt in zip(days, days[1:] + [None])
                if nxt is None or (date.fromisoformat(nxt) - date.fromisoformat(d)).days > CLUSTER_DAYS]
        out[s] = keep
    return out


def reaction_day(report_day: str, when: str, days: list[str]) -> str | None:
    """The first session whose close-to-close move holds the report: that day before the open, else the next."""
    if when == "pre-market":
        return next((d for d in days if d >= report_day), None)
    return next((d for d in days if d > report_day), None)


def from_archive(symbols: set[str], after: str, days: list[str], folder: Path = ARCHIVE) -> tuple[list[dict], list[str]]:
    """Reports dated after `after` in the calendar archives, as {symbol, reaction_day}. A date counts only when
    the last archive fetched on or before it still lists it, so a report that moved is taken at its final date
    and only as known in advance. Also returns the reports with no time of day (counted as after the close)."""
    arch = []
    for f in sorted(folder.glob("*.json")):
        reps = json.loads(f.read_text()).get("reports") or {}
        arch.append((f.stem, {(s, r.get("date") or ""): r.get("time") or "" for s, rs in reps.items() if s in symbols for r in rs}))
    out, unknown = [], []
    for key in sorted({k for _, a in arch for k in a}):
        s, d = key
        if d <= after:
            continue
        last = [a for on, a in arch if on <= d]
        if not last or key not in last[-1]:
            continue
        when = last[-1][key]
        rd = reaction_day(d, when, days)
        if rd is None:
            continue
        if when not in ("pre-market", "post-market"):
            unknown.append(f"{s} {d}")
        out.append({"symbol": s, "reaction_day": rd})
    return out, unknown


def simulate(closes: dict[str, list[float]], dates: list[str], p: dict, regime: str, max_pos: float,
             start: int, react: dict[str, set[str]], mode: str) -> dict:
    """hero.backtest.run's loop with the guard applied to the day's target weights."""
    rets, equity, w_prev, events = [], [1.0], {}, []
    for t in range(start, len(dates) - 1):
        hist = {s: xs[: t + 1] for s, xs in closes.items()}
        guarded = {s for s, ds in react.items() if dates[t] in ds}   # sold the night before, still ours to rebuy
        w = momentum.target_weights(hist, p, regime, max_pos, held=frozenset(w_prev) | guarded)
        nxt = dates[t + 1]
        for s in list(w):
            if nxt in react.get(s, ()):
                wt = w[s]
                ev = {"day": nxt, "symbol": s, "weight": wt, "ret": closes[s][t + 1] / closes[s][t] - 1,
                      "spy": closes["SPY"][t + 1] / closes["SPY"][t] - 1}
                if mode in ("cash", "spy"):
                    del w[s]
                    if mode == "spy":
                        w["SPY"] = w.get("SPY", 0.0) + wt
                events.append(ev)
            elif mode == "no-entry" and s not in w_prev:
                ahead = dates[t + 1: t + 1 + ENTRY_BLOCK]
                if any(d in react.get(s, ()) for d in ahead):
                    del w[s]
        turnover = sum(abs(w.get(s, 0) - w_prev.get(s, 0)) for s in set(w) | set(w_prev))
        r = sum(wt * (closes[s][t + 1] / closes[s][t] - 1) for s, wt in w.items()) - backtest.COST * turnover
        rets.append((nxt, r))
        equity.append(equity[-1] * (1 + r))
        w_prev = w
    return {"rets": rets, "equity": equity, "events": events}


def stats(rets: list[tuple[str, float]]) -> dict:
    xs = [r for _, r in rets]
    eq = [1.0]
    for r in xs:
        eq.append(eq[-1] * (1 + r))
    yrs = len(xs) / 252
    years: dict[str, float] = {}
    for d, r in rets:
        years[d[:4]] = years.get(d[:4], 1.0) * (1 + r)
    worst = sorted(rets, key=lambda x: x[1])[:5]
    return {"total": eq[-1] - 1, "cagr": eq[-1] ** (1 / yrs) - 1 if yrs else 0.0, "sharpe": sharpe(xs),
            "max_dd": max_drawdown(eq), "vol": statistics.pstdev(xs) * 252 ** 0.5,
            "halt_days": sum(r <= -0.03 for r in xs), "worst": [(d, round(r, 4)) for d, r in worst],
            "years": {y: v - 1 for y, v in sorted(years.items())}, "days": len(xs)}


def pct(x, n: int = 1) -> str:
    return "—" if x is None else f"{x * 100:+.{n}f}%"


def robust_rows(rows: dict[str, list[tuple[str, float]]], end: str) -> tuple[dict, str]:
    """hero.robust.summary for each group, printed to two decimals (robust.table rounds to whole percents,
    which hides a stock's daily moves)."""
    out = [f"| 情况 | 笔数 | 平均 | 去掉最好 {robust.DROP_BEST} 笔 | 中位数 | 赚钱比例 | 各年份 | 最近 3 个月 | 买卖各多 {EXTRA_COST:.1%} 后 | 提醒 |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    sums = {}
    for name, xs in rows.items():
        s = robust.summary(xs, end=end, extra_cost=EXTRA_COST)
        sums[name] = s
        if not s:
            out.append(f"| {name} | 0 | — | — | — | — | — | — | — | 没有 |")
            continue
        yrs = "、".join(f"{y} {pct(m, 2)}（{n}）" for y, (m, n) in s["years"].items())
        rec = f"{pct(s['recent'][0], 2)}（{s['recent'][1]}）" if s["recent"] else "—"
        out.append(f"| {name} | {s['n']} | {pct(s['mean'], 2)} | {pct(s['trim'], 2)} | {pct(s['median'], 2)} | {s['win']:.0%} | "
                   f"{yrs} | {rec} | {pct(s['costly'], 2)} | {'；'.join(s['flags']) or '—'} |")
    return sums, "\n".join(out) + "\n"


def run(closes: dict[str, list[float]], dates: list[str], cfg: dict, react: dict[str, set[str]], start: int) -> dict:
    p, regime, max_pos = cfg["stocks"], cfg["regime_symbol"], cfg["risk"]["max_position_pct"]
    sims = {m: simulate(closes, dates, p, regime, max_pos, start, react, m) for m in MODES}
    return {"sims": sims, "stats": {m: stats(s["rets"]) for m, s in sims.items()}}


def markdown(rep: dict, dates: list[str], start: int, unknown: list[str], ordinary: dict) -> str:
    st, sims = rep["stats"], rep["sims"]
    evs = sims["hold"]["events"]
    end = dates[-1]
    o = [f"# 主账户正股要不要也加财报保护（{date.today().isoformat()}）", "",
         f"用现在的主账户规则（config/strategy.json：20 只里挑动量前 {rep['p']['top_n']}、{rep['p']['momentum_lookback']} 日动量、"
         f"{rep['p']['trend_sma']} 日均线、总仓位 {rep['p']['gross_exposure']:.0%}），按 hero.backtest 的收盘对收盘模型回放 "
         f"{dates[start]} 到 {end}（{st['hold']['days']} 个交易日）。不算止损和期权，每单位换手 5 个基点成本。只研究，不改交易。", "",
         "## 四种做法，整个组合", "",
         "| 做法 | 总收益 | 年化 | 夏普 | 最大回撤 | 年化波动 | 单日 ≤ −3% 的天数 | 最差一天 |", "|---|---|---|---|---|---|---|---|"]
    for m, label in MODES.items():
        s = st[m]
        o.append(f"| {label} | {pct(s['total'])} | {pct(s['cagr'])} | {s['sharpe']:.2f} | {pct(s['max_dd'])} | {s['vol'] * 100:.1f}% | "
                 f"{s['halt_days']} | {s['worst'][0][0]} {pct(s['worst'][0][1], 2)} |")
    o += ["", f"{RECENT_FROM} 以来（最近这段财报反应变差，单独看）：", "",
          "| 做法 | 总收益 | 夏普 | 最大回撤 | 单日 ≤ −3% 的天数 |", "|---|---|---|---|---|"]
    for m, label in MODES.items():
        s = stats([x for x in sims[m]["rets"] if x[0] >= RECENT_FROM])
        o.append(f"| {label} | {pct(s['total'])} | {s['sharpe']:.2f} | {pct(s['max_dd'])} | {s['halt_days']} |")
    o += ["", "各年份收益：", "", "| 年 | " + " | ".join(MODES.values()) + " |", "|---|" + "---|" * len(MODES)]
    for y in st["hold"]["years"]:
        o.append(f"| {y} | " + " | ".join(pct(st[m]["years"].get(y)) for m in MODES) + " |")
    # the reports held into
    gain = sum(e["weight"] * e["ret"] for e in evs)
    o += ["", f"## 拿过的 {len(evs)} 次财报", "",
          f"照旧拿的做法里，组合在财报那天持有的 {len(evs)} 次，加起来对净值贡献约 {pct(gain)}（按当天仓位，单利相加）。"
          f"同一批持股在不是财报的普通日子，平均每天 {pct(ordinary['mean'], 3)}（{ordinary['n']} 个持股日），"
          f"财报那天平均 {pct(statistics.mean(e['ret'] for e in evs), 2)}。", "",
          "下面每一笔是「这只股票财报那天每 $1 的涨跌」，交给 hero.robust 检查（「多付成本」用每边 0.1%，robust 默认的 5% 是给期权的）：", ""]
    rows = {"拿过财报，那天每 $1": [(e["day"], e["ret"]) for e in evs],
            "　减去 SPY 当天（只看公司自己的消息）": [(e["day"], e["ret"] - e["spy"]) for e in evs],
            "　仓位里赚过 10% 以上的（期间涨幅 63 日 > 10%）": [(e["day"], e["ret"]) for e in evs if e.get("mom", 0) > 0.10]}
    sums, tab = robust_rows(rows, end)
    rep["robust"] = sums
    o += [tab, "注意：robust 的年份和最近 3 个月提醒要相差 5% 才会出现，那是按期权定的；股票一天的平均在 1% 以内，所以这里的「各年份」一列要自己看正负。", ""]
    early = [e["ret"] for e in evs if e["day"] < RECENT_FROM]
    late = [e["ret"] for e in evs if e["day"] >= RECENT_FROM]
    o += ["平均数有多可靠（t = 平均 ÷ (标准差 ÷ √笔数)，|t| 不到 2 就分不清是不是运气）：", "",
          "| 区间 | 笔数 | 平均 | 标准差 | t |", "|---|---|---|---|---|"]
    for name, xs in (("全部", [e["ret"] for e in evs]), (f"{RECENT_FROM} 以前", early), (f"{RECENT_FROM} 以来", late)):
        if len(xs) > 1:
            sd = statistics.stdev(xs)
            o.append(f"| {name} | {len(xs)} | {pct(statistics.mean(xs), 2)} | {sd * 100:.1f}% | {statistics.mean(xs) / (sd / len(xs) ** 0.5):+.2f} |")
    o.append("")
    big = sorted(evs, key=lambda e: e["weight"] * e["ret"])
    o += ["最伤的 5 次和最赚的 5 次（对净值）：", "", "| 日子 | 股票 | 当天 | 仓位 | 对净值 |", "|---|---|---|---|---|"]
    for e in big[:5] + big[-5:]:
        o.append(f"| {e['day']} | {e['symbol']} | {pct(e['ret'], 1)} | {e['weight']:.0%} | {pct(e['weight'] * e['ret'], 2)} |")
    by_sym: dict[str, list[float]] = {}
    for e in evs:
        by_sym.setdefault(e["symbol"], []).append(e["ret"])
    o += ["", "按股票：", "", "| 股票 | 次数 | 平均 | 涨的比例 | 最差 | 最好 |", "|---|---|---|---|---|---|"]
    for s, xs in sorted(by_sym.items(), key=lambda kv: -len(kv[1])):
        o.append(f"| {s} | {len(xs)} | {pct(statistics.mean(xs), 1)} | {sum(x > 0 for x in xs) / len(xs):.0%} | {pct(min(xs), 1)} | {pct(max(xs), 1)} |")
    if unknown:
        o += ["", "日历里没写盘前还是盘后的（按盘后算）：" + "、".join(unknown)]
    return "\n".join(o) + "\n"


def main() -> None:
    cfg = json.loads(CONFIG.read_text())
    syms = cfg["universe"]
    raw = {s: yahoo(s) for s in syms}
    today = date.today().isoformat()
    days = sorted(d for d in raw[cfg["regime_symbol"]] if d < today)   # whole sessions only
    bars = {s: [{"t": d, "c": c} for d, c in sorted(m.items()) if d < today] for s, m in raw.items()}
    dates, closes = backtest.align(bars, cfg["regime_symbol"])
    missing = [s for s in syms if s not in closes]
    if missing:
        raise SystemExit(f"no full price history for {missing}")
    rep = json.loads(EVENTS.read_text())
    sec = [e for e in rep["events"] if e["symbol"] in syms]
    later, unknown = from_archive(set(syms), rep["generated"], days)
    sched = scheduled(sec + later)
    react = {s: set(sched.get(s, [])) for s in syms}
    first = min(e["reaction_day"] for e in sec)
    start = next(i for i, d in enumerate(dates) if d >= first) - 1
    out = run(closes, dates, cfg, react, start)
    # 63-day run-up before each held report, and the ordinary-day mean of the same holdings
    idx = {d: i for i, d in enumerate(dates)}
    for e in out["sims"]["hold"]["events"]:
        i = idx[e["day"]] - 1
        e["mom"] = closes[e["symbol"]][i] / closes[e["symbol"]][i - 63] - 1
    ordinary = held_ordinary(closes, dates, cfg, react, start)
    out["p"] = cfg["stocks"]
    md = markdown(out, dates, start, unknown, ordinary)
    js = {"generated": today, "window": [dates[start], dates[-1]], "modes": MODES, "stats": out["stats"],
          "events": out["sims"]["hold"]["events"], "robust": out.get("robust"), "ordinary": ordinary,
          "reports": {s: sorted(v) for s, v in react.items() if v}}
    (ROOT / "research" / f"{today}-stock-earnings-guard.json").write_text(json.dumps(js, ensure_ascii=False) + "\n")
    (ROOT / "research" / f"{today}-stock-earnings-guard.md").write_text(md)
    print(md)


def held_ordinary(closes: dict[str, list[float]], dates: list[str], cfg: dict, react: dict[str, set[str]], start: int) -> dict:
    """Mean next-day return of the single stocks held on days with no report: the baseline a report day beats or not."""
    p, regime, max_pos = cfg["stocks"], cfg["regime_symbol"], cfg["risk"]["max_position_pct"]
    xs, w_prev = [], {}
    for t in range(start, len(dates) - 1):
        w = momentum.target_weights({s: c[: t + 1] for s, c in closes.items()}, p, regime, max_pos, held=frozenset(w_prev))
        for s in w:
            if react.get(s) is not None and react[s] and dates[t + 1] not in react[s]:
                xs.append(closes[s][t + 1] / closes[s][t] - 1)
        w_prev = w
    return {"n": len(xs), "mean": statistics.mean(xs) if xs else None}


if __name__ == "__main__":
    main()
