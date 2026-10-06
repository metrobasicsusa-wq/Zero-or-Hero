"""EP event-series sensitivity, frozen after observing v1; no network or orders.

Imports the byte-identical published v1 Decimal account engine.  Missing bars
are never synthesized.  Both modes are conditional aggregate-price models,
not an assertion about broker stop triggers or actual executions.
"""
from datetime import datetime, timezone
import re
from ep_engine import (Engine as BaseEngine, ZERO, INITIAL, dec, public,
                       timestamp, minutes, valid_bar, action_problem,
                       candidate_id, intent_key, is_market_impossible,
                       evidence_true, evidence_impossible)

MODES = ('evidence_gated', 'provider_event_series_assumption')
ALLOWED_CLASSIFICATION = 'target_missing_price_excluded_trades_consistent_with_documented_rules'


def utc_minute(value):
    fraction = re.search(r'\.(\d+)', value)
    if fraction and any(c != '0' for c in fraction.group(1)):
        raise ValueError('evidence timestamp must be an exact minute')
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None or dt.second or dt.microsecond:
        raise ValueError('evidence timestamp must be an aware exact minute')
    return dt.astimezone(timezone.utc).isoformat()


def source_bar(series, offset, session, open_only=False):
    result, error = valid_bar(series, offset, session, open_only=open_only)
    if error == 'missing_minute' and isinstance(series, dict):
        if str(offset) in series or offset in series:
            return None, 'invalid_minute'
    return result, error


