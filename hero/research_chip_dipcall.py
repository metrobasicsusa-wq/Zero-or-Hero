"""Research: the morning after a chip stock drops hard, buy its calls?

research_chipnext (2026-10-08): after a 2.5%+ drop the chip names closed up the next day about 60% of the
time, +0.7% on average, but another 2%+ fall came 18% of the time. Here that next day with real option
prices (Alpaca's history starts 2024-02), for SMH, SOXX, NVDA, AMD, AVGO, MU, TSM:
  event:   a close down 2.5%+ (adjusted daily bars); also the second straight down day, and 5%+ drops;
  entry:   10:00 the next session, a call on the nearest expiry from that day on (within a week):
           at the money (the first strike at or above the price) or 2% above it;
  exits:   that day at 15:50; the next session's close (or that day's 15:50 if it expires that day);
           a 2x take from the entry minute on (minute highs that day, then the next session's high),
           else the next session's close;
  costs:   pay 10% over (at least a cent) to get in, get 10% under to get out;
  control: the same calls on a sample of days after an ordinary day (-1% to +1%).
Record only. Run: python -m hero.research_chip_dipcall
"""

from __future__ import annotations

import json
import random
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from hero.research_0dte import at_or_after, at_or_before, et_minutes
from hero.research_chase import bars_for
from hero.research_jump_put import cost_in, value_out

START = "2024-02-05"
NAMES = ("SMH", "SOXX", "NVDA", "AMD", "AVGO", "MU", "TSM")
DROP = -0.025
OTM = (0.0, 0.02)
EXITS = ("day", "next", "take2")
EXIT_ZH = {"day": "当天 15:50 卖", "next": "第二天收盘卖", "take2": "翻倍就卖，否则第二天收盘卖"}
CONTROL_N = 400
FEED = "sip"


def pick_call(contracts: list[dict], spot: float, off: float, day: str) -> dict | None:
    exps = sorted({c["expiration_date"] for c in contracts if c["expiration_date"] >= day})
    if not exps:
        return None
    ks = sorted((c for c in contracts if c["expiration_date"] == exps[0] and c.get("type", "call") == "call"),
                key=lambda c: float(c["strike_price"]))
    return next((c for c in ks if float(c["strike_price"]) >= spot * (1 + off)), None)


def play(mins: dict[str, dict], nxt_bar: dict | None, expires_today: bool) -> dict[str, float] | None:
    got = at_or_after(mins, "10:00")
    if not got or float(got[1]["c"]) <= 0:
        return None
    k0, bar = got
    cost = cost_in(float(bar["c"]))
    later = {k: v for k, v in mins.items() if k > k0}
    last = at_or_before({k: v for k, v in later.items() if k <= "15:50"}, "15:50")
    day = value_out(float(last["c"])) / cost - 1 if last else -1.0
    if expires_today or not nxt_bar:
        nxt = day
        highs = [float(v.get("h", v["c"])) for k, v in later.items() if k <= "15:50"]
    else:
        nxt = value_out(float(nxt_bar["c"])) / cost - 1
        highs = [float(v.get("h", v["c"])) for k, v in later.items()] + [float(nxt_bar.get("h", nxt_bar["c"]))]
    take2 = 1.0 if any(h >= 2 * cost for h in highs) else nxt
    return {"day": day, "next": nxt, "take2": take2}


def entries(adj: list[dict]) -> list[dict]:
    """Each session after a close we can classify, with the drop that preceded it."""
    out = []
    for i in range(2, len(adj) - 2):
        r, prev = float(adj[i]["c"]) / float(adj[i - 1]["c"]) - 1, float(adj[i - 1]["c"]) / float(adj[i - 2]["c"]) - 1
        out.append({"day": adj[i + 1]["t"][:10], "next_day": adj[i + 2]["t"][:10], "drop": r, "prev": prev})
    return out


