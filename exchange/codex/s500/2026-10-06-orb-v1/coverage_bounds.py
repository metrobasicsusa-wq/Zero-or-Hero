"""Prove that incomplete prior volumes cannot change the exact ORB top20.

This does not impute missing bars, change eligibility, or repair source coverage.
All original gaps remain in the evidence. Unknown volume is only constrained to
be nonnegative. A currently missing bar cannot receive a finite RV bound.
"""
from __future__ import annotations
import argparse
from collections import Counter
from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
from features import valid_bar

ROOT = Path(__file__).resolve().parent


def _q(value):
    return Fraction(str(value))


def _volume(symbol, day, decision, volume, split_index):
    value = _q(volume)
    for action in split_index.get(symbol, []):
        effective = action.get('screen_date')
        if effective and day < effective <= decision:
            if action.get('issues'):
                raise ValueError('unresolved_split_in_opening_history')
            old, new = _q(action['old_rate']), _q(action['new_rate'])
            if old <= 0 or new <= 0:
                raise ValueError('invalid_split_ratio')
            value *= new / old
    return value


def _number(value):
    return {'numerator': value.numerator, 'denominator': value.denominator,
            'decimal': float(value)}


def _inputs(prepared, market, day, symbol):
    today = market.get(day, {}).get(symbol)
    if not valid_bar(today):
        return {'reason': 'missing_or_invalid_current_opening'}
    if today['o'] <= 5:
        return {'rejected': 'opening_price_not_above5'}
    prior_dates = prepared['prior_opening_dates'][day]
    if len(prior_dates) != 14:
        raise ValueError('opening_rv_requires14prior_sessions')
    known_sum = Fraction(0)
    missing = []
    try:
        for previous in prior_dates:
            bar = market.get(previous, {}).get(symbol)
            if not valid_bar(bar):
                missing.append(previous)
            else:
                known_sum += _volume(symbol, previous, day, bar['v'], prepared['split_index'])
    except (ValueError, KeyError, ZeroDivisionError):
        return {'reason': 'unresolved_split_in_opening_history'}
    out = {'known_sum': known_sum, 'today_volume': _q(today['v']), 'missing_dates': missing}
    if missing:
        out['reason'] = 'missing_or_invalid_prior_openings'
    elif known_sum <= 0:
        out['reason'] = 'zero_prior14_opening_volume_mean'
    else:
        out['rv'] = 14 * out['today_volume'] / known_sum
    return out


