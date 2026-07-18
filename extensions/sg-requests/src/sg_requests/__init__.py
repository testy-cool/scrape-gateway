from __future__ import annotations

import asyncio
import time

from scrape_gateway import FailureReason, ProviderAdapter, ScrapeRequest, ScrapeResult
from scrape_gateway.errors import classify_exception, classify_failure


class RequestsProvider(ProviderAdapter):
    name = "requests"
    cost_rank = 1
    capabilities = frozenset({"html"})

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        start = time.perf_counter()
        try:
            import requests

            response = await asyncio.to_thread(
                requests.get,
                request.url,
                headers=request.headers or None,
                timeout=request.timeout_seconds,
                allow_redirects=True,
            )
            status = int(response.status_code)
            html = response.text
            failure = classify_failure(status, html)
            return ScrapeResult(
                request.url,
                self.name,
                200 <= status < 400 and failure is None,
                status_code=status,
                html=html,
                failure_reason=failure,
                latency_ms=int((time.perf_counter() - start) * 1000),
                route="requests:http",
                metadata={"final_url": str(response.url)},
            )
        except Exception as exc:  # noqa: BLE001
            reason = classify_exception(exc)
            if reason == FailureReason.UNKNOWN:
                reason = FailureReason.PROVIDER_ERROR
            return ScrapeResult(
                request.url,
                self.name,
                False,
                error=str(exc),
                failure_reason=reason,
                latency_ms=int((time.perf_counter() - start) * 1000),
            )
