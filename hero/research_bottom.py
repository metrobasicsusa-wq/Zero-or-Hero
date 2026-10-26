"""Bottom timing on the index ETFs (owner, 10-26: 「抄底 我需要个抄底时机 这也是一个 hero 方式」).

The dip engines so far buy a single name's gap-down (dip-week) or SPY after one down day (spydip: no edge). This asks
the market-level question: after which kind of selling is next week's call on the index worth buying? SPY, QQQ, SMH,
IWM, 2024-02 on, daily bars. Signals, each taken once per ETF and not again for five sessions: the close first
crossing 3% / 5% / 8% under the 20-session high (dd3 / dd5 / dd8); the third and fourth straight down close (down3 /
down4); a two-day drop of 3%+ (SMH 6%+; drop2d); a single-day drop of 2%+ (SMH 4%+; bigdown); the first up close
after three or more down closes (green1); an open 1%+ under the prior close that closes above its open (reclaim, the
same-day bottom that held); a VIXY close 25%+ above its 10-session low (vix25) or 30%+ above three sessions earlier
(vix30); and every fifth session as the control (every5: the same call on an ordinary day). Legs, exits and costs are
dip-week's (hero.research_dip_week.legs: the nearest 5-12 day call at the money and 3% out, sold on the option's daily
closes; 5% a side, and 10%). Then the robustness checks, the SPY realised-vol regime of each signal and the hero game
(rounds to 20x, half and all in). A time budget (RESEARCH_BUDGET_MIN) keeps a partial run committable. Research only."""
from __future__ import annotations

import os
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from hero.research_dip_week import EXITS, START, legs
from hero.research_event_turn import Pace
from hero.research_hero_game import game, interval
from hero.research_ideas import report, save
from hero.research_vol_regime import realised

ROOT = Path(__file__).resolve().parent.parent
ETFS = ("SPY", "QQQ", "SMH", "IWM")
ONE_DAY = {"SMH": 0.04}       # bigdown threshold (default 2%)
TWO_DAY = {"SMH": 0.06}       # drop2d threshold (default 3%)
GAP = 5
BUDGET_MIN = float(os.environ.get("RESEARCH_BUDGET_MIN", "115"))
LABEL = {"dd3": "收盘首次跌到 20 日高点下 3%", "dd5": "跌到 20 日高点下 5%", "dd8": "跌到 20 日高点下 8%",
         "down3": "连跌第 3 天", "down4": "连跌第 4 天", "drop2d": "两天跌 3%+（SMH 6%+）", "bigdown": "单日跌 2%+（SMH 4%+）",
         "green1": "连跌 3 天后第一根阳线", "reclaim": "低开 1%+ 收回开盘价之上", "vix25": "VIXY 比 10 日最低高 25%+",
         "vix30": "VIXY 三天涨 30%+", "every5": "对照：每第 5 天随便买"}


def signals(days: list[str], opens: list[float], closes: list[float], und: str = "SPY",
            vix: dict[str, float] | None = None) -> list[dict]:
    """Every (signal, day) on this ETF's tape, each signal at most once per GAP sessions."""
    out, last = [], {}
    one, two = ONE_DAY.get(und, 0.02), TWO_DAY.get(und, 0.03)
    vdays = sorted(vix) if vix else []

    def streak_at(i: int) -> int:
        k = 0
        while i - k >= 1 and closes[i - k] < closes[i - k - 1]:
            k += 1
        return k

    for i in range(21, len(days)):
        hit = []
        dd = closes[i] / max(closes[i - 20:i + 1]) - 1
        dd_prev = closes[i - 1] / max(closes[i - 21:i]) - 1
        for th, name in ((0.03, "dd3"), (0.05, "dd5"), (0.08, "dd8")):
            if dd <= -th < dd_prev:
                hit.append(name)
        s = streak_at(i)
        if s == 3:
            hit.append("down3")
        if s == 4:
            hit.append("down4")
        if closes[i] / closes[i - 2] - 1 <= -two and not closes[i - 1] / closes[i - 3] - 1 <= -two:
            hit.append("drop2d")
        if closes[i] / closes[i - 1] - 1 <= -one:
            hit.append("bigdown")
        if closes[i] > closes[i - 1] and streak_at(i - 1) >= 3:
            hit.append("green1")
        if opens[i] <= closes[i - 1] * 0.99 and closes[i] > opens[i]:
            hit.append("reclaim")
        if vix and days[i] in vix:
            vs = [vix[d] for d in vdays if d < days[i]][-10:]
            v3 = [vix[d] for d in vdays if d < days[i]][-3:]
            if len(vs) >= 10:
                spike = vix[days[i]] >= 1.25 * min(vs)
                prev_spike = vs[-1] >= 1.25 * min(vs[:-1]) if len(vs) >= 2 else False
                if spike and not prev_spike:
                    hit.append("vix25")
            if len(v3) >= 3 and vix[days[i]] / v3[0] - 1 >= 0.30:
                prev = [vix[d] for d in vdays if d < days[i]][-4:]
                if not (len(prev) >= 4 and prev[-1] / prev[0] - 1 >= 0.30):
                    hit.append("vix30")
        if i % 5 == 0:
            hit.append("every5")
        for name in hit:
            if i - last.get(name, -99) < GAP:
                continue
            last[name] = i
            out.append({"day": days[i], "i": i, "sig": name, "und": und, "dd": round(dd, 4), "streak": s,
                        "stock": {f"s{k}": round(closes[i + k] / closes[i] - 1, 4) for k in (1, 3, 5, 10) if i + k < len(closes)}})
    return out


