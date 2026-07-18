from __future__ import annotations

import os
import time

import httpx

from ..errors import classify_failure
from ..models import FailureReason, ScrapeRequest, ScrapeResult
from ..provider import ProviderAdapter


class SpiderCloudProvider(ProviderAdapter):
    name = "spider_cloud"
    cost_rank = 24
    capabilities = frozenset({"html", "markdown", "render_js", "premium"})

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.spider.cloud",
    ) -> None:
        self.api_key = api_key or os.getenv("SPIDER_CLOUD_API_KEY")
        self.base_url = base_url.rstrip("/")

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        if not self.api_key:
            return ScrapeResult(request.url, self.name, False, error="Missing SPIDER_CLOUD_API_KEY")
        markdown_requested = request.output_format == "markdown"
        payload: dict[str, object] = {
            "url": request.url,
            "request": "chrome"
            if request.render_js
            else "smart_mode"
            if request.premium
            else "http",
            "return_format": "markdown" if markdown_requested else "raw",
        }
        if request.wait_selector:
            payload["wait_for"] = {
                "selector": {
                    "selector": request.wait_selector,
                    "timeout": {"secs": int(request.timeout_seconds), "nanos": 0},
                }
            }
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=request.timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url}/scrape",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
            data = response.json()
            first = data[0] if isinstance(data, list) and data else data
            first = first if isinstance(first, dict) else {}
            content = first.get("content") or first.get("html") or first.get("markdown")
            target_status = int(
                first.get("status") or first.get("status_code") or response.status_code
            )
            failure = classify_failure(target_status, content)
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=response.is_success and bool(first) and failure is None,
                status_code=target_status,
                html=content,
                markdown=content if markdown_requested else None,
                failure_reason=failure,
                cost_units=2 if request.render_js or request.premium else 1,
                latency_ms=int((time.perf_counter() - start) * 1000),
                route=f"spider_cloud:{payload['request']}",
                metadata={key: value for key, value in first.items() if key != "content"},
            )
        except httpx.TimeoutException as exc:
            return ScrapeResult(
                request.url,
                self.name,
                False,
                error=str(exc),
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
