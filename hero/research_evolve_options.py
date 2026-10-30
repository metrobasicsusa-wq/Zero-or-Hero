"""Search the main account's option parameters on real option history (owner, 10-30: 「那下次进化加上期权参数」).

The weekly evolution (hero/evolve.py) only searches the stock side, yet since 10-01 nearly all of the account's gain
came from the 10% option sleeve (XLV, XLE, AAPL, TSLA calls). This replays that sleeve's rule on Alpaca's option
history (daily bars from early 2024) with the stock parameters fixed at the live config, over a grid of option
parameters, and scores each set the way evolve.py scores stocks: the worse of the training and validation Sharpe of
the combined (stock + option) daily returns, so a set has to work in both periods.

The rule replayed (hero/engine.py _option_entries / _option_exits, hero/strategies/options.py):
  while the regime symbol is above its 200-day line, calls on the momentum ranking's top names not yet held (one
  contract series per name, `max_positions` at once), else a put on the regime symbol; the contract is the strike
  whose |delta| is closest to `target_delta` on the first Friday expiry at least `min_dte` days out and no later than
  `max_dte` days, and (earnings_guard) before the name's next report; size = allocation x equity / max_positions,
  whole contracts; out at `take_profit`, `stop_loss`, or `exit_dte` days before expiry, or the day before a report.
Simplifications, all written into the report: fills at the day's close, not 09:40 mids; delta from Black-Scholes
implied by that close (no greeks in history); 2% of the premium paid each way; a strike with no trade that day is
skipped at entry and carries its last close as a mark; equity for sizing is fixed at $100k; NVDA / AVGO entries
whose expiry crosses their 2024 splits are skipped (the adjusted contracts change symbol). Record only: nothing is
adopted here. Run: python -m hero.research_evolve_options  (the chain cache lives in research/evolve-cache/).
"""

from __future__ import annotations

