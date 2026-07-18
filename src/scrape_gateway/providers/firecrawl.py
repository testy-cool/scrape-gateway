from __future__ import annotations

import base64
import os
import time

import httpx

from ..errors import classify_failure
from ..models import FailureReason, ScrapeRequest, ScrapeResult
from ..provider import ProviderAdapter


class FirecrawlProvider(ProviderAdapter):
    name = "firecrawl"
    cost_rank = 26
    capabilities = frozenset({"html", "markdown", "country", "render_js", "premium", "screenshot"})

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("FIRECRAWL_API_KEY")
        self.base_url = (
            base_url or os.getenv("FIRECRAWL_BASE_URL") or "https://api.firecrawl.dev"
        ).rstrip("/")

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        if not self.api_key:
            return ScrapeResult(request.url, self.name, False, error="Missing FIRECRAWL_API_KEY")
        formats: list[object] = ["html", "markdown"]
        if request.screenshot:
            formats.append({"type": "screenshot", "fullPage": True})
        payload: dict[str, object] = {
            "url": request.url,
            "formats": formats,
            "onlyMainContent": False,
            "mobile": request.mobile,
            "blockAds": request.block_ads,
            "timeout": int(request.timeout_seconds * 1000),
        }
        if request.country:
            payload["location"] = {"country": request.country.upper()}
        if request.extra_wait_ms:
            payload["waitFor"] = request.extra_wait_ms
        if request.headers:
            payload["headers"] = request.headers
        if request.premium:
            payload["proxy"] = "stealth"

        start = time.perf_counter()
        try:
            headers = {"Authorization": f"Bearer {self.api_key}"}
            async with httpx.AsyncClient(
                timeout=request.timeout_seconds, follow_redirects=True
            ) as client:
                response = await client.post(
                    f"{self.base_url}/v2/scrape", json=payload, headers=headers
                )
                data = response.json()
                result_data = data.get("data", {}) if isinstance(data, dict) else {}
                html = result_data.get("html") or result_data.get("rawHtml")
                markdown = result_data.get("markdown")
                screenshot_value = result_data.get("screenshot")
                screenshot = None
                if request.screenshot and isinstance(screenshot_value, str):
                    if screenshot_value.startswith("data:image/"):
                        screenshot = base64.b64decode(screenshot_value.split(",", 1)[1])
                    elif screenshot_value.startswith(("http://", "https://")):
                        image_response = await client.get(screenshot_value)
                        if image_response.is_success and image_response.headers.get(
                            "content-type", ""
                        ).startswith("image/"):
                            screenshot = image_response.content
            metadata = result_data.get("metadata", {})
            target_status = int(metadata.get("statusCode") or response.status_code)
            failure = classify_failure(target_status, html or markdown)
            screenshot_error = request.screenshot and not screenshot
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=(
                    response.is_success
                    and bool(data.get("success", True))
                    and failure is None
                    and not screenshot_error
                ),
                status_code=target_status,
                html=html,
                markdown=markdown,
                screenshot=screenshot,
                failure_reason=FailureReason.PROVIDER_ERROR if screenshot_error else failure,
                error=("Screenshot was requested but not returned" if screenshot_error else None),
                cost_units=5 if request.premium else 1,
                latency_ms=int((time.perf_counter() - start) * 1000),
                route="firecrawl:stealth" if request.premium else "firecrawl",
                metadata={"firecrawl": metadata},
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
