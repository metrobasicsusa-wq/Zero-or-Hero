"""The year-end bottom plan, backtested (owner, 11-03: 「回测下」after the plan was argued through).

The plan as revised: buy the chip ETF's dip only while the broad market is healthy (SPY within 5% of its 20-day
high: the "740 gate"); the first batch at the signal, a second batch only if the ETF makes a new low while SPY still
passes the gate, never a third; SMH as the base (a call, or a $5-wide call spread for small money), MU as the
satellite; no price stop on the option, sell on a 40% giveback after doubling, otherwise at the 42nd session.

Price layer (Yahoo, SMH / SPY / MU / AMAT since 2000): the signal is the first close 10% / 15% / 20% under the
20-session high; each event is tagged healthy or sick by SPY's own distance from its 20-session high that day, and
followed 42 / 63 sessions, with the second-batch rule replayed. Option layer (Alpaca, 2024-02 on, Actions only):
the real SMH calls and $5 spreads and MU calls on those days, through the exits of the earlier bottom studies.
Record only: nothing here changes a rule.
"""
from __future__ import annotations

import os
import statistics
import sys
import time
from datetime import date, timedelta

from hero import robust
from hero.research_bottom_2m import EXITS as CALL_EXITS, legs as call_legs
from hero.research_bottom_spread import EXITS as SPREAD_EXITS, legs as spread_legs
from hero.research_event_turn import Pace
from hero.research_ideas import report, save
from hero.research_yearend import close_on_or_before, load

ETF, MARKET = "SMH", "SPY"
SATELLITES = ("MU", "AMAT")
LEVELS = ((0.10, "dd10"), (0.15, "dd15"), (0.20, "dd20"))
LABEL = {"dd10": "首次跌到 20 日高点下 10%", "dd15": "首次跌到 20 日高点下 15%", "dd20": "首次跌到 20 日高点下 20%", "every20": "对照：每第 20 天随便买"}
GATE = -0.05            # SPY within 5% of its 20-session high = healthy
HOLD = 42
OPT_START = "2024-02-01"
BUDGET_MIN = float(os.environ.get("RESEARCH_BUDGET_MIN", "60"))


# ---- price layer -----------------------------------------------------------------------------------------------

def dd20(closes: list[float], i: int) -> float:
    return closes[i] / max(closes[max(0, i - 20):i + 1]) - 1


def events(days: list[str], etf: list[float], mkt: dict[str, float], sats: dict[str, dict[str, float]]) -> list[dict]:
    """Every first crossing of each depth line, tagged by SPY's health that day, with the ETF's path, the second
    batch's day (a new low under the entry close while SPY still passes the gate, within HOLD sessions), and the
    satellites' returns over the same windows."""
    out = []
    for i in range(21, len(days)):
        d, dd, ddp = days[i], dd20(etf, i), dd20(etf, i - 1)
        tags = [tag for lvl, tag in LEVELS if dd <= -lvl < ddp]
        if i % 20 == 0:
            tags.append("every20")
        if not tags:
            continue
        m = mkt.get(d)
        if m is None:
            continue
        mi = sorted(k for k in mkt if k <= d)[-21:]
        spy_dd = m / max(mkt[k] for k in mi) - 1
        e = {"day": d, "i": i, "tags": tags, "dd20": round(dd, 4), "spy_dd20": round(spy_dd, 4), "healthy": spy_dd >= GATE}
        for k in (21, 42, 63):
            if i + k < len(days):
                e[f"f{k}"] = round(etf[i + k] / etf[i] - 1, 4)
        nxt = etf[i + 1:i + HOLD + 1]
        e["mae42"] = round(min(nxt) / etf[i] - 1, 4) if nxt else None
        # the second batch: the first new low under the entry close while SPY still passes the gate
        e["batch2"] = None
        for j in range(i + 1, min(i + HOLD, len(days) - 1)):
            if etf[j] < etf[i]:
                mj = mkt.get(days[j])
                if mj is None:
                    continue
                mji = sorted(k for k in mkt if k <= days[j])[-21:]
                if mj / max(mkt[k] for k in mji) - 1 >= GATE:
                    e["batch2"] = {"day": days[j], "sessions": j - i, "px": round(etf[j] / etf[i] - 1, 4)}
                    for k in (42, 63):
                        if i + k < len(days):
                            e["batch2"][f"f{k}"] = round(etf[i + k] / etf[j] - 1, 4)
                break
        for k in (42, 63):
            if f"f{k}" in e:
                b2 = e["batch2"]
                e[f"two_{k}"] = round((e[f"f{k}"] + (b2[f"f{k}"] if b2 and f"f{k}" in b2 else 0.0)) / 2, 4)
        for s, closes in sats.items():
            if d in closes:
                c0 = closes[d]
                later = sorted(k for k in closes if k > d)
                for k in (42, 63):
                    if len(later) >= k:
                        e[f"{s}_f{k}"] = round(closes[later[k - 1]] / c0 - 1, 4)
        out.append(e)
    return out


