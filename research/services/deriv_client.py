from __future__ import annotations

import json
import requests
from websocket import create_connection
from django.conf import settings


class DerivAPIError(RuntimeError):
    pass


class DerivPublicClient:
    def __init__(self, timeout=15):
        self.url = settings.DERIV_PUBLIC_WS
        self.timeout = timeout

    def _call(self, payload: dict):
        ws = create_connection(self.url, timeout=self.timeout)
        try:
            ws.send(json.dumps(payload))
            while True:
                raw = ws.recv()
                data = json.loads(raw)
                if data.get("error"):
                    raise DerivAPIError(str(data["error"]))
                return data
        finally:
            try:
                ws.close()
            except Exception:
                pass

    def active_symbols(self):
        data = self._call({"active_symbols": "brief"})
        return data.get("active_symbols", [])

    def contracts_for(self, symbol):
        data = self._call({"contracts_for": symbol})
        return data.get("contracts_for", {}).get("available", [])

    def ticks_history(self, symbol, count=5000):
        """Fetch recent ticks in backward pages and return chronological data.

        Important compatibility note:
        Deriv's public Options WebSocket currently rejects `subscribe: 0` on
        some deployments even though newer API documentation describes 0 as a
        valid value. For a one-time history request, `subscribe` is optional,
        so we omit it entirely. This works with both legacy-style and newer
        validation rules.

        Each request is kept at <=5,000 ticks and older pages are requested by
        moving the `end` epoch backwards. The final result is chronological.
        """
        target = max(100, min(int(count), 25000))
        remaining = target
        end = "latest"
        pages = []
        pip_size = 2
        seen_first = None

        while remaining > 0:
            ask = min(5000, remaining)
            payload = {
                "ticks_history": symbol,
                "count": ask,
                "end": end,
                "style": "ticks",
            }

            data = self._call(payload)
            hist = data.get("history") or {}
            prices = list(hist.get("prices", []))
            times = list(hist.get("times", []))

            response_pip_size = data.get("pip_size")
            if response_pip_size is not None:
                try:
                    pip_size = int(response_pip_size)
                except (TypeError, ValueError):
                    pass

            if not prices or not times:
                break

            first = int(times[0])
            if seen_first is not None and first == seen_first:
                break

            seen_first = first
            pages.append((prices, times))
            remaining -= len(prices)

            if len(prices) < ask:
                break

            end = first - 1

        all_prices = []
        all_times = []
        for prices, times in reversed(pages):
            all_prices.extend(prices)
            all_times.extend(times)

        if len(all_prices) > target:
            all_prices = all_prices[-target:]
            all_times = all_times[-target:]

        return {
            "prices": all_prices,
            "times": all_times,
            "pip_size": pip_size,
        }

    def proposal(
        self,
        *,
        symbol,
        contract_type,
        stake=1.0,
        barrier=None,
        duration=1,
        duration_unit="t",
        currency="USD",
    ):
        """Request a one-time public contract proposal.

        `subscribe` is deliberately omitted. We only need a single quote here,
        and omitting the optional field avoids the same cross-version enum
        validation problem seen on ticks_history.
        """
        req = {
            "proposal": 1,
            "amount": float(stake),
            "basis": "stake",
            "contract_type": contract_type,
            "currency": currency,
            "duration": int(duration),
            "duration_unit": duration_unit,
            "underlying_symbol": symbol,
        }

        if barrier not in (None, ""):
            req["barrier"] = str(barrier)

        data = self._call(req)
        proposal = data.get("proposal") or {}
        if not proposal.get("id"):
            raise DerivAPIError("Proposal response did not contain an id")
        return proposal