class EventEngine(BaseEngine):
    def __init__(self, variant, market, daily, calendar, actions, cutoff,
                 mode='evidence_gated', gap_evidence=None,
                 verified_symbol_sessions=None):
        super().__init__(variant, market, daily, calendar, actions, cutoff)
        if mode not in MODES:
            raise ValueError('unregistered data mode')
        self.mode = mode
        self.gap_evidence = {}
        for row in (gap_evidence or {}).get('rows', []):
            # The caller supplies the frozen whitelist; malformed or other
            # classifications cannot confer permission to advance a gap.
            if row.get('classification') != ALLOWED_CLASSIFICATION:
                continue
            key = (row['symbol'], utc_minute(row['time']))
            if key in self.gap_evidence:
                raise ValueError('duplicate gap evidence coordinate')
            self.gap_evidence[key] = dict(row)
        self.verified_sessions = (verified_symbol_sessions or {}).get('days', {})
        self.gap_spans = []
        self.drawdown_snapshots_evaluated = 0
        self.drawdown_snapshots_excluded = 0

    def absence_permission(self, s, offset, symbol):
        if self.mode == 'evidence_gated':
            row = self.gap_evidence.get((symbol, utc_minute(timestamp(s, offset))))
            if row is None:
                return None
            return {'basis': 'registered_exact_coordinate_evidence',
                    'point_ids': [row['point_id']], 'request_ids': []}
        row = self.verified_sessions.get(s['date'], {}).get(symbol, {})
        if (row.get('request_complete') is not True or
                row.get('regular_window_covered') is not True):
            return None
        return {'basis': 'provider_complete_event_series_assumption',
                'point_ids': [], 'request_ids': list(row.get('request_ids', []))}

    def record_gap(self, s, offset, symbol, permission):
        merge = (self.gap_spans and
                 self.gap_spans[-1]['date'] == s['date'] and
                 self.gap_spans[-1]['symbol'] == symbol and
                 self.gap_spans[-1]['last_offset_inclusive'] + 1 == offset and
                 self.gap_spans[-1]['basis'] == permission['basis'])
        if merge:
            row = self.gap_spans[-1]
            row['last_offset_inclusive'] = offset
            row['end_time_exclusive'] = timestamp(s, offset + 1)
            row['minute_count'] += 1
            for key in ('point_ids', 'request_ids'):
                row[key] = sorted(set(row[key]) | set(permission[key]))
        else:
            self.gap_spans.append({
                'symbol': symbol, 'date': s['date'], 'first_offset': offset,
                'last_offset_inclusive': offset, 'start_time': timestamp(s, offset),
                'end_time_exclusive': timestamp(s, offset + 1), 'minute_count': 1,
                **permission, 'execution_price_created': False,
                'meaning': 'No returned aggregate price event used at this minute; real trades and stop execution are not certified.'})

    def sell(self, s, offset, reference, reasons):
        success = super().sell(s, offset, reference, reasons)
        if success:
            trade = self.trades[-1]
            if 'protective_stop' in reasons:
                trade.update(time_semantics='source_bar_interval_start_not_known_execution_time',
                             source_bar_interval_end=timestamp(s, offset + 1))
            else:
                trade['time_semantics'] = 'scheduled_or_observed_bar_open_reference_not_actual_fill'
            # BaseEngine makes a separate ledger dictionary when selling.
            self.ledger[-1].update({key: trade[key] for key in
                                   ('time_semantics', 'source_bar_interval_end') if key in trade})
        return success

    def hold(self, s, start_offset=0, new_entry=False):
        p = self.position
        if not new_entry:
            event = action_problem(self.actions, p['symbol'], p['entry_date'], s['date'])
            if event:
                return self.incomplete(event.pop('reason'), s, 0, **event)
            p['sessions_held'] += 1
        series = self.market.get(s['date'], {}).get(p['symbol'], {})
        n = minutes(s)
        scheduled = ((self.variant['exit_mode'] == 'fixed10' and p['sessions_held'] >= 10) or
                     (self.variant['exit_mode'] == 'ma10_max63' and p['sessions_held'] >= 63))
        # A whole empty session is not proved event-free.  Known execution
        # deadlines take precedence; otherwise exposure becomes unresolved
        # at the close, not an invented price/stop event at the open.
        if not isinstance(series, dict):
            return self.incomplete('held_minute_unresolved', s, start_offset,
                                   bar_reason='invalid_minute_series', symbol=p['symbol'], quantity=p['quantity'])
        if not any(str(off) in series or off in series for off in range(start_offset, n)):
            if self.ma_pending and start_offset == 0:
                return self.incomplete('missing_required_execution_bar', s, 0,
                                       execution_intents=['ma10_next_open'], bar_reason='missing_minute',
                                       symbol=p['symbol'], quantity=p['quantity'])
            if scheduled:
                return self.incomplete('missing_required_execution_bar', s, n - 1,
                                       execution_intents=['fixed10' if self.variant['exit_mode'] == 'fixed10' else 'max63'],
                                       bar_reason='missing_minute', symbol=p['symbol'], quantity=p['quantity'])
            return self.incomplete('empty_held_session_unresolved', s, n,
                                   symbol=p['symbol'], quantity=p['quantity'],
                                   absence_not_evidence_of_halt=True)
        for off in range(start_offset, n):
            timed_exit = scheduled and off == n - 1
            ma_exit = self.ma_pending and off == 0
            required = (['ma10_next_open'] if ma_exit else []) + (
                ['fixed10' if self.variant['exit_mode'] == 'fixed10' else 'max63'] if timed_exit else [])
            b, error = source_bar(series, off, s, open_only=True)
            if error:
                if required:
                    return self.incomplete('missing_required_execution_bar', s, off,
                                           execution_intents=required, bar_reason=error,
                                           symbol=p['symbol'], quantity=p['quantity'])
                permission = self.absence_permission(s, off, p['symbol']) if error == 'missing_minute' else None
                if permission is None:
                    return self.incomplete('held_minute_unresolved', s, off,
                                           bar_reason=error, symbol=p['symbol'], quantity=p['quantity'])
                self.record_gap(s, off, p['symbol'], permission)
                continue
            reasons = list(required)
            if b['o'] <= p['stop']:
                reasons.append('gap_stop')
            if reasons:
                return self.sell(s, off, b['o'], reasons)
            b, error = source_bar(series, off, s)
            if error:
                return self.incomplete('held_minute_unresolved', s, off,
                                       bar_reason=error, symbol=p['symbol'], quantity=p['quantity'])
            if b['l'] <= p['stop']:
                return self.sell(s, off, p['stop'], ['protective_stop'])
            self.last_mark = b['c']
            self.last_mark_time = timestamp(s, off + 1)
        if self.variant['exit_mode'] == 'ma10_max63':
            return self.ma_decision(s)
        return True

    def snapshot(self, s, status='observed'):
        held = ZERO if self.position is None else self.position['quantity'] * (
            self.last_mark if self.last_mark is not None else self.position['entry_reference'])
        unsettled = sum((r['amount'] for r in self.receivables), ZERO)
        equity = self.cash + unsettled + held
        liquidation = self.cash + unsettled + held * (1 - self.cost)
        close_time = timestamp(s, minutes(s))
        mark_fresh = self.position is None or self.last_mark_time == close_time
        age = None
        if self.position is not None and self.last_mark_time is not None:
            age = int((datetime.fromisoformat(close_time) - datetime.fromisoformat(self.last_mark_time)).total_seconds() / 60)
        certified = not bool(self.failure) and mark_fresh
        if certified:
            self.drawdown_snapshots_evaluated += 1
            self.peak = max(self.peak, liquidation)
            self.max_dd = max(self.max_dd, 1 - liquidation / self.peak)
            for value in self.milestones:
                if liquidation >= dec(value) and self.milestones[value] is None:
                    self.milestones[value] = s['date']
        else:
            self.drawdown_snapshots_excluded += 1
        self.rows.append({
            'date': s['date'], 'status': status if mark_fresh or self.failure else 'stale_last_observed_mark',
            'cash_settled': self.cash, 'cash_unsettled': unsettled, 'marked_position': held,
            'last_observed_equity': equity, 'last_observed_liquidation_equity': liquidation,
            'mark_time': self.last_mark_time, 'mark_fresh': mark_fresh, 'mark_stale_minutes': age,
            'quantity': 0 if self.position is None else self.position['quantity'],
            'symbol': None if self.position is None else self.position['symbol'],
            'equity_certified': certified,
            'certification_scope': 'price timestamp freshness and account arithmetic only; conditional event model is not execution certification',
            'conditional_gap_minutes_to_date': sum(g['minute_count'] for g in self.gap_spans),
            'drawdown_and_milestone_eligible': certified})

    def finish(self, kind, conditional=False):
        result = super().finish(kind, True)
        certified = bool(self.rows) and self.rows[-1]['equity_certified']
        if not certified:
            result['ending_equity'] = None
        result.update({
            'data_mode': self.mode,
            'model_is_conditional_aggregate_event_sensitivity': True,
            'model_is_forward_or_independent_validation': False,
            'terminal_equity_certified': certified,
            'terminal_certification_scope': 'fresh observed mark and arithmetic only, subject to all event-series, price and input assumptions',
            'gap_spans': public(self.gap_spans),
            'skipped_minute_count': sum(g['minute_count'] for g in self.gap_spans),
            'daily_drawdown_evaluated_snapshot_count': self.drawdown_snapshots_evaluated,
            'daily_drawdown_excluded_snapshot_count': self.drawdown_snapshots_excluded,
            'observed_fresh_daily_liquidation_drawdown': str(self.max_dd) if self.drawdown_snapshots_evaluated else None,
            'milestone_scope': 'model end-of-day liquidation mark with fresh price only; not settled cash, broker realization or achieved live goal',
            'failure_does_not_certify_round_ruin_or_restart': True,
            'drawdown_scope': 'fresh completed observed daily liquidation snapshots only; excludes stale or unresolved snapshots, not intraday worst path or full broker-liquidation drawdown',
            'bar_absence_policy': 'exact registered coordinate evidence only' if self.mode == 'evidence_gated' else 'complete regular-session request required; assumes returned aggregate event-series completeness',
            'stop_model': 'bar open below stop uses observed open; low touch uses preset stop and cost; missing bars do not create prices or certify broker stop behavior',
        })
        return result


