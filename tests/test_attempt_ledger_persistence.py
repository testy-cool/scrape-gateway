from datetime import datetime, timezone

from scrape_gateway.memory import DomainMemory
from scrape_gateway.models import AttemptLedgerEntry, FailureReason, ScrapeRequest


def _entry(
    provider: str,
    cost_units: float,
    *,
    success: bool,
    route: str | None = None,
) -> AttemptLedgerEntry:
    return AttemptLedgerEntry(
        provider=provider,
        route=route or f"{provider}:default",
        cost_units=cost_units,
        cost_provenance="exact",
        success=success,
        latency_ms=25,
        status_code=200 if success else 403,
        failure_reason=None if success else FailureReason.HTTP_403,
        block_type=None,
    )


def _record(
    memory: DomainMemory,
    *,
    run_id: str,
    url: str,
    entries: list[AttemptLedgerEntry],
    recorded_at: datetime,
    country: str | None = "RO",
    render_js: bool = True,
    premium: bool = False,
    mobile: bool = True,
    screenshot: bool = False,
) -> None:
    memory.record_attempt_ledger(
        run_id,
        ScrapeRequest(
            url,
            country=country,
            render_js=render_js,
            premium=premium,
            mobile=mobile,
            screenshot=screenshot,
        ),
        entries,
        recorded_at=recorded_at,
    )


def test_record_attempt_ledger_denormalizes_request_and_exact_entry_fields(tmp_path) -> None:
    memory = DomainMemory(db_path=tmp_path / "memory.sqlite")
    recorded_at = datetime(2026, 7, 20, 11, 12, 13, 456789, tzinfo=timezone.utc)
    entries = [
        AttemptLedgerEntry(
            provider="scrapedrive",
            route="scrapedrive:standard",
            cost_units=1,
            cost_provenance="estimated",
            success=False,
            latency_ms=123,
            status_code=403,
            failure_reason=FailureReason.HTTP_403,
            block_type="cloudflare",
        ),
        AttemptLedgerEntry(
            provider="scrapfly",
            route="scrapfly:asp",
            cost_units=0,
            cost_provenance="exact",
            success=True,
            latency_ms=None,
            status_code=200,
            failure_reason=None,
            block_type=None,
        ),
    ]

    inserted = memory.record_attempt_ledger(
        "run_full_profile",
        ScrapeRequest(
            "https://www.example.com/products/7?ref=ledger",
            country="RO",
            render_js=True,
            premium=True,
            mobile=True,
            screenshot=True,
        ),
        entries,
        recorded_at=recorded_at,
    )

    rows = memory.conn.execute(
        """
        select run_id, attempt_index, recorded_at, domain, url, country,
               render_js, premium, mobile, screenshot, provider, route,
               cost_units, cost_provenance, success, status_code,
               failure_reason, block_type, latency_ms
        from attempt_ledger
        order by attempt_index
        """
    ).fetchall()

    assert inserted == 2
    assert [dict(row) for row in rows] == [
        {
            "run_id": "run_full_profile",
            "attempt_index": 1,
            "recorded_at": "2026-07-20T11:12:13.456789Z",
            "domain": "example.com",
            "url": "https://www.example.com/products/7?ref=ledger",
            "country": "RO",
            "render_js": 1,
            "premium": 1,
            "mobile": 1,
            "screenshot": 1,
            "provider": "scrapedrive",
            "route": "scrapedrive:standard",
            "cost_units": 1.0,
            "cost_provenance": "estimated",
            "success": 0,
            "status_code": 403,
            "failure_reason": "http_403",
            "block_type": "cloudflare",
            "latency_ms": 123,
        },
        {
            "run_id": "run_full_profile",
            "attempt_index": 2,
            "recorded_at": "2026-07-20T11:12:13.456789Z",
            "domain": "example.com",
            "url": "https://www.example.com/products/7?ref=ledger",
            "country": "RO",
            "render_js": 1,
            "premium": 1,
            "mobile": 1,
            "screenshot": 1,
            "provider": "scrapfly",
            "route": "scrapfly:asp",
            "cost_units": 0.0,
            "cost_provenance": "exact",
            "success": 1,
            "status_code": 200,
            "failure_reason": None,
            "block_type": None,
            "latency_ms": None,
        },
    ]


def test_attempt_cost_summary_answers_domain_provider_spend_for_recent_window(tmp_path) -> None:
    memory = DomainMemory(db_path=tmp_path / "memory.sqlite")
    as_of = datetime(2026, 7, 28, 12, tzinfo=timezone.utc)
    recent = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)
    old = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)
    _record(
        memory,
        run_id="recent_a",
        url="https://example.com/a",
        entries=[
            _entry("provider_a", 0, success=False),
            _entry("provider_a", 3, success=True),
        ],
        recorded_at=recent,
    )
    _record(
        memory,
        run_id="recent_b",
        url="https://example.com/b",
        entries=[_entry("provider_b", 4, success=False)],
        recorded_at=recent,
    )
    _record(
        memory,
        run_id="recent_other",
        url="https://other.example/a",
        entries=[_entry("provider_a", 9, success=True)],
        recorded_at=recent,
    )
    _record(
        memory,
        run_id="old_a",
        url="https://example.com/old",
        entries=[_entry("provider_a", 20, success=True)],
        recorded_at=old,
    )

    summary = memory.attempt_cost_summary(days=7, domain="example.com", as_of=as_of)

    assert summary == [
        {
            "domain": "example.com",
            "provider": "provider_a",
            "attempt_count": 2,
            "successful_attempt_count": 1,
            "failed_attempt_count": 1,
            "successful_attempt_cost_units": 3.0,
            "failed_attempt_cost_units": 0.0,
            "total_cost_units": 3.0,
        },
        {
            "domain": "example.com",
            "provider": "provider_b",
            "attempt_count": 1,
            "successful_attempt_count": 0,
            "failed_attempt_count": 1,
            "successful_attempt_cost_units": 0.0,
            "failed_attempt_cost_units": 4.0,
            "total_cost_units": 4.0,
        },
    ]


