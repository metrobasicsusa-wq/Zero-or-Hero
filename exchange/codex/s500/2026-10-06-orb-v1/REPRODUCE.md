This package preserves the actual Codex research code, all twelve continuous-path failures, all 1,140 independent diagnostic episodes, candidates, input hashes, methodology amendments, and audits. It is not fully reproducible from public files alone: the authorized raw SIP bars, full corporate-action inputs, and historical source revisions are not bundled.

Do not run broker/executor code. Every network call in the research files is a bounded market-data GET or public-document request; no order API is part of this study.

Restore this study as a directory named `s500-orb-20261006`. Its exact sibling dependency is the prior study `s500-aggressive-20261006/features.py`; `dependency-prior-features.py` contains the bytes to restore there. The sibling broad study is `s500-broad-20261006`, containing the authorized original `universe.json`, `current-instrument-classification.json`, `input-manifest.json`, and `raw/` daily/calendar pages. The aggressive sibling also supplies its sanitized `company-actions.json`; the web-study sibling `s500-web-research-20261006/experiment-registry.json` supplies the registered method. These layout requirements reflect the actual execution and are not a claim that a checkout alone contains all dependencies.

The earlier method registration is commit `1af6a6d741b1d6b1b561f1bfd3aa29f50d7801fe`; the prior daily-study publication is commit `2d7077d0ebc5bd5cf9ac78bc6086f6bea3850214` in the same shared repository. Verify required hashes before reusing restored data. Gzip files are deterministic (`mtime=0`), and manifest.json records both compressed and uncompressed SHA-256.

Offline verification order:

1. Restore authorized source inputs and compare their bytes against the supplied manifests and code/source hashes. Current provider responses may differ from the captured inputs.
2. Run `python -m unittest -v test_features test_engine test_audit test_coverage_bounds` (75 tests for the published code).
3. `features.py --prepare` rebuilds prior-only daily eligibility. `download_inputs.py openings` gathers narrowly scoped opening data, with checkpointed and hashed pages. Credentials belong only in the cloud secret configuration using the names referenced in market_data.py; never put values into reports or commands.
4. Call `features.rank_openings` using prepare.json and opening-market.json, save ranked.json and ranking-diagnostics.json, then run `coverage_bounds.py`. Preserve strict diagnostics alongside mathematical proofs; no missing volume is replaced by zero.
5. Run `download_inputs.py intraday`, which stores the selected 3,800 stock-days. Preserve actual calendar early closes and DST handling. Successful pagination alone does not establish market coverage.
6. Run `run_research.py` only after both input manifests are complete and all 190 dates are represented in both coverage gates. It retains the original six strict paths and six separately registered pre-entry-gap sensitivities. Reproduce `final_equity=null` on the captured inputs; do not replace null with the last observed equity.
7. Run `audit_engine_external.py` after the twelve path files exist to independently reconcile them and compare 5Min/1Min inputs.
8. `daily_diagnostic.py` is a deliberately post-failure diagnostic. Its six parameter groups each reset a statistical one-day episode to500. Never concatenate these episodes, add their PnL to500, or describe them as funded restarts or an investment return. Retain all skipped/incomplete episodes.
9. `quote_probe.py` probes only PRCT's first held-data gap. It does not repair the bar strategy, simulate fills, or establish quote availability for other dates or options.

The current/inactive directory, present-day classification, implicit current `asof` symbol mapping, possible historical aliases, incomplete corporate-action identity information, and idealized OHLC stop execution remain limitations even if all tests pass. Future work must be a new version; preserve this record and use `supersedes` for factual corrections.
