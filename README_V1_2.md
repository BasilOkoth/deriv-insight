# Deriv Insight v1.2 — Cross-Market & Stability Lab

v1.2 preserves the v1.1 statistical specification instead of loosening it after
a zero-candidate result.

## Frozen research specification

- 70% chronological discovery / 30% holdout
- minimum discovery context N = 150
- minimum discovery uplift = +0.75 percentage points
- Benjamini-Hochberg FDR q <= 0.10
- shrinkage strength = 200
- non-overlapping stability windows = 5,000 ticks

These settings are not editable from the Cross-Market page.

## Why cross-market?

A conditional effect that appears in only one synthetic market may be sample
noise or market-specific. v1.2 runs the same procedure on multiple currently
active Deriv synthetic symbols and reports when the exact same validated
condition/outcome pair appears on two or more markets.

The universe is discovered from active_symbols rather than hard-coded. This is
important because Deriv changes and adds synthetic markets over time.

## Rolling-window stability

Each complete 5,000-tick window is scanned using the same discovery thresholds.
The page reports a condition only as rolling-recurrent when it independently
passes the discovery gate in at least two windows.

This is diagnostic recurrence. It does not bypass the chronological
discovery/holdout validator.

## Candidate recurrence across research runs

Each completed symbol study is saved in the existing ResearchRun table using
contract_type = CROSS_MARKET_V12. Only compact summaries and candidate keys are
stored.

On later runs, the page counts how often a validated candidate has reappeared
in up to 12 recent v1.2 studies of that same symbol.

## Proposal-price archive

The existing ProposalSnapshot table is reused. No migration is needed.

A compact reference grid is archived during web studies if enabled. A new
management command can collect the same grid repeatedly:

    python manage.py archive_digit_quotes --stake 1 --interval 300

The archive stores timestamped ask, payout and break-even probability. This
creates the foundation for future backtests that use observed contract
economics instead of assuming a fixed return percentage.

## Execution

v1.2 does not change demo execution logic and does not enable real trading.
The new Cross-Market page is research-only.
