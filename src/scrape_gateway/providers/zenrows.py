from __future__ import annotations

import os
import time

import httpx

from ..errors import classify_failure
from ..models import FailureReason, ScrapeRequest, ScrapeResult
from ..provider import ProviderAdapter


class ZenRowsProvider(ProviderAdapter):
    name = "zenrows"
    cost_rank = 34
    capabilities = frozenset({"html", "country", "render_js", "premium"})

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.zenrows.com/v1/",
    ) -> None:
        self.api_key = api_key or os.getenv("ZENROWS_API_KEY")
        self.base_url = base_url

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        if not self.api_key:
            return ScrapeResult(request.url, self.name, False, error="Missing ZENROWS_API_KEY")
        params: dict[str, str] = {
            "apikey": self.api_key,
            "url": request.url,
            "original_status": "true",
        }
        if request.render_js:
            params["js_render"] = "true"
        if request.premium:
            params["premium_proxy"] = "true"
        if request.country:
            params["premium_proxy"] = "true"
            params["proxy_country"] = request.country.lower()
        if request.extra_wait_ms:
            params["wait"] = str(request.extra_wait_ms)

        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=request.timeout_seconds, follow_redirects=True
            ) as client:
                response = await client.get(self.base_url, params=params)
            failure = classify_failure(response.status_code, response.text)
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=response.is_success and failure is None,
                status_code=response.status_code,
                html=response.text,
                failure_reason=failure,
                cost_units=25
                if request.render_js and (request.premium or request.country)
                else 10
                if request.premium or request.country
                else 5
                if request.render_js
                else 1,
                latency_ms=int((time.perf_counter() - start) * 1000),
                route="zenrows:premium" if request.premium or request.country else "zenrows",
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
