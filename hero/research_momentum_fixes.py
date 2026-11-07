"""Three fixes for the stock momentum sleeve, backtested before any of them goes live (owner, 2026-11-02).

October's diagnosis (notes/lessons.md, 11-02): the live sleeve rebalances at 09:30–09:50 on a partly formed
daily bar and fills near the open, while hero/backtest.py (what the weekly evolve scores) decides and fills on
the close; 33 round trips in a month and most of the losers were held a week or less; AI names dominated the
book. So, on one set of Yahoo daily OHLC (split-adjusted) for the main account's universe:

1. Execution timing, same rules: decide and fill on the close (the backtest); decide on the close, fill the
   next close (one day late); decide on yesterday's closes, fill at the open; decide on yesterday's closes
   plus today's open as a half-formed bar, fill at the open (what runs live).
2. Exits for names already held: exit_buffer (stay while within x% under the trend line), exit_confirm
   (drop only after N closes under it), rank_buffer (keep the slot while ranked within top_n + k).
3. group_cap: at most N picks from one momentum.GROUPS bucket.

Scored like hero/evolve.py: 210 sessions of warm-up, the first 70% train, the rest validate, score = the worse
Sharpe of the two; on the full period and on the 3-year window evolve actually sees. Also total return,
drawdown, turnover per year, the live period since 2026-10-01, and hero/robust.py on every round trip. The
simulation keeps the engine's rebalance_drift (a holding within 25% of its target is left alone), pays
backtest.COST per unit of turnover, and ignores protective stops and the daily-loss halt (as evolve does).
Only research: nothing here changes config/strategy.json.
Run: python -m hero.research_momentum_fixes  (bars cached in research/evolve-cache/momentum-bars.json.gz)
"""

from __future__ import annotations

import gzip
import itertools
import json
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from hero import robust
from hero.backtest import COST
from hero.evolve import MIN_IMPROVEMENT, TRAIN_FRACTION, WARMUP
from hero.indicators import max_drawdown, sharpe
from hero.research_earnings_control import yahoo
from hero.research_ideas import save
from hero.strategies import momentum

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "research" / "evolve-cache" / "momentum-bars.json.gz"
HISTORY_START = "2019-01-01"
EVOLVE_YEARS = 3 * 365          # hero/__main__.py EVOLVE_HISTORY_DAYS
LIVE_FROM = "2026-10-01"
TIMING = {
    "close": "收盘决定、收盘成交（回测口径）",
    "lag": "收盘决定、第二天收盘成交（晚一天）",
    "open": "昨收决定、开盘成交",
    "open_partial": "昨收 + 今天开盘半根 K 线决定、开盘成交（实盘现状）",
}
EXIT_GRID = {"exit_buffer": [0, 0.02, 0.03, 0.05], "exit_confirm": [1, 2, 3], "rank_buffer": [0, 2, 4]}
CAP_GRID = [0, 2, 3, 4]


# ---------------------------------------------------------------- data

def load(syms: list[str], calendar: str, cache: Path | None = CACHE) -> tuple[list[str], dict, dict]:
    """(days, opens, closes) on the calendar symbol's sessions, forward-filled; from the cache when it has
    every symbol through yesterday or later."""
    raw = {}
    if cache and cache.exists():
        raw = json.loads(gzip.decompress(cache.read_bytes()))
        fresh = (date.today() - timedelta(days=1)).isoformat()
        if any(s not in raw or max(raw[s]) < fresh for s in syms + [calendar]):
            raw = {}
    if not raw:
        for s in sorted(set(syms) | {calendar}):
            raw[s] = yahoo(s, HISTORY_START)
            time.sleep(0.3)
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(gzip.compress(json.dumps(raw).encode()))
    days = sorted(raw[calendar])
    opens, closes = {}, {}
    for s in syms:
        o, c, lo, lc = [], [], None, None
        for d in days:
            b = raw[s].get(d)
            if b:
                lo, lc = float(b["o"]), float(b["c"])
            o.append(lo)
            c.append(lc)
        if o[0] is not None:
            opens[s], closes[s] = o, c
    return days, opens, closes


# ---------------------------------------------------------------- simulation

