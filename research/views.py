from __future__ import annotations

from collections import Counter, defaultdict

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.utils import timezone
from django.conf import settings

from .forms import (
    DigitLabForm,
    ConditionalEdgeForm,
    CrossMarketForm,
    EdgeForm,
    BacktestForm,
    RiskConfigForm,
)
from .models import (
    RiskConfig,
    ResearchRun,
    ProposalSnapshot,
    DemoTrade,
    DigitAggregate,
)
from .services.deriv_client import (
    DerivPublicClient,
    DerivDemoClient,
    DerivAPIError,
)
from .services.digits import (
    digits_from_prices,
    analyze_digits,
    proposal_evaluation,
    threshold_backtest,
)
from .services.conditional import run_conditional_edge_lab
from .services.cross_market import (
    CROSS_MARKET_ENGINE_VERSION,
    RUN_TYPE,
    FROZEN,
    run_symbol_stability,
    quote_reference_grid,
)


BARRIER_TYPES = {"DIGITMATCH", "DIGITDIFF", "DIGITOVER", "DIGITUNDER"}


def _history(symbol, ticks):
    data = DerivPublicClient().ticks_history(symbol, int(ticks))
    digits = digits_from_prices(data["prices"], data["pip_size"])
    return data, digits


def _symbols():
    try:
        rows = DerivPublicClient().active_symbols()
        out = []
        for r in rows:
            code = r.get("underlying_symbol") or r.get("symbol")
            name = (
                r.get("underlying_symbol_name")
                or r.get("display_name")
                or code
            )
            market = (r.get("market") or "").lower()
            sub = (r.get("submarket") or "").lower()
            if code and (
                "synthetic" in market
                or "synthetic" in sub
                or code.startswith(("R_", "1HZ", "JD", "BOOM", "CRASH"))
            ):
                out.append(
                    {
                        "code": code,
                        "name": name or code,
                        "pip_size": r.get("pip_size") or r.get("pip"),
                    }
                )
        return out[:200]
    except Exception:
        return []


@login_required
def overview(request):
    cfg = RiskConfig.current()
    api_ok = False
    symbols = []
    error = ""
    try:
        symbols = _symbols()
        api_ok = bool(symbols)
    except Exception as e:
        error = str(e)

    latest = ProposalSnapshot.objects.all()[:8]
    return render(
        request,
        "research/overview.html",
        {
            "cfg": cfg,
            "api_ok": api_ok,
            "symbols": symbols[:12],
            "symbol_count": len(symbols),
            "error": error,
            "runs": ResearchRun.objects.all()[:6],
            "proposals": latest,
            "aggregates": DigitAggregate.objects.all()
            .order_by("-total_ticks")[:8],
            "demo_env_enabled": settings.DERIV_DEMO_ENABLED,
            "real_enabled": settings.DERIV_REAL_ENABLED,
        },
    )


@login_required
def digit_lab(request):
    cfg = RiskConfig.current()
    form = DigitLabForm(
        request.POST or None,
        initial={
            "symbol": cfg.default_symbol,
            "ticks": min(cfg.history_ticks, 25000),
        },
    )
    result = None
    error = ""
    symbol_choices = _symbols()

    if request.method == "POST" and form.is_valid():
        try:
            data, digits = _history(
                form.cleaned_data["symbol"],
                form.cleaned_data["ticks"],
            )
            result = analyze_digits(digits)
            result["pip_size"] = data["pip_size"]
            result["symbol"] = form.cleaned_data["symbol"]
        except Exception as e:
            error = str(e)

    return render(
        request,
        "research/digits.html",
        {
            "form": form,
            "result": result,
            "error": error,
            "symbol_choices": symbol_choices,
        },
    )


