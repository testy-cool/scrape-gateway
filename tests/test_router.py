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
            html="<html><body>ok</body></html>",
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
    assert mem.preferred_provider("https://example.com/other") == "success"


async def test_preferred_provider_tried_first(tmp_dir):
    mem = DomainMemory(db_path=tmp_dir / "mem.sqlite")
    mem.remember_success("https://example.com", "success", None, False, False)

    call_order = []

    class TrackingSuccess(SuccessProvider):
        async def scrape(self, request):
            call_order.append(self.name)
            return await super().scrape(request)

    class TrackingFail(FailProvider):
        async def scrape(self, request):
            call_order.append(self.name)
            return await super().scrape(request)

    gw = ScrapeGateway(
        providers=[TrackingFail(), TrackingSuccess()],
        cache=ArtifactCache(root=tmp_dir / "cache"),
        memory=mem,
    )
    result = await gw.scrape(ScrapeRequest("https://example.com/page"), use_cache=False)
    assert result.success
    assert call_order[0] == "success"


async def test_no_providers_returns_error(tmp_dir):
    gw = ScrapeGateway(
        providers=[],
        cache=ArtifactCache(root=tmp_dir / "cache"),
        memory=DomainMemory(db_path=tmp_dir / "mem.sqlite"),
    )
    result = await gw.scrape(ScrapeRequest("https://example.com"), use_cache=False)
    assert not result.success
    assert result.provider == "none"
