"""Offline ORB cash simulation. No network, credentials, broker or scheduler imports."""
import copy
from datetime import date as Date, datetime, time as Time, timedelta
import math
from zoneinfo import ZoneInfo

NY = ZoneInfo('America/New_York')


def finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def bar_at(series, offset):
    if offset in series and str(offset) in series:
        return None
    return series.get(offset, series.get(str(offset)))


def valid_open(b):
    return isinstance(b, dict) and finite(b.get('o')) and b['o'] > 0


def valid_bar(b):
    return (valid_open(b) and all(finite(b.get(k)) and b[k] > 0 for k in ('h', 'l', 'c'))
            and finite(b.get('v')) and b['v'] >= 0
            and b['l'] <= min(b['o'], b['c']) <= max(b['o'], b['c']) <= b['h'])


def session(row):
    d = Date.fromisoformat(row['date'])
    opening = Time.fromisoformat(row['open']); closing = Time.fromisoformat(row['close'])
    if d.isoformat() != row['date'] or any(t.tzinfo is not None or t.second or t.microsecond for t in (opening,closing)):
        raise ValueError('noncanonical_session_time')
    start = datetime.combine(d, opening, NY)
    end = datetime.combine(d, closing, NY)
    n = (end-start).total_seconds()/60
    if start.hour != 9 or start.minute != 30 or n != int(n) or n <= 7:
        raise ValueError('unsupported_session')
    return start, int(n)-1