def cell(rs: list[dict], key: str) -> dict | None:
    v = [r[key] for r in rs if r.get(key) is not None]
    if not v:
        return None
    return {"n": len(v), "mean": statistics.mean(v), "median": statistics.median(v), "win": sum(x > 0 for x in v) / len(v),
            "worst": min(v), "best": max(v)}


def price_tables(evs: list[dict]) -> dict:
    out = {}
    for tag in [t for _, t in LEVELS] + ["every20"]:
        for health, keep in (("healthy", lambda e: e["healthy"]), ("sick", lambda e: not e["healthy"]), ("all", lambda e: True)):
            rs = [e for e in evs if tag in e["tags"] and keep(e)]
            if not rs:
                continue
            out[f"{tag}|{health}"] = {
                "n": len(rs), "years": sorted({e["day"][:4] for e in rs}),
                **{k: cell(rs, k) for k in ("f21", "f42", "f63", "mae42", "two_42", "two_63", "MU_f42", "MU_f63", "AMAT_f42", "AMAT_f63")},
                "batch2_rate": sum(1 for e in rs if e["batch2"]) / len(rs),
                "batch2_px": statistics.mean(e["batch2"]["px"] for e in rs if e["batch2"]) if any(e["batch2"] for e in rs) else None,
                "batch2_sessions": statistics.median(e["batch2"]["sessions"] for e in rs if e["batch2"]) if any(e["batch2"] for e in rs) else None,
                "robust": robust.summary([(e["day"], e["f42"]) for e in rs if e.get("f42") is not None], extra_cost=0.001),
            }
    return out


ERAS = (("2000–2009（网络泡沫、2008）", "2000", "2010"), ("2010 起", "2010", "2100"))


def era_tables(evs: list[dict]) -> dict:
    """The same cells by era: the dot-com and 2008 decade against the years since 2010."""
    out = {}
    for name, lo, hi in ERAS:
        sub = [e for e in evs if lo <= e["day"][:4] < hi]
        for tag in [t for _, t in LEVELS] + ["every20"]:
            for health, keep in (("healthy", lambda e: e["healthy"]), ("sick", lambda e: not e["healthy"]), ("all", lambda e: True)):
                rs = [e for e in sub if tag in e["tags"] and keep(e)]
                if rs:
                    out[f"{name}|{tag}|{health}"] = {"n": len(rs), **{k: cell(rs, k) for k in ("f42", "f63", "mae42", "MU_f42")}}
    return out


def run_price(bars: dict[str, list[dict]]) -> dict:
    etf = bars[ETF]
    days, closes = [b["d"] for b in etf], [b["c"] for b in etf]
    mkt = {b["d"]: b["c"] for b in bars[MARKET]}
    sats = {s: {b["d"]: b["c"] for b in bars[s]} for s in SATELLITES if s in bars}
    evs = events(days, closes, mkt, sats)
    return {"events": evs, "tables": price_tables(evs), "eras": era_tables(evs), "px_from": days[21], "px_to": days[-1]}


# ---- option layer (Actions) --------------------------------------------------------------------------------------

