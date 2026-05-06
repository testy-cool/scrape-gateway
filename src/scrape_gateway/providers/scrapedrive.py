from __future__ import annotations

import os
import time

import httpx

from ..errors import classify_failure
from ..models import FailureReason, ScrapeRequest, ScrapeResult
from ..provider import ProviderAdapter


class ScrapeDriveProvider(ProviderAdapter):
    """ScrapeDrive adapter.

    Endpoint/params need verification against actual ScrapeDrive docs.
    """

    name = "scrapedrive"
    cost_rank = 25
    capabilities = frozenset({"html", "markdown", "country", "render_js", "premium", "screenshot"})

    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        self.api_key = api_key or os.getenv("SCRAPEDRIVE_API_KEY")
        self.base_url = (
            base_url or os.getenv("SCRAPEDRIVE_BASE_URL") or "https://api.scrapedrive.com/v1/scrape"
        ).rstrip("/")

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        if not self.api_key:
            return ScrapeResult(request.url, self.name, False, error="Missing SCRAPEDRIVE_API_KEY")
        payload = {
            "url": request.url,
            "country": request.country,
            "render_js": request.render_js,
            "premium": request.premium,
            "screenshot": request.screenshot,
            "formats": ["html", "markdown"] + (["screenshot"] if request.screenshot else []),
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=request.timeout_seconds, follow_redirects=True
            ) as client:
                response = await client.post(self.base_url, json=payload, headers=headers)
            data = None
            try:
                data = response.json()
            except ValueError:
                pass
            html = data.get("html") if isinstance(data, dict) else response.text
            markdown = data.get("markdown") if isinstance(data, dict) else None
            failure = classify_failure(response.status_code, html)
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=response.is_success and failure is None,
                status_code=response.status_code,
                html=html,
                markdown=markdown,
                failure_reason=failure,
                cost_units=1,
                latency_ms=int((time.perf_counter() - start) * 1000),
                route="scrapedrive",
                metadata={"raw_json": data} if isinstance(data, dict) else {},
            )
        except httpx.TimeoutException as exc:
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=False,
                error=str(exc),
                failure_reason=FailureReason.TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=False,
                error=str(exc),
                failure_reason=FailureReason.PROVIDER_ERROR,
            )
