"""Independent offline adversarial checks for ORB; writes only its audit JSON.
No network, credentials, orders, scheduler or mutation of production inputs.
"""
from collections import defaultdict
import copy
from datetime import datetime
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import random
from zoneinfo import ZoneInfo

ROOT=Path(__file__).parent
NY=ZoneInfo('America/New_York')

def load_module(name,filename):
    spec=importlib.util.spec_from_file_location(name,ROOT/filename)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def bar(o=10.,c=None,h=None,l=None,v=100.):
    c=o if c is None else c
    return {'o':o,'c':c,'h':max(o,c) if h is None else h,'l':min(o,c) if l is None else l,'v':v}

def fixture(symbols=('A',),days=('2026-01-02',),delayed=False):
    ranked={};market={};cal=[];cov={}
    for i,d in enumerate(days):
        settlement=['2026-01-05','2026-01-06','2026-01-07'][i]
        if delayed and i==0:settlement='2026-01-06'
        cal.append({'date':d,'open':'09:30','close':'16:00','settlement_date':settlement})
        ranked[d]=[];market[d]={};cov[d]={'ranking_complete':True,'ranking_missing':[]}
        for n,s in enumerate(symbols):
            rv=3.-n*.5;vol=500.
            ranked[d].append({'symbol':s,'rank':n+1,'rv':rv,'relative_volume':rv,'opening_volume':vol,'mean_opening_volume14':vol/rv,'or_open':10.,'or_close':10.05,'or_high':10.1,'or_low':9.95,'atr14':1.})
            bars={m:bar(10.2) for m in range(390)}
            for m in range(5):bars[m]=bar(10.,c=10.05,h=10.1,l=9.95)
            bars[5]=bar(10.05,c=10.2,h=10.25,l=10.05)
            bars[389]=bar(10.6)
            market[d][s]=bars
    return ranked,cal,market,cov

def run(engine,args,fraction=1.,bps=25,**extra):
    ranked,cal,market,cov=args
    return engine.run_path(ranked,cal,market,fraction,bps,coverage=cov,**extra)

def assertclose(a,b):
    assert math.isclose(a,b,rel_tol=1e-10,abs_tol=1e-8),(a,b)

def reconcile(p):
    """Rebuild cash using only entry debits, sale credits and settlement dates."""
    cash=500.;unsettled=[]
    for d in p['daily']:
        date=d['date'];cash+=sum(r['amount'] for r in unsettled if r['date']<=date)
        unsettled=[r for r in unsettled if r['date']>date]
        assertclose(cash,d['settled_cash_before_signals'])
        for e in p['entries']:
            if e['entry_date']!=date:continue
            assert e['qty']==int(e['qty']) and e['qty']>0
            budget=cash*p['parameters']['capital_fraction']
            assert e['entry_debit']<=budget+1e-8
            assertclose(e['entry_debit'],e['qty']*e['entry_price']+e['entry_cost'])
            cash-=e['entry_debit'];assert cash>=-1e-8
        for t in p['trades']:
            if t['entry_date']!=date:continue
            assertclose(t['exit_credit'],t['qty']*t['exit_price']-t['exit_cost'])
            assertclose(t['pnl'],t['exit_credit']-t['entry_debit'])
            unsettled.append({'date':t['settlement_date'],'amount':t['exit_credit']})
        assertclose(cash,d['settled_cash']);assertclose(sum(r['amount'] for r in unsettled),d['unsettled_cash'])
        if d['equity'] is not None:assertclose(d['equity'],cash+sum(r['amount'] for r in unsettled))
    assertclose(cash,p['settled_cash']);assertclose(sum(r['amount'] for r in unsettled),p['unsettled_cash'])
    if p['final_equity'] is not None:
        assertclose(p['final_equity'],500+sum(t['pnl'] for t in p['trades']))
    else:assert p['status']=='incomplete'


def raw_pages(folder):
    m=json.loads((folder/'complete.json').read_text());rows=defaultdict(list)
    for page in m['pages']:
        payload=(folder/page['name']).read_bytes()
        assert hashlib.sha256(payload).hexdigest()==page['sha256']
        for s,bars in json.loads(payload)['response'].get('bars',{}).items():rows[s]+=bars
    return rows

def actual_first5(bars):
    if isinstance(bars,dict):sample=[bars.get(m,bars.get(str(m))) for m in range(5)]
    else:
        sample=sorted([b for b in bars if datetime.fromisoformat(b['t'].replace('Z','+00:00')).astimezone(NY).strftime('%H:%M') in ['09:30','09:31','09:32','09:33','09:34']],key=lambda b:b['t'])
    if len(sample)!=5 or any(b is None for b in sample):return None
    return {'or_open':sample[0]['o'],'or_close':sample[-1]['c'],'or_high':max(b['h'] for b in sample),'or_low':min(b['l'] for b in sample),'opening_volume':sum(b['v'] for b in sample)}