def simulate(days: list[str], opens: dict, closes: dict, p: dict, cfg: dict, start: int, end: int,
             mode: str = "close") -> dict:
    """Daily returns of the momentum sleeve under one execution timing (TIMING) and one parameter set.
    Returns rets (one per session), their fill-day labels, the equity path, turnover, and every round trip
    as (exit day, return per $1) for hero/robust.py."""
    regime, max_pos, drift = cfg["regime_symbol"], cfg["risk"]["max_position_pct"], p["rebalance_drift"]
    hold: dict[str, float] = {}
    entries: dict[str, tuple[str, float]] = {}
    rets, labels, turns, trades, equity = [], [], [], [], [1.0]
    for t in range(start, end - 2):
        if mode == "close":
            hist = {s: xs[: t + 1] for s, xs in closes.items()}
            fill, nxt, day = {s: closes[s][t] for s in closes}, {s: closes[s][t + 1] for s in closes}, days[t]
        elif mode == "lag":
            hist = {s: xs[:t] for s, xs in closes.items()}
            fill, nxt, day = {s: closes[s][t] for s in closes}, {s: closes[s][t + 1] for s in closes}, days[t]
        elif mode == "open":
            hist = {s: xs[: t + 1] for s, xs in closes.items()}
            fill, nxt, day = {s: opens[s][t + 1] for s in closes}, {s: opens[s][t + 2] for s in closes}, days[t + 1]
        elif mode == "open_partial":
            hist = {s: xs[: t + 1] + [opens[s][t + 1]] for s, xs in closes.items()}
            fill, nxt, day = {s: opens[s][t + 1] for s in closes}, {s: opens[s][t + 2] for s in closes}, days[t + 1]
        else:
            raise ValueError(mode)
        held = frozenset(hold)
        want = momentum.target_weights(hist, p, regime, max_pos, held=held)
        new = {}
        for s, w in want.items():
            have = hold.get(s, 0.0)
            new[s] = have if have and abs(have - w) / w <= drift else w   # the engine's drift tolerance
        turnover = sum(abs(new.get(s, 0.0) - hold.get(s, 0.0)) for s in set(new) | set(hold))
        for s in hold:
            if s not in new:
                trades.append((day, fill[s] / entries.pop(s)[1] - 1))
        for s in new:
            if s not in hold:
                entries[s] = (day, fill[s])
        gross = sum(w * nxt[s] / fill[s] for s, w in new.items())
        eq = gross + (1 - sum(new.values())) - COST * turnover
        rets.append(eq - 1)
        labels.append(day)
        turns.append(turnover)
        equity.append(equity[-1] * eq)
        hold = {s: w * nxt[s] / fill[s] / eq for s, w in new.items()}
    for s, (d0, px) in entries.items():   # still open at the end: marked at the last fill price
        trades.append((labels[-1], nxt[s] / px - 1))
    return {"rets": rets, "days": labels, "equity": equity, "turnover": turns, "trades": trades, "mode": mode}


def metrics(res: dict, since: str | None = None) -> dict:
    """Evolve-style score plus the plain numbers, over the sessions labelled `since` or later."""
    i0 = next((i for i, d in enumerate(res["days"]) if d >= since), len(res["days"])) if since else 0
    rets, days, turns = res["rets"][i0:], res["days"][i0:], res["turnover"][i0:]
    n = len(rets)
    if n < 10:
        return {"n": n}
    split = int(n * TRAIN_FRACTION)
    eq = [1.0]
    for r in rets:
        eq.append(eq[-1] * (1 + r))
    years: dict[str, float] = {}
    for d, r in zip(days, rets):
        years[d[:4]] = years.get(d[:4], 1.0) * (1 + r)
    live = [r for d, r in zip(days, rets) if d >= LIVE_FROM]
    live_ret = None
    if live:
        live_ret = 1.0
        for r in live:
            live_ret *= 1 + r
        live_ret -= 1
    return {"n": n, "from": days[0], "to": days[-1], "val_from": days[split],
            "train": round(sharpe(rets[:split]), 3), "val": round(sharpe(rets[split:]), 3),
            "score": round(min(sharpe(rets[:split]), sharpe(rets[split:])), 3),
            "sharpe": round(sharpe(rets), 3), "total": round(eq[-1] - 1, 4),
            "cagr": round(eq[-1] ** (252 / n) - 1, 4), "max_dd": round(max_drawdown(eq), 4),
            "turnover_year": round(sum(turns) / n * 252, 2),
            "trades": sum(1 for d, _ in res["trades"] if d >= days[0]),
            "years": {y: round(v - 1, 4) for y, v in years.items()},
            "live": None if live_ret is None else round(live_ret, 4), "live_days": len(live)}


def window_start(days: list[str], today: date | None = None) -> int:
    """Where evolve's 3-year window starts trading: WARMUP sessions after today minus 3 years."""
    since = ((today or date.today()) - timedelta(days=EVOLVE_YEARS)).isoformat()
    i0 = next((i for i, d in enumerate(days) if d >= since), len(days))
    return i0 + WARMUP


