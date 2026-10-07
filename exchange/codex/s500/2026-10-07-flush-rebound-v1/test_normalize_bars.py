"""Timestamp boundary and downstream-causality checks using synthetic inputs."""

import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from flush_engine import evaluate_prefix, family_decision
from normalize_bars import normalize_provider_bars


def bar(timestamp, value="100"):
    return {"t": timestamp, "o": value, "h": value, "l": value, "c": value,
            "v": "7.500", "n": 4, "vw": "100.00"}


def regular_bars(count=100):
    opening = datetime(2026, 10, 6, 13, 30, tzinfo=timezone.utc)
    return [bar((opening + timedelta(minutes=t)).isoformat().replace("+00:00", "Z")) for t in range(count)]


def rebound_bars():
    data = regular_bars()
    for row in data[1:]:
        row.update(o="97", h="97", l="97", c="97")
    data[30].update(h="98", c="98")
    return data


class NormalizeBarsTests(unittest.TestCase):
    def test_utc_and_eastern_same_minute(self):
        for timestamp in ("2026-10-06T13:30:00Z", "2026-10-06T09:30:00-04:00", "2026-10-06T13:30:00+00:00"):
            result = normalize_provider_bars([bar(timestamp)], "2026-10-06")
            self.assertEqual(result["bars"][0]["t"], 0)
            self.assertEqual(result["issues"], [])

    def test_winter_dst_offset(self):
        result = normalize_provider_bars([bar("2026-01-02T14:30:00Z")], "2026-01-02")
        self.assertEqual(result["bars"][0]["t"], 0)

    def test_nonzero_nanosecond_cannot_truncate(self):
        original = "2026-10-06T13:30:00.000000001Z"
        result = normalize_provider_bars([bar(original)], "2026-10-06")
        self.assertEqual(result["bars"][0]["t"], original)
        self.assertEqual(result["issues"][0]["kind"], "non_minute_timestamp")

    def test_all_fraction_precisions(self):
        for length in range(1, 10):
            with self.subTest(length=length):
                zero = normalize_provider_bars([bar("2026-10-06T13:30:00." + "0" * length + "Z")], "2026-10-06")
                nonzero = normalize_provider_bars([bar("2026-10-06T13:30:00." + "0" * (length - 1) + "1Z")], "2026-10-06")
                self.assertEqual(zero["bars"][0]["t"], 0)
                self.assertEqual(nonzero["issues"][0]["kind"], "non_minute_timestamp")

    def test_more_than_nine_fraction_digits_rejected_even_zero(self):
        result = normalize_provider_bars([bar("2026-10-06T13:30:00.0000000000Z")], "2026-10-06")
        self.assertEqual(result["issues"][0]["kind"], "invalid_rfc3339_timestamp")

    def test_invalid_offset_minute_not_normalized(self):
        for offset in ("+00:60", "-04:60", "+24:00", "+99:99"):
            result = normalize_provider_bars([bar("2026-10-06T13:30:00" + offset)], "2026-10-06")
            self.assertEqual(result["issues"][0]["kind"], "invalid_utc_offset")

    def test_unknown_negative_zero_offset_rejected(self):
        result = normalize_provider_bars([bar("2026-10-06T13:30:00-00:00")], "2026-10-06")
        self.assertEqual(result["issues"][0]["kind"], "unknown_local_offset")

    def test_naive_timezone_rejected(self):
        self.assertTrue(normalize_provider_bars([bar("2026-10-06T13:30:00")], "2026-10-06")["issues"])

    def test_seconds_must_exist_and_be_zero(self):
        for stamp in ("2026-10-06T13:30Z", "2026-10-06T13:30:01Z", "2026-10-06T13:30:59Z"):
            result = normalize_provider_bars([bar(stamp)], "2026-10-06")
            self.assertIsInstance(result["bars"][0]["t"], str)
            self.assertTrue(result["issues"])

    def test_invalid_clock_and_leap_second_fail_closed(self):
        for stamp in ("2026-10-06T24:30:00Z", "2026-10-06T13:60:00Z", "2026-10-06T13:30:60Z"):
            self.assertTrue(normalize_provider_bars([bar(stamp)], "2026-10-06")["issues"])

    def test_calendar_validation(self):
        for stamp in ("2026-02-30T13:30:00Z", "2026-13-06T13:30:00Z", "0000-10-06T13:30:00Z"):
            self.assertEqual(normalize_provider_bars([bar(stamp)], "2026-10-06")["issues"][0]["kind"], "invalid_calendar_date")

    def test_duplicate_records_preserved_and_diagnosed(self):
        data = regular_bars(3)
        data.append(bar("2026-10-06T09:31:00-04:00", "99"))
        result = normalize_provider_bars(data, "2026-10-06")
        self.assertEqual([row["t"] for row in result["bars"]], [0, 1, 2, 1])
        self.assertEqual(result["bars"][-1]["c"], "99")
        self.assertEqual(result["issues"], [{"index": 3, "kind": "duplicate_minute", "minute": 1, "first_index": 1}])

    def test_order_and_ohlcv_unchanged(self):
        data = [bar("2026-10-06T13:32:00Z"), bar("2026-10-06T13:30:00Z")]
        data[0]["v"] = Decimal("123.4500")
        before = copy.deepcopy(data)
        result = normalize_provider_bars(data, "2026-10-06")
        self.assertEqual([row["t"] for row in result["bars"]], [2, 0])
        for original, normalized in zip(data, result["bars"]):
            self.assertEqual({k: v for k, v in original.items() if k != "t"}, {k: v for k, v in normalized.items() if k != "t"})
        self.assertEqual(data, before)

    def test_no_clipping_prior_or_following_session(self):
        data = [bar("2026-10-06T13:29:00Z"), bar("2026-10-07T13:30:00Z")]
        self.assertEqual([row["t"] for row in normalize_provider_bars(data, "2026-10-06")["bars"]], [-1, 1440])

    def test_numeric_timestamp_cannot_become_valid_minute(self):
        for value in (0, 30, True, None):
            result = normalize_provider_bars([bar(value)], "2026-10-06")
            self.assertIsInstance(result["bars"][0]["t"], str)
            self.assertEqual(evaluate_prefix(result["bars"], ".02")["status"], "unknown_source")

    def test_nonmapping_record_retained_unknown(self):
        result = normalize_provider_bars([None, "unexpected"], "2026-10-06")
        self.assertEqual(len(result["bars"]), 2)
        self.assertEqual(evaluate_prefix(result["bars"], ".02")["status"], "unknown_source")

    def test_known_future_price_error_cannot_erase_signal(self):
        data = rebound_bars()
        before = family_decision(normalize_provider_bars(data, "2026-10-06")["bars"], ".02", "REBOUND")
        data[70]["c"] = "NaN"
        after = family_decision(normalize_provider_bars(data, "2026-10-06")["bars"], ".02", "REBOUND")
        self.assertEqual(before, after)
        self.assertEqual(after["signal_minute"], 30)

    def test_known_future_duplicate_cannot_erase_signal(self):
        data = rebound_bars()
        data.append(dict(data[70]))
        result = normalize_provider_bars(data, "2026-10-06")
        self.assertEqual(result["issues"][0]["kind"], "duplicate_minute")
        self.assertEqual(family_decision(result["bars"], ".02", "REBOUND")["signal_minute"], 30)

    def test_unlocatable_future_subsecond_conservatively_unknown_source(self):
        data = rebound_bars()
        data[70]["t"] = "2026-10-06T14:40:00.000000001Z"
        result = normalize_provider_bars(data, "2026-10-06")
        self.assertEqual(family_decision(result["bars"], ".02", "REBOUND")["status"], "unknown_source")

    def test_lowercase_t_and_z_permitted_by_rfc3339(self):
        self.assertEqual(normalize_provider_bars([bar("2026-10-06t13:30:00z")], "2026-10-06")["bars"][0]["t"], 0)

    def test_no_whitespace_or_compact_offset(self):
        for stamp in (" 2026-10-06T13:30:00Z", "2026-10-06T13:30:00Z\n", "2026-10-06 13:30:00Z", "2026-10-06T13:30:00+0000"):
            self.assertTrue(normalize_provider_bars([bar(stamp)], "2026-10-06")["issues"])

    def test_explicit_session_open(self):
        self.assertEqual(normalize_provider_bars([bar("2026-10-06T14:00:00Z")], "2026-10-06", "10:00")["bars"][0]["t"], 0)

    def test_invalid_session_parameters_raise(self):
        for date, clock in (("2026-02-30", "09:30"), ("2026-1-2", "09:30"), ("2026-01-02", "9:30"), ("2026-01-02", "24:00"), ("2026-03-08", "02:30"), ("2026-11-01", "01:30")):
            with self.assertRaises(ValueError):
                normalize_provider_bars([], date, clock)

    def test_non_sequence_parameters_raise(self):
        for value in (None, "bad", 7, {"t": "bad"}):
            with self.assertRaises(ValueError):
                normalize_provider_bars(value, "2026-10-06")


if __name__ == "__main__":
    unittest.main()
