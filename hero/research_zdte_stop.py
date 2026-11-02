"""Research: should a same-day option carry a stop? (claude-b's question; owner 11-02: 「做一期同日期权止损的回测」)

Our 0DTE ledgers have no loss stop and no time stop: a 3x take, out at 15:30 either way (half_take ledgers: half on
the take, half on a trail after 2x). On 11-02 a 0.2-delta same-day AMZN call went 0.43 -> 0.61 -> 0.08 while the
stock gave back only 0.5%. Same sample as research/2026-10-13-zdte-timing.md: SPY, QQQ, TSLA, NVDA, META every
weekday since 2024-02, the day's expiry (else the nearest within a week), a call when the 10:00 price is above the
open else a put, the first strike 0.3% and 0.6% beyond the price, bought at 10:00, judged on one-minute closes,
paying SLIP over to get in and under to get out. Each ticket is played through:
  take3             3x take, else 15:30 (the live rule)
  stop30 / 50 / 70  take3 plus sell once a minute close is 30% / 50% / 70% under the cost
  back_open         take3 plus sell once the underlying closes back through its 09:30 open (the direction is gone)
  back_entry        take3 plus sell once the underlying closes back through its 10:00 price
  time12 / time13   take3 plus sell at 12:00 / 13:00 if the option is under its cost
  stop50_back_open  both stops
  take3_trail       3x on half, the rest on a trail (2x then a 40% giveback), for reference
Reported per $1 (mean, median, the share ending at 2x or more), hero/robust.py checks, the sleeve game of
research_zdte_timing (half the ledger a ticket), how often each stop fires and how many of the tickets it fires on
would have ended at 2x or more under take3 anyway (the winners a stop kills). Record only, no rule changes.
Run: python -m hero.research_zdte_stop
"""

from __future__ import annotations

import bisect
import json
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from hero import robust
from hero.research_0dte import SLIP, at_or_after, at_or_before, et_minutes
from hero.research_0dte_names import minutes_by_day, pick
from hero.research_ideas import save
from hero.research_zdte_timing import NAMES, OUT, START, game, out_px

ENTRY = "10:00"
OFFS = (0.003, 0.006)
VARIANTS = ("take3", "stop30", "stop50", "stop70", "back_open", "back_entry", "time12", "time13", "stop50_back_open", "take3_trail")
ZH = {"take3": "3 倍卖、15:30 平（现行）", "stop30": "+ 亏 30% 止损", "stop50": "+ 亏 50% 止损", "stop70": "+ 亏 70% 止损",
      "back_open": "+ 标的跌回开盘价就卖", "back_entry": "+ 标的跌回 10:00 价就卖", "time12": "+ 12:00 没赚就卖",
      "time13": "+ 13:00 没赚就卖", "stop50_back_open": "+ 亏 50% 或跌回开盘价", "take3_trail": "3 倍卖一半、其余跟踪（参考）"}
SPEC = {"take3": {}, "stop30": {"stop": 0.3}, "stop50": {"stop": 0.5}, "stop70": {"stop": 0.7},
        "back_open": {"back": "open"}, "back_entry": {"back": "entry"}, "time12": {"tstop": "12:00"}, "time13": {"tstop": "13:00"},
        "stop50_back_open": {"stop": 0.5, "back": "open"}}


def play_all(opt: dict[str, dict], und: dict[str, dict], kind: str, open_px: float,
             entry: str = ENTRY) -> dict | None:
    """Per $1 and the exit reason for every variant, on minute closes after the entry minute.
    `und` is the underlying's minute bars (for the give-back stops), `kind` C or P, `open_px` its 09:30 open."""
    got = at_or_after(opt, entry)
    if not got:
        return None
    t0, bar = got
    c0 = float(bar["c"])
    if c0 <= 0:
        return None
    cost = c0 + max(c0 * SLIP, 0.01)
    path = [(k, float(v["c"])) for k, v in sorted(opt.items()) if t0 < k <= OUT]
    if not path:
        return None
    ub = at_or_before(und, entry)
    spot0 = float(ub["c"]) if ub else None
    ukeys = sorted(k for k in und if k <= OUT)
    ucl = [float(und[k]["c"]) for k in ukeys]

    def und_at(k: str) -> float | None:
        i = bisect.bisect_right(ukeys, k) - 1
        return ucl[i] if i >= 0 else None

    def gone(level: float, u: float) -> bool:
        return u <= level if kind == "C" else u >= level

    hold = out_px(path[-1][1]) / cost - 1

    def run(stop=None, back=None, tstop=None) -> tuple[float, str]:
        level = {"open": open_px, "entry": spot0, None: None}[back]
        for k, c in path:
            if c >= 3 * cost:
                return 2.0, "take"
            if stop is not None and c <= (1 - stop) * cost:
                return out_px(c) / cost - 1, "stop"
            if level is not None:
                u = und_at(k)
                if u is not None and gone(level, u):
                    return out_px(c) / cost - 1, "back"
            if tstop is not None and k >= tstop and c < cost:
                return out_px(c) / cost - 1, "time"
        return hold, "close"

    res, why = {}, {}
    for name, spec in SPEC.items():
        res[name], why[name] = run(**spec)
    best, armed, trail = 0.0, False, hold
    for _, c in path:
        best = max(best, c)
        armed = armed or c >= 2 * cost
        if armed and c <= 0.6 * best:
            trail = out_px(c) / cost - 1
            break
    res["take3_trail"] = (2.0 + trail) / 2 if res["take3"] == 2.0 else trail
    res["hold"] = hold
    why["take3_trail"] = "half" if res["take3"] == 2.0 else ("trail" if trail != hold else "close")
    return {"res": {k: round(v, 4) for k, v in res.items()}, "why": why, "cost": round(cost, 3),
            "min_ratio": round(min(c for _, c in path) / cost, 3), "max_ratio": round(max(c for _, c in path) / cost, 3)}