def run_options(client, evs: list[dict], budget_min: float = BUDGET_MIN, log=lambda *a: None) -> dict:
    """SMH calls (ATM / 5% out) and $5 spreads, MU ATM calls, on every depth event and its second-batch day since
    OPT_START, through the exits of research_bottom_2m / research_bottom_spread."""
    t0 = time.monotonic()
    end = (date.today() - timedelta(days=1)).isoformat()
    pace = Pace(client)
    raw = client.daily_bars([ETF, "MU"], OPT_START, adjustment="raw", feed="sip")
    stock = {s: {b["t"][:10]: float(b["c"]) for b in raw.get(s, [])} for s in (ETF, "MU")}
    sessions = {s: sorted(stock[s]) for s in stock}
    rows, misses, cache = [], {}, {}
    todo = []
    for e in evs:
        if e["day"] < OPT_START or not any(t in e["tags"] for t in ("dd10", "dd15", "dd20")):
            continue
        todo.append((e, "batch1", e["day"]))
        if e["batch2"]:
            todo.append((e, "batch2", e["batch2"]["day"]))
    partial = False
    for e, batch, day in todo:
        if (time.monotonic() - t0) / 60 > budget_min:
            partial = True
            break
        base = {"day": e["day"], "tags": e["tags"], "healthy": e["healthy"], "batch": batch, "entry_day": day}
        for und in (ETF, "MU"):
            if day not in stock[und]:
                continue
            after = [d for d in sessions[und] if d > day]
            got = call_legs(client, und, day, stock[und][day], end, after, stock[und], pace, misses, cache)
            for name, leg in got.items():
                if name in ("atm", "otm5"):
                    rows.append({**base, "und": und, "kind": "call", "leg": name, **{k: v for k, v in leg.items() if k != "path"}})
            if und == ETF:
                got = spread_legs(client, und, day, stock[und][day], end, after, stock[und], pace, misses, cache)
                for name, leg in got.items():
                    rows.append({**base, "und": und, "kind": "spread", "leg": name, **{k: v for k, v in leg.items() if k != "path"}})
        log(f"  {day} {batch} rows={len(rows)}")
    return {"rows": rows, "misses": misses, "partial": partial, "minutes": round((time.monotonic() - t0) / 60, 1), "end": end}


def option_groups(rows: list[dict], kind: str) -> dict[str, list[dict]]:
    gs: dict[str, list[dict]] = {}
    for r in rows:
        if r["kind"] != kind:
            continue
        for tag in r["tags"]:
            if tag.startswith("dd"):
                gs.setdefault(f"{LABEL[tag]} · {r['und']} · {r['leg']} · {'大盘健康' if r['healthy'] else '大盘病着'} · {r['batch']}", []).append(r)
    return dict(sorted(gs.items()))


# ---- report ----------------------------------------------------------------------------------------------------

def _p(x, nd=1) -> str:
    return "—" if x is None else f"{x * 100:+.{nd}f}%"


def _c(c: dict | None) -> str:
    return "—" if not c else f"{_p(c['mean'])} / {_p(c['median'])} / {c['win']:.0%}"


