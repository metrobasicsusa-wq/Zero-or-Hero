"""The opportunity log: every backtested bottom signal of the day, taken or not, scored later (owner, 10-28: 「这样子我们以后
培养的机会发现比较高」「人的脑袋一时间分析不了那么多」; docs/questions/2026-10-28-ladder-and-opportunities.md, task 2).

Each signal a backtest has looked at is one row a day per name and per backtested way to buy it (VARIANTS): the day,
the signal, the name, how far past its trigger, whether the ladder ledger (hero.research_ladder; live: the plan ledger
`ladder-plan-500`) would take it that day, and if not why -- 「阶段不对」 (a ladder play its balance is not on),
「买不起」, 「同一只拿着」, or 「回测不够」 (not one of the ladder's three plays) -- so that in a few months we can see
whether what we pass over beats what we take. The sniper's `skipped` (journal-s500/sniper/README.md) did this for one rule.

    python -m hero.opportunities scan [--date YYYY-MM-DD] [--out DIR]   # about 15:50: research/opportunities/YYYY-MM-DD.json
    python -m hero.opportunities score [--date YYYY-MM-DD] [--weekly]   # after the close: marks, exits, the Friday summary
    python -m hero.opportunities backfill                                # 2024-02 on, from the studies' saved trades

The scan reads Yahoo daily closes (and 5-minute bars for the 10:00 bounce) and CBOE delayed option quotes for the
contract each variant buys (its ask; a spread at its mid), and writes only files. The scorer books every logged row
with a contract in research/opportunities/book.json and marks it each evening on CBOE quotes (a single leg at its
bid, a spread at long mid - short mid), closing it by the variant's own exit (2x / $4 / the Nth session / the
expiry). The backfill uses the studies' per-trade results (5% paid a side) as "if taken" and the dated $500 ladder
replay for "taken or not". Research only; nothing is ordered."""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from hero import robust
from hero.follow_card import (ET, ETFS, ROOT, SPREAD_SELL, UA, depth_contracts, fast_contract, fetch_chain, fetch_daily,
                              is_session, latest)
from hero.signals import bounce, depth, down_run, fast_signal, green_signal

OUT = ROOT / "research" / "opportunities"
BOOK = OUT / "book.json"
STOCKS = ("NVDA", "AVGO", "AMD", "MU", "TSM", "ASML", "AMAT", "LRCX", "KLAC", "QCOM", "INTC", "MRVL", "TXN", "ARM")
BOUNCE_NAMES = ("AVGO", "NVDA", "AMD", "MU")
WIDTH = 5.0
# variant -> the signal, the way the backtest bought it, its exit, the names, and the ladder play it is (if any)
VARIANTS: dict[str, dict] = {
    "down3": {"sig": "down3", "name": "连跌第 3 天 · 20–45 天平值 · 2 倍止盈否则第 20 天", "src": "bottom-long", "leg": "atm",
              "exit": "take2", "hold": 20, "unds": ETFS, "ladder": ("fast", ("SMH",))},
    "green1": {"sig": "green1", "name": "连跌后第一根阳线 · 20–45 天价外 5% · 拿 10 天", "src": "bottom-long", "leg": "otm5",
               "exit": "d10", "hold": 10, "unds": ETFS, "ladder": None},
    "dd8-spread": {"sig": "dd8", "name": "ETF 第一次跌 8% · 平值 +$5 价差 · 到 $4 卖否则到期", "src": "bottom-spread", "leg": "atm5",
                   "exit": "take80", "hold": None, "unds": ETFS, "ladder": ("spread", ETFS)},
    "dd8-atm": {"sig": "dd8", "name": "ETF 第一次跌 8% · 月度平值 · 拿 63 天", "src": "bottom-2m", "leg": "atm",
                "exit": "d63", "hold": 63, "unds": ETFS, "ladder": ("atm63", ETFS)},
    "dd8-otm5": {"sig": "dd8", "name": "ETF 第一次跌 8% · 月度价外 5% · 拿 42 天", "src": "bottom-2m", "leg": "otm5",
                 "exit": "d42", "hold": 42, "unds": ETFS, "ladder": None},
    "dd20-atm": {"sig": "dd20", "name": "半导体股第一次跌 20% · 月度平值 · 拿 63 天", "src": "bottom-semi", "leg": "atm",
                 "exit": "d63", "hold": 63, "unds": STOCKS, "ladder": None},
    "dd20-otm5": {"sig": "dd20", "name": "半导体股第一次跌 20% · 月度价外 5% · 拿 63 天", "src": "bottom-semi", "leg": "otm5",
                  "exit": "d63", "hold": 63, "unds": STOCKS, "ladder": None},
    "bounce": {"sig": "bounce", "name": "板块反弹日 · 自己 10:00 涨 ≥1% · 价外 0.6% · 拿到 15:50", "src": "bounce", "leg": "otm06",
               "exit": "d0", "hold": 0, "unds": BOUNCE_NAMES, "ladder": None},
}
LEVELS = {"dd8": 0.08, "dd20": 0.20}
SIG_NAMES = {"down3": "连跌第 3 天", "green1": "连跌后第一根阳线", "dd8": "ETF 第一次跌 8%", "dd20": "半导体股第一次跌 20%", "bounce": "板块反弹日"}
REASONS = ("选了", "阶段不对", "买不起", "同一只拿着", "回测不够")


