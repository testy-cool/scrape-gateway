import json
import tempfile
from pathlib import Path

import httpx
import pytest
import respx

from scrape_gateway.cache import ArtifactCache
from scrape_gateway.config import EvaluationConfig, GatewayConfig
from scrape_gateway.memory import DomainMemory
from scrape_gateway.models import AttemptLedgerEntry, FailureReason, ScrapeRequest, ScrapeResult
from scrape_gateway.provider import ProviderAdapter
from scrape_gateway.progress import observe_progress
from scrape_gateway.providers.scrapfly import ScrapflyProvider
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


def test_default_gateway_state_is_isolated_to_pytest_tmp_path(tmp_path):
    gateway = ScrapeGateway.from_config(GatewayConfig(evaluation=EvaluationConfig(mode="audit")))
    state_paths = [
        gateway.cache.root,
        gateway.memory.db_path,
        gateway.telemetry.root,
        gateway.evaluator.config.cache_root,
    ]
    gateway.memory.conn.close()

    assert all(Path(path).resolve().is_relative_to(tmp_path) for path in state_paths)


async def test_routes_to_first_success(tmp_dir):
    gw = ScrapeGateway(
        providers=[FailProvider(), SuccessProvider()],
        cache=ArtifactCache(root=tmp_dir / "cache"),
        memory=DomainMemory(db_path=tmp_dir / "mem.sqlite"),
    )
    result = await gw.scrape(ScrapeRequest("https://example.com"), use_cache=False)
    assert result.success
    assert result.provider == "success"


async def test_router_persists_combined_fallback_ledger_with_run_and_request_profile(tmp_path):
    class EstimatedFailure(ProviderAdapter):
        name = "estimated_failure"
        cost_rank = 1
        capabilities = frozenset({"html", "render_js", "premium", "screenshot"})

        async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=False,
                status_code=403,
                failure_reason=FailureReason.HTTP_403,
                cost_units=2,
                latency_ms=10,
                route="estimated_failure",
            )

    class RetryingSuccess(ProviderAdapter):
        name = "retrying_success"
        cost_rank = 2
        capabilities = frozenset({"html", "render_js", "premium", "screenshot"})

        async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=True,
                status_code=200,
                html=(
                    "<html><body><h1>Recovered</h1><p>This provider recovered after an "
                    "internal retry and returned enough content.</p></body></html>"
                ),
                cost_units=6,
                latency_ms=30,
                route="retrying_success:advanced",
                attempt_ledger=[
                    AttemptLedgerEntry(
                        provider=self.name,
                        route="retrying_success:standard",
                        cost_units=1,
                        cost_provenance="estimated",
                        success=False,
                        latency_ms=10,
                        status_code=403,
                        failure_reason=FailureReason.HTTP_403,
                        block_type=None,
                    ),
                    AttemptLedgerEntry(
                        provider=self.name,
                        route="retrying_success:advanced",
                        cost_units=5,
                        cost_provenance="exact",
                        success=True,
                        latency_ms=20,
                        status_code=200,
                        failure_reason=None,
                        block_type=None,
                    ),
                ],
            )

    memory = DomainMemory(db_path=tmp_path / "mem.sqlite")
    gw = ScrapeGateway(
        providers=[EstimatedFailure(), RetryingSuccess()],
        cache=ArtifactCache(root=tmp_path / "cache"),
        memory=memory,
        telemetry=TelemetryRecorder(root=tmp_path / "runs"),
    )

    result = await gw.scrape(
        ScrapeRequest(
            "https://www.example.com/products/7?ref=ledger",
            country="RO",
            render_js=True,
            premium=True,
            mobile=True,
            screenshot=True,
            metadata={"run_id": "fallback_run_123"},
        ),
        use_cache=False,
        use_memory=False,
    )

    assert result.success is True
    assert result.cost_units == 6
    assert result.run_cost_units == 8
    assert [entry.provider for entry in result.attempt_ledger] == [
        "estimated_failure",
        "retrying_success",
        "retrying_success",
    ]
    assert [entry.cost_provenance for entry in result.attempt_ledger] == [
        "estimated",
        "estimated",
        "exact",
    ]
    report = json.loads(Path(result.metadata["telemetry_report"]).read_text())
    assert len(report["attempts"]) == 2
    assert report["run_cost_units"] == 8
    assert [entry["cost_units"] for entry in report["ledger"]] == [2, 1, 5]
    rows = memory.conn.execute(
        """
        select run_id, attempt_index, domain, url, country, render_js, premium,
               mobile, screenshot, provider, route, cost_units, cost_provenance,
               success, status_code, failure_reason, block_type, latency_ms
        from attempt_ledger
        order by attempt_index
        """
    ).fetchall()
    assert [dict(row) for row in rows] == [
        {
            "run_id": "fallback_run_123",
            "attempt_index": 1,
            "domain": "example.com",
            "url": "https://www.example.com/products/7?ref=ledger",
            "country": "RO",
            "render_js": 1,
            "premium": 1,
            "mobile": 1,
            "screenshot": 1,
            "provider": "estimated_failure",
            "route": "estimated_failure",
            "cost_units": 2.0,
            "cost_provenance": "estimated",
            "success": 0,
            "status_code": 403,
            "failure_reason": "http_403",
            "block_type": None,
            "latency_ms": 10,
        },
        {
            "run_id": "fallback_run_123",
            "attempt_index": 2,
            "domain": "example.com",
            "url": "https://www.example.com/products/7?ref=ledger",
            "country": "RO",
            "render_js": 1,
            "premium": 1,
            "mobile": 1,
            "screenshot": 1,
            "provider": "retrying_success",
            "route": "retrying_success:standard",
            "cost_units": 1.0,
            "cost_provenance": "estimated",
            "success": 0,
            "status_code": 403,
            "failure_reason": "http_403",
            "block_type": None,
            "latency_ms": 10,
        },
        {
            "run_id": "fallback_run_123",
            "attempt_index": 3,
            "domain": "example.com",
            "url": "https://www.example.com/products/7?ref=ledger",
            "country": "RO",
            "render_js": 1,
            "premium": 1,
            "mobile": 1,
            "screenshot": 1,
            "provider": "retrying_success",
            "route": "retrying_success:advanced",
            "cost_units": 5.0,
            "cost_provenance": "exact",
            "success": 1,
            "status_code": 200,
            "failure_reason": None,
            "block_type": None,
            "latency_ms": 20,
        },
    ]


