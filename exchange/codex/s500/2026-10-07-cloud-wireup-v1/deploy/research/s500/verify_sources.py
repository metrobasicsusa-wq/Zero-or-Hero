"""Offline byte-level validation and bounded private source archives.

Receipt timestamps are producer claims: recomputation proves internal source,
selection and scope consistency, not external delivery or a signed timestamp.
Raw source archives are private research state, never public research bundles.
No network, credentials, account access, scheduling or order operations exist.
"""
from collections import Counter
from datetime import timedelta
from decimal import Decimal
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tarfile

import capture_prior20 as capture
import protocol_tools

REGISTERED_PROTOCOL_SHA256 = '2916ce06a0db24e18a754abae8943470ba9419e914b11d03d2f05d99f64e6d71'
MAX_FILES = 5000
MAX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_BYTES = 30 * 1024 * 1024


class SourceVerificationError(ValueError):
    """Stable reason only; never include private source data or paths."""


def _require(condition, code):
    if not condition:
        raise SourceVerificationError(code)


def sha256(body):
    return hashlib.sha256(body).hexdigest()


def _safe_name(name):
    _require(isinstance(name, str) and bool(name), 'unsafe_source_name')
    _require('\\' not in name and '\x00' not in name and ':' not in name,
             'unsafe_source_name')
    pieces = name.split('/')
    _require(all(part not in ('', '.', '..') for part in pieces), 'unsafe_source_name')
    path = PurePosixPath(name)
    _require(not path.is_absolute() and path.suffix in ('.json', '.body'),
             'unsafe_source_name')
    return name


def _regular_bytes(path):
    info = path.lstat()
    _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
             'source_not_regular_unlinked_file')
    _require(info.st_size <= MAX_UNCOMPRESSED_BYTES, 'source_size_limit_exceeded')
    body = path.read_bytes()
    _require(len(body) == info.st_size, 'source_changed_during_read')
    return body