# ---------- the ladder's choice ----------
def ladder_choice(v: dict, und: str, ph: int, cash: float, eq: float, held: set[tuple[str, str]],
                  cost: float | None) -> tuple[str, int]:
    """Would the ladder take this row today, and if not why (hero.research_ladder's rules and sizing): (why, units)."""
    from hero.research_ladder import PHASES, size
    lad = v.get("ladder")
    if not lad or und not in lad[1]:
        return "回测不够", 0
    line = lad[0]
    if line not in PHASES[ph]:
        return "阶段不对", 0
    if (line, und) in held:
        return "同一只拿着", 0
    if cost is None:
        return "买不起", 0   # no contract to buy
    n = size(line, ph, cash, eq, cost)[0]
    return ("选了", n) if n >= 1 else ("买不起", 0)


# ---------- backfill (the studies' saved trades) ----------
def _matches(v: dict, r: dict) -> bool:
    if r.get("leg") != v["leg"] or r.get("und") not in v["unds"]:
        return False
    if v["src"] == "bottom-long":
        return r.get("sig") == v["sig"]
    if v["src"] == "bounce":
        return "bounce1" in (r.get("groups") or []) and (r.get("u10") or 0) >= 0.01
    return v["sig"] in (r.get("tags") or [])


def distance(v: dict, r: dict) -> tuple[float | None, str]:
    """How far past its trigger the row was (a number and a plain line)."""
    sig = v["sig"]
    if sig in LEVELS:
        dd = r.get("dd20")
        return (round(dd + LEVELS[sig], 4) if dd is not None else None,
                f"离 20 日高点 {dd:+.1%}（线 −{LEVELS[sig]:.0%}）" if dd is not None else "—")
    if sig == "bounce":
        return (round(r.get("m10", 0) - 0.01, 4), f"SMH 前 1–3 天 {r.get('drop', 0):+.1%}、10:00 离开盘 {r.get('m10', 0):+.1%}；"
                                                  f"{r['und']} 10:00 {r.get('u10', 0):+.1%}")
    dd = r.get("dd")
    return (dd, ("连跌第 3 天" if sig == "down3" else "连跌 3 天后第一根阳线") + (f"，离 20 日高点 {dd:+.1%}" if dd is not None else ""))


def backfill_rows(docs: dict[str, dict], log: list[dict]) -> list[dict]:
    """Every backtested row since 2024-02 with "if taken" (the study's own exit, 5% a side) and the dated $500 ladder's
    choice that day (the replay's log: hero.research_ladder.ladder(..., bank=None))."""
    chose = {(d["day"], d["line"], d["und"]): d for d in log}
    out = []
    for key, v in VARIANTS.items():
        doc = docs.get(v["src"]) or {}
        for r in doc.get("rows", []):
            if not _matches(v, r):
                continue
            ret = (r.get("res") or {}).get(v["exit"])
            if ret is None:
                continue
            lad = v.get("ladder")
            d = chose.get((r["day"], lad[0], r["und"])) if lad and r["und"] in lad[1] else None
            dist, text = distance(v, r)
            out.append({"day": r["day"], "sig": v["sig"], "variant": key, "und": r["und"], "dist": dist, "dist_text": text,
                        "picked": bool(d and d["why"] == "选了"), "why": d["why"] if d else "回测不够",
                        "phase": d["phase"] if d else None, "if_done": round(float(ret), 4), "exit": v["exit"]})
    return sorted(out, key=lambda x: (x["day"], x["variant"], x["und"]))