def run(client, budget_min: float = BUDGET_MIN) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    syms = list(ETFS) + ["VIXY"]
    split = client.daily_bars(syms, "2023-12-01", adjustment="split", feed="sip")
    raw = client.daily_bars(syms, "2023-12-01", adjustment="raw", feed="sip")
    vix = {b["t"][:10]: float(b["c"]) for b in split.get("VIXY", []) if b["t"][:10] <= end}
    rows, missing, done, t0 = [], 0, [], time.monotonic()
    pace = Pace(client)
    for und in ETFS:
        bs = [b for b in split.get(und, []) if b["t"][:10] <= end]
        if len(bs) < 30:
            done.append(und)
            continue
        days = [b["t"][:10] for b in bs]
        stock = {b["t"][:10]: float(b["c"]) for b in raw.get(und, [])}
        stop = False
        for e in signals(days, [float(b["o"]) for b in bs], [float(b["c"]) for b in bs], und, vix):
            if e["day"] < START:
                continue
            if (time.monotonic() - t0) / 60 > budget_min:
                stop = True
                break
            got = legs(client, und, e["day"], stock.get(e["day"], 0.0) or float(bs[e["i"]]["c"]), end, days[e["i"] + 1:], stock)
            if not got:
                missing += 1
                rows.append({**e, "leg": None, "res": {}})
            for name, leg in got.items():
                rows.append({**e, "leg": name, **leg})
            pace.wait()
        if stop:
            break
        done.append(und)
    return {"start": START, "end": end, "etfs": list(ETFS), "done": done, "partial": len(done) < len(ETFS), "rows": rows,
            "missing": missing, "minutes": round((time.monotonic() - t0) / 60, 1)}