def run_path(ranked, calendar, minute_market, fraction, bps, coverage=None,
             pre_entry_gap_policy='stop'):
    """Calendar supplied by caller defines the evaluation sessions and settlement dates.

    coverage[date] may provide ranking_complete and ranking_missing. Unknown
    metadata is disclosed; explicit incomplete ranking stops before any entry.
    Missing active-candidate bars before selection conservatively stop the path.
    skip_session is a separate operational sensitivity: observable data gaps
    before a modeled fill cancel the whole session; a held-position gap stops.
    """
    if fraction not in (.5, 1.) or bps not in (25, 50, 100):
        raise ValueError('unregistered_parameters')
    if pre_entry_gap_policy not in ('stop', 'skip_session'):
        raise ValueError('unregistered_pre_entry_gap_policy')
    if not calendar:
        raise ValueError('empty_evaluation_calendar')
    dates = [r['date'] for r in calendar]
    if dates != sorted(set(dates)):
        raise ValueError('calendar_not_unique_in_order')
    cost = bps/10000.
    cash = 500.; receivables = []; trades = []; entries = []; signals = []; skips = []; daily = []
    incomplete = None; position = None; pending_entry = None; aborted_sessions = []
    skippable_gap_reasons = {'incomplete_ranking_inputs', 'missing_ranked_day',
        'invalid_ranked_candidates', 'invalid_ranked_candidate',
        'missing_or_invalid_opening_window', 'opening_feature_bar_mismatch',
        'opening_volume_metadata_invalid', 'opening_volume_bar_mismatch',
        'relative_volume_feature_mismatch', 'unresolved_preselection_signal_gap',
        'missing_selected_entry_open'}
    last_observed_equity = 500.; daily_peak = 500.; daily_dd = 0.; observed_peak = 500.; observed_dd = 0.
    milestones = {str(x): None for x in (1000, 2000, 10000)}
    counts = {'ranking_verified_days': 0, 'ranking_unknown_days': 0, 'candidate_sessions': 0,
              'candidate_sessions_full_regular_bars': 0, 'candidate_sessions_with_missing_bars': 0,
              'opening_volume_verified_candidates': 0, 'opening_volume_unknown_candidates': 0,
              'aborted_pre_entry_sessions': 0}

    def mark(value):
        nonlocal last_observed_equity, observed_peak, observed_dd
        last_observed_equity = cash + sum(x['amount'] for x in receivables) + value
        observed_peak = max(observed_peak, last_observed_equity)
        observed_dd = max(observed_dd, 1-last_observed_equity/observed_peak)

    for calendar_row in calendar:
        day = calendar_row['date']; day_market = minute_market.get(day, {})
        paid = sum(x['amount'] for x in receivables if x['settlement_date'] <= day)
        cash += paid
        receivables = [x for x in receivables if x['settlement_date'] > day]
        cash_before = cash; outcomes = []; audit = []; status = 'no_entry'; start = None; aborted = None
        start_entry_count = len(entries); start_trade_count = len(trades)
        day_cov = (coverage or {}).get(day, {})
        ranking_status = 'verified' if day_cov.get('ranking_complete') is True else 'unknown'
        counts['ranking_verified_days' if ranking_status == 'verified' else 'ranking_unknown_days'] += 1
        try:
            start, last_offset = session(calendar_row)
        except (KeyError, TypeError, ValueError):
            incomplete = {'date': day, 'reason': 'invalid_session_calendar'}
        stamp = lambda m: (start+timedelta(minutes=m)).isoformat() if start else None
        if incomplete is None and (day_cov.get('ranking_complete') is False or day_cov.get('ranking_missing')):
            incomplete = {'date': day, 'time': stamp(5), 'reason': 'incomplete_ranking_inputs',
                          'ranking_missing': copy.deepcopy(day_cov.get('ranking_missing', []))}
        if incomplete is None and day not in ranked:
            incomplete = {'date': day, 'time': stamp(5), 'reason': 'missing_ranked_day'}
        rows = ranked.get(day, [])
        if incomplete is None and (not isinstance(rows, list) or len(rows) > 20):
            incomplete = {'date': day, 'time': stamp(5), 'reason': 'invalid_ranked_candidates'}
        active = []
        if incomplete is None:
            seen = set()
            for r in rows:
                if not isinstance(r,dict):
                    incomplete = {'date':day,'time':stamp(5),'reason':'invalid_ranked_candidate'};break
                symbol = r.get('symbol')
                out = {**copy.deepcopy(r), 'status': 'awaiting_first_signal'}; outcomes.append(out)
                valid = (isinstance(symbol, str) and symbol and symbol not in seen
                         and all(finite(r.get(k)) for k in ('rv','or_high','or_low','or_open','or_close','atr14'))
                         and r['rv'] >= 1 and r['or_open'] > 5 and r['atr14'] > .5
                         and 0 < r['or_low'] <= min(r['or_open'],r['or_close']) <= max(r['or_open'],r['or_close']) <= r['or_high'])
                if not valid:
                    incomplete = {'date': day, 'time': stamp(5), 'symbol': symbol, 'reason': 'invalid_ranked_candidate'}; break
                seen.add(symbol)
                series = day_market.get(symbol, {})
                missing = [m for m in range(last_offset+1) if not valid_bar(bar_at(series,m))]
                audit.append({'symbol':symbol,'regular_minutes_expected':last_offset+1,
                              'complete_ohlcv_minutes':last_offset+1-len(missing),'missing_or_invalid_offsets':missing,
                              'scope':'diagnostic_only; future coverage never changes earlier decisions'})
                counts['candidate_sessions'] += 1
                counts['candidate_sessions_with_missing_bars' if missing else 'candidate_sessions_full_regular_bars'] += 1
                opening = [bar_at(series,m) for m in range(5)]
                if not all(valid_bar(b) for b in opening):
                    incomplete = {'date':day,'time':stamp(5),'symbol':symbol,'reason':'missing_or_invalid_opening_window'}; break
                actual = {'or_open':opening[0]['o'],'or_close':opening[-1]['c'],
                          'or_high':max(b['h'] for b in opening),'or_low':min(b['l'] for b in opening)}
                if any(not math.isclose(actual[k],r[k],rel_tol=1e-10,abs_tol=1e-8) for k in actual):
                    incomplete = {'date':day,'time':stamp(5),'symbol':symbol,'reason':'opening_feature_bar_mismatch'}; break
                volume_keys = ('opening_volume', 'mean_opening_volume14')
                if any(k in r for k in volume_keys):
                    if not all(finite(r.get(k)) and r[k] > 0 for k in volume_keys):
                        incomplete = {'date':day,'time':stamp(5),'symbol':symbol,'reason':'opening_volume_metadata_invalid'}; break
                    actual_volume = sum(b['v'] for b in opening)
                    if not math.isclose(actual_volume,r['opening_volume'],rel_tol=1e-10,abs_tol=1e-8):
                        incomplete = {'date':day,'time':stamp(5),'symbol':symbol,
                                      'reason':'opening_volume_bar_mismatch','minute_sum':actual_volume,
                                      'ranked_opening_volume':r['opening_volume']}; break
                    if not math.isclose(r['rv'],r['opening_volume']/r['mean_opening_volume14'],rel_tol=1e-10,abs_tol=1e-8):
                        incomplete = {'date':day,'time':stamp(5),'symbol':symbol,'reason':'relative_volume_feature_mismatch'}; break
                    out['opening_volume_validation'] = 'verified'
                    counts['opening_volume_verified_candidates'] += 1
                else:
                    out['opening_volume_validation'] = 'not_supplied; only_suitable_for_explicit_synthetic_inputs'
                    counts['opening_volume_unknown_candidates'] += 1
                if r['or_close'] <= r['or_open']:
                    out['status'] = 'non_bullish_opening_range'
                else:
                    active.append((r,out))
        selected = None
        if incomplete is None:
            for m in range(5, min(89,last_offset-2)+1):
                if not active:
                    break
                holes = [r['symbol'] for r,o in active if not valid_bar(bar_at(day_market.get(r['symbol'],{}),m))]
                if holes:
                    incomplete = {'date':day,'time':stamp(m+1),'minute_offset':m,
                                  'reason':'unresolved_preselection_signal_gap','symbols':holes}; break
                fired=[]; still=[]
                for r,out in active:
                    b=bar_at(day_market[r['symbol']],m)
                    if b['c'] > r['or_high']:
                        fired.append((r,out,b))
                    else: still.append((r,out))
                fired.sort(key=lambda x:(-x[0]['rv'],x[0]['symbol']))
                for r,out,b in fired:
                    event={'date':day,'symbol':r['symbol'],'rank':r.get('rank'),'rv':r['rv'],
                           'signal_minute_offset':m,'signal_completed_at':stamp(m+1),
                           'signal_close':b['c'],'planned_entry_offset':m+2,'planned_entry_time':stamp(m+2),
                           'settled_cash_available':cash,'budget':cash*fraction}
                    if selected:
                        event['status']='lower_priority_same_time_not_selected'
                    elif b['c']*(1+cost)>cash*fraction:
                        event['status']='unaffordable_at_first_signal'
                    else:
                        event['status']='selected';selected=(r,event)
                    signals.append(event);out['status']=event['status'];out['first_signal_offset']=m
                active=still
                if selected:
                    break
            for r,out in active:
                out['status']=('not_observed_after_selection' if selected else
                               'unresolved_signal_gap' if incomplete else 'no_first_signal_before_cutoff')
        if incomplete is None and selected:
            r,event=selected;symbol=r['symbol'];m=event['planned_entry_offset']
            pending_entry={**event,'status':'model_entry_execution_unverified'}
            if m >= last_offset:
                skips.append({**event,'reason':'entry_not_before_scheduled_exit'});status='entry_too_late';pending_entry=None
            else:
                try:
                    settle=Date.fromisoformat(calendar_row['settlement_date']).isoformat()
                    if settle <= day:raise ValueError
                except (KeyError, TypeError, ValueError):
                    incomplete={'date':day,'time':stamp(m),'symbol':symbol,'reason':'invalid_or_missing_t_plus_one_settlement_date'}
                b=bar_at(day_market.get(symbol,{}),m)
                if incomplete is None and not valid_open(b):
                    incomplete={'date':day,'time':stamp(m),'minute_offset':m,'symbol':symbol,'reason':'missing_selected_entry_open'}
                if incomplete is None:
                    qty=math.floor((cash*fraction)/(b['o']*(1+cost)))
                    if qty < 1:
                        skips.append({**event,'reason':'selected_open_unaffordable_no_runner_up','entry_reference_open':b['o']})
                        pending_entry=None;status='selected_open_unaffordable'
                    else:
                        debit=qty*b['o']*(1+cost);cash-=debit
                        position={**event,'entry_date':day,'entry_offset':m,'entry_time':stamp(m),
                                  'entry_price':b['o'],'qty':qty,'entry_debit':debit,'entry_cost':qty*b['o']*cost,
                                  'stop_price':b['o']-.1*r['atr14'],'atr14':r['atr14'],
                                  'last_observed_price':b['o'],'last_observed_time':stamp(m),
                                  'settlement_date':settle,'status':'historical_model_position'}
                        entries.append(copy.deepcopy(position));pending_entry=None
                        mark(qty*b['o']*(1-cost))
                        for offset in range(m,last_offset+1):
                            current=bar_at(day_market.get(symbol,{}),offset)
                            if offset == last_offset:
                                if not valid_open(current):
                                    incomplete={'date':day,'time':stamp(offset),'symbol':symbol,'minute_offset':offset,'reason':'missing_scheduled_exit_open'};break
                                ref=current['o'];reason='scheduled_last_minute_open';exact=True
                            else:
                                if not valid_bar(current):
                                    incomplete={'date':day,'time':stamp(offset),'symbol':symbol,'minute_offset':offset,'reason':'missing_or_invalid_held_minute'};break
                                if current['o'] <= position['stop_price']:
                                    ref=current['o'];reason='stop_gap_open';exact=True
                                elif current['l'] <= position['stop_price']:
                                    ref=position['stop_price'];reason='stop_touch_in_minute';exact=False
                                else:
                                    position['last_observed_price']=current['c'];position['last_observed_time']=stamp(offset+1)
                                    mark(qty*current['c']*(1-cost));continue
                            credit=qty*ref*(1-cost)
                            trade={**position,'exit_offset':offset,'exit_time':stamp(offset) if exact else None,
                                   'exit_interval_start':stamp(offset),'exit_interval_end':stamp(offset+1),
                                   'exit_reason':reason,'exit_price':ref,'exit_cost':qty*ref*cost,
                                   'exit_credit':credit,'pnl':credit-debit,'status':'historical_model_trade'}
                            trades.append(trade)
                            receivables.append({'trade_index':len(trades)-1,'symbol':symbol,'trade_date':day,
                                                'settlement_date':settle,'amount':credit,'type':'unsettled_sale_proceeds'})
                            position=None;mark(0.);status='traded';break
        if (incomplete and pre_entry_gap_policy == 'skip_session'
                and incomplete['reason'] in skippable_gap_reasons
                and position is None and len(entries) == start_entry_count):
            aborted = {'date':day,'observed_at':incomplete.get('time'),
                       'reason':incomplete['reason'],'gap':copy.deepcopy(incomplete),
                       'cancelled_pending_entry':copy.deepcopy(pending_entry),
                       'settled_cash':cash,'unsettled_cash':sum(x['amount'] for x in receivables),
                       'policy':'skip_entire_session_no_replacement_no_fill'}
            aborted_sessions.append(aborted)
            skips.append(copy.deepcopy(aborted))
            counts['aborted_pre_entry_sessions'] += 1
            pending_entry = None; incomplete = None; status = 'skipped_data_gap'
        if incomplete:
            status='incomplete'
        unsettled=sum(x['amount'] for x in receivables)
        equity=None if incomplete else cash+unsettled
        row={'date':day,'status':status,'settlement_date':calendar_row.get('settlement_date'),
             'settlement_cash_paid':paid,'settled_cash_before_signals':cash_before,'settled_cash':cash,
             'unsettled_cash':unsettled,'equity':equity,'entries':len(entries)-start_entry_count,
             'trades':len(trades)-start_trade_count,'ranking_coverage':ranking_status,
             'ranking_input':copy.deepcopy(day_cov),'candidate_outcomes':outcomes,'minute_coverage':audit,
             'open_position':copy.deepcopy(position),'pending_entry':copy.deepcopy(pending_entry),
             'aborted_decision':copy.deepcopy(aborted),'incomplete':copy.deepcopy(incomplete)}
        daily.append(row)
        if cash < -1e-8:raise AssertionError('implicit_borrowing')
        if incomplete:break
        if position:raise AssertionError('unexpected_overnight_holding')
        mark(0.);daily_peak=max(daily_peak,equity);daily_dd=max(daily_dd,1-equity/daily_peak)
        for target in milestones:
            if milestones[target] is None and equity>=int(target):milestones[target]=day
        if not math.isclose(equity,500+sum(t['pnl'] for t in trades),abs_tol=1e-7):
            raise AssertionError('cash_equity_funding_identity')
    variant_suffix = '' if pre_entry_gap_policy == 'stop' else '__guarded-skip-session'
    return {'id':f'ORB-RV-500-v1__a{fraction:g}__c{bps}'+variant_suffix,
            'parameters':{'capital_fraction':fraction,'cost_bps_each_side':bps,'pre_entry_gap_policy':pre_entry_gap_policy},
            'status':'incomplete' if incomplete else 'provisional_complete','incomplete':incomplete,
            'initial_funding':500,'additional_funding':0,'restart_count':0,
            'final_equity':None if incomplete else cash+sum(x['amount'] for x in receivables),
            'settled_cash':cash,'unsettled_cash':sum(x['amount'] for x in receivables),
            'terminalreceivables':copy.deepcopy(receivables),'terminal_receivables':copy.deepcopy(receivables),
            'open_position':copy.deepcopy(position),'pending_entry':copy.deepcopy(pending_entry),
            'last_observed_equity':last_observed_equity,'max_daily_liquidation_drawdown':daily_dd,
            'max_observed_minute_liquidation_drawdown':observed_dd,'milestones':milestones,
            'milestone_basis':'completed_session_equity_including_unsettled_sale_proceeds',
            'drawdown_limit':'Daily/observed-minute marks are not an intrabar worst-case path.',
            'coverage':counts,'daily':daily,'entries':entries,'signals':signals,'skips':skips,'trades':trades,
            'aborted_sessions':aborted_sessions,
            'model_limitations':['Whole shares sized at the future opening print is an idealized cash-constrained fill.',
             'Stop touches use minute OHLC; exact within-minute fill time, spread, halts and liquidity are not verified.',
             'No overnight holdings in completed paths; unsupported intraday action/identity changes are not inferred.',
             'Upstream ranking coverage is unknown unless explicitly supplied; candidate minute coverage is separately reported.',
             'Opening volume metadata omission is allowed for explicit synthetic inputs; production must supply both volume fields.',
             'skip_session is a separate data-guard sensitivity, not evidence that omitted sessions had no profitable opportunity.'],
            'broker_orders_sent':0}
