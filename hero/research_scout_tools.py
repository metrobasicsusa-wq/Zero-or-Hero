"""The scout's three structures on the big drop days of the past, with estimated option prices (2026-11-21).

From 11-20 on hero/scout_ledger.py writes three structures down at real quotes on every big drop day. Nothing yet says
what the same structures did on the drop days before. This takes them back to 2016 on SPY, QQQ and seven megacaps,
entered on the scout's own drop days (hero.scout.big_drop: S&P -1.5%, Nasdaq 100 -2%, any sector fund -3%), beside the
same structure entered on every day:
  spread3m  a bull call spread, first monthly expiry 80 days out, at the money / 10% higher (5% for the two indexes);
  spread1y  a bull call spread, first monthly expiry 330 days out, at the money / 20% higher (10%);
  csp3m     a put 10% lower (5%) sold against cash, the expiry of spread3m
(the expiries and strikes of scout_ledger.PLAN). Held to expiry; per $1 of the most that can be lost; every leg 5% worse
than the estimate. 2022 apart.

The prices are estimated, not real: research_tools' volatility, fitted on the real three-month legs; one year scaled by
(VIX6M / VIX3M) ** 1.5, a guess that came out 8% too cheap on the one real quote there is (research_expiries); a strike
beyond the fitted skew (the 20% higher call) takes the last fitted point. The megacaps are today's winners. Drop days
come in runs, so the report counts stretches (a gap of 45 calendar days starts a new one) and the robustness table takes
one entry per underlying and stretch: the first day. Exploration: a lead, not a rule.
"""

from __future__ import annotations

import json
import math
import statistics
import time
from datetime import date
from pathlib import Path

from hero import research_expiries as rx
from hero import research_tools as rt
from hero import robust, scout
from hero import scout_ledger as sl
from hero.research_daybook_long import expiry

ROOT = Path(__file__).resolve().parent.parent
START, COST, GAP, YEAR_POWER = "2016-01-04", 0.05, 45, 1.5
FUNDS = ("SPY", "QQQ")
GROUPS = {"指数": lambda r: r["und"] in FUNDS, "个股": lambda r: r["und"] not in FUNDS}


def drop_days(moves: dict[str, dict[str, float]]) -> dict[str, list[str]]:
    """{day: why} for every big drop day by the scout's lines. moves: {fund: {day: close-to-close move}}."""
    out = {}
    for d in sorted({d for m in moves.values() for d in m}):
        why = scout.big_drop({f: m[d] for f, m in moves.items() if d in m})
        if why:
            out[d] = why
    return out


def first_days(days: list[str], gap: int = GAP) -> list[str]:
    """The first day of each stretch: a day more than `gap` calendar days after the one before starts a new stretch."""
    out, prev = [], None
    for d in sorted(days):
        if prev is None or (date.fromisoformat(d) - date.fromisoformat(prev)).days > gap:
            out.append(d)
        prev = d
    return out


def structure(kind: str, spot: float, settle: float, t: float, rate: float, q: float, vol, fund: bool) -> tuple[float, float] | None:
    """Per share, held to expiry: (profit, the most that can be lost). `vol(m)`: the volatility at a strike m x spot."""
    far = spot * sl.PLAN[kind][3 if fund else 2]
    if kind == "csp3m":
        got = rt.bs(spot, far, t, rate, q, vol(far / spot), "P") * (1 - COST)
        return got - max(far - settle, 0.0), far - got
    debit = rt.bs(spot, spot, t, rate, q, vol(1.0), "C") * (1 + COST) - rt.bs(spot, far, t, rate, q, vol(far / spot), "C") * (1 - COST)
    width = far - spot
    if not 0 < debit < width:
        return None
    return max(0.0, min(settle - spot, width)) - debit, debit


