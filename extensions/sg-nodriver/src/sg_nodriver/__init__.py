from __future__ import annotations

import asyncio
import base64
import time

from scrape_gateway import FailureReason, ProviderAdapter, ScrapeRequest, ScrapeResult
from scrape_gateway.errors import classify_failure


class NodriverProvider(ProviderAdapter):
    name = "nodriver"
    cost_rank = 16
    capabilities = frozenset({"html", "render_js", "premium", "screenshot"})

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        start = time.perf_counter()
        browser = None
        try:
            import nodriver

            browser = await nodriver.start(headless=True)
            page = await browser.get(request.url)
            if request.wait_selector:
                await page.select(request.wait_selector, timeout=request.timeout_seconds)
            if request.extra_wait_ms:
                await asyncio.sleep(request.extra_wait_ms / 1000)
            html = await page.get_content()
            screenshot = None
            if request.screenshot:
                encoded = await page.save_screenshot(format="png", full_page=True, as_base64=True)
                screenshot = base64.b64decode(encoded)
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
                route="nodriver:chrome",
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
        finally:
            if browser is not None:
                browser.stop()
