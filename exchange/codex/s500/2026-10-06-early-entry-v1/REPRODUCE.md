# Reproduction and evidence limits

This package records the technical OR5/OR15/OR30 early-entry experiment through October 5, 2026. The rules were frozen before the new gate and wealth runs. This date range has already been inspected in prior studies, so the experiment is exploratory. It is not independent or prospective validation.

The published files include all 3,237 family-candidate gate rows, every registered financial variant, the continuous account outputs, tests, methods, source/request hashes and independent audits. Larger JSON files are deterministic gzip archives. `manifest.json` identifies their original filenames and compressed/uncompressed SHA256 values.

Full raw minute/daily inputs, complete news/document bodies and credentials are private. The public package is **not independently reproducible from the public files alone**. Re-querying a vendor later can return revised bars, changed symbol mappings or different coverage; hashes establish which input version was used, not its real-time historical availability.

The scripts expect this stage beside the previously published `s500-news-20261006`, `s500-ep-paths-20261006`, `s500-ep-event-v2-20261006`, `s500-broad-20261006`, `s500-orb-20261006` and `s500-aggressive-20261006` research directories. Authorized private source caches and their manifests must be restored with their recorded hashes. The imported read-only transport preserves the inherited proxy and TLS configuration, fixed destination, bounded retries and pagination; it is never an order client.

To audit restored artifacts, run the synthetic unit tests and the independent gate/source/account audit scripts. The model tests use fabricated inputs and make no network calls:

```bash
python -m unittest -v test_early_models independent_gate_tests independent_engine_tests
```

`register_study.py`, `run_gates.py`, `prepare_inputs.py`, `collect_inputs.py`, `bind_pre_run.py`, `run_early_paths.py` and `diagnose_mechanisms.py` document the generation sequence. Registration and result writers deliberately refuse to overwrite prior runs. Reproduction should use a separate directory and preserve existing artifacts, including failed results, before producing a newly dated version. Do not rerun registration in a published directory or overwrite its timestamps to imply original preregistration.

The financial model uses Decimal $500 cash, integer shares, intent quantity fixed at signal completion, an additional one-minute latency to the entry open, T+1 settlement, no same-day exit/reentry, the family's already completed opening-range low as stop and a tenth-session last-minute-open exit. Both cost levels and cash fractions are retained. No account reset, new funding or actual broker fill is represented.

The strict data mode can advance only through the previously registered exact-coordinate absent-minute evidence. The provider mode requires a verified complete regular-session request and additionally assumes its returned aggregate price-event series is complete. Request completion does not prove all historical events, executable prices or stop fills. Execution at the required entry/exit minute still needs its exact reference. Only fresh daily marks enter drawdown and milestone diagnostics; an unknown path is not a zero return or a financial failure.

All original 1,079 candidates are retained in every family. Full-universe coverage and original-registry unknown guards are separate from retrospective ready-cohort diagnostics. The latter condition on available gate evidence and cannot establish an implementable full-market strategy. News labels are descriptive only; this experiment does not claim a verified catalyst or treat missing news as absence.

Source-version conflicts would conservatively quarantine the affected planned input window, even if a later conflict might not have been used before an earlier exit. Missing future request coverage instead fails at the time it is actually needed. Neither rule is an assertion of a live historical execution failure. The actual input collection in this stage found zero version conflicts and zero missing requested symbol-session scopes.