# ---------- the summary (weekly and for the backfill) ----------
def summarize(rows: list[dict], title: str, intro: str, end: str | None = None) -> str:
    """Taken against passed over, by variant and by signal, with hero.robust's checks."""
    scored = [r for r in rows if r.get("if_done") is not None]
    p = lambda x: "—" if x is None else f"{x:+.0%}"
    L = [f"# {title}", "", intro, "",
         "## 按买法：选了的和没选的", "",
         "| 买法 | 全部（笔 / 平均 / 中位数） | 选了 | 没选：阶段不对 | 没选：买不起 | 没选：同一只拿着 | 没选：回测不够 |", "|---|---|---|---|---|---|---|"]

    def cell(xs: list[dict]) -> str:
        ys = [x["if_done"] for x in xs]
        return f"{len(ys)} / {p(statistics.mean(ys))} / {p(statistics.median(ys))}" if ys else "—"
    for key, v in VARIANTS.items():
        g = [r for r in scored if r["variant"] == key]
        if not g:
            continue
        L.append(f"| {v['name']} | {cell(g)} | " + " | ".join(cell([r for r in g if r["why"] == w]) for w in REASONS) + " |")
    L += ["", "## 按信号（几种买法合在一起）", "", "| 信号 | 选了（笔 / 平均 / 中位数） | 没选 |", "|---|---|---|"]
    for sig, name in SIG_NAMES.items():
        g = [r for r in scored if r["sig"] == sig]
        if g:
            L.append(f"| {name} | {cell([r for r in g if r['picked']])} | {cell([r for r in g if not r['picked']])} |")
    groups: dict[str, dict | None] = {}
    picked = [(r["day"], r["if_done"]) for r in scored if r["picked"]]
    if picked:
        groups["阶梯选了的（全部）"] = robust.summary(picked, end=end)
    for key, v in VARIANTS.items():
        for lab, keep in (("选了", lambda r: r["picked"]), ("没选", lambda r: not r["picked"])):
            xs = [(r["day"], r["if_done"]) for r in scored if r["variant"] == key and keep(r)]
            if xs:
                groups[f"{v['name']} · {lab}"] = robust.summary(xs, end=end)
    L += ["", robust.table(groups, "稳健性检查（hero/robust.py；「如果做了」的每 $1）")]
    base = statistics.mean(x for _, x in picked) if picked else None
    better = []
    for key, v in VARIANTS.items():
        xs = [r["if_done"] for r in scored if r["variant"] == key and not r["picked"]]
        if xs and base is not None and statistics.mean(xs) > base:
            s = robust.summary([(r["day"], r["if_done"]) for r in scored if r["variant"] == key and not r["picked"]], end=end)
            better.append(f"- {v['name']}：没选的 {len(xs)} 笔平均 {p(statistics.mean(xs))}，高于阶梯选了的 {p(base)}；"
                          f"中位数 {p(s['median'])}、去掉最好 3 笔 {p(s['trim'])}、赚钱 {s['win']:.0%}" + (f"；提醒：{'；'.join(s['flags'])}" if s["flags"] else ""))
    if better:
        L += ["## 没选却平均更高的", ""] + better + [""]
    return "\n".join(L) + "\n"


def backfill(root: Path = ROOT) -> tuple[list[dict], str]:
    from hero import research_ladder as rl
    docs = {name: latest(name, root) or {} for name in ("bottom-long", "bottom-2m", "bottom-semi", "bottom-spread", "bounce")}
    ev = rl.events({"spread": docs["bottom-spread"], "2m": docs["bottom-2m"], "long": docs["bottom-long"]})
    log = rl.ladder(ev, 500.0, None)["log"]
    rows = backfill_rows(docs, log)
    end = min((d.get("end") for d in docs.values() if d.get("end")), default=None)
    intro = (f"2024-02 至 {end}，五个研究文件里每一笔回测过的信号（{len(rows)} 行：一天一个名字一种买法一行），"
             "「如果做了」用研究里那笔的结果（买卖各多付 5%）；「选了没」按 $500 起步、按日期重放的阶梯账本那天的状态"
             "（hero/research_ladder.py，继续爬的口径）。阶梯只做三种：ETF 跌 8% 的价差（阶段一、三）、SMH 连跌第 3 天平值（阶段二、三）、"
             "ETF 跌 8% 的平值拿 63 天（阶段三）；别的都算「回测不够」。只研究，不改交易。")
    return rows, summarize(rows, f"机会日志回填：选了的和没选的 {date.today().isoformat()}", intro, end)


