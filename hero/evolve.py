"""Self-evolution: search nearby parameters, adopt only robust out-of-sample improvements.

Each candidate is scored on a training window and a later validation window; the
score is the worse of the two Sharpe ratios, so a parameter set has to work in
both periods. The live config is replaced only when the best candidate beats it
by a clear margin. Every decision is appended to journal/evolution.md.
"""

from __future__ import annotations

import copy
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
}
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
    changed = {k: f"{cfg['stocks'].get(k)} -> {best_p[k]}" for k in GRID if cfg["stocks"].get(k) != best_p[k]}
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


def save(cfg: dict, report: str, cfg_path: Path, log_path: Path) -> None:
    cfg_path.write_text(json.dumps(cfg, indent=2) + "\n")
    header = "" if log_path.exists() else "# Evolution log\n\n"
    with open(log_path, "a") as f:
        f.write(header + report + "\n")
