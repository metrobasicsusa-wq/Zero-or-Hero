import copy
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from market_gates import evaluate_candidate

DAY='2026-01-05'


def candidate(day=DAY):
    return {'date':day,'symbol':'CASE','prior_session':'2026-01-02',
        'prior_close_adjusted':2,'daily_open_reference':2.2,'opening_gap_fraction':.1,
        'prior20_median_dollar_volume':10000000,'prior20_mean_adjusted_daily_volume':3000,
        'applied_splits':[],'current_classification_unknown':True}


def market(day=DAY, signal=30):
    start=datetime.fromisoformat(day+'T09:30:00').replace(tzinfo=ZoneInfo('America/New_York'))
    def bar(offset,o=2.2,h=2.3,l=2.1,c=2.2):
        return {'t':(start+timedelta(minutes=offset)).isoformat(),'o':o,'h':h,'l':l,'c':c,'v':100}
    rows={str(i):bar(i) for i in range(93)}
    if signal is not None:
        rows[str(signal)]=bar(signal,h=2.4,c=2.4)
        rows[str(signal+2)]=bar(signal+2,o=2.4,h=2.4,c=2.4)
    return rows


class MarketGateTests(unittest.TestCase):
    def test_exact_ten_percent_and_volume_equality_are_inclusive(self):
        r=evaluate_candidate(candidate(),market())
        self.assertTrue(r['market_gate_pass'])
        self.assertEqual(r['opening30']['volume'],3000)
        self.assertEqual(r['opening30']['exact_values']['gap_threshold_open'],'2.20')
        self.assertEqual(r['signal']['minute_offset'],30)
        self.assertEqual(r['entry_reference']['minute_offset'],32)
        self.assertEqual(r['signal']['completed_at_et'],'2026-01-05T10:01:00-05:00')
        self.assertEqual(r['entry_reference']['time_et'],'2026-01-05T10:02:00-05:00')

    def test_just_below_gap_threshold_is_not_rounded_up(self):
        m=market();m['0']['o']=2.199999999999
        self.assertEqual(evaluate_candidate(candidate(),m)['market_gate_status'],'opening_gap_gate_failed')

    def test_just_below_volume_threshold_is_not_rounded_up(self):
        c=candidate();c['prior20_mean_adjusted_daily_volume']=3000.00000001
        self.assertEqual(evaluate_candidate(c,market())['market_gate_status'],'opening30_volume_gate_failed')

    def test_prior_price_and_liquidity_minima(self):
        for field,value in [('prior_close_adjusted',1.99999),('prior20_median_dollar_volume',9999999.99)]:
            c=candidate();c[field]=value
            self.assertEqual(evaluate_candidate(c,market())['market_gate_status'],'prior_liquidity_gate_failed')

    def test_missing_prior_mean_is_unknown(self):
        c=candidate();del c['prior20_mean_adjusted_daily_volume']
        r=evaluate_candidate(c,market())
        self.assertEqual(r['market_gate_status'],'candidate_reference_unknown');self.assertIsNone(r['market_gate_pass'])

    def test_same_day_or_future_reference_is_not_prior_data(self):
        for previous in ('2026-01-05','2026-01-06',None):
            c=candidate();c['prior_session']=previous
            r=evaluate_candidate(c,market())
            self.assertEqual(r['market_gate_status'],'candidate_reference_unknown')
            self.assertEqual(r['issues'],['missing_or_noncausal_prior_session'])

    def test_first30_missing_is_unknown_not_zero_or_partial_volume(self):
        m=market();del m['14']
        r=evaluate_candidate(candidate(),m)
        self.assertEqual(r['market_gate_status'],'opening30_data_unknown')
        self.assertIsNone(r['opening30']);self.assertEqual(r['coverage']['opening30_observed_valid'],29)

    def test_current_timestamp_cannot_be_another_day(self):
        m=market();m['5']['t']='2026-01-06T09:35:00-05:00'
        r=evaluate_candidate(candidate(),m)
        self.assertEqual(r['market_gate_status'],'opening30_data_unknown')
        self.assertEqual(r['issues'][0]['opening30_gaps'][0]['reason'],'timestamp_not_current_session_exact_minute')

    def test_offset_timestamp_cannot_be_wrong_minute(self):
        m=market();m['30']['t']=m['31']['t']
        self.assertEqual(evaluate_candidate(candidate(),m)['market_gate_status'],'signal_data_unknown')

    def test_nanosecond_after_minute_is_not_truncated_into_valid_bar(self):
        m=market();m['0']['t']='2026-01-05T14:30:00.000000001Z'
        r=evaluate_candidate(candidate(),m)
        self.assertEqual(r['market_gate_status'],'opening30_data_unknown')
        m['0']['t']='2026-01-05T14:30:00.000000000Z'
        self.assertTrue(evaluate_candidate(candidate(),m)['market_gate_pass'])

    def test_missing_source_timestamp_cannot_be_invented(self):
        m=market();del m['0']['t']
        self.assertEqual(evaluate_candidate(candidate(),m)['market_gate_status'],'opening30_data_unknown')

    def test_duplicate_string_and_integer_key_is_unknown(self):
        m=market();m[0]=copy.deepcopy(m['0'])
        self.assertEqual(evaluate_candidate(candidate(),m)['market_gate_status'],'opening30_data_unknown')

    def test_first_missing_signal_bar_prevents_picking_later_breakout(self):
        m=market(signal=40);del m['35']
        r=evaluate_candidate(candidate(),m)
        self.assertEqual(r['market_gate_status'],'signal_data_unknown');self.assertIsNone(r['signal'])
        self.assertEqual(r['coverage']['signal_minutes_observed'],5)

    def test_equal_to_range_high_is_not_breakout(self):
        m=market(signal=None)
        for i in range(30,90):m[str(i)]['c']=2.3
        r=evaluate_candidate(candidate(),m)
        self.assertEqual(r['market_gate_status'],'no_breakout_before_11')
        self.assertEqual(r['coverage']['signal_minutes_observed'],60)

    def test_last_signal_at_1059_enters_1101(self):
        r=evaluate_candidate(candidate(),market(signal=89))
        self.assertEqual(r['signal']['completed_at_et'],'2026-01-05T11:00:00-05:00')
        self.assertEqual(r['entry_reference']['time_et'],'2026-01-05T11:01:00-05:00')

    def test_1100_signal_is_outside_frozen_window(self):
        self.assertEqual(evaluate_candidate(candidate(),market(signal=90))['market_gate_status'],'no_breakout_before_11')

    def test_missing_selected_entry_keeps_signal_and_unknown_state(self):
        m=market();del m['32']
        r=evaluate_candidate(candidate(),m)
        self.assertEqual(r['market_gate_status'],'entry_reference_unknown')
        self.assertIsNotNone(r['signal']);self.assertIsNone(r['market_gate_pass'])

    def test_entry_at_or_below_stop_is_unresolved_exposure(self):
        for opening in (2.1,2.0):
            m=market();m['32']['o']=opening
            r=evaluate_candidate(candidate(),m)
            self.assertEqual(r['market_gate_status'],'incomplete_gap_through_entry_stop')
            self.assertIsNone(r['market_gate_pass']);self.assertTrue(r['entry_reference']['gap_through_stop'])

    def test_entry_uses_only_open_not_later_minute_high_low_close(self):
        a=evaluate_candidate(candidate(),market());m=market()
        m['32'].update(h=999,l=.01,c=.5,v=0)
        self.assertEqual(a,evaluate_candidate(candidate(),m))

    def test_future_data_cannot_change_first_signal_or_ranges(self):
        m=market();a=evaluate_candidate(candidate(),m)
        for i in range(33,93):m[str(i)]={'invalid':'future'}
        self.assertEqual(a,evaluate_candidate(candidate(),m))

    def test_wait_minute_missing_does_not_invent_a_position(self):
        m=market();del m['31']
        self.assertTrue(evaluate_candidate(candidate(),m)['market_gate_pass'])

    def test_daily_future_high_close_volume_are_never_used(self):
        c=candidate();c.update(daily_high=999,daily_low=.01,daily_close=800,daily_volume=999999999)
        self.assertEqual(evaluate_candidate(c,market()),evaluate_candidate(candidate(),market()))

    def test_regular_open_overrides_stale_daily_reference_for_gap(self):
        c=candidate();c['daily_open_reference']=8
        r=evaluate_candidate(c,market())
        self.assertEqual(r['opening30']['open'],2.2)
        self.assertFalse(r['opening30']['daily_reference_open_matches_regular_open'])

    def test_split_references_preserved_without_second_adjustment(self):
        c=candidate();c['applied_splits']=[{'date':'2025-12-20','ratio':2}]
        r=evaluate_candidate(c,market())
        self.assertEqual(r['source_candidate_reference']['applied_splits'],c['applied_splits'])
        self.assertTrue(r['market_gate_pass']);self.assertTrue(r['source_candidate_reference']['current_classification_unknown'])

    def test_news_status_does_not_change_market_gates(self):
        c=candidate();a=evaluate_candidate(c,market());c['news_status']='missing'
        self.assertEqual(a,evaluate_candidate(c,market()));c['news_status']='confirmed'
        self.assertEqual(a,evaluate_candidate(c,market()))

    def test_DST_session_uses_correct_UTC(self):
        day='2026-07-02';r=evaluate_candidate(candidate(day),market(day))
        self.assertEqual(r['entry_reference']['time_utc'],'2026-07-02T14:02:00+00:00')

    def test_bad_OHLC_and_nonfinite_volume_are_unknown(self):
        for fields in ({'h':1},{'v':float('nan')},{'v':-1},{'o':True}):
            m=market();m['5'].update(fields)
            self.assertEqual(evaluate_candidate(candidate(),m)['market_gate_status'],'opening30_data_unknown')


if __name__=='__main__':unittest.main()
