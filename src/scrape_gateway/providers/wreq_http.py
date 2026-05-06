from __future__ import annotations

import time

from ..errors import classify_failure
from ..models import FailureReason, ScrapeRequest, ScrapeResult
from ..provider import ProviderAdapter


class WreqProvider(ProviderAdapter):
    """TLS-fingerprinted HTTP via wreq (Rust-backed, 107+ browser profiles)."""

    name = "wreq"
    cost_rank = 2
    capabilities = frozenset({"html"})

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        try:
            from datetime import timedelta

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
        try:
            proxy_url = os.getenv("SCRAPE_PROXY_URL")
            kwargs: dict = {
                "emulation": Emulation.random(),
                "redirect": Policy.limited(10),
                "timeout": timedelta(seconds=request.timeout_seconds),
            }
            if proxy_url:
                from wreq import Proxy

                kwargs["proxies"] = [Proxy(url=proxy_url)]
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
            is_timeout = "timeout" in type(exc).__name__.lower()
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=False,
                error=str(exc),
                failure_reason=FailureReason.TIMEOUT if is_timeout else FailureReason.UNKNOWN,
            )
