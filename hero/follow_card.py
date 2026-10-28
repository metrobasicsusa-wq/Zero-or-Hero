"""今日可跟单, the signal edition (docs/questions/2026-10-28-follow-card.md; owner, 10-28: 「这样累积样本也太慢了」).

The old card recommended SPY's 10:00 same-day call every day, a rule whose 664-day backtest lost 14% a $1 (median
-86%). This card only speaks when one of the bottom studies' rules fires, at about 15:45, on the price then:

- 快线 (fast): SMH's third straight down close (hero.sleeve_rules.Streak: the last two completed closes each fell,
  the one before them did not, and the price now is under yesterday's close). The call nearest the money on the
  nearest expiry 20-45 days out (hero.research_bottom_long.legs), bought at 15:50, sold at 2x, else on the 20th
  session. research/*-bottom-long.json, SMH down3 at the money, exit take2.
- 深度 (depth): SPY / QQQ / SMH / IWM first closing 8% under their 20-session high (21 closes with today's price, as
  the backtests and docs/bottom-depth-2026-10-27.md section 4 compute it; yesterday not). The monthly expiry 75-130
  days out: at the money (first strike at or above the price) held 63 sessions, 5% out held 42, or the $5 call
  spread from the money sold at $4. research/*-bottom-2m.json and *-bottom-spread.json.

Evidence is computed from those files on each run (hero.robust), never copied by hand; what to buy per account size
follows docs/quick-to-10k-2026-10-27.md, docs/quick-start-10k-2026-10-27.md and
docs/questions/2026-10-28-single-leg.answer.md. On a day without a signal the card says so and lists the nearest
trigger prices. It writes posts/follow/YYYY-MM-DD.md (Discord) and exchange/claude/YYYY-MM-DD.follow.json (the other
Claudes); it sends nothing and orders nothing. Paper-trading reference, not investment advice.

    python -m hero.follow_card [--date YYYY-MM-DD] [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from hero.signals import LEVEL, WINDOW, depth, down_run, fast_signal, is_monthly   # noqa: F401  (shared: hero.opportunities, sleeve_rules.Ladder)

ROOT = Path(__file__).resolve().parent.parent
ET = ZoneInfo("America/New_York")
ETFS = ("SMH", "QQQ", "IWM", "SPY")
FAST_DTE, DEPTH_DTE = (20, 45), (75, 130)
FAST_HOLD, ATM_HOLD, OTM5_HOLD = 20, 63, 42
ACCOUNTS = (500, 1000, 2000, 10000)
SPREAD_SELL = 4.0
# NYSE full-day holidays (for counting sessions to an exit, and the replay's calendar)
HOLIDAYS = {date(2024, 1, 1), date(2024, 1, 15), date(2024, 2, 19), date(2024, 3, 29), date(2024, 5, 27), date(2024, 6, 19),
            date(2024, 7, 4), date(2024, 9, 2), date(2024, 11, 28), date(2024, 12, 25), date(2025, 1, 1), date(2025, 1, 9),
            date(2025, 1, 20), date(2025, 2, 17), date(2025, 4, 18), date(2025, 5, 26), date(2025, 6, 19), date(2025, 7, 4),
            date(2025, 9, 1), date(2025, 11, 27), date(2025, 12, 25),
            date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3), date(2026, 5, 25), date(2026, 6, 19),
            date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25), date(2027, 1, 1), date(2027, 1, 18),
            date(2027, 2, 15), date(2027, 3, 26), date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6),
            date(2027, 11, 25), date(2027, 12, 24)}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


# ---------- sessions ----------
def is_session(d: date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS


def sessions_after(d: date, n: int) -> date:
    while n:
        d += timedelta(days=1)
        if is_session(d):
            n -= 1
    return d


# ---------- signals: hero.signals (closes: completed daily closes before the day, oldest first; spot: the price now) ----------
def nearest(closes: dict[str, list[float]], spots: dict[str, float]) -> list[str]:
    """Plain lines: how far each rule is from firing at today's close."""
    out = []
    for und in ETFS:
        cs, spot = closes.get(und), spots.get(und)
        if not cs or not spot or len(cs) < WINDOW:
            continue
        d = depth(cs, spot)
        if d["first"]:
            continue
        if d["armed"]:
            out.append(f"{und} 收在 ${d['trigger']:,.2f} 以下就是深度信号（现在离 20 日高点 {d['dd']:+.1%}，还要跌 {1 - d['trigger'] / spot:.1%}）")
        elif d["dd"] > -LEVEL:
            out.append(f"{und} 昨天在 8% 线下，今天收回到 8% 以内（{d['dd']:+.1%}）；明天起再收在 8% 线下就是新的信号")
        else:
            out.append(f"{und} 已经在 20 日高点下 8% 以下（{d['dd']:+.1%}），不会再触发，要先收回到 ${d['trigger']:,.2f} 以上")
    cs, spot = closes.get("SMH"), spots.get("SMH")
    if cs and spot and len(cs) >= 4 and not fast_signal(cs, spot):
        k = down_run(cs)
        if k == 2:
            out.append(f"SMH 已连跌 2 天，今天收在 ${cs[-1]:,.2f} 以下就是连跌第 3 天（快线信号）")
        elif k == 1:
            out.append(f"SMH 连跌 1 天：今天收在 ${cs[-1]:,.2f} 以下、明天再收低，就是连跌第 3 天")
        elif k == 0:
            out.append(f"SMH 要连跌 3 天：今天收在 ${cs[-1]:,.2f} 以下是第 1 天")
        else:
            out.append(f"SMH 已连跌 {k} 天，第 4 天以后不算（回测里不赚），要先收一次阳线再重新数")
    return out


