"""One bounded cloud invocation of registered research, never an order executor.

Real CLI time only. Dependency injection below is exclusively for offline tests.
An irrevocable create-only reservation precedes market access. A crashed worker
leaves an unknown observation; another worker cannot reacquire its reservation.
"""
import argparse
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
import uuid
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

import protocol_tools
from capture_prior20 import (capture as preopen_capture, check_bindings,
                             make_transport, strict_source_json)
from capture_postclose import capture as postclose_capture
from analyze_session import analyze
from checkpoint import build_checkpoint
from state_store import GitHubState
from verify_sources import (archive_sources, restore_sources,
                            verify_preopen_sources, verify_postclose_sources)

ROOT = Path(__file__).resolve().parent
STUDY = 'studies/s500-prospective-v1'
PROTOCOL_SHA = '2916ce06a0db24e18a754abae8943470ba9419e914b11d03d2f05d99f64e6d71'
ET = ZoneInfo('America/New_York')


class RunnerError(ValueError):
    pass


def now_utc():
    return datetime.now(timezone.utc)


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + '\n').encode()


def sha(body):
    return hashlib.sha256(body).hexdigest()


def stamp(value):
    if isinstance(value, str):
        value = protocol_tools.timestamp(value)
    if value.tzinfo is None or value.utcoffset() is None:
        raise RunnerError('timezone_required')
    return value.astimezone(timezone.utc)


def load_json(body):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise RunnerError('duplicate_json_key')
            result[key] = value
        return result
    def constant(_):
        raise RunnerError('nonfinite_json_number')
    return json.loads(body, object_pairs_hook=pairs, parse_constant=constant)


def safe_run_id(value):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
        raise RunnerError('invalid_run_identity')
    return value


def verify_deployment(root):
    root = Path(root)
    body = (root / 'protocol.json').read_bytes()
    if sha(body) != PROTOCOL_SHA:
        raise RunnerError('registered_protocol_changed')
    protocol = load_json(body)
    check_bindings(protocol, root, root / 'fixed-panel.json')
    manifest_body = (root / 'deployment.json').read_bytes()
    manifest = load_json(manifest_body)
    if (manifest['protocol_sha256'] != PROTOCOL_SHA
            or stamp(manifest['registered_at']) >= stamp(protocol['sessions'][0]['open_utc'])):
        raise RunnerError('deployment_binding_invalid')
    repository = root.parents[1]
    for name, digest in manifest['files_sha256'].items():
        path = Path(name)
        if path.is_absolute() or '..' in path.parts:
            raise RunnerError('unsafe_deployment_path')
        if sha((repository / path).read_bytes()) != digest:
            raise RunnerError('deployment_bytes_changed')
    return protocol, sha(manifest_body)


def phase_for(protocol, at):
    at = stamp(at)
    day = at.astimezone(ET).date().isoformat()
    session = next((s for s in protocol['sessions'] if s['date'] == day), None)
    if session is None:
        return ('before_study' if day < protocol['window'][0] else
                'after_study' if day > protocol['window'][1] else 'non_session'), day
    if at <= stamp(session['preopen_cutoff_utc']):
        return 'preopen', day
    if stamp(session['close_utc']) + timedelta(minutes=15) <= at <= stamp(session['receipt_deadline_utc']):
        return 'postclose', day
    if at >= stamp(session['review_at_utc']):
        return 'audit', day
    return 'outside_capture_windows', day


def _result_path(day, phase):
    return f'{STUDY}/days/{day}/{phase}/result.json'


def _read_result(state, day, phase):
    body = state.read(_result_path(day, phase))
    if body is None:
        return None
    obj = load_json(body)
    if obj['date'] != day or obj['phase'] != phase or obj['protocol_sha256'] != PROTOCOL_SHA:
        raise RunnerError('foreign_state_result')
    return obj


def _read_bound(state, name, expected):
    body = state.read(name)
    if body is None or sha(body) != expected:
        raise RunnerError('state_blob_digest_mismatch')
    return body


