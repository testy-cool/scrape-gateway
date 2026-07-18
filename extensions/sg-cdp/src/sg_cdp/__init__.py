from __future__ import annotations

import os
import time

from scrape_gateway import FailureReason, ProviderAdapter, ScrapeRequest, ScrapeResult
from scrape_gateway.errors import classify_exception, classify_failure


class _CdpProvider(ProviderAdapter):
    endpoint_env: str

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        start = time.perf_counter()
        endpoint = os.getenv(self.endpoint_env)
        if not endpoint:
            return ScrapeResult(
                request.url,
                self.name,
                False,
                error=f"{self.endpoint_env} is not configured",
                failure_reason=FailureReason.PROVIDER_ERROR,
                latency_ms=0,
            )

        browser = None
        page = None
        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as playwright:
                browser = await playwright.chromium.connect_over_cdp(
                    endpoint,
                    timeout=int(request.timeout_seconds * 1000),
                )
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = await context.new_page()
                if request.headers:
                    await page.set_extra_http_headers(request.headers)
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
                await page.close()
                page = None
                await browser.close()
                browser = None
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
                route=f"{self.name}:cdp",
                metadata={"final_url": final_url, "endpoint_env": self.endpoint_env},
            )
        except Exception as exc:  # noqa: BLE001
            if page is not None:
                await page.close()
            if browser is not None:
                await browser.close()
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


class ChromeCdpProvider(_CdpProvider):
    name = "chrome_cdp"
    cost_rank = 11
    capabilities = frozenset({"html", "render_js", "screenshot"})
    endpoint_env = "CHROME_CDP_URL"


class LightpandaProvider(_CdpProvider):
    name = "lightpanda"
    cost_rank = 11
    capabilities = frozenset({"html", "render_js"})
    endpoint_env = "LIGHTPANDA_CDP_URL"
