# Deriv Insight v1.3 — Forward Validation Lab

v1.3 adds a genuinely forward-only research layer on top of the historical
v1.1–v1.2.3 experiments.

## Why it exists

Historical discovery can produce patterns that disappear in new data. The
forward lab therefore records a no-lookback market boundary *before* the next
validation sample exists.

For every selected 1-second Volatility market, arming stores the latest Deriv
tick epoch. The next forward window starts one epoch later.

## Window integrity

A forward window contains exactly 25,000 consecutive 1-second epochs.

Deriv's `ticks_history` request supports explicit `start` and `end` boundaries.
v1.3 requests that future interval in chunks of at most 900 seconds, filters
every response back to the registered boundaries, deduplicates by epoch and
requires exactly 25,000 observations before saving a window.

A mismatch aborts the window rather than silently accepting partial history.

The next window starts immediately after the previous window end, making the
windows non-overlapping.

## Frozen statistical protocol

Each future window uses the existing Balanced Contracts engine:

- Even
- Odd
- Over 4
- Under 5
- 70% discovery
- 30% holdout
- context N >= 150
- discovery uplift >= +0.75 pp
- BH-FDR q <= 0.10
- shrinkage strength 200

Live pricing is checked after the statistical window is complete. This is an
evaluation-time price gate; it is not a claim that the quote was the historical
price at every tick inside the window.

## Persistence

Three new models are introduced:

- `ForwardCohort`
- `ForwardMarket`
- `ForwardWindow`

A completed window stores its immutable epoch interval, count, pip size,
SHA-256 raw-data fingerprint and compact research results.

## Repetition

The page aggregates validated candidate keys across all completed future
windows in the cohort.

An effect is called "repeated" on the page only after the same exact
context/outcome key survives holdout in at least two future windows.

## Execution

The forward lab is research-only. It does not enable demo or real trading.
