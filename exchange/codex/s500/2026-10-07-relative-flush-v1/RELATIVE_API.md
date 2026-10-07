# Relative opening-flush classification API

`classify_relative.classify_case(target_bars, benchmark_bars,
source_complete=True)` receives two normalized bar lists. Minute `t=0` denotes
the regular opening minute; each bar has `t/o/h/l/c/v`. Preserve duplicate
records. Both inputs require every minute 0–29 exactly once with valid finite
positive coherent OHLC and nonnegative volume. Validation reuses the previous
stage's byte-identical `vendor.flush_engine` module. Its 2% flush flag is not an
additional classification filter. Only literal `True` means source acquisition
is complete.

The function finds the target's **earliest** minimum close in minutes 0–29, named
`t_min`. The target's simple opening return is `minimum_close / open[0] - 1`.
Benchmark movement uses its close at **that same target-selected minute** divided
by its own opening open, minus one. It does not use the benchmark's own minimum,
its 09:59 return, or any later outcome. `relative_return` is target return minus
benchmark return, a difference of simple returns; it is not a price difference
or a ratio of the two assets. Values are fractions represented as Decimal
strings: `-0.005` means minus 0.5%, or minus 50 basis points.

Classification is `market_down` when benchmark return <= `-0.005`, including
equality; otherwise `market_not_down`. An algebraically equivalent exact
cross-product comparison protects the boundary from rounding of display-return
strings. Descriptive returns use 42-digit Decimal precision. No target-relative
return threshold, entry, exit, PnL, capital, or option operation is introduced.
These are exploratory groups and cannot be selected or revised using outcomes.

The output includes `target_valid`, `benchmark_valid`, `t_min`, the opening and
comparison prices, `target_return`, `benchmark_return`, `relative_return`,
`classification`, `status`, `reason`, and separate prefix `coverage` diagnostics.
Status is `classified`, `unknown_target_prefix`, `unknown_benchmark_prefix`, or
`unknown_both_prefixes`. Unknown classification is null. A valid target retains
its own known minimum and return when the benchmark is unknown. No benchmark
comparison return is produced without a valid target-selected minute.

Identifiable integer minutes outside 0–29 are excluded **before validation**.
Later invalid prices, missing fields, or duplicate bars therefore cannot erase
an earlier classification. An unlocatable/noninteger timestamp is preserved as
an unknown-source problem because it cannot be proven to lie outside the
prefix. This conservative coverage limitation must not be confused with an
economic rule using future prices. Input records are never mutated.

The runner must mark non-stock cases as `benchmark_control_not_target`; this
pure function has no symbol or asset-type metadata and cannot perform that
classification itself. Synthetic validation reads no historical outcomes.
