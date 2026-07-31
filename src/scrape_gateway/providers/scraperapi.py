from __future__ import annotations

import os
import time

import httpx

from ..errors import classify_provider_failure
from ..models import FailureReason, ScrapeRequest, ScrapeResult
from ..provider import ProviderAdapter


class ScraperApiProvider(ProviderAdapter):
    name = "scraperapi"
    cost_rank = 40
    capabilities = frozenset({"html", "country", "render_js", "premium", "screenshot"})
    required_configuration = (("api_key", "SCRAPERAPI_API_KEY"),)

    def estimated_cost_units(self, request: ScrapeRequest) -> float:
        if request.premium and request.render_js:
            return 25.0
        return 10.0 if request.premium or request.render_js else 1.0

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("SCRAPERAPI_API_KEY")

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        if error := self.availability_error():
            return ScrapeResult(
                request.url,
                self.name,
                False,
                error=error,
                failure_reason=FailureReason.PROVIDER_UNAVAILABLE,
            )
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
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            is_screenshot = request.screenshot and content_type.startswith("image/")
            body = None if is_screenshot else response.text
            failure = (
                None if is_screenshot else classify_provider_failure(response.status_code, body)
            )
            screenshot_error = request.screenshot and response.is_success and not is_screenshot
            if screenshot_error:
                failure = FailureReason.PROVIDER_ERROR
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=response.is_success and failure is None,
                status_code=response.status_code,
                html=body,
                screenshot=response.content if is_screenshot else None,
                failure_reason=failure,
                error=(
                    f"Screenshot was requested but ScraperAPI returned "
                    f"{content_type or 'an unknown content type'}"
                    if screenshot_error
                    else None
                ),
                cost_units=self.estimated_cost_units(request),
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
