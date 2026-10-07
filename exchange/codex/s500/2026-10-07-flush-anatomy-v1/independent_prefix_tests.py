"""Independent causal descriptor checks; generated synthetic bars only."""
import copy
import random
import unittest
from decimal import Decimal, localcontext
from fractions import Fraction
from prefix_features import first30_features


def make(seed=7):
    rng=random.Random(seed)
    out=[]
    for t in range(30):
        o=10000+rng.randrange(-300,301); c=10000+rng.randrange(-300,301)
        out.append({'t':t,'o':str(o),'h':str(max(o,c)+rng.randrange(1,40)),
                    'l':str(min(o,c)-rng.randrange(1,40)),'c':str(c),'v':str(rng.randrange(1000))})
    return out


def oracle(data,prior):
    rows=sorted(data,key=lambda r:r['t']);o=Fraction(rows[0]['o'])
    cs=[Fraction(r['c']) for r in rows]; hs=[Fraction(r['h']) for r in rows]; ls=[Fraction(r['l']) for r in rows]
    low=min(cs); high=max(cs)
    return {'open0':o,'close29':cs[-1],'min_close':low,'t_min':cs.index(low),'max_close':high,
            'close29_return':cs[-1]/o-1,'min_close_return':low/o-1,'max_close_return':high/o-1,
            'first30_volume':sum(Fraction(r['v']) for r in rows),'first30_high':max(hs),'first30_low':min(ls),
            'first30_high_low_range':(max(hs)-min(ls))/o,'previous_close':Fraction(prior),
            'gap_to_previous_close':o/Fraction(prior)-1}


class IndependentPrefixTests(unittest.TestCase):
    def test_randomized_fraction_oracle(self):
        for seed in range(40):
            bars=make(seed);prior=str(9999+seed); actual=first30_features(bars,prior); expect=oracle(bars,prior)
            self.assertEqual(actual['status'],'complete')
            for key,value in expect.items():
                with self.subTest(seed=seed,key=key):
                    if key=='t_min':self.assertEqual(actual[key],value)
                    else:
                        difference=abs(Fraction(actual[key])-value)
                        self.assertLessEqual(difference,Fraction(1,10**38)*max(1,abs(value)))
    def test_thirty_closes_monotonic_increase_keeps_open_anchor(self):
        data=[{'t':t,'o':'100','h':str(101+t),'l':'99','c':str(101+t),'v':'1'} for t in range(30)]
        row=first30_features(data,'80')
        self.assertEqual(row['t_min'],0);self.assertEqual(Decimal(row['min_close_return']),Decimal('.01'))
        self.assertEqual(Decimal(row['gap_to_previous_close']),Decimal('.25'))
    def test_closes_tie_earliest_independent_of_record_order(self):
        data=make();
        for r in data:r.update(o='100',h='101',l='97',c='100')
        for t in (8,2,29):data[t]['c']='98'
        random.Random(23).shuffle(data)
        self.assertEqual(first30_features(data,'100')['t_min'],2)
    def test_intra_bar_extremes_never_replace_close_extremes(self):
        data=make();before=first30_features(data,'10000')
        data[15]['h']='999999';data[19]['l']='.01';after=first30_features(data,'10000')
        for key in ('t_min','min_close','max_close','min_close_return','max_close_return'):
            self.assertEqual(before[key],after[key])
        self.assertNotEqual(before['first30_high_low_range'],after['first30_high_low_range'])
    def test_known_future_mutations_cannot_change_descriptor(self):
        data=make();before=first30_features(data,'10000')
        data += [{'t':t,'c':'NaN'} for t in (-100,-1,30,31,89,10000000)]
        data += [{'t':30,'v':'-500'}]*5
        self.assertEqual(before,first30_features(data,'10000'))
    def test_noninteger_timestamp_conservatively_unknown(self):
        for timestamp in (None,True,'30',Decimal(30),30.0):
            data=make()+[{'t':timestamp}]
            self.assertFalse(first30_features(data,'10000')['prefix_valid'])
    def test_last_close_required_and_next_bar_cannot_replace(self):
        data=make();data[-1]['t']=30
        row=first30_features(data,'10000')
        self.assertFalse(row['prefix_valid']);self.assertIsNone(row['gap_to_previous_close'])
        self.assertIn(29,row['coverage']['missing_minutes'])
    def test_duplicate_exact_record_is_unknown(self):
        data=make();data.append(copy.deepcopy(data[0]))
        row=first30_features(data,'10000');self.assertFalse(row['prefix_valid'])
        self.assertIsNone(row['first30_volume'])
    def test_missing_prior_close_not_synthetic_flat_gap(self):
        data=make();good=first30_features(data,'10000');bad=first30_features(data,None)
        self.assertTrue(bad['prefix_valid']);self.assertIsNone(bad['gap_to_previous_close'])
        for key in oracle(data,'10000'):
            if key not in ('previous_close','gap_to_previous_close'):self.assertEqual(good[key],bad[key])
    def test_source_completion_is_literal_not_truthiness(self):
        for flag in (1,'yes',[],{},False,None):
            row=first30_features(make(),'10000',source_complete=flag)
            self.assertFalse(row['prefix_valid']);self.assertIsNone(row['open0'])
    def test_volume_scale_changes_only_volume(self):
        data=make();before=first30_features(data,'10000')
        for row in data:row['v']=str(int(row['v'])*17)
        after=first30_features(data,'10000')
        self.assertEqual(Decimal(after['first30_volume']),Decimal(before['first30_volume'])*17)
        after['first30_volume']=before['first30_volume'];self.assertEqual(before,after)
    def test_no_input_mutation_and_iterator_contract(self):
        data=make();frozen=copy.deepcopy(data);actual=first30_features(iter(data),'10000')
        self.assertEqual(data,frozen);self.assertEqual(actual,first30_features(list(reversed(data)),'10000'))

if __name__=='__main__':unittest.main()
