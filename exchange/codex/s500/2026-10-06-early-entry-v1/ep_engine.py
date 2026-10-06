"""Frozen EP OHLC stress models. Pure computation; never brokers or network.
Money is Decimal throughout. Public outputs contain exact strings, never raw feeds.
"""
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import re

ET=ZoneInfo('America/New_York'); ZERO=Decimal(0); INITIAL=Decimal(500)

def dec(x):
    if isinstance(x,bool): raise ValueError('boolean numeric')
    try: y=Decimal(str(x))
    except (InvalidOperation,ValueError,TypeError): raise ValueError('invalid numeric')
    if not y.is_finite(): raise ValueError('nonfinite numeric')
    return y

def public(x):
    if isinstance(x,Decimal): return str(x)
    if isinstance(x,dict): return {k:public(v) for k,v in x.items()}
    if isinstance(x,(list,tuple)): return [public(v) for v in x]
    return x

def timestamp(session,offset=0):
    return (datetime.fromisoformat(session['date']+'T'+session['open']).replace(tzinfo=ET)+timedelta(minutes=offset)).isoformat()

def minutes(session):
    op=datetime.fromisoformat(session['date']+'T'+session['open']); cl=datetime.fromisoformat(session['date']+'T'+session['close'])
    n=(cl-op).total_seconds()/60
    if n<=0 or int(n)!=n: raise ValueError('invalid calendar session')
    return int(n)

def valid_bar(series,offset,session,open_only=False):
    b=series.get(str(offset),series.get(offset)) if isinstance(series,dict) else None
    if not isinstance(b,dict): return None,'missing_minute'
    try:
        t=b['t']; f=re.search(r'\.(\d+)',t)
        if f and any(c!='0' for c in f.group(1)): raise ValueError('fractional minute')
        dt=datetime.fromisoformat(t.replace('Z','+00:00'))
        if dt.tzinfo is None or dt!=datetime.fromisoformat(timestamp(session,offset)): raise ValueError('wrong minute timestamp')
        out={'o':dec(b['o'])}
        if out['o']<=0: raise ValueError('nonpositive open')
        if not open_only:
            out.update({k:dec(b[k]) for k in ('h','l','c')})
            if min(out.values())<=0 or out['l']>min(out['o'],out['c']) or out['h']<max(out['o'],out['c']) or out['h']<out['l']: raise ValueError('invalid OHLC')
        return out,None
    except (KeyError,TypeError,ValueError): return None,'invalid_minute'

def action_rows(actions,symbol):
    if isinstance(actions,list): return [r for r in actions if r.get('symbol')==symbol or r.get('new_symbol')==symbol]
    if not isinstance(actions,dict): return []
    if symbol in actions and isinstance(actions[symbol],list): return actions[symbol]
    for key in ('rows','actions'):
        if isinstance(actions.get(key),list): return action_rows(actions[key],symbol)
    if isinstance(actions.get('by_symbol'),dict): return actions['by_symbol'].get(symbol,[])
    return []

def action_type(a): return a.get('type',a.get('action_type','unknown'))

def action_problem(actions,symbol,entry_date,date):
    for a in action_rows(actions,symbol):
        ex=a.get('ex_date'); process=a.get('process_date')
        if not ex:
            if process is None or entry_date<process<=date: return {'reason':'corporate_event_missing_ex_date','event':a}
        elif entry_date<ex<=date:
            return {'reason':'unsupported_held_corporate_event','event':a}
    return None

def candidate_id(c): return c.get('candidate_id',c['date']+'__'+c['symbol'])

def intent_key(c):
    sig=c.get('signal') or {}; o=c.get('opening30') or {}; ex=o.get('exact_values') or {}
    try: rv=dec(ex['volume'])/dec(ex['prior20_mean_adjusted_daily_volume'])
    except (KeyError,ValueError,ZeroDivisionError): rv=dec(o.get('volume_ratio',0))
    return (int(sig.get('minute_offset',-1))+1,-rv,c['symbol'])

