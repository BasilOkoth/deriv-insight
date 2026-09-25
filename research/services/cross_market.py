from __future__ import annotations

from collections import defaultdict

from .conditional import (
    _accumulate,
    _scan_all,
    run_conditional_edge_lab,
)


CROSS_MARKET_ENGINE_VERSION = "1.2"
RUN_TYPE = "CROSS_MARKET_V12"

# Frozen from the v1.1 experiment. The cross-market page does not expose these
# as tuning knobs.
FROZEN = {
    "discovery_pct": 70,
    "min_context_n": 150,
    "min_uplift_pp": 0.75,
    "fdr_q": 0.10,
    "shrink": 200,
    "window_size": 5000,
}

# A compact reference grid archived on cross-market runs. A separate management
# command can archive a larger grid continuously.
REFERENCE_QUOTES = [
    {"contract_type": "DIGITMATCH", "barrier": "0", "label": "Match 0"},
    {"contract_type": "DIGITMATCH", "barrier": "5", "label": "Match 5"},
    {"contract_type": "DIGITOVER", "barrier": "4", "label": "Over 4"},
    {"contract_type": "DIGITUNDER", "barrier": "5", "label": "Under 5"},
    {"contract_type": "DIGITEVEN", "barrier": None, "label": "Even"},
    {"contract_type": "DIGITODD", "barrier": None, "label": "Odd"},
]


def synthetic_symbol_rows(client):
    """Return active synthetic-style symbols without hard-coding the universe."""
    rows = client.active_symbols()
    out = []

    for row in rows:
        code = row.get("underlying_symbol") or row.get("symbol")
        name = (
            row.get("underlying_symbol_name")
            or row.get("display_name")
            or code
        )
        market = (row.get("market") or "").lower()
        submarket = (row.get("submarket") or "").lower()
        symbol_type = (
            row.get("underlying_symbol_type")
            or row.get("symbol_type")
            or ""
        ).lower()

        looks_synthetic = (
            "synthetic" in market
            or "synthetic" in submarket
            or "synthetic" in symbol_type
            or (code or "").startswith(
                ("R_", "1HZ", "JD", "BOOM", "CRASH", "RDB")
            )
        )
        if code and looks_synthetic:
            out.append(
                {
                    "code": code,
                    "name": name or code,
                    "pip_size": row.get("pip_size") or row.get("pip"),
                }
            )

    # Stable display ordering.
    out.sort(key=lambda row: (str(row["name"]).lower(), row["code"]))
    return out


def _candidate_key(row):
    return f'{row["context_id"]}|{row["outcome_id"]}'


def _window_scan(window_digits):
    """Apply the same discovery gate to one non-overlapping rolling window."""
    acc = _accumulate(window_digits)
    tests = _scan_all(acc, FROZEN["min_context_n"])
    passing = [
        row
        for row in tests
        if row["uplift_pp"] >= FROZEN["min_uplift_pp"]
        and row["qvalue"] <= FROZEN["fdr_q"]
    ]
    passing.sort(key=lambda row: (row["qvalue"], -row["uplift_pp"], -row["n"]))
    return tests, passing