async def test_validation_rejection_persists_all_failed_sub_attempts(tmp_path):
    class InternallyRetriedBlock(ProviderAdapter):
        name = "internally_retried_block"
        capabilities = frozenset({"html"})

        async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
            return ScrapeResult(
                url=request.url,
                provider=self.name,
                success=True,
                status_code=200,
                html=(
                    "<html><body>Checking your browser before accessing the site. "
                    "Ray ID: abc123</body></html>" + "x" * 200
                ),
                cost_units=6,
                route="internally_retried_block:advanced",
                attempt_ledger=[
                    AttemptLedgerEntry(
                        provider=self.name,
                        route="internally_retried_block:standard",
                        cost_units=1,
                        cost_provenance="estimated",
                        success=False,
                        latency_ms=10,
                        status_code=403,
                        failure_reason=FailureReason.HTTP_403,
                        block_type=None,
                    ),
                    AttemptLedgerEntry(
                        provider=self.name,
                        route="internally_retried_block:advanced",
                        cost_units=5,
                        cost_provenance="estimated",
                        success=True,
                        latency_ms=20,
                        status_code=200,
                        failure_reason=None,
                        block_type=None,
                    ),
                ],
            )

    memory = DomainMemory(db_path=tmp_path / "mem.sqlite")
    result = await ScrapeGateway(
        providers=[InternallyRetriedBlock()],
        cache=ArtifactCache(root=tmp_path / "cache"),
        memory=memory,
    ).scrape(
        ScrapeRequest("https://example.com"),
        use_cache=False,
        use_memory=False,
    )

    assert result.success is False
    assert result.run_cost_units == 6
    assert result.attempt_ledger[0].success is False
    assert result.attempt_ledger[0].failure_reason == FailureReason.HTTP_403
    assert result.attempt_ledger[0].block_type is None
    assert result.attempt_ledger[1].success is False
    assert result.attempt_ledger[1].failure_reason is None
    assert result.attempt_ledger[1].block_type == "cloudflare"
    persisted = memory.conn.execute(
        """
        select attempt_index, success, failure_reason, block_type
        from attempt_ledger
        order by attempt_index
        """
    ).fetchall()
    assert [dict(row) for row in persisted] == [
        {
            "attempt_index": 1,
            "success": 0,
            "failure_reason": "http_403",
            "block_type": None,
        },
        {
            "attempt_index": 2,
            "success": 0,
            "failure_reason": None,
            "block_type": "cloudflare",
        },
    ]


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