# ---------- contracts (chain: CBOE delayed quotes' "options" list) ----------
def parse_chain(options: list[dict]) -> list[dict]:
    """Calls only: expiry, strike, bid, ask, symbol from OCC symbols (ROOT YYMMDD C STRIKE*1000)."""
    out = []
    for o in options:
        sym = str(o.get("option") or "")
        tail = sym[-15:]
        if len(sym) <= 15 or tail[6] != "C" or not tail[:6].isdigit():
            continue
        out.append({"symbol": sym, "expiry": f"20{tail[:2]}-{tail[2:4]}-{tail[4:6]}", "strike": int(tail[7:]) / 1000,
                    "bid": float(o.get("bid") or 0), "ask": float(o.get("ask") or 0)})
    return out


def fast_contract(chain: list[dict], spot: float, day: date) -> dict | None:
    """research_bottom_long.legs: the nearest expiry 20-45 days out, the strike nearest the price."""
    exps = sorted({c["expiry"] for c in chain if FAST_DTE[0] <= (date.fromisoformat(c["expiry"]) - day).days <= FAST_DTE[1]})
    if not exps:
        return None
    cs = [c for c in chain if c["expiry"] == exps[0] and c["ask"] > 0]
    return min(cs, key=lambda c: abs(c["strike"] - spot)) if cs else None


def depth_contracts(chain: list[dict], spot: float, day: date) -> dict:
    """The nearest monthly expiry 75-130 days out: at the money (first strike at or above the price), 5% out, and the
    $5 spread from the money (long the at-the-money call, short the strike $5 above)."""
    listed = {c["expiry"] for c in chain}
    exps = sorted(e for e in listed if is_monthly(e, listed) and DEPTH_DTE[0] <= (date.fromisoformat(e) - day).days <= DEPTH_DTE[1])
    if not exps:
        return {}
    cs = sorted((c for c in chain if c["expiry"] == exps[0]), key=lambda c: c["strike"])
    first = lambda lv: next((c for c in cs if c["strike"] >= spot * lv - 1e-9 and c["ask"] > 0), None)
    atm, otm5 = first(1.0), first(1.05)
    out = {"expiry": exps[0], "atm": atm, "otm5": otm5}
    if atm:
        short = next((c for c in cs if abs(c["strike"] - atm["strike"] - 5) < 1e-6), None)
        if short and short["bid"] + short["ask"] > 0:
            mid = (atm["bid"] + atm["ask"]) / 2 - (short["bid"] + short["ask"]) / 2
            out["spread"] = {"long": atm, "short": short, "mid": round(mid, 2), "natural": round(atm["ask"] - short["bid"], 2)}
    return out


