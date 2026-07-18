from __future__ import annotations

import asyncio
import time

from scrape_gateway import FailureReason, ProviderAdapter, ScrapeRequest, ScrapeResult
from scrape_gateway.errors import classify_failure


class SpiderRsProvider(ProviderAdapter):
    name = "spider_rs"
    cost_rank = 11
    capabilities = frozenset({"html"})

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        start = time.perf_counter()

        def fetch() -> str:
            from spider_rs import Page

            page = Page(request.url)
            page.fetch()
            return page.get_html()

        try:
            html = await asyncio.wait_for(asyncio.to_thread(fetch), timeout=request.timeout_seconds)
            failure = classify_failure(200, html)
            return ScrapeResult(
                request.url,
                self.name,
                failure is None,
                status_code=200,
                html=html,
                failure_reason=failure,
                latency_ms=int((time.perf_counter() - start) * 1000),
                route="spider_rs:page",
            )
        except TimeoutError as exc:
            return ScrapeResult(
                request.url,
                self.name,
                False,
                error=str(exc) or "spider-rs timed out",
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
