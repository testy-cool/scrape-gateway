from __future__ import annotations

from datetime import UTC, datetime, timedelta

from scrape_gateway.cache import ArtifactCache
from scrape_gateway.memory import DomainMemory
from scrape_gateway.models import AttemptLedgerEntry, FailureReason, ScrapeRequest, ScrapeResult
from scrape_gateway.provider import ProviderAdapter
from scrape_gateway.router import ScrapeGateway

NOW = datetime(2026, 7, 28, 12, tzinfo=UTC)


def _entry(
    provider: str,
    *,
    success: bool,
    route: str | None = None,
    failure_reason: FailureReason | None = None,
    block_type: str | None = None,
) -> AttemptLedgerEntry:
    return AttemptLedgerEntry(
        provider=provider,
        route=route,
        cost_units=1,
        cost_provenance="estimated",
        success=success,
        latency_ms=10,
        status_code=200 if success else None,
        failure_reason=failure_reason,
        block_type=block_type,
    )


def _record(
    memory: DomainMemory,
    run_id: str,
    request: ScrapeRequest,
    entry: AttemptLedgerEntry,
    *,
    recorded_at: datetime = NOW,
) -> None:
    memory.record_attempt_ledger(run_id, request, [entry], recorded_at=recorded_at)


def test_provider_unavailable_failures_do_not_blacklist_a_domain(tmp_path) -> None:
    memory = DomainMemory(
        tmp_path / "memory.sqlite",
        evidence_window_seconds=7 * 86400,
        clock=lambda: NOW,
    )
    request = ScrapeRequest("https://example.com/products")
    for index in range(5):
        _record(
            memory,
            f"unavailable-{index}",
            request,
            _entry(
                "scrapedrive",
                success=False,
                failure_reason=FailureReason.PROVIDER_UNAVAILABLE,
            ),
        )

    assert memory.should_skip_provider(request, "scrapedrive") is False


def test_failures_are_scoped_to_the_exact_request_profile(tmp_path) -> None:
    memory = DomainMemory(
        tmp_path / "memory.sqlite",
        evidence_window_seconds=7 * 86400,
        clock=lambda: NOW,
    )
    plain = ScrapeRequest("https://example.com/products")
    rendered = ScrapeRequest(
        "https://example.com/products",
        country="RO",
        render_js=True,
        premium=True,
        mobile=True,
        screenshot=True,
    )
    for index in range(5):
        _record(
            memory,
            f"plain-failure-{index}",
            plain,
            _entry("provider", success=False, failure_reason=FailureReason.HTTP_403),
        )

    assert memory.should_skip_provider(plain, "provider") is True
    assert memory.should_skip_provider(rendered, "provider") is False


def test_skipped_provider_gets_a_half_open_probe_after_evidence_expires(tmp_path) -> None:
    memory = DomainMemory(
        tmp_path / "memory.sqlite",
        evidence_window_seconds=7 * 86400,
        clock=lambda: NOW,
    )
    request = ScrapeRequest("https://example.com/products", country="RO")
    for index in range(5):
        _record(
            memory,
            f"failure-{index}",
            request,
            _entry("provider", success=False, failure_reason=FailureReason.HTTP_403),
            recorded_at=NOW - timedelta(days=1),
        )

    assert memory.should_skip_provider(request, "provider", as_of=NOW) is True
    assert (
        memory.should_skip_provider(
            request,
            "provider",
            as_of=NOW + timedelta(days=6, microseconds=1),
        )
        is False
    )