# ---------- evidence (from the studies' saved trades; hero.robust) ----------
def latest(name: str, root: Path = ROOT) -> dict | None:
    files = sorted((root / "research").glob(f"20*-{name}.json"))
    return json.loads(files[-1].read_text()) if files else None


def evidence(root: Path = ROOT) -> dict:
    """Each line's (day, return per $1) trades, summarised by hero.robust (mean, without the best 3, median, wins, years,
    the last three months, extra costs, warnings)."""
    from hero import robust
    long_, b2, sp = latest("bottom-long", root), latest("bottom-2m", root), latest("bottom-spread", root)
    out = {}

    def summ(key, rows, keep, exit_, end):
        xs = [(r["day"], (r.get("res") or {}).get(exit_)) for r in rows if keep(r)]
        out[key] = robust.summary([x for x in xs if x[1] is not None], end=end)
    if long_:
        summ("fast", long_["rows"], lambda r: r["und"] == "SMH" and r.get("sig") == "down3" and r.get("leg") == "atm", "take2", long_.get("end"))
    if b2:
        for und in ETFS + (None,):
            ok = (lambda r, u=und: (r["und"] == u if u else r["und"] in ETFS))
            tag = und or "all"
            summ(f"atm63:{tag}", b2["rows"], lambda r, ok=ok: ok(r) and "dd8" in r["tags"] and r.get("leg") == "atm", "d63", b2.get("end"))
            summ(f"otm5_42:{tag}", b2["rows"], lambda r, ok=ok: ok(r) and "dd8" in r["tags"] and r.get("leg") == "otm5", "d42", b2.get("end"))
    if sp:
        for und in ETFS + (None,):
            ok = (lambda r, u=und: (r["und"] == u if u else r["und"] in ETFS))
            summ(f"spread:{und or 'all'}", sp["rows"], lambda r, ok=ok: ok(r) and "dd8" in r["tags"] and r.get("leg") == "atm5", "take80", sp.get("end"))
    return out


def ev_text(s: dict | None) -> str:
    if not s:
        return "没有回测成交"
    p = lambda x: "—" if x is None else f"{x:+.0%}"
    yrs = "、".join(f"{y} {m:+.0%}（{n}）" for y, (m, n) in s["years"].items())
    flags = "；".join(s["flags"])
    return (f"{s['n']} 笔，每 $1 平均 {p(s['mean'])}，去掉最好 3 笔 {p(s['trim'])}，中位数 {p(s['median'])}，{s['win']:.0%} 赚，各年份 {yrs}"
            + (f"；提醒：{flags}" if flags else ""))