def gather(client, pace=None, names: tuple[str, ...] = NAMES, start: str = START, end: str | None = None,
           log=lambda *a: None) -> list[dict]:
    end = end or (date.today() - timedelta(days=1)).isoformat()
    rows = []
    for und in names:
        days = minutes_by_day(client, und, start, end)
        log(und, len(days), "days")
        for day in sorted(days):
            m = days[day]
            if "09:30" not in m or not at_or_before(m, OUT):
                continue
            o = float(m["09:30"]["o"])
            b = at_or_before(m, ENTRY)
            if not b:
                continue
            spot = float(b["c"])
            kind = "C" if spot >= o else "P"
            lo, hi = spot * 0.97, spot * 1.03
            cs = []
            for status in ("inactive", "active"):
                if pace:
                    pace.wait()
                try:
                    cs = client.option_contracts(und, status=status, expiration_date_gte=day,
                                                 expiration_date_lte=(date.fromisoformat(day) + timedelta(days=7)).isoformat(),
                                                 strike_price_gte=f"{lo:.2f}", strike_price_lte=f"{hi:.2f}")
                except Exception:
                    cs = []
                if cs:
                    break
            if not cs:
                continue
            exp = min(c["expiration_date"] for c in cs)
            near = [c for c in cs if c["expiration_date"] == exp]
            syms = {off: pick(near, kind, spot, off) for off in OFFS}
            want = sorted({s for s in syms.values() if s})
            if not want:
                continue
            if pace:
                pace.wait()
            try:
                bars = client.option_bars(want, f"{day}T13:00:00Z", f"{day}T21:00:00Z", timeframe="1Min")
            except Exception:
                continue
            time.sleep(0.05)
            mins = {s: et_minutes(b) for s, b in bars.items()}
            for off, s in syms.items():
                if s and s in mins:
                    got = play_all(mins[s], m, kind, o)
                    if got:
                        rows.append({"und": und, "day": day, "off": off, "kind": kind, "spot": spot, "open": o,
                                     "dte": (date.fromisoformat(exp) - date.fromisoformat(day)).days, **got})
    return rows


# ---------------------------------------------------------------- the study

GROUPS = {"SPY": ("SPY",), "QQQ": ("QQQ",), "个股（TSLA、NVDA、META）": ("TSLA", "NVDA", "META"), "全部": NAMES}


def cell(rows: list[dict], v: str) -> dict | None:
    if not rows:
        return None
    xs = [(r["day"], r["res"][v]) for r in rows]
    r = [x for _, x in xs]
    fired = [row for row in rows if row["why"][v] in ("stop", "back", "time")]
    killed = [row for row in fired if row["res"]["take3"] >= 1.0]
    g2, g20 = game(xs, 2), game(xs, 20)
    return {"n": len(r), "mean": round(statistics.mean(r), 3), "median": round(statistics.median(r), 3),
            "win": round(sum(x > 0 for x in r) / len(r), 3), "x2": round(sum(x >= 1 for x in r) / len(r), 3),
            "fired": round(len(fired) / len(rows), 3), "killed": round(len(killed) / len(fired), 3) if fired else None,
            "killed_n": len(killed), "fired_n": len(fired),
            "h2": g2["heroes"], "z2": g2["zeros"], "h20": g20["heroes"]}