@respx.mock
async def test_scrapfly_outer_proxy_error_stops_router_fallback(tmp_dir):
    respx.get("https://api.scrapfly.io/scrape").mock(
        return_value=httpx.Response(
            407,
            json={
                "result": {"content": "", "status_code": 200},
                "context": {"cost": 7},
            },
        )
    )
    fallback_called = False

    class ExpensiveSuccess(SuccessProvider):
        name = "expensive_success"
        cost_rank = 50

        async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
            nonlocal fallback_called
            fallback_called = True
            return await super().scrape(request)

    result = await ScrapeGateway(
        providers=[ScrapflyProvider(api_key="scrapfly-key"), ExpensiveSuccess()],
        cache=ArtifactCache(root=tmp_dir / "cache"),
        memory=DomainMemory(db_path=tmp_dir / "mem.sqlite"),
    ).scrape(
        ScrapeRequest("https://example.com"),
        use_cache=False,
        use_memory=False,
    )

    assert result.success is False
    assert result.provider == "scrapfly"
    assert result.failure_reason == FailureReason.PROXY_ERROR
    assert fallback_called is False


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
    memory = DomainMemory(db_path=tmp_dir / "mem.sqlite")
    gw = ScrapeGateway(
        providers=[FailProvider()],
        cache=cache,
        memory=memory,
    )
    result = await gw.scrape(ScrapeRequest("https://cached.com"))
    assert result.success
    assert result.provider == "cache"
    assert memory.conn.execute("select count(*) from attempt_ledger").fetchone()[0] == 0


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
    mem.record_attempt_ledger(
        "preferred",
        ScrapeRequest("https://example.com"),
        [
            AttemptLedgerEntry(
                provider="success",
                route="success",
                cost_units=0,
                cost_provenance="estimated",
                success=True,
                latency_ms=1,
                status_code=200,
                failure_reason=None,
                block_type=None,
            )
        ],
    )

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


async def test_request_provider_override_applies_to_only_that_scrape(tmp_dir):
    call_order = []

    class TrackingFailure(FailProvider):
        async def scrape(self, request):
            call_order.append(self.name)
            return await super().scrape(request)

    class TrackingSuccess(SuccessProvider):
        async def scrape(self, request):
            call_order.append(self.name)
            return await super().scrape(request)

    gw = ScrapeGateway(
        providers=[TrackingFailure(), TrackingSuccess()],
        cache=ArtifactCache(root=tmp_dir / "cache"),
        memory=DomainMemory(db_path=tmp_dir / "mem.sqlite"),
    )

    forced = await gw.scrape(
        ScrapeRequest(
            "https://example.com/forced",
            metadata={"preferred_provider": "success"},
        ),
        use_cache=False,
        use_memory=False,
    )
    assert forced.provider == "success"
    assert call_order == ["success"]

    call_order.clear()
    automatic = await gw.scrape(
        ScrapeRequest("https://another.example/automatic"),
        use_cache=False,
        use_memory=False,
    )
    assert automatic.provider == "success"
    assert call_order == ["fail", "success"]


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
    screenshot_path = Path(result.metadata["artifacts"]["screenshot"])
    assert screenshot_path.exists()
    assert screenshot_path.read_bytes() == result.screenshot


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


