from __future__ import annotations

import math
import os
import time

import httpx

from ..errors import classify_provider_failure
from ..models import AttemptLedgerEntry, FailureReason, ScrapeRequest, ScrapeResult
from ..provider import ProviderAdapter, caller_headers


def _reported_cost(value: object, fallback: float) -> tuple[float, str]:
    if isinstance(value, bool):
        return fallback, "estimated"
    try:
        cost = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback, "estimated"
    if not math.isfinite(cost) or cost < 0:
        return fallback, "estimated"
    return cost, "exact"


def _page_status(value: object, fallback: int) -> int:
    if isinstance(value, bool):
        return fallback
    try:
        status = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return fallback
    return status if 100 <= status <= 599 else fallback


class ScrapingAntProvider(ProviderAdapter):
    name = "scrapingant"
    cost_rank = 33
    capabilities = frozenset({"html", "country", "render_js", "premium"})
    required_configuration = (("api_key", "SCRAPINGANT_API_KEY"),)

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.scrapingant.com/v2/general",
    ) -> None:
        self.api_key = api_key or os.getenv("SCRAPINGANT_API_KEY")
        self.base_url = base_url

    def estimated_cost_units(self, request: ScrapeRequest) -> float:
        if request.premium and request.render_js:
            return 125.0
        if request.premium:
            return 25.0
        return 10.0 if request.render_js else 1.0

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
            "x-api-key": self.api_key,
            "url": request.url,
            "browser": str(request.render_js).lower(),
            "proxy_type": "residential" if request.premium else "datacenter",
            "timeout": str(max(5, min(60, math.ceil(request.timeout_seconds)))),
        }
        if request.country:
            params["proxy_country"] = request.country.upper()
        if request.render_js and request.wait_selector:
            params["wait_for_selector"] = request.wait_selector

        headers = {f"ant-{name}": value for name, value in caller_headers(request.headers).items()}
        if request.referer:
            headers.setdefault("ant-Referer", request.referer)

        route = "scrapingant:residential" if request.premium else "scrapingant"
        estimated_cost = self.estimated_cost_units(request)
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=request.timeout_seconds, follow_redirects=True
            ) as client:
                response = await client.get(self.base_url, params=params, headers=headers)

            latency_ms = int((time.perf_counter() - start) * 1000)
            status_code = (
                _page_status(
                    response.headers.get("ant-page-status-code"),
                    response.status_code,
                )
                if response.is_success
                else response.status_code
            )
            cost_units, cost_provenance = _reported_cost(
                response.headers.get("ant-credits-cost"),
                estimated_cost,
            )
            if response.status_code == 409:
                failure = FailureReason.HTTP_429
            else:
                failure = classify_provider_failure(status_code, response.text)
            if not response.is_success and failure is None:
                failure = FailureReason.PROVIDER_ERROR
            success = response.is_success and failure is None
            metadata = {
                "provider_status_code": response.status_code,
                "cost_provenance": cost_provenance,
            }
            if response.is_success:
                metadata["page_status_code"] = status_code

            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=success,
                status_code=status_code,
                html=response.text,
                failure_reason=failure,
                cost_units=cost_units,
                latency_ms=latency_ms,
                route=route,
                metadata=metadata,
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
        except httpx.TimeoutException as exc:
            return ScrapeResult(
                request.url,
                self.name,
                False,
                error=str(exc),
                failure_reason=FailureReason.TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001
            return ScrapeResult(
                request.url,
                self.name,
                False,
                error=str(exc),
                failure_reason=FailureReason.PROVIDER_ERROR,
            )