def _json(body):
    """Strict local JSON without converting legitimate protocol floats."""
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, 'duplicate_local_json_key')
            result[key] = value
        return result
    def constant(_):
        raise SourceVerificationError('nonfinite_local_json_number')
    try:
        return json.loads(body, object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SourceVerificationError('invalid_local_json') from None


def _json_file(path):
    return _json(_regular_bytes(path))


def _inventory(directory):
    directory = Path(directory)
    _require(directory.is_dir() and not directory.is_symlink(), 'invalid_source_directory')
    files, total = {}, 0
    for base, directories, filenames in os.walk(directory, followlinks=False):
        for child in directories:
            _require(not (Path(base) / child).is_symlink(), 'source_directory_symlink')
        for filename in filenames:
            path = Path(base) / filename
            name = _safe_name(path.relative_to(directory).as_posix())
            _require(name not in files, 'duplicate_source_name')
            body = _regular_bytes(path)
            total += len(body)
            _require(total <= MAX_UNCOMPRESSED_BYTES, 'source_size_limit_exceeded')
            files[name] = body
            _require(len(files) <= MAX_FILES, 'source_file_count_limit_exceeded')
    _require(bool(files), 'empty_source_archive')
    return files


def source_manifest(files):
    return {'files': [{'name': name, 'bytes': len(body), 'sha256': sha256(body)}
                      for name, body in sorted(files.items())],
            'file_count': len(files),
            'uncompressed_content_bytes': sum(map(len, files.values())),
            'raw_source_private': True}


def archive_sources(source_dir, archive_path):
    """Exclusive deterministic tar.gz of .json/.body files, including errors."""
    files = _inventory(source_dir)
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w', format=tarfile.USTAR_FORMAT) as archive:
        for name, body in sorted(files.items()):
            member = tarfile.TarInfo(name)
            member.size, member.mode = len(body), 0o600
            member.uid = member.gid = member.mtime = 0
            archive.addfile(member, io.BytesIO(body))
            _require(raw.tell() <= MAX_UNCOMPRESSED_BYTES, 'archive_expansion_limit_exceeded')
    raw_body = raw.getvalue()
    _require(len(raw_body) <= MAX_UNCOMPRESSED_BYTES, 'archive_expansion_limit_exceeded')
    packed = gzip.compress(raw_body, compresslevel=6, mtime=0)
    _require(len(packed) <= MAX_ARCHIVE_BYTES, 'archive_size_limit_exceeded')
    capture.private_bytes(Path(archive_path), packed)
    result = source_manifest(files)
    result.update(archive_sha256=sha256(packed), archive_bytes=len(packed),
                  uncompressed_tar_bytes=len(raw_body))
    return result


def restore_sources(archive_path, output_dir):
    """Validate every member before creating files; never use extract/extractall."""
    path = Path(archive_path)
    info = path.lstat()
    _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
             'archive_not_regular_unlinked_file')
    _require(info.st_size <= MAX_ARCHIVE_BYTES, 'archive_size_limit_exceeded')
    packed = path.read_bytes()
    _require(len(packed) <= MAX_ARCHIVE_BYTES, 'archive_size_limit_exceeded')
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(packed)) as stream:
            raw = stream.read(MAX_UNCOMPRESSED_BYTES + 1)
    except (OSError, EOFError):
        raise SourceVerificationError('invalid_source_archive') from None
    _require(len(raw) <= MAX_UNCOMPRESSED_BYTES, 'archive_expansion_limit_exceeded')
    files, total = {}, 0
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
            for member in archive:
                name = _safe_name(member.name)
                _require(member.isfile() and member.type in (tarfile.REGTYPE, tarfile.AREGTYPE),
                         'archive_member_not_regular_file')
                _require(not member.pax_headers, 'archive_extended_headers_forbidden')
                _require(name not in files, 'duplicate_archive_member')
                _require(member.size >= 0, 'invalid_archive_member_size')
                total += member.size
                _require(total <= MAX_UNCOMPRESSED_BYTES, 'archive_expansion_limit_exceeded')
                _require(len(files) < MAX_FILES, 'source_file_count_limit_exceeded')
                handle = archive.extractfile(member)
                _require(handle is not None, 'invalid_archive_member')
                body = handle.read(member.size + 1)
                _require(len(body) == member.size, 'archive_member_size_mismatch')
                files[name] = body
            # Reject a second hidden archive or non-padding bytes after EOF.
            _require(not any(raw[archive.offset:]), 'archive_trailing_content')
    except (tarfile.TarError, EOFError, OSError):
        raise SourceVerificationError('invalid_source_archive') from None
    _require(bool(files), 'empty_source_archive')
    # A regular file may not also be a parent directory of another member.
    for name in files:
        _require(not any(str(parent) in files for parent in PurePosixPath(name).parents
                         if str(parent) != '.'), 'archive_file_directory_collision')
    destination = Path(output_dir)
    _require(not destination.exists() and not destination.is_symlink(),
             'restore_directory_already_exists')
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name, body in sorted(files.items()):
        target = destination / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        capture.private_bytes(target, body)
    restored = _inventory(destination)
    _require(restored == files, 'restored_source_bytes_mismatch')
    result = source_manifest(files)
    result.update(archive_sha256=sha256(packed), archive_bytes=len(packed),
                  uncompressed_tar_bytes=len(raw))
    return result


