"""Self-evolution: search nearby parameters, adopt only robust out-of-sample improvements.

Each candidate is scored on a training window and a later validation window; the
score is the worse of the two Sharpe ratios, so a parameter set has to work in
both periods. The live config is replaced only when the best candidate beats it
by a clear margin. Every decision is appended to journal/evolution.md.
"""

from __future__ import annotations

import copy
import csv
import itertools
import json
from datetime import date
from pathlib import Path

from hero import backtest

GRID = {
    "momentum_lookback": [63, 126, 189],
    "trend_sma": [50, 100, 200],
    "top_n": [3, 5, 8],
    # True: RSI > rsi_max also forces out a held winner. False: RSI only gates new entries.
    "rsi_applies_to_holdings": [True, False],
    # 1.0: macro gauges are only recorded. 0.5: halve the stock book when two or more are flagged.
    "macro_scale": [1.0, 0.5],
    # 11-02 (research/2026-11-02-momentum-fixes.md): a held name keeps its slot while ranked within top_n + 4
    # (turnover halves); at most 2 or 3 picks from one momentum.GROUPS bucket. 0 = off, today's behaviour.
    "rank_buffer": [0, 4],
    "group_cap": [0, 2, 3],
}
DEFAULTS = {"rank_buffer": 0, "group_cap": 0}   # what a config without the key trades like
WARMUP = 210
TRAIN_FRACTION = 0.7
MIN_IMPROVEMENT = 0.15


def evaluate(closes: dict, p: dict, cfg: dict, n: int, macro_closes: dict | None = None) -> dict:
    split = WARMUP + int((n - WARMUP) * TRAIN_FRACTION)
    args = (cfg["regime_symbol"], cfg["risk"]["max_position_pct"])
    train = backtest.run(closes, p, *args, WARMUP, split, macro_closes)
    val = backtest.run(closes, p, *args, split, n, macro_closes)
    return {"train": train, "val": val, "score": min(train["sharpe"], val["sharpe"])}


def evolve(cfg: dict, closes: dict, live_curve: list[float], today: date,
           macro_closes: dict | None = None) -> tuple[dict, str]:
    n = min(len(xs) for xs in closes.values())
    if n < WARMUP + 120:
        return cfg, f"not enough history ({n} days)"

    current = evaluate(closes, cfg["stocks"], cfg, n, macro_closes)
    best_p, best = cfg["stocks"], current
    for combo in itertools.product(*GRID.values()):
        p = {**cfg["stocks"], **dict(zip(GRID, combo))}
        if p["macro_scale"] < 1.0 and not macro_closes:
            continue  # cannot test a macro rule without macro history
        res = evaluate(closes, p, cfg, n, macro_closes)
        if res["score"] > best["score"]:
            best_p, best = p, res

    adopt = best_p is not cfg["stocks"] and best["score"] >= current["score"] + MIN_IMPROVEMENT
    new = copy.deepcopy(cfg)
    if adopt:
        new["stocks"] = best_p
        new["generation"] = cfg["generation"] + 1

    live = ""
    if len(live_curve) >= 2:
        live = f"- live return since start: {live_curve[-1] / live_curve[0] - 1:+.2%} over {len(live_curve)} days\n"
    cur_p = {**DEFAULTS, **cfg["stocks"]}
    changed = {k: f"{cur_p.get(k)} -> {best_p[k]}" for k in GRID if cur_p.get(k) != best_p[k]}
    report = (
        f"## {today.isoformat()} — generation {new['generation']}\n"
        f"{live}"
        f"- current score {current['score']:.2f} (train {current['train']['sharpe']:.2f}, "
        f"val {current['val']['sharpe']:.2f}, val DD {current['val']['max_drawdown']:.1%})\n"
        f"- best score {best['score']:.2f} (train {best['train']['sharpe']:.2f}, "
        f"val {best['val']['sharpe']:.2f}, val DD {best['val']['max_drawdown']:.1%}) {changed or ''}\n"
        f"- decision: {'ADOPT' if adopt else 'keep'}\n"
    )
    return new, report


OPTION_KEYS = ("target_delta", "take_profit", "stop_loss", "exit_dte", "allocation")


def evolve_options(cfg: dict, rep: dict) -> tuple[dict, str]:
    """Apply the options parameter search (hero.research_evolve_options.run) under the stock search's gate.

    rep["would_adopt"] is that module's verdict: the best set traded at least MIN_VAL_TRADES times in the
    validation window and scores MIN_IMPROVEMENT above the live set, scored like evaluate() on the combined
    stock + options daily returns. Adopting writes its delta, DTE window, take-profit, stop-loss, exit day and
    allocation into cfg["options"] and bumps the generation; max_positions and the guards stay as they are.
    """
    from hero.research_evolve_options import MIN_VAL_TRADES

    cur, best = rep["current"], rep["best"]
    adopt = bool(rep.get("would_adopt"))
    new = copy.deepcopy(cfg)
    if adopt:
        bp = best["params"]
        new["options"].update({k: bp[k] for k in OPTION_KEYS})
        new["options"]["min_dte"], new["options"]["max_dte"] = bp["dte"]
        new["generation"] = cfg["generation"] + 1
    live = {k: cfg["options"].get(k) for k in OPTION_KEYS}
    live["dte"] = [cfg["options"].get("min_dte"), cfg["options"].get("max_dte")]
    want = {k: (list(v) if k == "dte" else v) for k, v in best["params"].items()}
    changed = {k: f"{live[k]} -> {want[k]}" for k in live if live[k] != want[k]}
    report = (f"- options: current score {cur['score']:.2f} (val {cur['val']:.2f}, {cur['val_trades']} val trades, "
              f"options P&L {cur['opt_pnl']:+,.0f}); best {best['score']:.2f} (val {best['val']:.2f}, "
              f"{best['val_trades']} val trades, P&L {best['opt_pnl']:+,.0f}) {changed or ''}\n"
              f"- options decision: {'ADOPT' if adopt else 'keep'}"
              + ("" if adopt or not changed else f" (needs >= {MIN_VAL_TRADES} validation trades and +{MIN_IMPROVEMENT:.2f})")
              + "\n")
    return new, report


def briefing_accuracy(scores_path: Path) -> str:
    """Report-only line on how the pre-market briefings' predictions scored.

    Rows come from macro/briefings/scores.csv (date,kind,prediction,result,source), written
    by the briefing/review routines. kind is "prediction" or "direction"; result is
    hit, miss or na. This never feeds parameter selection.
    """
    if not scores_path.exists():
        return ""
    with open(scores_path, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return ""
    parts = []
    for kind, label in (("prediction", "predictions"), ("direction", "bias vs SPY")):
        rs = [r["result"].strip() for r in rows if r["kind"].strip() == kind]
        hit, miss = rs.count("hit"), rs.count("miss")
        if hit + miss:
            parts.append(f"{label} {hit}/{hit + miss} ({hit / (hit + miss):.0%})")
        if rs.count("na"):
            parts.append(f"{label} unverifiable {rs.count('na')}")
    days = len({r["date"] for r in rows})
    return f"- briefing accuracy (record only, {days} days): {', '.join(parts) or 'nothing verified yet'}\n"


def save(cfg: dict, report: str, cfg_path: Path, log_path: Path) -> None:
    cfg_path.write_text(json.dumps(cfg, indent=2) + "\n")
    header = "" if log_path.exists() else "# Evolution log\n\n"
    with open(log_path, "a") as f:
        f.write(header + report + "\n")
