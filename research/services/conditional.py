from __future__ import annotations

import math
from collections import defaultdict

from .digits import outcome_hit, wilson

CONDITIONAL_ENGINE_VERSION = "1.1"


def _outcomes():
    rows = []

    for d in range(10):
        rows.append(
            {
                "id": f"MATCH_{d}",
                "label": f"Match {d}",
                "contract_type": "DIGITMATCH",
                "barrier": str(d),
            }
        )

    for d in range(10):
        rows.append(
            {
                "id": f"DIFF_{d}",
                "label": f"Differs {d}",
                "contract_type": "DIGITDIFF",
                "barrier": str(d),
            }
        )

    # Over 9 can never win, so the useful barriers are 0..8.
    for d in range(9):
        rows.append(
            {
                "id": f"OVER_{d}",
                "label": f"Over {d}",
                "contract_type": "DIGITOVER",
                "barrier": str(d),
            }
        )

    # Under 0 can never win, so the useful barriers are 1..9.
    for d in range(1, 10):
        rows.append(
            {
                "id": f"UNDER_{d}",
                "label": f"Under {d}",
                "contract_type": "DIGITUNDER",
                "barrier": str(d),
            }
        )

    rows.extend(
        [
            {
                "id": "EVEN",
                "label": "Even",
                "contract_type": "DIGITEVEN",
                "barrier": None,
            },
            {
                "id": "ODD",
                "label": "Odd",
                "contract_type": "DIGITODD",
                "barrier": None,
            },
        ]
    )
    return rows


OUTCOMES = _outcomes()
OUTCOME_BY_ID = {row["id"]: row for row in OUTCOMES}
WINNING_OUTCOMES_BY_DIGIT = {
    d: [
        row["id"]
        for row in OUTCOMES
        if outcome_hit(d, row["contract_type"], row["barrier"])
    ]
    for d in range(10)
}


def _parity(d):
    return "E" if int(d) % 2 == 0 else "O"


def _active_contexts(digits, i):
    """Return the pre-declared contexts that are true before target index i.

    Contexts use only digits that occurred before the target digit. That keeps
    the scan causal and prevents look-ahead.
    """
    p1 = digits[i - 1]
    p2 = digits[i - 2]
    p3 = digits[i - 3]

    contexts = [
        (
            f"PREV_DIGIT_{p1}",
            f"Previous digit = {p1}",
            "Previous digit",
        ),
        (
            f"PREV_PARITY_{_parity(p1)}",
            f"Previous digit is {'even' if p1 % 2 == 0 else 'odd'}",
            "Parity",
        ),
        (
            "PREV_BAND_LOW" if p1 <= 4 else "PREV_BAND_HIGH",
            "Previous digit is 0–4" if p1 <= 4 else "Previous digit is 5–9",
            "Digit band",
        ),
        (
            f"PAIR_{p2}{p1}",
            f"Previous two digits = {p2},{p1}",
            "Exact two-digit sequence",
        ),
        (
            f"PARITY2_{_parity(p2)}{_parity(p1)}",
            f"Previous two parities = {_parity(p2)},{_parity(p1)}",
            "Two-step parity",
        ),
        (
            "LAST2_SAME" if p2 == p1 else "LAST2_DIFFERENT",
            "Previous two digits are the same"
            if p2 == p1
            else "Previous two digits are different",
            "Repeat state",
        ),
        (
            f"PARITY3_{_parity(p3)}{_parity(p2)}{_parity(p1)}",
            f"Previous three parities = {_parity(p3)},{_parity(p2)},{_parity(p1)}",
            "Three-step parity",
        ),
    ]

    if p3 == p2 == p1:
        contexts.append(
            (
                "LAST3_SAME_DIGIT",
                "Previous three digits are identical",
                "Repeat streak",
            )
        )

    return contexts