def _verify_preopen_sources(protocol_path, panel_path, day, capture_dir,
                            expected_protocol_sha256):
    protocol_path, panel_path, directory = map(Path, (protocol_path, panel_path, capture_dir))
    protocol_bytes = _regular_bytes(protocol_path)
    _require(sha256(protocol_bytes) == expected_protocol_sha256, 'registered_protocol_bytes_changed')
    protocol, panel = _json(protocol_bytes), _json_file(panel_path)
    capture.check_bindings(protocol, protocol_path.resolve().parent, panel_path)
    selected_session = protocol_tools.session(protocol, day)
    symbols = [row['symbol'] for row in panel]
    _require(len(symbols) == len(set(symbols)) == protocol['fixed_panel_symbols'],
             'registered_panel_size_changed')
    _require(protocol_tools.digest(panel) == protocol['fixed_panel_content_digest'],
             'registered_panel_content_changed')
    files = _inventory(directory)
    fixed_files = {'source-bundle.json', 'selection-seal.json', 'capture-manifest.json'}
    _require(fixed_files <= set(files), 'missing_preopen_artifact')
    bundle, seal, manifest = (_json(files[name]) for name in
                              ('source-bundle.json', 'selection-seal.json', 'capture-manifest.json'))
    _require(manifest.get('status') == 'accepted' and manifest.get('accepted') is True
             and manifest.get('source_complete') is True and manifest.get('date') == day,
             'capture_not_accepted')
    _require(manifest.get('reason') == 'accepted_before_open'
             and manifest.get('actual_fill') is False and manifest.get('orders_sent') == 0
             and manifest.get('account_reads') == 0 and manifest.get('raw_bodies_private') is True,
             'capture_manifest_policy_mismatch')
    _require(seal.get('accepted') is True and seal.get('date') == day,
             'selection_seal_not_accepted')
    sealed_at = seal.get('sealed_at')
    protocol_tools.check_clock(protocol, day, sealed_at, 'preopen')
    _require(bundle.get('requested_symbols') == symbols, 'requested_symbols_not_exact_panel_order')
    receipts = bundle.get('raw_page_receipts')
    _require(isinstance(receipts, list) and receipts
             and receipts == manifest.get('page_receipts'), 'raw_receipt_lists_mismatch')
    _require(len(receipts) == manifest.get('get_count') <= capture.MAX_GETS,
             'raw_receipt_count_mismatch')
    requests = bundle.get('requests')
    batches = [symbols[index:index + capture.BATCH_SIZE]
               for index in range(0, len(symbols), capture.BATCH_SIZE)]
    _require(isinstance(requests, list) and len(requests) == len(batches)
             == manifest.get('completed_batches') and len(batches) <= capture.MAX_BATCHES,
             'completed_batch_count_mismatch')
    start, end = capture.request_window(selected_session['prior20_dates'], day)
    rebuilt = {symbol: [] for symbol in symbols}
    referenced_files, pointer, successful_pages, retry_errors = set(fixed_files), 0, 0, 0
    previous_received = None
    coverage = Counter()
    for batch_index, requested_symbols in enumerate(batches):
        daily = {symbol: [] for symbol in requested_symbols}
        page_token, seen_tokens = None, set()
        first_requested, final_received, page_index = None, None, 0
        while True:
            _require(page_index < capture.MAX_PAGES, 'page_limit_exceeded')
            payload = None
            for attempt in range(1, capture.MAX_ATTEMPTS + 1):
                _require(pointer < len(receipts), 'missing_source_page_receipt')
                receipt = receipts[pointer]
                pointer += 1
                _require(isinstance(receipt, dict)
                         and (receipt.get('batch_index'), receipt.get('page_index'), receipt.get('attempt'))
                         == (batch_index, page_index, attempt), 'page_receipt_sequence_mismatch')
                basename = f'batch-{batch_index:03d}-page-{page_index:02d}-attempt-{attempt}'
                body_name, receipt_name = basename + '.body', basename + '.receipt.json'
                _require(receipt.get('body_file') == body_name
                         and {body_name, receipt_name} <= set(files), 'missing_or_foreign_raw_page')
                _require(_json(files[receipt_name]) == receipt, 'receipt_file_content_mismatch')
                referenced_files.update((body_name, receipt_name))
                body = files[body_name]
                _require(receipt.get('body_sha256') == sha256(body)
                         and receipt.get('body_bytes') == len(body), 'raw_page_bytes_mismatch')
                expected_parameters = {'symbols': ','.join(requested_symbols), 'timeframe': '1Day',
                                       'start': start, 'end': end, 'feed': 'sip',
                                       'adjustment': 'raw', 'sort': 'asc', 'limit': 10000}
                _require(receipt.get('requested_symbols') == requested_symbols
                         and receipt.get('parameters') == expected_parameters,
                         'source_request_parameters_changed')
                _require(receipt.get('page_token_present') is (page_token is not None),
                         'request_page_token_presence_mismatch')
                requested_at, received_at = receipt.get('requested_at'), receipt.get('received_at')
                protocol_tools.check_clock(protocol, day, requested_at, 'preopen')
                protocol_tools.check_clock(protocol, day, received_at, 'preopen')
                requested_time, received_time = map(protocol_tools.timestamp, (requested_at, received_at))
                _require(requested_time <= received_time <= protocol_tools.timestamp(sealed_at),
                         'source_receipt_chronology_invalid')
                _require(previous_received is None or requested_time >= previous_received,
                         'source_receipt_order_regressed')
                previous_received, final_received = received_time, received_at
                first_requested = first_requested or requested_at
                status, transport_error = receipt.get('http_status'), receipt.get('transport_error')
                _require(type(transport_error) is bool and (status is None or type(status) is int),
                         'invalid_source_transport_status')
                if status == 200 and not transport_error:
                    try:
                        payload = capture.strict_source_json(body)
                    except (ValueError, UnicodeDecodeError):
                        raise SourceVerificationError('invalid_successful_source_json') from None
                    successful_pages += 1
                    break
                _require(status not in (401, 403), 'authorization_error_in_accepted_capture')
                _require((transport_error and status is None and body == b'')
                         or (not transport_error and (status == 429 or
                                                     (status is not None and 500 <= status <= 599))),
                         'nonretryable_error_in_accepted_capture')
                retry_errors += 1
            _require(payload is not None, 'successful_page_missing')
            _require(isinstance(payload, dict) and isinstance(payload.get('bars'), dict),
                     'malformed_successful_bars')
            for symbol, bars in payload['bars'].items():
                _require(symbol in daily, 'source_symbol_outside_requested_batch')
                _require(isinstance(bars, list), 'source_symbol_bars_not_list')
                normalized = [capture.normalize_bar(bar) for bar in bars]
                daily[symbol].extend(normalized)
                rebuilt[symbol].extend(normalized)
            next_token = payload.get('next_page_token')
            page_index += 1
            if next_token is None:
                break
            _require(isinstance(next_token, str) and bool(next_token), 'invalid_pagination_token')
            _require(next_token not in seen_tokens, 'repeated_pagination_token')
            seen_tokens.add(next_token)
            page_token = next_token
        batch_name = f'batch-{batch_index:03d}-normalized.json'
        normalized_bytes = capture.canonical_bytes(daily)
        _require(batch_name in files and files[batch_name] == normalized_bytes,
                 'normalized_batch_bytes_mismatch')
        referenced_files.add(batch_name)
        expected_request = {'requested_at': first_requested, 'received_at': final_received,
                            'requested_symbols': requested_symbols, 'http_status': 200,
                            'complete': True, 'body_sha256': sha256(normalized_bytes),
                            'merged_normalized_body_file': batch_name, 'page_count': page_index}
        _require(requests[batch_index] == expected_request, 'normalized_batch_receipt_mismatch')
        coverage.update(requested_symbols)
    _require(pointer == len(receipts), 'unreferenced_source_page_receipts')
    _require(set(coverage) == set(symbols) and all(value == 1 for value in coverage.values()),
             'source_batch_panel_coverage_mismatch')
    _require(bundle.get('daily') == rebuilt, 'bundle_daily_differs_from_raw_pages')
    _require(referenced_files == set(files), 'unreferenced_or_unexpected_source_files')
    recomputed_seal = protocol_tools.seal_selection(protocol, day, panel, bundle, sealed_at)
    _require(recomputed_seal.get('accepted') is True and recomputed_seal == seal,
             'selection_seal_recomputation_mismatch')
    _require(manifest.get('selected_count') == len(seal['selection']['selected_symbols']),
             'capture_selected_count_mismatch')
    result = source_manifest(files)
    result.update(accepted=True, date=day, protocol_sha256=sha256(protocol_bytes),
                  seal_content_digest=seal['seal_content_digest'],
                  selection_seal_sha256=sha256(files['selection-seal.json']),
                  source_bundle_sha256=sha256(files['source-bundle.json']),
                  source_bundle_digest=seal['source_bundle_digest'],
                  source_requests=len(receipts), successful_pages=successful_pages,
                  preserved_retry_error_pages=retry_errors, completed_batches=len(batches),
                  requested_symbols=len(symbols), selected_symbols=manifest['selected_count'],
                  raw_response_bytes_reconstructed=True, external_receipt_time_verified=False,
                  page_token_evidence='Response token chain and request presence only; frozen receipts do not retain requested token text.',
                  actual_fill=False, orders_sent=0, account_reads=0)
    return result


