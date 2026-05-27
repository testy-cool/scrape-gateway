from __future__ import annotations

import time

from ..errors import classify_exception, classify_failure
from ..models import FailureReason, ScrapeRequest, ScrapeResult
from ..provider import ProviderAdapter


class CurlCffiProvider(ProviderAdapter):
    """TLS-fingerprinted HTTP via curl_cffi (libcurl-impersonate, 43+ browser profiles)."""

    name = "curl_cffi"
    cost_rank = 3
    capabilities = frozenset({"html"})

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        try:
            from curl_cffi import AsyncSession  # noqa: F401
        except ImportError:
            return ScrapeResult(
                request.url,
                self.name,
                False,
                error="Missing curl_cffi — install with: pip install curl_cffi",
                failure_reason=FailureReason.PROVIDER_ERROR,
            )

        import os

        start = time.perf_counter()
        proxy_url = os.getenv("SCRAPE_PROXY_URL") or None
        result = await self._scrape(request, proxy_url, start, AsyncSession)
        if proxy_url and result.failure_reason == FailureReason.PROXY_ERROR:
            retry = await self._scrape(request, None, start, AsyncSession)
            retry.metadata["proxy_fallback"] = "disabled_after_proxy_error"
            retry.metadata["proxy_error"] = result.error
            return retry
        return result

    async def _scrape(
        self,
        request: ScrapeRequest,
        proxy_url: str | None,
        start: float,
        AsyncSession,
    ) -> ScrapeResult:
        try:
            async with AsyncSession(impersonate="chrome", proxy=proxy_url) as session:
                response = await session.get(
                    request.url,
                    headers=request.headers or None,
                    timeout=request.timeout_seconds,
                    allow_redirects=True,
                )
            failure = classify_failure(response.status_code, response.text)
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=response.ok and failure is None,
                status_code=response.status_code,
                html=response.text,
                failure_reason=failure,
                latency_ms=int((time.perf_counter() - start) * 1000),
                route="curl_cffi",
            )
        except Exception as exc:  # noqa: BLE001
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=False,
                error=str(exc),
                failure_reason=classify_exception(exc),
            )