# ---------- the live scan ----------
def otm_contract(chain: list[dict], spot: float, day: date, off: float) -> dict | None:
    """The fast window's expiry (20-45 days), the first strike `off` or more above the price."""
    near = fast_contract(chain, spot, day)
    if not near:
        return None
    cs = sorted((c for c in chain if c["expiry"] == near["expiry"] and c["ask"] > 0), key=lambda c: c["strike"])
    return next((c for c in cs if c["strike"] >= spot * (1 + off) - 1e-9), None)


def contract_for(key: str, chain: list[dict], spot: float, day: date) -> dict | None:
    """The contract the variant's backtest would buy, with what one costs (ask x 100; a spread at its mid)."""
    if not chain:
        return None
    if key == "down3":
        c = fast_contract(chain, spot, day)
    elif key == "green1":
        c = otm_contract(chain, spot, day, 0.05)
    elif key in ("dd8-spread",):
        sp = depth_contracts(chain, spot, day).get("spread")
        return {"legs": [sp["long"]["symbol"], sp["short"]["symbol"]], "expiry": sp["long"]["expiry"], "strike": sp["long"]["strike"],
                "entry": sp["mid"], "cost": round(sp["mid"] * 100, 2), "natural": sp["natural"]} if sp and sp["mid"] > 0 else None
    elif key in ("dd8-atm", "dd20-atm"):
        c = depth_contracts(chain, spot, day).get("atm")
    elif key in ("dd8-otm5", "dd20-otm5"):
        c = depth_contracts(chain, spot, day).get("otm5")
    else:
        return None
    return {"legs": [c["symbol"]], "expiry": c["expiry"], "strike": c["strike"], "entry": c["ask"], "cost": round(c["ask"] * 100, 2),
            "bid": c["bid"]} if c and c["ask"] > 0 else None


def scan(day: date, closes: dict[str, list[float]], spots: dict[str, float], ten: dict[str, tuple[float, float]],
         chains: dict[str, list[dict]], state: dict | None, written: str = "") -> dict:
    """The day's rows (every satisfied signal x variant x name), how far the rest are from triggering, and the plan
    ladder's choice. `ten`: {symbol: (today's open, the 10:00 price)}; `state`: the plan ledger ({"cash", "open"})."""
    from hero.research_ladder import phase
    state = state or {"cash": 500.0, "open": []}
    cash = float(state.get("cash") or 0)
    held = {(t.get("line"), t.get("und")) for t in state.get("open") or []}
    eq = cash + sum(float(t.get("paid") or 0) for t in state.get("open") or [])
    ph = phase(eq)
    rows, near = [], []
    smh = bounce(closes.get("SMH") or [], *(ten.get("SMH") or (0.0, 0.0))) if ten.get("SMH") else None
    for key, v in VARIANTS.items():
        for und in v["unds"]:
            cs, spot = closes.get(und) or [], spots.get(und)
            if not spot or len(cs) < 21:
                continue
            sig, on, text = v["sig"], False, ""
            if sig == "down3":
                on, text = fast_signal(cs, spot), f"连跌第 3 天（{cs[-3]:,.2f} → {cs[-2]:,.2f} → {cs[-1]:,.2f} → 现价 {spot:,.2f}）"
                dist = None
            elif sig == "green1":
                on, text, dist = green_signal(cs, spot), f"连跌 {down_run(cs)} 天后今天收阳（{cs[-1]:,.2f} → {spot:,.2f}）", None
            elif sig in LEVELS:
                d = depth(cs, spot, LEVELS[sig])
                on, dist = d["first"], round(d["dd"] + LEVELS[sig], 4)
                text = f"离 20 日高点 {d['dd']:+.1%}（线 −{LEVELS[sig]:.0%}，高点 ${d['hi']:,.2f}）"
                if not on and d["armed"] and key in ("dd8-atm", "dd20-atm"):
                    near.append({"sig": sig, "und": und, "trigger": d["trigger"], "to_go": round(d["trigger"] / spot - 1, 4),
                                 "text": f"{und} 收在 ${d['trigger']:,.2f} 以下就是「{SIG_NAMES[sig]}」（还要跌 {1 - d['trigger'] / spot:.1%}）"})
            else:   # the sector bounce: SMH flushed, up 1%+ at 10:00; the name itself up 1%+ at 10:00
                o, p10 = ten.get(und) or (0.0, 0.0)
                u10 = p10 / o - 1 if o else None
                on = bool(smh and smh["on"] and u10 is not None and u10 >= 0.01)
                dist = round(smh["m10"] - 0.01, 4) if smh else None
                text = (f"SMH 前 1–3 天 {smh['drop']:+.1%}、10:00 离开盘 {smh['m10']:+.1%}；{und} 10:00 {u10:+.1%}" if smh and u10 is not None
                        else "没有 10:00 的价")
            if sig in ("down3", "green1"):
                dist = round(spot / cs[-1] - 1, 4)
            if not on:
                continue
            c = contract_for(key, chains.get(und) or [], spot, day)
            why, n = ladder_choice(v, und, ph, cash, eq, held, c["cost"] if c else None)
            if why == "选了":   # the ledger's cash goes to this one; later rows see what is left
                cash -= n * c["cost"]
                held.add((v["ladder"][0], und))
            rows.append({"day": day.isoformat(), "sig": sig, "variant": key, "und": und, "spot": round(spot, 2), "dist": dist,
                         "dist_text": text, "picked": why == "选了", "why": why, "phase": ph, "contract": c, "exit": v["exit"],
                         "hold": v["hold"], "if_done": None})
    cs, spot = closes.get("SMH") or [], spots.get("SMH")
    if cs and spot and not fast_signal(cs, spot) and down_run(cs) == 2:
        near.append({"sig": "down3", "und": "SMH", "text": f"SMH 已连跌 2 天，今天收在 ${cs[-1]:,.2f} 以下就是连跌第 3 天"})
    return {"day": day.isoformat(), "written": written, "time": "15:50 ET", "ladder": {"phase": ph, "cash": round(float(state.get("cash") or 0), 2),
            "equity": round(eq, 2), "source": state.get("source", "新账本 $500")}, "rows": rows, "near": near,
            "note": "模拟盘研究，不是投资建议；只记录，不下单。"}


