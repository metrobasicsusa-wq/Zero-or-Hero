# Databento and Massive public-evidence comparison

Target interval: 2026-01-02 through 2026-10-05 inclusive. This review used only official public documentation; no vendor account, purchase, credential, authenticated API or market-data retrieval was used. Source bodies remain private. Publishable results are in each vendor's `findings.json`, `findings.md` and `manifest.json`.

| Selection question | Databento | Massive (formerly Polygon.io) |
|---|---|---|
| 2026 historical quotes | OPRA.PILLAR identifier exists; start/end dates and dataset-specific schemas unverified | Endpoint docs give historical quotes since 2022-03-07; target interval nominally covered, individual days/contracts untested |
| Quote versus sampled data | Generic DBN source distinguishes consolidated BBO (`cmbp-1`), sampled BBO (`cbbo-1s`/`cbbo-1m`), and trade-event BBO (`tbbo`/`tcbbo`); OPRA availability unverified | Historical per-contract quotes and daily quote files; OPRA/NBBO described; exact raw-message preservation/conflation unverified |
| Timestamp/size/conditions | Generic nanosecond event/receive timestamps, bid/ask sizes, publisher IDs and flags; OPRA-specific mappings unverified | Nanosecond SIP timestamp, bid/ask size in contracts, exchange IDs and per-ticker sequence; reviewed quote schema lacks condition/participant/receive fields |
| Historical contracts | README claims point-in-time definitions; date-range symbology API documented; OPRA field population and split deliverables unverified | Historical `as_of`, expired discovery, shares_per_contract/additional_underlyings; full corporate-action lineage not guaranteed |
| Public price for historical quotes | Amount and plan unknown; authenticated metadata cost method documented | Options Advanced $199/month for eligible individual/non-professional use; Business $1,999/month with EOD historical access; exact total/appropriate entitlement unknown |
| Storage and redistribution | Applicable market-data terms unavailable as readable content; software Apache license is not a data license | Individual personal/non-business limits; Business subscription-end deletion and redistribution/model-training restrictions; actual user entitlement unresolved |
| Cloud readiness | Generic Python historical HTTP client documented; Basic-auth API key and proxy compatibility not tested | HTTPS REST and S3-compatible files documented; vendor credentials, rights and authenticated operation untested |
| Documentation requests | 15: six HTTP 200 JavaScript shells, nine readable official SDK/source documents | 15: 14 successful official pages, one preserved HTTP 404 |

**Within these two vendors, advance Massive for a concrete licensing/entitlement check first.** Its public evidence addresses the requested historical interval and quote-plan access. Confirm the user's personal versus business/professional use and permitted cloud processing/retention before choosing a plan. Listed monthly prices do not establish a permanent archive license or a complete project budget.

Retain Databento as a conditional alternative where timestamp richness, point-in-time definitions and normalized schemas are useful. Require readable OPRA-specific schema/coverage/license evidence and an account-specific metadata cost quote before presenting it as eligible or priced. Generic `mbp-1` must not be relabelled consolidated NBBO solely from its name. Databento range filtering on `ts_recv` also requires an adapter decision if the research protocol bounds windows by an event timestamp.

Any later authorized validation should use the root task's bounded read-only acceptance sample. Check quote freshness, ordering, crossed/locked markets, nonstandard deliverables and source gaps; a successful HTTP response or public coverage claim is not a validation of quote completeness. No such sample was executed in this review.