def run(rows: list[dict], today: date | None = None) -> dict:
    tab = {}
    for off in OFFS:
        for gname, unds in GROUPS.items():
            rs = [r for r in rows if r["und"] in unds and r["off"] == off]
            for v in VARIANTS:
                tab[f"{off}|{gname}|{v}"] = cell(rs, v)
    # the stopped-out tickets under stop50: what happened to them by 15:30 without the stop?
    stopped = [r for r in rows if r["why"]["stop50"] == "stop"]
    after = {"n": len(stopped),
             "take3_mean": round(statistics.mean(r["res"]["take3"] for r in stopped), 3) if stopped else None,
             "ended_2x": sum(r["res"]["take3"] >= 1.0 for r in stopped),
             "ended_under_half": sum(r["res"]["take3"] <= -0.5 for r in stopped),
             "ended_zero": sum(r["res"]["take3"] <= -0.9 for r in stopped)}
    rob = {}
    for off in OFFS:
        rs = [r for r in rows if r["off"] == off]
        for v in ("take3", "stop50", "back_open", "time12", "stop50_back_open", "take3_trail"):
            rob[f"{off:.1%} {ZH[v]}"] = robust.summary([(r["day"], r["res"][v]) for r in rs], end=max((r["day"] for r in rs), default=None))
    return {"generated": (today or date.today()).isoformat(), "start": START, "n_rows": len(rows),
            "days": [min(r["day"] for r in rows), max(r["day"] for r in rows)] if rows else None,
            "table": tab, "stopped50": after, "robust": rob}


def markdown(rep: dict) -> str:
    md = [f"# 同日期权要不要止损 {rep['generated']}", "",
          f"claude-b 11-02 的问题（它的 AMZN 当日看涨 0.43 → 0.61 → 0.08，股价只回 0.5%）。样本同 10-13 的末日单研究：{'、'.join(NAMES)} 每个交易日，"
          f"{rep['start']} 到 {rep['days'][1] if rep.get('days') else '—'}，当天到期（没有就一周内最近的），10:00 比开盘高买看涨、低买看跌，"
          f"离现价 0.3% 和 0.6% 各一张，按分钟收盘价判断，买入多付 {SLIP:.0%}、卖出少拿 {SLIP:.0%}。共 {rep['n_rows']} 张。"
          "现行规则是 3 倍止盈、不管盈亏 15:30 平，没有止损；下面每一行都是在它上面加一条止损。只研究，不改规则。", "",
          "每格：平均每 $1 / 中位数 / 收在 2 倍以上的比例 / 止损触发的比例（其中本来能到 2 倍的比例）/ 账本玩法到 2 倍：成功·归零·到 20 倍成功次数。", ""]
    for off in OFFS:
        md += [f"## 离现价 {off:.1%}", "", "| 出场 | " + " | ".join(GROUPS) + " |", "|---|" + "---|" * len(GROUPS)]
        for v in VARIANTS:
            cells = []
            for g in GROUPS:
                c = rep["table"].get(f"{off}|{g}|{v}")
                if not c:
                    cells.append("—")
                    continue
                fired = f"{c['fired']:.0%}" + (f"（{c['killed']:.0%}）" if c["killed"] is not None else "") if v != "take3" else "—"
                cells.append(f"{c['mean']:+.0%} / {c['median']:+.0%} / {c['x2']:.0%} / {fired} / {c['h2']}·{c['z2']}·{c['h20']}")
            md.append(f"| {ZH[v]} | " + " | ".join(cells) + " |")
        md.append("")
    s = rep["stopped50"]
    if s["n"]:
        md += ["## 亏 50% 止损打掉的那些票，不止损会怎样", "",
               f"- 被打掉 {s['n']} 张；不止损按现行规则拿到 15:30，平均每 $1 {s['take3_mean']:+.0%}：其中 {s['ended_2x']} 张（{s['ended_2x'] / s['n']:.0%}）"
               f"最后到了 2 倍以上，{s['ended_under_half']} 张（{s['ended_under_half'] / s['n']:.0%}）收在 −50% 以下，{s['ended_zero']} 张（{s['ended_zero'] / s['n']:.0%}）基本归零。", ""]
    md.append(robust.table(rep["robust"], "稳健性检查（每张票一笔，每 $1 的收益）"))
    md += ["## 读法", "",
           "- 末日单的买价大多 $0.10–0.80，分钟收盘价本来就在买卖价差里跳，亏 30% 的止损最容易被噪音打到；看「触发比例」后面括号里有多少本来能到 2 倍。",
           "- 「跌回开盘价就卖」是按前因止损：买它的理由（比开盘高 / 低）没了就走，和期权本身亏多少无关。",
           "- 账本玩法那三个数（成功·归零·到 20 倍）才是账本要的：止损让归零变少，还是让成功也变少。",
           "- 样本是 10:00 进场、当天到期的规则票，不是 claude-b 11:03 追涨买的那种；它那张的问题是买在 +4% 之后的 0.2 delta，止损救不了时间价值。", ""]
    return "\n".join(md)


def main() -> None:
    from hero.alpaca import Alpaca
    from hero.research_event_turn import Pace
    client = Alpaca()
    rows = gather(client, pace=Pace(client), log=lambda *a: print(*a, file=sys.stderr))
    rep = run(rows)
    rep["rows"] = rows
    save("zdte-stop", rep, markdown(rep))


if __name__ == "__main__":
    sys.exit(main())
