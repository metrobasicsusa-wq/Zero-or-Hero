"""Audit published research artifacts, without market-data or broker access.

Usage: python audit.py PATH_TO_ZERO_OR_HERO_ROOT > audit-results.json
This checks published arithmetic and integrity, not independent backtest validity.
"""
import hashlib
import json
import math
import sys
from pathlib import Path


def require(condition, label):
    if not condition:
        raise ValueError(label)


def close(actual, expected, label):
    require(math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-7), label)


def audit(root):
    base = root / "exchange/codex/research/2026-10-02"
    manifest = json.loads((base / "manifest.json").read_text())
    for item in manifest["files"]:
        content = (root / item["path"]).read_bytes()
        require(len(content) == item["bytes"], "byte count: " + item["path"])
        require(hashlib.sha256(content).hexdigest() == item["sha256"],
                "hash mismatch: " + item["path"])
    index = json.loads((base / "backtest-results-index.json").read_text())
    require(len(index["results"]) == 72, "expected 72 published candidates")
    require(len(index["rolling_126_sessions"]) == 48, "expected 48 windows")
    rows = []
    seen = set()
    for summary in index["results"]:
        run = json.loads((base / summary["details_path"]).read_text())
        label = run["run_id"]
        require(label not in seen, "duplicate run: " + label)
        seen.add(label)
        for key, value in summary.items():
            if key != "details_path":
                require(run[key] == value, label + " summary mismatch: " + key)
        curve = [dict(zip(run["curve_columns"], row, strict=True)) for row in run["curve"]]
        trades = [dict(zip(run["trade_columns"], row, strict=True)) for row in run["trades"]]
        require(len(curve) == run["sessions"], label + " session count")
        days = [row["day"] for row in curve]
        require(days == sorted(set(days)), label + " curve dates")
        require(days[0] == run["start"] and days[-1] == run["end"], label + " bounds")
        last = curve[-1]
        close(last["equity"], run["final_active_equity"], label + " final equity")
        close(last["vault"], run["retired_cash"], label + " final retired cash")
        close(last["injected"], run["total_injected"], label + " final funding")
        close(last["equity"] + last["vault"], run["final_total_wealth"], label + " wealth")
        close(run["final_total_wealth"] - run["total_injected"], run["net_profit"], label + " pnl")
        close(run["net_profit"] / run["total_injected"], run["return_on_total_injected"], label + " return")
        close(run["initial"] * (1 + run["restart_count"]), run["total_injected"], label + " resets")
        require(sum(t["side"] == "buy" for t in trades) == run["buy_count"], label + " buy count")
        require(sum(t["side"] == "sell" for t in trades) == run["closed_trades"], label + " sell count")
        funding = []
        previous_vault = previous_funding = 0
        targets = {}
        peak = run["initial"]
        drawdown = 0
        for row in curve:
            require(row["injected"] >= previous_funding, label + " funding decreases")
            require(row["vault"] >= previous_vault, label + " retired cash decreases")
            if row["injected"] != previous_funding:
                funding.append({"day": row["day"], "total_injected": row["injected"]})
            previous_funding, previous_vault = row["injected"], row["vault"]
            for target in (1000, 2000, 10000):
                if row["equity"] >= target:
                    targets.setdefault(str(target), row["day"])
            peak = max(peak, row["equity"])
            drawdown = max(drawdown, 1 - row["equity"] / peak)
        require(funding == run["contributions"], label + " contribution schedule")
        require(targets == run["target_dates"], label + " target dates")
        if not run["restart_enabled"]:
            close(drawdown, run["max_close_drawdown"], label + " max drawdown")
        rows.append({key: run[key] for key in (
            "run_id", "name", "momentum", "slots", "cost_bps_each_side", "period",
            "restart_enabled", "restart_count", "halted", "pending_risk_liquidation",
            "final_active_equity", "retired_cash", "total_injected", "final_total_wealth",
            "net_profit", "target_dates")})
    for row in index["rolling_126_sessions"]:
        close(row["final_active_equity"] + row["retired_cash"], row["final_total_wealth"], "rolling wealth")
        close(row["final_total_wealth"] - row["total_injected"], row["net_profit"], "rolling pnl")
    return {
        "schema_version": "1.0-s500-artifact-audit",
        "status": "passed",
        "scope": "Published-file integrity and arithmetic only; no fresh market backtest or broker FILL.",
        "source_commit": "2ca9ee52dcf20903bf4318746fc994c42536d3cd",
        "source_head_reviewed": "66fa0d6ad24a7a8c0ab4798d0709eb0b1ba74af3",
        "manifest_files_verified": len(manifest["files"]),
        "candidate_paths_verified": len(rows),
        "rolling_summaries_verified": len(index["rolling_126_sessions"]),
        "candidate_target_hit_counts": {str(n): sum(str(n) in r["target_dates"] for r in rows) for n in (1000, 2000, 10000)},
        "candidate_paths_with_net_loss": sum(r["net_profit"] < 0 for r in rows),
        "candidate_paths_with_restarts": sum(r["restart_count"] > 0 for r in rows),
        "published_code_sha256": hashlib.sha256((base / "backtest_v01.py").read_bytes()).hexdigest(),
        "raw_input_published": index["raw_input_published"],
        "independent_backtest_reproduction": False,
        "candidates": rows,
    }


if __name__ == "__main__":
    print(json.dumps(audit(Path(sys.argv[1])), ensure_ascii=False, indent=2))
