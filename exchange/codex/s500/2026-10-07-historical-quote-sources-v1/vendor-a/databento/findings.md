# Databento — historical US options quote-source review

Target: 2026-01-02 through 2026-10-05. Read-only public documentation research; no account, API call, market-data download, install or purchase.

**Decision:** retain as a conditional technical candidate. Coverage dates, dataset-specific schemas, current price and licensed use remain unverified.

The official OPRA, MBP-1, pricing and terms pages returned HTTP 200 JavaScript shells. Two text-documentation discovery URLs returned the same shell. Those are preserved as content unknown. Official vendor-maintained GitHub source and docstrings supplied the generic format/API evidence below.

## Opra dataset identity

Status: partially_verified.

The official SDK names the dataset OPRA.PILLAR. This confirms an identifier, not the historical start, end, availability or scope of U.S. options contracts.

- [publishers-sdk](https://raw.githubusercontent.com/databento/databento-python/main/databento/common/publishers.py), retrieved 2026-10-07T13:33:30.753896Z, source line 825: “OPRA_PILLAR = "OPRA.PILLAR"”
  Body SHA-256: `9ff10e179995285f2821bfcc9e94b9d35035739a063ba38b1bb9f451ee652861`
- [metadata-sdk](https://raw.githubusercontent.com/databento/databento-python/main/databento/historical/api/metadata.py), retrieved 2026-10-07T13:33:00.415557Z, source line 245: “Request the available range for the dataset given the user's”
  Body SHA-256: `f6e0d1dac704b017597b270a6ef5c7f21d8b446b4bb3b35d4fceddfae524d890`

- No authenticated metadata.get_dataset_range call was made.
- 2026-01-02 through 2026-10-05 coverage and completeness remain unverified.

## Documentation failure

Status: verified_observation.

OPRA dataset, MBP-1, pricing, terms, and two text-documentation discovery URLs returned HTTP 200 JavaScript shells; their requested content is unknown.

- [opra](https://databento.com/docs/venues-and-datasets/opra-pillar), retrieved 2026-10-07T13:31:59.956449Z, source line 1: “You need to enable JavaScript to run this app”
  Body SHA-256: `8051ccd30c54fd0f51f9f4b317ca2412efb5312f59a5766eb5149c4ef4fc9493`

- Repeated shell hashes are preserved in manifest.json; HTTP 200 is not evidence of product coverage or legal terms.
- No browser execution, hidden API reconstruction, login, or bypass was attempted.

## Quote trade sampling distinction

Status: verified_generic_format.

Generic DBN schemas distinguish cmbp-1 consolidated BBO, interval-sampled cbbo-1s/cbbo-1m, and tbbo/tcbbo observations at trade events. trades and OHLCV are trade-price products. The public source does not establish which schemas are available for OPRA.PILLAR or their date ranges.

- [enums-dbn](https://raw.githubusercontent.com/databento/dbn/main/rust/dbn/src/enums.rs), retrieved 2026-10-07T13:33:00.780162Z, source line 760: “/// Consolidated best bid and offer.”
  Body SHA-256: `5017ea98cc1a80a11b101b8c0e79badd34ef0cf45e0c1ecd204e9a0c837f5b38`
- [enums-dbn](https://raw.githubusercontent.com/databento/dbn/main/rust/dbn/src/enums.rs), retrieved 2026-10-07T13:33:00.780162Z, source line 763: “/// Consolidated best bid and offer subsampled at one-second intervals, in addition to”
  Body SHA-256: `5017ea98cc1a80a11b101b8c0e79badd34ef0cf45e0c1ecd204e9a0c837f5b38`
- [enums-dbn](https://raw.githubusercontent.com/databento/dbn/main/rust/dbn/src/enums.rs), retrieved 2026-10-07T13:33:00.780162Z, source line 767: “/// Consolidated best bid and offer subsampled at one-minute intervals, in addition to”
  Body SHA-256: `5017ea98cc1a80a11b101b8c0e79badd34ef0cf45e0c1ecd204e9a0c837f5b38`

- Use metadata.list_schemas for OPRA.PILLAR after account authorization, then validate whether quote-event completeness and venue scope meet S500 needs.
- Sampled BBO or trade-event BBO cannot be assumed to preserve every intraday quote update.

## Fields timestamps sizes

Status: verified_generic_format.

Generic consolidated BBO records include bid/ask price, bid/ask size and best-bid/ask publisher IDs. RecordHeader has ts_event in nanoseconds, Cmbp1Msg has ts_recv in nanoseconds, and flags describe message characteristics and data quality. In CbboMsg, price/size are last-trade fields; quotes live in levels. This is format-level evidence only.

- [records-dbn](https://raw.githubusercontent.com/databento/dbn/main/rust/dbn/src/record.rs), retrieved 2026-10-07T13:33:00.417609Z, source line 142: “pub bid_px: i64,”
  Body SHA-256: `0658f5d38ae79f48768fe93cb71828a169947eaaff14aea72cd53972288fa744`
- [records-dbn](https://raw.githubusercontent.com/databento/dbn/main/rust/dbn/src/record.rs), retrieved 2026-10-07T13:33:00.417609Z, source line 149: “pub ask_px: i64,”
  Body SHA-256: `0658f5d38ae79f48768fe93cb71828a169947eaaff14aea72cd53972288fa744`
- [records-dbn](https://raw.githubusercontent.com/databento/dbn/main/rust/dbn/src/record.rs), retrieved 2026-10-07T13:33:00.417609Z, source line 152: “pub bid_sz: u32,”
  Body SHA-256: `0658f5d38ae79f48768fe93cb71828a169947eaaff14aea72cd53972288fa744`

- Venue-specific OPRA timestamp semantics, quote-condition mappings, sequence guarantees and underlying feed normalization remain unverified.
- Normalized flags are not proof that all raw OPRA quote-condition codes survive.
- No sample quote records were downloaded or inspected.

## Point in time contracts

Status: partially_verified.

The official client README advertises point-in-time instrument definitions without retroactive adjustment. The generic definition record includes activation, expiration, raw_symbol, underlying and strike fields. Symbology resolution accepts inclusive start_date and exclusive end_date.

- [python-readme](https://raw.githubusercontent.com/databento/databento-python/main/README.md), retrieved 2026-10-07T13:32:38.291041Z, source line 17: “[Point-in-time]() instrument definitions, free of look-ahead bias and retroactive adjustments.”
  Body SHA-256: `5b494741354dc3f6aef08daffa1af1d4d3ddffc755b4b43543abb1141867a229`
- [records-dbn](https://raw.githubusercontent.com/databento/dbn/main/rust/dbn/src/record.rs), retrieved 2026-10-07T13:33:00.417609Z, source line 682: “pub expiration: u64,”
  Body SHA-256: `0658f5d38ae79f48768fe93cb71828a169947eaaff14aea72cd53972288fa744`
- [records-dbn](https://raw.githubusercontent.com/databento/dbn/main/rust/dbn/src/record.rs), retrieved 2026-10-07T13:33:00.417609Z, source line 689: “pub activation: u64,”
  Body SHA-256: `0658f5d38ae79f48768fe93cb71828a169947eaaff14aea72cd53972288fa744`

- The generic fields do not confirm they are populated for OPRA.PILLAR.
- Corporate-action/split deliverables, adjusted-option identifiers, nonstandard multipliers and expired-contract completeness for the S500 universe remain unverified.
- Do not infer shares deliverable from a generic contract_multiplier field: its source comment refers to deliverable peak days and units hours/days.
- No current-contract list should be substituted for a historical contract universe.

## Cost and entitlement

Status: unknown_amount.

No current dollar price, required subscription tier or complete task cost was confirmed. The official SDK exposes unit-price, available-range and cost metadata methods. get_cost is request-specific and respects discounts from flat-rate plans.

- [metadata-sdk](https://raw.githubusercontent.com/databento/databento-python/main/databento/historical/api/metadata.py), retrieved 2026-10-07T13:33:00.415557Z, source line 171: “List unit prices for each feed mode and data schema in US dollars per”
  Body SHA-256: `f6e0d1dac704b017597b270a6ef5c7f21d8b446b4bb3b35d4fceddfae524d890`
- [metadata-sdk](https://raw.githubusercontent.com/databento/databento-python/main/databento/historical/api/metadata.py), retrieved 2026-10-07T13:33:00.415557Z, source line 420: “Request the cost in US dollars for historical streaming or batched”
  Body SHA-256: `f6e0d1dac704b017597b270a6ef5c7f21d8b446b4bb3b35d4fceddfae524d890`
- [metadata-sdk](https://raw.githubusercontent.com/databento/databento-python/main/databento/historical/api/metadata.py), retrieved 2026-10-07T13:33:00.415557Z, source line 422: “rate plans.”
  Body SHA-256: `f6e0d1dac704b017597b270a6ef5c7f21d8b446b4bb3b35d4fceddfae524d890`

- Public pricing returned a JavaScript shell.
- No account, API key, authorized quote request or data purchase exists for this research.
- The SDK get_cost docstring says GET but implementation uses _post; use the supported SDK method rather than inferring an HTTP verb from the docstring.
- A cost result requires the exact symbol universe, schema, start/end, entitlement and request limit; no numeric estimate was invented.

## Licensing rights

Status: unknown.

Market-data rights for individual/nonprofessional status, historical storage, cloud research, derived outputs and redistribution were not established. The README Apache 2.0 statement concerns the client software and is not a market-data license.

- [python-readme](https://raw.githubusercontent.com/databento/databento-python/main/README.md), retrieved 2026-10-07T13:32:38.291041Z, source line 97: “Distributed under the [Apache 2.0 License]”
  Body SHA-256: `5b494741354dc3f6aef08daffa1af1d4d3ddffc755b4b43543abb1141867a229`
- [terms](https://databento.com/terms), retrieved 2026-10-07T13:31:59.959497Z, source line 311: “You need to enable JavaScript to run this app”
  Body SHA-256: `8b3554e3df6aa1a8ede3f1d121c5b4617b53fb61df6cf66b8ba337fe3ef3c5cb`

- Obtain applicable Databento and OPRA historical-use terms before any acquisition.
- Do not infer redistribution, public artifact inclusion, team sharing or indefinite storage rights.

## Cloud and auth

Status: partially_verified.

Databento supports a Python historical HTTP client and API-key authentication through DATABENTO_API_KEY. Its synchronous HTTP source uses requests with HTTP Basic auth, placing the key as username and an empty password. This suggests a normal HTTPS cloud workflow without a desktop terminal, but authenticated compatibility has not been tested.

- [python-readme](https://raw.githubusercontent.com/databento/databento-python/main/README.md), retrieved 2026-10-07T13:32:38.291041Z, source line 10: “The official Python client library for [Databento]”
  Body SHA-256: `5b494741354dc3f6aef08daffa1af1d4d3ddffc755b4b43543abb1141867a229`
- [python-readme](https://raw.githubusercontent.com/databento/databento-python/main/README.md), retrieved 2026-10-07T13:32:38.291041Z, source line 33: “- python = "^3.10"”
  Body SHA-256: `5b494741354dc3f6aef08daffa1af1d4d3ddffc755b4b43543abb1141867a229`
- [historical-client-sdk](https://raw.githubusercontent.com/databento/databento-python/main/databento/historical/client.py), retrieved 2026-10-07T13:33:30.755448Z, source line 26: “If `None` then the `DATABENTO_API_KEY` environment variable is used.”
  Body SHA-256: `9fe714854b8bab2bc7feaa4c7e4c4f207d4d2ece5aaad68af1c1f5d4c0038248`

- Only public documentation HTTPS through the inherited proxy was verified; no Databento API endpoint or authenticated call was tested.
- Environment metadata contains no Databento credential binding. Existing Alpaca secrets were not read or reused.
- Verify the selected gateway hostname and proxy credential-injection compatibility with encoded Basic auth before adding a destination-scoped secret.
- Prefer the supported synchronous historical path when preserving inherited proxy behavior; asynchronous client code does not visibly set aiohttp trust_env in inspected source.
- No SDK or terminal was installed and no environment configuration changed.

## Time range filter

Status: verified_generic_api.

timeseries.get_range start is inclusive and end exclusive, filtering ts_recv where present and otherwise ts_event; naive times are assumed UTC.

- [timeseries-sdk](https://raw.githubusercontent.com/databento/databento-python/main/databento/historical/api/timeseries.py), retrieved 2026-10-07T13:33:00.416467Z, source line 63: “The inclusive start of the request range.”
  Body SHA-256: `c392df431654306eb50c66cacffacae48690443f182cfe7630848b7173a1bdda`
- [timeseries-sdk](https://raw.githubusercontent.com/databento/databento-python/main/databento/historical/api/timeseries.py), retrieved 2026-10-07T13:33:00.416467Z, source line 68: “The exclusive end of the request range.”
  Body SHA-256: `c392df431654306eb50c66cacffacae48690443f182cfe7630848b7173a1bdda`
- [timeseries-sdk](https://raw.githubusercontent.com/databento/databento-python/main/databento/historical/api/timeseries.py), retrieved 2026-10-07T13:33:00.416467Z, source line 64: “Filters on `ts_recv` if it exists in the schema, otherwise `ts_event`.”
  Body SHA-256: `c392df431654306eb50c66cacffacae48690443f182cfe7630848b7173a1bdda`

- Use explicit UTC instants converted from the intended exchange-local sessions; a UTC date interval is not itself proof of a complete U.S. trading session.
- A whole-date target through 2026-10-05 nominally has exclusive end 2026-10-06, subject to verified session boundaries.

## Proposed next step

1. After vendor selection, obtain readable applicable OPRA/Databento historical license, plan and current price terms.
2. Confirm OPRA.PILLAR availability, schema-specific date range and contract-definition population for 2026-01-02 through 2026-10-05.
3. Create a destination-scoped Databento credential binding only with user authorization; verify Basic-auth compatibility with the environment proxy.
4. Use metadata.list_schemas and metadata.get_dataset_range, then metadata.get_cost for an explicit historical contract universe and dates; do not request all symbols or download broad data merely to price it.
5. If coverage, rights and cost are accepted, validate one already-authorized narrowly scoped historical quote slice and accompanying point-in-time definition, then assess gaps, timestamps, condition treatment and split deliverables.

Evidence: `manifest.json` records all 15 public URLs, exact retrieval times, HTTP outcomes and body SHA-256 values. `findings.json` contains all claim quotations. Raw material remains private.
