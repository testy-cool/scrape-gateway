import tempfile
from pathlib import Path

import pytest

from scrape_gateway.cache import ArtifactCache
from scrape_gateway.memory import DomainMemory
from scrape_gateway.models import FailureReason, ScrapeRequest, ScrapeResult
from scrape_gateway.provider import ProviderAdapter
from scrape_gateway.router import ScrapeGateway


class SuccessProvider(ProviderAdapter):
    name = "success"
    cost_rank = 10
    capabilities = frozenset({"html"})

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        return ScrapeResult(
            url=request.url,
            provider=self.name,
            success=True,
            status_code=200,
            html="<html><body><h1>Example Product</h1><p>This is a real product page with enough content to pass validation checks.</p></body></html>",
            route="success",
        )


class FailProvider(ProviderAdapter):
    name = "fail"
    cost_rank = 5
    capabilities = frozenset({"html"})

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        return ScrapeResult(
            url=request.url,
            provider=self.name,
            success=False,
            status_code=403,
            failure_reason=FailureReason.HTTP_403,
            route="fail",
        )


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


async def test_routes_to_first_success(tmp_dir):
    gw = ScrapeGateway(
        providers=[FailProvider(), SuccessProvider()],
        cache=ArtifactCache(root=tmp_dir / "cache"),
        memory=DomainMemory(db_path=tmp_dir / "mem.sqlite"),
    )
    result = await gw.scrape(ScrapeRequest("https://example.com"), use_cache=False)
    assert result.success
    assert result.provider == "success"


async def test_returns_last_failure_when_all_fail(tmp_dir):
    gw = ScrapeGateway(
        providers=[FailProvider()],
        cache=ArtifactCache(root=tmp_dir / "cache"),
        memory=DomainMemory(db_path=tmp_dir / "mem.sqlite"),
    )
    result = await gw.scrape(ScrapeRequest("https://example.com"), use_cache=False)
    assert not result.success
    assert result.provider == "fail"


async def test_cache_hit(tmp_dir):
    cache = ArtifactCache(root=tmp_dir / "cache")
    cache.save(
        ScrapeResult(
            url="https://cached.com",
            provider="prior",
            success=True,
            html="<html>cached</html>",
        )
    )
    gw = ScrapeGateway(
        providers=[FailProvider()],
        cache=cache,
        memory=DomainMemory(db_path=tmp_dir / "mem.sqlite"),
    )
    result = await gw.scrape(ScrapeRequest("https://cached.com"))
    assert result.success
    assert result.provider == "cache"


async def test_remembers_successful_provider(tmp_dir):
    mem = DomainMemory(db_path=tmp_dir / "mem.sqlite")
    gw = ScrapeGateway(
        providers=[FailProvider(), SuccessProvider()],
        cache=ArtifactCache(root=tmp_dir / "cache"),
        memory=mem,
    )
    await gw.scrape(ScrapeRequest("https://example.com"), use_cache=False)
    assert mem.preferred_provider("https://example.com/other") == ("success", "success")


async def test_preferred_provider_tried_first(tmp_dir):
    mem = DomainMemory(db_path=tmp_dir / "mem.sqlite")
    mem.remember_success("https://example.com", "success", None, False, False, tier="success")

    call_order = []

    class TrackingSuccess(SuccessProvider):
        async def scrape(self, request):
            call_order.append(self.name)
            return await super().scrape(request)

    class ExpensiveFail(ProviderAdapter):
        name = "expensive_fail"
        cost_rank = 50
        capabilities = frozenset({"html"})

        async def scrape(self, request):
            call_order.append(self.name)
            return ScrapeResult(
                url=request.url, provider=self.name, success=False, status_code=500,
                failure_reason=FailureReason.HTTP_5XX,
            )

    gw = ScrapeGateway(
        providers=[ExpensiveFail(), TrackingSuccess()],
        cache=ArtifactCache(root=tmp_dir / "cache"),
        memory=mem,
    )
    result = await gw.scrape(ScrapeRequest("https://example.com/page"), use_cache=False)
    assert result.success
    assert call_order[0] == "success"


class CloudflareProvider(ProviderAdapter):
    """Returns 200 OK but with a Cloudflare challenge page."""

    name = "cloudflare_trap"
    cost_rank = 1
    capabilities = frozenset({"html"})

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        return ScrapeResult(
            url=request.url,
            provider=self.name,
            success=True,
            status_code=200,
            html="<html><body>Checking your browser before accessing the site. Ray ID: abc123</body></html>"
            + "x" * 200,
            route="cloudflare_trap",
        )


async def test_validator_rejects_block_page_and_escalates(tmp_dir):
    gw = ScrapeGateway(
        providers=[CloudflareProvider(), SuccessProvider()],
        cache=ArtifactCache(root=tmp_dir / "cache"),
        memory=DomainMemory(db_path=tmp_dir / "mem.sqlite"),
    )
    result = await gw.scrape(ScrapeRequest("https://example.com"), use_cache=False)
    assert result.success
    assert result.provider == "success"
    assert result.content_validated is True


async def test_validator_marks_block_type(tmp_dir):
    gw = ScrapeGateway(
        providers=[CloudflareProvider()],
        cache=ArtifactCache(root=tmp_dir / "cache"),
        memory=DomainMemory(db_path=tmp_dir / "mem.sqlite"),
    )
    result = await gw.scrape(ScrapeRequest("https://example.com"), use_cache=False)
    assert not result.success
    assert result.block_type == "cloudflare"
    assert result.content_validated is False


async def test_no_providers_returns_error(tmp_dir):
    gw = ScrapeGateway(
        providers=[],
        cache=ArtifactCache(root=tmp_dir / "cache"),
        memory=DomainMemory(db_path=tmp_dir / "mem.sqlite"),
    )
    result = await gw.scrape(ScrapeRequest("https://example.com"), use_cache=False)
    assert not result.success
    assert result.provider == "none"


class CheapProvider(ProviderAdapter):
    name = "cheap"
    cost_rank = 1
    capabilities = frozenset({"html"})

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        return ScrapeResult(
            url=request.url,
            provider=self.name,
            success=True,
            status_code=200,
            html="<html><body><h1>Cheap</h1><p>This is cheap provider content with enough chars to pass validation.</p></body></html>",
            route="cheap",
        )


async def test_skips_providers_cheaper_than_preferred(tmp_dir):
    mem = DomainMemory(db_path=tmp_dir / "mem.sqlite")
    mem.remember_success("https://example.com", "success", None, False, False, tier="success")

    call_order = []

    class TrackingCheap(CheapProvider):
        async def scrape(self, request):
            call_order.append(self.name)
            return await super().scrape(request)

    class TrackingSuccess(SuccessProvider):
        async def scrape(self, request):
            call_order.append(self.name)
            return await super().scrape(request)

    gw = ScrapeGateway(
        providers=[TrackingCheap(), TrackingSuccess()],
        cache=ArtifactCache(root=tmp_dir / "cache"),
        memory=mem,
    )
    result = await gw.scrape(ScrapeRequest("https://example.com/page"), use_cache=False)
    assert result.success
    assert result.provider == "success"
    assert "cheap" not in call_order