# ---------- the scorer ----------
def sessions_between(a: str, b: str) -> int:
    """Sessions after day a up to and including day b."""
    d, end, n = date.fromisoformat(a), date.fromisoformat(b), 0
    while d < end:
        d += timedelta(days=1)
        n += is_session(d)
    return n


def load_book(path: Path = BOOK) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"days": [], "rows": []}


def book_add(book: dict, log: dict) -> int:
    """Book the day's rows that name a contract (once per day)."""
    if log["day"] in book["days"]:
        return 0
    book["days"].append(log["day"])
    n = 0
    for r in log["rows"]:
        c = r.get("contract")
        if c and c.get("entry"):
            book["rows"].append({**{k: r[k] for k in ("day", "sig", "variant", "und", "picked", "why", "phase", "exit", "hold")},
                                 "legs": c["legs"], "expiry": c["expiry"], "entry": c["entry"], "status": "open",
                                 "mark": None, "mark_day": None, "if_done": None})
            n += 1
    return n


def value(r: dict, quotes: dict[str, dict]) -> float | None:
    qs = [quotes.get(s) for s in r["legs"]]
    if any(q is None for q in qs):
        return None
    if len(qs) == 1:
        return qs[0]["bid"]
    mid = lambda q: (q["bid"] + q["ask"]) / 2
    return min(max(mid(qs[0]) - mid(qs[1]), 0.0), WIDTH)


def score(book: dict, day: str, quotes: dict[str, dict]) -> list[dict]:
    """Mark every open row logged before `day`; close the ones whose exit says so: 2x (take2, a single leg's bid), $4
    (take80, the spread), the variant's Nth session after the log day, or the expiry."""
    closed = []
    for r in book["rows"]:
        if r["status"] != "open" or r["day"] >= day:
            continue
        v = value(r, quotes)
        if v is None:
            continue
        r.update(mark=round(v, 4), mark_day=day, if_done=round(v / r["entry"] - 1, 4))
        n = sessions_between(r["day"], day)
        out = ((r["exit"] == "take2" and v >= 2 * r["entry"]) or (r["exit"] == "take80" and v >= SPREAD_SELL)
               or (r.get("hold") is not None and n >= r["hold"]) or day >= r["expiry"])
        if out:
            r["status"] = "closed"
            closed.append(r)
    return closed


