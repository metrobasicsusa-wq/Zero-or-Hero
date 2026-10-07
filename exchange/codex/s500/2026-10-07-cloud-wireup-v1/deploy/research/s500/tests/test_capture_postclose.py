"""Offline boundary, provenance, timing and raw-preservation capture checks."""
import copy
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import urllib.error

MODULE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE))
import capture_postclose as cp
import protocol_tools


class PostcloseCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protocol = json.loads((MODULE / 'protocol.json').read_text())
        cls.panel = json.loads((MODULE / 'fixed-panel.json').read_text())
        cls.cache = {}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.requests, self.sleeps = [], []
        self.clock = 0.0
        self.day = '2026-10-08'

    def tearDown(self):
        self.temp.cleanup()

    def monotonic(self):
        return self.clock

    def sleep(self, delay):
        self.sleeps.append(delay)
        self.clock += delay

    @classmethod
    def fixture(cls, day):
        if day in cls.cache:
            return cls.cache[day]
        s = protocol_tools.session(cls.protocol, day)
        stamp = (protocol_tools.timestamp(s['preopen_cutoff_utc']) - timedelta(minutes=5)).isoformat()
        symbols = [row['symbol'] for row in cls.panel]
        daily = {symbol: [] for symbol in symbols}
        daily['AAPL'] = [{'session_date': date, 'c': '100', 'v': '300000'}
                         for date in s['prior20_dates']]
        bundle = {'protocol_id': cls.protocol['id'], 'date': day,
                  'panel_content_digest': protocol_tools.digest(cls.panel),
                  'prior20_dates': s['prior20_dates'], 'feed': 'sip', 'adjustment': 'raw',
                  'timeframe': '1Day', 'source_complete': True,
                  'requested_symbols': symbols, 'daily': daily,
                  'requests': [{'http_status': 200, 'complete': True,
                                'requested_at': stamp, 'received_at': stamp,
                                'body_sha256': '1' * 64, 'requested_symbols': symbols}]}
        seal = protocol_tools.seal_selection(cls.protocol, day, cls.panel, bundle, stamp)
        assert seal['accepted'], seal['reason']
        cls.cache[day] = bundle, seal
        return bundle, seal

    def actual_seal_files(self, day=None):
        day = day or self.day
        bundle, seal = self.fixture(day)
        path = self.root / 'preopen'
        path.mkdir(exist_ok=True)
        (path / 'source-bundle.json').write_text(json.dumps(bundle))
        (path / 'selection-seal.json').write_text(json.dumps(seal))
        return path / 'selection-seal.json'

    def run_capture(self, responses=None, at=None, day=None, real_load=False, now=None):
        day = day or self.day
        at = at or (protocol_tools.timestamp(protocol_tools.session(self.protocol, day)['close_utc']) + timedelta(minutes=20)).isoformat()
        _, seal = self.fixture(day)
        if responses is None:
            responses = [(200, b'{"bars":{},"next_page_token":null}')]
        stream = iter(responses)
        def transport(request):
            self.requests.append(request)
            value = next(stream)
            if isinstance(value, Exception):
                raise value
            return value
        args = dict(transport=transport, now=now or (lambda: at), monotonic=self.monotonic,
                    sleep=self.sleep, credentials=('offline_key', 'offline_secret'))
        if real_load:
            return cp.capture(MODULE / 'protocol.json', self.actual_seal_files(day), day,
                              self.root / 'output', **args)
        with patch.object(cp, 'load_sealed_selection', return_value=(self.protocol, seal, seal['selection']['selected_symbols'])):
            return cp.capture(MODULE / 'protocol.json', self.root / 'unused', day,
                              self.root / 'output', **args)

    def read(self, filename):
        return json.loads((self.root / 'output' / filename).read_text())

    def test_real_original_bindings_and_seal_recomputed(self):
        self.assertNotIn('SPY', {row['symbol'] for row in self.panel})
        result = self.run_capture(real_load=True)
        self.assertTrue(result['accepted'], result)
        receipt = self.read('postclose-receipt.json')
        source = (self.root / 'output' / 'normalized-source.json').read_bytes()
        self.assertEqual(receipt['body_sha256'], hashlib.sha256(source).hexdigest())
        self.assertEqual(receipt['requested_symbols'], ['AAPL', 'QQQ', 'SPY'])
        self.assertTrue(self.read('capture-manifest.json')['validation']['accepted'])

    def test_missing_minutes_and_symbols_not_retried(self):
        result = self.run_capture([(200, b'{"bars":{"AAPL":[{"t":"2026-10-08T13:31:00Z","c":100}]}}')])
        self.assertTrue(result['accepted'])
        self.assertEqual(result['get_count'], 1)
        source = self.read('normalized-source.json')
        self.assertEqual(source['bars']['QQQ'], [])
        self.assertEqual(source['bars']['SPY'], [])
        self.assertEqual(len(source['bars']['AAPL']), 1)
        self.assertFalse(self.read('capture-manifest.json')['minute_completeness_claimed'])

    def test_timestamp_nanoseconds_duplicates_invalid_rows_preserved(self):
        body = b'{"bars":{"AAPL":[{"t":"2026-10-08T13:30:00.000000001Z","c":100.00000000000000001},{"t":"x"},{"t":"x"},null]}}'
        result = self.run_capture([(200, body)])
        self.assertTrue(result['accepted'])
        rows = self.read('normalized-source.json')['bars']['AAPL']
        self.assertEqual(rows[0]['t'], '2026-10-08T13:30:00.000000001Z')
        self.assertEqual(rows[0]['c'], '100.00000000000000001')
        self.assertNotIn('session_date', rows[0])
        self.assertEqual(rows[1:],[{'t': 'x'}, {'t': 'x'}, None])
        self.assertEqual((self.root / 'output' / 'page-00-attempt-1.body').read_bytes(), body)

    def test_exact_endpoint_get_and_session_window(self):
        self.run_capture()
        request = self.requests[0]
        self.assertEqual(request.get_method(), 'GET')
        url = urlsplit(request.full_url)
        self.assertEqual(url.scheme + '://' + url.netloc + url.path, cp.ENDPOINT)
        params = parse_qs(url.query)
        self.assertEqual(params['timeframe'], ['1Min'])
        self.assertEqual(params['feed'], ['sip'])
        self.assertEqual(params['adjustment'], ['raw'])
        self.assertEqual(params['start'], ['2026-10-08T13:30:00Z'])
        self.assertEqual(params['end'], ['2026-10-08T19:59:59.999999999Z'])
        self.assertNotIn('offline_key', request.full_url)
        self.assertNotIn('offline_secret', request.full_url)

    def test_standard_time_after_dst(self):
        result = self.run_capture(day='2026-11-02')
        self.assertTrue(result['accepted'])
        params = parse_qs(urlsplit(self.requests[0].full_url).query)
        self.assertEqual(params['start'], ['2026-11-02T14:30:00Z'])
        self.assertEqual(params['end'], ['2026-11-02T20:59:59.999999999Z'])

    def test_early_close_uses_registered_close_not_four_pm(self):
        result = self.run_capture(day='2026-11-27', at='2026-11-27T18:15:00+00:00')
        self.assertTrue(result['accepted'])
        params = parse_qs(urlsplit(self.requests[0].full_url).query)
        self.assertEqual(params['end'], ['2026-11-27T17:59:59.999999999Z'])

    def test_before_close_plus15_no_get(self):
        result = self.run_capture(at='2026-10-08T20:14:59+00:00')
        self.assertEqual(result['reason'], 'postclose_capture_outside_window')
        self.assertEqual(result['get_count'], 0)

    def test_after_1715_no_get(self):
        result = self.run_capture(at='2026-10-08T21:15:00.000001+00:00')
        self.assertEqual(result['reason'], 'postclose_capture_outside_window')
        self.assertEqual(result['get_count'], 0)

    def test_next_day_no_get(self):
        result = self.run_capture(at='2026-10-09T20:20:00+00:00')
        self.assertEqual(result['reason'], 'capture_must_run_on_target_date')
        self.assertEqual(result['get_count'], 0)

    def test_late_response_saved_before_rejection(self):
        stamps = iter(['2026-10-08T21:14:59+00:00'] * 3 + ['2026-10-08T21:15:01+00:00'])
        result = self.run_capture(now=lambda: next(stamps))
        self.assertFalse(result['accepted'])
        self.assertEqual(result['reason'], 'postclose_capture_outside_window')
        self.assertTrue((self.root / 'output' / 'page-00-attempt-1.body').exists())
        self.assertFalse(self.read('normalized-source.json')['source_complete'])

    def test_response_clock_regression_preserved_and_rejected(self):
        stamps = iter(['2026-10-08T20:20:01+00:00'] * 3 + ['2026-10-08T20:20:00+00:00'])
        result = self.run_capture(now=lambda: next(stamps))
        self.assertEqual(result['reason'], 'receipt_clock_regressed')

    def test_final_gate_after_parsing(self):
        stamps = iter(['2026-10-08T21:14:59+00:00'] * 4 + ['2026-10-08T21:15:01+00:00'])
        result = self.run_capture(now=lambda: next(stamps))
        self.assertFalse(result['accepted'])
        self.assertFalse(self.read('normalized-source.json')['source_complete'])

    def test_authorization_denied_401_no_retry(self):
        result = self.run_capture([(401, b'private authorization failure')])
        self.assertEqual(result['reason'], 'authorization_denied_no_retry')
        self.assertEqual(result['get_count'], 1)
        self.assertFalse(self.read('postclose-receipt.json')['source_complete'])

    def test_authorization_denied_403_no_retry(self):
        result = self.run_capture([(403, b'private authorization failure')])
        self.assertEqual(result['reason'], 'authorization_denied_no_retry')
        self.assertEqual(result['get_count'], 1)

    def test_redirect_status_no_retry(self):
        result = self.run_capture([(302, b'redirect')])
        self.assertEqual(result['reason'], 'http_non_success_no_retry')
        self.assertEqual(result['get_count'], 1)

    def test_retry_transport_429_then_success_all_evidence(self):
        result = self.run_capture([urllib.error.URLError('private error'), (429, b'busy'),
                                   (200, b'{"bars":{}}')])
        self.assertTrue(result['accepted'])
        self.assertEqual(result['get_count'], 3)
        self.assertEqual(self.sleeps, [0.5, 0.5])
        pages = self.read('capture-manifest.json')['page_receipts']
        self.assertEqual([p['http_status'] for p in pages], [None, 429, 200])
        self.assertEqual([p['transport_error'] for p in pages], [True, False, False])

    def test_retry_exhaustion(self):
        result = self.run_capture([(500, b'a'), (502, b'b'), (503, b'c')])
        self.assertEqual(result['reason'], 'retry_limit_exhausted')
        self.assertEqual(result['get_count'], 3)

    def test_pagination_merge_order_and_request_token(self):
        result = self.run_capture([(200, b'{"bars":{"AAPL":[{"t":"first"}]},"next_page_token":"abc"}'),
                                   (200, b'{"bars":{"AAPL":[{"t":"second"}]},"next_page_token":null}')])
        self.assertTrue(result['accepted'])
        self.assertEqual(self.read('normalized-source.json')['bars']['AAPL'], [{'t':'first'}, {'t':'second'}])
        self.assertEqual(parse_qs(urlsplit(self.requests[1].full_url).query)['page_token'], ['abc'])
        self.assertNotIn('page_token', self.read('capture-manifest.json')['page_receipts'][1]['parameters'])

    def test_repeated_token_fails_retaining_prior_pages(self):
        result = self.run_capture([(200, b'{"bars":{},"next_page_token":"abc"}')] * 2)
        self.assertEqual(result['reason'], 'repeated_pagination_token')
        self.assertEqual(result['get_count'], 2)
        self.assertFalse(self.read('normalized-source.json')['source_complete'])

    def test_page_limit_and_total_gets(self):
        pages = []
        for i in range(20):
            pages.extend([(500,b'error'), (429,b'busy'),
                          (200,json.dumps({'bars':{},'next_page_token':str(i)}).encode())])
        result = self.run_capture(pages)
        self.assertEqual(result['reason'], 'page_limit_exceeded')
        self.assertEqual(result['get_count'], 60)
        self.assertEqual(len(self.read('capture-manifest.json')['page_receipts']), 60)

    def test_unexpected_symbol_fails(self):
        result = self.run_capture([(200,b'{"bars":{"EVIL":[]}}')])
        self.assertEqual(result['reason'], 'response_symbol_outside_sealed_selection')

    def test_duplicate_json_keys_fails(self):
        result = self.run_capture([(200,b'{"bars":{},"bars":{}}')])
        self.assertEqual(result['reason'], 'duplicate_json_key_in_response')

    def test_nonfinite_source_fails(self):
        result = self.run_capture([(200,b'{"bars":{"AAPL":[{"c":NaN}]}}')])
        self.assertEqual(result['reason'], 'nonfinite_number_in_response')

    def test_missing_seal_no_get(self):
        result = cp.capture(MODULE / 'protocol.json', self.root / 'missing', self.day,
                            self.root / 'output', transport=lambda req:self.fail('unexpected GET'),
                            credentials=('k','s'))
        self.assertFalse(result['accepted'])
        self.assertEqual(result['get_count'], 0)

    def test_nonobject_seal_rejected_with_manifest(self):
        path = self.root / 'seal.json'
        path.write_text('[]')
        result = cp.capture(MODULE / 'protocol.json', path, self.day,
                            self.root / 'output', transport=lambda req:self.fail('unexpected GET'),
                            credentials=('k','s'))
        self.assertEqual(result['reason'], 'missing_or_foreign_preopen_seal')
        self.assertEqual(result['get_count'], 0)
        self.assertEqual(self.read('capture-manifest.json')['observation_status'], 'unknown')

    def test_modified_seal_hash_rejected(self):
        path = self.actual_seal_files()
        seal = json.loads(path.read_text())
        seal['selection']['selected_symbols'].append('MSFT')
        path.write_text(json.dumps(seal))
        with self.assertRaisesRegex(cp.CaptureError, 'preopen_seal_content_changed'):
            cp.load_sealed_selection(MODULE / 'protocol.json', path, self.day)

    def test_rehashed_changed_selection_fails_recomputation(self):
        path = self.actual_seal_files()
        seal = json.loads(path.read_text())
        seal['selection']['selected_symbols'].append('MSFT')
        seal['seal_content_digest'] = protocol_tools.digest({k:v for k,v in seal.items() if k!='seal_content_digest'})
        path.write_text(json.dumps(seal))
        with self.assertRaisesRegex(cp.CaptureError, 'preopen_seal_recomputation_mismatch'):
            cp.load_sealed_selection(MODULE / 'protocol.json', path, self.day)

    def test_changed_source_bundle_rejected(self):
        path = self.actual_seal_files()
        bundle_path = path.parent / 'source-bundle.json'
        bundle = json.loads(bundle_path.read_text())
        bundle['daily']['AAPL'] = []
        bundle_path.write_text(json.dumps(bundle))
        with self.assertRaisesRegex(cp.CaptureError, 'preopen_source_bundle_changed'):
            cp.load_sealed_selection(MODULE / 'protocol.json', path, self.day)

    def test_changed_protocol_bytes_rejected(self):
        path = self.root / 'protocol.json'
        path.write_bytes((MODULE / 'protocol.json').read_bytes() + b' ')
        with self.assertRaisesRegex(cp.CaptureError, 'original_protocol_hash_mismatch'):
            cp.load_sealed_selection(path, self.root / 'unused', self.day)

    def test_output_directory_exclusive(self):
        self.run_capture()
        before = (self.root / 'output' / 'capture-manifest.json').read_bytes()
        result = cp.capture(MODULE / 'protocol.json', self.root / 'unused', self.day,
                            self.root / 'output', transport=lambda req:self.fail('unexpected GET'))
        self.assertEqual(result['reason'], 'output_directory_already_exists')
        self.assertEqual((self.root / 'output' / 'capture-manifest.json').read_bytes(), before)

    def test_no_private_values_in_summary_or_manifest(self):
        result = self.run_capture([(403, b'offline_secret confidential')])
        self.assertNotIn('offline_secret', json.dumps(result))
        self.assertNotIn('offline_secret', (self.root / 'output' / 'capture-manifest.json').read_text())


if __name__ == '__main__':
    unittest.main()
