import unittest,copy
from datetime import date,timedelta
from decimal import Decimal as D
from ep_engine import simulate_case,simulate_portfolio,timestamp

V={'exit_mode':'fixed10','fraction':1,'cost_bps':25}

def fixture(n=75):
    dates=[]; d=date(2025,12,15)
    while len(dates)<n+10:
        if d.weekday()<5: dates.append(d.isoformat())
        d+=timedelta(days=1)
    cal=[{'date':d,'open':'09:30','close':'16:00','settlement_date':dates[i+1]} for i,d in enumerate(dates[:-1])]
    market={};daily={'AAA':{}}
    for s in cal:
        day=s['date'];market[day]={'AAA':{str(m):{'t':timestamp(s,m),'o':10,'h':10.1,'l':9.9,'c':10,'v':100} for m in range(390)}}
        daily['AAA'][day]={'c':10}
    c={'candidate_id':dates[10]+'__AAA','date':dates[10],'symbol':'AAA','market_gate_status':'signal_and_entry_reference_ready','market_gate_pass':True,'opening30':{'volume_ratio':2},'signal':{'minute_offset':30,'close':10,'close_exact':'10','planned_entry_offset':32},'entry_reference':{'minute_offset':32,'stop_or30_low':8,'stop_exact':'8'}}
    return c,market,daily,cal

class EPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.base=fixture()
    def setUp(self):self.c,self.m,self.d,self.cal=copy.deepcopy(self.base);self.idx=10;self.day=self.c['date'];self.next=self.cal[11]['date'];self.cut=self.cal[-1]['date']
    def run_case(self,v=None,actions=None,cutoff=None):return simulate_case(self.c,v or V,self.m,self.d,self.cal,actions or {},cutoff or self.cut)
    def test_fixed10_known_cash(self):
        r=self.run_case();self.assertEqual(r['status'],'complete');self.assertEqual(r['trades'][0]['quantity'],49);self.assertEqual(D(r['ending_equity']),D('497.550'));self.assertEqual(r['trades'][-1]['date'],self.cal[19]['date']);self.assertIn('15:59:00',r['trades'][-1]['time']);self.assertEqual(len(r['daily']),10)
    def test_half_budget_quantity(self):
        r=self.run_case({**V,'fraction':'.5'});self.assertEqual(r['trades'][0]['quantity'],24);self.assertEqual(D(r['ending_equity']),D('498.8'))
    def test_quantity_fixed_at_intent_no_resize(self):
        self.m[self.day]['AAA']['32']['o']=10.3;r=self.run_case();self.assertEqual(r['trades'],[]);self.assertEqual(r['decisions'][0]['status'],'skipped_next_open_unaffordable');self.assertEqual(D(r['ending_equity']),500)
    def test_zero_quantity_consumes_day(self):
        self.c['signal']['close_exact']='1000';r=self.run_case();self.assertEqual(r['decisions'][0]['status'],'skipped_unaffordable_intent');self.assertEqual(r['trades'],[])
    def test_gap_through_entry_ambiguous(self):
        self.m[self.day]['AAA']['32']['o']=8;r=self.run_case();self.assertEqual(r['status'],'incomplete');self.assertIsNone(r['ending_equity']);self.assertIsNotNone(r['pending_order']);self.assertEqual(r['trades'],[])
    def test_entry_minute_stop(self):
        self.m[self.day]['AAA']['32']['l']=7;r=self.run_case();self.assertEqual(len(r['trades']),2);self.assertEqual(D(r['ending_equity']),D('399.795'));self.assertEqual(D(r['terminal_receivables'][0]['amount']),D('391.02'))
    def test_next_day_gap_stop_open_only(self):
        self.m[self.next]['AAA']['0']={'t':timestamp(self.cal[11]),'o':7};r=self.run_case();self.assertEqual(r['status'],'complete');self.assertEqual(r['trades'][-1]['reference_price'],'7');self.assertEqual(r['trades'][-1]['reasons'],['gap_stop'])
    def test_missing_entry_preserves_order(self):
        del self.m[self.day]['AAA']['32'];r=self.run_case();self.assertIsNotNone(r['pending_order']);self.assertEqual(D(r['cash_settled']),500)
    def test_missing_held_keeps_cash_and_position(self):
        del self.m[self.day]['AAA']['40'];r=self.run_case();self.assertEqual(r['status'],'incomplete');self.assertEqual(r['position']['quantity'],49);self.assertEqual(D(r['cash_settled']),D('8.775'));self.assertIsNone(r['profit'])
    def test_first_gap_cannot_skip_to_later_stop(self):
        del self.m[self.day]['AAA']['40'];self.m[self.day]['AAA']['41']['l']=1;r=self.run_case();self.assertEqual(len(r['trades']),1);self.assertIn('10:10:00',r['failure']['time'])
    def test_wrong_source_date_blocks(self):
        self.m[self.day]['AAA']['40']['t']=timestamp(self.cal[11],40);self.assertEqual(self.run_case()['status'],'incomplete')
    def test_submicrosecond_timestamp_rejected(self):
        self.m[self.day]['AAA']['40']['t']=timestamp(self.cal[10],40).replace('-05:00','.000000001-05:00');self.assertEqual(self.run_case()['status'],'incomplete')
    def test_fixed_exit_only_open_required(self):
        s=self.cal[19];self.m[s['date']]['AAA']['389']={'t':timestamp(s,389),'o':12};r=self.run_case();self.assertEqual(r['status'],'complete');self.assertEqual(r['trades'][-1]['reference_price'],'12')
    def test_early_close_final_minute(self):
        s=self.cal[19];s['close']='13:00';r=self.run_case();self.assertIn('12:59:00',r['trades'][-1]['time'])
    def test_exit_future_missing_bars_irrelevant(self):
        self.m[self.day]['AAA']['32']['l']=7;self.m[self.next]['AAA']={};r=self.run_case();self.assertEqual(r['status'],'complete')
    def test_future_corporate_action_does_not_filter(self):
        a={'AAA':[{'type':'cash_dividend','ex_date':self.cal[25]['date']}]};self.assertEqual(self.run_case(actions=a)['status'],'complete')
    def test_held_dividend_unknown_currency_stops(self):
        a={'AAA':[{'type':'cash_dividend','ex_date':self.next,'cash_rate':1,'currency':None}]};r=self.run_case(actions=a);self.assertEqual(r['status'],'incomplete');self.assertEqual(len(r['trades']),1);self.assertEqual(r['position']['quantity'],49)
    def test_entry_day_dividend_no_overnight_entitlement(self):
        a={'AAA':[{'type':'cash_dividend','ex_date':self.day,'cash_rate':1}]};self.assertEqual(self.run_case(actions=a)['status'],'complete')
    def test_held_split_stops_no_fake_share_adjustment(self):
        a={'AAA':[{'type':'forward_split','ex_date':self.next,'old_rate':1,'new_rate':2}]};r=self.run_case(actions=a);self.assertEqual(r['position']['quantity'],49);self.assertIsNone(r['ending_equity'])
    def test_ma_strict_equal_does_not_sell(self):
        r=self.run_case({**V,'exit_mode':'ma10_max63'},cutoff=self.next);self.assertEqual(r['status'],'censored_open_position');self.assertEqual(len(r['trades']),1)
    def test_ma_close_decision_next_open(self):
        self.d['AAA'][self.day]['c']=9;self.m[self.next]['AAA']['0']={'t':timestamp(self.cal[11]),'o':11};r=self.run_case({**V,'exit_mode':'ma10_max63'});self.assertEqual(r['trades'][-1]['date'],self.next);self.assertEqual(r['trades'][-1]['reasons'],['ma10_next_open'])
    def test_ma_next_open_gap_combined_once(self):
        self.d['AAA'][self.day]['c']=9;self.m[self.next]['AAA']['0']={'t':timestamp(self.cal[11]),'o':7};r=self.run_case({**V,'exit_mode':'ma10_max63'});self.assertEqual(len(r['trades']),2);self.assertEqual(set(r['trades'][-1]['reasons']),{'ma10_next_open','gap_stop'})
    def test_ma_uses_prior_nine_not_future(self):
        self.d['AAA'][self.next]['c']=.01;r=self.run_case({**V,'exit_mode':'ma10_max63'},cutoff=self.day);self.assertEqual(len(r['trades']),1);self.assertEqual(r['ledger'][-1]['decision'],'hold')
    def test_ma_warmup_missing_incomplete(self):
        del self.d['AAA'][self.cal[2]['date']];r=self.run_case({**V,'exit_mode':'ma10_max63'});self.assertEqual(r['failure']['reason'],'missing_or_invalid_ma_daily_close')
    def test_ma_warmup_split_unadjusted_incomplete(self):
        a={'AAA':[{'type':'forward_split','ex_date':self.cal[8]['date']}]};r=self.run_case({**V,'exit_mode':'ma10_max63'},a);self.assertEqual(r['failure']['reason'],'ma_history_requires_verified_split_adjustment')
    def test_ma_max63(self):
        r=self.run_case({**V,'exit_mode':'ma10_max63'});self.assertEqual(r['trades'][-1]['date'],self.cal[72]['date']);self.assertEqual(r['trades'][-1]['reasons'],['max63'])
    def test_cutoff_is_censored_not_forced_sale(self):
        r=self.run_case(cutoff=self.next);self.assertEqual(r['status'],'censored_open_position');self.assertEqual(len(r['trades']),1);self.assertIsNone(r['profit']);self.assertIsNotNone(r['ending_equity'])
    def test_ledger_identity_every_daily(self):
        r=self.run_case()
        for row in r['daily']:self.assertEqual(D(row['last_observed_equity']),sum(D(row[k]) for k in ['cash_settled','cash_unsettled','marked_position']))
    def port(self,cs,ev,coverage=None,cutoff=None):return simulate_portfolio(cs,V,self.m,self.d,self.cal,{},ev,coverage,start_date=self.day,cutoff=cutoff or self.cal[21]['date'])
    def test_news_false_story_still_unknown(self):
        r=self.port([self.c],{self.c['candidate_id']:{'pass_registered_news_gate':False}});self.assertEqual(r['failure']['reason'],'unresolved_candidate_precedence');self.assertEqual(r['trades'],[])
    def test_unknown_later_intent_does_not_block_earlier(self):
        u=copy.deepcopy(self.c);u['symbol']='ZZZ';u['candidate_id']=self.day+'__ZZZ';u['signal']['minute_offset']=40;r=self.port([self.c,u],{self.c['candidate_id']:{'pass_registered_news_gate':True}},cutoff=self.day);self.assertEqual(len(r['trades']),1)
    def test_unknown_earlier_blocks(self):
        u=copy.deepcopy(self.c);u['symbol']='ZZZ';u['candidate_id']=self.day+'__ZZZ';u['signal']['minute_offset']=29;r=self.port([self.c,u],{self.c['candidate_id']:{'pass_registered_news_gate':True}});self.assertEqual(r['trades'],[]);self.assertIn(u['candidate_id'],r['failure']['blocking_candidates'])
    def test_missing_signal_unknown_cannot_ignore(self):
        u=copy.deepcopy(self.c);u['symbol']='ZZZ';u['candidate_id']=self.day+'__ZZZ';u['signal']=None;u['market_gate_status']='signal_data_unknown';r=self.port([self.c,u],{self.c['candidate_id']:{'pass_registered_news_gate':True}});self.assertEqual(r['trades'],[])
    def test_failed_market_gate_does_not_block(self):
        c=copy.deepcopy(self.c);c['market_gate_status']='opening30_volume_gate_failed';c['signal']=None;r=self.port([c],{},cutoff=self.day);self.assertEqual(r['status'],'complete')
    def test_initial_screen_gap_strict_stop(self):
        r=self.port([self.c],{self.c['candidate_id']:{'pass_registered_news_gate':True}},{'days':{self.day:{'initial_screen_complete':False}}});self.assertEqual(r['failure']['reason'],'initial_screen_coverage_unknown')
    def test_no_exit_day_reentry(self):
        second=copy.deepcopy(self.c);second['date']=self.cal[19]['date'];second['candidate_id']=second['date']+'__AAA';r=self.port([self.c,second],{c['candidate_id']:{'pass_registered_news_gate':True} for c in [self.c,second]});self.assertEqual(len(r['trades']),2)
    def test_receivable_unspendable_until_calendar_date(self):
        self.cal[10]['settlement_date']=self.cal[12]['date'];self.m[self.day]['AAA']['32']['l']=7
        second=copy.deepcopy(self.c);second['date']=self.next;second['candidate_id']=self.next+'__AAA'
        r=self.port([self.c,second],{c['candidate_id']:{'pass_registered_news_gate':True} for c in [self.c,second]},cutoff=self.cal[12]['date']);self.assertEqual(len(r['trades']),2);self.assertEqual([x['status'] for x in r['decisions'] if x.get('candidate_id')==second['candidate_id']],['skipped_unaffordable_intent']);self.assertEqual(r['terminal_receivables'],[])

if __name__=='__main__':unittest.main()
