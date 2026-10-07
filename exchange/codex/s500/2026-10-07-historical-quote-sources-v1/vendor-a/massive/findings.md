# Massive historical US options quotes

Researched 2026-10-07 using 15 official public documentation requests (14 successful, one preserved 404). No credentials, account login, paid APIs, market-data downloads, subscriptions or installations were used. Machine-readable detail: [findings.json](findings.json); response provenance: [manifest.json](manifest.json); short exact supporting quotations: [evidence.json](evidence.json).

Massive is a strong documented candidate for **2026-01-02 through 2026-10-05**. Historical quote REST and bulk-file documentation explicitly start **2022-03-07**. Actual contract/day availability, completeness and user-specific licensing were not tested.

| Topic | Documented result | Limits |
|---|---|---|
| Historical product | `GET /v3/quotes/{optionsTicker}` and daily S3 dataset `us_options_opra/quotes_v1`; OPRA consolidated best-bid/offer event quotes, with top-of-book bulk schema | No guarantee of every raw OPRA message, conflation policy or condition filtering was established |
| Quote fields | Bid/ask price, size in **contracts**, exchange IDs, sequence number; `sip_timestamp` is Unix nanoseconds when SIP received the quote | Historical quote schemas expose no participant/receive timestamps or quote-condition/flag fields |
| Granularity | Business page advertises tick-by-tick data; overview describes OPRA NBBO; historical quote APIs are distinct from sampled bars and snapshots | No historical sampling cadence documented; endpoint-specific NBBO update semantics warrant confirmation |
| Contract history | List/detail endpoints support `as_of`; list supports `expired` (default false). History starts 2014-06-02 | A current active-contract list would introduce survivorship problems |
| Splits/adjustments | `shares_per_contract`, `additional_underlyings`, `correction`, underlying, strike, expiry and exercise style | Not proof of complete historical corporate-action lineage; do not assume 100-share deliverables or mechanically adjust quotes |
| Cloud access | HTTPS REST, official Python/Go/JS/JVM clients; S3-compatible compressed CSV and boto3 | Cloud execution and data entitlements were not tested; no desktop terminal is required by documented routes |

REST has timestamp range filters, up to 50,000 records per page, and `next_url` pagination. Flat files are normally available around 11:00 AM ET the next day. Sequence numbers are per ticker, can have gaps, and reset daily in the flat-file description. Live WebSocket quote timestamps are milliseconds and have a 1,000-contract subscription cap; that route supplies no history.

The cheapest listed qualifying individual plan is **Options Advanced, $199/month**, restricted to individual/non-professional use. Basic ($0), Starter ($29) and Developer ($79) explicitly exclude historical quotes. **Options Business is $1,999/month** and includes end-of-day historical quotes and flat files. Business delayed (+$499/month) and real-time (+$1,999/month) expansions are unnecessary solely for this completed historical interval. These are public monthly list prices; exact total and appropriate entitlement depend on status, taxes and contractual rights. The Business card says “2.5 Years Historical Quotes,” while endpoint docs say all history from 2022-03-07; both cover this requested interval.

Rights matter for the proposed archive. Individuals terms allow only personal, non-commercial, non-business use and incorporate Market Data Terms that were not fetched within the cap. Business terms allow internal processing/storage within purchased entitlements, but **section 11.3 requires deleting related Information, including downloads, after the relevant subscription/order/agreement ends**. Section 6.1 restricts redistribution and model training; ML/AI training, fine-tuning or distillation needs express order-form permission. Third-party/AI services cannot use data for their own purposes. A one-month purchase is therefore not documented as a permanent raw-data archive license. Cloud/non-display, retention and any AI use should be confirmed for the actual user before procurement.

Use historical `as_of` metadata with expired-contract discovery and preserve adjusted deliverables. A later authorized sample should cover both date boundaries, an expiry and an adjusted contract, then check quote completeness/ordering and invalid or crossed quotes. Confirm NBBO event/filtering semantics and absent condition information with the vendor. For a narrow contract set, REST avoids downloading the full options market; no target-universe volume or cloud-cost estimate was obtained.

Sources, all retrieved 2026-10-07:

- [Historical quotes](https://massive.com/docs/rest/options/trades-quotes/quotes.md), [quote flat files](https://massive.com/docs/flat-files/options/quotes.md), [options overview](https://massive.com/docs/rest/options/overview.md), [WebSocket quotes](https://massive.com/docs/websocket/options/quotes.md).
- [All contracts](https://massive.com/docs/rest/options/contracts/all-contracts), [contract detail](https://massive.com/docs/rest/options/contracts/contract-overview).
- [Individual pricing](https://massive.com/pricing?product=options), [business pricing](https://massive.com/business-options).
- [REST authentication and SDKs](https://massive.com/docs/rest/quickstart), [S3 quickstart](https://massive.com/docs/flat-files/quickstart.md).
- [Individuals terms](https://massive.com/legal/individuals-terms-of-service), [business terms](https://massive.com/legal/businesses-terms-of-service).
