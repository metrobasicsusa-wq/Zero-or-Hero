"""Run the independent synthetic checks and bind the preregistration audit."""
from datetime import datetime, timezone
from hashlib import sha256
import importlib
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).parent


def main():
    protocol = json.loads((ROOT / "protocol.json").read_text())
    if protocol["status"] != "draft_before_validation_and_registration":
        raise SystemExit("Do not replace this audit after registration")
    runs = []
    for module, filename, expected in (
        ("independent_selection_tests", "independent-selection-tests.log", 16),
        ("independent_protocol_tests", "independent-protocol-tests.log", 17),
        ("independent_capture_precision_tests", "independent-capture-precision-tests.log", 4),
    ):
        stream = io.StringIO()
        suite = unittest.defaultTestLoader.loadTestsFromModule(importlib.import_module(module))
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
        (ROOT / filename).write_text(stream.getvalue())
        runs.append({"module": module, "tests_run": result.testsRun,
                     "passed": result.wasSuccessful() and result.testsRun == expected,
                     "failures": len(result.failures), "errors": len(result.errors),
                     "log": filename})
    source = subprocess.run([sys.executable, "independent_input_audit.py"], cwd=ROOT,
                            capture_output=True, text=True)
    source_audit = json.loads((ROOT / "independent-input-draft-audit.json").read_text())
    passed = all(r["passed"] for r in runs) and source.returncode == 0 and source_audit["status"] == "passed"
    names = ["protocol.json", "prospect_selection.py", "protocol_tools.py", "capture_prior20.py",
             "fixed-panel.json", "fixed-panel-source.json", "calendar-sessions.json", "calendar-source.json",
             "independent_selection_tests.py", "independent_protocol_tests.py", "independent_capture_precision_tests.py",
             "independent_input_audit.py", "independent-input-draft-audit.json", "independent_pre_registration_audit.py"]
    names += [r["log"] for r in runs]
    output = {
        "passed": passed, "audited_at_utc": datetime.now(timezone.utc).isoformat(),
        "tests_passed": sum(r["tests_run"] for r in runs if r["passed"]),
        "failed_tests": sum(r["failures"] + r["errors"] for r in runs), "runs": runs,
        "source_checks_passed": source_audit["status"] == "passed", "source_checks": source_audit["check_count"],
        "bound_sha256": {name: sha256((ROOT / name).read_bytes()).hexdigest() for name in names},
        "resolved_production_findings": source_audit["resolved_production_findings"],
        "test_fixture_corrections": source_audit["test_fixture_corrections"],
        "real_price_gets": 0, "account_reads": 0, "orders_sent": 0,
        "scope": "Independent synthetic selection arithmetic, receipt guards, raw JSON precision and source/calendar/panel reconstruction. No future price observations, live execution or scheduler activation claim."
    }
    (ROOT / "independent-pre-registration-audit.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"passed": passed, "independent_tests": output["tests_passed"], "source_checks": output["source_checks"]}))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
