"""Independent, read-only inventory audit of the preceding feasibility stage.

This is a source/result audit, not a strategy test or new market-data fetch.
Usage: python review_prior_scope.py --prior-dir DIR --output-dir DIR
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prior-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    prior, out = Path(args.prior_dir), Path(args.output_dir)
    names = ["study-design.json", "selected-contracts.json", "historical-option-events.json", "historical-reference-results.json", "session-coverage-plan.json"]
    source = {name: json.loads((prior / name).read_text()) for name in names}
    selected = source["selected-contracts.json"]["historical"]
    events = source["historical-option-events.json"]["rows"]
    results = source["historical-reference-results.json"]["rows"]
    key = lambda row: (row["case_id"], row["type"])
    assert len(selected) == len(events) == len(results) == 800
    assert len({key(row) for row in selected}) == 800
    assert {key(row) for row in selected} == {key(row) for row in events} == {key(row) for row in results}
    cases = {row["case_id"] for row in selected}
    assert len(cases) == 400
    assert all({row["type"] for row in selected if row["case_id"] == case} == {"call", "put"} for case in cases)
    statuses = Counter(row["status"] for row in selected)
    event_statuses = Counter(row["trades_status"] for row in events)
    assert statuses == {"selected": 598, "unknown_or_unavailable": 202}
    assert event_statuses == {"events_observed": 400, "no_events_returned": 198, "not_requested_no_selected_contract": 202}
    assert all(row["historical_bid_ask_available"] is False for row in events)
    calendar = source["session-coverage-plan.json"]
    assert len(calendar["all_sessions"]) == 190 and len(calendar["sample_days"]) == 10
    output = {
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "status": "prior_scope_independently_reconciled",
        "scope": {"historical_rows": len(selected), "underlying_date_discovery_cells": len(cases), "underlyings": len({row["underlying_symbol"] for row in selected}), "sample_dates": len({row["date"] for row in selected}), "calendar_sessions": len(calendar["all_sessions"]), "calendar_sessions_not_sampled": 180, "unique_selected_contracts": len({row["contract"]["symbol"] for row in selected if row["contract"]})},
        "selection_statuses": dict(statuses),
        "trade_window_statuses": dict(event_statuses),
        "historical_quote_rows_verified": 0,
        "window": "10:00:00 through 10:00:59.999999999 America/New_York on ten month-first sessions; bars/trades only",
        "source_interpretation": "No-event-in-one-minute is not evidence of no historical contract, no all-day liquidity, no quote, or no possible trade. Current retrieved contract metadata does not prove historical listing availability.",
        "downstream_required_denominators": ["all 400 discovery cells", "all 800 call/put rows", "all 598 selected contracts, including 198 without a trade in the old window", "all 202 unresolved contract rows"],
        "forbidden_inferences": ["Only 400 rows with trades are the eligible research universe", "One-minute quote probe supports exit simulation or full-year strategy performance", "202 unresolved contracts prove no listing at that historical time", "190 calendar sessions mean 190 sampled sessions", "Historical provider documentation proves this account has access"],
        "sha256": {name: hashlib.sha256((prior / name).read_bytes()).hexdigest() for name in names},
        "new_market_data_calls": 0,
        "new_strategy_paths": 0,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "prior-scope-review.json").write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps({"status": output["status"], "scope": output["scope"], "selection_statuses": output["selection_statuses"], "trade_window_statuses": output["trade_window_statuses"]}))


if __name__ == "__main__":
    main()
