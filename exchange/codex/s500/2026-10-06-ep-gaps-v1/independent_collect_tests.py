"""Independent offline downloader tests. No credentials or network accesses."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import unittest

import collect_windows as collect


POINT = {
    'symbol': 'TEST', 'point_id': 'independent_fixture',
    'request_start_utc': '2026-01-06T19:27:00+00:00',
    'request_end_inclusive_utc': '2026-01-06T19:31:59.999999999Z',
}


def payload(stream='trades', rows=None, token=None, symbol='TEST'):
    return {'symbol': symbol, stream: [] if rows is None else rows, 'next_page_token': token}


class CollectionAudit(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root_patch = patch.object(collect, 'ROOT', Path(self.temp.name))
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.addCleanup(self.temp.cleanup)

    def outcome_path(self, stream='trades'):
        return Path(self.temp.name) / ('raw-' + stream) / POINT['point_id'] / 'outcome.json'

    def completed(self, stream='trades'):
        with patch.object(collect, 'get_json', return_value=payload(stream)) as get:
            result = collect.fetch(POINT, stream)
            self.assertEqual(get.call_count, 1)
            self.assertTrue(result['pages_complete'])
        return result

    def reject_bad_outcome(self, mutate):
        self.completed()
        path = self.outcome_path()
        value = json.loads(path.read_text())
        mutate(value)
        path.write_text(json.dumps(value))
        with patch.object(collect, 'get_json', side_effect=AssertionError('offline_test_no_fetch')):
            try:
                result = collect.fetch(POINT, 'trades')
            except (AssertionError, RuntimeError, ValueError, KeyError):
                return
        self.assertFalse(result['pages_complete'], 'Unverified cache was accepted as complete')

    def test_exact_raw_sip_request_and_ns_end(self):
        route, params = collect.request_spec(POINT, 'bars')
        self.assertEqual(route, '/v2/stocks/TEST/bars')
        self.assertEqual(params['feed'], 'sip')
        self.assertEqual(params['adjustment'], 'raw')
        self.assertEqual(params['timeframe'], '1Min')
        self.assertEqual(params['end'], '2026-01-06T19:31:59.999999999Z')

    def test_empty_page_with_token_continues(self):
        with patch.object(collect, 'get_json', side_effect=[payload(token='page2'), payload(rows=[{'t': 'fixture'}])]) as get:
            result = collect.fetch(POINT, 'trades')
        self.assertTrue(result['pages_complete'])
        self.assertEqual((len(result['pages']), result['records']), (2, 1))
        self.assertEqual(get.call_args_list[1].args[1]['page_token'], 'page2')

    def test_repeated_token_cannot_certify(self):
        with patch.object(collect, 'get_json', side_effect=[payload(token='same'), payload(token='same')]):
            result = collect.fetch(POINT, 'trades')
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['pages_complete'])

    def test_ten_pages_with_token_are_truncated(self):
        with patch.object(collect, 'get_json', side_effect=[payload(token='page' + str(i)) for i in range(10)]) as get:
            result = collect.fetch(POINT, 'trades')
        self.assertEqual(get.call_count, 10)
        self.assertEqual(result['status'], 'truncated')
        self.assertFalse(result['pages_complete'])

    def test_terminal_tenth_page_can_complete(self):
        with patch.object(collect, 'get_json', side_effect=[payload(token='page' + str(i)) for i in range(9)] + [payload()]):
            result = collect.fetch(POINT, 'trades')
        self.assertTrue(result['pages_complete'])
        self.assertEqual(len(result['pages']), 10)

    def test_bar_page_cap_preserves_unknown(self):
        with patch.object(collect, 'get_json', return_value=payload('bars', token='more')):
            result = collect.fetch(POINT, 'bars')
        self.assertEqual(result['status'], 'truncated')
        self.assertFalse(result['pages_complete'])

    def test_wrong_symbol_is_failed_and_raw_preserved(self):
        with patch.object(collect, 'get_json', return_value=payload(symbol='OTHER')):
            result = collect.fetch(POINT, 'trades')
        self.assertFalse(result['pages_complete'])
        self.assertTrue((self.outcome_path().parent / 'page-0000.json').exists())

    def test_malformed_or_empty_token_cannot_certify(self):
        for token in [0, {}, '']:
            with self.subTest(token=token), TemporaryDirectory() as folder, patch.object(collect, 'ROOT', Path(folder)):
                with patch.object(collect, 'get_json', return_value=payload(token=token)):
                    result = collect.fetch(POINT, 'trades')
                self.assertFalse(result['pages_complete'])

    def test_cached_bytes_tamper_rejected(self):
        self.completed()
        page = self.outcome_path().parent / 'page-0000.json'
        page.write_text(page.read_text() + ' ')
        with patch.object(collect, 'get_json', side_effect=AssertionError('offline_test_no_fetch')):
            with self.assertRaises((AssertionError, RuntimeError, ValueError)):
                collect.fetch(POINT, 'trades')

    def test_cached_outcome_count_not_trusted(self):
        self.reject_bad_outcome(lambda value: value.update(records=999))

    def test_cached_outcome_wrong_route_not_trusted(self):
        self.reject_bad_outcome(lambda value: value.update(route='/v2/stocks/OTHER/trades'))

    def test_cached_outcome_empty_pages_not_trusted(self):
        self.reject_bad_outcome(lambda value: value.update(pages=[]))

    def test_partial_page_actual_params_must_match_hash(self):
        result = self.completed()
        path = self.outcome_path().parent / result['pages'][0]['name']
        value = json.loads(path.read_text())
        value['request_parameters']['feed'] = 'iex'
        path.write_text(json.dumps(value))
        self.outcome_path().unlink()
        with patch.object(collect, 'get_json', side_effect=AssertionError('offline_test_no_fetch')):
            result = collect.fetch(POINT, 'trades')
        self.assertFalse(result['pages_complete'])

    def test_order_routes_and_redirects_rejected(self):
        for route in ['/v2/orders', '/v2/stocks/TEST/orders', 'https://other.example/trades']:
            with self.subTest(route=route), self.assertRaises(AssertionError):
                collect.get_json(route, {})
        with self.assertRaises(RuntimeError):
            collect.NoRedirect().redirect_request(None, None, None, None, None, None)


if __name__ == '__main__':
    unittest.main()
