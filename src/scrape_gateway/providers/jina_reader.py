from __future__ import annotations

import os
import time

import httpx

from ..errors import classify_failure
from ..models import FailureReason, ScrapeRequest, ScrapeResult
from ..provider import ProviderAdapter


class JinaReaderProvider(ProviderAdapter):
    name = "jina_reader"
    cost_rank = 8
    capabilities = frozenset({"html", "markdown", "render_js"})

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://r.jina.ai",
    ) -> None:
        self.api_key = api_key or os.getenv("JINA_API_KEY")
        self.base_url = base_url.rstrip("/")

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        headers = {
            "X-Respond-With": "markdown",
            "X-Engine": "browser" if request.render_js else "auto",
            "X-Timeout": str(min(int(request.timeout_seconds), 180)),
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if request.wait_selector:
            headers["X-Wait-For-Selector"] = request.wait_selector
        if request.metadata.get("no_cache"):
            headers["X-No-Cache"] = "true"

        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=request.timeout_seconds, follow_redirects=True
            ) as client:
                response = await client.get(f"{self.base_url}/{request.url}", headers=headers)
            failure = classify_failure(response.status_code, response.text)
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=response.is_success and failure is None,
                status_code=response.status_code,
                html=response.text,
                markdown=response.text,
                failure_reason=failure,
                cost_units=0,
                latency_ms=int((time.perf_counter() - start) * 1000),
                route="jina_reader:browser" if request.render_js else "jina_reader",
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
