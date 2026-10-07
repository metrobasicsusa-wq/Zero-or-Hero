# Registered S500 cloud research

This module executes the already registered 2026-10-08 through 2026-12-31
59-session stock-proxy observation protocol. It does not submit orders, read
accounts, reset capital, purchase data, or start an AI conversation. The original
`protocol.json` and its bound files remain byte-identical to registration commit
`3c9da12278bef5415baf123c8f5f810ceddd6ea8` in the shared research repository.
`deployment.json` separately binds this operational extension before the first
observation. Historical `operational_status` fields in the protocol are not edited.

The private repository workflow `.github/workflows/research.yml` uses existing
`ALPACA_500_API_KEY` and `ALPACA_500_SECRET_KEY` repository secrets, and the job's
GitHub token only for private state persistence. It requires the existing
`CLOUD_ENABLED=true` switch. Deploying this workflow enables research by default;
setting the optional `S500_RESEARCH_ENABLED=false` switch pauses it. This setup
does not need permission to read or edit repository variables, and does not change
the existing observer workflow or its external scheduler.

## Clock and schedule

UTC weekday trigger candidates in October–December: **13:03, 20:23, 21:23, 22:33**.
All decisions use the runner's actual America/New_York clock and the frozen
calendar; GitHub can delay or skip a trigger.

| Phase | Summer Eastern time | Winter Eastern time |
|---|---|---|
| Prior-20 selection capture | 09:03 | 08:03 |
| Regular-session postclose capture | 16:23 | 16:23 |
| Audit and due checkpoints | 18:33 | 17:33 |

The other postclose candidate is outside the allowed window and does no market
request. On the November 27 and December 24 13:00 close, the first eligible
candidate is 15:23 Eastern; the later candidate sees an existing reservation and
cannot repeat the capture. Selection seals must finish by 09:20. Source requests
and receipts must finish between the actual close plus 15 minutes and 17:15.
Offline verification and analysis may finish later. All review checkpoints fall
in winter time: November 4, December 3, December 31; the candidate is 17:33, three
minutes after the registered 17:30 review time, and actual delay is recorded.

Outside the 2026 study dates no primary capture is possible. GitHub cron has no
year field, so this workflow should be disabled after the final audit to avoid
lightweight no-op jobs in later years. The CLI does not keep a daemon running.

## Persistence and failures

The private `codex-500-research-state` branch holds append-only records under
`studies/s500-prospective-v1/`. It is separate from the observer's main-branch
records. Before any source request a create-only `claim.json` must be remotely
confirmed. Claims do not expire, and cannot be reacquired after a crashed worker.
This operational policy is more conservative than searching later for a complete
bundle: at most one bounded acquisition attempt per date/phase, with retries only
inside that collector. Failed and interrupted dates remain in the ledger.

The first request-complete source bundle is frozen even if bars are missing or
invalid. Missingness never triggers a more favorable source replacement. Raw
response bytes, request/receipt metadata, normalized inputs and selection seals
are compressed into private archives. Archive bounds: 5,000 files, 256 MiB
uncompressed, 30 MiB compressed. Traversal, links, duplicate members and overwrites
are rejected. Git state writes are nonforce, limited to immutable paths and
confirmed by exact blob/byte readback. A reservation without a confirmed result
means unknown, not success or zero return. A canceled job can leave unarchived
local source bytes; its permanent claim exposes that interruption.

The late audit appends an as-of ledger with all 59 dates and never occupies a
collector's result path. A later legal offline result remains visible in a newer
snapshot. Checkpoints are keyed by their input hashes, so identical input is not
recomputed and new evidence cannot silently replace the earlier report.

## Methods and limits

Every selected case retains all 36 frozen variants; the report includes all 216
variant/benchmark/group summaries and 12 fixed feature bins. The only primary
final endpoint is the date-equal mean SPY-down 2% REBOUND 60-minute 10bp-per-side
net stock proxy. Only the final checkpoint calculates the registered descriptive
2,000-draw whole-date bootstrap. Empty dates are not zero-return days. Fewer than
20 complete outcome dates is only an insufficient-sample label. There is no
minimum 40% win rate, automatic pass/fail, strategy retuning or trading activation.

This is a fixed 6,633-symbol cohort, not a point-in-time or expanding market
universe. Postclose SIP bars do not establish live intraday availability or option
fills. Receipt logs establish internal consistency, not externally authenticated
delivery. This program does not demonstrate $500-to-$10,000 profitability.

## Verification and operation

Run `PYTHONPATH=research/s500 python3 -m unittest discover -s research/s500/tests -v`.
The tests use synthetic fixtures and never use broker or GitHub credentials.
`cloud_runner.py --mode auto` routes by the real clock; there is no CLI date or
clock override. `--mode rehearsal` checks private GitHub writes, exact reads and
archive restoration using synthetic bytes only. `--mode connectivity` makes one
historical SPY SIP minute-bar GET for 2026-10-06 and stores its private receipt;
it is not an observation and does not retry on 401/403. Every invocation has a
12-minute job limit. No billing limit or paid data subscription is changed.

Reports for peer review must be published separately to new immutable
`exchange/codex/s500/` paths in the shared repository. Do not publish raw archives,
credentials, account identifiers, order identifiers, or private filesystem paths.