def rows(hist: dict, fits: dict[str, dict], vix6m: dict[str, float], drops: set[str], start: str = START) -> list[dict]:
    """Every entry day whose expiries have passed: was it a drop day, and each structure's result per $1 at risk."""
    days, sessions, out = hist["days"], set(hist["days"]), []
    for und, f in fits.items():
        closes, at, fund = hist["closes"][und], hist["index"][und], und in FUNDS
        for day in days:
            i, v3 = at.get(day), hist["vix3m"].get(day)
            if day < start or i is None or i < 252 or not v3:
                continue
            base = f["p"] * rt.realized(closes, i) + f["q"] * v3 / 100
            spot, rate, q, res = closes[i], hist["rate"].get(day, 0.02), rt.YIELD.get(und, 0.0), {}
            for kind, plan in sl.PLAN.items():
                exp = expiry(day, plan[0], sessions)
                j, ratio = at.get(exp), rx.term(v3, vix6m.get(day), YEAR_POWER if kind == "spread1y" else None)
                if j is None or ratio is None:
                    continue
                t = (date.fromisoformat(exp) - date.fromisoformat(day)).days / 365
                flat = math.sqrt(rx.FITTED_AT / t)

                def vol(m: float, base=base, ratio=ratio, flat=flat, skew=f["skew"]) -> float:
                    return base * ratio * (1 + (rx.skew_at(skew, m) - 1) * flat)
                got = structure(kind, spot, closes[j], t, rate, q, vol, fund)
                if got:
                    res[kind] = got[0] / got[1]
            if res:
                out.append({"und": und, "day": day, "drop": day in drops, "risk": res})
    return out


def stat(xs: list[float]) -> dict | None:
    return {"n": len(xs), "mean": statistics.mean(xs), "won": sum(x > 0 for x in xs) / len(xs), "lost": sum(x <= -0.95 for x in xs) / len(xs)} if xs else None


def entries(xs: list[dict], kind: str) -> list[tuple[str, float]]:
    """One entry per underlying and stretch of drop days: the first day that has this structure."""
    out = []
    for und in sorted({r["und"] for r in xs}):
        mine = {r["day"]: r["risk"][kind] for r in xs if r["und"] == und and r["drop"] and kind in r["risk"]}
        out += [(d, mine[d]) for d in first_days(list(mine))]
    return sorted(out)


def tables(xs: list[dict]) -> dict:
    out = {}
    for g, keep in GROUPS.items():
        sub, t = [r for r in xs if keep(r)], {}
        for kind in sl.PLAN:
            have = [r for r in sub if kind in r["risk"]]

            def pick(drop: bool | None, year: bool | None, have=have, kind=kind) -> list[float]:
                return [r["risk"][kind] for r in have if (drop is None or r["drop"] == drop) and (year is None or (r["day"][:4] == "2022") == year)]
            t[kind] = {"drop": stat(pick(True, None)), "all": stat(pick(None, None)), "drop_2022": stat(pick(True, True)), "drop_other": stat(pick(True, False)),
                       "all_2022": stat(pick(None, True)), "all_other": stat(pick(None, False)),
                       "stretches": len(first_days(sorted({r["day"] for r in have if r["drop"]}))), "entries": entries(sub, kind)}
        out[g] = t
    return out


def pct(x: float | None) -> str:
    return "—" if x is None else f"{x:+.0%}".replace("-", "−")


def cell(s: dict | None) -> str:
    return "—" if not s else f"{pct(s['mean'])}，赚 {s['won']:.0%}，亏光 {s['lost']:.0%}（{s['n']}）"