def _restore_preopen(root, state, day, work):
    result = _read_result(state, day, 'preopen')
    if result is None or result.get('accepted') is not True:
        raise RunnerError('accepted_preopen_result_unavailable')
    archive = result['source_archive']
    archive_path = work / 'restored-preopen.tar.gz'
    archive_path.write_bytes(_read_bound(state, archive['path'], archive['sha256']))
    sources = work / 'restored-preopen'
    restored = restore_sources(archive_path, sources)
    if restored != archive['manifest']:
        raise RunnerError('archive_manifest_mismatch')
    verify_preopen_sources(root / 'protocol.json', root / 'fixed-panel.json', day, sources)
    return sources, load_json((sources / 'selection-seal.json').read_bytes())


def _bundle_capture_files(prefix, work, sources, result):
    files = {}
    if sources.exists() and any(sources.iterdir()):
        packed = work / 'sources.tar.gz'
        manifest = archive_sources(sources, packed)
        body = packed.read_bytes()
        path = prefix + '/sources.tar.gz'
        files[path] = body
        result['source_archive'] = {'path': path, 'sha256': sha(body), 'manifest': manifest}
    return files


def _bundle_analysis_files(prefix, directory, result):
    files, manifest = {}, {}
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 30 * 1024 * 1024:
            raise RunnerError('invalid_analysis_file')
        body = path.read_bytes()
        name = prefix + '/analysis/' + path.name
        files[name] = body
        manifest[path.name] = {'path': name, 'sha256': sha(body), 'bytes': len(body)}
    result['analysis_files'] = manifest
    return files


def capture_phase(root, protocol, deployment_digest, state, phase, day, run_id, work,
                  *, now=now_utc, capture_pre=preopen_capture,
                  capture_post=postclose_capture, analysis=analyze):
    """No data request is possible until an owned durable reservation exists."""
    root, work = Path(root), Path(work)
    protocol_tools.check_clock(protocol, day, stamp(now()).isoformat(), phase)
    prefix = f'{STUDY}/days/{day}/{phase}'
    claim = {'owner': run_id + '-' + uuid.uuid4().hex, 'run_id': run_id,
             'date': day, 'phase': phase, 'reserved_at': stamp(now()).isoformat(),
             'protocol_sha256': PROTOCOL_SHA, 'deployment_sha256': deployment_digest,
             'reservation_expires': False, 'automatic_reacquisition': False,
             'actual_fill': False}
    if not state.reserve(prefix + '/claim.json', claim):
        return {'status': 'already_reserved_no_retry', 'date': day, 'phase': phase,
                'get_count': 0, 'orders_sent': 0}
    # Reservation latency cannot authorize a late request.
    result = {'date': day, 'phase': phase, 'run_id': run_id, 'claim_owner': claim['owner'],
              'protocol_sha256': PROTOCOL_SHA, 'deployment_sha256': deployment_digest,
              'accepted': False, 'status': 'failed', 'get_count': None,
              'actual_fill': False, 'orders_sent': 0, 'account_reads': 0,
              'market_endpoint': 'https://data.alpaca.markets/v2/stocks/bars'}
    files, seal, summary = {}, None, None
    sources = work / 'captured'
    try:
        protocol_tools.check_clock(protocol, day, stamp(now()).isoformat(), phase)
        if phase == 'preopen':
            outcome = capture_pre(root / 'protocol.json', root / 'fixed-panel.json', day,
                                  sources, now=lambda: stamp(now()).isoformat())
        elif phase == 'postclose':
            restored, seal = _restore_preopen(root, state, day, work)
            # Raw source restoration/verification may be slow; recheck before GET.
            protocol_tools.check_clock(protocol, day, stamp(now()).isoformat(), phase)
            outcome = capture_post(root / 'protocol.json', restored / 'selection-seal.json',
                                   day, sources, now=lambda: stamp(now()).isoformat())
        else:
            raise RunnerError('invalid_capture_phase')
        result.update(status=outcome['status'], reason=outcome.get('reason'),
                      get_count=outcome['get_count'])
        if outcome.get('accepted') is True:
            if phase == 'preopen':
                verification = verify_preopen_sources(root / 'protocol.json', root / 'fixed-panel.json',
                                                       day, sources)
                seal = load_json((sources / 'selection-seal.json').read_bytes())
                result['selected_count'] = len(seal['selection']['selected_symbols'])
                files[prefix + '/selection-seal.json'] = (sources / 'selection-seal.json').read_bytes()
            else:
                verification = verify_postclose_sources(root / 'protocol.json', day,
                                                        restored / 'selection-seal.json', sources,
                                                        now=stamp(now()).isoformat())
            result.update(accepted=True, source_verification=verification)
        if phase == 'postclose' and seal is not None:
            if result['accepted']:
                source = load_json((sources / 'normalized-source.json').read_bytes())
            else:
                # A failed request cannot turn selected cases into observed no-signals.
                source = {'date': day, 'requested_symbols': seal['selection']['selected_symbols'],
                          'source_complete': False, 'status': 'source_failed',
                          'bars': {s: [] for s in seal['selection']['selected_symbols']}}
            summary = analysis(protocol, seal, source, work / 'analysis')
            files.update(_bundle_analysis_files(prefix, work / 'analysis', result))
    except Exception as error:
        # Class only: vendor messages/paths/credentials must never reach logs.
        result.update(accepted=False, status='failed', reason='exception_' + type(error).__name__)
    try:
        files.update(_bundle_capture_files(prefix, work, sources, result))
    except Exception as error:
        result.update(accepted=False, status='failed', reason='archive_' + type(error).__name__)
    result['finished_at'] = stamp(now()).isoformat()
    result['primary_source_accepted'] = result['accepted']
    if summary is not None:
        result['summary_counts'] = summary['counts']
    files[prefix + '/result.json'] = canonical(result)
    published = state.publish(files, 'Preserve registered research ' + day + ' ' + phase)
    return {'status': result['status'], 'accepted': result['accepted'], 'date': day,
            'phase': phase, 'reason': result.get('reason'), 'get_count': result['get_count'],
            'state_commit': published['commit_sha'], 'orders_sent': 0}


