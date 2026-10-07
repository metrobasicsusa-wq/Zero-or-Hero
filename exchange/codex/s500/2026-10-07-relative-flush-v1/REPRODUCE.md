# Reproduce the derived classification and payoff study

Use Python 3.12 and its standard library. No installation, server or account operation is required for the offline analysis.

The parent study is immutable commit `634a93c65e1adec6c5908f9ad249c5b8e6224ba8`, directory `exchange/codex/s500/2026-10-07-flush-rebound-v1/` in `metrobasicsusa-wq/Zero-or-Hero`. Its full case registry, 36-variant result files, code and source hashes remain available there. This new package supplies all 19,519 case classifications, the joined 702,684-row derived ledger, all group summaries and independent audits. Parent outcomes are reused without modifying signal timing, exits, costs or selected peers.

The parent licensed minute/daily pages are not republished. Full source classification reconstruction requires those original private files and matching hashes. Requerying a provider later may yield revised data; matching date/symbol queries do not establish byte-identical reproduction. Public results can still be reaggregated and their interpretation inspected without making new market requests.

`artifact-manifest.json` records each original filename/hash and any deterministic gzip transport wrapper. Restore large JSON/JSONL files to their original filenames in a separate working copy; keep files whose original name already ends in `.gz` compressed. Existing published outputs must remain unchanged.

The production pipeline is `classify_inputs.py` followed by `analyze_relative.py`. Each expects the prior stage as sibling directory `s500-flush-rebound-20261007`, checks source bindings, and requires the stored validation/authorization files. The classification runner reads original minutes using copied, hash-identical `vendor/` normalization and OHLC validators. The statistics runner consumes the fixed parent ledger plus classifications. Do not overwrite the publication while reproducing; output timestamps will differ, so compare numerical content and source hashes deliberately.

Synthetic tests used before actual new classification and grouping:

```
python -m unittest -v test_classify_relative test_return_distribution test_analyze_relative independent_classifier_tests independent_relative_stats_tests
```

All 107 tests passed. Independent source classification and final-statistics audit code reconstructs the classifications and summary arithmetic separately from production functions; consult each script's interface and expected inputs before executing in a copy.

`METHODS.md` describes denominators, date weighting, matched comparisons and limitations. `HYPOTHETICAL_PAYOFF.md` contains arithmetic examples only. It is not an option backtest. User acceptance of low win rates does not establish the probabilities or payoff ratio of any particular trade. The experiment does not calculate a $500 capital path, ruin probability or $10,000 target probability.
