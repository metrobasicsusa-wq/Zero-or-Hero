# Provider minute normalization

Call `normalize_provider_bars(provider_bars, date, session_open_et="09:30")` from
`normalize_bars.py`. The result is `{"bars": [...], "issues": [...]}`. Feed its
`bars` list directly into `prepare_bars` or the event functions. Preserve list
form so duplicate records remain detectable. Do not convert it into a dict that
overwrites duplicate timestamps. `issues` is a diagnostic list, not a reason to
automatically discard all otherwise-valid earlier minutes: a duplicate at a
known later minute only invalidates affected engine prefixes/windows.

The helper requires a timezone-aware RFC3339 timestamp with explicit seconds and
at most nine fractional digits. Fractional digits must all be zero, and seconds
must equal zero. This is checked before datetime conversion, so a timestamp
ending `.000000001Z` cannot be silently truncated to a minute. UTC offset hours
must be <=23 and minutes <=59. `-00:00` denotes unknown local offset and is not
accepted as UTC. Unsupported leap seconds are preserved as unknown, not adjusted.
Valid timestamps are measured as exact integer minutes from the calendar date's
New York opening time, respecting daylight saving time. The helper does not
clip before or after session, sort, deduplicate, forward fill, or alter OHLCV.

Bad timestamps retain their original string and have a diagnostic issue; a
non-string timestamp is stringified so it cannot be mistaken for an integer
minute offset. Non-mapping records retain an invalid placeholder. These inputs
cause the engine's conservative `unknown_source`, even if the text resembles a
later timestamp, because they cannot be assigned to a valid minute. In contrast,
future price errors or duplicates at a valid identifiable minute cannot erase an
earlier signal. The conservative handling of unlocatable records is a coverage
limitation, not use of future prices in the economic signal.

`source_complete` is still the collector's acquisition-completeness flag; it is
not inferred from an empty issues list. The normalizer does not inspect market
returns or change the already-tested event-engine source.
