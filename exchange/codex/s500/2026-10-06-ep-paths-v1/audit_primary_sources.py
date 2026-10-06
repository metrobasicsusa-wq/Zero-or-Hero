"""Independent cache/time checks for frozen primary classifications. Offline only."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dt(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    assert parsed.tzinfo is not None
    return parsed.astimezone(timezone.utc)


def main():
    reviews = json.loads((ROOT / 'all-new-primary-reviews.json').read_text())
    for name, expected in reviews['source_files_sha256'].items():
        assert sha(ROOT / name) == expected, name
    rows = reviews['reviews']
    assert len(rows) == len({r['candidate_id'] for r in rows}) == 68
    assert Counter('pass' if r['pass_registered_news_gate'] is True else
                   'unknown' if r['pass_registered_news_gate'] is None else
                   'reviewed_story_failed_candidate_unknown' for r in rows) == reviews['counts']
    index = defaultdict(list)
    for parent in list(ROOT.glob('raw-source-group-*')) + [ROOT / 'raw-primary-root']:
        for path in parent.rglob('*.raw'):
            index[sha(path)].append(path)
    checks = []
    for r in rows:
        if r['pass_registered_news_gate'] is not True:
            continue
        window = r.get('window', r.get('required_window'))
        left = dt(window.get('strict_after_utc', window.get('strict_after')))
        right = dt(window.get('strict_before_utc', window.get('strict_before')))
        announced, available = dt(r['announcement_time_utc']), dt(r['conservative_available_by_utc'])
        assert left < announced <= available < right, r['candidate_id']
        sources = [s for s in r['sources'] if s['role'] in ('primary_announcement', 'event_evidence')]
        assert sources, r['candidate_id']
        source_checks = []
        directly_supported_times = []
        for s in sources:
            paths = index[s['sha256']]
            assert paths and s['status'] == 200 and s.get('body_hash_verified', True), r['candidate_id']
            path = paths[0]
            metadata_path = path.with_name(path.stem + '-meta.json')
            meta = json.loads(metadata_path.read_text())
            assert meta['sha256'] == s['sha256'] == sha(path)
            if meta.get('bytes') is not None:
                assert meta['bytes'] == path.stat().st_size
            assert dt(s['retrieved_at']) <= dt(json.loads((ROOT / 'primary-classification-lock.json').read_text())['locked_at'])
            body = unescape(path.read_text(errors='replace'))
            pub_values = re.findall(r'"datePublished"\s*:\s*"([^"]+)"', body)
            mod_values = re.findall(r'"dateModified"\s*:\s*"([^"]+)"', body)
            if 'declared_publication_values' in s:
                assert set(pub_values) == set(s['declared_publication_values'])
                assert set(mod_values) == set(s['declared_modification_values'])
            published = [dt(v) for v in pub_values]
            modified = [dt(v) for v in mod_values]
            for value in published + modified:
                assert left < value < right, (r['candidate_id'], value)
            method = 'timezone_aware_json_ld'
            if not published:
                # These exceptions inspect displayed timezone text, never infer it from price.
                plain = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', body))
                if r['symbol'] == 'CAPR':
                    assert 'August 13, 2026 4:05 pm EDT' in plain
                    published = [dt('2026-08-13T20:05:00Z')]
                    method = 'visible_release_timestamp_with_EDT'
                elif r['symbol'] == 'IMVT':
                    assert 'May 20, 2026 7:00 am EDT' in plain
                    published = [dt('2026-05-20T11:00:00Z')]
                    method = 'visible_release_timestamp_with_EDT'
                elif r['symbol'] == 'VELO':
                    assert 'datetime="2026-05-12T16:05:00"' in body
                    method = 'issuer_time_has_no_timezone_independent_wire_required'
                else:
                    raise AssertionError(('unverified_source_timestamp', r['candidate_id']))
            reported_pub = s.get('publication_time_utc', s.get('publication_time'))
            reported_mod = s.get('declared_revision_time_utc', s.get('declared_modified_time'))
            if reported_pub and published:
                assert dt(reported_pub) in published, r['candidate_id']
            if reported_mod:
                assert dt(reported_mod) in modified, r['candidate_id']
            directly_supported_times.extend(published + modified)
            source_checks.append({
                'url': s['url'], 'sha256': s['sha256'], 'body_hash_verified': True,
                'bytes_verified': path.stat().st_size,
                'time_evidence_method': method,
                'declared_publication_utc': sorted({v.isoformat() for v in published}),
                'declared_revision_utc': sorted({v.isoformat() for v in modified}),
                'historical_version_immutable': False,
                'missing_declared_revision_does_not_prove_no_revision': not bool(modified),
            })
        assert announced in directly_supported_times, r['candidate_id']
        assert available == max(directly_supported_times), r['candidate_id']
        checks.append({'candidate_id': r['candidate_id'], 'category': r['event_category'],
                       'strict_time_window_passed': True, 'announcement_time_utc': announced.isoformat(),
                       'conservative_available_by_utc': available.isoformat(), 'sources': source_checks})
    crm = next(r for r in rows if r['symbol'] == 'CRM')
    assert crm['pass_registered_news_gate'] is None
    bad = [s for s in crm['sources'] if s.get('source_integrity_status') == 'quarantined_no_proof']
    assert len(bad) == 1 and bad[0]['body_hash_verified'] is False
    crm_raw = ROOT / 'raw-source-group-4' / 'CRM__05.raw'
    assert sha(crm_raw) == bad[0]['observed_body_sha256'] != bad[0]['sha256']
    assert bad[0]['role'] != 'primary_announcement'
    report = {
        'audited_at': datetime.now(timezone.utc).isoformat(),
        'review_rows_retained': len(rows), 'positive_cases_checked': len(checks),
        'positive_sources_checked': sum(len(c['sources']) for c in checks),
        'positive_source_bytes_verified': sum(s['bytes_verified'] for c in checks for s in c['sources']),
        'positive_sources_without_declared_revision': sum(s['missing_declared_revision_does_not_prove_no_revision'] for c in checks for s in c['sources']),
        'source_group_hashes_verified': reviews['source_files_sha256'],
        'classifications_sha256': sha(ROOT / 'all-new-primary-reviews.json'),
        'classifications_changed_by_audit': False,
        'passed_cases': checks,
        'known_source_integrity_issue': {
            'symbol': 'CRM', 'recorded_sha256': bad[0]['sha256'],
            'observed_sha256': bad[0]['observed_body_sha256'],
            'quarantined': True, 'candidate_stays_unknown': True,
            'affected_body_used_for_positive_gate': False,
        },
        'limits': [
            'This verifies retrieved bytes and declared/displayed timestamps, not immutable historical versions or client receipt.',
            'News presence/category does not prove favorable news, a positive surprise, market causation or an executable opportunity.',
            'VELO issuer time alone has no timezone; a separate original wire timestamp supports the window.',
            'AMBQ issuer and wire differ by five hours; both are in the window, and the later wire time controls.',
            'Unknown and outside-category stories do not prove the absence of a qualifying catalyst.',
        ],
    }
    (ROOT / 'primary-source-independent-audit.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: report[k] for k in ('positive_cases_checked', 'positive_sources_checked', 'positive_sources_without_declared_revision', 'classifications_changed_by_audit')}))


if __name__ == '__main__':
    main()
