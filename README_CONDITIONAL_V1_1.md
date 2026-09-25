# Deriv Insight v1.1 — Conditional Edge Lab

The v1 Digit Laboratory established a baseline: raw digit frequencies and
one-step repeat rates can be close to uniform even when individual cells look
visually unusual.

v1.1 therefore moves the research question from:

> "Which digit appears more often?"

to:

> "Does any pre-declared condition change the probability of a Deriv digit
> contract outcome, survive a chronological holdout, and still beat the
> current proposal price?"

## Research flow

1. Retrieve 5k / 10k / 25k ticks.
2. Use only the older portion for discovery.
3. Scan pre-declared contexts:
   - previous exact digit
   - previous parity
   - previous 0–4 / 5–9 band
   - exact previous two digits
   - previous two parities
   - previous two same vs different
   - previous three parity pattern
   - three identical previous digits
4. Evaluate Digit Match, Differs, Over, Under, Even and Odd outcomes.
5. Enforce a minimum context sample.
6. Apply Benjamini-Hochberg false-discovery-rate correction.
7. Freeze discovery candidates.
8. Test only those frozen candidates on the newer holdout sample.
9. Apply an FDR check again to the holdout candidates.
10. Query live Deriv proposals only for candidates that survived holdout.
11. Compare:
    - current break-even probability = ask_price / payout
    - holdout probability
    - shrinkage-adjusted probability
    - raw Wilson 95% lower confidence bound

## Why shrink the holdout probability?

Conditional patterns can have much smaller samples than the whole 25,000-tick
dataset. v1.1 shrinks the conditional estimate toward the holdout-wide
probability before comparing it with live pricing. This reduces the temptation
to treat a small conditional sample as a precise probability estimate.

## Interpretation

A "RESEARCH CANDIDATE" is not proof of a profitable strategy and is not a trade
instruction. It means a pre-declared pattern survived this particular
discovery/holdout process and also cleared the current price hurdle
conservatively.

The next stage after a research candidate would be a future, unseen forward
sample and demo-only execution. Real-money execution remains hard-locked.
