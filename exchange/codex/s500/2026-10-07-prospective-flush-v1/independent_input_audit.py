"""Read-only source reconstruction and initial-ledger audit; never fetches prices."""
import argparse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent
ET = ZoneInfo("America/New_York")


def load(name):
    return json.loads((ROOT / name).read_text())


def digest_bytes(path):
    return sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger")
    args = parser.parse_args()
    p = load("protocol.json")
    panel = load("fixed-panel.json")
    panel_source = load("fixed-panel-source.json")
    source = load("calendar-source.json")
    raw_path = ROOT / "private-inputs" / source["private_response_name"]
    raw = json.loads(raw_path.read_text())
    normalized = load("calendar-sessions.json")
    checks = []

    def check(name, result):
        checks.append({"check": name, "passed": bool(result)})

    check("calendar_successful_get_only", source["method"] == "GET" and source["status"] == 200 and source["path"] == "/v2/calendar")
    check("calendar_raw_sha256", source["response_sha256"] == digest_bytes(raw_path))
    check("calendar_raw_bytes", source["response_bytes"] == len(raw_path.read_bytes()))
    check("calendar_receipt_order", datetime.fromisoformat(source["requested_at"]) <= datetime.fromisoformat(source["received_at"]))
    extracted = [{"date": r["date"], "open": r["open"], "close": r["close"]} for r in raw]
    check("all_85_calendar_rows_preserved_exactly", normalized == extracted and len(raw) == 85)
    expected = []
    current = date(2026, 9, 1)
    excluded = {date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25)}
    while current <= date(2026, 12, 31):
        if current.weekday() < 5 and current not in excluded:
            expected.append(current.isoformat())
        current += timedelta(days=1)
    check("calendar_dates_match_independent_weekday_holiday_construction", [r["date"] for r in raw] == expected)
    future = [d for d in expected if d >= "2026-10-08"]
    check("all_59_future_dates_exact", [r["date"] for r in p["sessions"]] == future and len(future) == 59)
    early_dates = {"2026-11-27", "2026-12-24"}
    for row in p["sessions"]:
        day = row["date"]
        closing = "13:00" if day in early_dates else "16:00"
        def utc(clock):
            return datetime.fromisoformat(day + "T" + clock).replace(tzinfo=ET).astimezone(timezone.utc).isoformat()
        position = expected.index(day)
        check("independent_times_and_prior20_" + day,
              row["open_et"] == "09:30" and row["close_et"] == closing
              and row["open_utc"] == utc("09:30") and row["close_utc"] == utc(closing)
              and row["preopen_cutoff_utc"] == utc("09:20")
              and row["receipt_deadline_utc"] == utc("17:15")
              and row["review_at_utc"] == utc("17:30")
              and row["prior20_dates"] == expected[position - 20:position])

    ancestor_names = {
        "broad_20261006": "s500-broad-20261006",
        "flush_rebound_20261007": "s500-flush-rebound-20261007",
    }
    inherited = {}
    for item in panel_source["sources"]:
        path = ROOT.parent / ancestor_names[item["ancestor"]] / item["name"]
        check("ancestor_hash_" + item["ancestor"], digest_bytes(path) == item["sha256"])
        inherited[item["ancestor"]] = json.loads(path.read_text())
    old_universe = inherited["broad_20261006"]
    old_labels = inherited["flush_rebound_20261007"]["population"]
    check("ancestor_labels_unique", len(old_labels) == len({r["symbol"] for r in old_labels}))
    by_symbol = {r["symbol"]: r["current_etf_classification"] for r in old_labels}
    translation = {"explicit_current_ETF_N": "N", "explicit_current_ETF_Y": "Y",
                   "current_ETF_classification_unknown": "unknown"}
    expected_panel = [{"symbol": s, "etf_classification": translation[by_symbol[s]]}
                      for s in sorted({r["symbol"] for r in old_universe})]
    check("all_panel_symbols_and_classifications_rebuilt_from_ancestors", panel == expected_panel)
    check("panel_exactly6633_unique_symbols", len(panel) == len({r["symbol"] for r in panel}) == 6633)
    check("panel_no_current_tradable_or_option_filter", len(panel) == len(old_universe))
    check("panel_hash_bound_by_protocol_and_source", digest_bytes(ROOT / "fixed-panel.json") == p["fixed_panel_sha256"] == panel_source["panel_sha256"])
    check("no_account_or_order_or_scheduler_claim", p["user_constraints"]["orders_sent"] == p["user_constraints"]["account_reads"] == 0
          and not p["operational_status"]["future_capture_automatically_connected"]
          and not p["operational_status"]["new_scheduler"])
    for name, count in (("independent-selection-tests.log", 16),
                        ("independent-protocol-tests.log", 17),
                        ("independent-capture-precision-tests.log", 4)):
        log = (ROOT / name).read_text()
        check("passing_log_" + name, bool(re.search(r"Ran " + str(count) + r" tests in [^\n]+\n\nOK\s*$", log)))

    ledger_summary = None
    if args.ledger:
        ledger = load(args.ledger)
        registration = load("protocol-registration.json")
        registered = datetime.fromisoformat(p["registered_at"])
        check("protocol_registered_before_all_future_target_dates", p["status"] == "registered" and registered < datetime(2026, 10, 8, tzinfo=ET))
        check("ledger_identity_and_count", ledger["protocol_id"] == p["id"] and ledger["planned_dates"] == len(future))
        check("ledger_asof_after_registration_before_first_date", registered <= datetime.fromisoformat(ledger["asof"]) < datetime(2026, 10, 8, tzinfo=ET))
        check("ledger_exact_dates_and_zero_future_observations", [r["date"] for r in ledger["rows"]] == future and ledger["observed_future_dates"] == 0 and ledger["actual_fills"] is False)
        check("ledger_unknown_outcomes_not_zero", all(r["status"] == "not_due" and r["sealed_selection"] is False and all(r[k] is None for k in ("observed_cases", "signals", "complete_returns", "mean_return")) for r in ledger["rows"]))
        check("registration_protocol_bytes_and_content_bound", registration["protocol_sha256"] == digest_bytes(ROOT / "protocol.json")
              and registration["protocol_content_digest"] == sha256(json.dumps(p, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest())
        check("registration_time_and_zero_observation_claim", registration["registered_at"] == p["registered_at"]
              and registration["prospective_observations_collected"] == 0 and registration["registered_not_scheduled"] is True)
        check("registration_links_preserved_pre_registration_artifacts",
              registration["draft_sha256"] == digest_bytes(ROOT / "protocol-draft-before-registration.json")
              and registration["validation_sha256"] == digest_bytes(ROOT / "pre-registration-validation.json")
              and registration["independent_pre_registration_audit_sha256"] == digest_bytes(ROOT / "independent-pre-registration-audit.json")
              and registration["independent_design_review_sha256"] == digest_bytes(ROOT / "independent-design-draft-review.json"))
        check("registered_all_method_bindings_still_exact", all(digest_bytes(ROOT / name) == value for name, value in p["bound_file_sha256"].items()))
        ledger_summary = {"file": args.ledger, "sha256": digest_bytes(ROOT / args.ledger), "rows": len(ledger["rows"]), "all_outcomes_null": True}

    bind = ["protocol.json", "fixed-panel.json", "fixed-panel-source.json", "calendar-source.json", "calendar-sessions.json",
            "prospect_selection.py", "protocol_tools.py", "capture_prior20.py", "independent_selection_tests.py", "independent_protocol_tests.py",
            "independent_capture_precision_tests.py", "independent-capture-precision-tests.log",
            "independent-selection-tests.log", "independent-protocol-tests.log", "independent_input_audit.py"]
    if args.ledger:
        bind.extend([args.ledger, "protocol-registration.json", "protocol-draft-before-registration.json", "pre-registration-validation.json", "independent-pre-registration-audit.json"])
    output = {
        "status": "passed" if all(c["passed"] for c in checks) else "failed",
        "audited_at_utc": datetime.now(timezone.utc).isoformat(),
        "bindings": {name: digest_bytes(ROOT / name) for name in bind},
        "checks": checks, "check_count": len(checks), "ledger": ledger_summary,
        "source_summary": {"calendar_raw_rows": len(raw), "planned_future_sessions": len(future),
                           "fixed_panel_symbols": len(panel), "panel_classification_counts": dict(Counter(r["etf_classification"] for r in panel))},
        "independent_test_results": {"selection_tests": 16, "protocol_tests": 17, "capture_precision_tests": 4, "random_fraction_pools": 16, "synthetic_full_pool_symbols": 6633,
                                     "synthetic_inputs_are_not_future_market_observations": True},
        "resolved_production_findings": [
            {"issue": "Unbounded finite Decimal exponents could raise Overflow or silently underflow", "resolution": "Explicit coefficient/exponent limits and wide local contexts; independently tested"},
            {"issue": "Source capture before protocol registration could be accepted", "resolution": "check_clock rejects capture before registered_at; independently tested"},
            {"issue": "Malformed bundle/receipt objects could escape as AttributeError", "resolution": "Gate returns malformed rejection; independently tested"},
            {"issue": "Parsing source JSON decimal literals into float could cross exact selection boundaries", "resolution": "Source parser preserves decimal literals; independent Fraction end-to-end precision tests"}],
        "test_fixture_corrections": [
            "Renamed an overlength synthetic symbol NEGATIVEPAIR to legal NEGPAIR before final passing run",
            "Renamed a shadowed fixture loop variable that wrongly accessed prior20_dates on a panel row"],
        "scope": "Source/calendar/panel reconstruction and receipt metadata guards. Does not authenticate logged historical delivery, certify raw future bars or prove live execution, scheduler activation, returns, or option profitability."
    }
    name = "independent-input-registered-audit.json" if args.ledger else "independent-input-draft-audit.json"
    (ROOT / name).write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": output["status"], "checks": len(checks), "output": name}))
    if output["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
