from __future__ import annotations

import hashlib
import json
import time

from django.db import transaction
from django.utils import timezone

from .balanced import run_balanced_lab
from .deriv_client import DerivAPIError, DerivPublicClient
from .digits import digits_from_prices
from ..models import ForwardWindow, ProposalSnapshot


FORWARD_ENGINE_VERSION = "1.3"
FORWARD_WINDOW_TICKS = 25000
FORWARD_FAMILY = ("EVEN", "ODD", "OVER_4", "UNDER_5")


def latest_boundary(client, symbol):
    tick = client.latest_tick(symbol)
    return {
        "epoch": int(tick["epoch"]),
        "pip_size": int(tick.get("pip_size") or 2),
        "price": tick["price"],
    }


def ready_end_epoch(market):
    return int(market.next_epoch) + int(market.cohort.window_ticks) - 1


def approximate_available_ticks(market, now_epoch=None):
    """For 1-second Volatility indices, elapsed seconds approximate ticks."""
    now_epoch = int(now_epoch or time.time())
    return max(
        0,
        min(
            int(market.cohort.window_ticks),
            now_epoch - int(market.next_epoch) + 1,
        ),
    )


def _quote_balanced(symbol, baselines, stake):
    client = DerivPublicClient()
    quote_map = {}

    for base in baselines:
        try:
            proposal = client.proposal(
                symbol=symbol,
                contract_type=base["contract_type"],
                barrier=base["barrier"],
                stake=stake,
                duration=1,
                duration_unit="t",
            )
            ask = float(proposal.get("ask_price") or stake)
            payout = float(proposal.get("payout") or 0)
            if ask <= 0 or payout <= 0:
                raise DerivAPIError(
                    "Proposal did not contain usable ask/payout."
                )

            break_even = ask / payout
            quote_map[base["outcome_id"]] = {
                "ok": True,
                "ask_price": ask,
                "payout": payout,
                "break_even": break_even,
                "error": "",
            }

            ProposalSnapshot.objects.create(
                symbol=symbol,
                contract_type=base["contract_type"],
                barrier=base["barrier"] or "",
                stake=stake,
                ask_price=ask,
                payout=payout,
                break_even_pct=break_even * 100,
                model_probability_pct=base["p"] * 100,
                lower95_pct=base["lower"] * 100,
                edge_pp=(base["p"] - break_even) * 100,
                sample_ticks=FORWARD_WINDOW_TICKS,
                decision="FORWARD V1.3",
            )
        except Exception as exc:
            quote_map[base["outcome_id"]] = {
                "ok": False,
                "ask_price": None,
                "payout": None,
                "break_even": None,
                "error": str(exc),
            }

    return quote_map


def _prepare_baselines(study, quote_map):
    rows = []
    for base in study["holdout_baselines"]:
        quote = quote_map[base["outcome_id"]]
        item = {
            "outcome_id": base["outcome_id"],
            "outcome": base["outcome"],
            "p_pct": base["p"] * 100,
            "lower_pct": base["lower"] * 100,
            "quote_ok": quote["ok"],
            "quote_error": quote["error"],
            "break_even_pct": None,
            "edge_pp": None,
            "lower_edge_pp": None,
            "confident_clear": False,
        }

        if quote["ok"]:
            be = quote["break_even"]
            item["break_even_pct"] = be * 100
            item["edge_pp"] = (base["p"] - be) * 100
            item["lower_edge_pp"] = (base["lower"] - be) * 100
            item["confident_clear"] = item["lower_edge_pp"] > 0

        rows.append(item)
    return rows


def _prepare_validation(study, quote_map):
    rows = []

    for source in study["validated"] + study["rejected"]:
        item = dict(source)
        quote = quote_map[item["outcome_id"]]

        item["candidate_key"] = (
            f'{item["context_id"]}|{item["outcome_id"]}'
        )
        item["validation_p_pct"] = item["validation_p"] * 100
        item["validation_shrunk_p_pct"] = (
            item["validation_shrunk_p"] * 100
        )
        item["validation_lower_pct"] = (
            item["validation_lower"] * 100
        )
        item["quote_ok"] = quote["ok"]
        item["break_even_pct"] = None
        item["live_edge_pp"] = None
        item["live_lower_edge_pp"] = None
        item["live_status"] = (
            "HOLDOUT REJECTED"
            if not item["holdout_pass"]
            else "QUOTE ERROR"
        )

        if quote["ok"]:
            be = quote["break_even"]
            item["break_even_pct"] = be * 100
            item["live_edge_pp"] = (
                item["validation_shrunk_p"] - be
            ) * 100
            item["live_lower_edge_pp"] = (
                item["validation_lower"] - be
            ) * 100

            if item["holdout_pass"]:
                if (
                    item["live_edge_pp"] > 0
                    and item["live_lower_edge_pp"] > 0
                ):
                    item["live_status"] = "RESEARCH CANDIDATE"
                elif item["live_edge_pp"] > 0:
                    item["live_status"] = "WATCH"
                else:
                    item["live_status"] = "NO LIVE EDGE"

        rows.append(item)

    rows.sort(
        key=lambda row: (
            not row["holdout_pass"],
            row["validation_qvalue"],
            -row["validation_uplift_pp"],
        )
    )
    return rows


