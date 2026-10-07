"""Independent raw-JSON to selector precision checks; no transport or credentials."""
from fractions import Fraction
import unittest

from capture_prior20 import strict_source_json, normalize_bar, CaptureError
from prospect_selection import selection


DAYS = [f"2026-09-{i:02d}" for i in range(1, 21)]


def raw_bars(close_literal, volume_literal):
    records = [('{"t":"' + d + 'T04:00:00Z","c":' + close_literal + ',"v":' + volume_literal + '}') for d in DAYS]
    return ('{"bars":{"AAA":[' + ','.join(records) + ']},"next_page_token":null}').encode()


def parsed_selection(close_literal, volume_literal):
    parsed = strict_source_json(raw_bars(close_literal, volume_literal))
    daily = {"AAA": [normalize_bar(row) for row in parsed["bars"]["AAA"]]}
    result = selection("2026-10-08", DAYS, [{"symbol": "AAA", "etf_classification": "N"}], daily)
    return parsed, result


class IndependentRawPrecisionTests(unittest.TestCase):
    def test_just_below_five_raw_number_not_rounded_eligible(self):
        literal = "4.9999999999999999999999999999"
        parsed, result = parsed_selection(literal, "5000000")
        self.assertEqual(Fraction(str(parsed["bars"]["AAA"][0]["c"])), Fraction(literal))
        self.assertEqual(result["eligible_symbols"], [])
        self.assertEqual(result["excluded_by_reason"]["prior_close_below_5"], ["AAA"])

    def test_liquidity_just_below_exact_threshold_not_rounded_up(self):
        volume = "3999999.9999999999999999999999"
        parsed, result = parsed_selection("5", volume)
        self.assertEqual(Fraction(str(parsed["bars"]["AAA"][0]["v"])), Fraction(volume))
        self.assertEqual(result["eligible_symbols"], [])
        self.assertEqual(Fraction(result["metrics"]["AAA"]["median_prior20_daily_close_times_volume"]), 5 * Fraction(volume))

    def test_exponent_and_long_fraction_survive_source_parsing(self):
        for close, volume in [("5e0", "4e6"), ("5.0000000000000000000000000001", "4000000.0000000000000000000001")]:
            with self.subTest(close=close, volume=volume):
                parsed, result = parsed_selection(close, volume)
                self.assertEqual(Fraction(str(parsed["bars"]["AAA"][0]["c"])), Fraction(close))
                self.assertEqual(result["eligible_symbols"], ["AAA"])
                self.assertEqual(Fraction(result["metrics"]["AAA"]["median_prior20_daily_close_times_volume"]), Fraction(close) * Fraction(volume))

    def test_json_nonfinite_constants_and_duplicate_keys_rejected(self):
        for value in (b'NaN', b'Infinity', b'-Infinity'):
            with self.subTest(value=value):
                with self.assertRaises(CaptureError): strict_source_json(b'{"bars":{"AAA":[{"c":' + value + b'}]}}')
        with self.assertRaises(CaptureError): strict_source_json(b'{"bars":{"AAA":[],"AAA":[]}}')


if __name__ == "__main__":
    unittest.main()
