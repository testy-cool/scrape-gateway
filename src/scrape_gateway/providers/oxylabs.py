from __future__ import annotations

import base64
import os
import time

import httpx

from ..errors import classify_failure
from ..models import FailureReason, ScrapeRequest, ScrapeResult
from ..provider import ProviderAdapter


class OxylabsProvider(ProviderAdapter):
    name = "oxylabs"
    cost_rank = 45
    capabilities = frozenset({"html", "country", "render_js", "premium", "screenshot"})

    def __init__(
        self,
        username: str | None = None,
        password: str | None = None,
        base_url: str = "https://realtime.oxylabs.io/v1/queries",
    ) -> None:
        self.username = username or os.getenv("OXYLABS_USERNAME")
        self.password = password or os.getenv("OXYLABS_PASSWORD")
        self.base_url = base_url

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        if not self.username or not self.password:
            return ScrapeResult(
                request.url,
                self.name,
                False,
                error="Missing OXYLABS_USERNAME or OXYLABS_PASSWORD",
            )
        payload: dict[str, object] = {"source": "universal", "url": request.url}
        if request.screenshot:
            payload["render"] = "png"
        elif request.render_js:
            payload["render"] = "html"
        if request.country:
            payload["geo_location"] = request.country
        if request.mobile:
            payload["user_agent_type"] = "mobile"

        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=request.timeout_seconds) as client:
                response = await client.post(
                    self.base_url,
                    json=payload,
                    auth=httpx.BasicAuth(self.username, self.password),
                )
            data = response.json()
            results = data.get("results", []) if isinstance(data, dict) else []
            first = results[0] if results else {}
            target_status = int(first.get("status_code") or response.status_code)
            content = first.get("content")
            screenshot = None
            html = content
            if request.screenshot and isinstance(content, str):
                screenshot = base64.b64decode(content)
                html = None
            failure = None if screenshot else classify_failure(target_status, html)
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=response.is_success and bool(first) and failure is None,
                status_code=target_status,
                html=html,
                screenshot=screenshot,
                failure_reason=failure,
                cost_units=10 if request.screenshot or request.render_js else 1,
                latency_ms=int((time.perf_counter() - start) * 1000),
                route="oxylabs:rendered" if request.render_js or request.screenshot else "oxylabs",
                metadata={"job_id": first.get("job_id")},
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
