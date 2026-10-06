# Event-based holding sensitivity v2

This completed experiment covers 2026-01-02 through 2026-10-05. It is an exploratory sensitivity analysis after viewing v1 outcomes and the first-gap study, not a forward test or brokerage fill log. All 73 original market-pass cases, both data modes and all 12 original parameter variants are retained.

The 1,848 outputs comprise 1,752 independently seeded hypothetical-$500 cases and 96 financed portfolios: original full-universe and news-precedence guards, retrospective market73 membership, and retrospective verified-news32 membership, each crossed with two modes and 12 parameters. Independent case resets are not sequential capital injections.

## Versioned inputs and dependencies

Python 3.11+ standard library, tested with Python 3.12. Original sibling directory names are `s500-ep-event-v2-20261006`, `s500-ep-paths-20261006`, `s500-ep-gaps-20261006`, and `s500-news-20261006`. Decompress published `.json.gz` files to their original filenames before analysis.

- Original holding study: commit `b29d77405cacc21cb2db1ff174e4ca6f32cd9b86`, `exchange/codex/s500/2026-10-06-ep-paths-v1/`.
- Gap study: commit `a441547e6d977fc2fcfbaebac7743a25235754b8`, `exchange/codex/s500/2026-10-06-ep-gaps-v1/`.
- News/gate parent: commit `06a84d57cdd91b9b2352ec33a2dee74f594873ab`, `exchange/codex/s500/2026-10-06-ep-news-v1/`.

`study-design.json` binds these inputs and the new 36-coordinate whitelist and 73/32 cohort membership before the first v2 outcome run. `ep_engine.py` is a byte-identical copy of the original Decimal account engine. New event behavior is in `ep_event_engine.py`; original published files are never modified.

Exact reproduction additionally requires private parent `holding-market.json`, `daily-market-private.json` and raw holding-page archives. Their hashes are preserved. Public derived ledgers, code and hashes do not supply the complete market corpus, so the package is **not fully reproducible on its own**. Historical current responses may be revised, mapped to current symbols or differ from the registered bytes.

## Offline execution

```sh
python prepare_event_inputs.py
python -m unittest -v test_ep_event_engine independent_event_tests
python run_event_paths.py
```

The preparer verifies 246 raw pages, request chains, hashes and cached assembly, with strict exact-minute timestamp syntax that rejects nonzero nanoseconds. It generates `verified-symbol-sessions.json` for 3,823 unique symbol-sessions. This proves stored request coverage, not an exhaustive historical event tape.

The runner requires the final engine and passing test counts bound in `pre-run-validation.json`, verifies all registered parent hashes, and refuses to overwrite an existing isolated output file. A deliberate rerun after code or input changes belongs in a new version with retained prior results. The saved validation record proves the tests completed before this recorded run; it must not be edited to bypass a failing check.

Outputs are `isolated-event-results.json`, `portfolio-event-results.json`, `all-candidate-registry.json`, `v1-v2-transitions.json` and `run-summary.json`. The runner applies explicit retrospective membership without changing any original news evidence. Full-registry coverage and unresolved-candidate guards remain separate results.

## Model boundaries

`evidence_gated` allows only the frozen 36 exact symbol/UTC-minute coordinates to advance held time without a returned price bar. `provider_event_series_assumption` permits absent held minutes only for verified complete regular-session requests and explicitly assumes the returned aggregate event sequence is complete. No synthetic bar or execution price is created.

Both modes require exact observed open references for entry, next-open MA exits and the fixed scheduled exit minute. Invalid bars, unsupported held corporate actions, unverified required execution prices and empty held sessions remain unresolved. A missing scheduled exit is not deferred to a favorable future tick.

Protective stops retain the original OHLC stress convention, not a broker execution model. Intrabar stop time denotes the source bar interval start, not a known trade timestamp. Cost assumptions of 25/50/100bp each side are not verified spread/slippage measurements. Both modes remain conditional, even after a fresh price appears following a gap.

Freshness certification fields refer only to source timestamp freshness and account arithmetic within the model. Stale or unresolved daily marks do not enter drawdown or milestone calculations. Use `observed_fresh_daily_liquidation_drawdown` together with evaluated/excluded snapshot counts; no eligible observations produce null in that dedicated field. The legacy maximum-drawdown field is retained for compatibility and is not evidence of a full zero drawdown. Terminal open positions are censored and have null realized profit, even when a fresh conditional market-value estimate is available.

Retrospective cohorts are selected using current evidence availability. Their financed path is neither a complete point-in-time market strategy nor proof the exact historical information was available. The 12 market73 provider paths still hold positions at cutoff; their marks are not realized final cash. The 12 verified-news32 provider paths are closed conditional models. All full-market and evidence-guarded portfolios remain unresolved.

Current default `asof` symbol mapping, stock-universe screening omissions, company-action coverage, current historical revisions, the official SIP-versus-participant time conflict and next-open vendor-close availability assumptions remain. No new market-data retrieval, broker order, automatic reinvestment contribution, restart or scheduler is part of this stage.

The final independent audit checks all outputs against sources, exact cash, T+1, gap permissions, valuation freshness, selection and unchanged parent results. Tests and audit scripts/logs are included. `peer-readonly-review` is a separate, unverified peer-literature note; it did not change the frozen experiment. Future protocol proposals are explicitly unexecuted.
