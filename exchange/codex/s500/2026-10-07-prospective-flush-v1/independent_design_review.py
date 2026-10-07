"""Independent calendar/scope audit for the prospective registration; no network."""
import argparse
import hashlib
import itertools
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent
ET = ZoneInfo("America/New_York")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(name):
    return json.loads((ROOT / name).read_text())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("draft", "registered"), default="draft")
    args = parser.parse_args()
    protocol = read("protocol.json")
    calendar = read("calendar-sessions.json")
    panel = read("fixed-panel.json")
    checks = []

    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})

    check("window", protocol["window"] == ["2026-10-08", "2026-12-31"])
    check("registration_day_excluded", protocol["registration_day_excluded"] == "2026-10-07")
    check("timezone", protocol["timezone"] == "America/New_York")
    check("calendar_hash", protocol["calendar_sha256"] == sha(ROOT / "calendar-sessions.json"))
    check("calendar_source_hash", protocol["calendar_source_sha256"] == sha(ROOT / "calendar-source.json"))
    check("panel_hash", protocol["fixed_panel_sha256"] == sha(ROOT / "fixed-panel.json"))
    panel_digest = hashlib.sha256(json.dumps(panel, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    check("panel_content_digest", protocol["fixed_panel_content_digest"] == panel_digest)
    check("panel_size_uniqueness", len(panel) == len({r["symbol"] for r in panel}) == protocol["fixed_panel_symbols"] == 6633)
    check("panel_flags", all(r["etf_classification"] in {"Y", "N", "unknown"} for r in panel))
    dates = [r["date"] for r in calendar]
    expected = [r for r in calendar if "2026-10-08" <= r["date"] <= "2026-12-31"]
    check("exact_future_session_sequence", [r["date"] for r in protocol["sessions"]] == [r["date"] for r in expected])
    check("future_session_count", len(expected) == 59)
    for ordinal, (declared, source) in enumerate(zip(protocol["sessions"], expected), 1):
        day = source["date"]
        utc = lambda t: datetime.fromisoformat(day + "T" + t).replace(tzinfo=ET).astimezone(timezone.utc).isoformat()
        index = dates.index(day)
        fields = {
            "ordinal": ordinal, "date": day,
            "open_et": source["open"], "close_et": source["close"],
            "open_utc": utc(source["open"]), "close_utc": utc(source["close"]),
            "preopen_cutoff_utc": utc("09:20"), "receipt_deadline_utc": utc("17:15"),
            "review_at_utc": utc("17:30"), "prior20_dates": dates[index-20:index],
        }
        check("all_calendar_time_fields_" + day, all(declared.get(k) == v for k, v in fields.items()))
    check("review_ordinals", protocol["review_ordinals"] == [20, 40, 59])
    check("review_dates", [(r["ordinal"], r["date"], r["at_et"]) for r in protocol["reviews"]] == [
        (20, "2026-11-04", "17:30"), (40, "2026-12-03", "17:30"), (59, "2026-12-31", "17:30")])
    check("half_days", [(r["date"], r["close"]) for r in expected if r["close"] != "16:00"] == [
        ("2026-11-27", "13:00"), ("2026-12-24", "13:00")])
    variants = list(itertools.product(("0.02", "0.03"), ("FIXED", "REBOUND"), ("30m", "60m", "close_minus_10m"), (5, 10, 25)))
    check("all36_exact_variants", [tuple(v) for v in protocol["inherited_variants"]] == variants)
    check("primary_variant", protocol["primary_variant"] == ["0.02", "REBOUND", "60m", 10])
    s = protocol["selection"]
    check("selection_constants", (s["prior_close_minimum"], s["median_close_times_volume_minimum"], s["top_liquidity"], s["remaining_hash_sample"], s["hash_namespace"]) == ("5", "20000000", 64, 32, "s500_flush_v1"))
    check("storage_overlay", s["storage"] == ["MU", "STX", "WDC", "SNDK", "NTAP", "RMBS", "SIMO", "P"])
    check("benchmark_controls", s["controls"] == ["SPY", "QQQ"])
    m = protocol["market_classification"]
    check("market_groups", m["primary"] == "SPY" and m["sensitivity"] == "QQQ" and m["groups"] == ["market_down", "market_not_down", "unknown"] and m["benchmark_targets_excluded_from_stock_statistics"] is True)
    b = protocol["feature_bins"]
    check("price_bins", b["gap"] == b["close29"] == ["le_minus_2pct", "minus_2pct_to_below_zero", "zero_or_positive", "unknown"])
    check("trough_bins", b["trough"] == ["minute0_9", "minute10_19", "minute20_29", "unknown"])
    check("bins_not_promoted", b["all_bins_reported"] is True and b["no_winning_bin_promotion"] is True)
    o = protocol["outcomes"]
    check("sample_label20_only", o["evidence_label_minimum_distinct_complete_outcome_dates"] == 20 and o["no_automatic_pass_fail_or_trading_activation"] is True)
    check("peer_scope_clarified", all(token in o["peers"] for token in ("sealed selected stock_eligible", "excluding target and SPY/QQQ", "SAME 2% or3% threshold", "never replace missing peers")))
    check("raw_price_bins_clarified", all(token in b.get("definitions", "") for token in ("gap=open0/sealed_prior_close-1", "close29=close[29]/open0-1", "earliest integer0..29", "exact raw-price", "<=-0.02,(-0.02,0),>=0", "No display-rounded boundaries")))
    timing = protocol["timing"]
    check("first_complete_source_not_gap_free", all(token in timing["primary_source_version"] for token in ("all required requests/pages completed", "irrespective of individual missing/invalid bars", "Freeze that version even with unknown outcomes", "gap-free")))
    check("offline_review_receipts_distinguished", all(token in timing.get("offline_review", "") for token in ("may happen after receipt_deadline", "original preopen seal", "Running late cannot fabricate")))
    op = protocol["operational_status"]
    check("no_scheduler_claim", op["registration_is_not_scheduler"] is True and op["new_scheduler"] is False and op["future_capture_automatically_connected"] is False and op["existing_observer_not_ai_research"] is True)
    u = protocol["user_constraints"]
    check("user_payoff_constraints", u["paper_initial_per_round"] == 500 and u["paper_target"] == 10000 and u["no_minimum_40_percent_winrate"] is True and u["preserve_all_tail_winners_and_losses"] is True)
    check("no_orders_or_wealth_claims", u["funded_portfolio_or_option_profit_claim"] is False and u["orders_sent"] == u["account_reads"] == 0 and u["vendor_purchase"] is False)
    if args.phase == "registered":
        check("registered_status", protocol["status"] == "registered")
        at = datetime.fromisoformat(protocol["registered_at"])
        first_midnight = datetime(2026, 10, 8, tzinfo=ET)
        check("registered_before_future_capture_date", at < first_midnight)

    output = {
        "phase": args.phase,
        "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
        "protocol_sha256": sha(ROOT / "protocol.json"),
        "reviewer_code_sha256": sha(Path(__file__)),
        "binding": {name: sha(ROOT / name) for name in ["calendar-sessions.json", "calendar-source.json", "fixed-panel.json", "protocol_tools.py", "prospect_selection.py"]},
        "structural_checks_passed": all(c["passed"] for c in checks),
        "checks_count": len(checks), "checks": checks,
        "manual_semantic_review": {
            "primary_endpoint": "One final SPYdown date-equal net stock-proxy mean; all other estimates descriptive.",
            "checks20_40": "No interval or retuning at interim checkpoints; no performance-dependent stopping.",
            "minimum20_dates": "Operational sufficiency label only, not independence/power/significance.",
            "capture_vs_review": "On-time source and preopen seal required; offline metadata verification may occur later.",
            "receipt_guard_scope": "Validates supplied metadata, not source-body hash correctness or authenticated historical receipt.",
            "activation": "Registration plus tools only; no connected schedule, daily-run promise, orders or option-execution validation.",
        },
        "resolved_design_clarifications": {
            "outcomes.peers": o["peers"],
            "feature_bins.definitions": b["definitions"],
            "timing.primary_source_version": timing["primary_source_version"],
            "timing.offline_review": timing["offline_review"],
        },
        "design_verdict": "No unresolved design objection within this registration-only scope; final registration and code hashes remain separately bound.",
        "approval_scope": "Structural and design review only; does not certify raw data, collection deployment, future observations, or future return implementation.",
    }
    target = ROOT / ("independent-design-" + args.phase + "-review.json")
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"phase": args.phase, "structural_checks_passed": output["structural_checks_passed"], "checks_count": len(checks), "protocol_sha256": output["protocol_sha256"]}))
    if not output["structural_checks_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
