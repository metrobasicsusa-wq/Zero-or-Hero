"""Exhaustive offline target-net payoff audit; no production module imports.

Every complete stock return is reconstructed from the enriched event ledger and
assigned using the separate classification ledger. Fraction arithmetic verifies
all 216 benchmark/group/variant records over all, ten months, and two periods.
Returns are stock-return fractions, not option returns or funded-account paths.
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
PARENT = ROOT.parent / 's500-flush-rebound-20261007'
FROZEN_DESIGN = '97f6bca2f3097fffa06af94454f3b5c4735bc7b4f212a9aa51eb762aced3a55e'
TOLERANCE = Fraction(1, 10 ** 45)
BENCHMARKS = ('SPY', 'QQQ')
GROUPS = ('market_down', 'market_not_down', 'unknown')
VARIANTS = tuple(product(('0.02', '0.03'), ('FIXED', 'REBOUND'),
                         ('30m', '60m', 'close_minus_10m'), (5, 10, 25)))
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


def log(message):
    entry = json.dumps({'at_utc': datetime.now(timezone.utc).isoformat(), **message})
    with (ROOT / 'independent-payoff-audit.log').open('a') as handle:
        handle.write(entry + '\n')
    print(entry, flush=True)


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


def mismatch(label, reason):
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
            mismatch(label, 'expected_finite_decimal_string')
            return
        normalized = abs(observed - expected) / max(Fraction(1), abs(expected))
        MAX_NORMALIZED_ERROR = max(MAX_NORMALIZED_ERROR, normalized)
        if normalized > TOLERANCE:
            mismatch(label, 'numeric_error_exceeds_1e_minus_45_times_max_1_abs_expected')
    elif isinstance(expected, dict):
        if not isinstance(actual, dict) or set(actual) != set(expected):
            mismatch(label, 'object_keys_differ')
            return
        for key in expected:
            compare(actual[key], expected[key], label + '.' + key)
    elif isinstance(expected, list):
        COUNTS['ordered_case_id_lists_checked'] += 1
        if not isinstance(actual, list) or actual != expected:
            mismatch(label, 'ordered_selected_case_ids_differ')
    else:
        COUNTS['exact_scalar_fields_checked'] += 1
        if type(actual) is not type(expected) or actual != expected:
            mismatch(label, 'exact_scalar_or_null_differs')


def main():
    started = datetime.now(timezone.utc).isoformat()
    (ROOT / 'independent-payoff-audit.log').write_text('')
    require(digest(ROOT / 'study-design.json') == FROZEN_DESIGN, 'frozen_study_design_hash')
    binding = read(ROOT / 'pre-analysis-validation.json')
    require(binding['failed_tests'] == 0 and binding['tests_passed'] == 107, 'preanalysis_tests')
    for name, sha in binding['bound_sha256'].items():
        require(digest(ROOT / name) == sha, 'preanalysis_binding_' + name)
    manifest = read(ROOT / 'analysis-output-manifest.json')
    manifest_by_path = {item['path']: item for item in manifest['files']}
    for name in ('summary.json', 'primary-results.json'):
        require(digest(ROOT / name) == manifest_by_path[name]['sha256'], 'output_manifest_' + name)
    class_manifest = read(ROOT / 'classification-output-manifest.json')
    class_path = ROOT / 'classifications.jsonl.gz'
    require(digest(class_path) == class_manifest['output']['sha256'], 'classification_output_hash')
    classifications = {}
    for row in lines(class_path):
        require(row['case_id'] not in classifications, 'duplicate_classification_case')
        classifications[row['case_id']] = row
    require(len(classifications) == 19519, 'classification_case_scope')
    protocol = read(ROOT / 'study-design.json')
    calendar_path = PARENT / 'calendar-2026-ytd.json'
    require(digest(calendar_path) == protocol['parent_bound_sha256']['calendar-2026-ytd.json'], 'calendar_binding')
    calendar = {row['date'] for row in read(calendar_path)}
    months = tuple(sorted({day[:7] for day in calendar}))
    require(len(calendar) == 191 and len(months) == 10, 'calendar_scope')
    bucket = defaultdict(list)
    missing = Counter()
    selected = Counter()
    seen_variants = defaultdict(set)
    files = sorted(name for name in manifest_by_path if name.startswith('enriched-results/'))
    require(bool(files) and set(files) == {path.relative_to(ROOT).as_posix() for path in (ROOT / 'enriched-results').glob('*.jsonl.gz')}, 'enriched_file_scope')
    for name in files:
        path = ROOT / name
        require(digest(path) == manifest_by_path[name]['sha256'], 'enriched_manifest_' + name)
        for row in lines(path):
            cid, date, variant = row['case_id'], row['date'], tuple(row['variant'])
            require(variant in VARIANTS and variant not in seen_variants[cid], 'duplicate_or_unregistered_variant')
            seen_variants[cid].add(variant)
            require(row['variant_id'] == '|'.join(map(str, variant)), 'variant_id_encoding')
            classification = classifications[cid]
            require(date in calendar and date == classification['date'] and row['symbol'] == classification['symbol'], 'classification_join')
            require(row['target_role'] == classification['target_role'], 'target_role_join')
            require(row['actual_fill'] is False and row['option_return'] is False and row['wealth_path'] is False, 'stock_proxy_scope_flags')
            COUNTS['enriched_rows_read'] += 1
            if row['target_role'] != 'stock':
                require(row['symbol'] in BENCHMARKS, 'benchmark_exclusion_scope')
                COUNTS['benchmark_rows_excluded'] += 1
                continue
            require(row['symbol'] not in BENCHMARKS, 'stock_excludes_benchmarks')
            COUNTS['stock_rows_read'] += 1
            value = None if row['net_return'] is None else fraction(row['net_return'])
            COUNTS['stock_missing_returns' if value is None else 'stock_complete_returns'] += 1
            observation = (cid, date, value)
            for benchmark in BENCHMARKS:
                info = classification['classification_by_benchmark'][benchmark]
                group = info['classification'] if info['classification'] is not None else 'unknown'
                require(group in GROUPS, 'known_or_retained_unknown_group')
                require(row['classification_by_benchmark'][benchmark]['classification'] == info['classification'], 'enriched_classification_join')
                key = (benchmark, group, variant)
                selected[key] += 1
                if value is None:
                    missing[key] += 1
                else:
                    bucket[key].append(observation)
        log({'stage': 'enriched_file_verified', 'files_verified': files.index(name) + 1, 'enriched_rows_read': COUNTS['enriched_rows_read']})
    require(COUNTS['enriched_rows_read'] == 702684 and COUNTS['benchmark_rows_excluded'] == 13752 and COUNTS['stock_rows_read'] == 688932, 'full_row_scope')
    require(set(seen_variants) == set(classifications) and all(len(value) == 36 for value in seen_variants.values()), 'complete_case_variant_grid')
    require(sum(row['target_role'] == 'stock' for row in classifications.values()) == 19137, 'stock_case_scope')
    summary = read(ROOT / 'summary.json')
    records = summary['groups']
    require(summary['group_count'] == 216 and len(records) == 216, 'summary_group_scope')
    require(summary['classification_sha256'] == digest(class_path), 'summary_classification_binding')
    actual_keys = set()
    for record in records:
        key = (record['benchmark'], record['group'], tuple(record['variant']))
        require(key not in actual_keys, 'duplicate_summary_group')
        actual_keys.add(key)
        benchmark, group, variant = key
        require(key in set(product(BENCHMARKS, GROUPS, VARIANTS)), 'unexpected_summary_group')
        base_label = benchmark + '|' + group + '|' + '|'.join(map(str, variant))
        observations = bucket[key]
        require(record['all']['counts']['selected_cases'] == selected[key], 'selected_case_denominator')
        require(len(observations) + missing[key] == selected[key], 'missing_not_imputed_zero')
        require(set(record['months']) == set(months) and set(record['periods']) == {'H1', 'H2_to_cutoff'}, 'summary_slice_scope')
        slices = [('all', observations, record['all'])]
        for month in months:
            slices.append(('month:' + month, [item for item in observations if item[1][:7] == month], record['months'][month]))
        for period in ('H1', 'H2_to_cutoff'):
            slices.append(('period:' + period, [item for item in observations if ('H1' if item[1] < '2026-07-01' else 'H2_to_cutoff') == period], record['periods'][period]))
        for name, items, actual in slices:
            label = base_label + '|' + name
            compare(actual['counts']['complete_target_returns'], len(items), label + '.complete_target_returns')
            compare(actual['target_net_distribution'], expected_distribution(items), label)
        COUNTS['benchmark_group_variant_records_checked'] += 1
    require(actual_keys == set(product(BENCHMARKS, GROUPS, VARIANTS)), 'exhaustive_group_variant_grid')
    require(COUNTS['distributions_rebuilt'] == 2808 and COUNTS['tail_diagnostics_rebuilt'] == 11232, 'exhaustive_distribution_scope')
    # The separate primary output repeats six complete group records; require
    # byte-for-byte JSON-value equality with the audited corresponding records.
    primary = read(ROOT / 'primary-results.json')['groups']
    expected_primary = [record for record in records if tuple(record['variant']) == ('0.02', 'REBOUND', '60m', 10)]
    require(primary == expected_primary and len(primary) == 6, 'primary_output_repeats_audited_groups')
    COUNTS['primary_distribution_copies_verified'] = len(primary) * 13
    result = {
        'audit_version': 'independent_relative_payoff_v1',
        'started_at_utc': started,
        'completed_at_utc': datetime.now(timezone.utc).isoformat(),
        'passed': COUNTS['mismatches'] == 0,
        'mismatches': ISSUES,
        'counts': dict(sorted(COUNTS.items())),
        'scope': {'cases': 19519, 'stock_cases': 19137, 'base_rows': 702684,
                  'benchmarks': list(BENCHMARKS), 'groups': list(GROUPS), 'variants': 36,
                  'months': list(months), 'periods': ['H1', 'H2_to_cutoff'],
                  'return_unit': 'stock_return_fraction; multiply_by_100_for_percent',
                  'actual_option_win_rate_estimated': False,
                  'funded_account_survival_estimated': False,
                  'win_rate_gate': None},
        'method': 'Independent exact Fraction reconstruction from enriched target net returns and separate classifications; no production module imports or calls. All numerical fields, ordered tail case IDs, counts, nulls, undefined reasons, and scope metadata verified.',
        'numeric_comparison': {'display_precision_significant_digits': 50,
                               'tolerance': '1e-45 * max(1, abs(exact_expected))',
                               'maximum_normalized_error_below_tolerance': MAX_NORMALIZED_ERROR <= TOLERANCE,
                               'arithmetic': 'exact Fraction from finite decimal strings; unrounded expectancy identity checked before displayed-value comparison'},
        'bound_sha256': {name: digest(ROOT / name) for name in (
            'study-design.json', 'pre-analysis-validation.json', 'classification-output-manifest.json',
            'classifications.jsonl.gz', 'analysis-output-manifest.json', 'summary.json',
            'primary-results.json', 'independent_payoff_audit.py')},
        'enriched_files_verified': [{'path': name, 'sha256': manifest_by_path[name]['sha256']} for name in files],
        'limitations': ['The inherited parent-return calculation and original classification inputs are verified by separate auditors.',
                       'Observed stock-return distributions do not estimate 0DTE option win rates, fills, nonlinear payoffs, or funded-account survival.',
                       'Tail removal is retrospective descriptive sensitivity, not an actionable filter or a trading target.'],
    }
    (ROOT / 'independent-payoff-audit.json').write_text(json.dumps(result, indent=2) + '\n')
    log({'stage': 'completed', 'passed': result['passed'], 'counts': result['counts'],
         'audit_sha256': digest(ROOT / 'independent-payoff-audit.json')})
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    try:
        exit_code = main()
    except Exception as error:
        # Keep public logs free of environment paths and source row contents.
        log({'stage': 'failed', 'error_type': type(error).__name__,
             'assertion': str(error) if isinstance(error, AssertionError) else 'unexpected_local_audit_error'})
        exit_code = 1
    raise SystemExit(exit_code)
