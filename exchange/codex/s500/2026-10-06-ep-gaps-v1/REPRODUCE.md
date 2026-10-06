# Missing-minute diagnostic reproduction

This is a post-outcome input diagnostic of all 38 first missing-held-minute coordinates from 414 incomplete records in the parent study. It is not a new strategy backtest, full-year data-quality certification, or fill log. The original 876 results and 24 incomplete portfolio paths are unchanged.

Parent: commit `b29d77405cacc21cb2db1ff174e4ca6f32cd9b86`, directory `exchange/codex/s500/2026-10-06-ep-paths-v1/`. The frozen proposal is the parent's `next-input-diagnostic.json`. `study-design.json` binds that proposal, prior results/inputs/code and this stage's `points.json` before market-window collection.

## Layout and inputs

Python 3.11+ standard library, tested on Python 3.12. The original sibling names are `s500-ep-gaps-20261006` and `s500-ep-paths-20261006`. Unpack public `.json.gz` files before local analysis. Reproduction requires the private raw market and official-document archives matching their recorded hashes, plus the parent's `holding-market.json`. The public package alone is **not fully reproducible**; current network responses may contain historical revisions.

`collect_windows_initial.py` is the code used to obtain the original 114 market windows. `input-manifest-initial.json` preserves that collection's metadata. `collect_windows.py` adds cache integrity checks after independent tests identified unsafe outcome reuse. The final manifest's `collection_provenance` distinguishes collection from the later zero-network cache verification. Old and new code are preserved rather than relabeling the initial requests as executed by the revised implementation.

Only fixed GET routes on `data.alpaca.markets` receive the configured network credentials. The collector has no broker or order endpoint, rejects redirects, retains inherited proxy/CA trust, caps pages and retries, and saves raw records privately. Official-document and halt-query scripts use public requests without market credentials. Do not print environment secrets or publish raw trade IDs, full feeds or full article bodies.

## Running with matching archives

```sh
python collect_windows.py
python analyze_windows.py
```

The first command normally verifies and reuses this completed cache. Missing caches cause the fixed read-only requests; a newly fetched version belongs in a separate versioned study, not over this completed publication. The second command verifies parent hashes, the final input manifest, page hashes and exact request/token chains, then produces `gap-diagnostics.json` and `gap-summary.json`.

Independent audit/test scripts and their logs are included. They verify selection from all 876 parent records, nanosecond boundaries, canceled/incorrect versus corrected trade flags, literal-space/unknown trade conditions, pagination and cache tampering, all actual inputs, all 38 derived diagnostics, and unchanged parent files. See the final audit report for the executed checks and any remaining source limitations.

## Interpretation

Intervals are exactly `[target−120 seconds, target+180 seconds)` and target `[target, target+60 seconds)`. API end is the inclusive upper boundary minus one nanosecond. Out-of-window and invalid observations remain in raw archives and invalidate a complete absence claim. Trade identities are checked privately; public output gives counts, not identifiers. Duplicate records are not silently merged.

Rules in `source-rules.json` are derived from current official documentation, with source body hashes. Trade condition descriptions use returned tape A/B/C metadata. Multiple conditions apply the most restrictive documented rule; empty or unknown conditions do not default to regular trades. Canceled/incorrect records do not contribute valid trade statistics; corrected records describe the current historical endpoint version, not when the correction arrived.

The current FAQ and the older minute-bar article disagree on SIP versus participant time. Bucket counts use the returned timestamp and cannot certify causal reconstruction of official bars. Requests inherit default current-symbol mapping (`asof` was not explicitly disabled), so a single requested/returned symbol is not proof of historical point-in-time identity.

Thirty-six missing targets contain only observed price-excluded trades, consistent with normal absent-bar behavior. Two have no returned trades. No target bar reappeared, and no parent PnL or fill was reconstructed. Quote activity is not proof of liquidity, no halt, stop triggering or execution. An empty or failed official halt query is not proof of no halt. The next-stage proposal is explicitly unexecuted and must not be confused with validated trading rules.