class Engine:
    def __init__(self,variant,market,daily,calendar,actions,cutoff):
        self.variant=dict(variant); self.frac=dec(variant['fraction']); self.cost=dec(variant['cost_bps'])/10000
        if self.frac not in (Decimal('.5'),Decimal('1')) or self.cost not in (Decimal('.0025'),Decimal('.005'),Decimal('.01')): raise ValueError('unregistered variant')
        if variant['exit_mode'] not in ('fixed10','ma10_max63'): raise ValueError('unregistered exit')
        self.market=market; self.daily=daily; self.actions=actions; self.cutoff=cutoff
        self.calendar=sorted(calendar,key=lambda s:s['date']); self.dates=[s['date'] for s in self.calendar]
        if len(set(self.dates))!=len(self.dates): raise ValueError('duplicate calendar date')
        self.cash=INITIAL; self.receivables=[]; self.position=None; self.pending_order=None; self.trades=[]; self.ledger=[]; self.rows=[]; self.decisions=[]
        self.failure=None; self.last_mark=None; self.last_mark_time=None; self.peak=INITIAL; self.max_dd=ZERO; self.ma_pending=False
        self.milestones={str(v):None for v in (1000,2000,5000,10000)}
    def settle(self,s):
        due=[r for r in self.receivables if r['settlement_date']<=s['date']]
        for r in due:
            self.cash+=r['amount']; self.ledger.append({'type':'settlement','date':s['date'],'amount':r['amount'],'origin_date':r['trade_date']})
            self.receivables.remove(r)
    def incomplete(self,reason,s,offset=0,**extra):
        self.failure={'reason':reason,'date':s['date'],'time':timestamp(s,offset),**extra}; return False
    def snapshot(self,s,status='observed'):
        held=ZERO if self.position is None else self.position['quantity']*(self.last_mark if self.last_mark is not None else self.position['entry_reference'])
        unsettled=sum((r['amount'] for r in self.receivables),ZERO); equity=self.cash+unsettled+held
        liq=self.cash+unsettled+held*(1-self.cost)
        if not self.failure:
            self.peak=max(self.peak,liq); self.max_dd=max(self.max_dd,1-liq/self.peak)
            for k in self.milestones:
                if liq>=dec(k) and self.milestones[k] is None: self.milestones[k]=s['date']
        self.rows.append({'date':s['date'],'status':status,'cash_settled':self.cash,'cash_unsettled':unsettled,'marked_position':held,'last_observed_equity':equity,'last_observed_liquidation_equity':liq,'mark_time':self.last_mark_time,'quantity':0 if self.position is None else self.position['quantity'],'symbol':None if self.position is None else self.position['symbol'],'equity_certified':not bool(self.failure)})
    def sell(self,s,offset,reference,reasons):
        settlement=s.get('settlement_date')
        if not isinstance(settlement,str) or settlement<=s['date']:
            return self.incomplete('missing_or_invalid_settlement_date',s,offset,exit_intent={'reference':reference,'reasons':reasons})
        q=self.position['quantity']; proceeds=q*reference*(1-self.cost)
        trade={'side':'SELL','date':s['date'],'time':timestamp(s,offset),'symbol':self.position['symbol'],'quantity':q,'reference_price':reference,'cost':q*reference*self.cost,'cash_amount':proceeds,'reasons':reasons,'settlement_date':settlement,'candidate_id':self.position['candidate_id'],'modeled_not_actual_fill':True}
        self.trades.append(trade); self.ledger.append({'type':'sale_receivable',**trade}); self.receivables.append({'amount':proceeds,'trade_date':s['date'],'settlement_date':settlement})
        self.position=None; self.ma_pending=False; self.last_mark=None; self.last_mark_time=None; return True
    def enter(self,c,s):
        sig=c.get('signal') or {}; ref=c.get('entry_reference') or {}; cid=candidate_id(c)
        try:
            m=int(sig['minute_offset']); entry=int(ref.get('minute_offset',sig.get('planned_entry_offset',m+2)))
            if not 30<=m<=89 or entry!=m+2: raise ValueError('latency')
            price=dec(sig['close_exact'] if 'close_exact' in sig else sig['close']); stop=dec(ref.get('stop_exact',ref.get('stop_or30_low',(c.get('opening30') or {}).get('low'))))
            if price<=0 or stop<=0: raise ValueError('price')
        except (ValueError,KeyError,TypeError): return self.incomplete('invalid_candidate_intent',s,candidate_id=cid)
        budget=self.cash*self.frac; q=int((budget/(price*(1+self.cost))).to_integral_value(rounding=ROUND_FLOOR))
        decision={'candidate_id':cid,'date':s['date'],'signal_time':timestamp(s,m+1),'entry_time':timestamp(s,entry),'signal_close':price,'budget':budget,'intended_quantity':q,'stop':stop}
        self.decisions.append(decision)
        if q==0: decision['status']='skipped_unaffordable_intent'; return True
        series=self.market.get(s['date'],{}).get(c['symbol'],{})
        b,err=valid_bar(series,entry,s,open_only=True)
        if err:
            self.pending_order={**decision,'symbol':c['symbol'],'exposure_status':'execution_unknown'}
            return self.incomplete('missing_or_invalid_selected_entry',s,entry,candidate_id=cid,bar_reason=err)
        op=b['o']; debit=q*op*(1+self.cost)
        if debit>budget or debit>self.cash:
            decision.update(status='skipped_next_open_unaffordable',entry_reference=op,required_cash=debit); return True
        if op<=stop:
            self.pending_order={**decision,'symbol':c['symbol'],'entry_reference':op,'exposure_status':'ambiguous_gap_through_entry_stop'}
            return self.incomplete('gap_through_entry_stop',s,entry,candidate_id=cid)
        self.cash-=debit; self.position={'symbol':c['symbol'],'candidate_id':cid,'quantity':q,'entry_reference':op,'entry_date':s['date'],'entry_offset':entry,'stop':stop,'sessions_held':1,'entry_cash_debit':debit}
        self.last_mark=op; self.last_mark_time=timestamp(s,entry); decision['status']='entered'
        trade={'side':'BUY','date':s['date'],'time':timestamp(s,entry),'symbol':c['symbol'],'candidate_id':cid,'quantity':q,'reference_price':op,'cost':q*op*self.cost,'cash_amount':debit,'modeled_not_actual_fill':True}
        self.trades.append(trade); self.ledger.append({'type':'purchase',**trade}); return self.hold(s,start_offset=entry,new_entry=True)
    def ma_decision(self,s):
        sym=self.position['symbol']; idx=self.dates.index(s['date']); wanted=self.dates[max(0,idx-9):idx+1]
        if len(wanted)!=10: return self.incomplete('insufficient_ma_warmup_sessions',s,minutes(s))
        for a in action_rows(self.actions,sym):
            if 'split' in action_type(a) and (not a.get('ex_date') or wanted[0]<a['ex_date']<=s['date']):
                return self.incomplete('ma_history_requires_verified_split_adjustment',s,minutes(s),event=a)
        vals=[]
        for date in wanted:
            b=self.daily.get(sym,{}).get(date)
            try:
                v=dec(b['c'])
                if v<=0: raise ValueError('nonpositive close')
                t=b.get('t')
                if t and datetime.fromisoformat(t.replace('Z','+00:00')).astimezone(ET).date().isoformat()!=date: raise ValueError('daily wrong day')
            except (TypeError,KeyError,ValueError): return self.incomplete('missing_or_invalid_ma_daily_close',s,minutes(s),missing_date=date)
            vals.append(v)
        average=sum(vals,ZERO)/10; self.ma_pending=vals[-1]<average
        self.ledger.append({'type':'ma_close_decision','date':s['date'],'time':datetime.fromisoformat(s['date']+'T'+s['close']).replace(tzinfo=ET).isoformat(),'vendor_close':vals[-1],'ma10':average,'decision':'exit_next_open' if self.ma_pending else 'hold','source_dates':wanted})
        return True
    def hold(self,s,start_offset=0,new_entry=False):
        p=self.position
        if not new_entry:
            event=action_problem(self.actions,p['symbol'],p['entry_date'],s['date'])
            if event:return self.incomplete(event.pop('reason'),s,0,**event)
            p['sessions_held']+=1
        series=self.market.get(s['date'],{}).get(p['symbol'],{}); n=minutes(s)
        for off in range(start_offset,n):
            timed_exit=(self.variant['exit_mode']=='fixed10' and p['sessions_held']>=10 and off==n-1) or (self.variant['exit_mode']=='ma10_max63' and p['sessions_held']>=63 and off==n-1)
            ma_exit=self.ma_pending and off==0
            b,err=valid_bar(series,off,s,open_only=True)
            if err:return self.incomplete('held_minute_unresolved',s,off,bar_reason=err,symbol=p['symbol'],quantity=p['quantity'])
            reasons=[]
            if ma_exit:reasons.append('ma10_next_open')
            if timed_exit:reasons.append('fixed10' if self.variant['exit_mode']=='fixed10' else 'max63')
            if b['o']<=p['stop']:reasons.append('gap_stop')
            if reasons:return self.sell(s,off,b['o'],reasons)
            b,err=valid_bar(series,off,s)
            if err:return self.incomplete('held_minute_unresolved',s,off,bar_reason=err,symbol=p['symbol'],quantity=p['quantity'])
            if b['l']<=p['stop']:return self.sell(s,off,p['stop'],['protective_stop'])
            self.last_mark=b['c']; self.last_mark_time=timestamp(s,off+1)
        if self.variant['exit_mode']=='ma10_max63':return self.ma_decision(s)
        return True
    def finish(self,kind,conditional=False):
        incomplete=self.failure is not None
        held=self.position is not None
        equity=self.cash+sum((r['amount'] for r in self.receivables),ZERO)+(ZERO if not held else self.position['quantity']*self.last_mark)
        status='incomplete' if incomplete else ('censored_open_position' if held else 'complete')
        return public({'kind':kind,'variant':self.variant,'status':status,'conditional_on_input_coverage':conditional,'initial_cash':INITIAL,'funding_injections_after_initial':ZERO,'restart_count':0,'cash_settled':self.cash,'terminal_receivables':self.receivables,'position':self.position,'pending_order':self.pending_order,'last_known_mark':self.last_mark,'last_known_mark_time':self.last_mark_time,'last_observed_equity':equity,'ending_equity':None if incomplete else equity,'profit':None if incomplete or held else equity-INITIAL,'profit_status':'unresolved' if incomplete else ('unrealized_censored' if held else 'realized_closed_model'),'failure':self.failure,'daily':self.rows,'trades':self.trades,'ledger':self.ledger,'decisions':self.decisions,'max_daily_liquidation_drawdown':self.max_dd,'drawdown_scope':'observed completed daily snapshots, not intraday worst path','milestones':self.milestones,'cost_model':'bar OHLC stress model; not executable quote or actual fill','corporate_action_policy':'all relevant held events unsupported => incomplete; source completeness remains external limitation'})

