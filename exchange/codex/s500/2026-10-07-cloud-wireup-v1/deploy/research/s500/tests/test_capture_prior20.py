"""Offline protocol-bound collector tests. Never use a real network or secret."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.parse

import capture_prior20 as capture
import protocol_tools


class Clock:
    def __init__(self, stamp='2026-10-08T12:00:00+00:00'):
        self.time = datetime.fromisoformat(stamp)
        self.elapsed = 0.0
    def now(self):
        return self.time.isoformat()
    def monotonic(self):
        return self.elapsed
    def sleep(self, amount):
        self.elapsed += amount
        self.time += timedelta(seconds=amount)


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.clock = Clock()
        registered = json.loads((capture.ROOT / 'protocol.json').read_text())
        self.session = dict(next(s for s in registered['sessions'] if s['date'] == '2026-10-08'))
        self.day = self.session['date']
        self.calls = []
        self.configure(3)

    def configure(self, count):
        self.panel = [{'symbol': 'SPY', 'etf_classification': 'Y'},
                      {'symbol': 'QQQ', 'etf_classification': 'Y'}]
        self.panel += [{'symbol': f'A{i:04}', 'etf_classification': 'N'} for i in range(count - 2)]
        (self.root / 'fixed-panel.json').write_text(json.dumps(self.panel))
        for name in capture.REQUIRED_BINDINGS:
            if name != 'fixed-panel.json':
                (self.root / name).write_bytes((capture.ROOT / name).read_bytes())
        self.protocol = {'id': 'mock_future_protocol', 'status': 'registered',
                         'registered_at': '2026-10-07T18:00:00+00:00',
                         'fixed_panel_symbols': count,
                         'fixed_panel_content_digest': protocol_tools.digest(self.panel),
                         'sessions': [self.session],
                         'bound_file_sha256': {name: hashlib.sha256((self.root / name).read_bytes()).hexdigest()
                                               for name in capture.REQUIRED_BINDINGS}}
        self.write_protocol()

    def write_protocol(self):
        (self.root / 'protocol.json').write_text(json.dumps(self.protocol))

    def response(self, request, *, token=None, day_override=None, additions=None):
        self.calls.append((request, self.clock.elapsed))
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
        rows = []
        for day in self.session['prior20_dates']:
            rows.append({'t': (day_override or day) + 'T04:00:00Z', 'c': 10, 'v': 3000000})
        bars = {s: list(rows) for s in query['symbols'][0].split(',')}
        bars.update(additions or {})
        return 200, json.dumps({'bars': bars, 'next_page_token': token}).encode()

    def run_capture(self, transport=None, suffix='output', **kwargs):
        self.output = self.root / suffix
        return capture.capture(self.root / 'protocol.json', self.root / 'fixed-panel.json',
                               self.day, self.output, transport=transport or self.response,
                               now=self.clock.now, monotonic=self.clock.monotonic,
                               sleep=self.clock.sleep, credentials=('fake-key', 'fake-secret'), **kwargs)

    def source(self):
        return json.loads((self.output / 'source-bundle.json').read_text())

    def test_full_panel_6633_and_exact_prior_only_query(self):
        self.configure(6633)
        result = self.run_capture()
        self.assertTrue(result['accepted'])
        self.assertEqual(result['get_count'], 104)
        self.assertEqual(result['completed_batches'], 104)
        bundle = self.source()
        self.assertEqual(len(bundle['daily']), 6633)
        self.assertEqual(len(bundle['requested_symbols']), 6633)
        self.assertTrue(bundle['source_complete'])
        for index, (req, when) in enumerate(self.calls):
            self.assertEqual(req.get_method(), 'GET')
            url = urllib.parse.urlsplit(req.full_url)
            self.assertEqual(url.netloc, 'data.alpaca.markets')
            self.assertEqual(url.path, '/v2/stocks/bars')
            query = urllib.parse.parse_qs(url.query)
            self.assertEqual(query['feed'], ['sip'])
            self.assertEqual(query['adjustment'], ['raw'])
            self.assertEqual(query['timeframe'], ['1Day'])
            self.assertEqual(query['end'], ['2026-10-08T03:59:59.999999999Z'])
            self.assertLessEqual(len(query['symbols'][0].split(',')), 64)
            if index:
                self.assertGreaterEqual(when - self.calls[index - 1][1], 0.5)
        seal = json.loads((self.output / 'selection-seal.json').read_text())
        self.assertEqual(seal['receipt_count'], 104)
        self.assertEqual(seal['selection']['counts']['universe'], 6633)

    def test_late_preopen_blocks_before_credentials_or_transport(self):
        self.clock = Clock('2026-10-08T13:20:00.000001+00:00')
        with patch.object(capture.os, 'environ', {}), patch.object(capture, 'make_transport', side_effect=AssertionError):
            result = capture.capture(self.root / 'protocol.json', self.root / 'fixed-panel.json',
                                     self.day, self.root / 'late', now=self.clock.now)
        self.assertEqual(result['reason'], 'preopen_deadline_missed')
        self.assertEqual(result['get_count'], 0)

    def test_today_cannot_capture_tomorrow(self):
        self.clock = Clock('2026-10-07T17:00:00+00:00')
        result = self.run_capture(transport=lambda _: self.fail('network must not happen'))
        self.assertEqual(result['reason'], 'capture_must_run_on_target_date')
        self.assertEqual(result['get_count'], 0)

    def test_unregistered_date_is_zero_get(self):
        self.day = '2026-10-07'
        result = self.run_capture(transport=lambda _: self.fail('network must not happen'))
        self.assertEqual(result['reason'], 'date_not_in_registered_calendar')

    def test_draft_protocol_zero_get(self):
        self.protocol['status'] = 'draft'
        self.write_protocol()
        result = self.run_capture()
        self.assertEqual(result['reason'], 'protocol_not_registered')
        self.assertFalse(self.calls)

    def test_method_hash_mismatch_zero_get(self):
        (self.root / 'capture_prior20.py').write_text('different')
        result = self.run_capture()
        self.assertEqual(result['reason'], 'bound_file_hash_mismatch')
        self.assertFalse(self.calls)

    def test_missing_required_binding_zero_get(self):
        del self.protocol['bound_file_sha256']['capture_prior20.py']
        self.write_protocol()
        result = self.run_capture()
        self.assertEqual(result['reason'], 'missing_required_method_bindings')
        self.assertFalse(self.calls)

    def test_binding_path_escape_zero_get(self):
        self.protocol['bound_file_sha256']['../escape'] = '0' * 64
        self.write_protocol()
        result = self.run_capture()
        self.assertEqual(result['reason'], 'invalid_binding_path')
        self.assertFalse(self.calls)

    def test_authorization_denial_stops_all_other_batches(self):
        for status in (401, 403):
            with self.subTest(status=status):
                self.configure(130)
                calls = []
                def deny(req):
                    calls.append(req)
                    return status, b'{"message":"denied mock private body"}'
                result = self.run_capture(deny, suffix=f'denied{status}')
                self.assertEqual(result['get_count'], 1)
                self.assertEqual(result['completed_batches'], 0)
                self.assertEqual(result['reason'], 'authorization_denied_no_retry')
                self.assertFalse(self.source()['source_complete'])
                self.assertEqual(len(calls), 1)
                self.assertNotIn('denied mock private body', json.dumps(result))
                self.assertFalse((self.output / 'selection-seal.json').exists())

    def test_redirect_refused_not_retried(self):
        result = self.run_capture(lambda _: (302, b''))
        self.assertEqual(result['get_count'], 1)
        self.assertEqual(result['reason'], 'http_non_success_no_retry')
        self.assertIsNone(capture.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.invalid'))

    def test_three_attempt_cap_retains_every_body(self):
        result = self.run_capture(lambda _: (503, b'failure'))
        self.assertEqual(result['get_count'], 3)
        self.assertEqual(result['reason'], 'retry_limit_exhausted')
        self.assertEqual(len(list(self.output.glob('*.body'))), 3)
        self.assertFalse(self.source()['source_complete'])

    def test_retry_then_success_complete_receipt(self):
        attempted = []
        def recover(request):
            attempted.append(request)
            if len(attempted) == 1:
                return 429, b'retry'
            return self.response(request)
        result = self.run_capture(recover)
        self.assertTrue(result['accepted'])
        self.assertEqual(result['get_count'], 2)
        self.assertEqual(len(self.source()['raw_page_receipts']), 2)
        self.assertEqual(len(self.source()['requests']), 1)

    def test_failed_later_batch_never_ranks_partial_pool(self):
        self.configure(130)
        attempted = []
        def fail_second(request):
            attempted.append(request)
            if len(attempted) == 1:
                return self.response(request)
            return 403, b'denied'
        with patch.object(capture.protocol_tools, 'seal_selection', side_effect=AssertionError('must not rank partial pool')):
            result = self.run_capture(fail_second)
        self.assertEqual(result['get_count'], 2)
        self.assertEqual(result['completed_batches'], 1)
        self.assertFalse(self.source()['source_complete'])
        self.assertEqual(len(self.source()['requests']), 1)
        self.assertEqual(len(self.source()['daily']), 130)

    def test_batch_cap_is_checked_before_any_request(self):
        with patch.object(capture, 'MAX_BATCHES', 0):
            result = self.run_capture()
        self.assertEqual(result['reason'], 'batch_limit_exceeded')
        self.assertFalse(self.calls)

    def test_distinct_pages_merge_and_hash_all_source_bytes(self):
        seen = []
        def paginated(req):
            seen.append(req)
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(req.full_url).query)
            symbols = query['symbols'][0].split(',')
            prior = self.session['prior20_dates'][:10] if 'page_token' not in query else self.session['prior20_dates'][10:]
            return 200, json.dumps({'bars': {s: [{'t': d + 'T04:00:00Z', 'c': 10, 'v': 3000000} for d in prior] for s in symbols},
                                    'next_page_token': 'second' if 'page_token' not in query else None}).encode()
        result = self.run_capture(paginated)
        self.assertTrue(result['accepted'])
        self.assertEqual(result['get_count'], 2)
        source = self.source()
        self.assertTrue(all(len(rows) == 20 for rows in source['daily'].values()))
        receipt = source['requests'][0]
        self.assertEqual(receipt['body_sha256'], hashlib.sha256((self.output / receipt['merged_normalized_body_file']).read_bytes()).hexdigest())
        for receipt in source['raw_page_receipts']:
            self.assertEqual(receipt['body_sha256'], hashlib.sha256((self.output / receipt['body_file']).read_bytes()).hexdigest())

    def test_repeated_page_token_stops_without_rank(self):
        result = self.run_capture(lambda req: self.response(req, token='repeat'))
        self.assertEqual(result['get_count'], 2)
        self.assertEqual(result['reason'], 'repeated_pagination_token')
        self.assertFalse(self.source()['source_complete'])
        self.assertFalse((self.output / 'selection-seal.json').exists())
        self.assertEqual(len(self.source()['daily']['A0000']), 40)

    def test_unknown_symbol_retained_and_seal_rejected(self):
        result = self.run_capture(lambda req: self.response(req, additions={'UNEXPECTED': []}))
        self.assertFalse(result['accepted'])
        self.assertEqual(result['reason'], 'daily_symbol_keys_not_full_panel')
        self.assertIn('UNEXPECTED', self.source()['daily'])

    def test_duplicate_dates_retained_and_seal_rejected(self):
        result = self.run_capture(lambda req: self.response(req, day_override=self.session['prior20_dates'][-1]))
        self.assertFalse(result['accepted'])
        self.assertEqual(result['reason'], 'selection_blocked_invalid_inputs')
        self.assertEqual(len(self.source()['daily']['A0000']), 20)

    def test_future_daily_bar_retained_and_seal_rejected(self):
        result = self.run_capture(lambda req: self.response(req, day_override=self.day))
        self.assertFalse(result['accepted'])
        self.assertEqual(result['reason'], 'selection_blocked_invalid_inputs')
        self.assertEqual(self.source()['daily']['A0000'][0]['session_date'], self.day)

    def test_empty_success_includes_every_requested_symbol(self):
        result = self.run_capture(lambda _: (200, b'{"bars":{},"next_page_token":null}'))
        self.assertTrue(result['accepted'])
        self.assertEqual(set(self.source()['daily']), {s['symbol'] for s in self.panel})
        seal = json.loads((self.output / 'selection-seal.json').read_text())
        self.assertEqual(seal['selection']['selected_symbols'], ['QQQ', 'SPY'])
        self.assertEqual(seal['selection']['counts']['eligible_stocks'], 0)

    def test_late_response_preserved_but_cannot_seal(self):
        self.clock = Clock('2026-10-08T13:19:59+00:00')
        def cross_deadline(request):
            result = self.response(request)
            self.clock.sleep(2)
            return result
        result = self.run_capture(cross_deadline)
        self.assertEqual(result['reason'], 'preopen_deadline_missed')
        self.assertEqual(len(list(self.output.glob('*.body'))), 1)
        self.assertFalse(self.source()['source_complete'])
        self.assertFalse((self.output / 'selection-seal.json').exists())

    def test_existing_output_never_overwritten(self):
        self.assertTrue(self.run_capture()['accepted'])
        original = (self.output / 'capture-manifest.json').read_bytes()
        result = self.run_capture(transport=lambda _: self.fail('no duplicate request'))
        self.assertEqual(result['reason'], 'output_directory_already_exists')
        self.assertEqual((self.output / 'capture-manifest.json').read_bytes(), original)

    def test_private_output_modes_and_no_credentials_in_receipts(self):
        result = self.run_capture()
        self.assertTrue(result['accepted'])
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o700)
        for path in self.output.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            if path.suffix == '.json':
                body = path.read_text()
                self.assertNotIn('fake-key', body)
                self.assertNotIn('fake-secret', body)

    def test_daily_timestamp_uses_ny_session_date(self):
        bar = capture.normalize_bar({'t': '2026-10-08T00:00:00Z', 'c': 10, 'v': 1})
        self.assertEqual(bar['session_date'], '2026-10-07')
        bar = capture.normalize_bar({'t': '2026-10-08T04:00:00Z', 'c': 10, 'v': 1})
        self.assertEqual(bar['session_date'], '2026-10-08')
        self.assertIsNone(capture.normalize_bar({'t': '2026-10-08T04:00:00'})['session_date'])

    def test_known_symbol_from_wrong_batch_blocks_pool(self):
        self.configure(130)
        result = self.run_capture(lambda req: self.response(req, additions={'A0127': []}))
        self.assertEqual(result['reason'], 'response_symbol_outside_requested_batch')
        self.assertFalse(self.source()['source_complete'])
        self.assertEqual(result['get_count'], 1)

    def test_page_and_total_get_limits_are_enforced(self):
        counter = [0]
        def forever(request):
            counter[0] += 1
            return self.response(request, token='page' + str(counter[0]))
        result = self.run_capture(forever)
        self.assertEqual(result['get_count'], 20)
        self.assertEqual(result['reason'], 'page_limit_exceeded')
        with patch.object(capture, 'MAX_GETS', 2):
            result = self.run_capture(forever, suffix='limited')
        self.assertEqual(result['get_count'], 2)
        self.assertEqual(result['reason'], 'total_get_limit_exceeded')

    def test_invalid_response_body_not_hidden_as_empty_success(self):
        for body in (b'not JSON', b'{"bars":null}', b'{"bars":{"A0000":null}}'):
            with self.subTest(body=body):
                result = self.run_capture(lambda _: (200, body), suffix='bad' + hashlib.sha256(body).hexdigest()[:8])
                self.assertFalse(result['accepted'])
                self.assertFalse(self.source()['source_complete'])

    def test_duplicate_json_symbol_keys_not_silently_dropped(self):
        body = b'{"bars":{"A0000":[],"A0000":[]},"next_page_token":null}'
        result = self.run_capture(lambda _: (200, body))
        self.assertEqual(result['reason'], 'duplicate_json_key_in_response')
        self.assertFalse(result['accepted'])
        self.assertFalse(self.source()['source_complete'])
        self.assertEqual(next(self.output.glob('*.body')).read_bytes(), body)

    def test_nonfinite_source_values_never_break_failure_manifest(self):
        for value in ('NaN', 'Infinity', '-Infinity'):
            body = ('{"bars":{"A0000":[{"t":"2026-10-07T04:00:00Z","c":'
                    + value + ',"v":1}]}}').encode()
            result = self.run_capture(lambda _: (200, body), suffix='nonfinite' + value)
            self.assertEqual(result['reason'], 'nonfinite_number_in_response')
            self.assertFalse(self.source()['source_complete'])

    def test_numeric_encoding_bounds_match_selector(self):
        for value in ('1e101', '1e-101', '1e9999999999999999999999999', '9' * 81):
            body = ('{"bars":{"A0000":[{"t":"2026-10-07T04:00:00Z","c":'
                    + value + ',"v":1}]}}').encode()
            result = self.run_capture(lambda _: (200, body), suffix='encoding' + value[:12])
            self.assertEqual(result['reason'], 'numeric_encoding_outside_bounds')
            self.assertFalse(self.source()['source_complete'])
        for value in ('1e100', '1e-100', '9' * 80):
            parsed = capture.strict_source_json(('{"value":' + value + '}').encode())
            self.assertEqual(Decimal(str(parsed['value'])), Decimal(value))

    def test_exact_json_numbers_on_both_sides_of_thresholds(self):
        examples = [
            ('closebelow', '4.99999999999999999999', '4000000.0', False, 'prior_close_below_5'),
            ('closeabove', '5.00000000000000000001', '4000000.0', True, None),
            ('volumebelow', '10.0', '1999999.99999999999999', False, 'prior20_median_dollar_volume_below_20m'),
            ('volumeabove', '10.0', '2000000.00000000000001', True, None),
        ]
        for name, close, volume, eligible, exclusion in examples:
            with self.subTest(name=name):
                bars = ','.join('{"t":"' + day + 'T04:00:00Z","c":'
                                + close + ',"v":' + volume + '}'
                                for day in self.session['prior20_dates'])
                body = ('{"bars":{"A0000":[' + bars + ']},"next_page_token":null}').encode()
                result = self.run_capture(lambda _: (200, body), suffix=name)
                self.assertTrue(result['accepted'])
                source = self.source()
                self.assertEqual(source['daily']['A0000'][0]['c'], close)
                self.assertEqual(source['daily']['A0000'][0]['v'], volume)
                self.assertEqual(next(self.output.glob('*.body')).read_bytes(), body)
                seal = json.loads((self.output / 'selection-seal.json').read_text())
                selected = seal['selection']
                self.assertEqual('A0000' in selected['eligible_symbols'], eligible)
                self.assertEqual(selected['metrics']['A0000']['prior_close'], close)
                if exclusion:
                    self.assertIn('A0000', selected['excluded_by_reason'][exclusion])
                self.assertEqual(seal['source_bundle_digest'], protocol_tools.digest(source))

    def test_numeric_pagination_token_is_not_coerced_to_string(self):
        result = self.run_capture(lambda _: (200, b'{"bars":{},"next_page_token":0.123456789012345678901}'))
        self.assertEqual(result['reason'], 'malformed_pagination_token')
        self.assertEqual(result['get_count'], 1)
        self.assertFalse(self.source()['source_complete'])

    def test_encoder_only_converts_decimal_and_rejects_other_objects(self):
        encoded = capture.canonical_bytes({'c': Decimal('4.99999999999999999999')})
        self.assertEqual(json.loads(encoded)['c'], '4.99999999999999999999')
        with self.assertRaises(TypeError):
            capture.canonical_bytes({'invalid': object()})
        with self.assertRaises(capture.CaptureError):
            capture.canonical_bytes({'invalid': Decimal('NaN')})


if __name__ == '__main__':
    unittest.main()
