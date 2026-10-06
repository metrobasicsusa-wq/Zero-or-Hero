"""Research: same-day-expiry (0DTE) call butterflies on SPY and QQQ, judged by the zero-or-hero goal.

A long call butterfly (buy K-w, sell 2 x K, buy K+w) costs little, loses at most its cost, and is
worth up to w at expiry if the price closes at K: a bet on where the day ends, not on how far it
runs. Every trading day from 2024-02 (Alpaca's option history), with one-minute bars:
  entry at 12:00, 14:00 or 15:00 ET;
  centre: the price rounded to $1 ("at"), or one wing further in the day's direction ("trend":
          above the price if it is above the open, else below);
  wings: $1, $2 or $5;
  exit: sell at 15:30 or 15:50, a resting take at 3x / 5x / 10x the cost (checked minute by
        minute, else sold at 15:50), or held to the 16:00 close (intrinsic value: an upper bound,
        since a broker may close expiring short legs early and settlement has its own risks).
Prices: each leg's last one-minute close (it must have traded within 5 minutes of the entry);
costs: every leg pays max($0.02, 3% of its price) against us on the way in and out.
Then each rule is replayed as the experiment runs: $500, half of the account on each trade, an
attempt ends below $50 (zero) or at $10,000 (hero) and a fresh $500 attempt begins; the current
SPY 0DTE call rule is replayed the same way for comparison. Record only.
Run: python -m hero.research_fly
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from hero import research_0dte as z
from hero import research_hero as rh

START = z.START
UNDERLYINGS = ("SPY", "QQQ")
ENTRIES = ("12:00", "14:00", "15:00")
WINGS = (1, 2, 5)
CENTERS = ("at", "trend")
TAKE = (3, 5, 10)
EXITS = ("15:30", "15:50")
FRACTION = 0.5
TARGET = 20  # $500 -> $10,000
END_LOSS = 0.9
CURRENT = "SPY|10:00|0.006|3|trend"


def slip(px: float) -> float:
    return max(0.02, 0.03 * px)


GRID = [f"{h:02d}:{m:02d}" for h in range(9, 16) for m in range(60) if "09:30" <= f"{h:02d}:{m:02d}" <= "15:59"]
IDX = {t: i for i, t in enumerate(GRID)}


def filled(minutes: dict[str, dict]) -> tuple[list[float | None], list[int | None]]:
    """Per grid minute: the last close so far, and the minute index it traded at."""
    px, at, last, li = [], [], None, None
    for i, t in enumerate(GRID):
        if t in minutes:
            last, li = float(minutes[t]["c"]), i
        px.append(last)
        at.append(li)
    return px, at


def fly_cost(lo: float, mid: float, hi: float) -> float:
    """Pay to open: buy the wings above their price, sell the body below it."""
    return (lo + slip(lo)) + (hi + slip(hi)) - 2 * (mid - slip(mid))


def fly_value(lo: float, mid: float, hi: float) -> float:
    """Get on close: sell the wings below their price, buy the body back above it."""
    return max((lo - slip(lo)) + (hi - slip(hi)) - 2 * (mid + slip(mid)), 0.0)


def intrinsic(spot: float, k: float, w: float) -> float:
    return max(0.0, w - abs(spot - k))


def trade(legs: list[tuple[list, list]], entry: str, k: float, w: float, settle: float) -> dict | None:
    """Returns per $1 for each exit, or None when a leg had no price within 5 minutes of entry."""
    i0 = IDX[entry]
    if any(at[i0] is None or i0 - at[i0] > 5 for _, at in legs):
        return None
    cost = fly_cost(*(px[i0] for px, _ in legs))
    if cost <= 0.01:
        return None  # a stale or crossed quote, not a real price
    value = [0.0] * i0 + [fly_value(*(px[i] for px, _ in legs)) for i in range(i0, len(GRID))]
    out = {"cost": round(cost, 3)}
    for ex in EXITS:
        out[ex] = round(value[IDX[ex]] / cost - 1, 4)
    for n in TAKE:
        hit = any(value[i] >= n * cost for i in range(i0 + 1, IDX["15:50"] + 1))
        out[f"tp{n}"] = (n - 1) if hit else out["15:50"]
    out["close"] = round(intrinsic(settle, k, w) / cost - 1, 4)
    return out


def run(client) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    trades: dict[str, list[tuple[str, float]]] = {}
    costs: dict[str, list[float]] = {}
    used = {}
    for und in UNDERLYINGS:
        days = z.trading_days(client, und, START, end)
        n = 0
        for day in sorted(days):
            m = days[day]
            if "09:30" not in m or "15:59" not in m:
                continue
            day_open, settle = float(m["09:30"]["o"]), float(m["15:59"]["c"])
            plan = []
            for entry in ENTRIES:
                bar = z.at_or_before(m, entry)
                if not bar:
                    continue
                spot = float(bar["c"])
                up = 1 if spot >= day_open else -1
                for w in WINGS:
                    for c in CENTERS:
                        k = float(round(spot)) + (up * w if c == "trend" else 0)
                        plan.append((entry, c, w, k))
            strikes = sorted({k + d for _, _, w, k in plan for d in (-w, 0, w)})
            syms = {s: z.occ(und, day, "C", s) for s in strikes}
            bars: dict[str, list] = {}
            try:
                for i in range(0, len(strikes), 50):
                    chunk = [syms[s] for s in strikes[i:i + 50]]
                    bars.update(client.option_bars(chunk, f"{day}T13:00:00Z", f"{day}T21:00:00Z", timeframe="1Min"))
            except Exception:
                continue
            time.sleep(0.15)
            if not bars:
                continue
            n += 1
            mins = {s: z.et_minutes(bars.get(syms[s], [])) for s in strikes}
            ff = {s: filled(v) for s, v in mins.items() if v}
            for entry, c, w, k in plan:
                if not all(x in ff for x in (k - w, k, k + w)):
                    continue
                legs = [ff[k - w], ff[k], ff[k + w]]
                r = trade(legs, entry, k, w, settle)
                if not r:
                    continue
                for ex in EXITS + tuple(f"tp{n_}" for n_ in TAKE) + ("close",):
                    key = f"{und}|{entry}|{c}|{w}|{ex}"
                    trades.setdefault(key, []).append((day, r[ex]))
                    costs.setdefault(key, []).append(r["cost"])
        used[und] = n
    return {"generated": date.today().isoformat(), "start": START, "days_used": used,
            "trades": {k: [[d, round(x, 4)] for d, x in v] for k, v in trades.items()},
            "median_cost": {k: round(statistics.median(v), 3) for k, v in costs.items()}}


def replay(ts: list[tuple[str, float]], last_day: str) -> dict:
    stream = [(d, max(FRACTION * r, -1.0)) for d, r in ts]
    first = (date.fromisoformat(ts[0][0]) - timedelta(days=1)).isoformat()
    seq = rh.sequential(stream, first, TARGET, END_LOSS)
    win = rh.windows(stream, first, last_day, TARGET, END_LOSS)
    return {"heroes": seq["heroes"], "zeros": seq["zeros"], "days": seq["median_days_to_hero"], "p12": win["p_hero"]}


def summarize(rep: dict, current: list | None) -> list[dict]:
    rows = []
    last = max(d for v in rep["trades"].values() for d, _ in v)
    for key, ts in rep["trades"].items():
        rets = [r for _, r in ts]
        und, entry, c, w, ex = key.split("|")
        rows.append({"key": key, "underlying": und, "entry": entry, "center": c, "wing": int(w), "exit": ex,
                     "n": len(rets), "mean": round(statistics.mean(rets), 3), "win": round(sum(r > 0 for r in rets) / len(rets), 3),
                     "x3": round(sum(r >= 2 for r in rets) / len(rets), 3), "cost": rep["median_cost"][key],
                     **replay([tuple(t) for t in ts], last)})
    if current:
        rets = [r for _, r in current]
        rows.append({"key": "现用：SPY 末日看涨（10:00、0.6%、顺势、3 倍）", "underlying": "SPY", "entry": "10:00", "center": "—",
                     "wing": 0, "exit": "tp3", "n": len(rets), "mean": round(statistics.mean(rets), 3),
                     "win": round(sum(r > 0 for r in rets) / len(rets), 3), "x3": round(sum(r >= 2 for r in rets) / len(rets), 3),
                     "cost": None, **replay([tuple(t) for t in current], last)})
    return rows


EXIT_NAME = {"15:30": "15:30 卖", "15:50": "15:50 卖", "tp3": "3 倍止盈", "tp5": "5 倍止盈", "tp10": "10 倍止盈",
             "close": "拿到收盘（理论值）"}


def markdown(rep: dict, rows: list[dict]) -> str:
    out = [f"# 末日蝴蝶（0DTE call butterfly）研究 {rep['generated']}", "",
           f"自 {rep['start']}，用到的交易日：{rep['days_used']}。每天在 12:00 / 14:00 / 15:00 买一组当天到期的看涨蝴蝶"
           "（买 K−w、卖 2 张 K、买 K+w），中心 K 取当时价格取整（「居中」）或往当天方向偏一个翼宽（「顺势」），翼宽 $1 / $2 / $5；"
           "15:30 或 15:50 卖出、挂 3 / 5 / 10 倍止盈（没到就 15:50 卖）、或拿到收盘按内在价值算（理论上限）。"
           "价格用各腿分钟收盘价，每腿进出各吃 max($0.02, 3%) 的差价。资金：$500，每次押一半，低于 $50 归零、到 $10,000 成功，都重来。只研究，不改交易。", ""]
    def line(r):
        name = r["key"] if r["center"] == "—" else \
            f"{r['underlying']} {r['entry']} {'居中' if r['center'] == 'at' else '顺势'} 翼宽 ${r['wing']} {EXIT_NAME[r['exit']]}"
        per = f"{(r['heroes'] + r['zeros']) / r['heroes']:.0f}" if r["heroes"] else "—"
        cost = f"${r['cost']:.2f}" if r["cost"] is not None else "—"
        p12 = f"{r['p12']:.0%}" if r["p12"] is not None else "—"
        return (f"| {name} | {cost} | {r['n']} | {r['mean']:+.0%} | {r['win']:.0%} | {r['x3']:.0%} | "
                f"{r['heroes']} / {r['zeros']} | {per} | {p12} |")
    head = ["| 规则 | 成本中位数 | 次数 | 平均每 $1 | 赚钱比例 | ≥3 倍 | 到 $10,000 成功 / 归零 | 每次成功要几轮 | 12 个月内成功比例 |",
            "|---|---|---|---|---|---|---|---|---|"]
    real = [r for r in rows if r["exit"] != "close" or r["center"] == "—"]
    out += ["## 平均每笔最好的 12 种（不含「拿到收盘」）", ""] + head
    out += [line(r) for r in sorted(real, key=lambda r: -r["mean"])[:12]]
    out += ["", f"共 {len(real)} 种可执行的规则，平均每 $1 为正的有 {sum(r['mean'] > 0 for r in real if r['center'] != '—')} 种。", "",
            "## 到 $10,000 机会最大的 12 种（不含「拿到收盘」）", ""] + head
    out += [line(r) for r in sorted(real, key=lambda r: (-r["heroes"] / max(r["heroes"] + r["zeros"], 1), -r["mean"]))[:12]]
    cur = [r for r in rows if r["center"] == "—"]
    if cur:
        out += ["", "## 对比：现在用的 SPY 末日看涨", ""] + head + [line(r) for r in cur]
    close = [r for r in rows if r["exit"] == "close"]
    out += ["", "## 拿到收盘（理论上限：券商可能提前平仓、结算有风险）最好的 8 种", ""] + head
    out += [line(r) for r in sorted(close, key=lambda r: -r["mean"])[:8]]
    return "\n".join(out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca())
    zfiles = sorted((root / "research").glob("*-0dte.json"))
    current = json.loads(zfiles[-1].read_text())["trades"].get(CURRENT) if zfiles else None
    rows = summarize(rep, current)
    rep["rows"] = rows
    (root / "research" / f"{rep['generated']}-fly.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False) + "\n")
    (root / "research" / f"{rep['generated']}-fly.md").write_text(markdown(rep, rows))
    print(markdown(rep, rows))


if __name__ == "__main__":
    sys.exit(main())
