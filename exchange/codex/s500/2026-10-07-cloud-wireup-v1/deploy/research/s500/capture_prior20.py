"""Same-day preopen GET-only acquisition for the frozen future protocol.

Run only on a registered future session before its 09:20 America/New_York
cutoff. Every page, including errors, stays in a new private output directory.
No account, asset, order, option, scheduler, or postclose endpoint is present.
"""
import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

import protocol_tools
from prospect_selection import NUMERIC_MAX_DIGITS, NUMERIC_MAX_ABS_EXPONENT

ROOT = Path(__file__).resolve().parent
ET = ZoneInfo('America/New_York')
ENDPOINT = 'https://data.alpaca.markets/v2/stocks/bars'
BATCH_SIZE = 64
MAX_BATCHES = 110
MAX_PAGES = 20
MAX_ATTEMPTS = 3
MAX_GETS = 330
MIN_SPACING_SECONDS = 0.5
REQUIRED_BINDINGS = ('capture_prior20.py', 'protocol_tools.py',
                     'prospect_selection.py', 'fixed-panel.json')


class CaptureError(ValueError):
    """A stable reason code, never a raw URL, body, exception, or credential."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def checked_decimal(value):
    """Match the selector's bounded numeric encoding without float conversion."""
    try:
        value = value if isinstance(value, Decimal) else Decimal(value)
    except (InvalidOperation, ValueError):
        raise CaptureError('numeric_encoding_outside_bounds') from None
    if not value.is_finite():
        raise CaptureError('nonfinite_number_in_response')
    parsed = value.as_tuple()
    if (len(parsed.digits) > NUMERIC_MAX_DIGITS
            or abs(parsed.exponent) > NUMERIC_MAX_ABS_EXPONENT):
        raise CaptureError('numeric_encoding_outside_bounds')
    return value


class ExactDecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(checked_decimal(obj))
        return super().default(obj)


def canonical_bytes(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False,
                      cls=ExactDecimalEncoder).encode()


def hash_bytes(body):
    return hashlib.sha256(body).hexdigest()


def strict_source_json(body):
    """Duplicate JSON keys and nonfinite numbers must not disappear in parsing."""
    def object_from_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise CaptureError('duplicate_json_key_in_response')
            result[key] = value
        return result
    def integer(text):
        checked_decimal(text)
        return int(text)
    def constant(_):
        raise CaptureError('nonfinite_number_in_response')
    return json.loads(body, object_pairs_hook=object_from_pairs,
                      parse_float=checked_decimal, parse_int=integer,
                      parse_constant=constant)


