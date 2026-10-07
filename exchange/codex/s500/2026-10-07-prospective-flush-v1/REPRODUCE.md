# Reproduce this registration, not nonexistent future results

Use Python 3.12 with the standard library and the America/New_York time-zone database. Restore logical filenames from artifact-manifest.json, decompressing only files marked as transport compression. Verify every original SHA256 before running. Keep this stage in an isolated directory outside the existing tracked checkout; a new Git worktree is unnecessary.

The public files contain the frozen panel, full calendar schedule, registered rules, source hashes, method code, synthetic tests and an empty future observation ledger. There are zero observed future price dates and no future return simulation to reproduce. Tests use explicitly injected fake transports and synthetic prices; they are not historical fills or actual future observations.

Run the exact validated set of 133 offline tests:

```bash
python -m unittest -v \
  test_prospect_selection test_protocol_tools test_capture_prior20 \
  independent_selection_tests independent_protocol_tests \
  independent_capture_precision_tests
```

The counts are 34 selection, 29 protocol, 33 capture, 16 independent selection, 17 independent protocol and 4 independent raw-JSON precision tests. This includes a full 6,633-symbol synthetic acquisition and adverse cases for request completion, date windows, hashes, authorization failures and decimal thresholds. Do not replace the original bound logs when saving a new test run.

`capture_prior20.py` is a real GET-only CLI, not a test runner. Calling it at the wrong date is intentionally rejected before credential lookup or network creation. Calling it on a permitted future date can acquire private market data. Use OPERATIONS.zh-CN.md and the existing configured secret bindings; never supply secrets in command-line arguments or files. Do not rerun a completed capture into a different directory to select a preferable version.

The calendar was acquired by one read-only request; the private raw response is not included. Its normalized schedule and raw-byte hash are public. `independent_input_audit.py` additionally reconstructs the panel from ancestor files and checks the saved private calendar response. Repeating that complete source audit requires the exact ancestor bytes and saved response; a new calendar download may differ and is a new source version. The public synthetic test suite does not need these private dependencies.

The final protocol hashes its production collector, selector, receipt gates, panel, calendar and inherited economic modules. Those original files must remain byte-identical. `pre-registration-validation.json` binds the pre-registration draft, preserved as `protocol-draft-before-registration.json`; `protocol-registration.json` binds the final `protocol.json`. The expected hash difference is the explicit transition from draft to registered plus the final method bindings and registration time, not an undocumented rule change.

`build_protocol.py` and `register_protocol.py` show how this version was prepared. Do not rerun them over a live registered version or alter its recorded time. A new study needs a new version, future start date and supersedes/parent references. `observation-ledger.json` remains the immutable initial state; later acquisition and review records must be appended as separately identified versions.

The stock signal, prefix, normalization and distribution modules are inherited by exact byte hashes from the published parent studies. They do not themselves provide a connected daily collector and analysis service. This stage implements preopen collection and postclose metadata checks; it does not deploy an automatic postclose runner, schedule AI activity or demonstrate executable options returns.
