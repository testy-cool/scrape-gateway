from __future__ import annotations

import time

from scrape_gateway import FailureReason, ProviderAdapter, ScrapeRequest, ScrapeResult
from scrape_gateway.errors import classify_failure


class CamoufoxProvider(ProviderAdapter):
    name = "camoufox"
    cost_rank = 13
    capabilities = frozenset({"html", "render_js", "premium", "screenshot"})

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        start = time.perf_counter()
        try:
            from camoufox.async_api import AsyncCamoufox

            async with AsyncCamoufox(headless=True) as browser:
                page = await browser.new_page(extra_http_headers=request.headers or None)
                await page.goto(
                    request.url,
                    wait_until=request.wait_event or "domcontentloaded",
                    timeout=int(request.timeout_seconds * 1000),
                )
                if request.wait_selector:
                    await page.wait_for_selector(
                        request.wait_selector, timeout=int(request.timeout_seconds * 1000)
                    )
                if request.extra_wait_ms:
                    await page.wait_for_timeout(request.extra_wait_ms)
                html = await page.content()
                screenshot = (
                    await page.screenshot(full_page=True, type="png")
                    if request.screenshot
                    else None
                )
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
                route="camoufox:browser",
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
