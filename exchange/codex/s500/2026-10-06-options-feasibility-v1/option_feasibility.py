"""Pure option contract/quote feasibility arithmetic; no I/O, orders or strategy PnL.

Historical discovery and present provider metadata are distinct. A premium
multiplier is read only from the explicit multiplier field, never from size,
deliverables or an OCC-style symbol. All fee reserves are hypothetical.
"""
import re
from datetime import datetime, date, timezone
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from zoneinfo import ZoneInfo

ET = ZoneInfo('America/New_York')
ZERO = Decimal(0)
DEFAULT_POLICY = {
    'budget': '500', 'roundtrip_reserves_per_contract': ['0', '0.10', '1'],
    'max_age_seconds': '5', 'max_relative_spread_mid': '0.10',
    'study_cutoff_date': '2026-10-05',
}
OPTION_SYMBOL = re.compile(r'^(?P<root>[A-Z0-9]{1,8})(?P<expiry>\d{6})(?P<right>[CP])(?P<strike>\d{8})$')
UNDERLYING_SYMBOL = re.compile(r'^[A-Z0-9][A-Z0-9.\-]{0,15}$')
RFC3339 = re.compile(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$')


def number(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError('invalid numeric type')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('invalid number') from None
    if not result.is_finite():
        raise ValueError('nonfinite number')
    return result


def maybe_number(value, positive=False, nonnegative=False):
    try:
        value = number(value)
        if positive and value <= 0 or nonnegative and value < 0:
            return None
        return value
    except ValueError:
        return None


def exact_timestamp_ns(value):
    """RFC3339 offset-aware timestamp to integer ns, without float truncation."""
    match = RFC3339.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise ValueError('timestamp must be RFC3339 with at most nine fractional digits')
    base, fraction, offset = match.groups()
    if offset != 'Z' and (int(offset[1:3]) > 23 or int(offset[4:6]) > 59):
        raise ValueError('invalid timezone offset')
    # Validation (including calendar and offset ranges) is delegated only for
    # integer seconds; the full fractional lexeme is retained separately.
    instant = datetime.fromisoformat(base + ('+00:00' if offset == 'Z' else offset))
    elapsed = instant.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    seconds = elapsed.days * 86400 + elapsed.seconds
    return seconds * 1_000_000_000 + int((fraction or '').ljust(9, '0') or '0')


def safe_date(value):
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            return None
        return value
    except ValueError:
        return None


def public(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: public(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [public(v) for v in value]
    return value


def get_policy(policy=None):
    result = {**DEFAULT_POLICY, **(policy or {})}
    budget = number(result['budget'])
    reserves = [number(v) for v in result['roundtrip_reserves_per_contract']]
    age = number(result['max_age_seconds'])
    spread = number(result['max_relative_spread_mid'])
    if budget != 500 or reserves != [Decimal(0), Decimal('.10'), Decimal(1)]:
        raise ValueError('unregistered budget or reserve scenarios')
    if age != 5 or spread != Decimal('.10'):
        raise ValueError('unregistered quote-quality threshold')
    if safe_date(result['study_cutoff_date']) is None:
        raise ValueError('invalid study cutoff date')
    result.update(budget=budget, roundtrip_reserves_per_contract=reserves,
                  max_age_seconds=age, max_relative_spread_mid=spread)
    return result


def sanitized_contract(contract):
    """Nested allowlist; excludes contract/asset UUIDs and arbitrary raw fields."""
    if not isinstance(contract, dict):
        return {}
    result = {}
    for field in ('symbol', 'underlying_symbol', 'root_symbol'):
        value = contract.get(field)
        pattern = OPTION_SYMBOL if field == 'symbol' else UNDERLYING_SYMBOL
        result[field] = value if isinstance(value, str) and pattern.fullmatch(value) else None
    for field, allowed in (('status', ('active', 'inactive')), ('type', ('call', 'put')),
                           ('style', ('american', 'european'))):
        result[field] = contract.get(field) if contract.get(field) in allowed else None
    result['tradable'] = contract.get('tradable') if type(contract.get('tradable')) is bool else None
    result['expiration_date'] = safe_date(contract.get('expiration_date'))
    for field in ('strike_price', 'multiplier', 'size', 'open_interest', 'close_price'):
        parsed = maybe_number(contract.get(field))
        result[field] = None if parsed is None else str(parsed)
    for field in ('open_interest_date', 'close_price_date'):
        result[field] = safe_date(contract.get(field))
    deliverables = contract.get('deliverables')
    result['deliverables'] = None
    if isinstance(deliverables, list):
        cleaned = []
        for raw in deliverables:
            if not isinstance(raw, dict):
                cleaned.append({'malformed_component': True})
                continue
            row = {}
            row['type'] = raw.get('type') if raw.get('type') in ('equity', 'cash') else None
            value = raw.get('symbol')
            row['symbol'] = value if isinstance(value, str) and UNDERLYING_SYMBOL.fullmatch(value) else None
            for field in ('amount', 'allocation_percentage'):
                value = maybe_number(raw.get(field))
                row[field] = None if value is None else str(value)
            # Fixed-format codes only, not unfiltered arbitrary provider text.
            for field in ('settlement_type', 'settlement_method'):
                value = raw.get(field)
                row[field] = value if isinstance(value, str) and re.fullmatch(r'[A-Z0-9+\-]{1,12}', value) else None
            row['delayed_settlement'] = raw.get('delayed_settlement') if type(raw.get('delayed_settlement')) is bool else None
            cleaned.append(row)
        result['deliverables'] = cleaned
    return result


def analyze_contract(contract, observation=None, policy=None):
    p = get_policy(policy)
    observation = observation or {}
    clean = sanitized_contract(contract)
    multiplier = maybe_number(clean.get('multiplier'), positive=True)
    size = maybe_number(clean.get('size'), positive=True)
    strike = maybe_number(clean.get('strike_price'), positive=True)
    expiry = clean.get('expiration_date')
    received = observation.get('received_at')
    local_day = None
    try:
        exact_timestamp_ns(received)
        match = RFC3339.fullmatch(received)
        base, _, offset = match.groups()
        local_day = datetime.fromisoformat(base + ('+00:00' if offset == 'Z' else offset)).astimezone(ET).date().isoformat()
    except (ValueError, TypeError, AttributeError):
        pass
    components = clean.get('deliverables')
    deliverables_present = isinstance(components, list) and bool(components)
    standard = bool(
        multiplier == 100 and size == 100 and deliverables_present and len(components) == 1 and
        components[0].get('type') == 'equity' and
        components[0].get('symbol') == clean.get('underlying_symbol') and
        components[0].get('symbol') is not None and
        maybe_number(components[0].get('amount')) == 100 and
        maybe_number(components[0].get('allocation_percentage')) == 100 and
        components[0].get('delayed_settlement') is False and
        components[0].get('settlement_type') is not None and
        components[0].get('settlement_method') is not None)
    if standard:
        structure = 'standard_equity_100_metadata_consistent'
    elif deliverables_present:
        structure = 'nonstandard_or_unresolved_deliverables'
    else:
        structure = 'deliverables_unknown'
    explicit_adjusted_proof = (isinstance(contract, dict) and
                               contract.get('adjusted_deliverable_proof_verified') is True)
    encoded = OPTION_SYMBOL.fullmatch(clean.get('symbol') or '')
    encoded_matches = None
    if encoded is not None and expiry is not None and strike is not None and clean.get('type'):
        encoded_matches = (encoded['expiry'] == date.fromisoformat(expiry).strftime('%y%m%d') and
                           encoded['right'] == ('C' if clean['type'] == 'call' else 'P') and
                           Decimal(encoded['strike']) / 1000 == strike and
                           (clean.get('root_symbol') is None or encoded['root'] == clean['root_symbol']))
    identity_complete = all((clean.get('symbol'), clean.get('underlying_symbol'), expiry,
                             clean.get('type'), clean.get('style'), strike is not None,
                             multiplier is not None, encoded_matches is True))
    reasons = []
    if not identity_complete:
        reasons.append('contract_identity_or_explicit_multiplier_unknown')
    if not standard:
        reasons.append('standard_deliverable_specification_not_certified')
    if encoded_matches is not True:
        reasons.append('encoded_symbol_and_contract_metadata_not_consistent')
    if local_day is None:
        reasons.append('observation_date_unknown_expiry_validity_unresolved')
    if clean.get('status') != 'active' or clean.get('tradable') is not True:
        reasons.append('current_metadata_not_active_and_tradable')
    expired_observation = None if expiry is None or local_day is None else expiry < local_day
    if expired_observation is True:
        reasons.append('expired_before_observation_date')
    return {
        'contract': clean, 'contract_structure': structure,
        'explicit_premium_multiplier': None if multiplier is None else str(multiplier),
        'multiplier_source': 'explicit_provider_multiplier_field' if multiplier is not None else 'unknown_not_inferred_from_size_or_symbol',
        'deliverable_size_distinct_from_premium_multiplier': True,
        'standard_metadata_consistent': standard,
        'encoded_symbol_matches_expiry_right_strike_and_declared_root': encoded_matches,
        'provider_asserted_adjusted_proof': explicit_adjusted_proof,
        'deliverables_independently_verified': False,
        'identity_complete_for_arithmetic': bool(identity_complete),
        'current_contract_metadata_eligible': not reasons,
        'current_contract_metadata_reasons': reasons,
        'metadata_temporal_scope': 'retrieved current provider metadata, not a historical as-of contract master',
        'historical_membership_or_listability_verified': False,
        'observation_date_et': local_day,
        'expiration_on_observation_date': None if expiry is None or local_day is None else expiry == local_day,
        'expired_before_observation_date': expired_observation,
        'expired_before_study_cutoff_date': None if expiry is None else expiry < p['study_cutoff_date'],
        'expiration_on_study_cutoff_date': None if expiry is None else expiry == p['study_cutoff_date'],
        'last_trading_time_or_exercise_cutoff_verified': False,
        'exercise_assignment_or_residual_share_funding_modeled': False,
        'delta': None, 'delta_status': 'unknown_not_inferred_from_strike_or_moneyness',
    }


def affordability(multiplier, reference_ask, reference_bid, quote_sizes, units_verified, policy):
    """Illustrative, independent of quote eligibility; never models actual fills."""
    ask = maybe_number(reference_ask, positive=True)
    bid = maybe_number(reference_bid, positive=True)
    multiplier = maybe_number(multiplier, positive=True)
    ask_size = maybe_number((quote_sizes or {}).get('ask_size'), positive=True)
    bid_size = maybe_number((quote_sizes or {}).get('bid_size'), positive=True)
    if ask_size is not None and ask_size != ask_size.to_integral_value():
        ask_size = None
    if bid_size is not None and bid_size != bid_size.to_integral_value():
        bid_size = None
    scenarios = []
    for reserve in policy['roundtrip_reserves_per_contract']:
        base = None if ask is None or multiplier is None else ask * multiplier
        total = None if base is None else base + reserve
        quantity = None if total is None else int((policy['budget'] / total).to_integral_value(rounding=ROUND_FLOOR))
        # A crossed market is invalid. Do not turn its negative spread into a
        # displayed arbitrage gain in this illustrative friction calculation.
        friction = None if base is None or bid is None or bid > ask else (ask-bid)*multiplier + reserve
        ask_cap = None if quantity is None or not units_verified or ask_size is None else min(quantity, int(ask_size))
        two_sided_cap = None if ask_cap is None or bid_size is None else min(ask_cap, int(bid_size))
        scenarios.append({
            'roundtrip_reserve_per_contract': reserve,
            'reserve_is_research_hypothesis_not_actual_broker_fee': True,
            'budget': policy['budget'], 'premium_per_contract': base,
            'one_contract_total_including_reserve': total,
            'one_contract_affordable': None if total is None else total <= policy['budget'],
            'whole_contracts_budget_only': quantity,
            'budget_used_including_reserve': None if quantity is None else quantity*total,
            'budget_remaining': None if quantity is None else policy['budget']-quantity*total,
            'displayed_ask_size_capped_quantity': ask_cap,
            'displayed_two_sided_size_capped_quantity': two_sided_cap,
            'equal_instant_bid_liquidation_friction_one_contract': friction,
            'friction_fraction_of_one_contract_total': None if friction is None else friction/total,
            'arithmetic_only_not_order_or_actual_return': True,
            'displayed_size_not_guaranteed_execution_capacity': True,
        })
    return public(scenarios)


def evaluate_quote(contract, quote, observation, policy=None):
    """Analyze the supplied latest quote; never fall back to an earlier record."""
    p = get_policy(policy)
    contract_analysis = analyze_contract(contract, observation, policy)
    observation = observation or {}
    raw = quote if isinstance(quote, dict) else {}
    bid = maybe_number(raw.get('bp'), positive=True)
    ask = maybe_number(raw.get('ap'), positive=True)
    bid_size = maybe_number(raw.get('bs'), positive=True)
    ask_size = maybe_number(raw.get('as'), positive=True)
    sizes_integer = (bid_size is not None and ask_size is not None and
                     bid_size == bid_size.to_integral_value() and ask_size == ask_size.to_integral_value())
    uncrossed = bid is not None and ask is not None and ask >= bid
    midpoint = (bid+ask)/2 if uncrossed else None
    relative_spread = None if midpoint is None else (ask-bid)/midpoint
    spread_pass = None if midpoint is None else ask-bid <= midpoint*p['max_relative_spread_mid']
    quote_ns = received_ns = age_ns = None
    try:
        quote_ns = exact_timestamp_ns(raw.get('t'))
        received_ns = exact_timestamp_ns(observation.get('received_at'))
        age_ns = received_ns - quote_ns
    except (ValueError, TypeError):
        pass
    fresh = None if age_ns is None else 0 <= age_ns <= int(p['max_age_seconds']*1_000_000_000)
    condition = raw.get('c')
    condition_supported = isinstance(condition, str) and condition in (' ', 'A')
    units_verified = (observation.get('quote_size_units') == 'contracts' and
                      observation.get('size_units_verified') is True)
    condition_verified = observation.get('condition_mapping_verified') is True
    supplied_quote_symbol = observation.get('quote_symbol', raw.get('symbol'))
    symbol_join_matches = (isinstance(supplied_quote_symbol, str) and
                           supplied_quote_symbol == contract_analysis['contract'].get('symbol'))
    criteria = {
        'quote_record_present': isinstance(quote, dict) and bool(quote),
        'supplied_quote_symbol_matches_contract': symbol_join_matches,
        'feed_is_opra': observation.get('feed') == 'opra',
        'real_provider_bid_ask': observation.get('real_provider_bid_ask') is True,
        'market_open_at_observation': observation.get('market_open_at_observation') is True,
        'finite_positive_uncrossed_bid_ask': uncrossed,
        'positive_integer_sizes': sizes_integer,
        'quote_condition_blank_or_A': condition_supported,
        'quote_condition_provider_mapping_verified': condition_verified,
        'quote_sizes_verified_in_contract_units': units_verified,
        'age_nonnegative_and_at_most_five_seconds': fresh,
        'spread_over_midpoint_at_most_ten_percent': spread_pass,
        'current_contract_metadata_eligible': contract_analysis['current_contract_metadata_eligible'],
    }
    eligible = all(value is True for value in criteria.values())
    clean_quote = {
        'timestamp': raw.get('t') if isinstance(raw.get('t'), str) and RFC3339.fullmatch(raw['t']) else None,
        'bid': None if bid is None else str(bid), 'ask': None if ask is None else str(ask),
        'bid_size': None if bid_size is None else str(bid_size),
        'ask_size': None if ask_size is None else str(ask_size),
        'condition': condition if isinstance(condition, str) and re.fullmatch(r'[ A-Z]{0,2}', condition) else None,
        'bid_exchange': raw.get('bx') if isinstance(raw.get('bx'), str) and re.fullmatch(r'[A-Z0-9]{1,4}', raw['bx']) else None,
        'ask_exchange': raw.get('ax') if isinstance(raw.get('ax'), str) and re.fullmatch(r'[A-Z0-9]{1,4}', raw['ax']) else None,
    }
    scenarios = affordability(contract_analysis['explicit_premium_multiplier'], raw.get('ap'), raw.get('bp'),
                              {'ask_size': raw.get('as'), 'bid_size': raw.get('bs')}, units_verified, p)
    return public({
        'contract_analysis': contract_analysis, 'quote': clean_quote,
        'quote_selection_policy': 'supplied latest record only; no fallback to older normal or cheaper quote',
        'feed': observation.get('feed') if observation.get('feed') in ('opra', 'indicative', 'delayed', 'unknown') else 'unknown',
        'quote_event_time_ns': quote_ns, 'observation_received_time_ns': received_ns,
        'age_nanoseconds': age_ns, 'age_seconds': None if age_ns is None else Decimal(age_ns)/1_000_000_000,
        'midpoint': midpoint, 'spread_fraction_of_midpoint': relative_spread,
        'quality_criteria': criteria, 'primary_quote_quality_eligible': eligible,
        'quality_reasons': [name for name, value in criteria.items() if value is not True],
        'affordability_scenarios': scenarios,
        'affordability_independent_of_quote_quality': True,
        'affordable_primary_quote_available': eligible and any(row['one_contract_affordable'] is True for row in scenarios),
        'historical_decision_time_receipt_or_availability_verified': False,
        'market_clock_source_independently_verified': False,
        'quote_eligibility_is_not_order_fill_or_strategy_edge': True,
        'exercise_and_liquidation_readiness_verified': False,
        'actual_orders_sent': 0, 'strategy_returns_computed': False,
    })


def evaluate_trade_price_proxy(contract, price, observation=None, policy=None):
    """Whole-contract arithmetic from a trade/close proxy, never ask liquidity."""
    p = get_policy(policy)
    analysis = analyze_contract(contract, observation, policy)
    return {
        'contract_analysis': analysis,
        'reference_price': public(maybe_number(price, positive=True)),
        'reference_kind': 'historical_trade_or_bar_price_proxy_not_bid_ask',
        'affordability_scenarios': affordability(analysis['explicit_premium_multiplier'], price, None, {}, False, p),
        'primary_quote_quality_eligible': False,
        'quote_age_spread_size_or_exit_coverage_verified': False,
        'historical_decision_time_receipt_or_availability_verified': False,
        'is_executable_premium_reference': False,
        'actual_orders_sent': 0, 'strategy_returns_computed': False,
    }
