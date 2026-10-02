"""Research step 1: how big are earnings reactions, and how often do they blow past what the
options market expected?

Earnings releases come from SEC EDGAR: 8-K filings with item 2.02 (Results of Operations), whose
acceptance time says whether the release was before the open (the stock reacts that day) or
after the close (it reacts the next trading day). Foreign issuers file 6-Ks instead and are
missing. The reaction is close-to-close over the reaction day; the gap is the open against the
previous close.

The implied move is the at-the-money straddle (call + put) on the close before the reaction, for
the first expiry on or after the reaction day, divided by the stock price: roughly the move the
options market priced in. Alpaca has option history from early 2024, so earlier events have
reactions but no implied move. Prices for the straddle are unadjusted (strikes are not split-
adjusted). Record only. Run: python -m hero.research_earnings
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from hero.indicators import momentum

ET = ZoneInfo("America/New_York")
# SEC's fair-access policy requires a real contact address in the User-Agent; it comes from the
# SEC_CONTACT_EMAIL secret and is never written to the repository.
SEC_HEADERS: dict[str, str] = {}


def sec_headers() -> dict[str, str]:
    import os
    email = os.environ.get("SEC_CONTACT_EMAIL", "").strip()
    if not email:
        raise RuntimeError("SEC_CONTACT_EMAIL is not set; SEC rejects requests without a real contact address")
    return {"User-Agent": f"Zero-or-Hero research {email}"}
YEARS = 5
OPTIONS_FROM = "2024-02-01"
BIG = 2.0  # "blew past expectations": realized move at least this many times the implied move


def cik_map() -> dict[str, int]:
    SEC_HEADERS.update(sec_headers())
    r = requests.get("https://www.sec.gov/files/company_tickers.json", headers=SEC_HEADERS, timeout=60)
    r.raise_for_status()
    return {v["ticker"]: int(v["cik_str"]) for v in r.json().values()}


def _sec(url: str) -> dict:
    r = requests.get(url, headers=SEC_HEADERS, timeout=60)
    r.raise_for_status()
    time.sleep(0.15)  # SEC asks for at most 10 requests per second
    return r.json()


def _item_202(block: dict, since: str) -> list[datetime]:
    out = []
    for form, filed, accepted, items in zip(block["form"], block["filingDate"], block["acceptanceDateTime"],
                                            block.get("items") or [""] * len(block["form"])):
        if form == "8-K" and "2.02" in (items or "") and filed >= since:
            out.append(datetime.fromisoformat(accepted.replace("Z", "+00:00")).astimezone(ET))
    return out


def releases(cik: int, since: str) -> list[datetime]:
    """Acceptance times (ET) of 8-K filings carrying item 2.02, following older filing pages
    when the recent list does not reach back far enough (frequent filers such as banks)."""
    data = _sec(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
    out = _item_202(data["filings"]["recent"], since)
    for f in data["filings"].get("files", []):
        if f.get("filingTo", "9999") < since:
            continue
        out += _item_202(_sec(f"https://data.sec.gov/submissions/{f['name']}"), since)
    return sorted(set(out))


def reaction_index(dates: list[str], accepted: datetime) -> int | None:
    """Index of the trading day that first reacts to a release accepted at this time."""
    day = accepted.date().isoformat()
    after_close = accepted.hour >= 16
    for i, d in enumerate(dates):
        if d > day or (d == day and not after_close):
            return i
    return None


def straddle(client, und: str, spot: float, on: str, react: str) -> dict | None:
    """ATM straddle on day `on`, first expiry on/after `react`."""
    try:
        cs = client.option_contracts(und, status="inactive", expiration_date_gte=react,
                                     expiration_date_lte=(date.fromisoformat(react) + timedelta(days=10)).isoformat(),
                                     strike_price_gte=f"{spot * 0.9:.2f}", strike_price_lte=f"{spot * 1.1:.2f}")
        if not cs:
            cs = client.option_contracts(und, status="active", expiration_date_gte=react,
                                         expiration_date_lte=(date.fromisoformat(react) + timedelta(days=10)).isoformat(),
                                         strike_price_gte=f"{spot * 0.9:.2f}", strike_price_lte=f"{spot * 1.1:.2f}")
    except Exception:
        return None
    if not cs:
        return None
    exp = min(c["expiration_date"] for c in cs)
    cs = [c for c in cs if c["expiration_date"] == exp]
    k = min({float(c["strike_price"]) for c in cs}, key=lambda x: abs(x - spot))
    legs = {c["type"]: c["symbol"] for c in cs if float(c["strike_price"]) == k}
    if set(legs) != {"call", "put"}:
        return None
    try:
        bars = client.option_bars(list(legs.values()), on, on)
    except Exception:
        return None
    px = {t: (bars.get(sym) or [{}])[-1].get("c") for t, sym in legs.items()}
    if not px["call"] or not px["put"]:
        return None
    return {"expiry": exp, "strike": k, "call": px["call"], "put": px["put"],
            "implied_move": round((px["call"] + px["put"]) / spot, 4)}


def run(client, root: Path) -> dict:
    from hero import universe
    syms = set(universe.load(root / "data" / "universe.json")["stocks"])
    for name in ("strategy.json", "s500.json"):
        syms |= set(json.loads((root / "config" / name).read_text())["universe"])
    since = (date.today() - timedelta(days=365 * YEARS)).isoformat()
    ciks = cik_map()
    adj, raw = {}, {}
    syms = sorted(syms)
    for i in range(0, len(syms), 100):
        adj.update(client.daily_bars(syms[i:i + 100], since))
        raw.update(client.daily_bars(syms[i:i + 100], since, adjustment="raw"))
    events, missing = [], []
    for s in syms:
        if s not in ciks or s not in adj:
            missing.append(s)  # ETFs and foreign filers have no 8-K earnings releases
            continue
        try:
            rel = releases(ciks[s], since)
        except Exception:
            missing.append(s)
            continue
        if not rel:
            missing.append(s)
            continue
        bars = adj[s]
        dates = [b["t"][:10] for b in bars]
        closes = [float(b["c"]) for b in bars]
        rawc = {b["t"][:10]: float(b["c"]) for b in raw.get(s, [])}
        avg_hist = []
        for t in rel:
            i = reaction_index(dates, t)
            if i is None or i < 1:
                continue
            move = closes[i] / closes[i - 1] - 1
            gap = float(bars[i]["o"]) / closes[i - 1] - 1
            ev = {"symbol": s, "accepted": t.isoformat(timespec="minutes"), "reaction_day": dates[i],
                  "timing": "after close" if t.hour >= 16 else ("before open" if t.hour < 9 or (t.hour == 9 and t.minute < 30) else "intraday"),
                  "move": round(move, 4), "gap": round(gap, 4),
                  "mom126": round(momentum(closes[: i], 126), 4) if i > 127 else None,
                  "prior_avg_abs_move": round(statistics.mean(avg_hist), 4) if avg_hist else None}
            if dates[i] >= OPTIONS_FROM and dates[i - 1] in rawc:
                st = straddle(client, s, rawc[dates[i - 1]], dates[i - 1], dates[i])
                if st:
                    ev.update(st)
                    ev["ratio"] = round(abs(move) / st["implied_move"], 2) if st["implied_move"] else None
                    if ev.get("prior_avg_abs_move"):
                        ev["cheapness"] = round(st["implied_move"] / ev["prior_avg_abs_move"], 2)
                time.sleep(0.2)
            avg_hist.append(abs(move))
            events.append(ev)
    return {"generated": date.today().isoformat(), "since": since, "symbols": len(syms),
            "missing": sorted(missing), "events": events}


def _pct(x):
    return f"{x:+.1%}" if x is not None else "—"


def summarize(rep: dict) -> dict:
    ev = rep["events"]
    with_iv = [e for e in ev if e.get("ratio") is not None]
    big = [e for e in with_iv if e["ratio"] >= BIG]
    big_up = [e for e in big if e["move"] > 0]
    out = {"events": len(ev), "with_implied": len(with_iv), "big": len(big), "big_up": len(big_up),
           "abs_move_median": statistics.median(abs(e["move"]) for e in ev) if ev else None,
           "abs_move_p90": sorted(abs(e["move"]) for e in ev)[int(len(ev) * 0.9)] if ev else None,
           "ratio_median": statistics.median(e["ratio"] for e in with_iv) if with_iv else None,
           "share_beyond_implied": sum(e["ratio"] > 1 for e in with_iv) / len(with_iv) if with_iv else None}

    def rate(rows):
        rows = [e for e in rows if e.get("ratio") is not None]
        return (sum(e["ratio"] >= BIG and e["move"] > 0 for e in rows) / len(rows), len(rows)) if rows else (None, 0)
    # Do the two candidate signals separate the big up-moves from the rest?
    for key, label in (("mom126", "动量"), ("cheapness", "期权便宜度")):
        rows = sorted([e for e in with_iv if e.get(key) is not None], key=lambda e: e[key])
        n = len(rows)
        if n >= 30:
            thirds = [rows[: n // 3], rows[n // 3: 2 * n // 3], rows[2 * n // 3:]]
            out[f"by_{key}"] = [{"range": f"{t[0][key]:+.2f} … {t[-1][key]:+.2f}", "rate": rate(t)[0], "n": rate(t)[1]}
                                for t in thirds]
    out["top_up"] = sorted([e for e in with_iv if e["move"] > 0], key=lambda e: -e["ratio"])[:15]
    out["top_moves"] = sorted(ev, key=lambda e: -abs(e["move"]))[:15]
    return out


def markdown(rep: dict, s: dict) -> str:
    out = [f"# 财报反应统计（第 1 步）{rep['generated']}", "",
           f"标的 {rep['symbols']} 只，自 {rep['since']}。财报时间来自 SEC 8-K（项目 2.02）的提交时间；"
           f"预期波动 = 反应日前一天收盘的平值跨式（看涨 + 看跌）÷ 股价，{OPTIONS_FROM} 之后才有期权数据。只研究，不改交易。", "",
           f"- 财报事件 {s['events']} 次，其中有期权预期数据的 {s['with_implied']} 次",
           f"- 财报日涨跌幅（绝对值）中位数 {s['abs_move_median']:.1%}，最大的 10% 超过 {s['abs_move_p90']:.1%}"
           if s["abs_move_median"] is not None else "- 无事件",
           f"- 实际波动 ÷ 期权预期：中位数 {s['ratio_median']:.2f}；超过预期的占 {s['share_beyond_implied']:.0%}"
           if s["ratio_median"] is not None else "- 无期权数据",
           f"- **超过预期 {BIG:g} 倍以上的 {s['big']} 次，其中上涨 {s['big_up']} 次**（彩票型看涨期权可能大赚的情形）", ""]
    for key, label in (("mom126", "按财报前 126 天动量分三组"), ("cheapness", "按期权便宜度（预期波动 ÷ 该股历史平均财报波动）分三组")):
        if f"by_{key}" in s:
            out += [f"## {label}：「上涨且超预期 {BIG:g} 倍」的比例", "", "| 组 | 区间 | 比例 | 样本 |", "|---|---|---|---|"]
            for i, g in enumerate(s[f"by_{key}"], 1):
                out.append(f"| {i} | {g['range']} | {_pct(g['rate']) if g['rate'] is not None else '—'} | {g['n']} |")
            out.append("")
    out += ["## 超预期最多的上涨（有期权数据）", "", "| 标的 | 反应日 | 时间 | 涨跌 | 期权预期 | 倍数 | 财报前动量 |", "|---|---|---|---|---|---|---|"]
    for e in s["top_up"]:
        out.append(f"| {e['symbol']} | {e['reaction_day']} | {e['timing']} | {_pct(e['move'])} | ±{e['implied_move']:.1%} | "
                   f"{e['ratio']:.1f}× | {_pct(e.get('mom126'))} |")
    out += ["", "## 涨跌幅最大的财报日（全部年份）", "", "| 标的 | 反应日 | 涨跌 | 开盘跳空 |", "|---|---|---|---|"]
    for e in s["top_moves"]:
        out.append(f"| {e['symbol']} | {e['reaction_day']} | {_pct(e['move'])} | {_pct(e['gap'])} |")
    out += ["", f"缺失（ETF、外国公司不交 8-K、或无数据）：{', '.join(rep['missing'])}", ""]
    return "\n".join(out)


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca(), root)
    s = summarize(rep)
    out = root / "research"
    out.mkdir(exist_ok=True)
    (out / f"{rep['generated']}-earnings-reactions.json").write_text(
        json.dumps({**rep, "summary": s}, indent=1, ensure_ascii=False) + "\n")
    (out / f"{rep['generated']}-earnings-reactions.md").write_text(markdown(rep, s))
    print(markdown(rep, s))


if __name__ == "__main__":
    sys.exit(main())