def certify(prepared, opening_market, ranked, strict_diagnostics):
    """Return JSON-compatible coverage/proofs without mutating any argument.

    ranking_complete means certified top20 equality, not complete source inputs.
    ranking_missing retains only unresolved gaps for the engine gate;
    original_ranking_missing and proofs preserve every original source gap.
    This routine compares exact known-candidate membership/top20 order against
    the strict ranking and withholds certification on numerical disagreement.
    """
    coverage, proofs, day_summary = {}, [], {}
    strict_all = strict_diagnostics.get('all_rv_qualified')
    strict_known = {}
    if strict_all is not None:
        for row in strict_all:
            strict_known.setdefault(row['date'], set()).add(row['symbol'])
    for session in prepared['calendar']:
        day = session['date']
        original = deepcopy(strict_diagnostics.get('coverage', {}).get(day, {}).get('ranking_missing', []))
        integrity = []
        if day not in strict_diagnostics.get('coverage', {}) or day not in ranked:
            integrity.append({'date': day, 'symbol': None, 'reason': 'missing_strict_day'})
        candidates = prepared['daily_candidates'][day]
        symbols = [r['symbol'] for r in candidates]
        if len(symbols) != len(set(symbols)):
            raise ValueError('duplicate_daily_candidate')
        info = {s: _inputs(prepared, opening_market, day, s) for s in symbols}
        known = sorted(((v['rv'], s) for s, v in info.items() if 'rv' in v and v['rv'] >= 1),
                       key=lambda pair: (-pair[0], pair[1]))
        expected_top = [s for _, s in known[:20]]
        if expected_top != [r['symbol'] for r in ranked.get(day, [])]:
            integrity.append({'date': day, 'symbol': None, 'reason': 'exact_vs_strict_top20_disagreement'})
        if strict_all is not None and {s for _, s in known} != strict_known.get(day, set()):
            integrity.append({'date': day, 'symbol': None, 'reason': 'exact_vs_strict_RV_eligibility_disagreement'})
        generated_gaps = {(s, v['reason']) for s, v in info.items() if 'reason' in v}
        if generated_gaps != {(g['symbol'], g['reason']) for g in original}:
            integrity.append({'date': day, 'symbol': None, 'reason': 'strict_gap_inventory_disagreement'})
        cutoff = known[19] if len(known) >= 20 else None
        unresolved = []
        day_proofs = []
        for gap in original:
            symbol = gap['symbol']
            values = info.get(symbol, {})
            proof = {'date': day, 'symbol': symbol, 'original_gap': deepcopy(gap),
                     'excluded_for_all_nonnegative_missing_volumes': False,
                     'known_qualified_count': len(known)}
            if cutoff:
                proof['known20th_cutoff'] = {'symbol': cutoff[1], 'rv_exact': _number(cutoff[0])}
            if values.get('reason') == 'missing_or_invalid_prior_openings':
                total = values['known_sum']
                proof['known_adjusted_prior_volume_sum'] = _number(total)
                proof['missing_dates'] = values['missing_dates']
                proof['known_prior_volume_count'] = 14 - len(values['missing_dates'])
                proof['today_volume'] = _number(values['today_volume'])
                if total > 0:
                    upper = 14 * values['today_volume'] / total
                    proof['relative_volume_upper_bound'] = _number(upper)
                    if upper < 1:
                        proof['excluded_for_all_nonnegative_missing_volumes'] = True
                        proof['proof_reason'] = 'RV_upper_bound_below1'
                    elif cutoff and (-upper, symbol) > (-cutoff[0], cutoff[1]):
                        proof['excluded_for_all_nonnegative_missing_volumes'] = True
                        proof['proof_reason'] = 'RV_upper_bound_sortkey_after_known20th'
                    else:
                        proof['proof_reason'] = 'could_enter_top20'
                else:
                    proof['relative_volume_upper_bound'] = None
                    proof['proof_reason'] = 'no_positive_known_denominator_unbounded_or_undefined'
            else:
                proof['proof_reason'] = values.get('reason', 'strict_gap_unmatched')
            if not proof['excluded_for_all_nonnegative_missing_volumes']:
                unresolved.append(deepcopy(gap))
            day_proofs.append(proof)
        unresolved.extend(integrity)
        complete = not unresolved
        coverage[day] = {'ranking_complete': complete, 'ranking_missing': unresolved,
                         'source_inputs_complete': not original and not integrity,
                         'strict_ranking_complete': bool(strict_diagnostics.get('coverage', {}).get(day, {}).get('ranking_complete', False)),
                         'original_ranking_missing': original,
                         'certified_excluded_gap_count': sum(p['excluded_for_all_nonnegative_missing_volumes'] for p in day_proofs),
                         'certification': 'exact_top20_equality_for_all_nonnegative_unknown_volumes' if complete else 'not_certified'}
        day_summary[day] = {'original_gap_count': len(original), 'unresolved_gap_count': len(unresolved),
                            'certified_excluded_gap_count': coverage[day]['certified_excluded_gap_count'],
                            'known_qualified_count': len(known), 'ranking_complete': complete}
        proofs.extend(day_proofs)
    original_gaps = sum(len(v['original_ranking_missing']) for v in coverage.values())
    return {'method': 'Nonnegative-volume upper bounds with exact rational split adjustment and lexicographic symbol ties.',
            'coverage': coverage, 'proofs': proofs, 'days': day_summary,
            'summary': {'evaluation_days': len(coverage),
                'strict_complete_days': sum(v['strict_ranking_complete'] for v in coverage.values()),
                'certified_top20_days': sum(v['ranking_complete'] for v in coverage.values()),
                'source_complete_days': sum(v['source_inputs_complete'] for v in coverage.values()),
                'original_gaps_preserved': original_gaps,
                'certified_excluded_gaps': sum(p['excluded_for_all_nonnegative_missing_volumes'] for p in proofs),
                'unresolved_gaps': sum(len(v['ranking_missing']) for v in coverage.values()),
                'proof_reason_counts': dict(Counter(p['proof_reason'] for p in proofs))},
            'limitations': ['Top20 certification does not repair missing bars or verify feed completeness.',
                'Strict ranked rows and their observed relative volumes remain unchanged; no zero imputation is used.',
                'Known volume nonnegativity and correctly mapped effective splits are assumptions of the bound.',
                'A missing current opening, undefined all-zero denominator, or possible top20 entry remains unresolved.',
                'Minute execution, costs, settlement, source identity, and universe bias remain separate checks.']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepared', type=Path, default=ROOT/'prepare.json')
    parser.add_argument('--market', type=Path, default=ROOT/'opening-market.json')
    parser.add_argument('--ranked', type=Path, default=ROOT/'ranked.json')
    parser.add_argument('--diagnostics', type=Path, default=ROOT/'ranking-diagnostics.json')
    parser.add_argument('--output', type=Path, default=ROOT/'bounded-coverage.json')
    args = parser.parse_args()
    read = lambda p: json.loads(p.read_text())
    result = certify(read(args.prepared), read(args.market), read(args.ranked), read(args.diagnostics))
    args.output.write_text(json.dumps(result, separators=(',', ':'))+'\n')
    print(json.dumps(result['summary'], indent=2))


if __name__ == '__main__':
    main()