async def test_invalid_caller_run_id_is_replaced_and_report_stays_inside_root(
    tmp_dir, monkeypatch, capsys
):
    runs_root = tmp_dir / "runs"
    monkeypatch.setattr("scrape_gateway.router.new_run_id", lambda: "generated123")
    gw = ScrapeGateway(
        providers=[SuccessProvider()],
        cache=ArtifactCache(root=tmp_dir / "cache"),
        memory=DomainMemory(db_path=tmp_dir / "mem.sqlite"),
        telemetry=TelemetryRecorder(root=runs_root),
    )

    result = await gw.scrape(
        ScrapeRequest("https://example.com", metadata={"run_id": "../escaped"}),
        use_cache=False,
        use_memory=False,
    )

    assert result.success is True
    assert result.metadata["run_id"] == "generated123"
    assert (
        Path(result.metadata["telemetry_report"]).resolve()
        == (runs_root / "generated123" / "report.json").resolve()
    )
    assert not (tmp_dir / "escaped").exists()
    assert "invalid run_id" in capsys.readouterr().err


async def test_valid_caller_run_id_is_honored(tmp_dir):
    runs_root = tmp_dir / "runs"
    gw = ScrapeGateway(
        providers=[SuccessProvider()],
        cache=ArtifactCache(root=tmp_dir / "cache"),
        memory=DomainMemory(db_path=tmp_dir / "mem.sqlite"),
        telemetry=TelemetryRecorder(root=runs_root),
    )

    result = await gw.scrape(
        ScrapeRequest("https://example.com", metadata={"run_id": "caller_run-123"}),
        use_cache=False,
        use_memory=False,
    )

    assert result.success is True
    assert result.metadata["run_id"] == "caller_run-123"
    assert (
        Path(result.metadata["telemetry_report"]).resolve()
        == (runs_root / "caller_run-123" / "report.json").resolve()
    )


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


async def test_preferred_provider_keeps_cheaper_fallback_behind_it(tmp_dir):
    mem = DomainMemory(db_path=tmp_dir / "mem.sqlite")
    mem.record_attempt_ledger(
        "preferred",
        ScrapeRequest("https://example.com"),
        [
            *[
                AttemptLedgerEntry(
                    provider="success",
                    route="success",
                    cost_units=1,
                    cost_provenance="exact",
                    success=True,
                    latency_ms=1,
                    status_code=200,
                    failure_reason=None,
                    block_type=None,
                )
                for _ in range(5)
            ],
            AttemptLedgerEntry(
                provider="cheap",
                route="cheap",
                cost_units=1,
                cost_provenance="exact",
                success=False,
                latency_ms=1,
                status_code=403,
                failure_reason=FailureReason.HTTP_403,
                block_type=None,
            ),
        ],
    )

    call_order = []

    class TrackingCheap(CheapProvider):
        async def scrape(self, request):
            call_order.append(self.name)
            return await super().scrape(request)

    class TrackingSuccess(SuccessProvider):
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
        providers=[TrackingCheap(), TrackingSuccess()],
        cache=ArtifactCache(root=tmp_dir / "cache"),
        memory=mem,
    )
    result = await gw.scrape(ScrapeRequest("https://example.com/page"), use_cache=False)
    assert result.success
    assert result.provider == "cheap"
    assert call_order == ["success", "cheap"]


async def test_tier_hint_applies_after_sufficient_cost_history(tmp_dir):
    """After enough evidence, skip the failing cheap provider and reuse the tier."""
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

    for index in range(5):
        result = await gw.scrape(
            ScrapeRequest(f"https://hard.com/page{index}"),
            use_cache=False,
        )
        assert result.success
        assert result.provider == "scrapedrive"
    assert cheap.call_count == 5

    learned = await gw.scrape(ScrapeRequest("https://hard.com/learned"), use_cache=False)
    assert learned.success
    assert learned.provider == "scrapedrive"
    assert cheap.call_count == 5
    assert learned.metadata.get("tier_used") == "scrapedrive:standard"


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
