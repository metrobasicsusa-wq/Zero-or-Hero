"""故事链 (owner, 11-02): when a theme's leader opens badly on earnings, how do the next reporters in that theme open?

The 10-22 to 10-30 slide ran as a chain: Meta's capex (-7%) -> Alphabet's capex and cash burn (-12%) -> Amazon's cash
flow (-4.6%), Intel's guide (-29%) and the memory names (Sandisk -13% on a beat-and-raise). The five-year rules study
found "peers that week opened down" useless for direction (57% up), but that was any same-group report, up or down.
This study asks the sharper question: after a LEADER of the group gaps down 5% or more, do the followers in the same
group (and the groups it pays, cloud -> chips / hardware / software) open down more often, fall harder, and does
"a leader just reported, without a shock" look any different? Over research/2026-10-02-earnings-reactions.json
(98 large caps since 2021-10, open against the last price before the report) plus the October 2026 reports scored in
research/earnings-forecast/. Record only; nothing trades on it. Run: python -m hero.research_chain
"""

from __future__ import annotations

import json
import random
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

from hero import robust
from hero.research_earnings_rules import GROUP, GROUPS
from hero.research_ideas import save

ROOT = Path(__file__).resolve().parent.parent
EVENTS = ROOT / "research" / "2026-10-02-earnings-reactions.json"
FORECAST = ROOT / "research" / "earnings-forecast"
TEST = "2026-07-01"
# The names whose report sets the theme's story (hand-picked, today's biggest in each group).
LEADERS = {
    "芯片": "NVDA AVGO AMD INTC MU QCOM TXN",
    "AI 硬件 / 算力": "ANET DELL CSCO SMCI",
    "软件": "ORCL CRM NOW ADBE PLTR",
    "大型互联网": "MSFT GOOGL META AMZN AAPL NFLX",
    "消费": "WMT COST HD TSLA",
    "银行 / 支付": "JPM BAC GS MS V",
    "加密 / 互联网金融": "COIN HOOD",
    "医疗": "LLY JNJ UNH ABBV",
    "工业 / 航天": "CAT GE BA",
    "能源 / 公用 / 电信": "XOM CVX",
}
LEADER = {s for ss in LEADERS.values() for s in ss.split()}
# Who pays whom: a shock in the payer's story reaches the receivers (cloud capex -> chips, hardware, software).
CHAINS = {"大型互联网": ["芯片", "AI 硬件 / 算力", "软件"], "芯片": ["AI 硬件 / 算力"]}
BIG = 0.05
BOOT = 10_000


def load_events() -> list[dict]:
    """The five-year dataset plus October 2026 reports already scored by the forecast pipeline (same gap definition)."""
    data = json.loads(EVENTS.read_text())
    out = [dict(e, source="dataset") for e in data["events"] if e.get("gap") is not None]
    seen = {(e["symbol"], e["reaction_day"]) for e in out}
    names = set(data["symbols"]) if isinstance(data.get("symbols"), list) else {e["symbol"] for e in out}
    last = max(e["reaction_day"] for e in out)
    for path in sorted(FORECAST.glob("2026-*.json")):
        if path.name.count(".") != 1:
            continue
        for r in json.loads(path.read_text()).get("reports", []):
            res = r.get("result") or {}
            gap = res.get("gap_pre", res.get("gap"))
            day = r.get("reaction_day")
            if gap is None or not day or day <= last or r["symbol"] not in names or (r["symbol"], day) in seen:
                continue
            seen.add((r["symbol"], day))
            out.append({"symbol": r["symbol"], "reaction_day": day, "gap": gap, "move": res.get("move"),
                        "implied_move": r.get("implied_move"), "source": "forecast"})
    for e in out:
        e["group"] = GROUP.get(e["symbol"])
    return sorted(out, key=lambda e: (e["reaction_day"], e["symbol"]))