@login_required
def conditional_lab(request):
    cfg = RiskConfig.current()
    form = ConditionalEdgeForm(
        request.POST or None,
        initial={
            "symbol": cfg.default_symbol,
            "ticks": "25000",
            "discovery_pct": "70",
            "min_context_n": 150,
            "min_uplift_pp": 0.75,
            "fdr_q": 0.10,
            "stake": cfg.stake_usd,
            "quote_limit": "8",
        },
    )

    result = None
    error = ""

    if request.method == "POST" and form.is_valid():
        try:
            symbol = form.cleaned_data["symbol"].strip()
            ticks = int(form.cleaned_data["ticks"])
            stake = float(form.cleaned_data["stake"])
            quote_limit = int(form.cleaned_data["quote_limit"])

            data, digits = _history(symbol, ticks)

            result = run_conditional_edge_lab(
                digits,
                discovery_pct=int(form.cleaned_data["discovery_pct"]),
                min_context_n=int(form.cleaned_data["min_context_n"]),
                min_uplift_pp=float(form.cleaned_data["min_uplift_pp"]),
                fdr_q=float(form.cleaned_data["fdr_q"]),
                shrink=200,
            )
            result["symbol"] = symbol
            result["pip_size"] = data["pip_size"]

            # Percent fields are prepared in Python so the template remains
            # simple and unambiguous.
            validation_rows = []
            for row in (
                result["validated"] + result["rejected"]
            ):
                item = dict(row)
                item["p_pct"] = item["p"] * 100
                item["validation_p_pct"] = (
                    item["validation_p"] * 100
                )
                item["validation_shrunk_p_pct"] = (
                    item["validation_shrunk_p"] * 100
                )
                item["validation_lower_pct"] = (
                    item["validation_lower"] * 100
                )
                validation_rows.append(item)

            validation_rows.sort(
                key=lambda row: (
                    not row["holdout_pass"],
                    row["validation_qvalue"],
                    -row["validation_uplift_pp"],
                )
            )
            result["validation_rows"] = validation_rows[:40]

            public = DerivPublicClient()
            quoted = []

            for row in result["validated"][:quote_limit]:
                item = dict(row)
                item["p_pct"] = item["p"] * 100
                item["validation_p_pct"] = (
                    item["validation_p"] * 100
                )
                item["validation_shrunk_p_pct"] = (
                    item["validation_shrunk_p"] * 100
                )
                item["validation_lower_pct"] = (
                    item["validation_lower"] * 100
                )
                item["live_break_even_pct"] = None
                item["live_edge_pp"] = None
                item["live_lower_edge_pp"] = None
                item["live_net_return_pct"] = None
                item["live_status"] = "QUOTE ERROR"
                item["quote_error"] = ""
                item["has_live_quote"] = False

                try:
                    proposal = public.proposal(
                        symbol=symbol,
                        contract_type=item["contract_type"],
                        stake=stake,
                        barrier=item["barrier"],
                        duration=1,
                        duration_unit="t",
                    )

                    ask = float(proposal.get("ask_price") or stake)
                    payout = float(proposal.get("payout") or 0)

                    if ask <= 0 or payout <= 0:
                        raise DerivAPIError(
                            "Proposal did not contain a usable ask/payout."
                        )

                    break_even = ask / payout
                    model_edge = (
                        item["validation_shrunk_p"] - break_even
                    ) * 100
                    lower_edge = (
                        item["validation_lower"] - break_even
                    ) * 100

                    item["live_break_even_pct"] = break_even * 100
                    item["live_edge_pp"] = model_edge
                    item["live_lower_edge_pp"] = lower_edge
                    item["has_live_quote"] = True
                    item["live_net_return_pct"] = (
                        (payout - ask) / ask * 100
                    )

                    if model_edge > 0 and lower_edge > 0:
                        item["live_status"] = "RESEARCH CANDIDATE"
                    elif model_edge > 0:
                        item["live_status"] = "WATCH"
                    else:
                        item["live_status"] = "NO LIVE EDGE"

                except Exception as quote_exc:
                    item["quote_error"] = str(quote_exc)

                quoted.append(item)

            # Most convincing candidates first, while preserving quote failures.
            quoted.sort(
                key=lambda row: (
                    row["live_status"] != "RESEARCH CANDIDATE",
                    row["live_status"] != "WATCH",
                    -(
                        row["live_lower_edge_pp"]
                        if row["live_lower_edge_pp"] is not None
                        else -9999
                    ),
                )
            )

            result["quoted"] = quoted
            result["live_candidate_count"] = sum(
                1
                for row in quoted
                if row["live_status"] == "RESEARCH CANDIDATE"
            )

        except Exception as e:
            error = str(e)

    return render(
        request,
        "research/conditional.html",
        {
            "form": form,
            "result": result,
            "error": error,
        },
    )


