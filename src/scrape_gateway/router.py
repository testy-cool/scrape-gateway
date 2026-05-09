from __future__ import annotations

import json
import logging
import re
import sys
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlparse

from markdownify import markdownify as md

from .cache import ArtifactCache
from .config import GatewayConfig, StrategyConfig, load_config
from .memory import DomainMemory
from .models import ScrapeRequest, ScrapeResult
from .provider import ProviderAdapter
from .validators import validate_content

LOG_DIR = Path(".scrape-gateway")
LOG_FILE = LOG_DIR / "scrape.log"

logger = logging.getLogger("scrape_gateway")


def _init_logger() -> None:
    if logger.handlers:
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(LOG_FILE, maxBytes=5_000_000, backupCount=3)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def _log_event(event: str, **data) -> None:
    _init_logger()
    entry = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **data}
    logger.info(json.dumps(entry, default=str))

CCTLD_TO_COUNTRY = {
    "ro": "RO", "de": "DE", "fr": "FR", "es": "ES", "it": "IT", "nl": "NL",
    "pt": "PT", "pl": "PL", "cz": "CZ", "at": "AT", "ch": "CH", "be": "BE",
    "se": "SE", "no": "NO", "dk": "DK", "fi": "FI", "ie": "IE", "hu": "HU",
    "bg": "BG", "hr": "HR", "sk": "SK", "si": "SI", "lt": "LT", "lv": "LV",
    "ee": "EE", "gr": "GR", "cy": "CY", "mt": "MT", "lu": "LU",
    "uk": "GB", "co.uk": "GB", "jp": "JP", "kr": "KR", "cn": "CN",
    "in": "IN", "br": "BR", "mx": "MX", "ar": "AR", "cl": "CL",
    "au": "AU", "nz": "NZ", "ca": "CA", "za": "ZA", "ru": "RU",
    "tr": "TR", "ua": "UA", "il": "IL", "ae": "AE", "sg": "SG",
    "th": "TH", "vn": "VN", "my": "MY", "ph": "PH", "id": "ID",
    "tw": "TW", "hk": "HK",
}

_HREFLANG_RE = re.compile(
    r'<link[^>]+hreflang=["\']([^"\']+)["\'][^>]+href=["\']([^"\']+)["\']',
    re.IGNORECASE,
)


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _country_from_tld(url: str) -> str | None:
    host = urlparse(url).hostname or ""
    parts = host.rsplit(".", 2)
    if len(parts) >= 3:
        two_part = f"{parts[-2]}.{parts[-1]}"
        if two_part in CCTLD_TO_COUNTRY:
            return CCTLD_TO_COUNTRY[two_part]
    tld = parts[-1] if parts else ""
    return CCTLD_TO_COUNTRY.get(tld)


def _check_hreflang(html: str, url: str, country: str | None) -> dict | None:
    matches = _HREFLANG_RE.findall(html)
    if not matches:
        return None
    hreflangs = {lang.lower(): href for lang, href in matches}
    result = {"hreflangs": hreflangs}
    if country:
        cc = country.lower()
        matching = [lang for lang in hreflangs if cc in lang.split("-")]
        if matching:
            result["country_match"] = True
            if hreflangs[matching[0]] != url:
                result["canonical_url"] = hreflangs[matching[0]]
        else:
            result["country_match"] = False
            available = [lang for lang in hreflangs if lang != "x-default"]
            if available:
                result["available_countries"] = available
    return result

def _default_providers() -> list[ProviderAdapter]:
    from .discovery import discover_providers

    return [cls() for cls in discover_providers().values()]


def _providers_from_config(config: GatewayConfig) -> list[ProviderAdapter]:
    if not config.providers:
        return _default_providers()

    from .discovery import discover_providers

    available = discover_providers()
    result = []
    for pc in config.providers:
        if not pc.enabled:
            continue
        cls = available.get(pc.name)
        if not cls:
            _log(f"  [config] unknown provider: {pc.name}")
            continue
        result.append(cls(**pc.options))
    return result


