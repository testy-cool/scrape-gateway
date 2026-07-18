from __future__ import annotations

import os
import time

import httpx

from ..errors import classify_failure
from ..models import FailureReason, ScrapeRequest, ScrapeResult
from ..provider import ProviderAdapter


class BrightDataProvider(ProviderAdapter):
    name = "brightdata"
    cost_rank = 50
    capabilities = frozenset({"html", "render_js", "premium", "screenshot"})

    def __init__(
        self,
        api_key: str | None = None,
        zone: str | None = None,
        base_url: str = "https://api.brightdata.com/request",
    ) -> None:
        self.api_key = api_key or os.getenv("BRIGHTDATA_API_KEY")
        self.zone = zone or os.getenv("BRIGHTDATA_WEB_UNLOCKER_ZONE")
        self.base_url = base_url

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        if not self.api_key or not self.zone:
            return ScrapeResult(
                request.url,
                self.name,
                False,
                error="Missing BRIGHTDATA_API_KEY or BRIGHTDATA_WEB_UNLOCKER_ZONE",
            )
        payload = {"zone": self.zone, "url": request.url, "format": "raw"}
        if request.screenshot:
            payload["data_format"] = "screenshot"
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=request.timeout_seconds, follow_redirects=True
            ) as client:
                response = await client.post(
                    self.base_url,
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
            is_image = response.headers.get("content-type", "").startswith("image/")
            screenshot = response.content if request.screenshot and is_image else None
            html = None if request.screenshot else response.text
            failure = None if screenshot else classify_failure(response.status_code, html)
            screenshot_error = request.screenshot and not screenshot
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=response.is_success and failure is None and not screenshot_error,
                status_code=response.status_code,
                html=html,
                screenshot=screenshot,
                failure_reason=FailureReason.PROVIDER_ERROR if screenshot_error else failure,
                error=("Screenshot was requested but not returned" if screenshot_error else None),
                cost_units=10 if request.screenshot else 5,
                latency_ms=int((time.perf_counter() - start) * 1000),
                route="brightdata:screenshot" if request.screenshot else "brightdata:unlocker",
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
