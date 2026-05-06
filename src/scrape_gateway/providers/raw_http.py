from __future__ import annotations

import os
import time

import httpx

from ..errors import classify_failure
from ..models import FailureReason, ScrapeRequest, ScrapeResult
from ..provider import ProviderAdapter


class RawHttpProvider(ProviderAdapter):
    name = "raw_http"
    cost_rank = 0
    capabilities = frozenset({"html"})

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        start = time.perf_counter()
        proxy_url = os.getenv("SCRAPE_PROXY_URL")
        try:
            async with httpx.AsyncClient(
                timeout=request.timeout_seconds,
                follow_redirects=True,
                proxy=proxy_url,
            ) as client:
                response = await client.get(request.url, headers=request.headers)
            failure = classify_failure(response.status_code, response.text)
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=response.is_success and failure is None,
                status_code=response.status_code,
                html=response.text,
                failure_reason=failure,
                latency_ms=int((time.perf_counter() - start) * 1000),
                route="raw_http",
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
                failure_reason=FailureReason.UNKNOWN,
            )