def shocks(events: list[dict], f: dict, thresh: float, days: int, leaders_only: bool, cross: bool,
           key: str = "gap") -> list[dict]:
    """Earlier reports (strictly before f's reaction day, within `days` calendar days) whose `key` (open gap, or the
    close `move`: Meta opened -3% on 10-22 but closed -7%) passed `thresh` in f's group, or in a group that pays f's
    group when `cross`. Negative thresh: down shocks; positive: up."""
    if not f["group"]:
        return []
    lo = (date.fromisoformat(f["reaction_day"]) - timedelta(days=days)).isoformat()
    srcs = {f["group"]} if not cross else {g for g, rs in CHAINS.items() if f["group"] in rs}
    out = []
    for g in events:
        if g["symbol"] == f["symbol"] or g["group"] not in srcs or not (lo <= g["reaction_day"] < f["reaction_day"]):
            continue
        if leaders_only and g["symbol"] not in LEADER:
            continue
        x = g.get(key)
        if x is None:
            continue
        if (thresh < 0 and x <= thresh) or (thresh > 0 and x >= thresh):
            out.append(g)
    return out


def reported(events: list[dict], f: dict, days: int, leaders_only: bool, cross: bool) -> list[dict]:
    """The same window without the shock test: leaders (or anyone) that simply reported just before f."""
    if not f["group"]:
        return []
    lo = (date.fromisoformat(f["reaction_day"]) - timedelta(days=days)).isoformat()
    srcs = {f["group"]} if not cross else {g for g, rs in CHAINS.items() if f["group"] in rs}
    return [g for g in events if g["symbol"] != f["symbol"] and g["group"] in srcs and lo <= g["reaction_day"] < f["reaction_day"]
            and (not leaders_only or g["symbol"] in LEADER)]


def stat(xs: list[dict]) -> dict | None:
    if not xs:
        return None
    gaps = [x["gap"] for x in xs]
    ratio = [abs(x["gap"]) / x["implied_move"] for x in xs if x.get("implied_move")]
    return {"n": len(gaps), "up": sum(g > 0 for g in gaps) / len(gaps), "mean": statistics.mean(gaps),
            "median": statistics.median(gaps), "big_down": sum(g <= -BIG for g in gaps) / len(gaps),
            "abs": statistics.mean(abs(g) for g in gaps),
            "vs_implied": (statistics.mean(ratio), len(ratio)) if ratio else None}


def boot_diff(a: list[dict], b: list[dict], seed: int = 1, what=lambda x: x["gap"] > 0) -> tuple[float, float, float] | None:
    """95% interval and share > 0 of (share with `what` in a) - (same in b), resampling both. Default: opened up."""
    if len(a) < 10 or len(b) < 10:
        return None
    rng = random.Random(seed)
    ga, gb = [what(x) for x in a], [what(x) for x in b]
    diffs = []
    for _ in range(BOOT):
        pa = sum(rng.choice(ga) for _ in ga) / len(ga)
        pb = sum(rng.choice(gb) for _ in gb) / len(gb)
        diffs.append(pa - pb)
    diffs.sort()
    return diffs[int(0.025 * BOOT)], diffs[int(0.975 * BOOT) - 1], sum(d > 0 for d in diffs) / BOOT


def cell(s: dict | None) -> str:
    if not s:
        return "—"
    return f"{s['n']} / {s['up']:.0%} / {s['mean']:+.1%} / {s['median']:+.1%} / {s['big_down']:.0%} / {s['abs']:.1%}"


def ci(c: tuple | None) -> str:
    return f"{c[0]:+.0%} 到 {c[1]:+.0%}（>0 的比例 {c[2]:.0%}）" if c else "样本不够"


def split_by(events: list[dict], thresh: float, days: int, leaders_only: bool, cross: bool,
             key: str = "gap") -> tuple[list, list, list]:
    """(shocked followers, followers whose leader reported but without a shock, everyone else)."""
    hit, quiet, rest = [], [], []
    for f in events:
        if shocks(events, f, thresh, days, leaders_only, cross, key):
            hit.append(f)
        elif reported(events, f, days, leaders_only, cross):
            quiet.append(f)
        else:
            rest.append(f)
    return hit, quiet, rest


