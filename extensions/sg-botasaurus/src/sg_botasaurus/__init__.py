from __future__ import annotations

import asyncio
import time

from scrape_gateway import FailureReason, ProviderAdapter, ScrapeRequest, ScrapeResult
from scrape_gateway.errors import classify_exception, classify_failure


class BotasaurusProvider(ProviderAdapter):
    name = "botosaurus"
    cost_rank = 9
    capabilities = frozenset({"html", "premium"})

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        start = time.perf_counter()
        try:
            from botasaurus.request import Request

            response = await asyncio.to_thread(
                Request().get,
                request.url,
                headers=request.headers or None,
                timeout=request.timeout_seconds,
                allow_redirects=True,
                referer=request.referer
                if request.referer is not None
                else "https://www.google.com/",
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
                route="botosaurus:request",
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