def test_failed_half_open_probe_recloses_provider_for_another_window(tmp_path) -> None:
    memory = DomainMemory(
        tmp_path / "memory.sqlite",
        evidence_window_seconds=7 * 86400,
        clock=lambda: NOW,
    )
    request = ScrapeRequest("https://example.com/products")
    for index in range(5):
        _record(
            memory,
            f"old-failure-{index}",
            request,
            _entry("provider", success=False, failure_reason=FailureReason.HTTP_403),
            recorded_at=NOW - timedelta(days=1),
        )
    probe_time = NOW + timedelta(days=6, microseconds=1)
    assert memory.should_skip_provider(request, "provider", as_of=probe_time) is False

    _record(
        memory,
        "failed-probe",
        request,
        _entry("provider", success=False, failure_reason=FailureReason.HTTP_403),
        recorded_at=probe_time,
    )

    assert memory.should_skip_provider(request, "provider", as_of=probe_time) is True


async def test_expired_skip_is_probed_ahead_of_the_current_winner(tmp_path) -> None:
    current_time = NOW
    recovered = False
    calls: list[str] = []
    memory = DomainMemory(
        tmp_path / "memory.sqlite",
        evidence_window_seconds=7 * 86400,
        clock=lambda: current_time,
    )
    request = ScrapeRequest("https://example.com/products")
    for index in range(5):
        _record(
            memory,
            f"failure-{index}",
            request,
            _entry("recovering", success=False, failure_reason=FailureReason.HTTP_403),
            recorded_at=NOW - timedelta(days=1),
        )

    class RecoveringProvider(ProviderAdapter):
        name = "recovering"
        cost_rank = 1
        capabilities = frozenset({"html"})

        async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
            calls.append(self.name)
            return ScrapeResult(
                request.url,
                self.name,
                recovered,
                status_code=200 if recovered else 403,
                html=(
                    "<html><body><h1>Recovered</h1><p>The provider works again and returns "
                    "enough meaningful page content to pass deterministic validation.</p></body></html>"
                    if recovered
                    else None
                ),
                failure_reason=None if recovered else FailureReason.HTTP_403,
                route="recovering",
            )

    class CurrentWinner(ProviderAdapter):
        name = "winner"
        cost_rank = 20
        capabilities = frozenset({"html"})

        async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
            calls.append(self.name)
            return ScrapeResult(
                request.url,
                self.name,
                True,
                status_code=200,
                html="<html><body><h1>Winner</h1><p>The current fallback returns enough "
                "meaningful page content to pass deterministic validation.</p></body></html>",
                route="winner",
            )

    gateway = ScrapeGateway(
        providers=[RecoveringProvider(), CurrentWinner()],
        cache=ArtifactCache(root=tmp_path / "cache"),
        memory=memory,
    )

    first = await gateway.scrape(request, use_cache=False)
    assert first.provider == "winner"
    assert calls == ["winner"]

    current_time = NOW + timedelta(days=6, microseconds=1)
    recovered = True
    second = await gateway.scrape(
        ScrapeRequest("https://example.com/products"),
        use_cache=False,
    )

    assert second.provider == "recovering"
    assert calls == ["winner", "recovering"]


def test_preferred_provider_uses_recent_successes_from_the_exact_profile(tmp_path) -> None:
    memory = DomainMemory(
        tmp_path / "memory.sqlite",
        evidence_window_seconds=7 * 86400,
        clock=lambda: NOW,
    )
    plain = ScrapeRequest("https://example.com/products")
    rendered = ScrapeRequest("https://example.com/products", render_js=True)
    _record(
        memory,
        "plain-success",
        plain,
        _entry("cheap", success=True, route="cheap:plain"),
    )
    _record(
        memory,
        "rendered-success",
        rendered,
        _entry("browser", success=True, route="browser:rendered"),
    )

    assert memory.preferred_provider(plain) == ("cheap", "cheap:plain")
    assert memory.preferred_provider(rendered) == ("browser", "browser:rendered")


