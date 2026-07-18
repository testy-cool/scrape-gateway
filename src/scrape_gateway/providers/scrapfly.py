from __future__ import annotations

import os
import time

import httpx

from ..errors import classify_failure
from ..models import FailureReason, ScrapeRequest, ScrapeResult
from ..provider import ProviderAdapter


class ScrapflyProvider(ProviderAdapter):
    name = "scrapfly"
    cost_rank = 32
    capabilities = frozenset({"html", "country", "render_js", "premium"})

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.scrapfly.io/scrape",
        cost_budget: int = 25,
    ) -> None:
        self.api_key = api_key or os.getenv("SCRAPFLY_API_KEY")
        self.base_url = base_url
        self.cost_budget = cost_budget

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        if not self.api_key:
            return ScrapeResult(request.url, self.name, False, error="Missing SCRAPFLY_API_KEY")
        params: dict[str, str] = {
            "key": self.api_key,
            "url": request.url,
            "format": "raw",
            "render_js": str(request.render_js).lower(),
        }
        if request.country:
            params["country"] = request.country.lower()
        if request.premium:
            params["asp"] = "true"
            params["cost_budget"] = str(self.cost_budget)
        if request.wait_selector:
            params["wait_for_selector"] = request.wait_selector
        if request.extra_wait_ms:
            params["rendering_wait"] = str(request.extra_wait_ms)
        session = request.metadata.get("session")
        if isinstance(session, str) and session:
            params["session"] = session

        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=request.timeout_seconds, follow_redirects=True
            ) as client:
                response = await client.get(self.base_url, params=params)
                data = response.json() if response.is_success else {}
                result_data = data.get("result", {}) if isinstance(data, dict) else {}
                html = result_data.get("content", "")
                if result_data.get("format") == "clob" and isinstance(html, str):
                    large_response = await client.get(html, params={"key": self.api_key})
                    large_response.raise_for_status()
                    html = large_response.text
            target_status = int(result_data.get("status_code") or response.status_code)
            failure = classify_failure(target_status, html)
            cost = response.headers.get("x-scrapfly-api-cost")
            context = data.get("context", {}) if isinstance(data, dict) else {}
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=response.is_success and failure is None,
                status_code=target_status,
                html=html,
                failure_reason=failure,
                cost_units=float(cost or context.get("cost") or 1),
                latency_ms=int((time.perf_counter() - start) * 1000),
                route="scrapfly:asp" if request.premium else "scrapfly",
                metadata={"context": context},
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
