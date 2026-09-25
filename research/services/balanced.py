from __future__ import annotations

from .conditional import (
    OUTCOME_BY_ID,
    _accumulate,
    _bh_qvalues,
    _two_proportion_pvalue,
)
from .digits import wilson


BALANCED_ENGINE_VERSION = "1.2.3"
RUN_TYPE = "BALANCED_V123"
BALANCED_OUTCOME_IDS = ["EVEN", "ODD", "OVER_4", "UNDER_5"]

FROZEN = {
    "discovery_pct": 70,
    "min_context_n": 150,
    "min_uplift_pp": 0.75,
    "fdr_q": 0.10,
    "shrink": 200,
}


def balanced_outcomes():
    return [OUTCOME_BY_ID[oid] for oid in BALANCED_OUTCOME_IDS]


def _scan_balanced(acc, min_context_n):
    """Scan only the four pre-declared balanced contract outcomes."""
    total = acc["global_n"]
    rows = []

    if total <= 0:
        return rows

    for context_id, n1 in acc["context_n"].items():
        if n1 < min_context_n:
            continue

        n2 = total - n1
        if n2 <= 0:
            continue

        meta = acc["context_meta"][context_id]

        for outcome_id in BALANCED_OUTCOME_IDS:
            outcome = OUTCOME_BY_ID[outcome_id]
            h1 = int(acc["context_hits"][context_id].get(outcome_id, 0))
            h_all = int(acc["global_hits"].get(outcome_id, 0))
            h2 = h_all - h1

            p_cond = h1 / n1
            p_global = h_all / total
            p_comp = h2 / n2

            rows.append(
                {
                    "context_id": context_id,
                    "condition": meta["label"],
                    "condition_group": meta["group"],
                    "outcome_id": outcome_id,
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


def _evaluate_holdout(acc, candidates, min_validation_n):
    total = acc["global_n"]
    rows = []

    for source in candidates:
        context_id = source["context_id"]
        outcome_id = source["outcome_id"]

        n1 = int(acc["context_n"].get(context_id, 0))
        h1 = int(acc["context_hits"].get(context_id, {}).get(outcome_id, 0))
        h_all = int(acc["global_hits"].get(outcome_id, 0))
        n2 = total - n1
        h2 = h_all - h1

        p_global = h_all / total if total else 0.0
        p_cond = h1 / n1 if n1 else 0.0
        lower, upper = wilson(h1, n1) if n1 else (0.0, 1.0)
        shrunk = (
            (h1 + FROZEN["shrink"] * p_global)
            / (n1 + FROZEN["shrink"])
            if n1
            else p_global
        )

        pvalue = (
            _two_proportion_pvalue(h1, n1, h2, n2)
            if n1 >= min_validation_n and n2 > 0
            else 1.0
        )

        row = dict(source)
        row.update(
            {
                "validation_n": n1,
                "validation_hits": h1,
                "validation_p": p_cond,
                "validation_global_p": p_global,
                "validation_uplift_pp": (p_cond - p_global) * 100.0,
                "validation_lower": lower,
                "validation_upper": upper,
                "validation_shrunk_p": shrunk,
                "validation_pvalue": pvalue,
            }
        )
        rows.append(row)

    qvalues = _bh_qvalues([row["validation_pvalue"] for row in rows])
    for row, q in zip(rows, qvalues):
        row["validation_qvalue"] = q
        row["holdout_pass"] = (
            row["validation_n"] >= min_validation_n
            and row["validation_uplift_pp"] > 0
            and q <= FROZEN["fdr_q"]
        )
    return rows


def _baseline_rows(acc):
    total = acc["global_n"]
    rows = []
    for outcome_id in BALANCED_OUTCOME_IDS:
        outcome = OUTCOME_BY_ID[outcome_id]
        hits = int(acc["global_hits"].get(outcome_id, 0))
        p = hits / total if total else 0.0
        lower, upper = wilson(hits, total) if total else (0.0, 1.0)
        rows.append(
            {
                "outcome_id": outcome_id,
                "outcome": outcome["label"],
                "contract_type": outcome["contract_type"],
                "barrier": outcome["barrier"],
                "n": total,
                "hits": hits,
                "p": p,
                "lower": lower,
                "upper": upper,
            }
        )
    return rows


def run_balanced_lab(digits):
    """Discovery/holdout research for Even, Odd, Over 4 and Under 5 only."""
    digits = [int(d) for d in digits]
    total = len(digits)

    if total < 5000:
        raise ValueError("Balanced Contracts Lab requires at least 5,000 ticks.")

    split = int(total * FROZEN["discovery_pct"] / 100.0)
    split = max(3500, min(split, total - 1500))

    discovery = digits[:split]
    holdout = digits[split:]

    disc_acc = _accumulate(discovery)
    hold_acc = _accumulate(holdout)

    tests = _scan_balanced(disc_acc, FROZEN["min_context_n"])
    candidates = [
        row
        for row in tests
        if row["uplift_pp"] >= FROZEN["min_uplift_pp"]
        and row["qvalue"] <= FROZEN["fdr_q"]
    ]
    candidates.sort(key=lambda row: (row["qvalue"], -row["uplift_pp"], -row["n"]))

    ratio = len(holdout) / max(len(discovery), 1)
    min_validation_n = max(
        40,
        int(round(FROZEN["min_context_n"] * ratio * 0.70)),
    )

    validation_rows = _evaluate_holdout(
        hold_acc,
        candidates,
        min_validation_n,
    )
    validated = [row for row in validation_rows if row["holdout_pass"]]
    rejected = [row for row in validation_rows if not row["holdout_pass"]]

    validated.sort(
        key=lambda row: (
            row["validation_qvalue"],
            -row["validation_uplift_pp"],
            -row["validation_n"],
        )
    )

    return {
        "engine_version": BALANCED_ENGINE_VERSION,
        "ticks": total,
        "discovery_ticks": len(discovery),
        "holdout_ticks": len(holdout),
        "frozen": dict(FROZEN),
        "tests_run": len(tests),
        "discovery_candidate_count": len(candidates),
        "validated_candidate_count": len(validated),
        "min_validation_n": min_validation_n,
        "discovery_candidates": candidates[:40],
        "validated": validated,
        "rejected": rejected[:40],
        "holdout_baselines": _baseline_rows(hold_acc),
    }