def build_ledger(protocol, records, at):
    """All dates retained, unknown is never a zero return or an observed no-signal."""
    at = stamp(at)
    rows = []
    for s in protocol['sessions']:
        day = s['date']
        pre = records.get(_result_path(day, 'preopen'))
        post = records.get(_result_path(day, 'postclose'))
        prefix = f'{STUDY}/days/{day}'
        pre_claim = prefix + '/preopen/claim.json' in records
        post_claim = prefix + '/postclose/claim.json' in records
        deadline_passed = at > stamp(s['receipt_deadline_utc'])
        if at.astimezone(ET).date().isoformat() < day:
            status = 'not_due'
        elif pre is None:
            status = ('preopen_claim_without_confirmed_result' if pre_claim else
                      'missed_preopen_as_of' if at > stamp(s['preopen_cutoff_utc']) else 'awaiting_preopen')
        elif pre.get('accepted') is not True:
            status = 'preopen_failed'
        elif post is None:
            status = ('postclose_claim_without_confirmed_result' if post_claim else
                      'missed_postclose_as_of' if deadline_passed else 'awaiting_postclose')
        elif post.get('accepted') is not True:
            status = 'postclose_failed'
        else:
            status = 'source_accepted_see_analysis_missingness'
        rows.append({'ordinal': s['ordinal'], 'date': day, 'status': status,
                     'as_of': at.isoformat(), 'preopen_claim_seen': pre_claim,
                     'postclose_claim_seen': post_claim, 'preopen_result': pre,
                     'postclose_result': post, 'returns': None, 'signals': None,
                     'actual_fill': False, 'status_is_as_of_not_permanent_failure': True})
    return rows


