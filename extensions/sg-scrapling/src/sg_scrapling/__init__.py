from __future__ import annotations

import asyncio
import time

from scrape_gateway import FailureReason, ProviderAdapter, ScrapeRequest, ScrapeResult
from scrape_gateway.errors import classify_failure


class ScraplingProvider(ProviderAdapter):
    name = "scrapling"
    cost_rank = 10
    capabilities = frozenset({"html", "render_js", "premium"})

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        start = time.perf_counter()
        try:
            from scrapling.fetchers import AsyncFetcher, StealthyFetcher

            if request.render_js or request.premium:
                response = await asyncio.to_thread(
                    StealthyFetcher.fetch,
                    request.url,
                    headless=True,
                    network_idle=request.wait_event == "networkidle",
                    wait_selector=request.wait_selector,
                    wait=request.extra_wait_ms,
                    extra_headers=request.headers or None,
                    block_ads=request.block_ads,
                    timeout=int(request.timeout_seconds * 1000),
                )
                route = "scrapling:stealth"
            else:
                response = await AsyncFetcher.get(
                    request.url,
                    headers=request.headers or None,
                    timeout=request.timeout_seconds,
                    follow_redirects=True,
                )
                route = "scrapling:http"
            body = response.body
            html = body.decode(response.encoding or "utf-8", errors="replace")
            status = int(response.status)
            failure = classify_failure(status, html)
            return ScrapeResult(
                request.url,
                self.name,
                200 <= status < 400 and failure is None,
                status_code=status,
                html=html,
                failure_reason=failure,
                latency_ms=int((time.perf_counter() - start) * 1000),
                route=route,
            )
        except Exception as exc:  # noqa: BLE001
            reason = (
                FailureReason.TIMEOUT
                if "timeout" in f"{type(exc).__name__} {exc}".lower()
                else FailureReason.PROVIDER_ERROR
            )
            return ScrapeResult(
                request.url,
                self.name,
                False,
                error=str(exc),
                failure_reason=reason,
                latency_ms=int((time.perf_counter() - start) * 1000),
            )