def _window_hash(times, prices):
    payload = "\n".join(
        f"{int(epoch)}:{price}"
        for epoch, price in zip(times, prices)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def collect_next_window(market):
    """Collect exactly one non-overlapping future window if it is ready."""
    target = int(market.cohort.window_ticks)
    if target != FORWARD_WINDOW_TICKS:
        raise DerivAPIError(
            "v1.3 forward cohorts are frozen at 25,000 ticks."
        )

    start_epoch = int(market.next_epoch)
    end_epoch = start_epoch + target - 1
    client = DerivPublicClient()
    latest = client.latest_tick(market.symbol)
    latest_epoch = int(latest["epoch"])

    if latest_epoch < end_epoch:
        return {
            "status": "waiting",
            "symbol": market.symbol,
            "latest_epoch": latest_epoch,
            "start_epoch": start_epoch,
            "required_end_epoch": end_epoch,
            "available_estimate": max(
                0,
                latest_epoch - start_epoch + 1,
            ),
            "remaining_estimate": max(
                0,
                end_epoch - latest_epoch,
            ),
        }

    data = client.ticks_between(
        market.symbol,
        start_epoch,
        end_epoch,
    )

    if int(data["returned_count"]) != target:
        raise DerivAPIError(
            f"Forward window integrity check failed for {market.symbol}: "
            f"expected {target} explicit future ticks from "
            f"{start_epoch} through {end_epoch}, received "
            f"{data['returned_count']}. Nothing was saved."
        )

    digits = digits_from_prices(
        data["prices"],
        data["pip_size"],
    )

    if len(digits) != target:
        raise DerivAPIError(
            f"Digit conversion produced {len(digits)} observations "
            f"instead of {target}. Nothing was saved."
        )

    study = run_balanced_lab(digits)
    quote_map = _quote_balanced(
        market.symbol,
        study["holdout_baselines"],
        float(market.cohort.stake),
    )
    baselines = _prepare_baselines(study, quote_map)
    validation_rows = _prepare_validation(study, quote_map)

    holdout_validated = [
        row
        for row in validation_rows
        if row["holdout_pass"]
    ]
    live_candidates = [
        row
        for row in validation_rows
        if row["live_status"] == "RESEARCH CANDIDATE"
    ]

    validated_summary = [
        {
            "candidate_key": row["candidate_key"],
            "condition": row["condition"],
            "condition_group": row["condition_group"],
            "outcome": row["outcome"],
            "validation_n": row["validation_n"],
            "validation_p_pct": row["validation_p_pct"],
            "validation_qvalue": row["validation_qvalue"],
            "validation_shrunk_p_pct": row[
                "validation_shrunk_p_pct"
            ],
            "validation_lower_pct": row["validation_lower_pct"],
            "break_even_pct": row["break_even_pct"],
            "live_edge_pp": row["live_edge_pp"],
            "live_lower_edge_pp": row["live_lower_edge_pp"],
            "live_status": row["live_status"],
        }
        for row in holdout_validated
    ]

    result_payload = {
        "engine_version": FORWARD_ENGINE_VERSION,
        "frozen_family": list(FORWARD_FAMILY),
        "frozen_balanced_gate": dict(study["frozen"]),
        "window_ticks": target,
        "discovery_ticks": study["discovery_ticks"],
        "holdout_ticks": study["holdout_ticks"],
        "tests_run": study["tests_run"],
        "discovery_candidate_count": study[
            "discovery_candidate_count"
        ],
        "validated_candidate_count": len(holdout_validated),
        "live_candidate_count": len(live_candidates),
        "baselines": baselines,
        "validated": validated_summary,
        "quoted_at": timezone.now().isoformat(),
    }

    with transaction.atomic():
        locked = type(market).objects.select_for_update().get(
            pk=market.pk
        )

        # Another request may have completed this window while we fetched.
        if int(locked.next_epoch) != start_epoch:
            return {
                "status": "already_advanced",
                "symbol": locked.symbol,
                "next_epoch": int(locked.next_epoch),
            }

        window_number = int(locked.windows_completed) + 1

        window = ForwardWindow.objects.create(
            market=locked,
            window_number=window_number,
            start_epoch=start_epoch,
            end_epoch=end_epoch,
            tick_count=target,
            pip_size=int(data["pip_size"]),
            data_hash=_window_hash(
                data["times"],
                data["prices"],
            ),
            tests_run=study["tests_run"],
            discovery_candidates=study[
                "discovery_candidate_count"
            ],
            validated_candidates=len(holdout_validated),
            live_candidates=len(live_candidates),
            results=result_payload,
        )

        locked.next_epoch = end_epoch + 1
        locked.windows_completed = window_number
        locked.save(
            update_fields=[
                "next_epoch",
                "windows_completed",
                "updated_at",
            ]
        )

    return {
        "status": "complete",
        "symbol": market.symbol,
        "window_id": window.id,
        "window_number": window.window_number,
        "start_epoch": start_epoch,
        "end_epoch": end_epoch,
        "tests_run": window.tests_run,
        "discovery_candidates": window.discovery_candidates,
        "validated_candidates": window.validated_candidates,
        "live_candidates": window.live_candidates,
    }
