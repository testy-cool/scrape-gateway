from __future__ import annotations

import sys
import time
from collections.abc import Iterable

from markdownify import markdownify as md

from .cache import ArtifactCache
from .config import GatewayConfig, load_config
from .memory import DomainMemory
from .models import ScrapeRequest, ScrapeResult
from .provider import ProviderAdapter
from .validators import validate_content


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)

PROVIDER_CLASSES: dict[str, str] = {
    "raw_http": "RawHttpProvider",
    "wreq": "WreqProvider",
    "curl_cffi": "CurlCffiProvider",
    "scrapedrive": "ScrapeDriveProvider",
    "scrape_do": "ScrapeDoProvider",
    "scrapingbee": "ScrapingBeeProvider",
    "scraperapi": "ScraperApiProvider",
}


def _default_providers() -> list[ProviderAdapter]:
    from .providers import (
        CurlCffiProvider,
        RawHttpProvider,
        ScrapeDoProvider,
        ScrapeDriveProvider,
        ScraperApiProvider,
        ScrapingBeeProvider,
        WreqProvider,
    )

    return [
        RawHttpProvider(),
        WreqProvider(),
        CurlCffiProvider(),
        ScrapeDriveProvider(),
        ScrapeDoProvider(),
        ScrapingBeeProvider(),
        ScraperApiProvider(),
    ]


def _providers_from_config(config: GatewayConfig) -> list[ProviderAdapter]:
    if not config.providers:
        return _default_providers()

    import importlib

    module = importlib.import_module(".providers", package="scrape_gateway")
    result = []
    for pc in config.providers:
        if not pc.enabled:
            continue
        class_name = PROVIDER_CLASSES.get(pc.name)
        if not class_name:
            continue
        cls = getattr(module, class_name)
        result.append(cls(**pc.options))
    return result


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

    @classmethod
    def from_config(cls, config: GatewayConfig | None = None) -> ScrapeGateway:
        config = config or load_config()
        return cls(
            providers=_providers_from_config(config),
            cache=ArtifactCache(root=config.cache.root, ttl_seconds=config.cache.ttl_seconds),
            memory=DomainMemory(db_path=config.memory_path),
        )

    async def scrape(self, request: ScrapeRequest, use_cache: bool = True) -> ScrapeResult:
        _log(f"\nscrape {request.url}")

        if use_cache:
            html = self.cache.get_html(request.url)
            if html:
                _log("  [cache] HIT")
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
            _log("  [cache] MISS")

        ordered = self._ordered_providers(request)
        skipped = []
        last_result: ScrapeResult | None = None
        for provider in ordered:
            if not provider.can_handle(request):
                skipped.append(f"{provider.name}(no capability)")
                continue
            if self.memory.should_skip_provider(request.url, provider.name):
                skipped.append(f"{provider.name}(bad history)")
                continue
            start = time.perf_counter()
            result = await provider.scrape(request)
            elapsed = time.perf_counter() - start
            if result.success:
                validation = validate_content(result.html)
                result.content_validated = validation.passed
                result.block_type = validation.block_type
                result.validation_detail = validation.detail
                if not validation.passed:
                    result.success = False
                    self.memory.remember_failure(request.url, provider.name, validation.block_type)
                    _log(f"  [{provider.name}] {result.status_code} {elapsed:.1f}s → ✗ {validation.block_type or 'failed'}")
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
                _log(f"  [{provider.name}] {result.status_code} {elapsed:.1f}s → ✓ pass")
                return result
            reason = result.failure_reason.value if result.failure_reason else result.error or "failed"
            _log(f"  [{provider.name}] {result.status_code or 'ERR'} {elapsed:.1f}s → ✗ {reason}")
            self.memory.remember_failure(request.url, provider.name)
            last_result = result

        if skipped:
            _log(f"  [skip] {', '.join(skipped)}")
        if not last_result:
            _log("  [result] no provider could handle request")
        return last_result or ScrapeResult(
            request.url, "none", False, error="No provider could handle request"
        )

    def _ordered_providers(self, request: ScrapeRequest) -> list[ProviderAdapter]:
        pref = self.memory.preferred_provider(request.url)
        providers = sorted(self.providers, key=lambda p: p.cost_rank)
        if pref:
            pref_name, pref_tier = pref
            pref_cost = next((p.cost_rank for p in providers if p.name == pref_name), None)
            if pref_cost is not None:
                skipped_names = [p.name for p in providers if p.cost_rank < pref_cost]
                providers = [p for p in providers if p.cost_rank >= pref_cost]
                providers = sorted(providers, key=lambda p: 0 if p.name == pref_name else 1)
                tier_info = f" ({pref_tier})" if pref_tier else ""
                skip_info = f", skip {'/'.join(skipped_names)}" if skipped_names else ""
                _log(f"  [memory] prefer {pref_name}{tier_info}{skip_info}")
            if pref_tier:
                request.metadata["start_tier"] = pref_tier
        else:
            _log("  [memory] no history")
        return providers
