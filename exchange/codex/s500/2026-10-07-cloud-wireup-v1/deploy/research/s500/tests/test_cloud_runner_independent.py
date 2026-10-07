"""Independent adverse-path tests: no real network, clocks, or GitHub writes."""
from datetime import datetime, timedelta, timezone
from contextlib import redirect_stderr, redirect_stdout
import io
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import cloud_runner as runner

ROOT = Path(runner.__file__).resolve().parent
PROTOCOL = json.loads((ROOT/'protocol.json').read_text())


def at(text):
    return datetime.fromisoformat(text.replace('Z','+00:00'))


def result_path(day, phase):
    return runner.STUDY+'/days/'+day+'/'+phase+'/result.json'


def claim_path(day, phase):
    return runner.STUDY+'/days/'+day+'/'+phase+'/claim.json'


class MemoryState:
    def __init__(self, records=None):
        self.files = {k:(v if isinstance(v,bytes) else runner.canonical(v)) for k,v in (records or {}).items()}
        self.reservation_calls = []
        self.published = []
    def reserve(self, path, value):
        self.reservation_calls.append(path)
        if path in self.files:
            return False
        self.files[path] = runner.canonical(value)
        return True
    def read(self, path):
        return self.files.get(path)
    def read_many(self, paths):
        return {p:self.files.get(p) for p in paths}
    def list_paths(self, prefix):
        return sorted(p for p in self.files if p.startswith(prefix))
    def publish(self, files, message):
        for p,b in files.items():
            if p in self.files and self.files[p] != b:
                raise AssertionError('test_rejected_immutable_replacement')
        self.files.update(files)
        self.published.append(files)
        return {'commit_sha':'a'*40,'readback':True}


class RoutingTests(unittest.TestCase):
    def test_registered_all_dates_single_preopen_schedule(self):
        for s in PROTOCOL['sessions']:
            self.assertEqual(runner.phase_for(PROTOCOL,at(s['date']+'T13:03:00Z')),('preopen',s['date']))
    def test_all_dates_have_postclose_slot_and_final_audit(self):
        for s in PROTOCOL['sessions']:
            day=s['date']
            phases=[runner.phase_for(PROTOCOL,at(day+'T'+t+':00Z'))[0] for t in ('20:23','21:23')]
            self.assertIn('postclose',phases)
            self.assertEqual(runner.phase_for(PROTOCOL,at(day+'T22:33:00Z'))[0],'audit')
    def test_winter_not_yet_closed(self):
        self.assertEqual(runner.phase_for(PROTOCOL,at('2026-11-02T20:23:00Z'))[0],'outside_capture_windows')
        self.assertEqual(runner.phase_for(PROTOCOL,at('2026-11-02T21:23:00Z'))[0],'postclose')
    def test_early_closes_obey_registered_13_et(self):
        for day in ('2026-11-27','2026-12-24'):
            self.assertEqual(runner.phase_for(PROTOCOL,at(day+'T18:14:59Z'))[0],'outside_capture_windows')
            self.assertEqual(runner.phase_for(PROTOCOL,at(day+'T18:15:00Z'))[0],'postclose')
            self.assertEqual(runner.phase_for(PROTOCOL,at(day+'T20:23:00Z'))[0],'postclose')
    def test_cutoff_edges_and_review_gap(self):
        self.assertEqual(runner.phase_for(PROTOCOL,at('2026-10-08T13:20:00Z'))[0],'preopen')
        self.assertEqual(runner.phase_for(PROTOCOL,at('2026-10-08T13:20:00.000001Z'))[0],'outside_capture_windows')
        self.assertEqual(runner.phase_for(PROTOCOL,at('2026-10-08T21:15:00Z'))[0],'postclose')
        self.assertEqual(runner.phase_for(PROTOCOL,at('2026-10-08T21:15:00.000001Z'))[0],'outside_capture_windows')
        self.assertEqual(runner.phase_for(PROTOCOL,at('2026-10-08T21:30:00Z'))[0],'audit')
    def test_non_sessions_and_expiry(self):
        for day,expected in [('2026-10-07','before_study'),('2026-10-10','non_session'),('2026-11-26','non_session'),('2026-12-25','non_session'),('2027-01-01','after_study')]:
            self.assertEqual(runner.phase_for(PROTOCOL,at(day+'T13:03:00Z'))[0],expected)
    def test_naive_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            runner.phase_for(PROTOCOL,datetime(2026,10,8,9,3))


