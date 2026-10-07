import unittest
from run_anatomy import price_bin,bins,summary
def r(cid,day,net=None,excess=None,status='signal'):
    return {'case_id':cid,'date':day,'net_return':net,'matched_excess':excess,'signal_status':status,'paired_control_status':'complete_three' if excess is not None else 'missing_return'}
class AnatomyRunnerTests(unittest.TestCase):
    def test_exact_boundaries(self):
        self.assertEqual(price_bin('98','100'),'le_minus_2pct');self.assertEqual(price_bin('98.0000000000000000000000000000000000000000000001','100'),'minus_2pct_to_below_zero');self.assertEqual(price_bin('100','100'),'zero_or_positive')
    def test_invalid_or_missing_price(self):
        self.assertEqual(price_bin(None,'100'),'unknown')
        with self.assertRaises(ValueError):price_bin('0','100')
    def test_bins_unknown(self):
        self.assertEqual(set(bins({'prefix_valid':False}).values()),{'unknown'})
    def test_gap_unknown_retains_other_bins(self):
        b=bins({'prefix_valid':True,'previous_close_valid':False,'open0':'100','close29':'99','t_min':29});self.assertEqual(b,{'gap':'unknown','close29':'minus_2pct_to_below_zero','trough':'minute20_29'})
    def test_all_states_retained_not_zero(self):
        s=summary([r('a','d1',status='no_flush'),r('b','d1',status='no_rebound'),r('c','d2',status='unknown'),r('d','d2'),r('e','d3','0')]);self.assertEqual(s['counts']['selected_cases'],5);self.assertEqual(s['counts']['complete_net'],1);self.assertEqual(s['counts']['signal_return_missing'],1);self.assertEqual(s['target_net_distribution']['zero_count'],1)
    def test_net_excess_not_same_denominator(self):
        s=summary([r('a','d1','-0.1','0.2'),r('b','d2','0.3')]);self.assertEqual(s['counts']['complete_pairs'],1);self.assertEqual(s['counts']['complete_net'],2);self.assertAlmostEqual(s['matched_excess']['mean'],.2)
    def test_date_weighting(self):
        s=summary([r('a','d1','0.1'),r('b','d1','0.3'),r('c','d2','-0.2')]);self.assertAlmostEqual(s['date_equal_net']['mean'],0);self.assertAlmostEqual(float(s['target_net_distribution']['mean']),.2/3)
    def test_empty(self):
        s=summary([]);self.assertEqual(s['counts']['selected_cases'],0);self.assertIsNone(s['date_equal_net']['mean'])
if __name__=='__main__':unittest.main()