def run(client) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    adj = bars_for(client, list(NAMES), f"{end}T23:00:00Z", "all")
    events, ordinary = [], []
    for s in NAMES:
        for e in entries(adj.get(s, [])):
            if e["day"] < START:
                continue
            e["symbol"] = s
            if e["drop"] <= DROP:
                events.append(e)
            elif -0.01 <= e["drop"] <= 0.01:
                ordinary.append(e)
    control = random.Random(9).sample(ordinary, min(CONTROL_N, len(ordinary)))
    rows = []
    for kind, evs in (("drop", events), ("control", control)):
        for e in evs:
            s, day = e["symbol"], e["day"]
            try:
                m = et_minutes(client.stock_bars([s], f"{day}T13:00:00Z", f"{day}T21:00:00Z", feed=FEED).get(s, []))
                spot_bar = at_or_before(m, "10:00")
                if not spot_bar:
                    continue
                spot = float(spot_bar["c"])
                cs = []
                for status in ("inactive", "active"):
                    cs = client.option_contracts(s, status=status, type="call", expiration_date_gte=day,
                                                 expiration_date_lte=(date.fromisoformat(day) + timedelta(days=7)).isoformat(),
                                                 strike_price_gte=f"{spot * 0.98:.2f}", strike_price_lte=f"{spot * 1.08:.2f}")
                    if cs:
                        break
                picks = {off: pick_call(cs, spot, off, day) for off in OTM}
                want = sorted({p["symbol"] for p in picks.values() if p})
                if not want:
                    continue
                mins = {k: et_minutes(v) for k, v in client.option_bars(want, f"{day}T13:00:00Z", f"{day}T21:00:00Z", timeframe="1Min").items()}
                nxt = {k: {b["t"][:10]: b for b in v} for k, v in
                       client.option_bars(want, e["next_day"], f"{e['next_day']}T23:00:00Z", timeframe="1Day").items()}
            except Exception:
                continue
            time.sleep(0.05)
            res = {}
            for off, c in picks.items():
                if not c:
                    continue
                got = play(mins.get(c["symbol"], {}), nxt.get(c["symbol"], {}).get(e["next_day"]), c["expiration_date"] == day)
                if got:
                    res[str(off)] = {k: round(v, 4) for k, v in got.items()}
            if res:
                rows.append({"kind": kind, "symbol": s, "day": day, "drop": round(e["drop"], 4), "prev": round(e["prev"], 4),
                             "res": res})
    return {"generated": date.today().isoformat(), "start": START, "end": end, "rows": rows}


def cell(xs: list[float]) -> dict | None:
    if not xs:
        return None
    return {"n": len(xs), "mean": round(statistics.mean(xs), 3), "median": round(statistics.median(xs), 3),
            "win": round(sum(x > 0 for x in xs) / len(xs), 3), "x2": round(sum(x >= 1 for x in xs) / len(xs), 3)}


def groups(rows: list[dict]) -> dict[str, list[dict]]:
    d = [r for r in rows if r["kind"] == "drop"]
    out = {"大跌 2.5% 以上后第二天": d,
           "　连跌两天、第二天跌 2.5% 以上": [r for r in d if r["prev"] < 0],
           "　大跌 5% 以上": [r for r in d if r["drop"] <= -0.05],
           "　只看 SMH / SOXX": [r for r in d if r["symbol"] in ("SMH", "SOXX")],
           "　只看个股": [r for r in d if r["symbol"] not in ("SMH", "SOXX")],
           "　2025 年以来": [r for r in d if r["day"] >= "2025-01-01"],
           "对照：普通一天后": [r for r in rows if r["kind"] == "control"]}
    for s in NAMES:
        out[f"　{s}"] = [r for r in d if r["symbol"] == s]
    return out


def table(rep: dict) -> dict:
    out = {}
    for g, rs in groups(rep["rows"]).items():
        for off in OTM:
            for e in EXITS:
                out[f"{g}|{off}|{e}"] = cell([r["res"][str(off)][e] for r in rs if str(off) in r["res"]])
    return out


def markdown(rep: dict, t: dict) -> str:
    out = [f"# 芯片股大跌后第二天买看涨 {rep['generated']}", "",
           f"{rep['start']} 至 {rep['end']}，{'、'.join(NAMES)}。事件：收盘跌 2.5% 以上；第二天 10:00 买最近到期（一周内）的看涨，"
           "平值或高 2%；当天 15:50 卖、第二天收盘卖、或翻倍就卖否则第二天收盘卖。买入多付 10%、卖出少拿 10%。"
           "对照：普通一天（-1% 到 +1%）后的随机 400 天。每格「平均 / 中位数 / 赚钱比例 / 翻倍比例（次数）」。只研究，不改交易。", ""]
    names = list(groups(rep["rows"]))
    for off in OTM:
        out += [f"## {'平值' if off == 0 else f'高 {off:.0%}'} 看涨", "", "| 情况 | " + " | ".join(EXIT_ZH[e] for e in EXITS) + " |",
                "|---|" + "---|" * len(EXITS)]
        for g in names:
            cells = []
            for e in EXITS:
                c = t.get(f"{g}|{off}|{e}")
                cells.append(f"{c['mean']:+.0%} / {c['median']:+.0%} / {c['win']:.0%} / {c['x2']:.0%}（{c['n']}）" if c else "—")
            out.append(f"| {g} | " + " | ".join(cells) + " |")
        out.append("")
    return "\n".join(out)


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca())
    t = table(rep)
    rep["table"] = t
    (root / "research" / f"{rep['generated']}-chip-dipcall.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    (root / "research" / f"{rep['generated']}-chip-dipcall.md").write_text(markdown(rep, t))
    print(markdown(rep, t))


if __name__ == "__main__":
    sys.exit(main())
