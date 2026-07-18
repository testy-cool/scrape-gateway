from __future__ import annotations

import time

from scrape_gateway import FailureReason, ProviderAdapter, ScrapeRequest, ScrapeResult
from scrape_gateway.errors import classify_exception, classify_failure
from scrape_gateway.headers import browser_context_headers


class PlaywrightProvider(ProviderAdapter):
    name = "playwright"
    cost_rank = 12
    capabilities = frozenset({"html", "render_js", "screenshot"})

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        start = time.perf_counter()
        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                try:
                    page_options: dict[str, object] = {
                        "extra_http_headers": browser_context_headers(request.headers) or None,
                    }
                    if request.mobile:
                        page_options.update(
                            {
                                "is_mobile": True,
                                "has_touch": True,
                                "viewport": {"width": 390, "height": 844},
                            }
                        )
                    page = await browser.new_page(**page_options)
                    response = await page.goto(
                        request.url,
                        wait_until=request.wait_event or "domcontentloaded",
                        timeout=int(request.timeout_seconds * 1000),
                    )
                    if request.wait_selector:
                        await page.wait_for_selector(
                            request.wait_selector,
                            timeout=int(request.timeout_seconds * 1000),
                        )
                    if request.extra_wait_ms:
                        await page.wait_for_timeout(request.extra_wait_ms)
                    html = await page.content()
                    screenshot = (
                        await page.screenshot(full_page=True, type="png")
                        if request.screenshot
                        else None
                    )
                    status = int(response.status) if response is not None else 200
                    final_url = page.url
                finally:
                    await browser.close()
            failure = classify_failure(status, html)
            return ScrapeResult(
                request.url,
                self.name,
                200 <= status < 400 and failure is None,
                status_code=status,
                html=html,
                screenshot=screenshot,
                failure_reason=failure,
                latency_ms=int((time.perf_counter() - start) * 1000),
                route="playwright:chromium",
                metadata={"final_url": final_url},
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
