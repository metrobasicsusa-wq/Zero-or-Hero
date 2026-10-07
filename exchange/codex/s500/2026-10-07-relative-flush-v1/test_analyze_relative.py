import unittest
from analyze_relative import summarize,contrast,negative_runs,bootstrap
def row(cid,day,ret,excess=None,status='signal'):
    return {'case_id':cid,'date':day,'signal_status':status,'paired_control_status':'complete_three' if excess is not None else 'missing_return','risk_set_eligible':True,'flush_detected':True,'net_return':ret,'matched_excess':excess,'entry_minute':32,'t_min':15}
class RelativeStatisticsTests(unittest.TestCase):
    def test_dates_not_event_weighted(self):
        s=summarize([row('a','d1','0.1','0.05'),row('b','d1','0.3','0.15'),row('c','d2','-0.2','-0.1')])
        self.assertAlmostEqual(s['date_equal_target_net']['mean'],0)
        self.assertAlmostEqual(float(s['target_net_distribution']['mean']),.2/3)
    def test_unknown_not_zero(self):
        s=summarize([row('a','d1',None,None,'unknown'),row('b','d2','0')])
        self.assertEqual(s['target_net_distribution']['n'],1);self.assertEqual(s['target_net_distribution']['zero_count'],1)
        self.assertEqual(s['signal_status_counts']['unknown'],1);self.assertNotIn('d1',s['date_means'])
    def test_same_date_only(self):
        c=contrast(['d1','d2','d3','d4'],{'d1':{'matched_excess_mean':.1,'matched_excess_n':1},'d2':{'matched_excess_mean':.2,'matched_excess_n':2}},{'d1':{'matched_excess_mean':.3,'matched_excess_n':1},'d3':{'matched_excess_mean':.1,'matched_excess_n':1}},'test',True)
        self.assertEqual(c['date_status_counts'],{'both_groups':1,'down_only':1,'not_down_only':1,'neither_group':1});self.assertAlmostEqual(c['date_difference_distribution']['mean'],.2);self.assertIsNone(c['bootstrap']['lower'])
    def test_streak_missing_zero_break(self):
        s=negative_runs(list('abcdefg'),{'a':-.1,'b':-.1,'d':-.2,'e':0,'f':-.3,'g':.1})
        self.assertEqual(s['maximum_observed_consecutive_negative_dates'],2);self.assertEqual(s['missing_dates_break_runs'],['c']);self.assertEqual(s['negative_runs'],[['a','b'],['d'],['f']])
    def test_bootstrap_deterministic_order_invariant(self):
        self.assertEqual(bootstrap({'b':.1,'a':-.1},'x'),bootstrap({'a':-.1,'b':.1},'x'))
    def test_net_and_excess_separated(self):
        s=summarize([row('a','d1','-0.1','0.2')]);self.assertEqual(s['target_net_distribution']['positive_count'],0);self.assertEqual(s['matched_excess_distribution']['positive_fraction'],1)
    def test_empty_group(self):
        s=summarize([]);self.assertEqual(s['target_net_distribution']['n'],0);self.assertIsNone(s['date_equal_target_net']['mean'])
if __name__=='__main__':unittest.main()