def _accumulate(digits):
    """Accumulate global and conditional outcome counts in one pass."""
    global_n = 0
    global_hits = defaultdict(int)

    context_n = defaultdict(int)
    context_hits = defaultdict(lambda: defaultdict(int))
    context_meta = {}

    # Three previous digits are required for the longest pre-declared context.
    for i in range(3, len(digits)):
        target = int(digits[i])
        global_n += 1

        winning_ids = WINNING_OUTCOMES_BY_DIGIT[target]
        for oid in winning_ids:
            global_hits[oid] += 1

        for cid, label, group in _active_contexts(digits, i):
            context_n[cid] += 1
            context_meta[cid] = {"label": label, "group": group}
            for oid in winning_ids:
                context_hits[cid][oid] += 1

    return {
        "global_n": global_n,
        "global_hits": global_hits,
        "context_n": context_n,
        "context_hits": context_hits,
        "context_meta": context_meta,
    }


def _two_proportion_pvalue(h1, n1, h2, n2):
    """Two-sided normal-approximation test for two proportions."""
    if n1 <= 0 or n2 <= 0:
        return 1.0

    p1 = h1 / n1
    p2 = h2 / n2
    pooled = (h1 + h2) / (n1 + n2)

    variance = pooled * (1.0 - pooled) * ((1.0 / n1) + (1.0 / n2))
    if variance <= 0:
        return 1.0

    z = (p1 - p2) / math.sqrt(variance)
    return math.erfc(abs(z) / math.sqrt(2.0))


def _bh_qvalues(pvalues):
    """Benjamini-Hochberg false-discovery-rate adjusted q-values."""
    m = len(pvalues)
    if not m:
        return []

    order = sorted(range(m), key=lambda idx: pvalues[idx])
    qvalues = [1.0] * m
    running = 1.0

    for pos in range(m - 1, -1, -1):
        idx = order[pos]
        rank = pos + 1
        raw_q = pvalues[idx] * m / rank
        running = min(running, raw_q)
        qvalues[idx] = min(1.0, running)

    return qvalues


def _scan_all(acc, min_context_n):
    """Test every pre-declared context/outcome pair on one sample."""
    N = acc["global_n"]
    rows = []

    if N <= 0:
        return rows

    for cid, n1 in acc["context_n"].items():
        if n1 < min_context_n:
            continue

        n2 = N - n1
        if n2 <= 0:
            continue

        meta = acc["context_meta"][cid]

        for outcome in OUTCOMES:
            oid = outcome["id"]
            h1 = int(acc["context_hits"][cid].get(oid, 0))
            h_all = int(acc["global_hits"].get(oid, 0))
            h2 = h_all - h1

            p_cond = h1 / n1
            p_global = h_all / N
            p_comp = h2 / n2 if n2 else p_global

            rows.append(
                {
                    "context_id": cid,
                    "condition": meta["label"],
                    "condition_group": meta["group"],
                    "outcome_id": oid,
                    "outcome": outcome["label"],
                    "contract_type": outcome["contract_type"],
                    "barrier": outcome["barrier"],
                    "n": n1,
                    "hits": h1,
                    "p": p_cond,
                    "global_p": p_global,
                    "complement_p": p_comp,
                    "uplift_pp": (p_cond - p_global) * 100.0,
                    "pvalue": _two_proportion_pvalue(h1, n1, h2, n2),
                }
            )

    qvalues = _bh_qvalues([row["pvalue"] for row in rows])
    for row, q in zip(rows, qvalues):
        row["qvalue"] = q

    return rows


