"""Research: a stock jumped 5%+, then opened 2-5% lower -- can buying puts turn that weakness into money?

research_jump_dip (2026-10-08) found the shares fell about 2% over the next 5 sessions after such a morning,
while ordinary lower opens rose about 0.8%. Here the same mornings since 2024-02 (Alpaca's option
history), for the 100 names in data/universe.json, buying a put:
  when:    10:00 or 15:50 that day (the 15:50 entry only if the stock is still below yesterday's close);
  which:   the first expiry at least 6 calendar days out (about a week), at the money (the first strike at
           or under the price) or 3% under it;
  exits:   the next session's close; 5 sessions later; or a 2x take from the next session on (daily
           highs), else 5 sessions later;
  costs:   pay 10% over (at least a cent) to get in, get 10% under to get out;
  control: the same puts on a sample of 2-5% lower opens after an ordinary day (-2% to +2%);
plus the shares' own move from each entry (as if shorted) for reference. Record only.
Run: python -m hero.research_jump_put
"""

from __future__ import annotations

import json
import random
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from hero.research_0dte import at_or_before, et_minutes
from hero.research_chase import bars_for
from hero.research_jump_dip import candidates

START = "2024-02-05"
JUMP = 0.05
GAP = (-0.05, -0.02)
ENTRIES = ("10:00", "15:50")
OTM = (0.0, 0.03)
EXITS = ("next", "five", "take2")
EXIT_ZH = {"next": "第二天收盘卖", "five": "拿 5 天", "take2": "翻倍就卖，否则拿 5 天"}
SLIP = 0.10
CONTROL_N = 400
FEED = "sip"


def cost_in(px: float) -> float:
    return px + max(px * SLIP, 0.01)


def value_out(px: float) -> float:
    return max(px - max(px * SLIP, 0.01), 0.0)


def pick_put(contracts: list[dict], spot: float, off: float, day: str) -> str | None:
    soon = (date.fromisoformat(day) + timedelta(days=6)).isoformat()
    exps = sorted({c["expiration_date"] for c in contracts if c["expiration_date"] >= soon})
    if not exps:
        return None
    ks = sorted((float(c["strike_price"]), c["symbol"]) for c in contracts
                if c["expiration_date"] == exps[0] and c.get("type", "put") == "put")
    under = [s for k, s in ks if k <= spot * (1 - off)]
    return under[-1] if under else None


def play(entry_px: float, daily: dict[str, dict], later_days: list[str]) -> dict[str, float] | None:
    """later_days: the 5 sessions after the entry day. daily: {day: bar} of the option."""
    cost = cost_in(entry_px)
    last5 = [daily[d] for d in later_days if d in daily]
    if not last5:
        return None  # no trade at all afterwards: leave it out rather than guess
    out = {"next": value_out(float(last5[0]["c"])) / cost - 1}  # the first later session it traded
    out["five"] = value_out(float(last5[-1]["c"])) / cost - 1
    out["take2"] = 1.0 if any(float(b["h"]) >= 2 * cost for b in last5) else out["five"]
    return out


def run(client) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    syms = list(json.loads((Path(__file__).resolve().parent.parent / "data" / "universe.json").read_text()).get("stocks") or [])
    adj = bars_for(client, syms, f"{end}T23:00:00Z", "all")
    raw = bars_for(client, syms, f"{end}T23:00:00Z", "raw")
    jumps, ordinary = [], []
    for s in adj:
        dates = [b["t"][:10] for b in adj[s]]
        idx = {d: i for i, d in enumerate(dates)}
        for ev in candidates(adj[s], raw.get(s, [])):
            if ev["day"] < START or not (GAP[0] < ev["gap"] <= GAP[1]):
                continue
            i = idx[ev["day"]]
            ev.update(symbol=s, later=dates[i + 1:i + 6], adj_close=float(adj[s][i]["c"]), adj_close5=float(adj[s][i + 5]["c"]))
            if ev["ret"] >= JUMP:
                jumps.append(ev)
            elif -0.02 <= ev["ret"] <= 0.02:
                ordinary.append(ev)
    control = random.Random(8).sample(ordinary, min(CONTROL_N, len(ordinary)))
    rows = []
    for kind, evs in (("jump", jumps), ("control", control)):
        for ev in evs:
            s, day = ev["symbol"], ev["day"]
            try:
                m = et_minutes(client.stock_bars([s], f"{day}T13:00:00Z", f"{day}T21:00:00Z", feed=FEED).get(s, []))
                cs = []
                for status in ("inactive", "active"):
                    cs = client.option_contracts(s, status=status, type="put", expiration_date_gte=day,
                                                 expiration_date_lte=(date.fromisoformat(day) + timedelta(days=20)).isoformat())
                    if cs:
                        break
            except Exception:
                continue
            time.sleep(0.05)
            if not cs:
                continue
            spots = {}
            for t in ENTRIES:
                bar = at_or_before(m, t)
                if bar and (t != "15:50" or float(bar["c"]) < ev["prev_raw"]):
                    spots[t] = float(bar["c"])
            plan = {(t, off): pick_put(cs, spots[t], off, day) for t in spots for off in OTM}
            want = sorted({p for p in plan.values() if p})
            if not want:
                continue
            try:
                mins = {k: et_minutes(v) for k, v in client.option_bars(want, f"{day}T13:00:00Z", f"{day}T21:00:00Z", timeframe="1Min").items()}
                days = {k: {b["t"][:10]: b for b in v} for k, v in
                        client.option_bars(want, ev["later"][0], f"{ev['later'][-1]}T23:00:00Z", timeframe="1Day").items()}
            except Exception:
                continue
            res = {}
            for (t, off), sym in plan.items():
                bar = at_or_before(mins.get(sym, {}), t) if sym else None
                if not bar or float(bar["c"]) <= 0:
                    continue
                got = play(float(bar["c"]), days.get(sym, {}), ev["later"])
                if got:
                    res[f"{t}|{off}"] = {k: round(v, 4) for k, v in got.items()}
            shares = {t: round(1 - ev["adj_close5"] / (spots[t] * ev["factor"]), 4) for t in spots}  # a short's 5-day gain
            if res:
                rows.append({"kind": kind, "symbol": s, "day": day, "ret": round(ev["ret"], 4), "gap": round(ev["gap"], 4),
                             "res": res, "short5": shares})
    return {"generated": date.today().isoformat(), "start": START, "end": end, "rows": rows}


