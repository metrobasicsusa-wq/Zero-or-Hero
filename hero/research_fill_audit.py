"""Did the SPY same-day ledgers buy and sell at the market's price? (owner 11-19: check it now.)

11-19 09:40: the near ledger's 733 put filled at $5.61 while that contract traded $1.82-2.31 in the same minute; $5.60
was its close the day before. The paper broker's quote had not moved off yesterday's close and the order filled on
it. On a gap up that makes puts dear; on a gap down it would make them cheap, and the ledgers' biggest days were
gap-down days. So: every SPY option the multi-ledger account bought or sold on its expiry day since 10-12, against what
that contract really traded in the minutes around the fill (OPRA minute bars; a paper order never prints, so the bars
are the market alone), and against the contract's last trade of the day before.

  ok     the fill is inside the low-high of the fill's minute and the one each side (widened to three minutes each side
         when nothing traded), give or take 10% or two cents;
  high / low   above / below that;
  stale  off the market and within 5% (or two cents) of the previous day's last trade: yesterday's quote.
"Flatter" is what the books show over what the same order would show at the market's volume-weighted price of those
minutes: paying less than the market or selling for more flatters the ledger. It reprices each fill where it happened;
it does not replay the day (a 3x take set off a wrong cost would have triggered at another time).
The plan ledgers record the ask instead of a fill: the same check on those, kept apart. Record only.
Run: python -m hero.research_fill_audit (needs the Alpaca keys: the research workflow, study "fill_audit").
"""

from __future__ import annotations

import json
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from hero.research_0dte import ET, et_minutes
from hero.research_event_turn import Pace
from hero.research_fills import expiry, load_orders
from hero.research_flow_daily import ERRORS, fetch
from hero.research_ideas import save

ROOT = Path(__file__).resolve().parent.parent
SINCE, UND = "2026-10-12", "SPY"
TOL, TICK = 0.10, 0.02
NEAR, WIDE = 1, 3
SAME = 0.05
SIDES = {"buy": "买入", "sell": "卖出"}


def shift(hhmm: str, k: int) -> str:
    m = int(hhmm[:2]) * 60 + int(hhmm[3:]) + k
    return f"{m // 60:02d}:{m % 60:02d}"


def market(mins: dict[str, dict], hhmm: str) -> dict | None:
    """What the contract traded in the minutes around `hhmm`: low, high and the volume-weighted price."""
    for span in (NEAR, WIDE):
        bars = [mins[k] for k in (shift(hhmm, d) for d in range(-span, span + 1)) if k in mins]
        if bars:
            vol = sum(float(b.get("v") or 0) for b in bars)
            vw = (sum(float(b.get("vw") or b["c"]) * float(b.get("v") or 0) for b in bars) / vol if vol
                  else statistics.mean(float(b["c"]) for b in bars))
            return {"lo": min(float(b["l"]) for b in bars), "hi": max(float(b["h"]) for b in bars), "vw": round(vw, 4), "span": span}
    return None


def verdict(price: float, mk: dict) -> str:
    if price > mk["hi"] + max(mk["hi"] * TOL, TICK):
        return "high"
    if price < mk["lo"] - max(mk["lo"] * TOL, TICK):
        return "low"
    return "ok"


def judge(row: dict, mins: dict[str, dict], prior: float | None) -> dict:
    mk = market(mins, row["hhmm"])
    if not mk:
        return {**row, "prior": prior, "verdict": "none"}
    v = verdict(row["price"], mk)
    sign = 1 if row["side"] == "buy" else -1
    return {**row, "prior": prior, "market": mk, "verdict": v, "off": round(row["price"] / mk["vw"] - 1, 4),
            "flatter": round(sign * (mk["vw"] - row["price"]) * row["qty"] * 100, 2),
            "stale": bool(v != "ok" and prior and abs(row["price"] - prior) <= max(prior * SAME, TICK))}


def events(root: Path) -> list[dict]:
    path = root / "journal-s500" / "trades.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def et_of(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(ET)


