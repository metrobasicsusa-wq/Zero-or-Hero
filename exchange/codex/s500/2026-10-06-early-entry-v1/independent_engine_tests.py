"""Independent technical-only selection and account tests, synthetic inputs only."""
import copy
import unittest
from decimal import Decimal as D

import early_engine as early
import ep_event_engine as parent
from ep_engine import timestamp

V = {'exit_mode': 'fixed10', 'fraction': 1, 'cost_bps': 25}
PROVIDER = 'provider_event_series_assumption'
DATES = ['2026-01-02','2026-01-05','2026-01-06','2026-01-07',
         '2026-01-08','2026-01-09','2026-01-12','2026-01-13',
         '2026-01-14','2026-01-15','2026-01-16','2026-01-20']


def fixture():
    cal = [{'date':d,'open':'09:30','close':'16:00','settlement_date':DATES[i+1]}
           for i,d in enumerate(DATES[:-1])]
    market = {s['date']:{sym:{str(m):{'t':timestamp(s,m),'o':10,'h':10.2,'l':9.9,'c':10,'v':100}
                            for m in range(390)} for sym in ['A','B']} for s in cal}
    c = {'date':DATES[0],'symbol':'A','candidate_id':DATES[0]+'__A','family':'OR5','opening_range_minutes':5,
         'market_gate_pass':True,'market_gate_status':'signal_and_entry_reference_ready',
         'opening_range':{'exact_values':{'volume':'200','prior20_mean_adjusted_daily_volume':'100'}},
         'signal':{'minute_offset':5,'close_exact':'10','planned_entry_offset':7},
         'entry_reference':{'minute_offset':7,'stop_exact':'8'}}
    coverage = {'days':{s['date']:{sym:{'request_complete':True,'regular_window_covered':True,'request_ids':['fixture']}
                                   for sym in ['A','B']} for s in cal}}
    return c,market,cal,coverage


class IndependentEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.original = fixture()
    def setUp(self): self.c,self.market,self.cal,self.verified = copy.deepcopy(self.original)
    def other(self, minute=6, symbol='B', day=None):
        c=copy.deepcopy(self.c); c.update(symbol=symbol,date=day or self.c['date'])
        c['candidate_id']=c['date']+'__'+symbol
        c['signal'].update(minute_offset=minute,planned_entry_offset=minute+2)
        c['entry_reference']['minute_offset']=minute+2
        return c
    def case(self, mode='evidence_gated', variant=None, cutoff=None, actions=None):
        return early.simulate_case(self.c,variant or V,self.market,{},self.cal,actions or {},
                                   cutoff or DATES[9],mode=mode,verified_symbol_sessions=self.verified)
    def portfolio(self, candidates, cutoff=None, mode='evidence_gated', coverage=None):
        return early.simulate_portfolio(candidates,V,self.market,{},self.cal,{},coverage=coverage,
                                        start_date=DATES[0],cutoff=cutoff or DATES[0],mode=mode,
                                        verified_symbol_sessions=self.verified,opening_range_minutes=5)
    def assert_account(self,r):
        buys=sum((D(t['cash_amount']) for t in r['trades'] if t['side']=='BUY'),D(0))
        sales=sum((D(t['cash_amount']) for t in r['trades'] if t['side']=='SELL'),D(0))
        settled=sum((D(l['amount']) for l in r['ledger'] if l['type']=='settlement'),D(0))
        self.assertEqual(D(r['cash_settled']),D(500)-buys+settled)
        self.assertEqual(sum((D(v['amount']) for v in r['terminal_receivables']),D(0)),sales-settled)
        self.assertEqual(r['funding_injections_after_initial'],'0')
        self.assertFalse(r['news_selection_used'])

    def test_all_financial_variants_exact_cost_and_fixed10(self):
        for n in [5,15,30]:
            for fraction in [0.5,1]:
                for bps in [25,100]:
                    self.c.update(family='OR'+str(n),opening_range_minutes=n)
                    self.c['signal'].update(minute_offset=n,planned_entry_offset=n+2)
                    self.c['entry_reference']['minute_offset']=n+2
                    v={**V,'fraction':fraction,'cost_bps':bps};r=self.case(variant=v)
                    q=int(D(500)*D(str(fraction))/(D(10)*(1+D(bps)/10000)))
                    self.assertEqual(r['trades'][0]['quantity'],q)
                    self.assertEqual(r['trades'][-1]['date'],DATES[9])
                    self.assertEqual(r['trades'][-1]['time'],timestamp(self.cal[9],389))
                    self.assertEqual(D(r['profit']),-D(q)*10*D(bps)/10000*2)
                    self.assert_account(r)

    def test_or30_financial_fields_match_parent_both_modes(self):
        self.c.update(family='OR30',opening_range_minutes=30)
        self.c['signal'].update(minute_offset=30,planned_entry_offset=32)
        self.c['entry_reference']['minute_offset']=32
        old=copy.deepcopy(self.c);old['opening30']=old.pop('opening_range')
        for mode in ['evidence_gated',PROVIDER]:
            a=self.case(mode=mode);b=parent.simulate_case(old,V,self.market,{},self.cal,{},cutoff=DATES[9],mode=mode,verified_symbol_sessions=self.verified)
            for key in ['status','cash_settled','terminal_receivables','ending_equity','profit','trades','daily','milestones']:
                self.assertEqual(a[key],b[key],key)

    def test_no_budget_resize_or_alternative_after_entry_gap(self):
        self.market[DATES[0]]['A']['7']['o']=11
        r=self.portfolio([self.other(),self.c])
        self.assertEqual(r['trades'],[])
        self.assertEqual(r['decisions'][0]['intended_quantity'],49)
        self.assertEqual(r['decisions'][0]['status'],'skipped_next_open_unaffordable')

    def test_zero_quantity_does_not_replace_selected_candidate(self):
        self.c['signal']['close_exact']='1000'
        r=self.portfolio([self.c,self.other()]);self.assertEqual(r['trades'],[])
        self.assertEqual(r['decisions'][0]['status'],'skipped_unaffordable_intent')

    def test_news_never_filters_or_ranks_technical_portfolio(self):
        self.c['news_status']='missing';b=self.other();b['news_status']='verified';b['news_gate']=True
        r=self.portfolio([b,self.c]);self.assertEqual(r['trades'][0]['symbol'],'A')
        self.assertFalse(r['news_selection_used'])

    def test_same_signal_uses_own_known_volume_then_symbol(self):
        b=self.other(5);b['opening_range']['exact_values']['volume']='300'
        self.assertEqual(self.portfolio([self.c,b])['trades'][0]['symbol'],'B')
        b['opening_range']['exact_values']['volume']='200'
        self.assertEqual(self.portfolio([b,self.c])['trades'][0]['symbol'],'A')

    def test_earliest_signal_precedes_higher_later_volume(self):
        b=self.other(6);b['opening_range']['exact_values']['volume']='9999999'
        self.assertEqual(self.portfolio([b,self.c])['trades'][0]['symbol'],'A')

    def test_unknown_earlier_intent_blocks(self):
        b=self.other(5);self.c['signal'].update(minute_offset=6,planned_entry_offset=8)
        self.c['entry_reference']['minute_offset']=8
        b.update(market_gate_pass=None,market_gate_status='entry_reference_unknown')
        r=self.portfolio([self.c,b]);self.assertEqual(r['failure']['reason'],'unresolved_candidate_precedence')
        self.assertEqual(r['trades'],[])

    def test_later_known_unknown_intent_does_not_retract_earlier(self):
        b=self.other(8);b.update(market_gate_pass=None,market_gate_status='entry_reference_unknown')
        self.assertEqual(self.portfolio([self.c,b])['trades'][0]['symbol'],'A')

    def test_signalless_unknown_is_conservative_blocker(self):
        b=self.other();b.update(market_gate_pass=None,market_gate_status='signal_data_unknown',signal=None)
        r=self.portfolio([self.c,b]);self.assertEqual(r['failure']['reason'],'unresolved_candidate_precedence')

    def test_full_universe_guard_kept(self):
        cov={'days':{DATES[0]:{'initial_screen_complete':False}}}
        r=self.portfolio([self.c],coverage=cov)
        self.assertEqual(r['failure']['reason'],'initial_screen_coverage_unknown');self.assertIsNone(r['profit'])

    def test_failed_gate_is_excluded_without_news_reclassification(self):
        b=self.other();b.update(market_gate_pass=False,market_gate_status='opening_range_volume_gate_failed',news_status='unknown')
        self.assertEqual(self.portfolio([self.c,b])['trades'][0]['symbol'],'A')

    def test_no_entry_on_exit_day_and_receivables_settle_next_session(self):
        self.market[DATES[1]]['A']['0']={'t':timestamp(self.cal[1],0),'o':7}
        b1=self.other(day=DATES[1]);b2=self.other(day=DATES[2])
        r=self.portfolio([self.c,b1,b2],cutoff=DATES[2])
        buys=[t for t in r['trades'] if t['side']=='BUY']
        self.assertEqual([t['date'] for t in buys],[DATES[0],DATES[2]])
        self.assertEqual(r['daily'][1]['cash_settled'],'8.7750')
        self.assertEqual(r['daily'][1]['cash_unsettled'],'342.1425')
        self.assertEqual(r['daily'][2]['cash_unsettled'],'0')
        self.assert_account(r)

    def test_missing_exact_entry_never_provider_gap(self):
        del self.market[DATES[0]]['A']['7'];r=self.case(mode=PROVIDER)
        self.assertEqual(r['failure']['reason'],'missing_or_invalid_selected_entry')
        self.assertEqual(r['trades'],[]);self.assertIsNotNone(r['pending_order'])

    def test_missing_fixed_exit_never_deferred(self):
        del self.market[DATES[9]]['A']['389'];r=self.case(mode=PROVIDER)
        self.assertEqual(r['failure']['reason'],'missing_required_execution_bar');self.assertIsNone(r['profit'])

    def test_provider_absence_needs_full_coverage_and_stale_is_not_equity(self):
        del self.market[DATES[0]]['A']['389'];r=self.case(mode=PROVIDER,cutoff=DATES[0])
        self.assertEqual(r['skipped_minute_count'],1);self.assertIsNone(r['ending_equity'])
        self.assertFalse(r['daily'][0]['mark_fresh']);self.assertIsNone(r['profit'])
        self.verified['days'][DATES[0]]['A']['regular_window_covered']=False
        self.assertEqual(self.case(mode=PROVIDER,cutoff=DATES[0])['status'],'incomplete')

    def test_unsupported_action_before_skipping_missing_held_session(self):
        self.market[DATES[1]]['A']={}
        r=self.case(mode=PROVIDER,actions={'A':[{'type':'cash_dividend','ex_date':DATES[1]}]})
        self.assertEqual(r['failure']['reason'],'unsupported_held_corporate_event')

    def test_duplicate_family_registry_is_rejected(self):
        with self.assertRaises(ValueError):self.portfolio([self.c,copy.deepcopy(self.c)])

    def test_retrospective_cohort_cannot_smuggle_unknown_gate(self):
        self.c.update(market_gate_pass=None,market_gate_status='signal_data_unknown')
        with self.assertRaises(ValueError):
            early.simulate_cohort_portfolio([self.c],V,self.market,{},self.cal,{},opening_range_minutes=5)

    def test_source_revision_conflict_stays_selected_and_unresolved(self):
        self.c['source_revision_conflict']=True
        self.c['source_revision_conflict_details']={'reason':'fixture_different_prefix_version'}
        r=self.case()
        self.assertEqual(r['status'],'incomplete')
        self.assertEqual(r['trades'],[])
        self.assertIsNone(r['profit'])
        self.assertIn('source_revision_conflict',r['failure']['reason'])
        self.assertEqual(r['failure']['time'],timestamp(self.cal[0],7))

    def test_later_ranked_prefix_conflict_is_unknown_precedence(self):
        b=self.other(8);b['source_revision_conflict']=True
        b['source_revision_conflict_details']={'reason':'unknown_earlier_signal_on_revised_prefix'}
        r=self.portfolio([self.c,b])
        self.assertEqual(r['status'],'incomplete')
        self.assertEqual(r['trades'],[])
        self.assertEqual(r['failure']['reason'],'unresolved_candidate_precedence')
        self.assertEqual(r['failure']['source_conflict_blockers'],[b['candidate_id']])

    def test_future_input_request_gap_does_not_erase_earlier_completed_path(self):
        self.c['source_input_incomplete']=True
        self.market[DATES[0]]['A']['8']['l']=7
        self.market[DATES[1]]['A']={}
        self.verified['days'][DATES[1]]['A']['request_complete']=False
        r=self.case(mode=PROVIDER)
        self.assertEqual(r['status'],'complete')
        self.assertEqual(r['trades'][-1]['date'],DATES[0])
        self.assert_account(r)


if __name__=='__main__':unittest.main(verbosity=2)
