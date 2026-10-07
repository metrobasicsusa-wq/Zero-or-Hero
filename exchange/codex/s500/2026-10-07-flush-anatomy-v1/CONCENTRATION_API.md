# Concentration API and fixed arithmetic

```python
from concentration import summarize_concentration

result = summarize_concentration([
    {"case_id": "stable-id", "date": "2026-01-02", "symbol": "AAA",
     "net_return": "0.01", "matched_excess": None},
])
```

Each row is one original signal, including an unobserved outcome. The caller
selects the previously fixed primary SPY `market_down` signal rows; this pure
module does not select signals, read source files, or alter their outcomes.
Values accept finite `Decimal`, decimal strings, integers, or `None`; floats,
booleans, nonfinite values, duplicate `case_id`, and noncanonical dates fail.
The module never silently drops missing-signal rows. It processes all supplied
positive, negative, zero, and missing outcomes.

The output is JSON safe: counts are integers; means, sums, and contributions are
decimal strings, or `null` where undefined. Arithmetic uses exact `Fraction`
values derived from the decimal inputs, with 50 significant digits at display.
Identity checks and ranking use exact values before display rounding. Summing
individually displayed rounded numbers can have a tiny rounding discrepancy.

Top-level output fields:

- `signal_count`, `signal_date_count`, `symbol_count` retain every input signal.
- `overall.net_return` and `overall.matched_excess` each contain their own
  `complete_count`, `missing_count`, sign counts, `event_sum`, `event_mean`,
  `observed_date_count`, `observed_dates`, `date_counts`, `date_means`, and
  `date_equal_mean`.
- `by_date` and `by_symbol` contain all entities, signal counts, stable case IDs,
  and those same metrics plus contributions to the **original** pooled and
  date-equal means. `entity_type` and `entity` identify the group.
- `date_symbol_matrix_observed_cells` retains every observed date/symbol cell,
  including cells with missing outcomes. Its `entity` is `[date, symbol]`.
  Unobserved Cartesian cells are not invented as zero-return observations.
- `contribution_identity_checks` independently checks every metric under date,
  symbol, and date-symbol partitions. Contributions add back exactly to the
  original pooled and date-equal means before decimal display rounding.
- `leave_one_date_out` and `leave_one_symbol_out` include **every** entity. Each
  independently removes exactly that complete entity, including all its positive,
  negative, zero, and missing rows. Remaining metrics are recomputed; each
  includes remaining `complete_count` and `observed_date_count`, removed counts,
  and `lost_observed_dates`. `remaining_signal_dates`/`lost_signal_dates` track
  signal availability separately from observed outcome availability.
- `rankings` gives complete descriptive positive/negative contributor lists,
  zero contributors, entities without observations, and signal-frequency order.
  Stable entity IDs break exact ties. No top-N trading selection is made.

For each metric separately, let `N` be the original complete outcome count,
`n_d` its complete count on date d, and `D` the count of dates with at least one
complete outcome. For entity E:

```
pooled contribution(E) = sum(return_i for i in E) / N
date-equal contribution(E) = sum(return_i / n_date(i) for i in E) / D
```

Its own `event_mean` divides by the entity's complete count, while its
`date_equal_mean` averages its own nonempty date means. These own means are not
the contributions to the original overall means. Net and matched-excess
denominators need not match and are never substituted for each other.

After leaving one entity out, remaining date-equal means use the **new** complete
counts for each remaining date and exclude dates with no remaining observations.
Subtracting the old entity contribution is not equivalent. All-missing signals
still remain in signal counts and missing coverage. Their mean is `null`; their
contribution is zero only when a valid original denominator exists. That zero
means no contribution, not an observed zero return. An observed zero return does
count toward N and makes its date observable.

Leave-one-out outputs are hypothetical sensitivity descriptions; the main sample
retains all entities. All rankings and sign summaries of matched excess describe
relative performance, not trade profits or winning probabilities. No win-rate
gate, new bootstrap, parameter optimization, trade filtering, option-return
conversion, equity curve, or funding-survival calculation is performed.

Run `python -m unittest -v test_concentration.py` for synthetic-only validation.
The real decomposition must wait for the parent's explicit GO and method/input
binding; these files and tests do not read actual stage outcomes.