def simulate_case(candidate, variant, market, daily, calendar, actions,
                  cutoff='2026-10-05', mode='evidence_gated', gap_evidence=None,
                  verified_symbol_sessions=None):
    e = EventEngine(variant, market, daily, calendar, actions, cutoff, mode,
                    gap_evidence, verified_symbol_sessions)
    date = candidate['date']
    sessions = [s for s in e.calendar if date <= s['date'] <= cutoff]
    if not sessions or sessions[0]['date'] != date:
        raise ValueError('entry date absent from calendar')
    for index, s in enumerate(sessions):
        e.settle(s)
        if index == 0:
            e.enter(candidate, s)
        elif e.position is not None:
            e.hold(s)
        e.snapshot(s, 'incomplete' if e.failure else 'observed')
        if e.failure or e.position is None:
            break
    result = e.finish('independent_hypothetical_500_case')
    result['candidate_id'] = candidate_id(candidate)
    result['news_not_used_for_case_selection'] = True
    return result


def simulate_portfolio(candidates, variant, market, daily, calendar, actions, evidence,
                       coverage=None, start_date='2026-01-02', cutoff='2026-10-05',
                       mode='evidence_gated', gap_evidence=None, verified_symbol_sessions=None):
    """Original full-registry guards: no reinterpretation of missing news."""
    e = EventEngine(variant, market, daily, calendar, actions, cutoff, mode,
                    gap_evidence, verified_symbol_sessions)
    byday = {}
    for c in candidates:
        byday.setdefault(c['date'], []).append(c)
    conditional = coverage is None or not bool(coverage.get('cohort_complete', False))
    for s in e.calendar:
        date = s['date']
        if not start_date <= date <= cutoff:
            continue
        e.settle(s)
        had_position = e.position is not None
        if had_position:
            e.hold(s)
        else:
            cov = (coverage or {}).get('days', {}).get(date, {})
            if cov.get('initial_screen_complete') is False:
                e.incomplete('initial_screen_coverage_unknown', s)
            else:
                confirmed, unresolved = [], []
                for c in byday.get(date, []):
                    ev = evidence.get(candidate_id(c), {})
                    cid = candidate_id(c)
                    if is_market_impossible(c) or evidence_impossible(ev):
                        e.decisions.append({'candidate_id': cid, 'date': date,
                                            'status': 'excluded_by_verified_gate',
                                            'market_gate_status': c.get('market_gate_status')})
                        continue
                    ready = c.get('market_gate_status') == 'signal_and_entry_reference_ready' and isinstance(c.get('signal'), dict)
                    (confirmed if ready and evidence_true(ev) else unresolved).append(c)
                confirmed.sort(key=intent_key)
                selected = confirmed[0] if confirmed else None
                blockers = [candidate_id(c) for c in unresolved if selected is None or not c.get('signal') or intent_key(c) <= intent_key(selected)]
                if blockers:
                    e.incomplete('unresolved_candidate_precedence', s, 0,
                                 blocking_candidates=blockers,
                                 selected_candidate=None if selected is None else candidate_id(selected))
                elif selected:
                    e.enter(selected, s)
                else:
                    e.decisions.append({'date': date, 'status': 'no_confirmed_eligible_intent'})
        if had_position:
            e.decisions.append({'date': date, 'status': 'no_entry_position_or_exit_day',
                                'candidate_ids': [candidate_id(c) for c in byday.get(date, [])]})
        e.snapshot(s, 'incomplete' if e.failure else 'observed')
        if e.failure:
            break
    result = e.finish('continuous_single_500_portfolio', conditional)
    result['candidate_count_supplied'] = len(candidates)
    result['candidate_register'] = [
        {'candidate_id': candidate_id(c), 'date': c['date'], 'symbol': c['symbol'],
         'market_gate_status': c.get('market_gate_status'),
         'news_pass': evidence.get(candidate_id(c), {}).get('pass_registered_news_gate'),
         'after_termination': bool(e.failure and c['date'] > e.failure['date'])}
        for c in candidates]
    return result


