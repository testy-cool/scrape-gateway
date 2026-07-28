from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scrape_gateway.cache import ArtifactCache
from scrape_gateway.memory import DomainMemory
from scrape_gateway.models import AttemptLedgerEntry, FailureReason, ScrapeRequest, ScrapeResult
from scrape_gateway.progress import observe_progress
from scrape_gateway.provider import ProviderAdapter
from scrape_gateway.router import ScrapeGateway
from scrape_gateway.telemetry import TelemetryRecorder


NOW = datetime(2026, 7, 28, 12, tzinfo=timezone.utc)


class TrackingProvider(ProviderAdapter):
    capabilities = frozenset({"html", "render_js", "country"})

    def __init__(
        self,
        name: str,
        cost_rank: int,
        calls: list[str],
        *,
        success: bool = True,
        cost_units: float = 1,
    ) -> None:
        self.name = name
        self.cost_rank = cost_rank
        self.calls = calls
        self.success = success
        self.cost_units = cost_units

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        self.calls.append(self.name)
        return ScrapeResult(
            url=request.url,
            provider=self.name,
            success=self.success,
            status_code=200 if self.success else 403,
            html=(
                "<html><body><h1>Useful page</h1>"
                "<p>Enough content for deterministic validation.</p></body></html>"
                if self.success
                else None
            ),
            failure_reason=None if self.success else FailureReason.HTTP_403,
            cost_units=self.cost_units,
            route=self.name,
        )


def _entry(
    provider: str,
    *,
    success: bool,
    cost_units: float,
    cost_provenance: str = "exact",
) -> AttemptLedgerEntry:
    return AttemptLedgerEntry(
        provider=provider,
        route=f"{provider}:route",
        cost_units=cost_units,
        cost_provenance=cost_provenance,
        success=success,
        latency_ms=10,
        status_code=200 if success else 403,
        failure_reason=None if success else FailureReason.HTTP_403,
        block_type=None,
    )


def _record_history(
    memory: DomainMemory,
    request: ScrapeRequest,
    provider: str,
    outcomes: list[bool],
    *,
    cost_units: float,
    cost_provenance: str = "exact",
    recorded_at: datetime = NOW,
) -> None:
    for index, success in enumerate(outcomes):
        memory.record_attempt_ledger(
            f"{provider}-{index}",
            request,
            [
                _entry(
                    provider,
                    success=success,
                    cost_units=cost_units,
                    cost_provenance=cost_provenance,
                )
            ],
            recorded_at=recorded_at,
        )


@pytest.fixture
def scrape_request() -> ScrapeRequest:
    return ScrapeRequest("https://example.com/products", country="RO", render_js=True)


def test_cost_effectiveness_includes_failures_and_downweights_estimates(
    tmp_path: Path, scrape_request: ScrapeRequest
) -> None:
    memory = DomainMemory(tmp_path / "memory.sqlite", clock=lambda: NOW)
    entries = [
        _entry("mixed", success=False, cost_units=4),
        _entry("mixed", success=True, cost_units=4),
        _entry("mixed", success=True, cost_units=4),
        _entry("mixed", success=False, cost_units=10, cost_provenance="estimated"),
        _entry("mixed", success=True, cost_units=10, cost_provenance="estimated"),
    ]
    memory.record_attempt_ledger("mixed-run", scrape_request, entries, recorded_at=NOW)

    [score] = memory.provider_cost_effectiveness(scrape_request)

    assert score["attempt_count"] == 5
    assert score["successful_attempt_count"] == 3
    assert score["exact_attempt_count"] == 3
    assert score["estimated_attempt_count"] == 2
    assert score["weighted_cost_units"] == pytest.approx(22)
    assert score["weighted_success_count"] == pytest.approx(2.5)
    assert score["cost_per_success"] == pytest.approx(8.8)


def test_observed_reliable_provider_outranks_cheapest_failure(
    tmp_path: Path, scrape_request: ScrapeRequest
) -> None:
    memory = DomainMemory(tmp_path / "memory.sqlite", clock=lambda: NOW)
    _record_history(memory, scrape_request, "cheap", [False] * 5, cost_units=1)
    _record_history(memory, scrape_request, "reliable", [True] * 5, cost_units=3)
    calls: list[str] = []
    gateway = ScrapeGateway(
        providers=[
            TrackingProvider("cheap", 1, calls),
            TrackingProvider("reliable", 20, calls),
        ],
        memory=memory,
    )

    ordered = gateway._ordered_providers(scrape_request)

    assert [provider.name for provider in ordered] == ["reliable", "cheap"]
    assert scrape_request.metadata["routing_decision"]["kind"] == "observed_cost"
    assert scrape_request.metadata["routing_decision"]["provider"] == "reliable"
    assert scrape_request.metadata["routing_decision"]["cost_per_success"] == pytest.approx(3)
    assert scrape_request.metadata["routing_decision"]["sample_count"] == 5