def cell(xs: list[float]) -> dict | None:
    if not xs:
        return None
    return {"n": len(xs), "mean": round(statistics.mean(xs), 3), "median": round(statistics.median(xs), 3),
            "win": round(sum(x > 0 for x in xs) / len(xs), 3), "x2": round(sum(x >= 1 for x in xs) / len(xs), 3)}


def table(rep: dict) -> dict:
    rows = rep["rows"]
    groups = {"大涨 5% 以上后低开 2–5%": [r for r in rows if r["kind"] == "jump"],
              "　其中大涨 10% 以上": [r for r in rows if r["kind"] == "jump" and r["ret"] >= 0.10],
              "对照：普通一天后低开 2–5%": [r for r in rows if r["kind"] == "control"]}
    out = {}
    for g, rs in groups.items():
        for t in ENTRIES:
            out[f"{g}|{t}|short"] = cell([r["short5"][t] for r in rs if t in r["short5"]])
            for off in OTM:
                for e in EXITS:
                    out[f"{g}|{t}|{off}|{e}"] = cell([r["res"][f"{t}|{off}"][e] for r in rs if f"{t}|{off}" in r["res"]])
    return out


def markdown(rep: dict, t: dict) -> str:
    out = [f"# 大涨后低开 2–5%：买看跌能不能赚钱 {rep['generated']}", "",
           f"{rep['start']} 至 {rep['end']}，data/universe.json 的 100 只股票。事件：某天收涨 5% 以上，第二天开盘低 2% 到 5%。"
           "当天 10:00 或 15:50（15:50 只在还低于昨收时）买约一周后到期的看跌（平值，或低 3%）；第二天收盘卖、拿 5 天、或翻倍就卖否则拿 5 天。"
           "买入多付 10%、卖出少拿 10%。对照：普通一天后低开 2–5% 的随机 400 次。每格「平均 / 中位数 / 赚钱比例 / 翻倍比例（次数）」。"
           "「做空正股 5 天」一列只作参考（不算借券费）。只研究，不改交易。", ""]
    groups = ("大涨 5% 以上后低开 2–5%", "　其中大涨 10% 以上", "对照：普通一天后低开 2–5%")
    for tm in ENTRIES:
        for off in OTM:
            out += [f"## {tm} 买，{'平值' if off == 0 else f'低 {off:.0%}'} 看跌", "",
                    "| 情况 | " + " | ".join(EXIT_ZH[e] for e in EXITS) + " | 做空正股 5 天 |", "|---|" + "---|" * (len(EXITS) + 1)]
            for g in groups:
                cells = []
                for e in EXITS:
                    c = t.get(f"{g}|{tm}|{off}|{e}")
                    cells.append(f"{c['mean']:+.0%} / {c['median']:+.0%} / {c['win']:.0%} / {c['x2']:.0%}（{c['n']}）" if c else "—")
                sh = t.get(f"{g}|{tm}|short")
                cells.append(f"{sh['mean']:+.1%} / {sh['median']:+.1%} / {sh['win']:.0%}（{sh['n']}）" if sh else "—")
                out.append(f"| {g} | " + " | ".join(cells) + " |")
            out.append("")
    return "\n".join(out)


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca())
    t = table(rep)
    rep["table"] = t
    (root / "research" / f"{rep['generated']}-jump-put.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    (root / "research" / f"{rep['generated']}-jump-put.md").write_text(markdown(rep, t))
    print(markdown(rep, t))


if __name__ == "__main__":
    sys.exit(main())
