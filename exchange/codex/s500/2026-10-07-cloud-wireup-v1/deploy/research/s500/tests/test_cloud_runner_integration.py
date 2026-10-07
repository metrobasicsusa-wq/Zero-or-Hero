"""Offline full 6,633-panel capture -> durable bytes -> restore -> analysis."""
from datetime import datetime, timedelta
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import capture_prior20
import capture_postclose
import cloud_runner as runner

ROOT = Path(__file__).resolve().parents[1]


class MemoryState:
    def __init__(self):
        self.files, self.publications = {}, []
    def read(self, path):
        return self.files.get(path)
    def read_many(self, paths):
        return {p: self.files.get(p) for p in paths}
    def list_paths(self, prefix=''):
        return sorted(p for p in self.files if p.startswith(prefix))
    def reserve(self, path, payload):
        if path in self.files:
            return False
        self.files[path] = runner.canonical(payload)
        return True
    def publish(self, files, message):
        for p, body in files.items():
            if p in self.files and self.files[p] != body:
                raise ValueError('immutable_conflict')
        self.files.update(files)
        self.publications.append(set(files))
        return {'commit_sha': 'a' * 40, 'readback': True}


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.protocol = json.loads((ROOT / 'protocol.json').read_text())
        self.day = self.protocol['sessions'][0]['date']
        self.state = MemoryState()
        self.gets = []
        self.at = datetime.fromisoformat('2026-10-08T13:03:00+00:00')

    def transport_prior(self, request):
        self.assertEqual(request.method, 'GET')
        self.assertEqual(urlsplit(request.full_url).path, '/v2/stocks/bars')
        self.assertTrue(any(p.endswith('/preopen/claim.json') for p in self.state.files))
        self.gets.append(request.full_url)
        query = parse_qs(urlsplit(request.full_url).query)
        symbols = query['symbols'][0].split(',')
        bars = {s: [{'t': d + 'T04:00:00Z', 'c': '100', 'v': '300000'}
                    for d in self.protocol['sessions'][0]['prior20_dates']]
                for s in symbols if s in ('AAPL', 'MU')}
        return 200, runner.canonical({'bars': bars, 'next_page_token': None})

    def transport_post(self, request):
        self.assertEqual(request.method, 'GET')
        self.assertTrue(any(p.endswith('/postclose/claim.json') for p in self.state.files))
        self.gets.append(request.full_url)
        symbols = parse_qs(urlsplit(request.full_url).query)['symbols'][0].split(',')
        bars = {}
        opening = datetime.fromisoformat(self.protocol['sessions'][0]['open_utc'])
        for s in symbols:
            rows = []
            for t in range(390):
                price = 97 if s == 'AAPL' and 20 <= t < 30 else 100
                if s in ('SPY', 'QQQ') and t >= 20:
                    price = 99
                rows.append({'t': (opening + timedelta(minutes=t)).isoformat(),
                             'o': price, 'h': price + 1, 'l': price - 1, 'c': price, 'v': 100})
            bars[s] = rows
        return 200, runner.canonical({'bars': bars, 'next_page_token': None})

    def capture_pre(self, *args, **kwargs):
        return capture_prior20.capture(*args, **kwargs, transport=self.transport_prior,
                                       credentials=('fixture', 'fixture'), sleep=lambda _: None)

    def capture_post(self, *args, **kwargs):
        return capture_postclose.capture(*args, **kwargs, transport=self.transport_post,
                                         credentials=('fixture', 'fixture'), sleep=lambda _: None)

    def run_phase(self, phase, name='one'):
        work = self.work / (phase + name)
        work.mkdir()
        return runner.capture_phase(ROOT, self.protocol, 'fixture-deployment', self.state,
                                    phase, self.day, name, work, now=lambda: self.at,
                                    capture_pre=self.capture_pre, capture_post=self.capture_post)

    def test_full_pipeline_and_repeated_runs_make_no_new_market_requests(self):
        pre = self.run_phase('preopen')
        self.assertTrue(pre['accepted'], pre)
        self.assertEqual(pre['get_count'], 104)
        count = len(self.gets)
        self.assertEqual(self.run_phase('preopen', 'rerun')['status'], 'already_reserved_no_retry')
        self.assertEqual(len(self.gets), count)
        self.at = datetime.fromisoformat('2026-10-08T20:23:00+00:00')
        post = self.run_phase('postclose')
        self.assertTrue(post['accepted'], post)
        self.assertEqual(post['get_count'], 1)
        path = runner._result_path(self.day, 'postclose')
        result = json.loads(self.state.read(path))
        summary_path = result['analysis_files']['summary.json']['path']
        summary = json.loads(self.state.read(summary_path))
        self.assertEqual(summary['counts']['selected_cases'], 4)
        self.assertEqual(summary['counts']['result_rows'], 144)
        self.assertEqual(summary['counts']['variant_group_summaries'], 216)
        self.assertEqual(len(summary['fixed_feature_bins']), 12)
        count = len(self.gets)
        self.assertEqual(self.run_phase('postclose', 'rerun')['status'], 'already_reserved_no_retry')
        self.assertEqual(len(self.gets), count)
        audit = runner.audit_and_checkpoints(self.protocol, self.state, 'lateaudit',
                                              '2026-10-08T22:33:00Z')
        self.assertEqual(audit['checkpoints'], [])
        self.assertEqual(audit['calendar_dates'], 59)
        self.assertEqual(len(self.gets), count)

    def test_permission_failure_preserves_selected_unknowns_and_never_retries(self):
        self.assertTrue(self.run_phase('preopen')['accepted'])
        self.at = datetime.fromisoformat('2026-10-08T20:23:00+00:00')
        def denied(request):
            self.gets.append(request.full_url)
            return 403, b'{"error":"fixture forbidden"}'
        self.transport_post = denied
        post = self.run_phase('postclose')
        self.assertFalse(post['accepted'])
        self.assertEqual(post['get_count'], 1)
        result = json.loads(self.state.read(runner._result_path(self.day, 'postclose')))
        self.assertIn('source_archive', result)
        body = self.state.read(result['analysis_files']['results.jsonl.gz']['path'])
        rows = [json.loads(line) for line in gzip.decompress(body).splitlines()]
        self.assertEqual(len(rows), 144)
        self.assertTrue(all(r['net_return'] is None and r['signal_status'] == 'unknown' for r in rows))
        before = len(self.gets)
        self.run_phase('postclose', 'rerun')
        self.assertEqual(len(self.gets), before)

    def test_tampered_private_archive_blocks_postclose_market_access(self):
        self.assertTrue(self.run_phase('preopen')['accepted'])
        result = json.loads(self.state.read(runner._result_path(self.day, 'preopen')))
        self.state.files[result['source_archive']['path']] = b'changed'
        before = len(self.gets)
        self.at = datetime.fromisoformat('2026-10-08T20:23:00+00:00')
        result = self.run_phase('postclose')
        self.assertFalse(result['accepted'])
        self.assertEqual(len(self.gets), before)

    def test_real_persistence_rehearsal_is_disjoint_from_primary_dates(self):
        result = runner.rehearsal(self.protocol, self.state, 'fixture-rehearsal',
                                  self.work, '2026-10-07T19:00:00Z')
        self.assertEqual(result['status'], 'cloud_state_roundtrip_passed')
        self.assertEqual(result['prospective_observations_added'], 0)
        self.assertTrue(all('/rehearsals/' in p for p in self.state.files))
        self.assertEqual(self.gets, [])

    def test_access_probe_one_read_and_no_primary_observation(self):
        calls = []
        def transport(request):
            calls.append(request)
            self.assertEqual(request.method, 'GET')
            self.assertIn('feed=sip', request.full_url)
            self.assertIn('SPY', request.full_url)
            return 200, b'{"bars":{"SPY":[{"t":"2026-10-06T13:30:00Z"}]},"next_page_token":null}'
        result = runner.connectivity(self.state, 'probe', '2026-10-07T18:00:00Z',
                                     transport=transport, credentials=('fixture', 'fixture'))
        self.assertTrue(result['accepted'])
        self.assertEqual(result['prospective_observations_added'], 0)
        self.assertEqual(len(calls), 1)
        self.assertTrue(all('/connectivity/' in p for p in self.state.files))
        again = runner.connectivity(self.state, 'probe', '2026-10-07T18:00:00Z',
                                    transport=transport, credentials=('fixture', 'fixture'))
        self.assertEqual(again['market_gets'], 0)
        self.assertEqual(len(calls), 1)

    def test_access_probe_permission_denied_never_retries(self):
        calls = []
        def transport(request):
            calls.append(request)
            return 403, b'{"error":"fixture"}'
        result = runner.connectivity(self.state, 'denied', '2026-10-07T18:00:00Z',
                                     transport=transport, credentials=('fixture', 'fixture'))
        self.assertFalse(result['accepted'])
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['http_status'], 403)


if __name__ == '__main__':
    unittest.main()
