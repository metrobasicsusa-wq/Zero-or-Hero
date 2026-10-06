# Reproduction and interpretation

This stage covers 2026-01-02 through 2026-10-05. Every one of the original 73 market-pass cases has all 12 registered variants, including failures, no-entry cases and censored positions. It also contains 12 full-universe and 12 explicitly cohort-conditional portfolio diagnostics. All 24 portfolio results are incomplete; their terminal wealth and profit are null.

The 876 isolated cases each begin with hypothetical $500. They are not sequential restarts, independent statistical observations or a continuous portfolio. Repeated variants share events. Cost-stressed OHLC prices are not actual broker fills. Read `REPORT.zh.md` and `run-summary.json` before comparing results.

## Dependencies and private inputs

Python 3.11 or later, standard library; tested with Python 3.12. The original sibling directories are `s500-ep-paths-20261006`, `s500-news-20261006`, `s500-quote-20261006`, `s500-orb-20261006`, `s500-broad-20261006` and `s500-aggressive-20261006`. Unpack published `.json.gz` to its original `.json` filename before analysis.

The direct parent is commit `06a84d57cdd91b9b2352ec33a2dee74f594873ab`, path `exchange/codex/s500/2026-10-06-ep-news-v1/`. Required parent files include `download_inputs.py`, `market_gates.py`, `market-gates.json`, `candidate-readiness.json`, `news-enriched.json`, and the raw entry-prefix input archives. The parent's REPRODUCE.md documents quote/ORB dependencies, their immutable commits and the earlier broad-universe construction. This stage additionally uses the original broad daily bars and the improved corporate-action cache; their hashes and coverage caveats are in `cached-source-manifest.json`.

Public files do not contain full raw market/news feeds, raw announcement bodies or the private assignment headline/summary corpus. Exact reproduction requires the matching private archives. Current provider requests may return different bytes or historical revisions; never silently replace a registered input. The public package therefore is **not fully reproducible on its own**.

## Execution

Preserve the frozen `study-design.json`, `holding-input-design.json` and `primary-classification-lock.json`; do not regenerate them over this completed study. `collect_inputs.py` calls the parent's market-data-only transport with configured network credentials and inherits the managed proxy/CA. It has no order route. The frozen manifest identifies 112 exact-symbol repair windows and 246 holding batches. The collection creates private `holding-market.json`, `repair-market.json`, `daily-market-private.json`, and public source manifests. A response completing successfully is not proof of dense minute coverage.

With the matching layout and private input archives:

```sh
python collect_inputs.py
python evaluate_repair.py
python -m unittest -v test_ep_engine audit_ep_paths
python run_paths.py
```

`run_paths.py` verifies the primary classification lock before computing outcomes. The authoritative dispositions, identified by SHA-256 hashes, are the saved primary group files and `all-new-primary-reviews.json`. Source fetch/assembly helper scripts document the research process; manual source assessments cannot be regenerated solely from downloaded HTML. Re-running `complete_primary_reviews.py` would write a new lock and must not overwrite this completed stage.

`audit_actual_paths.py` exposes `audit_path(result, market, daily, calendar)` to independently reconcile each saved output without rerunning the engine. `audit_ep_paths.py` provides independent test and source-audit routines. The final independent audit files record the actual 900-output check, summary reconciliation, lock ordering and evidence scope. Inputs needed for full source auditing remain private; publication preserves hashes and aggregate checks.

All account values in result ledgers are hypothetical. Missing held minutes halt with an unresolved exposure. Terminal receivables remain assets but are not immediately spendable; no same-day exit re-entry. Relevant unsupported corporate actions halt rather than disappear. Censored open positions have no certified realized final profit. Daily vendor closing prices are assumed available for next-open MA decisions; contemporary receipt/revision times are not established.

## Versions and follow-up

Full-universe screening coverage and current/non-point-in-time instrument classifications remain unresolved. A candidate's known outside-category story does not establish the absence of another qualifying story. Source publication and modification metadata are current declarations, not an immutable historical archive. CRM's mismatched cached body was quarantined, not repaired to fit its metadata.

`next-input-diagnostic.json` is a bounded follow-up plan created after reviewing these outcomes. It selects missing observation coordinates, not profitable cases. It does not authorize interpreting repaired inputs as a new out-of-sample strategy test, change this version's results, submit broker orders, or configure a scheduler. Corrections must use new immutable files and `supersedes` references rather than overwriting published history.
