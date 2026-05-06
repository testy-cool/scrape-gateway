from __future__ import annotations

from collections.abc import Iterable

from markdownify import markdownify as md

from .cache import ArtifactCache
from .memory import DomainMemory
from .models import ScrapeRequest, ScrapeResult
from .provider import ProviderAdapter
from .validators import validate_content


def _default_providers() -> list[ProviderAdapter]:
    from .providers import (
        RawHttpProvider,
        ScrapeDoProvider,
        ScrapeDriveProvider,
        ScraperApiProvider,
        ScrapingBeeProvider,
    )

    return [
        RawHttpProvider(),
        ScrapeDriveProvider(),
        ScrapeDoProvider(),
        ScrapingBeeProvider(),
        ScraperApiProvider(),
    ]


class ScrapeGateway:
    def __init__(
        self,
        providers: Iterable[ProviderAdapter] | None = None,
        cache: ArtifactCache | None = None,
        memory: DomainMemory | None = None,
    ) -> None:
        self.providers = list(providers if providers is not None else _default_providers())
        self.cache = cache or ArtifactCache()
        self.memory = memory or DomainMemory()

    async def scrape(self, request: ScrapeRequest, use_cache: bool = True) -> ScrapeResult:
        if use_cache:
            html = self.cache.get_html(request.url)
            if html:
                return ScrapeResult(
                    url=request.url,
                    provider="cache",
                    success=True,
                    html=html,
                    markdown=md(html),
                    cost_units=0,
                    route="cache",
                    metadata={"cache_hit": True},
                )

        ordered = self._ordered_providers(request)
        last_result: ScrapeResult | None = None
        for provider in ordered:
            if not provider.can_handle(request):
                continue
            if self.memory.should_skip_provider(request.url, provider.name):
                continue
            result = await provider.scrape(request)
            if result.success:
                validation = validate_content(result.html)
                result.content_validated = validation.passed
                result.block_type = validation.block_type
                result.validation_detail = validation.detail
                if not validation.passed:
                    result.success = False
                    self.memory.remember_failure(request.url, provider.name, validation.block_type)
                    last_result = result
                    continue
                if result.html and not result.markdown:
                    result.markdown = md(result.html)
                self.cache.save(result)
                self.memory.remember_success(
                    request.url,
                    provider.name,
                    request.country,
                    request.render_js,
                    request.premium,
                    tier=result.route,
                )
                return result
            self.memory.remember_failure(request.url, provider.name)
            last_result = result
        return last_result or ScrapeResult(
            request.url, "none", False, error="No provider could handle request"
        )

    def _ordered_providers(self, request: ScrapeRequest) -> list[ProviderAdapter]:
        preferred = self.memory.preferred_provider(request.url)
        providers = sorted(self.providers, key=lambda p: p.cost_rank)
        if preferred:
            providers = sorted(providers, key=lambda p: 0 if p.name == preferred else 1)
        return providers
