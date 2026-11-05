"""Is the follow card's logic real? (owner, 11-05: 「认真研究下这个逻辑」)

The card: at 10:00, if SPY has moved 0.5% or more from its open, buy the same-day option 0.3% beyond the price in
the direction of the move; sell half at 3x, trail the rest (40% off the high once it has doubled), else sell at
15:30. The option backtest (research/2026-10-16-zdte-minmove.md) covers 2024-02 on, 52 such days: mean +13%,
median -24%, the mean inside the noise. Two things that sample cannot say, asked here:

1. The signal under the option, over ten years. SPY minute bars since 2016: on each day, the 10:00 move from the
   open, the rest of the day to 15:30 in the direction of that move, how far it ran (the high or low after 10:00),
   grouped by the size of the move, up against down, year by year. Shares carry no premium, so this is the
   direction edge alone; the option can only add volatility to it.
2. Direction or volatility? For the 0.5%+ days since 2024-02 (where option bars exist), the card's contract and the
   contract on the OTHER side (a call on a down day, a put on an up day), same entry and exits. If the other side
   also pays, what pays is the big day, not the direction.

Minute closes, 10% over to get in and 10% under to get out as in the sleeve studies. Record only.
Run: python -m hero.research_zdte_logic
"""

from __future__ import annotations

import statistics
import sys
from datetime import date, timedelta

from hero.research_0dte import at_or_before, et_minutes
from hero.research_0dte_names import minutes_by_day, pick
from hero.research_event_turn import Pace
from hero.research_ideas import save
from hero.research_zdte_timing import OUT, play

START = "2016-01-04"
OPT_START = "2024-02-01"
FLOOR = 0.005
OFF = 0.003
BANDS = [(0.0, 0.003, "不到 0.3%"), (0.003, 0.005, "0.3%–0.5%"), (0.005, 0.0075, "0.5%–0.75%"),
         (0.0075, 0.01, "0.75%–1%"), (0.01, 1.0, "1% 以上")]
SIDES = {"all": "全部", "up": "高开走高（买看涨）", "down": "低开走低（买看跌）"}


def day_row(m: dict[str, dict]) -> dict | None:
    """One day's minute bars -> open, 10:00, 15:30, close, and the high / low after 10:00."""
    if "09:30" not in m:
        return None
    b10, b1530 = at_or_before(m, "10:00"), at_or_before(m, OUT)
    if not b10 or not b1530:
        return None
    after = [v for k, v in m.items() if "10:00" < k <= OUT]
    p10 = float(b10["c"])
    return {"o": float(m["09:30"]["o"]), "p10": p10, "p1530": float(b1530["c"]), "c": float(m[max(m)]["c"]),
            "hi": max((float(v["h"]) for v in after), default=p10), "lo": min((float(v["l"]) for v in after), default=p10)}


def shares(client) -> list[dict]:
    end = (date.today() - timedelta(days=1)).isoformat()
    days = minutes_by_day(client, "SPY", START, end)
    out = []
    for day in sorted(days):
        r = day_row(days[day])
        if r:
            out.append({"day": day, **{k: round(v, 4) for k, v in r.items()}})
    return out


def signal(r: dict) -> dict:
    """The move by 10:00 and what followed, signed in the direction of the move."""
    mv = r["p10"] / r["o"] - 1
    s = 1 if mv >= 0 else -1
    run = (r["hi"] / r["p10"] - 1) if s > 0 else (1 - r["lo"] / r["p10"])
    against = (1 - r["lo"] / r["p10"]) if s > 0 else (r["hi"] / r["p10"] - 1)
    return {"move": mv, "side": "up" if s > 0 else "down", "cont": s * (r["p1530"] / r["p10"] - 1),
            "cont_close": s * (r["c"] / r["p10"] - 1), "run": run, "against": against}


def band_of(mv: float) -> str:
    a = abs(mv)
    return next(name for lo, hi, name in BANDS if lo <= a < hi)


def cell(sig: list[dict]) -> dict | None:
    if not sig:
        return None
    c = [x["cont"] for x in sig]
    return {"n": len(c), "cont": sum(1 for x in c if x > 0) / len(c), "mean": statistics.mean(c),
            "median": statistics.median(c), "beyond": sum(1 for x in c if x > OFF) / len(c),
            "reversed": sum(1 for x in c if x < -OFF) / len(c),
            "run06": sum(1 for x in sig if x["run"] >= 0.006) / len(c), "run1": sum(1 for x in sig if x["run"] >= 0.01) / len(c),
            "against06": sum(1 for x in sig if x["against"] >= 0.006) / len(c)}