class ScrapeGateway:
    def __init__(
        self,
        providers: Iterable[ProviderAdapter] | None = None,
        cache: ArtifactCache | None = None,
        memory: DomainMemory | None = None,
        strategy: StrategyConfig | None = None,
    ) -> None:
        self.providers = list(providers if providers is not None else _default_providers())
        self.cache = cache or ArtifactCache()
        self.memory = memory or DomainMemory()
        self.strategy = strategy or StrategyConfig()

    @classmethod
    def from_config(cls, config: GatewayConfig | None = None) -> ScrapeGateway:
        config = config or load_config()
        return cls(
            providers=_providers_from_config(config),
            cache=ArtifactCache(root=config.cache.root, ttl_seconds=config.cache.ttl_seconds),
            memory=DomainMemory(db_path=config.memory_path),
            strategy=config.strategy,
        )

    async def scrape(self, request: ScrapeRequest, use_cache: bool = True, use_memory: bool = True) -> ScrapeResult:
        if not request.url.startswith(("http://", "https://")):
            request.url = f"https://{request.url}"
        _log(f"\nscrape {request.url}")
        scrape_start = time.perf_counter()
        req_data = {
            "url": request.url, "country": request.country, "render_js": request.render_js,
            "premium": request.premium, "mobile": request.mobile, "wait_event": request.wait_event,
            "wait_selector": request.wait_selector, "output_format": request.output_format,
        }

        if not request.country:
            detected = _country_from_tld(request.url)
            if detected:
                request.country = detected
                req_data["country"] = detected
                req_data["country_source"] = "tld"
                _log(f"  [auto-country] {detected} (from TLD)")

        _log_event("scrape_start", **req_data)

        if use_cache:
            html = self.cache.get_html(request.url)
            if html:
                _log("  [cache] HIT")
                _log_event("cache_hit", url=request.url)
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
        attempts = []
        skipped = []
        last_result: ScrapeResult | None = None
        for provider in ordered:
            if not provider.can_handle(request):
                skipped.append(f"{provider.name}(no capability)")
                continue
            if use_memory and self.memory.should_skip_provider(request.url, provider.name):
                skipped.append(f"{provider.name}(bad history)")
                continue
            start = time.perf_counter()
            result = await provider.scrape(request)
            elapsed = time.perf_counter() - start
            attempt = {
                "provider": provider.name, "status": result.status_code,
                "elapsed_ms": int(elapsed * 1000), "route": result.route,
                "cost": result.cost_units,
            }
            if result.success:
                validation = validate_content(result.html)
                result.content_validated = validation.passed
                result.block_type = validation.block_type
                result.validation_detail = validation.detail
                if not validation.passed:
                    result.success = False
                    self.memory.remember_failure(request.url, provider.name, validation.block_type)
                    attempt["result"] = "validation_failed"
                    attempt["block_type"] = validation.block_type
                    attempts.append(attempt)
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
                if result.html:
                    hreflang = _check_hreflang(result.html, request.url, request.country)
                    if hreflang:
                        result.metadata["hreflang"] = hreflang
                        if hreflang.get("country_match") is False:
                            avail = hreflang.get("available_countries", [])
                            _log(f"  [hreflang] country {request.country} not in page alternatives: {', '.join(avail)}")
                        elif hreflang.get("canonical_url"):
                            _log(f"  [hreflang] canonical for {request.country}: {hreflang['canonical_url']}")
                    changes = self.memory.record_scrape(request.url, result.html, provider.name)
                    if changes and changes != ["no changes"]:
                        result.metadata["changes"] = changes
                        _log(f"  [history] {'; '.join(changes)}")
                    elif changes == ["no changes"]:
                        _log("  [history] no changes since last scrape")
                attempt["result"] = "success"
                attempt["chars"] = len(result.html or "")
                attempts.append(attempt)
                _log(f"  [{provider.name}] {result.status_code} {elapsed:.1f}s → ✓ pass")
                _log_event("scrape_done", url=request.url, success=True,
                           provider=provider.name, route=result.route, cost=result.cost_units,
                           chars=len(result.html or ""), elapsed_ms=int((time.perf_counter() - scrape_start) * 1000),
                           attempts=attempts, skipped=skipped)
                return result
            reason = result.failure_reason.value if result.failure_reason else result.error or "failed"
            attempt["result"] = "failed"
            attempt["reason"] = reason
            if result.error:
                attempt["error"] = result.error
            attempts.append(attempt)
            _log(f"  [{provider.name}] {result.status_code or 'ERR'} {elapsed:.1f}s → ✗ {reason}")
            self.memory.remember_failure(request.url, provider.name)
            last_result = result

        if skipped:
            _log(f"  [skip] {', '.join(skipped)}")
        if not last_result:
            _log("  [result] no provider could handle request")

        final = last_result or ScrapeResult(
            request.url, "none", False, error="No provider could handle request"
        )
        _log_event("scrape_done", url=request.url, success=False,
                   provider=final.provider, error=final.error,
                   elapsed_ms=int((time.perf_counter() - scrape_start) * 1000),
                   attempts=attempts, skipped=skipped)
        return final

    def _ordered_providers(self, request: ScrapeRequest) -> list[ProviderAdapter]:
        providers = sorted(self.providers, key=lambda p: p.cost_rank)

        if self.strategy.provider:
            preferred = [p for p in providers if p.name == self.strategy.provider]
            rest = [p for p in providers if p.name != self.strategy.provider]
            if preferred:
                _log(f"  [strategy] preferred provider: {self.strategy.provider}")
                return preferred + rest
            _log(f"  [strategy] preferred provider {self.strategy.provider!r} not found, falling back")

        pref = self.memory.preferred_provider(request.url)
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
