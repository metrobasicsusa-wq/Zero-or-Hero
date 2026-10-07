"""Independent behavioral, time-causality and arithmetic tests of the frozen event API."""
import copy
import hashlib
import json
import unittest
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from pathlib import Path

from flush_engine import evaluate_prefix, family_decision, evaluate_window, evaluate_day, prepare_bars

ROOT=Path(__file__).resolve().parent

def bar(t,close='97',open_=None,low=None,high=None):
    c=Decimal(str(close));o=Decimal(str(open_ if open_ is not None else close))
    return {'t':t,'o':str(o),'h':str(Decimal(str(high)) if high is not None else max(o,c)),
            'l':str(Decimal(str(low)) if low is not None else min(o,c)),'c':str(c),'v':'1000'}

def day(close='97',count=390):
    rows={t:bar(t,close) for t in range(count)}
    rows[0]=bar(0,close,open_='100')
    return rows

class IndependentCausalTests(unittest.TestCase):
    def test_threshold_exact_boundary(self):
        b=day('98')
        self.assertTrue(evaluate_prefix(b,'0.02')['is_flush'])
        self.assertFalse(evaluate_prefix(b,'0.03')['is_flush'])
    def test_threshold_just_above(self):
        self.assertFalse(evaluate_prefix(day('98.0000000001'),'0.02')['is_flush'])
    def test_intrabar_low_does_not_create_close_flush(self):
        b=day('100');b[2]=bar(2,'100',low='50')
        self.assertFalse(evaluate_prefix(b,'0.02')['is_flush'])
    def test_opening_anchor_is_open_not_first_close(self):
        p=evaluate_prefix(day('97'),'0.02')
        self.assertEqual(p['opening_open'],'100')
        self.assertEqual(Decimal(p['opening_drop_fraction']),Decimal('.03'))
    def test_missing_first_minute_unknown(self):
        b=day();del b[0]
        self.assertIsNone(evaluate_prefix(b,'.02')['is_flush'])
    def test_missing_prefix_middle_unknown(self):
        b=day();del b[14]
        self.assertEqual(family_decision(b,'.02','FIXED')['status'],'unknown_prefix')
    def test_fixed_uses_exact_31_and_not_signal_bar(self):
        d=family_decision(day(),'.02','FIXED')
        self.assertEqual((d['signal_minute'],d['decision_minute'],d['entry_minute']),(29,30,31))
    def test_fixed_does_not_require_later_search_bars(self):
        b=day();b={k:v for k,v in b.items() if k<30}
        self.assertEqual(family_decision(b,'.02','FIXED')['status'],'signal')
    def test_rebound_earliest_t30_entry32(self):
        b=day();b[30]=bar(30,'97.97')
        d=family_decision(b,'.02','REBOUND')
        self.assertEqual((d['signal_minute'],d['decision_minute'],d['entry_minute']),(30,31,32))
    def test_rebound_just_below_threshold_no_early_signal(self):
        b=day();b[30]=bar(30,'97.9699999999');b[31]=bar(31,'97.97')
        self.assertEqual(family_decision(b,'.02','REBOUND')['signal_minute'],31)
    def test_new_low_cannot_same_bar_rebound_via_high(self):
        b=day();b[30]=bar(30,'95',high='99');b[31]=bar(31,'95.95')
        d=family_decision(b,'.02','REBOUND')
        self.assertEqual(d['signal_minute'],31)
        self.assertEqual(Decimal(d['prior_running_min_close']),Decimal('95'))
    def test_later_lower_low_does_not_move_prior_signal(self):
        b=day();b[35]=bar(35,'98')
        before=family_decision(b,'.02','REBOUND')
        b[50]=bar(50,'10');b[200]=bar(200,'1')
        self.assertEqual(before,family_decision(b,'.02','REBOUND'))
    def test_later_lower_low_does_not_create_historical_rebound(self):
        b=day();b[100]=bar(100,'50')
        self.assertEqual(family_decision(b,'.02','REBOUND')['status'],'valid_no_rebound')
    def test_last_search_minute_allowed(self):
        b=day();b[89]=bar(89,'98')
        d=family_decision(b,'.02','REBOUND')
        self.assertEqual((d['signal_minute'],d['entry_minute']),(89,91))
    def test_after_cutoff_signal_not_allowed(self):
        b=day();b[90]=bar(90,'98')
        self.assertEqual(family_decision(b,'.02','REBOUND')['status'],'valid_no_rebound')
    def test_missing_search_bar_prevents_later_confirmation(self):
        b=day();del b[31];b[35]=bar(35,'98')
        d=family_decision(b,'.02','REBOUND')
        self.assertEqual((d['status'],d['unknown_minute']),('unknown_rebound_prefix',31))
    def test_later_missing_search_bar_cannot_revoke_signal(self):
        b=day();b[30]=bar(30,'98');before=family_decision(b,'.02','REBOUND');del b[40]
        self.assertEqual(before,family_decision(b,'.02','REBOUND'))
    def test_later_invalid_ohlc_cannot_revoke_signal(self):
        b=day();b[30]=bar(30,'98');before=family_decision(b,'.02','REBOUND');b[40]['h']='1'
        self.assertEqual(before,family_decision(b,'.02','REBOUND'))
    def test_duplicate_early_timestamp_unknown(self):
        rows=list(day().values());rows.append(bar(2,'97'))
        self.assertEqual(evaluate_prefix(rows,'.02')['status'],'unknown_prefix')
    def test_duplicate_later_timestamp_does_not_affect_fixed(self):
        rows=list(day().values());rows.append(bar(100,'97'))
        self.assertEqual(family_decision(rows,'.02','FIXED')['status'],'signal')
    def test_missing_volume_is_invalid_not_zero_filled(self):
        b=day();del b[5]['v']
        self.assertIsNone(evaluate_prefix(b,'.02')['is_flush'])
    def test_invalid_ohlc_is_not_repaired(self):
        b=day();b[7]['l']='101'
        self.assertEqual(evaluate_prefix(b,'.02')['status'],'unknown_prefix')
    def test_nonfinite_number_invalid(self):
        b=day();b[7]['c']='NaN'
        self.assertEqual(evaluate_prefix(b,'.02')['status'],'unknown_prefix')
    def test_boolean_timestamp_invalid(self):
        b=list(day().values());b[7]['t']=True
        self.assertEqual(evaluate_prefix(b,'.02')['status'],'unknown_source')
    def test_incomplete_api_request_unknown(self):
        self.assertEqual(family_decision(day(),'.02','FIXED',source_complete=False)['status'],'unknown_source')
    def test_input_order_invariance(self):
        b=day();b[37]=bar(37,'98')
        self.assertEqual(family_decision(b,'.02','REBOUND'),family_decision(list(reversed(list(b.values()))),'.02','REBOUND'))
    def test_uniform_price_scaling_preserves_signal_and_return(self):
        b=day();b[37]=bar(37,'98');b[99]=bar(99,'103')
        scaled=copy.deepcopy(b)
        for row in scaled.values():
            for field in 'ohlc':row[field]=str(Decimal(row[field])*Decimal('7.25'))
        a=family_decision(b,'.02','REBOUND');s=family_decision(scaled,'.02','REBOUND')
        self.assertEqual((a['status'],a['signal_minute'],a['entry_minute']),(s['status'],s['signal_minute'],s['entry_minute']))
        self.assertEqual(evaluate_window(b,39,99,10)['net_return'],evaluate_window(scaled,39,99,10)['net_return'])
    def test_inputs_not_mutated(self):
        b=day();before=copy.deepcopy(b);evaluate_day(b)
        self.assertEqual(b,before)