def _default_cross_market_symbols(symbol_rows):
    """Prefer currently active Volatility 1s-style markets, then fill."""
    preferred = []
    fallback = []

    for row in symbol_rows:
        code = row["code"]
        name = str(row["name"] or "")
        if code.startswith("1HZ") and "volatility" in name.lower():
            preferred.append(code)
        else:
            fallback.append(code)

    chosen = preferred[:5]
    for code in fallback:
        if len(chosen) >= 5:
            break
        if code not in chosen:
            chosen.append(code)
    return chosen


def _historical_candidate_recurrence(symbol, current_meta):
    """Count validated candidate recurrence across recent saved v1.2 runs."""
    runs = list(
        ResearchRun.objects.filter(
            symbol=symbol,
            contract_type=RUN_TYPE,
        ).order_by("-created_at")[:12]
    )

    counts = Counter()
    meta = dict(current_meta or {})

    for run in runs:
        payload = run.results or {}
        for key in payload.get("validated_keys", []):
            counts[key] += 1
        for key, value in (payload.get("candidate_meta") or {}).items():
            meta.setdefault(key, value)

    recurrent = []
    for key, count in counts.items():
        if count < 2:
            continue
        info = meta.get(key, {})
        recurrent.append(
            {
                "key": key,
                "runs_hit": count,
                "runs_total": len(runs),
                "condition": info.get("condition", key),
                "condition_group": info.get("condition_group", ""),
                "outcome": info.get("outcome", ""),
            }
        )

    recurrent.sort(key=lambda row: (-row["runs_hit"], row["key"]))
    return len(runs), recurrent