class LedgerTests(unittest.TestCase):
    def test_all_59_stay_and_no_run_never_zero_return(self):
        rows=runner.build_ledger(PROTOCOL,{},at('2026-12-31T22:33:00Z'))
        self.assertEqual(len(rows),59)
        self.assertEqual(len({r['date'] for r in rows}),59)
        self.assertTrue(all(r['returns'] is None and r['signals'] is None for r in rows))
        self.assertTrue(all(r['status']=='missed_preopen_as_of' for r in rows))
    def test_future_dates_not_false_misses(self):
        rows=runner.build_ledger(PROTOCOL,{},at('2026-10-08T12:00:00Z'))
        self.assertEqual(rows[0]['status'],'awaiting_preopen')
        self.assertTrue(all(r['status']=='not_due' for r in rows[1:]))
    def test_crashed_preopen_claim_is_unknown(self):
        records={claim_path('2026-10-08','preopen'):{'owner':'crashed'}}
        row=runner.build_ledger(PROTOCOL,records,at('2026-10-08T22:33:00Z'))[0]
        self.assertEqual(row['status'],'preopen_claim_without_confirmed_result')
        self.assertTrue(row['status_is_as_of_not_permanent_failure'])
    def test_postclose_late_legal_result_can_follow_asof_unknown(self):
        day='2026-10-08'
        records={result_path(day,'preopen'):{'accepted':True},claim_path(day,'postclose'):{'owner':'collect'}}
        old=runner.build_ledger(PROTOCOL,records,at('2026-10-08T22:33:00Z'))[0]
        self.assertEqual(old['status'],'postclose_claim_without_confirmed_result')
        records[result_path(day,'postclose')]={'accepted':True}
        new=runner.build_ledger(PROTOCOL,records,at('2026-10-08T22:35:00Z'))[0]
        self.assertEqual(new['status'],'source_accepted_see_analysis_missingness')
        self.assertEqual(old['status'],'postclose_claim_without_confirmed_result')
    def test_failed_preopen_and_failed_postclose_not_signalless_days(self):
        day='2026-10-08'
        records={result_path(day,'preopen'):{'accepted':False}}
        self.assertEqual(runner.build_ledger(PROTOCOL,records,at('2026-10-08T22:33:00Z'))[0]['status'],'preopen_failed')
        records[result_path(day,'preopen')]={'accepted':True}
        records[result_path(day,'postclose')]={'accepted':False}
        row=runner.build_ledger(PROTOCOL,records,at('2026-10-08T22:33:00Z'))[0]
        self.assertEqual(row['status'],'postclose_failed')
        self.assertIsNone(row['signals'])


