"""Which of the sniper's judgement calls hold up on history? (owner, 2026-10-15: not enough live picks -- backtest.)

The confidence number is a judgement; the parts of it a rule can see are tested here on the open-catalyst events
(research/*-open-catalyst.json: 25 active names since 2024-02, the open 2%+ from the prior close, the option bought at
09:35 the way the first five minutes went, nearest expiry, 0.6% out, 10% costs each way):
  gap:      2-4% / 4-6% / 6%+ (has the gap already priced the news?), and whether the pick goes with the gap;
  streak:   how many sessions in a row the stock had already closed the pick's way (day 3 of a selloff, ...);
  market:   whether QQQ's first five minutes went the same way (research/*-zdte-timing.json, 09:35 side);
  macro:    a CPI, jobs or Fed day (hero.research_eventday dates);
  news:     headlines between the prior close and 09:30 (none / 1+ / 3+).
Exits: the trail (after 2x, out on a 40% giveback, else 15:30), half at 3x plus the trail, held to 15:30, and held
to the next close (the sniper holds to its expiry). Record only. Run: python -m hero.research_sniper_factors
"""

from __future__ import annotations

import glob
import json
import sys
from datetime import date

from hero.research_eventday import CPI, FED, JOBS
from hero.research_ideas import ROOT, report, save

EXITS = {"trail2": "翻倍后回撤 40% 卖", "take3_trail": "一半 3 倍、其余回撤卖", "hold": "拿到 15:30", "next": "拿到下一个交易日收盘"}
MACRO = set(CPI) | set(FED) | set(JOBS)


def latest(name: str) -> dict:
    return json.loads(open(sorted(glob.glob(str(ROOT / "research" / f"*-{name}.json")))[-1]).read())


def streaks(closes: dict[str, list[tuple[str, float]]]) -> dict[tuple[str, str], tuple[int, int]]:
    """(symbol, day) -> (up closes in a row, down closes in a row) ending the session before `day`."""
    out = {}
    for sym, xs in closes.items():
        up = down = 0
        for i in range(1, len(xs)):
            out[(sym, xs[i][0])] = (up, down)
            r = xs[i][1] / xs[i - 1][1] - 1
            up, down = (up + 1, 0) if r > 0 else ((0, down + 1) if r < 0 else (0, 0))
    return out


def tag(rows: list[dict], st: dict, qqq: dict[str, str]) -> list[dict]:
    out = []
    for r in rows:
        up, down = st.get((r["und"], r["day"]), (0, 0))
        res = dict(r["res"])
        if r.get("next") is not None:
            res["next"] = r["next"]
        out.append({**r, "res": res, "absgap": abs(r["gap"]), "streak": up if r["kind"] == "C" else down,
                    "market": qqq.get(r["day"]), "macro": r["day"] in MACRO})
    return out


def groups(rows: list[dict]) -> dict[str, list[dict]]:
    same = lambda r: r["market"] == r["kind"]
    g = {"全部开盘跳空 2%+": rows,
         "选的方向和跳空一致": [r for r in rows if r["with_gap"]],
         "选的方向和跳空相反（跳空后反转）": [r for r in rows if not r["with_gap"]],
         "跳空 2–4%": [r for r in rows if r["absgap"] < 0.04],
         "跳空 4–6%": [r for r in rows if 0.04 <= r["absgap"] < 0.06],
         "跳空 6% 以上": [r for r in rows if r["absgap"] >= 0.06],
         "之前没有同向收盘（第 1 天）": [r for r in rows if r["streak"] == 0],
         "之前同向收盘 1 天（第 2 天）": [r for r in rows if r["streak"] == 1],
         "之前同向收盘 2 天以上（第 3 天起）": [r for r in rows if r["streak"] >= 2],
         "QQQ 前 5 分钟同向": [r for r in rows if same(r)],
         "QQQ 前 5 分钟反向": [r for r in rows if r["market"] and not same(r)],
         "数据日（CPI / 非农 / Fed）": [r for r in rows if r["macro"]],
         "平日": [r for r in rows if not r["macro"]],
         "盘前没有新闻": [r for r in rows if not r["news"]],
         "盘前有新闻": [r for r in rows if r["news"]],
         "盘前新闻 3 条以上": [r for r in rows if r["news"] >= 3],
         "组合：顺跳空 + 跳空 2–6% + QQQ 同向": [r for r in rows if r["with_gap"] and r["absgap"] < 0.06 and same(r)],
         "组合：上面再加第 1 天": [r for r in rows if r["with_gap"] and r["absgap"] < 0.06 and same(r) and r["streak"] == 0],
         "组合：顺跳空 + 第 3 天起（像 10-15 的 CRWV）": [r for r in rows if r["with_gap"] and r["streak"] >= 2]}
    return g


def main() -> None:
    from hero.alpaca import Alpaca
    oc = latest("open-catalyst")
    rows = oc["rows"]
    timing = latest("zdte-timing")
    qqq = {r["day"]: r["side"].get("09:35") for r in timing["rows"] if r["und"] == "QQQ"}
    syms = sorted({r["und"] for r in rows})
    bars = Alpaca().daily_bars(syms, "2023-12-01", adjustment="all", feed="sip")
    closes = {s: [(b["t"][:10], float(b["c"])) for b in bs] for s, bs in bars.items()}
    tagged = tag(rows, streaks(closes), qqq)
    table, md = report(f"开盘狙击的判断因素回测 {date.today().isoformat()}",
                       f"{oc['start']} 至 {oc['end']}，25 只活跃股开盘离昨收 2% 以上的 {len(tagged)} 次（research/*-open-catalyst.json）："
                       "09:35 顺着前 5 分钟的方向买最近到期、离现价 0.6% 的期权，按分钟收盘价卖，买入多付 10%、卖出少拿 10%。"
                       "按盘前能看到的因素分组：跳空大小、是否顺跳空、此前同向收盘几天、QQQ 前 5 分钟方向、数据日、盘前新闻。",
                       groups(tagged), EXITS)
    save("sniper-factors", {"generated": date.today().isoformat(), "rows": len(tagged), "table": table}, md)


if __name__ == "__main__":
    sys.exit(main())
