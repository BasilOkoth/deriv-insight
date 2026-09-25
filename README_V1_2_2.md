# Deriv Insight v1.2.2 — Multi-Market Balanced Batch

v1.2.2 turns the Balanced Contracts Lab from a single-symbol screen into a
resumable multi-market experiment.

## Default experiment

The page dynamically selects up to eight currently active 1-second Volatility
indices and applies the same frozen research specification to each market.

The statistical family remains pre-declared:

- Even
- Odd
- Over 4
- Under 5

The statistical thresholds have not been loosened.

## One-click comparison

A single batch reports, for every completed market:

- holdout probability for all four balanced contracts
- current live break-even probability
- probability-minus-break-even edge
- number of statistical tests
- discovery candidate count
- validated count
- current live research-candidate count

## Persistence and retry

The existing `ResearchRun.results` JSON field stores a batch ID and each
market's latest COMPLETE or FAILED state. There is no migration.

If one or more symbols fail, the batch remains resumable. The retry action runs
only those failed symbols.

## Cross-market recurrence

Validated conditional effects are keyed by exact context + exact outcome.
v1.2.2 reports an effect only as cross-market recurrent when that same key
survives holdout on two or more markets in the same batch.

This is stronger evidence than one isolated market, but it still does not
enable execution.

## Execution

No demo or real trade is placed by this page. `DERIV_DEMO_ENABLED` should stay
false while the research family is being evaluated.
