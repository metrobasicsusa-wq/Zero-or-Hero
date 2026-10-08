"""Research: a stock jumped a lot today -- is it still worth chasing tomorrow?

On daily bars (SIP, split- and dividend-adjusted) since 2020 for the 100 names in data/universe.json,
for every day a stock closed up 5% / 10% / 20% (and two 5% days in a row), what buying it then did:
  at that day's close, held to the next close;
  at the next open (the usual "chase the next morning"), held to the next close / 5 / 20 sessions;
and the next morning's gap; each against every stock-day (the base rate), with standard errors.
Caveat: the names are today's most-traded, chosen with hindsight, which flatters every row equally
(the base too); compare rows with the base, not with zero. Record only. Run: python -m hero.research_chase
"""

from __future__ import annotations

import json
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

START = "2020-01-01"
FEED = "sip"
JUMPS = (0.05, 0.10, 0.20)
METRICS = ("gap", "close_next", "open_close", "open_5", "open_20")
METRIC_ZH = {"gap": "第二天跳空", "close_next": "当天收盘买、拿到第二天收盘", "open_close": "第二天开盘追、当天收盘卖",
             "open_5": "第二天开盘追、拿 5 天", "open_20": "第二天开盘追、拿 20 天"}


def bars_for(client, syms: list[str], end: str, adjustment: str = "all") -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for i in range(0, len(syms), 50):
        params = {"symbols": ",".join(syms[i:i + 50]), "timeframe": "1Day", "start": START, "end": end,
                  "adjustment": adjustment, "feed": FEED, "limit": 10000}
        while True:
            page = client._d("/v2/stocks/bars", params)
            for s, bs in (page.get("bars") or {}).items():
                out.setdefault(s, []).extend(bs)
            if not page.get("next_page_token"):
                break
            params["page_token"] = page["next_page_token"]
    return out


def events(bars: list[dict]) -> list[dict]:
    """Every day with 20 sessions after it: its own change, the day before's, and what followed."""
    o = [float(b["o"]) for b in bars]
    c = [float(b["c"]) for b in bars]
    rows = []
    for i in range(2, len(bars) - 20):
        rows.append({"day": bars[i]["t"][:10], "ret": c[i] / c[i - 1] - 1, "prev": c[i - 1] / c[i - 2] - 1,
                     "gap": o[i + 1] / c[i] - 1, "close_next": c[i + 1] / c[i] - 1, "open_close": c[i + 1] / o[i + 1] - 1,
                     "open_5": c[i + 5] / o[i + 1] - 1, "open_20": c[i + 20] / o[i + 1] - 1})
    return rows


def summary(rows: list[dict]) -> dict:
    n = len(rows)
    out = {"n": n}
    if not n:
        return out
    for m in METRICS:
        xs = [r[m] for r in rows]
        out[m] = round(statistics.mean(xs), 4)
        out[m + "_med"] = round(statistics.median(xs), 4)
        out[m + "_up"] = round(sum(x > 0 for x in xs) / n, 3)
        out[m + "_se"] = round(statistics.stdev(xs) / n ** 0.5, 4) if n > 1 else None
    return out


def groups(rows: list[dict]) -> dict[str, dict]:
    out = {"所有股票日（基准）": summary(rows)}
    for j in JUMPS:
        out[f"当天涨 {j:.0%} 以上"] = summary([r for r in rows if r["ret"] >= j])
    out["连续两天各涨 5% 以上"] = summary([r for r in rows if r["ret"] >= 0.05 and r["prev"] >= 0.05])
    out["当天涨 10% 以上、第二天高开 3% 以上"] = summary([r for r in rows if r["ret"] >= 0.10 and r["gap"] >= 0.03])
    out["当天涨 10% 以上、第二天低开"] = summary([r for r in rows if r["ret"] >= 0.10 and r["gap"] < 0])
    out["当天跌 10% 以上（对照）"] = summary([r for r in rows if r["ret"] <= -0.10])
    return out


def run(client) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    syms = list(json.loads((Path(__file__).resolve().parent.parent / "data" / "universe.json").read_text()).get("stocks") or [])
    bars = bars_for(client, syms, f"{end}T23:00:00Z")
    rows = []
    for s, bs in bars.items():
        bs = [b for b in bs if b["t"][:10] <= end]
        rows += [{"symbol": s, **r} for r in events(bs)]
    top = sorted((r for r in rows if r["ret"] >= 0.10 and r["day"] >= "2026-01-01"), key=lambda r: -r["ret"])[:15]
    return {"generated": date.today().isoformat(), "start": START, "end": end, "names": len(bars),
            "all": groups(rows), "since2024": groups([r for r in rows if r["day"] >= "2024-01-01"]),
            "recent_big": [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()} for r in top]}


def markdown(rep: dict) -> str:
    out = [f"# 大涨后第二天还能追吗 {rep['generated']}", "",
           f"{rep['start']} 至 {rep['end']}，data/universe.json 里的 {rep['names']} 只股票，日线（SIP，已按拆股和分红调整）。"
           "按当天涨幅分组，看第二天跳空、当天收盘买 / 第二天开盘追之后的涨跌。每格「平均（中位数），上涨比例」。"
           "注意：这些股票是现在最热门的，带着事后挑选的偏差，所有行（包括基准）都被抬高了；要和「基准」行比，不要和 0 比。只研究，不改交易。", ""]
    for key, title in (("all", "2020 年以来"), ("since2024", "2024 年以来")):
        out += [f"## {title}", "", "| 情况 | 次数 | " + " | ".join(METRIC_ZH[m] for m in METRICS) + " |", "|---|---|" + "---|" * len(METRICS)]
        for g, s in rep[key].items():
            if not s["n"]:
                continue
            cells = [f"{s[m]:+.2%}（{s[m + '_med']:+.2%}），{s[m + '_up']:.0%}" for m in METRICS]
            out.append(f"| {g} | {s['n']} | " + " | ".join(cells) + " |")
        out += ["", "误差（平均值的标准误）：" + "；".join(
            f"{g} 开盘追拿 5 天 ±{s['open_5_se']:.2%}" for g, s in rep[key].items() if s.get("open_5_se") is not None), ""]
    out += ["## 2026 年涨 10% 以上的 15 次", "", "| 日期 | 股票 | 当天 | 第二天跳空 | 开盘追当天 | 拿 5 天 | 拿 20 天 |", "|---|---|---|---|---|---|---|"]
    for r in rep["recent_big"]:
        out.append(f"| {r['day']} | {r['symbol']} | {r['ret']:+.1%} | {r['gap']:+.1%} | {r['open_close']:+.1%} | {r['open_5']:+.1%} | {r['open_20']:+.1%} |")
    return "\n".join(out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca())
    (root / "research" / f"{rep['generated']}-chase.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    (root / "research" / f"{rep['generated']}-chase.md").write_text(markdown(rep))
    print(markdown(rep))


if __name__ == "__main__":
    sys.exit(main())
