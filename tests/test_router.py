import json
import tempfile
from pathlib import Path

import pytest

from scrape_gateway.cache import ArtifactCache
from scrape_gateway.memory import DomainMemory
from scrape_gateway.models import FailureReason, ScrapeRequest, ScrapeResult
from scrape_gateway.provider import ProviderAdapter
from scrape_gateway.progress import observe_progress
from scrape_gateway.router import ScrapeGateway
from scrape_gateway.telemetry import TelemetryRecorder


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


async def test_reports_provider_validation_evaluation_and_persistence_progress(tmp_dir):
    events = []
    gw = ScrapeGateway(
        providers=[SuccessProvider()],
        cache=ArtifactCache(root=tmp_dir / "cache"),
        memory=DomainMemory(db_path=tmp_dir / "mem.sqlite"),
        telemetry=TelemetryRecorder(root=tmp_dir / "runs"),
    )

    with observe_progress(events.append):
        result = await gw.scrape(ScrapeRequest("https://example.com"), use_cache=False)

    assert result.success is True
    assert [event["id"] for event in events] == [
        "routing",
        "provider-1",
        "provider-1",
        "validation-1",
        "evaluation",
        "persistence",
        "persistence",
    ]
    assert events[1]["status"] == "running"
    assert events[2]["status"] == "ok"
    assert events[2]["attributes"]["screenshot_bytes"] == 0
    assert events[3]["outcome"] == "passed"
    assert events[-1]["outcome"] == "saved"


async def test_applies_global_and_per_provider_timeout_defaults(tmp_dir):
    observed = []

    class TimedFailure(FailProvider):
        name = "timed_failure"

        async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
            observed.append((self.name, request.timeout_seconds))
            return await super().scrape(request)

    class TimedSuccess(SuccessProvider):
        name = "timed_success"

        async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
            observed.append((self.name, request.timeout_seconds))
            return await super().scrape(request)

    gw = ScrapeGateway(
        providers=[TimedFailure(), TimedSuccess()],
        cache=ArtifactCache(root=tmp_dir / "cache"),
        memory=DomainMemory(db_path=tmp_dir / "mem.sqlite"),
        default_timeout_seconds=31,
        provider_timeouts={"timed_failure": 7},
    )

    result = await gw.scrape(ScrapeRequest("https://example.com"), use_cache=False)

    assert result.success is True
    assert observed == [("timed_failure", 7), ("timed_success", 31)]


async def test_returns_last_failure_when_all_fail(tmp_dir):
    gw = ScrapeGateway(
        providers=[FailProvider()],
        cache=ArtifactCache(root=tmp_dir / "cache"),
        memory=DomainMemory(db_path=tmp_dir / "mem.sqlite"),
    )
    result = await gw.scrape(ScrapeRequest("https://example.com"), use_cache=False)
    assert not result.success
    assert result.provider == "fail"


async def test_proxy_error_stops_escalation(tmp_dir):
    call_order = []

    class ProxyFailProvider(ProviderAdapter):
        name = "proxy_fail"
        cost_rank = 0
        capabilities = frozenset({"html"})

        async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
            call_order.append(self.name)
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=False,
                error="407 Proxy Authentication Required",
                failure_reason=FailureReason.PROXY_ERROR,
                route="proxy_fail",
            )

    class ExpensiveProvider(SuccessProvider):
        name = "expensive"
        cost_rank = 50

        async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
            call_order.append(self.name)
            return await super().scrape(request)

    gw = ScrapeGateway(
        providers=[ProxyFailProvider(), ExpensiveProvider()],
        cache=ArtifactCache(root=tmp_dir / "cache"),
        memory=DomainMemory(db_path=tmp_dir / "mem.sqlite"),
    )
    result = await gw.scrape(ScrapeRequest("https://example.com"), use_cache=False)
    assert not result.success
    assert result.failure_reason == FailureReason.PROXY_ERROR
    assert call_order == ["proxy_fail"]


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


async def test_cache_hit_restores_requested_screenshot(tmp_dir):
    cache = ArtifactCache(root=tmp_dir / "cache")
    cache.save(
        ScrapeResult(
            url="https://cached.com",
            provider="browserless",
            success=True,
            html="<html>cached with visual evidence</html>",
            screenshot=b"cached-screenshot",
            route="browserless:content+screenshot",
        )
    )
    gw = ScrapeGateway(
        providers=[FailProvider()],
        cache=cache,
        memory=DomainMemory(db_path=tmp_dir / "mem.sqlite"),
    )

    result = await gw.scrape(ScrapeRequest("https://cached.com", screenshot=True))

    assert result.success is True
    assert result.provider == "cache"
    assert result.screenshot == b"cached-screenshot"
    assert result.metadata["cache_source_provider"] == "browserless"


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
                url=request.url,
                provider=self.name,
                success=False,
                status_code=500,
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