def run_symbol_stability(digits):
    """Run v1.1 holdout validation plus non-overlapping stability windows."""
    digits = [int(d) for d in digits]

    whole = run_conditional_edge_lab(
        digits,
        discovery_pct=FROZEN["discovery_pct"],
        min_context_n=FROZEN["min_context_n"],
        min_uplift_pp=FROZEN["min_uplift_pp"],
        fdr_q=FROZEN["fdr_q"],
        shrink=FROZEN["shrink"],
    )

    window_size = FROZEN["window_size"]
    windows = []
    recurrence = defaultdict(
        lambda: {
            "windows_hit": 0,
            "window_indices": [],
            "uplifts": [],
            "qvalues": [],
            "condition": "",
            "condition_group": "",
            "outcome": "",
            "contract_type": "",
            "barrier": None,
        }
    )

    full_windows = len(digits) // window_size
    for wi in range(full_windows):
        start = wi * window_size
        end = start + window_size
        chunk = digits[start:end]

        tests, passing = _window_scan(chunk)
        windows.append(
            {
                "window": wi + 1,
                "start_tick": start + 1,
                "end_tick": end,
                "tests_run": len(tests),
                "candidate_count": len(passing),
            }
        )

        for row in passing:
            key = _candidate_key(row)
            rec = recurrence[key]
            rec["windows_hit"] += 1
            rec["window_indices"].append(wi + 1)
            rec["uplifts"].append(row["uplift_pp"])
            rec["qvalues"].append(row["qvalue"])
            rec["condition"] = row["condition"]
            rec["condition_group"] = row["condition_group"]
            rec["outcome"] = row["outcome"]
            rec["contract_type"] = row["contract_type"]
            rec["barrier"] = row["barrier"]

    rolling_recurrence = []
    for key, rec in recurrence.items():
        hit = rec["windows_hit"]
        rolling_recurrence.append(
            {
                "key": key,
                "condition": rec["condition"],
                "condition_group": rec["condition_group"],
                "outcome": rec["outcome"],
                "contract_type": rec["contract_type"],
                "barrier": rec["barrier"],
                "windows_hit": hit,
                "windows_total": full_windows,
                "recurrence_pct": (
                    hit / full_windows * 100 if full_windows else 0.0
                ),
                "avg_uplift_pp": (
                    sum(rec["uplifts"]) / hit if hit else 0.0
                ),
                "best_q": min(rec["qvalues"]) if rec["qvalues"] else 1.0,
                "window_indices": rec["window_indices"],
            }
        )

    rolling_recurrence.sort(
        key=lambda row: (
            -row["windows_hit"],
            row["best_q"],
            -row["avg_uplift_pp"],
        )
    )

    validated = []
    validated_keys = []
    candidate_meta = {}

    recurrence_by_key = {row["key"]: row for row in rolling_recurrence}

    for row in whole["validated"]:
        item = dict(row)
        key = _candidate_key(item)
        validated_keys.append(key)
        candidate_meta[key] = {
            "condition": item["condition"],
            "condition_group": item["condition_group"],
            "outcome": item["outcome"],
            "contract_type": item["contract_type"],
            "barrier": item["barrier"],
        }

        stable = recurrence_by_key.get(key)
        item["candidate_key"] = key
        item["rolling_windows_hit"] = stable["windows_hit"] if stable else 0
        item["rolling_windows_total"] = full_windows
        item["rolling_recurrence_pct"] = (
            stable["recurrence_pct"] if stable else 0.0
        )
        validated.append(item)

    return {
        "engine_version": CROSS_MARKET_ENGINE_VERSION,
        "frozen": dict(FROZEN),
        "ticks": len(digits),
        "tests_run": whole["tests_run"],
        "discovery_candidate_count": whole["discovery_candidate_count"],
        "validated_candidate_count": whole["validated_candidate_count"],
        "validated": validated,
        "validated_keys": validated_keys,
        "candidate_meta": candidate_meta,
        "window_count": full_windows,
        "windows": windows,
        "rolling_recurrence": rolling_recurrence[:30],
        "rolling_recurrent_count": sum(
            1 for row in rolling_recurrence if row["windows_hit"] >= 2
        ),
    }


def quote_reference_grid(client, symbol, stake=1.0):
    """Fetch a small public proposal grid for historical price archiving."""
    rows = []
    for spec in REFERENCE_QUOTES:
        try:
            proposal = client.proposal(
                symbol=symbol,
                contract_type=spec["contract_type"],
                barrier=spec["barrier"],
                stake=float(stake),
                duration=1,
                duration_unit="t",
            )
            ask = float(proposal.get("ask_price") or stake)
            payout = float(proposal.get("payout") or 0)
            if ask <= 0 or payout <= 0:
                raise ValueError("Proposal missing usable ask/payout")
            rows.append(
                {
                    **spec,
                    "ok": True,
                    "ask_price": ask,
                    "payout": payout,
                    "break_even_pct": ask / payout * 100.0,
                    "net_return_pct": (payout - ask) / ask * 100.0,
                    "proposal_id": str(proposal.get("id") or ""),
                    "error": "",
                }
            )
        except Exception as exc:
            rows.append(
                {
                    **spec,
                    "ok": False,
                    "ask_price": None,
                    "payout": None,
                    "break_even_pct": None,
                    "net_return_pct": None,
                    "proposal_id": "",
                    "error": str(exc),
                }
            )
    return rows