# ---------------------------------------------------------------- the study

def run(cfg: dict, days: list[str], opens: dict, closes: dict, today: date | None = None,
        log=lambda *a: None) -> dict:
    base = cfg["stocks"]
    n = len(days)
    w3 = days[min(window_start(days, today), n - 1)]
    sims: dict[str, dict] = {}

    def study(name: str, p: dict, mode: str) -> dict:
        res = simulate(days, opens, closes, p, cfg, WARMUP, n, mode)
        sims[name] = res
        row = {"name": name, "mode": mode, "params": {k: p[k] for k in list(EXIT_GRID) + ["group_cap"] if k in p},
               "full": metrics(res), "evolve": metrics(res, w3), "robust": robust.summary(res["trades"], end=days[-1])}
        log(name, row["full"].get("score"), row["evolve"].get("score"))
        return row

    timing = [study(f"timing:{m}", base, m) for m in TIMING]
    exits = {}
    for mode in ("close", "open_partial"):
        rows = []
        for combo in itertools.product(*EXIT_GRID.values()):
            p = {**base, **dict(zip(EXIT_GRID, combo))}
            rows.append(study(f"exit:{mode}:" + "/".join(str(v) for v in combo), p, mode))
        exits[mode] = rows
    caps = {mode: [study(f"cap:{mode}:{c}", {**base, "group_cap": c}, mode) for c in CAP_GRID]
            for mode in ("close", "open_partial")}

    def best_of(rows, key="evolve"):
        qual = [r for r in rows if r[key].get("score") is not None]
        return max(qual, key=lambda r: r[key]["score"]) if qual else None

    # the three best single fixes stacked, on the live timing
    live_rows = exits["open_partial"]
    off = {"exit_buffer": 0, "exit_confirm": 1, "rank_buffer": 0}   # the live rules; params may also carry group_cap since 11-07
    cur = next(r for r in live_rows if all(r["params"].get(k) == v for k, v in off.items()))
    best_exit = best_of(live_rows)
    best_cap = best_of(caps["open_partial"])
    stacked = study("stack:open_partial", {**base, **best_exit["params"], **best_cap["params"]}, "open_partial")
    stacked_close = study("stack:close", {**base, **best_exit["params"], **best_cap["params"]}, "close")

    def knob_view(rows):
        out = {}
        for k, vals in EXIT_GRID.items():
            out[k] = {str(v): {"evolve": round(statistics.mean(r["evolve"]["score"] for r in rows if r["params"][k] == v), 3),
                               "full": round(statistics.mean(r["full"]["score"] for r in rows if r["params"][k] == v), 3),
                               "turnover": round(statistics.mean(r["evolve"]["turnover_year"] for r in rows if r["params"][k] == v), 1)}
                      for v in vals}
        return out

    return {"generated": (today or date.today()).isoformat(), "days": [days[WARMUP], days[-1]],
            "sessions": n - WARMUP - 2, "evolve_window_from": w3, "universe": sorted(closes),
            "current_params": {k: base.get(k) for k in ("momentum_lookback", "trend_sma", "top_n", "rsi_max",
                                                         "gross_exposure", "rebalance_drift", "rsi_applies_to_holdings")},
            "timing": timing, "exits": exits, "caps": caps, "knobs": {m: knob_view(exits[m]) for m in exits},
            "current": cur, "best_exit": best_exit, "best_cap": best_cap, "stacked": stacked,
            "stacked_close": stacked_close,
            "would_adopt": {
                "exit": best_exit["evolve"]["score"] >= cur["evolve"]["score"] + MIN_IMPROVEMENT,
                "cap": best_cap["evolve"]["score"] >= cur["evolve"]["score"] + MIN_IMPROVEMENT,
                "stacked": stacked["evolve"]["score"] >= cur["evolve"]["score"] + MIN_IMPROVEMENT},
            "min_improvement": MIN_IMPROVEMENT}


# ---------------------------------------------------------------- report

def _p(x, nd=0) -> str:
    return "—" if x is None else f"{x:+.{nd}%}"


def _row(label: str, r: dict) -> str:
    f, e = r["full"], r["evolve"]
    return (f"| {label} | {e['score']:.2f} | {e['train']:.2f} / {e['val']:.2f} | {_p(e['total'])} | {_p(e['max_dd'])} | "
            f"{e['turnover_year']:.1f}x | {e['trades']} | {f['score']:.2f} | {_p(f['cagr'], 1)} | {_p(f['max_dd'])} | "
            f"{_p(e['live'], 1)} |")