def test_old_databases_with_legacy_tables_still_open_and_stay_ignored(tmp_path) -> None:
    """Pre-ledger databases carry domain_provider_stats and domain_routes rows.

    New code neither creates nor reads those tables, but it must keep opening old
    databases that have them, and their contents must not leak into stats or routing.
    """
    import sqlite3

    db = tmp_path / "memory.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        create table domain_provider_stats (
          domain text, provider text, success_count integer, primary key (domain, provider)
        );
        insert into domain_provider_stats values ('example.com', 'legacy', 9);
        create table domain_routes (domain text primary key, provider text);
        insert into domain_routes values ('example.com', 'legacy');
        """
    )
    conn.commit()
    conn.close()

    memory = DomainMemory(db, evidence_window_seconds=7 * 86400, clock=lambda: NOW)
    request = ScrapeRequest("https://example.com/products")

    assert memory.provider_stats(request.url) == []
    assert memory.preferred_provider(request) is None


def test_cost_effective_winner_moves_first_without_removing_recent_cheaper_provider(
    tmp_path,
) -> None:
    memory = DomainMemory(
        tmp_path / "memory.sqlite",
        evidence_window_seconds=7 * 86400,
        clock=lambda: NOW,
    )
    request = ScrapeRequest("https://example.com/products")
    for index in range(5):
        _record(
            memory,
            f"winner-{index}",
            request,
            _entry("winner", success=True, route="winner:advanced"),
        )
    _record(
        memory,
        "recent-cheap-failure",
        request,
        _entry(
            "cheap",
            success=False,
            failure_reason=FailureReason.HTTP_403,
        ),
    )

    class StubProvider(ProviderAdapter):
        capabilities = frozenset({"html"})

        def __init__(self, name: str, cost_rank: int) -> None:
            self.name = name
            self.cost_rank = cost_rank

        async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
            raise AssertionError("routing order is inspected without scraping")

    gateway = ScrapeGateway(
        providers=[
            StubProvider("cheap", 1),
            StubProvider("winner", 20),
            StubProvider("expensive", 30),
        ],
        cache=ArtifactCache(root=tmp_path / "cache"),
        memory=memory,
    )

    assert [provider.name for provider in gateway._ordered_providers(request)] == [
        "winner",
        "cheap",
        "expensive",
    ]
    assert request.metadata["start_tier"] == "winner:advanced"


async def test_unavailable_provider_is_skipped_before_scraping_and_can_recover(tmp_path) -> None:
    available = False
    calls: list[str] = []

    class RecoverableProvider(ProviderAdapter):
        name = "recoverable"
        cost_rank = 1
        capabilities = frozenset({"html"})

        def availability_error(self) -> str | None:
            return None if available else "Missing RECOVERABLE_API_KEY"

        async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
            calls.append(self.name)
            return ScrapeResult(
                request.url,
                self.name,
                True,
                status_code=200,
                html="<html><body><h1>Recovered</h1><p>Provider is configured and healthy now.</p></body></html>",
                route="recoverable",
            )

    provider = RecoverableProvider()
    memory = DomainMemory(
        tmp_path / "memory.sqlite",
        evidence_window_seconds=7 * 86400,
        clock=lambda: NOW,
    )
    request = ScrapeRequest("https://example.com/products")
    for index in range(5):
        _record(
            memory,
            f"credential-failure-{index}",
            request,
            _entry(
                "recoverable",
                success=False,
                failure_reason=FailureReason.PROVIDER_UNAVAILABLE,
            ),
        )
    gateway = ScrapeGateway(
        providers=[provider],
        cache=ArtifactCache(root=tmp_path / "cache"),
        memory=memory,
    )

    missing = await gateway.scrape(request, use_cache=False)
    assert missing.success is False
    assert calls == []
    assert memory.conn.execute("select count(*) from attempt_ledger").fetchone()[0] == 5

    available = True
    recovered = await gateway.scrape(request, use_cache=False)
    assert recovered.success is True
    assert calls == ["recoverable"]
    legacy_tables = memory.conn.execute(
        "select count(*) from sqlite_master where name in "
        "('domain_provider_stats', 'domain_routes')"
    ).fetchone()[0]
    assert legacy_tables == 0, "the scrape path must not resurrect legacy aggregate tables"