def verify_preopen_sources(protocol_path, panel_path, day, capture_dir, *,
                           expected_protocol_sha256=REGISTERED_PROTOCOL_SHA256):
    """Verify frozen inputs, all successful/error pages, then recompute seal.

    ``expected_protocol_sha256`` defaults to the independently registered bytes.
    The override exists for synthetic offline fixtures, not runtime rebinding.
    """
    try:
        return _verify_preopen_sources(protocol_path, panel_path, day, capture_dir,
                                       expected_protocol_sha256)
    except SourceVerificationError:
        raise
    except capture.CaptureError:
        raise SourceVerificationError('frozen_capture_binding_or_encoding_invalid') from None
    except (ValueError, TypeError, KeyError, OSError, AttributeError):
        raise SourceVerificationError('malformed_or_unavailable_source_artifact') from None


def _decimal_values(value):
    """Independent minute merge: preserve provider timestamps and every row."""
    if isinstance(value, Decimal):
        return str(capture.checked_decimal(value))
    if isinstance(value, list):
        return [_decimal_values(item) for item in value]
    if isinstance(value, dict):
        return {key: _decimal_values(item) for key, item in value.items()}
    return value


def _verify_postclose_sources(protocol_path, day, seal_path, capture_dir, now,
                              expected_protocol_sha256):
    protocol_path, seal_path = Path(protocol_path), Path(seal_path)
    preopen = verify_preopen_sources(protocol_path, protocol_path.parent / 'fixed-panel.json',
                                     day, seal_path.parent,
                                     expected_protocol_sha256=expected_protocol_sha256)
    _require(seal_path.name == 'selection-seal.json', 'foreign_selection_seal_filename')
    protocol, seal = _json_file(protocol_path), _json_file(seal_path)
    session = protocol_tools.session(protocol, day)
    symbols = seal['selection']['selected_symbols']
    files = _inventory(capture_dir)
    fixed = {'normalized-source.json', 'postclose-receipt.json', 'capture-manifest.json'}
    _require(fixed <= set(files), 'missing_postclose_artifact')
    source, aggregate, manifest = (_json(files[name]) for name in
                                  ('normalized-source.json', 'postclose-receipt.json', 'capture-manifest.json'))
    _require(manifest.get('status') == 'accepted' and manifest.get('accepted') is True
             and manifest.get('source_complete') is True and manifest.get('date') == day,
             'postclose_capture_not_accepted')
    _require(manifest.get('actual_fill') is False and manifest.get('orders_sent') == 0
             and manifest.get('account_reads') == 0 and manifest.get('raw_bodies_private') is True
             and manifest.get('minute_completeness_claimed') is False
             and manifest.get('observation_status') == 'source_metadata_accepted_pending_bar_validation',
             'postclose_manifest_policy_mismatch')
    receipts = source.get('page_receipts')
    _require(isinstance(receipts, list) and bool(receipts)
             and receipts == manifest.get('page_receipts'), 'postclose_raw_receipt_lists_mismatch')
    _require(len(receipts) == manifest.get('get_count') <= 60, 'postclose_get_count_mismatch')
    opening, closing = map(protocol_tools.timestamp, (session['open_utc'], session['close_utc']))
    _require(closing > opening and not opening.microsecond and not closing.microsecond,
             'invalid_registered_session_window')
    parameters = {'symbols': ','.join(symbols), 'timeframe': '1Min',
                  'start': opening.strftime('%Y-%m-%dT%H:%M:%SZ'),
                  'end': (closing - timedelta(seconds=1)).strftime('%Y-%m-%dT%H:%M:%S') + '.999999999Z',
                  'feed': 'sip', 'adjustment': 'raw', 'sort': 'asc', 'limit': 10000}
    rebuilt = {symbol: [] for symbol in symbols}
    referenced, pointer, successful_pages, errors = set(fixed), 0, 0, 0
    first_requested, final_received, previous_received = None, None, None
    page_index, page_token, seen_tokens = 0, None, set()
    while True:
        _require(page_index < 20, 'postclose_page_limit_exceeded')
        payload = None
        for attempt in range(1, 4):
            _require(pointer < len(receipts), 'postclose_page_receipt_missing')
            receipt = receipts[pointer]
            pointer += 1
            _require(isinstance(receipt, dict)
                     and (receipt.get('page_index'), receipt.get('attempt')) == (page_index, attempt),
                     'postclose_page_receipt_sequence_mismatch')
            base = f'page-{page_index:02d}-attempt-{attempt}'
            body_name, receipt_name = base + '.body', base + '.receipt.json'
            _require(receipt.get('body_file') == body_name and {body_name, receipt_name} <= set(files),
                     'postclose_raw_page_missing_or_foreign')
            body = files[body_name]
            referenced.update((body_name, receipt_name))
            _require(_json(files[receipt_name]) == receipt, 'postclose_receipt_file_mismatch')
            _require(receipt.get('body_sha256') == sha256(body) and receipt.get('body_bytes') == len(body),
                     'postclose_raw_page_bytes_mismatch')
            _require(receipt.get('requested_symbols') == symbols and receipt.get('parameters') == parameters,
                     'postclose_request_parameters_changed')
            _require(receipt.get('page_token_present') is (page_token is not None),
                     'postclose_request_page_token_presence_mismatch')
            requested_at, received_at = receipt.get('requested_at'), receipt.get('received_at')
            protocol_tools.check_clock(protocol, day, requested_at, 'postclose')
            protocol_tools.check_clock(protocol, day, received_at, 'postclose')
            requested_time, received_time = map(protocol_tools.timestamp, (requested_at, received_at))
            _require(requested_time <= received_time <= protocol_tools.timestamp(now),
                     'postclose_receipt_chronology_invalid')
            _require(previous_received is None or requested_time >= previous_received,
                     'postclose_receipt_order_regressed')
            previous_received, final_received = received_time, received_at
            first_requested = first_requested or requested_at
            status, transport_error = receipt.get('http_status'), receipt.get('transport_error')
            _require(type(transport_error) is bool and (status is None or type(status) is int),
                     'postclose_invalid_transport_status')
            if status == 200 and not transport_error:
                try:
                    payload = capture.strict_source_json(body)
                except (ValueError, UnicodeDecodeError):
                    raise SourceVerificationError('postclose_invalid_successful_source_json') from None
                successful_pages += 1
                break
            _require(status not in (401, 403), 'postclose_authorization_error_in_accepted_capture')
            _require((transport_error and status is None and body == b'')
                     or (not transport_error and (status == 429 or
                                                 (status is not None and 500 <= status <= 599))),
                     'postclose_nonretryable_error_in_accepted_capture')
            errors += 1
        _require(payload is not None, 'postclose_successful_page_missing')
        _require(isinstance(payload, dict) and isinstance(payload.get('bars'), dict),
                 'postclose_malformed_successful_bars')
        for symbol, bars in payload['bars'].items():
            _require(symbol in rebuilt, 'postclose_symbol_outside_sealed_selection')
            _require(isinstance(bars, list), 'postclose_symbol_bars_not_list')
            rebuilt[symbol].extend(_decimal_values(bars))
        next_token = payload.get('next_page_token')
        page_index += 1
        if next_token is None:
            break
        _require(isinstance(next_token, str) and bool(next_token), 'postclose_invalid_pagination_token')
        _require(next_token not in seen_tokens, 'postclose_repeated_pagination_token')
        seen_tokens.add(next_token)
        page_token = next_token
    _require(pointer == len(receipts), 'postclose_unreferenced_page_receipts')
    expected_source = {'protocol_id': protocol['id'], 'date': day,
                       'seal_content_digest': seal['seal_content_digest'],
                       'feed': 'sip', 'adjustment': 'raw', 'timeframe': '1Min',
                       'requested_symbols': symbols, 'source_complete': True,
                       'bars': rebuilt, 'page_receipts': receipts,
                       'bar_window_start_utc': session['open_utc'],
                       'bar_window_end_exclusive_utc': session['close_utc'],
                       'provider_timestamps_preserved': True,
                       'normalization': 'Only JSON Decimal numbers become exact strings; provider t, all rows and row order remain unchanged.'}
    expected_source_bytes = capture.canonical_bytes(expected_source) + b'\n'
    _require(files['normalized-source.json'] == expected_source_bytes,
             'postclose_normalized_source_bytes_mismatch')
    expected_aggregate = {'session_date': day, 'protocol_id': protocol['id'],
                          'seal_content_digest': seal['seal_content_digest'],
                          'requested_at': first_requested, 'received_at': final_received,
                          'requested_symbols': symbols, 'http_status': 200,
                          'source_complete': True, 'feed': 'sip', 'adjustment': 'raw',
                          'timeframe': '1Min', 'body_sha256': sha256(expected_source_bytes),
                          'body_file': 'normalized-source.json', 'body_bytes': len(expected_source_bytes),
                          'body_hash_definition': 'SHA256 of exact normalized-source.json bytes including trailing newline; original response bytes have separate page body hashes.',
                          'bar_window_start_utc': session['open_utc'],
                          'bar_window_end_exclusive_utc': session['close_utc'],
                          'page_count': page_index, 'get_count': len(receipts)}
    _require(aggregate == expected_aggregate, 'postclose_aggregate_receipt_mismatch')
    validation = protocol_tools.validate_postclose(protocol, day, seal, aggregate, now)
    _require(validation.get('accepted') is True and manifest.get('validation') == validation
             and manifest.get('reason') == validation['reason'], 'postclose_metadata_revalidation_failed')
    _require(manifest.get('selected_count') == len(symbols), 'postclose_selected_count_mismatch')
    _require(set(files) == referenced, 'postclose_unreferenced_or_unexpected_source_files')
    result = source_manifest(files)
    result.update(accepted=True, date=day, normalized_source_sha256=sha256(expected_source_bytes),
                  preopen_seal_content_digest=preopen['seal_content_digest'],
                  source_requests=len(receipts), successful_pages=successful_pages,
                  preserved_retry_error_pages=errors, selected_symbols=len(symbols),
                  raw_response_bytes_reconstructed=True, external_receipt_time_verified=False,
                  minute_completeness_claimed=False,
                  page_token_evidence='Response token chain and request presence only; frozen receipts do not retain requested token text.',
                  actual_fill=False, orders_sent=0, account_reads=0)
    return result


def verify_postclose_sources(protocol_path, day, seal_path, capture_dir, *, now=None,
                             expected_protocol_sha256=REGISTERED_PROTOCOL_SHA256):
    """Independently merge raw minute pages; reverify preopen bytes and seal.

    Complete pagination does not imply complete minutes or valid market bars.
    The inherited bar engine retains that separate validity responsibility.
    ``now`` permits later offline review; every source receipt must be on time.
    """
    try:
        return _verify_postclose_sources(protocol_path, day, seal_path, capture_dir,
                                        now or capture.utc_now(), expected_protocol_sha256)
    except SourceVerificationError:
        raise
    except capture.CaptureError:
        raise SourceVerificationError('postclose_capture_binding_or_encoding_invalid') from None
    except (ValueError, TypeError, KeyError, OSError, AttributeError):
        raise SourceVerificationError('malformed_or_unavailable_postclose_source_artifact') from None
