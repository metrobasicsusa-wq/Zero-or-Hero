"""Is the earnings night worth more than an ordinary night? (claude-c, 10-19)

The 10-19 rule change (earnings sleeves buy shares / 2x funds at the close before a report, sell at the next
open) leaned on a backtest over research/2026-10-02-earnings-reactions.json: each week, of the names reporting
that week, the one with the strongest 126-session momentum, bought at the close before its reaction and sold at
the reaction day's open. claude-c asked for the control: if the same picks on ordinary nights make about as much,
the money comes from momentum and the overnight drift (and survivorship: 98 of today's large caps), and the
report only adds gap risk.

Groups, all close -> next open on Yahoo's split-adjusted daily bars (dividends ignored; they cost every group):
  earnings   - the weekly pick on its earnings night (as in the 10-19 backtest);
  same -1w   - the same stock, entry five sessions earlier (no report within a session either side);
  same +1w   - the same stock, entry five sessions later;
  momentum   - on the same entry day, the strongest 126-session momentum name of the 98 with no report near;
  all nights - every name, every ordinary night (the overnight drift itself).
Each weekly group is also compounded from $500 in shares and in a 2x fund (twice the gap, a one-night reset).
Run: python -m hero.research_earnings_control
"""

from __future__ import annotations

import json
import statistics
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from hero import robust

ROOT = Path(__file__).resolve().parent.parent
EVENTS = ROOT / "research" / "2026-10-02-earnings-reactions.json"
OUT = ROOT / "research"
SINCE = ("2022-01-01", "2024-02-01")
SHIFT = 5          # sessions: "one week earlier / later"
MOM = 126


