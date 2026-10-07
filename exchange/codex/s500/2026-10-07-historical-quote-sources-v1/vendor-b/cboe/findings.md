# Cboe DataShop findings

Cboe is a documented **one-minute NBBO snapshot fallback**, not a verified tick-quote purchase path. Historical-file cost, complete point-in-time contract coverage and intended-use permission remain unresolved. S500 is treated as a project name, not a ticker.

## Interval Scope

Option Quote Intervals are 1-minute/custom-N-minute OPRA NBBO snapshots covering U.S. listed stock, ETF and index options, with bid/ask price, size and interval trade OHLC/volume. They do not preserve every quote update.

- “1-minute or custom N-minute summaries” — [intervals](https://datashop.cboe.com/option-quote-intervals), Product description.
- “National Best Bid and Offer (NBBO)” — [intervals](https://datashop.cboe.com/option-quote-intervals), Product description.
- “Options on U.S. listed Stock, ETFs, and Indices disseminated over the Options Price Reporting Authority (OPRA) market data feed” — [layout](https://datashop.cboe.com/documents/Option_Quotes_Layout.pdf), p1 Coverage.

Limitations: Options on futures/non-US markets excluded; no guarantee all chains/series or every date are present.

## History

Product page advertises January 2012 to present. This brackets January 2–October 5, 2026 in principle, but exact symbols/dates were not queried or purchased. FAQ says Option Quotes January 2010, a documentation discrepancy that does not affect the requested 2026 window.

- “Available from January 2012 to present.” — [intervals](https://datashop.cboe.com/option-quote-intervals), Historical Data.
- “Option Quotes Jan, 2010” — [faq](https://datashop.cboe.com/faqs), Data availability table.

Limitations: Universe is still unresolved: S500 treated as project name, not a ticker or assumed S&P500 membership.

## Tick

Historical tick-level data is advertised only by a sales-contact prompt on the reviewed interval page. No self-service tick quote SKU, tick schema, time range, conditions, sequence semantics, exchange scope or price was confirmed.

- “Looking for historical tick-level data? Contact” — [intervals](https://datashop.cboe.com/option-quote-intervals), Historical Data.

Limitations: Guessed /option-quote-tick-data URL returned HTTP 404; retained in manifest. No entitlement or data call attempted.

## Timestamps

quote_datetime is end-of-interval yyyy-mm-dd hh:mm:ss in US Eastern time. It is a snapshot time, not a per-event exchange/SIP receive timestamp.

- “End of interval timestamp: yyyy-mm-dd hh:mm:ss. Timestamp is in US Eastern time.” — [layout](https://datashop.cboe.com/documents/Option_Quotes_Layout.pdf), p1 quote_datetime.
- “For quotes, the bid and ask is a snapshot of the National Best Bid and Offer (NBBO) at that time.” — [faq](https://datashop.cboe.com/faqs), Fields and Values.

Limitations: Use America/New_York with DST for ingestion; this conversion is an implementation recommendation, not a documented UTC-offset field. Session coverage and handling of late/out-of-order quote events remain unconfirmed.

## Sizes

Bid and ask sizes are largest participant size at the NBBO price, not summed across venues. Starting June 22, 2026, sizes are from the most recent price change within the interval; earlier sizes were latest price and size even without a price change. Requested period crosses this change.

- “The largest size from an options exchange participant on the best bid price (NBB)” — [layout](https://datashop.cboe.com/documents/Option_Quotes_Layout.pdf), p2 bid_size.
- “Monday, June 22, 2026” — [intervals](https://datashop.cboe.com/option-quote-intervals), NBBO Bid Size / NBBO Ask Size.
- “quote sizes will be captured at the time of the most recent price change within the interval.” — [intervals](https://datashop.cboe.com/option-quote-intervals), NBBO Bid Size / NBBO Ask Size.
- “This applies to historical data purchases accordingly.” — [intervals](https://datashop.cboe.com/option-quote-intervals), NBBO Bid Size / NBBO Ask Size.

Limitations: Do not treat post-change size as latest size at the snapshot. Venue identifiers and quote-condition codes are absent from the published interval fields.

## Conditions Corrections

Trade OHLC eligibility is condition-filtered; this is distinct from quote-condition availability. The published interval schema exposes no quote-condition, cancellation/correction, sequence or revision fields. The historical license disclaims responsibility for corrections/updates.

- “trade condition not eligible to update OHLC” — [layout](https://datashop.cboe.com/documents/Option_Quotes_Layout.pdf), p1–2 OHLC descriptions.
- “Cboe shall not be responsible to Subscriber to provide any technical support, any maintenance, or any corrections or updates to the Data.” — [license](https://datashop.cboe.com/documents/Cboe_LiveVol_DataShop_License_Agreement.pdf), II.4, p12.

Limitations: No point-in-time/as-received versus retrospectively corrected history promise found. No tick quote correction behavior confirmed.

## Chain And Corporate Actions

Historical rows carry underlying_symbol, root, expiration, strike and option_type. Root denotes OCC trading class and digits can indicate corporate-action-adjusted non-standard contracts. This provides historical observed contracts, not a demonstrated complete point-in-time listed-chain master.

- “The root is another name for the OCC option symbol.” — [faq](https://datashop.cboe.com/faqs), Fields and Values.
- “These indicate non-standard options which are adjusted for a corporate action such as a stock split, special dividend, spin-off, or merger” — [faq](https://datashop.cboe.com/faqs), Root with digit.

Limitations: No listing/delisting effective timestamp, deliverable/multiplier, full adjustment lineage or guaranteed zero-activity contracts in published quote layout. Need as-of historical contract reference and corporate-action mapping to avoid survivorship and adjusted-contract errors.

## Historical Pricing

Concrete interval purchase configurator supports Historical or Subscription, selected/all underlying symbols, date range, interval, open interest, calcs, Individual/Firm and CGI license status. No final 2026 historical price was obtained. $0.00 initial subtotal is an unconfigured form value, not a free-data quote.

- “Historical Subscription” — [intervals](https://datashop.cboe.com/option-quote-intervals), Purchase Type.
- “Subtotal: $0.00” — [intervals](https://datashop.cboe.com/option-quote-intervals), Unconfigured purchase form.

Limitations: No checkout, account, pricing-form POST, signup, purchase, contact or sample download performed. Tick pricing unknown. Exact universe, period, granularity, optional fields and license are needed for binding quote.

## Allaccess Pricing

As a separate API offering, public All Access page shows Tier 3: $2,499/month, 1,250,000 points/month, $0.004 per extra point; Tier 4: $4,599/month, 4,000,000 points/month, $0.0025 extra. Free trial is 14 days, 500 points/day and requires credit-card authorization. These are not historical interval file prices and do not establish tick quote entitlement/cost.

- “Tier 3 1,250,000 points/mo. Monthly base price: $2,499 Overage rates: $0.004/point” — [allaccess](https://datashop.cboe.com/cboe-all-access-api), Fee Schedule.
- “Tier 4 4,000,000 points/mo. Monthly base price: $4,599 Overage rates: $0.0025/point” — [allaccess](https://datashop.cboe.com/cboe-all-access-api), Fee Schedule.
- “Credit card authorization is required” — [allaccess](https://datashop.cboe.com/cboe-all-access-api), Free Trial.

Limitations: Tier 1/2 appear as form choices but prices not exposed in rendered public text. No trial started. Licensed-provider access not included in base price; API SIP subscriptions classify user professional per page.

## Nonprofessional

Historical policy defines Non-Professional as a natural person using data solely for non-commercial personal purposes, with regulated-professional/adviser/employment exclusions. All others are Professional. User eligibility cannot be inferred from personal usage alone.

- ““Non-Professional” means a natural person that uses Data only for non-commercial personal purposes” — [history_policy](https://datashop.cboe.com/documents/DataShop_Policies_for_Historical_Data_Services.pdf), Section1 p1.
- ““Professional” means all other persons or entities who do not meet the definition of Non-Professional.” — [history_policy](https://datashop.cboe.com/documents/DataShop_Policies_for_Historical_Data_Services.pdf), Section1 p1.

Limitations: No claim that an historical interval order incurs listed live-OPRA SIP charges. SIP fee page estimates concern its stated platform/API live/delayed categories, not a confirmed historical-file bill.

## License

Default license is internal use; historical redistribution, where selectable and ordered, is display only and prohibits raw-data transfer/sublicensing. Non-index historical data has a perpetual license subject to terms. Broad investment-strategy/index/financial-product limitations require checking intended use and any order-specific exception.

- “Subscriber’s redistribution of the Data is for display only;” — [license](https://datashop.cboe.com/documents/Cboe_LiveVol_DataShop_License_Agreement.pdf), II.1(a), p12.
- “Subscriber may not transfer the Data to any third party;” — [license](https://datashop.cboe.com/documents/Cboe_LiveVol_DataShop_License_Agreement.pdf), II.1(c), p12.
- “connection with (1) creating any index, indicator, benchmark, investment strategy, or similar value” — [license](https://datashop.cboe.com/documents/Cboe_LiveVol_DataShop_License_Agreement.pdf), I.1(e)(viii), p4.
- “non-index Data provided by Cboe pursuant to an Order is licensed to Subscriber in perpetuity.” — [license](https://datashop.cboe.com/documents/Cboe_LiveVol_DataShop_License_Agreement.pdf), II.5, p13.

Limitations: Do not assume index historical data is perpetual. Order takes precedence over license, then policies. Separate Data Supplier requirements may also apply.

## Cloud

File product delivers zipped CSV through SFTP, supported from Linux, but current environment declares no TCP destinations. A supported SFTP TCP grant/forwarding path and vendor account/key are needed; HTTPS documentation access alone does not prove SFTP capability. Third-party cloud or AI processing permission is not established by the reviewed license.

- “Data will be made available to download from our SFTP server once it completes processing.” — [faq](https://datashop.cboe.com/faqs), Account and SFTP Delivery.
- “Primary SFTP: sftp.datashop.livevol.com (IP: 3.22.35.78, Port: 22).” — [faq](https://datashop.cboe.com/faqs), Account and SFTP Delivery.
- “Files are available to download for a 30-day period following the completion date” — [faq](https://datashop.cboe.com/faqs), Account and SFTP Delivery.
- “employees of a company providing services to Subscriber may be authorized as Users at Cboe’s discretion” — [license](https://datashop.cboe.com/documents/Cboe_LiveVol_DataShop_License_Agreement.pdf), Users definition, p3.

Limitations: Backup host sftp2.datashop.livevol.com port22. License limits access to Users; obtain written clarification for cloud service/AI processor and intended strategy. No SFTP connection/authentication attempted.

## Concrete next step

After symbol universe and use/license are resolved, configure Option Quote Intervals > Historical > actual underlying list > January 2–October 5 2026 > 1-minute > exclude optional calcs/open-interest unless strategy needs them; request final quote and complete date/contract coverage report before purchase. Treat size regimes separately. If every quote event or latest size is required, use a tick provider or obtain Cboe custom tick quote/specification first.

All 14 document retrieval records, UTC times and body SHA-256 values are in manifest.json; claim-level evidence is in findings.json. No account, checkout, trial, paid data request or sample download was made. Raw documentation remains private.
