"""Research: weekly options for the $500 account -- buying (the 0DTE rule stretched to a week) vs selling.

Each week since 2024-02, on its first trading day at 10:00, with that week's last expiry (normally Friday):
  buyer:  SPY, QQQ, TSLA, NVDA; a call if the price is above the day's open, else a put (or calls only),
          the nearest listed strike at least 0.5% / 1% / 2% beyond the price; a resting limit at 2x / 3x
          the cost (filled when a later minute's high reaches it), else sold at the last minute close
          before Thursday 15:50 (out before the final day's fastest decay); pay 10% over / get 10% under;
          the $500 game: half the account each trade, a round ends below $50, at $1,000 or $10,000;
  seller: SPY and QQQ put credit spreads, $1 / $2 / $5 wide, the short put the nearest listed strike at
          least 1% / 2% / 3% below the price, held to expiry and settled at that day's close; each leg pays
          max($0.01, 3%) of its 10:00 minute close; a $500 account risking 25% / 50% of itself (whole
          spreads only, so a small account often cannot place one), compounded week by week.
Record only. Run: python -m hero.research_weekly
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from hero import research_hero as rh
from hero.research_0dte import ET, at_or_after, at_or_before, et_minutes
from hero.research_seller import curve_stats

START = "2024-02-01"
BUY_NAMES = ("SPY", "QQQ", "TSLA", "NVDA")
SELL_NAMES = ("SPY", "QQQ")
ENTRY = "10:00"
OFFSETS = (0.005, 0.01, 0.02)
TAKES = (2, 3)
MODES = ("trend", "calls")
SLIP = 0.10
WIDTHS = (1, 2, 5)
SHORT_OTM = (0.01, 0.02, 0.03)
RISK = (0.25, 0.5)
CAPITAL = 500.0
FRACTION = 0.5
END_LOSS = 0.9
TARGETS = (2, 20)
FEED = "sip"


def week_starts(spy_days: list[str]) -> list[str]:
    out, seen = [], set()
    for d in spy_days:
        y, w, _ = date.fromisoformat(d).isocalendar()
        if (y, w) not in seen:
            seen.add((y, w))
            out.append(d)
    return out


def buy_trade(opt: dict[str, dict], take: float, last_key: str) -> float | None:
    """opt: {'YYYY-MM-DD HH:MM': bar} across the week; bought at the first minute at/after the entry key."""
    keys = sorted(opt)
    if not keys:
        return None
    k0 = keys[0]
    cost = float(opt[k0]["c"]) + max(float(opt[k0]["c"]) * SLIP, 0.01)
    for k in keys[1:]:
        if k > last_key:
            break
        if float(opt[k].get("h", opt[k]["c"])) >= take * cost:
            return take - 1
    later = [k for k in keys[1:] if k <= last_key]
    px = float(opt[later[-1]]["c"]) if later else 0.0
    return max(px - max(px * SLIP, 0.01), 0.0) / cost - 1


def leg_slip(px: float) -> float:
    return max(0.01, 0.03 * px)


def spread_result(short_px: float, long_px: float, k_short: float, k_long: float, settle: float) -> dict | None:
    credit = (short_px - leg_slip(short_px)) - (long_px + leg_slip(long_px))
    width = k_short - k_long
    if credit <= 0.02 or credit >= width:
        return None
    owed = min(max(k_short - settle, 0.0), width)
    return {"credit": round(credit, 3), "max_loss": round(width - credit, 3), "pnl": round(credit - owed, 3)}


def run(client) -> dict:
    end = (date.today() - timedelta(days=1)).isoformat()
    daily = {}
    for s in set(BUY_NAMES) | set(SELL_NAMES):
        daily[s] = {b["t"][:10]: float(b["c"]) for b in client.daily_bars([s], "2024-01-01", adjustment="raw", feed=FEED).get(s, [])}
    spy_days = sorted(d for d in daily["SPY"] if START <= d <= end)
    starts = week_starts(spy_days)
    buys: dict[str, list[tuple[str, float]]] = {}
    sells: dict[str, list[dict]] = {}
    for d0 in starts:
        monday = date.fromisoformat(d0)
        friday = (monday + timedelta(days=4 - monday.weekday())).isoformat()
        if friday > end:
            break
        week_days = [d for d in spy_days if d0 <= d <= friday]
        for und in sorted(set(BUY_NAMES) | set(SELL_NAMES)):
            try:
                sm = et_minutes(client.stock_bars([und], f"{d0}T13:00:00Z", f"{d0}T21:00:00Z", feed=FEED).get(und, []))
            except Exception:
                continue
            bar = at_or_before(sm, ENTRY)
            if not bar or "09:30" not in sm:
                continue
            spot, day_open = float(bar["c"]), float(sm["09:30"]["o"])
            after = (monday + timedelta(days=1)).isoformat()
            cs = []
            for status in ("inactive", "active"):
                try:
                    cs = client.option_contracts(und, status=status, expiration_date_gte=after, expiration_date_lte=friday,
                                                 strike_price_gte=f"{spot * 0.9:.2f}", strike_price_lte=f"{spot * 1.1:.2f}")
                except Exception:
                    cs = []
                if cs:
                    break
            if not cs:
                continue
            exp = max(c["expiration_date"] for c in cs)
            calls = sorted((float(c["strike_price"]), c["symbol"]) for c in cs if c["expiration_date"] == exp and c["type"] == "call")
            puts = sorted((float(c["strike_price"]), c["symbol"]) for c in cs if c["expiration_date"] == exp and c["type"] == "put")
            # the last trading day before expiry, at 15:50 (out before the final day)
            before = [d for d in week_days if d < exp]
            last_key = f"{before[-1]} 15:50" if before else f"{exp} 12:00"
            wanted: dict[str, str] = {}
            plans = []
            if und in BUY_NAMES:
                for mode in MODES:
                    kind = "C" if mode == "calls" or spot >= day_open else "P"
                    for off in OFFSETS:
                        if kind == "C":
                            sym = next((s for k, s in calls if k >= spot * (1 + off)), None)
                        else:
                            below = [s for k, s in puts if k <= spot * (1 - off)]
                            sym = below[-1] if below else None
                        if sym:
                            plans.append(("buy", mode, off, sym))
                            wanted[sym] = sym
            spreads = []
            if und in SELL_NAMES:
                pk = dict(puts)
                for otm in SHORT_OTM:
                    below = [k for k, _ in puts if k <= spot * (1 - otm)]
                    if not below:
                        continue
                    ks = below[-1]
                    for w in WIDTHS:
                        kl = ks - w
                        if kl in pk:
                            spreads.append((otm, w, ks, kl))
                            wanted[pk[ks]] = pk[ks]
                            wanted[pk[kl]] = pk[kl]
            if not wanted:
                continue
            try:
                ob = client.option_bars(sorted(wanted), f"{d0}T13:00:00Z", f"{exp}T21:00:00Z", timeframe="1Min")
            except Exception:
                continue
            time.sleep(0.1)
            mins: dict[str, dict[str, dict]] = {}
            for s, bs in ob.items():
                for b in bs:
                    t = datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ET)
                    mins.setdefault(s, {})[t.strftime("%Y-%m-%d %H:%M")] = b
            entry_key = f"{d0} {ENTRY}"
            for _, mode, off, sym in plans:
                m = {k: v for k, v in mins.get(sym, {}).items() if k >= entry_key}
                first = sorted(m)[:1]
                if not first or first[0] > f"{d0} 10:05":
                    continue
                for take in TAKES:
                    r = buy_trade(m, take, last_key)
                    if r is not None:
                        buys.setdefault(f"{und}|{mode}|{off}|{take}", []).append((d0, r))
            settle = daily[und].get(exp)
            if settle is None:
                continue
            for otm, w, ks, kl in spreads:
                px = {}
                for k in (ks, kl):
                    got = at_or_after({k2[11:]: v for k2, v in mins.get(pk[k], {}).items() if k2.startswith(d0)}, ENTRY)
                    if got:
                        px[k] = float(got[1]["c"])
                if len(px) < 2:
                    continue
                r = spread_result(px[ks], px[kl], ks, kl, settle)
                if r:
                    sells.setdefault(f"{und}|{otm}|{w}", []).append({"day": d0, "expiry": exp, **r})
    return {"generated": date.today().isoformat(), "start": START, "end": end, "weeks": len(starts),
            "buys": {k: [[d, round(r, 4)] for d, r in v] for k, v in buys.items()}, "sells": sells}


def buy_table(rep: dict) -> dict:
    out = {}
    for key, ts in rep["buys"].items():
        take = int(key.split("|")[3])
        rets = [r for _, r in ts]
        row = {"n": len(rets), "mean": round(statistics.mean(rets), 3), "median": round(statistics.median(rets), 3),
               "win": round(sum(r > 0 for r in rets) / len(rets), 3), "hit": round(sum(r >= take - 1 for r in rets) / len(rets), 3)}
        stream = [(d, max(FRACTION * r, -1.0)) for d, r in ts]
        first = (date.fromisoformat(ts[0][0]) - timedelta(days=1)).isoformat()
        for t in TARGETS:
            seq = rh.sequential(stream, first, t, END_LOSS)
            row[f"t{t}"] = {"heroes": seq["heroes"], "zeros": seq["zeros"]}
        out[key] = row
    return out


def sell_table(rep: dict) -> dict:
    out = {}
    for key, trades in rep["sells"].items():
        pnl = [t["pnl"] / t["max_loss"] for t in trades]
        row = {"n": len(trades), "win": round(sum(t["pnl"] > 0 for t in trades) / len(trades), 3),
               "per_risk": round(statistics.mean(pnl), 3), "worst": round(min(pnl), 2),
               "credit": round(statistics.median(t["credit"] for t in trades), 2)}
        for risk in RISK:
            eq, curve, placed = CAPITAL, [(rep["start"], CAPITAL)], 0
            for t in sorted(trades, key=lambda t: t["day"]):
                n = int(risk * eq // (t["max_loss"] * 100))
                if n >= 1:
                    placed += 1
                    eq += n * t["pnl"] * 100
                curve.append((t["expiry"], eq))
            row[f"acct{risk}"] = {**curve_stats(curve, rep["start"], rep["end"]), "placed": placed, "final": round(eq, 2)}
        out[key] = row
    return out


def markdown(rep: dict, bt: dict, st: dict) -> str:
    out = [f"# 周内期权：买方 vs 卖方 {rep['generated']}", "",
           f"{rep['start']} 至 {rep['end']}，{rep['weeks']} 周。每周第一个交易日 10:00 进场，用当周最后一个到期日（通常周五）。只研究，不改交易。", "",
           "## 买方：顺势（涨买看涨、跌买看跌）或只买看涨，止盈 2 / 3 倍，没到就在到期前一天 15:50 卖", "",
           "买入多付 10%、卖出少拿 10%。玩法：$500，每笔押一半，低于 $50 归零，到 $1,000 / $10,000 成功。", "",
           "| 标的 | 方向 | 离现价 | 止盈 | 笔数 | 平均每 $1 | 中位数 | 赚钱比例 | 碰到止盈 | 到 $1,000：成功 / 归零 | 到 $10,000：成功 / 归零 |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for key, r in sorted(bt.items(), key=lambda x: -x[1]["mean"]):
        und, mode, off, take = key.split("|")
        out.append(f"| {und} | {'顺势' if mode == 'trend' else '只买看涨'} | {float(off):.1%} | {take} 倍 | {r['n']} | {r['mean']:+.0%} | "
                   f"{r['median']:+.0%} | {r['win']:.0%} | {r['hit']:.0%} | {r['t2']['heroes']} / {r['t2']['zeros']} | "
                   f"{r['t20']['heroes']} / {r['t20']['zeros']} |")
    out += ["", f"共 {len(bt)} 种，平均为正的 {sum(r['mean'] > 0 for r in bt.values())} 种。", "",
            "## 卖方：卖看跌价差，拿到期", "",
            "卖出离现价 1% / 2% / 3% 以下的看跌、买再低 $1 / $2 / $5 的看跌，每腿 max($0.01, 3%) 成本，到期按当天收盘结算。"
            "账户 $500，每周最大亏损合计占账户 25% / 50%，只能买整组（账户小时常常一组都开不了）。", "",
            "| 标的 | 离现价 | 宽 | 笔数 | 胜率 | 每 $1 风险平均 | 最差一笔 | 收到（中位数） | 25%：开了几周 / 年化 / 最大回撤 / 期末 | 50%：开了几周 / 年化 / 最大回撤 / 期末 |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for key, r in sorted(st.items(), key=lambda x: -x[1]["acct0.25"]["final"]):
        und, otm, w = key.split("|")
        a, b = r["acct0.25"], r["acct0.5"]
        out.append(f"| {und} | {float(otm):.0%} | ${float(w):g} | {r['n']} | {r['win']:.0%} | {r['per_risk']:+.0%} | {r['worst']:+.0%} | ${r['credit']:.2f} | "
                   f"{a['placed']} / {a['cagr']:+.0%} / {a['max_dd']:.0%} / ${a['final']:,.0f} | {b['placed']} / {b['cagr']:+.0%} / {b['max_dd']:.0%} / ${b['final']:,.0f} |")
    return "\n".join(out) + "\n"


def main() -> None:
    from hero.alpaca import Alpaca
    root = Path(__file__).resolve().parent.parent
    rep = run(Alpaca())
    bt, st = buy_table(rep), sell_table(rep)
    rep["buy_table"], rep["sell_table"] = bt, st
    (root / "research" / f"{rep['generated']}-weekly.json").write_text(json.dumps(rep, ensure_ascii=False) + "\n")
    (root / "research" / f"{rep['generated']}-weekly.md").write_text(markdown(rep, bt, st))
    print(markdown(rep, bt, st))


if __name__ == "__main__":
    sys.exit(main())
