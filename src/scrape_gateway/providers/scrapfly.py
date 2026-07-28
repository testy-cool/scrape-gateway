from __future__ import annotations

import math
import os
import time

import httpx

from ..errors import classify_provider_failure
from ..models import AttemptLedgerEntry, FailureReason, ScrapeRequest, ScrapeResult
from ..provider import ProviderAdapter


def _valid_cost(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        cost = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(cost) or cost < 0:
        return None
    return cost


def _reported_cost(header_cost: object, context_cost: object) -> tuple[float, str]:
    for candidate in (header_cost, context_cost):
        cost = _valid_cost(candidate)
        if cost is not None:
            return cost, "exact"
    return 1, "estimated"


def _status_code(value: object, fallback: int) -> int:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
        return fallback
    try:
        status = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return fallback
    return status if 100 <= status <= 599 else fallback


class ScrapflyProvider(ProviderAdapter):
    name = "scrapfly"
    cost_rank = 32
    capabilities = frozenset({"html", "country", "render_js", "premium"})
    required_configuration = (("api_key", "SCRAPFLY_API_KEY"),)

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.scrapfly.io/scrape",
        cost_budget: int = 25,
    ) -> None:
        self.api_key = api_key or os.getenv("SCRAPFLY_API_KEY")
        self.base_url = base_url
        self.cost_budget = cost_budget

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        if error := self.availability_error():
            return ScrapeResult(
                request.url,
                self.name,
                False,
                error=error,
                failure_reason=FailureReason.PROVIDER_UNAVAILABLE,
            )
        params: dict[str, str] = {
            "key": self.api_key,
            "url": request.url,
            "format": "raw",
            "render_js": str(request.render_js).lower(),
        }
        if request.country:
            params["country"] = request.country.lower()
        if request.premium:
            params["asp"] = "true"
            params["cost_budget"] = str(self.cost_budget)
        if request.wait_selector:
            params["wait_for_selector"] = request.wait_selector
        if request.extra_wait_ms:
            params["rendering_wait"] = str(request.extra_wait_ms)
        session = request.metadata.get("session")
        if isinstance(session, str) and session:
            params["session"] = session

        route = "scrapfly:asp" if request.premium else "scrapfly"
        start = time.perf_counter()
        response_received = False
        status_code: int | None = None
        cost_units = 0.0
        cost_provenance = "estimated"
        context: dict = {}
        try:
            async with httpx.AsyncClient(
                timeout=request.timeout_seconds, follow_redirects=True
            ) as client:
                response = await client.get(self.base_url, params=params)
                response_received = True
                status_code = response.status_code
                try:
                    data = response.json()
                except ValueError:
                    data = {}
                raw_context = data.get("context", {}) if isinstance(data, dict) else {}
                context = raw_context if isinstance(raw_context, dict) else {}
                cost_units, cost_provenance = _reported_cost(
                    response.headers.get("x-scrapfly-api-cost"),
                    context.get("cost"),
                )
                raw_result = data.get("result", {}) if isinstance(data, dict) else {}
                result_data = raw_result if isinstance(raw_result, dict) else {}
                if response.is_success:
                    status_code = _status_code(
                        result_data.get("status_code"),
                        response.status_code,
                    )
                html = result_data.get("content", "")
                if not isinstance(html, str):
                    html = ""
                if response.is_success and result_data.get("format") == "clob":
                    large_response = await client.get(html, params={"key": self.api_key})
                    if not large_response.is_success:
                        status_code = large_response.status_code
                    large_response.raise_for_status()
                    html = large_response.text
            failure = classify_provider_failure(
                status_code,
                html if response.is_success else response.text,
            )
            success = response.is_success and failure is None
            latency_ms = int((time.perf_counter() - start) * 1000)
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=success,
                status_code=status_code,
                html=html,
                failure_reason=failure,
                cost_units=cost_units,
                latency_ms=latency_ms,
                route=route,
                metadata={"context": context},
                attempt_ledger=[
                    AttemptLedgerEntry(
                        provider=self.name,
                        route=route,
                        cost_units=cost_units,
                        cost_provenance=cost_provenance,
                        success=success,
                        latency_ms=latency_ms,
                        status_code=status_code,
                        failure_reason=failure,
                        block_type=None,
                    )
                ],
            )
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, httpx.TimeoutException):
                failure_reason = FailureReason.TIMEOUT
            elif isinstance(exc, httpx.HTTPStatusError):
                status_code = exc.response.status_code
                failure_reason = (
                    classify_provider_failure(status_code, exc.response.text)
                    or FailureReason.PROVIDER_ERROR
                )
            else:
                failure_reason = FailureReason.PROVIDER_ERROR
            latency_ms = int((time.perf_counter() - start) * 1000)
            result = ScrapeResult(
                url=request.url,
                provider=self.name,
                success=False,
                status_code=status_code,
                error=str(exc),
                failure_reason=failure_reason,
                cost_units=cost_units,
                latency_ms=latency_ms,
                route=route if response_received else None,
                metadata={"context": context} if response_received else {},
            )
            if response_received:
                result.attempt_ledger.append(
                    AttemptLedgerEntry(
                        provider=self.name,
                        route=route,
                        cost_units=cost_units,
                        cost_provenance=cost_provenance,
                        success=False,
                        latency_ms=latency_ms,
                        status_code=status_code,
                        failure_reason=failure_reason,
                        block_type=None,
                    )
                )
            return result
