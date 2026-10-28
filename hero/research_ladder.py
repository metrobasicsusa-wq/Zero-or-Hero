"""The ladder ledger: $500 climbing by balance (owner, 10-28: 「我想从低做到高」; docs/questions/2026-10-28-ladder-and-opportunities.md).

Three of the bottom studies' saved trades are the ladder's three plays, replayed by date from 2024-02 with no data
calls (the same rules as hero.research_bottom_ledger: whole contracts or spreads, the money locked until the exit,
then back with the trade's return):

- phase 1, under $2,000: the four ETFs' first close 8% under their 20-session high -> the monthly at-the-money
  +$5 call spread 75-130 days out, sold at $4 else at the expiry, all in (research/*-bottom-spread.json, `take80`;
  money back after 91 days or at the expiry).
- phase 2, $2,000 to $10,000: SMH's third down close -> the call nearest the money 20-45 days out, 2x take else the
  20th session, all in (research/*-bottom-long.json `down3` / `take2`; money back after 28 days or at the expiry --
  the study kept no exit day, so a take that came sooner is counted at the 20th session).
- phase 3, $10,000 and up: three bags of the balance -- A half of it in the depth call (the same signal, the monthly
  at-the-money call held 63 sessions, research/*-bottom-2m.json `d63`), staking half of the bag; B a quarter in the
  spreads, all of the bag; C a quarter in the fast line, half of the bag.

The phase is set by the balance at cost (cash plus what open trades cost) when a signal comes; open trades leave by
their own play. The engine's sizing (hero/sleeves.py): when a bag's share buys no whole contract but the bag can
pay for one, one; a flat ledger that cannot buy one with all it has left is a zero on the spot, and so is a flat
ledger under $100; either way the round restarts at the start. One trade per play and name at a time.

A round counts two ways: "到 $10,000 收手" ends it at $10,000 (the hero game, research/*-hero-game.md: the ledgers'
target), "继续爬" keeps climbing into phase 3. Reported: each round (how long, which phases, trades per phase, how
it ended), heroes / rounds, and the same ledger 2,000 times with each play's returns shuffled over its own signal
days (the prices of the day stay, so what a ledger can afford stays real): the chance a round reaches $10,000, how
long it takes, how phase 2 ends. Also from $1,000 and $2,000. Research only."""
from __future__ import annotations

import random
import statistics
import sys
from collections import Counter
from datetime import date, timedelta

from hero.research_bottom_ledger import ETFS, latest, trades
from hero.research_ideas import save

STEPS = (2000.0, 10000.0)
ZERO = 100.0
STARTS = (500.0, 1000.0, 2000.0)
BEGIN = "2024-02-01"
LINES = {"spread": "四只 ETF 第一次跌 8% · 平值 +$5 价差 · 到 $4 卖否则到期", "fast": "SMH 连跌第 3 天 · 20–45 天平值 · 2 倍止盈否则第 20 天",
         "atm63": "四只 ETF 第一次跌 8% · 月度平值 · 拿 63 天"}
ORDER = {"atm63": 0, "spread": 1, "fast": 2}
PHASES = {1: ("spread",), 2: ("fast",), 3: ("atm63", "spread", "fast")}
BAGS = {"atm63": (0.25, 0.5), "spread": (0.25, 0.25), "fast": (0.125, 0.25)}   # phase 3: (stake, bag) as shares of the balance
FAST_DAYS = 28


def phase(eq: float) -> int:
    return 1 if eq < STEPS[0] else 2 if eq < STEPS[1] else 3


def fast_trades(rows: list[dict]) -> list[dict]:
    """bottom-long's SMH third-down-day calls (the cost already carries the study's 5%)."""
    out = []
    for r in rows:
        if r["und"] != "SMH" or r.get("sig") != "down3" or r.get("leg") != "atm":
            continue
        ret = (r.get("res") or {}).get("take2")
        if ret is None:
            continue
        back = min(date.fromisoformat(r["day"]) + timedelta(days=FAST_DAYS), date.fromisoformat(r["expiry"]))
        out.append({"day": r["day"], "und": "SMH", "cost": round(r["cost"] * 100, 2), "ret": ret, "back": back.isoformat()})
    return out


