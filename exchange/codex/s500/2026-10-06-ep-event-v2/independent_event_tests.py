"""Independent v2 causality, event-absence and valuation tests; offline only."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
import unittest

import ep_engine as base
import ep_event_engine as event

V={'exit_mode':'fixed10','fraction':1,'cost_bps':25}
PROVIDER='provider_event_series_assumption'


def fixture():
    dates=[];day=date(2025,12,15)
    while len(dates)<77:
        if day.weekday()<5:dates.append(day.isoformat())
        day+=timedelta(days=1)
    calendar=[{'date':d,'open':'09:30','close':'16:00','settlement_date':dates[i+1]} for i,d in enumerate(dates[:-1])]
    market={s['date']:{'A':{str(m):{'t':base.timestamp(s,m),'o':10,'h':10.2,'l':9.9,'c':10,'v':100} for m in range(390)}} for s in calendar}
    daily={'A':{s['date']:{'c':10} for s in calendar}}
    candidate={'date':dates[10],'symbol':'A','candidate_id':dates[10]+'__A','market_gate_pass':True,
               'market_gate_status':'signal_and_entry_reference_ready',
               'opening30':{'exact_values':{'volume':'200','prior20_mean_adjusted_daily_volume':'100'}},
               'signal':{'minute_offset':30,'close':10,'close_exact':'10','planned_entry_offset':32},
               'entry_reference':{'minute_offset':32,'stop_exact':'8'}}
    coverage={'days':{s['date']:{'A':{'request_complete':True,'regular_window_covered':True,'request_ids':['fixture']}} for s in calendar}}
    return candidate,market,daily,calendar,coverage


class IndependentEventAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.template=fixture()
    def setUp(self):
        self.c,self.market,self.daily,self.calendar,self.coverage=deepcopy(self.template)
        self.day=self.c['date'];self.next=self.calendar[11]['date']
    def evidence(self,*coordinates):
        return {'rows':[{'symbol':symbol,'time':datetime.fromisoformat(base.timestamp(self.calendar[index],offset)).astimezone(timezone.utc).isoformat(),
                         'classification':event.ALLOWED_CLASSIFICATION,'point_id':f'fixture{index}-{offset}'} for symbol,index,offset in coordinates]}
    def case(self,mode='evidence_gated',gaps=None,variant=None,actions=None,cutoff=None,coverage=None):
        return event.simulate_case(self.c,variant or V,self.market,self.daily,self.calendar,actions or {},cutoff or self.calendar[-1]['date'],
                                   mode=mode,gap_evidence=gaps,verified_symbol_sessions=self.coverage if coverage is None else coverage)
    def assert_money(self,r):
        buys=sum((D(t['cash_amount']) for t in r['trades'] if t['side']=='BUY'),D(0))
        sales=sum((D(t['cash_amount']) for t in r['trades'] if t['side']=='SELL'),D(0))
        settled=sum((D(l['amount']) for l in r['ledger'] if l['type']=='settlement'),D(0))
        self.assertEqual(D(r['cash_settled']),500-buys+settled)
        self.assertEqual(sum((D(a['amount']) for a in r['terminal_receivables']),D(0)),sales-settled)
        self.assertEqual(r['funding_injections_after_initial'],'0');self.assertEqual(r['restart_count'],0)
        self.assertTrue(r['model_is_conditional_aggregate_event_sensitivity']);self.assertFalse(r['model_is_forward_or_independent_validation'])
    def other(self,symbol='B',offset=31,price='10'):
        c=deepcopy(self.c);c.update(symbol=symbol,candidate_id=self.day+'__'+symbol)
        c['signal'].update(minute_offset=offset,planned_entry_offset=offset+2,close_exact=price)
        c['entry_reference']['minute_offset']=offset+2
        for rows in self.market.values():rows[symbol]=deepcopy(rows['A'])
        self.daily[symbol]=deepcopy(self.daily['A'])
        return c

    def test_gap_free_records_match_v1_account_and_exits(self):
        old=base.simulate_case(self.c,V,self.market,self.daily,self.calendar,{})
        for mode in event.MODES:
            result=self.case(mode=mode)
            for key in ('status','cash_settled','ending_equity','profit','terminal_receivables','position','milestones','max_daily_liquidation_drawdown'):
                self.assertEqual(result[key],old[key],key)
            self.assertEqual([{k:t[k] for k in old['trades'][i]} for i,t in enumerate(result['trades'])],old['trades'])
            self.assertEqual(result['skipped_minute_count'],0);self.assert_money(result)

    def test_exact_evidence_gap_advances_without_price_or_quantity_change(self):
        del self.market[self.day]['A']['40']
        result=self.case(gaps=self.evidence(('A',10,40)),cutoff=self.day)
        self.assertEqual(result['status'],'censored_open_position')
        self.assertEqual(result['skipped_minute_count'],1)
        self.assertEqual(result['trades'][0]['quantity'],49)
        self.assertFalse(result['gap_spans'][0]['execution_price_created']);self.assert_money(result)

    def test_wrong_symbol_day_or_classification_does_not_authorize(self):
        del self.market[self.day]['A']['40']
        wrong=[self.evidence(('B',10,40)),self.evidence(('A',11,40)),self.evidence(('A',10,40))]
        wrong[2]['rows'][0]['classification']='target_missing_provider_returned_no_trades'
        for gaps in wrong:
            with self.subTest(gaps=gaps):self.assertEqual(self.case(gaps=gaps)['status'],'incomplete')

    def test_fractional_nanosecond_evidence_is_rejected(self):
        gaps=self.evidence(('A',10,40));gaps['rows'][0]['time']=gaps['rows'][0]['time'].replace('+00:00','.000000001Z')
        with self.assertRaises(ValueError):self.case(gaps=gaps)

    def test_unknown_next_gap_stops_before_later_stop_price(self):
        del self.market[self.day]['A']['40'];del self.market[self.day]['A']['41']
        self.market[self.day]['A']['42']['l']=1
        result=self.case(gaps=self.evidence(('A',10,40)))
        self.assertEqual(result['failure']['time'],base.timestamp(self.calendar[10],41))
        self.assertEqual(len(result['trades']),1);self.assertEqual(result['skipped_minute_count'],1)

    def test_provider_requires_both_boolean_source_coverage_flags(self):
        del self.market[self.day]['A']['40']
        for key in ('request_complete','regular_window_covered'):
            for value in (False,None,1):
                coverage=deepcopy(self.coverage);coverage['days'][self.day]['A'][key]=value
                with self.subTest(key=key,value=value):self.assertEqual(self.case(mode=PROVIDER,coverage=coverage)['status'],'incomplete')

    def test_provider_skip_then_lower_open_stop_uses_actual_lower_open(self):
        del self.market[self.day]['A']['40'];self.market[self.day]['A']['41']={'t':base.timestamp(self.calendar[10],41),'o':6}
        result=self.case(mode=PROVIDER)
        self.assertEqual(result['trades'][-1]['reference_price'],'6')
        self.assertEqual(result['trades'][-1]['reasons'],['gap_stop']);self.assert_money(result)

    def test_present_invalid_bar_is_never_absence(self):
        for value in (None,{}, {'t':base.timestamp(self.calendar[10],40),'o':0}):
            self.market[self.day]['A']['40']=value
            result=self.case(mode=PROVIDER,gaps=self.evidence(('A',10,40)))
            self.assertEqual(result['status'],'incomplete');self.assertEqual(result['skipped_minute_count'],0)

    def test_exact_entry_missing_is_not_gap_permission(self):
        del self.market[self.day]['A']['32']
        for mode in event.MODES:
            result=self.case(mode=mode,gaps=self.evidence(('A',10,32)))
            self.assertEqual(result['failure']['reason'],'missing_or_invalid_selected_entry')
            self.assertIsNotNone(result['pending_order']);self.assertEqual(result['trades'],[])

    def test_fixed10_exact_scheduled_exit_cannot_be_deferred(self):
        day=self.calendar[19]['date'];del self.market[day]['A']['389']
        for mode in event.MODES:
            result=self.case(mode=mode,gaps=self.evidence(('A',19,389)))
            self.assertEqual(result['failure']['reason'],'missing_required_execution_bar')
            self.assertEqual(result['failure']['time'],base.timestamp(self.calendar[19],389))
            self.assertEqual(len(result['trades']),1);self.assertIsNone(result['profit'])

    def test_max63_exact_exit_cannot_be_deferred(self):
        day=self.calendar[72]['date'];del self.market[day]['A']['389']
        result=self.case(mode=PROVIDER,variant={**V,'exit_mode':'ma10_max63'})
        self.assertEqual(result['failure']['reason'],'missing_required_execution_bar')
        self.assertEqual(result['failure']['execution_intents'],['max63'])

    def test_MA_exact_next_open_missing_stops_despite_later_available_price(self):
        self.daily['A'][self.day]['c']=9;del self.market[self.next]['A']['0']
        result=self.case(mode=PROVIDER,variant={**V,'exit_mode':'ma10_max63'})
        self.assertEqual(result['failure']['time'],base.timestamp(self.calendar[11],0))
        self.assertEqual(result['failure']['execution_intents'],['ma10_next_open']);self.assertEqual(len(result['trades']),1)

    def test_empty_held_session_is_incomplete_at_close(self):
        self.market[self.next]['A']={}
        result=self.case(mode=PROVIDER)
        self.assertEqual(result['failure']['reason'],'empty_held_session_unresolved')
        self.assertEqual(result['failure']['time'],base.timestamp(self.calendar[11],390))
        self.assertIsNotNone(result['position']);self.assertIsNone(result['profit'])

    def test_day_with_only_nonregular_key_is_not_valid_observed_session(self):
        self.market[self.next]['A']={'-1':{'t':base.timestamp(self.calendar[11],-1),'o':10,'h':10,'l':10,'c':10}}
        result=self.case(mode=PROVIDER,cutoff=self.next)
        self.assertEqual(result['status'],'incomplete')

    def test_action_checks_precede_empty_or_missing_held_data(self):
        self.market[self.next]['A']={}
        for kind in ('cash_dividend','forward_split','merger'):
            result=self.case(mode=PROVIDER,actions={'A':[{'type':kind,'ex_date':self.next}]})
            self.assertEqual(result['failure']['reason'],'unsupported_held_corporate_event')
            self.assertEqual(result['failure']['time'],base.timestamp(self.calendar[11],0))

    def test_stale_terminal_valuation_keeps_timestamp_and_excludes_milestones(self):
        del self.market[self.day]['A']['389'];self.market[self.day]['A']['388'].update(h=30,c=30)
        result=self.case(mode=PROVIDER,cutoff=self.day)
        row=result['daily'][-1]
        self.assertFalse(row['mark_fresh']);self.assertEqual(row['mark_stale_minutes'],1)
        self.assertEqual(row['mark_time'],base.timestamp(self.calendar[10],389))
        self.assertFalse(row['equity_certified']);self.assertFalse(row['drawdown_and_milestone_eligible'])
        self.assertIsNone(result['ending_equity']);self.assertIsNone(result['profit'])
        self.assertIsNone(result['milestones']['1000']);self.assertEqual(result['daily_drawdown_evaluated_snapshot_count'],0)
        self.assert_money(result)

    def test_fresh_mark_after_gap_does_not_remove_model_condition(self):
        del self.market[self.day]['A']['40'];result=self.case(mode=PROVIDER,cutoff=self.day)
        self.assertTrue(result['daily'][-1]['mark_fresh'])
        self.assertTrue(result['model_is_conditional_aggregate_event_sensitivity'])
        self.assertEqual(result['daily'][-1]['conditional_gap_minutes_to_date'],1)

    def test_cohort_selection_budget_does_not_replace_first_unaffordable_intent(self):
        b=self.other();self.c['signal']['close_exact']='1000'
        result=event.simulate_cohort_portfolio([b,self.c],V,self.market,self.daily,self.calendar,{},'market73',start_date=self.day,cutoff=self.day)
        self.assertEqual(result['trades'],[])
        selection=next(d for d in result['decisions'] if d['status']=='retrospective_frozen_membership_selection')
        self.assertEqual(selection['selected_candidate_id'],self.c['candidate_id'])
        self.assertTrue(result['retrospective_membership_condition']);self.assertFalse(result['historical_implementability_claim'])

    def test_T1_and_no_exit_day_reentry_remain_in_cohort(self):
        self.market[self.day]['A']['32']['l']=7
        b=self.other(offset=40);c2=deepcopy(self.c);c2.update(date=self.next,candidate_id=self.next+'__A')
        result=event.simulate_cohort_portfolio([b,c2,self.c],V,self.market,self.daily,self.calendar,{},'market73',start_date=self.day,cutoff=self.next)
        buys=[t for t in result['trades'] if t['side']=='BUY']
        self.assertEqual([t['quantity'] for t in buys],[49,39]);self.assertEqual([t['date'] for t in buys],[self.day,self.next])
        self.assert_money(result)

    def test_original_guard_does_not_turn_outside_category_into_news_absence(self):
        result=event.simulate_portfolio([self.c],V,self.market,self.daily,self.calendar,{},
            {self.c['candidate_id']:{'pass_registered_news_gate':False,'event_category':'outside_category'}},start_date=self.day,cutoff=self.day,mode=PROVIDER,verified_symbol_sessions=self.coverage)
        self.assertEqual(result['failure']['reason'],'unresolved_candidate_precedence');self.assertEqual(result['trades'],[])


if __name__=='__main__':unittest.main()