class DerivDemoClient:
    def __init__(self, timeout=15):
        self.timeout = timeout

    def _authenticated_ws_url(self):
        if not settings.DERIV_AUTH_TOKEN or not settings.DERIV_ACCOUNT_ID:
            raise DerivAPIError(
                "DERIV_AUTH_TOKEN and DERIV_ACCOUNT_ID are required for demo trading"
            )

        headers = {"Authorization": f"Bearer {settings.DERIV_AUTH_TOKEN}"}
        if settings.DERIV_APP_ID:
            headers["Deriv-App-ID"] = settings.DERIV_APP_ID

        url = (
            f"{settings.DERIV_REST_BASE.rstrip('/')}"
            f"/trading/v1/options/accounts/{settings.DERIV_ACCOUNT_ID}/otp"
        )

        response = requests.post(url, headers=headers, timeout=self.timeout)
        try:
            data = response.json()
        except Exception:
            data = {}

        if not response.ok:
            raise DerivAPIError(
                f"OTP request failed HTTP {response.status_code}: {data}"
            )

        ws_url = (data.get("data") or {}).get("url")
        if not ws_url:
            raise DerivAPIError("OTP response did not include WebSocket URL")

        if "/real?" in ws_url:
            raise DerivAPIError("Real-money WebSocket refused by Deriv Insight v1")

        return ws_url

    def trade_digit(
        self,
        *,
        symbol,
        contract_type,
        stake=1.0,
        barrier=None,
        duration=1,
        duration_unit="t",
        currency="USD",
        settle_timeout=20,
        model_probability=None,
        lower_probability=None,
        min_edge_pp=0.0,
        min_confidence_margin_pp=0.0,
    ):
        if not settings.DERIV_DEMO_ENABLED:
            raise DerivAPIError("DERIV_DEMO_ENABLED is false")

        ws = create_connection(self._authenticated_ws_url(), timeout=self.timeout)
        try:
            # One-time authenticated proposal. Do not send subscribe: 0; omit
            # the optional field for compatibility with current live validation.
            req = {
                "proposal": 1,
                "amount": float(stake),
                "basis": "stake",
                "contract_type": contract_type,
                "currency": currency,
                "duration": int(duration),
                "duration_unit": duration_unit,
                "underlying_symbol": symbol,
            }

            if barrier not in (None, ""):
                req["barrier"] = str(barrier)

            ws.send(json.dumps(req))
            proposal_msg = json.loads(ws.recv())
            if proposal_msg.get("error"):
                raise DerivAPIError(str(proposal_msg["error"]))

            proposal = proposal_msg.get("proposal") or {}
            proposal_id = proposal.get("id")

            try:
                ask = float(proposal.get("ask_price") or stake)
            except (TypeError, ValueError):
                ask = float(stake)

            try:
                payout = float(proposal.get("payout") or 0)
            except (TypeError, ValueError):
                payout = 0.0

            if not proposal_id:
                raise DerivAPIError("Authenticated proposal missing id")
            if payout <= 0:
                raise DerivAPIError("Authenticated proposal missing payout")

            break_even = ask / payout

            if model_probability is not None:
                model_edge_pp = (float(model_probability) - break_even) * 100
                if model_edge_pp < float(min_edge_pp):
                    raise DerivAPIError(
                        "Authenticated demo quote no longer meets minimum model edge"
                    )

            if lower_probability is not None:
                confidence_edge_pp = (float(lower_probability) - break_even) * 100
                if confidence_edge_pp < float(min_confidence_margin_pp):
                    raise DerivAPIError(
                        "Authenticated demo quote no longer clears confidence gate"
                    )

            ws.send(json.dumps({"buy": proposal_id, "price": ask}))
            buy_msg = json.loads(ws.recv())
            if buy_msg.get("error"):
                raise DerivAPIError(str(buy_msg["error"]))

            buy = buy_msg.get("buy") or {}
            contract_id = buy.get("contract_id")
            if not contract_id:
                return {
                    "proposal": proposal,
                    "buy": buy,
                    "settled": None,
                }

            # This one genuinely needs a subscription because we must keep
            # receiving contract updates until the demo contract settles.
            ws.settimeout(settle_timeout)
            ws.send(
                json.dumps(
                    {
                        "proposal_open_contract": 1,
                        "contract_id": contract_id,
                        "subscribe": 1,
                    }
                )
            )

            settled = None
            while True:
                msg = json.loads(ws.recv())
                if msg.get("error"):
                    raise DerivAPIError(str(msg["error"]))
                if msg.get("msg_type") != "proposal_open_contract":
                    continue

                contract = msg.get("proposal_open_contract") or {}
                status = str(contract.get("status", "")).lower()
                if contract.get("is_sold") or status in {"won", "lost", "sold"}:
                    settled = contract
                    break

            return {
                "proposal": proposal,
                "buy": buy,
                "settled": settled,
            }
        finally:
            try:
                ws.close()
            except Exception:
                pass