def simulate_case(candidate,variant,market,daily,calendar,actions,cutoff='2026-10-05'):
    e=Engine(variant,market,daily,calendar,actions,cutoff); date=candidate['date']; sessions=[s for s in e.calendar if date<=s['date']<=cutoff]
    if not sessions or sessions[0]['date']!=date: raise ValueError('entry date absent from calendar')
    for ix,s in enumerate(sessions):
        e.settle(s)
        if ix==0:e.enter(candidate,s)
        elif e.position is not None:e.hold(s)
        e.snapshot(s,'incomplete' if e.failure else 'observed')
        if e.failure or e.position is None:break
    result=e.finish('independent_hypothetical_500_case');result['candidate_id']=candidate_id(candidate);result['news_not_used_for_case_selection']=True
    return result

def is_market_impossible(c):
    return c.get('market_gate_status') in {'no_breakout','no_breakout_before_11','volume_gate_fail','gap_gate_fail','prior_price_gate_fail','dollar_volume_gate_fail','failed_opening_volume_gate','failed_gap_gate','opening_volume_below_prior_mean','opening_gap_below_threshold','opening30_volume_gate_failed','opening_gap_gate_failed'}

def evidence_true(ev): return isinstance(ev,dict) and ev.get('pass_registered_news_gate') is True