def audit_and_checkpoints(protocol, state, run_id, at):
    at = stamp(at)
    paths = state.list_paths(STUDY + '/')
    wanted = [p for p in paths if p.startswith(STUDY + '/days/') and
              (p.endswith('/claim.json') or p.endswith('/result.json'))]
    records = {p: load_json(b) for p, b in state.read_many(wanted).items() if b is not None}
    ledger = build_ledger(protocol, records, at)
    snapshot = {'as_of': at.isoformat(), 'protocol_sha256': PROTOCOL_SHA, 'ledger': ledger,
                'orders_sent': 0, 'market_gets': 0, 'missed_dates_backfilled_with_prices': False}
    prefix = f'{STUDY}/audits/{safe_run_id(run_id)}'
    files = {prefix + '/ledger.json': canonical(snapshot)}
    checkpoints = []
    for ordinal in protocol['review_ordinals']:
        s = protocol['sessions'][ordinal - 1]
        if at < stamp(s['review_at_utc']):
            continue
        checkpoint_inputs = {p: sha(canonical(value)) for p, value in records.items()
                             if p.split('/')[-3] <= s['date']}
        checkpoint_id = sha(canonical({'ordinal': ordinal, 'records': checkpoint_inputs}))
        checkpoint_path = f'{STUDY}/checkpoints/{ordinal}/{checkpoint_id}.json'
        if checkpoint_path in paths:
            continue
        # Snapshot revisions append rather than erase earlier unknown statuses.
        sessions = {}
        for row in ledger[:ordinal]:
            result = row['postclose_result']
            if (not result or result.get('accepted') is not True
                    or not result.get('source_archive') or not result.get('analysis_files')):
                continue
            bound = result['analysis_files']
            summary = load_json(_read_bound(state, bound['summary.json']['path'], bound['summary.json']['sha256']))
            compressed = _read_bound(state, bound['results.jsonl.gz']['path'], bound['results.jsonl.gz']['sha256'])
            with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
                decoded = stream.read(100 * 1024 * 1024 + 1)
            if len(decoded) > 100 * 1024 * 1024:
                raise RunnerError('analysis_expansion_limit')
            rows = [load_json(line) for line in decoded.splitlines()]
            sessions[row['date']] = {'summary': summary, 'rows': rows}
        checkpoint = build_checkpoint(protocol, ordinal, sessions, ledger, at.isoformat())
        checkpoint['scheduled_review_at'] = s['review_at_utc']
        checkpoint['actual_review_at'] = at.isoformat()
        checkpoint['delay_seconds'] = (at - stamp(s['review_at_utc'])).total_seconds()
        checkpoint['input_record_sha256'] = checkpoint_inputs
        files[checkpoint_path] = canonical(checkpoint)
        checkpoints.append(ordinal)
    committed = state.publish(files, 'Append research audit as of ' + at.isoformat())
    return {'status': 'audit_persisted', 'state_commit': committed['commit_sha'],
            'calendar_dates': len(ledger), 'checkpoints': checkpoints, 'market_gets': 0,
            'orders_sent': 0}


def rehearsal(protocol, state, run_id, work, at):
    """Real GitHub write/read/restore rehearsal, zero market requests or observations."""
    prefix = f'{STUDY}/rehearsals/{safe_run_id(run_id)}'
    claim = {'owner': run_id + '-' + uuid.uuid4().hex, 'kind': 'synthetic_rehearsal',
             'created_at': stamp(at).isoformat(), 'actual_fill': False}
    if not state.reserve(prefix + '/claim.json', claim):
        return {'status': 'rehearsal_already_reserved', 'market_gets': 0, 'orders_sent': 0}
    source = Path(work) / 'synthetic-source'
    source.mkdir(mode=0o700)
    (source / 'fixture.json').write_bytes(canonical({'synthetic': True, 'bytes': 'archive round trip'}))
    (source / 'fixture.body').write_bytes(b'\x00synthetic\n\xff')
    archive_path = Path(work) / 'synthetic.tar.gz'
    manifest = archive_sources(source, archive_path)
    state.publish({prefix + '/sources.tar.gz': archive_path.read_bytes(),
                   prefix + '/manifest.json': canonical(manifest)}, 'Private synthetic source round trip')
    roundtrip = _read_bound(state, prefix + '/sources.tar.gz', manifest['archive_sha256'])
    restored_archive = Path(work) / 'retrieved.tar.gz'
    restored_archive.write_bytes(roundtrip)
    restored = Path(work) / 'restored'
    if restore_sources(restored_archive, restored) != manifest:
        raise RunnerError('rehearsal_archive_mismatch')
    for file in source.iterdir():
        if (restored / file.name).read_bytes() != file.read_bytes():
            raise RunnerError('rehearsal_bytes_mismatch')
    phase, day = phase_for(protocol, at)
    result = {'status': 'cloud_state_roundtrip_passed', 'date': day,
              'real_clock_route': phase, 'archive_sha256': manifest['archive_sha256'],
              'archive_bytes_readback_identical': True, 'market_gets': 0,
              'account_reads': 0, 'orders_sent': 0, 'prospective_observations_added': 0,
              'synthetic': True, 'not_a_future_capture_validation': True}
    commit = state.publish({prefix + '/result.json': canonical(result)}, 'Verify cloud state rehearsal')
    return {**result, 'state_commit': commit['commit_sha']}


