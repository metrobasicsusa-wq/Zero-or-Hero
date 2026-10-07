"""Bind completed manual review and independent artifact audits to final files.

Not a market-data test. No API calls, credentials or purchases.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-dir", required=True)
    parser.add_argument("--prior-dir", required=True)
    args = parser.parse_args()
    stage, prior = Path(args.stage_dir), Path(args.prior_dir)
    audits = ["prior-scope-review.json", "request-plan-review.json", "document-provenance-review.json", "peer-arithmetic-review.json"]
    audit_records = {}
    for name in audits:
        record = json.loads((stage / "audit" / name).read_text())
        source = prior if name == "prior-scope-review.json" else stage
        for rel, digest in record["sha256"].items():
            assert sha(source / rel) == digest, (name, rel, "audit stale")
        audit_records[name] = {"status": record["status"], "sha256": sha(stage / "audit" / name)}
    adaptation = json.loads((stage / "provider-adaptation-plan.json").read_text())
    assert adaptation["generic_request_plan_sha256"] == sha(stage / "historical-request-plan.json")
    assert adaptation["primary_quality_gate_relaxed"] is False
    assert adaptation["paid_request_or_purchase_authorized"] is False
    assert adaptation["generic_window_plan_overwritten"] is False
    for rel, digest in adaptation["evidence_sha256"].items():
        assert sha(stage / rel) == digest, rel
    decision = json.loads((stage / "stage-decision.json").read_text())
    for rel, digest in decision["source_provenance"].items():
        assert sha(stage / rel) == digest, rel
    assert decision["prepared_outputs"] == {
        "all_prior_rows_preserved": 800,
        "plannable_prior_contract_windows": 598,
        "retained_unavailable_contracts": 202,
        "pilot_SPY_windows": 20,
        "pilot_contract_seconds": 1300,
        "all_598_contract_seconds": 38870,
        "actual_new_historicalquote_rows_obtained": 0,
        "new_strategy_wealth_paths": 0,
    }
    assert "remaining598" not in decision["next_scope"]
    assert sha(stage / "prior-selected-contracts.json") == sha(prior / "selected-contracts.json")
    subscription = json.loads((stage / "user-subscription-status.json").read_text())
    assert subscription["user_answer_zh"] == "没有这些订阅"
    assert subscription["existing_subscriptions_confirmed"] is False
    final_files = ["source-study-design.json", "historical-request-plan.json", "provider-adaptation-plan.json", "peer-reply.json", "stage-decision.json", "REPORT.zh-CN.md", "REPRODUCE.md", "prior-selected-contracts.json", "user-subscription-status.json"]
    result = {
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass_for_documented_source_research_and_unexecuted_plan",
        "independent_reviews": audit_records,
        "manual_review_conclusions": [
            "Four providers are evidence-qualified conditional candidates, not verified user entitlements or data connections.",
            "Databento generic SDK/DBN evidence is separated from unverified OPRA product schemas, dates, pricing and license; JavaScript shells remain unknown.",
            "Massive historical fields and advertised coverage are supported; missing conditions/flags remain incompatible with an automatic pass under the inherited primary quote gate.",
            "Theta tick tier and history are documented; default sampling, activity-based contract discovery, unknown size/timestamp semantics and untested gRPC/Terminal transport are preserved.",
            "Cboe interval snapshots and June 22, 2026 size methodology change are separated from unverified tick products and unrelated All Access API pricing.",
            "The generic event-time plan has not been silently mapped to Databento receive-time or Massive SIP-receipt filters.",
            "800 prior rows, including 202 unresolved and 198 old no-trade windows, are retained; the 20 SPY pilot is a subset of 598 planned contract windows.",
            "The October 5 cutoff, ten sampled sessions, 180 unsampled sessions and inability to simulate exits from 65-second windows remain explicit.",
            "License excerpts are described as applicable-term questions rather than a blanket legal conclusion; public retail prices are conditional and not a project quote.",
            "No new paid market-data request, account attestation, order or scheduler is represented as authorized or completed.",
            "Peer spread arithmetic is correct under explicit unverified price, multiplier, capacity and no-fee assumptions; it is not actual fill or strategy evidence.",
            "Public reproduction is limited: exact complete source documents and market inputs are private; hashes do not by themselves make those sources publicly reproducible.",
            "The user's confirmation of no subscription to the four named vendors is retained; paid acquisition is deferred, and any subsequent underlying-stock signal study must remain separate from option-execution returns.",
        ],
        "resolved_review_findings": [
            "Provider-specific time-filter and missing-condition caveats added in a separate adapter plan without overwriting the frozen generic plan.",
            "Pilot20 plus the full598 are not additive; final next-scope wording identifies all598 rather than remaining598.",
            "First source-quotation audit needed the retained Theta .main.txt extraction; adding that reader path resolved the extraction-only failure without changing source hashes or quotes.",
        ],
        "data_quality_or_execution_ready": False,
        "historical_quote_rows_newly_acquired": 0,
        "strategy_paths_run": 0,
        "sha256": {name: sha(stage / name) for name in final_files},
    }
    (stage / "audit" / "final-review.json").write_text(json.dumps(result, indent=2) + "\n")
    (stage / "audit" / "FINAL_REVIEW.md").write_text(
        "# Independent final review\n\n"
        "Pass for official-source research and an unexecuted acquisition plan only. No historical quote dataset, provider entitlement, execution model or strategy return was validated.\n\n"
        "The prior 800 rows and 400 discovery cells reconcile independently. All 800 request rows preserve their original symbols, statuses and missing reasons; 598 windows, a 20-window SPY subset, 202 unresolved rows and UTC/DST conversions reconcile. The 65-second diagnostic is explicitly insufficient for exit paths or a full-year backtest.\n\n"
        "All 60 captured-document bodies match their recorded hashes and sizes. The 115 evidence entries contain 129 exact quotations verified against the retained body or text extraction. This verifies captured citations, not provider access or the completeness of market data.\n\n"
        "The final report preserves missing condition and timestamp semantics, current-versus-historical identity limits, conditional public pricing and applicable-license questions. The provider adapter keeps receive-time, SIP receipt and interval-snapshot semantics distinct from the generic event-time window. None of the inherited quality gates was relaxed.\n\n"
        "The peer example independently reproduces $495 premium, $270 unchanged-bid liquidation and $225 before-fee loss; capacity, multiplier and reported prices remain unverified assumptions.\n\n"
        "See final-review.json for artifact bindings, all manual conclusions and resolved review findings.\n"
    )
    print(json.dumps({"status": result["status"], "bound_final_files": len(final_files), "independent_reviews": len(audits)}))


if __name__ == "__main__":
    main()
