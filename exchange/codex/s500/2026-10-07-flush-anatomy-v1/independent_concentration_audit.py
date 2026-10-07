"""Independent full concentration and payoff audit of the frozen anatomy stage.

No production modules are imported. Primary signal outcomes come directly from
original ancestor results; other target returns and original peer excesses stay
fixed during every leave-out. The payoff oracle is carried forward verbatim
from the prior independent audit, with its originating file SHA recorded.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from fractions import Fraction
from itertools import product
from pathlib import Path
import gzip
import hashlib
import json

ROOT = Path(__file__).resolve().parent
PARENT = ROOT.parent / 's500-relative-flush-20261007'
OLD = ROOT.parent / 's500-flush-rebound-20261007'
FROZEN_DESIGN = '7768a193c9fcddd4b846680ad278e23d4e5ad25bb57d461a01e30f8019a901be'
ORACLE_SOURCE_SHA256 = '1876a96b7cd7a43519f400105371e793b5a0319da1554593fceabb107e22b639'
TOLERANCE = Fraction(1, 10 ** 45)
PRIMARY = ('0.02', 'REBOUND', '60m', 10)
VARIANTS = tuple(product(('0.02', '0.03'), ('FIXED', 'REBOUND'), ('30m', '60m', 'close_minus_10m'), (5, 10, 25)))
GROUPS = ('market_down', 'market_not_down', 'unknown')
METRICS = ('net_return', 'matched_excess')
COUNTS = Counter()
ISSUES = []
MAX_NORMALIZED_ERROR = Fraction(0)

def digest(path):
    hasher = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1 << 20), b''):
            hasher.update(chunk)
    return hasher.hexdigest()

def read(path):
    return json.loads(path.read_text())

def lines(path):
    with gzip.open(path, 'rt') as source:
        for line in source:
            yield json.loads(line)

def require(condition, label):
    if not condition:
        raise AssertionError(label)

def fraction(value):
    require(isinstance(value, str), 'finite_decimal_string_required')
    parsed = Decimal(value)
    require(parsed.is_finite(), 'finite_return_required')
    return Fraction(parsed)

def divide(numerator, denominator):
    return Fraction(numerator, denominator) if denominator else None

def interpolation(values, numerator, denominator):
    if not values:
        return None
    quotient, remainder = divmod((len(values) - 1) * numerator, denominator)
    if not remainder:
        return values[quotient]
    return ((denominator - remainder) * values[quotient] +
            remainder * values[quotient + 1]) / denominator

def tail_expected(items, n, total, side_total, percentage, side):
    requested = (n * percentage + 99) // 100
    selected = items[:min(requested, len(items))]
    selected_sum = sum((item[1] for item in selected), Fraction(0))
    remaining = n - len(selected)
    COUNTS['tail_diagnostics_rebuilt'] += 1
    COUNTS['tail_selected_case_id_occurrences_rebuilt'] += len(selected)
    return {
        'tail': side,
        'fraction_of_all_n_for_requested_count': Fraction(percentage, 100),
        'requested_count_ceiling_fraction_times_all_n': requested,
        'original_n': n,
        'actual_removed_count': len(selected),
        'selected_case_ids': [item[0] for item in selected],
        'selected_return_sum': selected_sum,
        'contribution_to_original_mean': divide(selected_sum, n),
        ('share_of_total_positive_return' if side == 'positive' else
         'share_of_total_absolute_loss'): divide(abs(selected_sum), side_total),
        'remaining_count': remaining,
        'mean_without_selected_observations': divide(total - selected_sum, remaining),
        'interpretation': 'retrospective_observed_sample_concentration_not_an_actionable_filter_or_target',
    }

def expected_distribution(items):
    """Derive moments by signed partitions and a separate unpartitioned total."""
    n = len(items)
    require(len({item[0] for item in items}) == n, 'duplicate_case_in_distribution')
    ranked = sorted(((cid, value) for cid, day, value in items), key=lambda item: (item[1], item[0]))
    negative = [item for item in ranked if item[1] < 0]
    positive = sorted((item for item in ranked if item[1] > 0), key=lambda item: (-item[1], item[0]))
    wins, losses = len(positive), len(negative)
    zeros = n - wins - losses
    gain = sum((value for cid, value in positive), Fraction(0))
    loss = -sum((value for cid, value in negative), Fraction(0))
    total = sum((value for cid, value in ranked), Fraction(0))
    require(total == gain - loss, 'signed_partition_total_identity')
    mean = divide(total, n)
    average_gain, average_loss = divide(gain, wins), divide(loss, losses)
    wp, lp = divide(wins, n), divide(losses, n)
    positive_term = wp * average_gain if wins else (Fraction(0) if n else None)
    negative_term = lp * average_loss if losses else (Fraction(0) if n else None)
    expectancy = positive_term - negative_term if n else None
    require(expectancy == mean, 'independent_exact_expectancy_identity')
    values = [value for cid, value in ranked]
    reasons = {}
    if not n:
        for name in ('win_rate_all_n', 'loss_rate_all_n', 'zero_rate_all_n',
                     'mean', 'median', 'minimum', 'maximum'):
            reasons[name] = 'no_observations'
    if not wins + losses:
        reasons['win_rate_among_nonzero'] = 'no_nonzero_observations'
    if not wins:
        reasons['conditional_average_win'] = 'no_positive_observations'
    if not losses:
        reasons['conditional_average_absolute_loss'] = 'no_negative_observations'
        reasons['profit_factor_total_positive_to_total_absolute_loss'] = 'zero_total_absolute_loss_denominator'
    if not wins or not losses:
        reason = 'both_positive_and_negative_observations_required_to_estimate_conditional_magnitudes'
        reasons['payoff_ratio_average_win_to_average_absolute_loss'] = reason
        reasons['break_even_win_rate_among_nonzero'] = reason
    category = ('empty' if not n else 'all_zero' if not wins + losses else
                'both_signs' if wins and losses else 'no_negative' if wins else 'no_positive')
    COUNTS['distribution_category_' + category] += 1
    COUNTS['distributions_rebuilt'] += 1
    return {
        'method_version': 'return-distribution-v1',
        'return_unit': 'same_as_input; stock_return_fraction_when_used_with_stock_event_ledger',
        'n': n, 'positive_count': wins, 'negative_count': losses, 'zero_count': zeros,
        'win_rate_all_n': wp, 'loss_rate_all_n': lp, 'zero_rate_all_n': divide(zeros, n),
        'nonzero_count': wins + losses, 'win_rate_among_nonzero': divide(wins, wins + losses),
        'mean': mean, 'median': interpolation(values, 1, 2),
        'minimum': values[0] if n else None, 'maximum': values[-1] if n else None,
        'conditional_average_win': average_gain, 'conditional_average_absolute_loss': average_loss,
        'payoff_ratio_average_win_to_average_absolute_loss': average_gain / average_loss if wins and losses else None,
        'profit_factor_total_positive_to_total_absolute_loss': divide(gain, loss),
        'total_positive_return': gain, 'total_absolute_loss': loss,
        'expectancy_identity': {
            'formula': 'p_win_all_n * conditional_average_win - p_loss_all_n * conditional_average_absolute_loss = mean',
            'empty_side_convention': 'an_empty_side_contributes_zero_while_its_conditional_average_remains_null',
            'positive_term': positive_term, 'negative_term': negative_term,
            'reconstructed_mean': expectancy, 'mean': mean,
            'exact_rational_identity_holds': True if n else None,
            'verification_arithmetic': 'exact_Fraction_from_finite_Decimal_inputs_before_50_digit_display_rounding',
        },
        'break_even_win_rate_among_nonzero': average_loss / (average_gain + average_loss) if wins and losses else None,
        'break_even_scope': 'conditional_nonzero; observed_win_and_loss_magnitudes_held_fixed; zeros_excluded',
        'undefined_statistic_reasons': reasons,
        'quantiles': {
            'method': 'linear_interpolation_at_(n_minus_1)_times_probability',
            'p05': interpolation(values, 5, 100), 'p95': interpolation(values, 95, 100),
            'interpretation': 'empirical_single_event_return_quantiles_not_account_drawdown',
        },
        'top_positive_tails': {
            'top_1_percent_of_all_n': tail_expected(positive, n, total, gain, 1, 'positive'),
            'top_5_percent_of_all_n': tail_expected(positive, n, total, gain, 5, 'positive'),
        },
        'worst_negative_tails': {
            'worst_1_percent_of_all_n': tail_expected(negative, n, total, loss, 1, 'negative'),
            'worst_5_percent_of_all_n': tail_expected(negative, n, total, loss, 5, 'negative'),
        },
        'interpretation': {
            'win_rate_gate': 'none; no_arbitrary_40_percent_rule',
            'observations': 'all_supplied_observed_returns_retained; no_tail_removal_from_main_mean',
            'tail_sensitivity': 'retrospective_description_only; cannot_identify_winners_or_losers_in_advance',
            'undefined_ratios': 'null; no_infinity_or_NaN; all_positive_sample_does_not_prove_zero_loss_risk',
            'capital': 'no_equity_curve_compounding_funding_options_or_500_to_10000_inference',
        },
    }

def log(message):
    entry = json.dumps({'at_utc': datetime.now(timezone.utc).isoformat(), **message})
    with (ROOT / 'independent-concentration-audit.log').open('a') as handle:
        handle.write(entry + '\n')
    print(entry, flush=True)


def issue(label, reason):
    COUNTS['mismatches'] += 1
    if len(ISSUES) < 50:
        ISSUES.append({'field': label, 'reason': reason})


def compare(actual, expected, label):
    global MAX_NORMALIZED_ERROR
    COUNTS['comparison_nodes'] += 1
    if isinstance(expected, Fraction):
        COUNTS['numerical_decimal_fields_checked'] += 1
        try:
            observed = fraction(actual)
        except (AssertionError, ValueError, ArithmeticError):
            issue(label, 'expected_finite_decimal_string')
            return
        normalized = abs(observed - expected) / max(Fraction(1), abs(expected))
        MAX_NORMALIZED_ERROR = max(MAX_NORMALIZED_ERROR, normalized)
        if normalized > TOLERANCE:
            issue(label, 'numeric_error_exceeds_1e_minus_45_times_max_1_abs_expected')
    elif isinstance(expected, dict):
        if not isinstance(actual, dict) or set(actual) != set(expected):
            issue(label, 'object_keys_differ')
            return
        for key in expected:
            compare(actual[key], expected[key], label + '.' + key)
    elif isinstance(expected, list):
        COUNTS['ordered_lists_checked'] += 1
        if not isinstance(actual, list) or len(actual) != len(expected):
            issue(label, 'ordered_list_length_differs')
            return
        for index, value in enumerate(expected):
            compare(actual[index], value, label + '[' + str(index) + ']')
    else:
        COUNTS['exact_scalar_fields_checked'] += 1
        if type(actual) is not type(expected) or actual != expected:
            issue(label, 'exact_scalar_or_null_differs')


def metric_expected(records, metric):
    present = [(item['date'], item[metric]) for item in records if item[metric] is not None]
    days = sorted(set(day for day, value in present))
    counts = {day: sum(day == item_day for item_day, value in present) for day in days}
    means = {day: sum((value for item_day, value in present if day == item_day), Fraction(0)) / counts[day] for day in days}
    total = sum((value for day, value in present), Fraction(0))
    COUNTS['concentration_metric_summaries_rebuilt'] += 1
    return {
        'complete_count': len(present), 'missing_count': len(records) - len(present),
        'positive_count': sum(value > 0 for day, value in present),
        'negative_count': sum(value < 0 for day, value in present),
        'zero_count': sum(value == 0 for day, value in present),
        'event_sum': total, 'event_mean': total / len(present) if present else None,
        'observed_date_count': len(days), 'observed_dates': days,
        'date_means': means, 'date_counts': counts,
        'date_equal_mean': sum(means.values(), Fraction(0)) / len(days) if days else None,
    }


def concentration_expected(source_rows):
    records = sorted([{**{key: source[key] for key in ('case_id', 'date', 'symbol')},
                       **{metric: None if source[metric] is None else fraction(source[metric]) for metric in METRICS}}
                      for source in source_rows], key=lambda item: (item['date'], item['symbol'], item['case_id']))
    require(len({item['case_id'] for item in records}) == len(records), 'unique_primary_signal_case_ids')
    overall = {metric: metric_expected(records, metric) for metric in METRICS}
    result = {
        'method_version': 'concentration-v1',
        'signal_count': len(records),
        'signal_date_count': len({item['date'] for item in records}),
        'symbol_count': len({item['symbol'] for item in records}),
        'overall': overall,
        'contribution_identity_checks': {},
        'rankings': {},
        'method': {
            'return_unit': 'fraction; sums_are_sums_of_event_returns_not_account_profits',
            'pooled_entity_contribution': 'sum(entity_metric_returns)/original_metric_complete_N',
            'date_equal_entity_contribution': 'sum(entity_return/original_metric_complete_count_on_its_date)/original_metric_observed_D',
            'metric_denominators': 'net_return_and_matched_excess_use_their_own_N_date_counts_and_D',
            'missing': 'all_signal_rows_retained; missing_outcomes_excluded_only_from_return_arithmetic; empty_mean_null',
            'empty_entity_contribution': 'zero_if_overall_denominator_exists_means_no_contribution_not_a_zero_return_observation',
            'leave_one_out': 'remove_one_entire_entity_only; recompute_remaining_nonempty_date_means_and_counts; never_change_main_sample',
            'matrix': 'only_observed_symbol_date_cells; missing_signal_outcomes_retained; absent_cartesian_cells_not_fabricated',
            'identity_arithmetic': 'exact_Fraction_before_decimal_display_rounding',
            'display_precision_significant_digits': 50,
            'inference': 'descriptive_concentration_not_new_bootstrap_or_probability_or_causal_advantage',
            'no_win_rate_gate': True,
            'matched_excess': 'relative_peer_performance_not_trade_profit_or_winning_probability',
            'capital': 'no_equity_curve_drawdown_options_returns_or_funding_survival_model',
        },
    }
    for axis, field in [('date', 'by_date'), ('symbol', 'by_symbol'), ('date_symbol', 'date_symbol_matrix_observed_cells')]:
        select = (lambda item: (item['date'], item['symbol'])) if axis == 'date_symbol' else (lambda item: item[axis])
        values = sorted(set(select(item) for item in records))
        contribution = {metric: {'pooled': {}, 'date_equal': {}} for metric in METRICS}
        entities = []
        for value in values:
            subset = [item for item in records if select(item) == value]
            entry = {'entity_type': axis, 'entity': list(value) if isinstance(value, tuple) else value,
                     'signals': len(subset), 'case_ids': [item['case_id'] for item in subset],
                     'signal_dates': sorted(set(item['date'] for item in subset)), 'metrics': {}}
            for metric in METRICS:
                own = metric_expected(subset, metric)
                base = overall[metric]
                n, d = base['complete_count'], base['observed_date_count']
                pooled = own['event_sum'] / n if n else None
                weights = [item[metric] * Fraction(1, base['date_counts'][item['date']] * d)
                           for item in subset if item[metric] is not None]
                date_equal = sum(weights, Fraction(0)) if d else None
                entry['metrics'][metric] = {
                    **own,
                    'contribution_to_original_pooled_mean': pooled,
                    'contribution_to_original_date_equal_mean': date_equal,
                    'original_complete_denominator': n, 'original_observed_date_denominator': d,
                }
                contribution[metric]['pooled'][value] = pooled
                contribution[metric]['date_equal'][value] = date_equal
            entities.append(entry)
            COUNTS['concentration_' + axis + '_entities_rebuilt'] += 1
        result[field] = entities
        identity = {}
        for metric in METRICS:
            identity[metric] = {}
            for weight, mean_field in [('pooled', 'event_mean'), ('date_equal', 'date_equal_mean')]:
                mean = overall[metric][mean_field]
                summed = sum(contribution[metric][weight].values(), Fraction(0)) if mean is not None else None
                require(summed == mean, 'independent_exact_partition_contribution_identity')
                identity[metric][weight] = {
                    'sum_of_entity_contributions': summed, 'original_mean': mean,
                    'exact_fraction_identity_holds': True if mean is not None else None,
                    'status': 'verified_exact' if mean is not None else 'undefined_no_complete_outcomes',
                }
                COUNTS['concentration_exact_contribution_identities_checked'] += 1
        result['contribution_identity_checks'][axis] = identity
        if axis == 'date_symbol':
            continue
        frequency = Counter(select(item) for item in records)
        ranking = {
            'signal_frequency_all_entities': sorted(frequency, key=lambda value: (-frequency[value], value)),
            'metrics': {},
            'interpretation': 'all_entities_ranked_descriptively_not_selected_for_trading_or_parameter_optimization',
        }
        for metric in METRICS:
            observed = [value for value in values if any(select(item) == value and item[metric] is not None for item in records)]
            ranking['metrics'][metric] = {}
            for weight in ('pooled', 'date_equal'):
                scores = contribution[metric][weight]
                ranking['metrics'][metric][weight] = {
                    'positive_contributors_descending': sorted([value for value in observed if scores[value] > 0], key=lambda value: (-scores[value], value)),
                    'negative_contributors_ascending': sorted([value for value in observed if scores[value] < 0], key=lambda value: (scores[value], value)),
                    'zero_contributors': [value for value in observed if scores[value] == 0],
                    'no_complete_outcome_entities': [value for value in values if value not in observed],
                }
                COUNTS['concentration_rankings_rebuilt'] += 1
        result['rankings'][axis] = ranking
        leaveouts = []
        original_days = set(item['date'] for item in records)
        for value in values:
            removed = [item for item in records if select(item) == value]
            remaining = [item for item in records if select(item) != value]
            remaining_days = set(item['date'] for item in remaining)
            entry = {
                'removed_entity_type': axis, 'removed_entity': value,
                'removed_signal_count': len(removed), 'remaining_signal_count': len(remaining),
                'remaining_signal_dates': sorted(remaining_days),
                'lost_signal_dates': sorted(original_days - remaining_days), 'metrics': {},
                'interpretation': 'hypothetical_leave_one_whole_entity_out_including_wins_losses_and_missing_rows; original_main_sample_unchanged',
            }
            for metric in METRICS:
                remaining_stats, removed_stats = metric_expected(remaining, metric), metric_expected(removed, metric)
                entry['metrics'][metric] = {
                    **remaining_stats,
                    'removed_complete_count': removed_stats['complete_count'],
                    'removed_missing_count': removed_stats['missing_count'],
                    'removed_event_sum': removed_stats['event_sum'],
                    'lost_observed_dates': sorted(set(overall[metric]['observed_dates']) - set(remaining_stats['observed_dates'])),
                }
            leaveouts.append(entry)
            COUNTS['leave_one_' + axis + '_out_rebuilt'] += 1
        result['leave_one_' + axis + '_out'] = leaveouts
    return result


def independent_bins(record):
    feature = record['features']
    if feature['prefix_valid'] is not True:
        return {'gap': 'unknown', 'close29': 'unknown', 'trough': 'unknown'}
    def price_bucket(numerator, denominator):
        require(fraction(numerator) > 0 and fraction(denominator) > 0, 'positive_price_bin_inputs')
        exact_return = fraction(numerator) / fraction(denominator) - 1
        return ('le_minus_2pct' if exact_return <= Fraction(-1, 50) else
                'minus_2pct_to_below_zero' if exact_return < 0 else 'zero_or_positive')
    minute = feature['t_min']
    require(type(minute) is int and 0 <= minute < 30, 'first30_trough_minute')
    return {
        'gap': price_bucket(feature['open0'], feature['previous_close']) if feature['previous_close_valid'] is True else 'unknown',
        'close29': price_bucket(feature['close29'], feature['open0']),
        'trough': 'minute0_9' if minute < 10 else 'minute10_19' if minute < 20 else 'minute20_29',
    }


def variant_of(row):
    return tuple(row[key] for key in ('drop_threshold', 'family', 'exit_horizon', 'cost_bps_per_side'))


def main():
    started = datetime.now(timezone.utc).isoformat()
    (ROOT / 'independent-concentration-audit.log').write_text('')
    require(digest(ROOT / 'study-design.json') == FROZEN_DESIGN, 'frozen_design_hash')
    binding = read(ROOT / 'pre-analysis-validation.json')
    require(binding['tests_passed'] == 85 and binding['failed_tests'] == 0, 'preanalysis_binding_tests')
    for name, sha in binding['bound_sha256'].items():
        require(digest(ROOT / name) == sha, 'preanalysis_hash_' + name)
    design = read(ROOT / 'study-design.json')
    for name, sha in design['scope_sha256'].items():
        require(digest(ROOT / name) == sha, 'scope_binding_' + name)
    sources = read(ROOT / 'reuse-input-manifest.json')
    for source in sources['source_files']:
        ancestor = PARENT if source['ancestor'] == 'parent' else OLD
        require(digest(ancestor / source['name']) == source['sha256'], 'ancestor_binding_' + source['name'])
    output_manifest = read(ROOT / 'analysis-output-manifest.json')
    outputs = {item['path']: item for item in output_manifest['files']}
    for name, item in outputs.items():
        require(digest(ROOT / name) == item['sha256'], 'output_binding_' + name)
    require(output_manifest['cases'] == 1227 and output_manifest['inherited_rows'] == 44172, 'output_scope')
    registry = {item['case_id']: item for item in read(ROOT / 'anatomy-case-registry.json')['cases']}
    require(len(registry) == 1227, 'anatomy_case_scope')
    dates = set(design['scope']['anatomy_dates'])
    require(len(dates) == 12 and {item['date'] for item in registry.values()} == dates, 'anatomy_date_scope')
    classifications = {item['case_id']: item for item in lines(PARENT / 'classifications.jsonl.gz') if item['case_id'] in registry}
    require(set(classifications) == set(registry), 'all_case_classifications_present')
    groups = {}
    for cid, classification in classifications.items():
        value = classification['classification_by_benchmark']['SPY']['classification']
        groups[cid] = 'unknown' if value is None else value
        require(classification['symbol'] == registry[cid]['symbol'] and classification['date'] == registry[cid]['date'], 'original_classification_registry_join')
        if registry[cid]['target_role'] == 'stock':
            require(groups[cid] in GROUPS, 'retained_stock_group')
    inherited = {}
    for name in sorted(outputs):
        if name.startswith('inherited-results/'):
            for row in lines(ROOT / name):
                key = (row['case_id'], variant_of(row))
                require(key not in inherited, 'duplicate_inherited_case_variant')
                inherited[key] = row
    require(len(inherited) == 44172, 'inherited_case_variant_scope')
    stocks = []
    primary = []
    seen = set()
    source_manifest = read(OLD / 'analysis-output-manifest.json')
    originals = sorted((item for item in source_manifest['files'] if item['path'].startswith('results/')), key=lambda item: item['path'])
    for source in originals:
        path = OLD / source['path']
        require(digest(path) == source['sha256'], 'original_result_binding_' + source['path'])
        for row in lines(path):
            COUNTS['original_parent_rows_scanned'] += 1
            cid = row['case_id']
            if cid not in registry:
                continue
            variant = variant_of(row)
            key = (cid, variant)
            require(key not in seen and variant in VARIANTS, 'unique_original_case_variant')
            seen.add(key)
            require(row['date'] == registry[cid]['date'] and row['symbol'] == registry[cid]['symbol'], 'original_registry_join')
            local = inherited[key]
            expected_keys = set(row) | {'anatomy_date_role', 'anatomy_feature_case_id', 'SPY_group', 'parent_result_file'}
            require(set(local) == expected_keys and all(local[name] == value for name, value in row.items()), 'original_fields_and_peers_preserved')
            require(local['SPY_group'] == groups[cid] and local['parent_result_file'] == source['path'], 'inherited_classification_source_join')
            COUNTS['inherited_rows_original_fields_verified'] += 1
            if row['target_role'] == 'stock':
                stocks.append(row)
                if variant == PRIMARY:
                    primary.append(row)
            else:
                COUNTS['benchmark_rows_excluded'] += 1
        log({'stage': 'original_month_verified', 'original_files_verified': originals.index(source) + 1, 'selected_rows_verified': len(seen)})
    require(COUNTS['original_parent_rows_scanned'] == 702684 and seen == set(inherited) and len(seen) == 44172, 'full_original_source_scope')
    require(len(stocks) == 1203 * 36 and len(primary) == 1203, 'stock_scope')
    maincases = [row for row in primary if groups[row['case_id']] == 'market_down']
    signals = [row for row in maincases if row['signal_status'] == 'signal']
    require(len(maincases) == 363, 'all_primary_down_cases_retained')
    require(sum(row['net_return'] is not None for row in signals) == 153 and sum(row['matched_excess'] is not None for row in signals) == 134, 'original_primary_complete_totals')
    concentration = concentration_expected(signals)
    require(concentration['overall']['net_return']['observed_date_count'] == 11, 'primary_net_observed_date_count')
    compare(read(ROOT / 'concentration.json'), concentration, 'concentration')
    COUNTS['primary_down_cases'] = len(maincases)
    COUNTS['primary_down_signals'] = len(signals)
    COUNTS['primary_down_complete_net'] = 153
    COUNTS['primary_down_complete_excess'] = 134
    features = {row['case_id']: row for row in lines(ROOT / 'feature-records.jsonl.gz')}
    require(set(features) == set(registry), 'feature_case_scope')
    bins = {cid: independent_bins(features[cid]) for cid in features}
    for cid in features:
        require(bins[cid] == features[cid]['fixed_bins'], 'independent_bin_assignments_match')
    summary = read(ROOT / 'summary.json')
    variant_records = summary['variant_group_summaries']
    require(len(variant_records) == 108, 'variant_group_summary_count')
    variant_keys = set()
    for item in variant_records:
        key = (tuple(item['variant']), item['SPY_group'])
        require(key not in variant_keys, 'duplicate_variant_summary')
        variant_keys.add(key)
        selected = [row for row in stocks if variant_of(row) == key[0] and groups[row['case_id']] == key[1]]
        complete = [(row['case_id'], row['date'], fraction(row['net_return'])) for row in selected if row['net_return'] is not None]
        compare(item['summary']['target_net_distribution'], expected_distribution(complete), 'variant_group|' + '|'.join(map(str, key[0])) + '|' + key[1])
        COUNTS['variant_group_distributions_checked'] += 1
    require(variant_keys == set(product(VARIANTS, GROUPS)), 'full_variant_group_grid')
    date_records = summary['primary_date_group_summaries']
    require(len(date_records) == 36, 'date_group_summary_count')
    date_keys = set()
    for item in date_records:
        key = (item['date'], item['SPY_group'])
        require(key not in date_keys, 'duplicate_date_summary')
        date_keys.add(key)
        selected = [row for row in primary if row['date'] == key[0] and groups[row['case_id']] == key[1]]
        complete = [(row['case_id'], row['date'], fraction(row['net_return'])) for row in selected if row['net_return'] is not None]
        compare(item['summary']['target_net_distribution'], expected_distribution(complete), 'primary_date_group|' + '|'.join(key))
        COUNTS['primary_date_group_distributions_checked'] += 1
    require(date_keys == set(product(dates, GROUPS)), 'full_date_group_grid')
    bin_records = summary['fixed_feature_bins']
    require(len(bin_records) == 12, 'fixed_bin_summary_count')
    expected_bin_keys = set(product(('gap', 'close29'), ('le_minus_2pct', 'minus_2pct_to_below_zero', 'zero_or_positive', 'unknown'))) | set(product(('trough',), ('minute0_9', 'minute10_19', 'minute20_29', 'unknown')))
    bin_keys = set()
    for item in bin_records:
        key = (item['axis'], item['bin'])
        require(key not in bin_keys and key in expected_bin_keys, 'unique_expected_fixed_bin')
        bin_keys.add(key)
        selected = [row for row in maincases if bins[row['case_id']][key[0]] == key[1]]
        complete = [(row['case_id'], row['date'], fraction(row['net_return'])) for row in selected if row['net_return'] is not None]
        compare(item['primary_SPY_down_all_selected_case_summary']['target_net_distribution'], expected_distribution(complete), 'fixed_feature_bin|' + '|'.join(key))
        COUNTS['fixed_feature_bin_distributions_checked'] += 1
    require(bin_keys == expected_bin_keys, 'full_fixed_bin_grid')
    require(COUNTS['distributions_rebuilt'] == 156 and COUNTS['tail_diagnostics_rebuilt'] == 624, 'complete_distribution_and_tail_scope')
    result = {
        'audit_version': 'independent_anatomy_concentration_payoff_v1',
        'started_at_utc': started, 'completed_at_utc': datetime.now(timezone.utc).isoformat(),
        'passed': COUNTS['mismatches'] == 0, 'mismatches': ISSUES,
        'counts': dict(sorted(COUNTS.items())),
        'scope': {'all_anatomy_cases': 1227, 'all_original_variants': 36, 'inherited_rows': 44172,
                  'all_primary_SPY_down_cases': 363, 'concentration_input': 'only_original_primary_SPY_market_down_signal_rows_including_missing_outcomes',
                  'concentration_signal_count': len(signals), 'complete_net_count': 153,
                  'complete_matched_excess_count': 134, 'complete_net_dates': 11,
                  'primary_variant': list(PRIMARY), 'anatomy_dates': sorted(dates),
                  'stock_return_unit': 'fraction; multiply_by_100_for_percent',
                  'option_winrate_or_funded_survival_estimated': False, 'win_rate_gate': None},
        'method': 'Independent exact Fraction reconstruction from original ancestor results and original parent classifications. Every concentration field, partition, rank, identity and leave-out compared recursively. Original row and peer fields stay fixed. All 156 complete target-net distribution objects and 624 tail diagnostics independently rebuilt, including stable ordered selected case IDs.',
        'numeric_comparison': {'display_precision_significant_digits': 50,
                               'tolerance': '1e-45 * max(1, abs(exact_expected))',
                               'maximum_normalized_error_below_tolerance': MAX_NORMALIZED_ERROR <= TOLERANCE,
                               'exact_contribution_and_expectancy_identities_verified_before_display_rounding': True},
        'production_modules_imported_or_called': False,
        'carried_forward_independent_payoff_oracle_source_sha256': ORACLE_SOURCE_SHA256,
        'bound_sha256': {name: digest(ROOT / name) for name in (
            'study-design.json', 'pre-analysis-validation.json', 'reuse-input-manifest.json',
            'anatomy-case-registry.json', 'analysis-output-manifest.json', 'concentration.json',
            'summary.json', 'feature-records.jsonl.gz', 'independent_concentration_audit.py')},
        'limitations': ['Raw prefix feature reconstruction and calendar/source contextual claims are verified by separate auditors.',
                       'Fixed feature bins were independently recomputed from emitted primitive prefix prices and minute indices; their market-source primitives are separately audited.',
                       'Concentration, tail removal and leave-outs are retrospective descriptions; no trading filter, causal claim, option payoff or funded-account path is inferred.'],
    }
    (ROOT / 'independent-concentration-audit.json').write_text(json.dumps(result, indent=2) + '\n')
    log({'stage': 'completed', 'passed': result['passed'], 'counts': result['counts'], 'audit_sha256': digest(ROOT / 'independent-concentration-audit.json')})
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    try:
        code = main()
    except Exception as error:
        log({'stage': 'failed', 'error_type': type(error).__name__,
             'assertion': str(error) if isinstance(error, AssertionError) else 'unexpected_local_audit_error'})
        code = 1
    raise SystemExit(code)