import gzip
import itertools
import json
import math
import statistics
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from hero import backtest, robust
from hero.indicators import sharpe
from hero.strategies import momentum

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "research" / "evolve-cache" / "option-chains.json.gz"
EVENTS = ROOT / "research" / "2026-10-02-earnings-reactions.json"
HISTORY_START = "2024-01-02"      # stock closes from here (warm-up); Alpaca option bars begin 2024-02
OPTIONS_START = "2024-03-15"      # first entry day
EQUITY = 100_000.0
SLIP = 0.02                        # of the premium, each way
RATE = 0.045
SPLITS = {"NVDA": "2024-06-10", "AVGO": "2024-07-15"}
ETFS = {"SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "XLV", "XLY", "XLI", "XLP", "XLB", "XLU", "XLC", "SMH", "IGV"}
TRAIN_FRACTION = 0.7
MIN_IMPROVEMENT = 0.15
MIN_VAL_TRADES = 20
GRID = {
    "target_delta": [0.3, 0.5, 0.7],
    "dte": [(30, 60), (45, 90), (60, 120)],
    "take_profit": [0.4, 0.6, 1.0],
    "stop_loss": [-0.3, -0.4, -0.6],
    "exit_dte": [7, 14],
    "allocation": [0.05, 0.10, 0.15],
}


# ---------------------------------------------------------------- Black-Scholes (delta from a price, no greeks in history)
def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(s: float, k: float, t: float, sigma: float, call: bool, r: float = RATE) -> float:
    if t <= 0 or sigma <= 0:
        return max(s - k, 0.0) if call else max(k - s, 0.0)
    d1 = (math.log(s / k) + (r + sigma * sigma / 2) * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)
    if call:
        return s * _ncdf(d1) - k * math.exp(-r * t) * _ncdf(d2)
    return k * math.exp(-r * t) * _ncdf(-d2) - s * _ncdf(-d1)


def implied_vol(price: float, s: float, k: float, t: float, call: bool) -> float | None:
    """Bisection on [1%, 500%]; None when the price is below intrinsic or outside that band."""
    lo, hi = 0.01, 5.0
    if t <= 0 or price <= bs_price(s, k, t, lo, call) or price >= bs_price(s, k, t, hi, call):
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if bs_price(s, k, t, mid, call) < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def bs_delta(s: float, k: float, t: float, sigma: float, call: bool, r: float = RATE) -> float:
    d1 = (math.log(s / k) + (r + sigma * sigma / 2) * t) / (sigma * math.sqrt(t))
    return _ncdf(d1) if call else _ncdf(d1) - 1


# ---------------------------------------------------------------- calendar and earnings
FRIDAY_HOLIDAYS = {"2024-03-29", "2025-04-18", "2025-07-04", "2026-04-03", "2026-07-03", "2026-12-25", "2027-01-01",
                   "2027-03-26"}   # Fridays the market is shut: the expiry moves to Thursday


def friday_expiries(sessions: list[str], earliest: date, latest: date) -> list[str]:
    """The weekly expiries between the two dates, nearest first: each Friday on or after `earliest`, or the session
    before it when that Friday is a holiday (Good Friday, 2025-07-04). Past the last session in `sessions` (an entry
    near the end of the sample, whose expiry is still in the future) the Fridays come from the calendar and the
    holiday list. The caller tries them in turn: a sector ETF's near weekly often has no trades while its monthly
    (the third Friday) does, and the live engine scans the whole window and takes whatever has quotes."""
    out, last = [], sessions[-1] if sessions else ""
    d = earliest + timedelta(days=(4 - earliest.weekday()) % 7)
    while d <= latest:
        iso = d.isoformat()
        if iso > last:
            pick = (d - timedelta(days=1)).isoformat() if iso in FRIDAY_HOLIDAYS else iso
            if pick not in out:
                out.append(pick)
        elif iso in sessions:
            out.append(iso)
        else:
            prev = [x for x in sessions if x < iso]
            if prev and date.fromisoformat(prev[-1]) >= earliest and prev[-1] not in out:
                out.append(prev[-1])
        d += timedelta(days=7)
    return out


def friday_expiry(sessions: list[str], earliest: date, latest: date) -> str | None:
    xs = friday_expiries(sessions, earliest, latest)
    return xs[0] if xs else None


def report_calendar(events_path: Path = EVENTS, upcoming: Path | None = None) -> dict[str, list[tuple[str, str]]]:
    """symbol -> sorted (report date, 'pre' | 'post') from the five-year reactions file plus data/earnings.json."""
    out: dict[str, set] = {}
    try:
        for e in json.loads(events_path.read_text()).get("events", []):
            acc = datetime.fromisoformat(e["accepted"])
            out.setdefault(e["symbol"], set()).add((acc.date().isoformat(), "post" if acc.hour >= 16 else "pre"))
    except Exception:
        pass
    try:
        for s, rs in (json.loads((upcoming or ROOT / "data" / "earnings.json").read_text()).get("reports") or {}).items():
            for r in rs:
                out.setdefault(s, set()).add((r["date"], "pre" if "pre" in str(r.get("time") or "") else "post"))
    except Exception:
        pass
    return {s: sorted(v) for s, v in out.items()}


def last_safe_expiry(reports: list[tuple[str, str]], day: str) -> str | None:
    """The last expiry that settles before the next report after `day`: the day before a pre-market report, the
    report day itself for one after the close. None when no report is known (the ETFs)."""
    nxt = next((r for r in reports if r[0] > day), None)
    if not nxt:
        return None
    d = date.fromisoformat(nxt[0])
    return (d - timedelta(days=1) if nxt[1] == "pre" else d).isoformat()


# ---------------------------------------------------------------- option chains, cached
class Chains:
    """(underlying, expiry, kind) -> {strike: {"sym", "bars": {day: close}}}, fetched once from Alpaca (contracts, then
    daily bars from 130 days before the expiry) and kept in a gzip file so the weekly search only pays for new weeks."""

    def __init__(self, client, path: Path = CACHE, pace=None):
        self.c, self.path, self.pace = client, path, pace
        self.data: dict[str, dict] = {}
        self.dirty = 0
        if path.exists():
            with gzip.open(path, "rt") as f:
                self.data = json.load(f)

    def chain(self, und: str, expiry: str, kind: str, spot: float) -> dict[str, dict]:
        key = f"{und}|{expiry}|{kind}"
        if key in self.data:
            return self.data[key]
        band = 0.12 if und in ETFS else 0.22   # where a 0.3-0.7 delta lives at 30-120 days: index ETFs ~15% vol, single names ~50%
        cs = []
        for status in ("inactive", "active"):
            try:
                cs += self.c.option_contracts(und, status=status, type=kind, expiration_date_gte=expiry, expiration_date_lte=expiry,
                                              strike_price_gte=f"{spot * (1 - band):.2f}", strike_price_lte=f"{spot * (1 + band):.2f}")
            except Exception:
                pass
            if self.pace:
                self.pace.wait()
        out: dict[str, dict] = {}
        syms = sorted({c["symbol"] for c in cs})
        bars = {}
        if syms:
            start = (date.fromisoformat(expiry) - timedelta(days=130)).isoformat()
            try:
                bars = self.c.option_bars(syms, start, f"{expiry}T23:59:00Z", timeframe="1Day")
            except Exception:
                bars = {}
            if self.pace:
                self.pace.wait()
        for c in cs:
            k = f"{float(c['strike_price']):g}"
            out[k] = {"sym": c["symbol"], "bars": {b["t"][:10]: float(b["c"]) for b in bars.get(c["symbol"], [])}}
        self.data[key] = out
        self.dirty += 1
        if self.dirty % 25 == 0:
            self.save()
        return out

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(self.path, "wt") as f:
            json.dump(self.data, f)


# ---------------------------------------------------------------- the replay
def stock_returns(closes: dict[str, list[float]], p: dict, cfg: dict, start: int, end: int,
                  macro_closes: dict | None = None) -> list[float]:
    """backtest.run's daily return series (it only returns the summary)."""
    rets, w_prev = [], {}
    for t in range(start, end - 1):
        mc = {s: xs[: t + 1] for s, xs in macro_closes.items()} if macro_closes else None
        w = momentum.target_weights({s: xs[: t + 1] for s, xs in closes.items()}, p, cfg["regime_symbol"],
                                    cfg["risk"]["max_position_pct"], held=frozenset(w_prev), macro_closes=mc)
        turnover = sum(abs(w.get(s, 0) - w_prev.get(s, 0)) for s in set(w) | set(w_prev))
        rets.append(sum(wt * (closes[s][t + 1] / closes[s][t] - 1) for s, wt in w.items()) - backtest.COST * turnover)
        w_prev = w
    return rets


def pick_strike(chain: dict[str, dict], day: str, spot: float, expiry: str, call: bool, target: float) -> tuple[str, float, float] | None:
    """(strike, close, |delta|) of the strike traded on `day` whose Black-Scholes |delta| is nearest the target."""
    t = (date.fromisoformat(expiry) - date.fromisoformat(day)).days / 365
    best = None
    for k, c in chain.items():
        px = c["bars"].get(day)
        if not px or px <= 0:
            continue
        iv = implied_vol(px, spot, float(k), t, call)
        if iv is None:
            continue
        d = abs(bs_delta(spot, float(k), t, iv, call))
        if not 0.05 <= d <= 0.95:
            continue
        if best is None or abs(d - target) < abs(best[2] - target):
            best = (k, px, d)
    return best


def simulate(opt: dict, cfg: dict, days: list[str], adj: dict[str, list[float]], raw: dict[str, list[float]],
             chains: Chains, reports: dict, start: int, end: int, macro_closes: dict | None = None,
             stock: list[float] | None = None) -> dict:
    """One parameter set over sessions days[start:end]: daily option P&L in dollars on a fixed $100k, the trades,
    and the combined daily returns with the live stock parameters."""
    regime, p = cfg["regime_symbol"], cfg["stocks"]
    lo_dte, hi_dte = opt["dte"]
    budget = opt["allocation"] * EQUITY / opt["max_positions"]
    open_pos: dict[str, dict] = {}   # und -> position
    trades, pnl = [], []
    skips: dict[str, int] = {}       # why a candidate produced no entry, summed over the replay
    for t in range(start, end - 1):
        day = days[t]
        day_pnl = 0.0   # the day's change in the option book: marks moving, exits (with their slippage), entry slippage
        # mark and exit
        for und, pos in list(open_pos.items()):
            bars, prev = pos["bars"], pos["mark"]
            spot_raw = raw[und][t]
            if day >= pos["expiry"]:
                mark = max(spot_raw - pos["strike"], 0.0) if pos["call"] else max(pos["strike"] - spot_raw, 0.0)
                reason = "expiry"
            else:
                mark = bars.get(day, pos["mark"])
                pl = mark / pos["entry"] - 1
                dte = (date.fromisoformat(pos["expiry"]) - date.fromisoformat(day)).days
                safe = last_safe_expiry(reports.get(und, []), day)
                nxt = next((r for r in reports.get(und, []) if r[0] > day), None)
                reason = None
                if pl >= opt["take_profit"]:
                    reason = "take_profit"
                elif pl <= opt["stop_loss"]:
                    reason = "stop_loss"
                elif dte <= opt["exit_dte"]:
                    reason = "exit_dte"
                elif safe and pos["expiry"] > safe and nxt and (date.fromisoformat(nxt[0]) - date.fromisoformat(day)).days <= 1:
                    reason = "earnings"
            pos["mark"] = mark
            if reason:
                out_px = mark if reason == "expiry" else mark * (1 - SLIP)   # settlement costs nothing; a sale pays the spread
                day_pnl += (out_px - prev) * 100 * pos["qty"]
                trades.append({"und": und, "call": pos["call"], "in": pos["day"], "out": day, "expiry": pos["expiry"],
                               "strike": pos["strike"], "qty": pos["qty"], "entry": pos["entry"], "exit": round(mark, 4),
                               "reason": reason, "per_dollar": round(out_px / (pos["entry"] * (1 + SLIP)) - 1, 4),
                               "pnl": round((out_px - pos["entry"] * (1 + SLIP)) * 100 * pos["qty"], 2)})
                del open_pos[und]
            else:
                day_pnl += (mark - prev) * 100 * pos["qty"]
        # entries at the close
        slots = opt["max_positions"] - len(open_pos)
        if slots > 0 and day >= OPTIONS_START:
            hist = {s: xs[: t + 1] for s, xs in adj.items()}
            if momentum.is_bullish(hist[regime]):
                cands = [(s, True) for s in momentum.rank(hist, p)]
            else:
                cands = [(regime, False)]
            today = date.fromisoformat(day)
            for und, call in cands:
                if slots <= 0:
                    break
                if und in open_pos:
                    continue
                latest = today + timedelta(days=hi_dte)
                safe = last_safe_expiry(reports.get(und, []), day)
                if safe and date.fromisoformat(safe) < latest:
                    latest = date.fromisoformat(safe)
                    if latest < today + timedelta(days=lo_dte):
                        skips["earnings_guard"] = skips.get("earnings_guard", 0) + 1
                        continue
                exps = [e for e in friday_expiries(days, today + timedelta(days=lo_dte), latest)
                        if not (und in SPLITS and day < SPLITS[und] <= e)][:4]
                if not exps:
                    skips["no_expiry"] = skips.get("no_expiry", 0) + 1
                    continue
                spot = raw[und][t]
                pick, exp, chain = None, None, None
                for e in exps:   # the first expiry in the window with a strike that traded today
                    chain = chains.chain(und, e, "call" if call else "put", spot)
                    pick = pick_strike(chain, day, spot, e, call, opt["target_delta"])
                    if pick:
                        exp = e
                        break
                if not pick:
                    skips["no_traded_strike"] = skips.get("no_traded_strike", 0) + 1
                    continue
                k, px, d = pick
                qty = math.floor(budget / (px * 100))
                if qty < 1:
                    skips["over_budget"] = skips.get("over_budget", 0) + 1
                    continue
                open_pos[und] = {"day": day, "expiry": exp, "strike": float(k), "call": call, "qty": qty, "entry": px,
                                 "mark": px, "bars": chain[k]["bars"], "delta": round(d, 2)}
                day_pnl -= px * SLIP * 100 * qty
                slots -= 1
        pnl.append(day_pnl)
    stock = stock if stock is not None else stock_returns(adj, p, cfg, start, end, macro_closes)
    combined = [s + o / EQUITY for s, o in zip(stock, pnl)]
    still = [{"und": u, "call": x["call"], "in": x["day"], "expiry": x["expiry"], "strike": x["strike"], "qty": x["qty"],
              "entry": x["entry"], "mark": round(x["mark"], 4), "per_dollar": round(x["mark"] / x["entry"] - 1, 4)} for u, x in open_pos.items()]
    return {"pnl": pnl, "trades": trades, "stock_rets": stock, "combined": combined, "skips": skips, "open": still}


def score(res: dict, split: int) -> dict:
    """Train / validation Sharpe of the combined returns (the first `split` days train), like evolve.evaluate."""
    tr, va = res["combined"][:split], res["combined"][split:]
    st_tr, st_va = res["stock_rets"][:split], res["stock_rets"][split:]
    n_val = sum(1 for x in res["trades"] if x["out"] >= res.get("val_from", ""))
    return {"train": sharpe(tr), "val": sharpe(va), "score": min(sharpe(tr), sharpe(va)),
            "stock_only_train": sharpe(st_tr), "stock_only_val": sharpe(st_va),
            "opt_pnl": round(sum(res["pnl"]), 2), "opt_pnl_val": round(sum(res["pnl"][split:]), 2),
            "trades": len(res["trades"]), "val_trades": n_val,
            "win": round(sum(x["pnl"] > 0 for x in res["trades"]) / len(res["trades"]), 3) if res["trades"] else None}


def run(client, cfg: dict, pace=None) -> dict:
    syms = list(cfg["universe"])
    adj_bars = client.daily_bars(syms, HISTORY_START, adjustment="all")
    raw_bars = client.daily_bars(syms, HISTORY_START, adjustment="raw")
    days, adj = backtest.align(adj_bars, cfg["regime_symbol"])
    _, raw = backtest.align(raw_bars, cfg["regime_symbol"])
    adj = {s: xs for s, xs in adj.items() if s in raw}
    reports = report_calendar()
    chains = Chains(client, pace=pace)
    n = len(days)
    first = next(i for i, d in enumerate(days) if d >= OPTIONS_START)
    start = max(first, 210)
    split = start + int((n - start) * TRAIN_FRACTION)
    val_from = days[split]
    live = {**cfg["options"], "dte": (cfg["options"]["min_dte"], cfg["options"]["max_dte"])}
    results = []
    stock = stock_returns(adj, cfg["stocks"], cfg, start, n)   # the same for every combo: the stock side is fixed
    combos = [dict(zip(GRID, c), max_positions=cfg["options"]["max_positions"]) for c in itertools.product(*GRID.values())]
    for i, opt in enumerate([live] + combos):
        res = simulate(opt, cfg, days, adj, raw, chains, reports, start, n, stock=stock)
        res["val_from"] = val_from
        sc = score(res, split - start)
        results.append({"params": {k: opt[k] for k in GRID}, "live": i == 0, **sc, "skips": res["skips"],
                        "open_at_end": len(res["open"]), "trades_list": res["trades"] if i == 0 else None})
        if i == 0:
            results[0]["pnl"] = [round(x, 2) for x in res["pnl"]]
            results[0]["open_list"] = res["open"]
            results[0]["pnl_days"] = days[start:n - 1]
    chains.save()
    cur = results[0]
    best = max(results[1:], key=lambda r: r["score"])
    adopt = best["score"] >= cur["score"] + MIN_IMPROVEMENT and best["val_trades"] >= MIN_VAL_TRADES
    return {"generated": date.today().isoformat(), "days": [days[start], days[n - 1]], "val_from": val_from,
            "sessions": n - start, "current": cur, "best": best, "would_adopt": adopt,
            "top": sorted(results[1:], key=lambda r: -r["score"])[:15],
            "results": [{k: v for k, v in r.items() if k not in ("trades_list", "pnl")} for r in results],
            "chains_cached": len(chains.data)}


def markdown(rep: dict) -> str:
    cur, best = rep["current"], rep["best"]
    f = lambda r: (f"delta {r['params']['target_delta']}、{r['params']['dte'][0]}–{r['params']['dte'][1]} 天、止盈 {r['params']['take_profit']:+.0%}、"
                   f"止损 {r['params']['stop_loss']:+.0%}、剩 {r['params']['exit_dte']} 天平、仓位 {r['params']['allocation']:.0%}")
    md = [f"# 主账户期权参数搜索 {rep['generated']}", "",
          f"把主账户的期权规则放回 {rep['days'][0]} 到 {rep['days'][1]}（{rep['sessions']} 个交易日）的真实期权日线上重放（Alpaca 期权历史从 2024 年初开始），"
          f"正股参数固定为现在的配置，搜 {len(rep['results']) - 1} 组期权参数（delta、到期天数、止盈、止损、提前平仓天数、仓位）。"
          f"打分和每周进化一样：训练期（前 70%）和验证期（{rep['val_from']} 起）各算一次正股 + 期权合计的日收益夏普比，取较差的那个；"
          f"要比现在的参数高 {MIN_IMPROVEMENT} 以上、验证期至少 {MIN_VAL_TRADES} 笔才算可换。",
          "简化（都会让回测比实盘乐观或悲观，看方向）：按当天收盘成交而不是 09:40 的中间价；delta 用收盘价反推的 Black-Scholes（历史没有 greeks）；"
          f"每边付权利金的 {SLIP:.0%}；当天没成交的行权价不选、持仓按最近一次收盘记；仓位按固定 $100k 算；NVDA / AVGO 到期跨过 2024 年拆股的不买。"
          "只研究，不改配置。", "",
          "## 现在的参数 vs 最好的一组", "",
          "| | 参数 | 分数 | 训练夏普 | 验证夏普 | 只有正股：训练 / 验证 | 期权盈亏 $ | 验证期期权盈亏 $ | 笔数（验证期） | 胜率 |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for name, r in (("现在", cur), ("最好", best)):
        md.append(f"| {name} | {f(r)} | {r['score']:.2f} | {r['train']:.2f} | {r['val']:.2f} | {r['stock_only_train']:.2f} / {r['stock_only_val']:.2f} | "
                  f"{r['opt_pnl']:+,.0f} | {r['opt_pnl_val']:+,.0f} | {r['trades']}（{r['val_trades']}） | {r['win']:.0%} |" if r['win'] is not None else
                  f"| {name} | {f(r)} | {r['score']:.2f} | {r['train']:.2f} | {r['val']:.2f} | {r['stock_only_train']:.2f} / {r['stock_only_val']:.2f} | "
                  f"{r['opt_pnl']:+,.0f} | {r['opt_pnl_val']:+,.0f} | {r['trades']}（{r['val_trades']}） | — |")
    md += ["", f"**结论：{'最好的一组够格换' if rep['would_adopt'] else '没有一组够格换'}**（最好 {best['score']:.2f} 对现在 {cur['score']:.2f}，差 {best['score'] - cur['score']:+.2f}；"
           f"验证期 {best['val_trades']} 笔）。", "", "## 前 15 组", "",
           "| 参数 | 分数 | 训练 | 验证 | 期权盈亏 $ | 笔数 | 胜率 |", "|---|---|---|---|---|---|---|"]
    for r in rep["top"]:
        md.append(f"| {f(r)} | {r['score']:.2f} | {r['train']:.2f} | {r['val']:.2f} | {r['opt_pnl']:+,.0f} | {r['trades']} | {r['win']:.0%} |" if r['win'] is not None
                  else f"| {f(r)} | {r['score']:.2f} | {r['train']:.2f} | {r['val']:.2f} | {r['opt_pnl']:+,.0f} | {r['trades']} | — |")
    # which knobs matter: average score by each value
    md += ["", "## 每个旋钮单独看（其他旋钮取平均）", "", "| 旋钮 | 取值 | 平均分数 | 平均期权盈亏 $ |", "|---|---|---|---|"]
    rs = rep["results"][1:]
    for k in GRID:
        for v in GRID[k]:
            xs = [r for r in rs if r["params"][k] == v or (isinstance(v, tuple) and tuple(r["params"][k]) == v)]
            if xs:
                md.append(f"| {k} | {v} | {statistics.mean(r['score'] for r in xs):.2f} | {statistics.mean(r['opt_pnl'] for r in xs):+,.0f} |")
    tl = cur.get("trades_list") or []
    pd, pl = cur.get("pnl_days") or [], cur.get("pnl") or []
    if pd:
        last = [(d, x) for d, x in zip(pd, pl)][-22:]
        md += ["", "## 对照实盘（现在的参数，最近 22 个交易日）", "",
               f"重放里这段期权盈亏合计 {sum(x for _, x in last):+,.0f}，{sum(1 for _, x in last if abs(x) > 0.5)} 天有仓位变动；"
               f"重放结束时还拿着 {cur.get('open_at_end', 0)} 个仓位。实盘 10-01 起约 10 笔期权、合计约 +$10,400，两边对不上就说明重放哪里和引擎不一样（优先怀疑：到期选择、成交价、停损用的收盘价）。",
               "", "没买成的原因（现在的参数，整段合计）：" + "、".join(f"{k} {v}" for k, v in sorted((cur.get("skips") or {}).items(), key=lambda kv: -kv[1])) + "。"]
        if cur.get("open_list"):
            md += ["", "| 还拿着 | 买入 | 到期 | 行权价 | 张 | 买价 | 现价 | 每 $1 |", "|---|---|---|---|---|---|---|---|"]
            for x in cur["open_list"]:
                md.append(f"| {x['und']} {'看涨' if x['call'] else '看跌'} | {x['in']} | {x['expiry']} | {x['strike']:g} | {x['qty']} | {x['entry']:.2f} | {x['mark']:.2f} | {x['per_dollar']:+.0%} |")
    if tl:
        end = max(x["out"] for x in tl)
        rob = {"现在的参数：每笔每 $1": robust.summary([(x["out"], x["per_dollar"]) for x in tl], end)}
        md += ["", robust.table(rob, "稳健性检查（现在的参数，每笔按买入成本算）"), "",
               "## 现在的参数：每一笔", "", "| 买入 | 卖出 | 标的 | 方向 | 到期 | 行权价 | 张 | 买价 | 卖价 | 原因 | 每 $1 | 盈亏 $ |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for x in tl:
            md.append(f"| {x['in']} | {x['out']} | {x['und']} | {'看涨' if x['call'] else '看跌'} | {x['expiry']} | {x['strike']:g} | {x['qty']} | {x['entry']:.2f} | "
                      f"{x['exit']:.2f} | {x['reason']} | {x['per_dollar']:+.0%} | {x['pnl']:+,.0f} |")
    return "\n".join(md) + "\n"


def main() -> int:
    from hero.alpaca import Alpaca
    from hero.research_event_turn import Pace
    from hero.research_ideas import save
    cfg = json.loads((ROOT / "config" / "strategy.json").read_text())
    client = Alpaca()
    rep = run(client, cfg, pace=Pace(client))
    save("evolve-options", rep, markdown(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
