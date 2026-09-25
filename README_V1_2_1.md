# Deriv Insight v1.2.1

This release has two goals:

1. make cross-market studies robust to Deriv rate limiting without restarting
   already completed work;
2. focus a separate research family on the four balanced digit contracts whose
   naive baseline probability is 50%.

## Rate-limit resilience

Deriv documents per-minute and per-hour WebSocket request budgets. v1.2.1 paces
history pages, reuses the WebSocket connection, backs off after rate-limit
responses, and resumes the same page after reconnecting.

A cross-market batch is persisted market-by-market in `ResearchRun`. A failed
market is saved as failed. The page can then retry only the latest failed state
for that saved batch.

No new model or migration is required.

## Balanced Contracts Lab

The research family is frozen before looking at the result:

- Even
- Odd
- Over 4
- Under 5

The engine does not search other digit contract types on this page.

Discovery/validation:
- 70% discovery
- 30% holdout
- minimum context N 150
- minimum uplift +0.75 pp
- BH-FDR q <= 0.10
- shrinkage strength 200

The current Deriv proposal price is queried only after the statistical study is
formed. Break-even probability is `ask_price / payout`.

A conditional result is labelled `RESEARCH CANDIDATE` only if it survived
holdout, its shrinkage-adjusted probability is above live break-even, and its
raw Wilson 95% lower confidence bound is also above break-even.

This remains research-only. v1.2.1 does not enable demo execution or real
trading.