def connectivity(state, run_id, at, *, transport=None, credentials=None):
    """One historical SPY SIP GET, disjoint from primary observations; no retries."""
    if stamp(at) <= stamp('2026-10-07T00:00:00Z'):
        raise RunnerError('probe_history_not_yet_available')
    prefix = f'{STUDY}/connectivity/{safe_run_id(run_id)}'
    claim = {'owner': run_id + '-' + uuid.uuid4().hex, 'kind': 'historical_access_probe',
             'at': stamp(at).isoformat(), 'actual_fill': False}
    if not state.reserve(prefix + '/claim.json', claim):
        return {'status': 'connectivity_already_reserved', 'market_gets': 0, 'orders_sent': 0}
    key, secret = credentials or (os.environ.get('ALPACA_500_API_KEY'),
                                 os.environ.get('ALPACA_500_SECRET_KEY'))
    result = {'status': 'connectivity_failed', 'market_gets': 0, 'orders_sent': 0,
              'account_reads': 0, 'prospective_observations_added': 0,
              'historical_probe': True, 'symbol': 'SPY', 'date': '2026-10-06',
              'feed': 'sip', 'http_status': None, 'accepted': False}
    files = {}
    if not key or not secret:
        result['reason'] = 'credentials_unavailable'
    else:
        query = {'symbols': 'SPY', 'timeframe': '1Min', 'feed': 'sip', 'adjustment': 'raw',
                 'start': '2026-10-06T13:30:00Z', 'end': '2026-10-06T13:30:59.999999999Z',
                 'limit': 10000, 'sort': 'asc'}
        request = urllib.request.Request('https://data.alpaca.markets/v2/stocks/bars?' +
                                         urllib.parse.urlencode(query), method='GET',
                                         headers={'APCA-API-KEY-ID': key, 'APCA-API-SECRET-KEY': secret})
        result['requested_at'] = now_utc().isoformat()
        result['market_gets'] = 1
        try:
            status, body = (transport or make_transport())(request)
            result.update(http_status=status, received_at=now_utc().isoformat(),
                          source_sha256=sha(body), source_bytes=len(body))
            files[prefix + '/response.body'] = body
            if status == 200:
                payload = strict_source_json(body)
                bars = payload.get('bars', {}).get('SPY', [])
                result['returned_bars'] = len(bars)
                if (len(bars) == 1 and payload.get('next_page_token') is None
                        and bars[0].get('t', '').startswith('2026-10-06T13:30:')):
                    result.update(status='historical_sip_access_verified', accepted=True)
                else:
                    result['reason'] = 'unexpected_probe_response'
            else:
                result['reason'] = 'http_failure_no_retry'
        except Exception as error:
            result['reason'] = 'exception_' + type(error).__name__
    files[prefix + '/result.json'] = canonical(result)
    committed = state.publish(files, 'Preserve one historical stock data access probe')
    return {**result, 'state_commit': committed['commit_sha']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('auto', 'rehearsal', 'connectivity'), default='auto')
    args = parser.parse_args()
    run_id = safe_run_id(os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT'])
    protocol, deployment_digest = verify_deployment(ROOT)
    state = GitHubState()
    at = now_utc()
    with tempfile.TemporaryDirectory(prefix='s500-private-') as directory:
        work = Path(directory)
        if args.mode == 'rehearsal':
            result = rehearsal(protocol, state, run_id, work, at)
        elif args.mode == 'connectivity':
            result = connectivity(state, run_id, at)
        else:
            phase, day = phase_for(protocol, at)
            if phase in ('preopen', 'postclose'):
                result = capture_phase(ROOT, protocol, deployment_digest, state, phase, day, run_id, work)
            elif phase == 'audit' or (phase == 'non_session'
                                      and at.astimezone(ET).hour >= 17):
                result = audit_and_checkpoints(protocol, state, run_id, at)
            else:
                result = {'status': phase, 'date': day, 'market_gets': 0, 'orders_sent': 0}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
        stream.write('Research invocation: `' + result['status'] + '`; orders: 0.\n')
    return 1 if result.get('accepted') is False else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'status': 'cloud_runner_failed', 'error_class': type(error).__name__,
                          'market_get_count': 'unknown_if_reservation_present', 'orders_sent': 0}))
        raise SystemExit(1) from None
