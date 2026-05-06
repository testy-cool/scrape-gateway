from __future__ import annotations

import os
import time

import httpx

from ..errors import classify_failure
from ..models import FailureReason, ScrapeRequest, ScrapeResult
from ..provider import ProviderAdapter


class ScrapeDoProvider(ProviderAdapter):
    name = "scrape_do"
    cost_rank = 30
    capabilities = frozenset({"html", "country", "render_js", "premium"})

    def __init__(self, token: str | None = None) -> None:
        self.token = token or os.getenv("SCRAPE_DO_TOKEN")

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        if not self.token:
            return ScrapeResult(request.url, self.name, False, error="Missing SCRAPE_DO_TOKEN")
        params: dict[str, str] = {"token": self.token, "url": request.url}
        if request.country:
            params["geoCode"] = request.country.lower()
        if request.premium:
            params["super"] = "true"
        if request.render_js:
            params["render"] = "true"
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=request.timeout_seconds, follow_redirects=True
            ) as client:
                response = await client.get("https://api.scrape.do/", params=params)
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
                route="scrape_do:super" if request.premium else "scrape_do",
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