def yahoo(sym: str, start: str = "2021-01-01") -> dict[str, dict]:
    """day -> {o, c} from Yahoo's chart API (split-adjusted)."""
    p1 = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp())
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1={p1}"
           f"&period2={int(datetime.now().timestamp())}&interval=1d")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    res = json.load(urllib.request.urlopen(req, timeout=30))["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    day = lambda t: (datetime.fromtimestamp(t, timezone.utc) - timedelta(hours=5)).date().isoformat()
    return {day(t): {"o": o, "c": c} for t, o, c in zip(res["timestamp"], q["open"], q["close"]) if o and c}


def blocked(events: list[dict], days: list[str]) -> set[str]:
    """Entry days near a report: the night before the reaction, the reaction day's night and the night before
    that (so a control never holds through a report or its first follow-through)."""
    idx = {d: i for i, d in enumerate(days)}
    out = set()
    for e in events:
        i = idx.get(e["reaction_day"])
        if i is None:
            continue
        for k in (i - 2, i - 1, i):
            if 0 <= k < len(days):
                out.add(days[k])
    return out


def overnight(bars: dict[str, dict], days: list[str], entry: str) -> float | None:
    """Close on `entry` to the next session's open."""
    i = days.index(entry) if entry in bars else -1
    if i < 0 or i + 1 >= len(days):
        return None
    return bars[days[i + 1]]["o"] / bars[entry]["c"] - 1


def momentum(bars: dict[str, dict], days: list[str], entry: str) -> float | None:
    i = days.index(entry) if entry in bars else -1
    if i < MOM:
        return None
    return bars[entry]["c"] / bars[days[i - MOM]]["c"] - 1


def weekly_picks(events: list[dict], bars: dict[str, dict[str, dict]]) -> list[dict]:
    """Each ISO week, the reporting name with the strongest momentum (the events' own mom126), its entry day
    (the session before the reaction) and its earnings-night return."""
    by_week: dict[str, list[dict]] = {}
    for e in events:
        if e.get("mom126") is None or e.get("timing") not in ("after close", "before open") or e["symbol"] not in bars:
            continue
        y, w, _ = date.fromisoformat(e["reaction_day"]).isocalendar()
        by_week.setdefault(f"{y}-W{w:02d}", []).append(e)
    out = []
    for wk in sorted(by_week):
        e = max(by_week[wk], key=lambda x: x["mom126"])
        b = bars[e["symbol"]]
        days = sorted(b)
        if e["reaction_day"] not in b:
            continue
        i = days.index(e["reaction_day"])
        if i == 0:
            continue
        entry = days[i - 1]
        out.append({"week": wk, "symbol": e["symbol"], "entry": entry, "ret": overnight(b, days, entry)})
    return [p for p in out if p["ret"] is not None]


def same_name(picks: list[dict], bars: dict[str, dict[str, dict]], block: dict[str, set], shift: int) -> list[dict]:
    out = []
    for p in picks:
        b = bars[p["symbol"]]
        days = sorted(b)
        i = days.index(p["entry"]) + shift
        if not (0 <= i < len(days) - 1) or days[i] in block.get(p["symbol"], set()):
            continue
        r = overnight(b, days, days[i])
        if r is not None:
            out.append({**p, "entry": days[i], "ret": r})
    return out


def momentum_pick(picks: list[dict], bars: dict[str, dict[str, dict]], block: dict[str, set]) -> list[dict]:
    """On each pick's entry day, the strongest-momentum name with no report near, held the same night."""
    out = []
    cache = {s: sorted(b) for s, b in bars.items()}
    for p in picks:
        best = None
        for s, b in bars.items():
            if p["entry"] not in b or p["entry"] in block.get(s, set()):
                continue
            m = momentum(b, cache[s], p["entry"])
            if m is not None and (best is None or m > best[1]):
                best = (s, m)
        if best:
            r = overnight(bars[best[0]], cache[best[0]], p["entry"])
            if r is not None:
                out.append({**p, "symbol": best[0], "ret": r})
    return out


def all_nights(bars: dict[str, dict[str, dict]], block: dict[str, set], since: str) -> list[tuple[str, float]]:
    out = []
    for s, b in bars.items():
        days = sorted(b)
        for i in range(len(days) - 1):
            d = days[i]
            if d >= since and d not in block.get(s, set()):
                out.append((d, b[days[i + 1]]["o"] / b[d]["c"] - 1))
    return out


def compound(rets: list[float], lev: float, start: float = 500.0) -> tuple[float, float]:
    """End value and worst drawdown of a ledger that puts everything in each trade (lev x the move, floor -100%)."""
    v = peak = start
    dd = 0.0
    for r in rets:
        v *= 1 + max(lev * r, -1.0)
        peak = max(peak, v)
        dd = max(dd, 1 - v / peak)
    return v, dd


def run(events: list[dict], bars: dict[str, dict[str, dict]]) -> tuple[dict, str]:
    block = {}
    for s, b in bars.items():
        days = sorted(b)
        block[s] = blocked([e for e in events if e["symbol"] == s], days)
    picks = weekly_picks(events, bars)
    groups_all = {"财报那一晚（10-19 回测的选股）": picks, "同一只、早一周的普通晚上": same_name(picks, bars, block, -SHIFT),
                  "同一只、晚一周的普通晚上": same_name(picks, bars, block, SHIFT), "同一天动量最强、近期没有财报的": momentum_pick(picks, bars, block)}
    rep, md = {}, []
    for since in SINCE:
        g = {k: [p for p in v if p["entry"] >= since] for k, v in groups_all.items()}
        md += ["", f"## {since} 起", "", "| 组 | 笔数 | 平均 | 中位数 | 涨的比例 | 正股 $500 → | 最大回撤 | 两倍 $500 → | 最大回撤 |",
               "|---|---|---|---|---|---|---|---|---|"]
        rows = {}
        for name, ps in g.items():
            rs = [p["ret"] for p in ps]
            if not rs:
                continue
            v1, d1 = compound(rs, 1)
            v2, d2 = compound(rs, 2)
            md.append(f"| {name} | {len(rs)} | {statistics.mean(rs):+.2%} | {statistics.median(rs):+.2%} | "
                      f"{sum(r > 0 for r in rs) / len(rs):.0%} | ${v1:,.0f} | {d1:.0%} | ${v2:,.0f} | {d2:.0%} |")
            rows[name] = robust.summary([(p["entry"], p["ret"]) for p in ps])
            rep[f"{since}|{name}"] = {"n": len(rs), "mean": statistics.mean(rs), "median": statistics.median(rs),
                                      "up": sum(r > 0 for r in rs) / len(rs), "x1": v1, "dd1": d1, "x2": v2, "dd2": d2}
        nights = all_nights(bars, block, since)
        if nights:
            vs = [r for _, r in nights]
            md.append(f"| 全部 98 只的普通晚上（每晚平均） | {len(vs)} | {statistics.mean(vs):+.2%} | {statistics.median(vs):+.2%} | "
                      f"{sum(r > 0 for r in vs) / len(vs):.0%} | — | — | — | — |")
            rep[f"{since}|all nights"] = {"n": len(vs), "mean": statistics.mean(vs), "median": statistics.median(vs)}
        md += ["", robust.table(rows, f"稳健性检查（{since} 起，每笔正股收益；robust 的「多付成本」是每边 5%，对正股过重，只看方向）")]
    return rep, "\n".join(md)


def main() -> None:
    events = json.loads(EVENTS.read_text())["events"]
    syms = sorted({e["symbol"] for e in events})
    bars = {}
    for s in syms:
        try:
            bars[s] = yahoo(s)
        except Exception as e:
            print("skip", s, e)
        time.sleep(0.3)
    rep, body = run(events, bars)
    head = ["# 财报那一晚 vs 普通晚上（claude-c 10-19 的对照）", "",
            f"research/2026-10-02-earnings-reactions.json 的 {len(bars)} 只（今天的大票，幸存者偏差大），Yahoo 拆股调整日线，"
            "收盘买、下一个交易日开盘卖，不算分红和买卖价差。每周一笔：本周有财报里 126 日动量最强的一只（10-19 回测的选股）；"
            "对照用同一只早一周 / 晚一周的普通晚上（前后一个交易日内没有财报），和同一天 98 只里动量最强、近期没有财报的那只。"
            "两倍按一晚 2 倍算。只研究，不改交易。"]
    md = "\n".join(head) + "\n" + body + "\n"
    stamp = date.today().isoformat()
    (OUT / f"{stamp}-earnings-overnight-control.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    (OUT / f"{stamp}-earnings-overnight-control.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
