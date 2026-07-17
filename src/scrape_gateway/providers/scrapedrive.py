from __future__ import annotations

import os
import sys
import time
from urllib.parse import urlparse

import httpx

from ..errors import classify_failure
from ..models import FailureReason, ScrapeRequest, ScrapeResult
from ..provider import ProviderAdapter

SYNC_BASE = "https://sync.scrapedrive.com/api/v1/scrape"

TIER_ORDER = ["standard", "advanced", "hyperdrive"]
TIER_COST = {"standard": 1, "advanced": 5, "hyperdrive": 25}


def _start_tier(request: ScrapeRequest) -> str:
    start_tier = request.metadata.get("start_tier", "")
    if start_tier.startswith("scrapedrive:"):
        remembered = start_tier.split(":", 1)[1]
        if remembered in TIER_ORDER:
            return remembered

    if request.premium:
        return "hyperdrive"
    if request.country:
        return "advanced"
    return "standard"


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


class ScrapeDriveProvider(ProviderAdapter):
    name = "scrapedrive"
    cost_rank = 25
    capabilities = frozenset({"html", "markdown", "country", "render_js", "premium", "screenshot"})

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("SCRAPEDRIVE_API_KEY")

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        if not self.api_key:
            return ScrapeResult(request.url, self.name, False, error="Missing SCRAPEDRIVE_API_KEY")

        start = _start_tier(request)
        tiers = TIER_ORDER[TIER_ORDER.index(start) :]

        last_result: ScrapeResult | None = None
        for tier in tiers:
            result = await self._attempt(request, tier)
            if result.success:
                return result
            last_result = result
            if tier != tiers[-1]:
                _log(
                    f"    [{self.name}] {tier} failed, escalating to {tiers[tiers.index(tier) + 1]}"
                )

        return last_result  # type: ignore[return-value]

    async def _attempt(self, request: ScrapeRequest, tier: str) -> ScrapeResult:
        params: dict[str, str] = {
            "api_key": self.api_key,
            "url": request.url,
            "scrape_tier": tier,
            "render_js": "true" if request.render_js else "false",
            "device_type": "mobile" if request.mobile else "desktop",
            "block_resources": "true",
            "result_type": "html",
        }
        if request.country and tier != "standard":
            params["country_code"] = request.country.upper()
        if request.screenshot:
            params["screenshot"] = "true"
        if request.block_ads:
            params["block_ads"] = "true"
        if request.wait_selector:
            params["wait_for_selector"] = request.wait_selector
        if request.extra_wait_ms:
            params["extra_wait"] = str(request.extra_wait_ms)
        if tier == "hyperdrive":
            params["block_resources"] = "false"
            params["wait_browser"] = "networkidle"
            params["render_js"] = "true"
        elif request.wait_event:
            params["wait_browser"] = request.wait_event

        timeout = request.timeout_seconds
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.get(SYNC_BASE, params=params)

            content_type = response.headers.get("content-type", "")
            if "application/json" in content_type:
                data = response.json()
                html = data.get("html") or data.get("body") or data.get("content", "")
                markdown = data.get("markdown")
            else:
                html = response.text
                data = None
                markdown = None

            screenshot_url = response.headers.get("x-sdrive-screenshot-url") or (
                data.get("screenshot_url") if isinstance(data, dict) else None
            )

            screenshot = None
            screenshot_error = None
            if request.screenshot:
                parsed_screenshot_url = urlparse(screenshot_url or "")
                if parsed_screenshot_url.scheme not in {"http", "https"}:
                    screenshot_error = "Screenshot was requested but no downloadable URL was returned"
                else:
                    async with httpx.AsyncClient(
                        timeout=request.timeout_seconds, follow_redirects=True
                    ) as screenshot_client:
                        screenshot_response = await screenshot_client.get(screenshot_url)
                    screenshot_content_type = screenshot_response.headers.get("content-type", "")
                    if (
                        screenshot_response.is_success
                        and screenshot_response.content
                        and screenshot_content_type.lower().startswith("image/")
                    ):
                        screenshot = screenshot_response.content
                    else:
                        screenshot_error = (
                            "Screenshot download failed with HTTP "
                            f"{screenshot_response.status_code} ({screenshot_content_type or 'unknown type'})"
                        )

            failure = classify_failure(response.status_code, html)
            if request.screenshot and not screenshot and failure is None:
                failure = FailureReason.PROVIDER_ERROR
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=response.is_success and failure is None and not screenshot_error,
                status_code=response.status_code,
                html=html,
                markdown=markdown,
                screenshot=screenshot,
                failure_reason=failure,
                error=screenshot_error,
                cost_units=TIER_COST.get(tier, 1),
                latency_ms=int((time.perf_counter() - start) * 1000),
                route=f"scrapedrive:{tier}",
                metadata={
                    "tier": tier,
                    "screenshot_url": screenshot_url,
                    "screenshot_bytes": len(screenshot or b""),
                    **({"raw_json": data} if isinstance(data, dict) else {}),
                },
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