def run(events: list[dict]) -> tuple[dict, str]:
    end = max(e["reaction_day"] for e in events)
    base = stat(events)
    rep: dict = {"n": len(events), "base": base, "settings": {}, "groups": {}, "cross": {}, "years": {}, "recent": [],
                 "strategies": {}}
    md = ["## 定义", "",
          "「龙头」= 每组手工挑的几家最大的公司（见 LEADERS）；「链条启动」= 同组龙头在这只财报反应日之前的 N 天内（日历日）",
          f"财报开盘跌 5%（或 8%）以上；「龙头刚报了、没大跌」= 同一窗口里有龙头报了但没过门槛。开盘 = 反应日开盘对财报前最后一笔。"
          f"每格：次数 / 开盘涨的比例 / 平均开盘 / 中位数 / 开盘跌 5% 以上的比例 / 平均绝对幅度。",
          "", f"所有财报：{cell(base)}。", "",
          "## 同组龙头先大跌，后面报的公司", "",
          "| 条件 | 链条启动 | 龙头刚报了、没大跌 | 其余 | 启动 对 其余：开盘涨比例之差的 95% 区间 | 启动 对 刚报没大跌 | 启动 对 其余：跌 5% 以上比例之差 |",
          "|---|---|---|---|---|---|---|"]
    main_key = None
    big = lambda x: x["gap"] <= -BIG
    for leaders_only in (True, False):
        for by, label in (("gap", "开盘跌"), ("move", "收盘跌")):
            if by == "move" and not leaders_only:
                continue
            for thresh in (-0.05, -0.08):
                for days in (4, 7, 14):
                    hit, quiet, rest = split_by(events, thresh, days, leaders_only, False, by)
                    key = f"{'龙头' if leaders_only else '同组任何公司'} {label} {abs(thresh):.0%} 以上、{days} 天内"
                    c1, c2, c3 = boot_diff(hit, rest), boot_diff(hit, quiet), boot_diff(hit, rest, what=big)
                    rep["settings"][key] = {"hit": stat(hit), "quiet": stat(quiet), "rest": stat(rest), "ci_rest": c1, "ci_quiet": c2,
                                            "ci_big": c3}
                    md.append(f"| {key} | {cell(stat(hit))} | {cell(stat(quiet))} | {cell(stat(rest))} | {ci(c1)} | {ci(c2)} | {ci(c3)} |")
                    if leaders_only and by == "gap" and thresh == -0.05 and days == 7:
                        main_key = key
    # mirror: leader gapped UP 5%+
    md += ["", "## 镜像：同组龙头先大涨 5% 以上", "", "| 条件 | 链条启动 | 龙头刚报了、没大涨 | 其余 | 启动 对 其余 |", "|---|---|---|---|---|"]
    for days in (7, 14):
        hit, quiet, rest = split_by(events, 0.05, days, True, False)
        key = f"龙头涨 5% 以上、{days} 天内"
        rep["settings"][key] = {"hit": stat(hit), "quiet": stat(quiet), "rest": stat(rest), "ci_rest": boot_diff(hit, rest)}
        md.append(f"| {key} | {cell(stat(hit))} | {cell(stat(quiet))} | {cell(stat(rest))} | {ci(boot_diff(hit, rest))} |")
    # by group, main setting
    hit, quiet, rest = split_by(events, -0.05, 7, True, False)
    md += ["", f"## 按组（{main_key}）", "", "| 组 | 链条启动 | 其余同组 |", "|---|---|---|"]
    for g in GROUPS:
        a, b = [f for f in hit if f["group"] == g], [f for f in quiet + rest if f["group"] == g]
        rep["groups"][g] = {"hit": stat(a), "rest": stat(b)}
        md.append(f"| {g} | {cell(stat(a))} | {cell(stat(b))} |")
    # cross chains
    md += ["", "## 跨组：出钱的先大跌，收钱的怎么开（大型互联网 → 芯片 / AI 硬件 / 软件；芯片 → AI 硬件）", "",
           "| 条件 | 链条启动 | 龙头刚报了、没大跌 | 其余 | 启动 对 其余 |", "|---|---|---|---|---|"]
    for thresh in (-0.05, -0.08):
        for days in (7, 14):
            hit_x, quiet_x, rest_x = split_by([f for f in events if f["group"] in {r for rs in CHAINS.values() for r in rs}],
                                              thresh, days, True, True)
            # shocks must come from the paying group: split_by filters followers, shocks() looks at payers via CHAINS
            key = f"上游龙头跌 {abs(thresh):.0%} 以上、{days} 天内"
            rep["cross"][key] = {"hit": stat(hit_x), "quiet": stat(quiet_x), "rest": stat(rest_x), "ci_rest": boot_diff(hit_x, rest_x)}
            md.append(f"| {key} | {cell(stat(hit_x))} | {cell(stat(quiet_x))} | {cell(stat(rest_x))} | {ci(boot_diff(hit_x, rest_x))} |")
    # by year, main setting
    md += ["", f"## 按年份（{main_key}）", "", "| 年 | 链条启动 | 其余 |", "|---|---|---|"]
    for y in sorted({f["reaction_day"][:4] for f in events}):
        a, b = [f for f in hit if f["reaction_day"][:4] == y], [f for f in quiet + rest if f["reaction_day"][:4] == y]
        rep["years"][y] = {"hit": stat(a), "rest": stat(b)}
        md.append(f"| {y} | {cell(stat(a))} | {cell(stat(b))} |")
    # against the option-implied move (only the reports that carry one)
    md += ["", f"## 对期权定价（有定价的那部分；{main_key}）", ""]
    for name, xs in (("链条启动", hit), ("龙头刚报了、没大跌", quiet), ("其余", rest)):
        st = stat(xs)
        v = st and st["vs_implied"]
        md.append(f"- {name}：开盘幅度是定价的 {v[0]:.2f} 倍（{v[1]} 次）" if v else f"- {name}：没有定价数据")
    rep["vs_implied"] = {name: (stat(xs) or {}).get("vs_implied") for name, xs in (("hit", hit), ("quiet", quiet), ("rest", rest))}
    # recent quarter rows
    md += ["", f"## 最近一季（{TEST} 起）链条启动的每一笔", "", "| 反应日 | 公司 | 组 | 开盘 | 先大跌的龙头 |", "|---|---|---|---|---|"]
    for f in hit:
        if f["reaction_day"] >= TEST:
            who = "、".join(f"{g['symbol']} {g['reaction_day'][5:]} {g['gap']:+.0%}" for g in shocks(events, f, -0.05, 7, True, False))
            rep["recent"].append({"day": f["reaction_day"], "symbol": f["symbol"], "group": f["group"], "gap": f["gap"], "leaders": who})
            md.append(f"| {f['reaction_day']} | {f['symbol']} | {f['group']} | {f['gap']:+.1%} | {who} |")
    # strategies: signed gap per $1 in the called direction
    strategies = {
        "全部判涨": lambda f: True,
        "链条启动判跌、其余判涨（龙头 5%、7 天）": lambda f: not shocks(events, f, -0.05, 7, True, False),
        "链条启动判跌、其余判涨（含跨组、14 天）": lambda f: not (shocks(events, f, -0.05, 14, True, False) or shocks(events, f, -0.05, 14, True, True)),
        "同组任何公司 7 天内跌 5% 就判跌": lambda f: not shocks(events, f, -0.05, 7, False, False),
    }
    md += ["", "## 每只都判：猜对的比例，和按判断方向做正股每 $1（开盘对财报前）", "",
           "| 规则 | 猜对（全部） | 猜对（上一季起） | 每 $1 | 去掉最好 3 笔 | 中位数 | 各年 | 提醒 |", "|---|---|---|---|---|---|---|---|"]
    for name, rule in strategies.items():
        calls = [(f, rule(f)) for f in events]
        hit_all = sum((f["gap"] > 0) == c for f, c in calls) / len(calls)
        recent = [(f, c) for f, c in calls if f["reaction_day"] >= TEST]
        hit_new = sum((f["gap"] > 0) == c for f, c in recent) / len(recent) if recent else None
        s = robust.summary([(f["reaction_day"], f["gap"] if c else -f["gap"]) for f, c in calls], end)
        rep["strategies"][name] = {"hit": hit_all, "hit_recent": hit_new, "summary": s}
        years = "、".join(f"{y} {m:+.1%}（{n}）" for y, (m, n) in s["years"].items())
        md.append(f"| {name} | {hit_all:.0%} | {hit_new:.0%} | {s['mean']:+.2%} | {s['trim']:+.2%} | {s['median']:+.2%} | {years} | "
                  f"{'；'.join(s['flags']) or '—'} |")
    # only trade the chain: short the follower at the open when a leader shocked (gap per $1 = -gap)
    only = [(f["reaction_day"], -f["gap"]) for f in hit]
    s = robust.summary(only, end)
    rep["strategies"]["只在链条启动时判跌（其余不判）"] = {"summary": s}
    if s:
        md.append(f"| 只在链条启动时判跌、其余不判 | {s['win']:.0%} | — | {s['mean']:+.2%} | {s['trim']:+.2%} | {s['median']:+.2%} | "
                  + "、".join(f"{y} {m:+.1%}（{n}）" for y, (m, n) in s["years"].items()) + f" | {'；'.join(s['flags']) or '—'} |")
    # verdict
    m = rep["settings"][main_key]
    md += ["", "## 结论", ""]
    if m["hit"] and m["ci_rest"]:
        d_up = m["hit"]["up"] - m["rest"]["up"]
        d_big = m["hit"]["big_down"] - m["rest"]["big_down"]
        sign_ok = m["ci_rest"][1] < 0 or m["ci_rest"][0] > 0
        md.append(f"- 主设定（{main_key}）：链条启动 {m['hit']['n']} 次开盘涨 {m['hit']['up']:.0%}，其余 {m['rest']['up']:.0%}"
                  f"（差 {d_up:+.0%}，95% 区间 {m['ci_rest'][0]:+.0%} 到 {m['ci_rest'][1]:+.0%}，{'跨 0，方向上不算信号' if not sign_ok else '不跨 0'}）；"
                  f"开盘跌 5% 以上的比例 {m['hit']['big_down']:.0%} 对 {m['rest']['big_down']:.0%}（差 {d_big:+.0%}），"
                  f"平均绝对幅度 {m['hit']['abs']:.1%} 对 {m['rest']['abs']:.1%}。")
        c3 = m.get("ci_big")
        if c3:
            md.append(f"- 幅度：跌 5% 以上比例之差的 95% 区间 {c3[0]:+.0%} 到 {c3[1]:+.0%}（>0 的比例 {c3[2]:.0%}）——"
                      f"{'链条启动后尾部更肥，这是可用的信息（对期权、对止损），不是方向' if c3[0] > 0 else '幅度上也跨 0'}。")
        if m["quiet"]:
            q = m["quiet"]
            md.append(f"- 对照「龙头刚报了、没大跌」{q['n']} 次开盘涨 {q['up']:.0%}、跌 5% 以上 {q['big_down']:.0%}："
                      + ("方向上三组都差不多；" if abs(m['hit']['up'] - q['up']) < 0.05 else
                         ("龙头的坏消息压低了方向；" if m['hit']['up'] < q['up'] else "龙头的坏消息反而抬高了方向；"))
                      + ("尾部只在龙头真大跌之后变肥，龙头只是先报了不会。" if m['hit']['big_down'] > q['big_down'] + 0.03 else "尾部和龙头只是先报了分不开。"))
    md.append("- 每组和每年的表看方向是否一致；只有一两年或一两组撑起来的差别不算。「提醒」列里标了样本少或靠少数几笔的策略。")
    md.append("- 对我们：方向判断不因链条改——链里的公司照样一半涨一半跌，「龙头先大跌就判跌」每 $1 是负的；"
              "链条改变的是幅度：开盘跌 5% 以上的比例从约 18% 升到 25–28%，开盘幅度对期权定价的比例也更接近 1。"
              "所以链条该进的是期权和止损的考虑（财报彩票 / 财报账本的期权在链条里更值、持仓的财报风险更大），不是方向清单。"
              "15:05 判断里仍只把链条当背景：链里的公司「超」不算数，要看链条正在问的那个指标，但这是读新闻的判断，不是机械规则。")
    return rep, "\n".join(md)


def main() -> None:
    events = load_events()
    rep, body = run(events)
    n_fc = sum(e["source"] == "forecast" for e in events)
    head = [f"# 故事链：龙头财报先大跌，后面报的公司开盘怎么样？ {date.today().isoformat()}", "",
            f"research/2026-10-02-earnings-reactions.json 的 {len({e['symbol'] for e in events})} 只大公司、{len(events)} 次财报"
            f"（{events[0]['reaction_day']} 到 {events[-1]['reaction_day']}；其中 {n_fc} 次是 10 月由 research/earnings-forecast/ 记分补上的），"
            "开盘对财报前最后一笔。起因是 10-22 到 10-30 的下跌是一条链：Meta 开支 → 谷歌开支和现金流 → 亚马逊现金流、英特尔指引、存储股。"
            "五年回测说「同行这周先跌」对方向没用（57% 开盘涨），但那是任何同组财报；这里只看龙头先大跌之后。"
            "分组和龙头都是手工挑的、按今天的市值，有幸存者和后见偏差。只研究，不改交易。", ""]
    save("chain", rep, "\n".join(head) + "\n" + body + "\n")


if __name__ == "__main__":
    sys.exit(main())
