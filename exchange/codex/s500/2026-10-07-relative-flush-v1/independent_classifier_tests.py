"""Independent causal, precision and missing-data tests; synthetic bars only."""
import copy,hashlib,json,unittest
from datetime import datetime,timezone
from decimal import Decimal,localcontext
from pathlib import Path
from classify_relative import classify_case
ROOT=Path(__file__).resolve().parent

def bar(t,c='100',o=None):
    if o is None:o=c
    return {'t':t,'o':o,'h':str(max(Decimal(o),Decimal(c))),'l':str(min(Decimal(o),Decimal(c))),'c':c,'v':'100'}
def prefix(c='100'):
    values=[bar(t,c) for t in range(30)];values[0]=bar(0,c,'100');return values

def one_low(t=12,c='97'):
    values=prefix();values[t]=bar(t,c);return values

class IndependentClassifierTests(unittest.TestCase):
    def test_earliest_tied_minimum_uses_earlier_benchmark(self):
        target=one_low(2);target[17]=bar(17,'97');benchmark=prefix();benchmark[2]=bar(2,'99.4');benchmark[17]=bar(17,'100.2')
        r=classify_case(target,benchmark)
        self.assertEqual(r['t_min'],2);self.assertEqual(r['classification'],'market_down')
    def test_benchmark_own_low_not_used(self):
        target=one_low(12);benchmark=prefix();benchmark[1]=bar(1,'90');benchmark[12]=bar(12,'99.9')
        r=classify_case(target,benchmark)
        self.assertEqual(r['classification'],'market_not_down');self.assertEqual(Decimal(r['benchmark_return']),Decimal('-.001'))
    def test_final_prefix_benchmark_not_used(self):
        target=one_low(12);benchmark=prefix();benchmark[12]=bar(12,'99.8');benchmark[29]=bar(29,'90')
        self.assertEqual(classify_case(target,benchmark)['classification'],'market_not_down')
    def test_threshold_exact_inclusive(self):
        b=prefix();b[12]=bar(12,'99.5')
        self.assertEqual(classify_case(one_low(),b)['classification'],'market_down')
    def test_above_boundary_after_display_precision_still_not_down(self):
        b=prefix();b[12]=bar(12,'99.50000000000000000000000000000000000000000000000000000001')
        self.assertEqual(classify_case(one_low(),b)['classification'],'market_not_down')
    def test_below_boundary_after_display_precision_still_down(self):
        b=prefix();b[12]=bar(12,'99.49999999999999999999999999999999999999999999999999999999')
        self.assertEqual(classify_case(one_low(),b)['classification'],'market_down')
    def test_relative_return_is_difference_of_fractional_returns(self):
        t=prefix();t[0]=bar(0,'10','10');t[12]=bar(12,'9');b=prefix();b[0]=bar(0,'1000','1000');b[12]=bar(12,'990')
        r=classify_case(t,b)
        self.assertEqual(r['t_min'],12);self.assertEqual(Decimal(r['target_return']),Decimal('-.1'))
        self.assertEqual(Decimal(r['benchmark_return']),Decimal('-.01'));self.assertEqual(Decimal(r['relative_return']),Decimal('-.09'))
    def test_target_close_not_intrabar_low_selects_minute(self):
        t=one_low(12);t[1]['l']='1'
        self.assertEqual(classify_case(t,prefix())['t_min'],12)
    def test_no_flush_threshold_added_by_classifier(self):
        r=classify_case(prefix(),prefix())
        self.assertEqual(r['classification'],'market_not_down');self.assertEqual(r['t_min'],0)
    def test_target_later_low_cannot_change_classification(self):
        t=one_low();b=prefix();before=classify_case(t,b);t.append(bar(50,'1'));b.append(bar(50,'1'))
        self.assertEqual(before,classify_case(t,b))
    def test_known_future_invalid_bar_ignored_before_validation(self):
        t=one_low();b=prefix();before=classify_case(t,b);t.append({'t':60,'c':'NaN'});b.extend([{'t':60},{'t':60}])
        self.assertEqual(before,classify_case(t,b))
    def test_known_presession_bar_does_not_change_prefix(self):
        t=one_low();before=classify_case(t,prefix());t.append(bar(-1,'1'))
        self.assertEqual(before,classify_case(t,prefix()))
    def test_unlocatable_timestamp_retained_unknown(self):
        t=one_low();t.append({'t':'50','c':'1'})
        self.assertEqual(classify_case(t,prefix())['status'],'unknown_target_prefix')
    def test_missing_benchmark_after_target_low_still_unknown(self):
        b=prefix();b.pop(29)
        r=classify_case(one_low(2),b)
        self.assertEqual(r['status'],'unknown_benchmark_prefix');self.assertIsNone(r['classification']);self.assertEqual(r['t_min'],2)
    def test_unknown_target_retains_only_benchmark_open(self):
        t=one_low();t.pop(5);r=classify_case(t,prefix())
        self.assertEqual(r['status'],'unknown_target_prefix');self.assertIsNone(r['benchmark_return']);self.assertEqual(r['benchmark_open'],'100')
    def test_both_prefixes_missing_is_not_not_down(self):
        r=classify_case([],[])
        self.assertEqual(r['status'],'unknown_both_prefixes');self.assertIsNone(r['classification'])
    def test_duplicate_prefix_minute_unknown(self):
        t=one_low();t.append(bar(12,'97'))
        self.assertEqual(classify_case(t,prefix())['status'],'unknown_target_prefix')
    def test_source_complete_requires_literal_true(self):
        for value in [False,1,'true',None]:
            self.assertEqual(classify_case(one_low(),prefix(),source_complete=value)['status'],'unknown_both_prefixes')
    def test_input_order_cannot_change_earliest_time(self):
        t=one_low(2);t[20]=bar(20,'97');b=prefix()
        self.assertEqual(classify_case(t,b),classify_case(list(reversed(t)),list(reversed(b))))
    def test_independent_asset_scaling_preserves_returns_and_group(self):
        t=one_low();b=prefix();b[12]=bar(12,'99.4');before=classify_case(t,b)
        scaledt=copy.deepcopy(t);scaledb=copy.deepcopy(b)
        for rows,mult in [(scaledt,Decimal('7.25')),(scaledb,Decimal('3.17'))]:
            for row in rows:
                for field in 'ohlc':row[field]=str(Decimal(row[field])*mult)
        after=classify_case(scaledt,scaledb)
        for key in ['t_min','target_return','benchmark_return','relative_return','classification']:self.assertEqual(before[key],after[key])
    def test_nonfinite_prefix_not_repaired(self):
        b=prefix();b[5]['c']='NaN'
        self.assertEqual(classify_case(one_low(),b)['status'],'unknown_benchmark_prefix')
    def test_inputs_not_mutated(self):
        t=one_low();b=prefix();copyt=copy.deepcopy(t);copyb=copy.deepcopy(b);classify_case(t,b)
        self.assertEqual(t,copyt);self.assertEqual(b,copyb)

if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(IndependentClassifierTests))
    record={'completed_at_utc':datetime.now(timezone.utc).isoformat(),'passed':result.wasSuccessful(),'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),'classifier_sha256':hashlib.sha256((ROOT/'classify_relative.py').read_bytes()).hexdigest(),'independent_tests_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'actual_stratified_outcomes_used':False}
    (ROOT/'independent-classifier-test-binding.json').write_text(json.dumps(record,indent=2)+'\n')
    raise SystemExit(0 if result.wasSuccessful() else 1)
