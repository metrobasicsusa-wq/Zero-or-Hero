# Rechecking this study

Python 3.12 standard library is sufficient. Preserve the frozen study design, input bindings, original logs and all results. This package includes derived outcomes and source hashes; it does **not** contain all licensed raw market inputs. Full source reconstruction therefore needs the corresponding private inputs and permissions. A later provider query may return revised data and must not be described as byte-identical reproduction unless hashes match.

Large JSON/JSONL artifacts are stored as deterministic gzip files for transport. `artifact-manifest.json` lists their original names, original SHA-256 and compressed SHA-256. Expand them into a separate working copy before running tools that expect an uncompressed name. Existing `results/*.jsonl.gz` and `case-diagnostics/*.jsonl.gz` remain compressed.

## Public results without fetching market data

Run `summarize_flush.py --input results/*.jsonl.gz --calendar calendar-2026-ytd.json --registry selected-case-registry.json --output-dir reproduced-statistics`. It validates all 36 variants for every selected case and recreates the descriptive tables and date bootstrap. Creation timestamps differ; compare statistical content. `primary_concentration.py` provides descriptive contribution accounting and never selects a new strategy. Run such tools in a copy to preserve the published outputs.

The independent `independent_summary_audit.py` reconstructs the statistical results without importing the production summarizer. It checks all groups, months, halves, daily records and bootstrap values. See its CLI and default paths before running in a copy.

## Method and source checks

The pre-analysis validation record binds 160 passing synthetic/causal tests and the final source hashes. Test command:

```
python -m unittest -v test_flush_engine independent_engine_tests test_summarize_flush independent_stats_tests test_normalize_bars independent_normalization_tests test_run_flush
```

Raw SIP pages, normalized per-date files, prior daily bars and exchange directory captures remain private. Their relative names, byte sizes and hashes are retained in the source manifests. With those identical inputs restored, `verify_acquisition.py`, `independent_source_audit.py` and `independent_scope_audit.py` verify source reconstruction; `independent_actual_audit.py` reconstructs every signal, return and selected control directly, without the production engine or timestamp normalizer. The audit is not a certification of broker execution or of the provider's historical accuracy.

`prepare_inputs.py` and `register_study.py` document the original selection and registration procedure. Do not rerun them over the original frozen files. `collect_minutes.py` is an acquisition tool requiring the managed session's configured credentials and authorized domains; it is not required for public summary reproduction. No credential value is included. The initial acquisition binding and the replacement binding are both retained: timestamp validation was corrected before the first GET, without changing economic rules. The early independent statistics test binding and the later final-code binding are likewise preserved; the pre-analysis 160-test record already binds the final production code before outcome calculations.

Every result is a retrospective stock price-proxy event outcome with hypothetical costs. No row is an actual FILL, an option return, or a $500 wealth path. Current-universe selection, missing-minute observability, historical revisions and retrospective hypothesis formation limit inference. The stage decision should be read with the Chinese report, not inferred by ranking the best parameter row.
