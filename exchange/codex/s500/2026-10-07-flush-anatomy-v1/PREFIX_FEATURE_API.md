# First-30-minute descriptive features

`prefix_features.first30_features(bars, previous_close, source_complete=True)`
uses normalized bar lists (`t/o/h/l/c/v`), where minute 0 is the regular opening
minute. Source acquisition completeness must be literal `True`. Minutes 0–29
must be present exactly once, with valid coherent OHLC and nonnegative volume.
Validation reuses the unchanged prior engine in `vendor.flush_engine`; its
2% flush classification is ignored and is not an additional feature filter.

Features are available after minute 29 closes, at minute 30 (10:00 ET for a
09:30 open). No entry, exit, subsequent return, or outcome enters computation.
All numeric features are decimal strings using Decimal precision 42. `t_min`
and the timing metadata are integers. Returns and range are fractions: `0.01`
means 1%, or 100 basis points.

| Field | Definition |
| --- | --- |
| `open0` | Minute 0 opening price |
| `close29` | Minute 29 closing price |
| `min_close`, `t_min` | Minimum close in 0–29 and its earliest tied minute |
| `max_close` | Maximum close in 0–29 |
| `close29_return` | `close29 / open0 - 1` |
| `min_close_return` | `min_close / open0 - 1` |
| `max_close_return` | `max_close / open0 - 1` |
| `first30_volume` | Sum of the 30 raw minute volumes; no normalization |
| `first30_high`, `first30_low` | Maximum high and minimum low in 0–29 |
| `first30_high_low_range` | `(first30_high - first30_low) / open0` |
| `previous_close` | Supplied positive finite prior close, when valid |
| `gap_to_previous_close` | `open0 / previous_close - 1`, when both valid |

The high-low range is not `high/low-1` and not a close-to-close move. Minimum
close timing never uses intrabar low. Volume is the original prefix sum; it is
not RVOL, and daily volume is not substituted as an intraday baseline. Source
adjustments and consistency of the supplied previous close remain runner/input
provenance concerns; the function does not fetch or invent a previous close.

`status` is `complete` or `unknown_prefix`. `prefix_valid`, `reason`, and
`coverage` retain source, missing, duplicate, and invalid-record diagnostics.
Unknown prefixes leave every market feature null, including the opening gap.
Previous-close validity is independent: `previous_close_valid` and
`previous_close_reason` describe that input. A bad or missing previous close
leaves only `gap_to_previous_close` unknown on a complete prefix, with an
explicit `gap_reason`; it does not invalidate known minute-derived features.

Identifiable integer timestamps outside 0–29 are discarded before validation,
so future bad OHLC, missing fields, and duplicate records cannot erase known
prefix features. Unlocatable/noninteger timestamps are retained conservatively
as unknown source because they cannot be proven to be outside the prefix.
Input records are not mutated. No thresholds, bins, predictive conclusions,
or new strategy filters are selected by this module.