def test_schema_finds_cheapest_successful_provider_attempt_for_same_profile(tmp_path) -> None:
    memory = DomainMemory(db_path=tmp_path / "memory.sqlite")
    recorded_at = datetime(2026, 7, 28, tzinfo=timezone.utc)
    _record(
        memory,
        run_id="retrying_run",
        url="https://example.com/product",
        entries=[
            _entry("retrying", 1, success=False, route="retrying:standard"),
            _entry("retrying", 4, success=True, route="retrying:advanced"),
        ],
        recorded_at=recorded_at,
    )
    _record(
        memory,
        run_id="single_run",
        url="https://example.com/product",
        entries=[_entry("single", 3, success=True)],
        recorded_at=recorded_at,
    )
    _record(
        memory,
        run_id="failed_run",
        url="https://example.com/product",
        entries=[_entry("failed", 0.5, success=False)],
        recorded_at=recorded_at,
    )

    cheapest = memory.conn.execute(
        """
        with provider_attempts as (
          select run_id, domain, country, render_js, premium, mobile, screenshot,
                 provider, sum(cost_units) as provider_attempt_cost,
                 max(success) as provider_attempt_succeeded
          from attempt_ledger
          group by run_id, domain, country, render_js, premium, mobile, screenshot, provider
        )
        select provider, provider_attempt_cost
        from provider_attempts
        where domain = ?
          and country = ?
          and render_js = ?
          and premium = ?
          and mobile = ?
          and screenshot = ?
          and provider_attempt_succeeded = 1
        order by provider_attempt_cost, provider
        limit 1
        """,
        ("example.com", "RO", 1, 0, 1, 0),
    ).fetchone()

    assert dict(cheapest) == {
        "provider": "single",
        "provider_attempt_cost": 3.0,
    }


def test_schema_totals_failed_attempt_spend_without_losing_zero_cost_attempts(tmp_path) -> None:
    memory = DomainMemory(db_path=tmp_path / "memory.sqlite")
    recorded_at = datetime(2026, 7, 28, tzinfo=timezone.utc)
    _record(
        memory,
        run_id="failed_spend",
        url="https://example.com/product",
        entries=[
            _entry("free_failure", 0, success=False),
            _entry("paid_failure", 2.5, success=False),
            _entry("recovery", 1, success=True),
        ],
        recorded_at=recorded_at,
    )

    failed = memory.conn.execute(
        """
        select count(*) as failed_attempt_count,
               coalesce(sum(cost_units), 0) as failed_attempt_cost_units
        from attempt_ledger
        where success = 0
        """
    ).fetchone()

    assert dict(failed) == {
        "failed_attempt_count": 2,
        "failed_attempt_cost_units": 2.5,
    }


def test_schema_ranks_domains_by_credits_per_successful_run_with_fallback_spend(tmp_path) -> None:
    memory = DomainMemory(db_path=tmp_path / "memory.sqlite")
    recorded_at = datetime(2026, 7, 28, tzinfo=timezone.utc)
    _record(
        memory,
        run_id="alpha_success",
        url="https://alpha.example/product",
        entries=[
            _entry("fallback", 2, success=False),
            _entry("winner", 3, success=True),
        ],
        recorded_at=recorded_at,
    )
    _record(
        memory,
        run_id="alpha_all_failed",
        url="https://alpha.example/failed",
        entries=[_entry("fallback", 100, success=False)],
        recorded_at=recorded_at,
    )
    _record(
        memory,
        run_id="beta_success",
        url="https://beta.example/product",
        entries=[
            _entry("fallback", 9, success=False),
            _entry("winner", 1, success=True),
        ],
        recorded_at=recorded_at,
    )

    ranked = memory.conn.execute(
        """
        with runs as (
          select domain, run_id, sum(cost_units) as run_cost,
                 max(success) as run_succeeded
          from attempt_ledger
          group by domain, run_id
        )
        select domain, count(*) as successful_runs,
               sum(run_cost) as successful_run_cost_units,
               sum(run_cost) / count(*) as credits_per_successful_run
        from runs
        where run_succeeded = 1
        group by domain
        order by credits_per_successful_run desc, domain
        """
    ).fetchall()

    assert [dict(row) for row in ranked] == [
        {
            "domain": "beta.example",
            "successful_runs": 1,
            "successful_run_cost_units": 10.0,
            "credits_per_successful_run": 10.0,
        },
        {
            "domain": "alpha.example",
            "successful_runs": 1,
            "successful_run_cost_units": 5.0,
            "credits_per_successful_run": 5.0,
        },
    ]
