from __future__ import annotations

import time
from datetime import timedelta

from scrape_gateway import FailureReason, ProviderAdapter, ScrapeRequest, ScrapeResult
from scrape_gateway.errors import classify_failure


class CrawleeProvider(ProviderAdapter):
    name = "crawlee"
    cost_rank = 17
    capabilities = frozenset({"html", "render_js", "screenshot"})

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        start = time.perf_counter()
        captured: dict[str, object] = {}
        try:
            from crawlee.crawlers import PlaywrightCrawler

            crawler = PlaywrightCrawler(
                headless=True,
                max_requests_per_crawl=1,
                request_handler_timeout=timedelta(seconds=request.timeout_seconds),
            )

            @crawler.router.default_handler
            async def capture(context) -> None:
                if request.wait_selector:
                    await context.page.wait_for_selector(
                        request.wait_selector, timeout=int(request.timeout_seconds * 1000)
                    )
                if request.extra_wait_ms:
                    await context.page.wait_for_timeout(request.extra_wait_ms)
                captured["html"] = await context.page.content()
                if request.screenshot:
                    captured["screenshot"] = await context.page.screenshot(
                        full_page=True, type="png"
                    )

            await crawler.run([request.url])
            html = captured.get("html")
            if not isinstance(html, str):
                raise RuntimeError("Crawlee completed without returning page HTML")
            screenshot = captured.get("screenshot")
            if not isinstance(screenshot, bytes):
                screenshot = None
            failure = classify_failure(200, html)
            return ScrapeResult(
                request.url,
                self.name,
                failure is None,
                status_code=200,
                html=html,
                screenshot=screenshot,
                failure_reason=failure,
                latency_ms=int((time.perf_counter() - start) * 1000),
                route="crawlee:playwright",
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