class IndependentWindowTests(unittest.TestCase):
    def test_two_sided_cost_exact(self):
        b=day();b[31]=bar(31,'100');b[91]=bar(91,'110')
        r=evaluate_window(b,31,91,10)
        with localcontext() as ctx:
            ctx.prec=42
            expected=Decimal('109.89')/Decimal('100.1')-1
        self.assertEqual(Decimal(r['net_return']),expected)
        self.assertEqual(Decimal(r['gross_return']),Decimal('.1'))
    def test_cost_monotonically_reduces_same_path_return(self):
        b=day();values=[Decimal(evaluate_window(b,31,61,c)['net_return']) for c in [0,5,10,25]]
        self.assertTrue(all(a>b for a,b in zip(values,values[1:])))
        self.assertEqual(values[0],0)
    def test_missing_entry_never_uses_next_open(self):
        b=day();del b[31];b[32]=bar(32,'1')
        self.assertEqual(evaluate_window(b,31,61,10)['status'],'unknown_window')
    def test_missing_exit_never_uses_previous_open(self):
        b=day();del b[61];b[60]=bar(60,'1000')
        self.assertIsNone(evaluate_window(b,31,61,10)['net_return'])
    def test_middle_missing_preserved(self):
        b=day();del b[45]
        self.assertEqual(evaluate_window(b,31,61,10)['coverage']['missing_minutes'],[45])
    def test_endpoints_are_opens_not_highs_or_closes(self):
        b=day();b[31]=bar(31,'150',open_='100');b[61]=bar(61,'500',open_='110')
        self.assertEqual(Decimal(evaluate_window(b,31,61,0)['gross_return']),Decimal('.1'))
    def test_future_after_exit_does_not_affect_window(self):
        b=day();before=evaluate_window(b,31,61,10);b[200]['c']='NaN'
        self.assertEqual(before,evaluate_window(b,31,61,10))
    def test_no_verified_fill_claim(self):
        r=evaluate_window(day(),31,61,10)
        self.assertFalse(r['verified_fill'])
        self.assertEqual(r['price_kind'],'minute_open_proxy')
    def test_nontrading_or_reversed_window_invalid(self):
        for entry,exit_ in [(-1,30),(31,31),(40,30),(31,390)]:
            self.assertEqual(evaluate_window(day(),entry,exit_,10)['status'],'invalid_window')
    def test_registered_grid_all_rows_retained_without_signal(self):
        rows=evaluate_day(day('100'))
        self.assertEqual(len(rows),36)
        self.assertEqual(len({(r['threshold'],r['family'],r['horizon'],r['per_side_cost_bps']) for r in rows}),36)
        self.assertTrue(all(r['status']=='no_flush' and r['net_return'] is None for r in rows))
    def test_registered_grid_all_rows_retained_when_unknown(self):
        rows=evaluate_day({})
        self.assertEqual(len(rows),36)
        self.assertTrue(all(r['status']=='unknown_prefix' and r['net_return'] is None for r in rows))
    def test_half_day_close_minus10_is_relative_to_session(self):
        rows=evaluate_day(day(count=210),session_minutes=210)
        fixed=[r for r in rows if r['family']=='FIXED' and r['horizon']=='close_minus_10m']
        self.assertTrue(all(r['exit_minute']==200 for r in fixed))
    def test_prepared_input_cannot_promote_incomplete_source(self):
        p=prepare_bars(day(),source_complete=False)
        self.assertEqual(evaluate_prefix(p,'.02')['status'],'unknown_source')

if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromModule(__import__(__name__))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    record={'completed_at_utc':datetime.now(timezone.utc).isoformat(),'tests_run':result.testsRun,
        'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),
        'passed':result.wasSuccessful(),'engine_sha256':hashlib.sha256((ROOT/'flush_engine.py').read_bytes()).hexdigest(),
        'independent_tests_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'actual_minute_outcomes_used_for_fixtures':False}
    (ROOT/'independent-engine-test-binding.json').write_text(json.dumps(record,indent=2)+'\n')
    raise SystemExit(0 if result.wasSuccessful() else 1)
