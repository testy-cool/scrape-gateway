"""
Example sg extension — a custom provider that wraps an API.

To use: copy this file to ~/.config/scrape-gateway/providers/
and edit to fit your API. sg will auto-discover it on the next run.

Verify with: sg providers
"""

from scrape_gateway import FailureReason, ProviderAdapter, ScrapeRequest, ScrapeResult


class MyApiProvider(ProviderAdapter):
    # Unique name — appears in sg output and scrape-gateway.yml
    name = "my_api"
    # Lower = tried first. Built-in free providers are 0-3, paid APIs are 25-40.
    cost_rank = 10
    # What this provider supports. Options: html, markdown, country, render_js, premium, screenshot
    capabilities = frozenset({"html"})

    def __init__(self, api_key: str | None = None) -> None:
        import os

        self.api_key = api_key or os.getenv("MY_API_KEY")

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        if not self.api_key:
            return ScrapeResult(
                request.url,
                self.name,
                False,
                error="Set MY_API_KEY in your environment",
                failure_reason=FailureReason.PROVIDER_ERROR,
            )

        import httpx

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    "https://api.example.com/scrape",
                    params={"url": request.url, "key": self.api_key},
                )
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=resp.is_success,
                status_code=resp.status_code,
                html=resp.text,
                route=self.name,
            )
        except Exception as exc:
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=False,
                error=str(exc),
                failure_reason=FailureReason.UNKNOWN,
            )
