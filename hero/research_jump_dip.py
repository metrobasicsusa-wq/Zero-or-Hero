"""Research: a stock jumped, then opened lower the next morning -- when (if at all) is that a buy?

research_chase (2026-10-08): chasing the morning after a jump does nothing on average and loses when it
gaps up again; after a jump and a lower open it was flat. Here that lower-open morning in detail, on
SIP minutes (raw prices) for the 100 names in data/universe.json since 2020:
  event:   a close up 5% / 10% (adjusted daily bars), then the next session opens below that close;
  entries that morning: at the open; at 10:00; the first minute back at yesterday's close (the gap
           filled, by 15:00); the first minute above today's open after 10:00 (turned green); the first
           minute above the 09:30-10:00 high after 10:00 (opening-range break);
  exits:   that day's close, or 5 sessions later (adjusted, so splits do not count);
  by depth of the lower open (0 to -2%, -2 to -5%, below -5%);
  control: the same entries on a sample of lower opens after an ordinary day (-2% to +2%).
Record only. Run: python -m hero.research_jump_dip
"""

from __future__ import annotations

import json
import random
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from hero.research_0dte import et_minutes
from hero.research_chase import bars_for

JUMP = 0.05
ENTRIES = ("open", "10:00", "fill", "green", "orb")
ENTRY_ZH = {"open": "开盘买", "10:00": "10:00 买", "fill": "回补到昨收再买", "green": "10 点后翻红再买", "orb": "10 点后突破开盘半小时高点再买"}
CONTROL_N = 600
FEED = "sip"


def entries(m: dict[str, dict], prev_close: float) -> dict[str, tuple[str, float]]:
    """Entry name -> (minute, raw price) for the ones that happened."""
    keys = sorted(k for k in m if "09:30" <= k <= "15:59")
    if not keys or keys[0] > "09:35":
        return {}
    out = {"open": (keys[0], float(m[keys[0]]["o"]))}
    day_open = out["open"][1]
    at10 = [k for k in keys if k <= "10:00"]
    out["10:00"] = (at10[-1], float(m[at10[-1]]["c"]))
    orb = max(float(m[k]["h"]) for k in keys if k < "10:00")
    for k in keys:
        c = float(m[k]["c"])
        if k > "15:00":
            break
        if "fill" not in out and k > "09:30" and c >= prev_close:
            out["fill"] = (k, c)
        if k >= "10:00" and "green" not in out and c > day_open:
            out["green"] = (k, c)
        if k >= "10:00" and "orb" not in out and c > orb:
            out["orb"] = (k, c)
    return out


def candidates(adj: list[dict], raw: list[dict]) -> list[dict]:
    """Days i+1 that opened below day i's close, with day i's change; skipped across a split."""
    out = []
    by_raw = {b["t"][:10]: b for b in raw}
    for i in range(1, len(adj) - 6):
        d0, d1 = adj[i]["t"][:10], adj[i + 1]["t"][:10]
        r0, r1 = by_raw.get(d0), by_raw.get(d1)
        if not r0 or not r1:
            continue
        f0, f1 = float(adj[i]["c"]) / float(r0["c"]), float(adj[i + 1]["c"]) / float(r1["c"])
        if abs(f1 / f0 - 1) > 0.01:
            continue  # a split between the two days
        gap = float(adj[i + 1]["o"]) / float(adj[i]["c"]) - 1
        if gap >= 0:
            continue
        out.append({"day": d1, "ret": float(adj[i]["c"]) / float(adj[i - 1]["c"]) - 1, "gap": gap,
                    "prev_raw": float(r0["c"]), "close_raw": float(r1["c"]), "factor": f1,
                    "close5_adj": float(adj[i + 6]["c"])})
    return out


def outcome(ev: dict, got: dict[str, tuple[str, float]]) -> dict:
    res = {}
    for name, (_, px) in got.items():
        res[name] = {"day": ev["close_raw"] / px - 1, "five": ev["close5_adj"] / (px * ev["factor"]) - 1}
    return res