@login_required
def cross_market_lab(request):
    cfg = RiskConfig.current()
    symbol_rows = _symbols()

    choices = [
        (row["code"], f'{row["name"]} ({row["code"]})')
        for row in symbol_rows
    ]
    name_by_code = {
        row["code"]: row["name"]
        for row in symbol_rows
    }

    defaults = _default_cross_market_symbols(symbol_rows)

    form = CrossMarketForm(
        request.POST or None,
        symbol_choices=choices,
        initial_symbols=defaults,
        initial={
            "ticks": "25000",
            "stake": cfg.stake_usd,
            "archive_quotes": True,
        },
    )

    result = None
    error = ""

    if request.method == "POST" and form.is_valid():
        selected = form.cleaned_data["symbols"]
        ticks = int(form.cleaned_data["ticks"])
        stake = float(form.cleaned_data["stake"])
        should_archive = bool(form.cleaned_data["archive_quotes"])

        market_rows = []
        current_cross_symbol = defaultdict(
            lambda: {
                "symbols": [],
                "condition": "",
                "condition_group": "",
                "outcome": "",
            }
        )
        rolling_rows = []
        quotes_archived = 0
        total_tests = 0
        total_discovery = 0
        total_validated = 0

        public = DerivPublicClient()

        for symbol in selected:
            row = {
                "symbol": symbol,
                "name": name_by_code.get(symbol, symbol),
                "ok": False,
                "error": "",
            }

            try:
                data, digits = _history(symbol, ticks)
                study = run_symbol_stability(digits)

                row.update(
                    {
                        "ok": True,
                        "ticks": study["ticks"],
                        "window_count": study["window_count"],
                        "tests_run": study["tests_run"],
                        "discovery_candidate_count": study[
                            "discovery_candidate_count"
                        ],
                        "validated_candidate_count": study[
                            "validated_candidate_count"
                        ],
                        "rolling_recurrent_count": study[
                            "rolling_recurrent_count"
                        ],
                    }
                )

                total_tests += study["tests_run"]
                total_discovery += study["discovery_candidate_count"]
                total_validated += study["validated_candidate_count"]

                compact_results = {
                    "engine_version": CROSS_MARKET_ENGINE_VERSION,
                    "frozen": FROZEN,
                    "ticks": study["ticks"],
                    "tests_run": study["tests_run"],
                    "discovery_candidate_count": study[
                        "discovery_candidate_count"
                    ],
                    "validated_candidate_count": study[
                        "validated_candidate_count"
                    ],
                    "validated_keys": study["validated_keys"],
                    "candidate_meta": study["candidate_meta"],
                    "rolling_recurrence": study["rolling_recurrence"][:15],
                    "window_count": study["window_count"],
                }

                ResearchRun.objects.create(
                    symbol=symbol,
                    contract_type=RUN_TYPE,
                    ticks=study["ticks"],
                    results=compact_results,
                )

                prior_runs, hist_recurrent = _historical_candidate_recurrence(
                    symbol,
                    study["candidate_meta"],
                )
                row["prior_runs"] = prior_runs
                row["historical_recurrent_count"] = len(hist_recurrent)
                row["historical_recurrent"] = hist_recurrent[:10]

                for candidate in study["validated"]:
                    key = candidate["candidate_key"]
                    bucket = current_cross_symbol[key]
                    bucket["symbols"].append(symbol)
                    bucket["condition"] = candidate["condition"]
                    bucket["condition_group"] = candidate[
                        "condition_group"
                    ]
                    bucket["outcome"] = candidate["outcome"]

                for candidate in study["rolling_recurrence"]:
                    if candidate["windows_hit"] < 2:
                        continue
                    rolling_rows.append(
                        {
                            **candidate,
                            "symbol": symbol,
                        }
                    )

                if should_archive:
                    for quote in quote_reference_grid(
                        public,
                        symbol,
                        stake=stake,
                    ):
                        if not quote["ok"]:
                            continue

                        ProposalSnapshot.objects.create(
                            symbol=symbol,
                            contract_type=quote["contract_type"],
                            barrier=quote["barrier"] or "",
                            stake=stake,
                            ask_price=quote["ask_price"],
                            payout=quote["payout"],
                            break_even_pct=quote["break_even_pct"],
                            model_probability_pct=0,
                            lower95_pct=0,
                            edge_pp=0,
                            sample_ticks=0,
                            decision="ARCHIVE V1.2",
                        )
                        quotes_archived += 1

            except Exception as exc:
                row["error"] = str(exc)

            market_rows.append(row)

        cross_symbol = []
        for key, bucket in current_cross_symbol.items():
            symbols = sorted(set(bucket["symbols"]))
            if len(symbols) < 2:
                continue
            cross_symbol.append(
                {
                    "key": key,
                    "condition": bucket["condition"],
                    "condition_group": bucket["condition_group"],
                    "outcome": bucket["outcome"],
                    "symbol_count": len(symbols),
                    "symbols": symbols,
                }
            )

        cross_symbol.sort(
            key=lambda row: (-row["symbol_count"], row["key"])
        )
        rolling_rows.sort(
            key=lambda row: (
                -row["windows_hit"],
                row["best_q"],
                -row["avg_uplift_pp"],
            )
        )

        result = {
            "engine_version": CROSS_MARKET_ENGINE_VERSION,
            "requested": len(selected),
            "completed": sum(1 for row in market_rows if row["ok"]),
            "failed": sum(1 for row in market_rows if not row["ok"]),
            "total_tests": total_tests,
            "total_discovery": total_discovery,
            "total_validated": total_validated,
            "cross_symbol_recurrent_count": len(cross_symbol),
            "cross_symbol": cross_symbol[:30],
            "rolling": rolling_rows[:40],
            "quotes_archived": quotes_archived,
            "markets": market_rows,
        }

    archive = ProposalSnapshot.objects.filter(
        decision="ARCHIVE V1.2"
    ).order_by("-observed_at")[:30]

    return render(
        request,
        "research/cross_market.html",
        {
            "form": form,
            "result": result,
            "error": error,
            "archive": archive,
        },
    )