def events(docs: dict) -> list[dict]:
    """Every play's trades in date order (same day: the depth call, the spread, the fast line; ETFs as the studies)."""
    sp = trades(docs["spread"]["rows"], ("atm5",), "dd8", "take80", ETFS)
    a63 = trades(docs["2m"]["rows"], ("atm",), "dd8", "d63", ETFS)
    fast = fast_trades(docs["long"]["rows"])
    und = {u: i for i, u in enumerate(ETFS)}
    ev = [{**t, "line": line} for line, ts in (("spread", sp), ("atm63", a63), ("fast", fast)) for t in ts]
    return sorted(ev, key=lambda t: (t["day"], ORDER[t["line"]], und.get(t["und"], 9)))


def size(line: str, ph: int, cash: float, eq: float, cost: float) -> tuple[int, bool]:
    """Whole units for one trade, the engine's way: phases 1 and 2 all in; phase 3 the bag's stake, and when that buys
    none but the bag can pay for one, one. Returns (units, one bought all in)."""
    stake, bag = (cash, cash) if ph < 3 else (BAGS[line][0] * eq, BAGS[line][1] * eq)
    stake, bag = min(stake, cash), min(bag, cash)
    n = int(stake // cost) if cost > 0 else 0
    if n >= 1:
        return n, False
    return (1, True) if 0 < cost <= bag else (0, False)


def ladder(ev: list[dict], start: float = 500.0, bank: float | None = None, rets: list[float] | None = None,
           down: bool = False, plays: dict[int, tuple[str, ...]] | None = None, end: str | None = None) -> dict:
    """The dated ledger. `bank` ends a round at that balance (flat); `rets` replaces the trades' returns (a shuffle);
    `down` lets phase 2 fall back to phase 1's spread when one fast contract is out of reach; `plays` replaces PHASES;
    `end` is the data's last day (where a round still running stops)."""
    plays = plays or PHASES
    cash, open_, rounds, log = start, [], [], []
    topups = 0.0

    def new_round(day: str) -> dict:
        return {"round": len(rounds) + 1, "from": day, "start": start, "peak": start, "phases": Counter(),
                "at": {str(int(s)): day for s in STEPS if start >= s}, "trades": 0, "end": None, "to": None, "final": None,
                "skips": Counter()}

    cur = new_round(BEGIN)

    def marks(day: str) -> None:
        eq = cash + sum(o["paid"] for o in open_)
        cur["peak"] = max(cur["peak"], eq)
        for s in STEPS:
            if eq >= s and str(int(s)) not in cur["at"]:
                cur["at"][str(int(s))] = day

    def finish(how: str, day: str) -> None:
        nonlocal cur, cash, topups
        cur.update(end=how, to=day, final=round(cash, 2))
        rounds.append(cur)
        topups += start - cash
        cash = start
        cur = new_round(day)

    def release(upto: str) -> None:
        nonlocal cash
        open_.sort(key=lambda o: o["back"])
        while open_ and open_[0]["back"] <= upto:
            o = open_.pop(0)
            cash += o["proceeds"]
            marks(o["back"])
            if not open_ and cash < ZERO:
                finish("zero", o["back"])
            elif not open_ and bank and cash >= bank:
                finish("hero", o["back"])

    for i, t in enumerate(ev):
        release(t["day"])
        ret = t["ret"] if rets is None else rets[i]
        eq = cash + sum(o["paid"] for o in open_)
        ph = phase(eq)
        line = t["line"]
        use = plays[ph] + (("spread",) if down and ph == 2 else ())
        d = {"day": t["day"], "line": line, "und": t["und"], "phase": ph, "cash": round(cash, 2), "equity": round(eq, 2), "ret": ret}
        if line not in use:
            d["why"] = "阶段不对"
        elif any(o["line"] == line and o["und"] == t["und"] for o in open_):
            d["why"] = "同一只拿着"
        elif down and ph == 2 and line == "spread" and size("fast", 2, cash, eq, _fast_cost(ev, i))[0] >= 1:
            d["why"] = "阶段不对"   # phase 2 falls back to spreads only while a fast contract is out of reach
        else:
            n, allin = size(line, ph, cash, eq, t["cost"])
            if n < 1 and not open_ and ph < 3 and t["cost"] <= start:
                # the engine's dead zone (hero/sleeves.py Book.dead_zone_restart): a zero now, then this trade from the new round
                cur["skips"]["归零重来"] += 1
                finish("zero", t["day"])
                eq, ph = cash, phase(cash)
                use = plays[ph] + (("spread",) if down and ph == 2 else ())
                d.update(phase=ph, cash=round(cash, 2), equity=round(eq, 2), restart=True)
                n, allin = size(line, ph, cash, eq, t["cost"]) if line in use else (0, False)
                if line not in use:
                    d["why"] = "阶段不对"
            if "why" not in d and n < 1:
                d["why"] = "买不起"
            elif "why" not in d:
                paid = n * t["cost"]
                cash -= paid
                open_.append({"line": line, "und": t["und"], "back": t["back"], "paid": paid, "proceeds": round(paid * (1 + ret), 2)})
                cur["phases"][str(ph)] += 1
                cur["trades"] += 1
                d.update(why="选了", n=n, paid=round(paid, 2), all_in=allin, round=cur["round"])
        if d["why"] != "选了":
            cur["skips"][d["why"]] += 1
        log.append(d)
    release("9999-12-31")
    cur.update(end="running", to=end or (ev[-1]["day"] if ev else BEGIN), final=round(cash, 2))
    rounds.append(cur)
    for r in rounds:
        r["phases"], r["skips"] = dict(r["phases"]), dict(r["skips"])
    return {"start": start, "bank": bank, "rounds": rounds, "log": log, "final": round(cash, 2), "topups": round(topups, 2)}


def _fast_cost(ev: list[dict], i: int) -> float:
    """The last fast contract's price on or before event i (what phase 2 would pay today)."""
    for t in reversed(ev[: i + 1]):
        if t["line"] == "fast":
            return t["cost"]
    return next((t["cost"] for t in ev if t["line"] == "fast"), float("inf"))


def months(a: str, b: str) -> float:
    return round((date.fromisoformat(b) - date.fromisoformat(a)).days / 30.44, 1)


def rounds_view(rep: dict) -> list[dict]:
    out = []
    for r in rep["rounds"]:
        hit = r["at"].get("10000")
        out.append({"round": r["round"], "from": r["from"], "to": r["to"], "end": r["end"], "months": months(r["from"], r["to"]),
                    "to_2k": months(r["from"], r["at"]["2000"]) if "2000" in r["at"] else None,
                    "to_10k": months(r["from"], hit) if hit else None, "phases": r["phases"], "trades": r["trades"],
                    "peak": round(r["peak"], 2), "final": r["final"], "skips": r["skips"]})
    return out


def shuffled_rets(ev: list[dict], rng: random.Random) -> list[float]:
    """Each play's returns shuffled over its own signal days (the day, the name and the price stay)."""
    rets = [t["ret"] for t in ev]
    slots: dict[str, list[int]] = {}
    for i, t in enumerate(ev):
        slots.setdefault(t["line"], []).append(i)
    for idx in slots.values():
        xs = [ev[i]["ret"] for i in idx]
        rng.shuffle(xs)
        for i, x in zip(idx, xs):
            rets[i] = x
    return rets


def shuffles(ev: list[dict], start: float, sims: int = 2000, seed: int = 7, **kw) -> dict:
    """The same dated ledger `sims` times on shuffled returns: rounds that stop at $10,000 (the hero game) for the
    rates and times, the ledger that keeps climbing for where the money ends."""
    rng = random.Random(seed)
    rounds, heroes, t10, t2, first10, phase2, finals = 0, 0, [], [], 0, Counter(), []
    for _ in range(sims):
        rets = shuffled_rets(ev, rng)
        rs = ladder(ev, start, STEPS[1], rets, **kw)["rounds"]
        climb = ladder(ev, start, None, rets, **kw)
        finals.append(climb["final"] - climb["topups"])   # net of what each restart put back in
        first10 += "10000" in rs[0]["at"]
        for r in rs:
            hit = "10000" in r["at"] and start < STEPS[1]
            rounds += 1
            heroes += hit
            if hit:
                t10.append(months(r["from"], r["at"]["10000"]))
            if start < STEPS[0] and "2000" in r["at"]:
                t2.append(months(r["from"], r["at"]["2000"]))
            if "2000" in r["at"]:
                phase2["hero" if hit else r["end"]] += 1
    q = lambda xs, p: sorted(xs)[min(len(xs) - 1, int(p * len(xs)))] if xs else None
    p2 = sum(phase2.values())
    return {"sims": sims, "rounds": rounds, "heroes": heroes, "p_hero": round(heroes / rounds, 3) if rounds else None,
            "p_first_10k": round(first10 / sims, 3), "to_10k": {"n": len(t10), "p10": q(t10, 0.1), "median": q(t10, 0.5), "p90": q(t10, 0.9)},
            "to_2k": {"n": len(t2), "p10": q(t2, 0.1), "median": q(t2, 0.5), "p90": q(t2, 0.9)},
            "phase2": {k: round(v / p2, 3) for k, v in phase2.items()} if p2 else {}, "phase2_n": p2,
            "final": {"p10": q(finals, 0.1), "median": q(finals, 0.5), "p90": q(finals, 0.9),
                      "p_10k": round(sum(x >= STEPS[1] for x in finals) / sims, 3), "p_under_start": round(sum(x < start for x in finals) / sims, 3)}}


VARIANTS = {"ladder": ("阶梯（任务的规则）", {}),
            "down": ("阶梯，阶段二买不起快线时退回价差", {"down": True}),
            "spread_only": ("对照：一直只做价差押满", {"plays": {1: ("spread",), 2: ("spread",), 3: ("spread",)}})}


def run(docs: dict | None = None, sims: int = 2000) -> dict:
    docs = docs or {"spread": latest("bottom-spread"), "2m": latest("bottom-2m"), "long": latest("bottom-long")}
    ev = events(docs)
    end = min(d.get("end") or "9999" for d in docs.values())
    fast = [t for t in ev if t["line"] == "fast"]
    out = {"from": BEGIN, "end": end, "events": {k: sum(t["line"] == k for t in ev) for k in LINES}, "variants": {},
           "fast_cost": {y: [min(t["cost"] for t in fast if t["day"][:4] == y), max(t["cost"] for t in fast if t["day"][:4] == y)]
                         for y in sorted({t["day"][:4] for t in fast})}}
    for key, (name, kw) in VARIANTS.items():
        v = {"name": name, "starts": {}}
        for s0 in STARTS:
            hero = ladder(ev, s0, STEPS[1], end=end, **kw)
            climb = ladder(ev, s0, None, end=end, **kw)
            v["starts"][str(int(s0))] = {
                "hero": {"rounds": rounds_view(hero), "final": hero["final"]},
                "climb": {"rounds": rounds_view(climb), "final": climb["final"], "topups": climb["topups"],
                          "log": climb["log"] if key == "ladder" and s0 == 500 else None},
                "shuffled": shuffles(ev, s0, sims, **kw)}
        out["variants"][key] = v
    return out


def markdown(rep: dict) -> str:
    m = lambda x: "—" if x is None else f"${x:,.0f}"
    mo = lambda x: "—" if x is None else f"{x:.1f} 个月"
    pc = lambda x: "—" if x is None else f"{x:.0%}"
    ends = {"hero": "到 $10,000", "zero": "归零", "running": "还在跑"}
    e = rep["events"]
    L = [f"# 阶梯账本：$500 起按余额换玩法 {date.today().isoformat()}", "",
         f"{rep['from']} 至 {rep['end']}，三个研究文件里已有的逐笔收益按日期重放（买卖各多付 5%，不重新下载数据）："
         f"四只 ETF 第一次跌 8% 的 $5 价差 {e['spread']} 笔、SMH 连跌第 3 天的平值 {e['fast']} 笔、跌 8% 的月度平值拿 63 天 {e['atm63']} 笔。", "",
         "- 阶段一（< $2,000）：价差押满，到 $4 卖否则到期（钱 91 天或到期回来）。",
         "- 阶段二（$2,000–10,000）：SMH 快线押满，2 倍止盈否则第 20 天（研究没记出场日，钱按 28 天回来，提前止盈的也按 28 天算）。",
         "- 阶段三（≥ $10,000）：A 袋（余额一半）平值拿 63 天押一半；B 袋（四分之一）价差押满；C 袋（四分之一）快线押一半。",
         "- 阶段按出信号那天的余额（现金加持仓成本）定；拿着的按原玩法出场。只买整张；袋子的那份买不起一张、袋子付得起就买一张（引擎口径）；"
         "空仓时全部现金连一张都买不起、或剩不到 $100，就算归零，从起始金额重来。同一玩法同一只拿着不再买。", "",
         "「到 $10,000 收手」：到 $10,000 就算一轮成功、从头再来（hero 游戏的口径）；「继续爬」：到 $10,000 不收手，进阶段三。只研究，不改交易。", ""]
    for key, v in rep["variants"].items():
        L += [f"## {v['name']}", ""] + (["阶段只是余额的档位：三档都只做价差（阶段三按 B 袋押四分之一）。", ""] if key == "spread_only" else [])
        for s0, st in v["starts"].items():
            L += [f"### ${int(s0):,} 起步", "", "按日期（到 $10,000 收手）：", "",
                  "| 轮 | 从 | 到 | 结果 | 用时 | 到 $2,000 | 到 $10,000 | 各阶段笔数 | 峰值 | 结束时 | 没做的（原因：次数） |", "|---|---|---|---|---|---|---|---|---|---|---|"]
            for r in st["hero"]["rounds"]:
                ph = "、".join(f"阶段{k} {n} 笔" for k, n in sorted(r["phases"].items())) or "—"
                sk = "、".join(f"{k} {n}" for k, n in sorted(r["skips"].items(), key=lambda x: -x[1])) or "—"
                to2 = "起步就是" if float(s0) >= STEPS[0] else mo(r["to_2k"])
                L.append(f"| {r['round']} | {r['from']} | {r['to']} | {ends[r['end']]} | {mo(r['months'])} | {to2} | {mo(r['to_10k'])} | {ph} | {m(r['peak'])} | {m(r['final'])} | {sk} |")
            hr = st["hero"]["rounds"]
            L += ["", f"到 $10,000 的轮 / 全部轮：{sum(r['end'] == 'hero' for r in hr)} / {len(hr)}（最后一轮还在跑也算一轮）。", ""]
            c = st["climb"]
            last = c["rounds"][-1]
            L += [f"继续爬（到 $10,000 不收手）：最后 {m(c['final'])}（归零重来补进去 {m(c['topups'])}），共 {len(c['rounds'])} 轮（归零 {sum(r['end'] == 'zero' for r in c['rounds'])} 次）；"
                  f"最后一轮 {last['from']} 起，各阶段 " + ("、".join(f"阶段{k} {n} 笔" for k, n in sorted(last["phases"].items())) or "—") + "。", ""]
            sh = st["shuffled"]
            p2 = "、".join(f"{ends.get(k, k)} {pc(x)}" for k, x in sorted(sh["phase2"].items(), key=lambda x: -x[1]))
            L += [f"打乱 {sh['sims']:,} 次（每个玩法的收益在它自己的信号日之间打乱，价格和日期不动）：", "",
                  f"- 第一轮在这两年八个月里到 $10,000 的概率 **{pc(sh['p_first_10k'])}**。",
                  f"- 所有轮（到 $10,000 就从 ${int(s0):,} 重来，最后一轮没跑完也算）里到 $10,000 的：{pc(sh['p_hero'])}（{sh['heroes']:,} / {sh['rounds']:,}）。",
                  f"- 到 $10,000 用时：中位数 {mo(sh['to_10k']['median'])}（最快一成 {mo(sh['to_10k']['p10'])}、最慢一成 {mo(sh['to_10k']['p90'])}，{sh['to_10k']['n']:,} 次）。"]
            if sh["to_2k"]["n"]:
                L.append(f"- 到 $2,000（走完阶段一）用时：中位数 {mo(sh['to_2k']['median'])}（最快一成 {mo(sh['to_2k']['p10'])}、最慢一成 {mo(sh['to_2k']['p90'])}，{sh['to_2k']['n']:,} 次）。")
            f = sh["final"]
            L += [f"- 进了阶段二（到过 $2,000）的轮（{sh['phase2_n']:,} 个）怎么结束：{p2}。",
                  f"- 继续爬的账本期末，扣掉每次归零重来补进去的钱：中位数 {m(f['median'])}，最差一成 {m(f['p10'])}，最好一成 {m(f['p90'])}；"
                  f"在 $10,000 以上的 {pc(f['p_10k'])}，比起步少的 {pc(f['p_under_start'])}。", ""]
    fc = "、".join(f"{y} 年 ${lo:,.0f}–{hi:,.0f}" for y, (lo, hi) in rep.get("fast_cost", {}).items())
    sh = lambda key, s0: rep["variants"][key]["starts"][s0]["shuffled"]
    cmp = "；".join(f"${int(s0):,} 起 {pc(sh('ladder', s0)['p_first_10k'])} 对 {pc(sh('spread_only', s0)['p_first_10k'])}"
                   for s0 in rep["variants"]["ladder"]["starts"] if "spread_only" in rep["variants"])
    L += ["## 读法", "",
          "- 期权数据只有 2024-02 起两年八个月，多数时间在涨；打乱只打乱这段历史里的结果，不是预测。",
          f"- 价格是当时的价格：SMH 快线一张 {fc}；阶段二在后面常常只买得起一张、押上整个账本。",
          f"- 第一轮到 $10,000 的概率，阶梯对一直只做价差：{cmp}。阶梯多出来的只在最好的一成。",
          "- 快线提前止盈的钱按第 28 天回来算（研究没记出场日），所以阶段二偏慢。"]
    return "\n".join(L) + "\n"


def main() -> None:
    rep = run()
    md = markdown(rep)
    save("ladder", rep, md)


if __name__ == "__main__":
    sys.exit(main())
