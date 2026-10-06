# Independent final audit: EP holding and capital paths

The saved outputs pass the implemented accounting and source-reference checks. No unresolved blocking implementation or reconciliation discrepancy was found. This does **not** certify a complete annual strategy return, profitable strategy or executable fills.

All **900 results** were audited independently of the simulator: 876 isolated case/variant records and 24 capital-path records. The checks reconciled 1,168 modeled trades (808 purchases and 360 sales), 2,732 daily snapshots and 996 MA decisions against cached prices and exact decimal cash. Whole-share quantities, signal-time budgets, no resizing at the next open, costs, T+1 receivables, exit timing, marks, drawdowns and milestone fields reconcile. All **59 tests** pass: 36 implementation tests and 23 independent tests.

The entire 1,079-row decision-evidence join, all 24 summary groups and the primary classification lock were checked. The lock predates the first saved simulation start, its source hashes remain unchanged, and the independent audit changed no labels. Every numerical group summary matches recomputation from the saved records (absolute numeric tolerance 1e-10).

The isolated results retain **360 completed buy/sell models, 68 complete no-entry results, 414 incomplete held paths and 34 censored positions**. The 12 variants share the same 73 cases. Hypothetical $500 is reset for each isolated diagnostic; those rows are not a continuous wealth curve or 876 independent observations. Every completed-case group mean is negative, but missing and censored records prevent a full-cohort return claim. “Unconfirmed news” does not mean proven absence of news. Cost variants may enter different subsets at integer budget boundaries.

All **24 capital paths remain incomplete with null terminal wealth and profit**. The 12 full-universe paths stop January 2 on screen coverage. The 12 explicitly cohort-conditional paths stop the same day because BIDU's catalyst qualification is unknown. A known outside-category story cannot establish that no qualifying story existed. No path is presented as an unchanged $500 account or zero annual return.

Source auditing verified all 358 new minute pages, 104 original daily input hashes, reconstruction of daily/action inputs and the 6,778 original entry-prefix bars against the fresh holding input. The 112 repair windows remain unknown with no conflicting overlaps or newly passing cases. Successful request completion does not imply every minute is present.

All **29 newly positive news cases and 31 primary source bodies** passed independent hash and strict-window checks. Nine source bodies do not declare revision times; current publication metadata is not a contemporaneous immutable archive. CRM's one mismatched cached body remains quarantined and is not used to establish a positive gate. See `primary-source-independent-audit.json` and its Markdown note.

Earlier findings are preserved in `audit-checkpoint.json`: MA failure timestamps previously preceded the last observed mark; root fixed all three offsets and regression tests pass. The eager signal-close fallback was also fixed. Current source coverage, non-point-in-time universe construction, daily-open prescreen differences, company-action coverage, assumed availability of vendor daily closes and historical source revisions remain research limitations.

OHLC reference prices plus fixed modeled costs do not prove order fills, executable spreads or historical reception. No orders, resets, additional contributions, network calls or changes to primary classifications were made by this final audit. Full reproduction requires private raw input archives; public hashes and reports alone are insufficient.

Detailed evidence is saved in `actual-path-ledger-audit.json`, `actual-summary-independent-audit.json`, `primary-source-independent-audit.json`, `source-audit.json`, `entry-prefix-source-audit.json` and `independent-final-tests.log`. `final-independent-audit.json` binds the inspected code, inputs, reports and test log by hash. Offline rechecks use `python audit_actual_paths.py` and `python audit_primary_sources.py` with matching caches.
