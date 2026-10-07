"""Bounded GET-only SIP/raw/1Min capture of the same-day sealed selection.

Private source preservation only. No account, order, option or scheduler endpoint.
Complete means completed pagination, never complete minutes or valid observations.
"""
import argparse
from datetime import timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request

import protocol_tools
from capture_prior20 import (CaptureError, canonical_bytes, check_bindings,
                             checked_decimal, hash_bytes, make_transport,
                             private_bytes, private_json, strict_source_json,
                             utc_now)

ROOT = Path(__file__).resolve().parent
ENDPOINT = 'https://data.alpaca.markets/v2/stocks/bars'
ORIGINAL_PROTOCOL_SHA256 = '2916ce06a0db24e18a754abae8943470ba9419e914b11d03d2f05d99f64e6d71'
MAX_PAGES = 20
MAX_ATTEMPTS = 3
MAX_GETS = 60
MIN_SPACING_SECONDS = 0.5


def normalize_values(value):
    """Retain provider timestamp precision, invalid rows, ordering and duplicates."""
    if isinstance(value, Decimal):
        return str(checked_decimal(value))
    if isinstance(value, list):
        return [normalize_values(item) for item in value]
    if isinstance(value, dict):
        return {key: normalize_values(item) for key, item in value.items()}
    return value


def source_window(session):
    opening = protocol_tools.timestamp(session['open_utc'])
    close = protocol_tools.timestamp(session['close_utc'])
    if close <= opening or opening.microsecond or close.microsecond:
        raise CaptureError('invalid_registered_session_window')
    return (opening.strftime('%Y-%m-%dT%H:%M:%SZ'),
            (close - timedelta(seconds=1)).strftime('%Y-%m-%dT%H:%M:%S') + '.999999999Z')


def load_sealed_selection(protocol_path, seal_path, day):
    """Check registered bytes, every bound method and deterministic seal content.

    The orchestrator additionally verifies the prior20 raw-body provenance before
    calling capture. This method does not authenticate a forged receipt's clock.
    """
    protocol_path, seal_path = Path(protocol_path), Path(seal_path)
    original = protocol_path.read_bytes()
    if hash_bytes(original) != ORIGINAL_PROTOCOL_SHA256:
        raise CaptureError('original_protocol_hash_mismatch')
    protocol = json.loads(original)
    root = protocol_path.resolve().parent
    check_bindings(protocol, root, root / 'fixed-panel.json')
    panel = json.loads((root / 'fixed-panel.json').read_text())
    seal = json.loads(seal_path.read_text())
    if (not isinstance(seal, dict)
            or seal.get('accepted') is not True or seal.get('date') != day
            or seal.get('protocol_id') != protocol['id']
            or seal.get('protocol_digest') != protocol_tools.digest(protocol)):
        raise CaptureError('missing_or_foreign_preopen_seal')
    expected_digest = protocol_tools.digest({k: v for k, v in seal.items()
                                             if k != 'seal_content_digest'})
    if seal.get('seal_content_digest') != expected_digest:
        raise CaptureError('preopen_seal_content_changed')
    protocol_tools.check_clock(protocol, day, seal['sealed_at'], 'preopen')
    bundle = json.loads((seal_path.parent / 'source-bundle.json').read_text())
    if seal.get('source_bundle_digest') != protocol_tools.digest(bundle):
        raise CaptureError('preopen_source_bundle_changed')
    rebuilt = protocol_tools.seal_selection(protocol, day, panel, bundle, seal['sealed_at'])
    if rebuilt != seal:
        raise CaptureError('preopen_seal_recomputation_mismatch')
    selected = seal['selection']['selected_symbols']
    if (not isinstance(selected, list) or not selected
            or any(not isinstance(symbol, str) or not symbol for symbol in selected)
            or len(set(selected)) != len(selected)
            or not {'SPY', 'QQQ'} <= set(selected)
            or not set(selected) <= ({row['symbol'] for row in panel}
                                     | set(protocol['selection']['controls']))):
        raise CaptureError('invalid_sealed_selection')
    return protocol, seal, selected


