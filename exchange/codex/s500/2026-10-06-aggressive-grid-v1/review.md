# S500 aggressive experiment independent review

Status: protocol, corporate-action inputs, `engine.py`, `features.py` and all 192 output paths reviewed. All 31 combined synthetic tests pass (20 independent portfolio tests plus 11 feature tests). Independent reconstruction against the cached raw market/action inputs passes. No orders, credentials, workflow edits or external publications are part of this review.

## Independent output reconciliation

`audit_engine_review.py` does not import the portfolio engine, feature builder or their self-checks. It verifies 104 raw market-cache file hashes, reads the required 5,523 source bars for 364 actually used symbols, and independently rebuilds:

- 12,749 model entries from raw opens, checking the prior-session candidate, known closing price, ranking, cash budget, share count and debit;
- 12,644 model exits from raw closes, including share count, holding length, costs and dividend attribution;
- 34,600 daily cash balances, pending dividends, raw-price holdings, equity, daily PnL, drawdowns and milestone dates;
- 57 dividend entitlements and their payments from the underlying corporate-action rows;
- all 12 incomplete paths against the unsupported foreign-dividend source events, preserving their last valid mark, cash and remaining position.

The 192 registered combinations are present: 180 provisional completed paths and 12 incomplete paths. The highest completed ending marked equity is $955.245085; the lowest is $105.8984035. Seven completed paths crossed $1,000 at an earlier daily mark, none crossed $2,000 or $10,000. This is one ongoing $500 hypothetical account per path with no additional funding; the separate hypothetical accounts must not be summed into one performance claim.

The maximum-result path has only 16 closed trades and still holds a censored position at the cutoff. Its largest two closed-trade contributions, approximately $234.13 and $228.23, together exceed its roughly $455.25 net marked profit. This is a static PnL attribution and concentration warning, **not** a rerun that removes those trades while recomputing later integer share sizes and capital.

No held-position split occurred in these particular 192 ledger paths. Split accounting is covered by synthetic tests, and split-adjusted signal windows are separately covered by the feature builder. Do not misdescribe the audit as observing real held split settlements or actual order fills. Detailed evidence, sample dividend source comparisons and artifact hashes are in `independent-audit.json`.

## Code review and resolved findings

The simulation uses completed signal-day observations, next-session open entry, integer shares, one open position, a precommitted holding period and daily liquidation-value marks. It applies prior-close dividend entitlement and splits before trading, moves dividends from receivables to cash on or after payable date, and does not buy again on a close-exit day. Closing a position while dividends remain unpaid does not erase or double-count those receivables. The signal builder adjusts only history before a split already effective by the decision date; future-effective splits are ignored.

The implementing agent resolved the following review findings before the matrix calculation:

- Affordability now uses the signal-day closing price instead of the preceding day's close.
- Incomplete paths retain an interrupted-position snapshot and cash state, including missing marks and unsupported actions.
- Dividend `foreign` and `special` flags must be explicitly false; missing flags no longer silently pass as ordinary events.
- Multiple same-symbol, same-ex-date dividends stop a held path, even if their cash rates differ. The input contains 33 such potentially ambiguous ordinary-dividend groups in the study universe; near-identical rates can represent a revision rather than two separate entitlements. Exact-duplicate detection alone was insufficient.
- A split with an explicitly present but unmapped `new_symbol` stops as unresolved instead of silently retaining the old identity.

The independent suite covers entry decision invariance to later prices, no retrospective runner-up after an unaffordable opening gap, fixed holding length, no re-entry on exit day, whole-share cost/leftover-cash accounting, half-capital allocation, marked drawdown, ordinary dividends held through ex-date, payment after sale, unpaid receivables at cutoff, no dividend for ex-date new buyers, split wealth conservation, fractional reverse-split failure, missing held bars, multiple/unsupported dividends, unknown flag and ticker mappings, censored terminal holdings and future-action invariance. These are synthetic implementation checks, not tests of economic profitability.

## Items that must be resolved or disclosed

1. **Decision chronology.** Selection may use a fully completed signal-day bar; execution is at the next session's open or later. Entry-day highs, lows, volume and closing prices must not change pre-open selection or allocation. Any test that only calls a feature function with a fixed history is weaker than perturbing future data through the complete engine.
2. **Cash and positions.** Record cash, integer share count, cost basis, market value and total equity on each session, including held positions. Existing holdings reserve capital. State whether same-open sales may fund purchases; a conservative next-session cash rule avoids assuming simultaneous auction fills and broker settlement availability.
3. **Whole-share auction assumption.** Sizing from the eventual opening print assumes an ideal cash-constrained fill at that print; the opening price was unknown before the auction. State this limit or model a precommitted quantity and insufficient-funds handling.
4. **Exit timing.** A fixed holding period is known at entry. Close-based stops/trailing exits act at a later executable price. Daily OHLC cannot reveal whether a target or stop was reached first, and a gap through a stop cannot always fill at the stop price.
5. **Raw-price corporate actions.** The existing source uses raw prices. Filtering signal days with moves above 20% does not handle splits during an open position, historical high/volume lookbacks, dividends, ticker changes or delisting proceeds. Distinguish genuine event gaps from unverified corporate-action discontinuities; do not silently remove losing paths after seeing returns.
6. **Universe and instrument identity.** The previous name filter admitted ETFs; an official current directory identified SQQQ/TQQQ in the top-100 pool. Current classification and current active/inactive directories do not constitute point-in-time membership. Unknown and unclassified instruments must remain visible rather than being implicitly called common stocks.
7. **Missing data.** A selected security with missing execution/mark/exit data cannot be discarded in favor of a later winner. Stop and label the path incomplete, or use a predeclared conservative treatment while retaining the failure. Do not count incomplete paths as successful or harmless zero-return observations.
8. **Funding and resets.** Each path starts with $500 and carries residual cash across months. If rounds are reset, disclose total contributions, failures and residual holdings; aggregate wealth cannot be reported as only the latest winning round. Not being able to afford today's first candidate is not evidence that the whole account is untradeable.
9. **Measurement.** Mark drawdown daily while positions are open; a realized-trade-only drawdown understates risk. Label whether targets use marked equity or realized liquidated cash, and account for terminal positions and exit costs consistently.
10. **Model selection.** Keep all registered combinations, including losses, unaffordable trades and incomplete runs. Report best results as the best among the full grid, with trade count, worst months, cost sensitivity and concentration. The 2026 input set has already been inspected; its slices are exploratory and must not be called untouched out-of-sample evidence.

## Review scope

Materials read: the preceding broad-universe protocol, research engine, instrument audit, classification sensitivity code, tests and published report; the current protocol, 42 raw corporate-action pages and their sanitized derivative, portfolio engine and feature builder. The company-action quality findings and limitations are detailed in `actions-review.md`. No source currency field or complete point-in-time identity/action database was obtained, so completed paths remain provisional.
