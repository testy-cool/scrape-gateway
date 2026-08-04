import tempfile
from datetime import UTC, datetime
from pathlib import Path

from scrape_gateway.memory import DomainMemory
from scrape_gateway.models import AttemptLedgerEntry, FailureReason, ScrapeRequest

NOW = datetime(2026, 7, 28, 12, tzinfo=UTC)


def _record(
    memory: DomainMemory,
    run_id: str,
    request: ScrapeRequest,
    provider: str,
    *,
    success: bool,
    route: str | None = None,
    block_type: str | None = None,
) -> None:
    memory.record_attempt_ledger(
        run_id,
        request,
        [
            AttemptLedgerEntry(
                provider=provider,
                route=route,
                cost_units=0,
                cost_provenance="estimated",
                success=success,
                latency_ms=1,
                status_code=200 if success else 403,
                failure_reason=None if success else FailureReason.HTTP_403,
                block_type=block_type,
            )
        ],
        recorded_at=NOW,
    )


def test_remember_and_recall():
    with tempfile.TemporaryDirectory() as tmp:
        mem = DomainMemory(db_path=Path(tmp) / "test.sqlite", clock=lambda: NOW)
        request = ScrapeRequest("https://www.example.com/page", country="us", render_js=True)
        _record(mem, "success", request, "scrapingbee", success=True)
        recall = ScrapeRequest("https://www.example.com/other", country="US", render_js=True)
        assert mem.preferred_provider(recall) == ("scrapingbee", None)


def test_no_memory():
    with tempfile.TemporaryDirectory() as tmp:
        mem = DomainMemory(db_path=Path(tmp) / "test.sqlite")
        assert mem.preferred_provider("https://unknown.com") is None


def test_domain_extraction():
    assert DomainMemory.domain_for_url("https://www.example.com/path") == "example.com"
    assert DomainMemory.domain_for_url("https://sub.example.com") == "sub.example.com"


def test_success_count_increments():
    with tempfile.TemporaryDirectory() as tmp:
        mem = DomainMemory(db_path=Path(tmp) / "test.sqlite")
        mem.remember_success("https://example.com/a", "raw_http", None, False, False)
        mem.remember_success("https://example.com/b", "raw_http", None, False, False)
        stats = mem.provider_stats("https://example.com")
        assert len(stats) == 1
        assert stats[0]["provider"] == "raw_http"
        assert stats[0]["success_count"] == 2


def test_remember_failure():
    with tempfile.TemporaryDirectory() as tmp:
        mem = DomainMemory(db_path=Path(tmp) / "test.sqlite")
        mem.remember_failure("https://example.com/a", "raw_http")
        mem.remember_failure("https://example.com/b", "raw_http")
        stats = mem.provider_stats("https://example.com")
        assert stats[0]["failure_count"] == 2
        assert stats[0]["success_count"] == 0


def test_remember_block():
    with tempfile.TemporaryDirectory() as tmp:
        mem = DomainMemory(db_path=Path(tmp) / "test.sqlite")
        mem.remember_failure("https://example.com/a", "raw_http", block_type="cloudflare")
        stats = mem.provider_stats("https://example.com")
        assert stats[0]["block_count"] == 1
        assert stats[0]["last_block_type"] == "cloudflare"


def test_should_skip_after_repeated_failures():
    with tempfile.TemporaryDirectory() as tmp:
        mem = DomainMemory(db_path=Path(tmp) / "test.sqlite", clock=lambda: NOW)
        request = ScrapeRequest("https://hard.com/page")
        for index in range(5):
            _record(mem, f"failure-{index}", request, "raw_http", success=False)
        assert mem.should_skip_provider("https://hard.com/other", "raw_http") is True


def test_should_not_skip_with_no_history():
    with tempfile.TemporaryDirectory() as tmp:
        mem = DomainMemory(db_path=Path(tmp) / "test.sqlite")
        assert mem.should_skip_provider("https://new.com", "raw_http") is False


def test_should_not_skip_with_good_success_rate():
    with tempfile.TemporaryDirectory() as tmp:
        mem = DomainMemory(db_path=Path(tmp) / "test.sqlite", clock=lambda: NOW)
        request = ScrapeRequest("https://mixed.com/a")
        for index in range(10):
            _record(mem, f"success-{index}", request, "raw_http", success=True)
        _record(mem, "failure", request, "raw_http", success=False)
        assert mem.should_skip_provider("https://mixed.com/x", "raw_http") is False


def test_prefers_provider_with_better_record():
    with tempfile.TemporaryDirectory() as tmp:
        mem = DomainMemory(db_path=Path(tmp) / "test.sqlite", clock=lambda: NOW)
        request = ScrapeRequest("https://example.com/a")
        _record(mem, "raw", request, "raw_http", success=True)
        for index in range(5):
            _record(mem, f"scrapedrive-{index}", request, "scrapedrive", success=True)
        assert mem.preferred_provider("https://example.com") == ("scrapedrive", None)


def test_blocks_penalized_harder():
    with tempfile.TemporaryDirectory() as tmp:
        mem = DomainMemory(db_path=Path(tmp) / "test.sqlite", clock=lambda: NOW)
        request = ScrapeRequest("https://example.com/a")
        _record(mem, "raw-success", request, "raw_http", success=True)
        _record(
            mem,
            "raw-block",
            request,
            "raw_http",
            success=False,
            block_type="cloudflare",
        )
        _record(mem, "scrapedrive-success", request, "scrapedrive", success=True)
        # raw_http: 1 success - (0 failures + 1 block * 3) = -2
        # scrapedrive: 1 success - 0 = 1
        assert mem.preferred_provider("https://example.com") == ("scrapedrive", None)


def test_preferred_provider_returns_tier():
    with tempfile.TemporaryDirectory() as tmp:
        mem = DomainMemory(db_path=Path(tmp) / "test.sqlite", clock=lambda: NOW)
        request = ScrapeRequest("https://example.com/a", country="us", premium=True)
        _record(
            mem,
            "success",
            request,
            "scrapedrive",
            success=True,
            route="scrapedrive:advanced",
        )
        result = mem.preferred_provider(request)
        assert result == ("scrapedrive", "scrapedrive:advanced")


def test_preferred_provider_returns_none_tuple_when_no_tier():
    with tempfile.TemporaryDirectory() as tmp:
        mem = DomainMemory(db_path=Path(tmp) / "test.sqlite", clock=lambda: NOW)
        _record(
            mem,
            "success",
            ScrapeRequest("https://example.com/a"),
            "raw_http",
            success=True,
        )
        result = mem.preferred_provider("https://example.com")
        assert result == ("raw_http", None)


def test_preferred_provider_returns_none_when_no_history():
    with tempfile.TemporaryDirectory() as tmp:
        mem = DomainMemory(db_path=Path(tmp) / "test.sqlite")
        result = mem.preferred_provider("https://unknown.com")
        assert result is None


def test_stores_tier_info():
    with tempfile.TemporaryDirectory() as tmp:
        mem = DomainMemory(db_path=Path(tmp) / "test.sqlite")
        mem.remember_success(
            "https://example.com/a", "scrapedrive", "us", False, True, tier="scrapedrive:advanced"
        )
        stats = mem.provider_stats("https://example.com")
        assert stats[0]["last_success_tier"] == "scrapedrive:advanced"
        assert stats[0]["last_success_country"] == "us"
