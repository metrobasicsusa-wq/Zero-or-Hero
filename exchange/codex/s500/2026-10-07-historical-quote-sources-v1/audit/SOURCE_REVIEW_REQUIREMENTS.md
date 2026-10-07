# Historical quote source and request-plan audit criteria

This stage can identify supported products and a bounded request plan. Official documentation, an advertised archive, a successful cost estimate, an authenticated download, and an independently inspected data sample are separate levels of evidence. No market-data entitlement or working access follows from public documentation alone.

## Preserve the prior study scope

The previous stage contains 40 underlyings × 10 month-first dates = 400 discovery cells and 800 call/put rows. There are 598 selected contracts, including 198 with no trade returned in the one-minute window, plus 202 unresolved rows. A follow-on source probe must retain all denominators and reasons; choosing only the 400 rows that happened to contain trades would select on outcomes. The 190-session calendar is an inventory; only ten sessions were sampled.

Retain original contract selections and labels. New-source historical discovery is a separate version, not an overwrite of the original unresolved rows. Current directory membership, current inactive contract records, later corporate-action metadata, and a surviving current option root are not a point-in-time master.

## Required quote and identity fields

- Dataset/feed/schema and version; data publisher; official schema and condition/flag mapping; actual historical NBBO versus venue BBO versus snapshots or sampled intervals. Trades are not quotes.
- Raw instrument identifier, vendor symbol and symbol-mapping validity interval, underlying identity, option right, expiry and last trading timestamp, strike scale, explicit premium multiplier, deliverable components, adjustment/effective timestamps, exercise/settlement type. Unknown fields remain unknown.
- Event timestamp, receive timestamp when available, capture timestamp, units, timezone, precision, chronological sequence, correction/cancel handling, duplicate policy and ordering. A historical event timestamp is not the time this session downloaded it.
- Bid and ask prices and both sizes, their units, venue/publisher and market state, quote flags/conditions, zero/missing/crossed/locked conventions, and verification of whether sides belong to a valid consolidated quote state. Provider-normalized flags cannot be assumed to use Alpaca's letters.
- Exact request start/end semantics, exchange calendar and early close, pagination/continuation completeness, empty/truncated/error results, source hashes and retrieval times. Ask whether the first response includes the state preceding the interval or only in-window updates; stale state cannot be carried indefinitely.

## Separate acquisition smoke tests from execution-path research

An entry-window probe (for example 10:00–10:01 ET) can establish sample coverage and hypothetical affordability only. It cannot establish a stop, target, close, expiration liquidation, or whole-year return. A later execution study requires a separately registered decision schedule and continuous, sufficiently granular quote state from before entry through each possible exit, including open positions across sessions. It must define latency, decision-time versus receive-time availability, quote age, gaps/halts, missing exits, transaction costs, expiry/exercise avoidance, and quantity constraints before viewing performance.

At each decision, select only quote state available by the decision under the declared latency model. Choosing a favorable next quote after the decision is lookahead unless the delayed execution rule was fixed in advance. Displayed size and best bid/ask do not guarantee fills. Sampled one-minute NBBO data cannot reproduce every intervening stop/target sequence and must not be described as full tick history. For multileg structures, synchronized side quotes and a fill model are additionally required.

## Costs, licenses and credentials

Document each provider's current official coverage dates, access prerequisites, schema and pricing unit, as well as unresolved endpoint or plan differences. Preserve the distinction between documentation time and the intended 2026 data cutoff. A subscription price or general per-GB rate is not an exact project quote.

For Databento in particular, require official evidence for each definition/symbology mechanism, whether separately requested datasets incur charges, and whether a cost estimate includes all intended schema requests. Do not label an estimate comprehensive when it covers only one quotes schema. Historical discovery may require a master stream and symbol mappings even if the later quote request uses fewer selected contracts. Do not assume a derived snapshot schema preserves all source fields.

Read-only historical downloads can still create a financial charge. No purchase, paid export, card entry, legal agreement, professional-status attestation, order, funding change, or new scheduler is authorized by this audit. Never send Alpaca-scoped credentials to a new provider. Configure any later provider key through the supported secure environment settings; inspect only key names/presence. A network secret binding is not proof of subscription, market-data license, or readiness. Raw licensed data, account identifiers and credentials stay private; publishing aggregate research does not establish raw-data redistribution rights.

## Required final labels

Report available evidence as documented capability, priced but not accessed, licensed and downloaded, or independently inspected. Keep unknowns and blockers explicit. If this stage is documents plus a request plan, report zero acquired historical quotes and zero new strategy paths. Prefer a concrete next data-acquisition step conditional on verified access and costs, without claiming a strategy is now enabled.
