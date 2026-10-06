# Reproduction and evidence scope

Python 3.12 standard library is sufficient. Nothing in the pure analyzer needs credentials, network access, broker access or a scheduler.

1. Download this publication directory, preserving `code-history/` and `correction-history/` subdirectories. `artifact-manifest.json` lists public compressed and uncompressed SHA256 values. `.json.gz` files use deterministic gzip; `replay_public.py` reads these directly.
2. Run `python -m unittest -v test_option_feasibility independent_option_tests` for the 37 author and 25 independent analyzer tests.
3. Run `python replay_public.py`. It replays all 880 published analysis payloads exactly, including all 2,640 fee-reserve scenarios, with no network calls. This does not recreate market data or validate executable liquidity.
4. Read `independent-actual-audit.json`, `independent-source-audit.json`, `audit-findings.json`, the three correction records, and the report before interpreting counts.

The original pipeline is included: capability probes, frozen sampling, collection, bindings, pure analysis, independent source audit and independent arithmetic audit. Collection scripts require legitimately provisioned environment credentials and provider permission; do not execute them merely to replay arithmetic. Raw broker responses are private. Their manifests expose request parameters, timestamps, pagination, hashes and byte counts, not raw credential/account/asset identifiers. Earlier broad equity data and calendar inputs also require the preceding research stages and authorized caches. Full acquisition is therefore not publicly self-contained.

Historical first returned trade prices are proxies. Conditions and correction semantics are not independently certified, and quote-side liquidity is absent. Current indicative quotes are altered/indicative data observed after hours. Neither can be substituted for historical OPRA bid/ask or represented as actual fills. A mathematically affordable premium is not proof of execution or profit.

Version history remains explicit:

- `pre-price-v1-*` preserves the collector before correcting documented deliverable field names; `pre-price-correction.json` records the timing before option-price requests.
- `code-history/` preserves earlier analyzer evidence-label behavior; `analyzer-evidence-label-correction.json` and both independent test bindings show that provider assertions are not independent proof.
- `correction-history/` preserves the first runner, pre-first-arithmetic binding and complete 800/80 row outputs. That attempt failed only while summarizing missing count values. `summary-null-correction.json` records the null-count fix. The current `pre-analysis-validation.json` explicitly follows the first actual arithmetic; it must not be described as the original preregistration. Independent audit checks the original and corrected row arrays are identical.
- Successful final test logs are published. Initial failed test/audit logs are retained privately because their stack traces include private filesystem paths; the correction records disclose failures and outcomes.

To reproduce the original corrected runner, decompress the public JSON files in a fresh scratch copy and remove only the generated result copies (`historical-reference-results.json`, `current-indicative-results.json`, `run-summary.json`) from that scratch copy. `python run_feasibility.py` checks its bound hashes and recreates the outputs. The output generation timestamps differ; analysis rows should not. The bundled `bind_analysis.py` describes the original pre-first-arithmetic registration and is not a tool to overwrite the current historical binding.

No script in this publication should be used to sign an OPRA agreement, submit orders, reset capital or enable trading. Those actions are outside this data-feasibility stage. No broker account was read here.
