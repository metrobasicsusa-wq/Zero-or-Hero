# Pure option feasibility analyzer

`option_feasibility.py` performs deterministic contract and quote checks and Decimal arithmetic. It has no network, filesystem, account, order or runtime-clock calls. It does not compute a strategy return or an account wealth path.

## Entry points

```python
analyze_contract(contract, observation=None, policy=None)
evaluate_quote(contract, quote, observation, policy=None)
evaluate_trade_price_proxy(contract, price, observation=None, policy=None)
```

`contract` uses the raw provider fields `symbol`, `root_symbol`, `underlying_symbol`, `type`, `style`, `status`, `tradable`, `expiration_date`, `strike_price`, `multiplier`, `size` and `deliverables`. Optional open-interest and close-price descriptive fields are retained. UUIDs and arbitrary fields are removed through a nested allowlist. `multiplier` is the sole source for premium multiplication; neither `size`, deliverables nor an encoded symbol supplies a missing multiplier.

Contract eligibility requires complete identity, consistency between the encoded symbol and expiry/right/strike/declared root, active/tradable current metadata, valid expiration relative to the observation's New York date, and standard 100-share equity metadata. The standard test requires multiplier 100, size 100, one matching underlying equity deliverable of 100 shares with 100% allocation, explicit non-delayed settlement and present settlement codes. Other contracts remain distinct and cannot upgrade primary eligibility by supplying a self-certified adjusted-contract flag. Any such raw assertion is recorded only as `provider_asserted_adjusted_proof`; `deliverables_independently_verified` is always false because this stage has no independently verified adjusted-contract evidence channel. These checks do not establish historical listing completeness, independent OCC specification verification, exact expiration cutoff or funded exercise readiness.

`quote` is the single supplied latest provider record with fields `t`, `bp`, `ap`, `bs`, `as`, `c`, `bx`, `ax`. There is no fallback to any older or cheaper quote. A list is unsupported and yields missing-quote eligibility; callers must never preselect an older valid observation after finding the latest invalid.

For `evaluate_quote`, observation fields are:

| Field | Meaning / required value for primary quality |
|---|---|
| `received_at` | Explicit timezone-aware response-observation time, up to 9 fractional digits |
| `quote_symbol` | Required matching contract identifier; alternatively a matching `quote['symbol']` |
| `feed` | Literal `opra` |
| `real_provider_bid_ask` | Strict boolean `True` |
| `market_open_at_observation` | Strict boolean `True` from the caller's checked calendar |
| `quote_size_units` | Literal `contracts` |
| `size_units_verified` | Strict boolean `True`, requiring separate provider mapping evidence |
| `condition_mapping_verified` | Strict boolean `True`, requiring separate provider mapping evidence |

Unknown flags never become truthy by numeric or string conversion. The condition must be the literal ASCII space or `A`; an empty string is unknown. Bid and ask must be finite and positive, uncrossed, and within 10% spread divided by midpoint. Both sizes must be positive integers. Quote age is calculated using integer nanoseconds and must be between zero and five seconds inclusive. A future quote by one nanosecond is rejected. Current snapshots use their actual observation date, not the historical study cutoff.

The frozen optional policy keys are `budget='500'`, `roundtrip_reserves_per_contract=['0','0.10','1']`, `max_age_seconds='5'`, `max_relative_spread_mid='0.10'`, and `study_cutoff_date='2026-10-05'`. Changing the registered budget, reserves or quality thresholds raises `ValueError`.

## Outputs and interpretation

`evaluate_quote` returns a sanitized contract analysis, quote fields, exact quote/observation timestamps in nanoseconds, each quality criterion, blocking/unknown reasons, and `primary_quote_quality_eligible`. It does not certify an actual fill, historical decision-time receipt, settlement or a tradable strategy. Indicative/delayed feeds remain blocked even when their price arithmetic is finite.

`affordability_scenarios` remains separate from quality. For each reserve, the whole-contract count is `floor(500 / (positive_ask * explicit_positive_multiplier + reserve))`. Missing/invalid multipliers or asks produce null values, not zero account wealth. All reserves are hypothetical per-contract round-trip sensitivity inputs, not a broker fee schedule. The illustrative same-quote ask-to-bid friction is `(ask-bid)*multiplier + reserve`; a nonpositive or crossed bid produces null friction. This calculation is not an executed liquidation or a strategy PnL.

Displayed ask-size and two-sided caps are produced only when contract-unit mapping is explicitly verified. They are observations, not guaranteed capacity. Affordability can be true while quote eligibility remains false.

`evaluate_trade_price_proxy` applies the same whole-contract arithmetic to a separately labeled historical trade/bar price. Pass the event-date observation in `received_at` when reporting its historical expiration relationship. This timestamp is a reference-date input, not verified historical quote receipt. The returned fields explicitly state that no ask, spread, quote age, exit capacity or actual execution has been established. Current contract metadata remains a retrospective reconstruction even when the trade itself is historical.

No delta is inferred from moneyness, and no adjustment, exercise, automatic expiration liquidation or multi-leg financing is synthesized.
