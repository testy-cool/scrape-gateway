from __future__ import annotations

import os
import time

import httpx

from ..errors import classify_failure
from ..models import FailureReason, ScrapeRequest, ScrapeResult
from ..provider import ProviderAdapter


class ScraperApiProvider(ProviderAdapter):
    name = "scraperapi"
    cost_rank = 40
    capabilities = frozenset({"html", "country", "render_js", "premium", "screenshot"})

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("SCRAPERAPI_API_KEY")

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        if not self.api_key:
            return ScrapeResult(request.url, self.name, False, error="Missing SCRAPERAPI_API_KEY")
        params: dict[str, str] = {"api_key": self.api_key, "url": request.url}
        if request.country:
            params["country_code"] = request.country.lower()
        if request.render_js:
            params["render"] = "true"
        if request.premium:
            params["premium"] = "true"
        if request.screenshot:
            params["screenshot"] = "true"
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=request.timeout_seconds, follow_redirects=True
            ) as client:
                response = await client.get("https://api.scraperapi.com/", params=params)
            is_screenshot = request.screenshot and response.headers.get(
                "content-type", ""
            ).startswith("image/")
            body = None if is_screenshot else response.text
            failure = None if is_screenshot else classify_failure(response.status_code, body)
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=response.is_success and failure is None,
                status_code=response.status_code,
                html=body,
                screenshot=response.content if is_screenshot else None,
                failure_reason=failure,
                cost_units=(
                    25
                    if request.premium and request.render_js
                    else 10
                    if request.premium or request.render_js
                    else 1
                ),
                latency_ms=int((time.perf_counter() - start) * 1000),
                route="scraperapi:premium" if request.premium else "scraperapi",
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
