# Evolution log

## 2026-10-02 — generation 1
- live return since start: +0.55% over 2 days
- current score 0.57 (train 0.84, val 0.57, val DD -13.8%)
- best score 1.14 (train 1.31, val 1.14, val DD -10.2%) {'momentum_lookback': '126 -> 63', 'trend_sma': '100 -> 50', 'top_n': '5 -> 8', 'rsi_applies_to_holdings': 'True -> False'}
- decision: ADOPT

## 2026-10-03 — generation 1
- live return since start: +0.88% over 2 days
- current score 1.15 (train 1.31, val 1.15, val DD -10.2%)
- best score 1.15 (train 1.31, val 1.15, val DD -10.2%) 
- decision: keep
- briefing accuracy (record only, 1 days): predictions 2/2 (100%), bias vs SPY 1/1 (100%)

## 2026-10-10 — generation 1
- live return since start: +1.85% over 7 days
- current score 1.16 (train 1.21, val 1.16, val DD -10.2%)
- best score 1.16 (train 1.21, val 1.16, val DD -10.2%) 
- decision: keep
- briefing accuracy (record only, 6 days): predictions 9/11 (82%), predictions unverifiable 1, bias vs SPY 5/6 (83%)

## 2026-10-17 — generation 1
- live return since start: +3.91% over 12 days
- current score 0.89 (train 1.15, val 0.89, val DD -10.2%)
- best score 0.89 (train 1.15, val 0.89, val DD -10.2%) 
- decision: keep
- briefing accuracy (record only, 11 days): predictions 15/21 (71%), predictions unverifiable 1, bias vs SPY 8/11 (73%)

## 2026-10-24 — generation 1
- live return since start: +5.46% over 17 days
- current score 0.84 (train 1.11, val 0.84, val DD -10.2%)
- best score 0.84 (train 1.11, val 0.84, val DD -10.2%) 
- decision: keep
- briefing accuracy (record only, 16 days): predictions 25/31 (81%), predictions unverifiable 1, bias vs SPY 12/16 (75%)

## 2026-10-30 — generation 1
- live return since start: +8.01% over 22 days
- current score 1.01 (train 1.19, val 1.01, val DD -10.2%)
- best score 1.01 (train 1.19, val 1.01, val DD -10.2%) 
- decision: keep
- briefing accuracy (record only, 20 days): predictions 30/39 (77%), predictions unverifiable 1, bias vs SPY 13/20 (65%)

## 2026-10-31 — generation 1
- live return since start: +8.01% over 22 days
- current score 1.01 (train 1.19, val 1.01, val DD -10.2%)
- best score 1.01 (train 1.19, val 1.01, val DD -10.2%) 
- decision: keep
- briefing accuracy (record only, 21 days): predictions 32/41 (78%), predictions unverifiable 1, bias vs SPY 14/21 (67%)

## 2026-11-07 — generation 2
- live return since start: -0.90% over 27 days
- current score 0.38 (train 1.28, val 0.38, val DD -14.5%)
- best score 1.21 (train 1.24, val 1.21, val DD -7.1%) {'rank_buffer': '0 -> 4', 'group_cap': '0 -> 3'}
- decision: ADOPT
- options: current score 0.02 (val 0.02, 69 val trades, options P&L -17,288); best 1.59 (val 1.59, 35 val trades, P&L +14,219) {'target_delta': '0.5 -> 0.7', 'take_profit': '0.6 -> 0.4', 'exit_dte': '7 -> 14', 'allocation': '0.1 -> 0.05', 'dte': '[30, 60] -> [60, 120]'}
- options decision: ADOPT
- briefing accuracy (record only, 26 days): predictions 39/51 (76%), predictions unverifiable 1, bias vs SPY 16/26 (62%)

