# Flush/rebound pure engine API

`flush_engine.py` uses Python standard library and Decimal arithmetic at 42-digit
precision. It performs no network requests or trading. Economic parameters are
the fixed study design, not a fitted strategy. Returns are decimal strings.

Input bars are `{integer_minute: {"t": integer_minute, "o": ..., "h": ..., "l": ...,
"c": ..., "v": ...}}` or a sequence of such bars. Minute 0 is 09:30 ET. A bar's
timestamp is its opening minute; its close becomes available at minute `t+1`.
Prices must be positive finite numbers, volume nonnegative, and OHLC coherent.
Boolean numeric values, NaN, infinity, duplicate minutes, and missing fields are
invalid. Data at an identifiable later minute cannot invalidate an earlier
prefix/window. Unlocatable or mismatched timestamps invalidate the entire source
because their affected minute is unknown. `source_complete=False` explicitly
marks incomplete acquisition; only literal `True` counts as complete.

`prepared = prepare_bars(bars, source_complete=True)` validates once. Pass either
the original bars or `prepared` to all functions. Treat `PreparedBars` mappings as
immutable. Source-completeness false cannot be reversed by a subsequent default.

- `evaluate_prefix(bars, threshold, *, source_complete=True)` returns `status`,
  `is_flush` (true/false/null), `opening_open`, `first30_min_close`,
  `opening_drop_fraction`, and coverage (missing/invalid minute lists). It requires
  minutes 0–29 inclusive. Status: `flush`, `no_flush`, `unknown_prefix`, or
  `unknown_source`. Flush means minimum close <= minute-0 open × (1-threshold).
- `family_decision(bars, threshold, family, *, session_minutes=390,
  source_complete=True)` returns `prefix`, `status`, `signal_minute`,
  `decision_minute`, `entry_minute`, `signal_close`, `prior_running_min_close`,
  `observation_end_minute`, `unknown_minute`, and `reason`. `family` is `FIXED` or
  `REBOUND`. FIXED observes through minute 29 and enters minute 31. REBOUND starts
  with a first-30-minute flush, then scans minutes 30–89 inclusive; the first
  close >= 1.01 × minimum close of strictly earlier observed minutes is its
  signal. The signal bar cannot make a new minimum. Entry is signal+2, leaving a
  full intervening minute. Missing or invalid observation before the first
  signal returns `unknown_rebound_prefix`, with no subsequent-gap skipping.
  Remaining statuses: `signal`, `no_flush`, `valid_no_rebound`, prefix unknowns,
  or `invalid_session`. Later bars do not alter an already-formed signal.
- `evaluate_window(bars, entry_minute, exit_minute, cost_bps, *,
  session_minutes=390, source_complete=True)` is shared with controls. It requires
  complete valid bars throughout `[entry, exit]`, including both endpoints, and
  `0 <= entry < exit < session_minutes`. It returns `status`, `coverage`,
  `entry_open`, `exit_open`, `gross_return`, and `net_return`; missing data leaves
  all prices/returns null. Statuses: `ok`, `unknown_window`, `unknown_source`,
  `invalid_window`. Cost formula is
  `exit_open*(1-cost/10000)/(entry_open*(1+cost/10000))-1`. The `price_kind` is
  `minute_open_proxy`, costs are hypothetical per-side scenarios, and
  `verified_fill` is always false. Full exit-bar validity is a conservative data
  coverage rule, not information used for signal formation or execution.
- `evaluate_day(bars, *, session_minutes=390, source_complete=True)` returns all
  36 rows: thresholds 0.02/0.03 × FIXED/REBOUND × horizons `30m`, `60m`,
  `close_minus_10m` × per-side costs 5/10/25 bps. Entry-relative exits use exact
  minute +30/+60. Last horizon is `session_minutes-10` (380 or 200 on a 210-minute
  early close). Non-signals and unknowns remain rows with null returns.

No compounding, capital ledger, option returns, or verified fills are generated.
Malformed economic parameters raise ValueError. Known invalid market inputs
produce explicit unknown/missing statuses instead of zero returns or filling
from adjacent minutes. Primary configuration is threshold 0.02, REBOUND, 60m,
10 bps per side; all configurations must remain in output and reporting.
