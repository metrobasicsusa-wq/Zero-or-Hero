"""Technical gap OR5/15/30 stress engine, exploratory and never an order client.

Only entry range validation and technical candidate selection differ from the
byte-identical parent event/account engines.  No news pass is manufactured.
"""
from decimal import ROUND_FLOOR
from ep_engine import dec, timestamp, candidate_id
from ep_event_engine import EventEngine, source_bar


def checked_range(value):
    if type(value) is not int or value not in (5, 15, 30):
        raise ValueError('unregistered opening range')
    return value


def intent_key(candidate):
    signal = candidate.get('signal') or {}
    opening = candidate.get('opening_range') or {}
    exact = opening.get('exact_values') or {}
    try:
        ratio = dec(exact['volume']) / dec(exact['prior20_mean_adjusted_daily_volume'])
    except (KeyError, ValueError, ZeroDivisionError):
        ratio = dec(opening.get('volume_ratio', 0))
    return (int(signal.get('minute_offset', -1)) + 1, -ratio, candidate['symbol'])


class EarlyEventEngine(EventEngine):
    def __init__(self, variant, market, daily, calendar, actions, cutoff,
                 opening_range_minutes, mode='evidence_gated', gap_evidence=None,
                 verified_symbol_sessions=None):
        self.opening_range_minutes = checked_range(opening_range_minutes)
        if variant.get('exit_mode') != 'fixed10' or dec(variant['cost_bps']) not in (25, 100):
            raise ValueError('unregistered early-entry financial variant')
        super().__init__(variant, market, daily, calendar, actions, cutoff,
                         mode, gap_evidence, verified_symbol_sessions)

    def enter(self, candidate, session):
        signal = candidate.get('signal') or {}
        reference = candidate.get('entry_reference') or {}
        cid = candidate_id(candidate)
        n = self.opening_range_minutes
        try:
            if (candidate.get('opening_range_minutes') != n or
                    candidate.get('family') != 'OR'+str(n)):
                raise ValueError('candidate family mismatch')
            minute = signal['minute_offset']
            entry = reference.get('minute_offset', signal.get('planned_entry_offset', minute+2))
            if (type(minute) is not int or type(entry) is not int or
                    not n <= minute <= 89 or entry != minute+2):
                raise ValueError('latency or range boundary')
            price = dec(signal['close_exact'] if 'close_exact' in signal else signal['close'])
            stop = dec(reference.get('stop_exact', reference.get('stop_or_range_low',
                       (candidate.get('opening_range') or {}).get('low'))))
            if price <= 0 or stop <= 0:
                raise ValueError('nonpositive price')
        except (ValueError, KeyError, TypeError):
            return self.incomplete('invalid_candidate_intent', session, candidate_id=cid)
        if candidate.get('source_revision_conflict') is True:
            return self.incomplete('source_revision_conflict', session, entry,
                                   candidate_id=cid,
                                   gate_membership_retained=True,
                                   conflict_details=candidate.get('source_revision_conflict_details'),
                                   time_semantics='planned entry dependency unresolved by retrospective source comparison; not a realtime conflict detection claim')
        budget = self.cash * self.frac
        quantity = int((budget / (price * (1+self.cost))).to_integral_value(rounding=ROUND_FLOOR))
        decision = {'candidate_id': cid, 'date': session['date'],
                    'signal_time': timestamp(session, minute+1),
                    'entry_time': timestamp(session, entry), 'signal_close': price,
                    'budget': budget, 'intended_quantity': quantity, 'stop': stop}
        self.decisions.append(decision)
        if quantity == 0:
            decision['status'] = 'skipped_unaffordable_intent'
            return True
        series = self.market.get(session['date'], {}).get(candidate['symbol'], {})
        bar, error = source_bar(series, entry, session, open_only=True)
        if error:
            self.pending_order = {**decision, 'symbol': candidate['symbol'],
                                  'exposure_status': 'execution_unknown'}
            return self.incomplete('missing_or_invalid_selected_entry', session, entry,
                                   candidate_id=cid, bar_reason=error)
        opening = bar['o']
        debit = quantity * opening * (1+self.cost)
        if debit > budget or debit > self.cash:
            decision.update(status='skipped_next_open_unaffordable',
                            entry_reference=opening, required_cash=debit)
            return True
        if opening <= stop:
            self.pending_order = {**decision, 'symbol': candidate['symbol'],
                                  'entry_reference': opening,
                                  'exposure_status': 'ambiguous_gap_through_entry_stop'}
            return self.incomplete('gap_through_entry_stop', session, entry, candidate_id=cid)
        self.cash -= debit
        self.position = {'symbol': candidate['symbol'], 'candidate_id': cid,
                         'quantity': quantity, 'entry_reference': opening,
                         'entry_date': session['date'], 'entry_offset': entry,
                         'stop': stop, 'sessions_held': 1, 'entry_cash_debit': debit}
        self.last_mark = opening
        self.last_mark_time = timestamp(session, entry)
        decision['status'] = 'entered'
        trade = {'side': 'BUY', 'date': session['date'], 'time': timestamp(session, entry),
                 'symbol': candidate['symbol'], 'candidate_id': cid,
                 'quantity': quantity, 'reference_price': opening,
                 'cost': quantity*opening*self.cost, 'cash_amount': debit,
                 'modeled_not_actual_fill': True}
        self.trades.append(trade)
        self.ledger.append({'type': 'purchase', **trade})
        return self.hold(session, start_offset=entry, new_entry=True)

    def finish(self, kind, conditional=True):
        result = super().finish(kind, True)
        result.update(family='OR'+str(self.opening_range_minutes),
                      opening_range_minutes=self.opening_range_minutes,
                      news_selection_used=False,
                      strategy_scope='technical gap opening-range package; not verified-catalyst EP',
                      opening_volume_interpretation='linear-duration threshold using prior20 full-day mean; not same-time RVOL',
                      quarantine_scope='any source-version conflict in planned input window; may include unused future data',
                      source_revision_quarantine_applied=bool(self.failure and (self.failure.get('reason') == 'source_revision_conflict' or self.failure.get('source_conflict_blockers'))))
        return result


