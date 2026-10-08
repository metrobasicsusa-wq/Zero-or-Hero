"""Review: how would the 28 sleeves' plan-only picks of a day have ended, had they been bought?

Plan mode (config/s500.json sleeves.plan_only) logs each pick as a sleeve_plan event but holds nothing,
so nothing follows the pick afterwards. Here, after the close, each of that day's picks with a size is
played through its own exit on one-minute bars (SIP, so run it 15+ minutes after the close):
  bought at the logged limit (the ask + $0.01), at the minute the plan was logged;
  same-day 0DTE sleeves: the resting take (spec take x the limit), else out at 15:30;
  flush: a 2x take, else 15:50; gap: once worth 2x, out 40% below the best, else 15:50;
  anything held past today (weekly, earnings, net, TQQQ): marked at the day's last minute, "still open".
Exits are at the minute close (no spread on the way out), so this flatters the result a little.
Record only. Run: python -m hero.research_plan_review [YYYY-MM-DD]
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

from hero.research_0dte import ET, et_minutes

SIZE = re.compile(r"(\d+) 张 × \$([\d.]+)")
OUT = {"zdte": "15:30", "flush": "15:50", "gap": "15:50"}


def picks(events: list[dict], day: str) -> list[dict]:
    out = []
    for e in events:
        if e.get("kind") != "sleeve_plan" or not e.get("symbol") or not e["ts"].startswith(day):
            continue
        m = SIZE.search(e.get("why", ""))
        if not m or int(m.group(1)) == 0:
            continue
        at = datetime.fromisoformat(e["ts"]).astimezone(ET).strftime("%H:%M")
        out.append({"sleeve": e["sleeve"], "symbol": e["symbol"], "qty": int(m.group(1)),
                    "limit": float(m.group(2)), "at": at})
    return out


def play(bars: dict[str, dict], p: dict, kind: str, take: float) -> dict:
    """Walk the minutes after the buy under the sleeve's exit."""
    later = [(k, float(b.get("h", b["c"])), float(b["c"])) for k, b in sorted(bars.items()) if k > p["at"]]
    cost = p["limit"]
    end = OUT.get(kind)
    best = max((h for k, h, _ in later if not end or k <= end), default=0.0)
    res = {"best_x": round(best / cost, 2) if cost else 0.0}
    if kind in ("zdte", "flush"):
        tk = take if kind == "zdte" else 2.0
        hit = next((k for k, h, _ in later if k <= end and h >= tk * cost), None)
        if hit:
            return {**res, "exit": tk * cost, "when": hit, "how": f"{tk:g} 倍止盈"}
    if kind == "gap":
        peak, armed = 0.0, False
        for k, _, c in later:
            if k > end:
                break
            peak = max(peak, c)
            armed = armed or c >= 2 * cost
            if armed and c <= 0.6 * peak:
                return {**res, "exit": c, "when": k, "how": "翻倍后回撤 40%"}
    if end:
        last = [(k, c) for k, _, c in later if k <= end]
        k, c = last[-1] if last else (end, 0.0)
        return {**res, "exit": c, "when": k, "how": f"{end} 卖出"}
    k, c = (later[-1][0], later[-1][2]) if later else ("—", cost)
    return {**res, "exit": c, "when": k, "how": "还拿着（按收盘估值）"}


def run(client, root: Path, day: str) -> dict:
    events = [json.loads(l) for l in (root / "journal-s500" / "trades.jsonl").read_text().splitlines() if l.strip()]
    specs = {s["id"]: s for s in json.loads((root / "config" / "s500.json").read_text())["sleeves"]["list"]}
    ps = picks(events, day)
    syms = sorted({p["symbol"] for p in ps})
    opts = [s for s in syms if len(s) > 15]
    bars = client.option_bars(opts, f"{day}T13:00:00Z", f"{day}T21:00:00Z", timeframe="1Min") if opts else {}
    stocks = [s for s in syms if s not in opts]
    if stocks:
        bars.update(client.stock_bars(stocks, f"{day}T13:00:00Z", f"{day}T20:01:00Z", feed="sip"))
    rows = []
    for p in ps:
        spec = specs.get(p["sleeve"], {})
        r = play(et_minutes(bars.get(p["symbol"], [])), p, spec.get("kind", ""), float(spec.get("take", 3)))
        paid = p["qty"] * p["limit"] * 100
        got = p["qty"] * r["exit"] * 100
        rows.append({**p, **r, "paid": round(paid, 2), "got": round(got, 2), "pnl": round(got - paid, 2)})
    return {"day": day, "generated": date.today().isoformat(), "rows": rows}


def markdown(rep: dict) -> str:
    out = [f"# 28 个账本演算复盘 {rep['day']}", "",
           "演算模式只记录选了什么、不下单。这里假设按记录的限价（卖价 + $0.01）在记录那一分钟买进，再按各账本自己的卖出规则走完当天的分钟线。"
           "卖出按分钟收盘价，没扣价差，结果略偏乐观。只研究，不改交易。", "",
           "| 账本 | 合约 | 买入 | 张数 × 限价 | 花费 | 当天最高倍数 | 怎么出 | 时间 | 卖价 | 回收 | 盈亏 |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rep["rows"]:
        out.append(f"| {r['sleeve']} | {r['symbol']} | {r['at']} | {r['qty']} × ${r['limit']:.2f} | ${r['paid']:,.2f} | {r['best_x']:.1f} 倍 | "
                   f"{r['how']} | {r['when']} | ${r['exit']:.2f} | ${r['got']:,.2f} | {r['pnl']:+,.2f} |")
    if not rep["rows"]:
        out.append("| （这天没有带张数的选单） |  |  |  |  |  |  |  |  |  |  |")
    return "\n".join(out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    day = sys.argv[1] if len(sys.argv) > 1 else datetime.now(ET).date().isoformat()
    rep = run(Alpaca(), root, day)
    (root / "research" / f"{day}-plan-review.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    (root / "research" / f"{day}-plan-review.md").write_text(markdown(rep))
    print(markdown(rep))


if __name__ == "__main__":
    sys.exit(main())
