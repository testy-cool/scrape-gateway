import json
import time

from scrape_gateway.cache import ArtifactCache
from scrape_gateway.config import load_config
from scrape_gateway.memory import DomainMemory
from scrape_gateway.models import ScrapeRequest, ScrapeResult
from scrape_gateway.provider import ProviderAdapter
from scrape_gateway.recipes import DomainRecipeStore
from scrape_gateway.router import ScrapeGateway
from scrape_gateway.telemetry import TelemetryRecorder


def _write_recipe(root, body: str) -> DomainRecipeStore:
    root.mkdir()
    (root / "shop.example.yml").write_text(body, encoding="utf-8")
    return DomainRecipeStore(root)


def test_recipe_store_loads_routes_validation_failure_patterns_and_ttl(tmp_path) -> None:
    store = _write_recipe(
        tmp_path / "recipes",
        """
domain: shop.example
routes:
  - provider: premium_api
    settings:
      country_code: US
      render_js: true
      scrape_tier: advanced
validators:
  min_text_chars: 500
  must_contain_any: [reviews, pricing]
failure_patterns:
  blocked: [temporarily unavailable, access denied]
ttl: 14d
""",
    )

    recipe = store.for_url("https://www.shop.example/products")

    assert recipe is not None
    assert recipe.domain == "shop.example"
    assert recipe.provider_names == ["premium_api"]
    assert recipe.route_settings("premium_api") == {
        "country_code": "US",
        "render_js": True,
        "scrape_tier": "advanced",
    }
    assert recipe.validation_kwargs == {
        "min_text_chars": 500,
        "must_contain_any": ["reviews", "pricing"],
        "must_not_contain": ["temporarily unavailable", "access denied"],
    }
    assert recipe.ttl_seconds == 14 * 86400


def test_config_resolves_recipe_directory_relative_to_the_config_file(tmp_path) -> None:
    config_path = tmp_path / "scrape-gateway.yml"
    config_path.write_text("recipes_root: domain-recipes\n", encoding="utf-8")

    config = load_config(config_path)

    assert config.recipes_root == str(tmp_path / "domain-recipes")


async def test_gateway_prefers_recipe_route_and_applies_settings_and_validation(tmp_path) -> None:
    calls = []

    class RecipeProvider(ProviderAdapter):
        name = "premium_api"
        cost_rank = 100
        capabilities = frozenset({"html", "render_js", "premium", "country"})

        async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
            calls.append(self.name)
            assert request.country == "US"
            assert request.render_js is True
            assert request.premium is True
            assert request.metadata["start_tier"] == "premium_api:advanced"
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=True,
                status_code=200,
                html="<html><body>A complete product page without the required section. "
                "It has enough ordinary content to pass the global minimum.</body></html>",
                route=self.name,
            )

    class FallbackProvider(ProviderAdapter):
        name = "fallback"
        cost_rank = 0
        capabilities = frozenset({"html", "render_js", "premium", "country"})

        async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
            calls.append(self.name)
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=True,
                status_code=200,
                html="<html><body>Customer reviews and pricing are present on this complete "
                "product page, so the domain-specific validator accepts it.</body></html>",
                route=self.name,
            )

    store = _write_recipe(
        tmp_path / "recipes",
        """
domain: shop.example
routes:
  - provider: premium_api
    settings:
      country_code: US
      render_js: true
      premium: true
      scrape_tier: advanced
validators:
  min_text_chars: 80
  must_contain_any: [reviews]
""",
    )
    gateway = ScrapeGateway(
        providers=[FallbackProvider(), RecipeProvider()],
        cache=ArtifactCache(tmp_path / "cache"),
        memory=DomainMemory(tmp_path / "memory.sqlite"),
        telemetry=TelemetryRecorder(enabled=False),
        recipes=store,
    )
    request = ScrapeRequest("https://shop.example/product")

    result = await gateway.scrape(request, use_cache=False, use_memory=False)

    assert result.success is True
    assert result.provider == "fallback"
    assert calls == ["premium_api", "fallback"]
    assert request.metadata["domain_recipe"] == "shop.example"


async def test_gateway_uses_recipe_ttl_for_cached_pages(tmp_path) -> None:
    class UnexpectedProvider(ProviderAdapter):
        name = "unexpected"

        async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
            raise AssertionError("the recipe TTL should keep this page cached")

    cache = ArtifactCache(tmp_path / "cache", ttl_seconds=1)
    url = "https://shop.example/product"
    cache.save(
        ScrapeResult(
            url=url,
            provider="raw_http",
            success=True,
            html="<html><body>Cached product content remains fresh for this domain.</body></html>",
        )
    )
    meta_path = cache.paths_for_url(url)["meta"]
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    metadata["fetched_at"] = time.time() - (10 * 86400)
    meta_path.write_text(json.dumps(metadata), encoding="utf-8")

    store = _write_recipe(
        tmp_path / "recipes",
        """
domain: shop.example
ttl: 14d
""",
    )
    gateway = ScrapeGateway(
        providers=[UnexpectedProvider()],
        cache=cache,
        memory=DomainMemory(tmp_path / "memory.sqlite"),
        telemetry=TelemetryRecorder(enabled=False),
        recipes=store,
    )

    result = await gateway.scrape(ScrapeRequest(url))

    assert result.success is True
    assert result.provider == "cache"
