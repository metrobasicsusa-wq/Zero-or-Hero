"""Independent checks for feed timestamp precision and New York DST joins."""
import hashlib,json,unittest
from datetime import datetime,timezone
from pathlib import Path
from normalize_bars import normalize_provider_bars
from flush_engine import family_decision
ROOT=Path(__file__).resolve().parent

def bar(t):return {'t':t,'o':'100','h':'100','l':'97','c':'97','v':'100'}
class IndependentNormalizationTests(unittest.TestCase):
    def test_standard_time_open(self):
        r=normalize_provider_bars([bar('2026-01-02T14:30:00Z')],'2026-01-02')
        self.assertEqual(r['bars'][0]['t'],0)
    def test_daylight_time_open(self):
        r=normalize_provider_bars([bar('2026-03-09T13:30:00Z')],'2026-03-09')
        self.assertEqual(r['bars'][0]['t'],0)
    def test_one_nanosecond_not_rounded_to_minute(self):
        r=normalize_provider_bars([bar('2026-03-09T13:30:00.000000001Z')],'2026-03-09')
        self.assertIsInstance(r['bars'][0]['t'],str)
        self.assertEqual(r['issues'][0]['kind'],'non_minute_timestamp')
    def test_zero_nanoseconds_exact_minute(self):
        r=normalize_provider_bars([bar('2026-03-09T13:30:00.000000000Z')],'2026-03-09')
        self.assertEqual(r['bars'][0]['t'],0)
    def test_malformed_offset_not_normalized(self):
        r=normalize_provider_bars([bar('2026-03-09T13:30:00+00:60')],'2026-03-09')
        self.assertIsInstance(r['bars'][0]['t'],str)
    def test_unknown_offset_not_assumed_utc(self):
        r=normalize_provider_bars([bar('2026-03-09T13:30:00-00:00')],'2026-03-09')
        self.assertEqual(r['issues'][0]['kind'],'unknown_local_offset')
    def test_same_moment_different_offsets_duplicate_retained(self):
        r=normalize_provider_bars([bar('2026-03-09T13:30:00Z'),bar('2026-03-09T09:30:00-04:00')],'2026-03-09')
        self.assertEqual(len(r['bars']),2);self.assertEqual([b['t'] for b in r['bars']],[0,0])
        self.assertEqual(r['issues'][0]['kind'],'duplicate_minute')
    def test_wrong_date_not_clipped_into_session(self):
        r=normalize_provider_bars([bar('2026-03-10T13:30:00Z')],'2026-03-09')
        self.assertEqual(r['bars'][0]['t'],1440)
    def test_values_and_source_order_preserved(self):
        source=[bar('2026-03-09T13:31:00Z'),bar('2026-03-09T13:30:00Z')]
        source[0]['c']='NaN'
        r=normalize_provider_bars(source,'2026-03-09')
        self.assertEqual([b['t'] for b in r['bars']],[1,0]);self.assertEqual(r['bars'][0]['c'],'NaN')
        self.assertEqual(source[0]['t'],'2026-03-09T13:31:00Z')
if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(IndependentNormalizationTests))
    binding={'completed_at_utc':datetime.now(timezone.utc).isoformat(),'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),'passed':result.wasSuccessful(),'normalizer_sha256':hashlib.sha256((ROOT/'normalize_bars.py').read_bytes()).hexdigest(),'independent_normalization_tests_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'real_result_data_used':False}
    (ROOT/'independent-normalization-test-binding.json').write_text(json.dumps(binding,indent=2)+'\n')
    raise SystemExit(0 if result.wasSuccessful() else 1)
