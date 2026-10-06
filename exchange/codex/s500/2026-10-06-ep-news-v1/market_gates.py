"""Pure, causal EP market gates. No news selection, network, orders, exits or wealth."""
import copy
import re
from datetime import date as Date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

NY = ZoneInfo('America/New_York')
REFERENCE_FIELDS = ('date', 'symbol', 'name_current_directory', 'prior_session',
    'prior_close_adjusted', 'daily_open_reference', 'opening_gap_fraction',
    'prior20_median_dollar_volume', 'prior20_mean_adjusted_daily_volume',
    'applied_splits', 'current_classified_etf_or_test_issue',
    'current_classification_unknown', 'stock_research_eligible_initial',
    'regular_open_verification')


def number(value):
    """Interpret JSON numeric lexemes without a floating epsilon or hidden rounding."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def get_bar(series, offset, start, opening_only=False):
    """Return validated source fields; duplicate offset keys and wrong dates stay unknown."""
    if not isinstance(series, dict):
        return None, 'invalid_series_type'
    if offset in series and str(offset) in series:
        return None, 'ambiguous_duplicate_offset_keys'
    raw = series.get(offset, series.get(str(offset)))
    if not isinstance(raw, dict):
        return None, 'missing_bar'
    try:
        if not isinstance(raw.get('t'),str) or not re.fullmatch(
                r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00(?:\.0{1,9})?(?:Z|[+-]\d{2}:\d{2})',raw['t']):
            return None, 'not_exact_minute_source_timestamp'
        moment = datetime.fromisoformat(raw['t'].replace('Z', '+00:00'))
        if moment.tzinfo is None or moment != start + timedelta(minutes=offset):
            return None, 'timestamp_not_current_session_exact_minute'
    except (AttributeError, KeyError, TypeError, ValueError):
        return None, 'missing_or_invalid_source_timestamp'
    keys = ('o',) if opening_only else ('o', 'h', 'l', 'c', 'v')
    values = {key:number(raw.get(key)) for key in keys}
    if any(value is None for value in values.values()):
        return None, 'missing_or_nonfinite_numeric_field'
    if values['o'] <= 0:
        return None, 'nonpositive_open'
    if not opening_only:
        if any(values[k] <= 0 for k in ('h', 'l', 'c')) or values['v'] < 0:
            return None, 'invalid_price_or_volume'
        if not values['l'] <= min(values['o'],values['c']) <= max(values['o'],values['c']) <= values['h']:
            return None, 'inconsistent_ohlc'
    return {'raw':raw, 'value':values}, None


def evaluate_candidate(candidate, series):
    """Evaluate one isolated candidate using supplied prior references and current minutes.

    Series offsets are regular-session minutes (0 = 09:30), with source timestamps.
    Input split-adjusted references are preserved and not adjusted a second time.
    This function does not choose among competing stocks or interpret news status.
    """
    result = {'date':candidate.get('date'), 'symbol':candidate.get('symbol'),
        'market_gate_status':'candidate_reference_unknown', 'market_gate_pass':None,
        'opening30':None, 'signal':None, 'entry_reference':None,
        'source_candidate_reference':{k:copy.deepcopy(candidate[k]) for k in REFERENCE_FIELDS if k in candidate},
        'reference_fields_absent':[k for k in REFERENCE_FIELDS if k not in candidate],
        'comparison_policy':'Decimal(str(JSON numeric value)); exact inclusive thresholds; no float tolerance or rounding.',
        'scope':'isolated_candidate_market_gate_only; no_news_join_or_cross_candidate_selection',
        'coverage':{'opening30_expected':30, 'opening30_observed_valid':0,
                    'signal_window_expected_max':60, 'signal_minutes_observed':0},
        'issues':[], 'observed_signal_bars':[], 'orders_sent':0,
        'returns_or_wealth_computed':False}

    def finish(status, passed=None, reason=None):
        result['market_gate_status']=status; result['market_gate_pass']=passed
        if reason:result['issues'].append(reason)
        return result

    try:
        day = Date.fromisoformat(candidate['date'])
        if day.isoformat() != candidate['date'] or not isinstance(candidate.get('symbol'),str) or not candidate['symbol']:
            raise ValueError
        start = datetime(day.year,day.month,day.day,9,30,tzinfo=NY)
    except (KeyError, TypeError, ValueError):
        return finish('candidate_reference_unknown',reason='invalid_date_or_symbol')
    try:
        prior_day=Date.fromisoformat(candidate['prior_session'])
        if prior_day.isoformat()!=candidate['prior_session'] or prior_day>=day:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return finish('candidate_reference_unknown',reason='missing_or_noncausal_prior_session')
    required = ('prior_close_adjusted','prior20_median_dollar_volume','prior20_mean_adjusted_daily_volume')
    prior = {k:number(candidate.get(k)) for k in required}
    invalid = [k for k,v in prior.items() if v is None or v <= 0]
    if invalid:
        return finish('candidate_reference_unknown',reason={'invalid_prior_fields':invalid})
    result['prior_liquidity_gates']={'prior_close_minimum':'2', 'median_dollar_volume_minimum':'10000000',
        'prior_close_pass':prior['prior_close_adjusted']>=Decimal('2'),
        'median_dollar_volume_pass':prior['prior20_median_dollar_volume']>=Decimal('10000000')}
    if not all(result['prior_liquidity_gates'][k] for k in ('prior_close_pass','median_dollar_volume_pass')):
        return finish('prior_liquidity_gate_failed',False)
    opening=[]; holes=[]
    for offset in range(30):
        bar,error = get_bar(series,offset,start)
        if error:holes.append({'offset':offset,'reason':error})
        else:opening.append(bar)
    result['coverage']['opening30_observed_valid']=len(opening)
    if holes:
        return finish('opening30_data_unknown',reason={'opening30_gaps':holes})
    volume=sum((b['value']['v'] for b in opening),Decimal(0))
    open_price=opening[0]['value']['o']; high=max(b['value']['h'] for b in opening)
    low=min(b['value']['l'] for b in opening); close_price=opening[-1]['value']['c']
    prior_close=prior['prior_close_adjusted']; mean_volume=prior['prior20_mean_adjusted_daily_volume']
    # Cross multiplication makes exactly 10% inclusive without relying on division precision.
    gap_pass = open_price >= prior_close * Decimal('1.10')
    volume_pass = volume >= mean_volume
    daily_ref=number(candidate.get('daily_open_reference'))
    result['opening30']={'available_at_et':(start+timedelta(minutes=30)).isoformat(),
        'bar_count':30, 'open':float(open_price),'high':float(high),'low':float(low),'close':float(close_price),
        'volume':int(volume) if volume==volume.to_integral_value() else float(volume),
        'gap_fraction':float(open_price/prior_close-1),'volume_ratio':float(volume/mean_volume),
        'gap_gate_pass':gap_pass,'volume_gate_pass':volume_pass,
        'daily_reference_open_matches_regular_open':None if daily_ref is None else daily_ref==open_price,
        'exact_values':{'regular_open':str(open_price),'or30_high':str(high),'or30_low':str(low),
            'or30_close':str(close_price),'volume':str(volume),'prior_close_adjusted':str(prior_close),
            'gap_threshold_open':str(prior_close*Decimal('1.10')),'prior20_mean_adjusted_daily_volume':str(mean_volume)},
        'source_timestamps':[b['raw']['t'] for b in opening]}
    if not gap_pass:
        return finish('opening_gap_gate_failed',False)
    if not volume_pass:
        return finish('opening30_volume_gate_failed',False)
    for offset in range(30,90):
        bar,error=get_bar(series,offset,start)
        if error:
            return finish('signal_data_unknown',reason={'offset':offset,
                'observed_at_et':(start+timedelta(minutes=offset+1)).isoformat(),'reason':error})
        observed_close=bar['value']['c']; breakout=observed_close>high
        result['observed_signal_bars'].append({'offset':offset,'source_timestamp':bar['raw']['t'],
            'completed_at_et':(start+timedelta(minutes=offset+1)).isoformat(),
            'close':float(observed_close),'close_exact':str(observed_close),'above_or30_high':breakout})
        result['coverage']['signal_minutes_observed']+=1
        if not breakout:continue
        entry_offset=offset+2; completed=start+timedelta(minutes=offset+1)
        entry_time=start+timedelta(minutes=entry_offset)
        result['signal']={'minute_offset':offset,'source_timestamp':bar['raw']['t'],
            'completed_at_et':completed.isoformat(),'completed_at_utc':completed.astimezone(timezone.utc).isoformat(),
            'close':float(observed_close),'close_exact':str(observed_close),
            'or30_high':float(high),'first_signal':True,
            'all_prior_signal_bars_observed_without_breakout':True,
            'planned_entry_offset':entry_offset,'planned_entry_time_et':entry_time.isoformat(),
            'planned_entry_time_utc':entry_time.astimezone(timezone.utc).isoformat()}
        entry,error=get_bar(series,entry_offset,start,opening_only=True)
        if error:
            return finish('entry_reference_unknown',reason={'entry_offset':entry_offset,'reason':error})
        entry_open=entry['value']['o']; gap_through=entry_open<=low
        result['entry_reference']={'minute_offset':entry_offset,'time_et':entry_time.isoformat(),
            'time_utc':entry_time.astimezone(timezone.utc).isoformat(),
            'source_timestamp':entry['raw']['t'],'open':float(entry_open),'open_exact':str(entry_open),
            'stop_or30_low':float(low),'stop_exact':str(low),'distance_to_stop':float(entry_open-low),
            'gap_through_stop':gap_through,'modeled_fill_claimed':False,
            'exposure_status':'unresolved_gap_through_entry_stop' if gap_through else 'reference_only_no_order'}
        if gap_through:
            return finish('incomplete_gap_through_entry_stop',reason='entry open at/below predeclared OR30 low; do not assume retrospective cancellation or favorable stop fill')
        return finish('signal_and_entry_reference_ready',True)
    return finish('no_breakout_before_11',False)