def groups(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for sig, label in LABEL.items():
        for leg, lname in (("atm", "平值"), ("otm3", "价外 3%")):
            rs = [r for r in rows if r.get("sig") == sig and r.get("leg") == leg]
            if rs:
                out[f"{label} · {lname}"] = rs
    for sig in ("dd5", "down3", "green1", "vix25", "bigdown"):
        for und in ETFS:
            rs = [r for r in rows if r.get("sig") == sig and r.get("leg") == "atm" and r.get("und") == und]
            if rs:
                out[f"　{LABEL[sig]} · {und} · 平值"] = rs
    return out


def series(rows: list[dict], sig: str, leg: str, exit_: str, key: str = "res", und: str | None = None) -> list[tuple[str, float]]:
    xs = [(r["day"], r[key][exit_]) for r in rows if r.get("sig") == sig and r.get("leg") == leg and (r.get(key) or {}).get(exit_) is not None
          and (und is None or r.get("und") == und)]
    return sorted(xs)


def hero_table(rows: list[dict]) -> tuple[dict, str]:
    out = {}
    lines = ["", "## hero 游戏：每个信号按日期顺序重放成轮（四只 ETF 合在一起，同一天几只都触发就都做），每轮到 20 倍", "",
             "| 信号 · 腿 · 出场 · 成本 | 笔数 | 平均 | 押一半：到 20 倍 / 轮（区间） | 每个 hero 的成本 | 押满：到 20 倍 / 轮（区间） | 每个 hero 的成本 |",
             "|---|---|---|---|---|---|---|"]
    for sig, label in LABEL.items():
        for leg, lname in (("atm", "平值"), ("otm3", "价外 3%")):
            for ex in ("d5", "trail2"):
                for key, cost in (("res", "5%"), ("res10", "10%")):
                    xs = [x for _, x in series(rows, sig, leg, ex, key)]
                    if len(xs) < 15:
                        continue
                    g5, g10 = game(xs, 0.5), game(xs, 1.0)
                    c5, c10 = interval(g5["heroes"], g5["rounds"]) or (0, 0), interval(g10["heroes"], g10["rounds"]) or (0, 0)
                    out[f"{sig}|{leg}|{ex}|{cost}"] = {"n": len(xs), "mean": statistics.mean(xs), "half": g5, "full": g10}
                    lines.append(f"| {label} · {lname} · {ex} · {cost} | {len(xs)} | {statistics.mean(xs):+.0%} | "
                                 f"{g5['heroes']}/{g5['rounds']}（{c5[0]:.0%}–{c5[1]:.0%}） | {('$%s' % format(g5['cost_per_hero'], ',.0f')) if g5['cost_per_hero'] else '—'} | "
                                 f"{g10['heroes']}/{g10['rounds']}（{c10[0]:.0%}–{c10[1]:.0%}） | {('$%s' % format(g10['cost_per_hero'], ',.0f')) if g10['cost_per_hero'] else '—'} |")
    return out, "\n".join(lines) + "\n"


def regime_table(rows: list[dict], vol: dict[str, float]) -> tuple[dict, str]:
    """Each signal's at-the-money, fifth-close result by the SPY realised-vol third it fired in (known the evening before)."""
    vdays = sorted(d for d in vol if d >= START)
    if len(vdays) < 30:
        return {}, ""
    vs = sorted(vol[d] for d in vdays)
    lo, hi = vs[len(vs) // 3], vs[2 * len(vs) // 3]

    def regime(day: str) -> str | None:
        ks = [d for d in vdays if d < day]
        if not ks:
            return None
        v = vol[ks[-1]]
        return "高" if v > hi else ("低" if v < lo else "中")
    out = {}
    lines = ["", f"## 按 SPY 20 日实现波动分档（低 <{lo:.1%}、高 >{hi:.1%}，信号前一晚已知）：平值、第 5 天卖、5% 成本，平均每 $1（笔数）", "",
             "| 信号 | 低波动 | 中 | 高波动 |", "|---|---|---|---|"]
    for sig, label in LABEL.items():
        cells = []
        by = {"低": [], "中": [], "高": []}
        for d, x in series(rows, sig, "atm", "d5"):
            g = regime(d)
            if g:
                by[g].append(x)
        for g in ("低", "中", "高"):
            xs = by[g]
            cells.append(f"{statistics.mean(xs):+.0%}（{len(xs)}）" if len(xs) >= 5 else "—")
        out[sig] = {g: {"n": len(by[g]), "mean": (statistics.mean(by[g]) if by[g] else None)} for g in by}
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    return out, "\n".join(lines) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    c = Alpaca()
    rep = run(c)
    gs = groups(rep["rows"])
    table, md = report(f"抄底时机：指数 ETF 的哪种下跌之后买下周的看涨 {date.today().isoformat()}",
                       f"{rep['start']} 至 {rep['end']}，SPY / QQQ / SMH / IWM（时间预算 {BUDGET_MIN:.0f} 分钟，跑完 {len(rep['done'])} 只"
                       f"{'，没跑完' if rep['partial'] else ''}）。信号（每只 ETF 每种信号 5 天内只记一次）：收盘首次跌到 20 日高点下 3% / 5% / 8%、"
                       "连跌第 3 / 4 天、两天跌 3%+、单日跌 2%+、连跌 3 天后第一根阳线、低开 1%+ 收回开盘价、VIXY 比 10 日最低高 25%+、VIXY 三天涨 30%+，"
                       "和「每第 5 天随便买」的对照组。信号当天收盘买最近的 5–12 天到期看涨（平值 / 价外 3%），按期权每日收盘价卖，买卖各算 5% 成本（res10：各 10%）。"
                       f"没有期权数据的事件 {rep['missing']} 个。", gs, EXITS, rep["end"])
    hero, hero_md = hero_table(rep["rows"])
    spy = c.daily_bars(["SPY"], "2023-12-01", adjustment="split", feed="sip").get("SPY") or []
    reg, reg_md = regime_table(rep["rows"], realised(spy))
    md += hero_md + reg_md + ("\n## 读法\n\n- 先看对照组：信号组要比「随便买」好才算时机，不只是比零好。\n"
                               "- 轮数少，区间比点估计重要；低波动期为负、高波动期为正的信号，门槛和抄底给一周一样挂在 SPY 波动上。\n")
    rep["hero"], rep["regime"] = hero, reg
    save("bottom", rep, md)


if __name__ == "__main__":
    sys.exit(main())
