# Independent audit plan: missing-minute diagnosis

The frozen parent proposal reconciles exactly to all 414 held-minute failures at 38 unique symbol/time coordinates. Selection uses failure metadata, not news status or returns. The parent source hash matches; baseline hashes are saved for later immutability checks.

These are the first encountered gaps of failed paths, not all missing minutes in the year. Resolving them would not prove the paths become complete.

1. Use exact integer nanoseconds and half-open target[m,m+60s) and context[m-120s,m+180s). Inclusive REST end must be last ns before context end; returned out-of-range observations are retained as violations and excluded from target statistics.

2. Bind every cache to exact route,symbol,feed,adjustment,sort,timeframe,start/end,limit,base_request hash,page-token chain and frozen design. Verify bytes and hash before reuse; do not treat a raw file as proof of terminal pagination.

3. Empty pages with next_page_token continue. Repeated tokens, missing/malformed token or route/payload shape errors fail closed. Ten pages with remaining token are explicitly truncated and cannot establish absence.

4. Per-minute trades and quotes are separated from neighboring minutes. Preserve ID/version conflicts; never silently collapse different raw states sharing an ID or same-timestamp observations.

5. Trade eligibility uses authoritative provider rules and the relevant tape with multi-condition behavior, separately for bar OHLC/volume. Observed excluded trade is still a trade; unknown conditions cannot prove no eligible trade.

6. A complete provider response is not complete market history. No returned eligible trade means only no such returned record within this bounded current archive, never proof of no trade, halt, no risk or zero return.

7. Quote positivity,condition and age are state diagnostics. No older-valid fallback after latest invalid quote; quotes alone do not prove fills or absence of halt. Same-t conflicting states have no certified ordering.

8. A newly returned bar is a later-version discrepancy and may be future-revised. No silent replacement, interpolation,zero-fill,forward-fill or concatenation with old-v1 gap.

9. Halt claims require authoritative symbol/time halt-and-resume evidence covering the exact target interval. Adjacent trading,blank records or issuer news alone are insufficient.

10. All38 points and all414 failure associations remain retained independently of news or PnL. This stage is post-outcome data diagnosis, not holdout strategy validation; resolving first gaps may expose later gaps.

11. Oldv1 source/result/classification hashes remain unchanged. Public outputs exclude fullraw feeds,raworder/activity identifiers,account identifiers and private absolute paths.

Awaiting the frozen stage design, downloader/analyzer code and actual output for independent synthetic and all-source audits.
