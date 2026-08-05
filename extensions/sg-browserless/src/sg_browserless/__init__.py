from __future__ import annotations

import asyncio
import json
import os
import time
from urllib.parse import urlsplit

import httpx

from scrape_gateway import FailureReason, ProviderAdapter, ScrapeRequest, ScrapeResult
from scrape_gateway.errors import classify_provider_failure

# Chrome reports proxy trouble through the page load, so browserless returns 500 with
# the net:: error in the body rather than a proxy-specific status code.
_PROXY_ERROR_MARKERS = (
    "ERR_PROXY_CONNECTION_FAILED",
    "ERR_TUNNEL_CONNECTION_FAILED",
    "ERR_NO_SUPPORTED_PROXIES",
    "ERR_INVALID_AUTH_CREDENTIALS",
    "ERR_PROXY_AUTH_UNSUPPORTED",
)

_CREDENTIALS_UNSUPPORTED = (
    "SCRAPE_PROXY_URL carries credentials. Chrome's --proxy-server ignores them, and "
    "browserless's externalProxyServer parameter, which does accept them, exists only on "
    "the managed cloud service. Whitelist the browserless host's egress IP with the proxy "
    "vendor, or send proxied requests through raw_http, wreq, or curl_cffi instead."
)


def _wait_until(request: ScrapeRequest) -> str:
    if request.wait_event == "networkidle":
        return "networkidle2"
    return request.wait_event or "networkidle2"


def _proxy_config() -> tuple[str | None, str | None]:
    """Resolve SCRAPE_PROXY_URL into a --proxy-server value, or explain why not.

    Returns (proxy_server, skip_reason); at most one is ever set. An unusable proxy is
    reported on the result rather than raised, because it is not a reason to fail a
    scrape that works without one. It is recorded rather than dropped, because silently
    ignoring a proxy the operator configured is how a request they believed was proxied
    goes out from the wrong address.
    """
    raw = (os.getenv("SCRAPE_PROXY_URL") or "").strip()
    if not raw:
        return None, None
    parts = urlsplit(raw)
    if parts.username or parts.password:
        return None, _CREDENTIALS_UNSUPPORTED
    host = parts.hostname
    if not host:
        return None, f"SCRAPE_PROXY_URL is not a usable proxy URL: {raw!r}"
    netloc = f"{host}:{parts.port}" if parts.port else host
    return f"{parts.scheme or 'http'}://{netloc}", None


def _is_proxy_failure(response: httpx.Response) -> bool:
    if response.is_success:
        return False
    return any(marker in response.text for marker in _PROXY_ERROR_MARKERS)


class BrowserlessProvider(ProviderAdapter):
    name = "browserless"
    cost_rank = 20
    capabilities = frozenset({"html", "render_js", "screenshot"})
    required_configuration = (
        ("base_url", "BROWSERLESS_URL"),
        ("token", "BROWSERLESS_TOKEN"),
    )

    def estimated_cost_units(self, request: ScrapeRequest) -> float:
        return 10.0 if request.screenshot else 5.0

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("BROWSERLESS_URL", "")).rstrip("/")
        self.token = token or api_key or os.getenv("BROWSERLESS_TOKEN", "")

    async def _fetch(
        self,
        client: httpx.AsyncClient,
        body: dict[str, object],
        params: dict[str, str],
        *,
        screenshot: bool,
    ) -> tuple[httpx.Response, httpx.Response | None]:
        if screenshot:
            content_response, screenshot_response = await asyncio.gather(
                client.post(f"{self.base_url}/content", json=body, params=params),
                client.post(f"{self.base_url}/screenshot", json=body, params=params),
            )
            return content_response, screenshot_response
        return await client.post(f"{self.base_url}/content", json=body, params=params), None

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        if error := self.availability_error():
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=False,
                error=error,
                failure_reason=FailureReason.PROVIDER_UNAVAILABLE,
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

        proxy_server, proxy_skip_reason = _proxy_config()
        params: dict[str, str] = {}
        if proxy_server:
            params["launch"] = json.dumps({"args": [f"--proxy-server={proxy_server}"]})

        start = time.perf_counter()
        proxy_fallback: str | None = None
        try:
            async with httpx.AsyncClient(
                timeout=(timeout_ms / 1000) + 10,
                follow_redirects=True,
                headers={"Authorization": f"Bearer {self.token}"},
            ) as client:
                content_response, screenshot_response = await self._fetch(
                    client, body, params, screenshot=request.screenshot
                )
                if proxy_server and _is_proxy_failure(content_response):
                    proxy_fallback = content_response.text.strip()
                    content_response, screenshot_response = await self._fetch(
                        client, body, {}, screenshot=request.screenshot
                    )

            latency_ms = int((time.perf_counter() - start) * 1000)
            metadata: dict[str, object] = {}
            if proxy_server and not proxy_fallback:
                metadata["proxy"] = proxy_server
            if proxy_skip_reason:
                metadata["proxy_skipped"] = proxy_skip_reason
            if proxy_fallback:
                metadata["proxy_fallback"] = "disabled_after_proxy_error"
                metadata["proxy_error"] = proxy_fallback

            if screenshot_response is not None:
                html = content_response.text if content_response.is_success else ""
                content_failure = classify_provider_failure(
                    content_response.status_code,
                    content_response.text,
                )
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
                    cost_units=self.estimated_cost_units(request),
                    latency_ms=latency_ms,
                    route="browserless:content+screenshot",
                    metadata=metadata,
                )

            html = content_response.text if content_response.is_success else ""
            failure = classify_provider_failure(
                content_response.status_code,
                content_response.text,
            )
            if failure is None and _is_proxy_failure(content_response):
                failure = FailureReason.PROXY_ERROR
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=content_response.is_success and failure is None,
                status_code=content_response.status_code,
                html=html if content_response.is_success else None,
                failure_reason=failure,
                error=None if content_response.is_success else content_response.text,
                cost_units=self.estimated_cost_units(request),
                latency_ms=latency_ms,
                route="browserless:content",
                metadata=metadata,
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