@login_required
def edge_radar(request):
    cfg = RiskConfig.current()
    form = EdgeForm(
        request.POST or None,
        initial={
            "symbol": cfg.default_symbol,
            "stake": cfg.stake_usd,
            "ticks": min(cfg.history_ticks, 25000),
            "contract_type": "DIGITMATCH",
            "barrier": "7",
        },
    )
    result = None
    error = ""
    proposal = None
    history = None

    if request.method == "POST" and form.is_valid():
        try:
            symbol = form.cleaned_data["symbol"]
            ctype = form.cleaned_data["contract_type"]
            barrier = (
                form.cleaned_data.get("barrier")
                if ctype in BARRIER_TYPES
                else None
            )

            data, digits = _history(
                symbol,
                form.cleaned_data["ticks"],
            )
            history = analyze_digits(digits)
            proposal = DerivPublicClient().proposal(
                symbol=symbol,
                contract_type=ctype,
                stake=float(form.cleaned_data["stake"]),
                barrier=barrier,
            )

            ask = float(
                proposal.get("ask_price")
                or form.cleaned_data["stake"]
            )
            payout = float(proposal.get("payout") or 0)

            result = proposal_evaluation(
                digits=digits,
                contract_type=ctype,
                barrier=barrier,
                ask_price=ask,
                payout=payout,
                min_edge_pp=cfg.min_edge_pp,
                min_ticks=cfg.min_research_ticks,
                min_confidence_margin_pp=cfg.min_confidence_margin_pp,
            )

            ProposalSnapshot.objects.create(
                symbol=symbol,
                contract_type=ctype,
                barrier=barrier or "",
                stake=float(form.cleaned_data["stake"]),
                ask_price=ask,
                payout=payout,
                break_even_pct=result["break_even"] * 100,
                model_probability_pct=result["p"] * 100,
                lower95_pct=result["lower"] * 100,
                edge_pp=result["edge_pp"],
                sample_ticks=len(digits),
                decision=result["decision"],
            )

            result.update(
                {
                    "symbol": symbol,
                    "contract_type": ctype,
                    "barrier": barrier,
                    "ask_price": ask,
                    "payout": payout,
                    "proposal_id": proposal.get("id"),
                }
            )
        except Exception as e:
            error = str(e)

    return render(
        request,
        "research/radar.html",
        {
            "form": form,
            "result": result,
            "history": history,
            "proposal": proposal,
            "error": error,
            "cfg": cfg,
        },
    )