def evidence_impossible(ev):
    # A rejected observed story is NOT proof that all qualifying news is absent.
    return isinstance(ev,dict) and ev.get('all_possible_registered_catalysts_excluded') is True

def simulate_portfolio(candidates,variant,market,daily,calendar,actions,evidence,coverage=None,start_date='2026-01-02',cutoff='2026-10-05'):
    e=Engine(variant,market,daily,calendar,actions,cutoff); byday={}
    for c in candidates:byday.setdefault(c['date'],[]).append(c)
    conditional=coverage is None or not bool(coverage.get('cohort_complete',False))
    for s in e.calendar:
        date=s['date']
        if not start_date<=date<=cutoff:continue
        e.settle(s); had_position=e.position is not None
        if had_position:e.hold(s)
        else:
            cov=(coverage or {}).get('days',{}).get(date,{})
            if cov.get('initial_screen_complete') is False:e.incomplete('initial_screen_coverage_unknown',s)
            else:
                rows=byday.get(date,[]); confirmed=[]; unresolved=[]
                for c in rows:
                    ev=evidence.get(candidate_id(c),{}); cid=candidate_id(c)
                    if is_market_impossible(c) or evidence_impossible(ev):
                        e.decisions.append({'candidate_id':cid,'date':date,'status':'excluded_by_verified_gate','market_gate_status':c.get('market_gate_status')});continue
                    ready=c.get('market_gate_status')=='signal_and_entry_reference_ready' and isinstance(c.get('signal'),dict)
                    if ready and evidence_true(ev):confirmed.append(c)
                    else:unresolved.append(c)
                confirmed.sort(key=intent_key); selected=confirmed[0] if confirmed else None
                blockers=[]
                for c in unresolved:
                    if selected is None or not c.get('signal') or intent_key(c)<=intent_key(selected):blockers.append(candidate_id(c))
                if blockers:e.incomplete('unresolved_candidate_precedence',s,0,blocking_candidates=blockers,selected_candidate=None if selected is None else candidate_id(selected))
                elif selected:e.enter(selected,s)
                else:e.decisions.append({'date':date,'status':'no_confirmed_eligible_intent'})
        if had_position:e.decisions.append({'date':date,'status':'no_entry_position_or_exit_day','candidate_ids':[candidate_id(c) for c in byday.get(date,[])]})
        e.snapshot(s,'incomplete' if e.failure else 'observed')
        if e.failure:break
    result=e.finish('continuous_single_500_portfolio',conditional);result['candidate_count_supplied']=len(candidates); result['candidate_register']=[{'candidate_id':candidate_id(c),'date':c['date'],'symbol':c['symbol'],'market_gate_status':c.get('market_gate_status'),'news_pass':evidence.get(candidate_id(c),{}).get('pass_registered_news_gate'),'after_termination':bool(e.failure and c['date']>e.failure['date'])} for c in candidates]
    return result
