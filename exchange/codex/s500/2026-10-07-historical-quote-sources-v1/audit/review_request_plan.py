"""Audit the actual request-plan artifact against unchanged prior selections.

No network requests, credentials, downloads or strategy simulation are used.
Usage: python review_request_plan.py --stage-dir DIR --prior-dir DIR
"""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-dir", required=True)
    parser.add_argument("--prior-dir", required=True)
    args = parser.parse_args()
    stage, prior = Path(args.stage_dir), Path(args.prior_dir)
    design = json.loads((stage / "source-study-design.json").read_text())
    plan = json.loads((stage / "historical-request-plan.json").read_text())
    originals = json.loads((prior / "selected-contracts.json").read_text())["historical"]
    assert plan["design_sha256"] == sha(stage / "source-study-design.json")
    assert plan["prior_selected_sha256"] == sha(prior / "selected-contracts.json")
    for name, digest in design["prior_files_sha256"].items():
        assert sha(prior / name) == digest, name
    key = lambda row: (row["case_id"], row["type"])
    orig = {key(row): row for row in originals}
    rows = {key(row): row for row in plan["rows"]}
    assert len(plan["rows"]) == len(rows) == 800 and rows.keys() == orig.keys()
    offsets = Counter()
    actual_contract_seconds = 0
    pilot_seconds = 0
    for row_key, row in rows.items():
        old = orig[row_key]
        assert row["date"] == old["date"]
        assert row["underlying_symbol"] == old["underlying_symbol"]
        assert row["prior_selection_status"] == old["status"]
        assert row["original_missing_reasons"] == old["reasons"]
        contract = old["contract"] or {}
        for new_name, old_name in [("contract_symbol", "symbol"), ("expiration_date", "expiration_date"), ("strike_price", "strike_price"), ("explicit_provider_multiplier", "multiplier")]:
            assert row[new_name] == contract.get(old_name), (row_key, new_name)
        assert row["request_plannable"] is bool(contract)
        assert row["pilot"] is (row["underlying_symbol"] == "SPY" and bool(contract))
        assert row["data_acquired"] is False and row["historical_identity_independently_verified"] is False
        start = datetime.fromisoformat(row["start_utc"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(row["end_exclusive_utc"].replace("Z", "+00:00"))
        local_start = start.astimezone(ZoneInfo("America/New_York"))
        local_end = end.astimezone(ZoneInfo("America/New_York"))
        assert local_start.isoformat()[:10] == row["date"] == local_end.isoformat()[:10]
        assert local_start.strftime("%H:%M:%S") == "09:59:55"
        assert local_end.strftime("%H:%M:%S") == "10:01:00"
        assert (end - start).total_seconds() == 65
        actual_checkpoints = [datetime.fromisoformat(point.replace("Z", "+00:00")) for point in row["diagnostic_checkpoints_utc"]]
        assert actual_checkpoints == [start + timedelta(seconds=offset) for offset in [5, 20, 35, 50]]
        offsets[str(local_start.utcoffset())] += 1
        if row["request_plannable"]:
            actual_contract_seconds += 65
        if row["pilot"]:
            pilot_seconds += 65
    counts = {
        "all_rows": len(rows),
        "plannable_contract_windows": sum(row["request_plannable"] for row in rows.values()),
        "missing_contract_rows": sum(not row["request_plannable"] for row in rows.values()),
        "pilot_SPY_contract_windows": sum(row["pilot"] for row in rows.values()),
        "pilot_unique_dates": len({row["date"] for row in rows.values() if row["pilot"]}),
        "seconds_per_window": 65,
        "pilot_contract_seconds": pilot_seconds,
        "full_sample_contract_seconds": actual_contract_seconds,
    }
    assert plan["counts"] == counts
    assert plan["cost_gate"]["maximum_authorized_vendor_purchase"] == 0
    assert plan["cost_gate"]["paid_data_execution_authorized"] is False
    assert plan["cost_gate"]["provider_cost_estimate"] is None
    assert plan["strategy_returns_computed"] is False
    assert plan["account_reads"] == plan["order_requests"] == 0
    assert plan["new_scheduler"] is False
    output = {
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass_for_documentation_and_unexecuted_capability_request_plan_only",
        "independently_recomputed_counts": counts,
        "timezone_offset_row_counts": dict(offsets),
        "rows_matched_to_original_contract_fields_and_missing_reasons": 800,
        "historical_quotes_acquired_by_this_audit": 0,
        "scope_limitations": [
            "Retained source cohort ends October 5, 2026; not current-date full-year coverage.",
            "SPY pilot is deliberately liquid for source compatibility diagnostics, not representative performance evidence.",
            "65-second windows and four checkpoints do not contain full exits or whole trading days.",
            "598 plannable contracts have not acquired historical point-in-time identity certification.",
            "Original 202 missing rows are preserved, not classified as historically nonexistent.",
            "No exact vendor cost, credential readiness or historical quote entitlement is established.",
        ],
        "evidence_level": "Offline artifact consistency and manual scope review; not a downloaded-data or trading test.",
        "sha256": {name: sha(stage / name) for name in ["source-study-design.json", "historical-request-plan.json"]},
    }
    (stage / "audit" / "request-plan-review.json").write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps({"status": output["status"], "counts": counts, "timezone_offset_row_counts": dict(offsets)}))


if __name__ == "__main__":
    main()
