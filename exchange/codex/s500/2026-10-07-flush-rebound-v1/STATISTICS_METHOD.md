# Flush/rebound descriptive statistics

The input is the runner's immutable JSONL candidate ledger. One row is one
selected symbol-session and one of the 36 registered variants: drops of 2% or
3%, FIXED or REBOUND, exits at 30 minutes / 60 minutes / close minus 10 minutes,
and fixed costs of 5 / 10 / 25 basis points **per side**. The summarizer checks
every registry case has all 36 variants exactly once and rejects duplicate rows,
unknown dates, a changed case identity, and unregistered variants.

All returns are fractions (`0.01` means 1%). The runner supplies decimal strings;
the descriptive statistics convert finite values to Python floats. Missing values
are `null`, never zero. Zero is an observed zero return and is not counted as a
positive return. The input ledger, including every no-signal or missing row,
remains separate from the summary.

The primary variant is a 2% drop, REBOUND, 60-minute exit, and 10 basis points per
side. Primary target statistics include stocks only. SPY and QQQ each receive a
separate descriptive group and are never combined into the stock target sample.

For every group and variant, all-period, monthly, H1, and H2-to-cutoff slices retain
selected-case counts, eligible risk sets, true/false/unknown flush counts, signal
statuses, missing stages/reasons, complete outcome counts, and paired-control
statuses. `event_weighted` gives the count, mean, median, positive fraction, zeros,
negatives, minimum, and maximum across available event outcomes. `date_weighted`
first takes the mean of observed outcomes within each date and then describes
those equally weighted date means. A date without complete outcomes has no
return observation; it is not assigned a zero. The separate daily file includes
all registered calendar dates, even those with zero complete outcomes.

Matched excess is independently reconstructed as the target's net return minus
the equally weighted net returns of exactly three distinct, complete stock
controls. The target cannot control itself; SPY and QQQ are separate benchmarks,
not stock controls. An incomplete control set never becomes a two-control mean.
The recomputed value is checked against the runner's value. Alignment to the same
date, entry time, and exit time, and the causal control-selection rule are
runner/source-audit obligations; this module receives those already selected
controls and cannot infer their alignment from the compact row schema.

For all 36 stock variants, the reported bootstrap point estimate is the equally
weighted mean of complete matched-excess **date means**. Each replicate samples
the same number of dates with replacement and averages those date means. This
is not an IID event bootstrap. There are 2,000 replicates, with 2.5% and 97.5%
percentiles using linear interpolation at `(n-1)*p`. Fewer than two available
dates yields no interval. The seed is the integer encoded by the first 16 hex
digits of SHA-256 of:

```
s500-flush-rebound-20261007-date-mean-excess-v1|<group>|<variant_id>
```

The interval is descriptive. It does not adjust for temporal dependence between
dates, multiple hypotheses, or the current-directory and data-availability
selection biases. All variants are reported in the registered parameter order,
not ranked by realized return. H2 is a descriptive period, not a strict unseen
test set after earlier peer-inspired research. Monthly and H1/H2 slices have no
additional confidence intervals in this stage.

These are stock price-proxy event returns after hypothetical fixed costs, not
verified fills, options returns, or an implementable $500 capital process. There
is no conversion to account wealth, compounding, or a claimed $500-to-$10,000
probability.

`test_summarize_flush.py` uses synthetic rows only. It checks date versus pooled
weighting, absent outcomes, separate horizons, deterministic bootstrap seeds,
strict three-control matching, and complete case/variant coverage. Run the real
CLI only after the research code and synthetic tests have been bound in the
parent's pre-analysis validation record.