async def test_screenshot_only_result_is_not_rejected_as_empty_html(tmp_dir):
    class ScreenshotProvider(ProviderAdapter):
        name = "screenshot"
        capabilities = frozenset({"screenshot"})

        async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=True,
                status_code=200,
                screenshot=b"\x89PNG\r\n\x1a\nimage-bytes",
                route="screenshot:screenshot",
            )

    gw = ScrapeGateway(
        providers=[ScreenshotProvider()],
        cache=ArtifactCache(root=tmp_dir / "cache"),
        memory=DomainMemory(db_path=tmp_dir / "mem.sqlite"),
    )
    result = await gw.scrape(
        ScrapeRequest("https://example.com", screenshot=True),
        use_cache=False,
        use_memory=False,
    )

    assert result.success is True
    assert result.screenshot
    assert result.content_validated is None


async def test_telemetry_report_records_validation_evidence(tmp_dir):
    gw = ScrapeGateway(
        providers=[CloudflareProvider()],
        cache=ArtifactCache(root=tmp_dir / "cache"),
        memory=DomainMemory(db_path=tmp_dir / "mem.sqlite"),
        telemetry=TelemetryRecorder(root=tmp_dir / "runs"),
    )
    result = await gw.scrape(ScrapeRequest("https://example.com"), use_cache=False)
    report_path = Path(result.metadata["telemetry_report"])
    report = json.loads(report_path.read_text())
    assert report["run_id"] == result.metadata["run_id"]
    assert report["diagnosis"] == "validator_rejected"
    assert report["recommended_next_action"] == "inspect_validator_evidence_or_try_render_js"
    assert report["attempts"][0]["matched_pattern"] == "checking your browser"
    assert report["attempts"][0]["snippet"]


async def test_debug_artifacts_save_failed_html(tmp_dir):
    gw = ScrapeGateway(
        providers=[CloudflareProvider()],
        cache=ArtifactCache(root=tmp_dir / "cache"),
        memory=DomainMemory(db_path=tmp_dir / "mem.sqlite"),
        telemetry=TelemetryRecorder(root=tmp_dir / "runs"),
    )
    result = await gw.scrape(
        ScrapeRequest("https://example.com", metadata={"debug_artifacts": True}),
        use_cache=False,
    )
    report = json.loads(Path(result.metadata["telemetry_report"]).read_text())
    artifact_path = Path(report["attempts"][0]["artifact_path"])
    assert artifact_path.exists()
    assert "Checking your browser" in artifact_path.read_text()


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


async def test_tier_escalation_full_flow(tmp_dir):
    """After ScrapeDrive succeeds at 'advanced', next scrape skips cheap providers
    and tells ScrapeDrive to start at 'advanced'."""
    mem = DomainMemory(db_path=tmp_dir / "mem.sqlite")

    class FakeScrapeDrive(ProviderAdapter):
        name = "scrapedrive"
        cost_rank = 25
        capabilities = frozenset({"html"})

        async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
            tier = request.metadata.get("start_tier", "")
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=True,
                status_code=200,
                html="<html><body><h1>Real page</h1><p>Plenty of real content here to pass validation.</p></body></html>",
                route=f"scrapedrive:{tier.split(':')[1] if ':' in tier else 'standard'}",
                metadata={"tier_used": tier},
            )

    class CheapBlocked(ProviderAdapter):
        name = "raw_http"
        cost_rank = 0
        capabilities = frozenset({"html"})
        call_count = 0

        async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
            self.call_count += 1
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=True,
                status_code=200,
                html="<html><body>Checking your browser. Ray ID: x</body></html>" + "x" * 200,
                route="raw_http",
            )

    cheap = CheapBlocked()
    sd = FakeScrapeDrive()

    gw = ScrapeGateway(
        providers=[cheap, sd],
        cache=ArtifactCache(root=tmp_dir / "cache"),
        memory=mem,
    )

    # First scrape: raw_http gets blocked, scrapedrive succeeds
    r1 = await gw.scrape(ScrapeRequest("https://hard.com/page1"), use_cache=False)
    assert r1.success
    assert r1.provider == "scrapedrive"
    assert cheap.call_count == 1

    # Second scrape: should skip raw_http entirely, go straight to scrapedrive with tier hint
    r2 = await gw.scrape(ScrapeRequest("https://hard.com/page2"), use_cache=False)
    assert r2.success
    assert r2.provider == "scrapedrive"
    assert cheap.call_count == 1  # NOT called again
    assert r2.metadata.get("tier_used") == "scrapedrive:standard"  # tier was passed through