def weekly(book: dict, day: str) -> str:
    rows = [r for r in book["rows"] if r["status"] == "closed"]
    week = [r for r in book["rows"] if sessions_between(r["day"], day) < 5 and r["day"] <= day]
    why = Counter(r["why"] for r in week)
    intro = (f"截至 {day}：已出场 {len(rows)} 行（按 CBOE 延迟报价：单腿按卖价买、按买价卖，价差按中间价），还拿着 "
             f"{sum(r['status'] == 'open' for r in book['rows'])} 行；这一周新记 {len(week)} 行（"
             + ("、".join(f"{k} {v}" for k, v in why.most_common()) or "没有") + "）。历史回填见 research/opportunities/backfill.md。")
    return summarize(rows, f"机会日志周报 {day}", intro, day)


# ---------- data ----------
def fetch_ten(sym: str, day: date) -> tuple[float, float] | None:
    """(today's open, the 10:00 price) from Yahoo's 5-minute bars (only the last weeks are served)."""
    import requests
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=5d&interval=5m", headers={"User-Agent": UA}, timeout=30)
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    bars = [(datetime.fromtimestamp(t, timezone.utc).astimezone(ET), o, c) for t, o, c in zip(res["timestamp"], q["open"], q["close"])]
    bars = [b for b in bars if b[0].date() == day and b[1] and b[2]]
    first = next((b for b in bars if b[0].strftime("%H:%M") == "09:30"), None)
    ten = [b for b in bars if "09:30" <= b[0].strftime("%H:%M") < "10:00"]
    return (float(first[1]), float(ten[-1][2])) if first and ten else None


def plan_state(root: Path = ROOT) -> dict | None:
    """The plan ladder's ledger (journal-s500/sleeves_plan.json `ladder-plan-500`), when it runs."""
    p = root / "journal-s500" / "sleeves_plan.json"
    try:
        s = json.loads(p.read_text(encoding="utf-8"))["sleeves"]["ladder-plan-500"]
    except (FileNotFoundError, KeyError, ValueError):
        return None
    return {"cash": s.get("cash"), "open": [{"line": t.get("line"), "und": t.get("und"), "paid": t.get("paid")} for t in s.get("open", [])],
            "source": "演算账本 ladder-plan-500"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("what", choices=("scan", "score", "backfill"))
    ap.add_argument("--date", help="YYYY-MM-DD (default: today, US Eastern)")
    ap.add_argument("--out", help="write under this folder instead of the repo")
    ap.add_argument("--weekly", action="store_true", help="write the weekly summary whatever the day")
    a = ap.parse_args(argv)
    now = datetime.now(ET)
    day = date.fromisoformat(a.date) if a.date else now.date()
    base = (Path(a.out) if a.out else ROOT) / "research" / "opportunities"
    base.mkdir(parents=True, exist_ok=True)
    if a.what == "backfill":
        rows, md = backfill()
        (base / "backfill.json").write_text(json.dumps({"rows": rows}, ensure_ascii=False) + "\n", encoding="utf-8")
        (base / "backfill.md").write_text(md, encoding="utf-8")
        print(md)
        return 0
    if a.what == "scan":
        closes, spots, ten, chains = {}, {}, {}, {}
        for und in ETFS + STOCKS:
            closes[und], spots[und] = fetch_daily(und, day)
        for und in ("SMH",) + BOUNCE_NAMES:
            try:
                got = fetch_ten(und, day)
            except Exception:
                got = None
            if got:
                ten[und] = got
        log = scan(day, closes, spots, ten, {}, None)
        if day == now.date():   # quotes are today's only: a past date logs the signals without contracts
            for und in sorted({r["und"] for r in log["rows"]}):
                chains[und] = fetch_chain(und)
            log = scan(day, closes, spots, ten, chains, plan_state(), now.isoformat(timespec="minutes"))
        (base / f"{day.isoformat()}.json").write_text(json.dumps(log, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(json.dumps(log, ensure_ascii=False, indent=1))
        return 0
    book_path = base / "book.json"
    book = load_book(book_path)
    log_path = base / f"{day.isoformat()}.json"
    if log_path.exists():
        book_add(book, json.loads(log_path.read_text(encoding="utf-8")))
    held = sorted({r["und"] for r in book["rows"] if r["status"] == "open" and r["day"] < day.isoformat()})
    quotes = {c["symbol"]: c for u in held for c in fetch_chain(u)}
    closed = score(book, day.isoformat(), quotes)
    book_path.write_text(json.dumps(book, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{day}: {len(closed)} 行出场，{sum(r['status'] == 'open' for r in book['rows'])} 行还拿着")
    if a.weekly or day.weekday() == 4:
        (base / f"weekly-{day.isoformat()}.md").write_text(weekly(book, day.isoformat()), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
