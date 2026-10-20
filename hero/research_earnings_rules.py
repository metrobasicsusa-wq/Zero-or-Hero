"""Would simple before-the-report rules have called last quarter's earnings opens? (owner, 10-20: backtest last quarter first.)

The 10-20 judgments leaned on a few rules of thumb (peers that week opened up -> up; a big run into the report -> down;
"expectations already low" after a fall -> up; otherwise up). The reading-the-news part cannot be replayed without
seeing the outcome, but these rules can. Over research/2026-10-02-earnings-reactions.json (98 large caps, every report
since 2021-10, the open against the last price before the report), each rule is scored on 2021-10..2026-06 and checked
on the last quarter (2026-07-01..2026-10-02):
  - how often a report opened up under each before-the-report condition (run into the report, 126-day momentum,
    the company's own last reactions, same-group reports earlier that week, SPY's last week);
  - complete strategies that call every report, scored by hit rate and by the open gap per $1 in the called direction
    (hero.robust: without the best three, median, by year, the last three months).
Daily closes from Yahoo (split-adjusted). Record only. Run: python -m hero.research_earnings_rules
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from hero import robust
from hero.research_earnings_control import yahoo
from hero.research_ideas import save

ROOT = Path(__file__).resolve().parent.parent
EVENTS = ROOT / "research" / "2026-10-02-earnings-reactions.json"
TEST = "2026-07-01"
PEER_DAYS = 7
GROUPS = {
    "芯片": "ADI ALAB AMAT AMD AVGO COHR CRDO INTC KLAC LITE LRCX MRVL MU NVDA QCOM SNDK STX TXN WDC",
    "AI 硬件 / 算力": "ANET APH CSCO DELL HPE GLW VRT SMCI CRWV IREN",
    "软件": "ADBE APP CRM CRWD DDOG INTU NET NOW ORCL PANW PLTR SHOP SNOW IBM ACN",
    "大型互联网": "MSFT GOOGL META AMZN NFLX UBER BKNG WBD AAPL",
    "消费": "COST HD KO NKE PG WMT TSLA",
    "银行 / 支付": "BAC C GS JPM MA MS V WFC",
    "加密 / 互联网金融": "COIN HOOD SOFI MSTR",
    "医疗": "ABBV ABT BSX DHR ISRG JNJ LLY MDT MRNA PFE TMO UNH",
    "工业 / 航天": "BA CAT GE GEV BE FCX RKLB IONQ",
    "能源 / 公用 / 电信": "CVX XOM VLO NEE T VZ",
}
GROUP = {s: g for g, ss in GROUPS.items() for s in ss.split()}


def entry_day(days: list[str], e: dict) -> str | None:
    """The last session before the reaction: the report day for after-close reports, the session before for pre-market."""
    i = next((k for k, d in enumerate(days) if d >= e["reaction_day"]), None)
    return days[i - 1] if i else None


def features(events: list[dict], bars: dict[str, dict[str, dict]]) -> list[dict]:
    spy = bars.get("SPY", {})
    sdays = sorted(spy)
    by_sym: dict[str, list[dict]] = {}
    for e in sorted(events, key=lambda e: e["reaction_day"]):
        if e.get("gap") is not None:
            by_sym.setdefault(e["symbol"], []).append(e)
    out = []
    for sym, evs in by_sym.items():
        b = bars.get(sym, {})
        days = sorted(b)
        for i, e in enumerate(evs):
            d0 = entry_day(days, e)
            run20 = None
            if d0 and days.index(d0) >= 20:
                k = days.index(d0)
                run20 = b[d0]["c"] / b[days[k - 20]]["c"] - 1
            spy5 = None
            s0 = entry_day(sdays, e)
            if s0 and sdays.index(s0) >= 5:
                k = sdays.index(s0)
                spy5 = spy[s0]["c"] / spy[sdays[k - 5]]["c"] - 1
            prev = [x["gap"] for x in evs[max(0, i - 2):i]]
            out.append({"symbol": sym, "day": e["reaction_day"], "gap": e["gap"], "mom126": e.get("mom126"),
                        "implied": e.get("implied_move"), "prior": e.get("prior_avg_abs_move"), "run20": run20, "spy5": spy5,
                        "last": prev[-1] if prev else None, "last2": prev if len(prev) == 2 else None, "group": GROUP.get(sym)})
    # same-group reports that opened in the PEER_DAYS before (known by the time of the call)
    for f in out:
        lo = (date.fromisoformat(f["day"]) - timedelta(days=PEER_DAYS)).isoformat()
        peers = [g["gap"] for g in out if g["group"] == f["group"] and g["symbol"] != f["symbol"] and lo <= g["day"] < f["day"]]
        f["peers_n"] = len(peers)
        if len(peers) >= 2:
            up = sum(p > 0 for p in peers) / len(peers)
            f["peers"] = "up" if up >= 0.6 else "down" if up <= 0.4 else "mixed"
        else:
            f["peers"] = None
    return out


CONDITIONS = {
    "进财报前 20 天跌了 5% 以上": lambda f: f["run20"] is not None and f["run20"] <= -0.05,
    "进财报前 20 天 ±5% 以内": lambda f: f["run20"] is not None and -0.05 < f["run20"] < 0.05,
    "进财报前 20 天涨了 5% 以上": lambda f: f["run20"] is not None and f["run20"] >= 0.05,
    "进财报前 20 天涨了 8% 以上": lambda f: f["run20"] is not None and f["run20"] >= 0.08,
    "126 日动量为正": lambda f: f["mom126"] is not None and f["mom126"] > 0,
    "126 日动量为负": lambda f: f["mom126"] is not None and f["mom126"] <= 0,
    "上次财报开盘涨": lambda f: f["last"] is not None and f["last"] > 0,
    "上次财报开盘跌": lambda f: f["last"] is not None and f["last"] <= 0,
    "连续两次开盘跌": lambda f: f["last2"] is not None and all(x <= 0 for x in f["last2"]),
    "连续两次开盘涨": lambda f: f["last2"] is not None and all(x > 0 for x in f["last2"]),
    "同行这周先涨（≥60% 开盘涨）": lambda f: f["peers"] == "up",
    "同行这周先跌（≤40% 开盘涨）": lambda f: f["peers"] == "down",
    "同行这周有涨有跌": lambda f: f["peers"] == "mixed",
    "SPY 前 5 天涨": lambda f: f["spy5"] is not None and f["spy5"] > 0,
    "SPY 前 5 天跌": lambda f: f["spy5"] is not None and f["spy5"] <= 0,
}


def my_10_20(f: dict) -> bool:
    """The 10-20 rules of thumb, in the order they were used: peers that week, a big run in (-> down), else up."""
    if f["peers"] in ("up", "down"):
        return f["peers"] == "up"
    if f["run20"] is not None and f["run20"] >= 0.08:
        return False
    return True


STRATEGIES = {
    "全部判涨": lambda f: True,
    "10-20 的判法（同行 → 涨多了判跌 → 其余判涨）": my_10_20,
    "跟 126 日动量": lambda f: f["mom126"] is None or f["mom126"] > 0,
    "20 天跌了判涨、涨了判跌（低预期）": lambda f: f["run20"] is None or f["run20"] <= 0,
    "20 天涨了判涨、跌了判跌（顺势）": lambda f: f["run20"] is None or f["run20"] > 0,
    "跟上次财报方向": lambda f: f["last"] is None or f["last"] > 0,
    "和上次财报反着": lambda f: f["last"] is None or f["last"] <= 0,
    "跟同行，没有同行判涨": lambda f: f["peers"] != "down",
    "跟 SPY 前 5 天": lambda f: f["spy5"] is None or f["spy5"] > 0,
}


def split(fs: list[dict]) -> tuple[list[dict], list[dict]]:
    return [f for f in fs if f["day"] < TEST], [f for f in fs if f["day"] >= TEST]


def report(fs: list[dict], end: str) -> tuple[dict, str]:
    old, new = split(fs)
    up = lambda xs: (sum(f["gap"] > 0 for f in xs) / len(xs), len(xs)) if xs else None
    cell = lambda v: f"{v[0]:.0%}（{v[1]}）" if v else "—"
    base_old, base_new = up(old), up(new)
    md = ["## 各种财报前迹象下，开盘涨的比例", "",
          f"所有财报：2021-10 到 2026-06 开盘涨 {cell(base_old)}；上一季（{TEST} 起）{cell(base_new)}。", "",
          "| 财报前的迹象 | 2021-10 到 2026-06 | 上一季 | 两段都偏同一边？ |", "|---|---|---|---|"]
    table = {"base": {"old": base_old, "new": base_new}, "conditions": {}, "strategies": {}}
    for name, cond in CONDITIONS.items():
        a, b = up([f for f in old if cond(f)]), up([f for f in new if cond(f)])
        lean = "—"
        if a and b and a[1] >= 30 and b[1] >= 8:
            da, db = a[0] - base_old[0], b[0] - base_new[0]
            lean = ("都偏涨" if da > 0 and db > 0 else "都偏跌" if da < 0 and db < 0 else "方向不一致") + f"（{da:+.0%} / {db:+.0%}）"
        table["conditions"][name] = {"old": a, "new": b, "lean": lean}
        md.append(f"| {name} | {cell(a)} | {cell(b)} | {lean} |")
    md += ["", "## 每只都判的规则：猜对的比例，和按判断方向做正股（开盘对财报前）每 $1", "",
           "| 规则 | 猜对 2021-10 到 2026-06 | 猜对 上一季 | 上一季每 $1 平均 | 上一季中位数 | 全部每 $1 | 去掉最好 3 笔 | 提醒 |",
           "|---|---|---|---|---|---|---|---|"]
    for name, rule in STRATEGIES.items():
        hit = lambda xs: (sum((f["gap"] > 0) == rule(f) for f in xs) / len(xs), len(xs)) if xs else None
        signed = [(f["day"], f["gap"] if rule(f) else -f["gap"]) for f in fs]
        s_new = [r for d, r in signed if d >= TEST]
        s = robust.summary(signed, end)
        table["strategies"][name] = {"old": hit(old), "new": hit(new), "summary": s}
        md.append(f"| {name} | {cell(hit(old))} | {cell(hit(new))} | {statistics.mean(s_new):+.2%} | {statistics.median(s_new):+.2%} | "
                  f"{s['mean']:+.2%} | {s['trim']:+.2%} | {'；'.join(s['flags']) or '—'} |")
    return table, "\n".join(md)


def main() -> None:
    events = json.loads(EVENTS.read_text())["events"]
    syms = sorted({e["symbol"] for e in events}) + ["SPY"]
    bars = {}
    for s in syms:
        try:
            bars[s] = yahoo(s, "2021-01-01")
        except Exception as e:
            print("skip", s, e)
        time.sleep(0.3)
    fs = features(events, bars)
    end = max(f["day"] for f in fs)
    table, body = report(fs, end)
    head = [f"# 上一季财报回测：财报前的简单规则猜得准开盘吗？ {date.today().isoformat()}", "",
            f"research/2026-10-02-earnings-reactions.json 的 {len({f['symbol'] for f in fs})} 只大公司、{len(fs)} 次财报"
            f"（{min(f['day'] for f in fs)} 到 {end}），开盘对财报前最后一笔。上一季 = {TEST} 起的 {len(split(fs)[1])} 次，"
            "之前的当对照。我们盘前上网读新闻的判断没法回测（现在去搜会看到结果），这里只测能机械执行的规则，"
            "包括 10-20 实际用的几条（同行这周先涨就判涨、进财报前 20 天涨 8% 以上判跌、其余判涨）。"
            "同行 = 同一组公司（芯片、软件、银行等，手工分组）在前 7 天里已经开盘的财报，至少 2 只。"
            "幸存者偏差：都是今天的大公司。只研究，不改交易。", ""]
    save("earnings-rules", {"features": fs, "table": table}, "\n".join(head) + "\n" + body + "\n")


if __name__ == "__main__":
    sys.exit(main())