class HeaderCapture(ProviderAdapter):
    name = "header_capture"
    cost_rank = 0
    capabilities = frozenset({"html"})

    def __init__(self):
        self.captured_headers: dict[str, str] = {}

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        self.captured_headers = dict(request.headers)
        return ScrapeResult(
            url=request.url,
            provider=self.name,
            success=True,
            status_code=200,
            html="<html><body><h1>Example</h1><p>Enough content to pass validation.</p></body></html>",
            route="header_capture",
        )


async def test_auto_referer_from_pool(tmp_dir):
    cap = HeaderCapture()
    gw = ScrapeGateway(
        providers=[cap],
        cache=ArtifactCache(root=tmp_dir / "cache"),
        memory=DomainMemory(db_path=tmp_dir / "mem.sqlite"),
    )
    await gw.scrape(ScrapeRequest("https://example.com/page"), use_cache=False)
    ref = cap.captured_headers["Referer"]
    assert any(
        ref.startswith(prefix)
        for prefix in [
            "https://www.google.com/",
            "https://www.bing.com/",
            "https://duckduckgo.com/",
            "https://t.co/",
            "https://www.reddit.com/",
        ]
    )


async def test_browser_headers_applied(tmp_dir):
    cap = HeaderCapture()
    gw = ScrapeGateway(
        providers=[cap],
        cache=ArtifactCache(root=tmp_dir / "cache"),
        memory=DomainMemory(db_path=tmp_dir / "mem.sqlite"),
    )
    await gw.scrape(ScrapeRequest("https://example.com"), use_cache=False)
    h = cap.captured_headers
    assert h["Sec-Fetch-Dest"] == "document"
    assert h["Sec-Fetch-Mode"] == "navigate"
    assert h["Sec-Fetch-Site"] == "cross-site"
    assert h["Sec-Fetch-User"] == "?1"
    assert h["Upgrade-Insecure-Requests"] == "1"
    assert "text/html" in h["Accept"]
    assert "en" in h["Accept-Language"]


async def test_sec_fetch_site_cross_vs_none(tmp_dir):
    cap = HeaderCapture()
    gw = ScrapeGateway(
        providers=[cap],
        cache=ArtifactCache(root=tmp_dir / "cache"),
        memory=DomainMemory(db_path=tmp_dir / "mem.sqlite"),
    )
    await gw.scrape(ScrapeRequest("https://example.com", referer=""), use_cache=False)
    assert cap.captured_headers["Sec-Fetch-Site"] == "none"


async def test_browser_headers_dont_override_explicit(tmp_dir):
    cap = HeaderCapture()
    gw = ScrapeGateway(
        providers=[cap],
        cache=ArtifactCache(root=tmp_dir / "cache"),
        memory=DomainMemory(db_path=tmp_dir / "mem.sqlite"),
    )
    await gw.scrape(
        ScrapeRequest("https://example.com", headers={"Accept-Language": "ro-RO"}),
        use_cache=False,
    )
    assert cap.captured_headers["Accept-Language"] == "ro-RO"
    assert "Sec-Fetch-Dest" in cap.captured_headers


async def test_custom_referer(tmp_dir):
    cap = HeaderCapture()
    gw = ScrapeGateway(
        providers=[cap],
        cache=ArtifactCache(root=tmp_dir / "cache"),
        memory=DomainMemory(db_path=tmp_dir / "mem.sqlite"),
    )
    await gw.scrape(
        ScrapeRequest("https://example.com", referer="https://reddit.com/r/python"),
        use_cache=False,
    )
    assert cap.captured_headers["Referer"] == "https://reddit.com/r/python"


async def test_empty_referer_disables_auto(tmp_dir):
    cap = HeaderCapture()
    gw = ScrapeGateway(
        providers=[cap],
        cache=ArtifactCache(root=tmp_dir / "cache"),
        memory=DomainMemory(db_path=tmp_dir / "mem.sqlite"),
    )
    await gw.scrape(ScrapeRequest("https://example.com", referer=""), use_cache=False)
    assert "Referer" not in cap.captured_headers


async def test_explicit_header_overrides_auto_referer(tmp_dir):
    cap = HeaderCapture()
    gw = ScrapeGateway(
        providers=[cap],
        cache=ArtifactCache(root=tmp_dir / "cache"),
        memory=DomainMemory(db_path=tmp_dir / "mem.sqlite"),
    )
    await gw.scrape(
        ScrapeRequest("https://example.com", headers={"Referer": "https://bing.com"}),
        use_cache=False,
    )
    assert cap.captured_headers["Referer"] == "https://bing.com"
