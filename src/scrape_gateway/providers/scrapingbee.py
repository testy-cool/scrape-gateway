from __future__ import annotations

import os
import time

import httpx

from ..errors import classify_failure
from ..models import FailureReason, ScrapeRequest, ScrapeResult
from ..provider import ProviderAdapter


class ScrapingBeeProvider(ProviderAdapter):
    name = "scrapingbee"
    cost_rank = 35
    capabilities = frozenset({"html", "country", "render_js", "premium"})

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("SCRAPINGBEE_API_KEY")

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        if not self.api_key:
            return ScrapeResult(request.url, self.name, False, error="Missing SCRAPINGBEE_API_KEY")
        params: dict[str, str] = {
            "api_key": self.api_key,
            "url": request.url,
            "render_js": "true" if request.render_js else "false",
        }
        if request.country:
            params["country_code"] = request.country.lower()
        if request.premium:
            params["premium_proxy"] = "true"
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=request.timeout_seconds, follow_redirects=True
            ) as client:
                response = await client.get("https://app.scrapingbee.com/api/v1/", params=params)
            failure = classify_failure(response.status_code, response.text)
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=response.is_success and failure is None,
                status_code=response.status_code,
                html=response.text,
                failure_reason=failure,
                cost_units=25
                if request.premium and request.render_js
                else 10
                if request.premium
                else 1,
                latency_ms=int((time.perf_counter() - start) * 1000),
                route="scrapingbee:premium" if request.premium else "scrapingbee",
            )
        except httpx.TimeoutException as exc:
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=False,
                error=str(exc),
                failure_reason=FailureReason.TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=False,
                error=str(exc),
                failure_reason=FailureReason.PROVIDER_ERROR,
            )