def simulate_cohort_portfolio(candidates, variant, market, daily, calendar, actions,
                              cohort_name, start_date='2026-01-02', cutoff='2026-10-05',
                              mode='evidence_gated', gap_evidence=None,
                              verified_symbol_sessions=None):
    """Financed diagnostic of supplied frozen retrospective members only.

    Caller must publish the membership lock and original full candidate registry.
    This wrapper never mutates or invents original news/market evidence.
    """
    if cohort_name not in ('market73', 'verified_news32'):
        raise ValueError('unregistered retrospective cohort')
    ids = [candidate_id(c) for c in candidates]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate cohort member')
    for c in candidates:
        if c.get('market_gate_status') != 'signal_and_entry_reference_ready' or not isinstance(c.get('signal'), dict):
            raise ValueError('retrospective member lacks registered market intent')
    e = EventEngine(variant, market, daily, calendar, actions, cutoff, mode,
                    gap_evidence, verified_symbol_sessions)
    byday = {}
    for c in candidates:
        byday.setdefault(c['date'], []).append(c)
    for s in e.calendar:
        date = s['date']
        if not start_date <= date <= cutoff:
            continue
        e.settle(s)
        had_position = e.position is not None
        rows = sorted(byday.get(date, []), key=intent_key)
        if had_position:
            e.hold(s)
            e.decisions.append({'date': date, 'status': 'no_entry_position_or_exit_day',
                                'candidate_ids': [candidate_id(c) for c in rows]})
        elif rows:
            e.decisions.append({'date': date, 'status': 'retrospective_frozen_membership_selection',
                                'ranked_candidate_ids': [candidate_id(c) for c in rows],
                                'selected_candidate_id': candidate_id(rows[0]),
                                'source_news_evidence_not_overridden': True})
            e.enter(rows[0], s)
        else:
            e.decisions.append({'date': date, 'status': 'no_frozen_cohort_member_intent'})
        e.snapshot(s, 'incomplete' if e.failure else 'observed')
        if e.failure:
            break
    result = e.finish('retrospective_cohort_continuous_single_500_portfolio', True)
    result.update(cohort_name=cohort_name,
                  retrospective_membership_condition=True,
                  historical_implementability_claim=False,
                  candidate_count_supplied=len(candidates),
                  supplied_membership=ids,
                  original_news_evidence_not_overridden=True)
    return result
