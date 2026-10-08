"""Research: after SPY / QQQ close up several days in a row, does a pullback follow?

Daily closes (SIP, split- and dividend-adjusted) since 2016, up to yesterday. A streak is the number of
consecutive closes above the prior close (and, the other way, below it). For each streak length, from
that day's close:
  next day:   the chance it closes down, and the average change;
  next 5 days: the average change, and the chance the lowest low in those 5 days is 1% / 2% under that close;
each against every day (the base rate), so "a pullback is due" is measured, not assumed. Also the
streak the last close ended on. Record only. Run: python -m hero.research_streak
"""

from __future__ import annotations

import json
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

START = "2016-01-01"
NAMES = ("SPY", "QQQ")
LENGTHS = (1, 2, 3, 4, 5, 6, 7)  # 7 means 7 or more
DIPS = (0.01, 0.02)
FEED = "sip"


def streaks(closes: list[float]) -> list[int]:
    """+n: the n-th straight up close, -n: the n-th straight down close, 0: unchanged / first day."""
    out = [0]
    for i in range(1, len(closes)):
        prev = out[-1]
        if closes[i] > closes[i - 1]:
            out.append(prev + 1 if prev > 0 else 1)
        elif closes[i] < closes[i - 1]:
            out.append(prev - 1 if prev < 0 else -1)
        else:
            out.append(0)
    return out


def outcomes(bars: list[dict]) -> list[dict]:
    """Per day with 5 days after it: the streak and what followed."""
    c = [float(b["c"]) for b in bars]
    lo = [float(b["l"]) for b in bars]
    st = streaks(c)
    rows = []
    for i in range(len(bars) - 5):
        low5 = min(lo[i + 1:i + 6])
        rows.append({"day": bars[i]["t"][:10], "streak": st[i], "next": c[i + 1] / c[i] - 1, "five": c[i + 5] / c[i] - 1,
                     **{f"dip{d}": low5 <= c[i] * (1 - d) for d in DIPS}})
    return rows


def summary(rows: list[dict]) -> dict:
    n = len(rows)
    return {"n": n, "down_next": round(sum(r["next"] < 0 for r in rows) / n, 3),
            "next": round(statistics.mean(r["next"] for r in rows), 5),
            "five": round(statistics.mean(r["five"] for r in rows), 5),
            "five_se": round(statistics.stdev(r["five"] for r in rows) / n ** 0.5, 5) if n > 1 else None,
            **{f"dip{d}": round(sum(r[f"dip{d}"] for r in rows) / n, 3) for d in DIPS}}


def groups(rows: list[dict]) -> dict[str, dict]:
    out = {"全部日子": summary(rows)}
    for sign, word in ((1, "连涨"), (-1, "连跌")):
        for k in LENGTHS:
            sub = [r for r in rows if (r["streak"] * sign >= k if k == LENGTHS[-1] else r["streak"] * sign == k)]
            if sub:
                out[f"{word} {k}{' 天以上' if k == LENGTHS[-1] else ' 天'}"] = summary(sub)
    return out


def daily(client, sym: str, end: str) -> list[dict]:
    """Daily bars up to `end`: SIP refuses the last 15 minutes, so the request must stop before today."""
    params = {"symbols": sym, "timeframe": "1Day", "start": START, "end": end, "adjustment": "all", "feed": FEED, "limit": 10000}
    out: list[dict] = []
    while True:
        page = client._d("/v2/stocks/bars", params)
        out.extend((page.get("bars") or {}).get(sym, []))
        if not page.get("next_page_token"):
            return out
        params["page_token"] = page["next_page_token"]


def run(client) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    out = {"generated": date.today().isoformat(), "start": START, "end": end, "names": {}}
    for und in NAMES:
        bars = [b for b in daily(client, und, f"{end}T23:00:00Z") if b["t"][:10] <= end]  # a date alone counts as end of day
        c = [float(b["c"]) for b in bars]
        rows = outcomes(bars)
        out["names"][und] = {"last_day": bars[-1]["t"][:10] if bars else None, "last_streak": streaks(c)[-1] if c else 0,
                             "all": groups(rows), "since2024": groups([r for r in rows if r["day"] >= "2024-01-01"])}
    return out


def markdown(rep: dict) -> str:
    out = [f"# SPY / QQQ 连涨几天后会不会回调 {rep['generated']}", "",
           f"{rep['start']} 至 {rep['end']}，日线收盘（SIP，已按拆股和分红调整）。连涨 = 连续几天收盘高于前一天。"
           "从连涨（或连跌）那天的收盘算起：第二天收跌的比例和平均涨跌；之后 5 天的平均涨跌，以及 5 天内最低价跌破那天收盘 1% / 2% 的比例。"
           "和「全部日子」比，才知道是不是真的更容易回调。只研究，不改交易。", ""]
    for und, x in rep["names"].items():
        s = x["last_streak"]
        now = f"连涨 {s} 天" if s > 0 else (f"连跌 {-s} 天" if s < 0 else "持平")
        for key, title in (("all", "2016 年以来"), ("since2024", "2024 年以来")):
            out += [f"## {und}，{title}（截至 {x['last_day']}：{now}）", "",
                    "| 情况 | 次数 | 第二天收跌 | 第二天平均 | 5 天平均（± 误差） | 5 天内跌破 1% | 5 天内跌破 2% |",
                    "|---|---|---|---|---|---|---|"]
            for g, r in x[key].items():
                se = f" ± {r['five_se']:.2%}" if r["five_se"] is not None else ""
                out.append(f"| {g} | {r['n']} | {r['down_next']:.0%} | {r['next']:+.2%} | {r['five']:+.2%}{se} | {r['dip0.01']:.0%} | {r['dip0.02']:.0%} |")
            out.append("")
    return "\n".join(out)


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca())
    (root / "research" / f"{rep['generated']}-streak.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    (root / "research" / f"{rep['generated']}-streak.md").write_text(markdown(rep))
    print(markdown(rep))


if __name__ == "__main__":
    sys.exit(main())