class ReservationTests(unittest.TestCase):
    def run_capture(self,state,capture,clock=None):
        with tempfile.TemporaryDirectory() as d:
            return runner.capture_phase(ROOT,PROTOCOL,'d'*64,state,'preopen','2026-10-08','fixture-run',Path(d),now=clock or (lambda:at('2026-10-08T13:03:00Z')),capture_pre=capture)
    def test_existing_claim_prevents_second_get(self):
        state=MemoryState({claim_path('2026-10-08','preopen'):{'owner':'prior'}})
        capture=Mock(side_effect=AssertionError('must_not_fetch'))
        result=self.run_capture(state,capture)
        self.assertEqual(result['status'],'already_reserved_no_retry')
        capture.assert_not_called()
        self.assertFalse(state.published)
    def test_unconfirmed_reservation_stops_before_get(self):
        state=MemoryState();state.reserve=Mock(side_effect=runner.RunnerError('unconfirmed'))
        capture=Mock(side_effect=AssertionError('must_not_fetch'))
        with self.assertRaises(runner.RunnerError): self.run_capture(state,capture)
        capture.assert_not_called();self.assertFalse(state.published)
    def test_late_initial_clock_does_not_even_reserve(self):
        state=MemoryState();capture=Mock()
        with self.assertRaises(ValueError): self.run_capture(state,capture,lambda:at('2026-10-08T13:20:01Z'))
        self.assertFalse(state.reservation_calls);capture.assert_not_called()
    def test_clock_advance_during_claim_no_get_and_preserves_failed_attempt(self):
        state=MemoryState();capture=Mock()
        stamps=iter([at('2026-10-08T13:19:59Z'),at('2026-10-08T13:19:59Z'),at('2026-10-08T13:20:01Z'),at('2026-10-08T13:20:01Z')])
        result=self.run_capture(state,capture,lambda:next(stamps))
        capture.assert_not_called();self.assertFalse(result['accepted'])
        self.assertIn(claim_path('2026-10-08','preopen'),state.files)
        self.assertIn(result_path('2026-10-08','preopen'),state.files)
    def test_capture_failure_archived_and_rerun_cannot_retry(self):
        state=MemoryState()
        def failed(*args,now):
            self.assertIsInstance(now(),str)
            self.assertIn(claim_path('2026-10-08','preopen'),state.files)
            folder=Path(args[3]);folder.mkdir()
            (folder/'receipt.json').write_text('{"synthetic":true,"http_status":403}')
            return {'status':'failed','accepted':False,'get_count':1,'reason':'http_403'}
        first=self.run_capture(state,failed)
        self.assertFalse(first['accepted'])
        result=json.loads(state.files[result_path('2026-10-08','preopen')])
        self.assertIn(result['source_archive']['path'],state.files)
        again=Mock(side_effect=AssertionError('must_not_retry'))
        second=self.run_capture(state,again)
        self.assertEqual(second['status'],'already_reserved_no_retry');again.assert_not_called()
    def test_exception_details_never_in_persistent_result(self):
        state=MemoryState()
        capture=Mock(side_effect=RuntimeError('SYNTHETIC_CREDENTIAL_MUST_NOT_SURVIVE /private/path'))
        self.run_capture(state,capture)
        encoded=state.files[result_path('2026-10-08','preopen')]
        self.assertNotIn(b'SYNTHETIC_CREDENTIAL_MUST_NOT_SURVIVE',encoded)
        self.assertNotIn(b'/private/path',encoded)
        self.assertEqual(json.loads(encoded)['reason'],'exception_RuntimeError')