def shares_table(rows: list[dict]) -> dict:
    sig = [{"day": r["day"], **signal(r)} for r in rows]
    t: dict = {"bands": {}, "years": {}, "floor_years": {}, "halves": {}}
    for _, _, name in BANDS:
        for side, label in SIDES.items():
            t["bands"][f"{name}|{side}"] = cell([x for x in sig if band_of(x["move"]) == name and (side == "all" or x["side"] == side)])
    big = [x for x in sig if abs(x["move"]) >= FLOOR]
    for y in sorted({x["day"][:4] for x in sig}):
        t["years"][y] = {"days": sum(1 for x in sig if x["day"][:4] == y), **(cell([x for x in big if x["day"][:4] == y]) or {})}
    for name, keep in (("2016–2019", lambda d: d < "2020"), ("2020–2023", lambda d: "2020" <= d < "2024"), ("2024 起", lambda d: d >= "2024")):
        for side in SIDES:
            t["halves"][f"{name}|{side}"] = cell([x for x in big if keep(x["day"]) and (side == "all" or x["side"] == side)])
    return t


def options(client, rows: list[dict]) -> list[dict]:
    """Both sides' 0.3% same-day contracts on the 0.5%+ days since OPT_START."""
    pace, out = Pace(client), []
    for r in rows:
        day = r["day"]
        mv = r["p10"] / r["o"] - 1
        if day < OPT_START or abs(mv) < FLOOR:
            continue
        spot = r["p10"]
        cs = []
        for status in ("inactive", "active"):
            try:
                cs = client.option_contracts("SPY", status=status, expiration_date_gte=day, expiration_date_lte=day,
                                             strike_price_gte=f"{spot * 0.98:.2f}", strike_price_lte=f"{spot * 1.02:.2f}")
            except Exception:
                cs = []
            if cs:
                break
        if not cs:
            continue
        same_kind = "C" if mv >= 0 else "P"
        syms = {"same": pick(cs, same_kind, spot, OFF), "opp": pick(cs, "P" if same_kind == "C" else "C", spot, OFF)}
        want = [s for s in syms.values() if s]
        if len(want) < 2:
            continue
        pace.wait()
        try:
            bars = client.option_bars(want, f"{day}T13:00:00Z", f"{day}T21:00:00Z", timeframe="1Min")
        except Exception:
            continue
        res = {}
        for leg, s in syms.items():
            got = play(et_minutes(bars.get(s, [])), "10:00") if s in bars else None
            if got:
                res[leg] = {k: got[k] for k in ("hold", "take3", "take3_trail")}
        if len(res) == 2:
            out.append({"day": day, "move": round(mv, 5), "side": "up" if mv >= 0 else "down", "syms": syms, "res": res})
    return out


def legs_table(opt: list[dict]) -> dict:
    t = {}
    for side in SIDES:
        rows = [r for r in opt if side == "all" or r["side"] == side]
        for ex in ("take3_trail", "hold"):
            for leg in ("same", "opp", "both"):
                xs = [(r["res"]["same"][ex] + r["res"]["opp"][ex]) / 2 if leg == "both" else r["res"][leg][ex] for r in rows]
                t[f"{side}|{ex}|{leg}"] = None if not xs else {
                    "n": len(xs), "mean": statistics.mean(xs), "median": statistics.median(xs),
                    "trim": statistics.mean(sorted(xs)[:-3]) if len(xs) > 3 else None,
                    "win": sum(1 for x in xs if x > 0) / len(xs), "x2": sum(1 for x in xs if x >= 1) / len(xs)}
    return t


def pct(v, d=0) -> str:
    return "—" if v is None else f"{v:+.{d}%}"


