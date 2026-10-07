"""Independent payoff/asymmetry/date-contrast tests; no historical stratified data."""
from pathlib import Path
from decimal import Decimal
from fractions import Fraction
from datetime import datetime,timezone
import hashlib,json,unittest
from return_distribution import compute_distribution
from analyze_relative import summarize,contrast,negative_runs,bootstrap
ROOT=Path(__file__).resolve().parent

def records(values):return [{'case_id':f'case_{i:03d}','net_return':str(x)} for i,x in enumerate(values)]
def mini(cid,day,net,excess=None,status='signal'):
    return {'case_id':cid,'date':day,'signal_status':status,'paired_control_status':'complete_three' if excess is not None else 'missing_return','risk_set_eligible':True,'flush_detected':True,'net_return':net,'matched_excess':excess,'entry_minute':32,'t_min':12}
class IndependentPayoffTests(unittest.TestCase):
    def test_twenty_percent_winrate_can_have_positive_expectancy(self):
        r=compute_distribution(records(['1','-.1','-.1','-.1','-.1']))
        self.assertEqual(Decimal(r['win_rate_all_n']),Decimal('.2'));self.assertEqual(Decimal(r['mean']),Decimal('.12'))
        self.assertEqual(Decimal(r['payoff_ratio_average_win_to_average_absolute_loss']),10)
    def test_eighty_percent_winrate_can_have_negative_expectancy(self):
        r=compute_distribution(records(['.1','.1','.1','.1','-1']))
        self.assertEqual(Decimal(r['win_rate_all_n']),Decimal('.8'));self.assertEqual(Decimal(r['mean']),Decimal('-.12'))
    def test_zero_outcome_changes_unconditional_but_not_nonzero_rate(self):
        r=compute_distribution(records(['1','-1','0','0']))
        self.assertEqual(Decimal(r['win_rate_all_n']),Decimal('.25'));self.assertEqual(Decimal(r['win_rate_among_nonzero']),Decimal('.5'))
        self.assertEqual(Decimal(r['break_even_win_rate_among_nonzero']),Decimal('.5'));self.assertEqual(r['zero_count'],2)
        self.assertEqual(Decimal(r['mean']),0)
    def test_profitfactor_uses_total_profit_and_total_loss(self):
        r=compute_distribution(records(['.3','.3','-.1']))
        self.assertEqual(Decimal(r['payoff_ratio_average_win_to_average_absolute_loss']),3)
        self.assertEqual(Decimal(r['profit_factor_total_positive_to_total_absolute_loss']),6)
    def test_mean_expectancy_identity_includes_zero(self):
        r=compute_distribution(records(['.7','-.2','0','0']))
        self.assertEqual(Decimal(r['mean']),Decimal('.125'));self.assertTrue(r['expectancy_identity']['exact_rational_identity_holds'])
    def test_all_positive_no_infinite_profitfactor_or_zero_loss_claim(self):
        r=compute_distribution(records(['.1','.2']))
        self.assertIsNone(r['conditional_average_absolute_loss']);self.assertIsNone(r['profit_factor_total_positive_to_total_absolute_loss']);self.assertIsNone(r['break_even_win_rate_among_nonzero'])
        self.assertTrue(r['undefined_statistic_reasons'])
    def test_all_negative_has_zero_profitfactor_and_unknown_payoff(self):
        r=compute_distribution(records(['-.1','-.2']))
        self.assertEqual(Decimal(r['profit_factor_total_positive_to_total_absolute_loss']),0);self.assertIsNone(r['payoff_ratio_average_win_to_average_absolute_loss'])
    def test_empty_and_allzero_distinct(self):
        empty=compute_distribution([]);zero=compute_distribution(records(['0','0']))
        self.assertIsNone(empty['mean']);self.assertEqual(Decimal(zero['mean']),0);self.assertIsNone(zero['win_rate_among_nonzero'])
    def test_top_k_based_on_all_events_and_capped_to_wins(self):
        r=compute_distribution(records(['1','2']+['-.1']*98))
        one=r['top_positive_tails']['top_1_percent_of_all_n'];five=r['top_positive_tails']['top_5_percent_of_all_n']
        self.assertEqual(one['requested_count_ceiling_fraction_times_all_n'],1);self.assertEqual(one['actual_removed_count'],1)
        self.assertEqual(five['requested_count_ceiling_fraction_times_all_n'],5);self.assertEqual(five['actual_removed_count'],2);self.assertEqual(five['remaining_count'],98)
    def test_positive_tail_share_does_not_divide_negative_net_total(self):
        r=compute_distribution(records(['2','1','-10']))
        t=r['top_positive_tails']['top_1_percent_of_all_n']
        self.assertAlmostEqual(float(t['share_of_total_positive_return']),2/3)
        self.assertEqual(Decimal(t['mean_without_selected_observations']),Decimal('-4.5'))
    def test_tail_tie_uses_case_id_not_input_order(self):
        rows=[{'case_id':'z','net_return':'1'},{'case_id':'a','net_return':'1'},{'case_id':'q','net_return':'-1'}]
        a=compute_distribution(rows);b=compute_distribution(list(reversed(rows)))
        self.assertEqual(a,b);self.assertEqual(a['top_positive_tails']['top_1_percent_of_all_n']['selected_case_ids'],['a'])
    def test_removing_tail_does_not_modify_original_main_mean(self):
        r=compute_distribution(records(['2','-1','-1']))
        self.assertEqual(Decimal(r['mean']),0);self.assertEqual(Decimal(r['top_positive_tails']['top_1_percent_of_all_n']['mean_without_selected_observations']),-1)
    def test_missing_is_not_zero_and_duplicate_is_not_extra_trial(self):
        with self.assertRaises(ValueError):compute_distribution([{'case_id':'a','net_return':None}])
        with self.assertRaises(ValueError):compute_distribution([{'case_id':'a','net_return':'1'},{'case_id':'a','net_return':'1'}])
    def test_no_binaryfloat_or_nonfinite_payoff_inputs(self):
        for x in [float('nan'),'.NaN','NaN','Infinity',True,.1]:
            with self.assertRaises(ValueError):compute_distribution([{'case_id':'a','net_return':x}])