def make_engine(candidate_or_range, variant, market, daily, calendar, actions, cutoff,
                mode, gap_evidence, verified_symbol_sessions):
    n = candidate_or_range.get('opening_range_minutes') if isinstance(candidate_or_range, dict) else candidate_or_range
    return EarlyEventEngine(variant, market, daily, calendar, actions, cutoff,
                            n, mode, gap_evidence, verified_symbol_sessions)


def simulate_case(candidate, variant, market, daily, calendar, actions,
                  cutoff='2026-10-05', mode='evidence_gated', gap_evidence=None,
                  verified_symbol_sessions=None):
    """Only ready cases; all failed/unknown gates must remain in the full registry."""
    if candidate.get('market_gate_status') != 'signal_and_entry_reference_ready' or candidate.get('market_gate_pass') is not True:
        raise ValueError('isolated simulation requires a ready technical gate')
    e = make_engine(candidate, variant, market, daily, calendar, actions, cutoff,
                    mode, gap_evidence, verified_symbol_sessions)
    date = candidate['date']
    sessions = [s for s in e.calendar if date <= s['date'] <= cutoff]
    if not sessions or sessions[0]['date'] != date:
        raise ValueError('entry date absent from calendar')
    for index, session in enumerate(sessions):
        e.settle(session)
        if index == 0:
            e.enter(candidate, session)
        elif e.position is not None:
            e.hold(session)
        e.snapshot(session, 'incomplete' if e.failure else 'observed')
        if e.failure or e.position is None:
            break
    result = e.finish('independent_hypothetical_500_technical_case')
    result['candidate_id'] = candidate_id(candidate)
    result['news_not_used_for_case_selection'] = True
    return result


def validate_candidates(candidates, opening_range_minutes):
    n = checked_range(opening_range_minutes)
    ids = [candidate_id(c) for c in candidates]
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate candidate in family registry')
    if any(c.get('opening_range_minutes') != n or c.get('family') != 'OR'+str(n) for c in candidates):
        raise ValueError('mixed or mismatched family registry')