HEAD = ("| 情况 | 3 年分数 | 训练 / 验证夏普 | 3 年总收益 | 3 年最大回撤 | 年换手 | 来回笔数 | 全期分数 | 全期年化 | 全期回撤 | 10-01 起 |",
        "|---|---|---|---|---|---|---|---|---|---|---|")


def _exit_label(pr: dict) -> str:
    return (f"缓冲 {pr['exit_buffer']:.0%}、确认 {pr['exit_confirm']} 天、排名余地 {pr['rank_buffer']}"
            + (f"、同组上限 {pr['group_cap']}" if pr.get("group_cap") else ""))


def markdown(rep: dict) -> str:
    cur = rep["current"]
    cp = rep["current_params"]
    md = [f"# 主账户正股三项修正回测 {rep['generated']}", "",
          f"数据：Yahoo 日线（拆股调整）{rep['days'][0]} 到 {rep['days'][1]}，{rep['sessions']} 个交易日，{len(rep['universe'])} 只股票池；"
          f"规则就是现在的配置（{cp['momentum_lookback']} 日动量、{cp['trend_sma']} 日均线、前 {cp['top_n']} 名、RSI 上限 {cp['rsi_max']}、"
          f"总仓位 {cp['gross_exposure']:.0%}、偏离 {cp['rebalance_drift']:.0%} 内不动）。",
          f"打分和每周进化一样：前 70% 训练、后 30% 验证，取两段夏普较差的那个；「3 年」是进化实际看到的窗口（{rep['evolve_window_from']} 起），"
          f"「全期」从 {rep['days'][0]} 起；换一组参数要比现在高 {rep['min_improvement']} 以上。模拟保留了实盘的「偏离 25% 内不动」，每单位换手付 {COST:.2%}，"
          "不模拟保护性止损和日内熔断（进化也不模拟）。只研究，不改配置。", "",
          "## 一、成交时点：同一套规则，四种执行", "",
          "实盘 09:30–09:50 换仓：信号里多了今天开盘的半根 K 线，成交在开盘附近；回测按收盘决定、收盘成交。", "", *HEAD]
    for r in rep["timing"]:
        md.append(_row(TIMING[r["mode"]], r))
    t = {r["mode"]: r for r in rep["timing"]}
    gap = t["close"]["evolve"]["score"] - t["open_partial"]["evolve"]["score"]
    md += ["", f"回测口径比实盘口径 3 年分数高 {gap:+.2f}，10-01 起 {_p(t['close']['evolve']['live'], 1)} 对 {_p(t['open_partial']['evolve']['live'], 1)}"
           f"（实盘正股腿 10 月约 −2.2%）。「晚一天」那行看信号衰减：{t['lag']['evolve']['score']:.2f}。", "",
           "## 二、持仓的出场规则（实盘时点）", "",
           f"exit_buffer / exit_confirm / rank_buffer 只对已持有的名字放宽，新买入照旧。{len(EXIT_GRID['exit_buffer']) * len(EXIT_GRID['exit_confirm']) * len(EXIT_GRID['rank_buffer'])} 组里按 3 年分数排前 10：", "", *HEAD]
    rows = sorted(rep["exits"]["open_partial"], key=lambda r: -r["evolve"]["score"])
    md.append(_row("现在（0 / 1 / 0）", cur))
    for r in rows[:10]:
        md.append(_row(_exit_label(r["params"]), r))
    md += ["", "每个旋钮单独看（其他旋钮取平均；实盘时点 / 回测时点）：", "",
           "| 旋钮 | 取值 | 3 年分数（实盘时点） | 全期分数（实盘时点） | 年换手 | 3 年分数（回测时点） |", "|---|---|---|---|---|---|"]
    for k, vals in rep["knobs"]["open_partial"].items():
        for v, s in vals.items():
            c = rep["knobs"]["close"][k][v]
            md.append(f"| {k} | {v} | {s['evolve']:.2f} | {s['full']:.2f} | {s['turnover']:.1f}x | {c['evolve']:.2f} |")
    md += ["", "## 三、同组上限", "",
           "组：" + "；".join(f"{g}（{len(ss)}）" for g, ss in momentum.GROUPS.items()) + "。", "", *HEAD]
    for mode in ("open_partial", "close"):
        for r in rep["caps"][mode]:
            md.append(_row(f"{'实盘时点' if mode == 'open_partial' else '回测时点'}，同组最多 {r['params']['group_cap'] or '不限'}", r))
    be, bc, st = rep["best_exit"], rep["best_cap"], rep["stacked"]
    rb4 = next((r for r in rep["exits"]["open_partial"]
                if r["params"] == {"exit_buffer": 0, "exit_confirm": 1, "rank_buffer": 4}), None)
    yrs = sorted({y for r in rep["caps"]["open_partial"] for y in r["full"]["years"]})
    md += ["", "各年份收益（实盘时点）：哪一年赚的，比平均数要紧。", "",
           "| 情况 | " + " | ".join(yrs) + " |", "|---|" + "---|" * len(yrs)]
    for label, r in [("现在", cur), ("排名余地 4（其他不动）", rb4)] + \
                    [(f"同组最多 {x['params']['group_cap']}", x) for x in rep["caps"]["open_partial"] if x["params"]["group_cap"]]:
        if r:
            md.append(f"| {label} | " + " | ".join(_p(r["full"]["years"].get(y), 1) for y in yrs) + " |")
    md += ["", "## 四、叠起来", "", *HEAD, _row("现在", cur), _row("最好的出场规则：" + _exit_label(be["params"]), be),
           _row(f"最好的同组上限：{bc['params']['group_cap'] or '不限'}", bc),
           _row("两样都用（实盘时点）：" + _exit_label(st["params"]), st),
           _row("两样都用（回测时点）", rep["stacked_close"]), ""]
    wa = rep["would_adopt"]
    md += [f"**按进化的门槛（3 年分数比现在的 {cur['evolve']['score']:.2f} 高 {rep['min_improvement']} 以上）：出场规则{'够' if wa['exit'] else '不够'}格"
           f"（{be['evolve']['score']:.2f}），同组上限{'够' if wa['cap'] else '不够'}格（{bc['evolve']['score']:.2f}），"
           f"叠起来{'够' if wa['stacked'] else '不够'}格（{st['evolve']['score']:.2f}）。**", "",
           "读法：", "",
           f"- 成交时点不是 10 月亏钱的原因：开盘成交的两行分数不比收盘成交低，10-01 起两边都差不多；晚一天成交掉到 {t['lag']['evolve']['score']:.2f}，"
           "说明信号一天就衰减，换到 15:50 成交没有好处。10 月那 $1,531 是同一批单子开盘价比收盘价差，不是长期规律。",
           "- 50 日线出场加缓冲是反的：缓冲越大分数越低，确认天数也略差——跌破线就该走。真正减少换手的是「排名余地」：被挤到第 9–12 名的持仓不急着换，"
           + (f"年换手 {cur['evolve']['turnover_year']:.0f}x 降到 {rb4['evolve']['turnover_year']:.0f}x，来回笔数 {cur['evolve']['trades']} 到 {rb4['evolve']['trades']}，"
              f"3 年分数 {cur['evolve']['score']:.2f} 到 {rb4['evolve']['score']:.2f}（差 {rep['min_improvement']} 的门槛）。" if rb4 else ""),
           f"- 同组上限 2 是唯一过门槛的，但看各年份：它赢在 2026（AI 名字来回打脸的那段），2024、2025 这两年 AI 一条线领涨时反而少赚；"
           "验证期正好是 2026，所以验证夏普特别高。这是「少押 AI」的保险，不是更会选股。", "",
           robust.table({"现在（回测时点）": t["close"]["robust"], "现在（实盘时点）": cur["robust"],
                         "最好的出场规则": be["robust"], "最好的同组上限": bc["robust"], "两样都用": st["robust"]},
                        "稳健性检查（每一次买入到卖出算一笔，每 $1 的收益）"),
           "## 提醒", "",
           f"- 出场规则是 {len(rows)} 组里挑的、同组上限是 {len(CAP_GRID)} 组里挑的，有选择偏差；进化的门槛（高 {rep['min_improvement']}）就是为这个留的。",
           "- Yahoo 日线和进化用的 Alpaca 日线不是同一份数据，分数会差一点；两份都是拆股调整价。",
           "- 10-01 起只有一个月，那一列是看方向，不是证据。",
           "- 不模拟保护性止损、日内熔断和 09:40 对开盘价的差，实盘比「实盘时点」那行还要再差一点。",
           ""]
    return "\n".join(md)


def main() -> None:
    cfg = json.loads((ROOT / "config" / "strategy.json").read_text())
    days, opens, closes = load(cfg["universe"], cfg["regime_symbol"])
    rep = run(cfg, days, opens, closes, log=lambda *a: print(*a, file=sys.stderr))
    rep["robust_flags"] = {k: rep[k]["robust"]["flags"] for k in ("current", "best_exit", "best_cap", "stacked")}
    save("momentum-fixes", rep, markdown(rep))


if __name__ == "__main__":
    main()