def main():
    engine=load_module('orb_external_engine','engine.py');checks=[];observations=[]
    def check(name,fn):
        try:
            details=fn();checks.append({'name':name,'passed':True,'details':details})
        except Exception as e:checks.append({'name':name,'passed':False,'error':type(e).__name__+': '+str(e)})
    def volume_mismatch():
        args=fixture();args[2]['2026-01-02']['A'][0]['v']*=10
        p=run(engine,args)
        assert p['status']=='incomplete' and not p['entries'],{'actual_status':p['status'],'selected':[e['symbol'] for e in p['entries']],'expected':'incomplete_before_selection'}
        return p['incomplete']
    check('first5_volume_mismatch_must_block_selection',volume_mismatch)
    def rv_mismatch():
        args=fixture(('A','B'));args[0]['2026-01-02'][1]['rv']=100.
        p=run(engine,args)
        assert p['status']=='incomplete' and not p['entries'],{'actual_status':p['status'],'selected':[e['symbol'] for e in p['entries']],'expected':'incomplete_before_selection'}
        return p['incomplete']
    check('RV_must_agree_with_actual_volume_over_prior_mean',rv_mismatch)
    def ohlc_mismatch():
        args=fixture();args[2]['2026-01-02']['A'][1]['h']=10.3
        p=run(engine,args);assert p['status']=='incomplete' and not p['entries'];return p['incomplete']
    check('first5_price_mismatch_blocks_selection',ohlc_mismatch)
    def settlement():
        args=fixture(days=('2026-01-02','2026-01-05','2026-01-06'),delayed=True);p=run(engine,args);reconcile(p)
        assert [e['entry_date'] for e in p['entries']]==['2026-01-02','2026-01-06']
        return {'entries':len(p['entries']),'funding_reconciliation':'passed','unsettled_sale_principal_not_reused':True}
    check('fully_settled_cash_only_delayed_next_session',settlement)
    def halfcash():
        args=fixture(days=('2026-01-02','2026-01-05','2026-01-06'),delayed=True)
        p=run(engine,args,.5,100);reconcile(p)
        assert len(p['entries'])==3
        assert p['entries'][1]['entry_debit']<=p['daily'][1]['settled_cash_before_signals']*.5
        return {'leftover_settled_cash_may_trade':True,'unsettled_proceeds_not_reused':True}
    check('half_allocation_uses_remaining_settled_cash',halfcash)
    def terminal_exit():
        args=fixture();p=run(engine,args);changed=copy.deepcopy(args)
        changed[2]['2026-01-02']['A'][389].update(h=999.,l=.001,c=800.,v=9999999.)
        q=run(engine,changed);assert p['trades']==q['trades'];assertclose(p['final_equity'],q['final_equity'])
        assert q['trades'][0]['exit_offset']==389;return {'future_final_minute_HLCV_cannot_affect_scheduled_open_exit':True}
    check('scheduled_exit_precedes_final_minute_extremes',terminal_exit)
    def future():
        args=fixture(('A','B'));p=run(engine,args);changed=copy.deepcopy(args)
        for s in ('A','B'):
            for m in range(8,390):changed[2]['2026-01-02'][s][m]=bar(5000 if s=='B' else 1)
        q=run(engine,changed);assert p['signals']==q['signals'] and p['entries']==q['entries'];return {'entry_and_rank_unchanged':True}
    check('later_outcomes_cannot_reorder_signal_or_entry',future)
    def missing_held():
        args=fixture();del args[2]['2026-01-02']['A'][8];p=run(engine,args);reconcile(p)
        assert p['final_equity'] is None and p['daily'][-1]['equity'] is None and p['open_position']
        assert p['settled_cash']<500 and p['trades']==[]
        e=p['open_position'];assertclose(p['last_observed_equity'],p['settled_cash']+e['qty']*e['last_observed_price']*.9975)
        return {'terminal_unknown_not_fabricated':True,'cash_debit_and_open_position_retained':True}
    check('held_gap_keeps_unknown_final_and_last_known_mark',missing_held)
    def missing_exit():
        args=fixture();del args[2]['2026-01-02']['A'][389];p=run(engine,args);reconcile(p)
        assert p['final_equity'] is None and p['open_position'] and p['trades']==[];return p['incomplete']
    check('scheduled_exit_missing_does_not_invent_sale',missing_exit)
    def future_hole():
        args=fixture(('A','B'));p=run(engine,args);del args[2]['2026-01-02']['B'][8]
        q=run(engine,args);assert p['entries']==q['entries'] and p['trades']==q['trades'];return {'unselected_future_gap_no_lookahead':True}
    check('unselected_future_gap_does_not_change_trade',future_hole)
    def predecessor_hole():
        args=fixture(('A','B'));del args[2]['2026-01-02']['B'][5];p=run(engine,args)
        assert p['status']=='incomplete' and not p['entries'];return p['incomplete']
    check('competing_preselection_gap_does_not_choose_observed_winner',predecessor_hole)
    def guarded_ranking_skip():
        args=fixture(days=('2026-01-02','2026-01-05'));args[3]['2026-01-02']['ranking_complete']=False
        p=run(engine,args,pre_entry_gap_policy='skip_session');reconcile(p)
        assert p['daily'][0]['status']=='skipped_data_gap' and p['daily'][0]['entries']==0
        assert [e['entry_date'] for e in p['entries']]==['2026-01-05']
        assert p['aborted_sessions'][0]['observed_at']=='2026-01-02T09:35:00-05:00'
        return {'whole_session_cancelled_before_fill':True,'next_day_allowed':True}
    check('guarded_preopen_ranking_gap_cancels_whole_session',guarded_ranking_skip)
    def guarded_entry_open():
        args=fixture(('A','B'),days=('2026-01-02','2026-01-05'));del args[2]['2026-01-02']['A'][7]
        p=run(engine,args,pre_entry_gap_policy='skip_session');reconcile(p)
        assert p['daily'][0]['status']=='skipped_data_gap' and p['daily'][0]['entries']==0
        assert p['aborted_sessions'][0]['cancelled_pending_entry']['symbol']=='A'
        assert p['daily'][0]['settled_cash']==500 and p['daily'][0]['equity']==500
        assert [e['entry_date'] for e in p['entries']]==['2026-01-05']
        return {'no_runner_up_on_entry_gap':True,'pending_intent_cancelled_in_log':True}
    check('guarded_missing_selected_open_no_replacement',guarded_entry_open)
    def guarded_bad_entry_candle():
        args=fixture();del args[2]['2026-01-02']['A'][7]['l']
        p=run(engine,args,pre_entry_gap_policy='skip_session');reconcile(p)
        assert p['status']=='incomplete' and p['final_equity'] is None and p['open_position']
        assert len(p['entries'])==1 and p['aborted_sessions']==[]
        return {'known_entry_open_is_filled_before_missing_held_fields':True,'no_retroactive_skip':True}
    check('guarded_valid_open_but_unknown_entry_candle_stays_incomplete',guarded_bad_entry_candle)
    def guarded_held_gap():
        args=fixture();del args[2]['2026-01-02']['A'][8]
        strict=run(engine,args);p=run(engine,args,pre_entry_gap_policy='skip_session');reconcile(p)
        assert p['status']=='incomplete' and p['open_position'] and p['aborted_sessions']==[]
        assert p['entries']==strict['entries'] and p['settled_cash']==strict['settled_cash']
        return {'held_data_gap_never_skip':True}
    check('guarded_held_gap_never_discards_unknown_outcome',guarded_held_gap)
    def guarded_later_gap():
        args=fixture();args[2]['2026-01-02']['A'][7]=bar(10.2,l=10.)
        a=run(engine,args,pre_entry_gap_policy='skip_session');del args[2]['2026-01-02']['A'][100]
        b=run(engine,args,pre_entry_gap_policy='skip_session')
        assert a['trades']==b['trades'] and a['aborted_sessions']==b['aborted_sessions']==[]
        return {'data_missing_after_closed_trade_cannot_cancel_it':True}
    check('guarded_future_gap_after_exit_no_hindsight_cancel',guarded_later_gap)
    def guarded_competitor_future_gap():
        args=fixture(('A','B'));a=run(engine,args,pre_entry_gap_policy='skip_session')
        del args[2]['2026-01-02']['B'][100];b=run(engine,args,pre_entry_gap_policy='skip_session')
        assert a['trades']==b['trades'] and b['aborted_sessions']==[]
        return {'unselected_later_gap_no_skip':True}
    check('guarded_unselected_future_gap_no_hindsight_skip',guarded_competitor_future_gap)
    def guarded_competitor_current_gap():
        args=fixture(('A','B'));del args[2]['2026-01-02']['B'][5]
        p=run(engine,args,pre_entry_gap_policy='skip_session');reconcile(p)
        assert p['final_equity']==500 and p['entries']==[] and len(p['aborted_sessions'])==1
        assert p['aborted_sessions'][0]['observed_at']=='2026-01-02T09:36:00-05:00'
        return {'observable_competitor_gap_cancels_session':True}
    check('guarded_current_signal_gap_skips_at_observation_time',guarded_competitor_current_gap)
    def guarded_invalid_settlement():
        args=fixture();args[1][0]['settlement_date']='2026-01-02'
        p=run(engine,args,pre_entry_gap_policy='skip_session')
        assert p['status']=='incomplete' and p['aborted_sessions']==[] and p['final_equity'] is None
        return {'invalid_cash_calendar_not_censored_as_market_gap':True}
    check('guarded_invalid_settlement_remains_incomplete',guarded_invalid_settlement)
    def randomized():
        rng=random.Random(20261006);runs=0
        for k in range(24):
            args=fixture(days=('2026-01-02','2026-01-05','2026-01-06'),delayed=bool(k%2))
            for d in args[2]:
                x=10.2
                for m in range(7,390):
                    x=max(.001,x+rng.uniform(-.045,.05));args[2][d]['A'][m]=bar(x,h=x+.04,l=max(.0001,x-.04))
            for allocation in (.5,1.):
                for costs in (25,50,100):reconcile(run(engine,args,allocation,costs));runs+=1
        return {'seed':20261006,'synthetic_paths_reconciled':runs,'not_profitability_evidence':True}
    check('independent_entry_exit_settlement_bank_ledger',randomized)
    probes=[]
    for folder in sorted((ROOT/'raw-probe').glob('*')):
        if not (folder/'1Min/complete.json').exists() or not (folder/'5Min/complete.json').exists():continue
        ones=raw_pages(folder/'1Min');fives=raw_pages(folder/'5Min')
        for symbol,bars in ones.items():
            actual=actual_first5(bars);prior=[b for b in fives[symbol] if datetime.fromisoformat(b['t'].replace('Z','+00:00')).astimezone(NY).strftime('%H:%M')=='09:30']
            if actual is None or len(prior)!=1:probes.append({'date':folder.name,'symbol':symbol,'passed':False,'reason':'missing_or_duplicate_first5'});continue
            b=prior[0];expected={'or_open':b['o'],'or_close':b['c'],'or_high':b['h'],'or_low':b['l'],'opening_volume':b['v']}
            errors={k:{'one_minute_sum':actual[k],'five_minute_value':expected[k]} for k in actual if not math.isclose(actual[k],expected[k],rel_tol=1e-10,abs_tol=1e-8)}
            probes.append({'date':folder.name,'symbol':symbol,'passed':not errors,'errors':errors})
    check('real_probe_first5_OHLCV_aggregation',lambda: (all(r['passed'] for r in probes) and {'sample_symbol_days':len(probes)}) or (_ for _ in ()).throw(AssertionError(probes)))
    real={'status':'production_ranked_or_minute_file_not_yet_available','candidate_count':0,'mismatches':[]}
    if (ROOT/'ranked.json').exists() and (ROOT/'minute-market.json').exists():
        ranked=json.loads((ROOT/'ranked.json').read_text());minute=json.loads((ROOT/'minute-market.json').read_text());real['status']='checked_available_production_rows'
        for d,rows in ranked.items():
            for r in rows:
                real['candidate_count']+=1;a=actual_first5(minute.get(d,{}).get(r['symbol'],{}))
                if a is None:real['mismatches'].append({'date':d,'symbol':r['symbol'],'reason':'missing_first5'});continue
                bad={k:{'actual':a[k],'ranked':r.get(k)} for k in a if k not in r or not math.isclose(a[k],r[k],rel_tol=1e-10,abs_tol=1e-8)}
                if not math.isclose(r['rv'],a['opening_volume']/r['mean_opening_volume14'],rel_tol=1e-10,abs_tol=1e-8):bad['rv']='inconsistent_volume_over_mean'
                if bad:real['mismatches'].append({'date':d,'symbol':r['symbol'],'differences':bad})
    if real['status']=='checked_available_production_rows':
        real['missing_first5_count']=sum(r.get('reason')=='missing_first5' for r in real['mismatches'])
        real['OHLCV_or_RV_mismatch_count']=sum('differences' in r for r in real['mismatches'])
        real['matched_first5_OHLCV_RV_count']=real['candidate_count']-len(real['mismatches'])
        real['input_sha256']={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ['ranked.json','minute-market.json']}
    actual_paths=[]
    for pathfile in sorted((ROOT/'paths').glob('*.json')):
        path=json.loads(pathfile.read_text())
        if not isinstance(path,dict) or 'daily' not in path:continue
        outcome={'id':path['id'],'file':str(pathfile.relative_to(ROOT)),'status':path['status'],'sessions_accounted':len(path['daily']),'entries':len(path['entries']),'closed_trades':len(path['trades']),'final_equity':path['final_equity'],'incomplete':path['incomplete'],'aborted_sessions':len(path.get('aborted_sessions',[])),'sha256':hashlib.sha256(pathfile.read_bytes()).hexdigest()}
        try:
            reconcile(path)
            assert path['initial_funding']==500 and path['additional_funding']==0 and path['restart_count']==0
            assert all(d['entries']<=1 for d in path['daily'])
            for row in path['daily']:
                if row['status']=='skipped_data_gap':
                    assert row['entries']==0 and row['trades']==0 and row['open_position'] is None
                    assert row['settled_cash']==row['settled_cash_before_signals']
                    assert row['aborted_decision']['observed_at'] is not None
            if path['incomplete']:assert path['final_equity'] is None and path['daily'][-1]['equity'] is None
            outcome['ledger_passed']=True
        except Exception as e:outcome['ledger_passed']=False;outcome['error']=type(e).__name__+': '+str(e)
        actual_paths.append(outcome)
    actual_path_audit={'status':'checked' if actual_paths else 'not_yet_available','count':len(actual_paths),'ledger_passed':sum(r['ledger_passed'] for r in actual_paths),'complete_paths':sum(r['status']=='provisional_complete' for r in actual_paths),'incomplete_paths':sum(r['status']=='incomplete' for r in actual_paths),'paths':actual_paths}
    report={'schema':'s500.external-engine-audit.v1','audited_at':datetime.now().astimezone().isoformat(),'scope':'offline independent adversarial and ledger audit; no network or orders','code_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ['engine.py','features.py','audit_engine_external.py']},'checks':checks,'passed':sum(c['passed'] for c in checks),'failed':sum(not c['passed'] for c in checks),'known_reproducible_findings':[{'id':'opening_volume_and_rv_not_crosschecked','initial_engine_sha256':'ffc2025680bf6690a45290f9d88d9a61af1487c05fa9558b3c20259e0eb95fb3','evidence':'initial inspection: engine comparesfirst5 OHLC only, permitting volume/RV mismatch to change ranking while claiming provisional_complete. First two checks are permanent regressions.','severity':'must_correct_before_trust_in_production_rankings','initial_failures':[{'check':'first5_volume_mismatch_must_block_selection','mutant':'multiply first1minute volume by10, preserve all first5 OHLC','observed_status':'provisional_complete','observed_selected_symbol':'A','expected':'incomplete before selection'},{'check':'RV_must_agree_with_actual_volume_over_prior_mean','mutant':'change B rv from2.5 to100, keep opening_volume and mean unchanged','observed_status':'provisional_complete','observed_selected_symbol':'B','expected':'incomplete before selection'}],'fix_description':'Engine now requires both volume metadata fields if either is present, validates positive finite values, compares first5 minute summed volume to opening_volume, and validates rv equals opening_volume/mean_opening_volume14. Production driver requires both fields for every ranked row; omission remains explicitly diagnosed for synthetic fixtures only.','current_status':'fixed_on_current_checks' if all(c['passed'] for c in checks[:2]) else 'reproduced_on_current_engine'}],'real_aggregation_probe':probes,'real_production_aggregation':real,'actual_path_ledgers':actual_path_audit,'limitations':['Cached directory combines current active and inactive records without a historical security identifier; provider default symbol mapping may alias/duplicate historical instruments. Matching OHLCV does not resolve historical identity or point-in-time survivorship.','No executable spread/order/fill claim. Ledger invariants verify arithmetic and information boundaries, not profitability.','Real input aggregation is checked only for files present; unavailable production minute data is explicitly pending. A passing partial-path ledger audit is not a complete-year result: actual complete/incomplete path counts and stop reasons are reported separately.','Skipping an entire session based on later held missing data would be hindsight; current strict tests require unknown final equity. Any new failclosed policy requires separate temporal regression checks.'],'orders_sent':0}
    (ROOT/'audit-engine-external.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'passed':report['passed'],'failed':report['failed'],'failed_checks':[c for c in checks if not c['passed']],'real_production':{k:v for k,v in real.items() if k!='mismatches'},'actual_paths':{k:v for k,v in actual_path_audit.items() if k!='paths'}}))

if __name__=='__main__':main()
