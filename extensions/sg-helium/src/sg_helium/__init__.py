from __future__ import annotations

import asyncio
import shutil
import time

from scrape_gateway import FailureReason, ProviderAdapter, ScrapeRequest, ScrapeResult
from scrape_gateway.errors import classify_exception, classify_failure


def _run_session(request: ScrapeRequest) -> tuple[str, str, bytes | None]:
    from helium import get_driver, kill_browser, start_chrome
    from selenium.webdriver.chrome.options import Options

    options = Options()
    chrome = (
        shutil.which("google-chrome")
        or shutil.which("chromium")
        or shutil.which("chromium-browser")
    )
    if chrome:
        options.binary_location = chrome
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    if request.mobile:
        options.add_experimental_option(
            "mobileEmulation",
            {"deviceMetrics": {"width": 390, "height": 844, "pixelRatio": 3}},
        )

    try:
        start_chrome(request.url, headless=True, options=options)
        driver = get_driver()
        if request.wait_selector:
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait

            WebDriverWait(driver, request.timeout_seconds).until(
                lambda active_driver: active_driver.find_element(
                    By.CSS_SELECTOR,
                    request.wait_selector,
                )
            )
        if request.extra_wait_ms:
            time.sleep(request.extra_wait_ms / 1000)
        html = driver.page_source
        final_url = driver.current_url
        screenshot = driver.get_screenshot_as_png() if request.screenshot else None
        return html, final_url, screenshot
    finally:
        kill_browser()


class HeliumProvider(ProviderAdapter):
    name = "helium"
    cost_rank = 18
    capabilities = frozenset({"html", "render_js", "screenshot"})

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        start = time.perf_counter()
        try:
            html, final_url, screenshot = await asyncio.to_thread(_run_session, request)
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
                route="helium:selenium",
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
