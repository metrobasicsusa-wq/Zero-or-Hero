"""Research: fixed 20-name universe vs a monthly, liquidity-ranked dynamic universe.

The dynamic pool is rebuilt on the first trading day of each month from what was knowable
then: the most traded stocks by average dollar volume over the previous 63 days (price above
$10, at least a year of history), plus broad and sector ETFs. The same momentum rules then
pick from it. Record only, no trading. Run: python -m hero.research_universe

Remaining bias: candidates are today's listed, liquid, shortable stocks (delisted names are
not in Alpaca's asset list), so the dynamic pool still leans towards survivors, though less
than a hand-picked list.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

from hero.research_short import by_year, stats
from hero.strategies import momentum as mom

YEARS = 6
COST = 0.0005
MIN_PRICE = 10
LOOKBACK_DV = 63
ETFS = ["SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "XLV", "XLY", "XLI", "XLP", "XLU", "XLB", "XLRE", "XLC",
        "SMH"]


# Exchange-traded products are not companies: leveraged, inverse, bond, commodity and crypto
# funds would let a stock-momentum rule turn into a leveraged bet or a short. Matched on the
# issuer's name as Alpaca lists it; the broad and sector ETFs we want are added back explicitly.
FUND_NAME = re.compile(r"\b(ETF|ETN|ETP|Fund|ProShares|Direxion|iShares|SPDR|Invesco|Vanguard|Grayscale|"
                       r"WisdomTree|VanEck|GraniteShares|Defiance|Tradr|Leverage Shares|ProFunds|Roundhill|"
                       r"ARK |Global X|Schwab|First Trust|Bitcoin|Ether|Treasury|Bond|Index|Daily|"
                       r"[0-9](\.[0-9])?[xX]|Ultra|UltraPro|Inverse|Bear|Bull)\b", re.I)
SHARE_CLASS = re.compile(r"\b(class [a-z]|series [a-z]|common stock|ordinary shares|capital stock|"
                         r"american depositary shares?|ads|inc\.?|corp\.?|corporation|ltd\.?|plc|n\.?v\.?|"
                         r"s\.?a\.?|holdings?|co\.?|company)\b|[^a-z0-9 ]", re.I)


def company(name: str, symbol: str) -> str:
    """One key per company, so GOOG and GOOGL (or FOX/FOXA) count as one name."""
    key = " ".join(SHARE_CLASS.sub(" ", name or "").lower().split())
    return key or symbol


def candidates(client) -> dict[str, str]:
    """Listed operating companies we could trade, as {symbol: company key}."""
    out = {}
    for a in client.assets():
        s, name = a.get("symbol", ""), a.get("name") or ""
        if (a.get("tradable") and a.get("marginable") and a.get("shortable") and a.get("fractionable")
                and a.get("exchange") in ("NYSE", "NASDAQ") and s.isalpha() and not FUND_NAME.search(name)):
            out[s] = company(name, s)
    return dict(sorted(out.items()))


def top_by_company(ranked: list[tuple[float, str]], keys: dict[str, str], size: int) -> list[str]:
    """Highest dollar volume first, one share class per company."""
    pool, seen = [], set()
    for _, s in sorted(ranked, reverse=True):
        k = keys.get(s, s)
        if k in seen:
            continue
        seen.add(k)
        pool.append(s)
        if len(pool) == size:
            break
    return pool


def align(bars: dict[str, list[dict]], calendar: str):
    dates = sorted({b["t"][:10] for b in bars[calendar]})
    idx = {d: i for i, d in enumerate(dates)}
    closes, dvol = {}, {}
    for s, bs in bars.items():
        c, v = [None] * len(dates), [0.0] * len(dates)
        for b in bs:
            i = idx.get(b["t"][:10])
            if i is not None:
                c[i], v[i] = float(b["c"]), float(b["c"]) * float(b.get("v") or 0)
        last = None
        for i in range(len(dates)):  # forward-fill gaps after the first quote
            if c[i] is None and last is not None:
                c[i] = last
            last = c[i] if c[i] is not None else last
        closes[s], dvol[s] = c, v
    return dates, closes, dvol


def monthly_pools(dates, closes, dvol, firsts, stocks, size: int, start: int) -> dict[int, list[str]]:
    """stocks: {symbol: company key} (or a plain list, without share-class de-duplication)."""
    # Pool for each month-start index, using only data before that day.
    pools, month = {}, None
    for t in range(start, len(dates)):
        if dates[t][:7] == month:
            continue
        month = dates[t][:7]
        ranked = []
        for s in stocks:
            xs = closes[s]
            first = firsts[s]
            if first is None or t - first < 252 or not xs[t - 1] or xs[t - 1] < MIN_PRICE:
                continue
            ranked.append((sum(dvol[s][t - LOOKBACK_DV: t]) / LOOKBACK_DV, s))
        pools[t] = top_by_company(ranked, stocks if isinstance(stocks, dict) else {}, size)
    return pools


def window(xs: list, first: int | None, t: int, n: int) -> list[float]:
    """The last n+1 prices up to day t (enough for every indicator used), from the first quote."""
    if first is None or first > t:
        return []
    return xs[max(first, t - n): t + 1]


def simulate(dates, closes, firsts, p: dict, regime: str, max_pos: float, start: int, pool_at) -> tuple[list[float], dict]:
    """Same rules as momentum.target_weights (macro off); the regime index is only a gauge."""
    need = max(p["momentum_lookback"], p["trend_sma"], 20) + 2
    rets, prev, picks_count = [], {}, {}
    for t in range(start, len(dates) - 1):
        hist = {}
        for s in pool_at(t):
            xs = window(closes[s], firsts[s], t, need)
            if len(xs) > need - 1:
                hist[s] = xs
        picks = mom.rank(hist, p, frozenset(prev))[: p["top_n"]]
        gauge = window(closes[regime], firsts[regime], t, mom.REGIME_SMA + 1)
        gross = p["gross_exposure"] * (1 if not gauge or mom.is_bullish(gauge) else mom.BEAR_EXPOSURE_SCALE)
        w = {s: min(gross / len(picks), max_pos) for s in picks} if picks else {}
        r = sum(x * (closes[s][t + 1] / closes[s][t] - 1) for s, x in w.items() if closes[s][t] and closes[s][t + 1])
        turnover = sum(abs(w.get(s, 0) - prev.get(s, 0)) for s in set(w) | set(prev))
        rets.append(r - COST * turnover)
        for s in w:
            picks_count[s] = picks_count.get(s, 0) + 1
        prev = w
    return rets, picks_count


def run(client, root: Path) -> dict:
    start_day = (date.today() - timedelta(days=365 * YEARS)).isoformat()
    stocks = candidates(client)
    syms = sorted(set(stocks) | set(ETFS))
    for name in ("strategy.json", "s500.json"):
        syms = sorted(set(syms) | set(json.loads((root / "config" / name).read_text())["universe"]))
    bars: dict[str, list[dict]] = {}
    for i in range(0, len(syms), 200):  # keep each request URL short
        bars.update(client.daily_bars(syms[i:i + 200], start_day))
    dates, closes, dvol = align(bars, "SPY")
    for s in syms:
        closes.setdefault(s, [None] * len(dates))
        dvol.setdefault(s, [0.0] * len(dates))
    have = {s: k for s, k in stocks.items() if s in bars}
    firsts = {s: next((i for i, x in enumerate(xs) if x is not None), None) for s, xs in closes.items()}
    report = {"generated": date.today().isoformat(), "candidates": len(have), "years": YEARS, "results": {}}
    for exp, cfg_name in (("Claude", "strategy.json"), ("Claude-500", "s500.json")):
        cfg = json.loads((root / "config" / cfg_name).read_text())
        p, regime, max_pos = cfg["stocks"], cfg["regime_symbol"], cfg["risk"]["max_position_pct"]
        warm = 260 + LOOKBACK_DV
        variants = {"固定股票池（现在的 20 只）": lambda t, u=cfg["universe"]: u}
        for size in (100, 300):
            pools = monthly_pools(dates, closes, dvol, firsts, have, size, warm)
            keys = sorted(pools)

            def pool_at(t, pools=pools, keys=keys):
                k = max(k for k in keys if k <= t)
                return pools[k] + ETFS
            variants[f"动态：成交额前 {size} + ETF"] = pool_at
        out = {}
        for label, pool_at in variants.items():
            rets, picks = simulate(dates, closes, firsts, p, regime, max_pos, warm, pool_at)
            top = sorted(picks.items(), key=lambda kv: -kv[1])[:10]
            out[label] = {**stats(rets), "by_year": by_year(dates, rets, warm),
                          "most_held": [f"{s}({n}天)" for s, n in top]}
        report["results"][exp] = {"period": f"{dates[warm]} → {dates[-1]}", "params": {k: p[k] for k in
                                  ("momentum_lookback", "trend_sma", "top_n", "gross_exposure")}, "variants": out}
    return report


def markdown(rep: dict) -> str:
    out = [f"# 股票池回测 {rep['generated']}", "",
           f"只研究，不改交易。候选股票 {rep['candidates']} 只（今天在 NYSE/NASDAQ 上市、可融资可做空可碎股的股票）。"
           "动态股票池每月第一个交易日按过去 63 天平均成交额重选（只用当时已知的数据，股价 > $10、上市满一年），"
           "外加 16 只大盘和行业 ETF。成交额用 IEX 数据估算。含 5 个基点交易成本。", ""]
    for exp, r in rep["results"].items():
        out += [f"## {exp}（{r['period']}，参数 {r['params']}）", "",
                "| 股票池 | 总收益 | 年化 | 夏普 | 最大回撤 |", "|---|---|---|---|---|"]
        for label, s in r["variants"].items():
            cagr = f"{s['cagr']:+.1%}" if s["cagr"] is not None else "—"
            out.append(f"| {label} | {s['total_return']:+.1%} | {cagr} | {s['sharpe']:.2f} | {s['max_drawdown']:.1%} |")
        years = sorted(next(iter(r["variants"].values()))["by_year"])
        out += ["", "| 股票池 | " + " | ".join(years) + " |", "|---|" + "---|" * len(years)]
        for label, s in r["variants"].items():
            out.append(f"| {label} | " + " | ".join(f"{s['by_year'].get(y, 0):+.1%}" for y in years) + " |")
        out += ["", "持有天数最多的标的："]
        for label, s in r["variants"].items():
            out.append(f"- {label}：{', '.join(s['most_held'])}")
        out.append("")
    return "\n".join(out)


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca(), root)
    out = root / "research"
    out.mkdir(exist_ok=True)
    (out / f"{rep['generated']}-universe-backtest.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False) + "\n")
    (out / f"{rep['generated']}-universe-backtest.md").write_text(markdown(rep))
    print(markdown(rep))


if __name__ == "__main__":
    sys.exit(main())