def real_orders(root: Path = ROOT) -> list[dict]:
    """The multi-ledger account's SPY orders on the contract's own expiry day, with the ledgers that held it that day.
    No order keys: the result goes into a public repository."""
    held: dict[tuple, set] = {}
    for e in events(root):
        if e.get("kind") == "sleeve_fill" and e.get("symbol"):
            held.setdefault((e["symbol"], et_of(e["ts"]).date().isoformat()), set()).add(e.get("sleeve"))
    return [{"symbol": o["symbol"], "side": o["side"], "qty": o["qty"], "price": o["price"], "day": o["day"], "hhmm": o["hhmm"],
             "sleeves": sorted(held.get((o["symbol"], o["day"]), []))}
            for o in load_orders(root)
            if o["account"] == "多账本" and o["symbol"].startswith(UND) and o["expiry"] == o["day"] >= SINCE]


def plan_entries(root: Path = ROOT) -> list[dict]:
    """The plan ledgers' entries: the ask they recorded as the price paid."""
    rows = []
    for e in events(root):
        sym = str(e.get("symbol", ""))
        if e.get("kind") != "sleeve_plan_fill" or not sym.startswith(UND) or "spy0dte" not in str(e.get("sleeve")):
            continue
        t = et_of(e["ts"])
        day = t.date().isoformat()
        if day >= SINCE and expiry(sym) == day:
            rows.append({"symbol": sym, "side": "buy", "qty": float(e["qty"]), "price": float(e["price"]), "day": day,
                         "hhmm": t.strftime("%H:%M"), "sleeves": [e["sleeve"]]})
    return rows


