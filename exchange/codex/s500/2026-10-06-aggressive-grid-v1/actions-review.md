# Corporate-action input review

The read-only request to Alpaca Market Data `/v1/corporate-actions` completed all 41 pages for `start=2026-01-01`, `end=2026-10-05`, `types=forward_split,reverse_split,cash_dividend`, `limit=1000`. The requested type spellings were accepted by the API. Raw pages remain private; `action-manifest.json` records byte counts, hashes, pagination and field counts. `company-actions.json` is the sanitized derivative.

| Type | All source records | In the prior 6,633-symbol research universe |
|---|---:|---:|
| Forward split | 127 | 24 |
| Reverse split | 909 | 477 |
| Cash dividend | 39,928 | 6,700 |
| Total | 40,964 | 7,201 |

A separate warm-up request for `2025-11-01` through `2025-12-31`, with only forward/reverse splits, added 217 records (29 forward, 188 reverse; 6 and 67 respectively inside the study universe). Both request parameter sets, the additional raw-page hash and the source-request group are retained. The combined derivative therefore contains **41,181 rows from 42 pages**, including 7,274 rows whose ticker occurs in the study universe. All 43 raw/derived file hashes were rechecked after merging.

## Fields and simulator implications

- Splits provide `old_rate`, `new_rate` and `ex_date`; the new share quantity factor is `new_rate / old_rate`. Apply to prior-close holdings before the ex-date open, and re-express earlier signal-window prices and volumes only for splits effective by the decision date. A 2-for-1 split maps 1 share to 2; it is not a doubling of wealth. Check fractional post-split quantities and any cash-in-lieu requirement separately.
- Reverse splits sometimes provide `new_symbol`; 164 source rows change the symbol. Five are in the study universe: BURU→BURUD, DUKR→DUKRD, ELOX→ELOXD, ENLV→ENLV1 and VIVK→VIVKD. These mappings require identity/bar continuity checks, not blind ticker substitution.
- Dividend `rate` is preserved as `cash_rate`, with `foreign`, `special`, `sub_type`, ex-date, payable date and due-bill dates. **The API supplies no currency field in any of the 39,928 dividend rows.** The official Python SDK model likewise has no currency attribute. Any USD interpretation is a model assumption, not currency verified from the response.
- The API's returned `process_date` values fall inside the request interval, while **1,776 ex-dates fall outside it**, including malformed year values. Therefore this request is not a complete enumeration of all dividends whose ex-date is inside the study interval. In particular, a dividend already ex but processing/paying after the end date can be absent. Filter each retained row by relevant effective dates and report incomplete coverage.
- In the study universe, 125 dividend rows are special, 1,038 have the foreign flag, 15 have due-bill dates, and 465 have ex-dates outside the evaluation range. Do not apply a universal simple ex-date entitlement rule to due-bill or otherwise exceptional events without checking terms.
- Raw source IDs are unique. After keeping due-bill dates and subtype, there are **five groups of identical sanitized semantics**, including four PNNT groups inside the study universe. They remain present and flagged, not silently collapsed or doubled in the account. Dropping due-bill/subtype fields would have falsely inflated the apparent duplicate count to 21 groups.
- Nineteen source rows use CUSIP-shaped strings as their symbol. The public derivative replaces those symbols with `null`, labels the mapping problem and omits source IDs/CUSIPs. A syntactically valid ticker still does not prove historical identity or point-in-time availability.

## Limits and remaining checks

These are retrospectively retrieved corporate actions and may include revisions. Their announcement/first-availability times are not established. The raw daily market cache starts in November 2025; the initial 2026 signal windows can now use the separately retrieved 2025 warm-up splits. Two warm-up records have ex-dates outside their process-date request window, so action timing must still use each effective ex-date. Current official instrument classification and the source universe remain non-point-in-time.

The actual dividend subtype observations are 39,739 omitted `sub_type` fields and 189 `return_of_capital` values, with no explicit `normal` value. A pre-computation model amendment may treat an omitted subtype, `foreign=false`, `special=false`, no due bills/duplicates and valid dates/rate as a **provisional ordinary-dividend / USD assumption**. The absence of a subtype or currency is not affirmative source verification. Unresolved events encountered while holding a security must remain incomplete rather than being silently credited or ignored.

All fields and rows above are market-research inputs; no account or order IDs, credentials, actual orders, broker mutations or workflow edits were involved. This review is not evidence of executable trading profitability.
