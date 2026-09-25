# Deriv Insight v1.2.3 — Stepwise Balanced Batch

## Problem fixed

v1.2.2 made the Balanced Contracts Lab multi-market, but the entire selected
batch still ran synchronously inside one HTTP request.

With eight markets at 25,000 ticks, historical paging, proposal pricing,
request pacing and 429 backoff could push that single request past Gunicorn's
120-second timeout. The web worker could then be killed before the response was
returned.

## Stepwise architecture

v1.2.3 does not raise the timeout.

Instead, the initial POST only creates a batch. Each selected symbol receives a
`ResearchRun` row with `batch_status = "pending"`.

The browser then calls a dedicated authenticated POST endpoint for the batch.
Each endpoint call processes exactly one pending symbol and stores a newer row
for that same symbol with `batch_status = "complete"` or `"failed"`.

The existing latest-state logic means no schema migration is needed.

## Persistence

A refresh or navigation does not destroy progress. The batch is reconstructed
from the latest `ResearchRun` state for every symbol.

If pending work remains, reopening the batch page restarts browser-driven
processing automatically.

If the batch finishes with failed symbols, "Retry failed markets" creates new
pending states only for those failed symbols. Completed markets remain intact.

## Research logic

The statistical study is unchanged:

- Even
- Odd
- Over 4
- Under 5
- 70% discovery / 30% holdout
- minimum context N 150
- minimum uplift +0.75 pp
- BH-FDR q <= 0.10
- live ask/payout break-even comparison

No execution capability is added.