# ---------- what to buy, per account ----------
def plans_fast(c: dict | None) -> list[dict]:
    """docs/quick-to-10k: the fast line is a $2,000 line (all in); below it one contract is out of reach; at $10,000 it
    is bag C ($2,500, half)."""
    out = []
    cost = c["ask"] * 100 if c else None
    for acct in ACCOUNTS:
        if not cost:
            out.append({"account": acct, "buy": "没有报价", "n": 0, "cost": 0.0})
            continue
        budget = acct if acct < 10000 else 1250.0
        n = int(budget // cost) or (1 if acct == 10000 and cost <= 2500 else 0)
        how = "押满" if acct < 10000 else "C 袋 $2,500 押一半"
        out.append({"account": acct, "buy": f"{c['strike']:g}C（{c['expiry']} 到期）" if n else f"买不起（一张 ${cost:,.0f}），这条线跳过",
                    "n": n, "cost": round(n * cost, 2), "how": how if n else "—"})
    return out


def plans_depth(ct: dict) -> list[dict]:
    """$500 / $1,000: the $5 spread, all in; $2,000: the 5%-out call held 42 sessions, all in, else spreads (the single-leg
    answer: at today's prices one contract is often out of reach); $10,000: bag A $5,000 at-the-money half + bag B $2,500
    in spreads."""
    out = []
    if not ct:   # no quotes (a past day, or the chain did not load): say so, not "cannot afford"
        return [{"account": acct, "buy": "没有当天的报价", "n": 0, "cost": 0.0, "how": "—"} for acct in ACCOUNTS]
    sp, atm, otm5 = ct.get("spread"), ct.get("atm"), ct.get("otm5")
    sp_cost = sp["mid"] * 100 if sp else None
    for acct in ACCOUNTS:
        if acct <= 1000:
            n = int(acct // sp_cost) if sp_cost else 0
            out.append({"account": acct, "buy": f"$5 价差 {sp['long']['strike']:g}/{sp['short']['strike']:g}（中间价）" if n else "买不起 / 没有 $5 价差",
                        "n": n, "cost": round(n * (sp_cost or 0), 2), "how": "押满" if n else "—"})
        elif acct == 2000:
            c = otm5["ask"] * 100 if otm5 else None
            if c and c <= acct:
                out.append({"account": acct, "buy": f"价外 5% {otm5['strike']:g}C，拿 42 天", "n": int(acct // c), "cost": round(int(acct // c) * c, 2), "how": "押满"})
            elif sp_cost:
                n = int(acct / 2 // sp_cost)
                out.append({"account": acct, "buy": f"价外 5% 一张 ${c:,.0f} 买不起；改做 $5 价差 {sp['long']['strike']:g}/{sp['short']['strike']:g}" if c else "改做 $5 价差",
                            "n": n, "cost": round(n * sp_cost, 2), "how": "押一半"})
            else:
                out.append({"account": acct, "buy": "买不起", "n": 0, "cost": 0.0, "how": "—"})
        else:
            c = atm["ask"] * 100 if atm else None
            n = (int(2500 // c) or (1 if c <= 5000 else 0)) if c else 0
            m = int(2500 // sp_cost) if sp_cost else 0
            out.append({"account": acct, "buy": (f"A 袋：平值 {atm['strike']:g}C 拿 63 天 {n} 张" if n else "A 袋：平值买不起")
                        + (f"；B 袋：$5 价差 {m} 组" if m else ""), "n": n, "cost": round(n * (c or 0) + m * (sp_cost or 0), 2),
                        "how": "A 袋 $5,000 押一半，B 袋 $2,500 押满"})
    return out


# ---------- the card ----------
def build(day: date, closes: dict[str, list[float]], spots: dict[str, float], chains: dict[str, list[dict]],
          ev: dict, written: str, priced: bool = True) -> tuple[str, dict]:
    """The page and the exchange JSON. `closes`: completed closes before `day`; `spots`: the price at about 15:45;
    `chains`: parsed CBOE calls per symbol; `priced` False for a past day (no quotes of that day)."""
    sigs = []
    if closes.get("SMH") and spots.get("SMH") and fast_signal(closes["SMH"], spots["SMH"]):
        c = fast_contract(chains.get("SMH") or [], spots["SMH"], day) if priced else None
        sigs.append({"line": "fast", "und": "SMH", "contract": c, "exit_day": sessions_after(day, FAST_HOLD).isoformat(),
                     "plans": plans_fast(c)})
    for und in ETFS:
        cs, spot = closes.get(und), spots.get(und)
        if not cs or not spot or len(cs) < WINDOW:
            continue
        d = depth(cs, spot)
        if d["first"]:
            ct = depth_contracts(chains.get(und) or [], spot, day) if priced else {}
            sigs.append({"line": "depth", "und": und, "hi": round(d["hi"], 2), "dd": round(d["dd"], 4), "contracts": ct,
                         "exit_atm": sessions_after(day, ATM_HOLD).isoformat(), "exit_otm5": sessions_after(day, OTM5_HOLD).isoformat(),
                         "plans": plans_depth(ct)})
    near = nearest(closes, spots)
    m = lambda x: f"${x:,.2f}"
    L = [f"# 今日可跟单 {day:%m-%d}", "", "## 今天的信号", ""]
    if not sigs:
        L += ["- **今天没有信号。** 快线（SMH 连跌第 3 天）和深度（四只 ETF 第一次收在 20 日高点下 8%）都没触发，今天不跟。",
              "- 旧卡的「SPY 10:00 当天到期」不再推荐：664 天回测每 $1 −14%、中位数 −86%（research/2026-10-16-zdte-minmove.md）。"]
    for s in sigs:
        if s["line"] == "fast":
            cs = closes["SMH"]
            L += [f"- **快线：SMH 连跌第 3 天**（{cs[-3]:,.2f} → {cs[-2]:,.2f} → {cs[-1]:,.2f} → 现价 {spots['SMH']:,.2f}）"]
        else:
            L += [f"- **深度：{s['und']} 第一次收在 20 日高点下 8%**（高点 {m(s['hi'])} → 现价 {m(spots[s['und']])}，{s['dd']:+.1%}）"]
    for s in sigs:
        if s["line"] == "fast":
            c = s["contract"]
            double = f"（卖价到 {m(2 * c['ask'])}）" if c else ""
            L += ["", "## 快线：SMH 连跌第 3 天", "",
                  f"- 合约：SMH {c['expiry']} 到期 {c['strike']:g}C（最接近现价），买价 / 卖价 {c['bid']:.2f} / {c['ask']:.2f}，一张 **{m(c['ask'] * 100)}**" if c
                  else "- 合约：没有拿到当天的报价（历史日期或报价缺失）",
                  "- 什么时候买：15:50（用这时的卖价限价）。",
                  f"- 怎么卖：翻倍就卖{double}，否则第 20 个交易日（{s['exit_day']}）15:50 卖。",
                  f"- 回测（SMH 连跌第 3 天 · 20–45 天平值 · 2 倍止盈否则第 20 天）：{ev_text(ev.get('fast'))}。"]
        else:
            ct, und = s["contracts"], s["und"]
            L += ["", f"## 深度：{und} 第一次跌 8%", ""]
            if ct:
                a, o5, sp = ct.get("atm"), ct.get("otm5"), ct.get("spread")
                L += [f"- 到期：{ct['expiry']}（月度，75–130 天）。"]
                if a:
                    L += [f"- 平值 {a['strike']:g}C：买价 / 卖价 {a['bid']:.2f} / {a['ask']:.2f}，一张 **{m(a['ask'] * 100)}**；拿到第 63 个交易日（{s['exit_atm']}）或到期，不止盈不止损。"]
                if o5:
                    L += [f"- 价外 5% {o5['strike']:g}C：{o5['bid']:.2f} / {o5['ask']:.2f}，一张 **{m(o5['ask'] * 100)}**；拿到第 42 个交易日（{s['exit_otm5']}）。"]
                if sp:
                    L += [f"- $5 价差 买 {sp['long']['strike']:g}C / 卖 {sp['short']['strike']:g}C：中间价 {sp['mid']:.2f}（一组 **{m(sp['mid'] * 100)}**），"
                          f"自然价 {sp['natural']:.2f}；限价贴着中间价，价差到 $4 就卖，否则到期。"]
            else:
                L += ["- 合约：没有拿到当天的报价（历史日期或报价缺失）。"]
            L += ["- 什么时候买：15:50。",
                  f"- 回测 · 平值拿 63 天（{und}）：{ev_text(ev.get(f'atm63:{und}'))}。",
                  f"- 回测 · 平值拿 63 天（四只 ETF）：{ev_text(ev.get('atm63:all'))}。",
                  f"- 回测 · 价外 5% 拿 42 天（四只 ETF）：{ev_text(ev.get('otm5_42:all'))}。",
                  f"- 回测 · $5 价差到 $4 卖（四只 ETF）：{ev_text(ev.get('spread:all'))}。"]
    if sigs:
        L += ["", "## 按本金怎么买", "", "| 本金 | 线 | 买什么 | 几张 / 组 | 花多少 | 押法 |", "|---|---|---|---|---|---|"]
        for s in sigs:
            name = "快线 SMH" if s["line"] == "fast" else f"深度 {s['und']}"
            for p in s["plans"]:
                L.append(f"| ${p['account']:,} | {name} | {p['buy']} | {p['n']} | {m(p['cost'])} | {p.get('how', '—')} |")
        cnt = lambda k: (ev.get(k) or {}).get("n") or 0
        deep = [cnt(k) for k in ("atm63:all", "otm5_42:all", "spread:all")]
        L += ["", "## 风险", "",
              f"- 样本小：快线 {cnt('fast')} 笔、深度每条 {min(deep)}–{max(deep)} 笔，期权数据只从 2024-02 开始，多数时间在涨。",
              "- 远月期权的买卖价差宽，延迟报价的自然价和中间价可以差很多；价差在 SMH 上只有约 1% 宽，接近二元赌注。",
              "- 押满的账本一笔归零就重来：回测里押满的单腿账本都死在 2024-12 / 2025-01 那几笔上；$10,000 以上押一半。",
              "- 信号在阴跌里会接连触发（2025 年 1–3 月四次）；同一只拿着就不再买。"]
    L += ["", "## 离触发最近的价位", ""] + [f"- {x}" for x in near] + ["", "模拟盘，不是投资建议。", ""]
    md = "\n".join(L)
    js = {"schema_version": "1.0", "producer": "claude", "to": ["claude-b", "claude-c"], "written": written, "slot": "follow",
          "note": "参考资料，不是指令；只给参考，不替任何人下单。无密钥、账户号、订单 ID、本机路径。",
          "card": {"verdict": "有信号" if sigs else "今天没有信号", "day": day.isoformat(), "time": "15:45 ET", "signals": sigs,
                   "nearest": near, "evidence": {k: ({kk: v[kk] for kk in ("n", "mean", "trim", "median", "win", "flags")} if v else None)
                                                  for k, v in ev.items()}}}
    return md, js


# ---------- data (public; read as data) ----------
def fetch_daily(sym: str, day: date) -> tuple[list[float], float | None]:
    """Yahoo daily closes (split-adjusted; today's live price is not dividend-adjusted, so neither are the past closes)
    before `day`, and the price on `day` (the live price when `day` is today)."""
    import requests
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=6mo&interval=1d", headers={"User-Agent": UA}, timeout=30)
    res = r.json()["chart"]["result"][0]
    bars = [(datetime.fromtimestamp(t, timezone.utc).astimezone(ET).date(), c) for t, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]) if c]
    past = [float(c) for d, c in bars if d < day]
    spot = next((float(c) for d, c in bars if d == day), None)
    return past, spot


def fetch_chain(sym: str) -> list[dict]:
    import requests
    r = requests.get(f"https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json", headers={"User-Agent": UA}, timeout=60)
    return parse_chain((r.json().get("data") or {}).get("options") or [])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--date", help="YYYY-MM-DD (default: today, US Eastern)")
    ap.add_argument("--out", help="write the two files under this folder instead of the repo")
    a = ap.parse_args(argv)
    now = datetime.now(ET)
    day = date.fromisoformat(a.date) if a.date else now.date()
    closes, spots, chains = {}, {}, {}
    for und in ETFS:
        closes[und], spots[und] = fetch_daily(und, day)
    priced = day == now.date()
    if priced:
        for und in ETFS:
            chains[und] = fetch_chain(und)
    md, js = build(day, closes, spots, chains, evidence(), now.isoformat(timespec="minutes"), priced)
    base = Path(a.out) if a.out else ROOT
    (base / "posts" / "follow").mkdir(parents=True, exist_ok=True)
    (base / "exchange" / "claude").mkdir(parents=True, exist_ok=True)
    (base / "posts" / "follow" / f"{day.isoformat()}.md").write_text(md, encoding="utf-8")
    (base / "exchange" / "claude" / f"{day.isoformat()}.follow.json").write_text(json.dumps(js, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
