from __future__ import annotations

import base64
import os
import time
from typing import Any

import httpx

from ..errors import classify_failure
from ..headers import browser_context_headers
from ..models import FailureReason, ScrapeRequest, ScrapeResult
from ..provider import ProviderAdapter


def _markdown_text(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("raw_markdown", "fit_markdown", "markdown_with_citations"):
            text = value.get(key)
            if isinstance(text, str):
                return text
    return None


def _decode_screenshot(value: object) -> bytes | None:
    if not isinstance(value, str) or not value:
        return None
    encoded = value.split(",", 1)[1] if value.startswith("data:image/") else value
    try:
        return base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error):
        return None


class Crawl4AIProvider(ProviderAdapter):
    name = "crawl4ai"
    cost_rank = 18
    capabilities = frozenset({"html", "markdown", "render_js", "screenshot"})

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("CRAWL4AI_URL", "")).rstrip("/")
        self.token = token or os.getenv("CRAWL4AI_TOKEN", "")

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        if not self.base_url:
            return ScrapeResult(
                request.url,
                self.name,
                False,
                error="Missing CRAWL4AI_URL",
                failure_reason=FailureReason.PROVIDER_ERROR,
            )

        browser_params: dict[str, Any] = {"headless": True}
        if request.mobile:
            browser_params.update({"viewport_width": 390, "viewport_height": 844})
        extra_headers = browser_context_headers(request.headers)
        if extra_headers:
            browser_params["headers"] = extra_headers

        crawler_params: dict[str, Any] = {
            "stream": False,
            "cache_mode": "bypass",
            "page_timeout": int(request.timeout_seconds * 1000),
            "screenshot": request.screenshot,
        }
        if request.wait_event:
            crawler_params["wait_until"] = request.wait_event
        if request.wait_selector:
            crawler_params["wait_for"] = f"css:{request.wait_selector}"
        if request.extra_wait_ms:
            crawler_params["delay_before_return_html"] = request.extra_wait_ms / 1000

        payload = {
            "urls": [request.url],
            "browser_config": {"type": "BrowserConfig", "params": browser_params},
            "crawler_config": {"type": "CrawlerRunConfig", "params": crawler_params},
        }
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=request.timeout_seconds + 10,
                follow_redirects=True,
            ) as client:
                response = await client.post(
                    f"{self.base_url}/crawl",
                    json=payload,
                    headers=headers,
                )
            data = response.json()
            results = data.get("results", []) if isinstance(data, dict) else []
            item = results[0] if isinstance(results, list) and results else {}
            if not isinstance(item, dict):
                item = {}
            html = item.get("html") if isinstance(item.get("html"), str) else None
            markdown = _markdown_text(item.get("markdown"))
            status_code = int(item.get("status_code") or response.status_code)
            failure = classify_failure(status_code, html or markdown)
            provider_success = bool(item.get("success", data.get("success", False)))
            if (not response.is_success or not provider_success) and failure in {
                None,
                FailureReason.EMPTY_CONTENT,
            }:
                failure = FailureReason.PROVIDER_ERROR
            screenshot = _decode_screenshot(item.get("screenshot"))
            screenshot_error = request.screenshot and screenshot is None
            error = item.get("error_message") or data.get("detail") or data.get("error")
            if screenshot_error and not error:
                error = "Screenshot was requested but not returned"
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=(
                    response.is_success
                    and provider_success
                    and failure is None
                    and not screenshot_error
                ),
                status_code=status_code,
                html=html,
                markdown=markdown,
                screenshot=screenshot,
                failure_reason=(
                    FailureReason.PROVIDER_ERROR
                    if screenshot_error and failure is None
                    else failure
                ),
                error=str(error) if error else None,
                latency_ms=int((time.perf_counter() - start) * 1000),
                route="crawl4ai:docker",
                metadata={
                    "server_processing_time_s": data.get("server_processing_time_s"),
                },
            )
        except httpx.TimeoutException as exc:
            return ScrapeResult(
                request.url,
                self.name,
                False,
                error=str(exc),
                failure_reason=FailureReason.TIMEOUT,
                latency_ms=int((time.perf_counter() - start) * 1000),
            )
        except Exception as exc:  # noqa: BLE001
            return ScrapeResult(
                request.url,
                self.name,
                False,
                error=str(exc),
                failure_reason=FailureReason.PROVIDER_ERROR,
                latency_ms=int((time.perf_counter() - start) * 1000),
            )
