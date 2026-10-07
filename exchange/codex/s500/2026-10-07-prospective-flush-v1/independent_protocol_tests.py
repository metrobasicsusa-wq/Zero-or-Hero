"""Independent adversarial timing/receipt tests. Every quote here is synthetic."""
import copy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

import protocol_tools as gate

ROOT = Path(__file__).parent
ET = ZoneInfo("America/New_York")


def canonical(obj):
    return sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False).encode()).hexdigest()


def iso(day, clock):
    return datetime.fromisoformat(day + "T" + clock).replace(tzinfo=ET).astimezone(timezone.utc).isoformat()


def independent_fixture(day="2026-10-08"):
    p = json.loads((ROOT / "protocol.json").read_text())
    p["status"] = "registered"
    p["registered_at"] = "2026-10-07T17:00:00+00:00"
    panel = [{"symbol": "ONE", "etf_classification": "N"},
             {"symbol": "TWO", "etf_classification": "unknown"},
             {"symbol": "ETF", "etf_classification": "Y"}]
    p["fixed_panel_symbols"] = len(panel)
    p["fixed_panel_content_digest"] = canonical(panel)
    s = next(s for s in p["sessions"] if s["date"] == day)
    daily = {item["symbol"]: [{"session_date": d, "c": "5", "v": "4000000"}
                             for d in s["prior20_dates"]] for item in panel}
    records = []
    for symbols in (["ONE"], ["TWO", "ETF"]):
        records.append({"requested_symbols": symbols, "requested_at": iso(day, "08:00"),
                        "received_at": iso(day, "08:03"), "http_status": 200,
                        "complete": True, "body_sha256": "c" * 64})
    b = {"protocol_id": p["id"], "date": day, "panel_content_digest": canonical(panel),
         "prior20_dates": s["prior20_dates"], "feed": "sip", "adjustment": "raw", "timeframe": "1Day",
         "source_complete": True, "requested_symbols": [s["symbol"] for s in panel],
         "daily": daily, "requests": records}
    return p, panel, b


def make_seal(p, panel, bundle):
    return gate.seal_selection(p, bundle["date"], panel, bundle, iso(bundle["date"], "09:00"))


def postclose(p, seal):
    day = seal["date"]
    s = next(s for s in p["sessions"] if s["date"] == day)
    lower = (datetime.fromisoformat(s["close_utc"]) + timedelta(minutes=15)).isoformat()
    return {"requested_at": lower, "received_at": iso(day, "17:00"),
            "source_complete": True, "http_status": 200, "body_sha256": "d" * 64,
            "requested_symbols": seal["selection"]["selected_symbols"][:],
            "feed": "sip", "adjustment": "raw", "timeframe": "1Min", "session_date": day,
            "bar_window_start_utc": s["open_utc"], "bar_window_end_exclusive_utc": s["close_utc"]}


