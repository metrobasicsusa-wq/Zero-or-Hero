"""Bind the reviewed prospective report to the registered zero-result evidence."""
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(name):
    return json.loads((ROOT / name).read_text())


def main():
    p = read("protocol.json")
    reg = read("protocol-registration.json")
    decision = read("stage-decision.json")
    ledger = read("observation-ledger.json")
    validation = read("pre-registration-validation.json")
    peer = read("peer-exchange.json")
    registered_review = read("independent-design-registered-review.json")
    report = (ROOT / "REPORT.zh-CN.md").read_text()
    ops = (ROOT / "OPERATIONS.zh-CN.md").read_text()
    reproduce = (ROOT / "REPRODUCE.md").read_text()
    before_date = read("before-date-runtime-check.json")
    checks = []

    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})

    check("registered_protocol_hash", reg["protocol_sha256"] == sha(ROOT / "protocol.json") == registered_review["protocol_sha256"])
    check("registered_review_passed", registered_review["structural_checks_passed"] and registered_review["checks_count"] == 92)
    check("draft_tests_passed", validation["passed"] and validation["tests_passed"] == reg["tests_passed"] == 133 and validation["failed_tests"] == 0)
    testlog = (ROOT / "pre-registration-tests.log").read_text()
    check("test_log_133_success", "Ran 133 tests" in testlog and testlog.rstrip().endswith("OK"))
    for name, expected in validation["bound_sha256"].items():
        if name != "protocol.json":
            check("unchanged_validated_" + name, sha(ROOT / name) == expected)
    check("registration_time_reported", p["registered_at_et"] in report and p["registered_at"] == reg["registered_at"] == decision["registered_at"])
    check("scope_counts", decision["planned_sessions"] == ledger["planned_dates"] == len(p["sessions"]) == 59 and decision["fixed_panel_symbols"] == p["fixed_panel_symbols"] == 6633)
    check("scope_window", decision["window"] == p["window"] == ["2026-10-08", "2026-12-31"])
    check("zero_observed_future_dates", decision["observed_future_dates"] == ledger["observed_future_dates"] == reg["prospective_observations_collected"] == 0)
    check("unknown_not_zero_future_outcomes", decision["primary_future_complete_returns"] is None and decision["future_mean_return"] is None and all(all(r[k] is None for k in ("observed_cases", "signals", "complete_returns", "mean_return")) for r in ledger["rows"]))
    check("all_initial_dates_not_due", len(ledger["rows"]) == 59 and all(r["status"] == "not_due" and r["sealed_selection"] is False for r in ledger["rows"]))
    check("checkpoints_exact", decision["checkpoints"] == p["reviews"])
    with (ROOT / "planned-sessions.csv").open(newline="") as f:
        planned = list(csv.DictReader(f))
    check("planned_csv_dates_and_time_fields", len(planned) == 59 and all(all(row[k] == str(s[k]) for k in ("ordinal", "date", "open_et", "close_et", "open_utc", "close_utc", "preopen_cutoff_utc", "receipt_deadline_utc", "review_at_utc")) and row["is_checkpoint"] == str(s["ordinal"] in p["review_ordinals"]) for row, s in zip(planned, p["sessions"])))
    check("reported_review_dates", all(x in report for x in ("11月4日17:30", "12月3日17:30", "12月31日17:30")))
    check("source_review_time_distinction", all(x in report for x in ("收盘后15分钟至17:15", "离线验收和17:30复盘可以晚些运行", "原封存与资料收取已在相应窗口内完成")))
    check("fixed_panel_limit_disclosed", all(x in report for x in ("6,633", "未来新上市股票不加入本版主轨", "不能称为动态全市场扫描或完全无幸存者偏差")))
    check("one_final_endpoint_and_sample_label", all(x in report for x in ("唯一预先指定的期末主指标", "不是独立性证明、功效计算或显著性门槛", "少于2个日期不算区间")))
    check("zero_results_clearly_reported", "0 个未来日期已观察" in report and "未填零" in report)
    check("low_winrate_and_tails_preserved", decision["no_minimum_40_percent_winrate"] is True and decision["all_tail_winners_and_losses_retained"] is True and "不设40%胜率门槛，大赢家和亏损都保留" in report)
    check("no_automatic_execution", decision["new_scheduler"] is False and decision["existing_observer_connected_to_this_study"] is False and "登记不等于自动运行" in report and "没有部署日终自动采集和分析" in report)
    check("no_new_observation_claim_to_peers", peer["no_new_research_results_claimed"] is True and peer["options_returns_verified"] is False and peer["new_scheduler"] is False)
    check("operations_metadata_not_raw_proof", "元数据门控" in ops and "不能替代原始source哈希核验或分钟合法性检查" in ops and "日终整体执行器还需后续接线及真实日期验证" in ops)
    check("operations_any_failure_retained", "时间、传输和数据结构均失败时" not in ops)
    check("request_scope_counts", decision["this_stage_calendar_GET"] == 1 and decision["this_stage_market_price_GET"] == decision["account_reads"] == decision["orders_sent"] == 0 and decision["vendor_purchase"] is False)
    check("options_and_wealth_unverified", "尚未验证实际期权合约买卖价、整张合约成本、执行和500美元资金存活路径" in report)
    check("actual_cli_refused_before_date", before_date["target_date"] == "2026-10-08" and before_date["actual_exit"] == before_date["expected_exit"] == 2 and before_date["before_target_date_rejection_verified"] is True and before_date["result"]["reason"] == "capture_must_run_on_target_date")
    check("actual_cli_no_price_requests", before_date["result"]["get_count"] == before_date["market_price_requests"] == before_date["account_reads"] == before_date["orders_sent"] == 0 and before_date["result"]["accepted"] is False)
    check("actual_cli_same_bound_code", before_date["collector_sha256"] == sha(ROOT / "capture_prior20.py") and before_date["protocol_sha256"] == sha(ROOT / "protocol.json"))
    check("reproduce_test_count_breakdown", 34 + 29 + 33 + 16 + 17 + 4 == validation["tests_passed"] and "34 selection, 29 protocol, 33 capture, 16 independent selection, 17 independent protocol and 4 independent raw-JSON precision tests" in reproduce)
    check("reproduce_no_future_results", "There are zero observed future price dates and no future return simulation to reproduce" in reproduce and "They do not themselves provide a connected daily collector and analysis service" in reproduce)
    check("reproduce_raw_source_limits", "Repeating that complete source audit requires the exact ancestor bytes and saved response" in reproduce and "The public synthetic test suite does not need these private dependencies" in reproduce)
    for item in read("report-output-manifest.json")["files"]:
        check("report_manifest_" + item["name"], sha(ROOT / item["name"]) == item["sha256"])

    bound = ["REPORT.zh-CN.md", "OPERATIONS.zh-CN.md", "REPRODUCE.md", "stage-decision.json", "peer-exchange.json", "planned-sessions.csv", "report-output-manifest.json", "DESIGN_REVIEW.zh-CN.md", "DESIGN_COUNTEREXAMPLES.zh-CN.md", "protocol.json", "protocol-registration.json", "observation-ledger.json", "pre-registration-validation.json", "pre-registration-tests.log", "independent-design-registered-review.json", "runtime-audit.json", "before-date-runtime-check.json"]
    output = {
        "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
        "passed": all(c["passed"] for c in checks),
        "checks_count": len(checks), "checks": checks,
        "bound_sha256": {name: sha(ROOT / name) for name in bound},
        "reviewer_code_sha256": sha(Path(__file__)),
        "manual_review": {
            "documents_read_in_full": ["REPORT.zh-CN.md", "OPERATIONS.zh-CN.md", "REPRODUCE.md", "stage-decision.json", "peer-exchange.json"],
            "calendar_and_stats": "Dates, timezone changes, counts, one final endpoint and interim/final distinctions agree with registration.",
            "result_scope": "No future market observations or new performance conclusions are claimed. Null outcomes remain unknown.",
            "implementation_scope": "Preopen collector and metadata guards are implemented/tested; end-of-day capture/analysis is not connected or deployed.",
            "runtime_scope": "Observed workflow_dispatch successes are connection-observer records, not proof of AI wakeup, this protocol's execution, or external scheduler configuration.",
            "options_scope": "Stock proxy, low-winrate allowance and preserved tails are separated from option fillability and funded wealth.",
            "source_integrity_scope": "This audit binds documents and registered tests; it does not verify future source bodies or prove their historical receipt.",
        },
    }
    (ROOT / "independent-narrative-audit.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"passed": output["passed"], "checks_count": len(checks), "failed": [c["name"] for c in checks if not c["passed"]]}))
    if not output["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