class IndependentRelativeStatisticsTests(unittest.TestCase):
    def test_relative_win_is_not_target_profit(self):
        r=summarize([mini('a','d1','-.01','.03')])
        self.assertEqual(r['target_net_distribution']['positive_count'],0);self.assertEqual(r['matched_excess_distribution']['positive_fraction'],1)
    def test_date_equal_not_pooled_event_weighting(self):
        subset=[mini('only','d1','.1','.1')]+[mini('many'+str(i),'d2','-.1','-.1') for i in range(100)]
        r=summarize(subset)
        self.assertAlmostEqual(r['date_equal_target_net']['mean'],0);self.assertLess(Decimal(r['target_net_distribution']['mean']),0)
    def test_unknown_day_does_not_become_zero_return(self):
        r=summarize([mini('a','d1',None,None,'unknown'),mini('b','d2','.1','.1')])
        self.assertNotIn('d1',r['date_means']);self.assertEqual(r['date_equal_target_net']['n'],1)
    def test_contrast_uses_intersection_and_correct_sign(self):
        d={'a':{'matched_excess_mean':.1,'matched_excess_n':1},'b':{'matched_excess_mean':100,'matched_excess_n':1}}
        n={'a':{'matched_excess_mean':.3,'matched_excess_n':1},'c':{'matched_excess_mean':-100,'matched_excess_n':1}}
        r=contrast(list('abcd'),d,n,'test',True)
        self.assertAlmostEqual(r['date_difference_distribution']['mean'],.2);self.assertEqual(r['date_difference_distribution']['n'],1)
        self.assertEqual(r['date_status_counts'],{'both_groups':1,'down_only':1,'not_down_only':1,'neither_group':1})
        self.assertIsNone(r['bootstrap']['lower'])
    def test_zero_group_mean_is_available_not_missing(self):
        r=contrast(['a'],{'a':{'matched_excess_mean':0,'matched_excess_n':1}},{'a':{'matched_excess_mean':0,'matched_excess_n':1}},'zero',True)
        self.assertEqual(r['date_status_counts'],{'both_groups':1});self.assertEqual(r['date_difference_distribution']['mean'],0)
    def test_secondary_variants_do_not_silently_get_ci(self):
        self.assertIsNone(contrast(['a'],{}, {},'nonprimary',False)['bootstrap'])
    def test_negative_runs_follow_calendar_missing_and_zero_break(self):
        r=negative_runs(list('abcdefg'),{'a':-.1,'b':-.1,'c':0,'d':-.1,'f':-.1,'g':-.1})
        self.assertEqual(r['negative_runs'],[['a','b'],['d'],['f','g']]);self.assertEqual(r['maximum_observed_consecutive_negative_dates'],2)
        self.assertEqual(r['missing_dates_break_runs'],['e']);self.assertEqual(r['zero_dates_break_runs'],['c'])
    def test_nonconsecutive_observed_negative_dates_not_one_run(self):
        r=negative_runs(list('abc'),{'a':-1,'c':-1})
        self.assertEqual(r['maximum_observed_consecutive_negative_dates'],1)
    def test_bootstrap_all_equal_and_order_invariant(self):
        a=bootstrap({'b':.1,'a':.1},'x');b=bootstrap({'a':.1,'b':.1},'x')
        self.assertEqual(a,b);self.assertEqual(a['lower'],.1);self.assertEqual(a['upper'],.1)
    def test_empty_date_bootstrap_has_no_point_or_interval(self):
        r=bootstrap({},'empty');self.assertIsNone(r['point_estimate']);self.assertIsNone(r['lower']);self.assertEqual(r['status'],'insufficient_dates')

if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromModule(__import__(__name__));result=unittest.TextTestRunner(verbosity=2).run(suite)
    r={'completed_at_utc':datetime.now(timezone.utc).isoformat(),'passed':result.wasSuccessful(),'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),'bound_code_sha256':{n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in ['return_distribution.py','analyze_relative.py','independent_relative_stats_tests.py']},'actual_stratified_outcomes_used':False}
    (ROOT/'independent-relative-stats-test-binding.json').write_text(json.dumps(r,indent=2)+'\n')
    raise SystemExit(0 if result.wasSuccessful() else 1)
