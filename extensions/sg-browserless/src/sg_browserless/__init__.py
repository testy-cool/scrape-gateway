from __future__ import annotations

import asyncio
import os
import time

import httpx

from scrape_gateway import FailureReason, ProviderAdapter, ScrapeRequest, ScrapeResult
from scrape_gateway.errors import classify_failure


def _wait_until(request: ScrapeRequest) -> str:
    if request.wait_event == "networkidle":
        return "networkidle2"
    return request.wait_event or "networkidle2"


class BrowserlessProvider(ProviderAdapter):
    name = "browserless"
    cost_rank = 20
    capabilities = frozenset({"html", "render_js", "screenshot"})

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("BROWSERLESS_URL", "")).rstrip("/")
        self.token = token or api_key or os.getenv("BROWSERLESS_TOKEN", "")

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        if not self.base_url or not self.token:
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=False,
                error="Missing BROWSERLESS_URL or BROWSERLESS_TOKEN",
                failure_reason=FailureReason.PROVIDER_ERROR,
            )

        timeout_ms = int(request.timeout_seconds * 1000) + request.extra_wait_ms
        body: dict[str, object] = {
            "url": request.url,
            "gotoOptions": {
                "waitUntil": _wait_until(request),
                "timeout": timeout_ms,
            },
        }
        if request.wait_selector:
            body["waitForSelector"] = {
                "selector": request.wait_selector,
                "timeout": timeout_ms,
            }

        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=(timeout_ms / 1000) + 10,
                follow_redirects=True,
            ) as client:
                if request.screenshot:
                    content_response, screenshot_response = await asyncio.gather(
                        client.post(
                            f"{self.base_url}/content",
                            params={"token": self.token},
                            json=body,
                        ),
                        client.post(
                            f"{self.base_url}/screenshot",
                            params={"token": self.token},
                            json=body,
                        ),
                    )
                else:
                    content_response = await client.post(
                        f"{self.base_url}/content",
                        params={"token": self.token},
                        json=body,
                    )

            latency_ms = int((time.perf_counter() - start) * 1000)
            if request.screenshot:
                html = content_response.text if content_response.is_success else ""
                content_failure = classify_failure(content_response.status_code, html)
                screenshot_success = screenshot_response.is_success
                success = (
                    content_response.is_success and content_failure is None and screenshot_success
                )
                if content_failure is not None:
                    failure_reason = content_failure
                elif success:
                    failure_reason = None
                else:
                    failure_reason = FailureReason.PROVIDER_ERROR
                errors = []
                if not content_response.is_success:
                    errors.append(f"content: {content_response.text}")
                if not screenshot_success:
                    errors.append(f"screenshot: {screenshot_response.text}")
                return ScrapeResult(
                    url=request.url,
                    provider=self.name,
                    success=success,
                    status_code=content_response.status_code,
                    html=html if content_response.is_success else None,
                    screenshot=screenshot_response.content if screenshot_success else None,
                    error="; ".join(errors) or None,
                    failure_reason=failure_reason,
                    cost_units=10,
                    latency_ms=latency_ms,
                    route="browserless:content+screenshot",
                )

            html = content_response.text if content_response.is_success else ""
            failure = classify_failure(content_response.status_code, html)
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=content_response.is_success and failure is None,
                status_code=content_response.status_code,
                html=html if content_response.is_success else None,
                failure_reason=failure,
                error=None if content_response.is_success else content_response.text,
                cost_units=5,
                latency_ms=latency_ms,
                route="browserless:content",
            )
        except httpx.TimeoutException as exc:
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=False,
                error=str(exc),
                failure_reason=FailureReason.TIMEOUT,
                latency_ms=int((time.perf_counter() - start) * 1000),
            )
        except Exception as exc:  # noqa: BLE001
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=False,
                error=str(exc),
                failure_reason=FailureReason.PROVIDER_ERROR,
                latency_ms=int((time.perf_counter() - start) * 1000),
            )