def _evaluate_frozen_candidates(acc, candidates, min_validation_n, fdr_q, shrink):
    """Evaluate discovery-frozen candidates on the newer holdout sample."""
    N = acc["global_n"]
    rows = []

    if N <= 0:
        return rows

    for source in candidates:
        cid = source["context_id"]
        oid = source["outcome_id"]

        n1 = int(acc["context_n"].get(cid, 0))
        h1 = int(acc["context_hits"].get(cid, {}).get(oid, 0))
        h_all = int(acc["global_hits"].get(oid, 0))
        n2 = N - n1
        h2 = h_all - h1

        if n1 > 0:
            p_cond = h1 / n1
            lower, upper = wilson(h1, n1)
        else:
            p_cond = 0.0
            lower, upper = 0.0, 1.0

        p_global = h_all / N
        p_comp = h2 / n2 if n2 > 0 else p_global

        pvalue = (
            _two_proportion_pvalue(h1, n1, h2, n2)
            if n1 >= min_validation_n and n2 > 0
            else 1.0
        )

        shrunk_p = (
            (h1 + float(shrink) * p_global) / (n1 + float(shrink))
            if n1 > 0
            else p_global
        )

        row = dict(source)
        row.update(
            {
                "validation_n": n1,
                "validation_hits": h1,
                "validation_p": p_cond,
                "validation_global_p": p_global,
                "validation_complement_p": p_comp,
                "validation_uplift_pp": (p_cond - p_global) * 100.0,
                "validation_pvalue": pvalue,
                "validation_lower": lower,
                "validation_upper": upper,
                "validation_shrunk_p": shrunk_p,
                "min_validation_n": min_validation_n,
            }
        )
        rows.append(row)

    qvalues = _bh_qvalues([row["validation_pvalue"] for row in rows])
    for row, q in zip(rows, qvalues):
        row["validation_qvalue"] = q
        row["holdout_pass"] = (
            row["validation_n"] >= min_validation_n
            and row["validation_uplift_pp"] > 0
            and q <= fdr_q
        )

        if row["validation_n"] < min_validation_n:
            row["holdout_reason"] = "Too few holdout observations"
        elif row["validation_uplift_pp"] <= 0:
            row["holdout_reason"] = "Effect did not keep the same positive direction"
        elif q > fdr_q:
            row["holdout_reason"] = "Holdout effect did not survive FDR threshold"
        else:
            row["holdout_reason"] = "Validated"

    return rows


def run_conditional_edge_lab(
    digits,
    *,
    discovery_pct=70,
    min_context_n=150,
    min_uplift_pp=0.75,
    fdr_q=0.10,
    shrink=200,
):
    """Discover conditional digit effects, then validate them out-of-sample.

    The older portion is used for discovery. Candidate definitions are frozen
    before the newer holdout is inspected.
    """
    digits = [int(d) for d in digits]
    total = len(digits)

    if total < 2500:
        raise ValueError("Conditional Edge Lab requires at least 2,500 ticks.")

    discovery_pct = max(50, min(int(discovery_pct), 85))
    min_context_n = max(40, int(min_context_n))
    min_uplift_pp = max(0.0, float(min_uplift_pp))
    fdr_q = max(0.001, min(float(fdr_q), 0.25))
    shrink = max(0, int(shrink))

    split = int(total * discovery_pct / 100.0)
    # Keep both sides large enough for meaningful holdout testing.
    split = max(1250, min(split, total - 750))

    discovery_digits = digits[:split]
    validation_digits = digits[split:]

    discovery_acc = _accumulate(discovery_digits)
    discovery_tests = _scan_all(discovery_acc, min_context_n)

    discovery_candidates = [
        row
        for row in discovery_tests
        if row["uplift_pp"] >= min_uplift_pp and row["qvalue"] <= fdr_q
    ]

    discovery_candidates.sort(
        key=lambda row: (row["qvalue"], -row["uplift_pp"], -row["n"])
    )

    validation_ratio = len(validation_digits) / max(len(discovery_digits), 1)
    min_validation_n = max(
        40,
        int(round(min_context_n * validation_ratio * 0.70)),
    )

    validation_rows = _evaluate_frozen_candidates(
        _accumulate(validation_digits),
        discovery_candidates,
        min_validation_n,
        fdr_q,
        shrink,
    )

    validated = [row for row in validation_rows if row["holdout_pass"]]
    validated.sort(
        key=lambda row: (
            row["validation_qvalue"],
            -row["validation_uplift_pp"],
            -row["validation_n"],
        )
    )

    rejected = [row for row in validation_rows if not row["holdout_pass"]]
    rejected.sort(
        key=lambda row: (
            row["validation_qvalue"],
            -row["validation_uplift_pp"],
        )
    )

    return {
        "engine_version": CONDITIONAL_ENGINE_VERSION,
        "ticks": total,
        "discovery_ticks": len(discovery_digits),
        "validation_ticks": len(validation_digits),
        "discovery_pct": discovery_pct,
        "min_context_n": min_context_n,
        "min_validation_n": min_validation_n,
        "min_uplift_pp": min_uplift_pp,
        "fdr_q": fdr_q,
        "shrink": shrink,
        "tests_run": len(discovery_tests),
        "discovery_candidate_count": len(discovery_candidates),
        "validated_candidate_count": len(validated),
        "discovery_candidates": discovery_candidates[:30],
        "validated": validated,
        "rejected": rejected[:30],
    }
