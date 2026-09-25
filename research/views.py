from __future__ import annotations

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