def markdown(rep: dict) -> str:
    pt = rep["price"]["tables"]
    o = [f"# 年底抄底方案的回测：芯片 ETF 跌深、大盘健康时买，最多两批 {date.today().isoformat()}", "",
         f"价格层 Yahoo 日线 {rep['price']['px_from']} 至 {rep['price']['px_to']}：SMH 首次收在 20 日高点下 10% / 15% / 20%，当天 SPY 离自己 20 日高点"
         f"不到 5% 算「大盘健康」（今天的 740 线），否则「大盘病着」。第二批：之后 {HOLD} 个交易日内 SMH 收盘创新低、而 SPY 仍健康的第一天。"
         "每格「平均 / 中位数 / 赚的比例」。MU、AMAT 是同一天买单股的结果。只研究，不改交易。", "",
         "## 价格层：SMH 本身", "",
         "| 信号 | 大盘 | 次数 | 21 天 | 42 天 | 63 天 | 42 天内最深 | 第二批触发 | 触发时比第一批低 | 两批 42 天 | 两批 63 天 | MU 42 天 | AMAT 42 天 |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for key, t in pt.items():
        tag, health = key.split("|")
        o.append(f"| {LABEL[tag]} | {'健康' if health == 'healthy' else '病着' if health == 'sick' else '全部'} | {t['n']} | {_c(t['f21'])} | {_c(t['f42'])} | {_c(t['f63'])} | "
                 f"{_p(t['mae42']['mean']) if t['mae42'] else '—'}（中位 {_p(t['mae42']['median']) if t['mae42'] else '—'}） | {t['batch2_rate']:.0%} | {_p(t['batch2_px'])} | "
                 f"{_c(t['two_42'])} | {_c(t['two_63'])} | {_c(t['MU_f42'])} | {_c(t['AMAT_f42'])} |")
    o += ["", "### 按年代：同一张表拆成 2000–2009 和 2010 起", "",
          "| 年代 | 信号 | 大盘 | 次数 | 42 天 | 63 天 | 42 天内最深 | MU 42 天 |", "|---|---|---|---|---|---|---|---|"]
    for key, t in rep["price"]["eras"].items():
        era, tag, health = key.split("|")
        o.append(f"| {era} | {LABEL[tag]} | {'健康' if health == 'healthy' else '病着' if health == 'sick' else '全部'} | {t['n']} | {_c(t['f42'])} | {_c(t['f63'])} | "
                 f"{_p(t['mae42']['mean']) if t['mae42'] else '—'} | {_c(t['MU_f42'])} |")
    o += ["", "### 大盘健康时的每一次（15% 线）", "", "| 日期 | SMH 离高 | SPY 离高 | 21 天 | 42 天 | 63 天 | 最深 | 第二批 | MU 42 天 |", "|---|---|---|---|---|---|---|---|---|"]
    for e in rep["price"]["events"]:
        if "dd15" in e["tags"] and e["healthy"]:
            b2 = e["batch2"]
            o.append(f"| {e['day']} | {_p(e['dd20'])} | {_p(e['spy_dd20'])} | {_p(e.get('f21'))} | {_p(e.get('f42'))} | {_p(e.get('f63'))} | {_p(e.get('mae42'))} | "
                     f"{(b2['day'] + ' ' + _p(b2['px'])) if b2 else '没触发'} | {_p(e.get('MU_f42'))} |")
    o += ["", robust.table({f"{LABEL[t]} · {'健康' if h == 'healthy' else '病着'}": pt[f'{t}|{h}']['robust'] for t in ('dd15', 'dd20') for h in ('healthy', 'sick') if f'{t}|{h}' in pt},
                           "稳健性检查（SMH 42 天，每次信号当一笔）"), ""]
    op = rep.get("options")
    if op and op.get("rows"):
        _, md_calls = report("期权层：看涨", f"{OPT_START} 起，75–130 天的月度看涨，买卖各多付 5%；{op['minutes']} 分钟{'，预算到了没跑完' if op['partial'] else ''}；没拿到数据的原因：{op['misses'] or '无'}。",
                             option_groups(op["rows"], "call"), CALL_EXITS, op["end"])
        _, md_spreads = report("期权层：$5 价差", "同一天 SMH 平值买、高 $5 卖。", option_groups(op["rows"], "spread"), SPREAD_EXITS, op["end"])
        o += ["## " + md_calls.replace("# ", "", 1), "", "## " + md_spreads.replace("# ", "", 1), ""]
    elif op is not None:
        o += ["## 期权层", "", f"没有期权数据（{op.get('misses') or '没有信号日在期权数据范围内'}）。", ""]
    else:
        o += ["## 期权层", "", "本机没有券商钥匙，期权层要在 research.yml（study=yearend_plan）里跑。", ""]
    return "\n".join(o)


def main() -> None:
    log = lambda *a: print(*a, file=sys.stderr, flush=True)
    syms = [ETF, MARKET, *SATELLITES]
    bars = load(syms, log)
    rep = {"price": run_price(bars)}
    try:
        from hero.alpaca import Alpaca
        client = Alpaca()
        rep["options"] = run_options(client, rep["price"]["events"], log=log)
    except Exception as e:   # noqa: BLE001 -- no keys locally: the price layer alone
        log(f"options layer skipped: {type(e).__name__}: {e}")
        rep["options"] = None
    md = markdown(rep)
    rep["price"]["events"] = [{k: v for k, v in e.items() if k != "i"} for e in rep["price"]["events"]]
    save("yearend-plan", rep, md)


if __name__ == "__main__":
    main()
