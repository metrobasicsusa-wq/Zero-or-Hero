"""Independent checks that missing peers and cluster uncertainty are not promoted."""
import hashlib,json,unittest
from datetime import datetime,timezone
from pathlib import Path
from summarize_flush import describe,matched_excess,date_cluster_bootstrap
ROOT=Path(__file__).resolve().parent
class IndependentStatisticsTests(unittest.TestCase):
    def test_three_peers_equal_weights(self):
        peers=[{'symbol':s,'status':'complete','net_return':v} for s,v in zip('ABC',[.01,.02,.03])]
        value,status=matched_excess(.05,peers)
        self.assertEqual(status,'complete');self.assertAlmostEqual(value,.03)
    def test_missing_peer_does_not_collapse_to_two(self):
        peers=[{'symbol':s,'status':'complete','net_return':v} for s,v in zip('ABC',[.01,.02,None])]
        value,status=matched_excess(.05,peers)
        self.assertIsNone(value);self.assertEqual(status,'one_or_more_control_outcomes_incomplete')
    def test_extra_or_fewer_peers_not_accepted(self):
        peers=[{'symbol':s,'status':'complete','net_return':.01} for s in 'ABCD']
        for count in [0,1,2,4]:self.assertIsNone(matched_excess(.05,peers[:count])[0])
    def test_duplicate_symbols_not_independent_controls(self):
        peers=[{'symbol':'A','status':'complete','net_return':.01}]*3
        self.assertEqual(matched_excess(.05,peers)[1],'duplicate_control_symbol')
    def test_null_target_stays_missing(self):
        peers=[{'symbol':s,'status':'complete','net_return':.01} for s in 'ABC']
        self.assertEqual(matched_excess(None,peers)[1],'target_outcome_missing')
    def test_status_must_confirm_all_three(self):
        peers=[{'symbol':s,'status':'unknown','net_return':.01} for s in 'ABC']
        self.assertIsNone(matched_excess(.05,peers)[0])
    def test_zero_outcome_is_real_observation_not_missing(self):
        stats=describe([0,.1,-.1])
        self.assertEqual(stats['n'],3);self.assertEqual(stats['zero_count'],1)
        self.assertEqual(stats['positive_fraction'],1/3)
    def test_unknown_not_zero_padded(self):
        with self.assertRaises(ValueError):describe([None,.1])
        self.assertEqual(describe([])['n'],0);self.assertIsNone(describe([])['mean'])
    def test_bootstrap_order_does_not_change_seed_or_draws(self):
        dates={'2026-01-03':-.04,'2026-01-01':.01,'2026-01-02':.02}
        self.assertEqual(date_cluster_bootstrap(dates,'primary'),date_cluster_bootstrap(dict(reversed(list(dates.items()))),'primary'))
    def test_identical_date_means_have_degenerate_interval(self):
        r=date_cluster_bootstrap({'2026-01-01':.02,'2026-01-02':.02},'identical')
        self.assertAlmostEqual(r['point_estimate'],.02);self.assertAlmostEqual(r['lower'],.02);self.assertAlmostEqual(r['upper'],.02)
    def test_single_date_no_confidence_claim(self):
        r=date_cluster_bootstrap({'2026-01-01':.02},'single')
        self.assertEqual(r['status'],'insufficient_dates');self.assertIsNone(r['lower']);self.assertIsNone(r['upper'])
    def test_equal_weight_date_mean_not_event_count(self):
        r=date_cluster_bootstrap({'one_event_day':.10,'one_hundred_event_day':-.10},'imbalance')
        self.assertEqual(r['point_estimate'],0)
    def test_nonfinite_rejected(self):
        for bad in [float('nan'),float('inf'),True]:
            with self.assertRaises(ValueError):describe([bad])
if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(IndependentStatisticsTests)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    binding={'completed_at_utc':datetime.now(timezone.utc).isoformat(),'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),'passed':result.wasSuccessful(),'summarizer_sha256':hashlib.sha256((ROOT/'summarize_flush.py').read_bytes()).hexdigest(),'independent_stats_tests_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'real_result_data_used':False}
    (ROOT/'independent-stats-test-binding.json').write_text(json.dumps(binding,indent=2)+'\n')
    raise SystemExit(0 if result.wasSuccessful() else 1)