def test_no_history_keeps_cost_rank_order(tmp_path: Path, scrape_request: ScrapeRequest) -> None:
    calls: list[str] = []
    gateway = ScrapeGateway(
        providers=[
            TrackingProvider("reliable", 20, calls),
            TrackingProvider("cheap", 1, calls),
        ],
        memory=DomainMemory(tmp_path / "memory.sqlite", clock=lambda: NOW),
    )

    ordered = gateway._ordered_providers(scrape_request)

    assert [provider.name for provider in ordered] == ["cheap", "reliable"]
    assert scrape_request.metadata["routing_decision"]["kind"] == "cost_rank"


def test_one_success_is_thin_history_and_does_not_flip_order(
    tmp_path: Path, scrape_request: ScrapeRequest
) -> None:
    memory = DomainMemory(tmp_path / "memory.sqlite", clock=lambda: NOW)
    _record_history(memory, scrape_request, "reliable", [True], cost_units=1)
    calls: list[str] = []
    gateway = ScrapeGateway(
        providers=[
            TrackingProvider("reliable", 20, calls),
            TrackingProvider("cheap", 1, calls),
        ],
        memory=memory,
    )

    ordered = gateway._ordered_providers(scrape_request)

    assert [provider.name for provider in ordered] == ["cheap", "reliable"]
    assert scrape_request.metadata["routing_decision"]["kind"] == "cost_rank"


def test_explicit_provider_wins_over_observed_cost(
    tmp_path: Path, scrape_request: ScrapeRequest
) -> None:
    memory = DomainMemory(tmp_path / "memory.sqlite", clock=lambda: NOW)
    _record_history(memory, scrape_request, "cheap", [True] * 5, cost_units=1)
    calls: list[str] = []
    gateway = ScrapeGateway(
        providers=[
            TrackingProvider("cheap", 1, calls),
            TrackingProvider("forced", 50, calls),
        ],
        memory=memory,
    )
    scrape_request.metadata["preferred_provider"] = "forced"

    ordered = gateway._ordered_providers(scrape_request)

    assert [provider.name for provider in ordered] == ["forced", "cheap"]


async def test_stale_cheaper_provider_is_retried_as_an_exploration_probe(
    tmp_path: Path, scrape_request: ScrapeRequest
) -> None:
    memory = DomainMemory(tmp_path / "memory.sqlite", clock=lambda: NOW)
    _record_history(memory, scrape_request, "reliable", [True] * 5, cost_units=3)
    _record_history(
        memory,
        scrape_request,
        "cheap",
        [False],
        cost_units=1,
        recorded_at=NOW - timedelta(days=1, microseconds=1),
    )
    calls: list[str] = []
    gateway = ScrapeGateway(
        providers=[
            TrackingProvider("cheap", 1, calls),
            TrackingProvider("reliable", 20, calls),
        ],
        cache=ArtifactCache(root=tmp_path / "cache"),
        memory=memory,
        telemetry=TelemetryRecorder(root=tmp_path / "runs"),
    )

    result = await gateway.scrape(scrape_request, use_cache=False)

    assert result.success is True
    assert calls == ["cheap"]
    report = json.loads(Path(result.metadata["telemetry_report"]).read_text())
    assert report["routing_decision"]["kind"] == "exploration"
    assert report["routing_decision"]["provider"] == "cheap"


async def test_observed_cost_reason_is_visible_in_log_progress_and_telemetry(
    tmp_path: Path, scrape_request: ScrapeRequest, capsys: pytest.CaptureFixture[str]
) -> None:
    memory = DomainMemory(tmp_path / "memory.sqlite", clock=lambda: NOW)
    _record_history(memory, scrape_request, "cheap", [False] * 5, cost_units=1)
    _record_history(memory, scrape_request, "reliable", [True] * 5, cost_units=3)
    calls: list[str] = []
    gateway = ScrapeGateway(
        providers=[
            TrackingProvider("cheap", 1, calls),
            TrackingProvider("reliable", 20, calls),
        ],
        cache=ArtifactCache(root=tmp_path / "cache"),
        memory=memory,
        telemetry=TelemetryRecorder(root=tmp_path / "runs"),
    )
    events: list[dict] = []

    with observe_progress(events.append):
        result = await gateway.scrape(scrape_request, use_cache=False)

    route_event = next(event for event in events if event["id"] == "routing")
    assert "3 units per success" in route_event["summary"]
    assert "5 samples" in route_event["summary"]
    assert "3 units per success" in capsys.readouterr().err
    report = json.loads(Path(result.metadata["telemetry_report"]).read_text())
    assert report["routing_decision"]["kind"] == "observed_cost"
    assert "3 units per success" in report["routing_decision"]["reason"]