class AuditTests(unittest.TestCase):
    def test_late_audit_is_append_only_no_capture_or_result_path(self):
        state=MemoryState({claim_path('2026-10-08','preopen'):{'owner':'failed'}})
        with patch.object(runner,'preopen_capture',side_effect=AssertionError('no_get')),patch.object(runner,'postclose_capture',side_effect=AssertionError('no_get')):
            value=runner.audit_and_checkpoints(PROTOCOL,state,'audit-fixture',at('2026-10-08T22:33:00Z'))
        self.assertEqual(value['market_gets'],0)
        self.assertEqual(value['calendar_dates'],59)
        self.assertTrue(all(p.startswith(runner.STUDY+'/audits/') for p in state.published[0]))
        self.assertNotIn(result_path('2026-10-08','preopen'),state.files)
    def test_failed_archive_cannot_promote_existing_profit_to_checkpoint(self):
        day='2026-10-08'
        summary=b'{"synthetic":true}'
        rows=gzip.compress(b'{"net_return":"9"}\n')
        state=MemoryState({result_path(day,'preopen'):{'accepted':True},result_path(day,'postclose'):{'accepted':False,'reason':'archive_failed','analysis_files':{
            'summary.json':{'path':'summary','sha256':runner.sha(summary)},
            'results.jsonl.gz':{'path':'rows','sha256':runner.sha(rows)}}},'summary':summary,'rows':rows})
        captured=[]
        def checkpoint(protocol,ordinal,sessions,ledger,stamp):
            captured.append(sessions);return {'synthetic':True}
        with patch.object(runner,'build_checkpoint',checkpoint):
            runner.audit_and_checkpoints(PROTOCOL,state,'audit-filter',at('2026-11-04T22:33:00Z'))
        self.assertEqual(captured,[{}], 'Rejected source archive must not contribute a positive primary return')

    def test_same_checkpoint_inputs_not_recomputed_next_audit(self):
        state=MemoryState()
        build=Mock(return_value={'synthetic':True})
        with patch.object(runner,'build_checkpoint',build):
            first=runner.audit_and_checkpoints(PROTOCOL,state,'audit-one',at('2026-11-04T22:33:00Z'))
            second=runner.audit_and_checkpoints(PROTOCOL,state,'audit-two',at('2026-11-05T22:33:00Z'))
        self.assertEqual(first['checkpoints'],[20])
        self.assertEqual(second['checkpoints'],[])
        self.assertEqual(build.call_count,1)
        self.assertEqual(len([p for p in state.files if '/checkpoints/' in p]),1)
        self.assertEqual(len([p for p in state.files if '/audits/' in p]),2)


    def test_rehearsal_records_never_used_as_real_observation_inputs(self):
        state=MemoryState({runner.STUDY+'/rehearsals/fake/result.json':b'opaque synthetic marker not a registered result'})
        build=Mock(return_value={'synthetic':True})
        with patch.object(runner,'build_checkpoint',build):
            runner.audit_and_checkpoints(PROTOCOL,state,'audit-real-only',at('2026-11-04T22:33:00Z'))
        self.assertEqual(build.call_args.args[2],{})
    def test_new_source_state_appends_checkpoint_revision_preserving_old(self):
        state=MemoryState()
        build=Mock(side_effect=lambda *args:{'synthetic':True})
        with patch.object(runner,'build_checkpoint',build):
            runner.audit_and_checkpoints(PROTOCOL,state,'audit-before',at('2026-11-04T22:33:00Z'))
            before={p:b for p,b in state.files.items() if '/checkpoints/' in p}
            state.files[claim_path('2026-10-08','preopen')]=runner.canonical({'owner':'late-confirmed-attempt'})
            runner.audit_and_checkpoints(PROTOCOL,state,'audit-after',at('2026-11-04T22:34:00Z'))
        self.assertEqual(build.call_count,2)
        after={p:b for p,b in state.files.items() if '/checkpoints/' in p}
        self.assertEqual(len(after),2)
        self.assertTrue(all(after[p]==b for p,b in before.items()))


class MainEntryTests(unittest.TestCase):
    def test_after_study_does_not_capture_or_recompute_audit(self):
        state=MemoryState()
        with tempfile.TemporaryDirectory() as d:
            summary=Path(d)/'summary.md'
            env={'GITHUB_RUN_ID':'fixture','GITHUB_RUN_ATTEMPT':'1','GITHUB_STEP_SUMMARY':str(summary)}
            with patch.dict(runner.os.environ,env),patch('sys.argv',['cloud_runner.py','--mode','auto']),patch.object(runner,'verify_deployment',return_value=(PROTOCOL,'d'*64)),patch.object(runner,'GitHubState',return_value=state),patch.object(runner,'now_utc',return_value=at('2027-01-01T22:33:00Z')),patch.object(runner,'capture_phase',side_effect=AssertionError('expired_capture')) as capture,patch.object(runner,'audit_and_checkpoints',side_effect=AssertionError('expired_audit')) as audit,redirect_stdout(io.StringIO()):
                self.assertEqual(runner.main(),0)
            capture.assert_not_called();audit.assert_not_called()
            self.assertIn('after_study',summary.read_text())
            self.assertFalse(state.published)
    def test_cli_has_no_artificial_now_override(self):
        with patch('sys.argv',['cloud_runner.py','--now','2026-10-08T13:03:00Z']),redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                runner.main()
        self.assertEqual(raised.exception.code,2)


if __name__ == '__main__':
    unittest.main()