def report(t: dict, drops: dict[str, list[str]], fits: dict, start: str, end: str) -> str:
    in_range = [d for d in drops if start <= d <= end]
    out = ["# 侦察的三种做法，放到过去的大跌日上（2026-11-21）", "",
           "模拟盘研究，只记录，不是投资建议。探索档：线索，不是规则。**期权价是估的，不是真实成交价；一年那一栏的波动率是外推的，最粗。**", "",
           "侦察从 11-20 起在每个大跌日按真实报价记三种做法，但过去的大跌日上它们结果如何，一直没有回头测过（用户 11-20：「都有用过去做对比吗」）。"
           f"这里补上：{start} 到 {end}，标普、纳指和七只大股票，进场日按侦察同一条线判（标普 −1.5%、纳指 100 −2%、任一行业基金 −3%），"
           "到期和行权价照演算账本的写法，拿到到期。并排的是「同样的做法，不挑日子，每天都进」。", "",
           f"这段时间里大跌日一共 {len(in_range)} 天，连着的算一段是 {len(first_days(in_range))} 段。"
           "每 $1 是对「最多能亏的钱」；每条腿都比估的价差 5%。「亏光」= 亏掉 95% 以上。括号里是笔数：每天一笔、前后重叠，笔数多不等于样本多。", ""]
    for g, tk in t.items():
        out += [f"## {g}", "", "| 做法 | 大跌日进场 | 每天都进 | 大跌日：2022 年 / 其余 | 每天：2022 年 / 其余 |", "|---|---|---|---|---|"]
        for kind, name in sl.KINDS.items():
            c = tk[kind]
            split = lambda a, b: f"{pct(a['mean']) if a else '—'} / {pct(b['mean']) if b else '—'}"    # noqa: E731
            out.append(f"| {name} | {cell(c['drop'])} | {cell(c['all'])} | {split(c['drop_2022'], c['drop_other'])} | {split(c['all_2022'], c['all_other'])} |")
        out += ["", robust.table({sl.KINDS[k]: robust.summary(tk[k]["entries"], end=end) for k in sl.PLAN},
                                 title=f"稳健性检查（{g}，每只每段大跌日只算第一天）"), ""]
    out += ["## 说明", "",
            "- 行权价：三个月看涨价差是平值 / 高 10%（指数高 5%），一年是平值 / 高 20%（指数高 10%），卖看跌是低 10%（指数低 5%）；到期是 80 天、330 天以后的第一个月度到期。",
            "- 波动率的估法和 `research/2026-11-20-expiries.md` 一样；一年的那一档拿侦察 11-20 的真实报价对过一次，估的便宜 8%（只有 1 笔）。高 20% 的行权价超出了拟合的范围，用的是最远一个拟合点。",
            "- 七只大股票是事后的赢家；没有 2000–02 和 2008 那样的长熊。清单里那种跌了 25% 以上的股票，尾巴比这里长。",
            "- 稳健性表里「多付成本后」是在这里已经付的 5% 之上每边再多 5%。卖看跌那一行的这一栏不能用：它把 5% 加在整笔押金上，而卖看跌的成本只在权利金上，权利金只有押金的百分之二三。",
            "- 大跌日并不少见：这十年多里占了六分之一的交易日，一半多是只有某一只行业基金跌了 3%、标普和纳指都没过线。按 45 天的间隔分段，2021 年 9 月到 2025 年年中连成了一段，所以「每段第一天」里没有 2022 年进场的，2022 年看上面那张表的单列。",
            "- 每只的拟合：" + "；".join(f"{u} p={f['p']:.2f} q={f['q']:.2f}" for u, f in fits.items()) + "。"]
    return "\n".join(out) + "\n"


def load_moves(sleep=time.sleep) -> dict[str, dict[str, float]]:
    out = {}
    for f in list(scout.DROP) + list(scout.SECTORS):
        bars = scout.fetch(f, "2015-06-01")
        out[f] = {bars[i][0]: bars[i][2] / bars[i - 1][2] - 1 for i in range(1, len(bars))}
        sleep(0.15)
    return out


def main() -> None:
    hist = rt.load_history()
    fits = rt.fit(rt.observed(json.loads(rt.newest("*-vertical.json").read_text()), hist))
    drops = drop_days(load_moves())
    xs = rows(hist, fits, rx.load_vix6m(), set(drops))
    t = tables(xs)
    end = max(r["day"] for r in xs)
    text = report(t, drops, fits, START, end)
    out = ROOT / "research"
    (out / "2026-11-21-scout-tools.md").write_text(text)
    slim = {g: {k: {a: b for a, b in v.items() if a != "entries"} | {"entries": len(v["entries"])} for k, v in tk.items()} for g, tk in t.items()}
    (out / "2026-11-21-scout-tools.json").write_text(json.dumps({"start": START, "end": end, "rows": len(xs), "drop_days": len([d for d in drops if START <= d <= end]),
                                                                  "tables": slim}, ensure_ascii=False, indent=1) + "\n")
    print(text)


if __name__ == "__main__":
    main()
