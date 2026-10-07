"""Offline fixtures exercise raw-to-seal consistency and hostile archives."""
import copy
from datetime import date, timedelta
import gzip
import io
import json
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import capture_prior20 as capture
import capture_postclose as postclose
import protocol_tools
import verify_sources as verify


def write(path, value):
    path.write_bytes(capture.canonical_bytes(value) + b'\n')


class SourcesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.method = self.base / 'method'
        self.method.mkdir()
        for name in ('capture_prior20.py', 'protocol_tools.py', 'prospect_selection.py'):
            shutil.copyfile(ROOT / name, self.method / name)
        self.panel = [{'symbol': 'AAA', 'etf_classification': 'N'},
                      {'symbol': 'QQQ', 'etf_classification': 'Y'},
                      {'symbol': 'SPY', 'etf_classification': 'Y'}]
        self.panel_path = self.method / 'fixed-panel.json'
        write(self.panel_path, self.panel)
        self.day = '2026-10-08'
        self.prior20 = [(date(2026, 9, 18) + timedelta(days=i)).isoformat() for i in range(20)]
        self.protocol = {'id': 'synthetic_fixture', 'status': 'registered',
                         'registered_at': '2026-10-07T00:00:00+00:00',
                         'fixed_panel_symbols': 3,
                         'selection': {'controls': ['SPY', 'QQQ']},
                         'fixed_panel_content_digest': protocol_tools.digest(self.panel),
                         'sessions': [{'date': self.day, 'prior20_dates': self.prior20,
                                       'open_utc': '2026-10-08T13:30:00+00:00',
                                       'close_utc': '2026-10-08T20:00:00+00:00',
                                       'receipt_deadline_utc': '2026-10-08T21:15:00+00:00',
                                       'preopen_cutoff_utc': '2026-10-08T13:20:00+00:00'}],
                         'bound_file_sha256': {name: verify.sha256((self.method / name).read_bytes())
                                               for name in capture.REQUIRED_BINDINGS}}
        self.protocol_path = self.method / 'protocol.json'
        write(self.protocol_path, self.protocol)
        self.protocol_sha = verify.sha256(self.protocol_path.read_bytes())
        self.directory = self.base / 'capture'
        self.bars = {row['symbol']: [{'t': day + 'T04:00:00Z', 'c': '10', 'v': 3_000_000}
                                     for day in self.prior20] for row in self.panel}

    def tearDown(self):
        self.temp.cleanup()

    def create(self, *, retry=False, paginate=False, batches=False):
        pages = []
        if retry:
            pages.append((429, b'{"error":"synthetic rate limit"}'))
        if paginate:
            pages += [(200, capture.canonical_bytes({'bars': {s: b[:10] for s, b in self.bars.items()},
                                                     'next_page_token': 'page-two'})),
                      (200, capture.canonical_bytes({'bars': {s: b[10:] for s, b in self.bars.items()},
                                                     'next_page_token': None}))]
        elif batches:
            pages += [(200, capture.canonical_bytes({'bars': {s: self.bars[s] for s in symbols},
                                                     'next_page_token': None}))
                      for symbols in (['AAA', 'QQQ'], ['SPY'])]
        else:
            pages.append((200, capture.canonical_bytes({'bars': self.bars, 'next_page_token': None})))
        with patch.object(capture, 'BATCH_SIZE', 2 if batches else 64):
            result = capture.capture(self.protocol_path, self.panel_path, self.day, self.directory,
                                     transport=lambda _: pages.pop(0),
                                     credentials=('synthetic', 'synthetic'),
                                     now=lambda: '2026-10-08T12:00:00+00:00',
                                     monotonic=lambda: 0, sleep=lambda _: None)
        self.assertTrue(result['accepted'], result)

    def check(self):
        return verify.verify_preopen_sources(self.protocol_path, self.panel_path, self.day,
                                              self.directory,
                                              expected_protocol_sha256=self.protocol_sha)

    def json(self, name):
        return json.loads((self.directory / name).read_text())

    def change(self, name, function):
        value = self.json(name)
        function(value)
        write(self.directory / name, value)

    def update_receipts(self, function):
        bundle = self.json('source-bundle.json')
        function(bundle['raw_page_receipts'])
        write(self.directory / 'source-bundle.json', bundle)
        manifest = self.json('capture-manifest.json')
        manifest['page_receipts'] = bundle['raw_page_receipts']
        write(self.directory / 'capture-manifest.json', manifest)
        for receipt in bundle['raw_page_receipts']:
            write(self.directory / receipt['body_file'].replace('.body', '.receipt.json'), receipt)

    def test_complete_bytes_and_seal_are_recomputed(self):
        self.create()
        result = self.check()
        self.assertEqual(result['successful_pages'], 1)
        self.assertEqual(result['selected_symbols'], 3)
        self.assertFalse(result['external_receipt_time_verified'])

    def test_retry_and_paginated_bytes_preserved(self):
        self.create(retry=True, paginate=True)
        result = self.check()
        self.assertEqual(result['preserved_retry_error_pages'], 1)
        self.assertEqual(result['successful_pages'], 2)
        self.assertEqual(result['source_requests'], 3)

    def test_all_panel_batches_exactly_once(self):
        self.create(batches=True)
        with patch.object(capture, 'BATCH_SIZE', 2):
            result = self.check()
        self.assertEqual(result['completed_batches'], 2)

    def test_default_rejects_nonregistered_protocol(self):
        self.create()
        with self.assertRaisesRegex(verify.SourceVerificationError, 'registered_protocol_bytes_changed'):
            verify.verify_preopen_sources(self.protocol_path, self.panel_path, self.day, self.directory)

    def test_bound_method_change_is_rejected(self):
        self.create()
        (self.method / 'prospect_selection.py').write_text('altered')
        with self.assertRaisesRegex(verify.SourceVerificationError, 'binding'):
            self.check()

    def test_raw_page_tampering(self):
        self.create()
        (self.directory / 'batch-000-page-00-attempt-1.body').write_bytes(b'{}')
        with self.assertRaisesRegex(verify.SourceVerificationError, 'raw_page_bytes_mismatch'):
            self.check()

    def test_error_page_tampering(self):
        self.create(retry=True)
        (self.directory / 'batch-000-page-00-attempt-1.body').write_bytes(b'changed')
        with self.assertRaisesRegex(verify.SourceVerificationError, 'raw_page_bytes_mismatch'):
            self.check()

    def test_receipt_file_mismatch(self):
        self.create()
        self.change('batch-000-page-00-attempt-1.receipt.json', lambda row: row.update(body_bytes=1))
        with self.assertRaisesRegex(verify.SourceVerificationError, 'receipt_file_content_mismatch'):
            self.check()

    def test_bundle_manifest_receipts_mismatch(self):
        self.create()
        self.change('capture-manifest.json', lambda row: row['page_receipts'].clear())
        with self.assertRaisesRegex(verify.SourceVerificationError, 'raw_receipt_lists_mismatch'):
            self.check()

    def test_normalized_bytes_must_match_exactly(self):
        self.create()
        path = self.directory / 'batch-000-normalized.json'
        path.write_bytes(path.read_bytes() + b'\n')
        with self.assertRaisesRegex(verify.SourceVerificationError, 'normalized_batch_bytes_mismatch'):
            self.check()

    def test_daily_bundle_cannot_change(self):
        self.create()
        self.change('source-bundle.json', lambda row: row['daily']['AAA'][0].update(c='20'))
        with self.assertRaisesRegex(verify.SourceVerificationError, 'bundle_daily_differs_from_raw_pages'):
            self.check()

    def test_seal_scope_cannot_change(self):
        self.create()
        self.change('selection-seal.json', lambda row: row.update(scope='different'))
        with self.assertRaisesRegex(verify.SourceVerificationError, 'selection_seal_recomputation_mismatch'):
            self.check()

    def test_seal_selected_symbols_cannot_change(self):
        self.create()
        self.change('selection-seal.json', lambda row: row['selection']['selected_symbols'].remove('AAA'))
        with self.assertRaisesRegex(verify.SourceVerificationError, 'selection_seal_recomputation_mismatch'):
            self.check()

    def test_extra_unreferenced_body_is_rejected(self):
        self.create()
        (self.directory / 'unreferenced.body').write_bytes(b'private')
        with self.assertRaisesRegex(verify.SourceVerificationError, 'unreferenced_or_unexpected_source_files'):
            self.check()

    def test_request_parameter_change(self):
        self.create()
        self.update_receipts(lambda rows: rows[0]['parameters'].update(feed='iex'))
        with self.assertRaisesRegex(verify.SourceVerificationError, 'source_request_parameters_changed'):
            self.check()

    def test_page_presence_chain_must_match(self):
        self.create(paginate=True)
        self.update_receipts(lambda rows: rows[1].update(page_token_present=False))
        with self.assertRaisesRegex(verify.SourceVerificationError, 'request_page_token_presence_mismatch'):
            self.check()

    def test_late_receipt_is_rejected(self):
        self.create()
        self.update_receipts(lambda rows: rows[0].update(received_at='2026-10-08T13:21:00+00:00'))
        with self.assertRaises(verify.SourceVerificationError):
            self.check()

    def replace_body_with_updated_receipt_hash(self, body, *, page=0):
        filename = f'batch-000-page-{page:02d}-attempt-1.body'
        (self.directory / filename).write_bytes(body)
        self.update_receipts(lambda rows: rows[page].update(body_sha256=verify.sha256(body), body_bytes=len(body)))

    def test_duplicate_json_key_in_successful_body_is_rejected(self):
        self.create()
        self.replace_body_with_updated_receipt_hash(b'{"bars":{},"bars":{}}')
        with self.assertRaisesRegex(verify.SourceVerificationError, 'invalid_successful_source_json'):
            self.check()

    def test_nonfinite_successful_body_is_rejected(self):
        self.create()
        self.replace_body_with_updated_receipt_hash(b'{"bars":{"AAA":[{"c":NaN}]}}')
        with self.assertRaisesRegex(verify.SourceVerificationError, 'invalid_successful_source_json'):
            self.check()

    def test_unknown_source_symbol_is_rejected_even_with_updated_hash(self):
        self.create()
        self.replace_body_with_updated_receipt_hash(capture.canonical_bytes({'bars': {'FOREIGN': []}}))
        with self.assertRaisesRegex(verify.SourceVerificationError, 'source_symbol_outside_requested_batch'):
            self.check()

    def test_repeated_pagination_token_rejected(self):
        self.create(paginate=True)
        second = capture.canonical_bytes({'bars': {}, 'next_page_token': 'page-two'})
        self.replace_body_with_updated_receipt_hash(second, page=1)
        with self.assertRaisesRegex(verify.SourceVerificationError, 'repeated_pagination_token'):
            self.check()

    def test_no_retry_authorization_failure_in_successful_manifest(self):
        self.create(retry=True)
        self.update_receipts(lambda rows: rows[0].update(http_status=403))
        with self.assertRaisesRegex(verify.SourceVerificationError, 'authorization_error_in_accepted_capture'):
            self.check()

    def test_source_archive_roundtrip_exact_and_deterministic(self):
        self.create(retry=True, paginate=True)
        first, second = self.base / 'a.tar.gz', self.base / 'b.tar.gz'
        manifest = verify.archive_sources(self.directory, first)
        self.assertEqual(manifest, verify.archive_sources(self.directory, second))
        self.assertEqual(first.read_bytes(), second.read_bytes())
        restored = self.base / 'restored'
        self.assertEqual(manifest, verify.restore_sources(first, restored))
        self.assertEqual(verify._inventory(self.directory), verify._inventory(restored))
        result = verify.verify_preopen_sources(self.protocol_path, self.panel_path, self.day, restored,
                                               expected_protocol_sha256=self.protocol_sha)
        self.assertTrue(result['accepted'])


class ArchiveSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.archive = self.base / 'source.tar.gz'
        self.output = self.base / 'out'

    def tearDown(self):
        self.temp.cleanup()

    def tar(self, members, tail=b''):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w', format=tarfile.USTAR_FORMAT) as stream:
            for name, kind, body in members:
                member = tarfile.TarInfo(name)
                member.type = kind
                member.size = len(body) if kind == tarfile.REGTYPE else 0
                if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                    member.linkname = 'target.json'
                stream.addfile(member, io.BytesIO(body) if member.size else None)
        self.archive.write_bytes(gzip.compress(output.getvalue() + tail, mtime=0))

    def reject(self, code=None):
        with self.assertRaisesRegex(verify.SourceVerificationError, code or '.'):
            verify.restore_sources(self.archive, self.output)
        self.assertFalse(self.output.exists())

    def test_parent_traversal(self):
        self.tar([('../outside.json', tarfile.REGTYPE, b'{}')])
        self.reject('unsafe_source_name')

    def test_absolute_path(self):
        self.tar([('/outside.json', tarfile.REGTYPE, b'{}')])
        self.reject('unsafe_source_name')

    def test_windows_path(self):
        self.tar([('C:\\outside.json', tarfile.REGTYPE, b'{}')])
        self.reject('unsafe_source_name')

    def test_symlink(self):
        self.tar([('link.json', tarfile.SYMTYPE, b'')])
        self.reject('archive_member_not_regular_file')

    def test_hardlink(self):
        self.tar([('link.json', tarfile.LNKTYPE, b'')])
        self.reject('archive_member_not_regular_file')

    def test_duplicate(self):
        self.tar([('same.json', tarfile.REGTYPE, b'a'), ('same.json', tarfile.REGTYPE, b'b')])
        self.reject('duplicate_archive_member')

    def test_special_file(self):
        self.tar([('fifo.body', tarfile.FIFOTYPE, b'')])
        self.reject('archive_member_not_regular_file')

    def test_unknown_extension(self):
        self.tar([('run.py', tarfile.REGTYPE, b'print(1)')])
        self.reject('unsafe_source_name')

    def test_file_directory_collision(self):
        self.tar([('a.json', tarfile.REGTYPE, b'{}'), ('a.json/b.json', tarfile.REGTYPE, b'{}')])
        self.reject('archive_file_directory_collision')

    def test_compressed_size_limit(self):
        self.tar([('a.json', tarfile.REGTYPE, b'{}')])
        with patch.object(verify, 'MAX_ARCHIVE_BYTES', 2):
            self.reject('archive_size_limit_exceeded')

    def test_expansion_limit(self):
        self.tar([('a.body', tarfile.REGTYPE, b'a' * 10000)])
        with patch.object(verify, 'MAX_UNCOMPRESSED_BYTES', 1024):
            self.reject('archive_expansion_limit_exceeded')

    def test_file_count_limit(self):
        self.tar([('a.json', tarfile.REGTYPE, b'{}'), ('b.body', tarfile.REGTYPE, b'')])
        with patch.object(verify, 'MAX_FILES', 1):
            self.reject('source_file_count_limit_exceeded')

    def test_nonpadding_hidden_tail(self):
        self.tar([('a.json', tarfile.REGTYPE, b'{}')], tail=b'hidden archive')
        self.reject('archive_trailing_content')

    def test_empty_archive(self):
        self.tar([])
        self.reject('empty_source_archive')

    def test_corrupt_gzip(self):
        self.archive.write_bytes(b'not gzip')
        self.reject('invalid_source_archive')

    def test_existing_output_not_overwritten(self):
        self.tar([('a.json', tarfile.REGTYPE, b'{}')])
        self.output.mkdir()
        with self.assertRaisesRegex(verify.SourceVerificationError, 'restore_directory_already_exists'):
            verify.restore_sources(self.archive, self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_pack_source_symlink_rejected(self):
        self.output.mkdir()
        (self.output / 'a.json').symlink_to(self.archive)
        with self.assertRaises(verify.SourceVerificationError):
            verify.archive_sources(self.output, self.archive)

    def test_pack_unknown_file_rejected(self):
        self.output.mkdir()
        (self.output / 'a.py').write_text('no')
        with self.assertRaisesRegex(verify.SourceVerificationError, 'unsafe_source_name'):
            verify.archive_sources(self.output, self.archive)


class PostcloseSourceTests(unittest.TestCase):
    setUp = SourcesTests.setUp
    tearDown = SourcesTests.tearDown
    create_preopen = SourcesTests.create

    def create(self, retry=False, paginate=False, custom_body=None):
        self.create_preopen()
        self.postdir = self.base / 'postclose'
        minute = {'t': '2026-10-08T13:30:00.000000001Z', 'o': '10.01', 'c': '9.99',
                  'h': '10.2', 'l': '9.98', 'v': 0}
        # Duplicate timestamps, missing minutes and malformed rows must survive.
        bars = {'AAA': [minute, copy.deepcopy(minute), {'t': 'bad'}, None], 'SPY': [], 'QQQ': []}
        pages = [(500, b'synthetic server retry')] if retry else []
        if custom_body is not None:
            pages.append((200, custom_body))
        elif paginate:
            pages.extend([(200, capture.canonical_bytes({'bars': {'AAA': bars['AAA'][:2]},
                                                         'next_page_token': 'post-next'})),
                          (200, capture.canonical_bytes({'bars': {'AAA': bars['AAA'][2:]},
                                                         'next_page_token': None}))])
        else:
            pages.append((200, capture.canonical_bytes({'bars': bars, 'next_page_token': None})))
        with patch.object(postclose, 'ORIGINAL_PROTOCOL_SHA256', self.protocol_sha):
            result = postclose.capture(self.protocol_path, self.directory / 'selection-seal.json',
                                       self.day, self.postdir, transport=lambda _: pages.pop(0),
                                       credentials=('synthetic', 'synthetic'),
                                       now=lambda: '2026-10-08T20:20:00+00:00',
                                       monotonic=lambda: 0, sleep=lambda _: None)
        self.assertTrue(result['accepted'], result)

    def check(self):
        return verify.verify_postclose_sources(self.protocol_path, self.day,
                                                self.directory / 'selection-seal.json', self.postdir,
                                                now='2026-10-09T00:00:00+00:00',
                                                expected_protocol_sha256=self.protocol_sha)

    def change(self, name, function):
        path = self.postdir / name
        value = json.loads(path.read_text())
        function(value)
        write(path, value)

    def update_receipts(self, function):
        source = json.loads((self.postdir / 'normalized-source.json').read_text())
        function(source['page_receipts'])
        write(self.postdir / 'normalized-source.json', source)
        manifest = json.loads((self.postdir / 'capture-manifest.json').read_text())
        manifest['page_receipts'] = source['page_receipts']
        write(self.postdir / 'capture-manifest.json', manifest)
        for receipt in source['page_receipts']:
            write(self.postdir / receipt['body_file'].replace('.body', '.receipt.json'), receipt)

    def test_raw_minutes_and_errors_reconstruct(self):
        self.create(retry=True, paginate=True)
        result = self.check()
        self.assertEqual(result['successful_pages'], 2)
        self.assertEqual(result['preserved_retry_error_pages'], 1)
        self.assertFalse(result['minute_completeness_claimed'])
        self.assertFalse(result['external_receipt_time_verified'])

    def test_preserves_exact_decimal_and_submicrosecond_timestamp(self):
        body = (b'{"bars":{"AAA":[{"t":"2026-10-08T13:30:00.000000001Z",'
                b'"c":123.123456789012345678901234567890,"v":100}]}}')
        self.create(custom_body=body)
        self.assertTrue(self.check()['accepted'])
        bar = json.loads((self.postdir / 'normalized-source.json').read_text())['bars']['AAA'][0]
        self.assertEqual(bar['c'], '123.123456789012345678901234567890')
        self.assertEqual(bar['t'], '2026-10-08T13:30:00.000000001Z')

    def test_missing_minutes_still_not_complete_observation_claim(self):
        self.create(custom_body=b'{"bars":{}}')
        self.assertTrue(self.check()['accepted'])
        self.assertFalse(self.check()['minute_completeness_claimed'])

    def test_tampered_preopen_raw_rejected_again(self):
        self.create()
        (self.directory / 'batch-000-page-00-attempt-1.body').write_bytes(b'{}')
        with self.assertRaisesRegex(verify.SourceVerificationError, 'raw_page_bytes_mismatch'):
            self.check()

    def test_tampered_postclose_raw(self):
        self.create()
        (self.postdir / 'page-00-attempt-1.body').write_bytes(b'{}')
        with self.assertRaisesRegex(verify.SourceVerificationError, 'postclose_raw_page_bytes_mismatch'):
            self.check()

    def test_tampered_minute_merge(self):
        self.create()
        self.change('normalized-source.json', lambda row: row['bars']['AAA'].clear())
        with self.assertRaisesRegex(verify.SourceVerificationError, 'postclose_normalized_source_bytes_mismatch'):
            self.check()

    def test_tampered_aggregate_hash(self):
        self.create()
        self.change('postclose-receipt.json', lambda row: row.update(body_sha256='0' * 64))
        with self.assertRaisesRegex(verify.SourceVerificationError, 'postclose_aggregate_receipt_mismatch'):
            self.check()

    def test_foreign_page_symbol_with_updated_body_hash(self):
        self.create()
        body = b'{"bars":{"FOREIGN":[]}}'
        (self.postdir / 'page-00-attempt-1.body').write_bytes(body)
        self.update_receipts(lambda rows: rows[0].update(body_sha256=verify.sha256(body), body_bytes=len(body)))
        with self.assertRaisesRegex(verify.SourceVerificationError, 'postclose_symbol_outside_sealed_selection'):
            self.check()

    def test_duplicate_json_key_body_with_updated_hash(self):
        self.create()
        body = b'{"bars":{},"bars":{}}'
        (self.postdir / 'page-00-attempt-1.body').write_bytes(body)
        self.update_receipts(lambda rows: rows[0].update(body_sha256=verify.sha256(body), body_bytes=len(body)))
        with self.assertRaisesRegex(verify.SourceVerificationError, 'postclose_invalid_successful_source_json'):
            self.check()

    def test_repeated_page_token(self):
        self.create(paginate=True)
        body = b'{"bars":{},"next_page_token":"post-next"}'
        (self.postdir / 'page-01-attempt-1.body').write_bytes(body)
        self.update_receipts(lambda rows: rows[1].update(body_sha256=verify.sha256(body), body_bytes=len(body)))
        with self.assertRaisesRegex(verify.SourceVerificationError, 'postclose_repeated_pagination_token'):
            self.check()

    def test_unreferenced_postclose_raw_rejected(self):
        self.create()
        (self.postdir / 'extra.body').write_bytes(b'private')
        with self.assertRaisesRegex(verify.SourceVerificationError, 'postclose_unreferenced_or_unexpected_source_files'):
            self.check()

    def test_late_source_receipt_rejected(self):
        self.create()
        self.update_receipts(lambda rows: rows[0].update(received_at='2026-10-08T21:16:00+00:00'))
        with self.assertRaises(verify.SourceVerificationError):
            self.check()

    def test_changed_postclose_parameters_rejected(self):
        self.create()
        self.update_receipts(lambda rows: rows[0]['parameters'].update(timeframe='1Hour'))
        with self.assertRaisesRegex(verify.SourceVerificationError, 'postclose_request_parameters_changed'):
            self.check()

    def test_postclose_source_roundtrip(self):
        self.create(retry=True)
        archive = self.base / 'postclose.tar.gz'
        before = verify.archive_sources(self.postdir, archive)
        restored = self.base / 'postclose-restored'
        self.assertEqual(before, verify.restore_sources(archive, restored))
        self.postdir = restored
        self.assertTrue(self.check()['accepted'])


if __name__ == '__main__':
    unittest.main()