@login_required
def backtests(request):
    cfg = RiskConfig.current()
    form = BacktestForm(
        request.POST or None,
        initial={
            "symbol": cfg.default_symbol,
            "ticks": 5000,
            "assumed_return_pct": 792.9,
            "min_edge_pp": cfg.min_edge_pp,
            "contract_type": "DIGITMATCH",
            "barrier": "7",
        },
    )
    result = None
    error = ""

    if request.method == "POST" and form.is_valid():
        try:
            symbol = form.cleaned_data["symbol"]
            ctype = form.cleaned_data["contract_type"]
            barrier = (
                form.cleaned_data.get("barrier")
                if ctype in BARRIER_TYPES
                else None
            )
            data, digits = _history(
                symbol,
                form.cleaned_data["ticks"],
            )

            result = threshold_backtest(
                digits,
                ctype,
                barrier,
                float(form.cleaned_data["assumed_return_pct"]),
                warmup=min(1000, max(200, len(digits) // 4)),
                window=min(3000, len(digits)),
                min_edge_pp=float(form.cleaned_data["min_edge_pp"]),
            )

            ResearchRun.objects.create(
                symbol=symbol,
                contract_type=ctype,
                barrier=barrier or "",
                ticks=len(digits),
                assumed_return_pct=float(
                    form.cleaned_data["assumed_return_pct"]
                ),
                trades=result["trades"],
                win_rate_pct=result["win_rate_pct"],
                net_units=result["net_units"],
                expectancy_units=result["expectancy_units"],
                results=result,
            )

            result.update(
                {
                    "symbol": symbol,
                    "contract_type": ctype,
                    "barrier": barrier,
                }
            )
        except Exception as e:
            error = str(e)

    return render(
        request,
        "research/backtests.html",
        {
            "form": form,
            "result": result,
            "error": error,
            "runs": ResearchRun.objects.all()[:30],
        },
    )


def _demo_gate(cfg):
    today = timezone.localdate()
    qs = DemoTrade.objects.filter(opened_at__date=today)

    pnl = (
        qs.filter(pnl__isnull=False).aggregate(v=Sum("pnl"))["v"]
        or 0
    )

    closed = list(
        qs.filter(pnl__isnull=False)
        .order_by("-opened_at")[: cfg.max_consecutive_losses]
    )

    loss_streak = 0
    for t in closed:
        if (t.pnl or 0) < 0:
            loss_streak += 1
        else:
            break

    reasons = []
    if not settings.DERIV_DEMO_ENABLED:
        reasons.append("DERIV_DEMO_ENABLED is false")
    if not cfg.demo_execution_enabled:
        reasons.append(
            "demo execution is disabled in Risk Settings"
        )
    if qs.count() >= cfg.max_demo_trades_per_day:
        reasons.append("daily demo trade limit reached")
    if pnl <= -abs(cfg.max_daily_loss_usd):
        reasons.append("daily loss limit reached")
    if loss_streak >= cfg.max_consecutive_losses:
        reasons.append("consecutive-loss circuit breaker reached")
    if not settings.DERIV_AUTH_TOKEN or not settings.DERIV_ACCOUNT_ID:
        reasons.append("demo API credentials are not configured")

    return {
        "passes": not reasons,
        "reasons": reasons,
        "daily_pnl": float(pnl),
        "trades_today": qs.count(),
        "loss_streak": loss_streak,
    }


@login_required
def demo_lab(request):
    cfg = RiskConfig.current()
    gate = _demo_gate(cfg)
    error = ""

    if request.method == "POST":
        try:
            symbol = request.POST.get("symbol", "").strip()
            ctype = request.POST.get("contract_type", "").strip()
            barrier = (
                request.POST.get("barrier", "").strip() or None
            )
            stake = float(
                request.POST.get("stake") or cfg.stake_usd
            )

            # Fresh public evidence is mandatory immediately before
            # every demo order.
            data, digits = _history(
                symbol,
                min(max(cfg.min_demo_ticks, 1000), 25000),
            )

            prop = DerivPublicClient().proposal(
                symbol=symbol,
                contract_type=ctype,
                stake=stake,
                barrier=barrier,
            )

            ev = proposal_evaluation(
                digits=digits,
                contract_type=ctype,
                barrier=barrier,
                ask_price=float(prop.get("ask_price") or stake),
                payout=float(prop.get("payout") or 0),
                min_edge_pp=cfg.min_edge_pp,
                min_ticks=cfg.min_demo_ticks,
                min_confidence_margin_pp=cfg.min_confidence_margin_pp,
            )

            if not gate["passes"]:
                raise DerivAPIError("; ".join(gate["reasons"]))

            if not ev["demo_candidate"]:
                raise DerivAPIError(
                    "Fresh evidence failed demo gate: "
                    f"edge {ev['edge_pp']:.2f}pp, "
                    f"lower-edge {ev['lower_edge_pp']:.2f}pp"
                )

            trade = DemoTrade.objects.create(
                symbol=symbol,
                contract_type=ctype,
                barrier=barrier or "",
                stake=stake,
                status="opening",
                metadata={"pretrade_evidence": ev},
            )

            outcome = DerivDemoClient().trade_digit(
                symbol=symbol,
                contract_type=ctype,
                stake=stake,
                barrier=barrier,
                model_probability=ev["p"],
                lower_probability=ev["lower"],
                min_edge_pp=cfg.min_edge_pp,
                min_confidence_margin_pp=cfg.min_confidence_margin_pp,
            )

            buy = outcome.get("buy") or {}
            settled = outcome.get("settled") or {}

            trade.proposal_id = str(
                (outcome.get("proposal") or {}).get("id") or ""
            )
            trade.contract_id = str(
                buy.get("contract_id") or ""
            )
            trade.status = str(
                settled.get("status")
                or ("open" if buy else "failed")
            )

            if settled.get("profit") is not None:
                trade.pnl = float(settled.get("profit"))

            if trade.pnl is not None:
                trade.closed_at = timezone.now()

            trade.metadata = {
                **trade.metadata,
                "buy": buy,
                "settled": settled,
            }
            trade.save()

            messages.success(
                request,
                "Demo contract completed: "
                f"{trade.status}, P/L "
                f"{trade.pnl if trade.pnl is not None else 'pending'}",
            )
            return redirect("demo_lab")

        except Exception as e:
            error = str(e)
            messages.error(request, error)

    gate = _demo_gate(cfg)
    return render(
        request,
        "research/demo.html",
        {
            "cfg": cfg,
            "gate": gate,
            "trades": DemoTrade.objects.all()[:100],
            "error": error,
            "env_demo": settings.DERIV_DEMO_ENABLED,
        },
    )


@login_required
def settings_view(request):
    cfg = RiskConfig.current()
    form = RiskConfigForm(
        request.POST or None,
        instance=cfg,
    )

    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Risk settings saved.")
        return redirect("settings")

    return render(
        request,
        "research/settings.html",
        {
            "form": form,
            "cfg": cfg,
            "demo_env": settings.DERIV_DEMO_ENABLED,
            "real_enabled": settings.DERIV_REAL_ENABLED,
        },
    )


def health(request):
    return JsonResponse(
        {
            "ok": True,
            "service": "Deriv Insight",
            "real_trading_enabled": False,
        }
    )
