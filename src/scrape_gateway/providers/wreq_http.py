from __future__ import annotations

import time
from datetime import timedelta

from ..errors import classify_exception, classify_failure
from ..models import FailureReason, ScrapeRequest, ScrapeResult
from ..provider import ProviderAdapter


class WreqProvider(ProviderAdapter):
    """TLS-fingerprinted HTTP via wreq (Rust-backed, 107+ browser profiles)."""

    name = "wreq"
    cost_rank = 2
    capabilities = frozenset({"html"})

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        try:
            from wreq import Client, Emulation
            from wreq.redirect import Policy
        except ImportError:
            return ScrapeResult(
                request.url,
                self.name,
                False,
                error="Missing wreq — install with: pip install wreq",
                failure_reason=FailureReason.PROVIDER_ERROR,
            )

        import os

        start = time.perf_counter()
        proxy_url = os.getenv("SCRAPE_PROXY_URL") or None
        result = await self._scrape(request, proxy_url, start, Client, Emulation, Policy)
        if proxy_url and result.failure_reason == FailureReason.PROXY_ERROR:
            retry = await self._scrape(request, None, start, Client, Emulation, Policy)
            retry.metadata["proxy_fallback"] = "disabled_after_proxy_error"
            retry.metadata["proxy_error"] = result.error
            return retry
        return result

    async def _scrape(
        self,
        request: ScrapeRequest,
        proxy_url: str | None,
        start: float,
        Client,
        Emulation,
        Policy,
    ) -> ScrapeResult:
        try:
            kwargs: dict = {
                "emulation": Emulation.random(),
                "redirect": Policy.limited(10),
                "timeout": timedelta(seconds=request.timeout_seconds),
            }
            if proxy_url:
                from wreq import Proxy

                kwargs["proxies"] = [Proxy.all(proxy_url)]
            client = Client(**kwargs)
            response = await client.get(request.url, headers=request.headers or None)
            status_code = response.status.as_int()
            body = await response.text()
            failure = classify_failure(status_code, body)
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=response.status.is_success() and failure is None,
                status_code=status_code,
                html=body,
                failure_reason=failure,
                latency_ms=int((time.perf_counter() - start) * 1000),
                route="wreq",
            )
        except Exception as exc:  # noqa: BLE001
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=False,
                error=str(exc),
                failure_reason=classify_exception(exc),
            )