def simulate_portfolio(candidates, variant, market, daily, calendar, actions,
                       coverage=None, start_date='2026-01-02', cutoff='2026-10-05',
                       mode='evidence_gated', gap_evidence=None,
                       verified_symbol_sessions=None, opening_range_minutes=30):
    """Technical full registry: failed gates excluded, earlier unknowns block.

    No news evidence is accepted by this API because news does not select this
    strategy. Unknown signal/gate candidates cannot be converted to exclusions.
    """
    validate_candidates(candidates, opening_range_minutes)
    e = make_engine(opening_range_minutes, variant, market, daily, calendar, actions,
                    cutoff, mode, gap_evidence, verified_symbol_sessions)
    byday = {}
    for candidate in candidates:
        byday.setdefault(candidate['date'], []).append(candidate)
    for session in e.calendar:
        day = session['date']
        if not start_date <= day <= cutoff:
            continue
        e.settle(session)
        had_position = e.position is not None
        if had_position:
            e.hold(session)
        elif (coverage or {}).get('days', {}).get(day, {}).get('initial_screen_complete') is False:
            e.incomplete('initial_screen_coverage_unknown', session)
        else:
            confirmed, unresolved = [], []
            for candidate in byday.get(day, []):
                if candidate.get('source_revision_conflict') is True:
                    # A conflicting source may change the old gate or its first
                    # signal.  Do not trust its old (possibly later) rank.
                    unresolved.append(candidate)
                elif candidate.get('market_gate_pass') is False:
                    e.decisions.append({'candidate_id': candidate_id(candidate), 'date': day,
                                        'status': 'excluded_by_verified_market_gate',
                                        'market_gate_status': candidate.get('market_gate_status')})
                elif (candidate.get('market_gate_pass') is True and
                      candidate.get('market_gate_status') == 'signal_and_entry_reference_ready' and
                      isinstance(candidate.get('signal'), dict)):
                    confirmed.append(candidate)
                else:
                    unresolved.append(candidate)
            confirmed.sort(key=intent_key)
            selected = confirmed[0] if confirmed else None
            blockers = [candidate_id(c) for c in unresolved if
                        c.get('source_revision_conflict') is True or selected is None or
                        not c.get('signal') or intent_key(c) <= intent_key(selected)]
            if blockers:
                e.incomplete('unresolved_candidate_precedence', session, 0,
                             blocking_candidates=blockers,
                             selected_candidate=None if selected is None else candidate_id(selected),
                             unknown_scope='technical market gates; news not a strategy gate',
                             source_conflict_blockers=[candidate_id(c) for c in unresolved if c.get('source_revision_conflict') is True],
                             conflict_policy='any flagged candidate blocks flat-day selection irrespective of old rank; conservative retrospective whole-case input quarantine, not realtime conflict detection')
            elif selected:
                e.enter(selected, session)
            else:
                e.decisions.append({'date': day, 'status': 'no_confirmed_technical_intent'})
        if had_position:
            e.decisions.append({'date': day, 'status': 'no_entry_position_or_exit_day',
                                'candidate_ids': [candidate_id(c) for c in byday.get(day, [])]})
        e.snapshot(session, 'incomplete' if e.failure else 'observed')
        if e.failure:
            break
    result = e.finish('continuous_single_500_technical_portfolio')
    result['candidate_count_supplied'] = len(candidates)
    result['candidate_register'] = [
        {'candidate_id': candidate_id(c), 'date': c['date'], 'symbol': c['symbol'],
         'market_gate_status': c.get('market_gate_status'),
         'market_gate_pass': c.get('market_gate_pass'),
         'after_termination': bool(e.failure and c['date'] > e.failure['date'])}
        for c in candidates]
    return result


def simulate_cohort_portfolio(candidates, variant, market, daily, calendar, actions,
                              cohort_name='retrospective_market_pass',
                              start_date='2026-01-02', cutoff='2026-10-05',
                              mode='evidence_gated', gap_evidence=None,
                              verified_symbol_sessions=None, opening_range_minutes=30):
    """Conditional diagnostic of frozen ready members, omitting unknown gates."""
    if cohort_name != 'retrospective_market_pass':
        raise ValueError('unregistered technical cohort')
    if any(c.get('market_gate_pass') is not True or
           c.get('market_gate_status') != 'signal_and_entry_reference_ready' or
           not isinstance(c.get('signal'), dict) for c in candidates):
        raise ValueError('cohort contains a non-ready member')
    result = simulate_portfolio(candidates, variant, market, daily, calendar, actions,
                                None, start_date, cutoff, mode, gap_evidence,
                                verified_symbol_sessions, opening_range_minutes)
    result.update(kind='retrospective_technical_ready_cohort_single_500_portfolio',
                  cohort_name=cohort_name, retrospective_membership_condition=True,
                  historical_implementability_claim=False,
                  unknown_market_candidates_omitted_by_diagnostic_design=True,
                  news_selection_used=False,
                  supplied_membership=[candidate_id(c) for c in candidates])
    return result
