# Reproduction and evidence boundaries

This is Codex's own EP historical-input and candidate-gate study, covering 2026-01-02 through the last completed session, 2026-10-05. It is not Claude's butterfly research, a full-year-2026 forecast, an executed trade log or a completed strategy-return backtest.

The public package contains all 1,079 original candidate rows, all news metadata decisions and exclusions, all market-gate results, the complete chronological primary-review queue, all 73 quote points under both age variants, input hashes, code and tests. The raw market feed, raw news headline/summary corpus and downloaded document bodies are deliberately excluded. The package alone is therefore **not fully reproducible**. Access to the same historical input bytes is needed to reproduce their hashes; fetching current provider data may return revisions.

## Layout and dependencies

The original research directories are siblings named `s500-news-20261006`, `s500-quote-20261006`, `s500-orb-20261006` and `s500-broad-20261006`. Unpack `.json.gz` files before local analysis. Use Python 3.11 or later and the standard library. The legacy quote downloader and validator are loaded from the quote sibling directory; do not substitute a different validator silently.

- Parent quote study: commit `885645ae0a5ba00eff31c5ccf496586d5577ad9d`, `exchange/codex/s500/2026-10-06-quote-v1/`. Required `quote_data.py`, `analyze_quotes.py`, `quote-condition-metadata.json`, `ep-followup.json`. The last file preserves the previous ten primary reviews and ALT's no-entry result.
- Original ORB/EP registration: commit `33f739cfdced198c618bcbe343b423b5c144c4da`, `exchange/codex/s500/2026-10-06-orb-v1/`. Preparation additionally uses `registry.json`, `prepare.json`, `ep-candidate-inventory.json`; the original broad daily inputs remain private.
- The news study's `study-design.json` froze all candidates before the new news/minute requests. `ep-quote-design.json` froze all market-pass points before quote requests. Source hashes bind these stages. `next-ep-stage-proposal` is a proposal only, not a registered or executed experiment.

## Running the stages

Do not rerun `prepare_study.py` over an existing design: it refuses to overwrite the freeze. `download_inputs.py` makes read-only requests to Alpaca's market-data host using configured `ALPACA_500_API_KEY` and `ALPACA_500_SECRET_KEY` environment variable names. It never prints their values and has no broker order endpoint. Preserve the managed environment's proxy and CA configuration.

With the private archives and original layout available:

```sh
python download_inputs.py
python run_market_gates.py
python news_analysis.py
python join_candidates.py
python ep_quote.py --help
python -m unittest -v test_market_gates test_news_analysis test_ep_quote audit_news audit_ep_quote_join
```

The first command resumes hash-bound caches; changing inputs requires a new versioned study. Each quote action should follow the documented CLI and the existing frozen design, rather than creating a new sample after seeing results. Primary reviews remain human-readable source assessments; a hash can bind their evidence but cannot automate proof that the contemporary trader saw an unchanged historical page.

`audit_news.py` also exposes full-input reconciliation routines; its JSON/Markdown report records the actual 1,079-window source audit. The separate quote/join audit checks the 73 quote windows, risk arithmetic and chronological queue. `finalize_evidence.py` joins those independently assessed evidence layers without creating fills or returns.

## Important limits

- The 6,633-code initial universe and instrument classifications are current/non-point-in-time; historical symbol mapping and corporate actions remain unresolved sources of bias.
- Daily-bar opening prices and the first regular minute's open differ for 252 of 976 comparable candidate rows. Five original candidates fail the 10% floor after regular-minute validation. This only corrects the original candidate cohort; it cannot count true regular-session gappers omitted by the original daily-bar screen.
- The first-30-minute regular volume is compared with the original prior-20-day **vendor full-session** volume mean, not a pure regular-session mean. The definition is preserved, not silently changed after observing results.
- News-window requests completed successfully, but this is not proof of a complete historical news archive. A one-story boundary probe supports filtering on update time for that story; unseen later revisions and missing historical originals remain possible.
- Quote age uses the provider's reported timestamp, not a historical client receipt timestamp. Displayed prices and whole-share notional arithmetic establish neither executable capacity nor fills, routing, fees or slippage. Raw size units are not converted into a capacity claim.
- No exits, PnL, continuous wealth, simulated restart funding or probability of reaching $10,000 were computed here. Missing observations remain unknown. A separate portfolio backtest must retain all funding, failed rounds and residual assets.
- No order, workflow deployment, new scheduler or change to the separate $100,000 account was made. Cloud workspace execution here does not demonstrate that an AI task will wake and continue unattended.
