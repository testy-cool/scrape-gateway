from __future__ import annotations

import asyncio
import base64
import inspect
import shutil
import time

from scrape_gateway import FailureReason, ProviderAdapter, ScrapeRequest, ScrapeResult
from scrape_gateway.errors import classify_exception, classify_failure


async def _resolve(value):
    return await value if inspect.isawaitable(value) else value


class PydollProvider(ProviderAdapter):
    name = "pydoll"
    cost_rank = 16
    capabilities = frozenset({"html", "render_js", "screenshot"})

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        start = time.perf_counter()
        browser = None
        try:
            from pydoll.browser.chromium import Chrome
            from pydoll.browser.options import ChromiumOptions

            options = ChromiumOptions()
            chrome = (
                shutil.which("google-chrome")
                or shutil.which("chromium")
                or shutil.which("chromium-browser")
            )
            if chrome:
                options.binary_location = chrome
            options.headless = True
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            if request.mobile:
                options.add_argument("--window-size=390,844")

            browser = Chrome(options=options)
            await browser.start(headless=True)
            tab = await browser.new_tab()
            await tab.go_to(request.url, timeout=max(1, int(request.timeout_seconds)))
            if request.wait_selector:
                await tab.query(
                    request.wait_selector,
                    timeout=max(1, int(request.timeout_seconds)),
                )
            if request.extra_wait_ms:
                await asyncio.sleep(request.extra_wait_ms / 1000)
            html = str(await _resolve(tab.page_source))
            final_url = str(await _resolve(tab.current_url))
            screenshot = None
            if request.screenshot:
                encoded = await tab.take_screenshot(
                    beyond_viewport=True,
                    as_base64=True,
                )
                if encoded:
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
                route="pydoll:cdp",
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
        finally:
            if browser is not None:
                await browser.stop()