def run(client) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    syms = list(json.loads((Path(__file__).resolve().parent.parent / "data" / "universe.json").read_text()).get("stocks") or [])
    adj = bars_for(client, syms, f"{end}T23:00:00Z", "all")
    raw = bars_for(client, syms, f"{end}T23:00:00Z", "raw")
    jumps, ordinary = [], []
    for s in adj:
        for ev in candidates(adj[s], raw.get(s, [])):
            ev["symbol"] = s
            if ev["ret"] >= JUMP:
                jumps.append(ev)
            elif -0.02 <= ev["ret"] <= 0.02:
                ordinary.append(ev)
    control = random.Random(8).sample(ordinary, min(CONTROL_N, len(ordinary)))
    rows = []
    for kind, evs in (("jump", jumps), ("control", control)):
        for ev in evs:
            try:
                bars = client.stock_bars([ev["symbol"]], f"{ev['day']}T13:00:00Z", f"{ev['day']}T21:00:00Z", feed=FEED)
            except Exception:
                continue
            time.sleep(0.05)
            got = entries(et_minutes(bars.get(ev["symbol"], [])), ev["prev_raw"])
            if got:
                rows.append({"kind": kind, "symbol": ev["symbol"], "day": ev["day"], "ret": round(ev["ret"], 4),
                             "gap": round(ev["gap"], 4), "when": {k: v[0] for k, v in got.items()},
                             "res": {k: {kk: round(vv, 4) for kk, vv in v.items()} for k, v in outcome(ev, got).items()}})
    return {"generated": date.today().isoformat(), "end": end, "rows": rows}


def cell(rows: list[dict], entry: str, key: str) -> dict | None:
    xs = [r["res"][entry][key] for r in rows if entry in r["res"]]
    if not xs:
        return None
    return {"n": len(xs), "mean": round(statistics.mean(xs), 4), "median": round(statistics.median(xs), 4),
            "up": round(sum(x > 0 for x in xs) / len(xs), 3),
            "se": round(statistics.stdev(xs) / len(xs) ** 0.5, 4) if len(xs) > 1 else None}


def table(rep: dict) -> dict:
    rows = rep["rows"]
    j = [r for r in rows if r["kind"] == "jump"]
    groups = {"大涨 5% 以上后低开": j,
              "　大涨 10% 以上后低开": [r for r in j if r["ret"] >= 0.10],
              "　低开 0 到 -2%": [r for r in j if r["gap"] > -0.02],
              "　低开 -2% 到 -5%": [r for r in j if -0.05 < r["gap"] <= -0.02],
              "　低开超过 -5%": [r for r in j if r["gap"] <= -0.05],
              "　2024 年以来": [r for r in j if r["day"] >= "2024-01-01"],
              "对照：普通一天后低开": [r for r in rows if r["kind"] == "control"]}
    return {g: {"days": len(rs), **{f"{e}|{k}": cell(rs, e, k) for e in ENTRIES for k in ("day", "five")}} for g, rs in groups.items()}


def markdown(rep: dict, t: dict) -> str:
    out = [f"# 大涨后第二天低开，什么时候买 {rep['generated']}", "",
           f"2020 至 {rep['end']}，data/universe.json 的 100 只股票，SIP 分钟线（原始价）。事件：某天收涨 5% 以上，第二天开盘低于那天收盘。"
           "当天几种买点：开盘、10:00、回补到昨收（15:00 前第一次）、10 点后翻红（高于当天开盘）、10 点后突破 9:30–10:00 的最高价；"
           "卖点：当天收盘，或 5 个交易日后（按复权价，拆股不影响）。对照：普通一天（-2% 到 +2%）后低开的随机 600 次。"
           "每格「平均（中位数），赚钱比例，触发次数」。不扣手续费和滑点。只研究，不改交易。", ""]
    for key, title in (("day", "当天收盘卖"), ("five", "拿 5 天")):
        out += [f"## {title}", "", "| 情况 | 天数 | " + " | ".join(ENTRY_ZH[e] for e in ENTRIES) + " |", "|---|---|" + "---|" * len(ENTRIES)]
        for g, row in t.items():
            cells = []
            for e in ENTRIES:
                c = row.get(f"{e}|{key}")
                cells.append(f"{c['mean']:+.2%}（{c['median']:+.2%}），{c['up']:.0%}，{c['n']}" if c else "—")
            out.append(f"| {g} | {row['days']} | " + " | ".join(cells) + " |")
        out.append("")
    return "\n".join(out)


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca())
    t = table(rep)
    rep["table"] = t
    (root / "research" / f"{rep['generated']}-jump-dip.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    (root / "research" / f"{rep['generated']}-jump-dip.md").write_text(markdown(rep, t))
    print(markdown(rep, t))


if __name__ == "__main__":
    sys.exit(main())
