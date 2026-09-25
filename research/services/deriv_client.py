from __future__ import annotations

import json
import random
import re
import time

import requests
from websocket import (
    WebSocketBadStatusException,
    WebSocketTimeoutException,
    create_connection,
)
from django.conf import settings


class DerivAPIError(RuntimeError):
    pass


class DerivRateLimitError(DerivAPIError):
    pass


class DerivPublicClient:
    HISTORY_PAGE_SIZE = 1000

    def __init__(
        self,
        timeout=20,
        max_retries=5,
        base_backoff=2.0,
        page_delay=0.32,
        request_delay=0.18,
    ):
        self.url = settings.DERIV_PUBLIC_WS
        self.timeout = timeout
        self.max_retries = max(1, int(max_retries))
        self.base_backoff = max(0.5, float(base_backoff))
        self.page_delay = max(0.0, float(page_delay))
        self.request_delay = max(0.0, float(request_delay))

    @staticmethod
    def _is_rate_limit_exception(exc):
        status = getattr(exc, "status_code", None)
        text = str(exc).lower()
        return status == 429 or "429" in text or "ratelimit" in text or "rate limit" in text

    @staticmethod
    def _retry_after_from_exception(exc):
        headers = (
            getattr(exc, "resp_headers", None)
            or getattr(exc, "headers", None)
            or {}
        )
        if hasattr(headers, "get"):
            value = headers.get("retry-after") or headers.get("Retry-After")
            if value is not None:
                try:
                    return max(0.0, float(value))
                except (TypeError, ValueError):
                    pass

        # websocket-client sometimes exposes the headers only inside the
        # exception string. Keep this fallback deliberately conservative.
        match = re.search(r"retry-after['\"]?\s*[:=]\s*['\"]?([0-9.]+)", str(exc), re.I)
        if match:
            try:
                return max(0.0, float(match.group(1)))
            except (TypeError, ValueError):
                pass
        return None

    @staticmethod
    def _retry_after_from_message(data):
        error = data.get("error") or {}
        details = error.get("details") or {}
        candidates = [
            error.get("retry_after"),
            details.get("retry_after"),
            data.get("retry_after"),
        ]
        for value in candidates:
            if value is not None:
                try:
                    return max(0.0, float(value))
                except (TypeError, ValueError):
                    continue
        return None

    def _backoff_seconds(self, attempt, retry_after=None):
        if retry_after is not None:
            return max(float(retry_after), 0.5)
        # Small jitter stops several simultaneous workers reconnecting at the
        # exact same instant.
        return min(
            self.base_backoff * (2 ** max(0, attempt - 1)) + random.uniform(0, 0.35),
            30.0,
        )

    def _connect(self):
        last_exc = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return create_connection(self.url, timeout=self.timeout)
            except WebSocketBadStatusException as exc:
                last_exc = exc
                if not self._is_rate_limit_exception(exc):
                    raise
                wait = self._backoff_seconds(
                    attempt,
                    self._retry_after_from_exception(exc),
                )
                if attempt >= self.max_retries:
                    break
                time.sleep(wait)
            except Exception as exc:
                last_exc = exc
                if not self._is_rate_limit_exception(exc):
                    raise
                wait = self._backoff_seconds(
                    attempt,
                    self._retry_after_from_exception(exc),
                )
                if attempt >= self.max_retries:
                    break
                time.sleep(wait)

        raise DerivRateLimitError(
            f"Deriv WebSocket rate limit persisted after "
            f"{self.max_retries} connection attempts: {last_exc}"
        )

    def _receive_one(self, ws):
        raw = ws.recv()
        data = json.loads(raw)
        if data.get("error"):
            error = data["error"]
            code = str(error.get("code") or "").lower()
            message = str(error.get("message") or "")
            if "ratelimit" in code or "rate limit" in message.lower():
                raise DerivRateLimitError(
                    json.dumps(
                        {
                            "error": error,
                            "retry_after": self._retry_after_from_message(data),
                        }
                    )
                )
            raise DerivAPIError(str(error))
        return data

    def _call(self, payload: dict):
        """One public request with bounded retry/backoff."""
        last_exc = None

        for attempt in range(1, self.max_retries + 1):
            ws = None
            try:
                ws = self._connect()
                if self.request_delay:
                    time.sleep(self.request_delay)
                ws.send(json.dumps(payload))
                return self._receive_one(ws)
            except DerivRateLimitError as exc:
                last_exc = exc
                retry_after = None
                try:
                    parsed = json.loads(str(exc))
                    retry_after = parsed.get("retry_after")
                except Exception:
                    pass

                if attempt >= self.max_retries:
                    break
                time.sleep(self._backoff_seconds(attempt, retry_after))
            except (WebSocketTimeoutException, ConnectionError, OSError) as exc:
                last_exc = exc
                if attempt >= self.max_retries:
                    break
                time.sleep(self._backoff_seconds(attempt))
            finally:
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass

        raise DerivAPIError(
            f"Deriv public request failed after {self.max_retries} attempts: {last_exc}"
        )

    def active_symbols(self):
        data = self._call({"active_symbols": "brief"})
        return data.get("active_symbols", [])

    def contracts_for(self, symbol):
        data = self._call({"contracts_for": symbol})
        return data.get("contracts_for", {}).get("available", [])

    def ticks_history(self, symbol, count=5000):
        """Fetch recent ticks with pacing and resumable page retries.

        The public endpoint commonly returns up to 1,000 historical ticks per
        response. v1.2.1 keeps a WebSocket open for the whole symbol whenever
        possible, paces page requests, and reconnects/retries the *same page*
        when a transient transport or rate-limit error occurs.

        Results are returned in chronological order.
        """
        target = max(100, min(int(count), 25000))
        remaining = target
        end = "latest"
        pages = []
        pip_size = 2
        seen_boundaries = set()
        ws = None

        try:
            while remaining > 0:
                ask = min(self.HISTORY_PAGE_SIZE, remaining)
                payload = {
                    "ticks_history": symbol,
                    "count": ask,
                    "end": end,
                    "style": "ticks",
                }

                page_data = None
                last_exc = None

                for attempt in range(1, self.max_retries + 1):
                    try:
                        if ws is None:
                            ws = self._connect()

                        if self.page_delay:
                            time.sleep(self.page_delay)

                        ws.send(json.dumps(payload))
                        page_data = self._receive_one(ws)
                        break

                    except DerivRateLimitError as exc:
                        last_exc = exc
                        retry_after = None
                        try:
                            parsed = json.loads(str(exc))
                            retry_after = parsed.get("retry_after")
                        except Exception:
                            pass

                        try:
                            ws.close()
                        except Exception:
                            pass
                        ws = None

                        if attempt >= self.max_retries:
                            break
                        time.sleep(self._backoff_seconds(attempt, retry_after))

                    except (
                        WebSocketBadStatusException,
                        WebSocketTimeoutException,
                        ConnectionError,
                        OSError,
                    ) as exc:
                        last_exc = exc
                        retry_after = (
                            self._retry_after_from_exception(exc)
                            if self._is_rate_limit_exception(exc)
                            else None
                        )

                        try:
                            if ws is not None:
                                ws.close()
                        except Exception:
                            pass
                        ws = None

                        if attempt >= self.max_retries:
                            break
                        time.sleep(self._backoff_seconds(attempt, retry_after))

                if page_data is None:
                    raise DerivAPIError(
                        f"History page failed for {symbol} after "
                        f"{self.max_retries} attempts: {last_exc}"
                    )

                hist = page_data.get("history") or {}
                prices = list(hist.get("prices", []))
                times = list(hist.get("times", []))

                response_pip_size = page_data.get("pip_size")
                if response_pip_size is not None:
                    try:
                        pip_size = int(response_pip_size)
                    except (TypeError, ValueError):
                        pass

                if not prices or not times:
                    break

                usable = min(len(prices), len(times), remaining)
                prices = prices[-usable:]
                times = times[-usable:]

                first_epoch = int(times[0])
                last_epoch = int(times[-1])
                boundary = (first_epoch, last_epoch, len(times))

                if boundary in seen_boundaries:
                    break
                seen_boundaries.add(boundary)

                pages.append((prices, times))
                remaining -= usable

                if remaining <= 0:
                    break
                if usable < ask:
                    break

                # Move strictly behind the oldest tick already stored. If the
                # next page needs a reconnect, this boundary lets us resume
                # exactly where we stopped.
                end = first_epoch - 1

        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass

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
            "requested_count": target,
            "returned_count": len(all_prices),
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
            ask = float(proposal.get("ask_price") or stake)
            payout = float(proposal.get("payout") or 0)

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
                return {"proposal": proposal, "buy": buy, "settled": None}

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