class IndependentReceiptTests(unittest.TestCase):
    def check_post(self, p, seal, r, now=None):
        return gate.validate_postclose(p, seal["date"], seal, r, now or iso(seal["date"], "17:30"))

    def test_full_panel_two_disjoint_batches_accepted(self):
        p, panel, b = independent_fixture()
        result = make_seal(p, panel, b)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["selection"]["selected_symbols"], ["ONE", "QQQ", "SPY", "TWO"])
        self.assertEqual(result["seal_content_digest"], canonical({k: v for k, v in result.items() if k != "seal_content_digest"}))
        self.assertFalse(result["actual_fill"])

    def test_missing_daily_history_different_from_missing_batch(self):
        p, panel, b = independent_fixture()
        b["daily"]["TWO"] = []
        result = make_seal(p, panel, b)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["selection"]["eligible_symbols"], ["ONE"])
        b["requests"][1]["requested_symbols"] = ["ETF"]
        self.assertFalse(make_seal(p, panel, b)["accepted"])

    def test_duplicate_batch_omitted_etf_and_nonfinal_batch_rejected(self):
        for change in ("duplicate", "omitETF", "incomplete"):
            with self.subTest(change=change):
                p, panel, b = independent_fixture()
                if change == "duplicate": b["requests"].append(copy.deepcopy(b["requests"][0]))
                elif change == "omitETF": b["requests"][1]["requested_symbols"].remove("ETF")
                else: b["requests"][1]["complete"] = False
                self.assertFalse(make_seal(p, panel, b)["accepted"])

    def test_registration_must_precede_request_and_seal(self):
        p, panel, b = independent_fixture()
        p["registered_at"] = iso(b["date"], "08:02")
        self.assertFalse(make_seal(p, panel, b)["accepted"])
        p["registered_at"] = iso(b["date"], "09:01")
        self.assertFalse(make_seal(p, panel, b)["accepted"])

    def test_timezone_equivalent_cutoff_accepts_exact_microsecond_after_rejects(self):
        p, panel, b = independent_fixture()
        at = "2026-10-08T09:20:00-04:00"
        self.assertTrue(gate.seal_selection(p, b["date"], panel, b, at)["accepted"])
        self.assertFalse(gate.seal_selection(p, b["date"], panel, b, at.replace("00-04", "00.000001-04"))["accepted"])

    def test_real_first_day_cannot_be_sealed_on_registration_day(self):
        p, panel, b = independent_fixture()
        self.assertFalse(gate.seal_selection(p, b["date"], panel, b, "2026-10-07T18:00:00Z")["accepted"])

    def test_postclose_review_later_day_permitted_without_extending_receipt_deadline(self):
        p, panel, b = independent_fixture()
        seal = make_seal(p, panel, b); r = postclose(p, seal)
        self.assertTrue(self.check_post(p, seal, r, "2026-10-20T23:00:00Z")["accepted"])
        r["received_at"] = iso(b["date"], "17:15:00.000001")
        self.assertFalse(self.check_post(p, seal, r, "2026-10-20T23:00:00Z")["accepted"])

    def test_request_before_postclose_window_rejected_even_if_arrival_valid(self):
        p, panel, b = independent_fixture()
        seal = make_seal(p, panel, b); r = postclose(p, seal)
        r["requested_at"] = iso(b["date"], "16:14:59.999999")
        self.assertFalse(self.check_post(p, seal, r)["accepted"])
        r["requested_at"] = iso(b["date"], "16:15:00")
        self.assertTrue(self.check_post(p, seal, r)["accepted"])

    def test_both_early_closes_respect_1315_capture(self):
        for day in ("2026-11-27", "2026-12-24"):
            with self.subTest(day=day):
                p, panel, b = independent_fixture(day)
                seal = make_seal(p, panel, b); r = postclose(p, seal)
                self.assertEqual(r["requested_at"], iso(day, "13:15"))
                self.assertTrue(self.check_post(p, seal, r)["accepted"])
                r["requested_at"] = iso(day, "13:14:59.999999")
                self.assertFalse(self.check_post(p, seal, r)["accepted"])

    def test_dst_changes_utc_cutoff_without_moving_local_cutoff(self):
        for day, utc_hour in (("2026-10-30", 13), ("2026-11-02", 14)):
            with self.subTest(day=day):
                p, panel, b = independent_fixture(day)
                result = gate.seal_selection(p, day, panel, b, iso(day, "09:20"))
                self.assertTrue(result["accepted"])
                self.assertEqual(datetime.fromisoformat(result["sealed_at"]).hour, utc_hour)

    def test_instrument_panel_changes_cannot_be_hidden_by_new_bundle_hash(self):
        p, panel, b = independent_fixture()
        panel[0]["etf_classification"] = "Y"
        b["panel_content_digest"] = canonical(panel)
        self.assertFalse(make_seal(p, panel, b)["accepted"])

    def test_extra_or_missing_daily_symbol_not_full_panel(self):
        for add in (False, True):
            p, panel, b = independent_fixture()
            if add: b["daily"]["OUTSIDE"] = []
            else: del b["daily"]["ETF"]
            self.assertFalse(make_seal(p, panel, b)["accepted"])

    def test_protocol_or_seal_mutation_invalidates_postclose(self):
        p, panel, b = independent_fixture()
        seal = make_seal(p, panel, b); r = postclose(p, seal)
        changed = copy.deepcopy(seal)
        changed["selection"]["selected_symbols"] = ["ONE"]
        r2 = copy.deepcopy(r); r2["requested_symbols"] = ["ONE"]
        self.assertFalse(self.check_post(p, changed, r2)["accepted"])
        p["primary_variant"][-1] = 5
        self.assertFalse(self.check_post(p, seal, r)["accepted"])

    def test_bad_hash_failed_http_or_non_sip_never_accepted(self):
        changes = {"body_sha256": "f" * 63 + "G", "http_status": 403,
                   "feed": "iex", "timeframe": "5Min", "adjustment": "all", "source_complete": 1}
        for field, value in changes.items():
            with self.subTest(field=field):
                p, panel, b = independent_fixture()
                seal = make_seal(p, panel, b); r = postclose(p, seal)
                r[field] = value
                self.assertFalse(self.check_post(p, seal, r)["accepted"])

    def test_malformed_bundle_rows_and_receipts_return_rejection(self):
        p, panel, b = independent_fixture()
        for malformed in (None, [], "bad"):
            with self.subTest(malformed=malformed):
                self.assertFalse(gate.seal_selection(p, b["date"], panel, malformed, iso(b["date"], "09:00"))["accepted"])
        b["requests"] = [None]
        self.assertFalse(make_seal(p, panel, b)["accepted"])
        p, panel, b = independent_fixture(); seal = make_seal(p, panel, b)
        self.assertFalse(self.check_post(p, seal, None)["accepted"])

    def test_empty_ledger_contains_no_invented_outcomes_and_all_review_dates(self):
        p, _, _ = independent_fixture()
        initial = gate.initial_ledger(p, "2026-10-07T17:00:00Z")
        self.assertEqual(initial["observed_future_dates"], 0)
        self.assertEqual(len(initial["rows"]), 59)
        self.assertEqual([r["date"] for r in initial["rows"] if r["is_review_date"]],
                         ["2026-11-04", "2026-12-03", "2026-12-31"])
        for row in initial["rows"]:
            self.assertEqual(row["status"], "not_due")
            for field in ("observed_cases", "signals", "complete_returns", "mean_return"):
                self.assertIsNone(row[field])
        missed = gate.initial_ledger(p, "2027-01-01T05:00:00Z")
        self.assertEqual(len(missed["rows"]), 59)
        self.assertTrue(all(r["status"] == "missing_preopen_seal" for r in missed["rows"]))

    def test_exclusive_write_keeps_original_bytes(self):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "seal.json"
            gate.write_once(path, {"first": True})
            original = path.read_bytes()
            with self.assertRaises(FileExistsError): gate.write_once(path, {"first": False})
            self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