def markdown(rep: dict) -> str:
    t, lt = rep["shares_table"], rep["legs_table"]
    out = [f"# 跟单卡的逻辑：10:00 走了 0.5% 以上，后面会接着走吗？ {rep['generated']}", "",
           f"SPY 分钟线 {rep['start']} 至 {rep['end']}（{len(rep['shares'])} 个交易日）：每天看 10:00 比开盘走了多远，再看 10:00 到 15:30 "
           "沿着这个方向又走了多少（正数 = 接着走，负数 = 掉头）、10:00 以后顺向最远走到哪、逆向最远走到哪。正股没有期权的时间价值，"
           "所以这里量的是「方向」本身有没有优势；期权只能在这上面加上波动。只研究，不改交易。", "",
           "## 按 10:00 离开盘多远分组（10:00 → 15:30，沿方向）", "",
           "| 10:00 离开盘 | 方向 | 天数 | 接着走的比例 | 平均 | 中位数 | 走过 0.3%（行权价） | 掉头 0.3% 以上 | 顺向最远 ≥0.6% | 顺向最远 ≥1% | 逆向最远 ≥0.6% |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for _, _, name in BANDS:
        for side, label in SIDES.items():
            c = t["bands"].get(f"{name}|{side}")
            if c:
                out.append(f"| {name} | {label} | {c['n']} | {c['cont']:.0%} | {pct(c['mean'], 2)} | {pct(c['median'], 2)} | {c['beyond']:.0%} | "
                           f"{c['reversed']:.0%} | {c['run06']:.0%} | {c['run1']:.0%} | {c['against06']:.0%} |")
    out += ["", "## 0.5% 以上的日子，分时期", "",
            "| 时期 | 方向 | 天数 | 接着走的比例 | 平均 | 中位数 | 走过 0.3% | 掉头 0.3% 以上 | 顺向最远 ≥1% |", "|---|---|---|---|---|---|---|---|---|"]
    for key, c in t["halves"].items():
        if c:
            name, side = key.split("|")
            out.append(f"| {name} | {SIDES[side]} | {c['n']} | {c['cont']:.0%} | {pct(c['mean'], 2)} | {pct(c['median'], 2)} | {c['beyond']:.0%} | {c['reversed']:.0%} | {c['run1']:.0%} |")
    out += ["", "## 每年有多少天触发（0.5% 以上），那些天后面怎么走", "",
            "| 年 | 交易日 | 触发天数 | 接着走的比例 | 平均 | 走过 0.3% | 顺向最远 ≥1% |", "|---|---|---|---|---|---|---|"]
    for y, c in t["years"].items():
        if c.get("n"):
            out.append(f"| {y} | {c['days']} | {c['n']} | {c['cont']:.0%} | {pct(c['mean'], 2)} | {c['beyond']:.0%} | {c['run1']:.0%} |")
        else:
            out.append(f"| {y} | {c['days']} | 0 | — | — | — | — |")
    out += ["", f"## 方向还是波动？0.5% 以上的日子，两边的当天到期期权（{OPT_START} 起，{len(rep['options'])} 天）", "",
            "卡片那边 = 顺着 10:00 方向的那张（0.3% 外）；另一边 = 反方向那张；两边各一半 = 两张各押 $0.5。出场同卡片（一半 3 倍、其余翻倍后回撤 40%，否则 15:30）和「拿到 15:30」。",
            "", "| 方向 | 出场 | 哪张 | 天数 | 平均每 $1 | 去掉最好 3 笔 | 中位数 | 赚钱比例 | 到 2 倍 |", "|---|---|---|---|---|---|---|---|---|"]
    for side, label in SIDES.items():
        for ex, exn in (("take3_trail", "卡片的卖法"), ("hold", "拿到 15:30")):
            for leg, ln in (("same", "卡片那边"), ("opp", "另一边"), ("both", "两边各一半")):
                c = lt.get(f"{side}|{ex}|{leg}")
                if c:
                    out.append(f"| {label} | {exn} | {ln} | {c['n']} | {pct(c['mean'])} | {pct(c['trim'])} | {pct(c['median'])} | {c['win']:.0%} | {c['x2']:.0%} |")
    out += ["", "每天一行（0.5% 以上，期权两边）：", "", "| 日期 | 10:00 离开盘 | 卡片那边（卡片卖法 / 15:30） | 另一边（卡片卖法 / 15:30） |", "|---|---|---|---|"]
    for r in rep["options"]:
        s, o = r["res"]["same"], r["res"]["opp"]
        out.append(f"| {r['day']} | {r['move']:+.2%} | {s['take3_trail']:+.0%} / {s['hold']:+.0%} | {o['take3_trail']:+.0%} / {o['hold']:+.0%} |")
    return "\n".join(out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    client = Alpaca()
    rows = shares(client)
    opt = options(client, rows)
    rep = {"generated": date.today().isoformat(), "start": START, "end": rows[-1]["day"] if rows else None,
           "shares": rows, "shares_table": shares_table(rows), "options": opt, "legs_table": legs_table(opt)}
    save("zdte-logic", rep, markdown(rep))


if __name__ == "__main__":
    sys.exit(main())
