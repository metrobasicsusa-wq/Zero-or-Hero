"""Offline calendar/scheduler oracle; no network and no production module import."""
from datetime import datetime, time, timedelta, timezone
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
PROTO = ROOT / 'deploy/research/s500/protocol.json'
ET = ZoneInfo('America/New_York')
UTC = timezone.utc
SCHEDULE = ('13:03', '20:23', '21:23', '22:33')


def utc(day, clock):
    return datetime.fromisoformat(f'{day}T{clock}:00+00:00')


def local(day, clock):
    return datetime.combine(datetime.fromisoformat(day).date(), time.fromisoformat(clock), ET)


def audit():
    p = json.loads(PROTO.read_text())
    assert len(p['sessions']) == 59
    assert [p['sessions'][0]['date'], p['sessions'][-1]['date']] == ['2026-10-08','2026-12-31']
    assert len({s['date'] for s in p['sessions']}) == 59
    rows = []
    for expected_ordinal, s in enumerate(p['sessions'], 1):
        day = s['date']
        assert s['ordinal'] == expected_ordinal
        assert datetime.fromisoformat(day).weekday() < 5
        start = local(day, s['open_et'])
        end = local(day, s['close_et'])
        cutoff = local(day, '09:20')
        deadline = local(day, '17:15')
        review = local(day, '17:30')
        for expected, name in [(start,'open_utc'), (end,'close_utc'), (cutoff,'preopen_cutoff_utc'), (deadline,'receipt_deadline_utc'), (review,'review_at_utc')]:
            assert expected == datetime.fromisoformat(s[name]), (day, name)
        ticks = []
        for clock in SCHEDULE:
            stamp = utc(day, clock)
            et = stamp.astimezone(ET)
            stage = ('prepare' if et.date().isoformat()==day and et <= cutoff else
                     'postclose' if end+timedelta(minutes=15) <= et <= deadline else
                     'lateaudit' if et >= review else 'not_in_stage_window')
            ticks.append({'utc':stamp.isoformat(),'et':et.isoformat(),'candidate_stage':stage})
        # At least one schedule slot can start within each bound. Actual delayed
        # execution must independently pass clock gates; this is not a guarantee.
        for stage in ('prepare','postclose','lateaudit'):
            assert any(t['candidate_stage']==stage for t in ticks), (day,stage)
        rows.append({'date':day,'ordinal':expected_ordinal,'close_et':s['close_et'],'slots':ticks})
    assert [r['date'] for r in rows if r['close_et']=='13:00'] == ['2026-11-27','2026-12-24']
    assert [p['sessions'][n-1]['date'] for n in p['review_ordinals']] == ['2026-11-04','2026-12-03','2026-12-31']
    assert all(s['date'] not in {'2026-11-26','2026-12-25'} for s in p['sessions'])
    for day,offset in [('2026-10-30',-4),('2026-11-02',-5)]:
        assert local(day,'09:00').utcoffset().total_seconds() == offset*3600
    return {'status':'passed','scope':'schedule/calendar feasibility only; no actual trigger or production entrypoint validation',
            'network_calls':0,'protocol_sha256':hashlib.sha256(PROTO.read_bytes()).hexdigest(),
            'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'candidate_utc_schedule':list(SCHEDULE),'sessions_verified':len(rows),
            'rows':rows,'limitations':['GitHub can queue, delay or skip scheduled jobs.',
              'Actual clocks and durable claim deduplication require separate production tests.',
              'Candidate schedule can change before deployment; rerun oracle if changed.']}


if __name__ == '__main__':
    result = audit()
    (ROOT/'independent-calendar-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='rows'},ensure_ascii=False))