def bars_for(client, pace, day: str, syms: list[str], now: datetime) -> tuple[dict[str, dict], dict[str, float]]:
    """Each contract's minutes on `day`, and its last trade before that day (looking five days back)."""
    start = f"{date.fromisoformat(day) - timedelta(days=5)}T13:00:00Z"
    end = min(f"{day}T21:00:00Z", (now - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ"))   # the newest 15 minutes are not served
    got = fetch(pace, client.option_bars, syms, start, end, timeframe="1Min") or {}
    mins, prior = {}, {}
    for s in syms:
        today, before = [], []
        for b in got.get(s, []):
            (today if et_of(b["t"]).date().isoformat() == day else before).append(b)
        mins[s] = et_minutes(today)
        if before:
            prior[s] = float(max(before, key=lambda b: b["t"])["c"])
    return mins, prior


def run(client, root: Path = ROOT, now: datetime | None = None, pace=None) -> dict:
    now, pace = now or datetime.now(timezone.utc), pace or Pace(client)
    src = {"real": real_orders(root), "plan": plan_entries(root)}
    need: dict[str, set[str]] = {}
    for r in src["real"] + src["plan"]:
        need.setdefault(r["day"], set()).add(r["symbol"])
    got = {day: bars_for(client, pace, day, sorted(syms), now) for day, syms in sorted(need.items())}
    out = {k: [judge(r, got[r["day"]][0].get(r["symbol"], {}), got[r["day"]][1].get(r["symbol"])) for r in rows]
           for k, rows in src.items()}
    return {"generated": date.today().isoformat(), "since": SINCE, "last": max(need, default=None), **out, "errors": list(ERRORS)}


def money(x: float) -> str:
    return f"{'+' if x >= 0 else '−'}${abs(x):,.0f}"


def counts(rows: list[dict], side: str) -> str:
    rs = [r for r in rows if r["side"] == side]
    n = lambda v: sum(r["verdict"] == v for r in rs)
    off = [r for r in rs if r["verdict"] in ("high", "low")]
    return (f"| {SIDES[side]} | {len(rs)} | {n('ok')} | {n('high')} | {n('low')} | {n('none')} | {sum(r.get('stale', False) for r in off)} | "
            f"{money(sum(r.get('flatter', 0) for r in rs))} |")


def detail(rows: list[dict], cap: int) -> list[str]:
    off = sorted((r for r in rows if r["verdict"] in ("high", "low")), key=lambda r: -abs(r["flatter"]))
    out = ["| 日期 时刻 | 账本 | 合约 | 方向 | 张数 | 记的价 | 那几分钟市场的成交（低–高，均价） | 前一天最后成交 | 账面多记（+）/ 少记（−） | 是昨天的报价 |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for r in off[:cap]:
        mk = r["market"]
        out.append(f"| {r['day'][5:]} {r['hhmm']} | {'、'.join(s.replace('spy0dte', '').strip('-') or '10:00' for s in r['sleeves']) or '—'} | "
                   f"{r['symbol'][3:]} | {SIDES[r['side']]} | {r['qty']:g} | ${r['price']:.2f} | ${mk['lo']:.2f}–{mk['hi']:.2f}，${mk['vw']:.2f} | "
                   f"{'—' if r['prior'] is None else '$%.2f' % r['prior']} | {money(r['flatter'])} | {'是' if r['stale'] else '—'} |")
    if len(off) > cap:
        out.append(f"| …另有 {len(off) - cap} 笔 | | | | | | | | {money(sum(r['flatter'] for r in off[cap:]))} | |")
    return out


def by_day(rows: list[dict]) -> dict[str, dict]:
    """Per day: what the books show for these orders (sold minus bought) and the same at the market's price."""
    out: dict[str, dict] = {}
    for r in rows:
        d = out.setdefault(r["day"], {"book": 0.0, "flatter": 0.0, "off": 0, "stale": 0, "n": 0})
        d["book"] += (1 if r["side"] == "sell" else -1) * r["price"] * r["qty"] * 100
        d["flatter"] += r.get("flatter", 0)
        d["off"] += r["verdict"] in ("high", "low")
        d["stale"] += bool(r.get("stale"))
        d["n"] += 1
    return out


def report(rep: dict) -> str:
    real, plan = rep["real"], rep["plan"]
    days = by_day(real)
    book, flat = sum(d["book"] for d in days.values()), sum(d["flatter"] for d in days.values())
    head = ["| 方向 | 笔数 | 在市场成交范围内 | 比市场高 | 比市场低 | 那几分钟没有成交 | 对不上的里面等于前一天价的 | 账面多记（+）/ 少记（−） |", "|---|---|---|---|---|---|---|---|"]
    out = [f"# SPY 当天到期账本：记的成交价对不对得上市场（{rep['generated']}）", "",
           f"{rep['since']} 至 {rep['last']}，多账本账户里 SPY 当天到期期权的每一笔买卖（同一订单的分笔合并），对同一张合约在成交那一分钟和前后各一分钟"
           f"真实市场的成交（没有就放宽到前后各三分钟）。记的价在那段的最低到最高之间（放宽 {TOL:.0%} 或 2 美分）算对得上。"
           "「账面多记」= 按市场那几分钟的成交均价重算同一笔，账上比它多出来的钱：买得比市场便宜、卖得比市场贵都算多记。"
           "模拟盘的单不会出现在真实成交里，所以市场价不含我们自己。只核对，不改规则。", "",
           "## 真实下单的四个账本", "", *head, counts(real, "buy"), counts(real, "sell"), "",
           f"这些订单账面合计 {money(book)}（卖出收回减买入花掉，到期作废的没有卖出）；按市场价重算是 {money(book - flat)}，"
           f"账面{'多' if flat >= 0 else '少'}记 {money(abs(flat)).lstrip('+')}。", ""]
    out += ["### 对不上的每一笔（按金额从大到小）", "", *detail(real, 40), ""]
    top = sorted(days.items(), key=lambda kv: -kv[1]["book"])
    pick = top[:8] + [kv for kv in top[-3:] if kv not in top[:8]]
    out += ["### 赚得最多的几天和亏得最多的几天", "", "| 日期 | 订单数 | 账面盈亏 | 按市场价重算 | 账面多记（+）/ 少记（−） | 对不上的笔数（其中是昨天报价的） |", "|---|---|---|---|---|---|"]
    out += [f"| {d[5:]} | {v['n']} | {money(v['book'])} | {money(v['book'] - v['flatter'])} | {money(v['flatter'])} | {v['off']}（{v['stale']}） |" for d, v in pick]
    out += ["", "## 演算账本记的进场价（按卖价记作买进，没有真的下单）", "", *head, counts(plan, "buy"), "", *detail(plan, 25), ""]
    notes = ["这是把每一笔在它发生的那一刻换成市场价，不是把整天重放一遍：3 倍止盈是按记的成本挂的，成本记错了，真实情况下止盈会在别的时刻触发。",
             "「没有成交」的那几笔多半是快到期、几美分的合约，或者今天的行情还取不到（最新 15 分钟不提供）。"]
    notes += [f"请求失败的原因（最后几条）：`{e}`" for e in (rep.get("errors") or [])[-3:]]
    return "\n".join(out + ["## 说明", ""] + [f"- {n}" for n in notes]) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    rep = run(Alpaca())
    save("fill-audit", rep, report(rep))


if __name__ == "__main__":
    sys.exit(main())