def capture(protocol_path, seal_path, day, output_dir, *, transport=None,
            now=utc_now, monotonic=time.monotonic, sleep=time.sleep,
            credentials=None):
    """Acquire once; retain incomplete/late/error evidence without filling gaps.

    All clocks and transport are injectable for offline tests. Credentials are
    headers only, with redirects disabled and inherited proxy/TLS unchanged.
    """
    output_dir = Path(output_dir)
    try:
        output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    except FileExistsError:
        return {'status': 'rejected', 'reason': 'output_directory_already_exists',
                'get_count': 0, 'source_complete': False, 'accepted': False}
    manifest = {'date': day, 'status': 'failed', 'reason': None, 'get_count': 0,
                'page_receipts': [], 'source_complete': False, 'accepted': False,
                'actual_fill': False, 'orders_sent': 0, 'account_reads': 0,
                'raw_bodies_private': True, 'observation_status': 'unknown',
                'source': 'SIP/raw/1Min same-day sealed symbols only',
                'minute_completeness_claimed': False}
    source, receipt = None, None
    try:
        protocol, seal, symbols = load_sealed_selection(protocol_path, seal_path, day)
        session = protocol_tools.check_clock(protocol, day, now(), 'postclose')
        start, end = source_window(session)
        source = {'protocol_id': protocol['id'], 'date': day,
                  'seal_content_digest': seal['seal_content_digest'],
                  'feed': 'sip', 'adjustment': 'raw', 'timeframe': '1Min',
                  'requested_symbols': symbols, 'source_complete': False,
                  'bars': {symbol: [] for symbol in symbols}, 'page_receipts': [],
                  'bar_window_start_utc': session['open_utc'],
                  'bar_window_end_exclusive_utc': session['close_utc'],
                  'provider_timestamps_preserved': True,
                  'normalization': 'Only JSON Decimal numbers become exact strings; provider t, all rows and row order remain unchanged.'}
        if credentials is None:
            try:
                credentials = (os.environ['ALPACA_500_API_KEY'],
                               os.environ['ALPACA_500_SECRET_KEY'])
            except KeyError:
                raise CaptureError('configured_credentials_unavailable') from None
        if (not isinstance(credentials, tuple) or len(credentials) != 2
                or any(not isinstance(value, str) or not value for value in credentials)):
            raise CaptureError('invalid_credential_configuration')
        transport = transport if transport is not None else make_transport()
        first_requested, final_received, last_start = None, None, None
        page_token, seen_tokens = None, set()
        for page_index in range(MAX_PAGES):
            params = {'symbols': ','.join(symbols), 'timeframe': '1Min',
                      'start': start, 'end': end, 'feed': 'sip',
                      'adjustment': 'raw', 'sort': 'asc', 'limit': 10000}
            if page_token is not None:
                params['page_token'] = page_token
            response_body = None
            for attempt in range(1, MAX_ATTEMPTS + 1):
                protocol_tools.check_clock(protocol, day, now(), 'postclose')
                if manifest['get_count'] >= MAX_GETS:
                    raise CaptureError('total_get_limit_exceeded')
                if last_start is not None:
                    delay = MIN_SPACING_SECONDS - (monotonic() - last_start)
                    if delay > 0:
                        sleep(delay)
                requested_at = now()
                protocol_tools.check_clock(protocol, day, requested_at, 'postclose')
                first_requested = first_requested or requested_at
                request = urllib.request.Request(
                    ENDPOINT + '?' + urllib.parse.urlencode(params),
                    headers={'APCA-API-KEY-ID': credentials[0],
                             'APCA-API-SECRET-KEY': credentials[1]}, method='GET')
                last_start = monotonic()
                manifest['get_count'] += 1
                status, transport_error = None, False
                try:
                    status, response_body = transport(request)
                    if type(status) is not int or not isinstance(response_body, bytes):
                        raise CaptureError('invalid_transport_result')
                except (urllib.error.URLError, TimeoutError, OSError):
                    transport_error, response_body = True, b''
                received_at = now()
                final_received = received_at
                basename = f'page-{page_index:02d}-attempt-{attempt}'
                private_bytes(output_dir / (basename + '.body'), response_body)
                page_receipt = {'page_index': page_index, 'attempt': attempt,
                                'requested_at': requested_at, 'received_at': received_at,
                                'http_status': status, 'transport_error': transport_error,
                                'body_file': basename + '.body',
                                'body_sha256': hash_bytes(response_body),
                                'body_bytes': len(response_body),
                                'requested_symbols': symbols,
                                'page_token_present': page_token is not None,
                                'parameters': {k: v for k, v in params.items() if k != 'page_token'}}
                private_json(output_dir / (basename + '.receipt.json'), page_receipt)
                manifest['page_receipts'].append(page_receipt)
                source['page_receipts'].append(page_receipt)
                protocol_tools.check_clock(protocol, day, received_at, 'postclose')
                if protocol_tools.timestamp(received_at) < protocol_tools.timestamp(requested_at):
                    raise CaptureError('receipt_clock_regressed')
                if status in (401, 403):
                    raise CaptureError('authorization_denied_no_retry')
                if status == 200 and not transport_error:
                    break
                retryable = transport_error or status == 429 or (status is not None and 500 <= status <= 599)
                if not retryable:
                    raise CaptureError('http_non_success_no_retry')
                if attempt == MAX_ATTEMPTS:
                    raise CaptureError('retry_limit_exhausted')
            try:
                payload = strict_source_json(response_body)
            except CaptureError:
                raise
            except (ValueError, UnicodeDecodeError):
                raise CaptureError('malformed_response_json') from None
            if not isinstance(payload, dict) or not isinstance(payload.get('bars'), dict):
                raise CaptureError('malformed_bars_mapping')
            for symbol, bars in payload['bars'].items():
                if not isinstance(bars, list):
                    raise CaptureError('malformed_symbol_bars')
                # Unexpected source rows remain visible in private raw evidence,
                # but cannot become accepted selected observations.
                if symbol not in source['bars']:
                    raise CaptureError('response_symbol_outside_sealed_selection')
                source['bars'][symbol].extend(normalize_values(bars))
            next_token = payload.get('next_page_token')
            if next_token is None:
                break
            if not isinstance(next_token, str) or not next_token:
                raise CaptureError('malformed_pagination_token')
            if next_token in seen_tokens:
                raise CaptureError('repeated_pagination_token')
            seen_tokens.add(next_token)
            page_token = next_token
        else:
            raise CaptureError('page_limit_exceeded')
        final_at = now()
        protocol_tools.check_clock(protocol, day, final_at, 'postclose')
        source['source_complete'] = True
        source_bytes = canonical_bytes(source) + b'\n'
        private_bytes(output_dir / 'normalized-source.json', source_bytes)
        receipt = {'session_date': day, 'protocol_id': protocol['id'],
                   'seal_content_digest': seal['seal_content_digest'],
                   'requested_at': first_requested, 'received_at': final_received,
                   'requested_symbols': symbols, 'http_status': 200,
                   'source_complete': True, 'feed': 'sip', 'adjustment': 'raw',
                   'timeframe': '1Min', 'body_sha256': hash_bytes(source_bytes),
                   'body_file': 'normalized-source.json', 'body_bytes': len(source_bytes),
                   'body_hash_definition': 'SHA256 of exact normalized-source.json bytes including trailing newline; original response bytes have separate page body hashes.',
                   'bar_window_start_utc': session['open_utc'],
                   'bar_window_end_exclusive_utc': session['close_utc'],
                   'page_count': page_index + 1, 'get_count': manifest['get_count']}
        private_json(output_dir / 'postclose-receipt.json', receipt)
        validation = protocol_tools.validate_postclose(protocol, day, seal, receipt, final_at)
        manifest.update(status='accepted' if validation['accepted'] else 'rejected',
                        reason=validation['reason'], accepted=validation['accepted'],
                        source_complete=True, validation=validation,
                        selected_count=len(symbols),
                        observation_status='source_metadata_accepted_pending_bar_validation'
                        if validation['accepted'] else 'unknown')
    except (CaptureError, ValueError, TypeError, KeyError, OSError) as error:
        clock_codes = {'date_not_in_registered_calendar', 'capture_must_run_on_target_date',
                       'protocol_not_registered', 'registration_not_before_session',
                       'capture_before_registration', 'preopen_deadline_missed',
                       'postclose_capture_outside_window', 'timestamp_must_be_text',
                       'timestamp_must_have_timezone'}
        reason = str(error)
        manifest['reason'] = reason if isinstance(error, CaptureError) or reason in clock_codes else 'invalid_local_or_source_input'
    if source is not None and not (output_dir / 'normalized-source.json').exists():
        source['source_complete'] = False
        private_json(output_dir / 'normalized-source.json', source)
    if not (output_dir / 'postclose-receipt.json').exists():
        private_json(output_dir / 'postclose-receipt.json', {
            'session_date': day, 'source_complete': False, 'accepted': False,
            'reason': manifest['reason'], 'observation_status': 'unknown',
            'actual_fill': False, 'get_count': manifest['get_count']})
    private_json(output_dir / 'capture-manifest.json', manifest)
    return {key: manifest[key] for key in ('status', 'reason', 'get_count',
                                          'source_complete', 'accepted')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', required=True)
    parser.add_argument('--seal-path', required=True)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    result = capture(ROOT / 'protocol.json', args.seal_path, args.date, args.output_dir)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['accepted'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