def private_bytes(path, body):
    """Exclusive private creation; never replace a previous source or seal."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as handle:
        handle.write(body)


def private_json(path, value):
    private_bytes(path, canonical_bytes(value) + b'\n')


def check_bindings(protocol, root, panel_path):
    bound = protocol.get('bound_file_sha256')
    if not isinstance(bound, dict) or not set(REQUIRED_BINDINGS) <= set(bound):
        raise CaptureError('missing_required_method_bindings')
    if Path(panel_path).resolve() != (root / 'fixed-panel.json').resolve():
        raise CaptureError('panel_path_not_registered_file')
    for relative, expected in bound.items():
        if not isinstance(relative, str) or Path(relative).is_absolute():
            raise CaptureError('invalid_binding_path')
        path = (root / relative).resolve()
        if not path.is_relative_to(root.resolve()):
            raise CaptureError('invalid_binding_path')
        if (not isinstance(expected, str) or len(expected) != 64
                or any(char not in '0123456789abcdef' for char in expected)):
            raise CaptureError('invalid_binding_hash')
        try:
            actual = hash_bytes(path.read_bytes())
        except OSError:
            raise CaptureError('bound_file_unavailable') from None
        if actual != expected:
            raise CaptureError('bound_file_hash_mismatch')


def request_window(prior20, day):
    start = datetime.fromisoformat(prior20[0] + 'T00:00').replace(tzinfo=ET)
    end = datetime.fromisoformat(day + 'T00:00').replace(tzinfo=ET)
    start_text = start.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    previous_second = end.astimezone(timezone.utc) - timedelta(seconds=1)
    end_text = previous_second.strftime('%Y-%m-%dT%H:%M:%S') + '.999999999Z'
    return start_text, end_text


def normalize_bar(bar):
    """Keep malformed and duplicate rows visible to the selection validator."""
    if not isinstance(bar, dict):
        return bar
    def normalized_value(value):
        # Exact decimal strings are supported by the selector and by the
        # ordinary JSON digest used in seal_selection. This applies only to
        # Decimal values; other malformed input retains its type for rejection.
        if isinstance(value, Decimal):
            return str(checked_decimal(value))
        if isinstance(value, list):
            return [normalized_value(item) for item in value]
        if isinstance(value, dict):
            return {key: normalized_value(item) for key, item in value.items()}
        return value
    normalized = normalized_value(bar)
    try:
        stamp = protocol_tools.timestamp(bar.get('t'))
        normalized['session_date'] = stamp.astimezone(ET).date().isoformat()
    except (ValueError, TypeError):
        normalized['session_date'] = None
        normalized['timestamp_normalization_error'] = True
    return normalized


def make_transport():
    # Default opener components preserve inherited proxy and default TLS trust.
    # Replacing only the redirect handler prevents credential-bearing redirects.
    opener = urllib.request.build_opener(NoRedirect())
    def transport(request):
        try:
            with opener.open(request, timeout=30) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()
    return transport


def capture(protocol_path, panel_path, day, output_dir, *, transport=None,
            now=utc_now, monotonic=time.monotonic, sleep=time.sleep,
            credentials=None):
    """Capture all panel batches, then and only then seal a deterministic pool.

    Injectable clocks, transport and credentials exist for offline tests.
    CLI execution uses the real UTC clock and existing environment credentials.
    Failed capture artifacts are retained. All exception details are suppressed;
    only documented stable status codes enter the public-facing return value.
    """
    output_dir = Path(output_dir)
    try:
        output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    except FileExistsError:
        return {'status': 'rejected', 'reason': 'output_directory_already_exists',
                'get_count': 0, 'accepted': False}
    manifest = {'date': day, 'status': 'failed', 'reason': None,
                'get_count': 0, 'completed_batches': 0, 'page_receipts': [],
                'source_complete': False, 'accepted': False,
                'actual_fill': False, 'orders_sent': 0, 'account_reads': 0,
                'source': 'SIP/raw/1Day prior20 full frozen panel only',
                'raw_bodies_private': True}
    bundle = None
    try:
        protocol_path = Path(protocol_path)
        protocol = json.loads(protocol_path.read_text())
        # This must precede credentials, transport construction, and every GET.
        session = protocol_tools.check_clock(protocol, day, now(), 'preopen')
        root = protocol_path.resolve().parent
        check_bindings(protocol, root, panel_path)
        panel = json.loads(Path(panel_path).read_text())
        symbols = [row['symbol'] for row in panel]
        if (len(symbols) != protocol['fixed_panel_symbols']
                or len(symbols) != len(set(symbols))
                or protocol_tools.digest(panel) != protocol['fixed_panel_content_digest']):
            raise CaptureError('registered_panel_mismatch')
        batches = [symbols[i:i + BATCH_SIZE] for i in range(0, len(symbols), BATCH_SIZE)]
        if not batches or len(batches) > MAX_BATCHES:
            raise CaptureError('batch_limit_exceeded')
        start, end = request_window(session['prior20_dates'], day)
        bundle = {'protocol_id': protocol['id'], 'date': day,
                  'panel_content_digest': protocol_tools.digest(panel),
                  'prior20_dates': session['prior20_dates'], 'feed': 'sip',
                  'adjustment': 'raw', 'timeframe': '1Day',
                  'requested_symbols': symbols, 'source_complete': False,
                  'daily': {symbol: [] for symbol in symbols}, 'requests': [],
                  'raw_page_receipts': [],
                  'batch_body_sha256_definition': 'SHA256 of canonical JSON merged normalized batch source; individual untouched response bytes have separate page body_sha256 values.'}
        # Environment values can be proxy placeholders. Never inspect or log them.
        if credentials is None:
            try:
                credentials = (os.environ['ALPACA_500_API_KEY'],
                               os.environ['ALPACA_500_SECRET_KEY'])
            except KeyError:
                raise CaptureError('configured_credentials_unavailable') from None
        if not isinstance(credentials, tuple) or len(credentials) != 2:
            raise CaptureError('invalid_credential_configuration')
        if transport is None:
            transport = make_transport()
        last_start = None
        for batch_index, requested_symbols in enumerate(batches):
            batch_daily = {symbol: [] for symbol in requested_symbols}
            page_token = None
            seen_tokens = set()
            first_requested = None
            final_received = None
            for page_index in range(MAX_PAGES):
                params = {'symbols': ','.join(requested_symbols), 'timeframe': '1Day',
                          'start': start, 'end': end, 'feed': 'sip',
                          'adjustment': 'raw', 'sort': 'asc', 'limit': 10000}
                if page_token is not None:
                    params['page_token'] = page_token
                response_body = None
                for attempt in range(1, MAX_ATTEMPTS + 1):
                    protocol_tools.check_clock(protocol, day, now(), 'preopen')
                    if manifest['get_count'] >= MAX_GETS:
                        raise CaptureError('total_get_limit_exceeded')
                    if last_start is not None:
                        delay = MIN_SPACING_SECONDS - (monotonic() - last_start)
                        if delay > 0:
                            sleep(delay)
                    requested_at = now()
                    protocol_tools.check_clock(protocol, day, requested_at, 'preopen')
                    first_requested = first_requested or requested_at
                    request = urllib.request.Request(
                        ENDPOINT + '?' + urllib.parse.urlencode(params),
                        headers={'APCA-API-KEY-ID': credentials[0],
                                 'APCA-API-SECRET-KEY': credentials[1]}, method='GET')
                    last_start = monotonic()
                    manifest['get_count'] += 1
                    status = None
                    transport_error = False
                    try:
                        status, response_body = transport(request)
                        if not isinstance(status, int) or not isinstance(response_body, bytes):
                            raise CaptureError('invalid_transport_result')
                    except (urllib.error.URLError, TimeoutError, OSError):
                        transport_error = True
                        response_body = b''
                    received_at = now()
                    final_received = received_at
                    basename = f'batch-{batch_index:03d}-page-{page_index:02d}-attempt-{attempt}'
                    private_bytes(output_dir / (basename + '.body'), response_body)
                    receipt = {'batch_index': batch_index, 'page_index': page_index,
                               'attempt': attempt, 'requested_at': requested_at,
                               'received_at': received_at, 'http_status': status,
                               'transport_error': transport_error,
                               'body_file': basename + '.body',
                               'body_sha256': hash_bytes(response_body),
                               'body_bytes': len(response_body),
                               'requested_symbols': requested_symbols,
                               'page_token_present': page_token is not None,
                               'parameters': {key: value for key, value in params.items()
                                              if key != 'page_token'}}
                    private_json(output_dir / (basename + '.receipt.json'), receipt)
                    manifest['page_receipts'].append(receipt)
                    bundle['raw_page_receipts'].append(receipt)
                    # Persist source bytes before rejecting responses past deadline.
                    protocol_tools.check_clock(protocol, day, received_at, 'preopen')
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
                    normalized = [normalize_bar(bar) for bar in bars]
                    batch_daily.setdefault(symbol, []).extend(normalized)
                    bundle['daily'].setdefault(symbol, []).extend(normalized)
                    # Unknown symbols remain present; sealing fails the full-key gate.
                    # A known symbol returned from a different batch is never merged
                    # into an apparently complete normal source.
                    if symbol in symbols and symbol not in requested_symbols:
                        raise CaptureError('response_symbol_outside_requested_batch')
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
            batch_bytes = canonical_bytes(batch_daily)
            batch_name = f'batch-{batch_index:03d}-normalized.json'
            private_bytes(output_dir / batch_name, batch_bytes)
            bundle['requests'].append({'requested_at': first_requested,
                                       'received_at': final_received,
                                       'requested_symbols': requested_symbols,
                                       'http_status': 200, 'complete': True,
                                       'body_sha256': hash_bytes(batch_bytes),
                                       'merged_normalized_body_file': batch_name,
                                       'page_count': page_index + 1})
            manifest['completed_batches'] += 1
        seal_time = now()
        protocol_tools.check_clock(protocol, day, seal_time, 'preopen')
        bundle['source_complete'] = True
        private_json(output_dir / 'source-bundle.json', bundle)
        manifest['source_complete'] = True
        seal = protocol_tools.seal_selection(protocol, day, panel, bundle, seal_time)
        private_json(output_dir / 'selection-seal.json', seal)
        manifest.update(status='accepted' if seal['accepted'] else 'rejected',
                        reason=seal['reason'], accepted=seal['accepted'])
        if seal['accepted']:
            manifest['selected_count'] = len(seal['selection']['selected_symbols'])
    except (CaptureError, ValueError, TypeError, KeyError, OSError) as error:
        known_clock_codes = {'date_not_in_registered_calendar', 'capture_must_run_on_target_date',
                             'protocol_not_registered', 'registration_not_before_session',
                             'capture_before_registration',
                             'preopen_deadline_missed', 'timestamp_must_be_text',
                             'timestamp_must_have_timezone'}
        reason = str(error)
        manifest['reason'] = reason if isinstance(error, CaptureError) or reason in known_clock_codes else 'invalid_local_or_source_input'
        if bundle is not None and not (output_dir / 'source-bundle.json').exists():
            bundle['source_complete'] = False
            private_json(output_dir / 'source-bundle.json', bundle)
    private_json(output_dir / 'capture-manifest.json', manifest)
    return {key: manifest[key] for key in ('status', 'reason', 'get_count',
                                          'completed_batches', 'accepted')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', required=True)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    result = capture(ROOT / 'protocol.json', ROOT / 'fixed-panel.json',
                     args.date, args.output_dir)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['accepted'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
